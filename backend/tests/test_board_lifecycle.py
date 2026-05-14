"""Unit tests for services/boards/board_lifecycle.py and the
admin normalize/status flow."""
from __future__ import annotations

import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta

from services.boards import board_lifecycle as bl


pytestmark = pytest.mark.asyncio


# ─── Fixtures ──────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def db():
    from mongomock_motor import AsyncMongoMockClient
    client = AsyncMongoMockClient()
    return client["test_board_lifecycle"]


# ─── Unit: stamping helpers ────────────────────────────────────────
async def test_active_stamping_sets_all_fields():
    doc = {"player_name": "x", "props": []}
    bl.stamp_active_board_doc(doc)
    assert doc["active"] is True
    assert doc["ttl_purge_at"] is None
    assert doc["stale_reason"] is None
    assert doc["stale_marked_at"] is None
    assert isinstance(doc["updated_at"], datetime)


async def test_inactive_stamping_sets_ttl_purge_at_future():
    doc = {"player_name": "x"}
    before = datetime.now(timezone.utc)
    bl.stamp_inactive_board_doc(doc, reason="manual_test")
    assert doc["active"] is False
    assert doc["stale_reason"] == "manual_test"
    assert isinstance(doc["ttl_purge_at"], datetime)
    delta = doc["ttl_purge_at"] - before
    # default grace is 24h — between 23h59m and 24h01m
    assert timedelta(hours=23, minutes=59) < delta < timedelta(hours=24, minutes=1)


async def test_inactive_stamping_preserves_existing_purge_at():
    """Re-marking inactive must not extend the grace window."""
    earlier = datetime.now(timezone.utc) + timedelta(hours=2)
    doc = {"active": False, "ttl_purge_at": earlier}
    bl.stamp_inactive_board_doc(doc, reason="re_mark")
    assert doc["ttl_purge_at"] == earlier


async def test_normalize_repairs_doc_missing_active_field():
    """Doc with no `active` field is treated as active and repaired."""
    doc = {"player_name": "x", "props": []}
    bl.normalize_board_doc(doc)
    for f in bl.LIFECYCLE_FIELDS:
        assert f in doc
    assert doc["active"] is True
    assert doc["ttl_purge_at"] is None


async def test_normalize_preserves_active_false_and_fills_inactive_fields():
    doc = {"player_name": "x", "active": False}
    bl.normalize_board_doc(doc)
    assert doc["active"] is False
    assert isinstance(doc["ttl_purge_at"], datetime)
    assert doc["stale_reason"] == "normalize_backfill_active_false"
    assert isinstance(doc["stale_marked_at"], datetime)
    assert isinstance(doc["updated_at"], datetime)


