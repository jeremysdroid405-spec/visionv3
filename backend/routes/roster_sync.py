"""
Roster Sync Routes
==================
Endpoints for roster management, player photos, and stats synchronization.
"""
from fastapi import APIRouter, HTTPException
import logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Roster Sync"])

# Reference to demon_goblin_engine (set via dependency injection)
_demon_goblin_engine = None


def set_demon_goblin_engine(engine):
    """Set the demon goblin engine reference."""
    global _demon_goblin_engine
    _demon_goblin_engine = engine


def get_engine():
    """Get the demon goblin engine instance."""
    if _demon_goblin_engine is None:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    return _demon_goblin_engine


@router.post("/v3/sync-master-roster")
async def sync_master_roster():
    """
    WEEKLY ROSTER SYNC - Source of Truth for player-to-team mapping.
    
    Fetches ALL NBA players from BallDontLie API and stores them in 
    the player_master_roster collection. Should run weekly (Sunday midnight)
    but can be triggered manually.
    
    This ensures accurate team assignments by overriding Odds API data.
    """
    engine = get_engine()
    
    logger.info("[MASTER ROSTER] Manual sync triggered via API")
    result = await engine.sync_master_roster()
    
    return result


@router.post("/v3/sync-player-photos")
async def sync_player_photos():
    """
    PHOTO PIPELINE - Sync headshots for all active players.
    
    Sources:
    1. NBA CDN (official high-res headshots)
    2. Team logo fallback for missing headshots
    
    Updates cached_board and master_roster with photo URLs.
    """
    engine = get_engine()
    
    logger.info("[PHOTO SYNC] Manual sync triggered via API")
    result = await engine.sync_player_photos()
    
    return result


@router.post("/v3/sync-active-players")
async def sync_active_players():
    """
    ACTIVE PLAYER SYNC - Fetches ONLY current NBA players from BDL with headshots.
    
    This is the recommended way to populate the player database:
    - Gets ~530 active NBA players (not 5000+ historical)
    - Includes ESPN headshot URLs directly from BDL
    - Stores player metadata: team, position, jersey, height, weight, college
    
    Run this once to populate the database, then use sync-player-photos for updates.
    """
    engine = get_engine()
    
    logger.info("[ACTIVE PLAYER SYNC] Manual sync triggered via API")
    result = await engine.sync_active_players_with_photos()
    
    return result


@router.post("/v3/refresh-board-photos")
async def refresh_board_photos():
    """
    Refresh photo URLs in cached_board from master_roster with fuzzy matching.
    
    Use this after sync-active-players to fix any name mismatches between
    Odds API player names and BDL roster names.
    """
    engine = get_engine()
    
    logger.info("[PHOTO REFRESH] Manual refresh triggered via API")
    result = await engine.refresh_cached_board_photos()
    
    return result


@router.post("/v3/refresh-all-photos")
async def refresh_all_photos():
    """
    MASTER PHOTO REFRESH - Updates photo URLs across ALL collections.
    
    Refreshes photos in:
    - cached_board (main player board)
    - goblin_recon (parlay picks)
    - demon_radar (demon picks)
    - goblin_vault (goblin picks)
    
    Use this after sync-active-players to ensure all player photos are updated.
    """
    engine = get_engine()
    
    logger.info("[MASTER PHOTO REFRESH] Manual refresh triggered via API")
    result = await engine.refresh_all_photos()
    
    return result


@router.get("/v3/roster/players", deprecated=True)
async def get_all_players_roster():
    """
    DEPRECATED: Use /api/roster/full-active instead.
    
    This endpoint returns from dg_master_roster.
    For the canonical full NBA roster, use /api/roster/full-active which
    queries from nba_master_hub_2026 (the Single Source of Truth).
    """
    logger.warning("[DEPRECATED] /v3/roster/players called - use /roster/full-active instead")
    engine = get_engine()
    
    players = await engine.master_roster.find(
        {"is_active": True},
        {"_id": 0}
    ).sort("player_name", 1).to_list(None)
    
    return {
        "success": True,
        "count": len(players),
        "players": players
    }


