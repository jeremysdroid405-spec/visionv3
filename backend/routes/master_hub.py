"""
Master Hub Routes Module
========================
NBA Master Hub - Single Source of Truth for all player data
"""
from fastapi import APIRouter, HTTPException
from typing import Optional
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v3/master-hub", tags=["master-hub"])

# Service references (set by main app)
_db = None
_hub_functions = None  # Dict of functions: fetchPlayerIntel, hubSearchPlayers, etc.


def set_master_hub_deps(db, hub_functions: dict):
    """Set the db and hub function references."""
    global _db, _hub_functions
    _db = db
    _hub_functions = hub_functions


@router.get("/player/{player_id}")
async def get_player_intel(player_id: str):
    """
    THE VALET FUNCTION - Fetch player intel from Master Hub.
    
    This is the ONLY way to access player data from NBA_MASTER_HUB_2026.
    
    Args:
        player_id: Player ID (tank01_id, nba_id, or display_name)
        
    Returns:
        Complete player object with all fields
    """
    if _hub_functions is None:
        raise HTTPException(status_code=500, detail="Master Hub not initialized")
    
    player = await _hub_functions["fetchPlayerIntel"](player_id)
    if not player:
        raise HTTPException(status_code=404, detail=f"Player not found: {player_id}")
    return player


@router.get("/player/name/{display_name}")
async def get_player_by_name(display_name: str):
    """Fetch player by display name."""
    if _hub_functions is None:
        raise HTTPException(status_code=500, detail="Master Hub not initialized")
    
    player = await _hub_functions["fetchPlayerIntelByName"](display_name)
    if not player:
        raise HTTPException(status_code=404, detail=f"Player not found: {display_name}")
    return player


@router.get("/search")
async def search_hub_players(q: str, limit: int = 10):
    """Search players in Master Hub."""
    if _hub_functions is None:
        raise HTTPException(status_code=500, detail="Master Hub not initialized")
    
    players = await _hub_functions["hubSearchPlayers"](q, limit)
    return {"players": players, "count": len(players)}


@router.get("/stats")
async def get_hub_statistics():
    """Get Master Hub statistics."""
    if _hub_functions is None:
        raise HTTPException(status_code=500, detail="Master Hub not initialized")
    
    return await _hub_functions["getHubStats"]()


@router.post("/sync")
async def trigger_hub_sync():
    """
    Manually trigger Master Hub daily sync.
    Normally runs at 4:00 AM ET automatically.
    """
    if _hub_functions is None:
        raise HTTPException(status_code=500, detail="Master Hub not initialized")
    
    result = await _hub_functions["runHubSync"]()
    return result


@router.post("/sync-tank01")
async def trigger_tank01_sync():
    """
    DEPRECATED: Tank01 API has data quality issues.
    Use /sync-nba-official instead.
    
    This endpoint is kept for backwards compatibility but will
    redirect to the NBA Official sync.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    # Redirect to NBA Official sync
    from services.nba_official_sync import get_nba_official_sync_service
    service = get_nba_official_sync_service(_db)
    result = await service.sync_all_players()
    result["note"] = "DEPRECATED: Tank01 bypassed. Using official NBA API."
    return result


@router.post("/populate-tank01-ids")
async def populate_tank01_ids():
    """
    Populate Tank01 player IDs for players in master hub.
    
    Required before running Tank01 stats sync.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.tank01_stats_service import get_tank01_service
    service = get_tank01_service(_db)
    result = await service.populate_tank01_ids()
    return result


@router.post("/sync-player-logs/{player_name}")
async def sync_player_game_logs(player_name: str):
    """
    Sync game logs for a single player.
    
    This fetches and stores game_logs from Tank01 for coupled stat calculations.
    Use this to test the new coupled stats feature.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.tank01_stats_service import get_tank01_service
    service = get_tank01_service(_db)
    
    # First check if player exists and has tank01_id
    player = await service.master_hub.find_one(
        {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
        {"_id": 0, "display_name": 1, "tank01_id": 1, "playerID": 1}
    )
    
    if not player:
        raise HTTPException(status_code=404, detail=f"Player not found: {player_name}")
    
    tank01_id = player.get("tank01_id") or player.get("playerID")
    if not tank01_id:
        raise HTTPException(status_code=400, detail=f"No Tank01 ID for: {player_name}")
    
    import httpx
    async with httpx.AsyncClient(timeout=30) as http:
        # Fetch game logs
        game_logs = await service._fetch_game_logs(http, tank01_id)
        
        if not game_logs:
            return {"success": False, "message": "No game logs found", "player": player.get("display_name")}
        
        # Store game logs
        await service.master_hub.update_one(
            {"display_name": player.get("display_name")},
            {"$set": {
                "game_logs": game_logs,
                "game_logs_updated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
            }}
        )
        
        return {
            "success": True,
            "player": player.get("display_name"),
            "games_count": len(game_logs),
            "sample_game": game_logs[0] if game_logs else None
        }


@router.post("/start-scheduler")
async def start_hub_scheduler():
    """Start the 4:00 AM ET daily sync scheduler."""
    if _hub_functions is None:
        raise HTTPException(status_code=500, detail="Master Hub not initialized")
    
    hub = _hub_functions["get_master_hub"]()
    await hub.startDailyScheduler()
    return {"status": "started", "schedule": "4:00 AM ET daily"}


# ============================================
# NBA OFFICIAL API SYNC (Replaces Tank01)
# ============================================

@router.post("/sync-nba-official")
async def trigger_nba_official_sync():
    """
    Trigger full sync using official NBA API.
    
    PRIMARY DATA SOURCE - Replaces Tank01 due to data quality issues.
    Uses nba_api package to fetch official game logs.
    
    This is the method used by the 0400 EST CRON job.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.nba_official_sync import get_nba_official_sync_service
    service = get_nba_official_sync_service(_db)
    result = await service.sync_all_players()
    return result


@router.post("/sync-nba-official/{player_name}")
async def sync_single_player_nba(player_name: str):
    """
    Sync a single player using official NBA API.
    
    Useful for testing and on-demand updates.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.nba_official_sync import get_nba_official_sync_service
    service = get_nba_official_sync_service(_db)
    result = await service.sync_single_player(player_name)
    
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error"))
    
    return result

