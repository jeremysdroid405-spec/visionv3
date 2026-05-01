"""Regression tests for `services.scoring.mlb_statcast_bayes`.

These tests lock in the math behind the 2026-04-30 fix for the HF
MLB model's wildly inflated projections on tiny-sample Statcast
windows.

What's locked in:
  INV-BS1: At N=0, shrunk = league_avg exactly.
  INV-BS2: At N→∞, shrunk → observed_rate (limit).
  INV-BS3: Shrunk rate stays bounded by observed and league_avg.
  INV-BS4: Bleday's actual data (1 BBE, 1.0 barrel_rate) shrinks
           into the realistic MLB range.
  INV-BS5: prior_n=0 returns raw observed_rate (escape hatch).
  INV-BS6: bayes_shrink_rolling_window selects the right denominator
           (PA vs BBE) per feature.
"""
from __future__ import annotations

import math

import pytest

from services.scoring.mlb_statcast_bayes import (
    DEFAULT_PRIOR_N,
    LEAGUE_AVERAGES,
    bayes_shrink_rolling_window,
    shrink_rate,
)


# ─── INV-BS1: zero sample collapses to prior ─────────────────────────
def test_inv_bs1_zero_sample_returns_league_avg():
    """When n_observed=0, the prior is the only evidence — must
    return league_avg exactly."""
    assert shrink_rate(0.5, 0, league_avg=0.31, prior_n=30) == 0.31
    assert shrink_rate(None, 0, league_avg=0.08, prior_n=15) == 0.08
    # n=None also collapses to the prior
    assert shrink_rate(0.99, None, league_avg=0.5, prior_n=10) == 0.5


# ─── INV-BS2: large sample dominates the prior ───────────────────────
def test_inv_bs2_large_sample_approaches_observed_rate():
    """At very large n, shrunk rate → observed_rate. The prior
    becomes statistically irrelevant.

    Concrete: with prior_n=30 and n=1000, weight on observed is
    1000/(1000+30) = 97% — well within rounding of the observed rate.
    """
    observed = 0.42
    league_avg = 0.20
    shrunk = shrink_rate(observed, 1000, league_avg=league_avg, prior_n=30)
    assert math.isclose(shrunk, observed, abs_tol=0.01), (
        f"At n=1000 shrunk rate ({shrunk}) should be within 0.01 of "
        f"observed ({observed}). Otherwise the prior is overweight at "
        f"large samples."
    )


# ─── INV-BS3: shrunk rate stays bounded ──────────────────────────────
def test_inv_bs3_shrunk_rate_bounded_by_observed_and_prior():
    """The shrunk rate is a CONVEX COMBINATION of observed and
    league_avg — by construction it MUST stay between them.
    Catches a sign-flip or formula bug that could push the result
    outside the realistic range.
    """
    observed = 1.0  # Bleday's 1.0 barrel_rate
    league_avg = 0.08
    for n in (1, 5, 20, 50, 100):
        shrunk = shrink_rate(observed, n, league_avg=league_avg, prior_n=15)
        assert min(observed, league_avg) <= shrunk <= max(observed, league_avg), (
            f"At n={n}, shrunk={shrunk} escaped the [league_avg, observed] "
            f"interval [{league_avg}, {observed}]."
        )


# ─── INV-BS4: Bleday's actual data shrinks into realistic range ──────
def test_inv_bs4_bleday_barrel_rate_shrinks_to_realistic():
    """The exact Bleday case from 2026-04-30: 1 BBE, observed
    barrel_rate=1.0. With prior_n=15 (default for BBE-denominated
    rates) and league_avg=0.08:
        shrunk = (1.0 * 1 + 0.08 * 15) / (1 + 15)
               = (1.0 + 1.2) / 16
               = 0.1375
    This is well within the realistic MLB range (top hitters cap
    around 0.20-0.25). The model trained on healthy averages will
    NOT extrapolate wildly on this value.
    """
    league_avg = LEAGUE_AVERAGES["barrel_rate"]
    prior_n = DEFAULT_PRIOR_N["barrel_rate"]
    shrunk = shrink_rate(1.0, 1, league_avg=league_avg, prior_n=prior_n)
    assert 0.10 <= shrunk <= 0.20, (
        f"Bleday's 1-BBE barrel_rate must shrink to a realistic value "
        f"(target ≤0.20, MLB-leader range). Got {shrunk:.4f}. "
        f"If this assertion fires the prior_n is too weak — the model "
        f"would still see an out-of-distribution value."
    )
    # And specifically: the math.
    expected = (1.0 * 1 + league_avg * prior_n) / (1 + prior_n)
    assert math.isclose(shrunk, expected, abs_tol=1e-9)


