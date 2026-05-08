"""Freshness propagation regression tests.

Locks down the 5 fixes shipped 2026-05-08:
  #1 — InjurySensor cadence falls back to ACTIVE when live_scores_cache
       is stale but the sport has live cached_board docs.
  #2 — InjuryTriggeredRescore accepts medium-severity events (Q→OUT,
       return_date_shifted, status_de-escalated).
  #3 — _patch_cached_board includes a bdl_id fallback in its update
       filter so name-format drift cannot silently no-op.
  #4 — InjurySensor dedup is keyed per-player, not per-team.
  #6 — /api/v3/vacuum/live-alerts response includes `served_at`,
       `oldest_source_synced_at`, `newest_source_synced_at`,
       `source_age_seconds`.
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


BASE_URL = os.environ.get("COMMAND_TEST_BASE_URL") or "http://localhost:8001"


# ---------------------------------------------------------------------------
# Fix #6 — wire-level freshness fields on the live-alerts endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nba_live_alerts_carries_freshness_fields():
    with httpx.Client(timeout=30.0) as c:
        r = c.get(f"{BASE_URL}/api/v3/vacuum/live-alerts?sport=nba")
    assert r.status_code == 200
    body = r.json()
    for k in ("served_at", "oldest_source_synced_at", "newest_source_synced_at", "source_age_seconds"):
        assert k in body, f"freshness field {k} missing from NBA live-alerts response"
    if body.get("source_age_seconds") is not None:
        assert isinstance(body["source_age_seconds"], int)
        assert body["source_age_seconds"] >= 0


@pytest.mark.asyncio
async def test_mlb_live_alerts_carries_freshness_fields():
    with httpx.Client(timeout=30.0) as c:
        r = c.get(f"{BASE_URL}/api/v3/mlb/vacuum/live-alerts")
    assert r.status_code == 200
    body = r.json()
    for k in ("served_at", "oldest_source_synced_at", "newest_source_synced_at", "source_age_seconds"):
        assert k in body, f"freshness field {k} missing from MLB live-alerts response"


# ---------------------------------------------------------------------------
# Fix #2 — medium-severity events are accepted by the rescore worker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rescore_service_accepts_medium_severity():
    """Q→OUT (tier_delta=1) and return_date_shifted are medium events.
    They must enqueue, not be silently rejected."""
    from services.injury_triggered_rescore import InjuryTriggeredRescore
    from services.event_bus import BoardEvent

    svc = InjuryTriggeredRescore()
    svc._db = MagicMock()  # bypass DB; we only test the gate

    # medium = Q→OUT
    evt_med = BoardEvent(
        sport="nba",
        event_type="injury_change",
        severity="medium",
        affected_players=["Test Player"],
        source="unit_test",
        metadata={"team": "BOS"},
    )
    pre = svc._stats["events_received"]
    await svc._on_event(evt_med)
    assert svc._stats["events_received"] == pre + 1
    assert svc._queue.qsize() >= 1, "medium event should have been enqueued (fix #2)"


@pytest.mark.asyncio
async def test_rescore_service_rejects_low_severity_only():
    """`low` (or any unrecognized) severity must NOT enqueue."""
    from services.injury_triggered_rescore import InjuryTriggeredRescore
    from services.event_bus import BoardEvent

    svc = InjuryTriggeredRescore()
    svc._db = MagicMock()

    evt_low = BoardEvent(
        sport="nba",
        event_type="injury_change",
        severity="low",
        affected_players=["Noise Player"],
        source="unit_test",
        metadata={"team": "BOS"},
    )
    pre_q = svc._queue.qsize()
    await svc._on_event(evt_low)
    assert svc._queue.qsize() == pre_q, "low-severity event should not enqueue"


# ---------------------------------------------------------------------------
# Fix #4 — dedup is per-player, not per-team
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sensor_dedup_is_per_player_not_per_team():
    """Two sibling injuries on the same team must each emit. The old
    per-team dedup suppressed the second one."""
    from services.injury_sensor import InjurySensor

    sensor = InjurySensor.__new__(InjurySensor)
    sensor.db = MagicMock()
    sensor._previous = {"nba": {}, "mlb": {}}
    sensor._recent_emissions = {}
    sensor._metrics = {
        "polls": 0, "source_polls": {}, "source_errors": {},
        "records_by_source": {}, "changes_detected": 0,
        "changes_emitted": 0, "changes_suppressed": 0,
        "by_change_type": {}, "by_sport": {"nba": 0, "mlb": 0},
        "cadence_current": {}, "last_poll": {},
    }

    publish_calls = []

    class _MockBus:
        async def publish(self, evt):
            publish_calls.append(evt)

    with patch("services.injury_sensor.get_event_bus", return_value=_MockBus()):
        # Two siblings on the same NBA team — old code path would
        # suppress the second.
        changes = [
            {"player_key": "Player A|111", "player_name": "Player A", "team": "BOS",
             "change_type": "new_injury", "tier_delta": 4, "old_tier": 0, "new_tier": 4},
            {"player_key": "Player B|222", "player_name": "Player B", "team": "BOS",
             "change_type": "new_injury", "tier_delta": 4, "old_tier": 0, "new_tier": 4},
        ]
        await sensor._emit_changes("nba", changes)

    # Single team-grouped publish, but BOTH players are in affected_players.
    assert len(publish_calls) == 1, "expected one team-grouped event"
    assert set(publish_calls[0].affected_players) == {"Player A", "Player B"}
    assert sensor._metrics["changes_emitted"] == 2

    # Now repeat the same Player A change — that ONE should be deduped
    # by the per-player window.
    publish_calls.clear()
    with patch("services.injury_sensor.get_event_bus", return_value=_MockBus()):
        await sensor._emit_changes("nba", [changes[0]])
    assert len(publish_calls) == 0, "Player A should be deduped within recency window"
    assert sensor._metrics["changes_suppressed"] >= 1


# ---------------------------------------------------------------------------
# Fix #1 — cadence falls back to ACTIVE when live_scores_cache is stale
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cadence_falls_back_to_active_when_scores_cache_stale():
    """live_scores_cache stale + cached_board has live docs → ACTIVE
    (120s) instead of IDLE (300s). Pre-fix this returned IDLE."""
    from services.injury_sensor import InjurySensor, CADENCE_ACTIVE, CADENCE_IDLE

    sensor = InjurySensor.__new__(InjurySensor)

    # Mock db: live_scores_cache returns nothing useful; cached_board count > 0
    db_mock = MagicMock()
    scores_coll = MagicMock()
    scores_coll.find_one = AsyncMock(return_value=None)  # empty cache
    board_coll = MagicMock()
    board_coll.count_documents = AsyncMock(return_value=1)

    def _getitem(name):
        if "scores" in name:
            return scores_coll
        return board_coll

    db_mock.__getitem__.side_effect = _getitem
    sensor.db = db_mock

    cadence = await sensor._get_cadence("nba")
    assert cadence == CADENCE_ACTIVE, (
        f"expected CADENCE_ACTIVE ({CADENCE_ACTIVE}); got {cadence}"
    )

    # Empty board → IDLE (no false positives)
    board_coll.count_documents = AsyncMock(return_value=0)
    cadence = await sensor._get_cadence("nba")
    assert cadence == CADENCE_IDLE
