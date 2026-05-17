"""Universal Production Pipeline package.

Phase A (2026-05-17):
  • `apply_production_eligibility` SSOT
  • `SPORT_PP_SIDE_REGISTRY` fail-closed PP registry

Phase B (2026-05-17):
  • `run_pipeline(sport, mode, snapshot_time, output_namespace,
                  test_id, …)` orchestrator
  • Input providers (`LiveInputProvider`,
    `MLBHistoricalInputProvider`)
  • Output writers (`ProductionOutputWriter`, `TestOutputWriter`)
  • Audit envelope builder
"""

from services.pipeline.eligibility import (
    apply_production_eligibility, EligibilityResult,
)
from services.pipeline.pp_playability_registry import (
    is_pp_playable_side, SPORT_PP_SIDE_REGISTRY,
)
from services.pipeline.runner import run_pipeline
from services.pipeline.audit_envelope import (
    build_audit_envelope, PIPELINE_VERSION,
)

__all__ = [
    "apply_production_eligibility", "EligibilityResult",
    "is_pp_playable_side", "SPORT_PP_SIDE_REGISTRY",
    "run_pipeline",
    "build_audit_envelope", "PIPELINE_VERSION",
]
