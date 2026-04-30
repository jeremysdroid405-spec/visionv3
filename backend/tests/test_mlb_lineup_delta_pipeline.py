"""Regression tests for MLB Live Injury Advantage lineup-delta pipeline.

WHY THIS EXISTS
---------------
On 2026-04-30 the MLB Live Injury Advantage UI section was empty.
The endpoint `/api/v3/mlb/vacuum/live-alerts` reported
`raw_advantage_count=22, filtered_dropped=22, count=0`. The universal
injury-advantage engine was correctly producing 22 beneficiaries, but
every row was dropped by the lineup-delta gate because no
`previous_lineup_slot` was ever available.

ROOT CAUSE
----------
`services/mlb_lineup_delta.build_lineup_delta_index` read the
`mlb_lineups` collection for the "previous" snapshot. Nothing in the
codebase ever writes to that collection — it was designed for a daily
snapshot job that shipped with no producer. The "current" branch
merged ALL `mlb_projected_lineups` docs (multiple game_dates collapsed
via last-write-wins) into a single index, so `previous_lineup_slot`
was always `None`. The strict row-filter rejected every row.

THE FIX (Option A + B, 2026-04-30)
----------------------------------
A) `build_lineup_delta_index` — when `mlb_lineups` is empty, fall
   back to splitting `mlb_projected_lineups` by `game_date`. Most
   recent date = current, next most recent = previous. Players in
   both snapshots get a real `lineup_delta`.

B) `extract_deltas_for_player` — when a player is in the current
   snapshot but not the previous one (injury-driven new starter),
   emit `projected_ab_delta = current_expected_pa` and
   `is_new_starter = True`. The caller (`routes/mlb_vacuum.py::
   _row_qualifies` + `services/contract_enforcer::
   enforce_lineup_opportunity_contract`) accepts the new-starter
   shape with `previous_lineup_slot = None`.

WHAT THIS SUITE LOCKS IN
------------------------
INV-L1 (Option A): `build_lineup_delta_index` uses the most-recent
       game_date as "current" and the next-most-recent as "previous"
       when `mlb_lineups` is empty. Players present in both get a
       numeric `previous_lineup_slot` and `current_lineup_slot`.

INV-L2 (Option A): `build_lineup_delta_index` does NOT collapse two
       game_dates into one last-write-wins bucket (the pre-2026-04-30
       bug). A player at slot 6 yesterday and slot 2 today has
       distinct values, not both = 2.

INV-L3 (Option B): `extract_deltas_for_player` emits
       `is_new_starter=True` and a positive `projected_ab_delta` for
       a player in current-only.

INV-L4 (Option B): `enforce_lineup_opportunity_contract` accepts
       a new-starter row (previous_lineup_slot=None, lineup_delta=None)
       when `is_new_starter=True` + valid current_slot + ab_delta > 0.

INV-L5 (Option A backward-compat): When `mlb_lineups` is populated
       (future snapshot job), it still takes precedence over the
       game_date-split fallback.
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient


# ─── Fixtures ────────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    yield client[os.environ["DB_NAME"]]


# Use a unique marker field so tests never collide with real data and
# cleanup is trivial.
_TEST_TAG = "_lineup_delta_test_tag"


def _make_projected_doc(
    team: str,
    game_date: str,
    lineup_pairs: List[Dict[str, Any]],
    *,
    test_tag: str,
) -> Dict[str, Any]:
    """Shape a projected-lineup doc matching real collection schema."""
    # Unique `event_id` per (team, game_date) so the collection's
    # `(event_id, team_abbr)` unique index doesn't collide across test
    # fixtures.
    return {
        "event_id": f"evt_{test_tag}_{team}_{game_date}",
        "team_abbr": team,
        "game_date": game_date,
        "lineup": [{"slot": p["slot"], "player_name": p["name"]}
                   for p in lineup_pairs],
        _TEST_TAG: test_tag,
    }


def _make_canonical_doc(
    team: str, lineup_pairs: List[Dict[str, Any]], *, test_tag: str,
) -> Dict[str, Any]:
    """Shape a canonical-lineup doc (mlb_lineups)."""
    return {
        "team_abbr": team,
        "players": [{"slot": p["slot"], "player_name": p["name"]}
                    for p in lineup_pairs],
        _TEST_TAG: test_tag,
    }


async def _cleanup(db, test_tag: str) -> None:
    await db["mlb_projected_lineups"].delete_many({_TEST_TAG: test_tag})
    await db["mlb_lineups"].delete_many({_TEST_TAG: test_tag})


# ─── INV-L1 ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_inv_l1_game_date_split_populates_both_sides(db):
    """Given two game_dates in mlb_projected_lineups and an empty
    mlb_lineups, the index must populate both `previous_lineup_slot`
    and `current_lineup_slot` for players in both snapshots."""
    from services.mlb_lineup_delta import (
        build_lineup_delta_index, extract_deltas_for_player,
    )
    tag = f"inv_l1_{uuid.uuid4().hex[:8]}"
    await _cleanup(db, tag)
    try:
        # Yesterday: Kwan #1, Ramirez #3. Today: Kwan #1, Ramirez #2.
        await db["mlb_projected_lineups"].insert_many([
            _make_projected_doc("CLE", "2999-06-01", [
                {"name": "Steven Kwan", "slot": 1},
                {"name": "Jose Ramirez", "slot": 3},
            ], test_tag=tag),
            _make_projected_doc("CLE", "2999-06-02", [
                {"name": "Steven Kwan", "slot": 1},
                {"name": "Jose Ramirez", "slot": 2},
            ], test_tag=tag),
        ])
        # Scope the read by our tag so the real production data doesn't
        # interfere with the assertion. We do this by patching the
        # find() to include our tag filter via a wrapping call:
        # simpler — just assert on the two specific player names.
        idx = await build_lineup_delta_index(db)

        ramirez = extract_deltas_for_player(idx, "Jose Ramirez")
        assert ramirez["current_lineup_slot"] == 2
        assert ramirez["previous_lineup_slot"] == 3
        assert ramirez["lineup_delta"] == 1.0  # moved up 1 slot
        assert ramirez["is_new_starter"] is False

        kwan = extract_deltas_for_player(idx, "Steven Kwan")
        assert kwan["current_lineup_slot"] == 1
        assert kwan["previous_lineup_slot"] == 1
        assert kwan["lineup_delta"] == 0.0  # stayed at leadoff
    finally:
        await _cleanup(db, tag)


# ─── INV-L2 ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_inv_l2_does_not_collapse_two_dates_to_one_bucket(db):
    """Regression guard on the pre-fix bug: ALL mlb_projected_lineups
    docs (regardless of game_date) were last-write-wins merged into a
    single current_index, so yesterday's slot was overwritten by
    today's. Lock in that previous/current are kept SEPARATE."""
    from services.mlb_lineup_delta import (
        build_lineup_delta_index, extract_deltas_for_player,
    )
    tag = f"inv_l2_{uuid.uuid4().hex[:8]}"
    await _cleanup(db, tag)
    try:
        # Yesterday: Tucker at slot 6. Today: Tucker at slot 2.
        await db["mlb_projected_lineups"].insert_many([
            _make_projected_doc("HOU", "2999-07-01", [
                {"name": "Kyle Tucker", "slot": 6},
            ], test_tag=tag),
            _make_projected_doc("HOU", "2999-07-02", [
                {"name": "Kyle Tucker", "slot": 2},
            ], test_tag=tag),
        ])
        idx = await build_lineup_delta_index(db)
        tucker = extract_deltas_for_player(idx, "Kyle Tucker")
        assert tucker["previous_lineup_slot"] == 6, (
            "Pre-fix regression: both game_dates collapsed into one "
            "bucket via last-write-wins. Previous slot MUST be distinct."
        )
        assert tucker["current_lineup_slot"] == 2
        assert tucker["lineup_delta"] == 4.0  # moved up 4 spots
    finally:
        await _cleanup(db, tag)


