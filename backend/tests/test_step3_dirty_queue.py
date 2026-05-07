"""
Step 3 contract: dirty-queue replaces watermark detection
=========================================================

Three race scenarios from the stabilization spec — each MUST be
caught by the dirty queue when the timestamp watermark would have
silently dropped the writes.

Scenarios:
  1. Late commit          — `updated_at` stamped at T0, commit lands at
                            T0 + 90s+. Watermark advances past T0
                            before commit visible → writes lost. Queue:
                            ObjectId assigned at commit, > previous
                            watermark → picked up.

  2. Backend restart      — queue rows persist across restart. After
                            restart, detector resumes from `_id > 0`
                            and drains the queue, regardless of
                            watermark state.

  3. Burst ingestion      — 5,000+ rows enqueued in one ingestion
                            tick. Detector caps at `batch_limit`,
                            confirms only the slice it rescored, the
                            remainder stays queued for the next tick
                            → no loss.

Plus three structural contracts:
  4. AdvanceWatermarkStep no longer drives detection (purely
     diagnostic; no SAFE_LAG / no `tick_started_at` filtering).
  5. universal_odds_sync calls `enqueue_dirty()` on every successful
     batch (static parse).
  6. drain_dirty dedupes canonical_keys but returns ALL queue_ids
     (so confirm_processed deletes every duplicate).
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, "/app/backend")

# Load env so motor + Mongo work for the live integration tests.
_ENV = Path("/app/backend/.env")
if _ENV.exists():
    for ln in _ENV.read_text().splitlines():
        if "=" in ln and not ln.strip().startswith("#"):
            k, _, v = ln.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


# ── Shared fixture: live Mongo db, isolated test queue ───────────────
@pytest_asyncio.fixture
async def db_with_isolated_queue(monkeypatch):
    """Live Mongo + a per-test sport name so the queue is isolated.
    Yields (db, sport). Cleans up the test rows after the test."""
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.delta import dirty_queue as dq

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    test_sport = f"_test_step3_{os.getpid()}_{int(datetime.now().timestamp() * 1000)}"
    try:
        await dq.ensure_indexes(db)
        yield db, test_sport
    finally:
        await db[dq.DIRTY_QUEUE_COLLECTION].delete_many({"sport": test_sport})
        cli.close()


# ── Contract 1: late commit cannot be skipped ────────────────────────
@pytest.mark.asyncio
async def test_late_commit_is_picked_up_via_monotonic_id(db_with_isolated_queue):
    """The single bug we are eliminating: a late-arriving write whose
    `updated_at` predates the watermark must STILL be detected. The
    queue achieves this by ordering on `_id` (assigned at commit
    time), not on `updated_at`."""
    from services.delta.dirty_queue import (
        confirm_processed, drain_dirty, enqueue_dirty,
    )
    db, sport = db_with_isolated_queue

    # Tick 1: an "early" write enqueues 3 keys.
    await enqueue_dirty(db, ["k_early_1", "k_early_2", "k_early_3"],
                        sport=sport, reason="ingestion")
    keys, ids = await drain_dirty(db, sport=sport, batch_limit=100)
    assert set(keys) == {"k_early_1", "k_early_2", "k_early_3"}
    await confirm_processed(db, ids)

    # Simulate the racy "late commit": a row whose `updated_at` is
    # antedated by 5 minutes (well past any reasonable watermark
    # grace) but whose `_id` is fresh because it was just inserted.
    # In the watermark world, this row was lost forever. In the queue
    # world, it gets a NEW ObjectId greater than every prior one.
    await enqueue_dirty(db, ["k_late_commit"], sport=sport, reason="ingestion")
    # Manually antedate `enqueued_at` so the test mirrors the real
    # production race (the queue does NOT consult enqueued_at — only
    # _id).
    await db["delta_dirty_queue"].update_one(
        {"sport": sport, "canonical_key": "k_late_commit"},
        {"$set": {"enqueued_at": datetime.now(timezone.utc) - timedelta(minutes=5)}},
    )

    # Tick 2: drain again. The late-commit row MUST appear.
    keys, ids = await drain_dirty(db, sport=sport, batch_limit=100)
    assert "k_late_commit" in keys, (
        "RACE: late-committed row was not picked up by drain_dirty. "
        "This is the exact watermark bug we are replacing."
    )
    await confirm_processed(db, ids)


# ── Contract 2: backend restart → queue survives ────────────────────
@pytest.mark.asyncio
async def test_queue_survives_simulated_restart(db_with_isolated_queue):
    """Enqueue rows, simulate a restart by closing+reopening the
    client, verify rows are still drainable."""
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.delta.dirty_queue import (
        DIRTY_QUEUE_COLLECTION, drain_dirty, enqueue_dirty,
    )

    db_a, sport = db_with_isolated_queue
    await enqueue_dirty(db_a, [f"k_persist_{i}" for i in range(20)],
                        sport=sport, reason="ingestion")

    # "Restart": new motor client, fresh connection pool.
    cli_b = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db_b = cli_b[os.environ["DB_NAME"]]
    try:
        keys, ids = await drain_dirty(db_b, sport=sport, batch_limit=100)
        assert len(keys) == 20, (
            f"queue did not survive restart: drained {len(keys)} keys, "
            f"expected 20"
        )
    finally:
        await db_b[DIRTY_QUEUE_COLLECTION].delete_many({"sport": sport})
        cli_b.close()


# ── Contract 3: burst ingestion drains across multiple ticks ────────
@pytest.mark.asyncio
async def test_burst_ingestion_no_loss_across_capped_ticks(db_with_isolated_queue):
    """Enqueue 5,000 keys; drain in batches of 1,000 with confirm
    after each. After 5 ticks every key MUST have been seen exactly
    once and the queue MUST be empty."""
    from services.delta.dirty_queue import (
        confirm_processed, drain_dirty, enqueue_dirty, queue_depth,
    )

    db, sport = db_with_isolated_queue
    BURST = 5000
    BATCH = 1000

    keys_in = [f"k_burst_{i:05d}" for i in range(BURST)]
    n_enq = await enqueue_dirty(db, keys_in, sport=sport, reason="ingestion")
    assert n_enq == BURST

    seen: set[str] = set()
    drain_ticks_with_data = 0
    safety = 0
    while True:
        safety += 1
        keys, ids = await drain_dirty(db, sport=sport, batch_limit=BATCH)
        if not keys:
            break
        drain_ticks_with_data += 1
        # Process: in real life this is `recompute_sport(...)`. Here
        # we just record what we saw.
        seen.update(keys)
        # Confirm-and-delete: the rescore-step contract.
        await confirm_processed(db, ids)
        if safety > 20:  # brake — should be 5 ticks of work + 1 empty
            pytest.fail("burst drain exceeded 20 iterations — possible infinite loop")

    assert seen == set(keys_in), (
        f"burst drain lost keys: got {len(seen)}/{BURST} unique"
    )
    assert drain_ticks_with_data == BURST // BATCH, (
        f"expected exactly {BURST // BATCH} non-empty drain ticks, "
        f"got {drain_ticks_with_data}"
    )
    final_depth = await queue_depth(db, sport=sport)
    assert final_depth == 0, (
        f"queue not empty after drain: {final_depth} rows remain"
    )


# ── Contract 4: AdvanceWatermarkStep no longer drives detection ─────
def test_advance_watermark_step_is_diagnostic_only():
    """The watermark step's metrics MUST advertise `deprecated_for_detection`
    so any external observer treats `last_tick_utc` as informational."""
    from services.pipeline.delta_steps import AdvanceWatermarkStep
    step = AdvanceWatermarkStep()
    # The class no longer carries SAFE_LAG_SECONDS; tick_started_at is
    # not consulted; the metric is a deprecation flag.
    assert not hasattr(step, "SAFE_LAG_SECONDS"), (
        "AdvanceWatermarkStep still has SAFE_LAG_SECONDS — that was a "
        "watermark-era bandage; the queue eliminates the race so the "
        "cap should be gone."
    )

    # Inspect the source of the run() method to confirm it does not
    # consult `tick_started_at` or any timestamp filter for detection.
    import inspect
    src = inspect.getsource(step.run)
    assert "context.get(\"tick_started_at\")" not in src, (
        "AdvanceWatermarkStep still reads tick_started_at — must be "
        "purely informational stamping `now()`."
    )
    assert "deprecated_for_detection" in src
    assert "delta_dirty_queue" in src


# ── Contract 5: universal_odds_sync enqueues on every batch ─────────
def test_universal_odds_sync_calls_enqueue_dirty():
    """Static parse: the live_props writer MUST invoke
    `enqueue_dirty(...)` after `insert_many(clean_props)`. Without
    this, the queue stays empty and nothing rescores."""
    text = Path("/app/backend/services/universal_odds_sync.py").read_text()
    # Find the section between insert_many(clean_props) and the next
    # logger.info to confirm enqueue_dirty is reached on the success
    # path.
    m = re.search(
        r"insert_many\(clean_props\).*?dirty_queue.*?enqueue_dirty\(",
        text,
        flags=re.DOTALL,
    )
    assert m is not None, (
        "universal_odds_sync.py: enqueue_dirty(...) is missing or not "
        "in the same code block as insert_many(clean_props). The "
        "ingestion writer is the dirty-queue producer; without this "
        "call, the detector finds nothing to rescore."
    )


# ── Contract 6: drain_dirty dedupes keys but returns all queue_ids ──
@pytest.mark.asyncio
async def test_drain_dedupes_keys_but_returns_all_queue_ids(db_with_isolated_queue):
    """If the same canonical_key is enqueued 3 times in one tick (e.g.
    upstream sends a double batch), drain returns the key ONCE but
    must return ALL 3 queue_ids so confirm_processed can delete all of
    them. Otherwise duplicates accumulate forever."""
    from services.delta.dirty_queue import (
        confirm_processed, drain_dirty, enqueue_dirty, queue_depth,
    )

    db, sport = db_with_isolated_queue
    # 3 enqueues of "k_dup" + 1 of "k_unique"
    await enqueue_dirty(db, ["k_dup", "k_dup", "k_dup", "k_unique"],
                        sport=sport, reason="ingestion")
    keys, ids = await drain_dirty(db, sport=sport, batch_limit=100)

    assert sorted(keys) == ["k_dup", "k_unique"]
    assert len(ids) == 4, (
        f"drain returned {len(ids)} queue_ids; expected 4 (one per "
        f"queue row, regardless of canonical_key dedup)"
    )

    deleted = await confirm_processed(db, ids)
    assert deleted == 4
    assert await queue_depth(db, sport=sport) == 0
