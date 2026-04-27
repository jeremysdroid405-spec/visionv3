"""
Regression tests for the universal probability engine.
======================================================
Covers:
  • Registry resolution (sport, family) → Distribution
  • Per-distribution math (Normal, Bernoulli, Poisson, NB)
  • Selector rules (line-aware family routing)
  • Backwards-compatible public API
"""
import math
import sys
sys.path.insert(0, "/app/backend")

import pytest
from services.probability.distribution import (
    compute_probability, get_registry,
)
from services.probability.distribution.normal import (
    NormalCDFDistribution, NormalCDFConfig,
)
from services.probability.distribution.bernoulli import (
    BernoulliDistribution, BernoulliConfig,
)
from services.probability.distribution.poisson import (
    PoissonDistribution, PoissonConfig,
)
from services.probability.distribution.negative_binomial import (
    NegativeBinomialDistribution, NegativeBinomialConfig,
)


class TestRegistry:
    def test_sports_registered(self):
        sports = get_registry().list_sports()
        assert "mlb" in sports
        assert "nba" in sports
        assert "nfl" in sports

    def test_unknown_sport_falls_back_to_default(self):
        # Sport with no registered table → DEFAULT_DISTRIBUTION (Normal CDF)
        r = compute_probability(sport="nhl", stat_family="goals",
                                 mu=0.5, line=0.5, cv=0.6)
        assert r is not None
        assert r.distribution == "normal_cdf"

    def test_mlb_routes_hits_to_normal(self):
        r = compute_probability(sport="mlb", stat_family="Hits",
                                 mu=0.78, line=0.5, cv=0.9)
        assert r.distribution == "normal_cdf"

    def test_mlb_routes_home_runs_05_to_poisson(self):
        r = compute_probability(sport="mlb", stat_family="Home Runs",
                                 mu=0.17, line=0.5, cv=2.5)
        assert r.distribution == "poisson"
        assert r.lambda_ is not None
        # P(K ≥ 1) = 1 - e^{-0.17} ≈ 0.156
        assert abs(r.p_over - (1.0 - math.exp(-0.17))) < 0.01

    def test_mlb_routes_home_runs_above_05_to_nb(self):
        r = compute_probability(sport="mlb", stat_family="Home Runs",
                                 mu=0.5, line=1.5, cv=2.0)
        assert r.distribution == "negative_binomial"
        assert r.threshold == 2

    def test_canonicalization_handles_display_aliases(self):
        # "PITCHER_OUTS" / "Pitcher Outs" / "pitcher outs" all collapse.
        r1 = compute_probability(sport="mlb", stat_family="PITCHER_OUTS",
                                  mu=4.5, line=14.5, cv=0.4)
        r2 = compute_probability(sport="mlb", stat_family="Pitcher Outs",
                                  mu=4.5, line=14.5, cv=0.4)
        assert r1.distribution == r2.distribution == "normal_cdf"
        assert r1.effective_mu == r2.effective_mu == 12.0


class TestNormal:
    def test_at_line_returns_50pct(self):
        d = NormalCDFDistribution(NormalCDFConfig(cv_floor=0.5, mu_floor=0.0))
        r = d.compute("test", "stat", mu=1.5, line=1.5, cv=0.5)
        assert abs(r.p_over - 0.5) < 0.001

    def test_capped_only_when_floor_binding(self):
        # μ > μ_floor → cap should NOT shrink σ.
        d = NormalCDFDistribution(NormalCDFConfig(
            cv_floor=0.5, mu_floor=0.5, mu_floor_capped=True,
        ))
        r = d.compute("mlb", "hits", mu=0.78, line=0.5, cv=0.9)
        assert r.mu_floor_applied is False
        assert r.mu_floor_capped is False
        assert r.effective_mu == 0.78

    def test_capped_when_floor_binding_and_floor_above_line(self):
        # μ < μ_floor AND μ_floor > line → cap fires.
        d = NormalCDFDistribution(NormalCDFConfig(
            cv_floor=0.5, mu_floor=1.0, mu_floor_capped=True,
        ))
        r = d.compute("mlb", "hits", mu=0.05, line=0.5, cv=0.9)
        assert r.mu_floor_applied is True
        assert r.mu_floor_capped is True
        assert r.effective_mu == 0.5  # line cap


