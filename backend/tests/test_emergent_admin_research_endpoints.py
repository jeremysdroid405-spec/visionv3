"""
Integration test for the new Universal Research Result endpoints.

Seeds a tiny mocked dataset (≤100 docs) into the live local DB,
hits each endpoint via httpx against the already-running FastAPI app
(via REACT_APP_BACKEND_URL or http://127.0.0.1:8001), asserts
ranking / filtering / candidate behaviour, then tears the data down.
Will not run anything heavy locally.
"""
from __future__ import annotations
import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")
# We do not import the FastAPI app here — we hit the running supervisor
# instance because the emergent-admin router is mounted inside the app's
# async startup event, which ASGITransport does not trigger reliably.

BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8001")
HTTP_TIMEOUT = 20.0


RUN_ID_A = f"test_pp_free_{uuid.uuid4().hex[:8]}"
RUN_ID_B = f"test_tier_fam_{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture
async def seeded_db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    now = datetime.now(timezone.utc)

    # ── Run A: PP-free (market_truth_pp_free) ─────────────────────
    await db["research_grid_runs"].insert_one({
        "run_id": RUN_ID_A,
        "version": 2,
        "methodology": "market_truth_pp_free",
        "params": {"league": "MLB", "start": "2025-04-01", "end": "2025-04-30",
                     "min_bets": 30, "grid": {}},
        "status": "succeeded",
        "started_at": now, "finished_at": now,
        "n_eligible_rows": 1234,
        "n_cells_total": 30, "n_cells_qualified": 12,
    })
    a_cells = []
    for i in range(30):
        a_cells.append({
            "run_id": RUN_ID_A, "version": 2,
            "methodology": "market_truth_pp_free",
            "slice": "ALL",
            "consensus_prob_min": 0.50 + (i % 6) * 0.05,
            "devig_book_count_min": 1 + i % 5,
            "sharp_book_count_min": i % 4,
            "market_width_max": None,
            "consensus_disagreement_max": None,
            "n_bets": 20 + i * 7,
            "n_wins": 10 + i * 4,
            "n_losses": 8 + i * 2,
            "n_pushes": 0,
            "hit_rate": 0.40 + (i % 10) * 0.02,
            "consensus_prob_avg": 0.50 + (i % 5) * 0.01,
            "calibration_delta_consensus": -0.05 + (i % 11) * 0.01,
        })
    # 3 STAT_FAMILY slice rows
    for fam in ("hits", "home_runs", "strikeouts"):
        a_cells.append({
            "run_id": RUN_ID_A, "version": 2,
            "methodology": "market_truth_pp_free",
            "slice": "STAT_FAMILY", "stat_family": fam,
            "consensus_prob_min": 0.55,
            "n_bets": 80, "hit_rate": 0.58 if fam == "hits" else 0.50,
            "calibration_delta_consensus": 0.04 if fam == "hits" else 0.00,
        })
    await db["research_grid_results"].insert_many(a_cells)

    # 2 candidate threshold docs
    await db["candidate_thresholds"].insert_many([
        {"run_id": RUN_ID_A, "rank": 1, "league": "MLB",
          "version": 2, "methodology": "market_truth_pp_free",
          "params": {"consensus_prob_min": 0.65},
          "metrics": {"n_bets": 110, "hit_rate": 0.61,
                          "calibration_delta_consensus": 0.06},
          "created_at": now},
        {"run_id": RUN_ID_A, "rank": 2, "league": "MLB",
          "version": 2, "methodology": "market_truth_pp_free",
          "params": {"consensus_prob_min": 0.70},
          "metrics": {"n_bets": 80, "hit_rate": 0.58,
                          "calibration_delta_consensus": 0.03},
          "created_at": now},
    ])

    # ── Run B: per-tier × stat_family ─────────────────────────────
    await db["research_grid_runs"].insert_one({
        "run_id": RUN_ID_B,
        "version": 1,
        "methodology": "per_tier_per_stat_family",
        "params": {"league": "MLB", "start": "2025-04-01", "end": "2025-04-30",
                     "min_bets": 20},
        "status": "succeeded",
        "started_at": now, "finished_at": now,
        "n_cells_total": 20, "n_cells_qualified": 8,
    })
    b_cells = []
    for tier in ("safe_haven", "front_lines", "war_zone"):
        for fam in ("hits", "home_runs"):
            b_cells.append({
                "run_id": RUN_ID_B, "version": 1,
                "methodology": "per_tier_per_stat_family",
                "slice": "TIER_FAMILY", "tier": tier, "stat_family": fam,
                "hr_l20_min": 0.55, "hr_l5_min": 0.50,
                "cv_max": 0.90, "edge_min": 0.05, "tp_min": 0.55,
                "n_bets": 50 + (10 if tier == "safe_haven" else 0),
                "n_wins": 30 if tier == "safe_haven" else 25,
                "hit_rate": 0.62 if tier == "safe_haven" else 0.55,
                "calibration_delta": 0.04 if tier == "safe_haven" else 0.01,
                "avg_edge": 0.06, "avg_cv": 0.8, "avg_tp": 0.6,
            })
    await db["research_grid_results"].insert_many(b_cells)

    yield db

    # Cleanup
    await db["research_grid_runs"].delete_many({"run_id": {"$in": [RUN_ID_A, RUN_ID_B]}})
    await db["research_grid_results"].delete_many({"run_id": {"$in": [RUN_ID_A, RUN_ID_B]}})
    await db["candidate_thresholds"].delete_many({"run_id": {"$in": [RUN_ID_A, RUN_ID_B]}})
    client.close()


