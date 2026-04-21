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
    Get all currently active usage vacuums for TODAY's games only.
    
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


@router.get("/v3/vacuum/live-alerts")
async def get_live_injury_advantage(response: Response, sport: str = "nba"):
    """
    Live Injury Advantage — Strict board-scoped engine.

    Only returns advantages where:
    1. A meaningful injury (tier >= 3) exists on injuries_normalized
    2. A board pick on the SAME TEAM gains projected minutes >= 2.0
    3. If no board pick qualifies, returns empty (no generic boosts)

    Query params:
        sport: "nba" or "mlb"
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

    if _db is None:
        return {"has_alerts": False, "alert_count": 0, "alerts": [], "timestamp": datetime.now(timezone.utc).isoformat()}

    try:
        from services.injury_advantage import compute_injury_advantages, _get_recency_window

        advantages = await compute_injury_advantages(_db, sport)
        window_hours = await _get_recency_window(_db, sport)

        alerts = []
        for adv in advantages:
            alerts.append({
                "id": f"{adv['injured_player']}-{adv['beneficiary_name']}".replace(" ", "-").lower(),
                "beneficiary_name": adv["beneficiary_name"],
                "beneficiary_team": adv["beneficiary_team"],
                "beneficiary_rank": adv["rank"],
                # Injury-Rank Phase 2 (2026-04-21): usage-sorted provenance
                "usage_rank": adv.get("usage_rank"),
                "usage_source": adv.get("usage_source"),
                "stat_type": adv["stat_type"],
                "line": adv["line"],
                "board_tier": adv["board_tier"],
                "minutes_bump": adv["minutes_bump"],
                "usage_bump": adv["usage_bump"],
                "injured_player": adv["injured_player"],
                "injured_status": adv["injured_status"],
                "injured_tier_level": adv["injured_tier_level"],
                "injury_return_date": adv["injury_return_date"],
                "injury_reason": adv["injury_description"],
                "has_active_prop": True,
                "display_text": (
                    f"{adv['beneficiary_name']} ({adv['stat_type']} {adv['line']}) — "
                    f"{adv['injured_player']} {adv['injured_status']}. "
                    f"+{adv['minutes_bump']:.0f} min projected."
                ),
            })

        return {
            "has_alerts": len(alerts) > 0,
            "alert_count": len(alerts),
            "alerts": alerts,
            "sport": sport,
            "recency_window_hours": window_hours,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"[INJURY_ADV] Error: {e}")
        return {"has_alerts": False, "alert_count": 0, "alerts": [], "error": str(e)}

