"""
Scheduler Routes
================
Endpoints for scheduler management and breaking news.
"""
from fastapi import APIRouter, HTTPException
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Scheduler"])

# References set via dependency injection
_demon_goblin_engine = None
_live_scores_engine = None
_scheduler = None


def set_scheduler_deps(engine, live_scores_engine, scheduler):
    """Set scheduler route dependencies."""
    global _demon_goblin_engine, _live_scores_engine, _scheduler
    _demon_goblin_engine = engine
    _live_scores_engine = live_scores_engine
    _scheduler = scheduler


def get_engine():
    """Get the demon goblin engine instance."""
    if _demon_goblin_engine is None:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    return _demon_goblin_engine


@router.get("/v3/scheduler-status")
async def get_scheduler_status():
    """
    Get the current status of all scheduled jobs.
    
    Returns:
    - running: Whether scheduler is active
    - jobs: List of all scheduled jobs with next run times
    - timezone: Scheduler timezone
    """
    if not _scheduler:
        return {
            "running": False,
            "message": "Scheduler not initialized",
            "jobs": []
        }
    
    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger)
        })
    
    return {
        "running": _scheduler.running,
        "jobs": jobs,
        "timezone": str(_scheduler.timezone) if hasattr(_scheduler, 'timezone') else "UTC",
        "checked_at": datetime.now(timezone.utc).isoformat()
    }


@router.post("/v3/trigger-scheduled-sync")
async def trigger_scheduled_sync(sync_type: str = "full"):
    """
    Manually trigger a scheduled sync.
    
    Args:
    - sync_type: "full" (complete sync) or "delta" (odds only)
    
    This mimics what the scheduled jobs do, but can be triggered manually.
    """
    engine = get_engine()
    
    if sync_type == "full":
        logger.info("[MANUAL SYNC] Triggering full sync...")
        result = await engine.run_full_sync()
    else:
        logger.info("[MANUAL SYNC] Triggering delta sync...")
        result = await engine.run_delta_sync()
    
    return {
        "success": True,
        "sync_type": sync_type,
        "result": result,
        "triggered_at": datetime.now(timezone.utc).isoformat()
    }