# ─── INV-L3 ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_inv_l3_new_starter_gets_ab_delta_and_flag(db):
    """A player present ONLY in the current snapshot must be flagged
    `is_new_starter=True` with a positive `projected_ab_delta` equal
    to their current-slot expected PA."""
    from services.mlb_lineup_delta import (
        build_lineup_delta_index, extract_deltas_for_player,
    )
    tag = f"inv_l3_{uuid.uuid4().hex[:8]}"
    await _cleanup(db, tag)
    try:
        # Yesterday: veteran at slot 3. Today: rookie took slot 3.
        await db["mlb_projected_lineups"].insert_many([
            _make_projected_doc("BOS", "2999-08-01", [
                {"name": "Injured Veteran", "slot": 3},
            ], test_tag=tag),
            _make_projected_doc("BOS", "2999-08-02", [
                {"name": "Rookie Callup Test", "slot": 3},
            ], test_tag=tag),
        ])
        idx = await build_lineup_delta_index(db)
        rookie = extract_deltas_for_player(idx, "Rookie Callup Test")
        assert rookie["is_new_starter"] is True
        assert rookie["previous_lineup_slot"] is None
        assert rookie["current_lineup_slot"] == 3
        assert rookie["lineup_delta"] is None
        assert isinstance(rookie["projected_ab_delta"], float)
        assert rookie["projected_ab_delta"] >= 0.5, (
            "New-starter projected_ab_delta must be >= 0.5 (comes "
            "from _DEFAULT_PA_BY_SLOT). Got: "
            f"{rookie['projected_ab_delta']}"
        )
        # `current_expected_pa` is the absolute projected PA the UI
        # renders (not a delta). Must match the slot-3 default.
        assert rookie["current_expected_pa"] is not None
        assert 3.5 < rookie["current_expected_pa"] < 5.0, (
            "New-starter current_expected_pa must reflect the slot-3 "
            "default. Got: "
            f"{rookie['current_expected_pa']}"
        )
        # HONEST additional-AB: slot-3 PA (4.35) minus bench baseline
        # (1.0) ≈ 3.35 → rounds to 3 in the UI.
        assert rookie["extra_ab_from_injury"] is not None
        assert 3.0 <= rookie["extra_ab_from_injury"] <= 3.5, (
            "New-starter extra_ab_from_injury must be "
            "current_expected_pa - bench_baseline (~1.0). Got: "
            f"{rookie['extra_ab_from_injury']}"
        )
        assert round(rookie["extra_ab_from_injury"]) == 3
    finally:
        await _cleanup(db, tag)


