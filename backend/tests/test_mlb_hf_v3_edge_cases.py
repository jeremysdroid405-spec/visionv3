"""
PRODUCTION-GRADE EDGE-CASE TESTS — MLB HF v3.0_bayes
====================================================
Locks in error-handling and missing-data behavior for live predict():

  PROD-EDGE-01  insufficient_logs   → predict() returns error, not crash
  PROD-EDGE-02  unknown_player      → predict() returns error
  PROD-EDGE-03  unknown_stat        → predict() returns error
  PROD-EDGE-04  missing_park        → park_factor_is_imputed=1, μ valid
  PROD-EDGE-05  missing_dk_odds     → no field error (dk_odds dropped in v3)
  PROD-EDGE-06  missing_statcast    → sc_*_is_imputed=1, rectangular features
  PROD-EDGE-07  pitcher_no_starts   → pitcher_outs returns error gracefully
  PROD-EDGE-08  feature_health_count_matches_list

Run:
    cd /app/backend && python -m pytest tests/test_mlb_hf_v3_edge_cases.py -v
"""
from __future__ import annotations
import os
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


# ─── PROD-EDGE-01: insufficient logs ────────────────────────────────
def test_prod_edge_01_unknown_player(hf_model):
    """Unknown player name + bdl_id returns error gracefully."""
    r = hf_model.predict(
        player_name="Definitely Not A Real MLB Player Xyz123",
        stat_type="hits", line=1.5, bdl_player_id=99999999)
    assert "error" in r
    assert "not found" in r["error"].lower() or "insufficient" in r["error"].lower()


# ─── PROD-EDGE-02: unknown stat ─────────────────────────────────────
def test_prod_edge_02_unknown_stat(hf_model, db):
    """Unknown stat returns 'No model for ...' error."""
    p = db.mlb_master_hub_2026.find_one(
        {"bdl_id": {"$ne": None},
         "bdl_game_logs.5": {"$exists": True}},
        {"_id": 0, "bdl_id": 1, "display_name": 1, "player_name": 1})
    assert p
    r = hf_model.predict(
        player_name=p.get("display_name") or p.get("player_name"),
        stat_type="totally_made_up_stat",
        line=1.5, bdl_player_id=int(p["bdl_id"]))
    assert "error" in r
    assert "no model" in r["error"].lower()


# ─── PROD-EDGE-03: missing park stamps imputation ───────────────────
def test_prod_edge_03_missing_park_imputed(hf_model, db):
    """park_team=None → park_factor_is_imputed=1, μ still valid."""
    p = db.mlb_master_hub_2026.find_one(
        {"bdl_id": {"$ne": None}, "is_pitcher": {"$ne": True},
         "bdl_game_logs.10": {"$exists": True}},
        {"_id": 0, "bdl_id": 1, "display_name": 1, "player_name": 1})
    assert p
    r = hf_model.predict(
        player_name=p.get("display_name") or p.get("player_name"),
        stat_type="hits", line=1.5,
        bdl_player_id=int(p["bdl_id"]),
        park_team=None)
    assert "error" not in r
    assert "park_factor" in (r["feature_health"] or {}).get("imputed_features", [])
    assert r["predicted"] >= 0


# ─── PROD-EDGE-04: dk_odds dropped (no error) ───────────────────────
def test_prod_edge_04_dk_odds_ignored(hf_model, db):
    """v3 dropped dk_odds from features; passing it must not break anything."""
    p = db.mlb_master_hub_2026.find_one(
        {"bdl_id": {"$ne": None}, "is_pitcher": {"$ne": True},
         "bdl_game_logs.10": {"$exists": True}},
        {"_id": 0, "bdl_id": 1, "display_name": 1, "player_name": 1})
    assert p
    name = p.get("display_name") or p.get("player_name")
    bid = int(p["bdl_id"])
    # Same prediction whether dk_odds is supplied or not — v3 drops it.
    r1 = hf_model.predict(player_name=name, stat_type="hits", line=1.5,
                          bdl_player_id=bid, dk_odds=-110)
    r2 = hf_model.predict(player_name=name, stat_type="hits", line=1.5,
                          bdl_player_id=bid, dk_odds=None)
    assert "error" not in r1 and "error" not in r2
    assert r1["predicted"] == r2["predicted"], (
        "dk_odds dropped from v3 features but changes prediction → "
        "feature wiring leak"
    )


