"""
Ferrari Tier Routes
===================
API endpoints for the "Best of the Best" Ferrari-filtered picks.

Uses Bovada separation as the primary sharp benchmark.
Global 15% kill-switch ensures only elite plays are visible.
Whistle Matrix applies referee-based modifiers to power scores.
"""
from fastapi import APIRouter, HTTPException, Query, Response
from typing import Dict, Any
import logging

from services.ferrari_tier_service import get_ferrari_tier_service
from services.referee_scraper_service import get_referee_service

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
    1. Whistle Matrix sync (referee data)
    2. Global 15% separation kill-switch
    3. Power Score + Whistle Modifier calculation
    4. Top 10 sorting per tier
    """
    from datetime import datetime, timezone
    
    service = get_service()
    result = await service.build_ferrari_tiers(datetime.now(timezone.utc))
    return result


@router.post("/v3/ferrari/sync-refs")
async def sync_referee_data():
    """
    Manually sync referee assignments and stats.
    
    Fetches:
    - Daily assignments from official.nba.com
    - Referee O/U and PPG stats from Covers.com
    
    Returns whistle classifications for today's crews.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    ref_service = get_referee_service(_db)
    result = await ref_service.sync_all()
    return result


@router.get("/v3/ferrari/refs")
async def get_todays_refs(response: Response):
    """
    Get today's referee assignments with whistle classifications.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    ref_service = get_referee_service(_db)
    
    # Return cached assignments - convert dict_values to list explicitly
    assignments = list(ref_service.daily_assignments_cache.values()) if ref_service.daily_assignments_cache else []
    
    # Dedupe (same game appears for both teams)
    seen_games = set()
    unique_assignments = []
    for a in assignments:
        # Ensure a is a dict
        if not isinstance(a, dict):
            continue
        game = a.get("game", "")
        if game not in seen_games:
            seen_games.add(game)
            # Enrich with stats
            crew_chief = a.get("crew_chief", "")
            normalized = ref_service._normalize_ref_name(crew_chief)
            stats = ref_service.referee_stats_cache.get(normalized, {})
            # Build a clean dict without any non-serializable objects
            unique_assignments.append({
                "game": a.get("game"),
                "away_team": a.get("away_team"),
                "home_team": a.get("home_team"),
                "crew_chief": a.get("crew_chief"),
                "referee": a.get("referee"),
                "umpire": a.get("umpire"),
                "date": a.get("date"),
                "ppg": stats.get("ppg"),
                "ou_pct": stats.get("ou_pct"),
                "whistle_class": stats.get("whistle_class", "neutral")
            })
    
    # Get date safely
    date_str = None
    if ref_service.last_assignments_fetch:
        try:
            date_str = ref_service.last_assignments_fetch.strftime("%Y-%m-%d")
        except Exception:
            date_str = None
    
    return {
        "date": date_str,
        "assignments": unique_assignments,
        "total_refs_in_cache": len(ref_service.referee_stats_cache) if ref_service.referee_stats_cache else 0,
        "total_games": len(unique_assignments)
    }


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
    - verification: Market Intel stats
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    
    safe_haven = await service.get_safe_haven(limit)
    front_lines = await service.get_front_lines(limit)
    war_zone = await service.get_war_zone(limit)
    
    # Get verification stats from any tier (they all share the same stats)
    verification = safe_haven.get("verification", {})
    active_props = verification.get("active_props_verified", 0)
    output_total = safe_haven.get("count", 0) + front_lines.get("count", 0) + war_zone.get("count", 0)
    
    return {
        "safe_haven": safe_haven,
        "front_lines": front_lines,
        "war_zone": war_zone,
        "verification": {
            "active_props_verified": active_props,
            "elite_opportunities": output_total,
            "safe_haven_pool": verification.get("safe_haven_pool", 0),
            "front_lines_pool": verification.get("front_lines_pool", 0),
            "war_zone_pool": verification.get("war_zone_pool", 0),
            "message": f"Verified {active_props} active props to identify these {output_total} Elite opportunities."
        }
    }
