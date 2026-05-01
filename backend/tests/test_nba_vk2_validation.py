"""
PRODUCTION-GRADE ARTIFACT VALIDATION — NBA VK2 v2_5yr_weighted_pruned52
========================================================================
Locks in the success criteria for the NBA production model:

REQ-NBA-1  Every saved artifact under /app/backend/models/ for VK2
           (vk2_pts.pkl, vk2_reb.pkl, vk2_ast.pkl, vk2_3pm.pkl,
           vk2_pra.pkl) carries:
              - version == 'NBA_VK_v2_5yr_weighted_pruned52'
              - feature_count == 52
              - residual_sigma_empirical > 0
              - r2_test ≥ documented floor per stat
              - samples_train ≥ 100,000
              - samples_test ≥ 20,000

REQ-NBA-2  R²_test floor per stat (set ~10% below the trained values
           to catch artifact corruption / wrong files):
              PTS  ≥ 0.45   (trained: 0.5151)
              REB  ≥ 0.40   (trained: 0.4728)
              AST  ≥ 0.40   (trained: 0.4825)
              3PM  ≥ 0.28   (trained: 0.3512)
              PRA  ≥ 0.48   (trained: 0.5592)

REQ-NBA-3  RMSE_test ≤ documented ceiling per stat:
              PTS  ≤ 7.0
              REB  ≤ 3.0
              AST  ≤ 2.1
              3PM  ≤ 1.4
              PRA  ≤ 9.5

REQ-NBA-4  Live VegasKillerModel.predict() returns model_version
           consistency — every active stat loaded matches the
           expected version via metric attestation.

Run:
    cd /app/backend && python -m pytest tests/test_nba_vk2_validation.py -v
"""
from __future__ import annotations
import os
import pickle
from pathlib import Path

import pytest
import pymongo

EXPECTED_VERSION = "NBA_VK_v2_5yr_weighted_pruned52"
MODEL_DIR = Path("/app/backend/models")
STATS = ("PTS", "REB", "AST", "3PM", "PRA")

R2_FLOORS = {"PTS": 0.45, "REB": 0.40, "AST": 0.40, "3PM": 0.28, "PRA": 0.48}
RMSE_CEILINGS = {"PTS": 7.0, "REB": 3.0, "AST": 2.1, "3PM": 1.4, "PRA": 9.5}
SAMPLES_TRAIN_FLOOR = 100_000
SAMPLES_TEST_FLOOR = 20_000


def _pkl_path(stat: str) -> Path:
    return MODEL_DIR / f"vk2_{stat.lower()}.pkl"


# ─── REQ-NBA-1: artifact present + correct schema ──────────────────
@pytest.mark.parametrize("stat", STATS)
def test_nba_vk2_artifact_schema(stat):
    """REQ-NBA-1: every VK2 artifact carries the expected schema."""
    p = _pkl_path(stat)
    assert p.exists(), f"missing artifact: {p}"
    with open(p, "rb") as fh:
        d = pickle.load(fh)
    assert d.get("version") == EXPECTED_VERSION, (
        f"{stat}: version={d.get('version')!r}, expected {EXPECTED_VERSION!r}"
    )
    assert d.get("feature_count") == 52, (
        f"{stat}: feature_count={d.get('feature_count')}, expected 52"
    )
    assert len(d.get("features") or []) == 52, (
        f"{stat}: features list length = {len(d.get('features') or [])}, expected 52"
    )
    sigma = d.get("residual_sigma_empirical")
    assert sigma is not None and sigma > 0, (
        f"{stat}: residual_sigma_empirical={sigma}, must be > 0"
    )
    assert d.get("samples_train", 0) >= SAMPLES_TRAIN_FLOOR, (
        f"{stat}: samples_train={d.get('samples_train')} < {SAMPLES_TRAIN_FLOOR}"
    )
    assert d.get("samples_test", 0) >= SAMPLES_TEST_FLOOR, (
        f"{stat}: samples_test={d.get('samples_test')} < {SAMPLES_TEST_FLOOR}"
    )


# ─── REQ-NBA-2: R²_test floors ─────────────────────────────────────
@pytest.mark.parametrize("stat", STATS)
def test_nba_vk2_r2_test_floor(stat):
    """REQ-NBA-2: R²_test ≥ documented floor per stat."""
    with open(_pkl_path(stat), "rb") as fh:
        d = pickle.load(fh)
    floor = R2_FLOORS[stat]
    r2 = d.get("r2_test")
    assert r2 is not None, f"{stat}: r2_test missing"
    assert r2 >= floor, f"{stat}: r2_test={r2:.4f} < floor {floor}"


# ─── REQ-NBA-3: RMSE_test ceilings ─────────────────────────────────
@pytest.mark.parametrize("stat", STATS)
def test_nba_vk2_rmse_test_ceiling(stat):
    """REQ-NBA-3: RMSE_test ≤ documented ceiling per stat."""
    with open(_pkl_path(stat), "rb") as fh:
        d = pickle.load(fh)
    ceiling = RMSE_CEILINGS[stat]
    rmse = d.get("rmse_test")
    assert rmse is not None, f"{stat}: rmse_test missing"
    assert rmse <= ceiling, f"{stat}: rmse_test={rmse:.4f} > ceiling {ceiling}"


# ─── REQ-NBA-4: live predict returns model_version consistency ─────
@pytest.fixture(scope="module")
def adapter():
    """Production NBA scoring adapter — same instantiation path used
    by the live FastAPI server."""
    from services.scoring.adapters.nba_scoring import NBAScoringAdapter
    a = NBAScoringAdapter()
    a._load_vk2_models()
    yield a


def test_nba_vk2_live_models_loaded(adapter):
    """REQ-NBA-4: production adapter loads all 5 expected VK2 stats."""
    loaded = sorted(adapter._vk2_models.keys())
    assert set(loaded) == set(STATS), f"loaded {loaded}, expected {STATS}"


def test_nba_vk2_live_metrics_consistent(adapter):
    """REQ-NBA-4: each loaded model exposes 52 features and the
    expected version stamp consistent with the artifact metadata."""
    for stat in STATS:
        m = adapter._vk2_models[stat]
        feats = m.get("features") or []
        assert len(feats) == 52, f"{stat}: live features len={len(feats)}, expected 52"
        assert m.get("version") == EXPECTED_VERSION, \
            f"{stat}: version={m.get('version')}, expected {EXPECTED_VERSION}"
        assert m.get("sigma") and m["sigma"] > 0, \
            f"{stat}: sigma={m.get('sigma')}, must be > 0"
