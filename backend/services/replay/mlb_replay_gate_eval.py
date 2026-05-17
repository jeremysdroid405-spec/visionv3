"""
MLB Replay — Layer 4: Gate Evaluation + Backtest Grading.
=========================================================

Applies the finalised MLB War Zone gates to `mlb_replay_model_outputs`,
writes pass/fail rows, then joins qualified picks to the player's actual
game log for that date and computes ROI / hit-rate / unit P&L
aggregated by stat_family, book, line, edge bucket, CV bucket, etc.

NO model inference in this layer. NO external API calls. Reads only:
  - mlb_replay_model_outputs  (Layer 3 output)
  - mlb_master_hub_2026.bdl_game_logs[]  (for actual stat outcomes)
"""
from __future__ import annotations
import logging
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import psutil
from pymongo import ASCENDING, UpdateOne

from services.replay.mlb_feature_cache import (
    _STAT_FIELD_MAP, normalize_player_name,
)

logger = logging.getLogger(__name__)

GATE_RESULTS_COLL = "mlb_replay_gate_results"
BACKTEST_RUNS_COLL = "mlb_replay_backtest_runs"

GATE_CONFIG_VERSION = "mlb_war_zone_v1_2026_05_16"
GATE_SPEC = {
    "hit_rate_l20_min": 70.0,
    "hit_rate_l5_min":  60.0,
    "cv_max":           1.1,
    "edge_min":         0.05,
    "projection_direction": "strict",
}

DEFAULT_MEM_LIMIT_MB = 1_500


def _rss_mb() -> float:
    return psutil.Process().memory_info().rss / (1024 * 1024)


def _american_payout(odds: int, stake: float = 1.0) -> float:
    """Profit (NOT total return) on a winning bet of `stake` units."""
    odds = int(odds)
    if odds > 0:
        return stake * (odds / 100.0)
    return stake * (100.0 / (-odds))


