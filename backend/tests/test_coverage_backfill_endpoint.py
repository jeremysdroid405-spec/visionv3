"""
Test for POST /api/emergent-admin/coverage/backfill — verifies that
cache-preflight correctly short-circuits redundant enqueues and that
the cache-miss path routes to the research_worker.
"""
from __future__ import annotations
import os
import sys
import uuid

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from httpx import AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")

BACKEND_URL  = os.environ.get("BACKEND_URL", "http://127.0.0.1:8001")
HTTP_TIMEOUT = 20.0

TAG = f"_pytest_{uuid.uuid4().hex[:8]}"
START = "2099-01-01"   # in the future so we don't collide with real data
END   = "2099-01-02"
SPORT = "MLB"


def _auth() -> dict:
    tok = os.environ.get("EMERGENT_ADMIN_TOKEN")
    assert tok, "EMERGENT_ADMIN_TOKEN must be set"
    return {"X-Admin-Token": tok, "X-Agent-Id": "pytest"}


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    d = client[os.environ["DB_NAME"]]
    yield d
    # Tear down any test docs and queued jobs we may have created.
    await d["sgo_player_stats"].delete_many({"_pytest_tag": TAG})
    await d["emergent_admin_jobs"].delete_many({"args": {"$all": ["--start", START, "--end", END]}})
    client.close()


@pytest.mark.asyncio
async def test_backfill_cache_miss_enqueues_to_worker(db):
    # Empty collection for this window → must enqueue.
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.post("/api/emergent-admin/coverage/backfill",
                            headers=_auth(),
                            json={"key": "stats", "sport": SPORT,
                                    "start": START, "end": END})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "queued"
    assert body["routed_to"] in ("research_worker", "inline")
    assert body["preflight_rows"] == 0
    assert body["fix_job"] == "scripts.sgo.ingest_historical_player_stats"
    # Verify the doc made it into the jobs collection
    doc = await db["emergent_admin_jobs"].find_one({"job_id": body["job_id"]})
    assert doc is not None
    assert doc["module"] == "scripts.sgo.ingest_historical_player_stats"


@pytest.mark.asyncio
async def test_backfill_cache_hit_short_circuits(db):
    # Seed a row so the preflight count is > 0.
    await db["sgo_player_stats"].insert_one({
        "league_id": SPORT, "game_date": START, "player_id": "test",
        "_pytest_tag": TAG,
    })
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.post("/api/emergent-admin/coverage/backfill",
                            headers=_auth(),
                            json={"key": "stats", "sport": SPORT,
                                    "start": START, "end": END})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "cached_skip"
    assert body["row_count"] == 1
    assert "job_id" not in body  # critically: NO enqueue happened
    assert body["collection"] == "sgo_player_stats"


@pytest.mark.asyncio
async def test_backfill_force_bypasses_cache_hit(db):
    await db["sgo_player_stats"].insert_one({
        "league_id": SPORT, "game_date": START, "player_id": "test",
        "_pytest_tag": TAG,
    })
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.post("/api/emergent-admin/coverage/backfill",
                            headers=_auth(),
                            json={"key": "stats", "sport": SPORT,
                                    "start": START, "end": END,
                                    "force": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["preflight_rows"] == 1   # we saw the existing rows but bypassed


@pytest.mark.asyncio
async def test_backfill_rejects_unknown_key(db):
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.post("/api/emergent-admin/coverage/backfill",
                            headers=_auth(),
                            json={"key": "bogus_key", "sport": SPORT,
                                    "start": START, "end": END})
    assert r.status_code == 400, r.text
