"""
Unit tests for workers/live_sync_state.py state machine.

Pins the auto / manual interaction rules from the 2026-05-23 spec:

    1. Worker AUTO-PAUSE on job start.
    2. Worker AUTO-RESUME after queue drains, ONLY when the doc was
       set by another auto event (not by a manual operator).
    3. Manual override sticks across job boundaries.
    4. Crash leaves doc paused until manual resume (we test by
       simulating a finish() with queue_depth>0).
"""
from __future__ import annotations
import os
import sys
import uuid

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")
from workers.live_sync_state import (  # noqa: E402
    LIVE_SYNC_ID, STATE_COLL,
    get_live_sync, set_live_sync,
    worker_pause_for_job, worker_finish_job,
    manual_pause, manual_resume,
)


@pytest_asyncio.fixture
async def db():
    """Throwaway live_sync state on a unique _id so we don't stomp on
    the real singleton. We patch LIVE_SYNC_ID for the test then revert."""
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    d = cli[os.environ["DB_NAME"]]
    test_id = f"live_sync_test_{uuid.uuid4().hex[:8]}"
    # Monkey-swap the module constant for the duration of the test.
    import workers.live_sync_state as mod
    orig = mod.LIVE_SYNC_ID
    mod.LIVE_SYNC_ID = test_id
    try:
        yield d
    finally:
        await d[STATE_COLL].delete_one({"_id": test_id})
        mod.LIVE_SYNC_ID = orig
        cli.close()


@pytest.mark.asyncio
async def test_initial_state_is_unpaused(db):
    doc = await get_live_sync(db)
    assert doc["paused"] is False
    assert doc["manual_override"] is False


@pytest.mark.asyncio
async def test_worker_auto_pause(db):
    doc = await worker_pause_for_job(db, job_id="j1", worker_id="w1")
    assert doc["paused"] is True
    assert doc["reason"] == "auto:job=j1"
    assert doc["set_by"] == "worker:w1"
    assert doc["manual_override"] is False
    assert doc["active_job_id"] == "j1"


@pytest.mark.asyncio
async def test_worker_finish_with_empty_queue_resumes(db):
    await worker_pause_for_job(db, job_id="j1", worker_id="w1")
    doc = await worker_finish_job(db, job_id="j1", worker_id="w1",
                                          queue_depth=0)
    assert doc["paused"] is False
    assert doc["active_job_id"] is None
    assert doc["reason"].startswith("auto:")


@pytest.mark.asyncio
async def test_worker_finish_with_pending_queue_stays_paused(db):
    await worker_pause_for_job(db, job_id="j1", worker_id="w1")
    doc = await worker_finish_job(db, job_id="j1", worker_id="w1",
                                          queue_depth=3)
    # More jobs queued → keep paused; just update reason.
    assert doc["paused"] is True
    assert "queue_drain" in doc["reason"]
    assert doc["active_job_id"] is None


@pytest.mark.asyncio
async def test_manual_pause_then_worker_pause_keeps_manual(db):
    """Manual pause sticks even if the worker enters a job mid-stream.
    Operator intent must NOT be silently downgraded to auto."""
    await manual_pause(db, reason="dev debugging", agent_id="dev")
    doc = await worker_pause_for_job(db, job_id="jX", worker_id="w1")
    assert doc["paused"] is True
    assert doc["manual_override"] is True
    assert "manual" in doc["reason"]
    # active_job_id IS set so UI can still show what's running.
    assert doc["active_job_id"] == "jX"


@pytest.mark.asyncio
async def test_worker_finish_does_not_override_manual_pause(db):
    """Auto-resume must not undo a manual pause, even when the queue
    is empty."""
    await manual_pause(db, reason="dev debugging", agent_id="dev")
    await worker_pause_for_job(db, job_id="jX", worker_id="w1")
    doc = await worker_finish_job(db, job_id="jX", worker_id="w1",
                                          queue_depth=0)
    assert doc["paused"] is True
    assert doc["manual_override"] is True
    # active_job_id cleared (job is done) but pause stays.
    assert doc["active_job_id"] is None


@pytest.mark.asyncio
async def test_manual_resume_clears_override(db):
    await manual_pause(db, reason="dev", agent_id="dev")
    doc = await manual_resume(db, reason="back to live",
                                       agent_id="dev")
    assert doc["paused"] is False
    assert doc["manual_override"] is False


@pytest.mark.asyncio
async def test_crash_simulation_leaves_paused(db):
    """We simulate a crash by NEVER calling worker_finish_job. The doc
    remains in its paused state — the operator must resume manually.

    (In production the worker DOES call finish_job in a `finally:` to
    catch crashes, but a hard kill would still leave the doc paused.
    That's the desired guardrail.)"""
    await worker_pause_for_job(db, job_id="crashed_job", worker_id="w1")
    # No finish() call. Reload the doc.
    doc = await get_live_sync(db)
    assert doc["paused"] is True
    assert doc["active_job_id"] == "crashed_job"


@pytest.mark.asyncio
async def test_back_to_back_jobs_stay_paused(db):
    """Worker auto-resume should not flicker when jobs arrive back-to-
    back: pause(j1) → finish(j1, depth=1) → pause(j2) → finish(j2, 0)
    → resume."""
    await worker_pause_for_job(db, job_id="j1", worker_id="w1")
    d1 = await worker_finish_job(db, job_id="j1", worker_id="w1",
                                         queue_depth=1)
    assert d1["paused"] is True
    d2 = await worker_pause_for_job(db, job_id="j2", worker_id="w1")
    assert d2["paused"] is True and d2["active_job_id"] == "j2"
    d3 = await worker_finish_job(db, job_id="j2", worker_id="w1",
                                         queue_depth=0)
    assert d3["paused"] is False
