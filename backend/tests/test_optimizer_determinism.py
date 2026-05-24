"""
Locks in optimizer determinism and the new top-per-family endpoint.

The user's repeated complaint: "same window, different Top 1 each
run" + "Best by Tier doesn't match Top 25". Two causes:

  1. `_enumerate_combos` used unseeded `random.choice` to subsample
     when grid > max_per_cell → every run drew a different combo
     subset → different leaderboard.
  2. Mongo sort on `(score: -1)` alone left ties resolved in
     insertion order, which varied between runs because cells
     finish out-of-order in parallel workers. So "Best by …"
     could pick a different tied cell than what appeared in Top-25.

This test pins:
- _enumerate_combos returns the full Cartesian product, identical
  bytewise across calls, regardless of `max_per_cell`.
- A new /top-per-family endpoint exists and returns top-N per
  (stat_family, odds_bucket) with deterministic ordering.
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

from routes.emergent_admin.optimizer import _enumerate_combos  # noqa: E402

BACKEND_URL  = os.environ.get("BACKEND_URL", "http://127.0.0.1:8001")
TAG          = f"_pytest_det_{uuid.uuid4().hex[:8]}"


def _auth() -> dict:
    return {"X-Admin-Token": os.environ["EMERGENT_ADMIN_TOKEN"],
              "X-Agent-Id": "pytest"}


# ── Determinism ─────────────────────────────────────────────────────
def test_enumerate_combos_is_deterministic_and_brute_force():
    """Run the enumerator 10 times — the output must be byte-identical
    each time AND must contain every combination in the grid."""
    grid = {
        "hr_l20_min": [0.55, 0.60, 0.65],
        "cv_max":     [0.7, 0.9, 1.1],
        "edge_min":   [0.02, 0.05, 0.08, 0.10],
        "tp_min":     [0.50, 0.55, 0.60, 0.65],
    }
    expected_total = 3 * 3 * 4 * 4   # 144 combos
    runs = [_enumerate_combos(grid, max_per_cell=100) for _ in range(10)]
    for r in runs:
        assert len(r) == expected_total, \
            f"expected {expected_total} combos, got {len(r)}"
        # No random sampling: max_per_cell smaller than the grid must
        # NOT decimate the result. The user's explicit request is
        # brute-force, every combo.
    # All 10 runs identical
    first = runs[0]
    for i, r in enumerate(runs[1:], start=2):
        assert r == first, f"run #{i} differs from run #1"


def test_enumerate_combos_no_duplicates():
    grid = {"hr_l20_min": [0.55, 0.60], "cv_max": [0.7, 0.9]}
    combos = _enumerate_combos(grid, max_per_cell=10)
    keys = [tuple(sorted(c.items())) for c in combos]
    assert len(set(keys)) == len(keys), "duplicate combo found"


def test_enumerate_combos_max_per_cell_does_not_decimate():
    """Even with max_per_cell=10 and a 144-cell grid, every combo
    must be returned (the user explicitly demanded brute-force)."""
    grid = {
        "hr_l20_min": [0.55, 0.60, 0.65],
        "cv_max":     [0.7, 0.9, 1.1],
        "edge_min":   [0.02, 0.05, 0.08, 0.10],
        "tp_min":     [0.50, 0.55, 0.60, 0.65],
    }
    combos = _enumerate_combos(grid, max_per_cell=10)
    assert len(combos) == 144


# ── New /top-per-family endpoint ────────────────────────────────────
@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    d = client[os.environ["DB_NAME"]]
    yield d
    await d["optimizer_runs"].delete_many({"_pytest_tag": TAG})
    await d["optimizer_run_results"].delete_many({"_pytest_tag": TAG})
    client.close()


async def _seed_run(db) -> str:
    rid = f"pytest_tpf_{uuid.uuid4().hex[:6]}"
    await db["optimizer_runs"].insert_one({
        "run_id": rid, "status": "succeeded", "_pytest_tag": TAG,
    })
    # Build 2 families × 2 buckets × 5 cells each = 20 graded cells.
    docs = []
    fams = ["pitcher_strikeouts", "batter_strikeouts"]
    buckets = ["odds_-200_-100", "odds_lt_-200"]
    for fam in fams:
        for bk in buckets:
            for i in range(5):
                docs.append({
                    "_pytest_tag": TAG,
                    "run_id": rid, "tier": "safe_haven",
                    "stat_family": fam, "odds_bucket": bk,
                    "score": -i * 0.5,    # higher i ⇒ lower score
                    "n_bets": 30 + i, "n_graded": 30 + i,
                    "hit_rate": 0.5 + i * 0.01, "roi": -0.05 - i * 0.01,
                    "thresholds": {"tp_min": 0.50 + i * 0.05},
                })
    await db["optimizer_run_results"].insert_many(docs)
    return rid


@pytest.mark.asyncio
async def test_top_per_family_returns_topN_per_group(db):
    rid = await _seed_run(db)
    async with AsyncClient(base_url=BACKEND_URL, timeout=20.0) as c:
        r = await c.get(f"/api/emergent-admin/optimizer/{rid}/top-per-family",
                            headers=_auth(), params={"top_n": 3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["n_groups"] == 4   # 2 families × 2 buckets
    for g in body["groups"]:
        # Each group must return exactly top_n configs
        assert len(g["configs"]) == 3
        # AND they must be sorted by score desc
        scores = [c["score"] for c in g["configs"]]
        assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_top_per_family_is_repeatable(db):
    """Hit the endpoint 5 times → result must be byte-identical."""
    rid = await _seed_run(db)
    async with AsyncClient(base_url=BACKEND_URL, timeout=20.0) as c:
        responses = []
        for _ in range(5):
            r = await c.get(f"/api/emergent-admin/optimizer/{rid}/top-per-family",
                                headers=_auth(), params={"top_n": 3})
            responses.append(r.json())
    first = responses[0]
    for i, body in enumerate(responses[1:], start=2):
        assert body == first, f"response #{i} differs from #1"


@pytest.mark.asyncio
async def test_results_top_is_repeatable(db):
    """The Top-25 from /results must also be deterministic across
    repeated GETs on the same run."""
    rid = await _seed_run(db)
    async with AsyncClient(base_url=BACKEND_URL, timeout=20.0) as c:
        responses = []
        for _ in range(5):
            r = await c.get(f"/api/emergent-admin/optimizer/{rid}/results",
                                headers=_auth(), params={"limit": 10})
            responses.append(r.json())
    first_top = responses[0]["top"]
    for i, body in enumerate(responses[1:], start=2):
        assert body["top"] == first_top, (
            f"GET #{i} top differs — possible nondeterministic sort tiebreak")
