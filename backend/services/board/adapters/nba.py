"""NBA board adapter."""
from __future__ import annotations
from typing import Dict, List, Optional

from config.version_tags import NBA_LIVE
from services.board.adapters.base import SportBoardAdapter


_NBA_SORT_KEYS = {
    # Match the existing NBA tier-adapter sort-key conventions so the
    # board order is identical to today's tier-collection ordering.
    "safe_haven": "pp_utility",
    "front_lines": "vision_score",
    "war_zone": "vision_score",
    "unqualified": "vision_score",
}

# Must mirror the mapping in `services/scoring/adapters/nba_scoring.py`
# build_context() so the fast-path canonical_key is byte-identical to
# the scoring adapter's persisted key. A divergence here breaks scoped
# ingest filtering.
_NBA_STAT_TYPE_MAP = {
    "player_points": "PTS", "player_rebounds": "REB", "player_assists": "AST",
    "player_points_rebounds_assists": "PRA",
    "player_points_alternate": "PTS", "player_rebounds_alternate": "REB",
    "player_assists_alternate": "AST",
    "player_points_rebounds_assists_alternate": "PRA",
}


class NBABoardAdapter(SportBoardAdapter):
    sport = "nba"
    version_tag = NBA_LIVE
    # live_props_collection, scores_collection, cached_board_collection
    # are resolved via config.collections (the base class resolves them).
    tier_names = ("safe_haven", "front_lines", "war_zone")

    def sort_key_for_tier(self, tier: str) -> str:
        return _NBA_SORT_KEYS.get(tier, "vision_score")

    def canonical_key(self, prop: Dict) -> Optional[str]:
        """Hot-path canonical_key reconstruction — mirrors
        `NBAScoringAdapter.build_context()` exactly. Pure string I/O,
        no DB, no model inference. Used by the universal engine to
        pre-filter scoped-ingest batches in O(N) without running
        `build_context` across the entire live pool.

        Prefers the precomputed `canonical_key` already persisted on
        the live-props doc (matches MLB's behaviour). Falls back to
        field-by-field reconstruction only when the field is absent —
        which protects the universal delta path from silently
        zero-keying when adapter ↔ ingest field-name conventions
        drift (the cause of the 2026-04-29 NBA realtime miss)."""
        ck = prop.get("canonical_key")
        if ck:
            return ck
        player_name = prop.get("player_name")
        line = prop.get("line")
        if player_name is None or line is None:
            return None
        # Field-name resilience: accept either historical aliases or
        # the canonical names persisted by `universal_odds_sync`.
        market = (
            prop.get("market")
            or prop.get("market_key")
            or ""
        )
        stat_type = (
            prop.get("stat_type")
            or _NBA_STAT_TYPE_MAP.get(market, prop.get("stat_type_extracted") or market)
        )
        if not stat_type:
            return None
        direction = (
            prop.get("direction")
            or prop.get("recommendation")
            or "OVER"
        )
        side = "OVER" if "OVER" in direction.upper() else "UNDER"
        event_id = prop.get("event_id", "?")
        try:
            return f"nba|{event_id}|{player_name}|{stat_type}|{float(line)}|{side}"
        except (TypeError, ValueError):
            return None

    async def score_batch(self, db, canonical_keys: List[str]) -> List[Dict]:
        """Reserved for Step 5 (real-time ingest). Not invoked by the
        reader, scanner, or drift-sync paths shipped in Steps 1-4."""
        raise NotImplementedError(
            "NBABoardAdapter.score_batch() is reserved for Step 5 real-time "
            "ingest. Today the NBA pipeline runs via RebuildCoordinator."
        )
