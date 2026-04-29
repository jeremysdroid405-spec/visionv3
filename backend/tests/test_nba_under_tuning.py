"""NBA UNDER tuning (2026-04-29).

Spec: unified UNDER ruleset across SH / FL / WZ. OVER side untouched.

  Direction        : projection < line REQUIRED
  Hit rate         : HR >= 65
  CV               : stat-family caps + HR-conditional relax
                       HR >= 75 → cap += 0.10
                       HR >= 80 → CV is no longer a hard fail
  Critical filter  : (line - projection) / line >= 0.15
"""
from __future__ import annotations

from services.scoring.gates.engine import get_engine
from services.scoring.gates.schema import NormalizedMetrics
from services.scoring.gates.thresholds import (
    resolve_thresholds, _NBA_UNDER_CV_CAPS,
    _NBA_FRONT_LINES_BASE, _NBA_SAFE_HAVEN_BASE, _NBA_WAR_ZONE_BASE,
)


def _u(**ov):
    base = dict(
        sport="nba", tier="front_lines",
        stat_family="threes", side="UNDER",
        reference_book="dk", reference_odds=-119,
        book_count=3, tp=68.0, hit_rate=85.0, hit_rate_l20=85.0,
        p_model_pct=70.0,  # satisfies FL UNDER tp_gate.under_floor=65
        cv=0.50, edge_pct=15.0, vision_score=80.0, line=1.5,
        extras={"projection": 1.0},  # gap 0.333
    )
    base.update(ov)
    return NormalizedMetrics(**base)


# ─── side-aware resolver routes to UNDER config ───────────────
def test_under_routes_to_default_under_for_each_tier():
    for tier in ("safe_haven", "front_lines", "war_zone"):
        cfg = resolve_thresholds("nba", tier, "threes", side="UNDER")
        # UNDER block always carries direction_gate scoped to UNDER.
        dg = cfg.get("direction_gate")
        assert dg is not None
        assert "UNDER" in dg.get("applies_to_sides", [])
        # HR floor is the unified 65.
        assert (cfg.get("hit_rate_gate") or {}).get("min") == 65.0


def test_over_unchanged_for_each_tier():
    """OVER-side resolution returns the existing `_default` block —
    no leakage from UNDER changes."""
    for tier, expected_cfg in (
        ("safe_haven",  _NBA_SAFE_HAVEN_BASE),
        ("front_lines", _NBA_FRONT_LINES_BASE),
        ("war_zone",    _NBA_WAR_ZONE_BASE),
    ):
        cfg = resolve_thresholds("nba", tier, "threes", side="OVER")
        assert cfg is expected_cfg


def test_unspecified_side_resolves_to_default():
    """Existing call sites that don't pass `side` keep their
    OVER-`_default` behaviour."""
    cfg = resolve_thresholds("nba", "front_lines", "threes")
    assert cfg is _NBA_FRONT_LINES_BASE


# ─── direction (proj < line, gap >= 0.15) ─────────────────────
def test_under_direction_passes_when_gap_at_least_15pct():
    m = _u(line=1.5, extras={"projection": 1.275})  # gap 0.15 exactly
    res = get_engine().evaluate(m)
    assert "direction_gate" in res.gate_details
    assert res.gate_details["direction_gate"].passed


def test_under_direction_fails_just_below_15pct_gap():
    """LeBron-style: line 1.5, proj 1.28, gap (1.5-1.28)/1.5 = 0.146."""
    m = _u(line=1.5, extras={"projection": 1.28})
    res = get_engine().evaluate(m)
    assert "direction_gate" in res.failed_gates


def test_under_direction_fails_when_proj_above_line():
    m = _u(line=1.5, extras={"projection": 1.6})
    res = get_engine().evaluate(m)
    assert "direction_gate" in res.failed_gates


# ─── HR floor 65 — universal across UNDER tiers ───────────────
def test_under_fails_hr_below_65_safe_haven():
    m = _u(tier="safe_haven", reference_odds=-400, hit_rate=64.0,
           hit_rate_l20=64.0, vision_score=90.0, cv=0.30)
    res = get_engine().evaluate(m)
    assert "hit_rate_gate" in res.failed_gates


