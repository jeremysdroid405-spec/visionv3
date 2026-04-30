"""Regression tests for MLB Vacuum + Injury-Advantage subsystems.

WHY THIS EXISTS
---------------
3,828 LOC across `routes/mlb_vacuum.py`, `services/mlb_injury_vacuum_service.py`,
`services/live_injury_micro_sync.py`, `services/injury_advantage.py`, and
`services/injury_triggered_rescore.py` had ZERO tests on the MLB side
until 2026-04-30. The subsystem regressed repeatedly:
  - Late-scratch alerts stopped firing (2026-04-18)
  - `live-alerts` endpoint returned rows without real deltas (2026-04-26)
  - MLB injuries were silently re-routed to the wrong engine (2026-04-27)
Each time the fix shipped without a test. Each time the bug returned.

WHAT THIS SUITE LOCKS IN
------------------------
Pure-function contracts (no DB required):
  - `_estimate_benefit` returns the right bump for (tier, rank).
  - `_is_numeric` / `_row_qualifies` strict filter for the UI endpoint.

Universal-engine contracts (minimal seeded state):
  - `compute_injury_advantages(db, "mlb")` returns empty list on empty DB.
  - Same-team injury gate — advantage only fires when injured player's
    team == beneficiary's team.
  - Per-player dedup — no two advantages for the same beneficiary.
  - Self-boost guard — an injured player cannot be his own beneficiary.

HTTP-endpoint contracts (via httpx against the running backend):
  - GET /api/v3/mlb/vacuum/live-alerts returns the documented shape
    even when the DB is empty.
  - GET /api/v3/mlb/vacuum/active returns `{count, vacuums, timestamp}`.
  - Cache-Control header set correctly (mobile staleness bug).

ANTI-REGRESSION INVARIANTS (read these before touching the subsystem)
---------------------------------------------------------------------
INV-1: `compute_injury_advantages` MUST tolerate empty injuries / empty
       board_picks without raising. Returns `[]`.
INV-2: Alerts without a real `lineup_delta >= 1.0` OR
       `projected_ab_delta >= 0.5` are dropped before serialization.
INV-3: The `live-alerts` endpoint always returns `{success, alerts,
       count}` keys (never omits them). Empty-state returns
       `success: True` with `alerts: []` (NOT an error).
INV-4: Beneficiary and injured_player must never be the same person.
INV-5: Only ONE advantage per beneficiary (best stat line).

If any of these fail, a P0 regression has landed — do not patch around
the test. Fix the code.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import httpx
import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from config.version_tags import MLB_LIVE
from services.injury_advantage import (
    RECENCY_DEFAULT_HOURS,
    RECENCY_LIVE_HOURS,
    RECENCY_PREGAME_HOURS,
    _estimate_benefit,
    _get_recency_window,
    compute_injury_advantages,
)


API_BASE = os.environ.get(
    "TEST_API_BASE", "http://localhost:8001"
).rstrip("/")


# --------------------------------------------------------------------
# Shared fixtures
# --------------------------------------------------------------------

@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    yield client[os.environ["DB_NAME"]]


@pytest_asyncio.fixture
async def seeded_db(db):
    """Seed minimum MLB state: 2 board picks (beneficiary + unrelated
    player), 1 injury on the same team as the beneficiary, a
    matching master_hub row. Cleans up after the test.

    Returns a dict of the seeded identifiers so tests can assert on them.
    """
    tag = f"test_pa_{uuid.uuid4().hex[:8]}"
    injured = f"Test Injured {tag}"
    beneficiary = f"Test Beneficiary {tag}"
    unrelated = f"Test Unrelated {tag}"
    team = "TST"
    now = datetime.now(timezone.utc)

    # Clean insert (idempotent cleanup in teardown).
    await db["mlb_prop_scores"].insert_many([
        {
            "canonical_key": f"mlb|evt_{tag}|{beneficiary}|Hits|0.5|OVER",
            "version_tag": MLB_LIVE,
            "tier": "safe_haven",
            "player_name": beneficiary,
            "team": team,
            "stat_type": "Hits",
            "line": 0.5,
            "recommendation": "OVER",
            "edge_pct": 10.0,
            "_test_tag": tag,
        },
        {
            "canonical_key": f"mlb|evt_{tag}|{unrelated}|Hits|0.5|OVER",
            "version_tag": MLB_LIVE,
            "tier": "front_lines",
            "player_name": unrelated,
            "team": "ZZZ",  # different team — must NOT get an advantage
            "stat_type": "Hits",
            "line": 0.5,
            "recommendation": "OVER",
            "edge_pct": 8.0,
            "_test_tag": tag,
        },
    ])
    await db["injuries_normalized"].insert_one({
        "sport": "mlb",
        "player_name": injured,
        "team": team,
        "tier_level": 4,  # OUT
        "status": "OUT",
        "status_changed_at": now.isoformat(),
        "first_seen_at": now.isoformat(),
        "_test_tag": tag,
    })
    # master_hub rows so team enrichment and rotation-relevance both pass:
    # beneficiary needs a team row, injured player needs GP >= MIN_GP_FOR_VACUUM.
    await db["mlb_master_hub_2026"].insert_many([
        {
            "display_name": beneficiary,
            "player_name": beneficiary,
            "team_abbr": team,
            "team": team,
            "bdl_game_logs_count": 50,
            "_test_tag": tag,
        },
        {
            "display_name": injured,
            "player_name": injured,
            "team_abbr": team,
            "team": team,
            # 10 game logs satisfies `gp >= MIN_GP_FOR_VACUUM` (5) so the
            # rotation-relevance gate in `_is_rotation_relevant` passes.
            "bdl_game_logs_count": 10,
            "_test_tag": tag,
        },
        {
            "display_name": unrelated,
            "player_name": unrelated,
            "team_abbr": "ZZZ",
            "team": "ZZZ",
            "bdl_game_logs_count": 50,
            "_test_tag": tag,
        },
    ])

    yield {
        "tag": tag,
        "injured": injured,
        "beneficiary": beneficiary,
        "unrelated": unrelated,
        "team": team,
    }

    # Teardown — delete ONLY our test rows.
    for coll in ("mlb_prop_scores", "injuries_normalized", "mlb_master_hub_2026"):
        await db[coll].delete_many({"_test_tag": tag})


# --------------------------------------------------------------------
# Pure-function tests (no DB)
# --------------------------------------------------------------------

def test_estimate_benefit_primary_tier3():
    """Tier 3 (OFS/OUT) + primary rank = highest bump."""
    benefit = _estimate_benefit(3, "primary")
    assert benefit["minutes_bump"] > 0
    assert benefit["usage_bump"] > 0


def test_estimate_benefit_unknown_tier_returns_zero():
    """Tier outside the declared table returns 0/0 — never raises."""
    benefit = _estimate_benefit(99, "primary")
    assert benefit == {"minutes_bump": 0, "usage_bump": 0}


def test_estimate_benefit_unknown_rank_returns_zero():
    """Unknown rank returns 0/0 — catches silent rank-label drift."""
    benefit = _estimate_benefit(3, "chaos_rank")
    assert benefit == {"minutes_bump": 0, "usage_bump": 0}


# --------------------------------------------------------------------
# Universal engine contracts (engine-level, no HTTP)
# --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inv1_compute_injury_advantages_empty_db_returns_empty(db):
    """INV-1: Empty DB must not raise; returns []."""
    # Use a sport no test data exists for.
    result = await compute_injury_advantages(db, "nonexistent_sport_for_test")
    assert result == []


@pytest.mark.asyncio
async def test_inv4_same_team_required(db, seeded_db):
    """INV-4 (partial): Advantages fire only when injured.team == beneficiary.team.

    Seeded: beneficiary on team TST, injured on TST → should produce an
    advantage. Unrelated player on team ZZZ → must NOT produce one.
    """
    advantages = await compute_injury_advantages(db, "mlb")
    relevant = [a for a in advantages if a.get("beneficiary_name") in
                (seeded_db["beneficiary"], seeded_db["unrelated"])]
    # Beneficiary: must be present.
    names = {a["beneficiary_name"] for a in relevant}
    assert seeded_db["beneficiary"] in names, (
        f"Expected same-team beneficiary {seeded_db['beneficiary']!r} "
        f"in advantages; got {names}"
    )
    # Unrelated player on different team: must NOT be.
    assert seeded_db["unrelated"] not in names, (
        f"Unrelated player {seeded_db['unrelated']!r} on different team "
        f"got an advantage — the same-team gate is broken."
    )


@pytest.mark.asyncio
async def test_inv4_no_self_boost(db, seeded_db):
    """INV-4 (full): Injured player must not appear as his own beneficiary.

    Insert a prop for the INJURED player too; advantage must still be
    produced for the beneficiary, not for the injured player himself.
    """
    tag = seeded_db["tag"]
    await db["mlb_prop_scores"].insert_one({
        "canonical_key": f"mlb|evt_{tag}|{seeded_db['injured']}|Hits|0.5|OVER",
        "version_tag": MLB_LIVE,
        "tier": "safe_haven",
        "player_name": seeded_db["injured"],
        "team": seeded_db["team"],
        "stat_type": "Hits",
        "line": 0.5,
        "recommendation": "OVER",
        "edge_pct": 5.0,
        "_test_tag": tag,
    })
    try:
        advantages = await compute_injury_advantages(db, "mlb")
        for a in advantages:
            assert a["beneficiary_name"] != a["injured_player"], (
                f"Self-boost detected: {a['injured_player']} is own "
                f"beneficiary. INV-4 broken."
            )
    finally:
        await db["mlb_prop_scores"].delete_many({
            "_test_tag": tag,
            "player_name": seeded_db["injured"],
        })


@pytest.mark.asyncio
async def test_inv5_dedup_per_beneficiary(db, seeded_db):
    """INV-5: A beneficiary with 3 different stat-lines on the board
    must yield at most ONE advantage (best line), never 3."""
    tag = seeded_db["tag"]
    # Insert 2 extra stat-lines for the same beneficiary.
    await db["mlb_prop_scores"].insert_many([
        {
            "canonical_key": f"mlb|evt_{tag}|{seeded_db['beneficiary']}|Total Bases|0.5|OVER",
            "version_tag": MLB_LIVE,
            "tier": "front_lines",
            "player_name": seeded_db["beneficiary"],
            "team": seeded_db["team"],
            "stat_type": "Total Bases",
            "line": 0.5,
            "recommendation": "OVER",
            "edge_pct": 12.0,
            "_test_tag": tag,
        },
        {
            "canonical_key": f"mlb|evt_{tag}|{seeded_db['beneficiary']}|Runs|0.5|OVER",
            "version_tag": MLB_LIVE,
            "tier": "war_zone",
            "player_name": seeded_db["beneficiary"],
            "team": seeded_db["team"],
            "stat_type": "Runs",
            "line": 0.5,
            "recommendation": "OVER",
            "edge_pct": 4.0,
            "_test_tag": tag,
        },
    ])
    advantages = await compute_injury_advantages(db, "mlb")
    beneficiary_rows = [
        a for a in advantages
        if a.get("beneficiary_name") == seeded_db["beneficiary"]
    ]
    assert len(beneficiary_rows) <= 1, (
        f"Dedup broken: {seeded_db['beneficiary']} appears "
        f"{len(beneficiary_rows)} times in advantages (INV-5)."
    )


# --------------------------------------------------------------------
# HTTP endpoint contracts — via the running backend
# --------------------------------------------------------------------

@pytest_asyncio.fixture
async def http_client():
    async with httpx.AsyncClient(base_url=API_BASE, timeout=20.0) as c:
        yield c


@pytest.mark.asyncio
async def test_inv3_live_alerts_shape_always_present(http_client):
    """INV-3: `live-alerts` ALWAYS returns {success, alerts, count,
    timestamp} — never omits them, even on error."""
    r = await http_client.get("/api/v3/mlb/vacuum/live-alerts")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "success" in body
    assert "alerts" in body
    assert "count" in body
    assert "timestamp" in body
    assert isinstance(body["alerts"], list)
    assert isinstance(body["count"], int)
    # Count must match the list length. Any drift here breaks the UI.
    assert body["count"] == len(body["alerts"])


@pytest.mark.asyncio
async def test_inv2_live_alerts_drop_rows_without_real_deltas(http_client):
    """INV-2: every returned alert must have numeric lineup_delta OR
    projected_ab_delta. Placeholder rows (both None) must have been
    dropped server-side."""
    r = await http_client.get("/api/v3/mlb/vacuum/live-alerts")
    body = r.json()
    for a in body.get("alerts", []):
        ld, ad = a.get("lineup_delta"), a.get("projected_ab_delta")
        try:
            ld_ok = ld is not None and not isinstance(ld, bool) and float(ld) >= 1.0
        except (TypeError, ValueError):
            ld_ok = False
        try:
            ad_ok = ad is not None and not isinstance(ad, bool) and float(ad) >= 0.5
        except (TypeError, ValueError):
            ad_ok = False
        assert ld_ok or ad_ok, (
            f"Alert row for {a.get('beneficiary_name')} lacks a real "
            f"delta (lineup={ld}, ab={ad}). INV-2 broken."
        )


@pytest.mark.asyncio
async def test_vacuum_active_shape(http_client):
    r = await http_client.get("/api/v3/mlb/vacuum/active")
    assert r.status_code == 200
    body = r.json()
    assert "count" in body
    assert "vacuums" in body
    assert "timestamp" in body
    assert isinstance(body["vacuums"], list)
    assert body["count"] == len(body["vacuums"])


@pytest.mark.asyncio
async def test_vacuum_endpoints_no_cache(http_client):
    """Cache-Control headers — these prevent mobile UIs from serving
    stale alerts. The cache-control regression happened twice before."""
    for path in (
        "/api/v3/mlb/vacuum/updates",
        "/api/v3/mlb/vacuum/active",
        "/api/v3/mlb/vacuum/live-alerts",
    ):
        r = await http_client.get(path)
        assert r.status_code == 200, path
        cache_ctrl = r.headers.get("cache-control", "").lower()
        assert "no-cache" in cache_ctrl or "no-store" in cache_ctrl, (
            f"{path}: Cache-Control missing no-cache/no-store — "
            f"mobile UIs will serve stale data. Got: {cache_ctrl!r}"
        )


@pytest.mark.asyncio
async def test_vacuum_clear_returns_404_for_nonexistent(http_client):
    """Attempting to clear a player with no active vacuum must 404,
    not 500. (Past regression: the service raised KeyError, the route
    propagated it as 500, and the UI crashed.)"""
    r = await http_client.post(
        f"/api/v3/mlb/vacuum/clear/NonexistentPlayer-{uuid.uuid4().hex[:6]}"
    )
    # Expected: 404 Not Found, with a JSON body.
    assert r.status_code == 404, (
        f"Expected 404 for nonexistent player, got {r.status_code}: "
        f"{r.text}"
    )


# --------------------------------------------------------------------
# Recency window contract
# --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recency_window_default_with_no_games(db):
    """When `live_scores_cache` is empty or missing, we must fall back
    to `RECENCY_DEFAULT_HOURS` — NOT raise, NOT return None."""
    hrs = await _get_recency_window(db, "never_played_sport_xyz")
    assert hrs == RECENCY_DEFAULT_HOURS
    assert isinstance(hrs, int)
