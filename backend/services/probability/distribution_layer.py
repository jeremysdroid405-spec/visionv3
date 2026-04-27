"""
Distribution-based probability layer for MLB scoring.
=====================================================

Computes the probability of clearing a prop line directly from a
distributional model parameterised by μ (from the MLR projection
engine, i.e. `MLBHighFrictionModel`) and σ (derived from the player's
empirical coefficient of variation against the canonical stat family).

Why this exists
---------------
The legacy "TP" calculation inside `MLBHighFrictionModel.predict()`
combines two issues:

  1. σ is taken from `std_dev_l10` directly, then a floor of
     `l10_avg * 0.35` is applied per-family. This produces a σ value
     that is **inconsistent** with `final_pred` because final_pred
     applies park / opp-K multipliers AFTER the floor was set.

  2. When the resulting normal CDF would return ≥50% even though
     `final_pred < line` (mathematically possible only when σ is
     unrealistically wide), a **hard heuristic** kicks in:
        prob_over = 50 - abs(z_score) * 10
     This is not a probability calculation — it's a policy override
     that fakes the CDF.

Both issues are eliminated here by:

  • Computing σ from the player's coefficient of variation (CV)
    applied to the **post-modifier** μ, so σ scales with the same
    quantity the CDF integrates against.
  • Applying a floor on σ via per-family minimums (so the dispersion
    cannot collapse to zero on volatile stats).
  • Returning the raw normal-CDF probability without any post-hoc
    re-write. If the CDF puts the OVER above 50% when μ < line, the
    σ is too wide and the floor logic has to be re-tuned — silently
    flipping the value to "below 50" hides that signal.

Public API
----------
`compute_distribution_probability(...)` returns a dataclass with:
  • `p_over` / `p_under` (floats in [0, 1])
  • `mu`, `sigma`, `cv` used
  • `sigma_source`     ("cv_derived_from_l10", "cv_floor",
                         "stat_family_default")
  • `distribution`     ("normal_cdf")
  • `clamped`          True if the bare CDF value was clamped to
                        [0.01, 0.99] for downstream stability
  • `note`             optional debugging string

This module is sport-agnostic but only wired into MLB scoring today.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

# Per-family minimum CV. Below these values the empirical CV is
# treated as too tight to be trustworthy (small-sample under-dispersion)
# and we substitute the floor so the CDF reflects realistic volatility.
# Values calibrated against MLB 2024 season residual analysis.
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
# Fallback when stat family isn't in the table — conservative middle.
_CV_FLOOR_DEFAULT = 0.50

# Hard minimum σ (in stat units) to prevent divide-by-zero on tiny
# projections like Stolen Bases 0.05.
_SIGMA_MIN_ABSOLUTE = 0.20

# Bare-CDF clamp range. Values outside [0.01, 0.99] explode log-loss
# and edge calculations downstream; clamp without altering rank order.
_PROB_CLAMP_LO = 0.01
_PROB_CLAMP_HI = 0.99


@dataclass
class DistributionProbabilityResult:
    p_over: float
    p_under: float
    mu: float
    sigma: float
    cv: Optional[float]
    sigma_source: str
    distribution: str = "normal_cdf"
    clamped: bool = False
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _normal_cdf(z: float) -> float:
    """Standard normal CDF using math.erf (no scipy dep at this layer)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _resolve_sigma(
    mu: float,
    cv: Optional[float],
    stat_family: str,
) -> tuple[float, str]:
    """Pick σ in priority order:
        1. CV-derived from the player's L10 (cv * |mu|), if cv is
           a finite float and ≥ family floor.
        2. Family-floor CV * |mu|.
        3. Generic _CV_FLOOR_DEFAULT * |mu|.

    Always enforces an absolute lower bound (`_SIGMA_MIN_ABSOLUTE`)
    so σ never collapses to 0 on tiny projections.
    """
    family = (stat_family or "").lower().strip()
    floor_cv = _CV_FLOOR_BY_FAMILY.get(family, _CV_FLOOR_DEFAULT)
    base = abs(mu) if mu is not None else 0.0

    sigma_source: str
    if cv is not None and not math.isnan(cv) and cv > 0:
        if cv >= floor_cv:
            cv_used = cv
            sigma_source = "cv_derived_from_l10"
        else:
            cv_used = floor_cv
            sigma_source = "cv_floor"
    else:
        cv_used = floor_cv
        sigma_source = "stat_family_default"

    sigma = max(cv_used * base, _SIGMA_MIN_ABSOLUTE)
    return sigma, sigma_source


def compute_distribution_probability(
    mu: Optional[float],
    line: Optional[float],
    cv: Optional[float],
    stat_family: str,
) -> Optional[DistributionProbabilityResult]:
    """
    Returns the model's probability of clearing the line, derived from
    a normal distribution centred at μ with σ scaled by CV.

    Returns None when μ or line is None — caller should fall back to
    whatever rung sits below this layer (hit_rate, fair, etc.).

    No "force-below-50%" override. If the bare CDF returns p_over ≥ 0.5
    when μ < line, that is information about σ being too wide for the
    family — surfaced via `sigma_source` for observability rather
    than silently rewritten.
    """
    if mu is None or line is None:
        return None
    try:
        mu_f = float(mu)
        line_f = float(line)
    except (TypeError, ValueError):
        return None

    sigma, sigma_source = _resolve_sigma(mu_f, cv, stat_family)

    # Standard normal-CDF: probability that a Normal(μ, σ²) random
    # variable exceeds `line`.
    z = (line_f - mu_f) / sigma
    p_under = _normal_cdf(z)
    p_over = 1.0 - p_under

    # Clamp to [0.01, 0.99] for downstream numerical stability.
    clamped = False
    if p_over < _PROB_CLAMP_LO:
        p_over, clamped = _PROB_CLAMP_LO, True
    elif p_over > _PROB_CLAMP_HI:
        p_over, clamped = _PROB_CLAMP_HI, True
    p_under = 1.0 - p_over

    return DistributionProbabilityResult(
        p_over=round(p_over, 6),
        p_under=round(p_under, 6),
        mu=round(mu_f, 4),
        sigma=round(sigma, 4),
        cv=round(cv, 4) if cv is not None else None,
        sigma_source=sigma_source,
        distribution="normal_cdf",
        clamped=clamped,
        note=None,
    )


__all__ = [
    "compute_distribution_probability",
    "DistributionProbabilityResult",
]
