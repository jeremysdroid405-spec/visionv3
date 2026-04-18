"""MLB board adapter."""
from __future__ import annotations
from typing import Dict, List

from services.board.adapters.base import SportBoardAdapter


_MLB_SORT_KEYS = {
    "safe_haven": "pp_utility",
    "front_lines": "vision_score",
    "war_zone": "vision_score",
    "unqualified": "vision_score",
}


class MLBBoardAdapter(SportBoardAdapter):
    sport = "mlb"
    version_tag = "final-mlb"
    # live_props_collection, scores_collection, cached_board_collection
    # are resolved via config.collections (the base class resolves them).
    tier_names = ("safe_haven", "front_lines", "war_zone")

    def sort_key_for_tier(self, tier: str) -> str:
        return _MLB_SORT_KEYS.get(tier, "vision_score")

    async def score_batch(self, db, canonical_keys: List[str]) -> List[Dict]:
        raise NotImplementedError(
            "MLBBoardAdapter.score_batch() is reserved for Step 5 real-time "
            "ingest. Today the MLB pipeline runs via RebuildCoordinator."
        )
