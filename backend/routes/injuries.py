"""
Injuries Routes Module
======================
Handles injury-related endpoints including live micro-sync
"""
from fastapi import APIRouter, HTTPException, Query
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v3/injuries", tags=["injuries"])

# Injury service reference (set by main app)
_injury_service = None
_live_injury_service = None


def set_injury_service(service):
    """Set the injury service reference."""
    global _injury_service
    _injury_service = service


def set_live_injury_service(service):
    """Set the live injury micro-sync service reference."""
    global _live_injury_service
    _live_injury_service = service


@router.get("/live")
async def get_live_injuries(sport: str = Query(None, description="Filter by sport: nba or mlb")):
    """
    Get LIVE injury data from micro-sync cache.
    
    This endpoint returns the most up-to-date injury information
    from the high-frequency polling loop (60-second refresh).
    
    Returns:
    - high_risk: OUT, DOUBTFUL, IL players
    - medium_risk: DTD, GTD, QUESTIONABLE players
    - last_sync: Timestamp of last successful sync
    """
    if _live_injury_service is None:
        raise HTTPException(status_code=500, detail="Live Injury Service not initialized")
    
    return await _live_injury_service.get_live_injuries(sport)


@router.post("/live/sync")
async def trigger_live_injury_sync():
    """
    Manually trigger a live injury sync.
    
    Useful for forcing an immediate refresh instead of waiting
    for the next polling interval.
    """
    if _live_injury_service is None:
        raise HTTPException(status_code=500, detail="Live Injury Service not initialized")
    
    return await _live_injury_service.fetch_live_injuries()


@router.post("/sync")
async def sync_injuries():
    """
    INJURY SYNC - Fetch latest injury data from ESPN.
    
    Updates:
    - dg_injuries collection with current injury statuses
    - Usage ripple calculations for teammates of injured stars
    - Breaking news from ESPN
    
    Should be called periodically (every 30 mins during game days).
    """
    if _injury_service is None:
        raise HTTPException(status_code=500, detail="Injury Service not initialized")
    
    logger.info("[INJURY] Manual injury sync triggered")
    
    result = await _injury_service.sync_injuries()
    
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error', 'Sync failed'))
    
    return result


@router.get("")
async def get_all_injuries():
    """
    Get all current NBA injuries grouped by severity.
    
    Returns:
    - high_risk: Out, Doubtful players
    - medium_risk: Questionable, Day-To-Day, GTD players
    - low_risk: Probable players
    """
    if _injury_service is None:
        raise HTTPException(status_code=500, detail="Injury Service not initialized")
    
    return await _injury_service.get_all_injuries()


@router.get("/player/{player_name}")
async def get_player_injury(player_name: str):
    """Get injury status for a specific player."""
    if _injury_service is None:
        raise HTTPException(status_code=500, detail="Injury Service not initialized")
    
    injury = await _injury_service.get_player_injury_status(player_name)
    
    if not injury:
        return {"success": True, "injury": None, "message": f"{player_name} has no reported injury"}
    
    return {"success": True, "injury": injury}


@router.get("/team/{team_abbr}")
async def get_team_injuries(team_abbr: str):
    """Get all injuries for a specific team."""
    if _injury_service is None:
        raise HTTPException(status_code=500, detail="Injury Service not initialized")
    
    injuries = await _injury_service.get_team_injuries(team_abbr)
    
    return {
        "success": True,
        "team": team_abbr.upper(),
        "injuries_count": len(injuries),
        "injuries": injuries
    }


@router.get("/alerts")
async def get_injury_alerts():
    """
    Get injury alerts formatted for the dashboard board.
    Returns a dict mapping player_name -> injury_info for quick lookup.
    Used by frontend to display injury badges on player cards.
    """
    if _injury_service is None:
        raise HTTPException(status_code=500, detail="Injury Service not initialized")
    
    alerts = await _injury_service.get_injury_alerts_for_board()
    
    return {
        "success": True,
        "alerts_count": len(alerts),
        "alerts": alerts
    }
