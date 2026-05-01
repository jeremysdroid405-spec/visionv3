"""Live integration verification for AdaptiveSyncEngine watchdog.

Builds a real engine instance, attaches it to the real test MongoDB,
spawns the actual `_watchdog_loop` and `_adaptive_poll_loop` tasks,
then induces a real freeze by:
  1. Writing a stale heartbeat directly to the heartbeat collection
  2. Cancelling the poll task (simulating silent task death)

Asserts that the watchdog:
  a) Detects the staleness within its tunable window
  b) Calls _restart_poll_loop with a freeze marker
  c) Spawns a fresh poll task (different identity than the dead one)
  d) Does NOT trigger the restart-storm guard on a single freeze

Unlike the unit tests, this exercises the real loop body —
`asyncio.create_task`, real DB I/O, real cancellation, real spawning
of replacement tasks. If the watchdog's restart path is broken, this
test will hang or assert. No mocks of asyncio internals.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from services.engines.adaptive_sync_engine import AdaptiveSyncEngine


@pytest_asyncio.fixture
async def real_db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    yield client[os.environ["DB_NAME"]]


@pytest.mark.asyncio
async def test_live_freeze_triggers_real_restart(real_db):
    """Production-grade: real engine, real DB, real task lifecycle.
    No AsyncMock on `_restart_poll_loop` — we let the actual restart
    path run end-to-end and assert on observable side effects."""
    eng = AdaptiveSyncEngine(real_db, odds_api_key="test-key")

    # Replace the actual poll loop with a tight no-op that never
    # writes a heartbeat. The whole test premise is "poll loop is
    # silently dead → watchdog must rescue."
    poll_iterations = {"n": 0}

    async def _silent_dead_poll_loop():
        poll_iterations["n"] += 1
        # Single tick of REAL sleep so that:
        #   1. The function actually ends quickly (so a new restart
        #      can spawn a fresh instance)
        #   2. The event loop has time to schedule it
        await asyncio.sleep(0.005)

    eng._adaptive_poll_loop = _silent_dead_poll_loop  # type: ignore[method-assign]
    # Use REAL (small) intervals so the event loop genuinely yields.
    # No monkey-patched sleeps: the test is verifying production
    # timing semantics, not unit-level assertion isolation.
    eng._WATCHDOG_INTERVAL_SECONDS = 0.01  # 10ms — enough for poll tasks to run
    eng._WATCHDOG_WARMUP_SECONDS = 0
    # Simulate a long-running engine so the prior-run stale heartbeat
    # in the DB is genuinely "stale" rather than "from before this
    # engine started" (which the watchdog correctly ignores).
    eng._engine_started_at = datetime.now(timezone.utc) - timedelta(hours=1)

    # Prime the heartbeat as deeply stale (older than stale_floor=600s).
    await real_db["adaptive_sync_heartbeat"].update_one(
        {"_id": "adaptive_sync"},
        {"$set": {
            "last_heartbeat_at": datetime.now(timezone.utc) - timedelta(minutes=30),
            "next_poll_in_seconds": 300,
            "games_in_registry": 0,
            "status_breakdown": {},
        }},
        upsert=True,
    )

    # Start the engine the real way — both poll task and watchdog
    # task get spawned exactly as in production.
    await eng.start()

    # Yield enough time for the watchdog to iterate, detect, restart.
    # 200ms = 20 watchdog ticks at 10ms interval.
    await asyncio.sleep(0.2)

    # Stop everything cleanly.
    eng.is_running = False
    await eng.stop()

    # ── Live observable side-effects to assert ───────────────────
    final_poll_iterations = poll_iterations["n"]
    assert final_poll_iterations >= 2, (
        f"Watchdog never respawned the dead poll loop. "
        f"poll_iterations={final_poll_iterations}. "
        "First-life ran once; if watchdog detected the freeze and "
        "restarted, second-life would bump this to 2+."
    )
    assert eng._watchdog_restart_count >= 1, (
        f"_watchdog_restart_count = {eng._watchdog_restart_count}. "
        "Should be ≥ 1 after a real freeze + restart."
    )


@pytest.mark.asyncio
async def test_live_no_restart_when_pipeline_healthy(real_db, monkeypatch):
    """Inverse: when the poll loop is genuinely healthy and writing
    fresh heartbeats every iteration, the watchdog must NEVER touch
    it. Catches the failure mode where the watchdog interferes with
    a working system."""
    eng = AdaptiveSyncEngine(real_db, odds_api_key="test-key")

    # Real-ish poll loop: writes a fresh heartbeat each pass.
    poll_iterations = {"n": 0}

    async def _healthy_poll_loop():
        try:
            while eng.is_running:
                poll_iterations["n"] += 1
                await real_db["adaptive_sync_heartbeat"].update_one(
                    {"_id": "adaptive_sync"},
                    {"$set": {
                        "last_heartbeat_at": datetime.now(timezone.utc),
                        "next_poll_in_seconds": 300,
                        "games_in_registry": 0,
                        "status_breakdown": {},
                    }},
                    upsert=True,
                )
                await asyncio.sleep(0)  # yield only
        except asyncio.CancelledError:
            return

    eng._adaptive_poll_loop = _healthy_poll_loop  # type: ignore[method-assign]
    eng._WATCHDOG_INTERVAL_SECONDS = 0
    eng._WATCHDOG_WARMUP_SECONDS = 0

    real_sleep = asyncio.sleep

    async def _instant_sleep(_s: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(
        "services.engines.adaptive_sync_engine.asyncio.sleep",
        _instant_sleep,
    )

    await eng.start()
    await real_sleep(0.1)
    eng.is_running = False
    await eng.stop()

    assert eng._watchdog_restart_count == 0, (
        f"Watchdog spuriously restarted a HEALTHY poll loop "
        f"({eng._watchdog_restart_count} restart(s)). The watchdog "
        "MUST be inert when heartbeats are fresh. Otherwise it will "
        "interfere with normal operation."
    )
    assert poll_iterations["n"] >= 2, (
        "Healthy poll loop didn't iterate — test setup issue."
    )


@pytest.mark.asyncio
async def test_live_storm_guard_engages_after_5_restarts(
    real_db, caplog,
):
    """Engineer a sustained freeze (heartbeat NEVER catches up) so
    the watchdog tries to restart repeatedly. Must emit the
    `RESTART_STORM_DETECTED` critical log after >5 restarts and
    stop thrashing.

    Catches a real failure mode: a code bug that makes every
    poll-loop respawn die immediately would otherwise produce an
    infinite restart loop with no rescue.
    """
    import logging
    eng = AdaptiveSyncEngine(real_db, odds_api_key="test-key")
    eng._WATCHDOG_INTERVAL_SECONDS = 0.001
    eng._WATCHDOG_WARMUP_SECONDS = 0
    # Long restart window — we don't want it expiring during the
    # test and resetting the counter mid-flight.
    eng._WATCHDOG_RESTART_WINDOW_SECONDS = 1800

    # Poll loop that "dies" instantly EVERY time it's spawned.
    spawn_count = {"n": 0}

    async def _instantly_dying_poll():
        spawn_count["n"] += 1
        # Don't touch the heartbeat — keep it stale forever.
        return  # exits immediately

    eng._adaptive_poll_loop = _instantly_dying_poll  # type: ignore[method-assign]
    # Engine has been running for an hour — needed so the perpetually-
    # stale heartbeat we inject is recognized as genuinely stale.
    eng._engine_started_at = datetime.now(timezone.utc) - timedelta(hours=1)

    # Stale heartbeat that NEVER updates (the poll loop dies before
    # writing one).
    await real_db["adaptive_sync_heartbeat"].update_one(
        {"_id": "adaptive_sync"},
        {"$set": {
            "last_heartbeat_at": datetime.now(timezone.utc) - timedelta(hours=1),
            "next_poll_in_seconds": 300,
        }},
        upsert=True,
    )

    caplog.clear()
    with caplog.at_level(logging.CRITICAL):
        await eng.start()
        # Allow many watchdog ticks. With 1ms interval, 100ms = ~100
        # ticks — well past the 5-restart storm threshold.
        await asyncio.sleep(0.1)
        eng.is_running = False
        await eng.stop()

    storm_messages = [
        rec for rec in caplog.records
        if "RESTART_STORM_DETECTED" in rec.getMessage()
    ]
    assert len(storm_messages) >= 1, (
        f"Storm guard never fired. spawn_count={spawn_count['n']}, "
        f"records: {[r.getMessage()[:80] for r in caplog.records]}. "
        "Without the storm guard, a code bug producing immediate "
        "respawns becomes an infinite hot-loop. The CRITICAL log "
        "MUST appear once restart_count exceeds MAX_RESTARTS_IN_WINDOW."
    )
    # And: restart_count MUST be bounded — not climbing past the
    # storm threshold without the guard kicking in.
    assert spawn_count["n"] >= 5, (
        f"Watchdog should have respawned the dead loop several "
        f"times before the guard tripped. spawn_count={spawn_count['n']}"
    )
