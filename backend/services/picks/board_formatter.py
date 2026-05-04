"""
Board Formatter
===============
Service for formatting board data for frontend consumption.
Extracted from picks_getter_service.py for modularity.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.config.collection_names import COLL

from .game_utils import clean_object_ids

logger = logging.getLogger(__name__)


class BoardFormatter:
    """
    Formats board data for frontend consumption.
    
    Reads from nba_cached_board and formats responses
    for the /v3/board and related endpoints.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.cached_board = db[COLL("board_cache", "nba")]
        self.sync_status = db.dg_sync_status
    
    async def get_cached_board(self) -> Dict[str, Any]:
        """
        Get the full cached board for frontend.
        
        Returns:
            {
                "success": bool,
                "synced_at": datetime,
                "players_count": int,
                "total_props": int,
                "players": [...]
            }
        """
        try:
            # Get sync status
            status = await self.sync_status.find_one({}) or {}
            synced_at = status.get("last_sync_completed") or status.get("last_sync")
            
            # Get all players from cached board
            players = []
            cursor = self.cached_board.find({}, {"_id": 0})
            
            async for player in cursor:
                clean_object_ids(player)
                self._flatten_hit_rates_to_props(player)
                players.append(player)
            
            # Count total props
            total_props = sum(len(p.get("props", [])) for p in players)
            
            return {
                "success": True,
                "synced_at": synced_at,
                "players_count": len(players),
                "total_props": total_props,
                "players": players
            }
            
        except Exception as e:
            logger.error(f"[BOARD] Failed to get cached board: {e}")
            return {
                "success": False,
                "error": str(e),
                "players_count": 0,
                "total_props": 0,
                "players": []
            }
    
    async def get_cached_player(self, player_name: str) -> Optional[Dict]:
        """
        Get a single player from cached board.
        
        Args:
            player_name: Player's name
            
        Returns:
            Player dictionary or None
        """
        try:
            player = await self.cached_board.find_one(
                {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
                {"_id": 0}
            )
            
            if player:
                clean_object_ids(player)
                self._flatten_hit_rates_to_props(player)
            
            return player
            
        except Exception as e:
            logger.error(f"[BOARD] Failed to get player {player_name}: {e}")
            return None
    
    async def get_players_by_team(self, team: str) -> List[Dict]:
        """Get all players from a specific team."""
        players = []
        cursor = self.cached_board.find(
            {"team": {"$regex": f"^{team}$", "$options": "i"}},
            {"_id": 0}
        )
        
        async for player in cursor:
            clean_object_ids(player)
            players.append(player)
        
        return players
    
    async def search_players(self, query: str, limit: int = 20) -> List[Dict]:
        """
        Search players by name.
        
        Args:
            query: Search query
            limit: Max results to return
            
        Returns:
            List of matching players
        """
        try:
            players = []
            cursor = self.cached_board.find(
                {"player_name": {"$regex": query, "$options": "i"}},
                {"_id": 0}
            ).limit(limit)
            
            async for player in cursor:
                clean_object_ids(player)
                players.append(player)
            
            return players
            
        except Exception as e:
            logger.error(f"[BOARD] Search failed for '{query}': {e}")
            return []
    
    async def get_most_popular_bets(self, limit: int = 10) -> Dict[str, Any]:
        """
        Get most popular bets based on prop count and hit rates.
        
        Returns:
            Dictionary with popular bets organized by category
        """
        try:
            all_picks = []
            cursor = self.cached_board.find({}, {"_id": 0})
            
            async for player in cursor:
                for prop in player.get("props", []):
                    pick = {
                        "player_name": player.get("player_name"),
                        "team": player.get("team"),
                        "photo_url": player.get("photo_url"),
                        **prop
                    }
                    clean_object_ids(pick)
                    all_picks.append(pick)
            
            # Sort by hit rate
            def get_hit_rate(p):
                return p.get("h10_rate") or p.get("h5_rate") or 0
            
            sorted_picks = sorted(all_picks, key=get_hit_rate, reverse=True)
            
            return {
                "success": True,
                "count": len(sorted_picks[:limit]),
                "picks": sorted_picks[:limit],
                "generated_at": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"[BOARD] Failed to get popular bets: {e}")
            return {"success": False, "error": str(e), "picks": []}
    
    def _flatten_hit_rates_to_props(self, player: Dict) -> None:
        """
        Flatten hit_rates nested object to prop level for frontend.
        
        Modifies player in place.
        """
        props = player.get("props", [])
        
        for prop in props:
            hit_rates = prop.get("hit_rates", {})
            if isinstance(hit_rates, dict):
                # Flatten hit_rates to prop level
                prop["h5_rate"] = prop.get("h5_rate") or hit_rates.get("h5_rate") or hit_rates.get("l5_rate")
                prop["h10_rate"] = prop.get("h10_rate") or hit_rates.get("h10_rate") or hit_rates.get("l10_rate")
                prop["l5_avg"] = prop.get("l5_avg") or hit_rates.get("l5_avg")
                prop["l10_avg"] = prop.get("l10_avg") or hit_rates.get("l10_avg")
                prop["season_avg"] = prop.get("season_avg") or hit_rates.get("season_avg")
    
    async def get_board_stats(self) -> Dict[str, Any]:
        """Get statistics about the cached board."""
        try:
            player_count = await self.cached_board.count_documents({})
            
            # Count props
            pipeline = [
                {"$project": {"prop_count": {"$size": {"$ifNull": ["$props", []]}}}},
                {"$group": {"_id": None, "total": {"$sum": "$prop_count"}}}
            ]
            result = await self.cached_board.aggregate(pipeline).to_list(1)
            total_props = result[0]["total"] if result else 0
            
            # Get sync status
            status = await self.sync_status.find_one({}) or {}
            
            return {
                "player_count": player_count,
                "total_props": total_props,
                "last_sync": status.get("last_sync_completed"),
                "is_syncing": status.get("is_syncing", False)
            }
            
        except Exception as e:
            logger.error(f"[BOARD] Failed to get stats: {e}")
            return {"error": str(e)}
