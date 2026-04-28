"""Tests for `services.sync_lock` cross-process advisory lock."""
from __future__ import annotations

import asyncio
import os
import sys

import pytest
import pytest_asyncio

sys.path.insert(0, "/app/backend")

from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

import motor.motor_asyncio  # noqa: E402

from services.sync_lock import (  # noqa: E402
    COLLECTION as LOCK_COLL,
    DEFAULT_TTL_SECONDS,  # noqa: F401
    acquire,
    ensure_indexes,
    is_locked,
    janitor_once,
    release,
    with_sync_lock,
)


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db():
    cli = motor.motor_asyncio.AsyncIOMotorClient(os.environ["MONGO_URL"])
    handle = cli[os.environ["DB_NAME"]]
    await ensure_indexes(handle)
    await handle[LOCK_COLL].delete_many({"lock_key": {"$regex": "^pytest:"}})
    yield handle
    await handle[LOCK_COLL].delete_many({"lock_key": {"$regex": "^pytest:"}})
    cli.close()


# -----------------------------------------------------------------------
# 1. concurrent sync test  — second acquire must skip cleanly
# -----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_second_acquire_skips_when_first_holds(db):
    h1 = await acquire(db, "pytest:k1", ttl_seconds=30, holder="pytest/A")
    h2 = await acquire(db, "pytest:k1", ttl_seconds=30, holder="pytest/B")
    try:
        assert h1 is not None
        assert h2 is None, "second acquire must return None"
        assert await is_locked(db, "pytest:k1") is True
    finally:
        await release(db, h1)
    assert await is_locked(db, "pytest:k1") is False


# -----------------------------------------------------------------------
# 2. stale lock auto-steal
# -----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_expired_lock_can_be_stolen(db):
    h1 = await acquire(db, "pytest:k2", ttl_seconds=1, holder="pytest/A")
    assert h1 is not None
    await asyncio.sleep(1.5)
    # Original holder's TTL expired — new acquire should succeed.
    h2 = await acquire(db, "pytest:k2", ttl_seconds=30, holder="pytest/B")
    try:
        assert h2 is not None
        assert h2.holder_id != h1.holder_id
    finally:
        await release(db, h2)


# -----------------------------------------------------------------------
# 3. release-only-if-owner (cas)
# -----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_release_does_not_remove_other_holders_lock(db):
    h1 = await acquire(db, "pytest:k3", ttl_seconds=1, holder="pytest/A")
    assert h1 is not None
    await asyncio.sleep(1.5)
    h2 = await acquire(db, "pytest:k3", ttl_seconds=30, holder="pytest/B")
    assert h2 is not None
    # h1 tries to release after the steal — must be a no-op.
    ok = await release(db, h1)
    assert ok is False, "stale holder must NOT remove the new lock"
    assert await is_locked(db, "pytest:k3") is True
    await release(db, h2)
    assert await is_locked(db, "pytest:k3") is False


# -----------------------------------------------------------------------
# 4. context-manager skip path (raise_if_locked=False)
# -----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_context_manager_yields_none_when_busy(db):
    h1 = await acquire(db, "pytest:k4", ttl_seconds=30, holder="pytest/A")
    try:
        async with with_sync_lock(
            db, "pytest:k4", ttl_seconds=30,
            holder="pytest/B", raise_if_locked=False,
        ) as handle:
            assert handle is None
    finally:
        await release(db, h1)


# -----------------------------------------------------------------------
# 5. context-manager raises by default
# -----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_context_manager_raises_when_busy(db):
    h1 = await acquire(db, "pytest:k5", ttl_seconds=30, holder="pytest/A")
    try:
        with pytest.raises(RuntimeError, match="sync_lock busy"):
            async with with_sync_lock(
                db, "pytest:k5", ttl_seconds=30, holder="pytest/B",
            ):
                pass
    finally:
        await release(db, h1)


# -----------------------------------------------------------------------
# 6. janitor purges expired
# -----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_janitor_removes_expired_locks(db):
    for i in range(3):
        await acquire(db, f"pytest:k6_{i}", ttl_seconds=1, holder="pytest/A")
    await asyncio.sleep(1.5)
    n = await janitor_once(db)
    assert n >= 3
