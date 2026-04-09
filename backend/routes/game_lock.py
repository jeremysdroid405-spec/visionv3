"""
Game Lock Routes
================
Game Lock Engine endpoints for managing game start times and parlay validation.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
import logging

from services.engines.game_lock_engine import get_game_lock_engine

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Game Lock"])


class ParlayValidationRequest(BaseModel):
    player_names: List[str]


def get_engine():
    """Get the game lock engine instance."""
    engine = get_game_lock_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Game Lock Engine not initialized")
    return engine


@router.get("/v3/lock-status")
async def get_lock_status():
    """
    GAME LOCK STATUS - Dashboard overview of active/locked games.
    
    Returns:
    - active_games: Number of games still open for betting
    - locked_games: Number of games that have started (removed from feeds)
    - t_minus_games: Number of games starting in <15 minutes
    - t_minus_details: Top 5 soonest games with countdown timers
    - engine_running: Whether the 60-second lock check loop is active
    """
    engine = get_engine()
    result = await engine.get_lock_status()
    return result


@router.get("/v3/t-minus-games")
async def get_t_minus_games():
    """
    T-MINUS COUNTDOWN - Games starting within 15 minutes.
    
    Returns games with:
    - t_minus_seconds: Seconds until tip-off
    - t_minus_display: Human-readable format (e.g., "T-12:45")
    - matchup info and player count
    
    Use for high-stakes countdown timers on player cards.
    """
    engine = get_engine()
    result = await engine.get_t_minus_games()
    return {"games": result, "count": len(result)}


@router.get("/v3/locked-games")
async def get_locked_games():
    """
    LOCKED GAMES - Games that have started and are in progress.
    
    Use for Live Score Ticker integration - these games have been
    removed from the betting board but can be shown in real-time.
    """
    engine = get_engine()
    result = await engine.get_locked_games()
    return {"games": result, "count": len(result)}

# =============================================================================
# DUPLICATE ROUTE REMOVED - /v3/validate-parlay is now served by parlays.py
# =============================================================================


@router.post("/v3/check-locks")
async def manual_check_locks():
    """
    MANUAL LOCK CHECK - Trigger immediate lock check.
    
    Forces an immediate check for games that should be locked.
    Normally runs automatically every 60 seconds.
    """
    engine = get_engine()
    result = await engine.check_and_lock_games()
    return result
