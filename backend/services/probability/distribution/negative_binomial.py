"""
Negative Binomial distribution.
===============================

Over-dispersed count model. Used when Var(X) > E(X), which is the
common case for player-game stats with bursty outcomes (multi-hit
games, multi-K pitcher games, etc.).

Parameterisation
----------------
Mean-dispersion form:
    X ~ NB(r, p)   with   E[X] = μ   and   Var[X] = μ + μ² / r
=>  p = r / (r + μ)

The dispersion `r` is derived from the player's coefficient of
variation:

    σ² = (cv × μ)²
    Var(X) = μ + μ² / r = (cv × μ)²
    r = μ² / ((cv × μ)² − μ) = μ / (cv² × μ − 1)

If `cv² × μ ≤ 1` the NB collapses to Poisson (no over-dispersion); we
fall back to Poisson semantics in that branch.

P(K ≥ k_threshold) = 1 − F_NB(k_threshold − 1; r, p)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .base import (
    Distribution, DistributionConfig, ProbabilityResult, clamp_p,
)


@dataclass(frozen=True)
class NegativeBinomialConfig(DistributionConfig):
    name: str = "negative_binomial"
    cv_floor: float = 0.50          # used when player CV is missing
    lambda_min: float = 1e-3
    lambda_max: float = 25.0
    # If `cv² × μ` doesn't yield over-dispersion, fall back to Poisson
    # (instead of forcing a degenerate NB).
    fallback_to_poisson: bool = True


def _log_choose(n: float, k: int) -> float:
    """log C(n, k) using lgamma, valid for non-integer n (NB needs this)."""
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def _nb_pmf(k: int, r: float, p: float) -> float:
    """Negative Binomial PMF in (r, p) form. Numerically stable for moderate k."""
    if k < 0 or r <= 0 or not (0.0 < p <= 1.0):
        return 0.0
    # P(X=k) = C(k+r-1, k) * p^r * (1-p)^k
    log_pmf = (
        math.lgamma(k + r) - math.lgamma(k + 1) - math.lgamma(r)
        + r * math.log(p)
        + (k * math.log1p(-p) if 1.0 - p > 0 else (0.0 if k == 0 else float("-inf")))
    )
    return math.exp(log_pmf)


def _nb_cdf(k: int, r: float, p: float) -> float:
    """P(X ≤ k) by direct PMF summation."""
    if k < 0:
        return 0.0
    s = 0.0
    for j in range(0, k + 1):
        s += _nb_pmf(j, r, p)
        if s >= 1.0:
            return 1.0
    return s


def _poisson_cdf(k: int, lam: float) -> float:
    if k < 0:
        return 0.0
    s = 0.0
    log_lam = math.log(lam) if lam > 0 else float("-inf")
    s += math.exp(-lam)
    for j in range(1, k + 1):
        log_term = -lam + j * log_lam - math.lgamma(j + 1)
        s += math.exp(log_term)
    return min(s, 1.0)


class NegativeBinomialDistribution(Distribution):
    name = "negative_binomial"

    def __init__(self, config: NegativeBinomialConfig):
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

        # Resolve CV (player CV with floor fallback).
        cv_floor_applied = False
        if cv is not None and not math.isnan(cv) and cv > 0:
            cv_used = max(float(cv), cfg.cv_floor)
            if cv_used > float(cv):
                cv_floor_applied = True
        else:
            cv_used = cfg.cv_floor
            cv_floor_applied = True

        # NB dispersion r derived from CV-implied variance.
        denom = (cv_used ** 2) * lam - 1.0
        k_threshold = int(math.floor(line_f)) + 1
        if denom <= 0 or not cfg.fallback_to_poisson is False and denom <= 0:
            # Falls back to Poisson when no over-dispersion.
            if k_threshold <= 0:
                p_over = 1.0
            else:
                p_over = 1.0 - _poisson_cdf(k_threshold - 1, lam)
            p_over, clamped = clamp_p(p_over)
            return ProbabilityResult(
                p_over=round(p_over, 6),
                p_under=round(1.0 - p_over, 6),
                distribution="negative_binomial",
                sport=sport,
                stat_family=stat_family,
                line=line_f,
                selector_reason="nb→poisson (no overdispersion)",
                mu=round(mu_f, 4),
                lambda_=round(lam, 6),
                cv=round(cv, 4) if cv is not None else None,
                threshold=k_threshold,
                cv_floor_applied=cv_floor_applied,
                clamped=clamped,
            )

        r = lam ** 2 / denom
        p_param = r / (r + lam)

        if k_threshold <= 0:
            p_over = 1.0
        else:
            p_over = 1.0 - _nb_cdf(k_threshold - 1, r, p_param)

        p_over, clamped = clamp_p(p_over)
        return ProbabilityResult(
            p_over=round(p_over, 6),
            p_under=round(1.0 - p_over, 6),
            distribution="negative_binomial",
            sport=sport,
            stat_family=stat_family,
            line=line_f,
            selector_reason=f"nb r={r:.3f} p={p_param:.3f} from cv={cv_used:.3f}",
            mu=round(mu_f, 4),
            lambda_=round(lam, 6),
            cv=round(cv, 4) if cv is not None else None,
            dispersion_r=round(r, 4),
            p_param=round(p_param, 6),
            threshold=k_threshold,
            cv_floor_applied=cv_floor_applied,
            clamped=clamped,
        )


__all__ = ["NegativeBinomialConfig", "NegativeBinomialDistribution"]
