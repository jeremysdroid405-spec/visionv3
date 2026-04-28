"""
Contract Enforcer CI Tests — STRICT MODE Regression Lockdown
============================================================

Hard-fail tests for the runtime API-shape gates introduced 2026-04-29.

These tests exist precisely so the dashboard cannot regress to the
"Vucevic 75% vs graph 5/10" bug, the "+0 lineup spots" placeholder rows,
or yesterday's-finals-on-the-ticker class of issues.

Frozen fixtures
---------------
1. Vucevic P+R 9.5 — empirical L10 = 5/10 (50.0%); model-derived
   `hit_rate_over` = 75.0%. The enforcer MUST rewrite the displayed
   `hit_rate` to 50.0 and count the mismatch.
2. Lineup-opportunity zero-row — beneficiary present but
   lineup_delta == 0 and projected_ab_delta == 0. MUST be suppressed.
3. Past-game ticker — finished game (status_code=3) and a scheduled
   game whose start_time is in the past. Both MUST be suppressed.

If any of these tests fail, do NOT patch the enforcer to make them
pass. The failure means a real invariant regressed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

from services.contract_enforcer import (
    EVT_HIT_PROFILE_MISMATCH,
    EVT_LINEUP_OPPORTUNITY_SUPPRESSED,
    EVT_PAST_GAME_TICKET_SUPPRESSED,
    EVT_PICK_CARD_INVALID,
    PICK_CARD_REQUIRED_KEYS,
    aggregate_24h_counters,
    enforce_hit_profile_parity,
    enforce_lineup_opportunity_contract,
    enforce_pick_card_contract,
    enforce_ticker_freshness,
)


# ─── Mock async Mongo collection / db ────────────────────────────────
class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        async def _gen():
            for d in self._docs:
                yield d
        return _gen()


class _Coll:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": len(self.docs)})()

    async def create_index(self, *args, **kwargs):
        return None

    def aggregate(self, pipeline):
        # Minimal $match + $group{event,sport} support — exactly what
        # aggregate_24h_counters uses.
        match = {}
        for stage in pipeline:
            if "$match" in stage:
                match = stage["$match"]
        # Apply $match (only created_at $gte supported)
        cutoff = (match.get("created_at") or {}).get("$gte")
        rows = self.docs
        if cutoff is not None:
            rows = [d for d in rows if d.get("created_at", cutoff) >= cutoff]
        # Apply $group {_id: {event, sport}, count: $sum:1}
        buckets: Dict[tuple, int] = {}
        for d in rows:
            key = (d.get("event"), d.get("sport") or "unknown")
            buckets[key] = buckets.get(key, 0) + 1
        out = [
            {"_id": {"event": ev, "sport": sp}, "count": c}
            for (ev, sp), c in buckets.items()
        ]
        return _Cursor(out)


class _DB:
    def __init__(self):
        self._colls: Dict[str, _Coll] = {}

    def __getitem__(self, name):
        if name not in self._colls:
            self._colls[name] = _Coll()
        return self._colls[name]


# ─────────────────────────────────────────────────────────────────────
# 1. FROZEN VUCEVIC P+R 9.5 REGRESSION FIXTURE
# ─────────────────────────────────────────────────────────────────────
def _vucevic_pick() -> Dict[str, Any]:
    """Card payload as the Ferrari endpoint would emit AFTER the
    dashboard-card contract pass but BEFORE the runtime enforcer.

    The model-derived `hit_rate` (75%) disagrees with the empirical
    L10 (5/10 = 50%).  The enforcer must overwrite hit_rate -> 50.0.
    """
    return {
        "player_name":     "Nikola Vucevic",
        "team":            "CHI",
        "sport":           "nba",
        "stat_type":       "P+R",
        "line":            9.5,
        "recommendation":  "OVER",
        "direction":       "OVER",
        "tier_label":      "SAFE_HAVEN",
        "prop_type":       "STANDARD",
        # 8-field card contract
        "stat_line":       "P+R 9.5",
        "big_pick_text":   "OVER 9.5 P+R",
        "projection":      14.2,
        "hit_rate":        75.0,    # ← model-derived (wrong) display value
        "avg":             11.0,
        "short_sentence":  "Vucevic locked in.",
        # Empirical hit-profile fields (the source of truth)
        "l10_hit_count":   5,
        "l10_total":       10,
        "l10_values":      [9, 17, 14, 9, 18, 15, 9, 8, 0, 11],
        "hit_profile_line": 9.5,
        "hit_profile_rule": ">=",
    }


@pytest.mark.asyncio
async def test_vucevic_hit_profile_parity_rewrites_displayed_rate():
    """The frozen Vucevic regression: hit_rate must be rewritten to
    the empirical 50.0% from the broken 75.0%."""
    db = _DB()
    picks = [_vucevic_pick()]
    mismatches = await enforce_hit_profile_parity(
        db, picks, sport="nba", tier="safe_haven",
    )
    assert mismatches == 1, (
        f"Expected exactly 1 hit_profile mismatch on Vucevic fixture, "
        f"got {mismatches}"
    )
    assert picks[0]["hit_rate"] == 50.0, (
        "Enforcer MUST rewrite Vucevic displayed hit_rate to 50.0% "
        "(empirical L10 = 5/10). Frontend would otherwise drift again."
    )
    # Counter must surface in the 24h aggregate.
    counters = await aggregate_24h_counters(db)
    assert counters["hit_profile_mismatch_count_last_24h"] == 1


@pytest.mark.asyncio
async def test_hit_profile_parity_no_mismatch_on_aligned_pick():
    """When hit_rate matches l10_hit_count/l10_total, no rewrite, no
    counter increment."""
    db = _DB()
    pick = _vucevic_pick()
    pick["hit_rate"] = 50.0     # already empirical
    mismatches = await enforce_hit_profile_parity(
        db, [pick], sport="nba", tier="safe_haven",
    )
    assert mismatches == 0
    counters = await aggregate_24h_counters(db)
    assert counters["hit_profile_mismatch_count_last_24h"] == 0


@pytest.mark.asyncio
async def test_hit_profile_line_drift_is_counted():
    """Card line ≠ profile line is also a mismatch event."""
    db = _DB()
    pick = _vucevic_pick()
    pick["hit_rate"] = 50.0
    pick["hit_profile_line"] = 8.5     # drift vs card line 9.5
    mismatches = await enforce_hit_profile_parity(
        db, [pick], sport="nba", tier="safe_haven",
    )
    assert mismatches == 1


# ─────────────────────────────────────────────────────────────────────
# 2. PICK CARD CONTRACT — bad pick suppressed, good picks pass
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_pick_card_contract_drops_invalid_and_keeps_valid():
    """One bad pick must NOT break the whole tier. The bad row is
    dropped, valid rows continue, counter increments."""
    db = _DB()
    good = _vucevic_pick()
    good["hit_rate"] = 50.0
    bad = _vucevic_pick()
    bad["hit_rate"] = 50.0
    bad["player_name"] = None       # required-non-null violation
    third = _vucevic_pick()
    third["hit_rate"] = 50.0
    third["player_name"] = "Other Player"
    third.pop("recommendation")     # missing-key violation

    kept = await enforce_pick_card_contract(
        db, [good, bad, third], sport="nba", tier="safe_haven",
    )
    assert len(kept) == 1
    assert kept[0]["player_name"] == "Nikola Vucevic"
    counters = await aggregate_24h_counters(db)
    assert counters["invalid_pick_card_count_last_24h"] == 2
    # Sport-bucketed view too
    assert counters["missing_required_card_fields_by_sport"].get("nba") == 2


@pytest.mark.asyncio
async def test_pick_card_contract_allows_nullable_display_fields():
    """Nullable display keys (`projection`, `avg`, `short_sentence`,
    `stat_line`, `big_pick_text`, `hit_rate`) — KEY must exist; value
    may be null."""
    db = _DB()
    pick = _vucevic_pick()
    pick["hit_rate"] = None
    pick["avg"] = None
    pick["short_sentence"] = None
    pick["projection"] = None
    pick["stat_line"] = None
    pick["big_pick_text"] = None
    kept = await enforce_pick_card_contract(
        db, [pick], sport="nba", tier="safe_haven",
    )
    assert len(kept) == 1


@pytest.mark.asyncio
async def test_pick_card_contract_required_keys_locked():
    """Lockdown: this set must NOT be loosened without a CHANGELOG entry.
    Loosening this list is what got us the Vucevic / null-team
    regressions."""
    expected = {
        "player_name", "team", "sport",
        "stat_type", "line", "recommendation", "direction",
        "tier_label", "prop_type",
        "stat_line", "big_pick_text",
        "projection", "hit_rate", "avg", "short_sentence",
    }
    assert set(PICK_CARD_REQUIRED_KEYS) == expected, (
        f"PICK_CARD_REQUIRED_KEYS drift: {set(PICK_CARD_REQUIRED_KEYS) ^ expected}"
    )


# ─────────────────────────────────────────────────────────────────────
# 3. LINEUP OPPORTUNITY ZERO-ROW SUPPRESSION
# ─────────────────────────────────────────────────────────────────────
def _ok_lineup_alert() -> Dict[str, Any]:
    return {
        "beneficiary_name":     "Austin Riley",
        "current_lineup_slot":  3,
        "previous_lineup_slot": 5,
        "lineup_delta":         2,
        "projected_ab_delta":   0.5,
    }


@pytest.mark.asyncio
async def test_lineup_opportunity_drops_placeholder_zero_rows():
    """The classic '+0 lineup spots / +0 AB' placeholder bug. Both
    deltas zero MUST be dropped from the API response, not rendered.
    """
    db = _DB()
    bad = _ok_lineup_alert()
    bad["lineup_delta"] = 0
    bad["projected_ab_delta"] = 0
    kept = await enforce_lineup_opportunity_contract(db, [bad], sport="mlb")
    assert kept == []
    counters = await aggregate_24h_counters(db)
    assert counters["suppressed_lineup_opportunity_count_last_24h"] == 1


@pytest.mark.asyncio
async def test_lineup_opportunity_drops_missing_beneficiary():
    """A row with no beneficiary_name is structurally invalid even
    if the deltas look real."""
    db = _DB()
    bad = _ok_lineup_alert()
    bad["beneficiary_name"] = None
    kept = await enforce_lineup_opportunity_contract(db, [bad], sport="mlb")
    assert kept == []
    counters = await aggregate_24h_counters(db)
    assert counters["suppressed_lineup_opportunity_count_last_24h"] == 1


@pytest.mark.asyncio
async def test_lineup_opportunity_drops_non_numeric_slots():
    db = _DB()
    bad = _ok_lineup_alert()
    bad["current_lineup_slot"] = "—"
    kept = await enforce_lineup_opportunity_contract(db, [bad], sport="mlb")
    assert kept == []


@pytest.mark.asyncio
async def test_lineup_opportunity_keeps_valid_rows_unchanged():
    """A real row (Austin Riley moves from 5→3, projected +0.5 AB)
    passes through cleanly."""
    db = _DB()
    good = _ok_lineup_alert()
    kept = await enforce_lineup_opportunity_contract(
        db, [good, _ok_lineup_alert()], sport="mlb",
    )
    assert len(kept) == 2
    counters = await aggregate_24h_counters(db)
    assert counters["suppressed_lineup_opportunity_count_last_24h"] == 0


@pytest.mark.asyncio
async def test_lineup_opportunity_one_bad_row_does_not_break_others():
    """STRICT MODE invariant: one bad row CANNOT poison the whole
    section. Good rows survive, bad rows are dropped + counted."""
    db = _DB()
    good = _ok_lineup_alert()
    bad = _ok_lineup_alert()
    bad["beneficiary_name"] = None
    bad["lineup_delta"] = 0
    kept = await enforce_lineup_opportunity_contract(
        db, [good, bad], sport="mlb",
    )
    assert len(kept) == 1
    assert kept[0]["beneficiary_name"] == "Austin Riley"


# ─────────────────────────────────────────────────────────────────────
# 4. PAST-GAME TICKER SUPPRESSION
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_ticker_drops_finished_games():
    """Yesterday's finals (status_code == 3) MUST be suppressed."""
    db = _DB()
    final = {
        "game_id": "1",
        "status_code": 3,
        "start_time": datetime.now(timezone.utc).isoformat(),
    }
    kept = await enforce_ticker_freshness(db, [final], sport="nba")
    assert kept == []
    counters = await aggregate_24h_counters(db)
    assert counters["past_game_ticket_suppressed_count_last_24h"] == 1


