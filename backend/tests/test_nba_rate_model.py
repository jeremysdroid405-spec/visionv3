"""Unit tests for the NBA rate × minutes projection layer.

Covers:
  - rate computation from synthetic logs (per-minute math).
  - eligibility gate (FULL_GO + stable minutes ⇒ no-op).
  - PTS μ blend (100/0 production default; 60/40 path tested in
    test_nba_rate_blend_flag.py).
  - PRA μ blend (sum of 3 component rates).
  - Restriction factor multiplied into expected_minutes.
  - Audit fields stamped on the prop dict.
"""
import pytest
from services.scoring.adapters.nba_scoring import NBAScoringAdapter


def _logs(minutes_pts_reb_ast):
    """Build mock bdl_game_logs from a list of (date, min, pts, reb, ast)."""
    return [
        {"date": d, "min": m, "pts": p, "reb": r, "ast": a}
        for (d, m, p, r, a) in minutes_pts_reb_ast
    ]


def _make_adapter_with_logs(pid, logs):
    a = NBAScoringAdapter()
    a._logs_by_id = {pid: logs}
    a._logs_loaded = True
    return a


def test_compute_rate_components_basic():
    # Player playing 30 min/game scoring 30 pts, 6 reb, 6 ast.
    logs = _logs([
        ("2026-04-25", 30, 30, 6, 6),
        ("2026-04-23", 30, 30, 6, 6),
        ("2026-04-21", 30, 30, 6, 6),
        ("2026-04-19", 30, 30, 6, 6),
        ("2026-04-17", 30, 30, 6, 6),
        ("2026-04-15", 30, 30, 6, 6),
        ("2026-04-13", 30, 30, 6, 6),
        ("2026-04-11", 30, 30, 6, 6),
        ("2026-04-09", 30, 30, 6, 6),
        ("2026-04-07", 30, 30, 6, 6),
    ])
    comps = NBAScoringAdapter._compute_rate_components(logs)
    assert comps is not None
    assert comps["rate_pts_per_min"] == pytest.approx(1.0)
    assert comps["rate_reb_per_min"] == pytest.approx(0.2)
    assert comps["rate_ast_per_min"] == pytest.approx(0.2)
    assert comps["expected_minutes_raw"] == pytest.approx(30.0)


def test_compute_rate_components_skips_zero_min_games():
    logs = _logs([
        ("2026-04-25", 0, 0, 0, 0),     # DNP
        ("2026-04-23", 30, 30, 6, 6),
        ("2026-04-21", 30, 30, 6, 6),
        ("2026-04-19", 30, 30, 6, 6),
    ])
    comps = NBAScoringAdapter._compute_rate_components(logs)
    assert comps is not None
    assert comps["rate_pts_per_min"] == pytest.approx(1.0)


def test_rate_model_skipped_for_full_go_stable_minutes():
    """FULL_GO + L3 minutes ≥ L10 minutes ⇒ rate model should NOT fire."""
    logs = _logs([(f"2026-04-{20+i:02d}", 32, 25, 6, 5) for i in range(10)])
    a = _make_adapter_with_logs(123, logs)
    prop = {"availability_status": "FULL_GO", "minutes_restriction_factor": 1.0}
    out = a._maybe_apply_rate_model(
        stat_type="PTS", bdl_player_id=123, prop=prop, mu_model=22.0,
    )
    assert out == 22.0
    assert prop.get("rate_model_applied") is False


def test_rate_model_fires_on_l3_below_l10():
    # L3 minutes ~22, L10 minutes ~32 → eligibility triggers via L3<L10.
    logs = _logs([
        ("2026-04-25", 22, 18, 4, 3),
        ("2026-04-23", 22, 20, 5, 4),
        ("2026-04-21", 22, 22, 4, 5),
        ("2026-04-19", 32, 30, 6, 5),
        ("2026-04-17", 32, 30, 6, 5),
        ("2026-04-15", 32, 30, 6, 5),
        ("2026-04-13", 32, 30, 6, 5),
        ("2026-04-11", 32, 30, 6, 5),
        ("2026-04-09", 32, 30, 6, 5),
        ("2026-04-07", 32, 30, 6, 5),
    ])
    a = _make_adapter_with_logs(456, logs)
    prop = {"availability_status": "FULL_GO", "minutes_restriction_factor": 1.0}
    mu_model = 30.0
    out = a._maybe_apply_rate_model("PTS", 456, prop, mu_model)
    assert out is not None
    assert out != mu_model  # rate-blend changed it
    assert prop["rate_model_applied"] is True
    assert prop["rate_pts_per_min"] is not None
    # Expected minutes < L10 average because L3 dropped
    assert prop["expected_minutes_raw"] < 32.0
    # Final (100/0 production default) = μ_rate exactly.
    assert out == pytest.approx(prop["mu_rate_projection"], rel=1e-4)


def test_rate_model_pra_sums_components():
    logs = _logs([
        ("2026-04-25", 30, 24, 6, 6),
        ("2026-04-23", 30, 24, 6, 6),
        ("2026-04-21", 30, 24, 6, 6),
        ("2026-04-19", 30, 24, 6, 6),
        ("2026-04-17", 30, 24, 6, 6),
    ])
    # Force eligibility via guard fired non-trivially.
    a = _make_adapter_with_logs(789, logs)
    prop = {"availability_status": "RETURNING_FROM_ABSENCE",
            "minutes_restriction_factor": 0.9}
    mu_model = 36.0
    out = a._maybe_apply_rate_model("PRA", 789, prop, mu_model)
    assert prop["rate_model_applied"] is True
    # rate_total = 0.8 + 0.2 + 0.2 = 1.2 per min
    # exp_min = 30 × 0.9 = 27
    # μ_rate ≈ 1.2 × 27 = 32.4
    # final (100/0 prod default) = μ_rate = 32.4
    assert prop["expected_minutes"] == pytest.approx(27.0)
    assert prop["mu_rate_projection"] == pytest.approx(32.4, rel=1e-3)
    assert out == pytest.approx(32.4, rel=1e-3)


def test_rate_model_skipped_for_reb_and_ast():
    """REB / AST props use existing μ_model; rate layer is PTS/PRA only."""
    logs = _logs([(f"2026-04-{20+i:02d}", 22, 20, 4, 4) for i in range(5)])
    a = _make_adapter_with_logs(999, logs)
    prop = {"availability_status": "MINUTES_RESTRICTION",
            "minutes_restriction_factor": 0.7}
    out = a._maybe_apply_rate_model("REB", 999, prop, 6.0)
    assert out == 6.0
    assert prop["rate_model_applied"] is False


def test_rate_model_skipped_when_logs_missing():
    a = _make_adapter_with_logs(111, [])
    prop = {"availability_status": "DNP_RISK"}
    out = a._maybe_apply_rate_model("PTS", 111, prop, 25.0)
    assert out == 25.0
    assert prop["rate_model_applied"] is False


def test_rate_model_clamps_restriction_factor():
    """Out-of-range restriction factors must be clamped to [0.50, 1.00]."""
    logs = _logs([(f"2026-04-{20+i:02d}", 10, 10, 2, 2) for i in range(5)])
    a = _make_adapter_with_logs(222, logs)
    # Caller passes an absurd 0.10 — guard should clamp it to 0.50.
    prop = {"availability_status": "DNP_RISK", "minutes_restriction_factor": 0.10}
    a._maybe_apply_rate_model("PTS", 222, prop, 8.0)
    assert prop["expected_minutes"] == pytest.approx(prop["expected_minutes_raw"] * 0.50)
