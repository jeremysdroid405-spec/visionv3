"""
SSOT Data Access Layer
======================
SINGLE SOURCE OF TRUTH Architecture Enforcement

This module provides the ONLY authorized way to access player stats data.
All components MUST use this layer - direct database queries for stats are FORBIDDEN.

ARCHITECTURE:
┌─────────────────────────────────────────────────────────────────────────────┐
│                         SSOT DATA ARCHITECTURE                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  PIPE 1: Stats Vault (nba_master_hub_2026)                                 │
│  ├─ Source: Tank01 API (0400 EST CRON ONLY)                                │
│  ├─ Contains: baseline_stats, game_logs                                    │
│  └─ Read by: This module ONLY                                              │
│                                                                             │
│  PIPE 2: Live Wire (dg_cached_board / Active_Lines)                        │
│  ├─ Source: The Odds API (intraday polling)                                │
│  ├─ Contains: Live lines, odds, game times                                 │
│  └─ Read by: This module ONLY                                              │
│                                                                             │
│  INTERSECTION (Player Cards):                                              │
│  └─ Joined via player_name, hit rates calculated from PIPE 1 game_logs    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

FORBIDDEN:
- Frontend calling external APIs (Tank01, BallDontLie)
- Creating secondary internal APIs for stats
- Direct database queries bypassing this layer
"""

from typing import Dict, Any, List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
import logging

logger = logging.getLogger(__name__)


