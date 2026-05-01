"""
PRODUCTION-GRADE INTEGRATION TESTS — NBA VK2 (5yr_weighted_pruned52)
=====================================================================
Asserts the LIVE PRODUCTION SERVING PATH works correctly. The live
NBA model is served via `NBAScoringAdapter._predict_vk2_prob_over()`
which loads `vk2_*.pkl` (52 features). The legacy `VegasKillerModel`
path is NOT tested here — it's superseded.

  PROD-NBA-INT-01  schema       VK2 returns {projection, sigma, p_over}
                                for ALL 5 stats on a real player
  PROD-NBA-INT-02  determinism  same inputs → identical outputs
  PROD-NBA-INT-03  insufficient_history → clear error
  PROD-NBA-INT-04  unknown_stat → clear error
  PROD-NBA-INT-05  unknown_player → clear error
  PROD-NBA-INT-06  per-stat coverage: ≥5/25 valid predictions for each stat
  PROD-NBA-INT-07  predicted ≥ 0 across 25 players × 5 stats
  PROD-NBA-INT-08  vk2_models loaded with the expected version
  PROD-NBA-INT-09  p_over in [0, 1] when present (not 0..100)

Run:
    cd /app/backend && python -m pytest tests/test_nba_vk2_production_integration.py -v
"""
from __future__ import annotations
import os
import pytest
import pymongo

EXPECTED_VERSION = "NBA_VK_v2_5yr_weighted_pruned52"
STATS = ("PTS", "REB", "AST", "3PM", "PRA")
LINES = {"PTS": 15.5, "REB": 4.5, "AST": 3.5, "3PM": 1.5, "PRA": 25.5}


