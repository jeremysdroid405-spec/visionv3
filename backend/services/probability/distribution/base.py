"""
Universal probability engine — abstractions.
============================================

Sport-agnostic interface for converting a model projection (μ) and
market line into a calibrated p_over / p_under under an explicit
distributional model.

Architecture
------------
Each concrete distribution (Normal CDF, Bernoulli, Poisson, Negative
Binomial, …) lives in its own module and implements
`Distribution.compute(...)`. Distribution selection per
`(sport, stat_family, line, …)` is the responsibility of the
registry (`services.probability.distribution.registry`).

`ProbabilityResult` is the universal audit-field schema persisted on
every score doc. Sports / stat families that do not supply a given
parameter leave it `None`; the consumer (UI / observability) checks
`distribution` to know which fields are meaningful.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional


# Bare-CDF clamp range, applied identically across every distribution.
PROB_CLAMP_LO: float = 0.01
PROB_CLAMP_HI: float = 0.99


@dataclass
class ProbabilityResult:
    """Universal audit schema for the probability layer."""

    # ----- core outputs (always set) -----
    p_over: float
    p_under: float
    distribution: str          # "normal_cdf" | "bernoulli" | "poisson" | "negative_binomial"
    sport: str
    stat_family: str
    line: float
    selector_reason: str       # human-readable description of *why* this distribution was chosen

    # ----- continuous-distribution parameters (Normal, NB) -----
    mu: Optional[float] = None
    sigma: Optional[float] = None
    cv: Optional[float] = None
    sigma_source: Optional[str] = None     # "cv_derived_from_l10" | "cv_floor" | "stat_family_default"
    effective_mu: Optional[float] = None   # μ used inside σ scaling (post μ-floor / cap)
    mu_floor_applied: bool = False
    mu_floor_capped: bool = False

    # ----- count-distribution parameters (Poisson, NB) -----
    lambda_: Optional[float] = None        # Poisson rate / NB mean
    dispersion_r: Optional[float] = None   # NB size parameter
    threshold: Optional[int] = None        # integer ≥-threshold used in count CDFs

    # ----- binary-distribution parameter (Bernoulli) -----
    p_param: Optional[float] = None

    # ----- shared diagnostics -----
    clamped: bool = False
    cv_floor_applied: bool = False
    note: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DistributionConfig:
    """Distribution-agnostic configuration object.

    Subclassed in each distribution module to carry its own parameter
    schema (cv_floor, mu_floor, dispersion, etc.). The base class
    exists purely so the registry can store a heterogeneous mapping.
    """
    name: str  # informational; concrete subclass sets this


class Distribution(ABC):
    """Concrete distribution implementing μ → p_over conversion."""

    name: str = "abstract"

    @abstractmethod
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
        """
        Returns the model's probability of clearing the line, or None
        when the supplied inputs are insufficient (caller should fall
        back to whatever rung sits below the distribution layer).
        """


def clamp_p(p: float) -> tuple[float, bool]:
    """Clamp probability to [PROB_CLAMP_LO, PROB_CLAMP_HI]; return (p, was_clamped)."""
    if p < PROB_CLAMP_LO:
        return PROB_CLAMP_LO, True
    if p > PROB_CLAMP_HI:
        return PROB_CLAMP_HI, True
    return p, False


__all__ = [
    "Distribution",
    "DistributionConfig",
    "ProbabilityResult",
    "PROB_CLAMP_LO",
    "PROB_CLAMP_HI",
    "clamp_p",
]
