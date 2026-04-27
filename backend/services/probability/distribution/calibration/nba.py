"""
NBA stat-family calibration table for the universal probability engine.

Selection rules per family:
- High-volume continuous stats (PTS, REB, AST, 3PM, TO, PRA, P+R, P+A,
  R+A, BLK+STL): Normal CDF. The NBA scoring path supplies the empirical
  residual σ from the VK / VK2 projection model, which the universal
  engine honours via the `sigma=` arg in `compute_probability`.
- Low-count event stats at 0.5 lines (STL, BLK): Poisson. At higher
  lines (1.5+), Normal CDF with the empirical σ takes over again
  because elite players have wide enough variance that the count
  approximation breaks down.

When `sigma` is passed in by the caller, the Normal CDF distribution
short-circuits the CV-derived σ resolution and uses the provided value
directly (`sigma_source="explicit_empirical"`). This makes the
calibration tables below act as routing-only configurations — the
actual σ values come from the projection model.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..base import Distribution
from ..normal import NormalCDFDistribution, NormalCDFConfig
from ..poisson import PoissonDistribution, PoissonConfig
from ..registry import FamilySpec


def _normal(sigma_min: float = 0.50) -> NormalCDFDistribution:
    """Routing-only Normal CDF. σ is overridden by the caller-supplied
    empirical residual σ from the VK / VK2 projection model.
    `cv_floor` and `mu_floor` are inert when the explicit-sigma fast
    path fires."""
    return NormalCDFDistribution(NormalCDFConfig(
        cv_floor=0.30, mu_floor=0.0,
        sigma_min_absolute=sigma_min,
        mu_floor_capped=False,
    ))


def _poisson_event() -> PoissonDistribution:
    return PoissonDistribution(PoissonConfig(
        lambda_min=1e-3, lambda_max=10.0,
    ))


def _stl_blk_selector(
    line: float, mu: Optional[float], cv: Optional[float], extras: Optional[Dict[str, Any]],
) -> Distribution:
    """STL / BLK at line ≤ 0.5 → Poisson (true rare-event count).
    Higher lines fall back to Normal CDF with the empirical σ supplied
    by the projection model."""
    try:
        ln = float(line)
    except (TypeError, ValueError):
        ln = 0.5
    if ln <= 0.5:
        return _poisson_event()
    return _normal(sigma_min=0.30)


# ---------------------------------------------------------------------------
# NBA family table.
# Keys are CANONICAL display tokens — registry normalises to lowercase
# and underscores them.
# ---------------------------------------------------------------------------
NBA_FAMILIES: Dict[str, FamilySpec] = {
    # ----- High-volume continuous (Normal CDF + empirical σ) ------------
    "PTS":     FamilySpec(default=_normal(sigma_min=1.50),
                          notes="Empirical σ ≈ 6-8 from VK residuals."),
    "REB":     FamilySpec(default=_normal(sigma_min=0.80),
                          notes="Empirical σ ≈ 2.5-3.5."),
    "AST":     FamilySpec(default=_normal(sigma_min=0.80),
                          notes="Empirical σ ≈ 2.0-3.0."),
    "3PM":     FamilySpec(default=_normal(sigma_min=0.50),
                          notes="Empirical σ ≈ 1.0-1.5."),
    "TO":      FamilySpec(default=_normal(sigma_min=0.50),
                          notes="Empirical σ ≈ 1.0-1.5."),
    # ----- Combo families (synth or composed; Normal CDF + empirical σ) -
    "PRA":     FamilySpec(default=_normal(sigma_min=2.00),
                          notes="Empirical σ ≈ 7-9 (covariance-aware)."),
    "P+R":     FamilySpec(default=_normal(sigma_min=1.80),
                          notes="Empirical σ ≈ 6-8."),
    "P+A":     FamilySpec(default=_normal(sigma_min=1.80),
                          notes="Empirical σ ≈ 6-8."),
    "R+A":     FamilySpec(default=_normal(sigma_min=1.20),
                          notes="Empirical σ ≈ 3.5-4.5."),
    # Underscore tokens used internally by `_resolve_family()` for the
    # primary combo synth path (no direct trained model exists for
    # these). They share the same routing as their `+`-form display
    # variants above.
    "pts_reb": FamilySpec(default=_normal(sigma_min=1.80),
                          notes="Synth P+R."),
    "pts_ast": FamilySpec(default=_normal(sigma_min=1.80),
                          notes="Synth P+A."),
    "reb_ast": FamilySpec(default=_normal(sigma_min=1.20),
                          notes="Synth R+A."),
    "BLK+STL": FamilySpec(default=_normal(sigma_min=0.60),
                          notes="Combined σ from independent components."),
    # ----- Low-count event stats — Poisson at 0.5, Normal+σ at higher --
    "STL":     FamilySpec(default=_poisson_event(),
                          selector=_stl_blk_selector,
                          notes="STL μ ≈ 0.5-1.5; Poisson at 0.5 line."),
    "BLK":     FamilySpec(default=_poisson_event(),
                          selector=_stl_blk_selector,
                          notes="BLK μ ≈ 0.3-1.5; Poisson at 0.5 line."),
}


__all__ = ["NBA_FAMILIES"]
