"""
Defensive Momentum Routes
=========================
API endpoints for the Defensive Momentum service.

Endpoints:
- GET /api/v3/momentum/status - Get service status
- GET /api/v3/momentum/{team}/{stat_type} - Get momentum profile for team/stat
- GET /api/v3/momentum/rankings/{stat_type} - Get all team rankings for a stat
- POST /api/v3/momentum/rebuild - Trigger rebuild of momentum rankings
- GET /api/v3/momentum/modifier/{opponent}/{stat_type} - Get Ferrari modifier for matchup
"""
from fastapi import APIRouter, HTTPException, Response, Path, Query
from typing import Optional
from datetime import datetime, timezone
import logging

from services.defensive_momentum_service import get_momentum_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Defensive Momentum"])

# Database reference
_db = None


def set_momentum_db(db):
    """Set the database reference for Momentum service."""
    global _db
    _db = db


def get_service():
    """Get the Momentum service instance."""
    if _db is None:
        raise HTTPException(status_code=500, detail="Momentum service not initialized")
    return get_momentum_service(_db)


@router.get("/v3/momentum/status")
async def get_momentum_status(response: Response):
    """
    Get Defensive Momentum service status.
    
    Returns cache state, weights, and modifier thresholds.
    """
    response.headers["Cache-Control"] = "no-cache"
    
    service = get_service()
    await service.ensure_cache()
    
    return await service.get_status()


@router.get("/v3/momentum/rankings/{stat_type}")
async def get_momentum_rankings(
    response: Response,
    stat_type: str = Path(..., description="Stat type (e.g., PTS, AST, REB)"),
    limit: Optional[int] = Query(None, description="Limit number of teams")
):
    """
    Get all team momentum rankings for a stat type.
    
    Sorted by composite rank (best defense = rank 1).
    """
    response.headers["Cache-Control"] = "no-cache"
    
    service = get_service()
    await service.ensure_cache()
    
    rankings = service.get_all_team_momentum(stat_type.upper())
    
    if limit:
        rankings = rankings[:limit]
    
    return {
        "stat_type": stat_type.upper(),
        "rankings": rankings,
        "count": len(rankings),
        "weights": {
            "season": 0.50,
            "l10": 0.35,
            "l5": 0.15
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.post("/v3/momentum/rebuild")
async def rebuild_momentum():
    """
    Trigger rebuild of momentum rankings.
    
    This recalculates Season, L10, L5 rankings for all teams
    based on opponent game logs.
    """
    service = get_service()
    
    result = await service.build_momentum_rankings()
    
    return result


@router.get("/v3/momentum/modifier/{opponent}/{stat_type}")
async def get_momentum_modifier(
    response: Response,
    opponent: str = Path(..., description="Opponent team abbreviation"),
    stat_type: str = Path(..., description="Stat type")
):
    """
    Get Ferrari Score modifier for a matchup.
    
    Returns:
        - modifier: -15 (elite defense), 0 (middle), +15 (weak defense)
        - momentum_data: Full profile with ranks and trend
    """
    response.headers["Cache-Control"] = "no-cache"
    
    service = get_service()
    await service.ensure_cache()
    
    modifier, momentum_data = service.calculate_momentum_modifier(
        opponent.upper(),
        stat_type.upper()
    )
    
    return {
        "opponent": opponent.upper(),
        "stat_type": stat_type.upper(),
        "modifier": modifier,
        "momentum_data": momentum_data,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.get("/v3/momentum/test-detroit")
async def test_detroit_momentum(response: Response):
    """
    Test endpoint to verify Detroit Pistons momentum calculation.
    """
    response.headers["Cache-Control"] = "no-cache"
    
    service = get_service()
    await service.ensure_cache()
    
    pts_profile = service.get_momentum_profile("DET", "PTS")
    
    if not pts_profile:
        return {
            "team": "DET",
            "found": False,
            "message": "Detroit not found in cache. Try rebuilding: POST /api/v3/momentum/rebuild"
        }
    
    modifier, momentum_data = service.calculate_momentum_modifier("DET", "PTS")
    
    return {
        "team": "DET",
        "stat_type": "PTS",
        "profile": pts_profile.to_dict(),
        "modifier": modifier,
        "expected_result": "If L5/L10 is Top 5 with Season at 21, should show -15 penalty (difficult matchup)",
        "analysis": {
            "season_rank": pts_profile.season_rank,
            "l10_rank": pts_profile.l10_rank,
            "l5_rank": pts_profile.l5_rank,
            "composite_rank": pts_profile.composite_rank,
            "momentum": pts_profile.momentum,
            "trend_alert": pts_profile.trend_alert,
            "is_elite_composite": pts_profile.composite_rank <= 5,
            "modifier_applied": modifier
        }
    }


# This route must come LAST because it matches any two path segments
@router.get("/v3/momentum/{team}/{stat_type}")
async def get_team_momentum(
    response: Response,
    team: str = Path(..., description="Team abbreviation (e.g., DET, BOS)"),
    stat_type: str = Path(..., description="Stat type (e.g., PTS, AST, REB)")
):
    """
    Get momentum profile for a specific team/stat combination.
    """
    response.headers["Cache-Control"] = "no-cache"
    
    service = get_service()
    await service.ensure_cache()
    
    profile = service.get_momentum_profile(team.upper(), stat_type.upper())
    
    if not profile:
        return {
            "team": team.upper(),
            "stat_type": stat_type.upper(),
            "found": False,
            "message": f"No momentum data for {team.upper()} / {stat_type.upper()}"
        }
    
    data = profile.to_dict()
    data["found"] = True
    data["tooltip"] = service._generate_tooltip(profile)
    
    return data
