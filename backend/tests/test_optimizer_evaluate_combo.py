"""
Unit tests for the optimizer cell evaluator.

Locks in three behaviours that diagnose the "metrics null/zero" failure
the operator reported when replay rows lack `outcome_numeric`:

  1. When every row is ungraded → hit_rate=None (not 0).
  2. n_graded, n_ungraded, n_with_odds, n_with_payout are populated.
  3. ROI uses n_with_payout, NOT n. (Otherwise a few graded rows in a
     large ungraded pool look like ROI≈0, which is what tripped the
     operator up in the first place.)
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/app/backend")

from routes.emergent_admin.optimizer import _evaluate_combo


def _row(outcome, odds=-110, **kwargs):
    base = {
        "outcome_numeric": outcome, "odds": odds,
        "hit_rate_l20": 0.70, "hit_rate_l10": 0.70, "hit_rate_l5": 0.70,
        "cv": 0.4, "edge": 0.10, "model_probability": 0.60, "tp": 0.60,
        "game_date": "2025-04-01",
    }
    base.update(kwargs)
    return base


def _empty_combo():
    return {"hr_l20_min": 0.5, "hr_l5_min": 0.5, "cv_max": 1.5,
              "edge_min": 0.0, "tp_min": 0.5}


def test_evaluate_combo_returns_none_below_min_bets():
    rows = [_row(1), _row(0)]
    assert _evaluate_combo(rows, _empty_combo(), min_bets=10) is None


def test_evaluate_combo_all_ungraded_returns_null_hit_rate():
    """Reported failure: every replay row had outcome_numeric=None
    but cells still showed n_bets > 0. We must NOT collapse to 0.0
    hit_rate — that masked the upstream join bug. None signals
    "unknown" so the UI shows the em-dash placeholder."""
    rows = [_row(None) for _ in range(50)]
    metrics = _evaluate_combo(rows, _empty_combo(), min_bets=20)
    assert metrics is not None
    assert metrics["n_bets"] == 50
    assert metrics["n_graded"] == 0
    assert metrics["n_ungraded"] == 50
    assert metrics["wins"] == 0
    assert metrics["losses"] == 0
    assert metrics["hit_rate"] is None
    assert metrics["roi"] is None         # NOT 0.0
    assert metrics["calibration_delta"] is None


def test_evaluate_combo_mixed_grades_uses_payout_denominator():
    # 10 wins @ +100 = +10u, 10 losses = -10u, 30 ungraded
    rows = ([_row(1, odds=+100) for _ in range(10)]
              + [_row(0, odds=+100) for _ in range(10)]
              + [_row(None, odds=+100) for _ in range(30)])
    m = _evaluate_combo(rows, _empty_combo(), min_bets=20)
    assert m["n_bets"] == 50
    assert m["n_graded"] == 20
    assert m["n_ungraded"] == 30
    assert m["n_with_payout"] == 20
    assert m["hit_rate"] == 0.5
    # ROI must divide by graded (20), NOT total (50). 0u / 20 = 0.0
    assert m["roi"] == 0.0
    # Pushes don't contribute pnl
    assert abs(m["profit_units"]) < 1e-9


def test_evaluate_combo_diagnostic_fields_present():
    """The optimizer cell schema MUST surface n_graded / n_ungraded /
    n_with_odds / n_with_payout. The Admin UI keys off these to label
    the warning pill 'X/Y graded'. If they ever disappear, the failure
    mode silently returns."""
    rows = [_row(1), _row(0), _row(None)] * 10  # 30 rows
    m = _evaluate_combo(rows, _empty_combo(), min_bets=20)
    for k in ("n_bets", "n_graded", "n_ungraded",
                "n_with_odds", "n_with_payout"):
        assert k in m, f"missing diagnostic field: {k}"


def test_evaluate_combo_missing_odds_doesnt_break_grading():
    # outcome_numeric present but no odds → counted as graded but
    # contributes nothing to pnl / ROI denominator.
    rows = [_row(1, odds=None) for _ in range(20)]
    m = _evaluate_combo(rows, _empty_combo(), min_bets=10)
    assert m["n_bets"] == 20
    assert m["n_graded"] == 20
    assert m["n_with_odds"] == 0
    assert m["n_with_payout"] == 0
    assert m["hit_rate"] == 1.0
    assert m["roi"] is None     # 0 / 0 = None, NOT 0.0
