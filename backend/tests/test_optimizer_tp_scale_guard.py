"""
TP-scale defense-in-depth guard for the optimizer aggregation
(2026-06-02).

ROOT CAUSE this pins:
  The historical replay collection (`sgo_propvision_full_pipeline_replay`)
  carried a small population of legacy rows where `tp` was written in
  PERCENT scale (e.g. 49.43) instead of the post-2026-05-27 mirror
  contract of PROBABILITY scale (e.g. 0.4943). When the optimizer's
  `_evaluate_combo` averages `tp` across raw rows that mix both
  scales, `avg_tp` blows up from ~0.45 to ~25, which makes the
  calibration_delta meaningless and lets stale-data configs win the
  ranking on a non-feature.

CONTRACT this test pins:
  In `_evaluate_combo` the `tp` accumulator MUST defensively divide
  by 100 when a single row's `tp` value is > 1.5. The threshold uses
  1.5 (not just 1) so a model that fairly outputs `tp = 1.0`
  (degenerate sure-thing edge case) doesn't get clobbered by the
  guard. Only tp values that clearly cannot be probabilities are
  rescaled.
"""
from __future__ import annotations
import sys

import pytest

sys.path.insert(0, "/app/backend")
from routes.emergent_admin.optimizer import _evaluate_combo  # noqa: E402


def _row(tp_value, *, outcome=1, odds=120):
    """Build a minimal replay row that passes a wide-open combo."""
    return {
        "event_id":  "E1",
        "player_name_normalized": "p",
        "market":    "m",
        "side":      "OVER",
        "line":      1.5,
        "game_date": "2025-06-15",
        "tp":        tp_value,
        "cv":        0.2,
        "edge":      0.05,
        "odds":      odds,
        "outcome_numeric": outcome,
    }


def test_percent_scale_tp_is_normalized_in_avg() -> None:
    """A row with tp=49.43 (percent) must be averaged as 0.4943."""
    # One probability-scale row + one percent-scale row.
    rows = [_row(0.50, outcome=1, odds=120),
            _row(49.43, outcome=1, odds=120)]
    # Bypass per-bet dedupe by giving each row a different event_id
    rows[1]["event_id"] = "E2"
    m = _evaluate_combo(rows, combo={}, min_bets=1)
    assert m is not None
    # Expected avg = (0.50 + 0.4943) / 2 = 0.4972  (within float tolerance)
    assert abs(m["avg_tp"] - 0.4972) < 1e-3, (
        f"avg_tp={m['avg_tp']} — guard failed to rescale 49.43 → 0.4943"
    )


def test_probability_scale_tp_is_NOT_rescaled() -> None:
    """A row with tp=0.85 (probability) MUST stay 0.85, not become 0.0085."""
    rows = [_row(0.85, outcome=1, odds=120)]
    m = _evaluate_combo(rows, combo={}, min_bets=1)
    assert m is not None
    assert abs(m["avg_tp"] - 0.85) < 1e-9, (
        f"avg_tp={m['avg_tp']} — probability-scale tp was incorrectly rescaled"
    )


def test_tp_of_one_point_zero_is_NOT_rescaled() -> None:
    """Boundary: tp=1.0 is the degenerate-sure-thing edge; do NOT rescale.

    The guard threshold is intentionally 1.5 so legitimate
    boundary-value `tp=1.0` rows (rare but possible from a model that
    converges to a sure thing) survive unchanged.
    """
    rows = [_row(1.0, outcome=1, odds=120)]
    m = _evaluate_combo(rows, combo={}, min_bets=1)
    assert m is not None
    assert abs(m["avg_tp"] - 1.0) < 1e-9
