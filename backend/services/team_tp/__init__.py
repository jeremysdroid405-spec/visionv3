"""
Team True-Probability engine.

Phase 1.A.0 ships the contract only. Distribution math, devig, and
the model+market blend land in later slices.

Architecture: /app/memory/TEAM_PROPS_ARCHITECTURE.md §3 + §11.9.
"""
from .base import TeamTPAdapter, TeamTPResult, TP_SOURCES

__all__ = ["TeamTPAdapter", "TeamTPResult", "TP_SOURCES"]
