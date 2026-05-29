"""
Phase 1.A.3.1 follow-up — `list_ingest_runs` + endpoint tests.

Covers:
- Empty collection → empty list, n_total=0
- Latest-first sort by `started_at` desc
- Limit + offset pagination
- Sport filter
- Status filter
- Redaction of `_id` and `guard_reasons` (with `guard_blocked` set
  from the underlying flag)
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")

from services.team_master_hub.collections import (  # noqa: E402
    ensure_team_collections,
)
from services.team_master_hub.ingest_runs import (  # noqa: E402
    INGEST_RUNS_COLL,
    list_ingest_runs,
)


_TEST_DB_NAME = "team_ingest_runs_query_shared"
_TEST_COLLS = (
    "team_master_hub", "team_live_props", "team_historical_props",
    "team_prop_outcomes", "team_matchups", "team_injuries",
    "team_context", "team_features", "team_projections",
    "team_prop_scores", "team_replay_outputs",
    "team_odds_ingest_runs",
)


@pytest_asyncio.fixture
async def db():
    mongo_url = os.environ["MONGO_URL"]
    client = AsyncIOMotorClient(mongo_url)
    _db = client[_TEST_DB_NAME]
    for c in _TEST_COLLS:
        await _db[c].drop()
    try:
        yield _db
    finally:
        for c in _TEST_COLLS:
            try:
                await _db[c].drop()
            except Exception:
                pass
        client.close()


def _row(*, sport: str, status: str, started: datetime,
          guard_reasons: list[str] | None = None,
          mode: str = "dry_run") -> dict:
    return {
        "run_id":             str(uuid.uuid4()),
        "sport":              sport,
        "worker":             "team_odds_ingest",
        "mode_requested":     mode,
        "mode_effective":     mode,
        "dry_run":            mode == "dry_run",
        "live_write_allowed": mode == "live",
        "guard_reasons":      guard_reasons or [],
        "started_at":         started,
        "finished_at":        started + timedelta(milliseconds=100),
        "duration_ms":        100,
        "snapshot_iso":       started.isoformat(),
        "status":             status,
        "diagnosis":          "test",
        "n_writes":           0,
    }


@pytest.mark.asyncio
async def test_empty_collection_returns_empty_list(db) -> None:
    await ensure_team_collections(db)
    r = await list_ingest_runs(db)
    assert r["ok"] is True
    assert r["n_total"]    == 0
    assert r["n_returned"] == 0
    assert r["runs"]       == []
    assert r["filters"]    == {"sport": None, "status": None}


@pytest.mark.asyncio
async def test_returns_latest_first(db) -> None:
    await ensure_team_collections(db)
    base = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)
    rows = [
        _row(sport="mlb", status="succeeded",
              started=base + timedelta(minutes=i))
        for i in range(5)
    ]
    await db[INGEST_RUNS_COLL].insert_many(rows)
    r = await list_ingest_runs(db, limit=10)
    assert r["n_total"]    == 5
    assert r["n_returned"] == 5
    started_list = [run["started_at"] for run in r["runs"]]
    assert started_list == sorted(started_list, reverse=True)


@pytest.mark.asyncio
async def test_limit_and_offset(db) -> None:
    await ensure_team_collections(db)
    base = datetime(2026, 6, 2, tzinfo=timezone.utc)
    await db[INGEST_RUNS_COLL].insert_many([
        _row(sport="mlb", status="dry_run",
              started=base + timedelta(seconds=i))
        for i in range(7)
    ])
    page1 = await list_ingest_runs(db, limit=3, offset=0)
    page2 = await list_ingest_runs(db, limit=3, offset=3)
    assert page1["n_total"]    == 7
    assert page1["n_returned"] == 3
    assert page2["n_returned"] == 3
    # No overlap between pages
    run_ids_p1 = {r["run_id"] for r in page1["runs"]}
    run_ids_p2 = {r["run_id"] for r in page2["runs"]}
    assert run_ids_p1.isdisjoint(run_ids_p2)


@pytest.mark.asyncio
async def test_sport_filter(db) -> None:
    await ensure_team_collections(db)
    base = datetime(2026, 6, 2, tzinfo=timezone.utc)
    await db[INGEST_RUNS_COLL].insert_many([
        _row(sport="mlb", status="dry_run", started=base),
        _row(sport="mlb", status="dry_run",
              started=base + timedelta(seconds=1)),
        _row(sport="nba", status="dry_run", started=base),
        _row(sport="nfl", status="dry_run", started=base),
    ])
    r = await list_ingest_runs(db, sport="mlb")
    assert r["n_total"] == 2
    assert {run["sport"] for run in r["runs"]} == {"mlb"}


@pytest.mark.asyncio
async def test_status_filter(db) -> None:
    await ensure_team_collections(db)
    base = datetime(2026, 6, 2, tzinfo=timezone.utc)
    await db[INGEST_RUNS_COLL].insert_many([
        _row(sport="mlb", status="succeeded",         started=base),
        _row(sport="mlb", status="guard_closed",      started=base),
        _row(sport="mlb", status="aborted_explosion", started=base),
        _row(sport="mlb", status="dry_run",           started=base),
    ])
    r = await list_ingest_runs(db, status="guard_closed")
    assert r["n_total"] == 1
    assert r["runs"][0]["status"] == "guard_closed"


@pytest.mark.asyncio
async def test_redaction_of_id_and_guard_reasons(db) -> None:
    await ensure_team_collections(db)
    base = datetime(2026, 6, 2, tzinfo=timezone.utc)
    await db[INGEST_RUNS_COLL].insert_one(_row(
        sport="mlb", status="guard_closed", started=base,
        guard_reasons=["SGO_API_KEY env var is missing",
                       "TEAM_INGEST_ENABLED is not set to '1'"],
    ))
    r = await list_ingest_runs(db)
    row = r["runs"][0]
    # Sensitive fields removed
    assert "_id" not in row
    assert "guard_reasons" not in row
    # Redacted flag surfaced
    assert row["guard_blocked"] is True


@pytest.mark.asyncio
async def test_guard_blocked_false_when_no_reasons(db) -> None:
    await ensure_team_collections(db)
    base = datetime(2026, 6, 2, tzinfo=timezone.utc)
    await db[INGEST_RUNS_COLL].insert_one(_row(
        sport="mlb", status="succeeded", started=base,
        guard_reasons=[],
    ))
    r = await list_ingest_runs(db)
    assert r["runs"][0]["guard_blocked"] is False


@pytest.mark.asyncio
async def test_sport_filter_normalizes_case(db) -> None:
    await ensure_team_collections(db)
    base = datetime(2026, 6, 2, tzinfo=timezone.utc)
    await db[INGEST_RUNS_COLL].insert_one(_row(
        sport="mlb", status="dry_run", started=base))
    # Caller passes uppercase
    r = await list_ingest_runs(db, sport="MLB")
    assert r["n_total"] == 1
