"""
Player Lookup Utility
=====================
SSOT: Shared utility for player_id -> master hub data lookup.

PRIMARY KEY: player_id (NOT name)

This module is the SINGLE SOURCE for player lookup logic.
DO NOT duplicate this function in any other file.

Usage:
    from utils.player_lookup import get_player_by_id, get_player_by_name, invalidate_cache
    
    player = await get_player_by_id(db, "12345")  # PRIMARY
    player = await get_player_by_name(db, "Kevin Durant")  # FALLBACK ONLY
"""

import logging
from typing import Dict, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# Global caches
_player_id_cache: Optional[Dict] = None
_player_name_cache: Optional[Dict] = None


async def _build_caches(db: AsyncIOMotorDatabase) -> None:
    """Build both player_id and name lookup caches from master hub."""
    global _player_id_cache, _player_name_cache
    
    if _player_id_cache is not None:
        return
    
    _player_id_cache = {}
    _player_name_cache = {}
    
    # Load from master hub with all necessary fields
    players = await db.nba_master_hub_2026.find(
        {},
        {
            "_id": 0, 
            "player_id": 1,
            "nba_player_id": 1,
            "nba_id": 1, 
            "espn_id": 1,
            "tank01_id": 1,
            "headshot_url": 1, 
            "photo_url": 1,
            "team": 1, 
            "position": 1, 
            "display_name": 1, 
            "baseline_stats": 1,
            "game_logs": 1,
            "bdl_game_logs": 1,
            "last_updated": 1,
            "last_bdl_sync": 1
        }
    ).to_list(1500)
    
    for player in players:
        # PRIMARY: Index by player_id
        player_id = player.get("player_id")
        if player_id:
            _player_id_cache[str(player_id)] = player
        
        # Also index by nba_player_id (official NBA ID)
        nba_id = player.get("nba_player_id")
        if nba_id:
            _player_id_cache[str(nba_id)] = player
        
        # Also index by tank01_id for backwards compat
        tank01_id = player.get("tank01_id")
        if tank01_id:
            _player_id_cache[str(tank01_id)] = player
        
        # SECONDARY: Index by name (fallback only)
        display_name = player.get("display_name", "")
        if display_name:
            name_lower = display_name.lower()
            _player_name_cache[name_lower] = player
            
            # Handle suffixes
            for suffix in [" jr.", " jr", " sr.", " sr", " ii", " iii", " iv"]:
                if name_lower.endswith(suffix):
                    base_name = name_lower[:-len(suffix)]
                    if base_name not in _player_name_cache:
                        _player_name_cache[base_name] = player
    
    logger.info(f"[PLAYER_LOOKUP] Cached {len(_player_id_cache)} player IDs, {len(_player_name_cache)} names")


async def get_player_by_id(db: AsyncIOMotorDatabase, player_id: str) -> Optional[Dict]:
    """
    PRIMARY LOOKUP: Get player data from master hub by player_id.
    
    Args:
        db: Motor async database instance
        player_id: Player ID (player_id, nba_player_id, or tank01_id)
        
    Returns:
        Player data dict or None if not found
    """
    if not player_id:
        return None
    
    await _build_caches(db)
    return _player_id_cache.get(str(player_id))


async def get_player_by_name(db: AsyncIOMotorDatabase, player_name: str) -> Optional[Dict]:
    """
    FALLBACK LOOKUP: Get player data from master hub by name.
    
    Use get_player_by_id() when possible - this is a fallback only.
    
    Args:
        db: Motor async database instance
        player_name: Player name to look up (case-insensitive)
        
    Returns:
        Player data dict or None if not found
    """
    if not player_name:
        return None
    
    await _build_caches(db)
    return _player_name_cache.get(player_name.lower())


async def build_player_lookup(db: AsyncIOMotorDatabase) -> Dict:
    """
    DEPRECATED: Use get_player_by_id() instead.
    Returns name lookup for backwards compatibility.
    """
    await _build_caches(db)
    return _player_name_cache


def invalidate_cache():
    """
    Invalidate all player lookup caches.
    Call this after sync or any master hub update.
    """
    global _player_id_cache, _player_name_cache
    _player_id_cache = None
    _player_name_cache = None
    logger.info("[PLAYER_LOOKUP] Cache invalidated")
