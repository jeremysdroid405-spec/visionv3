"""
NFL stat-family calibration table — STUB.

NFL props will follow the MLB calibration pattern once the player
projection model lands:
- Continuous yardage stats (Rushing Yards, Receiving Yards, Passing
  Yards) → Normal CDF.
- Rare-event 0.5-line stats (Anytime TD, First TD) → Bernoulli
  / Poisson.
- Bursty count stats (Receptions, Completions) → Negative Binomial
  with player-CV-derived dispersion.

Today every NFL family resolves to a conservative Normal CDF
placeholder.
"""
from __future__ import annotations

from typing import Dict

from ..normal import NormalCDFDistribution, NormalCDFConfig
from ..registry import FamilySpec


def _placeholder_normal() -> NormalCDFDistribution:
    return NormalCDFDistribution(NormalCDFConfig(
        cv_floor=0.50, mu_floor=0.0,
        sigma_min_absolute=1.0,
        mu_floor_capped=False,
    ))


NFL_FAMILIES: Dict[str, FamilySpec] = {
    # Yardage families (placeholders pending projection model)
    "Rushing Yards":    FamilySpec(default=_placeholder_normal()),
    "Receiving Yards":  FamilySpec(default=_placeholder_normal()),
    "Passing Yards":    FamilySpec(default=_placeholder_normal()),
    # Count families (placeholders)
    "Receptions":       FamilySpec(default=_placeholder_normal()),
    "Completions":      FamilySpec(default=_placeholder_normal()),
    # Rare events (placeholders — should switch to Bernoulli/Poisson later)
    "Anytime TD":       FamilySpec(default=_placeholder_normal()),
    "First TD":         FamilySpec(default=_placeholder_normal()),
}


__all__ = ["NFL_FAMILIES"]
