"""Universal opportunity-model layer (2026-04-23).

Public API:
    from services.opportunity import (
        OpportunityAdapter, OpportunityOutput, PlayerContext,
        get_adapter,
    )

    adapter = get_adapter("nba")
    out = adapter.predict(PlayerContext(sport="nba", player_id="...",
                                        history_logs=[...], ...))

NBA is implemented and uses the strict 12-feat expected-minutes model
plus the 15-feat low-minutes classifier (both trained earlier). MLB
and NFL are scaffolds that conform to the contract but return
sentinel outputs.
"""
from __future__ import annotations

from typing import Dict

from .base import (
    OpportunityAdapter,
    OpportunityBucket,
    OpportunityOutput,
    OpportunityType,
    PlayerContext,
    Sport,
    bucket_from_value,
)
from .mlb import MLBOpportunityAdapter
from .nba import NBAOpportunityAdapter
from .nfl import NFLOpportunityAdapter

_ADAPTER_REGISTRY: Dict[str, OpportunityAdapter] = {}


def get_adapter(sport: str) -> OpportunityAdapter:
    """Return (lazily constructed + cached) sport-specific adapter."""
    key = sport.lower()
    if key not in _ADAPTER_REGISTRY:
        if key == "nba":
            _ADAPTER_REGISTRY[key] = NBAOpportunityAdapter()
        elif key == "mlb":
            _ADAPTER_REGISTRY[key] = MLBOpportunityAdapter()
        elif key == "nfl":
            _ADAPTER_REGISTRY[key] = NFLOpportunityAdapter()
        else:
            raise ValueError(f"unknown sport: {sport}")
    return _ADAPTER_REGISTRY[key]


__all__ = [
    "OpportunityAdapter", "OpportunityOutput", "PlayerContext",
    "OpportunityType", "OpportunityBucket", "Sport",
    "bucket_from_value", "get_adapter",
    "NBAOpportunityAdapter", "MLBOpportunityAdapter", "NFLOpportunityAdapter",
]
