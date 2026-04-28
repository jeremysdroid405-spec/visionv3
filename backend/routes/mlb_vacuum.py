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
    Get formatted alerts for the MLB "Live Injury Advantage" UI section.

    UNIVERSAL PIPELINE (2026-04-29):
    Re-routed to use the same `compute_injury_advantages` engine NBA
    uses, so MLB injuries flow through the canonical
    `injuries_normalized` → board-pick join → advantage scoring path.
    The legacy `MLBInjuryVacuumService` (BDL/ESPN refetch + hardcoded
    `MLB_STAR_PROFILES` and `MLB_BENEFICIARY_MAPPINGS`) is bypassed
    entirely for this endpoint — the dashboard component receives the
    same alert shape it expected, but the source-of-truth is now the
    universal engine.

    Args:
        refresh: Accepted for API back-compat; ignored (the universal
            engine reads `injuries_normalized` which is kept fresh by
            `services.injury_sensor` / `services.live_injury_micro_sync`).

    Returns:
        Same legacy alert shape the dashboard already renders:
            injured_player, injured_team, injury_reason, injured_ops,
            time_ago, is_late_scratch, beneficiary_name, beneficiary_team,
            minutes_bump, usage_bump, stat_type, line, board_tier
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

    if _db is None:
        return {
            "success": False, "alerts": [], "count": 0,
            "last_check": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    try:
        from services.injury_advantage import (
            compute_injury_advantages,
            _get_recency_window,
            RECENCY_PREGAME_HOURS,
        )

        advantages = await compute_injury_advantages(_db, "mlb")
        window_hours = await _get_recency_window(_db, "mlb")

        # ── Reshape universal advantage rows → legacy MLB alert shape ─
        now = datetime.now(timezone.utc)
        alerts = []
        for adv in advantages:
            # Best-effort `time_ago`: derive from the raw injury doc.
            injured_team = None
            injury_reason = adv.get("injury_description") or ""
            time_ago = None
            is_late_scratch = window_hours <= RECENCY_PREGAME_HOURS

            inj = await _db["injuries_normalized"].find_one(
                {"sport": "mlb", "player_name": adv.get("injured_player")},
                {"_id": 0, "team": 1, "status_changed_at": 1,
                 "display_only": 1},
            )
            if inj:
                injured_team = inj.get("team")
                changed = inj.get("status_changed_at")
                if isinstance(changed, str):
                    try:
                        ts = datetime.fromisoformat(changed.replace("Z", "+00:00"))
                        if not ts.tzinfo:
                            ts = ts.replace(tzinfo=timezone.utc)
                        delta = (now - ts).total_seconds()
                        if delta < 3600:
                            time_ago = f"{int(delta // 60)}m ago"
                        elif delta < 86400:
                            time_ago = f"{int(delta // 3600)}h ago"
                        else:
                            time_ago = f"{int(delta // 86400)}d ago"
                    except (ValueError, TypeError):
                        pass
                if not injury_reason:
                    disp = inj.get("display_only") or {}
                    injury_reason = (disp.get("description")
                                     or disp.get("short_comment") or "")[:120]

            alerts.append({
                "id": f"{adv['injured_player']}-{adv['beneficiary_name']}".replace(" ", "-").lower(),
                # Injured player block (legacy field names the UI groups by)
                "injured_player": adv.get("injured_player"),
                "injured_team": injured_team or adv.get("beneficiary_team"),
                "injured_status": adv.get("injured_status"),
                "injured_tier_level": adv.get("injured_tier_level"),
                "injury_return_date": adv.get("injury_return_date"),
                "injury_reason": injury_reason,
                "injured_ops": None,            # not used by current UI
                "time_ago": time_ago,
                "is_late_scratch": is_late_scratch,
                # Beneficiary block
                "beneficiary_name": adv.get("beneficiary_name"),
                "beneficiary_team": adv.get("beneficiary_team"),
                "beneficiary_rank": adv.get("rank"),
                "usage_rank": adv.get("usage_rank"),
                "usage_source": adv.get("usage_source"),
                "stat_type": adv.get("stat_type"),
                "line": adv.get("line"),
                "board_tier": adv.get("board_tier"),
                "minutes_bump": adv.get("minutes_bump"),
                "usage_bump": adv.get("usage_bump"),
                "has_active_prop": True,
                "display_text": (
                    f"{adv['beneficiary_name']} ({adv['stat_type']} {adv['line']}) — "
                    f"{adv['injured_player']} {adv['injured_status']}. "
                    f"+{adv['minutes_bump']:.0f}% usage projected."
                ),
            })

        return {
            "success": True,
            "alerts": alerts,
            "count": len(alerts),
            "last_check": now.isoformat(),
            "recency_window_hours": window_hours,
            "engine": "universal_injury_advantage",  # provenance flag
            "timestamp": now.isoformat(),
        }

    except Exception as e:
        logger.error(f"[MLB_INJURY_ADV] Error: {e}", exc_info=True)
        return {
            "success": False, "alerts": [], "count": 0,
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
