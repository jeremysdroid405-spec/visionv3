"""
historical_full_pipeline_replay.py — drive historical SGO props through
the EXACT live PropVision pipeline.

For each SGO outcome row in the window:
  1. resolve player via mlb_master_hub_2026 (live engine identity rule)
  2. invoke MLBHighFrictionModel.predict(..., as_of_date=game_date)
     → leakage-safe via the live `as_of_date` cutoff on bdl_game_logs
  3. derive books-only edge from sgo_pp_research_core_enriched
  4. compose the row schema expected by mlb_replay_multi_tier_eval
  5. evaluate Safe Haven / Front Lines / War Zone gates
  6. attach outcome (hit/loss/push) from sgo_pp_research_outcomes
  7. upsert to sgo_propvision_full_pipeline_replay

Answers exactly: "If this prop had appeared in production this morning,
would PropVision have selected it, in which tier, and did it win?"

Output schema (per row):
    event_id, player_id, stat_id, side, line, period_id, game_date,
    league_id, sport, stat_family, player_name, bdl_player_id, market,
    book, odds, odds_bucket,
    projection_mu, sigma, cv, tp, model_probability,
    fair_probability, implied_probability, edge, projection_margin,
    hit_rate_l5, hit_rate_l10, hit_rate_l20,
    safe_haven_pass, safe_haven_failed_reasons,
    front_lines_pass, front_lines_failed_reasons,
    war_zone_pass,   war_zone_failed_reasons,
    selected_tier,                        # "safe_haven"|"front_lines"|"war_zone"|None
    outcome_resolved, outcome_numeric, hit, actual,
    pipeline_version, scored_at, as_of_date

Idempotent batched upserts. Resumable. Admin API job-runner compatible.
"""
from __future__ import annotations
import argparse
import asyncio
import math
import os
import sys
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient, UpdateOne

OUTCOMES = "sgo_pp_research_outcomes"
ENRICHED = "sgo_pp_research_core_enriched"
OUT_COLL = "sgo_propvision_full_pipeline_replay"

PIPELINE_VERSION = "ppv_full_2026_05_21"

# stat_family → live MLB-HF stat_type
_FAMILY_TO_HF: Dict[str, str] = {
    "hits":                 "hits",
    "total_bases":          "total_bases",
    "hits_runs_rbis":       "hits+runs+rbis",
    "rbis":                 "rbis",
    "runs":                 "runs",
    "home_runs":            "home_runs",
    "singles":              "singles",
    "doubles":              "doubles",
    "batter_strikeouts":    "strikeouts",
    "batting_strikeouts":   "strikeouts",
    "batting_walks":        "walks",
    "pitcher_strikeouts":   "pitcher_strikeouts",
    "earned_runs":          "earned_runs",
    "hits_allowed":         "hits_allowed",
    "walks_allowed":        "pitcher_walks",
    "pitching_outs":        "pitcher_outs",
    "pitcher_hits_allowed": "hits_allowed",
    "pitching_basesOnBalls": "pitcher_walks",
    "stolen_bases":         "stolen_bases",
    "rbi":                  "rbis",
}


def _odds_bucket(odds: Optional[int]) -> str:
    if odds is None: return "odds_na"
    o = int(odds)
    if o < -200: return "odds_lt_-200"
    if o < -100: return "odds_-200_-100"
    if o <    0: return "odds_-100_-0"
    if o <  150: return "odds_+0_+150"
    if o <  300: return "odds_+150_+300"
    return "odds_+300p"


def _cv(mu: Optional[float], sigma: Optional[float]) -> Optional[float]:
    if mu is None or sigma is None: return None
    m = abs(float(mu))
    if m < 1e-6: return None
    return float(sigma) / m


def _margin(mu: Optional[float], line: Optional[float],
              side: Optional[str]) -> Optional[float]:
    if mu is None or line is None or side is None: return None
    raw = float(mu) - float(line)
    return raw if side.upper() == "OVER" else -raw


