"""Regression tests for pitcher_outs SSOT & Phase 2B routing.

These tests pin the contract introduced on 2026-05-17:
  • pitcher_outs is extractable via `_get_stat_value` from the
    `innings_pitched` MLB-notation float (5.2 → 17 outs).
  • predict() routes to the analytical fallback when no XGBoost
    pickle is loaded, and otherwise prefers the model path.
  • The analytical fallback exposes the pitcher_hit_rate diagnostic
    in friction_audit, mirroring the 10-/5-start contract enforced
    downstream by `mlb_tier_sorter::_calculate_pitcher_hit_rate_sides`.

The tests intentionally use a fake DB stub — they do not exercise
the master_hub. The hub lookup is short-circuited by injecting a
prepared `player` dict on the model under test.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "/app/backend")

from services.mlb_high_friction_model import MLBHighFrictionModel  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# _get_stat_value — extraction fidelity
# ─────────────────────────────────────────────────────────────────────
@pytest.fixture
def model() -> MLBHighFrictionModel:
    """Build a model with mocked DB (no I/O)."""
    db = MagicMock()
    db.__getitem__.return_value = MagicMock()
    return MLBHighFrictionModel(db)


@pytest.mark.parametrize("ip,expected_outs", [
    (5.0, 15.0),
    (5.1, 16.0),
    (5.2, 17.0),
    (0.0, 0.0),
    (0.1, 1.0),
    (0.2, 2.0),
    (6.0, 18.0),
    (7.1, 22.0),
    (9.0, 27.0),
])
def test_pitcher_outs_decoded_from_ip(model, ip, expected_outs):
    """MLB-notation IP must decode to integer outs (5.2 → 17)."""
    assert model._get_stat_value(
        {"innings_pitched": ip}, "pitcher_outs",
    ) == expected_outs


def test_pitcher_outs_prefers_explicit_outs_field(model):
    """If feed supplies `outs` directly we trust it over IP decoding."""
    assert model._get_stat_value(
        {"innings_pitched": 5.0, "outs": 17}, "pitcher_outs",
    ) == 17.0


def test_pitcher_outs_handles_missing(model):
    assert model._get_stat_value(
        {"innings_pitched": None}, "pitcher_outs",
    ) is None
    assert model._get_stat_value({}, "pitcher_outs") is None


def test_pitcher_outs_in_stat_field_map(model):
    """STAT_FIELD_MAP must route pitcher_outs through the calc path so
    every consumer (training, retrain workers, live predict) shares
    the same SSOT extraction."""
    assert model.STAT_FIELD_MAP["pitcher_outs"] == "_calc_pitcher_outs"


def test_pitcher_outs_listed_in_stat_types(model):
    assert "pitcher_outs" in model.MLB_STAT_TYPES


# ─────────────────────────────────────────────────────────────────────
# Analytical fallback — pitcher_hit_rate diagnostic wiring
# ─────────────────────────────────────────────────────────────────────
def _fake_game_logs(ip_series: List[float]) -> List[Dict[str, Any]]:
    """Build minimal game-log shape, most-recent first."""
    return [
        {
            "innings_pitched": ip,
            "pitch_count": 95,
            "pitcher_strikeouts": 6,
            "date": f"2026-05-{1 + i:02d}",
        }
        for i, ip in enumerate(ip_series)
    ]


def test_hit_rate_diag_window_caps_at_20(model):
    """≥20 starts → window=20 with strict denominator (rolling L20)."""
    logs = _fake_game_logs([6.0] * 25)  # 18 outs every start
    diag = model._compute_pitcher_outs_hit_rate(logs, line=17.5)
    assert diag is not None
    assert diag["pitcher_hit_rate_n"] == 20
    assert diag["pitcher_hit_rate_window_used"] == "20"
    # 18 > 17.5 → all 20 OVER → 100%
    assert diag["pitcher_hit_rate_over"] == 100.0
    assert diag["pitcher_hit_rate_under"] == 0.0
    assert diag["pitcher_hit_rate_avg_outs"] == 18.0


def test_hit_rate_diag_uses_all_in_growth_phase(model):
    """12 starts → window=12 (mid-growth, 5 ≤ n < 20)."""
    logs = _fake_game_logs([6.0] * 12)
    diag = model._compute_pitcher_outs_hit_rate(logs, line=17.5)
    assert diag is not None
    assert diag["pitcher_hit_rate_n"] == 12
    assert diag["pitcher_hit_rate_window_used"] == "12"


def test_hit_rate_diag_5_start_minimum(model):
    """5-9 starts → variable window equal to start count."""
    logs = _fake_game_logs([5.0, 5.0, 6.0, 6.0, 6.0, 6.0])  # 6 starts
    diag = model._compute_pitcher_outs_hit_rate(logs, line=15.5)
    assert diag is not None
    assert diag["pitcher_hit_rate_n"] == 6
    assert diag["pitcher_hit_rate_window_used"] == "6"
    # 15.0, 15.0, 18.0, 18.0, 18.0, 18.0 vs 15.5 → 4 over / 6
    assert diag["pitcher_hit_rate_over"] == pytest.approx(66.7, abs=0.1)


def test_hit_rate_diag_below_5_returns_none(model):
    """<5 starts → diagnostic is suppressed entirely (cold start)."""
    logs = _fake_game_logs([6.0, 6.0, 6.0, 6.0])  # 4 starts
    assert model._compute_pitcher_outs_hit_rate(logs, line=17.5) is None


def test_hit_rate_diag_requires_line(model):
    logs = _fake_game_logs([6.0] * 10)
    assert model._compute_pitcher_outs_hit_rate(logs, line=None) is None


def test_analytical_outs_block_shape(model):
    logs = _fake_game_logs([6.0, 5.2, 6.1, 5.0])  # 4 starts
    block = model._compute_analytical_outs_block(logs)
    assert block is not None
    assert "expected_ip" in block
    assert "starts_used" in block
    assert "analytical_mu_outs" in block
    assert "analytical_sigma_outs" in block
    assert block["starts_used"] == 4


def test_analytical_outs_block_needs_two_starts(model):
    """1-start cold path → no analytical block."""
    logs = _fake_game_logs([6.0])
    assert model._compute_analytical_outs_block(logs) is None


# ─────────────────────────────────────────────────────────────────────
# predict() routing — analytical fallback wired when model absent
# ─────────────────────────────────────────────────────────────────────
def test_predict_routes_to_analytical_when_model_missing(model):
    """When `pitcher_outs` is NOT in self.models, predict must call
    `_predict_pitcher_outs` (analytical fallback)."""
    # Ensure no model is loaded.
    assert "pitcher_outs" not in model.models

    # Stub the master hub lookup with a fake pitcher.
    fake_player = {
        "bdl_id": 999,
        "player_name": "Test Pitcher",
        "display_name": "Test Pitcher",
        "bdl_game_logs": _fake_game_logs([6.0, 5.2, 6.1, 5.0, 6.0]),
    }
    model.master_hub.find_one = MagicMock(return_value=fake_player)

    out = model.predict(
        player_name="Test Pitcher", stat_type="pitcher_outs",
        line=17.5, bdl_player_id=999,
    )
    assert "error" not in out, out
    assert out["model_version"].endswith("analytical")
    # Workload anchor must surface as a diagnostic.
    assert out["mu_pitcher_workload_anchored"] is True
    fa = out["friction_audit"]
    assert "analytical_pitcher_outs" in fa
    # Hit-rate diagnostic must be present (5 starts → window=5).
    assert "pitcher_hit_rate" in fa
    assert fa["pitcher_hit_rate"]["pitcher_hit_rate_window_used"] == "5"
