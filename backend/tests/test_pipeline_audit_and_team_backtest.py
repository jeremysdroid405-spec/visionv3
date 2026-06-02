"""Tests for the pipeline-audit endpoint + team backtest orchestrator.

The pipeline-audit endpoint surfaces the locked 4-quadrant health
snapshot. The orchestrator script imports and dry-runs cleanly.
"""
from __future__ import annotations

import os
import asyncio
import pytest
from motor.motor_asyncio import AsyncIOMotorClient


def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.mark.asyncio
async def test_pipeline_audit_returns_four_quadrants():
    """Smoke test the audit endpoint returns the 4 quadrants the
    locked 2-pipeline contract requires."""
    from routes.pipeline_audit import (
        get_pipeline_audit, set_pipeline_audit_db,
    )
    set_pipeline_audit_db(_db())
    out = await get_pipeline_audit()
    quadrants = out.get("quadrants") or {}
    assert set(quadrants.keys()) == {
        "live_player", "live_team",
        "backtest_player", "backtest_team",
    }, f"unexpected quadrants: {sorted(quadrants.keys())}"
    # Health gradient must cover all 4 quadrants.
    health = out.get("health") or {}
    assert set(health.keys()) == set(quadrants.keys())
    for q, h in health.items():
        assert h in {"green", "amber", "red"}, (
            f"unexpected health value for {q}: {h!r}"
        )


@pytest.mark.asyncio
async def test_pipeline_audit_carries_registry_snapshot():
    """The endpoint must echo `PIPELINE_REGISTRY` so dashboards can
    show the canonical contract without re-importing the module."""
    from routes.pipeline_audit import (
        get_pipeline_audit, set_pipeline_audit_db,
    )
    set_pipeline_audit_db(_db())
    out = await get_pipeline_audit()
    reg = out.get("pipeline_registry") or {}
    assert set(reg.keys()) == {"player", "team"}, (
        f"PIPELINE_REGISTRY shape drift in audit response: "
        f"{sorted(reg.keys())}"
    )
    for pipeline in ("player", "team"):
        assert {"live", "backtest"}.issubset(reg[pipeline].keys()), (
            f"Pipeline {pipeline} missing live/backtest in audit "
            f"response. Got: {sorted(reg[pipeline].keys())}"
        )


def test_run_team_backtest_imports_cleanly():
    """The team backtest orchestrator MUST import the SAME team
    predictor `score_team_props_batch` the live scorer wraps —
    enforces Rule 4 of the 2-pipeline contract (no separate replay
    scorer for teams)."""
    import importlib
    mod = importlib.import_module("scripts.sgo.run_team_backtest")
    assert hasattr(mod, "run_sport"), "run_team_backtest missing run_sport()"
    src = open(mod.__file__).read()
    assert "from services.team_xgb_loader import score_team_props_batch" in src, (
        "Team backtest orchestrator must import "
        "`services.team_xgb_loader.score_team_props_batch` — the "
        "same predictor wrapped by the live scorer. No separate "
        "replay-only scorer per the locked pipeline contract."
    )


def test_mirror_team_replay_imports_cleanly():
    """The team mirror script lands rows in the shared optimizer
    collection — the same one the player mirror writes to."""
    import importlib
    mod = importlib.import_module(
        "scripts.sgo.mirror_team_replay_to_unified")
    assert hasattr(mod, "UNIFIED_COLL")
    assert mod.UNIFIED_COLL == "sgo_propvision_full_pipeline_replay", (
        "Team mirror must write to the unified collection "
        "`sgo_propvision_full_pipeline_replay` so player + team rows "
        "share one optimizer dataset (Rule 1 of the locked "
        "pipeline contract)."
    )
