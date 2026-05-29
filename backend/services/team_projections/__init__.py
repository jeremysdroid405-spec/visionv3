"""
Team Projection Engine.

Phase 1.A.0 ships ONLY the contract (`base.TeamProjectionAdapter`).
Sport-specific implementations land in later slices, each in its own
module under `services/team_projections/<sport>/<market>.py`.

Architecture: see /app/memory/TEAM_PROPS_ARCHITECTURE.md §2.1.
"""

from .base import (
    SUPPORTED_DISTRIBUTIONS,
    TeamProjection,
    TeamProjectionAdapter,
)

__all__ = [
    "SUPPORTED_DISTRIBUTIONS",
    "TeamProjection",
    "TeamProjectionAdapter",
]
