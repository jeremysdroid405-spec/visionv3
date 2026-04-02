"""
Vacuum Routes - Usage Vacuum API Endpoints
==========================================
API endpoints for the InjuryVacuumService microservice.

Endpoints:
- GET /api/v3/vacuum/updates - Get current vacuum state for Ferrari Engine
- POST /api/v3/vacuum/check - Trigger manual injury check
- GET /api/v3/vacuum/active - Get all active usage vacuums
- GET /api/v3/vacuum/beneficiary/{player_name} - Check if player is a beneficiary
- POST /api/v3/vacuum/clear/{injured_player} - Clear a vacuum when player returns
"""
from fastapi import APIRouter, HTTPException, Response, Path
from typing import Dict, Any
from datetime import datetime, timezone
import logging

from services.injury_vacuum_service import get_vacuum_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Usage Vacuum"])

# Database reference
_db = None


def set_vacuum_db(db):
    """Set the database reference for Vacuum service."""
    global _db
    _db = db


def get_service():
    """Get the Vacuum service instance."""
    if _db is None:
        raise HTTPException(status_code=500, detail="Vacuum service not initialized")
    return get_vacuum_service(_db)


@router.get("/v3/vacuum/updates")
async def get_vacuum_updates(response: Response):
    """
    Get current vacuum state for the Ferrari Engine.
    
    This endpoint is polled by the Ferrari Engine to get:
    - Active usage vacuums (injured star players)
    - Beneficiary list with modifiers
    - Timestamps for UI display
    
    Returns:
        JSON payload with vacuum state for score adjustments.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    return await service.get_vacuum_updates()


@router.post("/v3/vacuum/check")
async def check_injuries():
    """
    Manually trigger an injury check.
    
    This fetches the latest NBA injury report and:
    1. Compares status against cached state
    2. Triggers vacuum if star (Usage > 25%) is OUT/DOUBTFUL
    3. Calculates beneficiaries and modifiers
    
    Returns:
        Dict with triggered vacuums and status changes.
    """
    service = get_service()
    
    # First sync star profiles if not done
    await service.sync_star_profiles()
    
    # Then check injuries
    result = await service.check_injuries()
    
    return result


@router.get("/v3/vacuum/active")
async def get_active_vacuums(response: Response):
    """
    Get all currently active usage vacuums.
    
    Returns:
        List of active vacuums with injured players and beneficiaries.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    vacuums = service.get_active_vacuums()
    
    return {
        "count": len(vacuums),
        "vacuums": vacuums,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.get("/v3/vacuum/beneficiary/{player_name}")
async def check_beneficiary(
    response: Response,
    player_name: str = Path(..., description="Player name to check")
):
    """
    Check if a specific player is a beneficiary of any active vacuum.
    
    This is used to determine if a player's Ferrari Score should be boosted
    due to a teammate being out.
    
    Args:
        player_name: The player to check
        
    Returns:
        Vacuum data if player is a beneficiary, null otherwise.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    modifier, vacuum_data = service.calculate_vacuum_modifier(player_name)
    
    return {
        "player_name": player_name,
        "is_beneficiary": modifier > 0,
        "modifier": modifier,
        "vacuum_data": vacuum_data,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.post("/v3/vacuum/clear/{injured_player}")
async def clear_vacuum(
    injured_player: str = Path(..., description="Injured player name to clear")
):
    """
    Clear an active vacuum when an injured player returns to lineup.
    
    Args:
        injured_player: The injured player whose vacuum to clear
        
    Returns:
        Success status.
    """
    service = get_service()
    success = service.clear_vacuum(injured_player)
    
    return {
        "success": success,
        "cleared": injured_player if success else None,
        "message": f"Vacuum for {injured_player} cleared" if success else f"No active vacuum found for {injured_player}"
    }


@router.post("/v3/vacuum/sync-profiles")
async def sync_star_profiles():
    """
    Sync star player usage profiles.
    
    This loads/refreshes the star player database with usage rates.
    Stars are defined as players with Usage > 25%.
    
    Returns:
        Sync result with profile count.
    """
    service = get_service()
    return await service.sync_star_profiles()
