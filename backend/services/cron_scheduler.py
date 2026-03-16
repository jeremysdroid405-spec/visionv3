"""
Master Hub CRON Scheduler
=========================
Schedules daily sync of NBA Master Hub at 0300 EST using Tank01 API.

This ensures the database is always up-to-date with:
- Player baseline stats (L5, L10, season averages) calculated from real game logs
- All prop categories pre-computed
- Ready for instant client access

Data Source: Tank01 Fantasy Stats API (RapidAPI)
"""

import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler: Optional[AsyncIOScheduler] = None


async def run_daily_sync():
    """
    Daily sync job - runs at 0300 EST.
    
    Uses Tank01 Fantasy Stats API to fetch real game logs and calculate:
    - L5_avg: Last 5 games average
    - L10_avg: Last 10 games average  
    - season_avg: Full season average
    
    Only counts games where minutes > 0 (actually played).
    """
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.tank01_stats_service import run_tank01_sync
    
    logger.info("[CRON] Starting daily Master Hub sync (Tank01)...")
    
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME", "test_database")
    
    if not mongo_url:
        logger.error("[CRON] MONGO_URL not configured")
        return
    
    try:
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        
        # Run Tank01 stats sync
        result = await run_tank01_sync(db)
        
        logger.info(f"[CRON] Daily sync completed: {result}")
        
    except Exception as e:
        logger.error(f"[CRON] Daily sync failed: {e}")


def start_scheduler():
    """
    Start the CRON scheduler for daily Master Hub sync.
    
    Schedule: 0300 EST (0800 UTC) daily
    """
    global _scheduler
    
    if _scheduler is not None:
        logger.warning("[CRON] Scheduler already running")
        return _scheduler
    
    _scheduler = AsyncIOScheduler()
    
    # Schedule daily sync at 0300 EST (0800 UTC)
    # EST is UTC-5, so 3 AM EST = 8 AM UTC
    _scheduler.add_job(
        run_daily_sync,
        CronTrigger(hour=8, minute=0, timezone='UTC'),  # 0300 EST = 0800 UTC
        id='master_hub_daily_sync',
        name='NBA Master Hub Daily Sync',
        replace_existing=True
    )
    
    _scheduler.start()
    logger.info("[CRON] Scheduler started - Master Hub sync scheduled for 0300 EST daily")
    
    return _scheduler


def stop_scheduler():
    """Stop the CRON scheduler."""
    global _scheduler
    
    if _scheduler:
        _scheduler.shutdown()
        _scheduler = None
        logger.info("[CRON] Scheduler stopped")


def get_scheduler_status():
    """Get current scheduler status."""
    global _scheduler
    
    if _scheduler is None:
        return {"running": False, "jobs": []}
    
    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None
        })
    
    return {
        "running": _scheduler.running,
        "jobs": jobs
    }
