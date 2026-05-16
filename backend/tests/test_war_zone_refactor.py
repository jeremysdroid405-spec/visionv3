"""Tests for the NBA War Zone gate refactor (2026-04-29).

Spec:
- WZ uses ONLY the universal gate types: coverage / direction / hit_rate
  / cv / vision_score.
- REMOVED: market_trap_gate, tp_source vision branches, stat-family CV caps.
- OVER rules:
    direction        : projection >= line × 1.05
    hit_rate         : HR >= 55
    cv               : CV <= 0.75
    HR-expansion     : HR > 70 → CV <= 1.00 (rescue layer)
    vision_score     : v1 vision_score >= 60 (v2 stays shadow-only)
- SH / FL / UNDER side / non-NBA configs unchanged.
"""
from __future__ import annotations

from services.scoring.gates.engine import get_engine
from services.scoring.gates.schema import NormalizedMetrics
from services.scoring.gates.thresholds import (
    _NBA_WAR_ZONE_BASE,
    _NBA_FRONT_LINES_BASE,
    _NBA_SAFE_HAVEN_BASE,
)


def _wz(**overrides):
    base = dict(
        sport="nba", tier="war_zone",
        stat_family="pts", side="OVER",
        reference_book="dk", reference_odds=+216, book_count=3,
        tp=31.6, hit_rate=55.0, hit_rate_l20=55.0, cv=0.726,
        edge_pct=27.7, vision_score=92.3, line=7.5,
        extras={"projection": 8.86},
    )
    base.update(overrides)
    return NormalizedMetrics(**base)


# ─── WZ config shape ──────────────────────────────────────────
def test_wz_no_market_trap_gate():
    assert "market_trap_gate" not in _NBA_WAR_ZONE_BASE


def test_wz_no_tp_source_branching_in_vision():
    vs = _NBA_WAR_ZONE_BASE.get("vision_score_gate") or {}
    assert "by_tp_source" not in vs


def test_wz_no_stat_family_cv_caps():
    cv = _NBA_WAR_ZONE_BASE.get("cv_gate") or {}
    assert "caps" not in cv
    # flat scalar
    assert cv.get("max") == 0.75


def test_wz_uses_v1_vision_not_v2():
    vs = _NBA_WAR_ZONE_BASE.get("vision_score_gate") or {}
    # use_v2 stays out of the live config; v2 is shadow-only
    assert vs.get("use_v2") is None or vs.get("use_v2") is False


def test_wz_gate_keys_subset_of_universal_set():
    universal = {"coverage_gate", "direction_gate", "hit_rate_gate",
                 "cv_gate", "vision_score_gate", "edge_gate",
                 "tp_gate", "ceiling_gate", "context_gate",
                 "margin_gate", "market_structure_gate",
                 # accepted sentinels
                 "__safe_haven_overrides__",
                 "__front_lines_over_overrides__",
                 "__war_zone_overrides__"}
    for k in _NBA_WAR_ZONE_BASE.keys():
        assert k in universal, k


# ─── WZ rules — pass cases ────────────────────────────────────
def test_jaxson_hayes_pts_7_5_passes():
    """Spec validation pick — proj 8.86 / line 7.5 (ratio 1.18),
    HR 55, CV 0.726, vis_v1 92.3."""
    m = _wz(stat_family="pts", side="OVER", reference_odds=+216,
            line=7.5, hit_rate=55.0, hit_rate_l20=55.0, cv=0.726,
            vision_score=92.3, edge_pct=27.7,
            extras={"projection": 8.86})
    res = get_engine().evaluate(m)
    assert res.passed, f"failed={res.failed_gates}"


def test_pts_pick_passes_at_min_thresholds():
    m = _wz(line=10.0, extras={"projection": 10.5},  # ratio 1.05
            hit_rate=55.0, hit_rate_l20=55.0, cv=0.75, vision_score=60.0)
    res = get_engine().evaluate(m)
    assert res.passed


# ─── WZ rules — direction (strict OVER, 2026-05-15 refactor) ──
# Post 2026-05-15 the direction gate is pure side-lean:
#   OVER passes iff projection > line. No ratio / cushion logic.
# Config still carries a `min_projection_to_line_ratio` for
# backwards-compat — the engine ignores it.
def test_wz_fails_when_proj_strictly_at_line():
    """Strict semantics: proj == line fails direction (no side-lean)."""
    m = _wz(stat_family="ast", line=2.5, extras={"projection": 2.5},
            hit_rate=60.0, hit_rate_l20=60.0, cv=0.685, vision_score=92.0)
    res = get_engine().evaluate(m)
    assert not res.passed
    assert "direction_gate" in res.failed_gates


def test_wz_passes_at_exactly_1_05x():
    m = _wz(line=10.0, extras={"projection": 10.5},
            hit_rate=55.0, hit_rate_l20=55.0, cv=0.50, vision_score=80.0)
    res = get_engine().evaluate(m)
    assert res.passed


def test_wz_passes_with_tiny_positive_margin():
    """Post-refactor: any proj > line passes direction. Confidence
    is enforced by CV / HR / vision / edge gates — not direction.
    """
    m = _wz(line=10.0, extras={"projection": 10.49},
            hit_rate=55.0, hit_rate_l20=55.0, cv=0.50, vision_score=80.0)
    res = get_engine().evaluate(m)
    assert res.gate_details["direction_gate"].passed


# ─── WZ rules — HR / CV ───────────────────────────────────────
# (HR floor lowered 55 → 50 on 2026-05-09; CV-ladder rescue refined.)
def test_wz_fails_hr_below_50():
    m = _wz(hit_rate=49.0, hit_rate_l20=49.0)
    res = get_engine().evaluate(m)
    assert not res.passed
    assert "hit_rate_gate" in res.failed_gates


