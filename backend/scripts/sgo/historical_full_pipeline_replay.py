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


# Lazy-import the live engine + gate evaluators
def _import_live():
    from services.mlb_high_friction_model import MLBHighFrictionModel
    from services.replay.mlb_replay_multi_tier_eval import (
        eval_safe_haven, eval_front_lines, eval_war_zone, _resolve_family,
    )
    return (MLBHighFrictionModel,
              eval_safe_haven, eval_front_lines, eval_war_zone, _resolve_family)


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

        mu = result.get("projection_mu") or result.get("mu")
        sigma = result.get("sigma")
        model_p = result.get("model_probability") or result.get("model_prob")
        tp_val = result.get("tp") or result.get("fair_probability")
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
        edge_v   = (float(model_p) - fair_p) if fair_p is not None else None

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

            "hit_rate_l5":  result.get("hit_rate_l5"),
            "hit_rate_l10": result.get("hit_rate_l10"),
            "hit_rate_l20": result.get("hit_rate_l20"),

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