@router.post("/v3/sync-baseline-stats")
async def sync_baseline_stats():
    """
    Manually trigger Master Hub baseline stats sync.
    
    Updates nba_master_hub_2026 with L5, L10, and season averages
    for all prop categories (PTS, REB, AST, 3PM, PRA, etc.)
    
    This ensures the frontend can instantly access pre-computed
    player stats without any external API calls.
    """
    import os
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.master_hub_sync import MasterHubSyncService
    
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    
    if not mongo_url:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    logger.info("[MANUAL SYNC] Triggering Master Hub baseline stats sync...")
    
    try:
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        
        service = MasterHubSyncService(db)
        result = await service.run_full_sync()
        
        return {
            "success": True,
            "sync_type": "baseline_stats",
            "result": result,
            "triggered_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"[MANUAL SYNC] Baseline stats sync failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v3/sync-bdl")
async def sync_bdl_comprehensive():
    """
    Manually trigger BDL (BallDontLie) comprehensive sync.
    
    This syncs:
    - Player profiles (height, weight, position, draft info)
    - Season averages (pts, reb, ast, etc.) - OFFICIAL from BDL API
    - L5/L10/L15/L20 averages - OFFICIAL from NBA.com API (nba_api)
    
    This is the PRIMARY data source for accurate player stats.
    Automatically runs daily at 4:00 AM EST.
    """
    import os
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.bdl_comprehensive_sync import get_bdl_sync_service
    
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    
    if not mongo_url:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    logger.info("[MANUAL SYNC] Triggering BDL + NBA.com comprehensive sync...")
    
    try:
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        
        # Sync from BDL API + NBA.com for L5/L10
        bdl_service = get_bdl_sync_service(db)
        sync_result = await bdl_service.sync_all_active_players()
        
        return {
            "success": True,
            "sync_type": "bdl_plus_nba",
            "sync_result": {
                "players_synced": sync_result.get("success", 0),
                "players_failed": sync_result.get("failed", 0),
                "nba_enriched": sync_result.get("nba_enriched", 0),
                "total_players": sync_result.get("total", 0),
                "duration_seconds": sync_result.get("duration_seconds", 0)
            },
            "note": "Season averages from BDL API. L5/L10 from NBA.com official stats.",
            "triggered_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"[MANUAL SYNC] BDL + NBA.com sync failed: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v3/sync-bdl-mapping")
async def sync_bdl_player_mapping():
    """
    Sync all active NBA players from BDL to build complete name-to-ID mapping.
    
    This creates/updates the bdl_player_mapping collection which enables
    efficient ID-based lookups instead of name-based searches.
    
    Should be run:
    - Once on initial setup
    - Weekly to catch roster changes
    """
    import os
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.bdl_player_mapping import get_bdl_mapping_service
    
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    
    if not mongo_url:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    logger.info("[MANUAL SYNC] Syncing BDL player ID mappings...")
    
    try:
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        
        mapping_service = get_bdl_mapping_service(db)
        result = await mapping_service.sync_all_active_players()
        
        return {
            "success": True,
            "sync_type": "bdl_player_mapping",
            "total_players": result.get("total_players", 0),
            "mappings_stored": result.get("mappings_stored", 0),
            "cache_size": result.get("cache_size", 0),
            "triggered_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"[MANUAL SYNC] BDL mapping sync failed: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v3/sync-nba-l5l10")
async def sync_nba_l5l10(limit: int = 200):
    """
    Manually trigger NBA.com L5/L10 batch enrichment.
    
    Uses playerdashboardbylastngames endpoint to fetch official
    pre-calculated L5/L10/L15/L20 stats from NBA.com.
    
    This runs automatically at 4:05 AM EST, but can be triggered manually
    to ensure the board has fresh hit rate data.
    
    Args:
        limit: Max number of players to enrich (default 200)
    
    Returns:
        Summary of enrichment results
    """
    import os
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.bdl_comprehensive_sync import get_bdl_sync_service
    
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    
    if not mongo_url:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    logger.info(f"[MANUAL SYNC] Triggering NBA.com L5/L10 batch enrichment (limit={limit})...")
    
    try:
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        
        bdl_service = get_bdl_sync_service(db)
        
        # Find players needing enrichment
        players = await db.nba_master_hub_2026.find({
            "nba_id": {"$exists": True, "$ne": None},
            "$or": [
                {"baseline_stats.PTS.l5_avg": {"$exists": False}},
                {"baseline_stats.PTS.l5_avg": None}
            ]
        }, {"bdl_id": 1, "display_name": 1}).limit(limit).to_list(limit)
        
        logger.info(f"[MANUAL SYNC] Found {len(players)} players needing L5/L10 enrichment")
        
        success = 0
        failed = 0
        
        for player in players:
            try:
                result = await bdl_service.enrich_baseline_with_nba_stats(player["bdl_id"])
                if result:
                    success += 1
                else:
                    failed += 1
            except Exception as e:
                logger.debug(f"[MANUAL SYNC] Failed to enrich {player.get('display_name')}: {e}")
                failed += 1
        
        remaining = await db.nba_master_hub_2026.count_documents({
            "nba_id": {"$exists": True, "$ne": None},
            "$or": [
                {"baseline_stats.PTS.l5_avg": {"$exists": False}},
                {"baseline_stats.PTS.l5_avg": None}
            ]
        })
        
        return {
            "success": True,
            "sync_type": "nba_l5l10_batch",
            "players_processed": len(players),
            "enriched": success,
            "failed": failed,
            "remaining": remaining,
            "note": "L5/L10/L15/L20 stats from NBA.com playerdashboardbylastngames",
            "triggered_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"[MANUAL SYNC] NBA.com L5/L10 batch enrichment failed: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v3/sync-injuries")
async def sync_injuries():
    """
    Manually trigger injury reports sync from BDL.
    
    This fetches current NBA injury reports and:
    - Stores in bdl_injuries collection
    - Updates context badges (deep_water) for injured players
    
    Automatically included in the 4:00 AM EST daily sync.
    """
    import os
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.bdl_enhanced_data import get_bdl_enhanced_service
    
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    
    if not mongo_url:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    logger.info("[MANUAL SYNC] Triggering injury reports sync...")
    
    try:
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        
        service = get_bdl_enhanced_service(db)
        result = await service.sync_injuries()
        
        return {
            "success": True,
            "sync_type": "injuries",
            "injuries_count": result.get("injuries_count", 0),
            "context_badges_updated": result.get("players_updated", 0),
            "triggered_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"[MANUAL SYNC] Injuries sync failed: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v3/sync-advanced-stats")
async def sync_advanced_stats():
    """
    Manually trigger advanced stats sync from BDL.
    
    Fetches for all players in master hub:
    - PIE (Player Impact Estimate)
    - Net Rating
    - Offensive/Defensive Ratings
    
    These metrics enhance player analysis in the Vision Intel Suite.
    """
    import os
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.bdl_enhanced_data import get_bdl_enhanced_service
    
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    
    if not mongo_url:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    logger.info("[MANUAL SYNC] Triggering advanced stats sync...")
    
    try:
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        
        service = get_bdl_enhanced_service(db)
        result = await service.sync_advanced_stats()
        
        return {
            "success": True,
            "sync_type": "advanced_stats",
            "players_synced": result.get("players_synced", 0),
            "players_failed": result.get("players_failed", 0),
            "triggered_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"[MANUAL SYNC] Advanced stats sync failed: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/v3/injuries")
async def get_injuries():
    """
    Get current NBA injury reports.
    
    Returns list of injured players with status and severity.
    """
    import os
    from motor.motor_asyncio import AsyncIOMotorClient
    
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME", "best_bet_finder")
    
    if not mongo_url:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        
        # Query directly from the collection
        injuries = await db.bdl_injuries.find(
            {},
            {"_id": 0}
        ).to_list(100)
        
        return {
            "success": True,
            "count": len(injuries),
            "injuries": injuries
        }
    except Exception as e:
        logger.error(f"[INJURIES] Failed to get injuries: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v3/sync-dvp")
async def sync_dvp_rankings():
    """
    Manually trigger DvP (Defense vs Position) rankings refresh.
    
    Fetches live defensive rankings from BallDontLie API and caches them.
    This data is used for the Vision Intel Suite "Defensive Friction" analysis.
    
    Automatically included in the 4:00 AM EST daily sync.
    """
    from services.dvp_service import force_refresh_dvp, get_dvp_status
    
    logger.info("[MANUAL SYNC] Triggering DvP rankings refresh...")
    
    try:
        result = await force_refresh_dvp()
        status = get_dvp_status()
        
        return {
            "success": result.get("success", False),
            "sync_type": "dvp_rankings",
            "source": result.get("source"),
            "teams_count": result.get("teams_count", 0),
            "stat_types": result.get("stat_types", []),
            "status": status,
            "triggered_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"[MANUAL SYNC] DvP refresh failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v3/sync-full-daily")
async def sync_full_daily():
    """
    Manually trigger the FULL daily sync (same as 4:00 AM scheduled job).
    
    This is a comprehensive sync that includes:
    1. ALL active NBA players from BDL (season averages)
    2. NBA.com L5/L10/L15/L20 stats (official pre-calculated)
    3. DvP rankings refresh (Defense vs Position)
    
    For the complete scheduled sync (including injuries, odds, insights, Vision AI),
    use the scheduled job or wait for the 4:00 AM automatic run.
    """
    import os
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.bdl_comprehensive_sync import get_bdl_sync_service
    from services.dvp_service import force_refresh_dvp, get_dvp_status
    
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    
    if not mongo_url:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    logger.info("[DAILY SYNC] Starting combined BDL + NBA.com + DvP sync...")
    
    results = {
        "success": True,
        "sync_type": "daily_full",
        "triggered_at": datetime.now(timezone.utc).isoformat(),
        "components": {}
    }
    
    # Step 1: BDL + NBA.com Stats
    try:
        logger.info("[DAILY SYNC] Step 1/2: BDL + NBA.com stats sync...")
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        
        bdl_service = get_bdl_sync_service(db)
        sync_result = await bdl_service.sync_all_active_players()
        
        results["components"]["player_stats"] = {
            "success": True,
            "players_synced": sync_result.get("success", 0),
            "nba_enriched": sync_result.get("nba_enriched", 0),
            "duration_seconds": sync_result.get("duration_seconds", 0)
        }
        logger.info(f"[DAILY SYNC] Stats complete: {sync_result.get('success', 0)} players, {sync_result.get('nba_enriched', 0)} NBA.com enriched")
    except Exception as e:
        logger.error(f"[DAILY SYNC] Stats sync failed: {e}")
        results["components"]["player_stats"] = {
            "success": False,
            "error": str(e)
        }
        results["success"] = False
    
    # Step 2: DvP Rankings
    try:
        logger.info("[DAILY SYNC] Step 2/2: DvP rankings refresh...")
        dvp_result = await force_refresh_dvp()
        dvp_status = get_dvp_status()
        results["components"]["dvp_rankings"] = {
            "success": dvp_result.get("success", False),
            "source": dvp_result.get("source"),
            "teams_count": dvp_result.get("teams_count", 0),
            "stat_types": dvp_result.get("stat_types", []),
            "status": dvp_status
        }
        logger.info(f"[DAILY SYNC] DvP refresh complete: {dvp_result.get('source')}")
    except Exception as e:
        logger.error(f"[DAILY SYNC] DvP refresh failed: {e}")
        results["components"]["dvp_rankings"] = {
            "success": False,
            "error": str(e)
        }
        results["success"] = False
    
    logger.info(f"[DAILY SYNC] Complete. Success: {results['success']}")
    return results


@router.get("/v3/breaking-news")
async def get_breaking_news(limit: int = 10):
    """
    Get breaking news alerts from Live Scores Engine.
    
    Returns recent injury reports, lineup changes, and game updates
    that may affect player props.
    
    Args:
    - limit: Maximum number of alerts to return (default: 10)
    """
    if not _live_scores_engine:
        return {
            "success": True,
            "alerts": [],
            "count": 0,
            "message": "Live Scores Engine not available"
        }
    
    try:
        alerts = await _live_scores_engine.get_breaking_alerts(limit=limit)
        return {
            "success": True,
            "alerts": alerts,
            "count": len(alerts),
            "retrieved_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Breaking news error: {e}")
        return {
            "success": False,
            "alerts": [],
            "count": 0,
            "error": str(e)
        }
