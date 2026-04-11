"""
MLB Vacuum Routes - MLB Usage Vacuum API Endpoints
==================================================
API endpoints for the MLB InjuryVacuumService.

Endpoints:
- GET /api/v3/mlb/vacuum/updates - Get current MLB vacuum state
- POST /api/v3/mlb/vacuum/check - Trigger manual MLB injury check
- GET /api/v3/mlb/vacuum/active - Get all active MLB usage vacuums
- GET /api/v3/mlb/vacuum/live-alerts - Get formatted alerts for UI
- POST /api/v3/mlb/vacuum/clear/{injured_player} - Clear a vacuum
"""
from fastapi import APIRouter, HTTPException, Response, Path
from typing import Dict, Any
from datetime import datetime, timezone
import logging

from services.mlb_injury_vacuum_service import get_mlb_vacuum_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["MLB Usage Vacuum"])

# Database reference
_db = None


def set_mlb_vacuum_db(db):
    """Set the database reference for MLB Vacuum service."""
    global _db
    _db = db


def get_service():
    """Get the MLB Vacuum service instance."""
    if _db is None:
        raise HTTPException(status_code=500, detail="MLB Vacuum service not initialized")
    return get_mlb_vacuum_service(_db)


@router.get("/v3/mlb/vacuum/updates")
async def get_mlb_vacuum_updates(response: Response):
    """
    Get current MLB vacuum state.
    
    Returns:
        JSON payload with vacuum state for MLB injury advantage.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    return await service.get_vacuum_updates()


@router.post("/v3/mlb/vacuum/check")
async def check_mlb_injuries():
    """
    Manually trigger an MLB injury check.
    
    This fetches the latest MLB injury report and:
    1. Identifies star players who are OUT/IL/DTD
    2. Calculates beneficiaries (lineup movers)
    3. Creates vacuum alerts for the UI
    
    Returns:
        Dict with triggered vacuums and status changes.
    """
    service = get_service()
    result = await service.check_injuries()
    return result


@router.get("/v3/mlb/vacuum/active")
async def get_active_mlb_vacuums(response: Response):
    """
    Get all currently active MLB usage vacuums.
    
    Returns:
        List of active vacuums with injured players and beneficiaries.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    vacuums = await service.get_active_vacuums_for_today()
    
    return {
        "count": len(vacuums),
        "vacuums": vacuums,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.get("/v3/mlb/vacuum/live-alerts")
async def get_mlb_live_alerts(
    response: Response,
    refresh: bool = False
):
    """
    Get formatted alerts for the "Live Injury Advantage" UI section.
    
    Args:
        refresh: If True, fetches fresh injury data first.
    
    Returns:
        List of formatted alerts for MLB injury advantage display.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    
    # Check if we need to refresh
    if refresh or service.last_injury_check is None:
        mins_since_check = float('inf')
    else:
        mins_since_check = (datetime.now(timezone.utc) - service.last_injury_check).total_seconds() / 60
    
    # Refresh if stale (> 5 minutes)
    if mins_since_check > 5:
        await service.check_injuries()
    
    alerts = await service.get_live_alerts(refresh=False)
    
    return {
        "success": True,
        "alerts": alerts,
        "count": len(alerts),
        "last_check": service.last_injury_check.isoformat() if service.last_injury_check else None,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.post("/v3/mlb/vacuum/clear/{injured_player}")
async def clear_mlb_vacuum(
    injured_player: str = Path(..., description="Name of the injured player to clear")
):
    """
    Clear a vacuum when a player returns from injury.
    
    Args:
        injured_player: Name of the injured player
    
    Returns:
        Success status and message.
    """
    service = get_service()
    result = await service.clear_vacuum(injured_player)
    
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("message"))
    
    return result
