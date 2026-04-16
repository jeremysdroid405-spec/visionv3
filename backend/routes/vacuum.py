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
async def get_live_vacuum_alerts(response: Response, refresh: bool = False):
    """
    Get live usage vacuum alerts for frontend display.
    
    ACTIVE PROP GATE: Only returns alerts where the beneficiary has an 
    active prop on today's board. Injuries without actionable betting 
    value are filtered out.
    
    Returns:
        List of formatted alerts for the "Live Injury Advantage" section.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    
    # If refresh requested or no recent check, fetch fresh injuries
    should_refresh = refresh
    if service.last_injury_check:
        mins_since_check = (datetime.now(timezone.utc) - service.last_injury_check).total_seconds() / 60
        if mins_since_check > 5:  # Auto-refresh if last check > 5 mins ago
            should_refresh = True
    else:
        should_refresh = True
    
    if should_refresh:
        try:
            await service.check_injuries()
        except Exception as e:
            logger.warning(f"[VacuumAlerts] Refresh failed: {e}")
    
    # Get vacuums filtered to today's teams only
    vacuums = await service.get_active_vacuums_for_today()
    
    # =========================================================================
    # ACTIVE PROP GATE: Get players with active props on today's board
    # =========================================================================
    active_players_on_board = set()
    active_props_by_player = {}
    
    try:
        if _db is not None:
            cached_board = _db.get_collection("dg_cached_board")
            async for player_doc in cached_board.find({}, {"player_name": 1, "props": 1, "_id": 0}):
                player_name = player_doc.get("player_name")
                if player_name:
                    normalized = player_name.strip().lower()
                    active_players_on_board.add(normalized)
                    active_props_by_player[normalized] = player_doc.get("props", [])
            
            logger.info(f"[VacuumAlerts] Active Prop Gate: {len(active_players_on_board)} players on today's board")
    except Exception as e:
        logger.warning(f"[VacuumAlerts] Error fetching active board: {e}")
    
    # Format alerts for frontend display
    alerts = []
    board_promotions = []
    filtered_count = 0
    
    for vacuum in vacuums:
        injured_player = vacuum.get("injured_player", "Unknown")
        injured_team = vacuum.get("team", "")
        reason = vacuum.get("reason", "")
        usage_rate = vacuum.get("usage_rate", 0)
        triggered_at = vacuum.get("triggered_at", "")
        is_late_scratch = vacuum.get("is_late_scratch", False)
        return_date = vacuum.get("return_date")
        
        # Calculate time since triggered
        time_ago = "recently"
        mins_ago = 0
        if triggered_at:
            try:
                triggered_dt = datetime.fromisoformat(triggered_at.replace("Z", "+00:00"))
                delta = datetime.now(timezone.utc) - triggered_dt
                mins_ago = int(delta.total_seconds() / 60)
                if mins_ago < 60:
                    time_ago = f"{mins_ago} mins ago"
                elif mins_ago < 120:
                    time_ago = f"{mins_ago // 60} hour ago"
                else:
                    time_ago = f"{mins_ago // 60} hours ago"
            except:
                pass
        
        for beneficiary in vacuum.get("beneficiaries", []):
            beneficiary_name = beneficiary.get("name", "") or beneficiary.get("player_name", "Unknown")
            normalized_beneficiary = beneficiary_name.strip().lower()
            
            # ACTIVE PROP GATE: Only include if beneficiary has active prop
            if active_players_on_board and normalized_beneficiary not in active_players_on_board:
                filtered_count += 1
                continue
            
            # Get beneficiary's active props
            beneficiary_props = active_props_by_player.get(normalized_beneficiary, [])
            prop_lines = []
            for prop in beneficiary_props[:3]:
                stat_type = prop.get("stat_type", "")
                line = prop.get("line", 0)
                if stat_type and line:
                    prop_lines.append(f"{stat_type} {line}")
            
            projections = beneficiary.get("projections", {})
            promotion = beneficiary.get("board_promotion", {})
            
            # Extract dynamic model fields
            usage_pct = beneficiary.get("usage_percentage", 0)
            usage_per_min = beneficiary.get("usage_per_minute", 0)
            boost_pct = beneficiary.get("boost_percentage", 0) or projections.get("boost_percentage", 0)
            is_dynamic = beneficiary.get("dynamic_calculation", False)
            
            # For frontend compatibility, usage_bump should show the boost percentage
            usage_bump_for_display = boost_pct
            
            alert = {
                "id": f"{injured_player}-{beneficiary_name}".replace(" ", "-").lower(),
                "beneficiary_name": beneficiary_name,
                "beneficiary_rank": beneficiary.get("rank", "primary"),
                "usage_bump": usage_bump_for_display,  # Frontend displays this as +X%
                "minutes_bump": beneficiary.get("minutes_bump", 0),
                "modifier": beneficiary.get("modifier", 0),
                "injured_player": injured_player,
                "injured_team": injured_team,
                "injury_reason": reason,
                "injury_return_date": return_date,
                "injured_usage_rate": usage_rate,
                "triggered_at": triggered_at,
                "time_ago": time_ago,
                "mins_ago": mins_ago,
                # Dynamic Usage Model v3.0 fields
                "usage_percentage": usage_pct,
                "usage_per_minute": usage_per_min,
                "dynamic_calculation": is_dynamic,
                # Projections with +12% PTS/PRA boost
                "projections": projections,
                "boost_percentage": boost_pct,
                # Board promotion
                "should_promote": promotion.get("should_promote", False),
                "eligible_props": promotion.get("eligible_props", []),
                "top_edge_stat": promotion.get("top_edge_stat"),
                # Badge flags
                "high_usage_advantage": beneficiary.get("high_usage_advantage", True),
                "late_injury_boost": beneficiary.get("late_injury_boost", True),
                "is_late_scratch": is_late_scratch,
                # NEW: Active prop data
                "has_active_prop": True,
                "active_prop_lines": prop_lines,
                # Formatted display string with boost info
                "display_text": f"{beneficiary_name} — {injured_player} ruled OUT {time_ago}. +{boost_pct:.0f}% PTS/PRA boost (Usage: {usage_pct:.1f}%)."
            }
            
            alerts.append(alert)
            
            # Track board promotions separately
            if promotion.get("should_promote"):
                board_promotions.append({
                    "player_name": beneficiary_name,
                    "injured_star": injured_player,
                    "props": promotion.get("eligible_props", []),
                    "high_usage_advantage": True
                })
    
    if filtered_count > 0:
        logger.info(f"[VacuumAlerts] Active Prop Gate: Filtered {filtered_count} beneficiaries (no active props)")
    
    return {
        "has_alerts": len(alerts) > 0,
        "alert_count": len(alerts),
        "alerts": alerts,
        "board_promotions": board_promotions,
        "total_promotions": len(board_promotions),
        "filtered_count": filtered_count,
        "last_check": service.last_injury_check.isoformat() if service.last_injury_check else None,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
