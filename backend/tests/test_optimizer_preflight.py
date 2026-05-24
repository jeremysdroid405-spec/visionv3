"""
Tests for the optimizer's tier-by-odds routing + preflight.

Tier routing in the optimizer mirrors the live runner exactly
(`services/scoring/gates/thresholds.py::resolve_target_tier`):

  safe_haven  : odds ≤ -300        (heavy chalk)
  front_lines : -299 ≤ odds ≤ 149   (mid range)
  war_zone    : odds ≥ 150          (longshot)

Rows are routed by their American-odds value — NOT by the
`{tier}_pass` boolean, which depends on live `book_count` data that
historical replay rows don't carry (every historical row fails
`coverage_gate`, so every `{tier}_pass` is False, which starved the
optimizer of samples). `enforce_tier_gates=True` is an opt-in flag
that adds the prod gate-pass on top of the odds-range routing —
useful for parity validation, never the default.
"""
from __future__ import annotations
import os
import sys
import uuid

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from httpx import AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")

BACKEND_URL  = os.environ.get("BACKEND_URL", "http://127.0.0.1:8001")
HTTP_TIMEOUT = 30.0
TAG          = f"_pytest_opt_{uuid.uuid4().hex[:8]}"


def _auth() -> dict:
    return {"X-Admin-Token": os.environ["EMERGENT_ADMIN_TOKEN"],
              "X-Agent-Id": "pytest"}


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    d = client[os.environ["DB_NAME"]]
    yield d
    await d["sgo_propvision_full_pipeline_replay"].delete_many(
        {"_pytest_tag": TAG})
    client.close()


async def _seed_rows_spanning_all_tiers(db):
    """20 safe_haven (odds=-350) + 60 front_lines (odds=-110) + 20
    war_zone (odds=+200). 30% graded across the board. Half the rows
    flag `safe_haven_pass=True` so we can also exercise the opt-in
    `enforce_tier_gates` filter on top of odds routing."""
    docs = []
    # 20 safe_haven rows (odds = -350)
    for i in range(20):
        docs.append({
            "_pytest_tag": TAG, "league_id": "PFTEST",
            "game_date": "2099-08-01",
            "stat_family": "fam_X", "odds_bucket": "odds_lt_-200",
            "odds": -350, "side": "OVER", "line": 0.5,
            "event_id": f"sh_{i}", "player_name_normalized": f"sh{i}",
            "safe_haven_pass": (i < 5), "front_lines_pass": False,
            "war_zone_pass": False,
            "outcome_numeric": (1 if i % 3 == 0 else 0) if i < 6 else None,
            "hit_rate_l20": 0.7, "cv": 0.4, "edge": 0.10,
            "model_probability": 0.6, "tp": 0.6,
        })
    # 60 front_lines rows (odds = -110)
    for i in range(60):
        docs.append({
            "_pytest_tag": TAG, "league_id": "PFTEST",
            "game_date": "2099-08-01",
            "stat_family": "fam_X", "odds_bucket": "odds_-200_-100",
            "odds": -110, "side": "OVER", "line": 0.5,
            "event_id": f"fl_{i}", "player_name_normalized": f"fl{i}",
            "safe_haven_pass": False, "front_lines_pass": (i < 12),
            "war_zone_pass": False,
            "outcome_numeric": (1 if i % 3 == 0 else 0) if i < 18 else None,
            "hit_rate_l20": 0.7, "cv": 0.4, "edge": 0.10,
            "model_probability": 0.6, "tp": 0.6,
        })
    # 20 war_zone rows (odds = +200)
    for i in range(20):
        docs.append({
            "_pytest_tag": TAG, "league_id": "PFTEST",
            "game_date": "2099-08-01",
            "stat_family": "fam_X", "odds_bucket": "odds_+150_+300",
            "odds": +200, "side": "OVER", "line": 0.5,
            "event_id": f"wz_{i}", "player_name_normalized": f"wz{i}",
            "safe_haven_pass": False, "front_lines_pass": False,
            "war_zone_pass": (i < 8),
            "outcome_numeric": (1 if i % 3 == 0 else 0) if i < 6 else None,
            "hit_rate_l20": 0.7, "cv": 0.4, "edge": 0.10,
            "model_probability": 0.6, "tp": 0.6,
        })
    await db["sgo_propvision_full_pipeline_replay"].insert_many(docs)


