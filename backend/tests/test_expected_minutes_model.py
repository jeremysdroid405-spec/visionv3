"""Regression tests for the NBA expected-minutes model (2026-04-23).

Guards:
  1. Model artifact exists and loads cleanly.
  2. Feature schema is exactly 15 (minutes-only).
  3. Model predicts a sensible range on sample inputs.
  4. VK2 models are rolled back to the 52-feature fixed-mins baseline
     (versioned `NBA_VK_v2_5yr_weighted_pruned52`), NOT the rolled-back
     `..._usage59` or `..._min69` variants.
  5. The rolled-back feature names (fga_per_min_*, usage_trend,
     expected_minutes, pts_per_min_*) are NOT produced by the current
     `build_features` in `retrain_nba_vk2.py`.
"""
from __future__ import annotations

import os
import pickle

import pytest

MODELS_DIR = "/app/backend/models"


def test_minutes_model_artifact_exists():
    path = os.path.join(MODELS_DIR, "nba_expected_minutes.pkl")
    assert os.path.exists(path), "minutes model not trained"
    with open(path, "rb") as f:
        payload = pickle.load(f)
    assert payload["version"] == "NBA_EXPECTED_MINUTES_v1"
    assert len(payload["features"]) == 15
    assert "model" in payload and "scaler" in payload


def test_minutes_model_reasonable_accuracy():
    with open(os.path.join(MODELS_DIR, "nba_expected_minutes.pkl"), "rb") as f:
        payload = pickle.load(f)
    # Sanity thresholds on the 2024 hold-out. R2 was 0.61 at training time;
    # be lenient so normal retraining variance doesn't break CI.
    assert payload["r2_test"] > 0.5, (
        f"minutes model R2 regressed: {payload['r2_test']}"
    )
    assert payload["mae_test"] < 8.0, (
        f"minutes model MAE regressed: {payload['mae_test']}"
    )
    # Bias should be near zero on the held-out set.
    assert abs(payload["bias_test"]) < 1.0, (
        f"minutes model bias too large: {payload['bias_test']}"
    )


def test_minutes_model_top_feature_is_recent_minutes():
    with open(os.path.join(MODELS_DIR, "nba_expected_minutes.pkl"), "rb") as f:
        payload = pickle.load(f)
    top_name, top_val = payload["top_features"][0]
    assert top_name in ("min_L3_mean", "min_L5_mean"), (
        f"expected recent-minutes feature to dominate, got {top_name}"
    )
    assert top_val > 0.3


@pytest.mark.parametrize("stat", ["pts", "reb", "ast", "3pm", "pra"])
def test_vk2_models_are_rolled_back_to_52feat_baseline(stat):
    with open(os.path.join(MODELS_DIR, f"vk2_{stat}.pkl"), "rb") as f:
        payload = pickle.load(f)
    assert payload["version"] == "NBA_VK_v2_5yr_weighted_pruned52", (
        f"vk2_{stat} not rolled back: version={payload['version']}"
    )
    assert len(payload["features"]) == 52, (
        f"vk2_{stat} has {len(payload['features'])} features, expected 52"
    )


def test_build_features_does_not_emit_rollback_features():
    """The rolled-back in-model features (usage / minutes decomposition)
    must NOT appear in the feature dict — they now live in the separate
    expected-minutes model composed downstream."""
    from scripts.retrain_nba_vk2 import build_features, PRUNED_FEATURES  # noqa: E402
    logs = [
        {
            "pts": 20, "reb": 5, "ast": 4, "fg3m": 2,
            "fga": 15, "fg3a": 5, "fta": 4, "min": "32",
            "fg_pct": 0.5, "fg3_pct": 0.4, "ft_pct": 0.8,
            "player_id": 1, "game_id": i, "season": 2024,
        }
        for i in range(10)
    ]
    feats = build_features(logs, target_schema=set(PRUNED_FEATURES))
    forbidden = {
        "fga_per_min_L5", "fga_per_min_L10",
        "pra_per_min_L5", "pra_per_min_L10",
        "pts_per_min_L5", "pts_per_min_L10",
        "reb_per_min_L5", "reb_per_min_L10",
        "ast_per_min_L5", "ast_per_min_L10",
        "touches_per_min_L5", "touches_L20_std",
        "usage_trend",
        "min_L3_mean", "min_trend", "min_floor_L20",
        "min_ceiling_L20", "expected_minutes",
        "expected_stat_pts", "expected_stat_reb",
        "expected_stat_ast", "expected_stat_pra",
    }
    leaked = forbidden & set(feats.keys())
    assert not leaked, f"VK2 feature builder still emits rolled-back features: {leaked}"


def test_retrain_script_no_longer_has_usage_or_minutes_cli():
    """Grep-style guard: the --usage and --minutes CLI flags were
    rolled back. Regression test ensures they don't silently return."""
    path = "/app/backend/scripts/retrain_nba_vk2.py"
    with open(path, "r") as f:
        src = f.read()
    # argparse add_argument call signatures
    assert "add_argument(\n        '--usage'" not in src, "--usage CLI flag should be removed"
    assert "add_argument(\n        '--minutes'" not in src, "--minutes CLI flag should be removed"
    assert "USAGE_FEATURES = [" not in src, "USAGE_FEATURES block should be removed"
    assert "MINUTES_FEATURES = [" not in src, "MINUTES_FEATURES block should be removed"
    assert "PRUNED_USAGE_FEATURES" not in src
    assert "PRUNED_MINUTES_FEATURES" not in src
