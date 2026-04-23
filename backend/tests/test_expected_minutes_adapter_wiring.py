"""Regression tests for the blend_bench expected-minutes composition
wired into `NBAScoringAdapter._predict_vk2_prob_over` (2026-04-23).

Scope:
  * PTS and PRA in bench regime → projection replaced with
    `predicted_minutes × historical per-min rate`.
  * REB / AST / 3PM → never composed (narrow rollout).
  * Starter regime (min_L10>=20) → composition skipped.
  * Loader / feature-vector builder / rate clamp sanity.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from services.scoring.adapters.nba_scoring import NBAScoringAdapter


def _bench_feats(pts_L5=6.0, min_L5=14.0, min_L10=14.0,
                 min_L20=16.0, pra_L5=12.0) -> Dict[str, float]:
    """Minimal feats dict covering every field the composer reads."""
    return {
        "pts_L5_mean": pts_L5,
        "reb_L5_mean": 3.0,
        "ast_L5_mean": 2.0,
        "pra_L5_mean": pra_L5,
        "min_played_L3_mean": min_L5,
        "min_played_L3_std": 2.0,
        "min_played_L5_mean": min_L5,
        "min_played_L5_std": 2.5,
        "min_played_L10_mean": min_L10,
        "min_played_L10_std": 3.0,
        "min_played_L20_mean": min_L20,
        "min_played_L20_std": 3.5,
    }


def _starter_feats() -> Dict[str, float]:
    return _bench_feats(pts_L5=22.0, min_L5=34.0, min_L10=34.0,
                        min_L20=33.0, pra_L5=40.0)


@pytest.fixture(scope="module")
def adapter_with_min_model() -> NBAScoringAdapter:
    a = NBAScoringAdapter()
    a._load_min_model()
    assert a._min_model_payload, "minutes model must be loadable"
    return a


@pytest.mark.parametrize("stat_type", ["REB", "AST", "3PM"])
def test_composition_skips_non_target_stats(adapter_with_min_model, stat_type):
    """REB / AST / 3PM are explicitly out of the narrow rollout."""
    feats = _bench_feats()
    result = adapter_with_min_model._compose_minutes_adjusted_projection(
        stat_type=stat_type, baseline_projection=5.0, feats=feats,
    )
    assert result["composition_applied"] is False
    assert result["projection"] == 5.0
    assert result["composed_from_minutes"] is None


@pytest.mark.parametrize("stat_type", ["PTS", "PRA"])
def test_composition_applies_for_pts_pra_in_bench_regime(
    adapter_with_min_model, stat_type,
):
    feats = _bench_feats()
    baseline = 9.0 if stat_type == "PTS" else 18.0
    result = adapter_with_min_model._compose_minutes_adjusted_projection(
        stat_type=stat_type, baseline_projection=baseline, feats=feats,
    )
    assert result["composition_applied"] is True, result
    assert result["composed_from_minutes"] is not None
    assert 0.0 < result["composed_from_minutes"] <= 48.0
    assert 0.0 < result["per_min_rate"] <= adapter_with_min_model._MIN_PER_MIN_RATE_CAP
    # The composed projection should be the product of predicted minutes
    # and per-min rate, not equal to the baseline.
    assert abs(result["projection"] - baseline) > 0.01
    expected = result["composed_from_minutes"] * result["per_min_rate"]
    # per_min_rate is stored rounded to 4 decimals; allow small slack.
    assert abs(result["projection"] - expected) < 0.01


@pytest.mark.parametrize("stat_type", ["PTS", "PRA"])
def test_composition_skips_starter_regime(adapter_with_min_model, stat_type):
    feats = _starter_feats()
    result = adapter_with_min_model._compose_minutes_adjusted_projection(
        stat_type=stat_type, baseline_projection=22.0, feats=feats,
    )
    assert result["composition_applied"] is False
    assert result["projection"] == 22.0
    assert result["min_played_L10_mean"] == pytest.approx(34.0)


def test_composition_skips_when_no_per_min_rate(adapter_with_min_model):
    """Player with rolling L5 minutes ≈ 0 → can't compute rate."""
    feats = _bench_feats(min_L5=0.0)
    before = adapter_with_min_model._min_composition_skipped_no_rate
    result = adapter_with_min_model._compose_minutes_adjusted_projection(
        stat_type="PTS", baseline_projection=5.0, feats=feats,
    )
    assert result["composition_applied"] is False
    assert result["projection"] == 5.0
    assert result["error"] == "insufficient_per_min_rate_inputs"
    assert adapter_with_min_model._min_composition_skipped_no_rate > before


def test_per_min_rate_is_clamped(adapter_with_min_model):
    """Sanity-clamp: historical rates above 5 pts/min are truncated."""
    feats = _bench_feats(pts_L5=100.0, min_L5=2.0)  # 50 pts/min — absurd
    result = adapter_with_min_model._compose_minutes_adjusted_projection(
        stat_type="PTS", baseline_projection=30.0, feats=feats,
    )
    assert result["composition_applied"] is True
    assert result["per_min_rate"] == adapter_with_min_model._MIN_PER_MIN_RATE_CAP


def test_predict_expected_minutes_returns_sane_range(adapter_with_min_model):
    feats = _bench_feats()
    pred = adapter_with_min_model._predict_expected_minutes(feats)
    assert pred is not None
    assert 0.0 <= pred <= 48.0


def test_minutes_feature_vector_covers_schema(adapter_with_min_model):
    feats = _bench_feats()
    row = adapter_with_min_model._build_minutes_feature_vector(feats)
    assert row is not None
    assert len(row) == len(adapter_with_min_model._min_model_payload["features"])


def test_composition_constants_match_narrow_rollout(adapter_with_min_model):
    """Guard against silent regime expansion."""
    assert adapter_with_min_model._MIN_COMPOSITION_STATS == {"PTS", "PRA"}
    assert adapter_with_min_model._MIN_BENCH_THRESHOLD == 20.0


def test_adapter_feature_builder_parses_plain_minute_strings():
    """Adapter-side feature builder must handle the BDL "30" format.
    (Was previously only accepting "30:00".)"""
    from services.scoring.nba_vk2_features import build_features
    logs = [
        {"pts": 10, "reb": 3, "ast": 2, "fg3m": 1, "fga": 8, "fg3a": 3,
         "fta": 2, "min": "18", "fg_pct": 0.45, "fg3_pct": 0.35,
         "ft_pct": 0.75, "player_id": 1, "game_id": i, "season": 2025}
        for i in range(8)
    ]
    feats = build_features(logs)
    assert feats is not None
    # "18" should parse as 18.0, not be dropped.
    assert feats["min_played_L5_mean"] == pytest.approx(18.0, abs=0.01)
    assert feats["min_played_L10_mean"] == pytest.approx(18.0, abs=0.01)
