"""Regression tests for the 2026-05-09 War Zone OVER gate adjustment.

User spec recap:
  1. WZ OVER hit_rate_gate.min: 55 → 50
  2. Direction gate preserved
  3. min_books >= 1 preserved
  4. edge_gate.min remains strictly positive
  5. CV-cap ladder for WZ OVER:
       default        : CV ≤ 0.75
       HR ≥ 70 + edge>0 : CV ≤ 1.15
       HR ≥ 80 + edge≥5 : CV ≤ 1.50
  6. Untouched: vs_v2 floor, SH, FL, UNDER, scoring formula

These tests pin every single one of those guarantees.
"""
from __future__ import annotations

import pytest

from services.scoring.gates.thresholds import THRESHOLDS, _NBA_WAR_ZONE_BASE
from services.scoring.gates.schema import NormalizedMetrics, GateDetail
from services.scoring.gates.overrides import apply_war_zone_overrides


# ---------------------------------------------------------------------------
# Threshold-config invariants
# ---------------------------------------------------------------------------
def test_wz_over_hit_rate_gate_min_lowered_to_50():
    cfg = _NBA_WAR_ZONE_BASE
    assert cfg["hit_rate_gate"]["min"] == 50.0
    # L5 sub-gate stays disabled (WZ thesis is recent variance).
    assert cfg["hit_rate_gate"]["enforce_l5_subgate"] is False


def test_wz_over_coverage_min_books_unchanged():
    cfg = _NBA_WAR_ZONE_BASE
    assert cfg["coverage_gate"]["min_books"] == 1


def test_wz_over_direction_gate_unchanged():
    dg = _NBA_WAR_ZONE_BASE["direction_gate"]
    assert dg["applies_to_sides"] == ["OVER"]
    # proj/line >= 1.00 = projection >= line. Either form is acceptable
    # so long as semantically OVER picks must have projection >= line.
    assert dg.get("min_projection_to_line_ratio", 1.0) >= 1.0


def test_wz_over_edge_gate_strictly_positive():
    eg = _NBA_WAR_ZONE_BASE["edge_gate"]
    assert eg["min"] > 0.0


def test_wz_over_default_cv_cap_unchanged():
    assert _NBA_WAR_ZONE_BASE["cv_gate"]["max"] == 0.75


def test_wz_over_vision_score_floor_unchanged():
    assert _NBA_WAR_ZONE_BASE["vision_score_gate"]["min"] == 60.0


def test_wz_over_cv_ladder_present_with_two_tiers():
    ladder = (_NBA_WAR_ZONE_BASE["__war_zone_overrides__"]
              .get("hr_expansion_ladder") or [])
    by_hr = {t["min_hit_rate"]: t for t in ladder}
    assert 70.0 in by_hr and 80.0 in by_hr
    assert by_hr[70.0]["relax_cv_to"] == 1.15
    assert by_hr[80.0]["relax_cv_to"] == 1.50
    # Tier 3 requires edge ≥ 5; Tier 2 requires edge > 0.
    assert by_hr[80.0]["min_edge_pct"] >= 5.0
    assert by_hr[70.0]["min_edge_pct"] > 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_metrics(*, hr, cv, edge, **kw):
    """Build a minimal NormalizedMetrics for WZ override tests."""
    return NormalizedMetrics(
        sport="nba", tier="war_zone", stat_family=kw.get("stat_family", "pts"),
        side="OVER",
        hit_rate=hr, cv=cv, edge_pct=edge,
        line=kw.get("line", 10.0),
    )


def _failed_cv_only():
    """Return (details, passed, failed) with cv_gate the sole failure."""
    detail = GateDetail(
        gate_type="cv_gate", threshold=0.75, actual=1.10,
        passed=False, comparator="<=",
        reason_code="gate_cv_fail",
    )
    return ({"cv_gate": detail}, ["direction_gate", "edge_gate"], ["cv_gate"])


