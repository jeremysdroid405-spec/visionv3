"""
Tests for the optimizer's enforce_tier_gates behavior + preflight.

This is the bug that produced "succeeded but no results" the operator
saw on prod: requiring `{tier}_pass=True` on rows where production
gates rarely pass leaves the optimizer with nothing to score.

We default `enforce_tier_gates=False` so tier becomes a label, NOT a
hard filter. A preflight endpoint shows the operator exactly what
each tier will see BEFORE the run kicks off.
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
    await d["sgo_propvision_full_pipeline_replay"].delete_many({"_pytest_tag": TAG})
    client.close()


async def _seed_realistic_rows(db, *, n=100):
    """Mimic the prod failure pattern: most rows fail safe_haven gates,
    only war_zone has a healthy sample, 30% of rows are graded."""
    docs = []
    for i in range(n):
        graded = i < int(n * 0.30)
        sh = i < int(n * 0.05)
        fl = i < int(n * 0.20)
        wz = i < int(n * 0.60)
        docs.append({
            "_pytest_tag": TAG, "league_id": "PFTEST",
            "game_date": "2099-08-01",
            "stat_family": "fam_X", "odds_bucket": "odds_-200_-100",
            "safe_haven_pass": sh, "front_lines_pass": fl, "war_zone_pass": wz,
            "outcome_numeric": (1 if graded and i % 2 == 0 else 0
                                    if graded else None),
            "odds": -110, "side": "OVER", "line": 0.5,
            "event_id": f"e_{i}", "player_name_normalized": f"p{i}",
            "hit_rate_l20": 0.7, "cv": 0.4, "edge": 0.10,
            "model_probability": 0.6, "tp": 0.6,
        })
    await db["sgo_propvision_full_pipeline_replay"].insert_many(docs)


@pytest.mark.asyncio
async def test_preflight_default_treats_tier_as_label(db):
    """Default `enforce_tier_gates=False` ⇒ every tier sees full pool."""
    await _seed_realistic_rows(db, n=100)
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.post("/api/emergent-admin/optimizer/preflight",
                            headers=_auth(),
                            json={"sport": "PFTEST",
                                    "start": "2099-08-01", "end": "2099-08-01"})
    body = r.json()
    assert body["n_total_in_window"] == 100
    assert body["n_graded"] == 30
    # All three tiers must see the same n in default mode
    by_tier = {t["tier"]: t for t in body["by_tier"]}
    assert by_tier["safe_haven"]["n_rows"] == 100
    assert by_tier["front_lines"]["n_rows"] == 100
    assert by_tier["war_zone"]["n_rows"] == 100
    assert body["diagnosis"].startswith("Healthy")


@pytest.mark.asyncio
async def test_preflight_strict_mode_surfaces_thin_safe_haven(db):
    """`enforce_tier_gates=True` ⇒ tier samples reflect real gate pass
    rate. Diagnosis must warn the operator when safe_haven is < 30."""
    await _seed_realistic_rows(db, n=100)
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.post("/api/emergent-admin/optimizer/preflight",
                            headers=_auth(),
                            json={"sport": "PFTEST", "start": "2099-08-01",
                                    "end": "2099-08-01",
                                    "enforce_tier_gates": True})
    body = r.json()
    by_tier = {t["tier"]: t for t in body["by_tier"]}
    assert by_tier["safe_haven"]["n_rows"] == 5
    assert by_tier["front_lines"]["n_rows"] == 20
    assert by_tier["war_zone"]["n_rows"] == 60
    assert "safe_haven" in body["diagnosis"]
    assert "enforce_tier_gates=false" in body["diagnosis"]


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
    """When < 1% of rows are graded, the diagnosis must point the
    operator at the join-diagnose endpoint, not at the optimizer."""
    # Seed 100 rows, all ungraded
    docs = []
    for i in range(100):
        docs.append({
            "_pytest_tag": TAG, "league_id": "PFTEST2",
            "game_date": "2099-08-02",
            "stat_family": "fam_X", "odds_bucket": "odds_-200_-100",
            "safe_haven_pass": True, "front_lines_pass": True,
            "war_zone_pass": True,
            "outcome_numeric": None,  # ← ungraded
            "odds": -110, "side": "OVER", "line": 0.5,
        })
    await db["sgo_propvision_full_pipeline_replay"].insert_many(docs)
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.post("/api/emergent-admin/optimizer/preflight",
                            headers=_auth(),
                            json={"sport": "PFTEST2",
                                    "start": "2099-08-02", "end": "2099-08-02"})
    body = r.json()
    assert body["n_graded"] == 0
    assert body["pct_graded"] == 0.0
    assert "join-diagnose" in body["diagnosis"] or "graded" in body["diagnosis"]