@pytest.mark.asyncio
async def test_inv_l6_slot_shifter_extra_ab_is_fractional(db):
    """INV-L6: A slot-shift beneficiary's `extra_ab_from_injury` is
    the projected_ab_delta (current_pa - previous_pa). For typical
    2-slot moves this is ~0.25-0.55 — rounds to 0 and the UI hides
    the AB column. Lock in the small, honest value (NOT the full
    current_expected_pa)."""
    from services.mlb_lineup_delta import (
        build_lineup_delta_index, extract_deltas_for_player,
    )
    tag = f"inv_l6_{uuid.uuid4().hex[:8]}"
    await _cleanup(db, tag)
    try:
        await db["mlb_projected_lineups"].insert_many([
            _make_projected_doc("NYM", "2999-10-01", [
                {"name": "Slot Shifter Test", "slot": 6},
            ], test_tag=tag),
            _make_projected_doc("NYM", "2999-10-02", [
                {"name": "Slot Shifter Test", "slot": 4},
            ], test_tag=tag),
        ])
        idx = await build_lineup_delta_index(db)
        shifter = extract_deltas_for_player(idx, "Slot Shifter Test")
        assert shifter["is_new_starter"] is False
        assert shifter["extra_ab_from_injury"] is not None
        # Slot 6 = 3.95 PA, Slot 4 = 4.20 PA → delta = 0.25.
        assert 0.2 <= shifter["extra_ab_from_injury"] <= 0.3, (
            "Slot-shifter extra_ab_from_injury MUST equal "
            "projected_ab_delta, NOT current_expected_pa. Got: "
            f"{shifter['extra_ab_from_injury']}"
        )
        assert round(shifter["extra_ab_from_injury"]) == 0
    finally:
        await _cleanup(db, tag)


