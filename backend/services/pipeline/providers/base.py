"""Universal Pipeline — provider interfaces.

Two protocols define the contracts every input / output adapter
implements:

  IInputProvider     — loads scoring-shape props for one snapshot.
  IOutputWriter      — persists run + outputs + cards for one run.

Phase B intentionally uses `typing.Protocol` (structural) instead of
abstract base classes — keeps providers decoupled from imports of
each other and makes mocking trivial.
"""
from __future__ import annotations

from typing import (
    Any, Dict, List, Optional, Protocol, runtime_checkable,
)


@runtime_checkable
class IInputProvider(Protocol):
    """Loads props for one (sport, mode, snapshot_time) tuple.

    Output contract: returns a list of prop dicts in the LIVE prop
    shape — i.e. one row per (player × stat × line × side) with
    flat book-price fields. The runner immediately hands this list
    to `apply_production_eligibility` (the Phase A SSOT), so
    providers MUST NOT pre-filter PP playability or coverage — that
    is the SSOT's job.
    """

    sport: str
    mode: str        # "live" | "historical"
    name: str        # human-readable provider name for audit envelope

    async def load_props(self, db) -> List[Dict[str, Any]]:
        """Return the scoring-shape props."""
        ...

    def describe_source(self) -> Dict[str, Any]:
        """Return audit-envelope source descriptor:
        `{"source_collections": [...], "input_snapshot_hash": str|None,
          "extras": {...}}`. Stamped on the run doc unchanged."""
        ...


@runtime_checkable
class IOutputWriter(Protocol):
    """Persists run doc + per-prop outputs + cards for one run.

    Phase B writes through the existing
    `services.replay.production_replay_runner.run_production_replay`
    function which already implements the universal write contract;
    output writers only choose which namespace (production vs test)
    that runner targets.
    """

    output_namespace: str   # "production_replay" | "test"
    name: str

    def describe(self) -> Dict[str, Any]:
        """Audit-envelope descriptor stamped on the run doc."""
        ...


__all__ = ["IInputProvider", "IOutputWriter"]
