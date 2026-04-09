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
        {"_id": 0, "sync_time": 1, "synced_at": 1},
        sort=[("synced_at", -1)]
    )
    
    total_players = await engine.cached_board.count_documents({})
    
    # Count demons and goblins from the nested props structure
    # Each player document has a props array with is_demon/is_goblin booleans
    pipeline = [
        {"$unwind": "$props"},
        {"$group": {
            "_id": None,
            "demons": {"$sum": {"$cond": ["$props.is_demon", 1, 0]}},
            "goblins": {"$sum": {"$cond": ["$props.is_goblin", 1, 0]}}
        }}
    ]
    counts = {"demons": 0, "goblins": 0}
    async for doc in engine.cached_board.aggregate(pipeline):
        counts["demons"] = doc.get("demons", 0)
        counts["goblins"] = doc.get("goblins", 0)
    
    return {
        "success": True,
        "data": {
            "last_sync": latest.get("synced_at") or latest.get("sync_time") if latest else None,
            "sync_source": "adaptive_sync_engine",
            "total_players": total_players,
            "demons_count": counts["demons"],
            "goblins_count": counts["goblins"],
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


# =============================================================================
# DUPLICATE ROUTES REMOVED - These are now served by their dedicated files:
# - /v3/players -> board.py
# - /v3/player/{player_name} -> board.py
# - /v3/demons -> picks.py
# - /v3/goblins -> picks.py
# - /v3/search -> board.py
# - /v3/board -> cached_data.py
# - /v3/trending -> board.py
# - /v3/most-popular-bets -> picks.py
# =============================================================================