# ─── INV-L4 ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_inv_l4_contract_enforcer_accepts_new_starter(db):
    """`enforce_lineup_opportunity_contract` must NOT suppress a
    new-starter row whose previous_lineup_slot is None, as long as
    `is_new_starter=True` and the current_slot/ab_delta are valid."""
    from services.contract_enforcer import (
        enforce_lineup_opportunity_contract,
    )
    new_starter_row = {
        "beneficiary_name": "Rookie Callup Test",
        "current_lineup_slot": 3,
        "previous_lineup_slot": None,
        "lineup_delta": None,
        "projected_ab_delta": 4.35,
        "is_new_starter": True,
        "injured_player": "Star McInjured",
    }
    shift_row_valid = {
        "beneficiary_name": "Jose Ramirez",
        "current_lineup_slot": 2,
        "previous_lineup_slot": 3,
        "lineup_delta": 1,
        "projected_ab_delta": 0.15,
        "is_new_starter": False,
        "injured_player": "Star McInjured",
    }
    invalid_placeholder_row = {
        "beneficiary_name": "Null Row",
        "current_lineup_slot": None,  # missing required
        "previous_lineup_slot": None,
        "lineup_delta": None,
        "projected_ab_delta": None,
        "is_new_starter": False,
        "injured_player": "Star McInjured",
    }
    kept = await enforce_lineup_opportunity_contract(
        db, [new_starter_row, shift_row_valid, invalid_placeholder_row],
        sport="mlb",
    )
    kept_names = {k["beneficiary_name"] for k in kept}
    assert kept_names == {"Rookie Callup Test", "Jose Ramirez"}, (
        f"New-starter row was suppressed (or placeholder leaked). "
        f"kept={kept_names}"
    )


# ─── INV-L5 ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_inv_l5_canonical_mlb_lineups_takes_precedence(db):
    """When `mlb_lineups` is populated it MUST take precedence over
    the game_date-split fallback — the fallback is only used when
    the canonical snapshot is empty."""
    from services.mlb_lineup_delta import (
        build_lineup_delta_index, extract_deltas_for_player,
    )
    tag = f"inv_l5_{uuid.uuid4().hex[:8]}"
    await _cleanup(db, tag)
    try:
        # `mlb_lineups` (canonical previous) says Tucker was at slot 7.
        # `mlb_projected_lineups` has two dates with Tucker at slot 6
        # yesterday and slot 2 today. The canonical source must win
        # for the "previous" value (7 not 6).
        await db["mlb_lineups"].insert_one(
            _make_canonical_doc("HOU", [
                {"name": "Kyle Tucker", "slot": 7},
            ], test_tag=tag)
        )
        await db["mlb_projected_lineups"].insert_many([
            _make_projected_doc("HOU", "2999-09-01", [
                {"name": "Kyle Tucker", "slot": 6},
            ], test_tag=tag),
            _make_projected_doc("HOU", "2999-09-02", [
                {"name": "Kyle Tucker", "slot": 2},
            ], test_tag=tag),
        ])
        idx = await build_lineup_delta_index(db)
        tucker = extract_deltas_for_player(idx, "Kyle Tucker")
        assert tucker["previous_lineup_slot"] == 7, (
            "mlb_lineups canonical snapshot MUST take precedence over "
            "game_date fallback. Got previous_slot="
            f"{tucker['previous_lineup_slot']} (expected 7)."
        )
        assert tucker["current_lineup_slot"] == 2
    finally:
        await _cleanup(db, tag)
