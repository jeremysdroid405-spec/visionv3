"""
Ferrari Tier Routes
===================
API endpoints for the "Best of the Best" Ferrari-filtered picks.

Uses Bovada separation as the primary sharp benchmark.
Global 15% kill-switch ensures only elite plays are visible.
"""
from fastapi import APIRouter, HTTPException, Query, Response
from typing import Dict, Any
import logging

from services.ferrari_tier_service import get_ferrari_tier_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Ferrari Tiers"])

# Engine reference for DB access
_db = None


def set_ferrari_db(db):
    """Set the database reference for Ferrari service."""
    global _db
    _db = db


def get_service():
    """Get the Ferrari tier service instance."""
    if _db is None:
        raise HTTPException(status_code=500, detail="Ferrari service not initialized")
    return get_ferrari_tier_service(_db)


@router.get("/v3/ferrari/safe-haven")
async def get_ferrari_safe_haven(
    response: Response,
    limit: int = Query(10, ge=1, le=50)
):
    """
    FERRARI SAFE HAVEN - Elite Goblins with massive market separation.
    
    Criteria:
    - Sharp price <= -250 (heavy favorite on Bovada/DK/FD)
    - OR PP line 1.5+ pts below Bovada standard
    - L10 hit rate >= 70%
    
    Sorted by: Most negative sharp_price (strongest locks first)
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    return await service.get_safe_haven(limit)


@router.get("/v3/ferrari/front-lines")
async def get_ferrari_front_lines(
    response: Response,
    limit: int = Query(10, ge=1, le=50)
):
    """
    FERRARI FRONT LINES - Battleground picks with real market edges.
    
    Criteria:
    - Sharp price between -149 and +110
    - 40-cent price gap from PP -137
    - L5 hit rate >= 60% (momentum check)
    
    Sorted by: Highest L10 hit rate
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    return await service.get_front_lines(limit)


@router.get("/v3/ferrari/war-zone")
async def get_ferrari_war_zone(
    response: Response,
    limit: int = Query(10, ge=1, le=50)
):
    """
    FERRARI WAR ZONE - Elite Demons with huge payout edges.
    
    Criteria:
    - Must be a demon (PP even odds)
    - Sharp price >= +500
    - Bovada 200+ pts shorter than PP implied
    - Hit at least 2 times in L10
    
    Sorted by: Highest sharp_price (biggest payout edge)
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    return await service.get_war_zone(limit)


@router.get("/v3/ferrari/discarded")
async def get_ferrari_discarded(
    response: Response,
    limit: int = Query(50, ge=1, le=100)
):
    """
    FERRARI DISCARDED - Props killed by the 15% separation filter.
    
    Shows what was filtered out for being "mid" plays.
    Useful for debugging and transparency.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    return await service.get_discarded(limit)


@router.post("/v3/ferrari/rebuild")
async def rebuild_ferrari_tiers():
    """
    Manually trigger a rebuild of all Ferrari tiers.
    
    Reads from cached_board and applies:
    1. Global 15% separation kill-switch
    2. Tier-specific classification
    3. Top 10 sorting per tier
    """
    from datetime import datetime, timezone
    
    service = get_service()
    result = await service.build_ferrari_tiers(datetime.now(timezone.utc))
    return result


@router.get("/v3/ferrari/all")
async def get_all_ferrari_tiers(
    response: Response,
    limit: int = Query(10, ge=1, le=50)
):
    """
    Get all Ferrari tiers in a single response.
    
    Returns:
    - safe_haven: Top 10 elite goblins
    - front_lines: Top 10 battleground picks
    - war_zone: Top 10 elite demons
    - stats: Filtering statistics
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    
    safe_haven = await service.get_safe_haven(limit)
    front_lines = await service.get_front_lines(limit)
    war_zone = await service.get_war_zone(limit)
    discarded = await service.get_discarded(10)  # Just stats
    
    return {
        "safe_haven": safe_haven,
        "front_lines": front_lines,
        "war_zone": war_zone,
        "stats": {
            "discarded_sample": discarded.get("count", 0),
            "kill_switch_threshold": discarded.get("kill_switch_threshold", "15%")
        }
    }
