"""Unit tests for services/cleanup/ephemeral_cleanup.py.

Uses mongomock-motor for in-memory MongoDB; isolates each test in its
own DB.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from datetime import datetime, timezone

from services.cleanup import ephemeral_cleanup as ec
from services.cleanup import ephemeral_collections as eco


pytestmark = pytest.mark.asyncio


# ─── Fixtures ──────────────────────────────────────────────────────
@pytest.fixture
def patched_config(monkeypatch):
    """Temporary config block with a fake 'tst' sport."""
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
    # ephemeral_cleanup module imported the name — patch there too.
    monkeypatch.setattr(ec, "EPHEMERAL_CLEANUP_CONFIG", config)
    return config


@pytest_asyncio.fixture
async def db():
    from mongomock_motor import AsyncMongoMockClient
    client = AsyncMongoMockClient()
    return client["test_ephemeral_cleanup"]


# ─── Tests ─────────────────────────────────────────────────────────
async def test_ensure_ttl_indexes_creates_index(db, patched_config):
    res = await ec.ensure_ttl_indexes(db, sport="tst")
    assert any(
        r["collection"] == "tst_prop_scores" and r["status"] == "ensured"
        for r in res["results"]
    )


async def test_mark_orphans_skips_when_live_empty(db, patched_config):
    """Safety: refuse to mark all docs stale during ingest outage."""
    await db["tst_prop_scores"].insert_many([
        {"canonical_key": "tst|x|p1|hits|0.5|OVER", "active": True},
    ])
    res = await ec.mark_orphan_docs(db, sport="tst", dry_run=False)
    assert res["status"] == "aborted_live_empty"
    # Doc is still active
    doc = await db["tst_prop_scores"].find_one({})
    assert doc["active"] is True


async def test_force_overrides_live_empty_abort(db, patched_config):
    await db["tst_prop_scores"].insert_many([
        {"canonical_key": "tst|x|p1|hits|0.5|OVER", "active": True},
    ])
    res = await ec.mark_orphan_docs(
        db, sport="tst", dry_run=False, force=True,
    )
    assert "collections" in res
    # All docs were orphan since live is empty + force ignored guard
    doc = await db["tst_prop_scores"].find_one({})
    assert doc["active"] is False
    assert doc["ttl_purge_at"] is not None
    assert doc["stale_reason"] == ec.STALE_REASON_ORPHAN


async def test_mark_and_restore_lifecycle(db, patched_config):
    """End-to-end: orphan marked → reappears on slate → restored."""
    await db["tst_live_props"].insert_many([
        {"canonical_key": "tst|x|p1|hits|0.5|OVER"},
    ])
    await db["tst_prop_scores"].insert_many([
        {"canonical_key": "tst|x|p1|hits|0.5|OVER", "active": True},
        {"canonical_key": "tst|x|stale|hits|0.5|OVER", "active": True},
    ])
    # First mark: stale doc gets inactivated
    res = await ec.mark_orphan_docs(db, sport="tst", dry_run=False)
    fresh = await db["tst_prop_scores"].find_one(
        {"canonical_key": "tst|x|p1|hits|0.5|OVER"},
    )
    stale = await db["tst_prop_scores"].find_one(
        {"canonical_key": "tst|x|stale|hits|0.5|OVER"},
    )
    assert fresh["active"] is True
    assert stale["active"] is False
    assert stale["ttl_purge_at"] is not None
    assert isinstance(stale["ttl_purge_at"], datetime)
    # mongomock strips tzinfo on round-trip; compare as naive.
    purge_naive = stale["ttl_purge_at"].replace(tzinfo=None)
    assert purge_naive > datetime.now()

    # Stale doc's canonical_key reappears on the slate.
    await db["tst_live_props"].insert_one(
        {"canonical_key": "tst|x|stale|hits|0.5|OVER"},
    )
    await ec.restore_active_docs(db, sport="tst", dry_run=False)
    restored = await db["tst_prop_scores"].find_one(
        {"canonical_key": "tst|x|stale|hits|0.5|OVER"},
    )
    assert restored["active"] is True
    assert restored["ttl_purge_at"] is None
    assert restored["stale_reason"] is None


async def test_dry_run_does_not_mutate(db, patched_config):
    await db["tst_live_props"].insert_many([
        {"canonical_key": "tst|x|p1|hits|0.5|OVER"},
    ])
    await db["tst_prop_scores"].insert_many([
        {"canonical_key": "tst|x|stale|hits|0.5|OVER", "active": True},
    ])
    res = await ec.mark_orphan_docs(db, sport="tst", dry_run=True)
    doc = await db["tst_prop_scores"].find_one({})
    # Untouched
    assert doc["active"] is True
    assert doc.get("ttl_purge_at") is None
    # But report indicates it WOULD be marked
    coll_report = res["collections"][0]
    assert coll_report["would_mark_inactive"] == 1
    assert coll_report["applied"] is False


async def test_protected_collection_rejected(monkeypatch):
    """Adding a protected collection to config must raise at iter time."""
    bad_config = {
        "tst": {
            "enabled": True,
            "live_collection": "tst_live_props",
            "canonical_key_field": "canonical_key",
            "grace_hours": 24,
            "collections": ["mlb_outcomes"],  # PROTECTED
        },
    }
    monkeypatch.setattr(eco, "EPHEMERAL_CLEANUP_CONFIG", bad_config)
    with pytest.raises(RuntimeError, match="protected collection"):
        list(eco.iter_collections("tst"))


async def test_status_report_shape(db, patched_config):
    await db["tst_live_props"].insert_one(
        {"canonical_key": "tst|x|p1|hits|0.5|OVER"},
    )
    await db["tst_prop_scores"].insert_many([
        {"canonical_key": "tst|x|p1|hits|0.5|OVER", "active": True},
        {"canonical_key": "tst|x|stale|hits|0.5|OVER", "active": False,
         "ttl_purge_at": datetime.now(timezone.utc)},
    ])
    rep = await ec.status_report(db, sport="tst")
    info = rep["sports"]["tst"]
    assert info["enabled"] is True
    assert info["live_canonical_keys"] == 1
    assert info["collections"][0]["total"] == 2
    assert info["collections"][0]["active"] == 1
    assert info["collections"][0]["inactive"] == 1
    assert info["collections"][0]["pending_purge"] == 1