async def _load_outcomes_enriched(
    db, *, league: str, start: str, end: str,
    exclude_families: List[str],
) -> List[Dict[str, Any]]:
    match: Dict[str, Any] = {
        "outcome_resolved": True,
        "league_id": league,
        "game_date": {"$gte": start, "$lte": end},
    }
    if exclude_families:
        match["stat_family"] = {"$nin": exclude_families}

    pipeline = [
        {"$match": match},
        {"$lookup": {
            "from": ENRICHED,
            "let": {"ev": "$event_id", "pid": "$player_id",
                     "sid": "$stat_id", "sd": "$side",
                     "ln": "$line", "per": "$period_id"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$event_id", "$$ev"]},
                    {"$eq": ["$player_id", "$$pid"]},
                    {"$eq": ["$stat_id", "$$sid"]},
                    {"$eq": ["$side", "$$sd"]},
                    {"$eq": ["$line", "$$ln"]},
                    {"$eq": [{"$ifNull": ["$period_id", None]},
                              {"$ifNull": ["$$per", None]}]},
                ]}}},
                {"$project": {
                    "_id": 0,
                    "consensus_probability": 1,
                    "best_book_probability": 1,
                    "best_book_id": 1,
                    "best_book_odds": 1,
                    "sharp_consensus_probability": 1,
                    "devig_book_count": 1, "sharp_book_count": 1,
                    "market_width": 1, "consensus_disagreement": 1,
                }},
                {"$limit": 1},
            ],
            "as": "enr",
        }},
        {"$match": {"enr.0": {"$exists": True}}},
        {"$project": {
            "_id": 0,
            "event_id": 1, "player_id": 1, "stat_id": 1, "side": 1,
            "line": 1, "period_id": 1, "game_date": 1, "league_id": 1,
            "player_name": 1, "stat_family": 1, "market": 1, "book": 1,
            "odds": 1, "outcome_numeric": 1, "hit": 1, "actual": 1,
            "consensus_prob": {"$arrayElemAt": ["$enr.consensus_probability", 0]},
            "best_book_prob": {"$arrayElemAt": ["$enr.best_book_probability", 0]},
            "best_book_id":   {"$arrayElemAt": ["$enr.best_book_id", 0]},
            "best_book_odds": {"$arrayElemAt": ["$enr.best_book_odds", 0]},
            "sharp_book_count":     {"$arrayElemAt": ["$enr.sharp_book_count", 0]},
            "devig_book_count":     {"$arrayElemAt": ["$enr.devig_book_count", 0]},
            "market_width":         {"$arrayElemAt": ["$enr.market_width", 0]},
            "consensus_disagreement":{"$arrayElemAt": ["$enr.consensus_disagreement", 0]},
        }},
    ]
    rows: List[Dict[str, Any]] = []
    async for r in db[OUTCOMES].aggregate(pipeline, allowDiskUse=True):
        rows.append(r)
    return rows


_STAT_FIELD_MAP = {
    "hits": "hits", "total_bases": "total_bases",
    "runs": "runs", "rbis": "rbis",
    "batter_strikeouts": "strikeouts", "batting_strikeouts": "strikeouts",
    "pitcher_strikeouts": "pitcher_strikeouts",
    "pitcher_walks": "pitcher_walks", "walks_allowed": "pitcher_walks",
    "pitching_basesOnBalls": "pitcher_walks",
    "earned_runs": "earned_runs",
    "pitcher_outs": "pitcher_outs", "pitching_outs": "pitcher_outs",
    "home_runs": "home_runs",
    "hits_runs_rbis": "hits+runs+rbis",
    "hits_allowed": "hits_allowed", "pitcher_hits_allowed": "hits_allowed",
    "singles": "singles", "doubles": "doubles",
    "batting_walks": "walks",
}