def _auth_headers() -> dict:
    tok = os.environ.get("EMERGENT_ADMIN_TOKEN")
    assert tok, "EMERGENT_ADMIN_TOKEN must be set in backend/.env"
    return {"X-Admin-Token": tok, "X-Agent-Id": "pytest"}


@pytest.mark.asyncio
async def test_list_grid_runs(seeded_db):
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.get("/api/emergent-admin/research/grid-runs",
                          headers=_auth_headers(), params={"sport": "MLB", "limit": 10})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        run_ids = {x["run_id"] for x in data["runs"]}
        assert RUN_ID_A in run_ids and RUN_ID_B in run_ids


@pytest.mark.asyncio
async def test_get_single_run(seeded_db):
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.get(f"/api/emergent-admin/research/grid-runs/{RUN_ID_A}",
                          headers=_auth_headers())
        assert r.status_code == 200
        body = r.json()
        assert body["run"]["methodology"] == "market_truth_pp_free"
        assert body["run"]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_grid_results_sort_by_hit_rate(seeded_db):
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.get(f"/api/emergent-admin/research/grid-results/{RUN_ID_B}",
                          headers=_auth_headers(),
                          params={"sort_metric": "hit_rate", "min_bets": 20, "top_k": 3})
        assert r.status_code == 200
        body = r.json()
        # Top should be ordered descending by hit_rate
        top = body["top"]
        assert len(top) >= 1
        hits = [c["hit_rate"] for c in top]
        assert hits == sorted(hits, reverse=True)
        # Safe haven should win
        assert top[0]["tier"] == "safe_haven"
        assert "best_by_tier" in body and "best_by_stat_family" in body


@pytest.mark.asyncio
async def test_grid_results_sort_by_calibration_delta_any(seeded_db):
    """`calibration_delta_any` should fall back to the legacy field when
    consensus delta is absent (run B), and use consensus when present
    (run A)."""
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.get(f"/api/emergent-admin/research/grid-results/{RUN_ID_B}",
                          headers=_auth_headers(),
                          params={"sort_metric": "calibration_delta_any",
                                    "min_bets": 20, "top_k": 3})
        assert r.status_code == 200
        top = r.json()["top"]
        # Safe haven cells with calibration_delta=0.04 should rank highest
        assert top[0]["calibration_delta_any"] >= 0.04


@pytest.mark.asyncio
async def test_grid_results_filter_by_slice(seeded_db):
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.get(f"/api/emergent-admin/research/grid-results/{RUN_ID_A}",
                          headers=_auth_headers(),
                          params={"slice": "STAT_FAMILY", "min_bets": 30, "top_k": 5})
        assert r.status_code == 200
        body = r.json()
        assert all(cell["slice"] == "STAT_FAMILY" for cell in body["top"])


@pytest.mark.asyncio
async def test_get_candidate_thresholds(seeded_db):
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.get(f"/api/emergent-admin/research/candidate-thresholds/{RUN_ID_A}",
                          headers=_auth_headers())
        assert r.status_code == 200
        body = r.json()
        assert body["n"] == 2
        assert body["candidates"][0]["rank"] == 1


