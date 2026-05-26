"""
Test the /jobs/run dedupe (2026-05-26).

PROBLEM this pins:
  Prod log (2026-05-26 22:13-15) showed 87 identical jobs enqueued for
  `scripts.sgo.reshape_sgo_to_replay_odds --league MLB --start 2025-06-01
  --end 2025-07-01` in under two minutes. Root cause was a frontend
  race (AdminTesting.jsx WorkflowTab `drive()` re-enqueuing while the
  first POST /jobs/run was in flight) plus zero server-side defense.

  The user perceived this as "the worker is hung" because each job
  takes ~10 s and the chain never advanced — but the worker was
  actually busy chewing through 87 redundant copies of the same step.

CONTRACT this pins:
  POST /jobs/run with a (module, args) pair that already has a job in
  ['queued', 'claimed', 'running'] returns the EXISTING job_id with
  `deduped=true` instead of inserting a new one.
"""
from __future__ import annotations
import asyncio
import inspect
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

JOBS_COLL = "emergent_admin_jobs"


@pytest_asyncio.fixture
async def jobs_db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    yield client[os.environ["DB_NAME"]]
    client.close()


@pytest.mark.asyncio
async def test_run_job_endpoint_has_dedupe_check():
    """Source-inspect: /jobs/run must lookup an in-flight job with
    matching (module, args) before inserting a new one."""
    from routes.emergent_admin.jobs import run_job
    src = inspect.getsource(run_job)
    assert "find_one" in src, "/jobs/run must check for in-flight duplicate"
    assert "deduped" in src, "/jobs/run must mark the dedupe response"
    assert '"queued"' in src and '"running"' in src, (
        "/jobs/run dedupe must consider queued AND running states")


@pytest.mark.asyncio
async def test_dedupe_index_is_created(jobs_db):
    """The dedupe lookup MUST be backed by an index (module, status,
    queued_at) — otherwise it COLLSCANs and becomes a new 504 source
    on busy hosts."""
    info = await jobs_db[JOBS_COLL].index_information()
    by_name = list(info.keys())
    assert any("dedupe_lookup_idx" in n for n in by_name) or any(
        spec.get("key", [])[:1] == [("module", 1)] for spec in info.values()
    ), (
        f"missing dedupe_lookup_idx — found indexes: {by_name}. "
        f"The enqueue path should lazily create this.")


@pytest.mark.asyncio
async def test_dedupe_query_returns_existing_in_flight_job(jobs_db):
    """Behavioural test: seed an in-flight job, then run the same
    Mongo query the endpoint does — must return the existing row."""
    jid = f"pytest_dedupe_{uuid.uuid4().hex[:8]}"
    try:
        await jobs_db[JOBS_COLL].insert_one({
            "job_id":     jid,
            "module":     "scripts.sgo.reshape_sgo_to_replay_odds",
            "args":       ["--league", "MLB", "--start", "2025-06-01",
                              "--end", "2025-07-01"],
            "status":     "running",
            "queued_at":  datetime.now(timezone.utc),
        })
        # This is exactly the query the endpoint runs:
        existing = await jobs_db[JOBS_COLL].find_one(
            {"module": "scripts.sgo.reshape_sgo_to_replay_odds",
              "args":   ["--league", "MLB", "--start", "2025-06-01",
                          "--end", "2025-07-01"],
              "status": {"$in": ["queued", "claimed", "running"]}},
            {"_id": 0, "job_id": 1, "status": 1, "queued_at": 1},
            sort=[("queued_at", -1)],
        )
        assert existing is not None
        assert existing["job_id"] == jid
        assert existing["status"] == "running"
    finally:
        await jobs_db[JOBS_COLL].delete_one({"job_id": jid})


@pytest.mark.asyncio
async def test_dedupe_does_NOT_match_finished_jobs(jobs_db):
    """Once a job finishes (succeeded/failed/errored/cancelled), it
    must NOT block a fresh enqueue with the same args. Otherwise
    re-running the same step after success would silently no-op."""
    jid = f"pytest_dedupe_done_{uuid.uuid4().hex[:8]}"
    try:
        await jobs_db[JOBS_COLL].insert_one({
            "job_id":     jid,
            "module":     "scripts.sgo.reshape_sgo_to_replay_odds",
            "args":       ["--league", "MLB", "--start", "2025-07-15",
                              "--end", "2025-07-15"],
            "status":     "succeeded",
            "queued_at":  datetime.now(timezone.utc),
        })
        existing = await jobs_db[JOBS_COLL].find_one(
            {"module": "scripts.sgo.reshape_sgo_to_replay_odds",
              "args":   ["--league", "MLB", "--start", "2025-07-15",
                          "--end", "2025-07-15"],
              "status": {"$in": ["queued", "claimed", "running"]}},
        )
        assert existing is None, (
            "dedupe must not match finished jobs — that would block "
            "re-running the same step after success/failure")
    finally:
        await jobs_db[JOBS_COLL].delete_one({"job_id": jid})


@pytest.mark.asyncio
async def test_frontend_has_dispatch_mutex():
    """Source-inspect AdminTesting.jsx: the Workflow tab MUST have a
    dispatchingRef mutex around the /jobs/run POST so a 1-s poll tick
    can't fire a second enqueue while the first is in flight."""
    src = open("/app/frontend/src/pages/AdminTesting.jsx").read()
    assert "dispatchingRef" in src, (
        "WorkflowTab missing dispatchingRef — the 1-s setInterval "
        "drive() will race and enqueue duplicate /jobs/run requests "
        "(prod showed 87 duplicates in 2 minutes from this bug)")
    # The mutex MUST be checked BEFORE the POST and released in finally
    # so a network error doesn't permanently jam the chain.
    assert "dispatchingRef.current = true" in src
    assert "dispatchingRef.current = false" in src
    # The release MUST be in a finally so exceptions don't permanently
    # lock the mutex.
    snippet = src[src.index("dispatchingRef.current = true"):
                       src.index("dispatchingRef.current = false") + 50]
    assert "finally" in snippet, (
        "dispatchingRef release must be in a finally block — an "
        "exception in the POST path would otherwise permanently lock "
        "the chain at the current step")