async def test_normalize_does_not_clobber_existing_lifecycle_fields():
    purge = datetime.now(timezone.utc) + timedelta(hours=5)
    doc = {
        "active": False, "ttl_purge_at": purge,
        "stale_reason": "preexisting", "stale_marked_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    bl.normalize_board_doc(doc)
    assert doc["ttl_purge_at"] == purge
    assert doc["stale_reason"] == "preexisting"


async def test_is_lifecycle_compliant_predicate():
    bad = {"active": True}
    good = {f: None for f in bl.LIFECYCLE_FIELDS}
    assert not bl.is_lifecycle_compliant(bad)
    assert bl.is_lifecycle_compliant(good)
    assert "ttl_purge_at" in bl.missing_lifecycle_fields(bad)


# ─── Mongo round-trip: upsert lifecycle compliance ─────────────────
async def test_upsert_fragment_writes_full_lifecycle(db):
    """Spreading lifecycle_set_for_upsert() into a real $set must
    produce a fully-compliant doc."""
    set_frag = {
        "player_name": "Joe",
        "sport": "test",
        **bl.lifecycle_set_for_upsert(),
    }
    await db["test_cb"].update_one(
        {"player_name": "Joe"},
        {"$set": set_frag, "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    stored = await db["test_cb"].find_one({"player_name": "Joe"})
    for f in bl.LIFECYCLE_FIELDS:
        assert f in stored, f"missing lifecycle field: {f}"
    assert stored["active"] is True
    assert stored["ttl_purge_at"] is None


async def test_inactive_set_fragment_round_trip(db):
    """$set fragment from lifecycle_set_inactive must materialize
    a fully-compliant inactive doc."""
    await db["test_cb"].insert_one({"player_name": "Bob", "active": True})
    await db["test_cb"].update_one(
        {"player_name": "Bob"},
        {"$set": bl.lifecycle_set_inactive(reason="test_orphan")},
    )
    stored = await db["test_cb"].find_one({"player_name": "Bob"})
    assert stored["active"] is False
    assert isinstance(stored["ttl_purge_at"], datetime)
    assert stored["stale_reason"] == "test_orphan"
    assert isinstance(stored["stale_marked_at"], datetime)


# ─── /v3/board defensive read — only active=True ──────────────────
async def test_board_filter_excludes_inactive_and_unstamped_docs(db):
    """Simulate the /v3/board query path. Only active=True docs are
    returned; inactive and missing-active-field docs are filtered."""
    await db["sport_prop_scores"].insert_many([
        {"canonical_key": "a", "version_tag": "final-sport-rt",
         "ranking_score_v2": 0.9, "active": True, "ttl_purge_at": None,
         "stale_reason": None, "stale_marked_at": None,
         "updated_at": datetime.now(timezone.utc)},
        {"canonical_key": "b", "version_tag": "final-sport-rt",
         "ranking_score_v2": 0.8, "active": False,
         "ttl_purge_at": datetime.now(timezone.utc) + timedelta(hours=24),
         "stale_reason": "orphan", "stale_marked_at": datetime.now(timezone.utc),
         "updated_at": datetime.now(timezone.utc)},
        {"canonical_key": "c", "version_tag": "final-sport-rt",
         "ranking_score_v2": 0.7},  # malformed (no active)
    ])
    served = await db["sport_prop_scores"].find(
        {"version_tag": "final-sport-rt", "active": True}
    ).to_list(length=10)
    assert len(served) == 1
    assert served[0]["canonical_key"] == "a"


# ─── Migration dry-run behaviour ───────────────────────────────────
async def test_normalize_dry_run_does_not_mutate(db):
    """Manual replay of the normalize endpoint's dry-run branch."""
    await db["test_cb"].insert_many([
        {"player_name": "good", "active": True, "ttl_purge_at": None,
         "stale_reason": None, "stale_marked_at": None,
         "updated_at": datetime.now(timezone.utc)},
        {"player_name": "bad"},  # missing every lifecycle field
    ])
    missing_filter = {"$or": [{f: {"$exists": False}}
                              for f in bl.LIFECYCLE_FIELDS]}
    n_before = await db["test_cb"].count_documents(missing_filter)
    assert n_before == 1
    # Dry run: do NOT update — just count
    scanned = 0
    async for _ in db["test_cb"].find(missing_filter):
        scanned += 1
    assert scanned == 1
    # No mutation
    n_after = await db["test_cb"].count_documents(missing_filter)
    assert n_after == 1


async def test_normalize_real_run_repairs_doc(db):
    await db["test_cb"].insert_many([
        {"player_name": "bad1"},
        {"player_name": "bad2", "active": False},
    ])
    missing_filter = {"$or": [{f: {"$exists": False}}
                              for f in bl.LIFECYCLE_FIELDS]}
    async for d in db["test_cb"].find(missing_filter):
        patch = {f: d.get(f) for f in bl.LIFECYCLE_FIELDS if f in d}
        scratch = {**patch}
        bl.normalize_board_doc(scratch)
        update = {f: scratch[f] for f in bl.LIFECYCLE_FIELDS}
        await db["test_cb"].update_one({"_id": d["_id"]}, {"$set": update})
    # All docs now compliant
    n_after = await db["test_cb"].count_documents(missing_filter)
    assert n_after == 0
    # bad2 (active=False) preserved as inactive with full lifecycle.
    bad2 = await db["test_cb"].find_one({"player_name": "bad2"})
    assert bad2["active"] is False
    assert isinstance(bad2["ttl_purge_at"], datetime)
    assert bad2["stale_reason"] is not None
    # bad1 normalized to active
    bad1 = await db["test_cb"].find_one({"player_name": "bad1"})
    assert bad1["active"] is True
    assert bad1["ttl_purge_at"] is None
