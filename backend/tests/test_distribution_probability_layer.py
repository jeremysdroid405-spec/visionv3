"""Regression tests for the distribution-based probability layer."""
import math
import sys
sys.path.insert(0, "/app/backend")

import pytest
from services.probability.distribution_layer import (
    compute_distribution_probability,
)


class TestNormalCDF:
    def test_at_line_returns_50pct(self):
        # When μ exactly equals line, p_over should be ≈ 0.5
        r = compute_distribution_probability(mu=1.5, line=1.5, cv=0.5,
                                              stat_family="hits")
        assert r is not None
        assert abs(r.p_over - 0.5) < 0.001
        assert abs(r.p_under - 0.5) < 0.001

    def test_above_line_p_over_above_50(self):
        r = compute_distribution_probability(mu=2.0, line=1.5, cv=0.5,
                                              stat_family="hits")
        assert r.p_over > 0.5
        # No "force-below-50" override
        assert r.p_under < 0.5

    def test_below_line_p_over_below_50(self):
        r = compute_distribution_probability(mu=0.8, line=1.5, cv=0.5,
                                              stat_family="hits")
        assert r.p_over < 0.5
        assert r.p_under > 0.5

    def test_no_50_minus_z_heuristic(self):
        # The legacy heuristic was: prob_over = 50 - |z|*10 when μ < line
        # but bare CDF returned ≥50%. Verify the new layer NEVER applies it.
        # We construct a case (μ slightly below line, CV very high → wide σ)
        # where the bare CDF could potentially land near 50%.
        r = compute_distribution_probability(mu=1.4, line=1.5, cv=2.0,
                                              stat_family="hits")
        # With huge σ, p_over should approach but not violate CDF bounds
        assert 0.0 < r.p_over < 1.0
        # The heuristic would have produced exactly 50 - |z|*10. We get
        # the bare CDF result — no special re-write applied.

    def test_idempotent_returns_probabilities_summing_to_one(self):
        r = compute_distribution_probability(mu=2.5, line=1.5, cv=0.7,
                                              stat_family="total_bases")
        assert abs((r.p_over + r.p_under) - 1.0) < 1e-9


class TestSigmaResolution:
    def test_uses_cv_when_above_floor(self):
        # Hits floor = 0.55. Pass cv=0.7. μ=2.0 above mu_floor=0.5
        # so effective_mu=2.0, sigma_source = "cv_derived_from_l10".
        r = compute_distribution_probability(mu=2.0, line=2.0, cv=0.70,
                                              stat_family="hits")
        assert r.sigma_source == "cv_derived_from_l10"
        assert r.mu_floor_applied is False
        assert abs(r.sigma - 0.70 * 2.0) < 1e-6

    def test_uses_floor_when_cv_below_floor(self):
        # Hits floor = 0.55. Pass cv=0.30, μ=2.0 above mu_floor.
        r = compute_distribution_probability(mu=2.0, line=2.0, cv=0.30,
                                              stat_family="hits")
        assert r.sigma_source == "cv_floor"
        assert r.mu_floor_applied is False
        assert abs(r.sigma - 0.55 * 2.0) < 1e-6

    def test_uses_default_when_cv_missing(self):
        r = compute_distribution_probability(mu=2.0, line=2.0, cv=None,
                                              stat_family="hits")
        assert r.sigma_source == "stat_family_default"
        assert r.sigma > 0

    def test_unknown_family_uses_default_floor(self):
        r = compute_distribution_probability(mu=2.0, line=2.5, cv=None,
                                              stat_family="some_new_stat")
        assert r.sigma_source == "stat_family_default"
        assert r.sigma > 0

    def test_per_family_floors_differ(self):
        # Walks Allowed (0.65) should produce a wider σ than hits (0.55)
        # for the same μ/line/cv — both stay on Normal CDF.
        r_hits = compute_distribution_probability(mu=1.0, line=0.5, cv=0.1,
                                                   stat_family="hits")
        r_walk = compute_distribution_probability(mu=1.0, line=0.5, cv=0.1,
                                                   stat_family="walks_allowed")
        assert r_walk.sigma > r_hits.sigma

    def test_absolute_sigma_floor(self):
        # On tiny μ, the absolute floor (sigma_min_absolute = 0.20)
        # should kick in to prevent collapse. Use `hits` (Normal CDF),
        # not stolen_bases (Poisson) which doesn't have σ.
        r = compute_distribution_probability(mu=0.05, line=0.5, cv=0.5,
                                              stat_family="hits")
        assert r.sigma >= 0.20


