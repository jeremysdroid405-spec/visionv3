"""
Master Hub CRON Scheduler
=========================
SINGLE SOURCE OF TRUTH (SSOT) Architecture - PIPE 1: Stats Vault

Schedules daily sync of NBA Master Hub at 0400 EST using Tank01 API.
This is the ONLY authorized script to call external stat APIs.

Data Flow:
  Tank01 API → 0400 CRON → nba_master_hub_2026 → All App Components

CRITICAL RULES:
1. This CRON is the ONLY code allowed to call Tank01/external stat APIs
2. Sync ONLY overwrites: baseline_stats, game_logs, stats metadata
3. Sync NEVER touches: player_id, player_name, display_name, photo_url, headshot_url
4. All other components read from nba_master_hub_2026 ONLY

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
    Daily sync job - runs at 0400 EST (0900 UTC).
    
    SSOT PIPE 1: The ONLY authorized Tank01 caller.
    
    Updates ONLY statistical fields:
    - baseline_stats: {PTS: {l5_avg, l10_avg, season_avg}, ...}
    - game_logs: [{gameID, pts, reb, ast, ...}, ...]
    - stats metadata: stats_source, baseline_stats_updated_at
    
    NEVER modifies structural fields:
    - player_id, player_name, display_name
    - photo_url, headshot_url
    - team, position
    """
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.tank01_stats_service import run_tank01_sync
    
    logger.info("[CRON] ========================================")
    logger.info("[CRON] SSOT PIPE 1: Starting 0400 EST Tank01 sync")
    logger.info("[CRON] ========================================")
    
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME", "test_database")
    
    if not mongo_url:
        logger.error("[CRON] MONGO_URL not configured")
        return
    
    try:
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        
        # Run Tank01 stats sync (ONLY updates stats fields)
        result = await run_tank01_sync(db)
        
        logger.info(f"[CRON] SSOT sync completed: {result.get('updated', 0)} players updated")
        logger.info("[CRON] ========================================")
        
    except Exception as e:
        logger.error(f"[CRON] SSOT sync failed: {e}")


def start_scheduler():
    """
    Start the CRON scheduler for daily Master Hub sync.
    
    Schedule: 0400 EST (0900 UTC) daily
    
    This scheduler manages PIPE 1 (Stats Vault) of the SSOT architecture.
    """
    global _scheduler
    
    if _scheduler is not None:
        logger.warning("[CRON] Scheduler already running")
        return _scheduler
    
    _scheduler = AsyncIOScheduler()
    
    # Schedule daily sync at 0400 EST (0900 UTC)
    # EST is UTC-5, so 4 AM EST = 9 AM UTC
    _scheduler.add_job(
        run_daily_sync,
        CronTrigger(hour=9, minute=0, timezone='UTC'),  # 0400 EST = 0900 UTC
        id='master_hub_daily_sync',
        name='SSOT PIPE 1: NBA Master Hub Daily Sync',
        replace_existing=True
    )
    
    _scheduler.start()
    logger.info("[CRON] SSOT Scheduler started - Tank01 sync at 0400 EST daily")
    
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
