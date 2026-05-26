"""
Job-log capping + slicing contract (2026-05-26).

PROBLEM this pins (user-reported step-2 504s):
  - `append_log` previously used an unbounded `$push`. A long-running
    pipeline step (e.g. `build_historical_model_features` over a 90-
    day MLB window) easily emits 50 000+ lines. The job doc grew past
    the 16 MB BSON limit → writes silently failed, downstream polls
    returned 16 MB → nginx 504 → operator saw "worker died."
  - `/jobs/{id}/log` pulled the WHOLE `log` array, then sliced in
    Python. Every 1-second poll re-transferred the full log. Six
    concurrent runs each polling 10× their tail = quick path to
    saturated Mongo.

CONTRACT:
  1. `append_log` writes with `$slice: -LOG_CAP_LINES` so the rolling
     buffer is hard-capped server-side.
  2. `LOG_CAP_LINES` is bounded ≤ 5 000 so a worst-case 5 KB line ×
     5 000 entries is well under the 16 MB BSON ceiling.
  3. `append_log` also `$inc`s `log_lines_total` so the operator can
     still see cumulative line count.
  4. `/jobs/{id}/log` uses `$slice: -tail` in the projection so Mongo
     ONLY sends back the requested tail — never the whole buffer.
  5. Every find_one in /jobs/* pins `max_time_ms` so a missing index
     can't blow nginx's 60-second ceiling.
"""
from __future__ import annotations
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402
load_dotenv("/app/backend/.env")

# Import the production functions we're pinning.
from workers.queue import (  # noqa: E402
    JOBS_COLL, LOG_CAP_LINES, append_log,
)


@pytest_asyncio.fixture
async def jobs_db():
    """Per-test motor client to avoid the cached-loop pitfall."""
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    yield client[os.environ["DB_NAME"]]
    client.close()


@pytest.mark.asyncio
async def test_log_cap_is_bounded():
    """The cap MUST be ≤ 5 000 lines so a 5 KB line × CAP × 2 bytes
    BSON overhead stays well below the 16 MB ceiling."""
    assert 100 <= LOG_CAP_LINES <= 5_000, (
        f"LOG_CAP_LINES must be in [100, 5 000] to keep the doc "
        f"under the 16 MB BSON ceiling — got {LOG_CAP_LINES}")


@pytest.mark.asyncio
async def test_append_log_caps_rolling_buffer(jobs_db):
    """Push 3× the cap; the rolling `log` array must be hard-trimmed
    to LOG_CAP_LINES on the server side, NOT in Python."""
    jid = f"pytest_logcap_{uuid.uuid4().hex[:8]}"
    try:
        await jobs_db[JOBS_COLL].insert_one({
            "job_id": jid, "status": "running", "log": [],
            "log_lines_total": 0,
        })
        # Push 3× the cap, one batch at a time, simulating a noisy
        # build_features run.
        batch = ["x" * 100 + f" line {i}" for i in range(500)]
        # Patch the queue.db() function so append_log writes to our
        # test handle (workers.queue.db caches its own client).
        import workers.queue as q
        _orig = q.db
        q.db = lambda: jobs_db.client[jobs_db.name]  # type: ignore[assignment]
        try:
            for _ in range(LOG_CAP_LINES // 500 * 3 + 2):
                await append_log(jid, batch)
        finally:
            q.db = _orig

        doc = await jobs_db[JOBS_COLL].find_one({"job_id": jid})
        log = doc.get("log") or []
        assert len(log) == LOG_CAP_LINES, (
            f"rolling buffer should be hard-capped at {LOG_CAP_LINES}; "
            f"got {len(log)}")
        # Cumulative counter must reflect TOTAL pushed (not just buffer)
        total = doc.get("log_lines_total")
        assert total >= 2 * LOG_CAP_LINES, (
            f"log_lines_total should track every pushed line; got {total}")
    finally:
        await jobs_db[JOBS_COLL].delete_one({"job_id": jid})


@pytest.mark.asyncio
async def test_get_job_log_endpoint_uses_slice_projection():
    """The /jobs/{id}/log handler must use $slice in the projection so
    Mongo ships only `tail` entries. Verified via source inspection —
    a behavioural test would require spinning up the full FastAPI app
    and seeding a job doc, which is more flaky than valuable here."""
    import inspect
    from routes.emergent_admin.jobs import get_job_log
    src = inspect.getsource(get_job_log)
    assert '"$slice"' in src or "'$slice'" in src, (
        "/jobs/{id}/log must use $slice projection to avoid shipping "
        "the entire log array (was the 504 root cause for noisy steps)")
    assert "-tail" in src.replace(" ", ""), (
        "the slice must be `-tail` (last N), not a fixed number")


@pytest.mark.asyncio
async def test_jobs_endpoints_have_max_time_ms():
    """Both /jobs/{id} and /jobs/{id}/log must pin max_time_ms on their
    find_one so a missing index or replica lag can't 504 the polling
    loop."""
    import inspect
    from routes.emergent_admin.jobs import get_job, get_job_log
    src_get = inspect.getsource(get_job)
    src_log = inspect.getsource(get_job_log)
    assert "max_time_ms" in src_get, "/jobs/{id} must pin max_time_ms"
    assert "max_time_ms" in src_log, "/jobs/{id}/log must pin max_time_ms"


@pytest.mark.asyncio
async def test_jobs_collection_has_job_id_index(jobs_db):
    """The `emergent_admin_jobs` collection MUST have a unique index on
    `job_id`. Without it, every /jobs/{id} poll is a COLLSCAN — which
    on a busy host with 100 k+ jobs accumulated turns into a 504 per
    poll. The enqueue path lazily creates this index."""
    info = await jobs_db[JOBS_COLL].index_information()
    has_job_id = any(
        any(field == "job_id" for field, _ in spec.get("key", []))
        for spec in info.values()
    )
    assert has_job_id, (
        f"emergent_admin_jobs is missing a job_id index — "
        f"found: {list(info.keys())}. The enqueue path should "
        f"lazily create this.")
