"""
AI Context Routes Module
========================
Handles AI context engine endpoints for player evaluation
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v3/ai-context", tags=["ai-context"])

# DB and engine references (set by main app)
_db = None
_ai_context_engine_class = None


def set_ai_context_deps(db, engine_class):
    """Set the DB and AI context engine class references."""
    global _db, _ai_context_engine_class
    _db = db
    _ai_context_engine_class = engine_class


@router.post("/run")
async def run_ai_context_engine(limit: Optional[int] = Query(None, description="Limit number of players to process")):
    """
    Run the AI Context Engine to evaluate all players.
    
    This will:
    1. Fetch news/injury reports for each player
    2. Send to LLM for impact evaluation
    3. Update nba_master_hub_2026 with ai_context_score and ai_context_reason
    """
    if _db is None or _ai_context_engine_class is None:
        raise HTTPException(status_code=500, detail="AI Context Engine not configured")
    
    engine = _ai_context_engine_class(_db)
    result = await engine.update_master_hub_with_context(limit=limit)
    return result


@router.post("/evaluate/{player_name}")
async def evaluate_player_context(player_name: str):
    """Evaluate and update context for a single player."""
    if _db is None or _ai_context_engine_class is None:
        raise HTTPException(status_code=500, detail="AI Context Engine not configured")
    
    engine = _ai_context_engine_class(_db)
    result = await engine.evaluate_single_player(player_name)
    return result


@router.get("/status")
async def get_ai_context_status():
    """Get status of AI context scores in the master hub."""
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    
    total = await _db[COLL("master_hub", "nba")].count_documents({})
    with_context = await _db[COLL("master_hub", "nba")].count_documents({"ai_context_score": {"$exists": True}})
    
    positive = await _db[COLL("master_hub", "nba")].count_documents({"ai_context_score": {"$gt": 0.6}})
    neutral = await _db[COLL("master_hub", "nba")].count_documents({"ai_context_score": {"$gte": 0.4, "$lte": 0.6}})
    negative = await _db[COLL("master_hub", "nba")].count_documents({"ai_context_score": {"$lt": 0.4}})
    
    latest = await _db[COLL("master_hub", "nba")].find_one(
        {"ai_context_updated_at": {"$exists": True}},
        {"_id": 0, "player_name": 1, "ai_context_updated_at": 1},
        sort=[("ai_context_updated_at", -1)]
    )
    
    return {
        "success": True,
        "total_players": total,
        "players_with_context": with_context,
        "coverage_pct": round((with_context / total * 100) if total > 0 else 0, 1),
        "distribution": {
            "positive_boost": positive,
            "neutral": neutral,
            "negative_flag": negative
        },
        "last_update": latest.get("ai_context_updated_at") if latest else None,
        "last_player": latest.get("player_name") if latest else None
    }


@router.get("/player/{player_name}")
async def get_player_context(player_name: str):
    """Get AI context data for a specific player."""
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    
    player = await _db[COLL("master_hub", "nba")].find_one(
        {"$or": [
            {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}}
        ]},
        {"_id": 0, "display_name": 1, "player_name": 1, "ai_context_score": 1, "ai_context_reason": 1, "ai_context_updated_at": 1}
    )
    
    if not player:
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found")
    
    return {
        "success": True,
        "player_name": player.get("display_name") or player.get("player_name"),
        "context_score": player.get("ai_context_score", 0.5),
        "context_reason": player.get("ai_context_reason", "Not evaluated"),
        "updated_at": player.get("ai_context_updated_at")
    }