@pytest.mark.asyncio
async def test_ticker_drops_scheduled_with_past_start_time():
    """A scheduled game whose commence_time is already past is stale
    and MUST be dropped."""
    db = _DB()
    past_start = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    stale = {
        "game_id": "2",
        "status_code": 1,
        "start_time": past_start,
    }
    kept = await enforce_ticker_freshness(db, [stale], sport="mlb")
    assert kept == []


@pytest.mark.asyncio
async def test_ticker_keeps_in_play_regardless_of_start_time():
    """In-play (status_code == 2) must be kept even if start_time was
    hours ago — the game IS happening now."""
    db = _DB()
    past_start = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    in_play = {
        "game_id": "3",
        "status_code": 2,
        "start_time": past_start,
    }
    kept = await enforce_ticker_freshness(db, [in_play], sport="nba")
    assert len(kept) == 1


@pytest.mark.asyncio
async def test_ticker_keeps_scheduled_with_future_start_time():
    db = _DB()
    future_start = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    upcoming = {
        "game_id": "4",
        "status_code": 1,
        "start_time": future_start,
    }
    kept = await enforce_ticker_freshness(db, [upcoming], sport="mlb")
    assert len(kept) == 1


# ─────────────────────────────────────────────────────────────────────
# 5. AGGREGATED 24h COUNTERS — payload shape lockdown
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_health_contracts_payload_shape_is_complete():
    """Every counter key the /api/health/contracts endpoint promises
    must always exist (default 0). Frontend dashboards depend on this
    invariance — a missing key would break observability."""
    db = _DB()
    out = await aggregate_24h_counters(db)
    expected_keys = {
        "invalid_pick_card_count_last_24h",
        "suppressed_lineup_opportunity_count_last_24h",
        "hit_profile_mismatch_count_last_24h",
        "past_game_ticket_suppressed_count_last_24h",
        "logo_lookup_not_sport_keyed_count_last_24h",
        "missing_required_card_fields_by_sport",
    }
    assert expected_keys.issubset(out.keys())
    for k in expected_keys - {"missing_required_card_fields_by_sport"}:
        assert out[k] == 0
    assert out["missing_required_card_fields_by_sport"] == {}


