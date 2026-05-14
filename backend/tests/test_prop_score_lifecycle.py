"""Universal lifecycle compliance tests for `{sport}_prop_scores`.

Mirrors `test_board_lifecycle.py` but exercises the prop-score write
path and the integration with the ephemeral cleanup utility.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta

from services.boards import board_lifecycle as bl
from services.cleanup import ephemeral_cleanup as ec
from services.cleanup import ephemeral_collections as eco


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db():
    from mongomock_motor import AsyncMongoMockClient
    client = AsyncMongoMockClient()
    return client["test_prop_score_lifecycle"]


@pytest.fixture
def patched_cleanup_config(monkeypatch):
    """Single-sport config covering prop_scores only."""
    config = {
        "tst": {
            "enabled": True,
            "live_collection": "tst_live_props",
            "canonical_key_field": "canonical_key",
            "grace_hours": 24,
            "collections": ["tst_prop_scores"],
        },
    }
    monkeypatch.setattr(eco, "EPHEMERAL_CLEANUP_CONFIG", config)
    monkeypatch.setattr(ec, "EPHEMERAL_CLEANUP_CONFIG", config)
    return config


# ─── Active-doc stamp via helper (single source of truth) ─────────
async def test_prop_score_active_doc_has_full_lifecycle():
    """Calling stamp_active_board_doc on a synthetic score doc must
    produce every lifecycle field — same contract as cached_board."""
    doc = {
        "canonical_key": "tst|x|p|hits|0.5|OVER",
        "player_name": "p", "version_tag": "final-tst-rt",
        "vision_score": 95.0, "tier": "safe_haven",
    }
    bl.stamp_active_board_doc(doc)
    for f in bl.LIFECYCLE_FIELDS:
        assert f in doc, f"missing lifecycle field: {f}"
    assert doc["active"] is True
    assert doc["ttl_purge_at"] is None


# ─── Inactive stamp via helper ────────────────────────────────────
async def test_prop_score_inactive_doc_has_full_lifecycle():
    doc = {"canonical_key": "tst|x|p|hits|0.5|OVER", "active": True}
    bl.stamp_inactive_board_doc(doc, reason="orphan_test")
    assert doc["active"] is False
    assert doc["stale_reason"] == "orphan_test"
    assert isinstance(doc["ttl_purge_at"], datetime)
    assert isinstance(doc["stale_marked_at"], datetime)


# ─── Cleanup utility now stamps via helper (no duplication) ───────
async def test_ephemeral_cleanup_uses_lifecycle_helper(
    db, patched_cleanup_config,
):
    """End-to-end: mark_orphan_docs must stamp the same shape as
    stamp_inactive_board_doc() — verifies cleanup consumes the
    universal helper, not its own duplicated stamping logic."""
    await db["tst_live_props"].insert_many([
        {"canonical_key": "tst|x|live|hits|0.5|OVER"},
    ])
    await db["tst_prop_scores"].insert_many([
        {"canonical_key": "tst|x|live|hits|0.5|OVER", "active": True},
        {"canonical_key": "tst|x|orphan|hits|0.5|OVER", "active": True},
    ])
    await ec.mark_orphan_docs(db, sport="tst", dry_run=False)
    orphan = await db["tst_prop_scores"].find_one(
        {"canonical_key": "tst|x|orphan|hits|0.5|OVER"},
    )
    # Every lifecycle field present and consistent.
    for f in bl.LIFECYCLE_FIELDS:
        assert f in orphan
    assert orphan["active"] is False
    assert orphan["stale_reason"] == ec.STALE_REASON_ORPHAN
    assert isinstance(orphan["ttl_purge_at"], datetime)
    assert isinstance(orphan["stale_marked_at"], datetime)
    assert isinstance(orphan["updated_at"], datetime)
    # And the active doc is undisturbed.
    live = await db["tst_prop_scores"].find_one(
        {"canonical_key": "tst|x|live|hits|0.5|OVER"},
    )
    assert live["active"] is True


# ─── Restore path uses helper too ─────────────────────────────────
async def test_cleanup_restore_uses_active_helper(
    db, patched_cleanup_config,
):
    """When a previously-orphaned key returns to live_props, the
    restore path must use lifecycle_set_for_upsert() so every
    lifecycle field is reset, not just `active=True`."""
    purge = datetime.now(timezone.utc) + timedelta(hours=1)
    await db["tst_live_props"].insert_one(
        {"canonical_key": "tst|x|returned|hits|0.5|OVER"},
    )
    await db["tst_prop_scores"].insert_one({
        "canonical_key": "tst|x|returned|hits|0.5|OVER",
        "active": False,
        "ttl_purge_at": purge,
        "stale_reason": "orphan_missing_from_live_props",
        "stale_marked_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    })
    await ec.restore_active_docs(db, sport="tst", dry_run=False)
    restored = await db["tst_prop_scores"].find_one({})
    assert restored["active"] is True
    assert restored["ttl_purge_at"] is None
    assert restored["stale_reason"] is None
    assert restored["stale_marked_at"] is None
    # Verify `updated_at` was bumped to a new value.
    assert restored["updated_at"] is not None


# ─── Active-only audit filter (gate audit safety) ─────────────────
async def test_gate_audit_active_filter_excludes_orphans(db):
    """Simulate an MLB FL OVER reject audit. After filtering
    active=True only the current-slate orphan rejects appear."""
    await db["mlb_prop_scores"].insert_many([
        # Active reject on the current slate
        {"canonical_key": "k_active", "active": True,
         "routed_tier": "front_lines", "tier": "unqualified",
         "recommendation": "OVER"},
        # Stale orphan from yesterday's slate
        {"canonical_key": "k_orphan", "active": False,
         "stale_reason": "orphan_missing_from_live_props",
         "routed_tier": "front_lines", "tier": "unqualified",
         "recommendation": "OVER"},
    ])
    n_total = await db["mlb_prop_scores"].count_documents({
        "routed_tier": "front_lines", "tier": "unqualified",
        "recommendation": "OVER",
    })
    n_active = await db["mlb_prop_scores"].count_documents({
        "routed_tier": "front_lines", "tier": "unqualified",
        "recommendation": "OVER", "active": True,
    })
    assert n_total == 2
    assert n_active == 1


# ─── Normalize endpoint handles prop_scores collection ────────────
async def test_normalize_admin_handles_prop_scores(db):
    """The admin /normalize endpoint walks every collection from
    _board_collections(); after widening it must include
    *_prop_scores. Repair a malformed doc end-to-end."""
    await db["tst_prop_scores"].insert_one({
        "canonical_key": "k1", "active": True,
        # missing ttl_purge_at, stale_reason, stale_marked_at, updated_at
    })
    missing_filter = {"$or": [{f: {"$exists": False}}
                              for f in bl.LIFECYCLE_FIELDS]}
    n_before = await db["tst_prop_scores"].count_documents(missing_filter)
    assert n_before == 1
    # Real-run path
    async for d in db["tst_prop_scores"].find(missing_filter):
        patch = {f: d.get(f) for f in bl.LIFECYCLE_FIELDS if f in d}
        scratch = {**patch}
        bl.normalize_board_doc(scratch)
        update = {f: scratch[f] for f in bl.LIFECYCLE_FIELDS}
        await db["tst_prop_scores"].update_one(
            {"_id": d["_id"]}, {"$set": update},
        )
    n_after = await db["tst_prop_scores"].count_documents(missing_filter)
    assert n_after == 0
    fixed = await db["tst_prop_scores"].find_one({"canonical_key": "k1"})
    assert fixed["active"] is True
    assert fixed["ttl_purge_at"] is None
    assert fixed["stale_reason"] is None


# ─── Replay compatibility: historical docs aren't molested ────────
async def test_replay_docs_in_other_collections_untouched(
    db, patched_cleanup_config,
):
    """The config above only lists `tst_prop_scores`; any other
    collection (e.g. backtest replay results) must be ignored."""
    await db["tst_prop_scores"].insert_one(
        {"canonical_key": "live", "active": True},
    )
    await db["replay_outcomes"].insert_one(
        {"canonical_key": "historical", "active": True,
         "settled_at": datetime.now(timezone.utc)},
    )
    await ec.mark_orphan_docs(db, sport="tst", dry_run=False, force=True)
    # Replay collection untouched
    replay = await db["replay_outcomes"].find_one({})
    assert replay["active"] is True
    assert "ttl_purge_at" not in replay
