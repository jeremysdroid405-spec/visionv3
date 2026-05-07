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


# ── Contract 4: AdvanceWatermarkStep is removed entirely ────────────
def test_advance_watermark_step_is_removed():
    """2026-05-07 P0-A SSOT cleanup: `AdvanceWatermarkStep` and the
    `delta_watermarks` collection have been deleted. The dirty queue
    is the single detection source — no observability shim remains.
    """
    from services.pipeline import delta_steps
    assert not hasattr(delta_steps, "AdvanceWatermarkStep"), (
        "AdvanceWatermarkStep was removed — re-introducing it would "
        "restore a second 'when did engine last tick' source and "
        "violate the 'one detection system only' rule."
    )
    # `__all__` must not advertise it either.
    assert "AdvanceWatermarkStep" not in delta_steps.__all__
    # And the default chain must not contain a watermark-named step.
    step_names = [s.name for s in delta_steps.DEFAULT_DELTA_STEPS]
    assert not any("watermark" in n for n in step_names), step_names


def test_delta_watermarks_module_is_deleted():
    """The `services/delta_watermarks.py` module has been removed.
    No production code may import from it."""
    from pathlib import Path
    assert not Path(
        "/app/backend/services/delta_watermarks.py"
    ).exists(), "delta_watermarks module must remain deleted"
    import importlib
    try:
        importlib.import_module("services.delta_watermarks")
    except ModuleNotFoundError:
        pass
    else:
        raise AssertionError(
            "services.delta_watermarks unexpectedly importable"
        )


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


# ── Contract 7: RescoreDirtyPropsStep deletes ALL drained ids ────────
@pytest.mark.asyncio
async def test_rescore_step_confirms_all_drained_regardless_of_match(
    db_with_isolated_queue, monkeypatch,
):
    """2026-05-07 P0-A leak fix.

    Pre-fix bug: `RescoreDirtyPropsStep` proportionally trimmed the
    confirm list when `batch_capped=True`, AND silently retained queue
    rows for keys that `coverage_filter` dropped. Both modes leaked
    ~94% of queued rows, causing the queue to grow unbounded and the
    same low-`_id` rows to be re-drained every tick.

    Contract: after the rescore step runs, ALL drained queue_ids are
    deleted regardless of (a) whether the cap kicked in or
    (b) whether `recompute_sport` matched the keys.
    """
    from services.pipeline.delta_steps import RescoreDirtyPropsStep
    from services.delta.dirty_queue import (
        enqueue_dirty, queue_depth, DIRTY_QUEUE_COLLECTION,
    )
    from services.delta.detector import DeltaDetectionResult
    import services.pipeline.delta_steps as ds_mod

    db, sport = db_with_isolated_queue

    # Seed the queue with 100 keys.
    seeded_keys = [f"sport|evt|player_{i}|PTS|10.5|OVER" for i in range(100)]
    await enqueue_dirty(db, seeded_keys, sport=sport, reason="ingestion")
    assert await queue_depth(db, sport=sport) == 100

    # Hand-fetch the queue_ids in stable order so the test mirrors what
    # `drain_dirty` returns on production.
    raw = await db[DIRTY_QUEUE_COLLECTION].find(
        {"sport": sport}, {"_id": 1, "canonical_key": 1},
    ).sort([("_id", 1)]).to_list(None)
    drained_ids = [d["_id"] for d in raw]
    assert len(drained_ids) == 100

    # Stub `recompute_sport` to simulate the worst case: ZERO keys
    # match the live_props post-filter (all the queued keys are
    # retired / not playable). Pre-fix, this caused 100 rows to leak.
    async def _stub_recompute(db, sport, version_tag, **kw):
        return {
            "processed": 0, "written": 0, "skipped": 0, "replaced": 0,
            "collection": f"{sport}_prop_scores",
            "version_tag": version_tag,
            "only_canonical_keys_matched": 0,
        }
    monkeypatch.setattr(ds_mod, "recompute_sport", _stub_recompute)

    # Build a detection result the step expects.
    det = DeltaDetectionResult(sport=sport)
    det.updated_keys = set(seeded_keys[:60])
    det.new_keys = set(seeded_keys[60:])
    det.dirty_keys = det.updated_keys | det.new_keys
    det.drained_queue_ids = drained_ids
    context: dict = {"detection": det, "rescore_batch_cap": 10}

    step = RescoreDirtyPropsStep()
    result = await step.run(sport, db, context)

    # Cap kicked in: keys_requested=10, but ALL 100 drained_ids must
    # have been confirmed.
    assert result["batch_capped"] is True, result
    assert result["queue_ids_confirmed"] == 100, (
        f"leak fix regression: confirmed {result['queue_ids_confirmed']} / "
        f"100 drained_ids. Pre-fix value was ~10 due to proportional "
        f"trim. The contract is: confirm-all once rescore returns."
    )
    final_depth = await queue_depth(db, sport=sport)
    assert final_depth == 0, (
        f"queue still has {final_depth} rows after confirm-all. The "
        f"step is leaking rows again."
    )


@pytest.mark.asyncio
async def test_rescore_step_confirms_all_when_zero_match(
    db_with_isolated_queue, monkeypatch,
):
    """A more direct restatement: when `coverage_filter` rejects 100% of
    the drained keys (matched=0), ALL drained_ids are still deleted.
    Pre-fix, this scenario silently retained every row forever."""
    from services.pipeline.delta_steps import RescoreDirtyPropsStep
    from services.delta.dirty_queue import (
        enqueue_dirty, queue_depth, DIRTY_QUEUE_COLLECTION,
    )
    from services.delta.detector import DeltaDetectionResult
    import services.pipeline.delta_steps as ds_mod

    db, sport = db_with_isolated_queue

    keys = [f"sport|stale|p_{i}|PTS|0.5|OVER" for i in range(50)]
    await enqueue_dirty(db, keys, sport=sport, reason="ingestion")
    raw = await db[DIRTY_QUEUE_COLLECTION].find(
        {"sport": sport}, {"_id": 1},
    ).sort([("_id", 1)]).to_list(None)
    drained_ids = [d["_id"] for d in raw]

    async def _stub_zero_match(db, sport, version_tag, **kw):
        return {
            "processed": 0, "written": 0, "skipped": 0,
            "only_canonical_keys_matched": 0,
        }
    monkeypatch.setattr(ds_mod, "recompute_sport", _stub_zero_match)

    det = DeltaDetectionResult(sport=sport)
    det.updated_keys = set(keys)
    det.dirty_keys = set(keys)
    det.drained_queue_ids = drained_ids
    context: dict = {"detection": det}  # no batch cap → all 50 requested

    step = RescoreDirtyPropsStep()
    result = await step.run(sport, db, context)

    assert result["batch_capped"] is False
    assert result["written"] == 0
    assert result["queue_ids_confirmed"] == 50
    assert await queue_depth(db, sport=sport) == 0
