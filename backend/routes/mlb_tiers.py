"""
MLB Tiers API Routes - 1:1 Clone of NBA Ferrari Routes
========================================================
Serves mlb_safe_haven, mlb_front_lines, mlb_war_zone collections.
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, Dict, Any
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

router = APIRouter(tags=["MLB Tiers"])

# Database reference (set by server.py)
_db = None

def set_mlb_tiers_db(db):
    global _db
    _db = db
    logger.info("[MLB_TIERS] Database connection set")


# =============================================================================
# MLB TIER ENDPOINTS - Direct 1:1 with NBA
# =============================================================================

@router.get("/v3/mlb/safe-haven")
async def get_mlb_safe_haven(limit: int = Query(default=10, le=20)):
    """
    Get MLB Safe Haven picks (DK <= -250, highest juice).
    Reads from mlb_safe_haven collection.
    """
    if _db is None:
        raise HTTPException(status_code=503, detail="Database not connected")
    
    try:
        collection = _db.mlb_safe_haven
        
        # Fetch picks with vision_intel
        cursor = collection.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        
        # Count picks with vision_intel
        with_intel = sum(1 for p in picks if p.get("vision_intel"))
        
        return {
            "tier": "safe_haven",
            "tier_label": "SAFE HAVEN",
            "logic": "DK Odds <= -250 | 3-Gate Qualified | JIT Vision Intel",
            "sport": "mlb",
            "collection": "mlb_safe_haven",
            "picks": picks,
            "count": len(picks),
            "with_vision_intel": with_intel,
            "fetched_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"[MLB_SAFE_HAVEN] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/v3/mlb/front-lines")
async def get_mlb_front_lines(limit: int = Query(default=10, le=20)):
    """
    Get MLB Front Lines picks (DK -249 to +199, mid-juice).
    Reads from mlb_front_lines collection.
    """
    if _db is None:
        raise HTTPException(status_code=503, detail="Database not connected")
    
    try:
        collection = _db.mlb_front_lines
        
        cursor = collection.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        
        with_intel = sum(1 for p in picks if p.get("vision_intel"))
        
        return {
            "tier": "front_lines",
            "tier_label": "THE FRONT LINES",
            "logic": "DK Odds -249 to +199 | 3-Gate Qualified | JIT Vision Intel",
            "sport": "mlb",
            "collection": "mlb_front_lines",
            "picks": picks,
            "count": len(picks),
            "with_vision_intel": with_intel,
            "fetched_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"[MLB_FRONT_LINES] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/v3/mlb/war-zone")
async def get_mlb_war_zone(limit: int = Query(default=10, le=20)):
    """
    Get MLB War Zone picks (DK >= +200, longshots).
    Reads from mlb_war_zone collection.
    """
    if _db is None:
        raise HTTPException(status_code=503, detail="Database not connected")
    
    try:
        collection = _db.mlb_war_zone
        
        cursor = collection.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        
        with_intel = sum(1 for p in picks if p.get("vision_intel"))
        
        return {
            "tier": "war_zone",
            "tier_label": "WAR ZONE",
            "logic": "DK Odds >= +200 | Ceiling Protocol | JIT Vision Intel",
            "sport": "mlb",
            "collection": "mlb_war_zone",
            "picks": picks,
            "count": len(picks),
            "with_vision_intel": with_intel,
            "fetched_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"[MLB_WAR_ZONE] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v3/mlb/rebuild")
async def rebuild_mlb_tiers(save_to_db: bool = Query(default=True)):
    """
    MLB Rebuild — routes through Rebuild Coordinator → UnifiedPipeline(MLBAdapter).

    Phase 3: Same authoritative publish path as all MLB syncs.
    """
    if _db is None:
        raise HTTPException(status_code=503, detail="Database not connected")

    try:
        from services.event_bus import BoardEvent, get_event_bus
        from services.rebuild_coordinator import get_coordinator
        import asyncio

        event = BoardEvent(
            sport="mlb",
            event_type="manual",
            severity="high",
            source="manual_api_mlb_rebuild",
        )
        await get_event_bus().publish(event)
        await asyncio.sleep(1)

        stats = get_coordinator().get_stats()
        last = stats.get("last_publish", {}).get("mlb", {})

        return {
            "success": True,
            "message": "MLB rebuild dispatched via coordinator",
            "coordinator_mode": stats["sport_modes"]["mlb"],
            "last_publish": last,
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"[MLB_REBUILD] Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# UNIFIED ENDPOINT - Same as NBA /v3/ferrari/safe-haven?sport=mlb
# =============================================================================

@router.get("/v3/mlb/ferrari/safe-haven")
async def get_mlb_ferrari_safe_haven(limit: int = Query(default=10, le=20)):
    """Alias for /v3/mlb/safe-haven (matches NBA naming)."""
    return await get_mlb_safe_haven(limit=limit)


@router.get("/v3/mlb/ferrari/front-lines")
async def get_mlb_ferrari_front_lines(limit: int = Query(default=10, le=20)):
    """Alias for /v3/mlb/front-lines (matches NBA naming)."""
    return await get_mlb_front_lines(limit=limit)


@router.get("/v3/mlb/ferrari/war-zone")
async def get_mlb_ferrari_war_zone(limit: int = Query(default=10, le=20)):
    """Alias for /v3/mlb/war-zone (matches NBA naming)."""
    return await get_mlb_war_zone(limit=limit)