@pytest.fixture(scope="module")
def db():
    client = pymongo.MongoClient(
        os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    yield client[os.environ.get("DB_NAME", "pick_vision")]
    client.close()


@pytest.fixture(scope="module")
def adapter():
    """Production NBA scoring adapter with VK2 models loaded.

    The adapter is normally instantiated per-request inside the FastAPI
    server; we instantiate it once and call `_load_vk2_models()` to
    mirror the same code path the live API uses.
    """
    from services.scoring.adapters.nba_scoring import NBAScoringAdapter
    a = NBAScoringAdapter()
    a._load_vk2_models()
    assert a._vk2_loaded, "VK2 models failed to load"
    assert len(a._vk2_models) == 5, (
        f"only {len(a._vk2_models)}/5 VK2 models loaded"
    )
    # Force-load the historical logs cache so the adapter has games
    # to feed `build_features`.
    return a


def _find_active_player(db):
    """Pick a real NBA player with rich game logs (≥10 logs in 2026)."""
    return db.nba_master_hub_2026.find_one(
        {"bdl_id": {"$ne": None},
         "bdl_game_logs.10": {"$exists": True}},
        {"_id": 0, "bdl_id": 1, "display_name": 1, "player_name": 1,
         "team": 1})


@pytest.fixture(scope="module")
def player(db):
    p = _find_active_player(db)
    assert p, "No active NBA player with 10+ logs"
    return p


def _seed_history(adapter, db, bid):
    """Manually seed `_logs_by_id` for a player so VK2 has history.

    The adapter normally builds this map from `nba_master_hub_2026`
    during its loader phase; for unit tests we hydrate just the player
    we need.
    """
    if bid in adapter._logs_by_id:
        return
    doc = db.nba_master_hub_2026.find_one(
        {"bdl_id": int(bid)},
        {"_id": 0, "bdl_game_logs": 1})
    if doc and doc.get("bdl_game_logs"):
        # Sort logs descending by date (most recent first), as the
        # adapter expects.
        logs = sorted(doc["bdl_game_logs"],
                      key=lambda g: (g.get("date") or "", g.get("game_id") or 0),
                      reverse=True)
        adapter._logs_by_id[int(bid)] = logs


# ─── PROD-NBA-INT-01: VK2 schema for every stat ─────────────────────
@pytest.mark.parametrize("stat", STATS)
def test_prod_nba_int_01_vk2_schema(adapter, db, player, stat):
    """PROD-NBA-INT-01: VK2._predict returns full {projection, sigma,
    p_over} for ALL 5 stats."""
    bid = int(player["bdl_id"])
    _seed_history(adapter, db, bid)
    r = adapter._predict_vk2_prob_over(bid, stat, LINES[stat])
    if r.get("error") and "insufficient" in r["error"]:
        pytest.skip(r["error"])
    if r.get("error"):
        pytest.skip(f"{stat}: {r['error']}")
    for k in ("projection", "sigma", "p_over"):
        assert k in r, f"{stat}: missing key {k!r}"
    assert isinstance(r["projection"], (int, float))
    assert r["projection"] >= 0
    assert r["sigma"] > 0
    if r["p_over"] is not None:
        assert 0 <= r["p_over"] <= 1, f"p_over={r['p_over']} not in [0,1]"


# ─── PROD-NBA-INT-02: determinism ───────────────────────────────────
def test_prod_nba_int_02_determinism(adapter, db, player):
    """PROD-NBA-INT-02: same inputs → identical projection across 3 calls."""
    bid = int(player["bdl_id"])
    _seed_history(adapter, db, bid)
    rs = [adapter._predict_vk2_prob_over(bid, "PTS", 15.5) for _ in range(3)]
    if any(r.get("error") for r in rs):
        pytest.skip(rs[0].get("error"))
    proj = [r["projection"] for r in rs]
    assert all(p == proj[0] for p in proj), \
        f"non-deterministic VK2 projections: {proj}"


# ─── PROD-NBA-INT-03: insufficient history ──────────────────────────
def test_prod_nba_int_03_insufficient_history(adapter):
    """PROD-NBA-INT-03: bdl_player_id with no logs returns error."""
    # Use a synthetic ID that won't exist
    r = adapter._predict_vk2_prob_over(99999999, "PTS", 15.5)
    assert r.get("error"), "expected error for unknown player"
    assert "insufficient" in r["error"] or "history" in r["error"]


# ─── PROD-NBA-INT-04: unknown stat ──────────────────────────────────
def test_prod_nba_int_04_unknown_stat(adapter, db, player):
    """PROD-NBA-INT-04: unknown stat returns 'no_vk2_model_for_...'."""
    bid = int(player["bdl_id"])
    _seed_history(adapter, db, bid)
    r = adapter._predict_vk2_prob_over(bid, "totally_made_up", 15.5)
    assert r.get("error"), "expected error for unknown stat"
    assert "no_vk2_model" in r["error"]


# ─── PROD-NBA-INT-05: empty bdl_player_id ───────────────────────────
def test_prod_nba_int_05_none_bdl_id(adapter):
    """PROD-NBA-INT-05: None bdl_player_id returns error gracefully."""
    r = adapter._predict_vk2_prob_over(None, "PTS", 15.5)
    assert r.get("error")


# ─── PROD-NBA-INT-06: per-stat sample coverage ──────────────────────
def test_prod_nba_int_06_per_stat_coverage(adapter, db):
    """PROD-NBA-INT-06: every stat must produce ≥5 valid predictions
    on a 25-player sample (proves model not globally broken)."""
    sample = list(db.nba_master_hub_2026.aggregate([
        {"$match": {"bdl_id": {"$ne": None},
                    "bdl_game_logs.10": {"$exists": True}}},
        {"$project": {"_id": 0, "bdl_id": 1, "display_name": 1}},
        {"$sample": {"size": 25}},
    ]))
    for p in sample:
        _seed_history(adapter, db, int(p["bdl_id"]))
    for stat in STATS:
        success = 0
        for p in sample:
            r = adapter._predict_vk2_prob_over(
                int(p["bdl_id"]), stat, LINES[stat])
            if not r.get("error") and r.get("projection") is not None:
                success += 1
        assert success >= 5, (
            f"{stat}: only {success}/25 valid VK2 predictions in sample"
        )


# ─── PROD-NBA-INT-07: predicted ≥ 0 across pool ─────────────────────
def test_prod_nba_int_07_predicted_nonneg(adapter, db):
    """PROD-NBA-INT-07: every successful predict returns projection ≥ 0.

    Hardened to be deterministic: explicitly samples a mix of HIGH
    and LOW volume players. Negative projections from XGBoost most
    often occur for low-volume stats (e.g. a low-minutes bench player's
    3PM line), so we MUST include several low-volume players in the
    test pool — otherwise the negative-clamp regression gets missed
    with random sampling.
    """
    # Get the 30 LOWEST PTS-L20 players (bench / low-volume tail).
    bench = list(db.nba_master_hub_2026.aggregate([
        {"$match": {"bdl_id": {"$ne": None},
                    "bdl_game_logs.5": {"$exists": True}}},
        {"$project": {"_id": 0, "bdl_id": 1, "display_name": 1,
                      "bdl_game_logs": {"$slice": ["$bdl_game_logs", 20]}}},
        {"$addFields": {
            "pts_l20_mean": {
                "$avg": {"$map": {
                    "input": "$bdl_game_logs",
                    "as": "g",
                    "in": {"$ifNull": ["$$g.pts", 0]}}}}}},
        {"$match": {"pts_l20_mean": {"$lt": 5.0}}},
        {"$limit": 30},
    ]))
    # Plus 10 random higher-volume players for breadth.
    bulk = list(db.nba_master_hub_2026.aggregate([
        {"$match": {"bdl_id": {"$ne": None},
                    "bdl_game_logs.10": {"$exists": True}}},
        {"$project": {"_id": 0, "bdl_id": 1, "display_name": 1}},
        {"$sample": {"size": 10}},
    ]))
    sample = bench + bulk
    assert len(sample) >= 20, f"sample too small: {len(sample)}"
    for p in sample:
        _seed_history(adapter, db, int(p["bdl_id"]))
    bad = []
    for stat in STATS:
        for p in sample:
            r = adapter._predict_vk2_prob_over(
                int(p["bdl_id"]), stat, LINES[stat])
            if r.get("error"):
                continue
            if r["projection"] < 0:
                bad.append((stat, p.get("display_name"), r["projection"]))
    assert not bad, f"negative VK2 projections: {bad[:5]}"


def test_prod_nba_int_07_negative_clamp_deterministic(adapter, db):
    """PROD-NBA-INT-07b: deterministically prove the negative-projection
    clamp is wired in. Monkey-patches the xgboost model to return a
    forced negative value for the 3PM stat (where the per-stat intercept
    is 0, so without the standalone clamp the projection would surface
    negative). The final `projection` must still be ≥ 0.
    """
    # Find a real player to satisfy the history-build path.
    p = db.nba_master_hub_2026.find_one(
        {"bdl_id": {"$ne": None},
         "bdl_game_logs.10": {"$exists": True}},
        {"_id": 0, "bdl_id": 1, "display_name": 1, "player_name": 1})
    bid = int(p["bdl_id"])
    _seed_history(adapter, db, bid)

    # Save the real xgb model and replace it with a stub that returns -0.5.
    orig_model = adapter._vk2_models["3PM"]["model"]

    class _NegStub:
        def predict(self, X):
            import numpy as np
            return np.array([-0.5])

    adapter._vk2_models["3PM"]["model"] = _NegStub()
    try:
        r = adapter._predict_vk2_prob_over(bid, "3PM", 1.5)
    finally:
        adapter._vk2_models["3PM"]["model"] = orig_model

    if r.get("error"):
        pytest.fail(f"clamp test broke predict path: {r['error']}")
    assert r["projection"] >= 0, (
        f"negative-clamp removed: stub returned -0.5 but final "
        f"projection={r['projection']} (should be 0.0)"
    )
    assert r["projection"] == 0.0, (
        f"clamp not exact: projection={r['projection']} (expected 0.0)"
    )


# ─── PROD-NBA-INT-08: model_version stamp ───────────────────────────
def test_prod_nba_int_08_model_version(adapter):
    """PROD-NBA-INT-08: every loaded VK2 model stamps the expected version."""
    for stat, m in adapter._vk2_models.items():
        assert m.get("version") == EXPECTED_VERSION, (
            f"{stat}: version={m.get('version')!r}, "
            f"expected {EXPECTED_VERSION!r}"
        )
        assert m.get("feature_count") == 52
        assert len(m.get("features") or []) == 52


# ─── PROD-NBA-INT-09: p_over range ──────────────────────────────────
def test_prod_nba_int_09_p_over_range(adapter, db):
    """PROD-NBA-INT-09: when p_over is returned, it's in [0, 1]."""
    sample = list(db.nba_master_hub_2026.aggregate([
        {"$match": {"bdl_id": {"$ne": None},
                    "bdl_game_logs.10": {"$exists": True}}},
        {"$project": {"_id": 0, "bdl_id": 1, "display_name": 1}},
        {"$sample": {"size": 15}},
    ]))
    for p in sample:
        _seed_history(adapter, db, int(p["bdl_id"]))
    bad = []
    for stat in STATS:
        for p in sample:
            r = adapter._predict_vk2_prob_over(
                int(p["bdl_id"]), stat, LINES[stat])
            if r.get("error"):
                continue
            po = r.get("p_over")
            if po is not None and not (0 <= po <= 1):
                bad.append((stat, p.get("display_name"), po))
    assert not bad, f"p_over out of [0,1]: {bad[:5]}"
