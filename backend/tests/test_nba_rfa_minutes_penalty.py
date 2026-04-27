"""Tests for the RFA minutes penalty (2026-04-29 promotion).

Behavior the tests pin in place:
  • Default (env unset) → penalty DISABLED (factor = 1.0, no μ change)
  • NBA_RFA_MINUTES_PENALTY=0.85 → applied ONLY when status == RFA
  • FULL_GO, MINUTES_RESTRICTION, MINUTES_VOLATILITY, DNP_RISK NEVER
    receive the penalty.
  • Penalty multiplies expected_minutes AFTER the restriction_factor.
  • μ_final reflects the penalized expected_minutes when applied.
  • Audit stamps populated:
      rfa_minutes_penalty_applied, rfa_minutes_penalty_factor,
      expected_minutes_before_rfa_penalty,
      expected_minutes_after_rfa_penalty
  • Rate-blend mode is unchanged (still 100/0 by default).
"""
import importlib
import sys
import pytest


def _reload_module():
    sys.modules.pop("services.scoring.adapters.nba_scoring", None)
    return importlib.import_module("services.scoring.adapters.nba_scoring")


def _build_logs():
    """Dense logs that fire the rate-layer eligibility gate.
    L3 (last 3 games) avg ~18 min, L10 avg ~30 min → L3 < L10 ⇒ rate fires.
    """
    return [{
        "date": f"2026-01-{12 - i:02d}",
        "season": 2025,
        "min": 18.0 if i < 3 else 30.0,
        "pts": (18.0 if i < 3 else 30.0) * 0.5,
        "reb": (18.0 if i < 3 else 30.0) * 0.2,
        "ast": (18.0 if i < 3 else 30.0) * 0.15,
    } for i in range(12)]


def _build_adapter(mod, logs=None):
    a = mod.NBAScoringAdapter()
    a._logs_by_id  = {99: logs if logs is not None else _build_logs()}
    a._logs_loaded = True
    return a


# ---- Default (env unset): penalty is DISABLED ---------------------------

def test_default_factor_is_disabled(monkeypatch):
    monkeypatch.delenv("NBA_RFA_MINUTES_PENALTY", raising=False)
    mod = _reload_module()
    assert mod.NBAScoringAdapter._RFA_MINUTES_PENALTY == 1.0


def test_default_does_not_apply_to_rfa(monkeypatch):
    monkeypatch.delenv("NBA_RFA_MINUTES_PENALTY", raising=False)
    mod = _reload_module()
    a = _build_adapter(mod)
    prop = {"commence_time": "2027-01-01T00:00:00Z",
            "minutes_restriction_factor": None,
            "availability_status": "RETURNING_FROM_ABSENCE"}
    a._maybe_apply_rate_model("PTS", 99, prop, 25.0)
    assert prop["rfa_minutes_penalty_applied"] is False
    assert prop["rfa_minutes_penalty_factor"] == 1.0
    # before == after when penalty is disabled.
    assert (prop["expected_minutes_before_rfa_penalty"]
            == prop["expected_minutes_after_rfa_penalty"])


# ---- 0.85 promotion: penalty is APPLIED to RFA only --------------------

def test_85_factor_applied_to_rfa(monkeypatch):
    monkeypatch.setenv("NBA_RFA_MINUTES_PENALTY", "0.85")
    mod = _reload_module()
    a = _build_adapter(mod)
    prop = {"commence_time": "2027-01-01T00:00:00Z",
            "minutes_restriction_factor": None,
            "availability_status": "RETURNING_FROM_ABSENCE"}
    a._maybe_apply_rate_model("PTS", 99, prop, 25.0)
    assert prop["rfa_minutes_penalty_applied"] is True
    assert prop["rfa_minutes_penalty_factor"] == pytest.approx(0.85)
    em_before = prop["expected_minutes_before_rfa_penalty"]
    em_after  = prop["expected_minutes_after_rfa_penalty"]
    assert em_after == pytest.approx(em_before * 0.85, rel=1e-4)
    # `expected_minutes` (the actual one used in projection) reflects
    # the penalized value.
    assert prop["expected_minutes"] == pytest.approx(em_after, rel=1e-4)
    # μ_rate must reflect the penalized minutes.
    assert prop["mu_rate_projection"] < 25.0  # the rate × penalized minutes


