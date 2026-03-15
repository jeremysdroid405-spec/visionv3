"""
Legacy Routes
=============
Backward-compatible legacy endpoints.
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Legacy"])

# Reference to DemonGoblinEngine and stats_manager (set via dependency injection)
_demon_goblin_engine = None
_stats_manager = None


def set_legacy_deps(engine, stats_manager=None):
    """Set legacy route dependencies."""
    global _demon_goblin_engine, _stats_manager
    _demon_goblin_engine = engine
    _stats_manager = stats_manager


def get_engine():
    """Get the demon goblin engine instance."""
    if _demon_goblin_engine is None:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    return _demon_goblin_engine


@router.get("/full-board")
async def get_full_board():
    """
    LEGACY: Get full board with mock data for testing.
    Use /v3/board or /v3/cached-props for production.
    """
    return {
        "success": True,
        "message": "Legacy endpoint - use /v3/board for production",
        "data": {
            "players": [],
            "timestamp": None
        }
    }


@router.get("/calculate-hit-rate")
async def calculate_hit_rate(
    player_name: str = Query(...),
    stat_type: str = Query(...),
    line: float = Query(...)
):
    """
    Calculate hit rate for a specific player/stat/line combination.
    
    Args:
    - player_name: Player name
    - stat_type: Stat type (PTS, REB, AST, etc.)
    - line: The betting line to check against
    
    Returns:
    - hit_rate: Percentage of games over the line
    - games_checked: Number of games analyzed
    - last_5_values: Recent game values
    """
    engine = get_engine()
    
    # Get player stats from cache or BDL
    stats = await engine.get_cached_player_stats(player_name)
    
    if not stats:
        return {
            "success": False,
            "player_name": player_name,
            "stat_type": stat_type,
            "line": line,
            "hit_rate": 0,
            "games_checked": 0,
            "message": "Player stats not found"
        }
    
    # Calculate hit rate from game logs
    stat_key = stat_type.lower()
    game_logs = stats.get("game_logs", [])
    
    if not game_logs:
        return {
            "success": False,
            "player_name": player_name,
            "stat_type": stat_type,
            "line": line,
            "hit_rate": 0,
            "games_checked": 0,
            "message": "No game logs found"
        }
    
    hits = sum(1 for g in game_logs if g.get(stat_key, 0) > line)
    hit_rate = (hits / len(game_logs)) * 100 if game_logs else 0
    
    return {
        "success": True,
        "player_name": player_name,
        "stat_type": stat_type,
        "line": line,
        "hit_rate": round(hit_rate, 1),
        "games_checked": len(game_logs),
        "hits": hits,
        "last_5_values": [g.get(stat_key, 0) for g in game_logs[:5]]
    }


@router.get("/validate-demon")
async def validate_demon(
    player_name: str = Query(...),
    prop_type: str = Query(...),
    demon_line: float = Query(...)
):
    """
    Validate if a prop qualifies as a Demon pick.
    
    A Demon is identified when:
    - Line is significantly above season average
    - Player has shown ability to exceed this line
    - Risk/reward ratio meets threshold
    
    Returns:
    - is_valid_demon: Boolean
    - reason: Explanation
    - ev_score: Expected value score
    """
    engine = get_engine()
    
    # Get player from cached board
    player = await engine.cached_board.find_one(
        {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
        {"_id": 0}
    )
    
    if not player:
        return {
            "is_valid_demon": False,
            "reason": f"Player '{player_name}' not found in cached board",
            "ev_score": 0
        }
    
    # Check if already tagged as demon
    if player.get("is_demon"):
        return {
            "is_valid_demon": True,
            "reason": "Already validated as Demon pick",
            "ev_score": player.get("ev_score", 0),
            "hit_rate_10": player.get("hit_rate_10", 0)
        }
    
    # Validate based on criteria
    hit_rate = player.get("hit_rate_10", 0)
    season_avg = player.get(f"season_avg_{prop_type.lower()}", 0)
    
    # Demon criteria: line above season avg by 15%+
    is_above_avg = demon_line > (season_avg * 1.15) if season_avg > 0 else False
    has_hit_potential = hit_rate > 30  # At least 30% hit rate
    
    is_valid = is_above_avg and has_hit_potential
    
    return {
        "is_valid_demon": is_valid,
        "reason": "Line significantly above season average with hit potential" if is_valid else "Does not meet Demon criteria",
        "ev_score": player.get("ev_score", 0),
        "season_avg": season_avg,
        "demon_line": demon_line,
        "hit_rate_10": hit_rate
    }


@router.get("/")
async def root():
    """
    API root endpoint - returns basic API info.
    """
    return {
        "name": "PickVision API",
        "version": "3.0",
        "status": "operational",
        "endpoints": {
            "status": "/api/v3/status",
            "war_zone": "/api/v3/war-zone",
            "safe_haven": "/api/v3/goblin-vault",
            "front_lines": "/api/v3/front-lines",
            "docs": "/docs"
        }
    }