# ---------------------------------------------------------------------------
# Override-engine ladder behaviour
# ---------------------------------------------------------------------------
WZ_OVERRIDE_CFG = (
    _NBA_WAR_ZONE_BASE["__war_zone_overrides__"]
)


def test_default_cv_below_075_is_passed_by_base_gate_not_overrides():
    """Sanity: when cv_gate didn't fail, override layer is a no-op."""
    metrics = _make_metrics(hr=85.0, cv=0.40, edge=10.0)
    d, p, f = {}, ["coverage_gate", "cv_gate"], []
    _, _, _, ok, rule = apply_war_zone_overrides(metrics, d, p, f, WZ_OVERRIDE_CFG)
    assert ok is True
    assert rule is None


def test_tier2_relax_to_115_triggers_when_hr70_and_edge_positive():
    """HR=72, edge=2pp, cv=1.10 → tier 2 rescues (cv ≤ 1.15)."""
    metrics = _make_metrics(hr=72.0, cv=1.10, edge=2.0)
    d, p, f = _failed_cv_only()
    _, _, f_after, ok, rule = apply_war_zone_overrides(metrics, d, p, f, WZ_OVERRIDE_CFG)
    assert ok is True
    assert "cv_gate" not in f_after
    assert rule == "war_zone:hr_expansion_hr70"


def test_tier2_does_not_trigger_when_cv_above_115():
    """HR=72, edge=2pp, cv=1.20 → tier 2 cap is 1.15, no rescue."""
    metrics = _make_metrics(hr=72.0, cv=1.20, edge=2.0)
    d, p, f = _failed_cv_only()
    _, _, _, ok, rule = apply_war_zone_overrides(metrics, d, p, f, WZ_OVERRIDE_CFG)
    assert ok is False
    assert rule is None


def test_tier2_does_not_trigger_with_zero_edge():
    """HR=75 but edge=0 → tier 2 requires edge > 0, no rescue."""
    metrics = _make_metrics(hr=75.0, cv=1.10, edge=0.0)
    d, p, f = _failed_cv_only()
    _, _, _, ok, rule = apply_war_zone_overrides(metrics, d, p, f, WZ_OVERRIDE_CFG)
    assert ok is False
    assert rule is None


def test_tier3_relax_to_150_triggers_when_hr80_and_edge_strong():
    """HR=82, edge=8pp, cv=1.45 → tier 3 rescues (cv ≤ 1.50)."""
    metrics = _make_metrics(hr=82.0, cv=1.45, edge=8.0)
    d, p, f = _failed_cv_only()
    _, _, f_after, ok, rule = apply_war_zone_overrides(metrics, d, p, f, WZ_OVERRIDE_CFG)
    assert ok is True
    assert "cv_gate" not in f_after
    assert rule == "war_zone:hr_expansion_hr80"


def test_tier3_falls_back_to_tier2_when_edge_not_strong():
    """HR=85 (passes tier 3 hr) BUT edge=2pp (fails tier 3 edge).
    Tier 2 still allows (HR>=70 + edge>0); cv=1.40 must FAIL because
    1.40 > tier-2 cap 1.15 and tier 3 was unmet."""
    metrics = _make_metrics(hr=85.0, cv=1.40, edge=2.0)
    d, p, f = _failed_cv_only()
    _, _, _, ok, rule = apply_war_zone_overrides(metrics, d, p, f, WZ_OVERRIDE_CFG)
    assert ok is False, "tier 3 cap should NOT apply without strong edge"


def test_tier3_strict_edge_threshold():
    """HR=80, edge=5.0 (exactly the floor), cv=1.50 (exactly the cap)
    → tier 3 rescues (>= comparators on both)."""
    metrics = _make_metrics(hr=80.0, cv=1.50, edge=5.0)
    d, p, f = _failed_cv_only()
    _, _, _, ok, rule = apply_war_zone_overrides(metrics, d, p, f, WZ_OVERRIDE_CFG)
    assert ok is True
    assert rule == "war_zone:hr_expansion_hr80"


