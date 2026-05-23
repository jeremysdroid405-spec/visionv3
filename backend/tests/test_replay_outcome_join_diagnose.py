"""
Integration test for the join-diagnostic endpoint.

Seeds a controlled (replay, outcomes) pair where the `line` field is a
float on one side and a string on the other — the exact failure pattern
that explained the user's "8,693 replay rows, 46 graded (0.5%)" report.
Verifies the endpoint correctly identifies `line` as the failing key.
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
HTTP_TIMEOUT = 20.0
TAG          = f"_pytest_join_{uuid.uuid4().hex[:8]}"


def _auth() -> dict:
    return {"X-Admin-Token": os.environ["EMERGENT_ADMIN_TOKEN"],
              "X-Agent-Id": "pytest"}


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    d = client[os.environ["DB_NAME"]]
    yield d
    await d["sgo_propvision_full_pipeline_replay"].delete_many({"_pytest_tag": TAG})
    await d["sgo_pp_research_outcomes"].delete_many({"_pytest_tag": TAG})
    client.close()


@pytest.mark.asyncio
async def test_join_diagnose_identifies_line_type_mismatch(db):
    """Seed 10 replay rows w/ line=2.5 (float) and 10 outcomes w/
    line='2.5' (string). The endpoint must report `line` as the
    failing key with a clear jump in match-rate when line is dropped."""
    coll_r = db["sgo_propvision_full_pipeline_replay"]
    coll_o = db["sgo_pp_research_outcomes"]
    for i in range(10):
        eid = f"evt_jt_{i}_{TAG}"
        await coll_r.insert_one({
            "_pytest_tag": TAG, "league_id": "TEST",
            "game_date": "2099-09-01",
            "event_id": eid,
            "player_name_normalized": f"p{i}",
            "market": "hits", "line": 2.5, "side": "OVER", "odds": -110,
            "outcome_resolved": False, "outcome_numeric": None,
        })
        await coll_o.insert_one({
            "_pytest_tag": TAG, "league_id": "TEST",
            "game_date": "2099-09-01",
            "event_id": eid,
            "player_name_normalized": f"p{i}",
            "market": "hits", "line": "2.5", "side": "OVER",
            "outcome_resolved": True, "outcome_numeric": 1,
        })
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.get("/api/emergent-admin/research/replay-outcome-join-diagnose",
                            headers=_auth(),
                            params={"sport": "TEST",
                                       "start": "2099-09-01",
                                       "end":   "2099-09-01",
                                       "sample_size": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    rates = body["match_rates_pct"]
    # K0 full join must fail (line type mismatch); K1 (line dropped) must succeed.
    assert rates["K0_full"] == 0.0
    assert rates["K1_no_line"] == 100.0
    assert "line" in body["diagnosis"]
    # We must surface at least one side-by-side mismatch so the operator
    # can see "float vs str" immediately.
    assert body["sample_mismatches"]
    first = body["sample_mismatches"][0]
    assert first["replay"]["line_type"] == "float"
    assert first["outcome"]["line_type"] == "str"


@pytest.mark.asyncio
async def test_join_diagnose_no_outcomes_in_window(db):
    """When outcomes simply don't exist in the window, K4 (event_id
    only) should be 0% and the diagnosis must say so."""
    await db["sgo_propvision_full_pipeline_replay"].insert_one({
        "_pytest_tag": TAG, "league_id": "TEST", "game_date": "2099-09-02",
        "event_id": f"orph_{TAG}", "player_name_normalized": "orphan",
        "market": "hits", "line": 1.5, "side": "OVER", "odds": -110,
        "outcome_resolved": False, "outcome_numeric": None,
    })
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.get("/api/emergent-admin/research/replay-outcome-join-diagnose",
                            headers=_auth(),
                            params={"sport": "TEST",
                                       "start": "2099-09-02",
                                       "end":   "2099-09-02",
                                       "sample_size": 1})
    body = r.json()
    assert body["match_rates_pct"]["K4_event_only"] == 0.0
    assert "event_id" in body["diagnosis"] or "don't exist" in body["diagnosis"]