class SSOTDataLayer:
    """
    Single Source of Truth Data Access Layer.
    
    This class provides the ONLY authorized interface for accessing
    player statistics. All stats flow through nba_master_hub_2026.
    """
    
    # Protected structural fields - NEVER modified by stats sync
    PROTECTED_FIELDS = frozenset([
        "player_id",
        "player_name", 
        "display_name",
        "photo_url",
        "headshot_url",
        "team",
        "position",
        "nba_id",
        "espn_id",
        "tank01_id",
        "playerID"
    ])
    
    # Stats fields - ONLY modified by 0400 CRON
    STATS_FIELDS = frozenset([
        "baseline_stats",
        "game_logs",
        "baseline_stats_updated_at",
        "game_logs_updated_at",
        "stats_source"
    ])
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.master_hub = db.nba_master_hub_2026
        self.active_lines = db.dg_cached_board
        self._player_cache: Optional[Dict[str, Dict]] = None
    
    async def get_player_stats(self, player_name: str) -> Optional[Dict[str, Any]]:
        """
        PIPE 1: Get player statistics from NBA Master Hub.
        
        This is the ONLY authorized way to access player stats.
        Returns baseline_stats and game_logs for hit rate calculation.
        """
        if not player_name:
            return None
        
        player = await self.master_hub.find_one(
            {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0}
        )
        
        if player:
            logger.debug(f"[SSOT] Stats retrieved for {player_name} from master_hub")
        
        return player
    
    async def get_live_lines(self, player_name: str) -> List[Dict[str, Any]]:
        """
        PIPE 2: Get live betting lines from Active Lines cache.
        
        Returns current lines and odds - NO statistical data.
        """
        if not player_name:
            return []
        
        # Get active lines for this player
        player_data = await self.active_lines.find_one(
            {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0, "props": 1}
        )
        
        if player_data:
            return player_data.get("props", [])
        
        return []
    
    async def get_player_card_data(self, player_name: str) -> Dict[str, Any]:
        """
        INTERSECTION: Join PIPE 1 (stats) + PIPE 2 (lines) for UI rendering.
        
        This is the primary method for rendering player cards.
        Returns unified data with stats calculated from game_logs.
        """
        # PIPE 1: Get stats from master hub
        stats_data = await self.get_player_stats(player_name)
        
        # PIPE 2: Get live lines
        live_lines = await self.get_live_lines(player_name)
        
        if not stats_data:
            return {
                "player_name": player_name,
                "has_stats": False,
                "props": live_lines
            }
        
        # Join: Enrich lines with stats calculated from game_logs
        from services.stats_service import calculate_coupled_stats
        
        game_logs = stats_data.get("game_logs", [])
        baseline_stats = stats_data.get("baseline_stats", {})
        
        enriched_props = []
        for prop in live_lines:
            stat_type = prop.get("stat_type_extracted") or prop.get("stat_type", "")
            line = prop.get("line", 0)
            
            # Calculate hit rates from PIPE 1 game_logs
            if game_logs and stat_type and line > 0:
                coupled = calculate_coupled_stats(game_logs, stat_type, line)
                prop["l5_avg"] = coupled["l5"]["avg"]
                prop["l10_avg"] = coupled["l10"]["avg"]
                prop["season_avg"] = coupled["season"]["avg"]
                prop["l5_hit_rate"] = coupled["l5"]["hit_rate"]
                prop["l10_hit_rate"] = coupled["l10"]["hit_rate"]
                prop["l5_games_over"] = coupled["l5"]["games_over"]
                prop["l10_games_over"] = coupled["l10"]["games_over"]
                prop["stats_source"] = "ssot_master_hub"
            else:
                # Fallback to baseline_stats if no game_logs
                stat_key = stat_type.replace("+", "")
                stat_data = baseline_stats.get(stat_key, {})
                prop["l5_avg"] = stat_data.get("l5_avg")
                prop["l10_avg"] = stat_data.get("l10_avg")
                prop["season_avg"] = stat_data.get("season_avg")
                prop["stats_source"] = "ssot_baseline"
            
            enriched_props.append(prop)
        
        return {
            "player_name": player_name,
            "display_name": stats_data.get("display_name"),
            "photo_url": stats_data.get("headshot_url") or stats_data.get("photo_url"),
            "team": stats_data.get("team"),
            "position": stats_data.get("position"),
            "has_stats": True,
            "games_played": baseline_stats.get("games_played", 0),
            "stats_updated_at": stats_data.get("baseline_stats_updated_at"),
            "props": enriched_props
        }
    
    async def build_player_lookup_cache(self) -> Dict[str, Dict]:
        """
        Build a cached lookup of all players from master hub.
        Used for efficient name matching and data access.
        """
        if self._player_cache is not None:
            return self._player_cache
        
        self._player_cache = {}
        
        players = await self.master_hub.find(
            {},
            {"_id": 0, "player_id": 1, "nba_id": 1, "espn_id": 1, 
             "headshot_url": 1, "team": 1, "position": 1, 
             "display_name": 1, "baseline_stats": 1, "game_logs": 1}
        ).to_list(1500)
        
        for player in players:
            display_name = player.get("display_name", "")
            if not display_name:
                continue
            
            name_lower = display_name.lower()
            self._player_cache[name_lower] = player
            
            # Handle name variations (Jr., periods, etc.)
            for suffix in [" jr.", " jr", " sr.", " sr", " ii", " iii", " iv"]:
                if name_lower.endswith(suffix):
                    base_name = name_lower[:-len(suffix)]
                    if base_name not in self._player_cache:
                        self._player_cache[base_name] = player
            
            if "." in display_name:
                no_periods = display_name.replace(".", "").lower()
                if no_periods not in self._player_cache:
                    self._player_cache[no_periods] = player
        
        logger.info(f"[SSOT] Player cache built: {len(self._player_cache)} entries")
        return self._player_cache
    
    def invalidate_cache(self):
        """Invalidate the player cache (call after sync)."""
        self._player_cache = None
        logger.info("[SSOT] Player cache invalidated")


# Singleton instance
_ssot_layer: Optional[SSOTDataLayer] = None


def get_ssot_layer(db: AsyncIOMotorDatabase) -> SSOTDataLayer:
    """Get or create the SSOT data layer singleton."""
    global _ssot_layer
    if _ssot_layer is None:
        _ssot_layer = SSOTDataLayer(db)
        logger.info("[SSOT] Data layer initialized - enforcing single source of truth")
    return _ssot_layer


def invalidate_ssot_cache():
    """Invalidate SSOT cache (call after 0400 sync)."""
    global _ssot_layer
    if _ssot_layer:
        _ssot_layer.invalidate_cache()
