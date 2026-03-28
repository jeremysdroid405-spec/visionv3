"""
Stats Cache Service
====================
Implements Cache-Aside pattern for player stats with 6-hour TTL.

Design:
- Check dg_stats_cache first before hitting APIs
- Only trigger API calls if data is older than 6 hours
- Reduces API calls and improves sync performance
"""

from typing import Dict, List, Any, Optional
from datetime import datetime, timezone, timedelta
import logging

from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# Cache TTL in hours
STATS_CACHE_TTL_HOURS = 6


class StatsCacheService:
    """
    Cache-aside pattern for player stats.
    
    Usage:
        cache = StatsCacheService(db)
        
        # Get cached stats (returns None if expired/missing)
        stats = await cache.get_player_stats("LeBron James", "PTS")
        
        # If None, fetch from API then cache
        if stats is None:
            stats = await fetch_from_api(...)
            await cache.set_player_stats("LeBron James", "PTS", stats)
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.cache = db.dg_stats_cache
        self.ttl = timedelta(hours=STATS_CACHE_TTL_HOURS)
    
    async def initialize(self):
        """Create indexes for fast lookups."""
        await self.cache.create_index([("player_name", 1), ("stat_type", 1)], unique=True)
        await self.cache.create_index("cached_at", expireAfterSeconds=STATS_CACHE_TTL_HOURS * 3600)
        logger.info(f"[STATS_CACHE] Initialized with {STATS_CACHE_TTL_HOURS}h TTL")
    
    async def get_player_stats(
        self, 
        player_name: str, 
        stat_type: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get cached stats for a player/stat combo.
        
        Returns None if:
        - Not in cache
        - Cache entry is older than TTL
        """
        cache_key = {"player_name": player_name, "stat_type": stat_type}
        entry = await self.cache.find_one(cache_key)
        
        if not entry:
            return None
        
        # Check TTL
        cached_at = entry.get("cached_at")
        if cached_at:
            if isinstance(cached_at, str):
                cached_at = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
            
            age = datetime.now(timezone.utc) - cached_at
            if age > self.ttl:
                logger.debug(f"[STATS_CACHE] Expired: {player_name}/{stat_type} (age: {age})")
                return None
        
        logger.debug(f"[STATS_CACHE] HIT: {player_name}/{stat_type}")
        return entry.get("stats")
    
    async def set_player_stats(
        self,
        player_name: str,
        stat_type: str,
        stats: Dict[str, Any]
    ) -> bool:
        """Cache stats for a player/stat combo."""
        try:
            await self.cache.update_one(
                {"player_name": player_name, "stat_type": stat_type},
                {"$set": {
                    "player_name": player_name,
                    "stat_type": stat_type,
                    "stats": stats,
                    "cached_at": datetime.now(timezone.utc)
                }},
                upsert=True
            )
            logger.debug(f"[STATS_CACHE] SET: {player_name}/{stat_type}")
            return True
        except Exception as e:
            logger.error(f"[STATS_CACHE] Error caching {player_name}/{stat_type}: {e}")
            return False
    
    async def get_bulk_stats(
        self,
        player_stat_pairs: List[tuple]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get cached stats for multiple player/stat combos.
        
        Args:
            player_stat_pairs: List of (player_name, stat_type) tuples
            
        Returns:
            Dict mapping "player_name|stat_type" -> stats (only for cache hits)
        """
        if not player_stat_pairs:
            return {}
        
        # Build OR query
        queries = [
            {"player_name": name, "stat_type": stat}
            for name, stat in player_stat_pairs
        ]
        
        cutoff = datetime.now(timezone.utc) - self.ttl
        cursor = self.cache.find({
            "$or": queries,
            "cached_at": {"$gte": cutoff}
        })
        
        results = {}
        async for entry in cursor:
            key = f"{entry['player_name']}|{entry['stat_type']}"
            results[key] = entry.get("stats")
        
        hit_count = len(results)
        miss_count = len(player_stat_pairs) - hit_count
        if player_stat_pairs:
            logger.info(f"[STATS_CACHE] Bulk lookup: {hit_count} hits, {miss_count} misses")
        
        return results
    
    async def set_bulk_stats(
        self,
        stats_list: List[Dict[str, Any]]
    ) -> int:
        """
        Cache multiple player stats at once.
        
        Args:
            stats_list: List of dicts with player_name, stat_type, and stats
            
        Returns:
            Number of entries cached
        """
        if not stats_list:
            return 0
        
        from pymongo import UpdateOne
        
        operations = []
        now = datetime.now(timezone.utc)
        
        for item in stats_list:
            operations.append(UpdateOne(
                {"player_name": item["player_name"], "stat_type": item["stat_type"]},
                {"$set": {
                    "player_name": item["player_name"],
                    "stat_type": item["stat_type"],
                    "stats": item["stats"],
                    "cached_at": now
                }},
                upsert=True
            ))
        
        try:
            result = await self.cache.bulk_write(operations)
            logger.info(f"[STATS_CACHE] Bulk cached {result.upserted_count + result.modified_count} entries")
            return result.upserted_count + result.modified_count
        except Exception as e:
            logger.error(f"[STATS_CACHE] Bulk cache error: {e}")
            return 0
    
    async def get_players_needing_refresh(
        self,
        player_names: List[str],
        stat_types: List[str] = None
    ) -> List[str]:
        """
        Get list of players whose cache is expired or missing.
        
        This enables DELTA UPDATES - only fetch stats for players
        who actually need a refresh.
        """
        if not player_names:
            return []
        
        stat_types = stat_types or ["PTS", "REB", "AST", "3PM", "PRA"]
        cutoff = datetime.now(timezone.utc) - self.ttl
        
        # Find players with valid cache
        cursor = self.cache.find({
            "player_name": {"$in": player_names},
            "stat_type": {"$in": stat_types},
            "cached_at": {"$gte": cutoff}
        }, {"player_name": 1})
        
        cached_players = set()
        async for entry in cursor:
            cached_players.add(entry["player_name"])
        
        # Return players NOT in cache (need refresh)
        needs_refresh = [p for p in player_names if p not in cached_players]
        
        logger.info(f"[STATS_CACHE] {len(cached_players)} cached, {len(needs_refresh)} need refresh")
        return needs_refresh
    
    async def invalidate_player(self, player_name: str):
        """Remove all cache entries for a player (e.g., after injury update)."""
        result = await self.cache.delete_many({"player_name": player_name})
        if result.deleted_count:
            logger.info(f"[STATS_CACHE] Invalidated {result.deleted_count} entries for {player_name}")
    
    async def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics for monitoring."""
        total = await self.cache.count_documents({})
        cutoff = datetime.now(timezone.utc) - self.ttl
        valid = await self.cache.count_documents({"cached_at": {"$gte": cutoff}})
        expired = total - valid
        
        return {
            "total_entries": total,
            "valid_entries": valid,
            "expired_entries": expired,
            "ttl_hours": STATS_CACHE_TTL_HOURS,
            "checked_at": datetime.now(timezone.utc).isoformat()
        }


def get_stats_cache_service(db: AsyncIOMotorDatabase) -> StatsCacheService:
    """Factory function for StatsCacheService."""
    return StatsCacheService(db)