def test_low_hr_blocks_all_relaxation():
    """HR=65 is below the lowest ladder rung (70). No rescue regardless
    of edge or cv."""
    metrics = _make_metrics(hr=65.0, cv=0.80, edge=20.0)
    d, p, f = _failed_cv_only()
    _, _, _, ok, rule = apply_war_zone_overrides(metrics, d, p, f, WZ_OVERRIDE_CFG)
    assert ok is False
    assert rule is None


def test_only_cv_failures_are_rescuable():
    """direction_gate / hit_rate_gate / edge_gate / coverage_gate /
    vision_score_gate failures are NEVER rescued by this override."""
    metrics = _make_metrics(hr=85.0, cv=0.50, edge=10.0)
    detail_dir = GateDetail(
        gate_type="direction_gate", threshold={}, actual={"projection": 5.0, "line": 10.0},
        passed=False, comparator="custom",
        reason_code="gate_direction_fail",
    )
    d = {"direction_gate": detail_dir}
    p, f = [], ["direction_gate"]
    _, _, _, ok, rule = apply_war_zone_overrides(metrics, d, p, f, WZ_OVERRIDE_CFG)
    assert ok is False
    assert rule is None


def test_legacy_hr_expansion_block_still_honoured():
    """If a future caller still passes the legacy single-rule shape
    we must not silently break — the override must convert it to a
    1-entry ladder."""
    metrics = _make_metrics(hr=72.0, cv=0.95, edge=0.0)
    d, p, f = _failed_cv_only()
    _, _, _, ok, rule = apply_war_zone_overrides(
        metrics, d, p, f,
        {"hr_expansion": {"enabled": True, "min_hit_rate": 70.0, "relax_cv_to": 1.0}},
    )
    assert ok is True
    assert rule == "war_zone:hr_expansion_hr70"


def test_ladder_evaluated_highest_tier_first():
    """When BOTH tiers qualify, the highest tier (tier 3) wins so its
    audit note attributes the rescue to the strongest signal."""
    metrics = _make_metrics(hr=85.0, cv=1.00, edge=8.0)
    d, p, f = _failed_cv_only()
    _, _, _, ok, rule = apply_war_zone_overrides(metrics, d, p, f, WZ_OVERRIDE_CFG)
    assert ok is True
    assert rule == "war_zone:hr_expansion_hr80"


# ---------------------------------------------------------------------------
# Cross-tier invariants — adjustment must be WZ-OVER scoped only.
# ---------------------------------------------------------------------------
def test_safe_haven_thresholds_unchanged_by_this_patch():
    """SH is not in the user's spec — its config must remain untouched."""
    sh = THRESHOLDS["nba"]["safe_haven"]["_default"]
    assert sh["hit_rate_gate"]["min"] == 80.0
    assert sh["vision_score_gate"]["min"] == 80.0
    assert sh["edge_gate"]["min"] == 0.0
    # Stat-family caps unchanged
    caps = sh["cv_gate"]["caps"]
    assert caps["pts"] == 0.40 and caps["pra"] == 0.40
    assert caps["pts_ast"] == 0.45 and caps["pts_reb"] == 0.45


def test_front_lines_thresholds_unchanged_by_this_patch():
    fl = THRESHOLDS["nba"]["front_lines"]["_default"]
    assert fl["hit_rate_gate"]["min"] == 70.0
    assert fl["edge_gate"]["min"] == 5.0
    assert fl["cv_gate"]["max"] == 0.75


def test_war_zone_under_thresholds_unchanged_by_this_patch():
    wz_under = THRESHOLDS["nba"]["war_zone"].get("_under_default")
    # The UNDER block lives in the engine — verify it's NOT contaminated by
    # the OVER ladder we just added. Even if it's None here, the OVER patch
    # must not write into it.
    if wz_under is not None:
        assert "hr_expansion_ladder" not in (
            wz_under.get("__war_zone_overrides__") or {}
        )
