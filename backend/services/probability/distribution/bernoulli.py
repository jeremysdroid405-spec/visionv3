"""
Bernoulli distribution.
=======================

For binary 0/1-style props at line=0.5, where μ from the model is the
expected count of events (and ≤ 1 in the typical regime):

    p_over  = clip(μ, ε, 1 − ε)
    p_under = 1 − p_over

This is the right model when the underlying random variable can take
at most one event per game (or the prop wording asks "did event
occur"). For multi-event tails (line ≥ 1.5) Poisson / Negative
Binomial are better.

The registry is responsible for routing 0.5-line rare events here;
the distribution itself does NOT enforce a line check.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .base import (
    Distribution, DistributionConfig, ProbabilityResult, clamp_p,
)


@dataclass(frozen=True)
class BernoulliConfig(DistributionConfig):
    name: str = "bernoulli"
    # μ from a count-style projection can exceed 1.0 on rare slates
    # (e.g., μ_HR = 1.2 for an elite power hitter at Coors). Cap at
    # `mu_max` so the per-game P(event) stays a probability.
    mu_max: float = 1.0
    # Optional shrinkage toward the family base rate when sample size
    # is thin. Off by default (set base_rate / weight in calibration).
    base_rate: Optional[float] = None
    base_rate_weight: float = 0.0  # 0 = no shrinkage; 1 = full shrinkage


class BernoulliDistribution(Distribution):
    name = "bernoulli"

    def __init__(self, config: BernoulliConfig):
        self.config = config

    def compute(
        self,
        sport: str,
        stat_family: str,
        mu: Optional[float],
        line: Optional[float],
        cv: Optional[float] = None,
        sigma: Optional[float] = None,
        extras: Optional[Dict[str, Any]] = None,
    ) -> Optional[ProbabilityResult]:
        if mu is None or line is None:
            return None
        try:
            mu_f = float(mu)
            line_f = float(line)
        except (TypeError, ValueError):
            return None

        cfg = self.config

        # μ → P(event in the game). Cap at mu_max so the rate is a probability.
        p = max(0.0, min(mu_f, cfg.mu_max))

        # Optional Bayesian shrinkage toward base rate.
        if cfg.base_rate is not None and cfg.base_rate_weight > 0:
            w = max(0.0, min(cfg.base_rate_weight, 1.0))
            p = (1.0 - w) * p + w * cfg.base_rate

        # For lines ≥ 1.5 in a Bernoulli-modelled family, the best we
        # can say is "essentially zero" because the support is {0, 1}.
        # Caller registries shouldn't route here for line ≥ 1.5; we
        # short-circuit defensively.
        if line_f >= 1.0:
            p_over = 0.0
        else:
            p_over = p

        p_over, clamped = clamp_p(p_over)
        p_under = 1.0 - p_over

        return ProbabilityResult(
            p_over=round(p_over, 6),
            p_under=round(p_under, 6),
            distribution="bernoulli",
            sport=sport,
            stat_family=stat_family,
            line=line_f,
            selector_reason=(
                f"bernoulli mu_max={cfg.mu_max}"
                + (f" shrink_w={cfg.base_rate_weight}" if cfg.base_rate_weight > 0 else "")
            ),
            mu=round(mu_f, 4),
            p_param=round(p, 6),
            clamped=clamped,
        )


__all__ = ["BernoulliConfig", "BernoulliDistribution"]