def test_inv_bs4b_bleday_woba_shrinks_to_realistic():
    """Same player, wOBA: 1 PA at 1.35 → must shrink to MLB-realistic
    (~0.31, since 1 PA can't move a 30-PA prior much)."""
    league_avg = LEAGUE_AVERAGES["wOBA"]  # 0.315
    prior_n = DEFAULT_PRIOR_N["wOBA"]     # 30
    shrunk = shrink_rate(1.35, 1, league_avg=league_avg, prior_n=prior_n)
    assert 0.30 <= shrunk <= 0.40, (
        f"1-PA wOBA=1.35 must shrink near league avg. Got {shrunk:.4f}."
    )


# ─── INV-BS5: prior_n=0 escape hatch ─────────────────────────────────
def test_inv_bs5_prior_n_zero_returns_observed():
    """`prior_n=0` is an explicit escape hatch — returns the raw
    observed rate. Used by tests that need to exercise downstream
    code with un-shrunk values."""
    assert shrink_rate(0.42, 100, league_avg=0.20, prior_n=0) == 0.42
    assert shrink_rate(None, 0, league_avg=0.20, prior_n=0) == 0.20


# ─── INV-BS6: bayes_shrink_rolling_window selects right denominator ─
def test_inv_bs6_pa_vs_bbe_denominator_selection():
    """Rolling-window shrinkage MUST use:
      * `plate_appearances` for PA-denominated rates (k_rate, wOBA, ...)
      * `batted_ball_events` for BBE-denominated rates (barrel_rate, ...)
    Mixing them (using PA for barrel_rate) would silently make the
    shrinkage too weak — barrel_rate has fewer events than PA so
    using PA inflates the effective sample size.
    """
    # Bleday-shape window: 2 PAs, 1 BBE. Both rates set to 1.0
    # so we can see the difference clearly.
    window = {
        "plate_appearances": 2,
        "batted_ball_events": 1,
        "wOBA": 1.0,         # PA-denominated
        "barrel_rate": 1.0,  # BBE-denominated
    }
    out = bayes_shrink_rolling_window(window)

    # wOBA: shrink with n=2 (PA) and prior_n=30 → (1.0*2 + 0.315*30)/(2+30) = 0.358
    expected_woba = (1.0 * 2 + 0.315 * 30) / (2 + 30)
    assert math.isclose(out["wOBA"], expected_woba, abs_tol=1e-9), (
        f"wOBA must use PA={window['plate_appearances']} as denom. "
        f"Got {out['wOBA']:.4f}, expected {expected_woba:.4f}."
    )

    # barrel_rate: shrink with n=1 (BBE) and prior_n=15 → (1.0*1 + 0.08*15)/(1+15) = 0.1375
    expected_barrel = (1.0 * 1 + 0.08 * 15) / (1 + 15)
    assert math.isclose(out["barrel_rate"], expected_barrel, abs_tol=1e-9), (
        f"barrel_rate must use BBE={window['batted_ball_events']} as "
        f"denom. Got {out['barrel_rate']:.4f}, expected "
        f"{expected_barrel:.4f}. If this fires the denominator "
        f"selection is wrong — using PA would weakly shrink an "
        f"out-of-distribution value, defeating the fix."
    )


def test_inv_bs6b_sample_size_fields_passed_through():
    """The sample-size fields themselves (PA, BBE) MUST NOT be
    shrunk — they're the denominators, not rates."""
    window = {
        "plate_appearances": 2,
        "batted_ball_events": 1,
        "wOBA": 0.5,
    }
    out = bayes_shrink_rolling_window(window)
    assert out["plate_appearances"] == 2
    assert out["batted_ball_events"] == 1


# ─── INV-BS7: idempotent on empty / missing input ────────────────────
def test_inv_bs7_empty_window_handled():
    """Empty input → empty output. No exception, no crash."""
    assert bayes_shrink_rolling_window({}) == {}
    assert bayes_shrink_rolling_window(None) == {}


# ─── INV-BS8: monotonicity in N (smoothness check) ───────────────────
def test_inv_bs8_shrinkage_smoothly_relaxes_with_sample_size():
    """As n_observed increases (with same observed_rate vs prior),
    the shrunk value should monotonically approach the observed rate.
    A sign error or threshold-style cliff would break this."""
    observed = 1.0
    league_avg = 0.08
    last = league_avg  # at n=0 we equal league_avg
    for n in (1, 5, 10, 50, 100, 500, 5000):
        v = shrink_rate(observed, n, league_avg=league_avg, prior_n=15)
        assert v >= last - 1e-9, (
            f"Non-monotonic at n={n}: prev={last}, this={v}. "
            f"Shrinkage must smoothly relax toward observed."
        )
        last = v
    # Final value at n=5000 should be near observed (1.0)
    assert last > 0.99
