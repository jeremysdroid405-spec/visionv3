"""
Live-sync reconciler — bridges the Mongo SSOT to APScheduler.

Runs as a small asyncio task in the backend process. Polls
`system_state.live_sync` every `LIVE_SYNC_POLL_S` seconds and:
    - calls `scheduler.pause()` when `paused=true` and scheduler is RUNNING
    - calls `scheduler.resume()` when `paused=false` and scheduler is PAUSED

Cheap (one Mongo find per tick). Idempotent — the only side effect is
the actual scheduler state transition.

The reconciler does NOT manage the background `asyncio.create_task`
loops gated by `TESTING_MODE` at startup. Those require a backend
restart to flip. APScheduler is the only thing toggled at runtime.
"""
from __future__ import annotations
import asyncio
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

LIVE_SYNC_POLL_S = float(os.environ.get("LIVE_SYNC_RECONCILE_S", "3.0"))


async def reconciler_loop(get_db, get_scheduler) -> None:
    """`get_db` returns a Motor DB; `get_scheduler` returns the
    AsyncIOScheduler or None. Both are passed lazily to avoid import-
    order issues at server startup."""
    try:
        from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING
    except Exception:  # noqa: BLE001
        logger.warning("[live-sync] APScheduler unavailable; reconciler exiting")
        return
    from workers.live_sync_state import get_live_sync

    logger.info("[live-sync] reconciler started (interval=%.1fs)",
                  LIVE_SYNC_POLL_S)
    last_applied: Optional[bool] = None
    while True:
        try:
            db = get_db()
            doc = await get_live_sync(db)
            want_paused = bool(doc.get("paused"))
            sched = get_scheduler()
            if sched is None:
                await asyncio.sleep(LIVE_SYNC_POLL_S)
                continue
            cur_state = sched.state
            if want_paused and cur_state == STATE_RUNNING:
                sched.pause()
                logger.info("[live-sync] scheduler paused (reason=%r set_by=%r)",
                              doc.get("reason"), doc.get("set_by"))
                last_applied = True
            elif (not want_paused) and cur_state == STATE_PAUSED:
                sched.resume()
                logger.info("[live-sync] scheduler resumed (reason=%r set_by=%r)",
                              doc.get("reason"), doc.get("set_by"))
                last_applied = False
            elif last_applied != want_paused:
                # First poll, or scheduler is in a non-applicable state
                # (e.g. STOPPED). Just remember what we want.
                last_applied = want_paused
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("[live-sync] reconciler tick failed")
        await asyncio.sleep(LIVE_SYNC_POLL_S)