class TestBernoulli:
    def test_05_line_returns_mu_as_p(self):
        d = BernoulliDistribution(BernoulliConfig(mu_max=1.0))
        r = d.compute("mlb", "x", mu=0.3, line=0.5, cv=None)
        assert abs(r.p_over - 0.3) < 1e-6
        assert r.p_param == 0.3

    def test_high_line_returns_zero(self):
        d = BernoulliDistribution(BernoulliConfig(mu_max=1.0))
        r = d.compute("mlb", "x", mu=0.5, line=1.5, cv=None)
        # Bernoulli support is {0,1}; line ≥ 1.0 → p_over clamped to 0.01
        assert r.p_over <= 0.01

    def test_mu_capped_at_max(self):
        d = BernoulliDistribution(BernoulliConfig(mu_max=1.0))
        r = d.compute("mlb", "x", mu=1.5, line=0.5, cv=None)
        assert r.p_param == 1.0
        assert r.p_over >= 0.99


class TestPoisson:
    def test_05_line_matches_1_minus_exp_neg_lambda(self):
        d = PoissonDistribution(PoissonConfig())
        r = d.compute("mlb", "x", mu=0.5, line=0.5, cv=None)
        assert abs(r.p_over - (1.0 - math.exp(-0.5))) < 1e-4
        assert r.threshold == 1

    def test_15_line_matches_poisson_tail(self):
        d = PoissonDistribution(PoissonConfig())
        r = d.compute("mlb", "x", mu=2.0, line=1.5, cv=None)
        # P(K ≥ 2; λ=2) = 1 - (P(0) + P(1)) = 1 - e^{-2}(1 + 2)
        expected = 1.0 - math.exp(-2.0) * (1.0 + 2.0)
        assert abs(r.p_over - expected) < 1e-4
        assert r.threshold == 2

    def test_lambda_clipped_to_min(self):
        d = PoissonDistribution(PoissonConfig(lambda_min=0.01))
        r = d.compute("mlb", "x", mu=0.001, line=0.5, cv=None)
        assert r.lambda_ == 0.01


class TestNegativeBinomial:
    def test_falls_back_to_poisson_when_no_overdispersion(self):
        # cv² × μ ≤ 1 → no over-dispersion → falls back to Poisson math
        d = NegativeBinomialDistribution(NegativeBinomialConfig(cv_floor=0.5))
        r = d.compute("mlb", "x", mu=2.0, line=1.5, cv=0.3)
        # 0.3² × 2 = 0.18 < 1 → fallback path
        # Should match Poisson tail at λ=2
        expected = 1.0 - math.exp(-2.0) * (1.0 + 2.0)
        assert abs(r.p_over - expected) < 1e-3

    def test_uses_cv_for_overdispersion(self):
        # cv=1.5, μ=2 → cv² × μ = 4.5 > 1 → real NB
        d = NegativeBinomialDistribution(NegativeBinomialConfig(cv_floor=0.5))
        r = d.compute("mlb", "x", mu=2.0, line=1.5, cv=1.5)
        assert r.dispersion_r is not None
        assert r.p_param is not None
        assert 0.0 < r.p_over < 1.0


class TestBackwardsCompat:
    def test_legacy_facade_still_works(self):
        from services.probability.distribution_layer import (
            compute_distribution_probability,
        )
        r = compute_distribution_probability(
            mu=0.78, line=0.5, cv=0.9, stat_family="hits",
        )
        assert r is not None
        assert r.p_over > 0.5  # μ > line → p_over > 0.5
