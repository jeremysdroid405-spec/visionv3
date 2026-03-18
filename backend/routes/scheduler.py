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


@router.post("/v3/sync-dvp")
async def sync_dvp_rankings():
    """
    Manually trigger DvP (Defense vs Position) rankings refresh.
    
    Fetches live defensive rankings from BallDontLie API and caches them.
    This data is used for the Vision Intel Suite "Defensive Friction" analysis.
    
    Automatically scheduled daily at 8:00 AM EST.
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


@router.post("/v3/sync-all-8am")
async def sync_all_8am():
    """
    Manually trigger the full 8:00 AM sync (Stats + DvP).
    
    This combines:
    1. Master Hub baseline stats sync (L5, L10, season averages)
    2. DvP rankings refresh (defensive rankings)
    
    Automatically scheduled daily at 8:00 AM EST (13:00 UTC).
    """
    import os
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.master_hub_sync import MasterHubSyncService
    from services.dvp_service import force_refresh_dvp, get_dvp_status
    
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    
    if not mongo_url:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    logger.info("[8AM SYNC] Starting combined Stats + DvP sync...")
    
    results = {
        "success": True,
        "sync_type": "8am_full",
        "triggered_at": datetime.now(timezone.utc).isoformat(),
        "components": {}
    }
    
    # Step 1: Baseline Stats
    try:
        logger.info("[8AM SYNC] Step 1/2: Baseline stats sync...")
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        
        service = MasterHubSyncService(db)
        stats_result = await service.run_full_sync()
        results["components"]["baseline_stats"] = {
            "success": True,
            "result": stats_result
        }
        logger.info(f"[8AM SYNC] Baseline stats complete: {stats_result}")
    except Exception as e:
        logger.error(f"[8AM SYNC] Baseline stats failed: {e}")
        results["components"]["baseline_stats"] = {
            "success": False,
            "error": str(e)
        }
        results["success"] = False
    
    # Step 2: DvP Rankings
    try:
        logger.info("[8AM SYNC] Step 2/2: DvP rankings refresh...")
        dvp_result = await force_refresh_dvp()
        dvp_status = get_dvp_status()
        results["components"]["dvp_rankings"] = {
            "success": dvp_result.get("success", False),
            "source": dvp_result.get("source"),
            "teams_count": dvp_result.get("teams_count", 0),
            "stat_types": dvp_result.get("stat_types", []),
            "status": dvp_status
        }
        logger.info(f"[8AM SYNC] DvP refresh complete: {dvp_result.get('source')}")
    except Exception as e:
        logger.error(f"[8AM SYNC] DvP refresh failed: {e}")
        results["components"]["dvp_rankings"] = {
            "success": False,
            "error": str(e)
        }
        results["success"] = False
    
    logger.info(f"[8AM SYNC] Complete. Success: {results['success']}")
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
