"""
Legacy facade for the distribution-based probability layer.
===========================================================

The implementation now lives in `services.probability.distribution`
(universal probability engine — see that package's __init__ docstring).

This module preserves the **previous public surface**
(`compute_distribution_probability`, `DistributionProbabilityResult`,
plus the floor-table constants used by the calibration / report
scripts) so existing callers do not break while we migrate them
sport-by-sport. Internally it now dispatches to the new universal
engine with `sport` plumbed through; the MLB scoring adapter
(the only production caller) was updated in the same change to pass
sport explicitly.

What changed (2026-04-27, universal engine refactor)
----------------------------------------------------
- σ / probability math no longer lives here. New per-distribution
  modules: `normal.py`, `bernoulli.py`, `poisson.py`,
  `negative_binomial.py`.
- Per-family floors moved to `distribution/calibration/{sport}.py`.
- This file now ONLY: (1) wraps the universal engine in the legacy
  signature, (2) re-exports the previous Result type & constants for
  backwards compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional

from .distribution import compute_probability, ProbabilityResult


# --------------------------------------------------------------------------
# Legacy constants — preserved verbatim because /tmp report scripts and
# downstream tooling reference them. The actual values are now
# authoritative inside `distribution/calibration/mlb.py`; these mirrors
# are kept in sync manually.
# --------------------------------------------------------------------------
_CV_FLOOR_BY_FAMILY = {
    "hits":              0.55,
    "total_bases":       0.65,
    "hits+runs+rbis":    0.55,
    "rbis":              0.85,
    "runs":              0.85,
    "singles":           0.65,
    "doubles":           1.10,
    "triples":           1.40,
    "home_runs":         1.20,
    "stolen_bases":      1.30,
    "batter_strikeouts": 0.45,
    "batter_walks":      0.85,
    "pitcher_strikeouts":0.30,
    "pitcher_outs":      0.18,
    "earned_runs":       0.85,
    "hits_allowed":      0.55,
    "walks_allowed":     0.65,
}
_CV_FLOOR_DEFAULT = 0.50

_MU_FLOOR_BY_FAMILY = {
    "hits":              0.5,
    "singles":           0.5,
    "runs":              0.5,
    "rbis":              0.5,
    "hits+runs+rbis":    0.5,
    "total_bases":       1.0,
    "doubles":           0.5,
    "home_runs":         0.3,
    "stolen_bases":      0.3,
    "batter_strikeouts": 0.5,
    "batter_walks":      0.5,
    "triples":           0.3,
    "pitcher_strikeouts":2.0,
    "earned_runs":       1.5,
    "hits_allowed":      2.5,
    "pitcher_outs":     12.0,
    "walks_allowed":     1.0,
}
_MU_FLOOR_DEFAULT = 0.5
_SIGMA_MIN_ABSOLUTE = 0.20
_PROB_CLAMP_LO = 0.01
_PROB_CLAMP_HI = 0.99


@dataclass
class DistributionProbabilityResult:
    """Legacy result schema. Mapped from the universal `ProbabilityResult`."""
    p_over: float
    p_under: float
    mu: float
    sigma: float
    cv: Optional[float]
    sigma_source: str
    distribution: str = "normal_cdf"
    clamped: bool = False
    note: Optional[str] = None
    effective_mu: Optional[float] = None
    mu_floor_applied: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _to_legacy(result: Optional[ProbabilityResult]) -> Optional[DistributionProbabilityResult]:
    if result is None:
        return None
    return DistributionProbabilityResult(
        p_over=result.p_over,
        p_under=result.p_under,
        mu=result.mu if result.mu is not None else 0.0,
        sigma=result.sigma if result.sigma is not None else 0.0,
        cv=result.cv,
        sigma_source=result.sigma_source or result.distribution,
        distribution=result.distribution,
        clamped=result.clamped,
        note=result.note,
        effective_mu=result.effective_mu,
        mu_floor_applied=result.mu_floor_applied,
    )


def compute_distribution_probability(
    mu: Optional[float],
    line: Optional[float],
    cv: Optional[float],
    stat_family: str,
    sport: str = "mlb",
) -> Optional[DistributionProbabilityResult]:
    """
    LEGACY signature. Calls the universal engine under the hood.

    `sport` defaults to "mlb" because every existing in-repo caller is
    MLB. New callers should use
    `services.probability.distribution.compute_probability` directly.
    """
    res = compute_probability(
        sport=sport, stat_family=stat_family,
        mu=mu, line=line, cv=cv,
    )
    return _to_legacy(res)


__all__ = [
    "compute_distribution_probability",
    "DistributionProbabilityResult",
    "_CV_FLOOR_BY_FAMILY", "_CV_FLOOR_DEFAULT",
    "_MU_FLOOR_BY_FAMILY", "_MU_FLOOR_DEFAULT",
    "_SIGMA_MIN_ABSOLUTE", "_PROB_CLAMP_LO", "_PROB_CLAMP_HI",
]
