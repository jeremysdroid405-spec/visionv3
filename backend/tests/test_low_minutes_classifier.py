"""Regression tests for the low-minutes classifier (2026-04-23).

Guards:
  * Model artifact exists and carries both low_12 and very_low_8 variants.
  * Feature schema is exactly 15.
  * AUC >= 0.85 on the 2024 hold-out (v1 was 0.912; generous slack).
  * Bench-segment AUC >= 0.82 (v1 was 0.879) — this is the regime we
    most need the classifier to be strong in.
  * Top-feature is a recency-minutes rolling feature (sanity check).
  * The classifier-based blend was NOT wired into production — no
    traces of `low_minutes_prob`, `projection_blend_method` in the
    live scoring adapter.
"""
from __future__ import annotations

import os
import pickle

import pytest


MODEL_PATH = "/app/backend/models/low_minutes_classifier.pkl"


def test_classifier_artifact_exists():
    assert os.path.exists(MODEL_PATH), "classifier not trained"
    with open(MODEL_PATH, "rb") as f:
        p = pickle.load(f)
    assert p["version"] == "NBA_LOW_MINUTES_CLASSIFIER_v1"
    assert "model_low_12" in p and "model_very_low_8" in p
    assert "scaler" in p
    assert len(p["features"]) == 15


def test_feature_schema_matches_spec():
    with open(MODEL_PATH, "rb") as f:
        p = pickle.load(f)
    required = {
        "min_played_L3_mean", "min_played_L5_mean",
        "min_played_L10_mean", "min_played_L20_mean",
        "min_played_L10_std", "min_played_L20_std",
        "min_trend_L5_vs_L20",
        "games_played_last_10", "games_started_last_10",
        "starter_flag", "rotation_flag", "bench_flag",
        "home_flag", "rest_days", "back_to_back_flag",
    }
    assert set(p["features"]) == required


def test_classifier_overall_auc_acceptable():
    with open(MODEL_PATH, "rb") as f:
        p = pickle.load(f)
    metrics = p["metrics"]["low_12"]["overall"]
    assert metrics["auc"] >= 0.85, (
        f"overall AUC regressed: {metrics['auc']}"
    )


def test_classifier_bench_segment_strong():
    with open(MODEL_PATH, "rb") as f:
        p = pickle.load(f)
    bench = p["metrics"]["low_12"]["segments"].get("bench (L10<20)")
    assert bench is not None
    assert bench["auc"] >= 0.82, (
        f"bench segment AUC regressed: {bench['auc']}"
    )


def test_top_feature_is_recent_minutes():
    with open(MODEL_PATH, "rb") as f:
        p = pickle.load(f)
    top_name, top_val = p["metrics"]["low_12"]["top_features"][0]
    assert top_name in ("min_played_L3_mean", "min_played_L5_mean")
    assert top_val >= 0.3


def test_very_low_8_variant_trained():
    with open(MODEL_PATH, "rb") as f:
        p = pickle.load(f)
    ov = p["metrics"]["very_low_8"]["overall"]
    assert ov["auc"] >= 0.85
    assert "thresholds" in p["metrics"]["very_low_8"]


def test_no_low_minutes_blend_wired_to_production():
    """The classifier-based blend was evaluated and rejected (REVERT).
    Guard against accidental wiring in the NBA scoring adapter."""
    with open("/app/backend/services/scoring/adapters/nba_scoring.py") as f:
        src = f.read()
    for name in (
        "low_minutes_prob",
        "low_minutes_classifier",
        "projection_blend_method",
        "load_low_minutes_player_projections",
    ):
        assert name not in src, (
            f"'{name}' found in nba_scoring.py — classifier-based "
            f"blend was wired without approval"
        )


@pytest.mark.parametrize("threshold_key", ["0.30", "0.50", "0.70"])
def test_threshold_table_includes_full_confusion(threshold_key):
    """Guard the threshold sweep stored on the artifact carries all
    four confusion cells."""
    with open(MODEL_PATH, "rb") as f:
        p = pickle.load(f)
    thr = p["metrics"]["low_12"]["thresholds"][threshold_key]
    for k in ("tp", "tn", "fp", "fn", "precision", "recall", "f1"):
        assert k in thr, f"missing {k} at threshold {threshold_key}"
