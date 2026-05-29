"""
TeamProjectionAdapter — contract for sport-specific team projection
engines.

Architecture: /app/memory/TEAM_PROPS_ARCHITECTURE.md §2.1 + §11.

Phase 1.A.0 ships the ABC only. No implementations.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional

# 2026-06-02 — Locked in §2.3 of the architecture doc. Any
# distribution string returned by a subclass MUST be one of these.
SUPPORTED_DISTRIBUTIONS = frozenset({
    "normal",   # NFL / NBA totals, passing yards
    "poisson",  # MLB strikeouts
    "nbinom",   # MLB runs / hits / total bases
    "mixture",  # escalation path for right-tail markets
})


@dataclass(frozen=True)
class TeamProjection:
    """Pure value object returned by every adapter.

    The shape is fixed by §1.2 (`team_projections` collection schema)
    so the persistence layer can write any adapter's output without
    branching on sport.
    """
    distribution: str
    mu: float
    sigma: Optional[float]          # set for `normal`; None otherwise
    dispersion_k: Optional[float]   # set for `nbinom`; None otherwise
    model_version: str
    confidence_metric: float        # cross-val fold score; bounded [0, 1]

    def __post_init__(self) -> None:
        # Hard invariants. A misconfigured adapter must fail loudly
        # at construction time, not silently downstream.
        if self.distribution not in SUPPORTED_DISTRIBUTIONS:
            raise ValueError(
                f"distribution={self.distribution!r} not in "
                f"SUPPORTED_DISTRIBUTIONS={sorted(SUPPORTED_DISTRIBUTIONS)}"
            )
        if self.distribution == "normal" and self.sigma is None:
            raise ValueError("distribution='normal' requires sigma")
        if self.distribution == "nbinom" and self.dispersion_k is None:
            raise ValueError("distribution='nbinom' requires dispersion_k")
        if not (0.0 <= self.confidence_metric <= 1.0):
            raise ValueError(
                f"confidence_metric={self.confidence_metric} must be in [0, 1]"
            )


class TeamProjectionAdapter(ABC):
    """Sport × market projection engine.

    Subclasses live under `services/team_projections/<sport>/`
    (e.g. `services/team_projections/mlb/runs.py`) and are
    registered by `(sport, market)` into a runtime dispatch table.
    No business logic lives on the ABC.
    """

    #: Subclass MUST set both. Used by the runtime registry.
    sport: str = ""    # e.g. "mlb"
    market: str = ""   # e.g. "team_total_runs"

    @abstractmethod
    def project(
        self,
        *,
        event_id: str,
        team_id: str,
        features: Dict[str, Any],
        context: Dict[str, Any],
    ) -> TeamProjection:
        """Build a TeamProjection from a leak-audited feature vector.

        Inputs MUST be pre-game (`team_features.leakage_audit_passed=True`).
        The adapter is forbidden from reading the live odds market
        directly — anchoring against odds happens later in the TP
        engine, not here.
        """
        raise NotImplementedError