def test_wz_passes_hr_at_50():
    m = _wz(hit_rate=50.0, hit_rate_l20=50.0)
    res = get_engine().evaluate(m)
    assert "hit_rate_gate" not in res.failed_gates


def test_wz_fails_cv_above_0_75_when_hr_below_70():
    """Below the HR≥70 ladder threshold, the flat CV cap (0.75) applies."""
    m = _wz(hit_rate=65.0, hit_rate_l20=65.0, cv=0.80)
    res = get_engine().evaluate(m)
    assert not res.passed
    assert "cv_gate" in res.failed_gates


def test_wz_hr_expansion_rescues_high_cv_when_hr_above_70():
    """HR≥70 + edge>0 → CV cap relaxes to 1.15 (2026-05-09 ladder)."""
    m = _wz(hit_rate=80.0, hit_rate_l20=80.0, cv=0.95)
    res = get_engine().evaluate(m)
    assert res.passed


def test_wz_hr_expansion_does_not_rescue_above_ladder_ceiling():
    """CV beyond the highest ladder cap (1.50 at HR≥80 + edge≥5) fails."""
    m = _wz(hit_rate=80.0, hit_rate_l20=80.0, cv=1.60)
    res = get_engine().evaluate(m)
    assert not res.passed


def test_wz_hr_expansion_does_not_rescue_below_70():
    """The HR-ladder rescue tier requires HR ≥ 70. At HR=69 the
    rescue does not apply and the flat 0.75 cap binds.
    """
    m = _wz(hit_rate=69.0, hit_rate_l20=69.0, cv=0.85)
    res = get_engine().evaluate(m)
    assert not res.passed


# ─── WZ rules — vision (v1, ≥ 60) ─────────────────────────────
def test_wz_fails_v1_vision_below_60():
    m = _wz(vision_score=59.0)
    res = get_engine().evaluate(m)
    assert not res.passed
    assert "vision_score_gate" in res.failed_gates


def test_wz_passes_v1_vision_exactly_60():
    m = _wz(vision_score=60.0)
    res = get_engine().evaluate(m)
    assert res.passed


def test_wz_does_not_consult_vision_v2():
    """v2 stays shadow-only; pass with high v1 + low v2."""
    m = _wz(vision_score=92.0,
            extras={"projection": 8.86, "vision_score_v2": 12.0})
    res = get_engine().evaluate(m)
    assert res.passed


# ─── WZ overrides scope ───────────────────────────────────────
def test_wz_override_does_not_rescue_non_cv_failures():
    """HR-expansion rule must NEVER bypass direction / vision /
    hit_rate / coverage failures."""
    # direction fails (ratio 1.0):
    m = _wz(line=10.0, extras={"projection": 10.0},
            hit_rate=80.0, hit_rate_l20=80.0, cv=0.85)
    res = get_engine().evaluate(m)
    assert not res.passed


# ─── SH / FL configs (current production reality) ─────────────
def test_sh_config_vision_score_floor():
    """SH vision floor lowered 85 → 80 on 2026-05-02 post Phase 1 Debias."""
    vs = _NBA_SAFE_HAVEN_BASE.get("vision_score_gate") or {}
    assert vs.get("min") == 80.0
    assert vs.get("use_v2") is None or vs.get("use_v2") is False


def test_sh_config_still_uses_stat_family_cv_caps():
    cv = _NBA_SAFE_HAVEN_BASE.get("cv_gate") or {}
    assert "caps" in cv


def test_sh_config_carries_universal_direction_gate():
    """Universal OVER-side rule applied to SH on 2026-04-29.
    Post 2026-05-15 the engine uses strict-inequality semantics
    (`projection > line` only); the 2026-05-17 cleanup removed the
    legacy `min_projection_minus_line` cushion key from the config.
    """
    dg = _NBA_SAFE_HAVEN_BASE.get("direction_gate") or {}
    assert dg.get("applies_to_sides") == ["OVER"]
    # Cushion keys must be absent in the cleaned config.
    assert "min_projection_minus_line" not in dg


def test_fl_config_direction_is_strict_sign_only():
    """FL OVER-side direction gate is sign-only (post-2026-05-17
    cleanup). The strict engine consults `applies_to_sides` and the
    sign of `projection - line` only — no cushion key in config.
    """
    dg = _NBA_FRONT_LINES_BASE.get("direction_gate") or {}
    assert dg.get("applies_to_sides") == ["OVER"]
    assert "min_projection_minus_line" not in dg
    # FL has no ratio rule
    assert "min_projection_to_line_ratio" not in dg


def test_fl_config_still_has_tp_under_floor():
    tp = _NBA_FRONT_LINES_BASE.get("tp_gate") or {}
    assert tp.get("min") == 50.0
    assert tp.get("under_floor") == 65.0


def test_fl_config_still_has_fl_over_overrides_block():
    assert "__front_lines_over_overrides__" in _NBA_FRONT_LINES_BASE


# ─── UNDER side: WZ direction skipped ─────────────────────────
def test_wz_under_side_skips_direction_gate():
    """UNDER picks (rare in WZ band) must auto-pass direction since
    `applies_to_sides=['OVER']` per WZ config."""
    m = _wz(side="UNDER", line=10.0, extras={"projection": 8.0},
            hit_rate=80.0, hit_rate_l20=80.0,
            tp=70.0, p_model_pct=70.0,
            cv=0.50, vision_score=80.0, edge_pct=10.0)
    res = get_engine().evaluate(m)
    assert "direction_gate" not in res.failed_gates
