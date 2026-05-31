"""
Unit tests for scripts/sgo/_index_utils — the shared tolerant index helper.

Validates the full contract documented in _index_utils.py:
  1. Match by KEY PATTERN, not name (reuses an existing same-pattern index).
  2. Never drops existing indexes.
  3. Never mutates existing indexes (e.g. unique flag is not flipped).
  4. Codes 85 / 86 are non-fatal IF a same-pattern index now exists.
  5. Other errors propagate.

Runs against the local preview Mongo. Uses an isolated test collection
that is created and dropped per-test — does NOT touch production
collections (`sgo_*`, `mlb_*`, `nfl_*`, `ncaaf_*`).
"""
from __future__ import annotations
import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import OperationFailure

from scripts.sgo._index_utils import ensure_index, ensure_indexes


# ───────────────────────── fixtures ─────────────────────────
@pytest_asyncio.fixture
async def coll():
    """Isolated test collection — never collides with production names."""
    name = f"test_index_utils_{uuid.uuid4().hex[:8]}"
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        yield db[name]
    finally:
        await db[name].drop()
        client.close()


# ───────────────────────── contract tests ─────────────────────────
@pytest.mark.asyncio
async def test_creates_index_when_none_exists(coll):
    name = await ensure_index(
        coll, [("event_id", 1), ("snapshot_time", 1)],
        unique=True, name="events_pk")
    assert name == "events_pk"
    info = await coll.index_information()
    assert "events_pk" in info
    assert info["events_pk"].get("unique") is True


@pytest.mark.asyncio
async def test_reuses_existing_same_pattern_under_different_name(coll):
    """Contract #1: match by KEY PATTERN, not name."""
    # Pre-existing legacy index — different name, same pattern, same unique
    await coll.create_index(
        [("event_id", 1), ("snapshot_time", 1)],
        unique=True, name="event_id_1_snapshot_time_1")
    # New code path requests a different name with the same pattern
    name = await ensure_index(
        coll, [("event_id", 1), ("snapshot_time", 1)],
        unique=True, name="events_pk")
    # Returned name is the EXISTING one — not the requested one
    assert name == "event_id_1_snapshot_time_1"
    info = await coll.index_information()
    # Existing index preserved, NO new "events_pk" created (contract #2 + #3)
    assert "event_id_1_snapshot_time_1" in info
    assert "events_pk" not in info


@pytest.mark.asyncio
async def test_never_drops_existing_index(coll):
    """Contract #2: NEVER drops existing indexes."""
    await coll.create_index(
        [("event_id", 1), ("snapshot_time", 1)],
        unique=True, name="legacy_pk")
    before = set((await coll.index_information()).keys())
    await ensure_index(
        coll, [("event_id", 1), ("snapshot_time", 1)],
        unique=True, name="events_pk")
    after = set((await coll.index_information()).keys())
    assert before == after, (
        f"Index set must be unchanged. before={before} after={after}")


@pytest.mark.asyncio
async def test_never_mutates_unique_flag(coll):
    """Contract #3: NEVER mutates existing indexes — even if unique
    differs. (Changing uniqueness in place is the caller's explicit
    decision; this helper leaves it alone.)"""
    # Pre-existing NON-unique index with the same key pattern
    await coll.create_index(
        [("event_id", 1), ("snapshot_time", 1)],
        unique=False, name="legacy_non_unique")
    # Request a unique index with the same pattern → must NOT mutate
    name = await ensure_index(
        coll, [("event_id", 1), ("snapshot_time", 1)],
        unique=True, name="events_pk")
    assert name == "legacy_non_unique"
    info = await coll.index_information()
    # The pre-existing index's `unique` flag must remain unchanged (False/absent)
    assert info["legacy_non_unique"].get("unique") in (False, None)
    # And no new index was created
    assert "events_pk" not in info


