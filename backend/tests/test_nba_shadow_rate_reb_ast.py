"""
Tests for `_maybe_apply_shadow_rate_reb_ast` (2026-04-29).

Shadow rate × minutes layer for REB / AST. Must:
  • NEVER replace μ_current — return value identical to input.
  • Stamp shadow fields ONLY for REB and AST stats.
  • Handle missing logs / missing rates / non-numeric pids gracefully.
  • Use restriction_factor from the availability guard, clamped 0.50-1.00.
"""
import pytest

from services.scoring.adapters.nba_scoring import NBAScoringAdapter


def _build_logs(n=12, minutes=30.0, reb=8.0, ast=5.0, pts=20.0):
    """Dense log generator — every game played, simple constant stats."""
    return [{
        "date": f"2026-01-{n - i:02d}",
        "season": 2025,
        "min": minutes,
        "pts": pts, "reb": reb, "ast": ast, "fg3m": 2,
    } for i in range(n)]


@pytest.fixture
def adapter_with_logs():
    a = NBAScoringAdapter()
    a._logs_by_id = {42: _build_logs()}
    a._logs_loaded = True
    return a


def _prop():
    return {
        "commence_time": "2027-01-01T00:00:00Z",
        "minutes_restriction_factor": None,
        "availability_status": "FULL_GO",
    }


# ---- Returns μ_current unchanged in every case --------------------------

@pytest.mark.parametrize("stat", ["REB", "AST", "PTS", "PRA", "STL", "3PM", ""])
def test_returns_mu_current_unchanged(adapter_with_logs, stat):
    p = _prop()
    out = adapter_with_logs._maybe_apply_shadow_rate_reb_ast(stat, 42, p, 7.0)
    assert out == 7.0


# ---- Stamps shadow fields only for REB / AST ----------------------------

def test_stamps_reb_shadow_fields(adapter_with_logs):
    p = _prop()
    adapter_with_logs._maybe_apply_shadow_rate_reb_ast("REB", 42, p, 7.0)
    assert p["mu_rate_reb_shadow_applied"] is False
    assert p["mu_rate_reb_shadow"] is not None
    assert "delta_mu_rate_reb_shadow_vs_current" in p
    assert "rate_reb_per_min_shadow" in p
    assert "expected_minutes_shadow" in p
    # Sanity: μ_shadow ≈ rate × exp_min
    assert abs(
        p["mu_rate_reb_shadow"]
        - p["rate_reb_per_min_shadow"] * p["expected_minutes_shadow"]
    ) < 0.01
    # No AST stamps when stat is REB.
    assert "mu_rate_ast_shadow" not in p


def test_stamps_ast_shadow_fields(adapter_with_logs):
    p = _prop()
    adapter_with_logs._maybe_apply_shadow_rate_reb_ast("AST", 42, p, 5.0)
    assert p["mu_rate_ast_shadow_applied"] is False
    assert p["mu_rate_ast_shadow"] is not None
    assert "rate_ast_per_min_shadow" in p
    # No REB stamps when stat is AST.
    assert "mu_rate_reb_shadow" not in p


def test_no_stamps_for_pts(adapter_with_logs):
    p = _prop()
    adapter_with_logs._maybe_apply_shadow_rate_reb_ast("PTS", 42, p, 25.0)
    # Nothing related to the REB/AST shadow should be on the prop.
    assert not any("rate_reb_shadow" in k or "rate_ast_shadow" in k for k in p)


# ---- Eligibility guards -------------------------------------------------

def test_no_logs_returns_unchanged():
    a = NBAScoringAdapter()
    a._logs_by_id = {}
    a._logs_loaded = True
    p = _prop()
    out = a._maybe_apply_shadow_rate_reb_ast("REB", 42, p, 7.0)
    assert out == 7.0
    # Default applied flag stamped, no further stamps.
    assert p["mu_rate_reb_shadow_applied"] is False
    assert "mu_rate_reb_shadow" not in p


def test_invalid_pid_returns_unchanged(adapter_with_logs):
    p = _prop()
    out = adapter_with_logs._maybe_apply_shadow_rate_reb_ast(
        "REB", "not-an-int", p, 7.0,
    )
    assert out == 7.0
    assert "mu_rate_reb_shadow" not in p


def test_none_mu_returns_unchanged(adapter_with_logs):
    p = _prop()
    out = adapter_with_logs._maybe_apply_shadow_rate_reb_ast(
        "REB", 42, p, None,
    )
    assert out is None
    assert "mu_rate_reb_shadow" not in p


# ---- Restriction factor clamping ---------------------------------------

def test_restriction_factor_applied(adapter_with_logs):
    p = _prop(); p["minutes_restriction_factor"] = 0.70
    adapter_with_logs._maybe_apply_shadow_rate_reb_ast("REB", 42, p, 7.0)
    em = p["expected_minutes_shadow"]
    # exp_min should be ~30 × 0.70 = 21
    assert 19.0 < em < 23.0


def test_restriction_factor_clamped_low(adapter_with_logs):
    p = _prop(); p["minutes_restriction_factor"] = 0.10
    adapter_with_logs._maybe_apply_shadow_rate_reb_ast("REB", 42, p, 7.0)
    em = p["expected_minutes_shadow"]
    # 30 × 0.50 (lower clamp) = 15
    assert 13.0 < em < 17.0


def test_restriction_factor_clamped_high(adapter_with_logs):
    p = _prop(); p["minutes_restriction_factor"] = 1.50
    adapter_with_logs._maybe_apply_shadow_rate_reb_ast("REB", 42, p, 7.0)
    em = p["expected_minutes_shadow"]
    # 30 × 1.00 (upper clamp) = 30
    assert 28.0 < em < 32.0


# ---- Delta stamping correctness ----------------------------------------

def test_delta_is_signed(adapter_with_logs):
    p = _prop()
    mu_curr = 12.0  # well above the rate × minutes shadow
    adapter_with_logs._maybe_apply_shadow_rate_reb_ast("REB", 42, p, mu_curr)
    delta = p["delta_mu_rate_reb_shadow_vs_current"]
    assert delta == round(p["mu_rate_reb_shadow"] - mu_curr, 4)
    assert delta < 0  # shadow < current
