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


def test_unified_collection_is_team_backtest_ssot():
    """The team backtest pipeline reads from the UNIFIED collection
    `sgo_propvision_full_pipeline_replay` (partitioned by
    `prop_type=team`) — NOT a separate `team_replay_model_outputs`
    collection. Locked 2026-06-02 after a duplicate-path drift was
    rolled back.

    Production audit (5,787,461 rows total in unified) was confirmed
    SSOT. Any future code reintroducing `run_team_backtest.py` or
    `mirror_team_replay_to_unified.py` is architecture drift.
    """
    from services.replay.contract import PIPELINE_REGISTRY
    bt = PIPELINE_REGISTRY["team"]["backtest"]
    assert bt.get("optimizer_collection") == \
            "sgo_propvision_full_pipeline_replay", (
        "Team backtest SSOT MUST be the unified collection. "
        f"Got: {bt.get('optimizer_collection')!r}"
    )
    assert bt.get("row_filter") == {"prop_type": "team"}, (
        f"Team backtest row filter drifted: {bt.get('row_filter')!r}"
    )
    assert bt.get("model_version") == "team_xgb_v1", (
        "Unified rows are scored by `team_xgb_v1` (the SAME live "
        "team model). If the model_version changes, the live "
        "scorer and unified rows must move together — drift "
        f"detected: {bt.get('model_version')!r}"
    )


def test_deprecated_team_backtest_scripts_are_removed():
    """Negative test: `run_team_backtest.py` and
    `mirror_team_replay_to_unified.py` were a duplicate path that
    overlapped with the unified SSOT. They were removed 2026-06-02
    after a production audit confirmed unified is sufficient. Reject
    any reintroduction at the file-system level."""
    import os
    deleted = [
        "/app/backend/scripts/sgo/run_team_backtest.py",
        "/app/backend/scripts/sgo/mirror_team_replay_to_unified.py",
    ]
    for p in deleted:
        assert not os.path.exists(p), (
            f"{p} was deleted as a duplicate-path drift; "
            "re-introducing it duplicates the unified optimizer "
            "dataset. Use `sgo_propvision_full_pipeline_replay` "
            "with `prop_type=team` instead."
        )