def test_under_fails_hr_below_65_front_lines():
    m = _u(tier="front_lines", reference_odds=-150, hit_rate=64.0,
           hit_rate_l20=64.0)
    res = get_engine().evaluate(m)
    assert "hit_rate_gate" in res.failed_gates


def test_under_fails_hr_below_65_war_zone():
    m = _u(tier="war_zone", reference_odds=200, hit_rate=64.0,
           hit_rate_l20=64.0, vision_score=90.0)
    res = get_engine().evaluate(m)
    assert "hit_rate_gate" in res.failed_gates


# ─── CV caps (canonical map) ──────────────────────────────────
def test_under_cv_threes_default_cap_is_0_55():
    """At HR < 75 (no relax) — 3PM cap 0.55."""
    m = _u(stat_family="threes", hit_rate=70.0, hit_rate_l20=70.0,
           cv=0.60)  # > 0.55
    res = get_engine().evaluate(m)
    assert "cv_gate" in res.failed_gates


def test_under_cv_pts_cap_is_0_40():
    m = _u(stat_family="pts", hit_rate=70.0, hit_rate_l20=70.0,
           cv=0.45, line=14.5, extras={"projection": 10.0})
    res = get_engine().evaluate(m)
    assert "cv_gate" in res.failed_gates


# ─── HR-conditional CV relax (≥75 cap+0.10 / ≥80 disable) ─────
def test_under_cv_relaxed_by_0_10_at_hr_75():
    """McDaniels 3PM-style: HR 85, CV 1.427 — HR>=80 disables CV."""
    m = _u(stat_family="threes", hit_rate=85.0, hit_rate_l20=85.0,
           cv=1.427, line=1.5, extras={"projection": 1.07})
    res = get_engine().evaluate(m)
    assert res.passed, f"failed={res.failed_gates}"


def test_under_cv_relax_at_hr_75_to_80_uses_plus_0_10():
    """At HR exactly 75 (>=75 not >=80), the cap += 0.10 applies."""
    # 3PM cap 0.55 → relaxed to 0.65 at HR 75
    m_pass = _u(stat_family="threes", hit_rate=75.0, hit_rate_l20=75.0,
                cv=0.65)
    res_pass = get_engine().evaluate(m_pass)
    assert "cv_gate" not in res_pass.failed_gates
    # CV 0.66 fails the relaxed cap 0.65
    m_fail = _u(stat_family="threes", hit_rate=75.0, hit_rate_l20=75.0,
                cv=0.66)
    res_fail = get_engine().evaluate(m_fail)
    assert "cv_gate" in res_fail.failed_gates


def test_under_cv_disabled_at_hr_80():
    """Anunoby BLK-style: HR 85, CV 1.042 — way over any cap; HR>=80
    disables the CV gate entirely."""
    m = _u(stat_family="blk", hit_rate=85.0, hit_rate_l20=85.0,
           cv=1.042, line=1.5, extras={"projection": 0.4})  # gap 0.73
    res = get_engine().evaluate(m)
    assert "cv_gate" not in res.failed_gates


def test_under_cv_relax_does_not_apply_at_hr_below_75():
    m = _u(stat_family="threes", hit_rate=74.0, hit_rate_l20=74.0,
           cv=0.60)  # 3PM base cap 0.55, HR < 75 → no relax
    res = get_engine().evaluate(m)
    assert "cv_gate" in res.failed_gates


# ─── Spec validation picks ────────────────────────────────────
def test_mcdaniels_3pm_under_passes():
    """L=1.5, proj=1.07, HR=85, CV=1.427.
       gap=(1.5-1.07)/1.5 = 0.287 ≥ 0.15 ✓
       HR>=80 → CV disabled ✓"""
    m = _u(stat_family="threes", line=1.5, hit_rate=85.0,
           hit_rate_l20=85.0, cv=1.427, vision_score=85.0,
           reference_odds=-119, extras={"projection": 1.07},
           edge_pct=32.3, tp=52.7)
    res = get_engine().evaluate(m)
    assert res.passed, f"failed={res.failed_gates}"


