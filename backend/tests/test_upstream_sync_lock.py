"""
D4 — UpstreamSyncLock behaviour tests.

Verifies:
  1. Fresh lock reports not-held for any sport.
  2. `exclusive(sport)` blocks `try_acquire_tick(sport)` while held.
  3. `exclusive(sport)` does NOT block `try_acquire_tick(other_sport)`.
  4. Lock is released after the context manager exits, even on error.
  5. `describe()` returns holder + acquired_at while held.
"""
import asyncio
import pytest

from services.upstream_sync_lock import UpstreamSyncLock


@pytest.mark.asyncio
async def test_fresh_lock_not_held_anywhere():
    lock = UpstreamSyncLock()
    assert lock.try_acquire_tick("nba") is True
    assert lock.try_acquire_tick("mlb") is True
    assert lock.is_held("nba") is False


@pytest.mark.asyncio
async def test_exclusive_blocks_same_sport_tick():
    lock = UpstreamSyncLock()

    async with lock.exclusive("nba", holder="master_sync:test"):
        assert lock.try_acquire_tick("nba") is False
        assert lock.is_held("nba") is True
        state = lock.describe("nba")
        assert state["held"] is True
        assert state["holder"] == "master_sync:test"
        assert state["acquired_at"] is not None

    # released after context exit
    assert lock.try_acquire_tick("nba") is True
    assert lock.is_held("nba") is False


@pytest.mark.asyncio
async def test_exclusive_on_sport_does_not_block_other_sport():
    lock = UpstreamSyncLock()

    async with lock.exclusive("nba"):
        assert lock.try_acquire_tick("nba") is False
        # Cross-sport must still be ackable.
        assert lock.try_acquire_tick("mlb") is True


@pytest.mark.asyncio
async def test_lock_released_on_exception():
    lock = UpstreamSyncLock()

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with lock.exclusive("mlb"):
            assert lock.is_held("mlb") is True
            raise Boom()

    # Must be released despite the exception.
    assert lock.is_held("mlb") is False
    assert lock.try_acquire_tick("mlb") is True


@pytest.mark.asyncio
async def test_exclusive_acquires_serially():
    """Second exclusive() call waits for the first to release."""
    lock = UpstreamSyncLock()
    order = []

    async def holder_a():
        async with lock.exclusive("nba", holder="A"):
            order.append("A-acquired")
            await asyncio.sleep(0.05)
            order.append("A-releasing")

    async def holder_b():
        # tiny delay so A enters first
        await asyncio.sleep(0.01)
        async with lock.exclusive("nba", holder="B"):
            order.append("B-acquired")

    await asyncio.gather(holder_a(), holder_b())
    assert order == ["A-acquired", "A-releasing", "B-acquired"]
