"""
Contract test for _extract_live_outputs() — the single normalisation
boundary between live MLBHighFrictionModel.predict() output keys
(`predicted` / `std_dev` / `prob_over` 0-100) and the historical
replay scorer's expected schema (`projection_mu` / `sigma` /
`model_probability` 0-1).

This was the silent-failure that made 1736/2020 SGO feature rows fail
with "predict ok but mu/sigma/model_p incomplete" on 2026-05-23 —
the scorer was reading legacy field names that the live model never
emits. The test pins the contract so any future drift fails fast.
"""
from __future__ import annotations
import importlib.util
import os
import sys

# Load the module directly so we don't trigger its dotenv side-effects
# on import. (The script does sys.path mangling + load_dotenv at import.)
sys.path.insert(0, "/app/backend")
spec = importlib.util.spec_from_file_location(
    "score_historical_with_live_mlb_hf",
    "/app/backend/scripts/sgo/score_historical_with_live_mlb_hf.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)  # type: ignore[union-attr]
_extract_live_outputs = mod._extract_live_outputs


# ── Live-shaped happy path: OVER side, sane μ/σ/prob ────────────────
def test_extract_live_outputs_over_happy_path():
    result = {
        "predicted": 1.85, "std_dev": 0.42, "prob_over": 62.5,
        "model_version": "MLB_HF_v2", "line": 1.5,
    }
    mu, sigma, model_p, missing = _extract_live_outputs(result, side="OVER")
    assert missing == []
    assert abs(mu    - 1.85)   < 1e-9
    assert abs(sigma - 0.42)   < 1e-9
    assert abs(model_p - 0.625) < 1e-9


# ── UNDER side flips probability ────────────────────────────────────
def test_extract_live_outputs_under_flips_prob():
    result = {"predicted": 2.5, "std_dev": 0.5, "prob_over": 70.0}
    _, _, model_p, missing = _extract_live_outputs(result, side="UNDER")
    assert missing == []
    assert abs(model_p - 0.30) < 1e-9  # 1 - 0.70


# ── Missing μ ────────────────────────────────────────────────────────
def test_extract_live_outputs_missing_predicted():
    result = {"std_dev": 0.3, "prob_over": 50.0}
    mu, sigma, model_p, missing = _extract_live_outputs(result, side="OVER")
    assert mu is None
    assert "predicted" in missing
    assert sigma == 0.3
    assert abs(model_p - 0.5) < 1e-9


# ── Missing σ ────────────────────────────────────────────────────────
def test_extract_live_outputs_missing_std_dev():
    result = {"predicted": 1.0, "prob_over": 50.0}
    mu, sigma, model_p, missing = _extract_live_outputs(result, side="OVER")
    assert sigma is None
    assert "std_dev" in missing


# ── σ = 0 is treated as missing (CV blows up downstream) ────────────
def test_extract_live_outputs_zero_sigma_is_missing():
    result = {"predicted": 1.0, "std_dev": 0.0, "prob_over": 50.0}
    mu, sigma, model_p, missing = _extract_live_outputs(result, side="OVER")
    assert sigma is None
    assert any("std_dev" in f for f in missing)


# ── Missing prob_over ────────────────────────────────────────────────
def test_extract_live_outputs_missing_prob_over():
    result = {"predicted": 1.0, "std_dev": 0.4}
    mu, sigma, model_p, missing = _extract_live_outputs(result, side="OVER")
    assert model_p is None
    assert "prob_over" in missing


# ── The OLD historical field names alone are NOT enough ─────────────
def test_extract_live_outputs_rejects_old_schema():
    """If only the legacy `projection_mu / sigma / model_probability`
    keys are present, the extractor must refuse — that's the silent
    failure mode the live scorer hit in production on 2026-05-23."""
    result = {"projection_mu": 1.8, "sigma": 0.5, "model_probability": 0.6}
    mu, sigma, model_p, missing = _extract_live_outputs(result, side="OVER")
    assert mu is None and sigma is None and model_p is None
    assert {"predicted", "std_dev", "prob_over"}.issubset(set(missing))


# ── Clamping out-of-range prob_over ──────────────────────────────────
def test_extract_live_outputs_clamps_extreme_prob():
    over_result   = {"predicted": 1.0, "std_dev": 0.5, "prob_over": 105.5}
    under_result  = {"predicted": 1.0, "std_dev": 0.5, "prob_over": -3.0}
    _, _, mp1, _ = _extract_live_outputs(over_result,  side="OVER")
    _, _, mp2, _ = _extract_live_outputs(under_result, side="OVER")
    assert mp1 == 1.0
    assert mp2 == 0.0


# ── Non-numeric values are reported, never silently coerced ──────────
def test_extract_live_outputs_non_numeric_payload():
    result = {"predicted": "n/a", "std_dev": 0.5, "prob_over": 50.0}
    mu, sigma, model_p, missing = _extract_live_outputs(result, side="OVER")
    assert mu is None
    assert any("non-numeric" in f for f in missing)


# ── _FAMILY_TO_HF_STAT covers every replay stat_family used in MLB ──
def test_family_to_hf_stat_covers_observed_families():
    """Sanity check on the alias table. If a new stat_family lands in
    the SGO feature builder, this test fails until we map it."""
    expected = {
        "hits", "home_runs", "rbis", "runs", "total_bases", "doubles",
        "singles", "batter_strikeouts", "pitcher_strikeouts",
        "earned_runs", "hits_allowed", "walks_allowed", "pitching_outs",
        "pitcher_hits_allowed", "pitching_basesOnBalls", "stolen_bases",
        "batting_strikeouts", "batting_walks", "hits_runs_rbis",
    }
    assert expected.issubset(set(mod._FAMILY_TO_HF_STAT.keys()))