def _hit_rate_panels(stat_vals: List[float], line: float
                       ) -> Dict[str, Optional[float]]:
    """Mirrors `services.replay.mlb_replay_engine._hit_rate_panels`.
    Inputs: stat_vals sorted MOST-RECENT-FIRST. Returns percentages 0..100."""
    out: Dict[str, Optional[float]] = {
        "hit_rate_l5": None, "hit_rate_l10": None, "hit_rate_l20": None,
    }
    for n, key in ((5, "hit_rate_l5"), (10, "hit_rate_l10"),
                     (20, "hit_rate_l20")):
        sub = stat_vals[:n]
        if len(sub) >= max(1, min(n, 3)):
            wins = sum(1 for v in sub if v > line)
            out[key] = 100.0 * wins / len(sub)
    return out


def _extract_stat_vals_from_game_logs(
    game_logs: List[Dict[str, Any]],
    stat_family: str,
    as_of_date: Optional[str],
) -> List[float]:
    """Pull values for the relevant stat field from merged game_logs,
    strictly before `as_of_date`, sorted most-recent-first.  Leakage-safe."""
    field = _STAT_FIELD_MAP.get(stat_family, stat_family)
    rows: List[Tuple[Any, float]] = []
    for g in game_logs or []:
        d = g.get("date") or g.get("game_date") or g.get("game_dt")
        if as_of_date and d and str(d) >= str(as_of_date):
            continue  # leakage guard
        if field == "hits+runs+rbis":
            h = g.get("hits"); r = g.get("runs"); rbi = g.get("rbis")
            try:
                if h is None or r is None or rbi is None: continue
                val = float(h) + float(r) + float(rbi)
            except (TypeError, ValueError):
                continue
        else:
            v = g.get(field)
            if v is None: continue
            try:
                val = float(v)
            except (TypeError, ValueError):
                continue
        rows.append((d or "", val))
    rows.sort(key=lambda kv: kv[0], reverse=True)
    return [v for _, v in rows]


# ── Inlined gate evaluator (mirrors services.replay.mlb_replay_multi_tier_eval)
# Inlined to avoid pulling in psutil + the rest of that module's deps when
# this script runs under the Admin API job runner.
_SH_SPEC: Dict[str, Dict[str, float]] = {
    "hits":               {"cv_max": 0.90, "hr_min": 70.0, "edge_min": 0.05, "tp_min": 0.74, "min_margin": 0.50},
    "total_bases":        {"cv_max": 0.75, "hr_min": 70.0, "edge_min": 0.01, "tp_min": 0.50, "min_margin": 1.00},
    "hits_runs_rbis":     {"cv_max": 0.90, "hr_min": 80.0, "edge_min": 0.04, "tp_min": 0.80, "min_margin": 1.00},
    "rbis":               {"cv_max": 0.55, "hr_min": 80.0, "edge_min": 0.01, "tp_min": 0.50, "min_margin": 0.75},
    "runs":               {"cv_max": 0.55, "hr_min": 80.0, "edge_min": 0.01, "tp_min": 0.50, "min_margin": 0.75},
    "pitcher_strikeouts": {"cv_max": 0.45, "hr_min": 70.0, "edge_min": 0.01, "tp_min": 0.50, "min_margin": 0.75},
    "batter_strikeouts":  {"cv_max": 0.80, "hr_min": 80.0, "edge_min": 0.04, "tp_min": 0.78, "min_margin": 0.50},
    "earned_runs":        {"cv_max": 0.40, "hr_min": 70.0, "edge_min": 0.01, "tp_min": 0.50, "min_margin": 0.75},
    "_default":           {"cv_max": 0.60, "hr_min": 80.0, "edge_min": 0.01, "tp_min": 0.50, "min_margin": 0.75},
}
_FL_SPEC: Dict[str, Dict[str, float]] = {
    "hits":               {"cv_max": 0.55, "hr_min": 70.0, "hr_l5_min": 70.0, "edge_min": 0.04, "tp_min": 0.50},
    "total_bases":        {"cv_max": 0.70, "hr_min": 70.0, "hr_l5_min": 70.0, "edge_min": 0.04, "tp_min": 0.50},
    "hits_runs_rbis":     {"cv_max": 0.75, "hr_min": 70.0, "hr_l5_min": 70.0, "edge_min": 0.04, "tp_min": 0.50},
    "rbis":               {"cv_max": 0.55, "hr_min": 70.0, "hr_l5_min": 70.0, "edge_min": 0.04, "tp_min": 0.50},
    "runs":               {"cv_max": 0.55, "hr_min": 70.0, "hr_l5_min": 70.0, "edge_min": 0.04, "tp_min": 0.50},
    "pitcher_outs":       {"cv_max": 0.40, "hr_min": 70.0, "hr_l5_min": 70.0, "edge_min": 0.04, "tp_min": 0.50},
    "pitcher_strikeouts": {"cv_max": 0.50, "hr_min": 70.0, "hr_l5_min": 70.0, "edge_min": 0.04, "tp_min": 0.50},
    "batter_strikeouts":  {"cv_max": 0.65, "hr_min": 70.0, "hr_l5_min": 70.0, "edge_min": 0.04, "tp_min": 0.50},
    "earned_runs":        {"cv_max": 0.50, "hr_min": 70.0, "hr_l5_min": 70.0, "edge_min": 0.04, "tp_min": 0.50},
    "walks_allowed":      {"cv_max": 0.60, "hr_min": 70.0, "hr_l5_min": 70.0, "edge_min": 0.04, "tp_min": 0.50},
    "_default":           {"cv_max": 0.65, "hr_min": 70.0, "hr_l5_min": 70.0, "edge_min": 0.04, "tp_min": 0.50},
}
_WZ_SPEC: Dict[str, float] = {
    "hr_l20_min": 70.0, "hr_l5_min": 60.0, "cv_max": 1.10, "edge_min": 0.05,
}


