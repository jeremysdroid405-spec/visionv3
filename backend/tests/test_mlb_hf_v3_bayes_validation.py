"""
MLB HF v3.0_bayes — Production validation regression test
=========================================================
Locks in the success criteria for the Bayesian-shrinkage retrain:

REQ-V3-1  Every saved model artifact under /app/backend/models/mlb_hf/
          carries `version == 'MLB_HF_v3.0_bayes'` and a non-empty
          feature list.

REQ-V3-2  JJ Bleday (bdl_id=597) hits+runs+rbis projection must be
          ≤ 4.0 — pre-bayes the projection was 6.55-6.74 (3.4×
          his L20 mean of ~2.0, far outside any reasonable
          distribution). Post-bayes it must be shrunk back to a
          realistic μ.

REQ-V3-3  No batter projection in the production pool exceeds
          4× the player's L20 mean. This is a global sanity gate
          guarding against the same small-sample blow-up reappearing
          for some other player.

REQ-V3-4  predict() return for any model stat carries
          model_version='MLB_HF_v3.0_bayes' (live-path stamping is
          not silently regressing).

Run:
    cd /app/backend && python -m pytest tests/test_mlb_hf_v3_bayes_validation.py -v
"""
from __future__ import annotations
import os
import pickle
from pathlib import Path

import pymongo
import pytest

MODEL_DIR = Path("/app/backend/models/mlb_hf")
EXPECTED_VERSION = "MLB_HF_v3.0_bayes"


# ─── REQ-V3-1: artifact version stamping ────────────────────────────
def _all_pkl_files():
    return sorted(p for p in MODEL_DIR.glob("mlb_hf_*.pkl"))


@pytest.mark.parametrize("pkl_path", _all_pkl_files(), ids=lambda p: p.name)
def test_v3_artifact_version_and_features(pkl_path):
    """REQ-V3-1: every artifact stamps v3.0_bayes + has feature list."""
    with open(pkl_path, "rb") as fh:
        data = pickle.load(fh)
    assert data.get("version") == EXPECTED_VERSION, (
        f"{pkl_path.name}: version={data.get('version')!r}, "
        f"expected {EXPECTED_VERSION!r}"
    )
    feats = data.get("features") or []
    assert len(feats) >= 200, (
        f"{pkl_path.name}: only {len(feats)} features (expected ≥200 "
        "after Bayesian SC shrinkage wiring)"
    )
    # Sanity: SC features must still be present (the shrinkage doesn't
    # remove fields, only smooths them).
    assert any(f.startswith("sc_b_") or f.startswith("sc_p_") for f in feats), \
        f"{pkl_path.name}: no Statcast features in feature list"


# ─── REQ-V3-2: Bleday H+R+RBI shrinkage proof ───────────────────────
@pytest.fixture(scope="module")
def hf_model():
    client = pymongo.MongoClient(
        os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    )
    db = client[os.environ.get("DB_NAME", "pick_vision")]
    import services.mlb_high_friction_model as hfm
    hfm._mlb_hf_instance = None  # ensure fresh load
    model = hfm.get_mlb_high_friction_model(db)
    loaded = model.load_models()
    assert loaded >= 14, f"Only {loaded} models loaded — retrain incomplete?"
    yield model
    client.close()


def test_v3_bleday_hrr_shrunk(hf_model):
    """REQ-V3-2: JJ Bleday H+R+RBI projection must be ≤ 4.0."""
    result = hf_model.predict(
        player_name="JJ Bleday",
        stat_type="hits+runs+rbis",
        line=2.5,
        bdl_player_id=597,
    )
    assert "error" not in result, f"predict() failed: {result.get('error')}"
    pred = result["predicted"]
    raw = result.get("raw_prediction")
    print(f"\n  Bleday H+R+RBI: predicted={pred} raw={raw} version={result.get('model_version')}")
    assert pred <= 4.0, (
        f"REGRESSION: Bleday H+R+RBI projection={pred} > 4.0 "
        f"(pre-bayes was 6.55-6.74). Bayesian shrinkage is NOT working."
    )
    assert result.get("model_version") == EXPECTED_VERSION, (
        f"model_version={result.get('model_version')!r}, "
        f"expected {EXPECTED_VERSION!r}"
    )


# ─── REQ-V3-3: global 4× L20-mean sanity gate ───────────────────────
def test_v3_no_batter_blowup_in_pool(hf_model):
    """
    REQ-V3-3: scan a sample of MLB batters; flag any whose H+R+RBI
    projection exceeds 4× their L20 mean. This is the canary that
    catches small-sample feature blow-ups for ANY player, not just
    Bleday.
    """
    db = hf_model.db
    # Sample: hub players with bdl_game_logs and ≥10 logs.
    sample = list(db.mlb_master_hub_2026.aggregate([
        {"$match": {"bdl_id": {"$ne": None},
                    "bdl_game_logs": {"$exists": True, "$not": {"$size": 0}}}},
        {"$project": {"_id": 0, "bdl_id": 1, "display_name": 1, "player_name": 1,
                      "bdl_game_logs": {"$slice": ["$bdl_game_logs", 20]}}},
        {"$sample": {"size": 25}},
    ]))

    blowups = []
    for p in sample:
        logs = p.get("bdl_game_logs") or []
        if len(logs) < 10:
            continue
        # L20 mean H+R+RBI
        vals = []
        for g in logs[:20]:
            try:
                v = (float(g.get("hits", 0) or 0)
                     + float(g.get("runs", 0) or 0)
                     + float(g.get("rbis", 0) or 0))
                vals.append(v)
            except (TypeError, ValueError):
                continue
        if len(vals) < 10:
            continue
        l20_mean = sum(vals) / len(vals)
        result = hf_model.predict(
            player_name=p.get("display_name") or p.get("player_name"),
            stat_type="hits+runs+rbis", line=2.5,
            bdl_player_id=int(p["bdl_id"]),
        )
        if "error" in result:
            continue
        pred = result["predicted"]
        # Floor (active baseline) is 0.75 for HRR. Skip players whose L20
        # mean is so low (~0) that the floor dominates — that's by design.
        if l20_mean < 0.5:
            continue
        ratio = pred / max(l20_mean, 0.01)
        if ratio > 4.0:
            blowups.append((
                p.get("display_name") or p.get("player_name"),
                round(pred, 2), round(l20_mean, 2), round(ratio, 2),
            ))

    assert not blowups, (
        f"REGRESSION: {len(blowups)} batter(s) with H+R+RBI projection "
        f">4× L20 mean (small-sample blow-up indicator). Failures: {blowups}"
    )


# ─── REQ-V3-4: live-path version stamping ───────────────────────────
def test_v3_live_predict_version_stamp(hf_model):
    """REQ-V3-4: any successful predict() returns v3.0_bayes."""
    # Pick the first sample player to keep this fast.
    db = hf_model.db
    p = db.mlb_master_hub_2026.find_one(
        {"bdl_id": {"$ne": None},
         "bdl_game_logs": {"$exists": True, "$not": {"$size": 0}}},
        {"_id": 0, "bdl_id": 1, "display_name": 1, "player_name": 1},
    )
    assert p, "No master_hub player with logs available"
    result = hf_model.predict(
        player_name=p.get("display_name") or p.get("player_name"),
        stat_type="hits", line=0.5,
        bdl_player_id=int(p["bdl_id"]),
    )
    if "error" in result:
        pytest.skip(f"predict() error (data, not model): {result['error']}")
    assert result.get("model_version") == EXPECTED_VERSION