@pytest.mark.asyncio
async def test_idempotent_rerun_is_noop(coll):
    """Re-running ensure_index after a successful create must be a no-op
    (matches by pattern → reuses)."""
    n1 = await ensure_index(coll, [("a", 1), ("b", 1)],
                              unique=True, name="ab_pk")
    after_first = await coll.index_information()
    n2 = await ensure_index(coll, [("a", 1), ("b", 1)],
                              unique=True, name="ab_pk")
    after_second = await coll.index_information()
    assert n1 == n2 == "ab_pk"
    assert set(after_first.keys()) == set(after_second.keys())


@pytest.mark.asyncio
async def test_bare_string_key_shorthand(coll):
    """Pymongo accepts a bare string as a single-key ASCENDING index;
    the helper must too."""
    n = await ensure_index(coll, "league_id", name="league_id_1")
    assert n == "league_id_1"
    info = await coll.index_information()
    assert info["league_id_1"]["key"] == [("league_id", 1)]


@pytest.mark.asyncio
async def test_batch_ensure_indexes(coll):
    names = await ensure_indexes(coll, [
        {"keys": [("event_id", 1), ("snapshot_time", 1)],
         "unique": True, "name": "pk"},
        {"keys": "league_id", "name": "league_id_1"},
        {"keys": "game_date", "name": "game_date_1"},
    ])
    assert names == ["pk", "league_id_1", "game_date_1"]
    info = await coll.index_information()
    assert "pk" in info and info["pk"].get("unique") is True
    assert "league_id_1" in info
    assert "game_date_1" in info


@pytest.mark.asyncio
async def test_descending_direction_preserved(coll):
    """Same field with different direction is a DIFFERENT pattern → new
    index must be created, not reuse the ascending one."""
    await ensure_index(coll, [("game_date", 1)], name="gd_asc")
    await ensure_index(coll, [("game_date", -1)], name="gd_desc")
    info = await coll.index_information()
    assert "gd_asc" in info
    assert "gd_desc" in info
    assert info["gd_asc"]["key"]  == [("game_date", 1)]
    assert info["gd_desc"]["key"] == [("game_date", -1)]


@pytest.mark.asyncio
async def test_unrelated_operation_failure_propagates(coll, monkeypatch):
    """Contract #5: any OperationFailure other than 85/86 must propagate."""
    class _FakeColl:
        async def index_information(self):
            return {}
        async def create_index(self, *a, **kw):
            raise OperationFailure("boom", code=11000)   # NOT 85/86
    with pytest.raises(OperationFailure):
        await ensure_index(_FakeColl(), [("x", 1)], name="x_1")


@pytest.mark.asyncio
async def test_code_85_with_no_matching_pattern_propagates(coll, monkeypatch):
    """Contract #4 negative case: code 85 raised but no same-pattern
    index exists after the conflict → must propagate (caller bug)."""
    class _FakeColl:
        def __init__(self):
            self._calls = 0
        async def index_information(self):
            self._calls += 1
            return {}   # always empty — no same-pattern index ever
        async def create_index(self, *a, **kw):
            raise OperationFailure("conflict", code=85)
    with pytest.raises(OperationFailure):
        await ensure_index(_FakeColl(), [("x", 1)], name="x_1")


@pytest.mark.asyncio
async def test_code_85_race_safety_net(coll, monkeypatch):
    """Contract #4 positive case: code 85 raised AND a same-pattern
    index appeared between the check and create → swallow and return
    the existing name."""
    class _FakeColl:
        def __init__(self):
            self._calls = 0
        async def index_information(self):
            self._calls += 1
            if self._calls == 1:
                return {}   # first scan: empty
            # second scan (after the create raise): pretend a racing
            # process created an index with the same pattern
            return {
                "racer_pk": {"key": [("x", 1)], "unique": True},
            }
        async def create_index(self, *a, **kw):
            raise OperationFailure("conflict", code=85)
    name = await ensure_index(_FakeColl(), [("x", 1)], name="x_1")
    assert name == "racer_pk"
