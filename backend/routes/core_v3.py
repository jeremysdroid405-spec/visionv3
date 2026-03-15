"""
Core V3 Routes
==============
Main API v3 endpoints for status, players, demons, goblins, and board data.
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Core V3"])

# Reference to DemonGoblinEngine (set via dependency injection)
_demon_goblin_engine = None


def set_core_v3_engine(engine):
    """Set the demon goblin engine reference."""
    global _demon_goblin_engine
    _demon_goblin_engine = engine


def get_engine():
    """Get the demon goblin engine instance."""
    if _demon_goblin_engine is None:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    return _demon_goblin_engine


@router.get("/v3/status")
async def get_v3_status():
    """
    Get current system status including last sync time and counts.
    """
    engine = get_engine()
    # Get status from cached_board collection
    latest = await engine.cached_board.find_one(
        {},
        {"_id": 0, "sync_time": 1},
        sort=[("sync_time", -1)]
    )
    
    total_players = await engine.cached_board.count_documents({})
    demons_count = await engine.radar_picks.count_documents({})
    goblins_count = await engine.goblin_vault.count_documents({})
    
    return {
        "success": True,
        "data": {
            "last_sync": latest.get("sync_time") if latest else None,
            "sync_source": "adaptive_sync_engine",
            "total_players": total_players,
            "demons_count": demons_count,
            "goblins_count": goblins_count,
            "season": "2025"
        }
    }


@router.post("/v3/sync")
async def trigger_v3_sync():
    """
    Manually trigger a data sync from Odds API.
    Updates cached_board with fresh lines.
    """
    engine = get_engine()
    result = await engine.sync_odds_to_mongo()
    return result


@router.get("/v3/players")
async def get_v3_players(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    team: Optional[str] = None,
    sort_by: str = Query("hit_rate", enum=["hit_rate", "ev", "name"])
):
    """
    Get all players with their props.
    Supports pagination and filtering by team.
    """
    engine = get_engine()
    
    # Build query
    query = {}
    if team:
        query["team"] = team.upper()
    
    # Get players from cached_board
    cursor = engine.cached_board.find(query, {"_id": 0})
    
    # Apply sorting
    if sort_by == "hit_rate":
        cursor = cursor.sort("hit_rate_10", -1)
    elif sort_by == "ev":
        cursor = cursor.sort("ev_score", -1)
    else:
        cursor = cursor.sort("player_name", 1)
    
    # Apply pagination
    players = await cursor.skip(offset).limit(limit).to_list(None)
    total = await engine.cached_board.count_documents(query)
    
    return {
        "success": True,
        "players": players,
        "total": total,
        "limit": limit,
        "offset": offset
    }


@router.get("/v3/player/{player_name}")
async def get_v3_player(player_name: str):
    """
    Get detailed data for a single player.
    Includes all props, hit rates, and AI insights.
    """
    engine = get_engine()
    result = await engine.picks_getter_service.get_cached_player(player_name)
    
    if not result or not result.get("success"):
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found")
    
    return result


@router.get("/v3/demons")
async def get_v3_demons(limit: int = Query(50, ge=1, le=200)):
    """
    Get all Demon picks (high-risk, high-reward).
    Demons have lines significantly above season averages.
    """
    engine = get_engine()
    demons = await engine.radar_picks.find(
        {},
        {"_id": 0}
    ).sort("ev_score", -1).limit(limit).to_list(None)
    
    return {
        "success": True,
        "demons": demons,
        "count": len(demons)
    }


@router.get("/v3/goblins")
async def get_v3_goblins(limit: int = Query(50, ge=1, le=200)):
    """
    Get all Goblin picks (safer, consistent).
    Goblins have high hit rates and lower volatility.
    """
    engine = get_engine()
    goblins = await engine.goblin_vault.find(
        {},
        {"_id": 0}
    ).sort("hit_rate_10", -1).limit(limit).to_list(None)
    
    return {
        "success": True,
        "goblins": goblins,
        "count": len(goblins)
    }


@router.get("/v3/search")
async def search_v3_players(q: str = Query(..., min_length=2)):
    """
    Search players by name (fuzzy matching).
    """
    engine = get_engine()
    
    # Use regex for partial matching
    import re
    regex_pattern = re.compile(f".*{re.escape(q)}.*", re.IGNORECASE)
    
    players = await engine.cached_board.find(
        {"player_name": {"$regex": regex_pattern}},
        {"_id": 0}
    ).limit(20).to_list(None)
    
    return {
        "success": True,
        "query": q,
        "players": players,
        "count": len(players)
    }


@router.get("/v3/board")
async def get_v3_board(
    include_locked: bool = Query(True),
    limit: int = Query(100, ge=1, le=500)
):
    """
    Get the full player board with all props.
    Use include_locked=false to hide games that have started.
    """
    engine = get_engine()
    result = await engine.picks_getter_service.get_cached_board()
    return result


@router.get("/v3/trending")
async def get_v3_trending(limit: int = Query(10, ge=1, le=50)):
    """
    Get trending players based on recent activity.
    """
    engine = get_engine()
    
    # Get top players by demon/goblin status and hit rate
    trending = await engine.cached_board.find(
        {"$or": [{"is_demon": True}, {"is_goblin": True}]},
        {"_id": 0}
    ).sort("hit_rate_10", -1).limit(limit).to_list(None)
    
    return {
        "success": True,
        "trending": trending,
        "count": len(trending)
    }


@router.get("/v3/most-popular-bets")
async def get_v3_most_popular_bets():
    """
    Get the most popular bets across all players.
    Used for the live ticker on the dashboard.
    """
    engine = get_engine()
    result = await engine.picks_getter_service.get_most_popular_bets()
    return result
