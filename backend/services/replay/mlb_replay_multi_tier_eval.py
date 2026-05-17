"""MLB Replay — Layer 4 multi-tier sweep.

Evaluates the SAME `mlb_replay_model_outputs` universe against THREE
gate configurations in parallel (Safe Haven / Front Lines / War Zone)
and produces a side-by-side tier comparison, overlap analysis, and
per-tier breakdowns.

NO model inference, NO external API, NO Layer 1-3 rewrites.
Reads only:
  - mlb_replay_model_outputs
  - mlb_master_hub_2026.bdl_game_logs[]   (for grading)

Persists:
  - mlb_replay_gate_results   (one row per (model-output × tier))
  - mlb_replay_backtest_runs  (one summary doc per (date × snapshot × tier))
"""
from __future__ import annotations
import logging
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import psutil
from pymongo import ASCENDING, UpdateOne

from services.replay.mlb_replay_gate_eval import (
    BACKTEST_RUNS_COLL, DEFAULT_MEM_LIMIT_MB, GATE_RESULTS_COLL,
    _actual_for, _build_actual_outcomes, _cv_bucket, _edge_bucket,
    _hr_bucket, _odds_bucket, ensure_indexes, grade_one,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Tier gate configs (mirror /app/backend/services/scoring/gates/thresholds.py
# but re-stated here so Layer-4 stays a self-contained read-only evaluator.)
# Edge stored in fractions (0.05 = 5%). model_probability/tp in fractions.
# ─────────────────────────────────────────────────────────────────────

# Safe Haven — mirrors `_MLB_SAFE_HAVEN` (production source of truth).
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

# Front Lines — mirrors `_MLB_FRONT_LINES` (HR floor 70, edge≥0.04,
# tp≥0.50, L5 sub-gate enforced at hr_min).
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

# War Zone — 2026-05-16 5-gate strict (mirrors `mlb_war_zone_v1_2026_05_16`).
_WZ_SPEC: Dict[str, float] = {
    "hr_l20_min": 70.0, "hr_l5_min": 60.0, "cv_max": 1.10, "edge_min": 0.05,
}

TIER_CONFIGS: Dict[str, Dict[str, Any]] = {
    "safe_haven":  {"version": "mlb_sh_v1_2026_05_16",
                    "spec": _SH_SPEC,
                    "evaluator": "safe_haven"},
    "front_lines": {"version": "mlb_fl_v1_2026_05_16",
                    "spec": _FL_SPEC,
                    "evaluator": "front_lines"},
    "war_zone":    {"version": "mlb_war_zone_v1_2026_05_16",
                    "spec": _WZ_SPEC,
                    "evaluator": "war_zone"},
}


# ── stat_family mapping (replay → production) ────────────────────────
def _resolve_family(market: Optional[str], stat_family: Optional[str]) -> str:
    """Map (market, replay-stat_family) → production threshold family key.

    Replay uses generic stat_family names ("strikeouts", "pitcher_walks");
    production specs distinguish batter vs pitcher.
    """
    m = (market or "").lower()
    sf = (stat_family or "").lower()
    if sf == "strikeouts":
        return "pitcher_strikeouts" if "pitcher" in m else "batter_strikeouts"
    if sf == "pitcher_walks":
        return "walks_allowed"
    return sf


def _lookup(spec: Dict[str, Dict[str, float]], fam: str) -> Dict[str, float]:
    return spec.get(fam) or spec["_default"]


# ── Tier evaluators ──────────────────────────────────────────────────
def _direction_ok(row: Dict[str, Any]) -> bool:
    mu = row.get("projection_mu"); line = row.get("line")
    if mu is None or line is None:
        return False
    if row.get("side") == "OVER":
        return mu > line
    return mu < line


def eval_safe_haven(row: Dict[str, Any]) -> Tuple[bool, List[str]]:
    fam = _resolve_family(row.get("market"), row.get("stat_family"))
    s = _lookup(_SH_SPEC, fam)
    failed: List[str] = []
    if not _direction_ok(row):
        failed.append("direction_fail")
    # SH has its own min_margin cushion gate (mlb-specific).
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


def eval_front_lines(row: Dict[str, Any]) -> Tuple[bool, List[str]]:
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


def eval_war_zone(row: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Identical to the strict 2026-05-16 5-gate spec evaluated in
    `mlb_replay_gate_eval.evaluate_gates`."""
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


_EVAL_FNS: Dict[str, Callable[[Dict[str, Any]], Tuple[bool, List[str]]]] = {
    "safe_haven":  eval_safe_haven,
    "front_lines": eval_front_lines,
    "war_zone":    eval_war_zone,
}


def _rss_mb() -> float:
    return psutil.Process().memory_info().rss / (1024 * 1024)


def _pick_key(r: Dict[str, Any]) -> Tuple:
    return (r["event_id"], r["player_name_normalized"], r["market"],
            r["line"], r["side"], r["book"])


# ── Main entrypoint ──────────────────────────────────────────────────
async def run_multi_tier_for_date(
    db, game_date: str, *,
    snapshot_iso: str,
    scoring_config_version: str,
    tiers: Tuple[str, ...] = ("safe_haven", "front_lines", "war_zone"),
    mem_limit_mb: int = DEFAULT_MEM_LIMIT_MB,
) -> Dict[str, Any]:
    await ensure_indexes(db)
    started_at = datetime.now(timezone.utc)
    rss0 = _rss_mb()

    actuals = await _build_actual_outcomes(db, game_date)
    rss_after_actuals = _rss_mb()

    cursor = db.mlb_replay_model_outputs.find(
        {"game_date": game_date, "snapshot_iso": snapshot_iso,
         "scoring_config_version": scoring_config_version},
        projection={"_id": 0},
    )

    per_tier_rows: Dict[str, List[Dict[str, Any]]] = {t: [] for t in tiers}
    per_tier_fail_counter: Dict[str, Counter] = {t: Counter() for t in tiers}
    per_tier_qualified_keys: Dict[str, set] = {t: set() for t in tiers}
    per_tier_n_pass: Dict[str, int] = {t: 0 for t in tiers}
    per_tier_n_fail: Dict[str, int] = {t: 0 for t in tiers}

    buffer: List[Dict[str, Any]] = []
    seen = 0
    rss_peak = max(rss0, rss_after_actuals)

    async def _flush():
        nonlocal buffer
        if not buffer: return
        key_fields = (
            "game_date", "event_id", "player_name_normalized", "market",
            "line", "side", "book", "snapshot_iso",
            "scoring_config_version", "gate_config_version",
        )
        ops = []
        for r in buffer:
            f = {k: r[k] for k in key_fields}
            ops.append(UpdateOne(f, {"$set": r}, upsert=True))
        try:
            await db[GATE_RESULTS_COLL].bulk_write(ops, ordered=False)
        except Exception as exc:  # noqa: BLE001
            logger.error("[layer4-multi] bulk_write failed: %s", exc)
        buffer.clear()

    async for r in cursor:
        seen += 1
        for tier in tiers:
            cfg = TIER_CONFIGS[tier]
            evaluator = _EVAL_FNS[cfg["evaluator"]]
            passed, failed = evaluator(r)
            if passed:
                per_tier_n_pass[tier] += 1
                per_tier_qualified_keys[tier].add(_pick_key(r))
            else:
                per_tier_n_fail[tier] += 1
                for fg in failed:
                    per_tier_fail_counter[tier][fg] += 1

            graded = {"status": "ungated", "actual": None,
                      "profit_units": 0.0, "stake": 0.0}
            if passed:
                actual = _actual_for(actuals, r["player_name_normalized"],
                                      r["stat_family"])
                graded = grade_one(actual, float(r["line"]), r["side"],
                                    int(r["odds"]))

            gr = {
                "sport": "mlb", "game_date": r["game_date"],
                "event_id": r["event_id"],
                "home_team": r.get("home_team"), "away_team": r.get("away_team"),
                "commence_time": r.get("commence_time"),
                "snapshot_iso": r["snapshot_iso"],
                "player_name_normalized": r["player_name_normalized"],
                "player_name": r.get("player_name"),
                "player_id": r.get("player_id"),
                "team": r.get("team"), "opponent": r.get("opponent"),
                "is_away_team": r.get("is_away_team"),
                "market": r["market"], "is_alternate": r.get("is_alternate"),
                "stat_family": r["stat_family"],
                "production_family": _resolve_family(r.get("market"),
                                                     r.get("stat_family")),
                "line": float(r["line"]), "side": r["side"],
                "book": r["book"], "odds": int(r["odds"]),
                "projection_mu": r["projection_mu"], "sigma": r["sigma"],
                "model_probability": r["model_probability"],
                "fair_probability": r["fair_probability"],
                "implied_probability": r["implied_probability"],
                "edge": r["edge"],
                "hit_rate_l5":  r.get("hit_rate_l5"),
                "hit_rate_l10": r.get("hit_rate_l10"),
                "hit_rate_l20": r.get("hit_rate_l20"),
                "cv": r.get("cv"),
                "tier": tier,
                "gate_pass": passed,
                "failed_gates": failed,
                "gate_config_version": cfg["version"],
                "scoring_config_version": scoring_config_version,
                "grade_status": graded["status"],
                "actual": graded["actual"],
                "profit_units": graded["profit_units"],
                "stake_units": graded["stake"],
                "evaluated_at": datetime.now(timezone.utc),
            }
            buffer.append(gr)
            per_tier_rows[tier].append(gr)

        if len(buffer) >= 500:
            await _flush()
            rss = _rss_mb()
            if rss > rss_peak: rss_peak = rss
            if rss > mem_limit_mb:
                raise MemoryError(f"Layer4 RSS {rss:.1f}>{mem_limit_mb}")
    await _flush()

    # ── Per-tier summary helper (same shape as Layer 4 single tier) ──
    def _summary_of(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not rows:
            return {"total": 0, "wins": 0, "losses": 0, "pushes": 0,
                    "ungraded": 0, "hit_rate_pct": None,
                    "profit_units": 0.0, "stake_units": 0.0,
                    "roi_pct": None, "avg_odds": None,
                    "median_odds": None, "avg_edge": None,
                    "avg_cv": None, "avg_mu_minus_line": None}
        graded = [r for r in rows
                  if r["grade_status"] in ("win", "loss", "push")]
        wins = sum(1 for r in graded if r["grade_status"] == "win")
        losses = sum(1 for r in graded if r["grade_status"] == "loss")
        pushes = sum(1 for r in graded if r["grade_status"] == "push")
        ungraded = len(rows) - len(graded)
        graded_decisions = wins + losses
        hit_rate = (wins / graded_decisions * 100.0) if graded_decisions else None
        profit = sum(r["profit_units"] for r in graded)
        stake  = sum(r["stake_units"]  for r in graded)
        roi = (profit / stake * 100.0) if stake else None
        odds_list = [int(r["odds"]) for r in rows]
        edges = [r["edge"] for r in rows if r.get("edge") is not None]
        cvs   = [r["cv"]   for r in rows if r.get("cv")   is not None]
        gaps  = []
        for r in rows:
            mu, ln = r.get("projection_mu"), r.get("line")
            if mu is None or ln is None: continue
            gap = (mu - ln) if r["side"] == "OVER" else (ln - mu)
            gaps.append(gap)
        return {
            "total": len(rows),
            "wins": wins, "losses": losses, "pushes": pushes,
            "ungraded": ungraded,
            "hit_rate_pct": hit_rate,
            "profit_units": profit, "stake_units": stake,
            "roi_pct": roi,
            "avg_odds": (sum(odds_list) / len(odds_list)) if odds_list else None,
            "median_odds": statistics.median(odds_list) if odds_list else None,
            "avg_edge": (sum(edges) / len(edges)) if edges else None,
            "avg_cv":   (sum(cvs)   / len(cvs))   if cvs   else None,
            "avg_mu_minus_line": (sum(gaps)/len(gaps)) if gaps else None,
        }

    finished_at = datetime.now(timezone.utc)
    elapsed = (finished_at - started_at).total_seconds()
    rss_end = _rss_mb()

    out: Dict[str, Any] = {
        "game_date": game_date, "snapshot_iso": snapshot_iso,
        "scoring_config_version": scoring_config_version,
        "rows_scanned": seen,
        "started_at": started_at, "finished_at": finished_at,
        "elapsed_s": elapsed,
        "rss_mb_start": round(rss0, 1),
        "rss_mb_after_actuals_load": round(rss_after_actuals, 1),
        "rss_mb_peak": round(rss_peak, 1),
        "rss_mb_end": round(rss_end, 1),
        "tiers": {},
    }

    for tier in tiers:
        cfg = TIER_CONFIGS[tier]
        rows = per_tier_rows[tier]
        qual_rows = [r for r in rows if r["gate_pass"]]
        by_family = {fam: _summary_of(
            [r for r in qual_rows if r["production_family"] == fam]
        ) for fam in {r["production_family"] for r in qual_rows}}
        by_book = {bk: _summary_of(
            [r for r in qual_rows if r["book"] == bk]
        ) for bk in {r["book"] for r in qual_rows}}
        by_market_type = {
            "alternate": _summary_of([r for r in qual_rows if r["is_alternate"]]),
            "standard":  _summary_of([r for r in qual_rows if not r["is_alternate"]]),
        }
        by_edge = defaultdict(list); by_cv = defaultdict(list)
        by_hr  = defaultdict(list); by_odds = defaultdict(list)
        for r in qual_rows:
            by_edge[_edge_bucket(r["edge"])].append(r)
            by_cv[_cv_bucket(r["cv"])].append(r)
            by_hr[_hr_bucket(r["hit_rate_l20"])].append(r)
            by_odds[_odds_bucket(r["odds"])].append(r)
        tier_summary = {
            "tier": tier,
            "gate_config_version": cfg["version"],
            "gate_pass":     per_tier_n_pass[tier],
            "gate_fail":     per_tier_n_fail[tier],
            "failed_gate_breakdown": dict(per_tier_fail_counter[tier]),
            "overall":       _summary_of(qual_rows),
            "by_stat_family": by_family,
            "by_book":        by_book,
            "by_market_type": by_market_type,
            "by_edge_bucket": {k: _summary_of(v) for k, v in by_edge.items()},
            "by_cv_bucket":   {k: _summary_of(v) for k, v in by_cv.items()},
            "by_hr_bucket":   {k: _summary_of(v) for k, v in by_hr.items()},
            "by_odds_bucket": {k: _summary_of(v) for k, v in by_odds.items()},
            "qualified_keys": list(per_tier_qualified_keys[tier]),
        }
        out["tiers"][tier] = tier_summary
        # Persist per-tier backtest run doc.
        persisted = {
            "game_date_start": game_date, "game_date_end": game_date,
            "snapshot_iso": snapshot_iso,
            "scoring_config_version": scoring_config_version,
            "gate_config_version": cfg["version"],
            "tier": tier,
            "started_at": started_at, "finished_at": finished_at,
            "elapsed_s": elapsed,
            "rows_scanned": seen,
            "gate_pass": per_tier_n_pass[tier],
            "gate_fail": per_tier_n_fail[tier],
            "failed_gate_breakdown": dict(per_tier_fail_counter[tier]),
            "overall":       tier_summary["overall"],
            "by_stat_family": by_family,
            "by_book":        by_book,
            "by_market_type": by_market_type,
            "by_edge_bucket": tier_summary["by_edge_bucket"],
            "by_cv_bucket":   tier_summary["by_cv_bucket"],
            "by_hr_bucket":   tier_summary["by_hr_bucket"],
            "by_odds_bucket": tier_summary["by_odds_bucket"],
        }
        await db[BACKTEST_RUNS_COLL].insert_one(persisted)

    # ── Overlap analysis on UNIQUE PICK IDENTITY (event/player/market/line/side/book) ──
    sets = {t: per_tier_qualified_keys[t] for t in tiers}
    out["overlap"] = {
        "safe_haven_only":          len(sets.get("safe_haven", set()) -
                                        sets.get("front_lines", set()) -
                                        sets.get("war_zone", set())),
        "front_lines_only":         len(sets.get("front_lines", set()) -
                                        sets.get("safe_haven", set()) -
                                        sets.get("war_zone", set())),
        "war_zone_only":            len(sets.get("war_zone", set()) -
                                        sets.get("safe_haven", set()) -
                                        sets.get("front_lines", set())),
        "sh_∩_fl":                  len(sets.get("safe_haven", set()) &
                                        sets.get("front_lines", set())),
        "fl_∩_wz":                  len(sets.get("front_lines", set()) &
                                        sets.get("war_zone", set())),
        "sh_∩_wz":                  len(sets.get("safe_haven", set()) &
                                        sets.get("war_zone", set())),
        "sh_∩_fl_∩_wz":             len(sets.get("safe_haven", set()) &
                                        sets.get("front_lines", set()) &
                                        sets.get("war_zone", set())),
        "sh_size":  len(sets.get("safe_haven", set())),
        "fl_size":  len(sets.get("front_lines", set())),
        "wz_size":  len(sets.get("war_zone", set())),
    }
    return out


__all__ = ["TIER_CONFIGS", "run_multi_tier_for_date"]