# ── Gate evaluation ───────────────────────────────────────────────────
def evaluate_gates(row: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Return (passed_all, failed_gates_list). Mirrors EXACTLY the
    production WZ gate spec from /memory/PRD-style rules (Source:
    `services/scoring/gates/thresholds.py::_MLB_WAR_ZONE_*` 2026-05-16)."""
    failed: List[str] = []

    side = row.get("side")
    line = row.get("line")
    mu = row.get("projection_mu")
    edge = row.get("edge")
    hr_l5 = row.get("hit_rate_l5")
    hr_l20 = row.get("hit_rate_l20")
    cv = row.get("cv")

    # 1. L20 hit-rate gate (≥ 70)
    if hr_l20 is None or hr_l20 < GATE_SPEC["hit_rate_l20_min"]:
        failed.append("l20_hit_rate_fail")
    # 2. L5 hit-rate gate (≥ 60)
    if hr_l5 is None or hr_l5 < GATE_SPEC["hit_rate_l5_min"]:
        failed.append("l5_hit_rate_fail")
    # 3. CV gate (≤ 1.1)
    if cv is None or cv > GATE_SPEC["cv_max"]:
        failed.append("cv_fail")
    # 4. Direction gate (strict)
    if mu is None or line is None:
        failed.append("projection_direction_fail")
    else:
        if side == "OVER" and not (mu > line):
            failed.append("projection_direction_fail")
        elif side == "UNDER" and not (mu < line):
            failed.append("projection_direction_fail")
    # 5. Edge gate (≥ 0.05)
    if edge is None or edge < GATE_SPEC["edge_min"]:
        failed.append("edge_fail")

    return (not failed), failed


# ── Grading ────────────────────────────────────────────────────────────
async def _build_actual_outcomes(
    db, game_date: str,
) -> Dict[str, Dict[str, float]]:
    """Return {player_name_normalized → {stat_field → actual_value}}
    for `game_date`. One pass over master_hub."""
    out: Dict[str, Dict[str, float]] = {}
    fields_of_interest = (
        "hits", "total_bases", "runs", "rbis", "strikeouts",
        "plate_appearances", "at_bats", "walks",
        "pitcher_strikeouts", "pitcher_hits_allowed",
        "pitcher_walks", "earned_runs", "outs_recorded",
        "innings_pitched", "pitcher_outs",
    )
    proj = {
        "_id": 0, "display_name": 1, "player_name": 1, "mlb_full_name": 1,
        "bdl_game_logs": 1,
    }
    async for p in db.mlb_master_hub_2026.find({}, proj):
        nk = normalize_player_name(
            p.get("display_name") or p.get("player_name")
            or p.get("mlb_full_name"))
        if not nk:
            continue
        for g in (p.get("bdl_game_logs") or []):
            d = (g.get("date") or g.get("game_date") or "")[:10]
            if d != game_date:
                continue
            row: Dict[str, float] = {}
            for f in fields_of_interest:
                v = g.get(f)
                if v is None:
                    continue
                try:
                    row[f] = float(v)
                except (TypeError, ValueError):
                    continue
            # Composite: hits_runs_rbis
            if all(k in row for k in ("hits", "runs", "rbis")):
                row["hits_runs_rbis"] = row["hits"] + row["runs"] + row["rbis"]
            if row:
                out[nk] = row
            break
    return out


def _actual_for(
    actuals: Dict[str, Dict[str, float]],
    player_norm: str,
    stat_family: str,
) -> Optional[float]:
    pdoc = actuals.get(player_norm)
    if not pdoc:
        return None
    field = _STAT_FIELD_MAP.get(stat_family, stat_family)
    if field in pdoc:
        return pdoc[field]
    # Fallback: outs_recorded may be stored under `pitcher_outs`
    if field == "outs_recorded" and "pitcher_outs" in pdoc:
        return pdoc["pitcher_outs"]
    return None


def grade_one(
    actual: Optional[float], line: float, side: str, odds: int,
) -> Dict[str, Any]:
    """Returns dict with status (win/loss/push/ungraded) + profit units."""
    if actual is None:
        return {"status": "ungraded", "actual": None,
                "profit_units": 0.0, "stake": 0.0}
    win = (
        (side == "OVER" and actual > line) or
        (side == "UNDER" and actual < line)
    )
    push = (actual == line)
    if push:
        return {"status": "push", "actual": actual,
                "profit_units": 0.0, "stake": 1.0}
    if win:
        return {"status": "win", "actual": actual,
                "profit_units": _american_payout(odds, 1.0), "stake": 1.0}
    return {"status": "loss", "actual": actual,
            "profit_units": -1.0, "stake": 1.0}


# ── Bucketing helpers ─────────────────────────────────────────────────
def _edge_bucket(edge: Optional[float]) -> str:
    if edge is None: return "edge_na"
    if edge < 0.05:  return "edge_lt5"  # shouldn't appear for qualified rows
    if edge < 0.10:  return "edge_05_10"
    if edge < 0.20:  return "edge_10_20"
    if edge < 0.30:  return "edge_20_30"
    return "edge_30p"


def _cv_bucket(cv: Optional[float]) -> str:
    if cv is None: return "cv_na"
    if cv < 0.50:  return "cv_lt50"
    if cv < 0.75:  return "cv_50_75"
    if cv < 1.00:  return "cv_75_100"
    return "cv_100_110"  # spec caps at 1.10


def _hr_bucket(hr: Optional[float]) -> str:
    if hr is None: return "hr_na"
    if hr < 75:  return "hr_70_75"
    if hr < 85:  return "hr_75_85"
    if hr < 95:  return "hr_85_95"
    return "hr_95p"


def _odds_bucket(odds: Optional[int]) -> str:
    if odds is None: return "odds_na"
    if odds < -200: return "odds_lt_-200"
    if odds < -100: return "odds_-200_-100"
    if odds <    0: return "odds_-100_-0"
    if odds <  150: return "odds_+0_+150"
    if odds <  300: return "odds_+150_+300"
    return "odds_+300p"


# ── Indexes ────────────────────────────────────────────────────────────
async def ensure_indexes(db) -> None:
    await db[GATE_RESULTS_COLL].create_index(
        [("game_date", ASCENDING), ("event_id", ASCENDING),
         ("player_name_normalized", ASCENDING),
         ("market", ASCENDING), ("line", ASCENDING),
         ("side", ASCENDING), ("book", ASCENDING),
         ("snapshot_iso", ASCENDING),
         ("scoring_config_version", ASCENDING),
         ("gate_config_version", ASCENDING)],
        name="gate_results_compound_unique", unique=True,
    )
    await db[GATE_RESULTS_COLL].create_index(
        [("game_date", ASCENDING), ("gate_pass", ASCENDING)])
    await db[GATE_RESULTS_COLL].create_index(
        [("game_date", ASCENDING), ("gate_pass", ASCENDING),
         ("edge", ASCENDING)])
    await db[BACKTEST_RUNS_COLL].create_index(
        [("game_date_start", ASCENDING), ("game_date_end", ASCENDING),
         ("snapshot_iso", ASCENDING),
         ("scoring_config_version", ASCENDING),
         ("gate_config_version", ASCENDING),
         ("started_at", ASCENDING)],
        name="backtest_runs_unique",
    )


# ── Layer 4 entrypoint ────────────────────────────────────────────────
async def run_layer4_for_date(
    db, game_date: str, *,
    snapshot_iso: str,
    scoring_config_version: str,
    gate_config_version: str = GATE_CONFIG_VERSION,
    mem_limit_mb: int = DEFAULT_MEM_LIMIT_MB,
) -> Dict[str, Any]:
    """One-shot: evaluate gates + grade + summarise for a single date+snapshot."""
    await ensure_indexes(db)
    started_at = datetime.now(timezone.utc)
    rss0 = _rss_mb()

    # 1. Load actual outcomes ONCE.
    actuals = await _build_actual_outcomes(db, game_date)
    rss_after_actuals = _rss_mb()

    # 2. Stream replay outputs.
    cursor = db.mlb_replay_model_outputs.find(
        {"game_date": game_date, "snapshot_iso": snapshot_iso,
         "scoring_config_version": scoring_config_version},
        projection={"_id": 0},
    )

    buffer: List[Dict[str, Any]] = []
    seen = 0
    n_pass = 0
    n_fail = 0
    fail_counter: Counter = Counter()
    rss_peak = max(rss0, rss_after_actuals)

    # Buckets for backtest summary
    qualified_rows: List[Dict[str, Any]] = []

    async def _flush():
        nonlocal buffer
        if not buffer:
            return
        ops = []
        key_fields = (
            "game_date", "event_id", "player_name_normalized", "market",
            "line", "side", "book", "snapshot_iso",
            "scoring_config_version", "gate_config_version",
        )
        for r in buffer:
            f = {k: r[k] for k in key_fields}
            ops.append(UpdateOne(f, {"$set": r}, upsert=True))
        try:
            await db[GATE_RESULTS_COLL].bulk_write(ops, ordered=False)
        except Exception as exc:  # noqa: BLE001
            logger.error("[layer4] bulk_write failed: %s", exc)
        buffer.clear()

    async for r in cursor:
        seen += 1
        passed, failed = evaluate_gates(r)
        if passed:
            n_pass += 1
        else:
            n_fail += 1
            for fg in failed:
                fail_counter[fg] += 1

        # Grade qualified rows
        graded = {"status": "ungated", "actual": None,
                  "profit_units": 0.0, "stake": 0.0}
        if passed:
            actual = _actual_for(actuals, r["player_name_normalized"],
                                  r["stat_family"])
            graded = grade_one(actual, float(r["line"]), r["side"],
                                int(r["odds"]))

        gr = {
            # Identity
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
            # Market
            "market": r["market"], "is_alternate": r.get("is_alternate"),
            "stat_family": r["stat_family"],
            "line": float(r["line"]), "side": r["side"],
            "book": r["book"], "odds": int(r["odds"]),
            # Model snapshot
            "projection_mu": r["projection_mu"], "sigma": r["sigma"],
            "model_probability": r["model_probability"],
            "fair_probability": r["fair_probability"],
            "implied_probability": r["implied_probability"],
            "edge": r["edge"],
            "hit_rate_l5":  r.get("hit_rate_l5"),
            "hit_rate_l10": r.get("hit_rate_l10"),
            "hit_rate_l20": r.get("hit_rate_l20"),
            "cv": r.get("cv"),
            # Gate
            "gate_pass": passed,
            "failed_gates": failed,
            "gate_config_version": gate_config_version,
            "scoring_config_version": scoring_config_version,
            # Grading
            "grade_status": graded["status"],
            "actual": graded["actual"],
            "profit_units": graded["profit_units"],
            "stake_units": graded["stake"],
            "evaluated_at": datetime.now(timezone.utc),
        }
        buffer.append(gr)
        if passed:
            qualified_rows.append(gr)

        if len(buffer) >= 500:
            await _flush()
            rss = _rss_mb()
            if rss > rss_peak:
                rss_peak = rss
            if rss > mem_limit_mb:
                raise MemoryError(f"Layer4 RSS {rss:.1f} > {mem_limit_mb}")
    await _flush()

    # ── Backtest summary ─────────────────────────────────────────────
    def _summary_of(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        graded = [r for r in rows
                  if r["grade_status"] in ("win", "loss", "push")]
        wins = sum(1 for r in graded if r["grade_status"] == "win")
        losses = sum(1 for r in graded if r["grade_status"] == "loss")
        pushes = sum(1 for r in graded if r["grade_status"] == "push")
        ungraded = len(rows) - len(graded)
        graded_decisions = wins + losses  # ignore pushes for hit rate
        hit_rate = (wins / graded_decisions * 100.0) if graded_decisions else None
        profit = sum(r["profit_units"] for r in graded)
        stake = sum(r["stake_units"] for r in graded)
        roi = (profit / stake * 100.0) if stake else None
        odds_list = [int(r["odds"]) for r in rows]
        return {
            "total": len(rows),
            "wins": wins, "losses": losses, "pushes": pushes,
            "ungraded": ungraded,
            "hit_rate_pct": hit_rate,
            "profit_units": profit, "stake_units": stake,
            "roi_pct": roi,
            "avg_odds": (sum(odds_list) / len(odds_list)) if odds_list else None,
            "median_odds": statistics.median(odds_list) if odds_list else None,
        }

    overall = _summary_of(qualified_rows)
    by_family = {fam: _summary_of(
        [r for r in qualified_rows if r["stat_family"] == fam]
    ) for fam in {r["stat_family"] for r in qualified_rows}}
    by_book = {bk: _summary_of(
        [r for r in qualified_rows if r["book"] == bk]
    ) for bk in {r["book"] for r in qualified_rows}}
    by_market_type = {
        "alternate": _summary_of([r for r in qualified_rows if r["is_alternate"]]),
        "standard":  _summary_of([r for r in qualified_rows if not r["is_alternate"]]),
    }
    by_edge = defaultdict(list)
    by_cv = defaultdict(list)
    by_hr = defaultdict(list)
    by_odds = defaultdict(list)
    by_line = defaultdict(list)
    for r in qualified_rows:
        by_edge[_edge_bucket(r["edge"])].append(r)
        by_cv[_cv_bucket(r["cv"])].append(r)
        by_hr[_hr_bucket(r["hit_rate_l20"])].append(r)
        by_odds[_odds_bucket(r["odds"])].append(r)
        by_line[str(r["line"])].append(r)

    finished_at = datetime.now(timezone.utc)
    summary_doc = {
        "game_date_start": game_date, "game_date_end": game_date,
        "snapshot_iso": snapshot_iso,
        "scoring_config_version": scoring_config_version,
        "gate_config_version": gate_config_version,
        "gate_spec": GATE_SPEC,
        "started_at": started_at, "finished_at": finished_at,
        "elapsed_s": (finished_at - started_at).total_seconds(),
        "rss_mb_start": round(rss0, 1),
        "rss_mb_after_actuals_load": round(rss_after_actuals, 1),
        "rss_mb_peak": round(rss_peak, 1),
        "rss_mb_end": round(_rss_mb(), 1),
        # Counts
        "rows_scanned": seen,
        "gate_pass": n_pass, "gate_fail": n_fail,
        "failed_gate_breakdown": dict(fail_counter),
        # Backtest summaries
        "overall": overall,
        "by_stat_family": by_family,
        "by_book": by_book,
        "by_market_type": by_market_type,
        "by_edge_bucket": {k: _summary_of(v) for k, v in by_edge.items()},
        "by_cv_bucket":   {k: _summary_of(v) for k, v in by_cv.items()},
        "by_hr_bucket":   {k: _summary_of(v) for k, v in by_hr.items()},
        "by_odds_bucket": {k: _summary_of(v) for k, v in by_odds.items()},
        "by_line":        {k: _summary_of(v) for k, v in by_line.items()},
    }
    await db[BACKTEST_RUNS_COLL].insert_one(dict(summary_doc))
    summary_doc.pop("_id", None)
    return summary_doc


__all__ = [
    "GATE_CONFIG_VERSION", "GATE_SPEC",
    "GATE_RESULTS_COLL", "BACKTEST_RUNS_COLL",
    "evaluate_gates", "grade_one", "run_layer4_for_date",
    "ensure_indexes",
]
