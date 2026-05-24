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


# ── _score must return None for ungradable cells ───────────────────
# (the "Top 25 all empty" bug fix)
from routes.emergent_admin.optimizer import _score  # noqa: E402


def test_score_returns_none_when_no_graded_rows():
    """The earlier bug: when every row in a cell was ungraded,
    _evaluate_combo returned metrics with hit_rate=None and ROI=None
    (correctly), but _score did `hr or 0.0` + `roi or 0.0` and
    produced 0.0 for the cell, which outranked legitimately-graded
    cells with negative scores. Fix asserts: ungraded → None."""
    metrics = {"n_bets": 80, "n_graded": 0, "n_ungraded": 80,
                  "wins": 0, "losses": 0, "pushes": 0,
                  "hit_rate": None, "roi": None,
                  "calibration_delta": None,
                  "daily_consistency": 0, "max_drawdown_units": 0,
                  "profit_units": 0, "n_days": 0,
                  "avg_tp": 0.6, "avg_cv": 0.4, "avg_edge": 0.05}
    assert _score(metrics, "balanced", baseline_n=50) is None
    assert _score(metrics, "hit_rate", baseline_n=50) is None
    assert _score(metrics, "roi", baseline_n=50) is None


def test_score_is_finite_when_some_rows_graded():
    """A real (negative or positive) score should still be returned
    when ≥1 row is graded."""
    metrics = {"n_bets": 80, "n_graded": 20, "n_ungraded": 60,
                  "wins": 5, "losses": 15, "pushes": 0,
                  "hit_rate": 0.25, "roi": -0.20,
                  "calibration_delta": -0.10,
                  "daily_consistency": 0.5, "max_drawdown_units": 8.0,
                  "profit_units": -4.0, "n_days": 10,
                  "avg_tp": 0.6, "avg_cv": 0.4, "avg_edge": 0.05,
                  "n_with_payout": 20}
    s = _score(metrics, "balanced", baseline_n=50)
    assert s is not None and isinstance(s, float)
    # And it should be NEGATIVE (bad performance) — sanity-check that
    # the score discriminates against losing cells (i.e. they cannot
    # be tied with ungradable score=None).
    assert s < 0


def test_score_returns_none_for_legacy_pre_diagnostic_shape():
    """Backwards-compat: if a saved cell predates the `n_graded`
    field, treat hit_rate=None AND roi=None as ungradable."""
    metrics = {"n_bets": 80, "hit_rate": None, "roi": None,
                  "calibration_delta": None}
    assert _score(metrics, "balanced", baseline_n=50) is None


# ── Daily consistency must be bounded ─────────────────────────────
def test_daily_consistency_is_proportion_of_profitable_days():
    """The previous formula (1 - stddev/|mean|) blew up to ~-100 when
    daily mean was near zero, destroying the ranking. New definition:
    proportion of days with positive net PnL ∈ [0, 1]."""
    # 5 wins @ +100 odds = +1u, 5 losses = -1u, spread across 4 days
    # so days look like [+2, -1, +1, -1] — 50% profitable.
    rows = []
    for i in range(5):
        rows.append(_row(1, odds=+100, game_date=f"2025-04-0{(i%4)+1}"))
    for i in range(5):
        rows.append(_row(0, odds=+100, game_date=f"2025-04-0{(i%4)+1}"))
    m = _evaluate_combo(rows, _empty_combo(), min_bets=5)
    assert m["daily_consistency"] is not None
    assert 0.0 <= m["daily_consistency"] <= 1.0


def test_daily_consistency_one_when_every_day_profitable():
    rows = [_row(1, odds=+100, game_date=f"2025-04-0{(i%5)+1}") for i in range(10)]
    m = _evaluate_combo(rows, _empty_combo(), min_bets=5)
    assert m["daily_consistency"] == 1.0


def test_daily_consistency_zero_when_no_day_profitable():
    rows = [_row(0, odds=+100, game_date=f"2025-04-0{(i%5)+1}") for i in range(10)]
    m = _evaluate_combo(rows, _empty_combo(), min_bets=5)
    assert m["daily_consistency"] == 0.0


def test_score_clamps_legacy_unbounded_consistency():
    """If an old cell document with the broken consistency value
    (e.g. -99.72) is replayed through `_score`, the clamp must
    prevent it from dominating the ranking."""
    legacy_metrics = {"n_bets": 32, "n_graded": 30,
                          "wins": 16, "losses": 14, "pushes": 0,
                          "hit_rate": 0.533, "roi": -0.008,
                          "calibration_delta": -0.153,
                          "daily_consistency": -99.72,   # ← legacy bad
                          "max_drawdown_units": 6.1,
                          "profit_units": -0.25,
                          "n_with_payout": 30}
    s = _score(legacy_metrics, "balanced", baseline_n=50)
    assert s is not None
    # Without the clamp, score would be ~ -150. With the clamp,
    # consistency contributes 0 and the score should be small in
    # magnitude (driven mostly by cal_score and roi_score).
    assert -10.0 < s < 10.0, f"score {s} not clamped"
