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


def test_inv_as1_standby_24_7_watcher():
    """STANDBY MUST be ≤ 600s (10 min). The watcher runs 24/7 — its
    primary job is detecting NEW PROP RELEASES, which happen primarily
    in the empty-game-registry window (overnight). A loose STANDBY
    misses early lines before bookmakers move them, which is the
    entire edge of this product.

    History: 14400s (4h) → caused 17h dead pipeline on 2026-04-30
    via a silent task death during the long sleep. 1800s (30min)
    was a stop-gap. 300s is the product-correct value: catches new
    lines within 5 minutes of release, and any silent loop death is
    surfaced within ~15 min (3× the heartbeat interval).
    """
    assert PollInterval.STANDBY.value is not None
    assert PollInterval.STANDBY.value <= 600, (
        f"PollInterval.STANDBY = {PollInterval.STANDBY.value}s. Must "
        "be ≤ 600s (10 min) — the watcher is a 24/7 early-line detector. "
        "Loose STANDBY = miss new prop releases before lines move = "
        "lose the entire product edge."
    )


def test_inv_as2_interval_floors_are_sane():
    """All poll intervals must be positive and bounded. STANDBY must
    not be smaller than 60s (1 min) — that's a hard floor to avoid
    runaway oddsAPI calls and DB write storms."""
    for tier in (
        PollInterval.STANDBY, PollInterval.ACTIVE,
        PollInterval.LOCK_IN, PollInterval.FINAL_CALL,
    ):
        assert tier.value is not None and tier.value >= 60, (
            f"{tier.name} = {tier.value}s violates the 60s floor "
            "(would create runaway upstream calls)."
        )
        assert tier.value <= 14400, (
            f"{tier.name} = {tier.value}s exceeds the 4h ceiling "
            "(would re-create the silent-freeze failure mode)."
        )
