"""
PRODUCTION-GRADE PERFORMANCE TESTS — MLB HF v3.0_bayes
======================================================
Asserts the model's live-path latency and memory footprint stay
within production budgets. Catches regressions where someone adds
expensive feature lookups, blocking I/O, or unbounded caches.

  PROD-PERF-01  cold latency      ≤ 8000 ms  (first predict, builds PA cache)
  PROD-PERF-02  warm latency      ≤ 250  ms  median over 30 calls
  PROD-PERF-03  load_models       ≤ 5    sec  for all 15 artifacts
  PROD-PERF-04  pa_cache memory   ≤ 1.5  GB   resident after load

Run:
    cd /app/backend && python -m pytest tests/test_mlb_hf_v3_performance.py -v -s
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


# ─── PROD-PERF-01: cold predict latency ─────────────────────────────
def test_prod_perf_01_cold_predict_latency(db):
    """First predict() (with PA cache warm-up, 1.6M rows) ≤ 8000 ms.

    NOTE: live production hot path goes through `predict_live()` which
    lazy-loads the PA cache on first request. After that, all subsequent
    calls are warm — see `test_prod_perf_02_warm_predict_latency`.
    """
    import services.mlb_high_friction_model as hfm
    hfm._mlb_hf_instance = None  # force fresh instance
    m = hfm.get_mlb_high_friction_model(db)
    m.load_models()
    p = db.mlb_master_hub_2026.find_one(
        {"bdl_id": {"$ne": None}, "is_pitcher": {"$ne": True},
         "bdl_game_logs.10": {"$exists": True}},
        {"_id": 0, "bdl_id": 1, "display_name": 1, "player_name": 1})
    t0 = time.perf_counter()
    r = m.predict(
        player_name=p.get("display_name") or p.get("player_name"),
        stat_type="hits", line=1.5,
        bdl_player_id=int(p["bdl_id"]))
    dt_ms = (time.perf_counter() - t0) * 1000
    print(f"\n  cold predict (incl. PA cache build, 1.6M rows): {dt_ms:.0f} ms")
    assert "error" not in r
    assert dt_ms < 8000, f"cold predict() {dt_ms:.0f} ms > 8000 ms budget"


# ─── PROD-PERF-02: warm predict latency ─────────────────────────────
def test_prod_perf_02_warm_predict_latency(db):
    """Median of 30 warm predict() calls ≤ 250 ms."""
    import services.mlb_high_friction_model as hfm
    m = hfm.get_mlb_high_friction_model(db)
    if not m.models:
        m.load_models()
    # Warm the PA cache and DB connection pool.
    p = db.mlb_master_hub_2026.find_one(
        {"bdl_id": {"$ne": None}, "is_pitcher": {"$ne": True},
         "bdl_game_logs.10": {"$exists": True}},
        {"_id": 0, "bdl_id": 1, "display_name": 1, "player_name": 1})
    name = p.get("display_name") or p.get("player_name")
    bid = int(p["bdl_id"])
    m.predict(player_name=name, stat_type="hits", line=1.5, bdl_player_id=bid)
    # Now measure 30 calls.
    times = []
    for _ in range(30):
        t0 = time.perf_counter()
        m.predict(player_name=name, stat_type="hits", line=1.5,
                  bdl_player_id=bid)
        times.append((time.perf_counter() - t0) * 1000)
    median = statistics.median(times)
    p95 = sorted(times)[int(len(times) * 0.95)]
    print(f"\n  warm predict: median={median:.1f} ms  p95={p95:.1f} ms")
    assert median < 250, f"warm median {median:.1f} ms > 250 ms"
    assert p95 < 500, f"warm p95 {p95:.1f} ms > 500 ms"


# ─── PROD-PERF-03: load_models latency ──────────────────────────────
def test_prod_perf_03_load_models_latency(db):
    """load_models() loads all 15 artifacts in ≤ 5 sec."""
    import services.mlb_high_friction_model as hfm
    hfm._mlb_hf_instance = None
    m = hfm.get_mlb_high_friction_model(db)
    t0 = time.perf_counter()
    n = m.load_models()
    dt = time.perf_counter() - t0
    print(f"\n  load_models: {dt:.2f} s for {n} models")
    assert n >= 14
    assert dt < 5.0, f"load_models took {dt:.2f}s > 5s budget"


# ─── PROD-PERF-04: PA cache memory ──────────────────────────────────
def test_prod_perf_04_pa_cache_memory(db):
    """PA cache resident memory ≤ 1.5 GB after load."""
    try:
        import psutil
    except ImportError:
        pytest.skip("psutil not installed")
    import services.mlb_high_friction_model as hfm
    hfm._mlb_hf_instance = None
    m = hfm.get_mlb_high_friction_model(db)
    m.load_models()
    proc = psutil.Process()
    rss_before = proc.memory_info().rss
    m._get_pa_cache()
    rss_after = proc.memory_info().rss
    delta_gb = (rss_after - rss_before) / (1024 ** 3)
    print(f"\n  PA cache RSS delta: {delta_gb:.2f} GB")
    assert delta_gb < 1.5, f"PA cache uses {delta_gb:.2f} GB > 1.5 GB budget"
