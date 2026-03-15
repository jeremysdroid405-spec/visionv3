"""
Odds Mapper Routes Module
=========================
Permanent mapping between Odds API V4 player names and nba_master_hub_2026 player_ids
"""
from fastapi import APIRouter, HTTPException
from typing import List
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v3/odds-mapper", tags=["odds-mapper"])

# Service references (set by main app)
_db = None
_get_mapper_func = None


def set_odds_mapper_deps(db, get_mapper_func):
    """Set the db and mapper function references."""
    global _db, _get_mapper_func
    _db = db
    _get_mapper_func = get_mapper_func


@router.get("/stats")
async def get_odds_mapper_stats():
    """
    Get Odds API Mapper statistics.
    
    Returns:
    - total_mappings: Number of players in mapping
    - in_memory_count: Mappings loaded in memory
    - is_loaded: Whether mapper is ready
    - by_team: Player count per team
    """
    if _db is None or _get_mapper_func is None:
        raise HTTPException(status_code=500, detail="Odds Mapper not initialized")
    
    try:
        mapper = _get_mapper_func(_db)
        await mapper.loadMapping()
        return await mapper.getStats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/lookup/{odds_api_name}")
async def lookup_player_by_odds_name(odds_api_name: str):
    """
    Look up player by their Odds API V4 name string.
    
    This is the PRIMARY lookup method for Odds API integration.
    Returns the full player data from nba_master_hub_2026.
    
    Args:
        odds_api_name: The exact player name from Odds API (e.g., "LeBron James")
    
    Returns:
        Complete player object with all fields from master hub
    """
    if _db is None or _get_mapper_func is None:
        raise HTTPException(status_code=500, detail="Odds Mapper not initialized")
    
    try:
        mapper = _get_mapper_func(_db)
        await mapper.loadMapping()
        
        player_data = mapper.getFullPlayerData(odds_api_name)
        
        if not player_data:
            raise HTTPException(
                status_code=404, 
                detail=f"Player not found in mapping: {odds_api_name}"
            )
        
        return {
            "success": True,
            "odds_api_name": odds_api_name,
            "player_id": player_data.get("player_id"),
            "player_data": player_data
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/lookup-batch")
async def batch_lookup_by_odds_names(odds_api_names: List[str]):
    """
    Batch lookup multiple players by their Odds API names.
    
    Args:
        odds_api_names: List of player names from Odds API
    
    Returns:
        Dict mapping each odds_api_name to player_data (or None if not found)
    """
    if _db is None or _get_mapper_func is None:
        raise HTTPException(status_code=500, detail="Odds Mapper not initialized")
    
    try:
        mapper = _get_mapper_func(_db)
        await mapper.loadMapping()
        
        results = await mapper.lookupBatch(odds_api_names)
        
        matched = sum(1 for v in results.values() if v is not None)
        unmatched = len(odds_api_names) - matched
        
        return {
            "success": True,
            "total_requested": len(odds_api_names),
            "matched": matched,
            "unmatched": unmatched,
            "results": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rebuild")
async def rebuild_odds_mapper():
    """
    REBUILD the Odds API Mapper from nba_master_hub_2026.
    
    This regenerates the odds_api_mapping_master collection by:
    1. Reading all players from nba_master_hub_2026
    2. Extracting odds_api_name field for each
    3. Creating permanent mapping for fast lookups
    
    Should be run:
    - After initial deployment
    - After any mass update to nba_master_hub_2026
    """
    if _db is None or _get_mapper_func is None:
        raise HTTPException(status_code=500, detail="Odds Mapper not initialized")
    
    try:
        mapper = _get_mapper_func(_db)
        result = await mapper.rebuildMapping()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/player-id/{player_id}")
async def get_odds_name_from_player_id(player_id: str):
    """
    Reverse lookup - get Odds API name from player_id.
    
    Args:
        player_id: The player_id from nba_master_hub_2026
    
    Returns:
        The odds_api_name string for this player
    """
    if _db is None or _get_mapper_func is None:
        raise HTTPException(status_code=500, detail="Odds Mapper not initialized")
    
    try:
        mapper = _get_mapper_func(_db)
        await mapper.loadMapping()
        
        odds_name = mapper.getOddsNameFromPlayerId(player_id)
        
        if not odds_name:
            raise HTTPException(
                status_code=404, 
                detail=f"Player ID not found in mapping: {player_id}"
            )
        
        return {
            "success": True,
            "player_id": player_id,
            "odds_api_name": odds_name
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
