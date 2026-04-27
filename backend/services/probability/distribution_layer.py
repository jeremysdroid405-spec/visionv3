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

# Per-family μ-floor used for σ scaling. Below this μ the player's
# CV-based dispersion would collapse to near-zero (because σ = CV × |μ|),
# producing |z| → ∞ and degenerate probabilities (0.01 / 0.99) on
# low-μ event props (e.g. a Hit 0.5 OVER for a player with 0.05 L20
# avg). The μ-floor establishes a minimum effective μ used **only**
# inside σ scaling — the projection itself (numerator of the z-score)
# is unchanged.
#
# Calibrated to the natural variance scale of each event family:
#   • Hits / Singles / Runs / RBIs / HRR — line is 0.5, σ should
#     allow ≈ ±1 stat-unit dispersion. floor=0.5 → σ_min ≈ 0.5×CV.
#   • Total Bases — line typically 1.5; floor=1.0.
#   • Doubles / Home Runs / Stolen Bases — rarer events but lines
#     are 0.5, so floor must be at least 0.3-0.5 to keep σ reasonable.
#   • Pitcher Strikeouts / Pitcher Outs / Earned Runs / Hits Allowed
#     — high-magnitude families with lines at 4-16; floors scale up.
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
    # 2026-04-27 — μ-floor diagnostic. `effective_mu` is the value
    # actually used inside σ scaling (= max(|μ|, μ_floor)). When the
    # floor was binding, `mu_floor_applied` is True and `sigma_source`
    # reflects "mu_floor_adjusted".
    effective_mu: Optional[float] = None
    mu_floor_applied: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _normal_cdf(z: float) -> float:
    """Standard normal CDF using math.erf (no scipy dep at this layer)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _resolve_sigma(
    mu: float,
    cv: Optional[float],
    stat_family: str,
) -> tuple[float, str, float, bool]:
    """Pick σ in priority order:
        1. CV-derived (cv * effective_mu) when cv is finite and ≥ family floor.
        2. Family-floor CV * effective_mu.
        3. Generic _CV_FLOOR_DEFAULT * effective_mu.

    `effective_mu = max(|μ|, μ_floor[stat_family])`. The μ-floor is
    NEVER applied to the projection itself — only to the magnitude
    used inside σ scaling, so the z-score numerator (line - μ) is
    unchanged. This stops σ from collapsing on low-μ event props.

    Returns
    -------
    (sigma, sigma_source, effective_mu, mu_floor_applied)
    """
    # 2026-04-27 fix: canonical MLB stat tokens come through as
    # space-separated lowercase ("home runs", "stolen bases", "pitcher
    # outs"), but the floor tables historically used snake_case keys
    # ("home_runs", ...). Normalize to snake_case so both formats hit.
    # NBA tokens (no spaces / already canonical compact form) are
    # unaffected by the replacement.
    family = (stat_family or "").lower().strip().replace(" ", "_")
    floor_cv = _CV_FLOOR_BY_FAMILY.get(family, _CV_FLOOR_DEFAULT)
    mu_floor = _MU_FLOOR_BY_FAMILY.get(family, _MU_FLOOR_DEFAULT)

    raw_mu = abs(mu) if mu is not None else 0.0
    effective_mu = max(raw_mu, mu_floor)
    mu_floor_applied = effective_mu > raw_mu

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

    if mu_floor_applied:
        # Annotate so observability can count μ-floor usage
        # independently of which CV rung produced cv_used.
        sigma_source = sigma_source + "+mu_floor_adjusted"

    sigma = max(cv_used * effective_mu, _SIGMA_MIN_ABSOLUTE)
    return sigma, sigma_source, effective_mu, mu_floor_applied


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

    sigma, sigma_source, effective_mu, mu_floor_applied = _resolve_sigma(
        mu_f, cv, stat_family,
    )

    # Standard normal-CDF: probability that a Normal(μ, σ²) random
    # variable exceeds `line`. NOTE the z-score uses the *raw* μ_f
    # in the numerator — the μ-floor is only applied to σ scaling.
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
        effective_mu=round(effective_mu, 4),
        mu_floor_applied=mu_floor_applied,
    )


__all__ = [
    "compute_distribution_probability",
    "DistributionProbabilityResult",
]
