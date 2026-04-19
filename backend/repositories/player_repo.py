"""
Player Repository - Master Roster and Player Data
==================================================
Handles player roster and identity operations.
"""
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from .base import BaseRepository
from services.config.collection_names import COLL
import logging

logger = logging.getLogger(__name__)


class PlayerRepository:
    """Repository for managing player collections"""
    
    def __init__(self, db):
        self.db = db
        self.master_roster = BaseRepository(db[COLL("master_roster", "nba")])
        self.daily_insights = BaseRepository(db.dg_daily_insights)
        
        # In-memory cache for fast lookups
        self._team_cache: Dict[str, str] = {}
        self._player_cache: Dict[str, Dict] = {}
    
    # ==================== MASTER ROSTER ====================
    
    async def get_player_team(self, player_name: str) -> Optional[str]:
        """Get team abbreviation for a player (with caching)"""
        # Check cache first
        normalized = player_name.lower().strip()
        if normalized in self._team_cache:
            return self._team_cache[normalized]
        
        # Query database
        doc = await self.master_roster.find_one(
            {"normalized_name": normalized},
            {"team_abbreviation": 1}
        )
        
        if doc:
            team = doc.get("team_abbreviation")
            self._team_cache[normalized] = team
            return team
        
        return None
    
    async def get_player_info(self, player_name: str) -> Optional[Dict]:
        """Get full player info from roster"""
        # Check cache
        normalized = player_name.lower().strip()
        if normalized in self._player_cache:
            return self._player_cache[normalized]
        
        doc = await self.master_roster.find_one({"normalized_name": normalized})
        
        if doc:
            self._player_cache[normalized] = doc
            return doc
        
        return None
    
    async def get_all_players(self) -> List[Dict]:
        """Get all players from master roster"""
        return await self.master_roster.find_many(
            query={},
            sort=[("player_name", 1)]
        )
    
    async def get_players_by_team(self, team: str) -> List[Dict]:
        """Get all players for a team"""
        return await self.master_roster.find_many({"team_abbreviation": team})
    
    async def save_master_roster(self, players: List[Dict]) -> int:
        """Replace entire master roster"""
        await self.master_roster.delete_many()
        if players:
            count = await self.master_roster.insert_many(players)
            # Create indexes
            await self.master_roster.create_index("player_name")
            await self.master_roster.create_index("normalized_name")
            await self.master_roster.create_index("team_abbreviation")
            # Rebuild cache
            await self._rebuild_team_cache()
            return count
        return 0
    
    async def update_player_roster(self, player_name: str, update_data: Dict) -> bool:
        """Update single player in roster"""
        success = await self.master_roster.update_one(
            {"player_name": player_name},
            {"$set": update_data}
        )
        # Invalidate cache
        normalized = player_name.lower().strip()
        self._team_cache.pop(normalized, None)
        self._player_cache.pop(normalized, None)
        return success
    
    async def get_roster_count(self) -> int:
        """Count players in roster"""
        return await self.master_roster.count()
    
    async def search_players(self, query: str, limit: int = 10) -> List[Dict]:
        """Search players by name"""
        search_lower = query.lower()
        return await self.master_roster.find_many(
            query={"normalized_name": {"$regex": search_lower}},
            limit=limit
        )
    
    async def _rebuild_team_cache(self):
        """Rebuild team cache from database"""
        self._team_cache.clear()
        players = await self.master_roster.find_many(
            projection={"normalized_name": 1, "team_abbreviation": 1}
        )
        for p in players:
            if p.get("normalized_name") and p.get("team_abbreviation"):
                self._team_cache[p["normalized_name"]] = p["team_abbreviation"]
    
    def clear_cache(self):
        """Clear all caches"""
        self._team_cache.clear()
        self._player_cache.clear()
    
    # ==================== DAILY INSIGHTS ====================
    
    async def get_player_insights(self, player_name: str) -> Optional[Dict]:
        """Get daily insights for a player"""
        return await self.daily_insights.find_one({"player_name": player_name})
    
    async def get_all_insights(self) -> List[Dict]:
        """Get all daily insights"""
        return await self.daily_insights.find_many()
    
    async def save_player_insight(self, player_name: str, insight_data: Dict) -> bool:
        """Save or update player insight"""
        insight_data["player_name"] = player_name
        return await self.daily_insights.update_one(
            {"player_name": player_name},
            {"$set": insight_data},
            upsert=True
        )
    
    async def save_all_insights(self, insights: List[Dict]) -> int:
        """Replace all daily insights"""
        await self.daily_insights.delete_many()
        if insights:
            return await self.daily_insights.insert_many(insights)
        return 0
    
    async def get_insights_count(self) -> int:
        """Count daily insights"""
        return await self.daily_insights.count()
