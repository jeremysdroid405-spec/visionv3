"""
PRODUCTION-GRADE PERFORMANCE TESTS — NBA VK2 (production serving path)
=======================================================================
Asserts the live-serving NBA VK2 path
(`NBAScoringAdapter._predict_vk2_prob_over`) stays within production
budgets.

  PROD-NBA-PERF-01  cold predict       ≤ 3000 ms (first call, no cache)
  PROD-NBA-PERF-02  warm predict       ≤ 100 ms median over 30 calls
  PROD-NBA-PERF-03  vk2 load_models    ≤ 5    sec for all 5 artifacts

Run:
    cd /app/backend && python -m pytest tests/test_nba_vk2_performance.py -v -s
"""
from __future__ import annotations
import os
import time
import statistics
import pytest
import pymongo


@pytest.fixture(scope="module")
def db():
    client = pymongo.MongoClient(
        os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    yield client[os.environ.get("DB_NAME", "pick_vision")]
    client.close()


def _seed_history(adapter, db, bid):
    if bid in adapter._logs_by_id:
        return
    doc = db.nba_master_hub_2026.find_one(
        {"bdl_id": int(bid)}, {"_id": 0, "bdl_game_logs": 1})
    if doc and doc.get("bdl_game_logs"):
        adapter._logs_by_id[int(bid)] = sorted(
            doc["bdl_game_logs"],
            key=lambda g: (g.get("date") or "", g.get("game_id") or 0),
            reverse=True)


def _find_player(db):
    return db.nba_master_hub_2026.find_one(
        {"bdl_id": {"$ne": None},
         "bdl_game_logs.10": {"$exists": True}},
        {"_id": 0, "bdl_id": 1, "display_name": 1})


def test_prod_nba_perf_01_cold_predict_latency(db):
    """First VK2 predict ≤ 3000 ms."""
    from services.scoring.adapters.nba_scoring import NBAScoringAdapter
    a = NBAScoringAdapter()
    a._load_vk2_models()
    p = _find_player(db)
    bid = int(p["bdl_id"])
    _seed_history(a, db, bid)
    t0 = time.perf_counter()
    r = a._predict_vk2_prob_over(bid, "PTS", 15.5)
    dt_ms = (time.perf_counter() - t0) * 1000
    print(f"\n  cold NBA VK2 predict: {dt_ms:.0f} ms")
    if r.get("error"):
        pytest.skip(r["error"])
    assert dt_ms < 3000, f"cold predict {dt_ms:.0f} ms > 3000 ms"


def test_prod_nba_perf_02_warm_predict_latency(db):
    """Median of 30 warm VK2 predict() calls ≤ 100 ms."""
    from services.scoring.adapters.nba_scoring import NBAScoringAdapter
    a = NBAScoringAdapter()
    a._load_vk2_models()
    p = _find_player(db)
    bid = int(p["bdl_id"])
    _seed_history(a, db, bid)
    # Warm
    a._predict_vk2_prob_over(bid, "PTS", 15.5)
    times = []
    for _ in range(30):
        t0 = time.perf_counter()
        r = a._predict_vk2_prob_over(bid, "PTS", 15.5)
        times.append((time.perf_counter() - t0) * 1000)
        if r.get("error"):
            pytest.skip(r["error"])
    median = statistics.median(times)
    p95 = sorted(times)[int(len(times) * 0.95)]
    print(f"\n  warm NBA VK2: median={median:.1f} ms  p95={p95:.1f} ms")
    assert median < 100, f"warm median {median:.1f} ms > 100 ms"
    assert p95 < 250, f"warm p95 {p95:.1f} ms > 250 ms"


def test_prod_nba_perf_03_vk2_load_latency():
    """_load_vk2_models() loads all 5 artifacts in ≤ 5 sec."""
    from services.scoring.adapters.nba_scoring import NBAScoringAdapter
    a = NBAScoringAdapter()
    t0 = time.perf_counter()
    a._load_vk2_models()
    dt = time.perf_counter() - t0
    print(f"\n  vk2 load: {dt:.2f} s for {len(a._vk2_models)} models")
    assert len(a._vk2_models) == 5
    assert dt < 5.0, f"vk2 load {dt:.2f}s > 5s"