@pytest.mark.asyncio
async def test_preflight_routes_rows_to_tiers_by_odds_range(db):
    """The DEFAULT tier filter is purely by odds — each tier sees
    only the rows in its odds range, NOT the full pool."""
    await _seed_rows_spanning_all_tiers(db)
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.post("/api/emergent-admin/optimizer/preflight",
                            headers=_auth(),
                            json={"sport": "PFTEST",
                                    "start": "2099-08-01",
                                    "end":   "2099-08-01"})
    body = r.json()
    assert body["n_total_in_window"] == 100
    by_tier = {t["tier"]: t for t in body["by_tier"]}
    assert by_tier["safe_haven"]["n_rows"]  == 20
    assert by_tier["front_lines"]["n_rows"] == 60
    assert by_tier["war_zone"]["n_rows"]    == 20
    # Tiers must NOT overlap — sum equals total rows in window.
    assert (by_tier["safe_haven"]["n_rows"]
              + by_tier["front_lines"]["n_rows"]
              + by_tier["war_zone"]["n_rows"]) == 100


@pytest.mark.asyncio
async def test_preflight_enforce_tier_gates_adds_pass_filter_on_top(db):
    """`enforce_tier_gates=True` adds `{tier}_pass=True` ON TOP of
    odds routing. Counts must shrink to rows that match BOTH."""
    await _seed_rows_spanning_all_tiers(db)
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.post("/api/emergent-admin/optimizer/preflight",
                            headers=_auth(),
                            json={"sport": "PFTEST",
                                    "start": "2099-08-01",
                                    "end":   "2099-08-01",
                                    "enforce_tier_gates": True})
    body = r.json()
    by_tier = {t["tier"]: t for t in body["by_tier"]}
    assert by_tier["safe_haven"]["n_rows"]  == 5
    assert by_tier["front_lines"]["n_rows"] == 12
    assert by_tier["war_zone"]["n_rows"]    == 8


@pytest.mark.asyncio
async def test_preflight_unqualified_odds_excluded_from_all_tiers(db):
    """Rows with `odds=null` route to no tier. Total in window may
    include them, but per-tier counts must sum to total ONLY if
    every row has odds."""
    # 10 rows with odds=null + 10 rows with odds=-110
    docs = []
    for i in range(10):
        docs.append({
            "_pytest_tag": TAG, "league_id": "PFTEST3",
            "game_date": "2099-08-03",
            "stat_family": "fam_Y", "odds_bucket": "odds_na",
            "odds": None, "side": "OVER", "line": 0.5,
            "event_id": f"na_{i}", "outcome_numeric": None,
        })
    for i in range(10):
        docs.append({
            "_pytest_tag": TAG, "league_id": "PFTEST3",
            "game_date": "2099-08-03",
            "stat_family": "fam_Y", "odds_bucket": "odds_-200_-100",
            "odds": -110, "side": "OVER", "line": 0.5,
            "event_id": f"fl_{i}", "outcome_numeric": 1 if i < 5 else 0,
        })
    await db["sgo_propvision_full_pipeline_replay"].insert_many(docs)
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.post("/api/emergent-admin/optimizer/preflight",
                            headers=_auth(),
                            json={"sport": "PFTEST3",
                                    "start": "2099-08-03",
                                    "end":   "2099-08-03"})
    body = r.json()
    assert body["n_total_in_window"] == 20
    by_tier = {t["tier"]: t for t in body["by_tier"]}
    # null-odds rows are NOT routed to any tier
    assert by_tier["safe_haven"]["n_rows"]  == 0
    assert by_tier["front_lines"]["n_rows"] == 10
    assert by_tier["war_zone"]["n_rows"]    == 0


@pytest.mark.asyncio
async def test_preflight_empty_window_diagnoses_clearly(db):
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.post("/api/emergent-admin/optimizer/preflight",
                            headers=_auth(),
                            json={"sport": "NONEXISTENT",
                                    "start": "2099-12-01",
                                    "end":   "2099-12-01"})
    body = r.json()
    assert body["n_total_in_window"] == 0
    assert "No rows" in body["diagnosis"]


@pytest.mark.asyncio
async def test_preflight_low_graded_pct_warns_about_join_failure(db):
    """When < 1% of rows are graded, the diagnosis must point at the
    join-diagnose endpoint, not the optimizer."""
    docs = []
    for i in range(100):
        docs.append({
            "_pytest_tag": TAG, "league_id": "PFTEST2",
            "game_date": "2099-08-02",
            "stat_family": "fam_X", "odds_bucket": "odds_-200_-100",
            "outcome_numeric": None,  # ungraded
            "odds": -110, "side": "OVER", "line": 0.5,
        })
    await db["sgo_propvision_full_pipeline_replay"].insert_many(docs)
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.post("/api/emergent-admin/optimizer/preflight",
                            headers=_auth(),
                            json={"sport": "PFTEST2",
                                    "start": "2099-08-02",
                                    "end":   "2099-08-02"})
    body = r.json()
    assert body["n_graded"] == 0
    assert body["pct_graded"] == 0.0
    assert ("join-diagnose" in body["diagnosis"]
              or "graded" in body["diagnosis"])
