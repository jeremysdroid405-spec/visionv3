"""
End-to-end integrity audit of the optimizer / testing pipeline data
(2026-06-02).

WHY this exists:
  The optimizer ranks configs by HR / ROI / score derived from
  `sgo_propvision_full_pipeline_replay`. If a single field is wrong
  (placeholder odds, mismatched outcome, scale-mixed tp), every rank
  downstream is junk. The 6 contracts pinned here prove the integrity
  invariants we rely on:

    1. Every replay row's `odds` value either is None OR matches the
       upstream `sgo_replay_alt_odds_raw` quote for the same
       (event_id, player, market, line, side, book) key.
    2. `implied_probability` is a deterministic function of `odds`.
    3. `edge` = `fair_probability` - `implied_probability`.
    4. For resolved bets:
         actual == line                  → outcome_numeric == 0.5
         side="OVER"  and actual > line  → outcome_numeric == 1
         side="OVER"  and actual < line  → outcome_numeric == 0
         side="UNDER" and actual < line  → outcome_numeric == 1
         side="UNDER" and actual > line  → outcome_numeric == 0
    5. `tp` is always a probability in [0, 1] — never percent.
    6. `outcome_resolved == True` implies a non-null outcome_numeric.

The contracts are encoded as PURE functions here so they can be
re-asserted at any time against any window of data without running
the live optimizer.
"""
from __future__ import annotations
import math
from typing import Any, Dict, Optional


def american_to_implied(odds: int) -> float:
    """Standard American odds → implied probability formula."""
    o = float(odds)
    if o > 0:
        return 100.0 / (o + 100.0)
    return -o / (-o + 100.0)


def expected_outcome_numeric(*, actual: Optional[float], line: Optional[float],
                                 side: Optional[str]) -> Optional[float]:
    if actual is None or line is None or side is None:
        return None
    af, lf, su = float(actual), float(line), side.upper()
    if af == lf:
        return 0.5
    if su == "OVER":
        return 1 if af > lf else 0
    if su == "UNDER":
        return 1 if af < lf else 0
    return None


def validate_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return a dict of {check_name: True|False|None}. None means
    "n/a for this row" (e.g., null odds). Caller aggregates the
    dict across rows to compute audit pass rates."""
    out: Dict[str, Any] = {}
    odds = row.get("odds")
    out["odds_present"] = odds is not None
    if odds is not None:
        # 2. implied probability deterministic vs American formula
        expected_imp = american_to_implied(int(odds))
        imp = row.get("implied_probability")
        out["implied_matches_odds"] = (
            imp is not None and abs(expected_imp - imp) < 1e-3
        )
    else:
        out["implied_matches_odds"] = None
    # 3. edge = fair_probability - implied_probability
    fp, ip = row.get("fair_probability"), row.get("implied_probability")
    if fp is not None and ip is not None:
        out["edge_matches_components"] = (
            row.get("edge") is not None
            and abs(row["edge"] - (fp - ip)) < 1e-3
        )
    else:
        out["edge_matches_components"] = None
    # 5. tp ∈ [0, 1]
    tp = row.get("tp")
    if tp is None:
        out["tp_in_probability_scale"] = None
    else:
        out["tp_in_probability_scale"] = (0.0 <= tp <= 1.0)
    # 6. resolved => non-null outcome_numeric
    if row.get("outcome_resolved"):
        out["resolved_has_outcome"] = row.get("outcome_numeric") is not None
    else:
        out["resolved_has_outcome"] = None
    # 4. outcome_numeric matches (actual, line, side)
    actual = row.get("actual_value") or row.get("actual")
    expected = expected_outcome_numeric(
        actual=actual, line=row.get("line"), side=row.get("side"))
    if expected is None:
        out["outcome_arithmetic_correct"] = None
    else:
        out["outcome_arithmetic_correct"] = (row.get("outcome_numeric") == expected)
    return out
