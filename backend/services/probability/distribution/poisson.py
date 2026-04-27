"""
Poisson distribution.
=====================

Models count outcomes with rate λ = μ:

    P(K ≥ k_threshold) = 1 − F_Poisson(k_threshold − 1; λ)

where `k_threshold = floor(line) + 1` for OVER (e.g., line=0.5 →
k_threshold=1, P(K ≥ 1) = 1 − e^{−λ}; line=1.5 → k_threshold=2,
P(K ≥ 2) = 1 − e^{−λ}(1 + λ); etc.).

Best-fit when:
- The stat is a non-negative integer count.
- Variance ≈ mean (no over-dispersion). For over-dispersed counts use
  Negative Binomial (`negative_binomial.py`).

Calibration knobs:
- `lambda_min` / `lambda_max` clip pathological projections.
- Optional `base_rate` shrinkage toward a family-level λ when the
  player's projection is unreliable (small-sample).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .base import (
    Distribution, DistributionConfig, ProbabilityResult, clamp_p,
)


@dataclass(frozen=True)
class PoissonConfig(DistributionConfig):
    name: str = "poisson"
    lambda_min: float = 1e-3
    lambda_max: float = 25.0
    base_rate: Optional[float] = None
    base_rate_weight: float = 0.0


def _poisson_cdf(k: int, lam: float) -> float:
    """P(K ≤ k) for K ~ Poisson(lam). Numerically stable for k ≤ ~50."""
    if k < 0:
        return 0.0
    # Use the closed-form sum; fine for the count ranges we deal with.
    s = 0.0
    log_lam = math.log(lam) if lam > 0 else float("-inf")
    log_term = -lam  # P(K=0) = e^{-λ}
    s += math.exp(log_term)
    for j in range(1, k + 1):
        # log P(K=j) = -λ + j*log(λ) - lgamma(j+1)
        log_term = -lam + j * log_lam - math.lgamma(j + 1)
        s += math.exp(log_term)
    if s > 1.0:
        s = 1.0
    return s


class PoissonDistribution(Distribution):
    name = "poisson"

    def __init__(self, config: PoissonConfig):
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
        lam = max(cfg.lambda_min, min(mu_f, cfg.lambda_max))

        if cfg.base_rate is not None and cfg.base_rate_weight > 0:
            w = max(0.0, min(cfg.base_rate_weight, 1.0))
            lam = (1.0 - w) * lam + w * cfg.base_rate

        # OVER means K ≥ k_threshold. For line=0.5 this is K ≥ 1,
        # i.e. floor(line)+1.
        k_threshold = int(math.floor(line_f)) + 1
        if k_threshold <= 0:
            p_over = 1.0
        else:
            p_over = 1.0 - _poisson_cdf(k_threshold - 1, lam)

        p_over, clamped = clamp_p(p_over)
        p_under = 1.0 - p_over

        return ProbabilityResult(
            p_over=round(p_over, 6),
            p_under=round(p_under, 6),
            distribution="poisson",
            sport=sport,
            stat_family=stat_family,
            line=line_f,
            selector_reason=(
                f"poisson λ_clip=[{cfg.lambda_min},{cfg.lambda_max}]"
                + (f" shrink_w={cfg.base_rate_weight}" if cfg.base_rate_weight > 0 else "")
            ),
            mu=round(mu_f, 4),
            lambda_=round(lam, 6),
            threshold=k_threshold,
            clamped=clamped,
        )


__all__ = ["PoissonConfig", "PoissonDistribution"]