@pytest.mark.asyncio
async def test_meta_sort_metrics(seeded_db):
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.get("/api/emergent-admin/research/_meta/sort-metrics",
                          headers=_auth_headers())
        assert r.status_code == 200
        keys = {m["key"] for m in r.json()["metrics"]}
        for required in ("hit_rate", "calibration_delta_any",
                            "calibration_delta_consensus", "n_bets"):
            assert required in keys


@pytest.mark.asyncio
async def test_run_not_found(seeded_db):
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.get("/api/emergent-admin/research/grid-runs/does_not_exist",
                          headers=_auth_headers())
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_auth_required():
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.get("/api/emergent-admin/research/grid-runs")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_invalid_sort_metric(seeded_db):
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.get(f"/api/emergent-admin/research/grid-results/{RUN_ID_B}",
                          headers=_auth_headers(),
                          params={"sort_metric": "made_up_metric"})
        assert r.status_code == 400


# ── Additional edge case tests ────────────────────────────────────────

@pytest.mark.asyncio
async def test_high_min_bets_excludes_all_returns_empty_lists(seeded_db):
    """Very large min_bets should return empty top/worst but n_total > 0."""
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.get(f"/api/emergent-admin/research/grid-results/{RUN_ID_A}",
                          headers=_auth_headers(),
                          params={"sort_metric": "hit_rate", "min_bets": 999999, "top_k": 10})
        assert r.status_code == 200
        body = r.json()
        # n_total should be > 0 (cells exist)
        assert body["n_total"] > 0
        # n_qualified should be 0 (none meet min_bets)
        assert body["n_qualified"] == 0
        # top and worst should be empty
        assert body["top"] == []
        assert body["worst"] == []


@pytest.mark.asyncio
async def test_sort_metric_with_no_data_does_not_crash(seeded_db):
    """profit_units on a PP-free run that doesn't compute payouts should not crash."""
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        # Run A (PP-free) does not have profit_units field
        r = await c.get(f"/api/emergent-admin/research/grid-results/{RUN_ID_A}",
                          headers=_auth_headers(),
                          params={"sort_metric": "profit_units", "min_bets": 0, "top_k": 10})
        assert r.status_code == 200
        body = r.json()
        # Should return ok but with empty ranked lists (no cells have profit_units)
        assert body["ok"] is True
        assert body["n_total"] > 0
        # top/worst may be empty since no cells have the metric
        assert isinstance(body["top"], list)
        assert isinstance(body["worst"], list)


@pytest.mark.asyncio
async def test_worst_list_ordering_most_negative_first(seeded_db):
    """Worst list should have most-negative metric values first (ascending order)."""
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.get(f"/api/emergent-admin/research/grid-results/{RUN_ID_A}",
                          headers=_auth_headers(),
                          params={"sort_metric": "calibration_delta_consensus", "min_bets": 0, "top_k": 30})
        assert r.status_code == 200
        body = r.json()
        worst = body["worst"]
        if len(worst) > 1:
            # Worst should be ordered ascending (most negative first)
            deltas = [c.get("calibration_delta_consensus") for c in worst if c.get("calibration_delta_consensus") is not None]
            assert deltas == sorted(deltas), f"Worst list not in ascending order: {deltas}"


