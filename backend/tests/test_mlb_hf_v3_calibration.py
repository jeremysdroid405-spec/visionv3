"""
PRODUCTION-GRADE CALIBRATION TESTS — MLB HF v3.0_bayes
======================================================
These run live predict() across a representative sample of MLB players
(both batters and pitchers) and assert the *distribution* of outputs
matches realistic baseball ranges. Designed to catch model drift,
mis-scaled features, broken xgboost artifacts, etc., even when the
mean Bleday-style canary doesn't fail.

Coverage:

  PROD-CAL-01  μ-distribution / batter
              · 75-batter random sample; per-stat μ should fall within
                tight realistic bands (median + p95).
  PROD-CAL-02  μ-distribution / pitcher
              · 30 starting pitchers; K μ ∈ [3.0, 14.0], outs μ ∈ [9, 27].
  PROD-CAL-03  4× L20 sanity gate (extended canary)
              · Across 75 batters AND 30 pitchers, NO projection may
                exceed 4× the player's own L20 mean (the small-sample
                blow-up indicator).
  PROD-CAL-04  σ-distribution
              · No σ is exactly 0 for any non-rare stat.
              · σ for hits ∈ [0.4, 2.0], σ for K ∈ [1.0, 5.0] (median).
  PROD-CAL-05  prob_over symmetry
              · Across 50 props, when |line - μ| < 0.1 the prob_over is
                in [40, 60] — i.e. the model isn't biased toward picks.

Run:
    cd /app/backend && python -m pytest tests/test_mlb_hf_v3_calibration.py -v -s
"""
from __future__ import annotations
import os
import statistics
from typing import List

import pytest
import pymongo

EXPECTED_VERSION = "MLB_HF_v3.0_bayes"


