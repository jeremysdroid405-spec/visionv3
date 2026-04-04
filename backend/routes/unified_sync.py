"""
Unified Sync API Routes

Single endpoint for all data synchronization with failsafe retry.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from datetime import datetime, timezone
import logging

router = APIRouter(prefix="/v4", tags=["sync"])
logger = logging.getLogger(__name__)

# Service will be initialized in server.py
_sync_service = None

def init_sync_routes(sync_service):
    """Initialize routes with the sync service."""
    global _sync_service
    _sync_service = sync_service


@router.post("/sync/full")
async def trigger_full_sync(background_tasks: BackgroundTasks):
    """
    Trigger a full sync of all data sources.
    
    This runs in the background with automatic retries.
    
    Data sources:
    - BDL: Players, Game Logs, Team Stats
    - Odds API: Props
    - ESPN: Injuries, News
    """
    if not _sync_service:
        raise HTTPException(status_code=500, detail="Sync service not initialized")
    
    # Run sync in background
    background_tasks.add_task(_sync_service.run_full_sync)
    
    return {
        "status": "sync_started",
        "message": "Full sync started in background with failsafe retry",
        "started_at": datetime.now(timezone.utc).isoformat()
    }


@router.post("/sync/bdl")
async def sync_bdl_data():
    """Sync all BDL data (players, game logs, team stats)."""
    if not _sync_service:
        raise HTTPException(status_code=500, detail="Sync service not initialized")
    
    results = {
        "players": await _sync_service.sync_bdl_players(),
        "game_logs": await _sync_service.sync_bdl_game_logs(),
        "team_stats": await _sync_service.sync_bdl_team_stats()
    }
    
    return {
        "success": all(r.get("success") for r in results.values()),
        "results": results,
        "synced_at": datetime.now(timezone.utc).isoformat()
    }


@router.post("/sync/odds")
async def sync_odds_data():
    """Sync all Odds API props."""
    if not _sync_service:
        raise HTTPException(status_code=500, detail="Sync service not initialized")
    
    result = await _sync_service.sync_odds_api_props()
    return result


@router.post("/sync/espn")
async def sync_espn_data():
    """Sync ESPN injuries and news."""
    if not _sync_service:
        raise HTTPException(status_code=500, detail="Sync service not initialized")
    
    results = {
        "injuries": await _sync_service.sync_espn_injuries(),
        "news": await _sync_service.sync_espn_news()
    }
    
    return {
        "success": all(r.get("success") for r in results.values()),
        "results": results,
        "synced_at": datetime.now(timezone.utc).isoformat()
    }


@router.get("/sync/status")
async def get_sync_status():
    """Get current sync status for all data sources."""
    if not _sync_service:
        raise HTTPException(status_code=500, detail="Sync service not initialized")
    
    return {
        "status": _sync_service.get_sync_status(),
        "checked_at": datetime.now(timezone.utc).isoformat()
    }


@router.get("/hit-rates/{player_name}/{stat_type}/{line}")
async def get_hit_rates(player_name: str, stat_type: str, line: float):
    """
    Get hit rates calculated FRESH from BDL game logs.
    
    NEVER returns cached data. Always computes live.
    
    Args:
        player_name: Player name (partial match supported)
        stat_type: PTS, REB, AST, PRA, etc.
        line: The betting line to check against
    """
    if not _sync_service:
        raise HTTPException(status_code=500, detail="Sync service not initialized")
    
    result = await _sync_service.calculate_hit_rates(player_name, stat_type, line)
    
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    
    return result


@router.get("/player/{player_name}")
async def get_player_data(player_name: str):
    """
    Get complete player data from BDL SSOT.
    
    Includes:
    - Basic info
    - Last 20 game logs
    - Current props from Odds API
    - Injury status from ESPN
    """
    if not _sync_service:
        raise HTTPException(status_code=500, detail="Sync service not initialized")
    
    db = _sync_service.db
    
    # Get player from master hub
    player = await db.nba_master_hub_2026.find_one({
        "player_name": {"$regex": player_name, "$options": "i"}
    }, {"_id": 0})
    
    if not player:
        raise HTTPException(status_code=404, detail=f"Player not found: {player_name}")
    
    # Get current props
    props_cursor = db.odds_api_props.find({
        "player_name": {"$regex": player_name, "$options": "i"}
    }, {"_id": 0})
    props = await props_cursor.to_list(length=50)
    
    # Get injury status
    injury = await db.espn_injuries.find_one({
        "player_name": {"$regex": player_name, "$options": "i"}
    }, {"_id": 0})
    
    return {
        "player": player,
        "props": props,
        "injury": injury,
        "fetched_at": datetime.now(timezone.utc).isoformat()
    }


@router.get("/props/today")
async def get_todays_props():
    """Get all props for today's games from Odds API."""
    if not _sync_service:
        raise HTTPException(status_code=500, detail="Sync service not initialized")
    
    db = _sync_service.db
    
    # Get all current props
    cursor = db.odds_api_props.find({}, {"_id": 0})
    props = await cursor.to_list(length=1000)
    
    # Group by player
    by_player = {}
    for prop in props:
        name = prop.get("player_name", "Unknown")
        if name not in by_player:
            by_player[name] = []
        by_player[name].append(prop)
    
    return {
        "total_props": len(props),
        "players": len(by_player),
        "props_by_player": by_player,
        "fetched_at": datetime.now(timezone.utc).isoformat()
    }


@router.post("/tiers/rebuild")
async def rebuild_tiers():
    """
    Rebuild all pick tiers using SSOT architecture.
    
    1. Gets props from Odds API
    2. Calculates fresh hit rates from BDL
    3. Applies DvP penalties
    4. Scores and ranks picks
    5. Populates tier collections
    """
    if not _sync_service:
        raise HTTPException(status_code=500, detail="Sync service not initialized")
    
    from services.ferrari_tier_builder_v2 import get_tier_builder
    
    builder = get_tier_builder(_sync_service.db)
    result = await builder.build_tiers()
    
    return result


@router.get("/tiers/all")
async def get_all_tiers():
    """Get all tier picks."""
    if not _sync_service:
        raise HTTPException(status_code=500, detail="Sync service not initialized")
    
    db = _sync_service.db
    
    safe_haven = await db.ferrari_safe_haven.find({}, {"_id": 0}).to_list(length=10)
    front_lines = await db.ferrari_front_lines.find({}, {"_id": 0}).to_list(length=10)
    war_zone = await db.ferrari_war_zone.find({}, {"_id": 0}).to_list(length=10)
    
    return {
        "safe_haven": {
            "count": len(safe_haven),
            "picks": safe_haven
        },
        "front_lines": {
            "count": len(front_lines),
            "picks": front_lines
        },
        "war_zone": {
            "count": len(war_zone),
            "picks": war_zone
        },
        "fetched_at": datetime.now(timezone.utc).isoformat()
    }