def _resolve_family(market: Optional[str], stat_family: Optional[str]) -> str:
    m = (market or "").lower()
    sf = (stat_family or "").lower()
    if sf == "strikeouts":
        return "pitcher_strikeouts" if "pitcher" in m else "batter_strikeouts"
    if sf == "pitcher_walks":
        return "walks_allowed"
    return sf


def _lookup(spec: Dict[str, Dict[str, float]], fam: str) -> Dict[str, float]:
    return spec.get(fam) or spec["_default"]


def _direction_ok(row: Dict[str, Any]) -> bool:
    mu = row.get("projection_mu"); ln = row.get("line")
    if mu is None or ln is None:
        return False
    if row.get("side") == "OVER":
        return mu > ln
    return mu < ln


def eval_safe_haven(row: Dict[str, Any]):
    fam = _resolve_family(row.get("market"), row.get("stat_family"))
    s = _lookup(_SH_SPEC, fam)
    failed: List[str] = []
    if not _direction_ok(row):
        failed.append("direction_fail")
    mu, ln, side = row.get("projection_mu"), row.get("line"), row.get("side")
    if mu is not None and ln is not None:
        gap = (mu - ln) if side == "OVER" else (ln - mu)
        if gap < s["min_margin"]:
            failed.append("margin_fail")
    hr20 = row.get("hit_rate_l20")
    if hr20 is None or hr20 < s["hr_min"]:
        failed.append("hit_rate_l20_fail")
    cv = row.get("cv")
    if cv is None or cv > s["cv_max"]:
        failed.append("cv_fail")
    edge = row.get("edge")
    if edge is None or edge < s["edge_min"]:
        failed.append("edge_fail")
    mp = row.get("model_probability")
    if mp is None or mp < s["tp_min"]:
        failed.append("tp_fail")
    return (not failed), failed