# ─── PROD-EDGE-05: feature health count consistency ─────────────────
def test_prod_edge_05_feature_health_consistent(hf_model, db):
    """imputed_count must equal len(imputed_features)."""
    p = db.mlb_master_hub_2026.find_one(
        {"bdl_id": {"$ne": None}, "is_pitcher": {"$ne": True},
         "bdl_game_logs.10": {"$exists": True}},
        {"_id": 0, "bdl_id": 1, "display_name": 1, "player_name": 1})
    r = hf_model.predict(
        player_name=p.get("display_name") or p.get("player_name"),
        stat_type="hits", line=1.5,
        bdl_player_id=int(p["bdl_id"]))
    fh = r.get("feature_health", {})
    assert fh.get("imputed_count") == len(fh.get("imputed_features") or []), (
        f"count={fh.get('imputed_count')}, list_len="
        f"{len(fh.get('imputed_features') or [])}"
    )


# ─── PROD-EDGE-06: pitcher_outs analytical fallback ─────────────────
def test_prod_edge_06_pitcher_outs_no_starts(hf_model, db):
    """Pitcher with <2 starts in logs → pitcher_outs returns error."""
    # Find a pitcher with very few starts (likely a reliever)
    cursor = db.mlb_master_hub_2026.find(
        {"bdl_id": {"$ne": None}, "is_pitcher": True,
         "bdl_game_logs.5": {"$exists": True}},
        {"_id": 0, "bdl_id": 1, "display_name": 1, "player_name": 1,
         "bdl_game_logs": 1}).limit(50)
    target = None
    for p in cursor:
        starts = sum(
            1 for g in (p.get("bdl_game_logs") or [])
            if (g.get("pitch_count") or 0) >= 60
            or float(g.get("innings_pitched") or 0) >= 4.0)
        if starts < 2:
            target = p
            break
    if not target:
        pytest.skip("no <2-starts pitcher found in 50 sample")
    r = hf_model.predict(
        player_name=target.get("display_name") or target.get("player_name"),
        stat_type="pitcher_outs", line=15.5,
        bdl_player_id=int(target["bdl_id"]))
    assert "error" in r and "starts" in r["error"].lower()


# ─── PROD-EDGE-07: feature vector rectangularity ────────────────────
def test_prod_edge_07_feature_vector_rectangular(hf_model, db):
    """Ensure the model's feature_cols matches what _build_friction_features
    emits — no missing/extra fields when SC data is fully missing."""
    # We can't easily call _build_friction_features standalone w/o all
    # inputs; verify via predict() that the call succeeds even when SC
    # inputs are absent (model must impute defaults and produce a valid
    # rectangular vector).
    p = db.mlb_master_hub_2026.find_one(
        {"bdl_id": {"$ne": None}, "is_pitcher": {"$ne": True},
         "bdl_game_logs.5": {"$exists": True}},
        {"_id": 0, "bdl_id": 1, "display_name": 1, "player_name": 1})
    # Patch the SC lookup methods to always return None.
    orig_b = hf_model._get_batter_sc_latest
    orig_p = hf_model._get_pitcher_sc_latest
    hf_model._get_batter_sc_latest = lambda *a, **kw: None
    hf_model._get_pitcher_sc_latest = lambda *a, **kw: None
    try:
        r = hf_model.predict(
            player_name=p.get("display_name") or p.get("player_name"),
            stat_type="hits", line=1.5,
            bdl_player_id=int(p["bdl_id"]))
    finally:
        hf_model._get_batter_sc_latest = orig_b
        hf_model._get_pitcher_sc_latest = orig_p
    assert "error" not in r, f"feature vector broken when SC missing: {r['error']}"
    fh = r.get("feature_health") or {}
    assert "sc_batter" in (fh.get("imputed_features") or []), \
        "sc_batter not flagged as imputed despite SC=None"
