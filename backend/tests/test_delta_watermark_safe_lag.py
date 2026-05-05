"""
Delta-watermark race contract
=============================

Pins the AdvanceWatermarkStep behaviour fixed on 2026-05-05:

  * The watermark MUST trail real time by at least `SAFE_LAG_SECONDS`,
    so a late-arriving upstream write whose `updated_at` was stamped
    inside the safe-lag window is still visible to the next detect
    tick.

  * The cap MUST be enforced even when `tick_started_at` is in the
    "now" frame — i.e. the watermark NEVER advances to `now()` with
    less than `SAFE_LAG` seconds of lag.

  * If `tick_started_at` is older than `now − SAFE_LAG` (i.e. the
    tick took a long time to run), the watermark advances to
    `tick_started_at` (smaller of the two — preserves correctness
    when a tick falls behind).

  * The step is a no-op when `abort_remaining_steps` is set
    (upstream-lock guard preserved).

Without this trailing window, MLB tiers froze for >2 hours because
upstream sync was bulk-committing 13,930 rows stamped `updated_at=T0`
while the detect tick that ran at `T0 + 1s` had already advanced the
watermark to `T0 + 1s`, permanently masking the writes from every
subsequent detect query.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone

import pytest


sys.path.insert(0, "/app/backend")


# ── In-memory `delta_watermarks` collection stub ─────────────────────
class _StubColl:
    def __init__(self):
        self.docs: dict = {}

    async def update_one(self, query, update, upsert=False):
        _id = query["_id"]
        existing = self.docs.get(_id, {"_id": _id})
        for op, fields in update.items():
            if op == "$set":
                existing.update(fields)
            elif op == "$setOnInsert" and _id not in self.docs:
                existing.update(fields)
        self.docs[_id] = existing

    async def find_one(self, query, projection=None):
        return self.docs.get(query["_id"])


class _StubDB:
    def __init__(self):
        self._coll = _StubColl()

    def __getitem__(self, _name):
        return self._coll


# ── Shared fixture ───────────────────────────────────────────────────
@pytest.fixture
def step_and_db():
    from services.pipeline.delta_steps import AdvanceWatermarkStep
    return AdvanceWatermarkStep(), _StubDB()


# ── Contract 1 ───────────────────────────────────────────────────────
def test_watermark_trails_now_by_at_least_safe_lag(step_and_db):
    """Even when `tick_started_at` is `now()`, the committed watermark
    must be `now − SAFE_LAG` (or earlier). Without this, late-arriving
    upstream writes are masked."""
    step, db = step_and_db
    now = datetime.now(timezone.utc)
    context = {"tick_started_at": now}

    metrics = asyncio.run(step.run("mlb", db, context))

    advanced_to = datetime.fromisoformat(metrics["advanced_to"])
    assert (now - advanced_to).total_seconds() >= step.SAFE_LAG_SECONDS - 1, (
        f"watermark advanced to {advanced_to}, only "
        f"{(now - advanced_to).total_seconds():.1f}s behind now() — "
        f"expected at least {step.SAFE_LAG_SECONDS}s safe lag."
    )
    assert metrics["capped_by_safe_lag"] is True


# ── Contract 2 ───────────────────────────────────────────────────────
def test_late_committed_write_is_visible_to_next_tick(step_and_db):
    """Simulate the production failure mode (MLB stuck-tier scenario):

      T0 = 17:56:16  upstream stamps 13,930 rows with updated_at=T0
      T1 = 17:56:30  detect tick runs (commit not yet visible)
                     pre-fix:  watermark advances to 17:56:30
                     post-fix: watermark advances to 17:56:30 only if
                               that's < now()-SAFE_LAG; else capped
      T2 = 17:57:00  detect tick runs.
                     pre-fix:  query `updated_at > 17:56:25` → MISS
                     post-fix: query `updated_at > T1 − SAFE_LAG`
                               → captures the T0 writes
    """
    step, db = step_and_db
    # Simulate "tick T1 runs at T_now, took 1ms, immediately advances".
    t1_started = datetime.now(timezone.utc)
    asyncio.run(step.run("mlb", db, {"tick_started_at": t1_started}))

    stored = db._coll.docs["mlb"]["last_tick_utc"]
    # The writes are stamped 30 seconds before T1 — well within
    # SAFE_LAG_SECONDS. They must remain visible (i.e. the watermark
    # is BEFORE the write timestamp).
    upstream_write_stamp = t1_started - timedelta(seconds=30)
    assert stored < upstream_write_stamp, (
        f"watermark={stored} is AHEAD of an upstream write at "
        f"{upstream_write_stamp} that committed within SAFE_LAG. The "
        f"write would be lost forever — this is the bug we patched."
    )


# ── Contract 3 ───────────────────────────────────────────────────────
def test_tick_started_at_in_the_past_advances_normally(step_and_db):
    """If `tick_started_at` is OLDER than `now − SAFE_LAG` (slow tick),
    the watermark advances to `tick_started_at`. Don't OVERSHOOT — the
    cap is a ceiling, not a floor."""
    step, db = step_and_db
    # Simulate a tick that started 5 minutes ago (took a long time).
    five_min_ago = datetime.now(timezone.utc) - timedelta(minutes=5)
    metrics = asyncio.run(step.run("nba", db, {"tick_started_at": five_min_ago}))

    advanced_to = datetime.fromisoformat(metrics["advanced_to"])
    # min(five_min_ago, now-90s) = five_min_ago.
    assert abs((advanced_to - five_min_ago).total_seconds()) < 1, (
        f"watermark advanced to {advanced_to}; expected ≈ {five_min_ago} "
        f"(tick_started_at is older than SAFE_LAG ceiling)."
    )
    assert metrics["capped_by_safe_lag"] is False


# ── Contract 4 ───────────────────────────────────────────────────────
def test_step_is_noop_when_abort_remaining_steps_set(step_and_db):
    """Upstream-lock guard MUST still short-circuit the step — preserves
    the contract that a full sync in flight pauses the delta engine."""
    step, db = step_and_db
    metrics = asyncio.run(step.run("mlb", db, {"abort_remaining_steps": True}))

    assert metrics == {"skipped": True, "reason": "upstream_lock_held"}
    assert "mlb" not in db._coll.docs, (
        "watermark mutated even though abort_remaining_steps was set"
    )


# ── Contract 5 ───────────────────────────────────────────────────────
def test_safe_lag_seconds_is_at_least_30_seconds(step_and_db):
    """Floor on the configurable safe-lag. Anything < 30s leaves us
    vulnerable to the original race (we observed ~90s commit lag in
    production for 10K-row bulk upserts)."""
    step, _db = step_and_db
    assert step.SAFE_LAG_SECONDS >= 30, (
        f"SAFE_LAG_SECONDS={step.SAFE_LAG_SECONDS} is too small. "
        f"Production bulk-write commit latency for 10K+ rows can "
        f"reach 60-90s; the floor here protects against regressions."
    )
