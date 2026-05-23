"""
Live-sync state coordination.

A single Mongo doc `system_state.{_id="live_sync"}` is the SSOT for
whether the backend's APScheduler / background loops should pause.
Both the API process (manual toggle) and the research_worker process
(auto-pause/resume around jobs) write to it; the backend's reconciler
loop reads it every ~3 s and applies the desired state to APScheduler.

Schema:
    {
        "_id":             "live_sync",
        "paused":           bool,
        "reason":           str,           # e.g. "auto:job=...", "manual:..."
        "manual_override":  bool,          # auto-resume refuses to clear this
        "set_at":           datetime UTC,
        "set_by":           str,           # "worker:pid=NN" or "api:agent=…"
        "active_job_id":    str | None,    # populated when worker is mid-job
    }

`manual_override = True` short-circuits auto-resume — the operator
explicitly flipped it, so the worker won't undo that decision.
"""
from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

STATE_COLL  = "system_state"
LIVE_SYNC_ID = "live_sync"

AUTO_REASON_PREFIX = "auto:"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def get_live_sync(db: AsyncIOMotorDatabase) -> Dict[str, Any]:
    """Returns the current live_sync doc (without `_id`). Empty defaults
    when the doc has never been written."""
    doc = await db[STATE_COLL].find_one(
        {"_id": LIVE_SYNC_ID}, {"_id": 0})
    if not doc:
        return {"paused": False, "reason": "", "manual_override": False,
                  "set_at": None, "set_by": "", "active_job_id": None}
    return doc


async def set_live_sync(
    db: AsyncIOMotorDatabase,
    *, paused: bool, reason: str, set_by: str,
    manual_override: bool = False,
    active_job_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Atomic upsert of the live_sync doc. Returns the new doc."""
    new_doc = {
        "paused":           bool(paused),
        "reason":           reason,
        "manual_override":  bool(manual_override),
        "set_at":           _now(),
        "set_by":           set_by,
        "active_job_id":    active_job_id,
    }
    await db[STATE_COLL].update_one(
        {"_id": LIVE_SYNC_ID},
        {"$set": new_doc},
        upsert=True,
    )
    return new_doc


# ── Worker-facing helpers ──────────────────────────────────────────────
async def worker_pause_for_job(
    db: AsyncIOMotorDatabase, *, job_id: str, worker_id: str,
) -> Dict[str, Any]:
    """Called by the worker BEFORE running a job. Only flips when not
    already paused by a manual override."""
    cur = await get_live_sync(db)
    # Manual override sticks — we don't overwrite the reason but we DO
    # tag the active job so the UI can show it.
    if cur.get("manual_override") and cur.get("paused"):
        return await set_live_sync(
            db, paused=True,
            reason=cur.get("reason", "manual"),
            set_by=cur.get("set_by", "manual"),
            manual_override=True,
            active_job_id=job_id,
        )
    return await set_live_sync(
        db,
        paused=True,
        reason=f"{AUTO_REASON_PREFIX}job={job_id}",
        set_by=f"worker:{worker_id}",
        manual_override=False,
        active_job_id=job_id,
    )


async def worker_finish_job(
    db: AsyncIOMotorDatabase, *, job_id: str, worker_id: str,
    queue_depth: int,
) -> Dict[str, Any]:
    """Called by the worker AFTER each job (success OR failure).
    Resumes only when (1) the current reason is auto (we set it) AND
    (2) no manual override AND (3) queue is empty. Otherwise keeps it
    paused — the next job's `worker_pause_for_job` will refresh the
    reason."""
    cur = await get_live_sync(db)
    is_auto = (cur.get("reason") or "").startswith(AUTO_REASON_PREFIX)
    if cur.get("manual_override") or not is_auto:
        # Operator-paused; do NOT auto-resume. Just clear active_job_id.
        return await set_live_sync(
            db, paused=cur.get("paused", False),
            reason=cur.get("reason", ""),
            set_by=cur.get("set_by", "manual"),
            manual_override=bool(cur.get("manual_override")),
            active_job_id=None,
        )
    if queue_depth > 0:
        # Another job pending. Keep paused; clear active_job_id.
        return await set_live_sync(
            db, paused=True,
            reason=f"{AUTO_REASON_PREFIX}queue_drain pending={queue_depth}",
            set_by=f"worker:{worker_id}",
            manual_override=False,
            active_job_id=None,
        )
    # Queue drained, no manual override → auto-resume.
    return await set_live_sync(
        db, paused=False,
        reason=f"{AUTO_REASON_PREFIX}auto-resumed after job={job_id}",
        set_by=f"worker:{worker_id}",
        manual_override=False,
        active_job_id=None,
    )


# ── API-facing helpers (manual toggle) ─────────────────────────────────
async def manual_pause(db: AsyncIOMotorDatabase, *,
                            reason: str, agent_id: str) -> Dict[str, Any]:
    return await set_live_sync(
        db, paused=True,
        reason=f"manual: {reason}" if reason else "manual",
        set_by=f"api:agent={agent_id}",
        manual_override=True,
    )


async def manual_resume(db: AsyncIOMotorDatabase, *,
                              reason: str, agent_id: str) -> Dict[str, Any]:
    return await set_live_sync(
        db, paused=False,
        reason=f"manual: {reason}" if reason else "manual:resumed",
        set_by=f"api:agent={agent_id}",
        manual_override=False,
    )
