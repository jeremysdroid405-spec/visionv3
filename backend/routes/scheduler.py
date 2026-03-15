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
