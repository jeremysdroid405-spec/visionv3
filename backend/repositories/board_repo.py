"""
Board Repository - Cached Board and Props
==========================================
Handles cached board and live props operations.
"""
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from .base import BaseRepository
from services.config.collection_names import COLL
import logging

logger = logging.getLogger(__name__)


class BoardRepository:
    """Repository for managing board and props collections"""
    
    def __init__(self, db):
        self.db = db
        self.cached_board = BaseRepository(db[COLL("board_cache", "nba")])
        # Wave 1 shadow-writes: COLL.handle returns a ShadowWriter that
        # fans mutations out to `nba_live_props` while delegating reads
        # to the current primary (`dg_live_props`).
        self.live_props = BaseRepository(COLL.handle(db, "live_props", "nba"))
    
    # ==================== CACHED BOARD ====================
    
    async def get_cached_board(self, limit: int = None) -> List[Dict]:
        """Get all players from cached board"""
        return await self.cached_board.find_many(
            query={},
            sort=[("player_name", 1)],
            limit=limit
        )
    
    async def get_player_from_board(self, player_name: str) -> Optional[Dict]:
        """Get single player from cached board"""
        return await self.cached_board.find_one({"player_name": player_name})
    
    async def get_player_names(self) -> List[str]:
        """Get distinct player names from board"""
        return await self.cached_board.distinct("player_name")
    
    async def save_cached_board(self, players: List[Dict]) -> int:
        """Replace entire cached board"""
        await self.cached_board.delete_many()
        if players:
            return await self.cached_board.insert_many(players)
        return 0
    
    async def update_player_on_board(self, player_name: str, update_data: Dict) -> bool:
        """Update single player on board"""
        return await self.cached_board.update_one(
            {"player_name": player_name},
            {"$set": update_data}
        )
    
    async def get_board_count(self) -> int:
        """Count players on board"""
        return await self.cached_board.count()
    
    async def get_board_by_team(self, team: str) -> List[Dict]:
        """Get players by team"""
        return await self.cached_board.find_many({"team": team})
    
    async def get_board_by_stat_type(self, stat_type: str) -> List[Dict]:
        """Get props by stat type"""
        return await self.cached_board.find_many(
            query={},
            projection={"_id": 0, "player_name": 1, "team": 1, f"props.{stat_type}": 1}
        )
    
    # ==================== LIVE PROPS ====================
    
    async def get_live_props(self) -> List[Dict]:
        """Get all live props"""
        return await self.live_props.find_many()
    
    async def get_props_for_player(self, player_name: str) -> List[Dict]:
        """Get props for specific player"""
        return await self.live_props.find_many({"player_name": player_name})
    
    async def save_live_props(self, props: List[Dict]) -> int:
        """Replace all live props"""
        await self.live_props.delete_many()
        if props:
            return await self.live_props.insert_many(props)
        return 0
    
    async def upsert_prop(self, composite_key: str, prop_data: Dict) -> bool:
        """Upsert single prop by composite key"""
        return await self.live_props.update_one(
            {"composite_key": composite_key},
            {"$set": prop_data},
            upsert=True
        )
    
    async def get_props_count(self) -> int:
        """Count live props"""
        return await self.live_props.count()
    
    # ==================== AGGREGATIONS ====================
    
    async def get_props_by_game(self) -> Dict[str, List[Dict]]:
        """Group props by game"""
        pipeline = [
            {"$group": {
                "_id": "$game_key",
                "props": {"$push": "$$ROOT"},
                "count": {"$sum": 1}
            }},
            {"$project": {"_id": 0, "game_key": "$_id", "props": 1, "count": 1}}
        ]
        results = await self.cached_board.aggregate(pipeline)
        return {r["game_key"]: r["props"] for r in results if r.get("game_key")}
    
    async def get_stats_summary(self) -> Dict[str, Any]:
        """Get statistics summary for board"""
        pipeline = [
            {"$unwind": "$props"},
            {"$group": {
                "_id": None,
                "total_props": {"$sum": 1},
                "avg_hit_rate": {"$avg": "$props.h10_rate"},
                "players": {"$addToSet": "$player_name"}
            }},
            {"$project": {
                "_id": 0,
                "total_props": 1,
                "avg_hit_rate": 1,
                "player_count": {"$size": "$players"}
            }}
        ]
        results = await self.cached_board.aggregate(pipeline)
        return results[0] if results else {}