@pytest.mark.asyncio
async def test_auth_bad_token_returns_401():
    """Bad token should return 401."""
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.get("/api/emergent-admin/research/grid-runs",
                          headers={"X-Admin-Token": "invalid_token_12345"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_best_by_tier_is_dict_keyed_by_tier(seeded_db):
    """best_by_tier should be an object keyed by tier name."""
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.get(f"/api/emergent-admin/research/grid-results/{RUN_ID_B}",
                          headers=_auth_headers(),
                          params={"sort_metric": "hit_rate", "min_bets": 0, "top_k": 10})
        assert r.status_code == 200
        body = r.json()
        best_by_tier = body["best_by_tier"]
        assert isinstance(best_by_tier, dict)
        # Should have tier keys
        for tier in ("safe_haven", "front_lines", "war_zone"):
            if tier in best_by_tier:
                assert "tier" in best_by_tier[tier]
                assert best_by_tier[tier]["tier"] == tier


@pytest.mark.asyncio
async def test_best_by_stat_family_is_dict_keyed_by_family(seeded_db):
    """best_by_stat_family should be an object keyed by stat_family name."""
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.get(f"/api/emergent-admin/research/grid-results/{RUN_ID_B}",
                          headers=_auth_headers(),
                          params={"sort_metric": "hit_rate", "min_bets": 0, "top_k": 10})
        assert r.status_code == 200
        body = r.json()
        best_by_stat_family = body["best_by_stat_family"]
        assert isinstance(best_by_stat_family, dict)
        # Should have stat_family keys
        for fam in ("hits", "home_runs"):
            if fam in best_by_stat_family:
                assert "stat_family" in best_by_stat_family[fam]
                assert best_by_stat_family[fam]["stat_family"] == fam


@pytest.mark.asyncio
async def test_best_by_side_keyed_by_over_under(seeded_db):
    """best_by_side should be keyed by 'OVER'/'UNDER' when side data exists."""
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        # Run A has STAT_FAMILY slice but no side data in our seed
        # This test verifies the structure is correct (empty dict if no side data)
        r = await c.get(f"/api/emergent-admin/research/grid-results/{RUN_ID_A}",
                          headers=_auth_headers(),
                          params={"sort_metric": "hit_rate", "min_bets": 0, "top_k": 10})
        assert r.status_code == 200
        body = r.json()
        best_by_side = body["best_by_side"]
        assert isinstance(best_by_side, dict)
        # If keys exist, they should be OVER/UNDER
        for key in best_by_side.keys():
            assert key in ("OVER", "UNDER", "over", "under")


# ── Verify existing emergent_admin routes still work ──────────────────

@pytest.mark.asyncio
async def test_existing_optimizer_endpoint_still_works():
    """Verify the new research router does NOT break existing optimizer routes."""
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        # GET /{run_id} requires a valid run_id, so test the _meta endpoint instead
        r = await c.get("/api/emergent-admin/optimizer/_meta/testing_default",
                          headers=_auth_headers())
        # Should not be 404 or 500 (may return null doc if no default set)
        assert r.status_code in (200, 401, 403), f"Unexpected status: {r.status_code}"


@pytest.mark.asyncio
async def test_existing_jobs_endpoint_still_works():
    """Verify the new research router does NOT break existing jobs routes."""
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        # GET /jobs/ lists jobs
        r = await c.get("/api/emergent-admin/jobs/",
                          headers=_auth_headers(), params={"limit": 1})
        # Should not be 404 or 500
        assert r.status_code in (200, 401, 403), f"Unexpected status: {r.status_code}"


@pytest.mark.asyncio
async def test_existing_coverage_endpoint_still_works():
    """Verify the new research router does NOT break existing coverage routes."""
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        # GET /coverage/ requires sport, start, end params
        r = await c.get("/api/emergent-admin/coverage/",
                          headers=_auth_headers(),
                          params={"sport": "MLB", "start": "2025-01-01", "end": "2025-01-02"})
        # Should not be 404 or 500
        assert r.status_code in (200, 401, 403), f"Unexpected status: {r.status_code}"


@pytest.mark.asyncio
async def test_existing_collections_find_endpoint_still_works():
    """Verify the new research router does NOT break existing collections routes."""
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        # POST /{coll}/find is the main collections endpoint
        r = await c.post("/api/emergent-admin/collections/research_grid_runs/find",
                          headers=_auth_headers(),
                          json={"filter": {}, "limit": 1})
        # Should not be 404 or 500
        assert r.status_code in (200, 401, 403), f"Unexpected status: {r.status_code}"


@pytest.mark.asyncio
async def test_list_candidates_endpoint(seeded_db):
    """Test the list candidates endpoint (without run_id filter)."""
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.get("/api/emergent-admin/research/candidate-thresholds",
                          headers=_auth_headers(), params={"sport": "MLB", "limit": 10})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert isinstance(body["candidates"], list)
        # Our seeded candidates should be in there
        run_ids = {c.get("run_id") for c in body["candidates"]}
        assert RUN_ID_A in run_ids


@pytest.mark.asyncio
async def test_grid_results_404_for_unknown_run():
    """grid-results should return 404 for unknown run_id."""
    async with AsyncClient(base_url=BACKEND_URL, timeout=HTTP_TIMEOUT) as c:
        r = await c.get("/api/emergent-admin/research/grid-results/nonexistent_run_xyz",
                          headers=_auth_headers())
        assert r.status_code == 404