def test_anunoby_blk_under_passes():
    """L=1.5, HR=85, CV=1.042. Need gap >= 0.15 — pick a low proj."""
    m = _u(stat_family="blk", line=1.5, hit_rate=85.0,
           hit_rate_l20=85.0, cv=1.042, vision_score=85.0,
           reference_odds=-125, extras={"projection": 0.4},  # gap 0.73
           edge_pct=33.0, tp=52.0)
    res = get_engine().evaluate(m)
    assert res.passed, f"failed={res.failed_gates}"


def test_lebron_3pm_under_fails_when_gap_below_15pct():
    """L=1.5, proj=1.28 → gap 0.147 < 0.15 (FL UNDER). HR=75 → relaxed
    cap, but direction_gate still fails."""
    m = _u(stat_family="threes", line=1.5, hit_rate=75.0,
           hit_rate_l20=75.0, cv=1.047, vision_score=85.0,
           reference_odds=-124, extras={"projection": 1.28},
           edge_pct=22.3, tp=52.7)
    res = get_engine().evaluate(m)
    assert "direction_gate" in res.failed_gates
    assert not res.passed


# ─── Hard rules: do NOT override these ────────────────────────
def test_under_does_not_override_hr_below_65():
    """The CV-relax / disable rules NEVER rescue HR < 65."""
    m = _u(stat_family="threes", line=1.5, hit_rate=60.0,
           hit_rate_l20=60.0, cv=0.30, vision_score=85.0,
           extras={"projection": 1.0})  # gap 0.33
    res = get_engine().evaluate(m)
    assert not res.passed
    assert "hit_rate_gate" in res.failed_gates


def test_under_does_not_override_direction_failure():
    m = _u(stat_family="threes", line=1.5, hit_rate=85.0,
           hit_rate_l20=85.0, cv=0.30, vision_score=85.0,
           extras={"projection": 1.4})  # gap 0.067 < 0.15
    res = get_engine().evaluate(m)
    assert not res.passed
    assert "direction_gate" in res.failed_gates


def test_fl_under_still_has_tp_gate_with_under_floor():
    """Spec: 'do NOT change TP calculation'. Confirm FL UNDER block
    still carries the TP gate exactly as in OVER `_default`."""
    cfg = resolve_thresholds("nba", "front_lines", "threes",
                             side="UNDER")
    assert cfg["tp_gate"]["min"] == 50.0
    assert cfg["tp_gate"]["under_floor"] == 65.0


def test_fl_under_still_has_edge_gate():
    cfg = resolve_thresholds("nba", "front_lines", "threes",
                             side="UNDER")
    assert cfg["edge_gate"]["min"] == 5.0


def test_sh_under_still_has_market_structure_gate():
    cfg = resolve_thresholds("nba", "safe_haven", "threes",
                             side="UNDER")
    ms = cfg.get("market_structure_gate")
    assert ms is not None
    assert ms["reject_when"]["is_alt"] is True
    assert ms["reject_when"]["tp_source"] == "one_sided"


# ─── OVER-side regression ─────────────────────────────────────
def test_over_pick_does_not_use_under_block():
    """An OVER pick must continue to evaluate against `_default`,
    not `_default_under`. We pick a setup that PASSES OVER but
    would fail UNDER's direction (proj < line)."""
    over = NormalizedMetrics(
        sport="nba", tier="front_lines", stat_family="threes",
        side="OVER", reference_book="dk", reference_odds=-110,
        book_count=3, tp=58.0, hit_rate=80.0, hit_rate_l20=80.0,
        cv=0.40, edge_pct=10.0, vision_score=85.0, line=1.5,
        extras={"projection": 1.86},
    )
    res = get_engine().evaluate(over)
    assert res.passed
    # direction_gate ran against OVER config (min_projection_minus_line=0.0)
    dg = res.gate_details["direction_gate"]
    assert dg.passed
    assert "UNDER" not in dg.threshold.get("applies_to_sides", [])


# ─── CV caps map sanity ───────────────────────────────────────
def test_cv_caps_cover_all_target_stat_families():
    expected = {"pts", "pra", "ast", "reb", "threes",
                "stl", "blk", "pts_ast", "pts_reb", "reb_ast"}
    for fam in expected:
        assert fam in _NBA_UNDER_CV_CAPS, fam
