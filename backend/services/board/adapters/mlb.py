"""MLB board adapter."""
from __future__ import annotations
from typing import Dict, List, Optional

from services.board.adapters.base import SportBoardAdapter


_MLB_SORT_KEYS = {
    "safe_haven": "pp_utility",
    "front_lines": "vision_score",
    "war_zone": "vision_score",
    "unqualified": "vision_score",
}


class MLBBoardAdapter(SportBoardAdapter):
    sport = "mlb"
    # Stage 7 (2026-04-21, MLB↔NBA carbon-copy): the live UI reader now pins
    # to the real-time shadow tag `final-mlb-rt` — architectural parity with
    # NBA (whose `NBABoardAdapter.version_tag = "final-nba-rt"`). MLB master
    # sync's Step 6 writes `final-mlb` (canonical baseline); Step 6-RT writes
    # `final-mlb-rt` (live UI tag) with bit-identical score fields. Future
    # event-driven mechanisms (MLB injury-triggered partial rescore, analog
    # of `services/injury_triggered_rescore.py`) will patch `final-mlb-rt`
    # in seconds when an event arrives — matching NBA's live behaviour.
    # Eliminates D9.
    version_tag = "final-mlb-rt"
    # live_props_collection, scores_collection, cached_board_collection
    # are resolved via config.collections (the base class resolves them).
    tier_names = ("safe_haven", "front_lines", "war_zone")

    def sort_key_for_tier(self, tier: str) -> str:
        return _MLB_SORT_KEYS.get(tier, "vision_score")

    def canonical_key(self, prop: Dict) -> Optional[str]:
        """Hot-path canonical_key — mirrors
        `MLBScoringAdapter.build_context()` exactly. MLB live props
        usually carry the pre-computed `canonical_key` field already,
        so this is a pointer-read in the common case. Fallback
        reconstruction matches the scoring adapter's format."""
        ck = prop.get("canonical_key")
        if ck:
            return ck
        player_name = prop.get("player_name")
        stat_type = prop.get("stat_type")
        line = prop.get("line")
        if player_name is None or line is None or stat_type is None:
            return None
        event_id = prop.get("event_id", "?")
        rec = prop.get("recommendation", "OVER")
        try:
            return f"mlb|{event_id}|{player_name}|{stat_type}|{float(line)}|{rec}"
        except (TypeError, ValueError):
            return None

    async def score_batch(self, db, canonical_keys: List[str]) -> List[Dict]:
        raise NotImplementedError(
            "MLBBoardAdapter.score_batch() is reserved for Step 5 real-time "
            "ingest. Today the MLB pipeline runs via RebuildCoordinator."
        )
