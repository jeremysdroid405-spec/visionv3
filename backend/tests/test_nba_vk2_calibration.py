"""
PRODUCTION-GRADE CALIBRATION TESTS — NBA VK2 (production serving path)
=======================================================================
Runs the LIVE PRODUCTION VK2 path
(`NBAScoringAdapter._predict_vk2_prob_over`) across a representative
sample of NBA players and asserts the *distribution* of outputs
matches realistic NBA ranges.

  PROD-NBA-CAL-01  μ-distribution per stat (median + p95 in band)
  PROD-NBA-CAL-02  4× L20 sanity gate (extended canary)
  PROD-NBA-CAL-03  σ source verification (rmse_test ≈ residual sigma)
  PROD-NBA-CAL-04  PRA ≈ PTS+REB+AST correlation (additivity)

Run:
    cd /app/backend && python -m pytest tests/test_nba_vk2_calibration.py -v -s
"""
from __future__ import annotations
import os
import statistics
import pytest
import pymongo

EXPECTED_VERSION = "NBA_VK_v2_5yr_weighted_pruned52"
STATS = ("PTS", "REB", "AST", "3PM", "PRA")
LINES = {"PTS": 15.5, "REB": 4.5, "AST": 3.5, "3PM": 1.5, "PRA": 25.5}

# Realistic per-stat μ bands for an active contributor pool.
STAT_BANDS = {
    "PTS": (8.0, 22.0, 35.0),
    "REB": (2.5,  8.5, 13.0),
    "AST": (1.5,  6.5, 11.0),
    "3PM": (0.6,  2.8,  4.5),
    "PRA": (15.0, 38.0, 55.0),
}


