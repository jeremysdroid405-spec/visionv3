"""
Player Lookup Utility
=====================
SSOT: Shared utility for building player name -> master hub data lookup.

This module is the SINGLE SOURCE for player lookup logic.
DO NOT duplicate this function in any other file.

Usage:
    from utils.player_lookup import build_player_lookup, get_player_by_name, invalidate_cache
    
    lookup = await build_player_lookup(db)
    player = await get_player_by_name(db, "Kevin Durant")
"""

import logging
from typing import Dict, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# Global cache - shared across all modules
_player_lookup_cache: Optional[Dict] = None


async def build_player_lookup(db: AsyncIOMotorDatabase) -> Dict:
    """
    Build a cached lookup of player names -> master hub data.
    
    Maps all name variations to ensure correct player_id -> photo_url -> stats matching.
    Includes baseline_stats and game_logs for stat calculations.
    
    Args:
        db: Motor async database instance
        
    Returns:
        Dict mapping lowercase player names to player data dicts
    """
    global _player_lookup_cache
    
    if _player_lookup_cache is not None:
        return _player_lookup_cache
    
    _player_lookup_cache = {}
    
    # Load from master hub with all necessary fields
    players = await db.nba_master_hub_2026.find(
        {},
        {
            "_id": 0, 
            "player_id": 1, 
            "nba_id": 1, 
            "espn_id": 1, 
            "headshot_url": 1, 
            "team": 1, 
            "position": 1, 
            "display_name": 1, 
            "baseline_stats": 1,
            "game_logs": 1
        }
    ).to_list(1500)
    
    for player in players:
        display_name = player.get("display_name", "")
        if not display_name:
            continue
        
        name_lower = display_name.lower()
        _player_lookup_cache[name_lower] = player
        
        # Store without Jr./Sr./II/III/IV suffix
        for suffix in [" jr.", " jr", " sr.", " sr", " ii", " iii", " iv"]:
            if name_lower.endswith(suffix):
                base_name = name_lower[:-len(suffix)]
                if base_name not in _player_lookup_cache:
                    _player_lookup_cache[base_name] = player
        
        # Handle periods in names (PJ <-> P.J.)
        if "." in display_name:
            no_periods = display_name.replace(".", "").lower()
            if no_periods not in _player_lookup_cache:
                _player_lookup_cache[no_periods] = player
        else:
            words = display_name.split()
            if words and len(words[0]) == 2 and words[0].isupper():
                with_periods = f"{words[0][0]}.{words[0][1]}. {' '.join(words[1:])}".lower()
                if with_periods not in _player_lookup_cache:
                    _player_lookup_cache[with_periods] = player
    
    logger.info(f"[PLAYER_LOOKUP] Cached {len(_player_lookup_cache)} name variations for {len(players)} players")
    return _player_lookup_cache


async def get_player_by_name(db: AsyncIOMotorDatabase, player_name: str) -> Optional[Dict]:
    """
    Get player data from master hub by name.
    
    Args:
        db: Motor async database instance
        player_name: Player name to look up (case-insensitive)
        
    Returns:
        Player data dict or None if not found
    """
    if not player_name:
        return None
    
    lookup = await build_player_lookup(db)
    return lookup.get(player_name.lower())


def invalidate_cache():
    """
    Invalidate the player lookup cache.
    
    Call this after Tank01 sync or any master hub update.
    """
    global _player_lookup_cache
    _player_lookup_cache = None
    logger.info("[PLAYER_LOOKUP] Cache invalidated")
