"""
Replay Outcome Resolver — joins `replay_evaluations` × `replay_results`
to produce `replay_outcomes` rows.

Settlement math is pure-functional and lives in this module so it can
be unit-tested without a database.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


REPLAY_OUTCOMES = "replay_outcomes"
REPLAY_EVALUATIONS = "replay_evaluations"
REPLAY_RESULTS = "replay_results"


# ---------------------------------------------------------------- math
# Family → field on the canonical replay_results doc
STAT_FAMILY_TO_FIELD: Dict[str, str] = {
    "PTS":     "pts",
    "REB":     "reb",
    "AST":     "ast",
    "THREES":  "fg3m",
    "BLK":     "blk",
    "STL":     "stl",
    "PTS_REB": "pr",
    "PTS_AST": "pa",
    "REB_AST": "ra",
    "PRA":     "pra",
}


def settle(side: str, line: float, actual_value: Optional[float],
           did_play: bool) -> str:
    """Pure settlement: returns 'hit' / 'miss' / 'push' / 'void_dnp'."""
    if not did_play and (actual_value is None or actual_value == 0):
        return "void_dnp"
    if actual_value is None:
        return "void_dnp"
    side_u = (side or "").upper()
    if side_u == "OVER":
        if actual_value > line:
            return "hit"
        if actual_value == line:
            return "push"
        return "miss"
    if side_u == "UNDER":
        if actual_value < line:
            return "hit"
        if actual_value == line:
            return "push"
        return "miss"
    raise ValueError(f"unsupported side: {side!r}")


def realized_payout(outcome: str, odds_american: int) -> float:
    """Returns net P&L per $1 staked. push/void = 0; miss = -1."""
    if outcome == "void_dnp":
        return 0.0
    if outcome == "push":
        return 0.0
    if outcome == "miss":
        return -1.0
    if outcome != "hit":
        raise ValueError(f"unsupported outcome: {outcome!r}")
    if odds_american > 0:
        return odds_american / 100.0
    if odds_american < 0:
        return 100.0 / (-odds_american)
    raise ValueError("odds_american cannot be 0")


def implied_probability(odds_american: int) -> float:
    if odds_american > 0:
        return round(100.0 / (odds_american + 100.0), 6)
    if odds_american < 0:
        return round((-odds_american) / ((-odds_american) + 100.0), 6)
    raise ValueError("odds_american cannot be 0")


def calibration_gap(p_true_active: Optional[float], outcome: str
                     ) -> Optional[float]:
    """Difference between predicted probability and realized indicator.
    Returns None for void/push (those are not learning signal).
    """
    if p_true_active is None:
        return None
    if outcome == "hit":
        realized = 1.0
    elif outcome == "miss":
        realized = 0.0
    else:
        return None
    return round(p_true_active - realized, 6)


def closing_line_value(
    entry_implied_prob: Optional[float],
    closing_implied_prob: Optional[float],
) -> Optional[float]:
    """Positive CLV = entry probability beat the closing line.
    Returns delta in probability points (e.g. 0.05 = +5 pp)."""
    if entry_implied_prob is None or closing_implied_prob is None:
        return None
    return round(entry_implied_prob - closing_implied_prob, 6)


# ---------------------------------------------------------------- IO
def build_outcome_row(
    *,
    evaluation: Dict[str, Any],
    result: Optional[Dict[str, Any]],
    closing_implied_prob: Optional[float] = None,
) -> Dict[str, Any]:
    """Compose a `replay_outcomes` row from one evaluation + its result."""
    family = evaluation.get("stat_family")
    actual_field = STAT_FAMILY_TO_FIELD.get(family)
    actual_value: Optional[float] = None
    did_play = bool(result and result.get("did_play"))
    if result and actual_field:
        actual_value = result.get(actual_field)

    side = evaluation.get("side") or evaluation.get("recommendation")
    line = evaluation.get("line")
    odds = evaluation.get("odds_american")
    outcome = (settle(side, line, actual_value, did_play)
               if (side and line is not None)
               else "void_dnp")
    payout = (realized_payout(outcome, odds) if odds is not None else 0.0)
    entry_ip = (implied_probability(odds) if odds is not None else None)

    return {
        "replay_run_id":    evaluation.get("replay_run_id"),
        "event_id":         evaluation.get("event_id"),
        "canonical_key":    evaluation.get("canonical_key"),
        "snapshot_label":   evaluation.get("snapshot_label"),
        "snapshot_ts":      evaluation.get("snapshot_ts"),
        "bookmaker":        evaluation.get("bookmaker"),
        "player":           evaluation.get("player"),
        "stat_family":      family,
        "is_alternate":     evaluation.get("is_alternate"),
        "is_combo":         evaluation.get("is_combo"),
        "side":             side,
        "line":             line,
        "odds_american":    odds,
        "implied_probability": entry_ip,
        "p_true_active":    evaluation.get("p_true_active"),
        "edge_vs_fair":     evaluation.get("edge_vs_fair"),
        "tier_at_eval":     evaluation.get("tier"),
        "vision_score":     evaluation.get("vision_score"),
        "vision_score_v2":  evaluation.get("vision_score_v2"),
        "actual_value":     actual_value,
        "did_play":         did_play,
        "outcome":          outcome,
        "pnl_units":        payout,
        "roi":              payout,           # 1u stake → roi == payout
        "calibration_gap":  calibration_gap(
            evaluation.get("p_true_active"), outcome),
        "clv":              closing_line_value(
            entry_ip, closing_implied_prob),
        "closing_implied_prob": closing_implied_prob,
        "resolved_at":      datetime.now(timezone.utc),
    }


# ---------------------------------------------------------------- driver
async def ensure_outcome_indexes(db) -> List[str]:
    coll = db[REPLAY_OUTCOMES]
    out = []
    out.append(await coll.create_index(
        [("replay_run_id", 1), ("canonical_key", 1), ("snapshot_label", 1),
         ("bookmaker", 1), ("side", 1)],
        name="uniq_run_canonical_snap_book_side", unique=True))
    out.append(await coll.create_index(
        [("replay_run_id", 1), ("outcome", 1)], name="run_outcome"))
    out.append(await coll.create_index(
        [("replay_run_id", 1), ("tier_at_eval", 1)], name="run_tier"))
    return out


__all__ = [
    "REPLAY_OUTCOMES", "REPLAY_EVALUATIONS", "REPLAY_RESULTS",
    "STAT_FAMILY_TO_FIELD",
    "settle", "realized_payout", "implied_probability",
    "calibration_gap", "closing_line_value", "build_outcome_row",
    "ensure_outcome_indexes",
]