def eval_front_lines(row: Dict[str, Any]):
    fam = _resolve_family(row.get("market"), row.get("stat_family"))
    s = _lookup(_FL_SPEC, fam)
    failed: List[str] = []
    if not _direction_ok(row):
        failed.append("direction_fail")
    hr20 = row.get("hit_rate_l20")
    if hr20 is None or hr20 < s["hr_min"]:
        failed.append("hit_rate_l20_fail")
    hr5 = row.get("hit_rate_l5")
    if hr5 is None or hr5 < s["hr_l5_min"]:
        failed.append("hit_rate_l5_fail")
    cv = row.get("cv")
    if cv is None or cv > s["cv_max"]:
        failed.append("cv_fail")
    edge = row.get("edge")
    if edge is None or edge < s["edge_min"]:
        failed.append("edge_fail")
    mp = row.get("model_probability")
    if mp is None or mp < s["tp_min"]:
        failed.append("tp_fail")
    return (not failed), failed


def eval_war_zone(row: Dict[str, Any]):
    failed: List[str] = []
    if not _direction_ok(row):
        failed.append("direction_fail")
    hr20 = row.get("hit_rate_l20")
    if hr20 is None or hr20 < _WZ_SPEC["hr_l20_min"]:
        failed.append("hit_rate_l20_fail")
    hr5 = row.get("hit_rate_l5")
    if hr5 is None or hr5 < _WZ_SPEC["hr_l5_min"]:
        failed.append("hit_rate_l5_fail")
    cv = row.get("cv")
    if cv is None or cv > _WZ_SPEC["cv_max"]:
        failed.append("cv_fail")
    edge = row.get("edge")
    if edge is None or edge < _WZ_SPEC["edge_min"]:
        failed.append("edge_fail")
    return (not failed), failed


# Lazy-import the live engine
def _import_live():
    from services.mlb_high_friction_model import MLBHighFrictionModel
    return (MLBHighFrictionModel,
              eval_safe_haven, eval_front_lines, eval_war_zone,
              _resolve_family)


async def _resume_set(db, *, league: str, start: str, end: str) -> set:
    found: set = set()
    async for r in db[OUT_COLL].find(
        {"league_id": league, "game_date": {"$gte": start, "$lte": end},
          "pipeline_version": PIPELINE_VERSION},
        projection={"_id": 0, "event_id": 1, "player_id": 1, "stat_id": 1,
                     "side": 1, "line": 1, "period_id": 1},
    ):
        found.add((r["event_id"], r["player_id"], r["stat_id"],
                     r["side"], r["line"], r.get("period_id")))
    return found