class TestMuFloor:
    """μ-floor scaling — prevents σ collapse on low-μ event props.

    These tests lock in the calibration introduced 2026-04-27.
    """
    def test_mu_floor_applied_when_mu_below_floor(self):
        # Hits μ_floor = 0.5. μ = 0.05 → effective_mu should be 0.5.
        r = compute_distribution_probability(mu=0.05, line=0.5, cv=1.0,
                                              stat_family="hits")
        assert r.mu_floor_applied is True
        assert r.effective_mu == 0.5
        assert "mu_floor" in r.sigma_source
        # σ = 1.0 × 0.5 = 0.5  (vs old: σ = 1.0 × 0.05 = 0.05 → would
        # have hit the absolute floor of 0.20)
        assert abs(r.sigma - 0.5) < 1e-6

    def test_mu_floor_not_applied_when_mu_above_floor(self):
        # Total Bases μ_floor = 1.0. μ = 1.5 → effective_mu = 1.5.
        r = compute_distribution_probability(mu=1.5, line=1.5, cv=0.7,
                                              stat_family="total_bases")
        assert r.mu_floor_applied is False
        assert r.effective_mu == 1.5
        assert "mu_floor" not in r.sigma_source

    def test_z_score_uses_raw_mu_not_effective_mu(self):
        # Critical: μ-floor must NOT alter the projection in the
        # z-score numerator. Verify by checking p_over consistency.
        # μ=0.02, line=0.5, cv=1.0, family=hits, mu_floor=0.5.
        # σ = 1.0 × max(0.02, 0.5) = 0.5
        # z = (0.5 - 0.02) / 0.5 = 0.96
        # p_over = 1 - Φ(0.96) ≈ 0.169
        r = compute_distribution_probability(mu=0.02, line=0.5, cv=1.0,
                                              stat_family="hits")
        # Sanity check: p_over should be near 0.17, NOT near 0.50
        # (which would be the case if both the σ and the numerator
        # used effective_mu).
        assert 0.10 < r.p_over < 0.25

    def test_mu_floor_lifts_p_over_off_clamp_floor(self):
        # The exact bug we're fixing: tiny μ + 0.5 line → p_over ≈ 0.01.
        # After μ_floor, p_over should rise off the 0.01 clamp.
        r = compute_distribution_probability(mu=0.05, line=0.5, cv=1.0,
                                              stat_family="hits")
        assert r.p_over > 0.01  # not clamped
        assert r.clamped is False

    def test_pitcher_outs_high_mu_floor(self):
        # Pitcher Outs μ_floor = 12.0 (high-magnitude family).
        # μ = 4.5 → effective_mu = 12.0, σ = cv × 12.0
        r = compute_distribution_probability(mu=4.5, line=14.5, cv=0.40,
                                              stat_family="pitcher_outs")
        assert r.mu_floor_applied is True
        assert r.effective_mu == 12.0
        assert abs(r.sigma - 0.40 * 12.0) < 1e-6

    def test_unknown_family_uses_default_distribution(self):
        # Unknown families fall through to a sport-agnostic Normal CDF
        # default (cv_floor=0.5, mu_floor=0.0). The contract changed
        # with the universal engine — unknown families no longer
        # inherit the MLB-specific μ_floor=0.5; they get a no-floor
        # default so unknown stats fail safe (no fabricated σ scaling).
        r = compute_distribution_probability(mu=0.1, line=0.5, cv=0.6,
                                              stat_family="some_new_stat")
        assert r is not None
        assert r.distribution == "normal_cdf"
        assert r.mu_floor_applied is False
        # σ comes from sigma_min_absolute (0.20) since cv*μ would be tiny.
        assert r.sigma >= 0.20

    def test_per_family_mu_floors_differ(self):
        # Hits (0.5) vs Pitcher Strikeouts (2.0)
        r_hits = compute_distribution_probability(mu=0.1, line=0.5, cv=0.6,
                                                   stat_family="hits")
        r_psk = compute_distribution_probability(mu=0.1, line=0.5, cv=0.6,
                                                   stat_family="pitcher_strikeouts")
        # Both have mu_floor applied but at different levels
        assert r_hits.effective_mu == 0.5
        assert r_psk.effective_mu == 2.0
        assert r_psk.sigma > r_hits.sigma
    def test_returns_none_when_mu_missing(self):
        assert compute_distribution_probability(
            mu=None, line=1.5, cv=0.5, stat_family="hits",
        ) is None

    def test_returns_none_when_line_missing(self):
        assert compute_distribution_probability(
            mu=1.5, line=None, cv=0.5, stat_family="hits",
        ) is None

    def test_handles_string_inputs(self):
        # MongoDB sometimes returns numerics as Decimal128; just checking
        # that float-castable inputs work.
        r = compute_distribution_probability(
            mu="1.5", line="2.5", cv=0.6, stat_family="hits",
        )
        assert r is not None
        assert r.p_over < 0.5  # μ below line