@pytest.fixture(scope="module")
def db():
    client = pymongo.MongoClient(
        os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    yield client[os.environ.get("DB_NAME", "pick_vision")]
    client.close()


@pytest.fixture(scope="module")
def adapter():
    from services.scoring.adapters.nba_scoring import NBAScoringAdapter
    a = NBAScoringAdapter()
    a._load_vk2_models()
    return a


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


def _l20_mean(logs, *fields):
    vals = []
    for g in logs[:20]:
        try:
            vals.append(sum(float(g.get(f, 0) or 0) for f in fields))
        except (TypeError, ValueError):
            continue
    return sum(vals) / len(vals) if vals else 0.0


def _sample_players(db, n=100):
    return list(db.nba_master_hub_2026.aggregate([
        {"$match": {"bdl_id": {"$ne": None},
                    "bdl_game_logs.10": {"$exists": True}}},
        {"$project": {"_id": 0, "bdl_id": 1, "display_name": 1,
                      "bdl_game_logs": {"$slice": ["$bdl_game_logs", 20]}}},
        {"$sample": {"size": n}},
    ]))


# ─── PROD-NBA-CAL-01: μ distribution per stat ───────────────────────
@pytest.mark.parametrize("stat,band", STAT_BANDS.items(), ids=list(STAT_BANDS))
def test_prod_nba_cal_01_mu_distribution(adapter, db, stat, band):
    """PROD-NBA-CAL-01: median μ in realistic band; p95 ≤ ceiling."""
    median_low, median_high, p95_max = band
    sample = _sample_players(db, 100)
    preds = []
    for p in sample:
        logs = p.get("bdl_game_logs") or []
        if _l20_mean(logs, "pts") < 5.0:
            continue  # filter bench
        bid = int(p["bdl_id"])
        _seed_history(adapter, db, bid)
        r = adapter._predict_vk2_prob_over(bid, stat, LINES[stat])
        if not r.get("error") and r.get("projection") is not None:
            preds.append(r["projection"])
    assert len(preds) >= 30, f"{stat}: too few predictions: {len(preds)}"
    median = statistics.median(preds)
    p95 = statistics.quantiles(preds, n=20)[-1] if len(preds) >= 20 else max(preds)
    print(f"\n  {stat}: n={len(preds)} median={median:.3f} p95={p95:.3f}")
    assert median_low <= median <= median_high, (
        f"{stat}: median={median:.2f} outside band [{median_low}, {median_high}]"
    )
    assert p95 <= p95_max, f"{stat}: p95={p95:.2f} exceeds ceiling {p95_max}"


# ─── PROD-NBA-CAL-02: 4× L20 canary ─────────────────────────────────
def test_prod_nba_cal_02_no_blowup_in_sample(adapter, db):
    """PROD-NBA-CAL-02: no NBA prediction > 4× player's L20 mean."""
    sample = _sample_players(db, 100)
    blowups = []
    for stat in STATS:
        if stat == "PRA":
            fields = ("pts", "reb", "ast")
        elif stat == "PTS":
            fields = ("pts",)
        elif stat == "REB":
            fields = ("reb",)
        elif stat == "AST":
            fields = ("ast",)
        else:
            fields = ("fg3m",)
        for p in sample:
            logs = p.get("bdl_game_logs") or []
            if len(logs) < 10:
                continue
            l20m = _l20_mean(logs, *fields)
            if l20m < 1.0:
                continue
            bid = int(p["bdl_id"])
            _seed_history(adapter, db, bid)
            r = adapter._predict_vk2_prob_over(bid, stat, LINES[stat])
            if r.get("error") or r.get("projection") is None:
                continue
            ratio = r["projection"] / max(l20m, 0.01)
            if ratio > 4.0:
                blowups.append((stat, p.get("display_name"),
                                round(r["projection"], 2),
                                round(l20m, 2), round(ratio, 2)))
    assert not blowups, (
        f"REGRESSION: {len(blowups)} blow-ups: {blowups[:5]}"
    )


# ─── PROD-NBA-CAL-03: σ source verification ────────────────────────
def test_prod_nba_cal_03_sigma_matches_rmse(adapter):
    """PROD-NBA-CAL-03: each VK2 model's runtime σ matches the artifact's
    `residual_sigma_empirical`. A drift between them indicates a wrong
    pkl was loaded or sigma wasn't propagated correctly."""
    import pickle
    for stat in STATS:
        rt_sigma = adapter._vk2_models[stat]["sigma"]
        with open(f"/app/backend/models/vk2_{stat.lower()}.pkl", "rb") as fh:
            d = pickle.load(fh)
        artifact_sigma = d["residual_sigma_empirical"]
        assert abs(rt_sigma - artifact_sigma) < 1e-6, (
            f"{stat}: runtime σ={rt_sigma} vs artifact {artifact_sigma}"
        )
        # And: artifact σ should match rmse_test (they're the same
        # quantity in v2 trainer — drift catches a stale-load bug).
        assert abs(artifact_sigma - d["rmse_test"]) < 0.01, (
            f"{stat}: artifact σ={artifact_sigma} != rmse_test {d['rmse_test']}"
        )


# ─── PROD-NBA-CAL-04: PRA additivity correlation ───────────────────
def test_prod_nba_cal_04_pra_additive_correlation(adapter, db):
    """PROD-NBA-CAL-04: PRA prediction correlates strongly (r > 0.85)
    with PTS+REB+AST sum across active sample."""
    sample = _sample_players(db, 60)
    pairs = []
    for p in sample:
        logs = p.get("bdl_game_logs") or []
        if _l20_mean(logs, "pts") < 5.0:
            continue
        bid = int(p["bdl_id"])
        _seed_history(adapter, db, bid)
        rs = {}
        for stat in ("PTS", "REB", "AST", "PRA"):
            r = adapter._predict_vk2_prob_over(bid, stat, LINES[stat])
            if r.get("error") or r.get("projection") is None:
                rs = None
                break
            rs[stat] = r["projection"]
        if rs:
            pairs.append((rs["PRA"], rs["PTS"] + rs["REB"] + rs["AST"]))
    assert len(pairs) >= 20, f"too few pairs: {len(pairs)}"
    n = len(pairs)
    xs = [a for a, _ in pairs]
    ys = [b for _, b in pairs]
    mx, my = sum(xs)/n, sum(ys)/n
    num = sum((x-mx)*(y-my) for x, y in pairs)
    dx = sum((x-mx)**2 for x in xs) ** 0.5
    dy = sum((y-my)**2 for y in ys) ** 0.5
    corr = num/(dx*dy) if dx > 0 and dy > 0 else 0.0
    print(f"\n  PRA vs PTS+REB+AST: corr={corr:.3f} n={n}")
    assert corr > 0.85, (
        f"PRA vs PTS+REB+AST corr={corr:.3f} < 0.85 — model drift"
    )
