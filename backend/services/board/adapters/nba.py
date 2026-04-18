"""NBA board adapter."""
from __future__ import annotations
from typing import Dict, List

from services.board.adapters.base import SportBoardAdapter


_NBA_SORT_KEYS = {
    # Match the existing NBA tier-adapter sort-key conventions so the
    # board order is identical to today's tier-collection ordering.
    "safe_haven": "pp_utility",
    "front_lines": "vision_score",
    "war_zone": "vision_score",
    "unqualified": "vision_score",
}


class NBABoardAdapter(SportBoardAdapter):
    sport = "nba"
    version_tag = "final-nba"
    # live_props_collection, scores_collection, cached_board_collection
    # are resolved via config.collections (the base class resolves them).
    tier_names = ("safe_haven", "front_lines", "war_zone")

    def sort_key_for_tier(self, tier: str) -> str:
        return _NBA_SORT_KEYS.get(tier, "vision_score")

    async def score_batch(self, db, canonical_keys: List[str]) -> List[Dict]:
        """Reserved for Step 5 (real-time ingest). Not invoked by the
        reader, scanner, or drift-sync paths shipped in Steps 1-4."""
        raise NotImplementedError(
            "NBABoardAdapter.score_batch() is reserved for Step 5 real-time "
            "ingest. Today the NBA pipeline runs via RebuildCoordinator."
        )
