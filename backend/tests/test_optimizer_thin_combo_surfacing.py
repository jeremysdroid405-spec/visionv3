"""
Thin-combo surfacing tests — pin the user's actual strategy.

The user is hunting for thin-but-consistent edges (e.g., 5–11 bets
in a single month at high `daily_consistency`, recurring across
months). The optimizer must NOT bury those under fatter losing
samples just because of sample size.

These tests pin:
  1. Default `min_bets` is 3 (not 15 / not 30).
  2. A thin winning combo (n=5, hr=80%, roi=+30%) MUST outrank a
     fat losing combo (n=50, hr=44%, roi=-12%) on `balanced` goal.
  3. The sample-size penalty zeros out once n ≥ max(min_bets, 10).
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/app/backend")

from routes.emergent_admin.optimizer import OptimizerRunBody, _score


def test_default_min_bets_is_three():
    body = OptimizerRunBody(start="2025-05-01", end="2025-05-31")
    assert body.min_bets == 3, (
        f"Default min_bets must be 3 to surface thin combos; got "
        f"{body.min_bets}")


def _balanced(metrics, baseline=10):
    return _score(metrics, "balanced", baseline_n=baseline)


def test_thin_winning_combo_outranks_fat_losing_combo():
    """5-bet @ 80% HR, +30% ROI MUST beat 50-bet @ 44% HR, -12% ROI."""
    thin_winner = {
        "n_bets": 5, "n_graded": 5, "hit_rate": 0.80, "roi": 0.30,
        "calibration_delta": 0.05, "daily_consistency": 1.0,
        "max_drawdown_units": 0.5,
    }
    fat_loser = {
        "n_bets": 50, "n_graded": 50, "hit_rate": 0.44, "roi": -0.12,
        "calibration_delta": -0.06, "daily_consistency": 0.30,
        "max_drawdown_units": 6.0,
    }
    s_thin = _balanced(thin_winner, baseline=10)
    s_fat  = _balanced(fat_loser,   baseline=10)
    assert s_thin > s_fat, (
        f"thin winner ({s_thin:.3f}) must beat fat loser ({s_fat:.3f})")


def test_sample_penalty_is_zero_at_or_above_baseline():
    """For n ≥ baseline_n the sample penalty must not subtract."""
    m = {
        "n_bets": 50, "n_graded": 50, "hit_rate": 0.60, "roi": 0.10,
        "calibration_delta": 0.0, "daily_consistency": 0.5,
        "max_drawdown_units": 0.0,
    }
    # Same metrics, different n. With n=50 and baseline=10 there
    # must be no penalty differential vs n=200.
    s50  = _balanced({**m, "n_bets": 50},  baseline=10)
    s200 = _balanced({**m, "n_bets": 200}, baseline=10)
    assert abs(s50 - s200) < 1e-9, (
        f"sample penalty must zero out once n ≥ baseline; "
        f"got s50={s50}, s200={s200}")


def test_thin_sample_penalty_is_mild_not_crushing():
    """A 3-bet combo's penalty must be a nudge (< 0.3), not the
    -1.22 penalty that the old `coefficient=1.0` baseline=50 formula
    produced. Otherwise no thin combo can ever rank #1."""
    m = {
        "n_bets": 3, "n_graded": 3, "hit_rate": 0.66, "roi": 0.20,
        "calibration_delta": 0.05, "daily_consistency": 1.0,
        "max_drawdown_units": 0.0,
    }
    s_thin   = _balanced(m, baseline=10)
    # Same combo with n=10 (no penalty)
    s_at_bln = _balanced({**m, "n_bets": 10}, baseline=10)
    penalty = s_at_bln - s_thin
    assert 0 <= penalty < 0.30, (
        f"sample penalty for n=3 vs n=10 should be a gentle nudge "
        f"(< 0.30), got {penalty:.3f}")


def test_consistency_dominates_over_sample_size_for_thin_combos():
    """The user's strategy depends on consistent thin combos. A
    thin combo with consistency=1.0 must beat a thin combo with
    same n + ROI but consistency=0.0."""
    base = {
        "n_bets": 5, "n_graded": 5, "hit_rate": 0.60, "roi": 0.10,
        "calibration_delta": 0.0, "max_drawdown_units": 0.0,
    }
    s_consistent  = _balanced({**base, "daily_consistency": 1.0},
                                       baseline=10)
    s_inconsistent = _balanced({**base, "daily_consistency": 0.0},
                                       baseline=10)
    assert s_consistent > s_inconsistent + 1.0, (
        f"consistency must materially differentiate thin combos; "
        f"got {s_consistent:.3f} vs {s_inconsistent:.3f}")
