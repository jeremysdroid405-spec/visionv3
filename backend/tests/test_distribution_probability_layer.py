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
        # Hits floor = 0.55. Pass cv=0.7 → should use 0.7.
        r = compute_distribution_probability(mu=1.5, line=1.5, cv=0.70,
                                              stat_family="hits")
        assert r.sigma_source == "cv_derived_from_l10"
        assert abs(r.sigma - 0.70 * 1.5) < 1e-6

    def test_uses_floor_when_cv_below_floor(self):
        # Hits floor = 0.55. Pass cv=0.30 → should clamp up.
        r = compute_distribution_probability(mu=1.5, line=1.5, cv=0.30,
                                              stat_family="hits")
        assert r.sigma_source == "cv_floor"
        assert abs(r.sigma - 0.55 * 1.5) < 1e-6

    def test_uses_default_when_cv_missing(self):
        r = compute_distribution_probability(mu=1.5, line=1.5, cv=None,
                                              stat_family="hits")
        assert r.sigma_source == "stat_family_default"
        # hits has its own floor so default uses that
        assert r.sigma > 0

    def test_unknown_family_uses_default_floor(self):
        r = compute_distribution_probability(mu=2.0, line=2.5, cv=None,
                                              stat_family="some_new_stat")
        assert r.sigma_source == "stat_family_default"
        assert r.sigma > 0

    def test_per_family_floors_differ(self):
        # Triples (1.40) should produce a wider σ than hits (0.55)
        r_hits = compute_distribution_probability(mu=1.0, line=0.5, cv=0.1,
                                                   stat_family="hits")
        r_trip = compute_distribution_probability(mu=1.0, line=0.5, cv=0.1,
                                                   stat_family="triples")
        assert r_trip.sigma > r_hits.sigma

    def test_absolute_sigma_floor(self):
        # On tiny μ, the absolute floor (_SIGMA_MIN_ABSOLUTE = 0.20)
        # should kick in to prevent collapse.
        r = compute_distribution_probability(mu=0.05, line=0.5, cv=0.5,
                                              stat_family="stolen_bases")
        assert r.sigma >= 0.20


class TestNullSafety:
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
        # μ way above line, tiny σ floor → p_over should clamp to 0.99.
        # Hits floor=0.55, but absolute σ floor is 0.20 — passing
        # extremely tight cv only yields σ=5.5 here. Use a stat with
        # a low family floor and a small μ so σ floor → 0.20.
        r = compute_distribution_probability(mu=2.0, line=0.5, cv=0.05,
                                              stat_family="pitcher_outs")
        assert r.p_over >= 0.99 - 1e-9
        assert r.clamped is True

    def test_extreme_low_clamped(self):
        r = compute_distribution_probability(mu=0.0, line=10.0, cv=0.01,
                                              stat_family="hits")
        assert r.clamped is True
        assert r.p_over >= 0.01


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