async def _run(args: argparse.Namespace) -> int:
    (MLBHighFrictionModel,
     eval_sh, eval_fl, eval_wz, resolve_family) = _import_live()

    sync_db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    model = MLBHighFrictionModel(sync_db)
    for p in ("/var/www/app/backend/models/mlb_hf",
                "/app/backend/models/mlb_hf"):
        if os.path.isdir(p):
            model.MODEL_DIR = p; break
    n_loaded = model.load_models()
    print(f"  load_models() → {n_loaded}; "
            f"loaded: {sorted(model.models.keys())}")
    if not model.models:
        print("  ❌ no models loaded; aborting.")
        return 2

    # 2026-05-21 — `master_hub.bdl_game_logs[]` on prod only holds the
    # CURRENT season's logs (live engine reads it that way). For
    # historical replay we need to merge pre-window logs from
    # `mlb_historical_logs` so `predict()`'s `_filter_logs_before(...
    # as_of_date)` has enough rows to satisfy the `len(game_logs) >= 5`
    # gate. We monkey-patch `model.master_hub.find_one` to do this
    # merge in-memory. The production collection is NEVER written to.
    _orig_find_one = model.master_hub.find_one
    _hist = sync_db.mlb_historical_logs
    _hist_cache: Dict[int, List[Dict[str, Any]]] = {}

    def _merged_find_one(query, projection=None, **kw):
        doc = _orig_find_one(query, projection, **kw)
        if not doc:
            return doc
        # Resolve player_id for the historical lookup
        pid = doc.get("bdl_player_id") or doc.get("bdl_id")
        if pid is None:
            return doc
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            return doc
        if pid_int not in _hist_cache:
            hist = _hist.find_one(
                {"$or": [{"player_id": pid_int},
                          {"bdl_player_id": pid_int}]},
                {"_id": 0, "game_logs": 1},
            )
            _hist_cache[pid_int] = (hist or {}).get("game_logs") or []
        hist_logs = _hist_cache[pid_int]
        if not hist_logs:
            return doc
        # Merge — historical archive on top, current-season after.
        # Dedupe by game_id.
        live = doc.get("bdl_game_logs") or []
        seen = {g.get("game_id") for g in live if g.get("game_id") is not None}
        merged = list(live)
        for g in hist_logs:
            if g.get("game_id") in seen:
                continue
            merged.append(g)
            if g.get("game_id") is not None:
                seen.add(g.get("game_id"))
        doc["bdl_game_logs"] = merged
        return doc

    model.master_hub.find_one = _merged_find_one  # type: ignore[method-assign]
    print(f"  historical-logs merger installed "
            f"(reads {_hist.database.name}.mlb_historical_logs)")

    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    excl = [s.strip() for s in (args.exclude_stat_family or "").split(",") if s.strip()]
    print(f"  loading outcomes-enriched join … window={args.start}..{args.end}")
    rows = await _load_outcomes_enriched(
        db, league=args.league, start=args.start, end=args.end,
        exclude_families=excl,
    )
    print(f"  rows in scope: {len(rows):,}")
    if args.limit:
        rows = rows[: args.limit]
        print(f"  --limit applied: scoring {len(rows):,}")

    already = set()
    if not args.force:
        already = await _resume_set(
            db, league=args.league, start=args.start, end=args.end)
        print(f"  resume: {len(already):,} rows already in {OUT_COLL}")

    # Counters
    n = scored = skipped = err_predict = err_no_hub = err_no_hf_stat = 0
    err_insufficient = err_player_not_found = err_no_model = err_other = 0
    err_first_examples: List[str] = []
    tier_counts: Dict[str, int] = {"safe_haven": 0, "front_lines": 0,
                                       "war_zone": 0, "none": 0}
    pass_any = 0

    buf: List[UpdateOne] = []

    for doc in rows:
        n += 1
        key = (doc["event_id"], doc["player_id"], doc["stat_id"],
               doc["side"], doc["line"], doc.get("period_id"))
        if key in already:
            skipped += 1; continue

        hf_stat = _FAMILY_TO_HF.get(doc.get("stat_family"))
        if not hf_stat:
            err_no_hf_stat += 1; continue

        pname = doc.get("player_name")
        hub = None
        for q in [{"display_name": pname}, {"player_name": pname},
                    {"mlb_full_name": pname}]:
            hub = sync_db.mlb_master_hub_2026.find_one(
                q, {"bdl_player_id": 1, "bdl_id": 1, "_id": 0})
            if hub: break
        bdl_pid = (hub.get("bdl_player_id") or hub.get("bdl_id")) if hub else None
        if bdl_pid is None:
            err_no_hub += 1; continue

        try:
            result = model.predict(
                player_name=pname,
                stat_type=hf_stat,
                line=float(doc.get("line") or 0),
                bdl_player_id=int(bdl_pid),
                as_of_date=doc.get("game_date"),
            )
        except Exception as e:
            err_predict += 1
            if len(err_first_examples) < 10:
                err_first_examples.append(f"raised {pname}/{hf_stat}: {e!r}")
            continue
        if isinstance(result, dict) and "error" in result:
            err_predict += 1
            emsg = str(result.get("error") or "")
            if "Insufficient games" in emsg:
                err_insufficient += 1
            elif "Player not found" in emsg:
                err_player_not_found += 1
            elif "No model for" in emsg:
                err_no_model += 1
            else:
                err_other += 1
            if len(err_first_examples) < 10:
                err_first_examples.append(f"{pname}/{hf_stat}: {emsg!r}")
            continue

        mu = result.get("predicted")
        if mu is None:
            mu = result.get("projection_mu") or result.get("mu")
        sigma = result.get("std_dev")
        if sigma is None:
            sigma = result.get("sigma")
        prob_over_pct = result.get("prob_over")    # in PERCENT (0-100)
        model_p = (prob_over_pct / 100.0
                       if isinstance(prob_over_pct, (int, float))
                       else (result.get("model_probability") or
                              result.get("model_prob")))
        # `tp` (true probability) doesn't exist in the live `predict()`
        # return; the gate spec treats model_probability AS the tp gate
        # value (see eval_safe_haven etc which check row['model_probability']
        # against s['tp_min']). Surface model_p as tp for downstream consumers.
        tp_val = result.get("tp") or result.get("fair_probability") or model_p

        # 2026-05-21 — `prob_over` is the OVER probability. For an
        # UNDER prop, the model probability is 1 - prob_over.
        side = (doc.get("side") or "").upper()
        if side == "UNDER" and model_p is not None:
            model_p = 1.0 - float(model_p)
            tp_val   = model_p
        if mu is None or sigma is None or model_p is None:
            err_predict += 1
            err_other += 1
            if len(err_first_examples) < 10:
                err_first_examples.append(
                    f"incomplete {pname}/{hf_stat}: mu={mu} sigma={sigma} "
                    f"model_p={model_p}")
            continue

        consensus_p = doc.get("consensus_prob")
        fair_p   = float(consensus_p) if consensus_p is not None else None
        edge_v   = (float(model_p) - fair_p) if (model_p is not None and fair_p is not None) else None
        trends   = (result.get("friction_audit") or {}).get("trends") or {}

        # 2026-05-21 — compute hit_rate_l5/l10/l20 from the MERGED
        # `bdl_game_logs[]` (which includes mlb_historical_logs.game_logs
        # after the merger monkey-patch). The live `predict()` only
        # emits L5/L10; L20 is computed downstream in the production
        # pipeline. We replicate that step here so the gate evaluators
        # (which require hit_rate_l20) can run unmodified.
        hub_with_logs = _merged_find_one({"bdl_player_id": int(bdl_pid)},
                                              {"_id": 0, "bdl_player_id": 1,
                                               "bdl_id": 1, "bdl_game_logs": 1})
        merged_logs = (hub_with_logs or {}).get("bdl_game_logs") or []
        stat_vals = _extract_stat_vals_from_game_logs(
            merged_logs,
            doc.get("stat_family") or "",
            doc.get("game_date"),
        )
        panels = _hit_rate_panels(stat_vals, float(doc.get("line") or 0))
        hr5  = panels["hit_rate_l5"]  if panels["hit_rate_l5"]  is not None else trends.get("hit_rate_l5")
        hr10 = panels["hit_rate_l10"] if panels["hit_rate_l10"] is not None else trends.get("hit_rate_l10")
        hr20 = panels["hit_rate_l20"]

        # For UNDER props the live engine inverts hit_rate (so hr_l5
        # measures "how often UNDER hit"). Mirror that.
        if side == "UNDER":
            if hr5  is not None: hr5  = 100.0 - hr5
            if hr10 is not None: hr10 = 100.0 - hr10
            if hr20 is not None: hr20 = 100.0 - hr20

        replay_row = {
            "event_id": doc["event_id"],
            "player_id": doc["player_id"],
            "stat_id":   doc["stat_id"],
            "side":      (doc["side"] or "").upper(),
            "line":      float(doc.get("line")) if doc.get("line") is not None else None,
            "period_id": doc.get("period_id"),
            "game_date": doc.get("game_date"),
            "league_id": doc.get("league_id"),
            "sport":     "mlb",
            "stat_family": doc.get("stat_family"),
            "player_name": pname,
            "bdl_player_id": int(bdl_pid),
            "market":      doc.get("market"),
            "book":        doc.get("best_book_id") or doc.get("book"),
            "odds":        doc.get("best_book_odds") or doc.get("odds"),
            "odds_bucket": _odds_bucket(doc.get("best_book_odds") or doc.get("odds")),

            "projection_mu":     float(mu),
            "sigma":             float(sigma),
            "cv":                _cv(mu, sigma),
            "tp":                float(tp_val) if tp_val is not None else None,
            "model_probability": float(model_p),
            "fair_probability":  fair_p,
            "implied_probability": fair_p,
            "edge":              edge_v,
            "projection_margin": _margin(mu, doc.get("line"), doc.get("side")),

            "hit_rate_l5":  hr5,
            "hit_rate_l10": hr10,
            "hit_rate_l20": hr20,

            "outcome_resolved": True,
            "outcome_numeric":  doc.get("outcome_numeric"),
            "hit":              doc.get("hit"),
            "actual":           doc.get("actual"),

            "pipeline_version": PIPELINE_VERSION,
            "scored_at":        datetime.now(timezone.utc),
            "as_of_date":       doc.get("game_date"),
        }

        # ── Gate evaluation (the row needs to look like what the
        # multi_tier_eval expects) ───────────────────────────────
        gate_row = {**replay_row,
                       "market": doc.get("market"),
                       "stat_family": doc.get("stat_family")}
        sh_pass, sh_reasons = eval_sh(gate_row)
        fl_pass, fl_reasons = eval_fl(gate_row)
        wz_pass, wz_reasons = eval_wz(gate_row)
        replay_row["safe_haven_pass"]            = sh_pass
        replay_row["safe_haven_failed_reasons"]  = sh_reasons
        replay_row["front_lines_pass"]           = fl_pass
        replay_row["front_lines_failed_reasons"] = fl_reasons
        replay_row["war_zone_pass"]              = wz_pass
        replay_row["war_zone_failed_reasons"]    = wz_reasons

        # Tier assignment: SH > FL > WZ (same priority as live runner)
        sel = ("safe_haven" if sh_pass else
                "front_lines" if fl_pass else
                "war_zone" if wz_pass else None)
        replay_row["selected_tier"] = sel
        tier_counts[sel or "none"] += 1
        if sel: pass_any += 1

        # Upsert
        flt = {k: replay_row[k] for k in
                ("event_id", "player_id", "stat_id", "side", "line",
                 "period_id", "pipeline_version")}
        buf.append(UpdateOne(flt, {"$set": replay_row}, upsert=True))
        scored += 1

        if len(buf) >= 500:
            await db[OUT_COLL].bulk_write(buf, ordered=False)
            buf = []
            if scored % 2000 == 0:
                print(f"  progress: scanned={n} scored={scored} "
                        f"skipped={skipped} predict_err={err_predict} "
                        f"any_tier={pass_any}")

    if buf:
        await db[OUT_COLL].bulk_write(buf, ordered=False)

    print()
    print("=" * 78)
    print(f"  rows scanned      {n}")
    print(f"  scored            {scored}")
    print(f"  skipped (resume)  {skipped}")
    print(f"  no_hub            {err_no_hub}")
    print(f"  no_hf_stat        {err_no_hf_stat}")
    print(f"  predict errors    {err_predict}")
    print(f"    insufficient_games   {err_insufficient}")
    print(f"    player_not_found     {err_player_not_found}")
    print(f"    no_model_for_stat    {err_no_model}")
    print(f"    other / incomplete   {err_other}")
    if err_first_examples:
        print("  first 10 predict-error examples:")
        for ex in err_first_examples:
            print(f"    {ex}")
    print()
    print(f"  tier selection counts (of scored):")
    for t, c in tier_counts.items():
        pct = (c * 100.0 / max(scored, 1))
        print(f"    {t:<14} {c:>6} ({pct:5.1f}%)")
    print(f"  any_tier_pass     {pass_any} ({(pass_any*100.0/max(scored,1)):.1f}%)")
    print("=" * 78)
    return 0


def _parse():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--league",  default="MLB")
    p.add_argument("--start",   required=True)
    p.add_argument("--end",     required=True)
    p.add_argument("--exclude-stat-family", default="fantasy_score")
    p.add_argument("--limit",   type=int, default=None)
    p.add_argument("--force",   action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(_run(_parse())))