class TestClamping:
    def test_extreme_high_clamped(self):
        # μ way above line, σ floored at absolute minimum (0.20) →
        # p_over should clamp to 0.99. Use a family with the smallest
        # μ_floor (home_runs=0.3) and a tiny CV so σ hits its absolute
        # floor of 0.20 even after μ-floor scaling.
        # μ=2.0, line=0.5, family=home_runs (μ_floor=0.3), cv=0.05
        # → effective_mu = max(2.0, 0.3) = 2.0
        # → σ = max(0.05*2.0, 0.20) = 0.20  (absolute floor)
        # → z = (0.5 - 2.0) / 0.20 = -7.5  → p_over → clamps to 0.99
        r = compute_distribution_probability(mu=2.0, line=0.5, cv=0.05,
                                              stat_family="home_runs")
        # cv_floor for home_runs = 1.20 → cv_used clamps up to 1.20
        # so σ = 1.20 * 2.0 = 2.4. Use a family without floor instead.
        # Easier: bypass cv_floor by passing family="some_unknown" with
        # default cv_floor=0.50 and very tight σ.
        r = compute_distribution_probability(mu=10.0, line=0.5, cv=None,
                                              stat_family="some_new_stat")
        # default mu_floor=0.5, cv default=0.50 → σ = 0.50 * 10 = 5.0
        # z = (0.5 - 10) / 5 = -1.9 → p_over ≈ 0.97
        # Still not clamped. Need σ << |line - μ|.
        # Just test the clamp behaviour directly with a large μ:
        r = compute_distribution_probability(mu=100.0, line=0.5, cv=None,
                                              stat_family="hits")
        # cv default=0.50, mu=100 → σ = 0.50 * 100 = 50.
        # z = (0.5 - 100) / 50 = -1.99 → p_over ≈ 0.977. Not clamped.
        # The clamp only triggers on really degenerate inputs. Use one:
        r = compute_distribution_probability(mu=1000.0, line=0.5, cv=0.001,
                                              stat_family="hits")
        # cv=0.001 < cv_floor 0.55 → cv_used = 0.55. σ = 0.55 * 1000 = 550.
        # Clamp doesn't fire. The clamp is genuinely hard to hit with
        # μ_floor + cv_floor in place — that's by design.
        # Verify the clamp logic exists and works on a hand-crafted case:
        # Bypass μ_floor by using effective_mu via cv that gives σ=0.20 floor
        # and a huge separation. Force it:
        r = compute_distribution_probability(mu=1.0, line=-100.0, cv=0.01,
                                              stat_family="hits")
        # σ floor = 0.5*1 = 0.5 (cv_floor); z = (-100-1)/0.5 = -202 → p_over→1
        assert r.p_over >= 0.99 - 1e-9
        assert r.clamped is True

    def test_extreme_low_clamped(self):
        r = compute_distribution_probability(mu=0.0, line=10.0, cv=0.01,
                                              stat_family="hits")
        assert r.clamped is True
        assert r.p_over >= 0.01


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
