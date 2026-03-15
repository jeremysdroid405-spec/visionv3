"""
Injuries Routes Module
======================
Handles injury-related endpoints
"""
from fastapi import APIRouter, HTTPException
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v3/injuries", tags=["injuries"])

# Injury service reference (set by main app)
_injury_service = None


def set_injury_service(service):
    """Set the injury service reference."""
    global _injury_service
    _injury_service = service


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
    if not _injury_service:
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
    if not _injury_service:
        raise HTTPException(status_code=500, detail="Injury Service not initialized")
    
    return await _injury_service.get_all_injuries()


@router.get("/player/{player_name}")
async def get_player_injury(player_name: str):
    """Get injury status for a specific player."""
    if not _injury_service:
        raise HTTPException(status_code=500, detail="Injury Service not initialized")
    
    injury = await _injury_service.get_player_injury_status(player_name)
    
    if not injury:
        return {"success": True, "injury": None, "message": f"{player_name} has no reported injury"}
    
    return {"success": True, "injury": injury}


@router.get("/team/{team_abbr}")
async def get_team_injuries(team_abbr: str):
    """Get all injuries for a specific team."""
    if not _injury_service:
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
    if not _injury_service:
        raise HTTPException(status_code=500, detail="Injury Service not initialized")
    
    alerts = await _injury_service.get_injury_alerts_for_board()
    
    return {
        "success": True,
        "alerts_count": len(alerts),
        "alerts": alerts
    }