@pytest.mark.parametrize("avail_status", [
    "FULL_GO",
    "MINUTES_RESTRICTION",
    "MINUTES_VOLATILITY",
    "DNP_RISK",
    "UNKNOWN",
    None,
])
def test_85_factor_does_not_apply_to_non_rfa(monkeypatch, avail_status):
    monkeypatch.setenv("NBA_RFA_MINUTES_PENALTY", "0.85")
    mod = _reload_module()
    a = _build_adapter(mod)
    prop = {"commence_time": "2027-01-01T00:00:00Z",
            "minutes_restriction_factor": None,
            "availability_status": avail_status}
    a._maybe_apply_rate_model("PTS", 99, prop, 25.0)
    if not prop.get("rate_model_applied"):
        # If the eligibility gate didn't fire (e.g. avail_status=FULL_GO
        # AND minutes are stable), there's nothing to assert here — but
        # in our test fixture L3<L10 always fires the gate.
        return
    assert prop["rfa_minutes_penalty_applied"] is False
    assert prop["rfa_minutes_penalty_factor"] == 1.0
    assert (prop["expected_minutes_before_rfa_penalty"]
            == prop["expected_minutes_after_rfa_penalty"])


# ---- Penalty is composed AFTER the restriction_factor ------------------

def test_penalty_composes_after_restriction_factor(monkeypatch):
    monkeypatch.setenv("NBA_RFA_MINUTES_PENALTY", "0.85")
    mod = _reload_module()
    a = _build_adapter(mod)
    prop = {"commence_time": "2027-01-01T00:00:00Z",
            "minutes_restriction_factor": 0.80,
            "availability_status": "RETURNING_FROM_ABSENCE"}
    a._maybe_apply_rate_model("PRA", 99, prop, 35.0)
    em_raw    = prop["expected_minutes_raw"]
    em_pre    = prop["expected_minutes_before_rfa_penalty"]
    em_after  = prop["expected_minutes_after_rfa_penalty"]
    # em_pre  = em_raw × 0.80 (restriction_factor)
    assert em_pre == pytest.approx(em_raw * 0.80, rel=1e-4)
    # em_after = em_pre × 0.85 (RFA penalty)
    assert em_after == pytest.approx(em_pre * 0.85, rel=1e-4)


# ---- 100/0 rate-blend remains the default after RFA promotion -----------

def test_rate_blend_mode_is_unaffected_by_rfa_flag(monkeypatch):
    monkeypatch.setenv("NBA_RFA_MINUTES_PENALTY", "0.85")
    monkeypatch.delenv("NBA_RATE_BLEND_MODE", raising=False)
    mod = _reload_module()
    assert mod.NBAScoringAdapter._RATE_BLEND_MODE  == "100_0"
    assert mod.NBAScoringAdapter._RATE_BLEND_RATE  == 1.0
    assert mod.NBAScoringAdapter._RATE_BLEND_MODEL == 0.0


# ---- Out-of-range values are clamped safely -----------------------------

def test_out_of_range_low_is_clamped(monkeypatch):
    monkeypatch.setenv("NBA_RFA_MINUTES_PENALTY", "0.10")
    mod = _reload_module()
    assert mod.NBAScoringAdapter._RFA_MINUTES_PENALTY == 0.50  # clamp floor


def test_out_of_range_high_is_clamped(monkeypatch):
    monkeypatch.setenv("NBA_RFA_MINUTES_PENALTY", "1.50")
    mod = _reload_module()
    assert mod.NBAScoringAdapter._RFA_MINUTES_PENALTY == 1.00  # clamp ceiling


def test_invalid_string_is_default_disabled(monkeypatch):
    monkeypatch.setenv("NBA_RFA_MINUTES_PENALTY", "not_a_number")
    mod = _reload_module()
    assert mod.NBAScoringAdapter._RFA_MINUTES_PENALTY == 1.0
