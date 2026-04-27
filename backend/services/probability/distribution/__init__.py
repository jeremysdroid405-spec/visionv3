"""
Universal probability engine.
=============================

Public API
----------
`compute_probability(sport, stat_family, mu, line, cv=None,
                     sigma=None, extras=None) → ProbabilityResult`

Resolves the per-(sport, family, line) `Distribution` from the
registry and computes p_over / p_under under that distribution.
Audit fields on the result indicate which distribution was used,
which floors / caps fired, and the parameters that produced the
probability.

Sports / stat-families not registered fall through to a sport-agnostic
default Normal CDF so the engine NEVER returns garbage on an
unknown family.

Migration shim
--------------
The legacy entry point `services.probability.distribution_layer.
compute_distribution_probability` continues to work; it now wraps
this engine with `sport="mlb"` for backwards compatibility while we
finish migrating callers.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .base import (
    Distribution, DistributionConfig, ProbabilityResult,
    PROB_CLAMP_LO, PROB_CLAMP_HI,
)
from .registry import (
    DistributionRegistry, FamilySpec, get_registry,
    DEFAULT_DISTRIBUTION,
)

# Importing calibration registers every sport into the singleton.
from . import calibration  # noqa: F401 (side effect)


def compute_probability(
    sport: str,
    stat_family: str,
    mu: Optional[float],
    line: Optional[float],
    cv: Optional[float] = None,
    sigma: Optional[float] = None,
    extras: Optional[Dict[str, Any]] = None,
) -> Optional[ProbabilityResult]:
    """
    Universal entry point.

    Returns None when `mu` or `line` is None — caller falls back to
    whatever rung sits below the distribution layer (hit_rate, fair, etc.).
    """
    if mu is None or line is None:
        return None
    try:
        line_f = float(line)
    except (TypeError, ValueError):
        return None

    dist = get_registry().resolve(
        sport=sport, stat_family=stat_family, line=line_f,
        mu=mu, cv=cv, extras=extras,
    )
    return dist.compute(
        sport=sport, stat_family=stat_family,
        mu=mu, line=line, cv=cv, sigma=sigma, extras=extras,
    )


__all__ = [
    "compute_probability",
    "Distribution",
    "DistributionConfig",
    "ProbabilityResult",
    "DistributionRegistry",
    "FamilySpec",
    "get_registry",
    "DEFAULT_DISTRIBUTION",
    "PROB_CLAMP_LO",
    "PROB_CLAMP_HI",
]
