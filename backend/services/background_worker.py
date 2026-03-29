"""
Background Worker Service
==========================
Handles all heavy async operations OFF the main thread.
Runs board intelligence enrichment on a schedule without blocking API requests.

This worker should be started as a separate process in production.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# Worker state
_worker_task: Optional[asyncio.Task] = None
_is_running = False
_last_enrichment: Optional[datetime] = None
_enrichment_interval_seconds = 120  # Run every 2 minutes


async def get_worker_db() -> AsyncIOMotorDatabase:
    """Get a dedicated MongoDB connection for the worker."""
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "best_bet_finder")
    client = AsyncIOMotorClient(mongo_url)
    return client[db_name]


async def run_enrichment_cycle(db: AsyncIOMotorDatabase) -> dict:
    """
    Run a single board intelligence enrichment cycle.
    
    This is the heavy operation that should NEVER run on API request threads.
    """
    global _last_enrichment
    
    try:
        from services.board_intelligence_service import run_board_intelligence_enrichment
        
        start = datetime.now(timezone.utc)
        logger.info("[WORKER] Starting board intelligence enrichment cycle...")
        
        result = await run_board_intelligence_enrichment(db)
        
        duration = (datetime.now(timezone.utc) - start).total_seconds()
        _last_enrichment = datetime.now(timezone.utc)
        
        logger.info(f"[WORKER] Enrichment complete: {result.get('enriched', 0)} players, {result.get('errors', 0)} errors, {duration:.1f}s")
        
        return {
            "success": True,
            "enriched": result.get("enriched", 0),
            "errors": result.get("errors", 0),
            "duration": duration,
            "timestamp": _last_enrichment.isoformat()
        }
    except Exception as e:
        logger.error(f"[WORKER] Enrichment cycle failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }


async def worker_loop():
    """
    Main worker loop that runs enrichment on a schedule.
    
    This runs in a separate asyncio task, not blocking the main event loop.
    """
    global _is_running
    _is_running = True
    
    logger.info(f"[WORKER] Background worker started (interval: {_enrichment_interval_seconds}s)")
    
    db = await get_worker_db()
    
    # Initial enrichment on startup
    await run_enrichment_cycle(db)
    
    while _is_running:
        try:
            # Sleep for the interval
            await asyncio.sleep(_enrichment_interval_seconds)
            
            if not _is_running:
                break
            
            # Run enrichment
            await run_enrichment_cycle(db)
            
        except asyncio.CancelledError:
            logger.info("[WORKER] Worker loop cancelled")
            break
        except Exception as e:
            logger.error(f"[WORKER] Error in worker loop: {e}")
            # Continue running, don't crash the worker
            await asyncio.sleep(30)  # Back off on error
    
    _is_running = False
    logger.info("[WORKER] Background worker stopped")


def start_background_worker():
    """
    Start the background worker as an asyncio task.
    
    Call this from your FastAPI startup event.
    """
    global _worker_task
    
    if _worker_task is not None and not _worker_task.done():
        logger.warning("[WORKER] Worker already running")
        return
    
    _worker_task = asyncio.create_task(worker_loop())
    logger.info("[WORKER] Background worker task created")


def stop_background_worker():
    """
    Stop the background worker gracefully.
    
    Call this from your FastAPI shutdown event.
    """
    global _is_running, _worker_task
    
    _is_running = False
    
    if _worker_task is not None:
        _worker_task.cancel()
        _worker_task = None
    
    logger.info("[WORKER] Background worker stop requested")


def get_worker_status() -> dict:
    """Get the current status of the background worker."""
    return {
        "is_running": _is_running,
        "last_enrichment": _last_enrichment.isoformat() if _last_enrichment else None,
        "interval_seconds": _enrichment_interval_seconds
    }


def set_enrichment_interval(seconds: int):
    """Update the enrichment interval (minimum 30 seconds)."""
    global _enrichment_interval_seconds
    _enrichment_interval_seconds = max(30, seconds)
    logger.info(f"[WORKER] Enrichment interval set to {_enrichment_interval_seconds}s")
