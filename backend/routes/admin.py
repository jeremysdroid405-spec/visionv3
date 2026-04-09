"""
Admin Routes
=============
Administrative and cache management endpoints.
"""
from fastapi import APIRouter, HTTPException
import logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Admin"])

# References set via dependency injection
_stats_manager = None
_db = None


def set_admin_deps(stats_manager, db):
    """Set admin route dependencies."""
    global _stats_manager, _db
    _stats_manager = stats_manager
    _db = db


def get_stats_manager():
    """Get the stats manager instance."""
    if _stats_manager is None:
        raise HTTPException(status_code=500, detail="Stats manager not initialized")
    return _stats_manager


@router.get("/cache-status")
async def get_cache_status():
    """Get cache statistics"""
    stats = get_stats_manager()
    status = await stats.get_cache_status()
    return {"success": True, "data": status}


@router.post("/clear-expired-cache")
async def clear_expired_cache():
    """Clear expired cache entries"""
    stats = get_stats_manager()
    deleted_count = await stats.clear_expired_cache()
    return {"success": True, "deleted_count": deleted_count}


@router.post("/sync-rosters")
async def sync_rosters(force: bool = False):
    """
    Sync NBA rosters for all 30 teams
    This creates a global player database for fast lookups
    """
    stats = get_stats_manager()
    result = await stats.sync_nba_rosters(force=force)
    return {"success": True, "sync_result": result}


@router.post("/clear-all-cache")
async def clear_all_cache():
    """Clear ALL cache (use when changing seasons)"""
    stats = get_stats_manager()
    deleted_count = await stats.clear_all_cache()
    return {"success": True, "deleted_count": deleted_count, "reason": "Season change - cleared all 2024 data"}


@router.get("/todays-games")
async def get_todays_games():
    """Get today's NBA games from BallDontLie"""
    stats = get_stats_manager()
    result = await stats.get_todays_games_summary()
    return result


@router.post("/trigger-daily-sync")
async def trigger_daily_sync():
    """Manually trigger the autonomous daily sync"""
    stats = get_stats_manager()
    result = await stats.autonomous_daily_sync()
    return {"success": True, "sync_result": result}


@router.post("/sync-lakers-test")
async def sync_lakers_test():
    """
    Test Lakers roster sync for season 2025 using BallDontLie
    """
    stats = get_stats_manager()
    logger.info("Testing Lakers roster sync for season 2025 (BallDontLie)...")
    
    # Lakers team ID in BallDontLie is 14
    player_ids = await stats.sync_players_for_team(14)
    
    return {
        "success": True,
        "message": "Lakers roster synced successfully via BallDontLie",
        "players_synced": len(player_ids),
        "data_source": "BallDontLie API"
    }


@router.get("/rate-limit-status")
async def get_rate_limit_status():
    """
    Get current API rate limit status.
    
    Returns:
    - active_buckets: Number of active rate limit buckets
    - tiers: Configuration for each rate limit tier
    - enabled: Whether rate limiting is active
    """
    from middleware import get_rate_limit_storage, RATE_LIMIT_TIERS
    import os
    
    storage = get_rate_limit_storage()
    stats = storage.get_stats()
    
    tiers = {
        tier: {
            "requests_per_minute": config.requests_per_minute,
            "burst_size": config.burst_size
        }
        for tier, config in RATE_LIMIT_TIERS.items()
    }
    
    return {
        "success": True,
        "rate_limit": {
            "enabled": os.environ.get("RATE_LIMITING_ENABLED", "true").lower() == "true",
            "active_buckets": stats["active_buckets"],
            "last_cleanup": stats["last_cleanup"],
            "tiers": tiers
        }
    }


@router.get("/roster-status")
async def get_roster_status():
    """Get roster sync status and statistics"""
    stats = get_stats_manager()
    from config.settings import CURRENT_SEASON
    
    try:
        total_players = await stats.league_roster.count_documents({})
        
        # Get teams count
        teams = await stats.league_roster.distinct("team_name")
        
        # Get last sync time
        latest = await stats.league_roster.find_one(
            {},
            sort=[("synced_at", -1)]
        )
        
        last_synced = latest.get("synced_at") if latest else None
        
        return {
            "success": True,
            "total_players": total_players,
            "total_teams": len(teams),
            "teams": sorted(teams),
            "last_synced": last_synced,
            "season": CURRENT_SEASON
        }
    except Exception as e:
        logger.error(f"Roster status error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== DVP MANAGEMENT ====================

@router.get("/dvp-status")
async def get_dvp_status():
    """
    Get DvP (Defense vs Position) service status.
    
    Returns current data source, cache age, and configuration.
    """
    from services.dvp_service import get_dvp_status as dvp_status
    
    status = dvp_status()
    return {
        "success": True,
        "dvp": status
    }


@router.post("/dvp-refresh")
async def trigger_dvp_refresh():
    """
    Manually trigger a DvP data refresh.
    
    This forces a fresh fetch from the BallDontLie API and updates
    both the in-memory cache and MongoDB storage.
    """
    from services.dvp_service import force_refresh_dvp
    
    try:
        result = await force_refresh_dvp()
        return {
            "success": result["success"],
            "message": "DvP refresh completed",
            "result": result
        }
    except Exception as e:
        logger.error(f"DvP refresh error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dvp-rankings")
async def get_dvp_rankings():
    """
    Get current DvP rankings data.
    
    Returns the full defensive rankings for all teams and stat categories.
    """
    from services.dvp_service import get_dvp_rankings_with_source
    
    try:
        rankings, headers = await get_dvp_rankings_with_source()
        
        return {
            "success": True,
            "headers": headers,
            "rankings": rankings,
            "stat_types": list(rankings.keys()) if rankings else [],
            "teams_count": len(next(iter(rankings.values()))) if rankings else 0
        }
    except Exception as e:
        logger.error(f"DvP rankings error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dvp-analysis/{opponent_team}/{stat_type}")
async def get_dvp_analysis(opponent_team: str, stat_type: str, player_position: str = None):
    """
    Get DvP analysis for a specific matchup.
    
    Args:
        opponent_team: 3-letter team abbreviation (e.g., "LAL", "BOS")
        stat_type: Stat type (e.g., "PTS", "REB", "player_points")
        player_position: Optional player position for matchup multiplier (e.g., "C", "PG")
    
    Returns:
        Complete DvP analysis including modifier, label, rank, and matchup multiplier.
    """
    from services.dvp_service import get_full_dvp_analysis
    
    try:
        analysis = get_full_dvp_analysis(opponent_team.upper(), stat_type, player_position)
        return {
            "success": True,
            "analysis": analysis
        }
    except Exception as e:
        logger.error(f"DvP analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))



# File download endpoints
from fastapi.responses import FileResponse

@router.get("/download/api-traffic-csv")
async def download_api_traffic_csv():
    """Download API traffic report as CSV"""
    return FileResponse(
        path="/app/frontend/public/api_traffic_report.csv",
        filename="propvision_api_traffic.csv",
        media_type="text/csv"
    )

@router.get("/download/unused-endpoints-csv")
async def download_unused_endpoints_csv():
    """Download unused endpoints as CSV"""
    return FileResponse(
        path="/app/frontend/public/unused_endpoints.csv",
        filename="propvision_unused_endpoints.csv",
        media_type="text/csv"
    )

@router.get("/download/backend-code-json")
async def download_backend_code_json():
    """Download backend code export as JSON"""
    return FileResponse(
        path="/app/frontend/public/backend_code_export.json",
        filename="propvision_backend_code.json",
        media_type="application/json"
    )
