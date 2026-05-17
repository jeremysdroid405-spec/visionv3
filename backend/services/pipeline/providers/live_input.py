"""LiveInputProvider — wraps the existing live production adapter.

This provider's `load_props` does EXACTLY what the live serving
path does:

  sport_adapter.load_live_props(db)

…which internally calls `apply_production_eligibility` (Phase A
SSOT). The runner therefore does NOT re-run eligibility on live
mode — that would be a double-filter. The runner detects
`provider.mode == "live"` and skips its own eligibility call (the
adapter has already done it inside `load_live_props`).

This is the seam that guarantees Phase B live behaviour is
byte-identical to the current production live path.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.pipeline.providers.base import IInputProvider


class LiveInputProvider(IInputProvider):
    """Live mode input source.

    Args:
        sport: "mlb" | "nba" | "nfl".
    """

    def __init__(self, sport: str):
        self.sport = sport.lower()
        self.mode = "live"
        self.name = f"LiveInputProvider({self.sport})"

    async def load_props(self, db) -> List[Dict[str, Any]]:
        adapter = self._resolve_adapter(db)
        return await adapter.load_live_props(db)

    def _resolve_adapter(self, db):
        # Lazy import — adapters carry heavy model imports the
        # pipeline runner shouldn't trigger when only the
        # historical provider is needed.
        if self.sport == "mlb":
            from services.scoring.adapters.mlb_scoring import (
                MLBScoringAdapter,
            )
            return MLBScoringAdapter()
        if self.sport == "nba":
            from services.scoring.adapters.nba_scoring import (
                NBAScoringAdapter,
            )
            return NBAScoringAdapter()
        raise NotImplementedError(
            f"LiveInputProvider: unsupported sport {self.sport!r}"
        )

    def describe_source(self) -> Dict[str, Any]:
        return {
            "provider": self.name,
            "mode": self.mode,
            "sport": self.sport,
            "source_collections": [f"{self.sport}_live_props"],
            "input_snapshot_hash": None,
            "extras": {
                "eligibility_already_applied_by_adapter": True,
                "ssot_function": "apply_production_eligibility",
                "use_pp_registry_fallback": False,
            },
        }


__all__ = ["LiveInputProvider"]
