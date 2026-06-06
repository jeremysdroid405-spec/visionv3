"""
Pytest contract for /api/v3/pipeline-audit/team-ssot.

Validates response shape + every per-sport stage's field surface and the
red/yellow/green health roll-up logic. Runs in-process via FastAPI's
TestClient so no live HTTP/server is required.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, "/app/backend")

EXPECTED_STAGES = (
    "ingest", "features", "outcomes", "feature_cache",
    "score", "reshape", "replay", "grid",
)
EXPECTED_MARKET_CATEGORIES = ("h2h", "spread", "game_total", "team_total")
EXPECTED_HEALTH = {"green", "yellow", "red"}


@pytest.fixture
def client():
    """Spin up the audit router with the production DB so the contract
    test reflects current data. Read-only — no writes touch Mongo.

    Function-scoped to avoid the "Event loop is closed" issue when a
    module-scoped motor client gets reused across multiple test
    requests (TestClient runs each request on its own loop)."""
    from motor.motor_asyncio import AsyncIOMotorClient
    from routes.team_ssot_audit import router, set_team_ssot_audit_db
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ["DB_NAME"]]
    set_team_ssot_audit_db(db)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    with TestClient(app) as c:
        yield c
    db.client.close()


def test_endpoint_returns_200(client):
    r = client.get("/api/v3/pipeline-audit/team-ssot")
    assert r.status_code == 200, r.text


def test_top_level_shape(client):
    j = client.get("/api/v3/pipeline-audit/team-ssot").json()
    for key in ("generated_at", "now_iso", "stale_thresholds",
                  "sports", "scheduler", "summary"):
        assert key in j, f"missing top-level key {key!r}"
    assert isinstance(j["sports"], list) and len(j["sports"]) == 3


def test_stale_thresholds_have_expected_keys(client):
    j = client.get("/api/v3/pipeline-audit/team-ssot").json()
    st = j["stale_thresholds"]
    for k in ("team_live_props_min", "team_prop_scores_min",
                "optimizer_run_hours"):
        assert k in st
        assert isinstance(st[k], int)


def test_each_sport_carries_every_stage(client):
    j = client.get("/api/v3/pipeline-audit/team-ssot").json()
    leagues = {s["league"] for s in j["sports"]}
    assert leagues == {"MLB", "NBA", "NFL"}
    for sp in j["sports"]:
        assert set(sp["stages"].keys()) == set(EXPECTED_STAGES), (
            f"{sp['league']} missing stages: "
            f"{set(EXPECTED_STAGES) - set(sp['stages'].keys())}")
        assert sp["health"] in EXPECTED_HEALTH


def test_score_stage_lists_all_market_categories(client):
    j = client.get("/api/v3/pipeline-audit/team-ssot").json()
    for sp in j["sports"]:
        adapters = sp["stages"]["score"]["adapters"]
        assert set(adapters.keys()) == set(EXPECTED_MARKET_CATEGORIES), (
            f"{sp['league']} score adapters: {adapters.keys()}")
        for mc, ok in adapters.items():
            assert isinstance(ok, bool)


def test_replay_stage_exposes_coverage_percent(client):
    j = client.get("/api/v3/pipeline-audit/team-ssot").json()
    for sp in j["sports"]:
        r = sp["stages"]["replay"]
        assert set(r.keys()) >= {
            "reshape_count", "scored_count", "scored_pct", "status"}
        assert isinstance(r["scored_pct"], (int, float))
        if r["reshape_count"] > 0:
            # pct must lie in [0, 100]
            assert 0.0 <= r["scored_pct"] <= 100.0


def test_grid_stage_carries_latest_run_metadata(client):
    j = client.get("/api/v3/pipeline-audit/team-ssot").json()
    for sp in j["sports"]:
        g = sp["stages"]["grid"]
        assert "latest_run_id" in g
        assert "latest_started_at" in g
        assert "age_hours" in g


def test_scheduler_state_block_exposed(client):
    j = client.get("/api/v3/pipeline-audit/team-ssot").json()
    sched = j["scheduler"]
    for k in ("paused", "manual_override", "reason",
                "set_by", "set_at", "warning"):
        assert k in sched
    assert isinstance(sched["paused"], bool)
    assert isinstance(sched["manual_override"], bool)


def test_summary_roll_up(client):
    j = client.get("/api/v3/pipeline-audit/team-ssot").json()
    s = j["summary"]
    assert s["overall_health"] in EXPECTED_HEALTH
    assert isinstance(s["gap_count"], int) and s["gap_count"] >= 0
    assert isinstance(s["warning_count"], int) and s["warning_count"] >= 0
    # Roll-up consistency: any sport red → overall red
    healths = {sp["health"] for sp in j["sports"]}
    if "red" in healths:
        assert s["overall_health"] == "red"
    elif "yellow" in healths or j["scheduler"].get("paused"):
        assert s["overall_health"] in {"yellow", "red"}


def test_warning_count_matches_per_sport_warnings_plus_scheduler(client):
    j = client.get("/api/v3/pipeline-audit/team-ssot").json()
    per_sport_total = sum(len(sp["warnings"]) for sp in j["sports"])
    if j["scheduler"]["paused"]:
        per_sport_total += 1
    assert j["summary"]["warning_count"] == per_sport_total


def test_stages_have_latest_dates_when_populated(client):
    """If a stage has count>0 it must publish a `latest_date` (or
    `latest_iso` for ingest). Otherwise the operator can't tell
    'fresh' from 'stale' in the UI."""
    j = client.get("/api/v3/pipeline-audit/team-ssot").json()
    for sp in j["sports"]:
        st = sp["stages"]
        if st["features"]["count"] > 0:
            assert st["features"]["latest_date"] is not None
        if st["outcomes"]["count"] > 0:
            assert st["outcomes"]["latest_date"] is not None
        if st["feature_cache"]["count"] > 0:
            assert st["feature_cache"]["latest_date"] is not None
        if st["reshape"]["count"] > 0:
            assert st["reshape"]["latest_date"] is not None
        if st["ingest"]["count"] > 0:
            assert st["ingest"]["latest_iso"] is not None
