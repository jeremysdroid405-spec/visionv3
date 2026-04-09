"""
Roster Routes - Semantic Roster Endpoints
==========================================
Explicit, clearly-named endpoints for different roster types.

Three distinct roster endpoints:
1. /api/roster/full-active - Full current NBA roster (~430-450 players)
2. /api/roster/mapped - Players with full system support/mapping
3. /api/roster/live-today - Players with active props today (~141 players)

Created: 2025-01 for P0 - Roster API Semantic Separation
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional, Dict, Any, List
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/roster", tags=["Roster"])

# Database reference (set via dependency injection)
_db = None


def set_roster_db(db):
    """Set the database reference for roster routes."""
    global _db
    _db = db


def get_db():
    """Get the database instance."""
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    return _db


# =============================================================================
# CANONICAL ROSTER ENDPOINTS
# =============================================================================

@router.get("/full-active")
async def get_full_active_roster(
    team: Optional[str] = Query(None, description="Filter by team abbreviation (e.g., LAL, BOS)"),
    position: Optional[str] = Query(None, description="Filter by position (e.g., G, F, C, PG, SF)"),
    limit: int = Query(500, ge=1, le=600, description="Max players to return"),
    offset: int = Query(0, ge=0, description="Pagination offset")
) -> Dict[str, Any]:
    """
    FULL ACTIVE NBA ROSTER
    ======================
    Returns the complete current active NBA roster from the Master Hub.
    
    This includes ALL active NBA players (~430-450), regardless of whether
    they have props available today.
    
    Source: nba_master_hub_2026 (Single Source of Truth)
    
    Use this endpoint when you need:
    - Complete league roster view
    - Player search/lookup across all NBA players
    - Pre-game research before props are posted
    
    Returns:
        - player_name: Display name
        - team: Team abbreviation
        - position: Player position
        - bdl_id: BallDontLie ID
        - nba_id: NBA.com ID (if available)
        - photo_url: Player headshot URL
        - baseline_stats: Season averages (if synced)
    """
    db = get_db()
    
    # Build query for active players
    query = {}
    if team:
        query["team"] = team.upper()
    if position:
        # Handle both short (G, F, C) and full (PG, SF, etc.) position codes
        query["position"] = {"$regex": position.upper(), "$options": "i"}
    
    # Project only essential fields to reduce payload
    projection = {
        "_id": 0,
        "player_name": "$display_name",
        "display_name": 1,
        "team": 1,
        "position": 1,
        "bdl_id": 1,
        "nba_id": 1,
        "photo_url": 1,
        "jersey_number": 1,
        "height": 1,
        "weight": 1,
        "college": 1,
        "country": 1,
        "draft_year": 1,
        "draft_round": 1,
        "draft_number": 1,
        "is_active": 1,
        "synced_at": 1
    }
    
    # Query Master Hub for all active players
    cursor = db.nba_master_hub_2026.find(query, projection)
    cursor = cursor.sort("display_name", 1)  # Alphabetical
    cursor = cursor.skip(offset).limit(limit)
    
    players = await cursor.to_list(None)
    
    # Get total count for pagination
    total = await db.nba_master_hub_2026.count_documents(query)
    
    # Normalize player_name field
    for player in players:
        if "player_name" not in player and "display_name" in player:
            player["player_name"] = player["display_name"]
    
    return {
        "success": True,
        "roster_type": "full_active",
        "description": "Complete active NBA roster from Master Hub",
        "source_collection": "nba_master_hub_2026",
        "count": len(players),
        "total": total,
        "limit": limit,
        "offset": offset,
        "filters": {
            "team": team,
            "position": position
        },
        "players": players,
        "retrieved_at": datetime.now(timezone.utc).isoformat()
    }


@router.get("/mapped")
async def get_mapped_roster(
    team: Optional[str] = Query(None, description="Filter by team abbreviation"),
    limit: int = Query(500, ge=1, le=600, description="Max players to return"),
    offset: int = Query(0, ge=0, description="Pagination offset")
) -> Dict[str, Any]:
    """
    MAPPED/SUPPORTED ROSTER
    =======================
    Returns players who are fully supported by our internal provider mappings.
    
    A "mapped" player has:
    - BallDontLie ID (bdl_id) for stats
    - Odds API mapping for prop matching
    - Photo URL synced
    - Baseline stats populated
    
    This is a system-supported subset of the full roster. Players here
    can have their stats fetched, props matched, and hit rates calculated.
    
    Source: nba_master_hub_2026 with mapping completeness filters
    
    Use this endpoint when you need:
    - Players guaranteed to work with our analytics
    - Pre-flight check before displaying player data
    - System health/coverage monitoring
    """
    db = get_db()
    
    # Build query for mapped players
    # A player is "mapped" if they have:
    # 1. A bdl_id (for stats)
    # 2. Baseline stats populated (for hit rate calculations)
    query = {
        "bdl_id": {"$exists": True, "$ne": None},
        "baseline_stats": {"$exists": True, "$ne": {}},
    }
    
    if team:
        query["team"] = team.upper()
    
    cursor = db.nba_master_hub_2026.find(query, {
        "_id": 0,
        "display_name": 1,
        "team": 1,
        "position": 1,
        "bdl_id": 1,
        "nba_id": 1,
        "photo_url": 1,
        "jersey_number": 1,
        "synced_at": 1
    })
    cursor = cursor.sort("display_name", 1)
    cursor = cursor.skip(offset).limit(limit)
    
    players = await cursor.to_list(None)
    
    # Normalize and add mapping status
    for player in players:
        player["player_name"] = player.get("display_name", "Unknown")
        player["mapping_status"] = "complete" if player.get("photo_url") else "partial"
    
    total = await db.nba_master_hub_2026.count_documents(query)
    
    # Also get total full roster count for coverage stats
    total_active = await db.nba_master_hub_2026.count_documents({})
    
    return {
        "success": True,
        "roster_type": "mapped",
        "description": "Players with full system support (BDL mapping + baseline stats)",
        "source_collection": "nba_master_hub_2026",
        "count": len(players),
        "total": total,
        "total_active_roster": total_active,
        "coverage_percent": round((total / total_active * 100), 1) if total_active > 0 else 0,
        "limit": limit,
        "offset": offset,
        "filters": {
            "team": team
        },
        "players": players,
        "retrieved_at": datetime.now(timezone.utc).isoformat()
    }


@router.get("/live-today")
async def get_live_today_roster(
    team: Optional[str] = Query(None, description="Filter by team abbreviation"),
    has_props: bool = Query(True, description="Only players with active props"),
    limit: int = Query(200, ge=1, le=500, description="Max players to return"),
    offset: int = Query(0, ge=0, description="Pagination offset")
) -> Dict[str, Any]:
    """
    LIVE/TODAY ROSTER
    =================
    Returns only players with active props, bets, or games TODAY.
    
    This is the "playable" subset - players you can actually bet on right now.
    Typically ~100-200 players depending on the day's slate.
    
    Source: dg_cached_board (derived cache, rebuilt on each sync)
    
    Use this endpoint when you need:
    - Players available for betting today
    - Live odds board population
    - Today's slate overview
    
    Note: This roster changes daily based on which games are scheduled
    and which props are posted by sportsbooks.
    """
    db = get_db()
    
    # Build query for live players (those in cached_board)
    query = {}
    if team:
        query["team"] = team.upper()
    
    if has_props:
        # Only players with at least one prop
        query["props"] = {"$exists": True, "$ne": []}
    
    # Use aggregation for computed fields
    pipeline = [
        {"$match": query},
        {"$project": {
            "_id": 0,
            "player_name": 1,
            "team": 1,
            "position": 1,
            "opponent": 1,
            "game_time": 1,
            "photo_url": 1,
            "nba_com_id": 1,  # HYDRATED: nba_id from Master Hub
            "nba_id": 1,      # Also expose as nba_id
            "bdl_id": 1,      # Primary join key
            "is_home": 1,
            "synced_at": 1,
            "props_count": {"$size": {"$ifNull": ["$props", []]}},
        }},
        {"$sort": {"player_name": 1}},
        {"$skip": offset},
        {"$limit": limit}
    ]
    
    players = await db.dg_cached_board.aggregate(pipeline).to_list(None)
    
    # Get total count
    total = await db.dg_cached_board.count_documents(query)
    
    # Get sync timestamp
    sync_doc = await db.dg_cached_board.find_one({}, {"synced_at": 1}, sort=[("synced_at", -1)])
    last_sync = sync_doc.get("synced_at") if sync_doc else None
    
    # Count props breakdown
    props_pipeline = [
        {"$match": query},
        {"$unwind": "$props"},
        {"$group": {
            "_id": None,
            "total_props": {"$sum": 1},
            "demons": {"$sum": {"$cond": ["$props.is_demon", 1, 0]}},
            "goblins": {"$sum": {"$cond": ["$props.is_goblin", 1, 0]}}
        }}
    ]
    props_stats = await db.dg_cached_board.aggregate(props_pipeline).to_list(1)
    props_breakdown = props_stats[0] if props_stats else {"total_props": 0, "demons": 0, "goblins": 0}
    
    return {
        "success": True,
        "roster_type": "live_today",
        "description": "Players with active props available for betting today",
        "source_collection": "dg_cached_board",
        "count": len(players),
        "total": total,
        "limit": limit,
        "offset": offset,
        "filters": {
            "team": team,
            "has_props": has_props
        },
        "props_breakdown": {
            "total_props": props_breakdown.get("total_props", 0),
            "demon_props": props_breakdown.get("demons", 0),
            "goblin_props": props_breakdown.get("goblins", 0)
        },
        "last_sync": last_sync,
        "players": players,
        "retrieved_at": datetime.now(timezone.utc).isoformat()
    }


# =============================================================================
# ROSTER STATUS / SUMMARY ENDPOINT
# =============================================================================

@router.get("/status")
async def get_roster_status() -> Dict[str, Any]:
    """
    ROSTER STATUS SUMMARY
    =====================
    Returns counts and health metrics for all three roster types.
    
    Useful for:
    - Dashboard overview
    - System health monitoring
    - Coverage tracking
    """
    db = get_db()
    
    # Full Active count from Master Hub
    full_active_count = await db.nba_master_hub_2026.count_documents({})
    
    # Mapped count (players with bdl_id and baseline_stats)
    mapped_count = await db.nba_master_hub_2026.count_documents({
        "bdl_id": {"$exists": True, "$ne": None},
        "baseline_stats": {"$exists": True, "$ne": {}}
    })
    
    # With photos
    with_photos = await db.nba_master_hub_2026.count_documents({
        "photo_url": {"$exists": True, "$ne": None}
    })
    
    # Live Today count from Cached Board
    live_today_count = await db.dg_cached_board.count_documents({})
    live_with_props = await db.dg_cached_board.count_documents({
        "props": {"$exists": True, "$ne": []}
    })
    
    # Last sync times
    hub_sync = await db.nba_master_hub_2026.find_one({}, {"synced_at": 1}, sort=[("synced_at", -1)])
    board_sync = await db.dg_cached_board.find_one({}, {"synced_at": 1}, sort=[("synced_at", -1)])
    
    # Team breakdown
    teams = await db.nba_master_hub_2026.distinct("team")
    
    return {
        "success": True,
        "roster_summary": {
            "full_active": {
                "count": full_active_count,
                "source": "nba_master_hub_2026",
                "description": "All active NBA players",
                "endpoint": "/api/roster/full-active"
            },
            "mapped": {
                "count": mapped_count,
                "coverage_percent": round((mapped_count / full_active_count * 100), 1) if full_active_count > 0 else 0,
                "with_photos": with_photos,
                "source": "nba_master_hub_2026 (filtered)",
                "description": "Players with full system support",
                "endpoint": "/api/roster/mapped"
            },
            "live_today": {
                "count": live_today_count,
                "with_props": live_with_props,
                "source": "dg_cached_board",
                "description": "Players with props available today",
                "endpoint": "/api/roster/live-today"
            }
        },
        "teams": {
            "count": len(teams),
            "list": sorted(teams) if teams else []
        },
        "sync_status": {
            "master_hub_last_sync": hub_sync.get("synced_at") if hub_sync else None,
            "cached_board_last_sync": board_sync.get("synced_at") if board_sync else None
        },
        "retrieved_at": datetime.now(timezone.utc).isoformat()
    }


# =============================================================================
# DEPRECATED ENDPOINT ALIASES (Backward Compatibility)
# =============================================================================

@router.get("/players", deprecated=True)
async def get_roster_players_deprecated(
    team: Optional[str] = None,
    limit: int = Query(500, ge=1, le=600)
) -> Dict[str, Any]:
    """
    DEPRECATED: Use /roster/full-active instead.
    
    This endpoint is maintained for backward compatibility.
    Returns the same data as /roster/full-active.
    """
    logger.warning("[DEPRECATED] /roster/players called - use /roster/full-active instead")
    return await get_full_active_roster(team=team, limit=limit)
