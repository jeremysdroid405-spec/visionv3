"""
Tests for the rate-blend feature flag (2026-04-29 promotion).

Behavior the test fixes in place:
  • Default mode = "100_0"  → wR=1.0, wM=0.0
  • Setting NBA_RATE_BLEND_MODE="60_40" must yield wR=0.6, wM=0.4
  • The legacy constants (_RATE_BLEND_RATE_LEGACY / _LEGACY) are
    permanently 0.6 / 0.4 — they are the revert-target values.
  • When the rate layer fires, mu_final_projection MUST equal
    mu_rate_projection in 100_0 mode (no μ_model contribution).
  • rate_model_blend_mode is stamped on every fired prop.
"""
import importlib
import sys
import pytest


def _reload_module():
    sys.modules.pop("services.scoring.adapters.nba_scoring", None)
    return importlib.import_module("services.scoring.adapters.nba_scoring")


def test_default_mode_is_100_0(monkeypatch):
    monkeypatch.delenv("NBA_RATE_BLEND_MODE", raising=False)
    mod = _reload_module()
    assert mod.NBAScoringAdapter._RATE_BLEND_MODE == "100_0"
    assert mod.NBAScoringAdapter._RATE_BLEND_RATE  == 1.0
    assert mod.NBAScoringAdapter._RATE_BLEND_MODEL == 0.0


def test_explicit_100_0(monkeypatch):
    monkeypatch.setenv("NBA_RATE_BLEND_MODE", "100_0")
    mod = _reload_module()
    assert mod.NBAScoringAdapter._RATE_BLEND_RATE  == 1.0
    assert mod.NBAScoringAdapter._RATE_BLEND_MODEL == 0.0


def test_revert_to_60_40(monkeypatch):
    monkeypatch.setenv("NBA_RATE_BLEND_MODE", "60_40")
    mod = _reload_module()
    assert mod.NBAScoringAdapter._RATE_BLEND_MODE  == "60_40"
    assert mod.NBAScoringAdapter._RATE_BLEND_RATE  == 0.6
    assert mod.NBAScoringAdapter._RATE_BLEND_MODEL == 0.4


def test_unknown_mode_falls_back_to_100_0(monkeypatch):
    monkeypatch.setenv("NBA_RATE_BLEND_MODE", "garbage_value")
    mod = _reload_module()
    # Unknown values default to 100_0 (the new production default).
    assert mod.NBAScoringAdapter._RATE_BLEND_RATE  == 1.0
    assert mod.NBAScoringAdapter._RATE_BLEND_MODEL == 0.0


def test_legacy_constants_are_pinned_at_60_40():
    """The _LEGACY constants are the revert target — they must NEVER move."""
    mod = _reload_module()
    assert mod.NBAScoringAdapter._RATE_BLEND_RATE_LEGACY  == 0.6
    assert mod.NBAScoringAdapter._RATE_BLEND_MODEL_LEGACY == 0.4


def test_rate_layer_blend_match_in_100_0_mode(monkeypatch):
    """When the layer fires in 100_0 mode, μ_final = μ_rate exactly."""
    monkeypatch.setenv("NBA_RATE_BLEND_MODE", "100_0")
    mod = _reload_module()
    a = mod.NBAScoringAdapter()
    # Build dense dummy logs so the rate layer is eligible. Last 3 games
    # have lower minutes than the 10-game window → triggers L3<L10 gate.
    logs = []
    for i in range(12):
        m = 18.0 if i < 3 else 30.0   # L3=18, L10=30 → L3<L10 fires
        logs.append({"date": f"2026-01-{12 - i:02d}", "season": 2025,
                     "min": m, "pts": 0.5 * m, "reb": 0.2 * m, "ast": 0.15 * m})
    pid = 99
    a._logs_by_id  = {pid: logs}
    a._logs_loaded = True

    prop = {"commence_time": "2027-01-01T00:00:00Z",
            "minutes_restriction_factor": None,
            "availability_status": "FULL_GO"}
    mu_in = 25.0   # contrived "high" model μ
    mu_out = a._maybe_apply_rate_model("PTS", pid, prop, mu_in)
    assert prop["rate_model_applied"] is True
    assert prop["rate_model_blend_mode"] == "100_0"
    assert prop["rate_model_blend_weights"] == {"rate": 1.0, "model": 0.0}
    # In 100_0 mode, μ_final == μ_rate exactly.
    assert mu_out == pytest.approx(prop["mu_rate_projection"], rel=0, abs=1e-6)
    # And μ_final ≠ μ_in (the layer must have actually moved μ).
    assert abs(mu_out - mu_in) > 0.5


def test_rate_layer_blend_match_in_60_40_mode(monkeypatch):
    """Revert path: μ_final = 0.6·μ_rate + 0.4·μ_in."""
    monkeypatch.setenv("NBA_RATE_BLEND_MODE", "60_40")
    mod = _reload_module()
    a = mod.NBAScoringAdapter()
    logs = []
    for i in range(12):
        m = 18.0 if i < 3 else 30.0
        logs.append({"date": f"2026-01-{12 - i:02d}", "season": 2025,
                     "min": m, "pts": 0.5 * m, "reb": 0.2 * m, "ast": 0.15 * m})
    pid = 99
    a._logs_by_id  = {pid: logs}
    a._logs_loaded = True

    prop = {"commence_time": "2027-01-01T00:00:00Z",
            "minutes_restriction_factor": None,
            "availability_status": "FULL_GO"}
    mu_in = 25.0
    mu_out = a._maybe_apply_rate_model("PRA", pid, prop, mu_in)
    expected = 0.6 * prop["mu_rate_projection"] + 0.4 * mu_in
    assert prop["rate_model_blend_mode"] == "60_40"
    assert mu_out == pytest.approx(expected, rel=0, abs=1e-4)