@pytest.mark.asyncio
async def test_aggregate_counters_handles_none_db_gracefully():
    """Health endpoint must never 5xx when db is unset — we ship
    zero counters instead."""
    out = await aggregate_24h_counters(None)
    assert out["invalid_pick_card_count_last_24h"] == 0
    assert out["missing_required_card_fields_by_sport"] == {}


# ─────────────────────────────────────────────────────────────────────
# 6. NO MODEL / SCORING / GATE / THRESHOLD MUTATION
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_enforcer_does_not_touch_model_fields():
    """The enforcer is a pure DISPLAY-shape gate. Model-derived
    fields (`hit_rate_over`, `vk2_projection`, `vision_score`,
    `tier`, `tier_gate_results`, `p_true_active`) MUST be left
    untouched even when the displayed `hit_rate` is rewritten."""
    db = _DB()
    pick = _vucevic_pick()
    pick["hit_rate_over"] = 75.0
    pick["vk2_projection"] = 14.2
    pick["vision_score"] = 87.5
    pick["tier"] = "safe_haven"
    pick["tier_gate_results"] = {"gate1": True}
    pick["p_true_active"] = 0.624

    snapshot_before = {
        k: pick[k] for k in
        ("hit_rate_over", "vk2_projection", "vision_score",
         "tier", "tier_gate_results", "p_true_active")
    }
    await enforce_hit_profile_parity(db, [pick], sport="nba", tier="safe_haven")
    snapshot_after = {k: pick[k] for k in snapshot_before}
    assert snapshot_before == snapshot_after, (
        f"Enforcer leaked into model fields: "
        f"before={snapshot_before} after={snapshot_after}"
    )
    # And the displayed hit_rate WAS rewritten.
    assert pick["hit_rate"] == 50.0
