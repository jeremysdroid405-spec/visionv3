"""Universal Production Pipeline package.

Phase A scope (2026-05-17):
  • SSOT extraction of the live production eligibility chain
    (`apply_production_eligibility`) used by every sport's
    `load_live_props` entry point.
  • Hardcoded PrizePicks playability registry as a fail-closed
    fallback for historical / test inputs that lack `pp_layer`.

Phase B+ scope (NOT in this session):
  • `runner.run_pipeline(sport, mode, ...)` orchestrator.
  • `IInputProvider` / `IOutputWriter` interfaces.
  • `LiveInputProvider`, `HistoricalInputProvider`,
    `ProductionOutputWriter`, `TestOutputWriter`.
"""

from services.pipeline.eligibility import (
    apply_production_eligibility,
    EligibilityResult,
)
from services.pipeline.pp_playability_registry import (
    is_pp_playable_side,
    SPORT_PP_SIDE_REGISTRY,
)

__all__ = [
    "apply_production_eligibility",
    "EligibilityResult",
    "is_pp_playable_side",
    "SPORT_PP_SIDE_REGISTRY",
]