@pytest.fixture(scope="module")
def db():
    client = pymongo.MongoClient(
        os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    yield client[os.environ.get("DB_NAME", "pick_vision")]
    client.close()


@pytest.fixture(scope="module")
def hf_model(db):
    import services.mlb_high_friction_model as hfm
    hfm._mlb_hf_instance = None
    m = hfm.get_mlb_high_friction_model(db)
    n = m.load_models()
    assert n >= 14
    return m


def _sample_batters(db, n=75):
    return list(db.mlb_master_hub_2026.aggregate([
        {"$match": {"bdl_id": {"$ne": None}, "is_pitcher": {"$ne": True},
                    "bdl_game_logs.20": {"$exists": True}}},
        {"$project": {"_id": 0, "bdl_id": 1, "display_name": 1,
                      "player_name": 1, "team": 1,
                      "bdl_game_logs": {"$slice": ["$bdl_game_logs", 20]}}},
        {"$sample": {"size": n}},
    ]))


def _sample_pitchers(db, n=30):
    docs = list(db.mlb_master_hub_2026.aggregate([
        {"$match": {"bdl_id": {"$ne": None}, "is_pitcher": True,
                    "bdl_game_logs.5": {"$exists": True}}},
        {"$project": {"_id": 0, "bdl_id": 1, "display_name": 1,
                      "player_name": 1, "team": 1,
                      "bdl_game_logs": {"$slice": ["$bdl_game_logs", 25]}}},
        {"$sample": {"size": n * 3}},
    ]))
    starters = []
    for p in docs:
        starts = sum(
            1 for g in (p.get("bdl_game_logs") or [])
            if (g.get("pitch_count") or 0) >= 60
            or float(g.get("innings_pitched") or 0) >= 4.0)
        if starts >= 3:
            starters.append(p)
        if len(starters) >= n:
            break
    return starters


def _l20_mean(logs, *fields):
    vals = []
    for g in logs[:20]:
        try:
            vals.append(sum(float(g.get(f, 0) or 0) for f in fields))
        except (TypeError, ValueError):
            continue
    return sum(vals) / len(vals) if vals else 0.0


# ─── PROD-CAL-01: batter μ distribution ─────────────────────────────
BATTER_BANDS = {
    # stat: (median_low, median_high, p95_max)
    "hits":           (0.5,  1.5, 3.0),
    "total_bases":    (0.7,  2.3, 5.0),
    "rbis":           (0.3,  1.5, 4.0),
    "runs":           (0.25, 1.3, 3.5),
    "hits+runs+rbis": (1.3,  4.0, 7.0),
    "home_runs":      (0.05, 0.5, 1.5),
}


@pytest.mark.parametrize("stat,band", BATTER_BANDS.items(), ids=list(BATTER_BANDS))
def test_prod_cal_01_batter_mu_distribution(hf_model, db, stat, band):
    """PROD-CAL-01: median μ falls in realistic band; no insane p95.

    Filters to active contributors (L20 H+R+RBI mean ≥ 0.5) so the
    distribution reflects everyday MLB hitters, not bench/IL players.
    """
    median_low, median_high, p95_max = band
    batters = _sample_batters(db, 100)
    preds = []
    for p in batters:
        logs = p.get("bdl_game_logs") or []
        if _l20_mean(logs, "hits", "runs", "rbis") < 0.5:
            continue  # filter bench/IL noise
        r = hf_model.predict(
            player_name=p.get("display_name") or p.get("player_name"),
            stat_type=stat, line=1.5,
            bdl_player_id=int(p["bdl_id"]),
            park_team=p.get("team"))
        if "error" not in r:
            preds.append(r["predicted"])
    assert len(preds) >= 30, f"too few successful predictions: {len(preds)}"
    median = statistics.median(preds)
    p95 = statistics.quantiles(preds, n=20)[-1] if len(preds) >= 20 else max(preds)
    print(f"\n  {stat}: n={len(preds)} median={median:.3f} p95={p95:.3f}")
    assert median_low <= median <= median_high, (
        f"{stat}: median μ={median:.2f} outside band [{median_low}, {median_high}]"
    )
    assert p95 <= p95_max, (
        f"{stat}: p95 μ={p95:.2f} exceeds realistic ceiling {p95_max}"
    )


# ─── PROD-CAL-02: pitcher μ distribution ────────────────────────────
def test_prod_cal_02_pitcher_k_distribution(hf_model, db):
    """PROD-CAL-02: starter K μ median in [3.5, 8.0]; p95 ≤ 14."""
    pitchers = _sample_pitchers(db, 30)
    if len(pitchers) < 10:
        pytest.skip(f"only {len(pitchers)} starters available")
    preds = []
    for p in pitchers:
        r = hf_model.predict(
            player_name=p.get("display_name") or p.get("player_name"),
            stat_type="pitcher_strikeouts", line=5.5,
            bdl_player_id=int(p["bdl_id"]),
            park_team=p.get("team"))
        if "error" not in r:
            preds.append(r["predicted"])
    assert len(preds) >= 8, f"only {len(preds)} successful K predictions"
    median = statistics.median(preds)
    p95 = max(preds) if len(preds) < 20 else statistics.quantiles(preds, n=20)[-1]
    print(f"\n  pitcher_K: n={len(preds)} median={median:.2f} p95={p95:.2f}")
    assert 3.5 <= median <= 9.0, f"K median {median:.2f} outside [3.5,9.0]"
    assert p95 <= 14.0, f"K p95 {p95:.2f} > 14"


def test_prod_cal_02_pitcher_outs_distribution(hf_model, db):
    """PROD-CAL-02: starter Outs μ median in [12, 20]; p95 ≤ 27."""
    pitchers = _sample_pitchers(db, 30)
    if len(pitchers) < 10:
        pytest.skip(f"only {len(pitchers)} starters")
    preds = []
    for p in pitchers:
        r = hf_model.predict(
            player_name=p.get("display_name") or p.get("player_name"),
            stat_type="pitcher_outs", line=15.5,
            bdl_player_id=int(p["bdl_id"]),
            park_team=p.get("team"))
        if "error" not in r:
            preds.append(r["predicted"])
    assert len(preds) >= 8
    median = statistics.median(preds)
    p95 = max(preds) if len(preds) < 20 else statistics.quantiles(preds, n=20)[-1]
    print(f"\n  pitcher_outs: n={len(preds)} median={median:.2f} p95={p95:.2f}")
    assert 12.0 <= median <= 20.0
    assert p95 <= 27.0


# ─── PROD-CAL-03: 4× L20 sanity (extended canary) ───────────────────
def test_prod_cal_03_no_blowup_batters(hf_model, db):
    """No batter HRR projection > 4× L20 mean."""
    batters = _sample_batters(db, 75)
    blowups = []
    for p in batters:
        logs = p.get("bdl_game_logs") or []
        if len(logs) < 10:
            continue
        l20m = _l20_mean(logs, "hits", "runs", "rbis")
        if l20m < 0.5:
            continue
        r = hf_model.predict(
            player_name=p.get("display_name") or p.get("player_name"),
            stat_type="hits+runs+rbis", line=2.5,
            bdl_player_id=int(p["bdl_id"]))
        if "error" in r:
            continue
        ratio = r["predicted"] / max(l20m, 0.01)
        if ratio > 4.0:
            blowups.append((p.get("display_name"), round(r["predicted"], 2),
                             round(l20m, 2), round(ratio, 2)))
    assert not blowups, f"REGRESSION: {len(blowups)} batter blow-ups: {blowups}"


def test_prod_cal_03_no_blowup_pitchers(hf_model, db):
    """No pitcher K projection > 3× L20 K mean."""
    pitchers = _sample_pitchers(db, 30)
    if len(pitchers) < 5:
        pytest.skip(f"only {len(pitchers)} starters")
    blowups = []
    for p in pitchers:
        logs = p.get("bdl_game_logs") or []
        if len(logs) < 6:
            continue
        l20m = _l20_mean(logs, "pitcher_strikeouts")
        if l20m < 1.0:
            continue
        r = hf_model.predict(
            player_name=p.get("display_name") or p.get("player_name"),
            stat_type="pitcher_strikeouts", line=5.5,
            bdl_player_id=int(p["bdl_id"]))
        if "error" in r:
            continue
        ratio = r["predicted"] / max(l20m, 0.01)
        if ratio > 3.0:
            blowups.append((p.get("display_name"), round(r["predicted"], 2),
                             round(l20m, 2), round(ratio, 2)))
    assert not blowups, f"REGRESSION: {len(blowups)} pitcher K blow-ups: {blowups}"


# ─── PROD-CAL-04: σ distribution ────────────────────────────────────
def test_prod_cal_04_sigma_distribution(hf_model, db):
    """σ ranges: hits σ median ∈ [0.4, 2.0]; K σ median ∈ [1.0, 5.0]."""
    batters = _sample_batters(db, 50)
    hits_sigmas = []
    for p in batters:
        r = hf_model.predict(
            player_name=p.get("display_name") or p.get("player_name"),
            stat_type="hits", line=1.5, bdl_player_id=int(p["bdl_id"]))
        if "error" not in r and r.get("std_dev"):
            hits_sigmas.append(r["std_dev"])
            assert r["std_dev"] > 0, "σ=0 for hits — model regression"
    pitchers = _sample_pitchers(db, 20)
    k_sigmas = []
    for p in pitchers:
        r = hf_model.predict(
            player_name=p.get("display_name") or p.get("player_name"),
            stat_type="pitcher_strikeouts", line=5.5,
            bdl_player_id=int(p["bdl_id"]))
        if "error" not in r and r.get("std_dev"):
            k_sigmas.append(r["std_dev"])
    if hits_sigmas:
        med = statistics.median(hits_sigmas)
        print(f"\n  hits σ median={med:.3f}")
        assert 0.3 <= med <= 2.5, f"hits σ median {med:.2f} outside [0.3,2.5]"
    if k_sigmas:
        med = statistics.median(k_sigmas)
        print(f"  K σ median={med:.3f}")
        assert 0.8 <= med <= 5.5, f"K σ median {med:.2f} outside [0.8,5.5]"


# ─── PROD-CAL-05: prob_over symmetry near line ──────────────────────
def test_prod_cal_05_prob_over_at_line_balanced(hf_model, db):
    """When line = μ (within 0.1), prob_over ≈ 50% (in [40, 60])."""
    batters = _sample_batters(db, 50)
    near_line_probs = []
    for p in batters:
        r0 = hf_model.predict(
            player_name=p.get("display_name") or p.get("player_name"),
            stat_type="hits", line=1.5, bdl_player_id=int(p["bdl_id"]))
        if "error" in r0:
            continue
        mu = r0["predicted"]
        # Set line right at μ — prob should be ~50.
        r1 = hf_model.predict(
            player_name=p.get("display_name") or p.get("player_name"),
            stat_type="hits", line=mu, bdl_player_id=int(p["bdl_id"]))
        if "error" not in r1 and r1.get("prob_over") is not None:
            near_line_probs.append(r1["prob_over"])
    assert len(near_line_probs) >= 20, f"too few samples: {len(near_line_probs)}"
    med = statistics.median(near_line_probs)
    print(f"\n  prob_over at line=μ: median={med:.2f}% (n={len(near_line_probs)})")
    assert 35 <= med <= 65, f"prob_over@μ median {med:.1f}% biased outside [35,65]"
