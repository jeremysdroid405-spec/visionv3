"""
Cached Data Routes
==================
Endpoints for reading cached/warehouse data with zero API calls.
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Cached Data"])

# Reference to DemonGoblinEngine (set via dependency injection)
_demon_goblin_engine = None


def set_cached_data_engine(engine):
    """Set the demon goblin engine reference."""
    global _demon_goblin_engine
    _demon_goblin_engine = engine


def get_engine():
    """Get the demon goblin engine instance."""
    if _demon_goblin_engine is None:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    return _demon_goblin_engine


@router.get("/v3/static-shell")
async def get_static_shell():
    """
    Get STATIC SHELL data (24h TTL)
    Contains: Player metadata, teams, positions, historical stats
    Does NOT contain: Live betting lines
    
    Use this for initial page load - instant render of player cards
    """
    engine = get_engine()
    shell = await engine.get_static_shell()
    
    return {
        "success": True,
        "cache_hit": shell.get("cache_hit", False),
        "cache_age_seconds": shell.get("cache_age_seconds", 0),
        "sync_date": shell.get("sync_date"),
        "players_count": len(shell.get("players", [])),
        "players": shell.get("players", []),
        "trending": shell.get("trending", [])
    }


@router.get("/v3/live-lines")
async def get_live_lines():
    """
    Get DYNAMIC PULSE data (60s TTL)
    Contains ONLY: Live betting lines (price, point, demon/goblin tags)
    
    Use this to hydrate cards with live data after initial render
    Lightweight endpoint - minimal payload
    """
    engine = get_engine()
    lines = await engine.get_live_lines()
    
    # Count totals
    total_lines = sum(len(v) for v in lines.get("lines", {}).values())
    total_demons = sum(
        sum(1 for line in player_lines if line.get("is_demon"))
        for player_lines in lines.get("lines", {}).values()
    )
    total_goblins = sum(
        sum(1 for line in player_lines if line.get("is_goblin"))
        for player_lines in lines.get("lines", {}).values()
    )
    
    return {
        "success": True,
        "cache_hit": lines.get("cache_hit", False),
        "cache_age_seconds": lines.get("cache_age_seconds", 0),
        "last_update": lines.get("last_update"),
        "total_lines": total_lines,
        "total_demons": total_demons,
        "total_goblins": total_goblins,
        "players_count": len(lines.get("lines", {})),
        "lines": lines.get("lines", {})
    }


@router.get("/v3/hydrated-board")
async def get_hydrated_board():
    """
    DEPRECATED - Use /api/v3/cached-props instead.
    Redirects to cached board for backward compatibility.
    """
    return await get_cached_props()


@router.get("/v3/cached-props")
async def get_cached_props(include_locked: bool = True):
    """
    THE PRIMARY ENDPOINT - Reads ONLY from MongoDB.
    NO Odds API calls. Zero credit usage.
    
    Returns the full cached board with:
    - All players grouped by props (with locked status marked)
    - Trending 10
    - Demon/Goblin flags
    - Hit rates
    - AI Vision summaries
    
    Filter with include_locked=false to hide locked games from frontend.
    """
    engine = get_engine()
    result = await engine.get_cached_board(include_locked=include_locked)
    return result


@router.get("/v3/cached-player/{player_name}")
async def get_cached_player(player_name: str):
    """
    Get cached data for a single player.
    No API calls - reads from MongoDB cached_board.
    
    Returns:
    - Player metadata
    - All props with lines
    - Hit rates
    - AI Vision summary
    - Demon/Goblin status
    """
    engine = get_engine()
    result = await engine.get_cached_player(player_name)
    
    if not result:
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found in cache")
    
    return result
