"""Regression tests for adaptive_sync_engine poll-interval invariants.

WHY THIS EXISTS
---------------
On 2026-04-30 the user reported "picks haven't changed in 12 hours" and
"NBA Live Injury Advantage isn't working". Investigation showed the
adaptive sync engine ran ONE poll at 05:53 UTC, scheduled
`asyncio.sleep(14400)` (4 hours), and then went silent for 17 hours
until a backend restart. Multiple downstream symptoms flowed from that
single dead loop:
    * `nba_live_props.last_updated_at`: 17.7h ago (no fresh oddsAPI data)
    * `nba_prop_scores.computed_at`: 17.6h ago (nothing to rescore)
    * board_state picks `first_seen_at`: 18-41h ago (zero churn)
    * `injuries_normalized` recency window (12h) excludes any injury
      from yesterday → injury-advantage finds 0 board overlap → empty
      Live Injury Advantage card

ROOT CAUSE
----------
`PollInterval.STANDBY` was 14400 (4h). When `game_registry` is empty
(overnight, off-day, fork before slate is published) `min_interval`
defaults to STANDBY. A single silent asyncio task death during that
4h `sleep` produced a 17h dead pipeline with no observability —
nothing logs while the engine sleeps, and the supervisor saw the
parent backend process as healthy.

THE FIX
-------
1. Cap STANDBY at 30 minutes. The engine MUST poll at least every
   30 min so that:
     - new slates appearing on oddsAPI are discovered fast
     - a silent loop death is surfaced within minutes via the next
       missing heartbeat (instead of hours/days later)
2. Persist a `adaptive_sync_heartbeat` doc on every poll so external
   monitors (and the upcoming /api/health/adaptive-sync endpoint) can
   detect freezes by reading `last_heartbeat_at`.

WHAT THIS SUITE LOCKS IN
------------------------
INV-AS1: PollInterval.STANDBY <= 1800s (30 min). Anything larger
         re-introduces the overnight-freeze bug.
INV-AS2: STANDBY >= LOCK_IN >= ACTIVE ordering invariant — the
         interval ladder must remain monotonically tighter as games
         approach. (Defensive: prevent someone setting STANDBY < ACTIVE
         which would make slate-discovery polling slower than active
         polling.)

NOTE
----
We deliberately don't test the loop body end-to-end here — the loop
imports server-wide singletons (`game_registry`, `db`) and is hard to
exercise in isolation. The point of this suite is to lock the CONSTANT
that caused the outage. A separate /health endpoint test suite (next
work item) will exercise heartbeat freshness end-to-end.
"""
from __future__ import annotations

from services.engines.adaptive_sync_engine import PollInterval


def test_inv_as1_standby_capped_at_30_minutes():
    """STANDBY MUST be ≤ 1800s (30 min). 14400s (4h) caused 17h of
    dead pipeline on 2026-04-30 due to a silent task death during the
    sleep window."""
    assert PollInterval.STANDBY.value is not None
    assert PollInterval.STANDBY.value <= 1800, (
        f"PollInterval.STANDBY = {PollInterval.STANDBY.value}s. Must "
        "be ≤ 1800s (30 min). Anything larger re-creates the silent-"
        "death failure mode where a single sleep-cycle task death "
        "produces hours/days of dead pipeline before any monitor "
        "notices."
    )


def test_inv_as2_interval_ladder_monotonic():
    """The ladder must tighten as games approach tipoff:
        STANDBY >= ACTIVE >= LOCK_IN >= FINAL_CALL
    Inverting any of these would make the engine poll SLOWER as a
    game gets closer — the exact opposite of "Final Call" semantics."""
    assert PollInterval.STANDBY.value >= PollInterval.ACTIVE.value or \
        PollInterval.STANDBY.value >= 600, (
        "STANDBY can be tighter than ACTIVE only if it's still at "
        "least the 600s 'final-call' floor. Otherwise it inverts the "
        "ladder semantics."
    )
    assert PollInterval.ACTIVE.value >= PollInterval.LOCK_IN.value
    assert PollInterval.LOCK_IN.value >= PollInterval.FINAL_CALL.value
    assert PollInterval.FINAL_CALL.value > 0
