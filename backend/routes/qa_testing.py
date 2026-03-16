"""
QA DIRECTIVE 2: Reactivity Injection Test Endpoint
==================================================
Temporarily injects a line move to test TanStack Query polling.
"""
from fastapi import APIRouter, HTTPException
from typing import Optional
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/qa", tags=["QA Testing"])

# Database reference (set by server.py)
_db = None

def set_qa_db(db):
    global _db
    _db = db

@router.post("/inject-line-move")
async def inject_line_move(
    player_name: Optional[str] = "Luka Doncic",
    new_line: float = 99.5,
    stat_type: str = "points"
):
    """
    QA TEST: Inject a fake line move to test UI reactivity.
    
    Within 30 seconds, the UI should auto-update without refresh.
    
    Args:
        player_name: Player to modify (default: Luka Doncic)
        new_line: New line value (default: 99.5 - obviously fake)
        stat_type: Stat type to modify (default: points)
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    cached_board = _db.dg_cached_board
    
    # Find the player document
    player_doc = await cached_board.find_one({
        "player_name": {"$regex": player_name, "$options": "i"}
    })
    
    if not player_doc:
        sample_players = await cached_board.distinct("player_name")
        return {
            "success": False,
            "error": f"Player '{player_name}' not found",
            "available_players_sample": sample_players[:20]
        }
    
    player_found = player_doc.get("player_name")
    props = player_doc.get("props", [])
    
    # Find the specific prop
    prop_index = None
    original_line = None
    stat_type_lower = stat_type.lower()
    
    # Map common names to extracted format
    stat_map = {
        "points": "PTS",
        "rebounds": "REB", 
        "assists": "AST",
        "threes": "3PM",
        "steals": "STL",
        "blocks": "BLK"
    }
    search_stat = stat_map.get(stat_type_lower, stat_type.upper())
    
    for i, prop in enumerate(props):
        prop_stat = prop.get("stat_type_extracted", "")
        if prop_stat and prop_stat.upper() == search_stat:
            prop_index = i
            original_line = prop.get("line")
            break
    
    if prop_index is None:
        available_stats = list(set([p.get("stat_type_extracted") for p in props if p.get("stat_type_extracted")]))
        return {
            "success": False,
            "error": f"Stat type '{stat_type}' (searched as {search_stat}) not found for {player_found}",
            "available_stats": available_stats
        }
    
    # Inject the new line
    update_path = f"props.{prop_index}.line"
    await cached_board.update_one(
        {"_id": player_doc["_id"]},
        {
            "$set": {
                update_path: new_line,
                "_qa_injection": {
                    "original_line": original_line,
                    "prop_index": prop_index,
                    "stat_type": stat_type
                }
            }
        }
    )
    
    logger.info(f"[QA INJECT] {player_found} {stat_type} line moved: {original_line} -> {new_line}")
    
    return {
        "success": True,
        "player": player_found,
        "stat_type": stat_type,
        "original_line": original_line,
        "injected_line": new_line,
        "message": f"Line injected! Watch the UI - within 30 seconds, {player_found}'s {stat_type} line should change from {original_line} to {new_line} WITHOUT refreshing the page.",
        "revert_endpoint": "POST /api/qa/revert-line-move"
    }


@router.post("/revert-line-move")
async def revert_line_move():
    """Revert all QA line injections back to original values."""
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    cached_board = _db.dg_cached_board
    
    # Find all docs with QA injection marker
    injected = cached_board.find({"_qa_injection": {"$exists": True}})
    
    reverted = []
    async for doc in injected:
        qa_info = doc.get("_qa_injection", {})
        original = qa_info.get("original_line")
        prop_index = qa_info.get("prop_index")
        stat_type = qa_info.get("stat_type")
        
        if prop_index is not None:
            update_path = f"props.{prop_index}.line"
            await cached_board.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {update_path: original},
                    "$unset": {"_qa_injection": ""}
                }
            )
            reverted.append({
                "player": doc.get("player_name"),
                "stat_type": stat_type,
                "reverted_to": original
            })
    
    return {
        "success": True,
        "reverted_count": len(reverted),
        "reverted": reverted
    }