@router.get("/v3/player/{player_name}/photo")
async def get_player_photo(player_name: str):
    """
    Get a specific player's headshot URL.
    """
    engine = get_engine()
    
    # Try exact match first
    player = await engine.master_roster.find_one(
        {"player_name": player_name},
        {"_id": 0, "player_name": 1, "team_abbreviation": 1, "photo_url": 1, "photo_source": 1}
    )
    
    # If not found, try normalized name match
    if not player:
        normalized = engine.sanitize_player_name(player_name)
        player = await engine.master_roster.find_one(
            {"normalized_name": normalized},
            {"_id": 0, "player_name": 1, "team_abbreviation": 1, "photo_url": 1, "photo_source": 1}
        )
    
    if not player:
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found")
    
    return player


@router.get("/v3/team/{team_abbrev}/roster")
async def get_team_roster(team_abbrev: str):
    """
    Get all players on a specific team with their headshots.
    """
    engine = get_engine()
    
    players = await engine.master_roster.find(
        {"team_abbreviation": team_abbrev.upper()},
        {"_id": 0}
    ).sort("player_name", 1).to_list(None)
    
    if not players:
        raise HTTPException(status_code=404, detail=f"Team '{team_abbrev}' not found")
    
    return {
        "team": team_abbrev.upper(),
        "count": len(players),
        "players": players
    }


@router.post("/v3/sync-player-stats")
async def sync_player_stats():
    """
    STATS CACHE - Sync player game logs to MongoDB.
    
    Fetches stats from:
    1. BallDontLie API (primary)
    2. NBA.com API (fallback for missing players)
    
    Stores in dg_player_stats collection for fast hit rate calculations.
    Should be run daily before sync-to-mongo.
    """
    engine = get_engine()
    
    logger.info("[STATS SYNC] Manual sync triggered via API")
    result = await engine.sync_player_stats()
    
    return result


@router.post("/v3/sync-daily-insights")
async def sync_daily_insights():
    """
    ADVANCED ANALYTICS - Calculate and cache daily insights for all players.
    
    Calculates:
    - Schedule Density Factor (B2B, 3-in-4 fatigue)
    - Pace Adjustment Factor (matchup tempo)
    - Usage Ripple Effect (teammate injuries)
    - Volatility Score (consistency rating)
    - Template-based Insight Summaries
    
    Should be run daily at 8:00 AM EST.
    """
    engine = get_engine()
    
    logger.info("[INSIGHTS] Manual sync triggered via API")
    result = await engine.sync_daily_insights()
    
    return result


@router.get("/v3/player-insights/{player_name}")
async def get_player_insights(player_name: str):
    """
    Get advanced analytics insights for a specific player.
    
    Returns:
    - schedule_density_factor
    - pace_adjustment_factor
    - usage_bump_percent
    - volatility_score
    - insight_summary
    - ai_confidence_rating
    """
    engine = get_engine()
    
    insights = await engine.get_player_insights(player_name)
    
    if not insights:
        raise HTTPException(status_code=404, detail=f"No insights found for {player_name}")
    
    return insights


@router.get("/v3/master-roster-status")
async def get_master_roster_status():
    """
    Get the status of the master roster collection.
    
    Returns:
    - total_players: Total players in the database
    - active_players: Players marked as active
    - players_with_photos: Players with photo URLs
    - last_sync: When the roster was last synced
    """
    engine = get_engine()
    
    total = await engine.master_roster.count_documents({})
    active = await engine.master_roster.count_documents({"is_active": True})
    with_photos = await engine.master_roster.count_documents({"photo_url": {"$ne": None}})
    
    # Get last sync time
    last_sync_doc = await engine.master_roster.find_one(
        {},
        {"_id": 0, "synced_at": 1},
        sort=[("synced_at", -1)]
    )
    last_sync = last_sync_doc.get("synced_at") if last_sync_doc else None
    
    return {
        "total_players": total,
        "active_players": active,
        "players_with_photos": with_photos,
        "photo_coverage": f"{(with_photos / active * 100):.1f}%" if active > 0 else "0%",
        "last_sync": last_sync
    }
