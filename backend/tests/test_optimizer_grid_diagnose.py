"""
Locks in /grid-diagnose semantics and the wildcard threshold behavior.

The user's "I only see 4 of 14 families" was caused by the grid's
most-strict threshold (hr_l20_min ≥ 0.55) cutting families whose L20
hit rates sit below 55%. The wildcard sentinel `-inf` / `+inf` lets
the operator add "no-filter" cells to the sweep so thin families
still surface.
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

from routes.emergent_admin.optimizer import (  # noqa: E402
    DEFAULT_GRID, _row_passes_combo,
)

BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8001")
TAG         = f"_pytest_grid_{uuid.uuid4().hex[:6]}"


def _auth() -> dict:
    return {"X-Admin-Token": os.environ["EMERGENT_ADMIN_TOKEN"],
              "X-Agent-Id": "pytest"}


# ── Wildcard sentinel ───────────────────────────────────────────────
def test_default_grid_includes_wildcards():
    """Every numeric axis in DEFAULT_GRID must have a wildcard value
    so the optimizer can ask 'what's the best combo if I IGNORE this
    axis'. Without it, every combo applies all 6 filters, masking
    thin-but-real families."""
    assert float("-inf") in DEFAULT_GRID["hr_l20_min"]
    assert float("-inf") in DEFAULT_GRID["hr_l10_min"]
    assert float("-inf") in DEFAULT_GRID["hr_l5_min"]
    assert float("+inf") in DEFAULT_GRID["cv_max"]
    assert float("-inf") in DEFAULT_GRID["edge_min"]
    assert float("-inf") in DEFAULT_GRID["tp_min"]


def test_row_passes_combo_respects_wildcard_min_when_row_value_null():
    """Critical: a row missing a value (e.g. `hit_rate_l20`) must
    still pass the combo when the threshold for that axis is the
    wildcard. Previously row.get() returned None → function returned
    False → wildcard cells produced 0 rows."""
    row = {"hit_rate_l20": None, "cv": 0.5, "model_probability": 0.6,
              "hit_rate_l10": 0.65, "hit_rate_l5": 0.65, "edge": 0.05}
    combo = {"hr_l20_min": float("-inf"), "cv_max": 1.0,
                "tp_min": 0.5, "hr_l10_min": float("-inf"),
                "hr_l5_min": float("-inf"), "edge_min": 0.0}
    assert _row_passes_combo(row, combo) is True


def test_row_passes_combo_wildcard_max_when_row_value_null():
    row = {"hit_rate_l20": 0.65, "cv": None, "model_probability": 0.6,
              "hit_rate_l10": 0.65, "hit_rate_l5": 0.65, "edge": 0.05}
    combo = {"hr_l20_min": 0.55, "cv_max": float("+inf"),
                "tp_min": 0.5, "hr_l10_min": 0.5,
                "hr_l5_min": 0.5, "edge_min": 0.0}
    assert _row_passes_combo(row, combo) is True


def test_row_blocked_by_non_wildcard_threshold():
    """Sanity: when the row IS missing a value and the threshold is
    NOT a wildcard, the row must still be filtered out."""
    row = {"hit_rate_l20": None, "cv": 0.5, "model_probability": 0.6,
              "hit_rate_l10": 0.65, "hit_rate_l5": 0.65, "edge": 0.05}
    combo = {"hr_l20_min": 0.55,             # ← not wildcard
                "cv_max": 1.0, "tp_min": 0.5,
                "hr_l10_min": 0.5, "hr_l5_min": 0.5, "edge_min": 0.0}
    assert _row_passes_combo(row, combo) is False


# ── /grid-diagnose endpoint ──────────────────────────────────────
@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    d = client[os.environ["DB_NAME"]]
    yield d
    await d["sgo_propvision_full_pipeline_replay"].delete_many({"_pytest_tag": TAG})
    client.close()


async def _seed(db, *, n=200):
    import random
    random.seed(7)
    docs = []
    for i in range(n):
        docs.append({
            "_pytest_tag": TAG, "league_id": "GDTEST",
            "game_date": "2099-09-01",
            "stat_family": "hits", "odds_bucket": "odds_-200_-100",
            "outcome_numeric": (1 if i % 2 == 0 else 0),
            "odds": -150, "side": "OVER", "line": 0.5,
            "hit_rate_l20": 0.3 + (i % 50) * 0.01,
            "hit_rate_l10": 0.5, "hit_rate_l5": 0.5,
            "cv": 0.3 + (i % 20) * 0.05,
            "edge": 0.0 + (i % 30) * 0.005,
            "model_probability": 0.5 + (i % 25) * 0.01,
        })
    await db["sgo_propvision_full_pipeline_replay"].insert_many(docs)


@pytest.mark.asyncio
async def test_grid_diagnose_reports_per_threshold_pass_counts(db):
    await _seed(db)
    async with AsyncClient(base_url=BACKEND_URL, timeout=15.0) as c:
        r = await c.post("/api/emergent-admin/optimizer/grid-diagnose",
                            headers=_auth(),
                            json={"sport": "GDTEST",
                                    "start": "2099-09-01", "end": "2099-09-01"})
    body = r.json()
    assert body["n_graded"] == 200
    ax = body["axes"]["hr_l20_min"]
    assert ax["n_with_value"] == 200
    # Wildcard must pass 100% of rows
    wc = next(p for p in ax["per_threshold"] if p["threshold"] == "wildcard")
    assert wc["pct_pass"] == 100.0
    # Stricter values must pass fewer rows
    strict_vals = sorted([p["threshold"] for p in ax["per_threshold"]
                                if isinstance(p["threshold"], (int, float))])
    npass_vals = [next(p["n_pass"] for p in ax["per_threshold"]
                                if p["threshold"] == v) for v in strict_vals]
    # Monotonically non-increasing for "min" axes
    for a, b in zip(npass_vals, npass_vals[1:]):
        assert a >= b, f"hr_l20_min should be monotone non-increasing: {npass_vals}"


@pytest.mark.asyncio
async def test_grid_diagnose_flags_overstrict_axis(db):
    """When the most-strict grid value passes < 30 rows, the
    endpoint must surface an actionable warning."""
    # Seed only 25 rows so any threshold > p50 fails the 30-row gate
    import random
    random.seed(1)
    docs = []
    for i in range(25):
        docs.append({
            "_pytest_tag": TAG, "league_id": "GDFAIL",
            "game_date": "2099-09-02",
            "stat_family": "hits", "odds_bucket": "odds_-200_-100",
            "outcome_numeric": (1 if i % 2 == 0 else 0),
            "hit_rate_l20": 0.40, "hit_rate_l10": 0.40,
            "hit_rate_l5": 0.40, "cv": 0.5,
            "edge": 0.02, "model_probability": 0.5,
        })
    await db["sgo_propvision_full_pipeline_replay"].insert_many(docs)
    async with AsyncClient(base_url=BACKEND_URL, timeout=15.0) as c:
        r = await c.post("/api/emergent-admin/optimizer/grid-diagnose",
                            headers=_auth(),
                            json={"sport": "GDFAIL",
                                    "start": "2099-09-02", "end": "2099-09-02"})
    body = r.json()
    assert body["n_graded"] == 25
    assert len(body["issues"]) >= 1
    assert any("hr_l20_min" in s for s in body["issues"])
