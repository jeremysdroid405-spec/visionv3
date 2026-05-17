"""Registry-mirrors-live-config tests.

Asserts that the values in `POLICY_REGISTRY` match what
`gates/thresholds.py` and `gates/engine.py` and `vision_v2.py`
actually carry today. If thresholds.py changes, these tests fail
loudly so the registry has to be updated in lockstep.

These tests are intentionally written against the LIVE config
objects, not hand-typed expected values, so the registry can never
silently drift.
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/app/backend")

import pytest

from services.scoring.market_structure_policy import (
    POLICY_REGISTRY, policy_for, EliteBinaryOverride,
)


# ── NBA SH — `market_structure_gate` mirror ────────────────────────
def test_nba_safe_haven_mirrors_market_structure_gate_cfg():
    """`POLICY_REGISTRY[(nba, safe_haven)]` must match the live
    `_NBA_SAFE_HAVEN_BASE['market_structure_gate']` cfg."""
    from services.scoring.gates.thresholds import _NBA_SAFE_HAVEN_BASE
    ms = _NBA_SAFE_HAVEN_BASE["market_structure_gate"]["reject_when"]
    # The live cfg keys (is_alt + tp_source) imply alt rejection.
    assert ms.get("is_alt") is True
    assert ms.get("tp_source") == "one_sided"

    pol = policy_for("nba", "safe_haven")
    assert pol.reject_alt_one_sided is True
    assert pol.reject_standard_one_sided is False
    assert pol.elite_binary_override is None
    assert pol.source_gate_in_config == "market_structure_gate"


def test_nba_safe_haven_under_mirrors_under_market_structure_gate_cfg():
    """NBA SH UNDER also has `market_structure_gate` with the same
    rule. The single OneSidedPolicy entry covers both sides
    (engine applies same gate to both)."""
    from services.scoring.gates.thresholds import _NBA_SAFE_HAVEN_UNDER
    ms_under = _NBA_SAFE_HAVEN_UNDER["market_structure_gate"]["reject_when"]
    assert ms_under.get("is_alt") is True
    assert ms_under.get("tp_source") == "one_sided"


# ── NBA FL / WZ ────────────────────────────────────────────────────
def test_nba_fl_and_wz_do_not_carry_market_structure_gate():
    """FL / WZ on NBA today have NO market_structure_gate. Registry
    must reflect this — no rejection at either gate."""
    from services.scoring.gates.thresholds import (
        _NBA_FRONT_LINES_BASE, _NBA_WAR_ZONE_BASE,
    )
    assert "market_structure_gate" not in _NBA_FRONT_LINES_BASE
    assert "market_structure_gate" not in _NBA_WAR_ZONE_BASE
    assert "tp_source_gate"        not in _NBA_FRONT_LINES_BASE
    assert "tp_source_gate"        not in _NBA_WAR_ZONE_BASE

    for tier in ("front_lines", "war_zone"):
        pol = policy_for("nba", tier)
        assert pol.reject_alt_one_sided is False
        assert pol.reject_standard_one_sided is False
        assert pol.elite_binary_override is None


# ── MLB SH — `tp_source_gate` mirror ───────────────────────────────
def test_mlb_safe_haven_mirrors_tp_source_gate_cfg():
    """MLB SH's `tp_source_gate` (built by `_mlb_thresholds`) carries
    `required_source=devig` + `one_sided_override`. Registry must
    match the override fields."""
    from services.scoring.gates.thresholds import resolve_thresholds
    cfg = resolve_thresholds("mlb", "safe_haven", "hits", side="OVER")
    tps = cfg.get("tp_source_gate") or {}
    assert tps.get("required_source") == "devig", (
        "MLB SH must require devig; if this changed, update "
        "POLICY_REGISTRY[(mlb, safe_haven)].reject_standard_one_sided "
        "accordingly."
    )
    over = tps.get("one_sided_override") or {}
    assert over.get("hr_l20_min") == 90.0
    assert over.get("hr_l5_min")  == 80.0
    assert over.get("min_edge_pp") == 5.0
    assert over.get("cv_max") == 0.70
    assert set(over.get("allowed_stat_families") or []) == {
        "hits", "hits_runs_rbis", "runs", "rbis",
        "batter_strikeouts", "stolen_bases", "batter_walks",
    }

    pol = policy_for("mlb", "safe_haven")
    assert pol.reject_alt_one_sided is True
    assert pol.reject_standard_one_sided is True
    assert pol.source_gate_in_config == "tp_source_gate"
    eo = pol.elite_binary_override
    assert isinstance(eo, EliteBinaryOverride)
    assert eo.hr_l20_min == 90.0 and eo.hr_l5_min == 80.0
    assert eo.min_edge_pp == 5.0 and eo.cv_max == 0.70
    assert eo.allowed_stat_families == frozenset({
        "hits", "hits_runs_rbis", "runs", "rbis",
        "batter_strikeouts", "stolen_bases", "batter_walks",
    })


def test_mlb_fl_and_wz_do_not_carry_tp_source_gate():
    """MLB FL has no tp_source_gate (per `if not front_lines` in
    `_mlb_thresholds`). MLB WZ has no tp_source_gate either (per the
    2026-05-16 full rewrite). Registry must reflect."""
    from services.scoring.gates.thresholds import resolve_thresholds
    cfg_fl = resolve_thresholds("mlb", "front_lines", "hits", side="OVER")
    assert "tp_source_gate" not in cfg_fl

    cfg_wz = resolve_thresholds("mlb", "war_zone", "hits", side="OVER")
    assert "tp_source_gate" not in cfg_wz

    for tier in ("front_lines", "war_zone"):
        pol = policy_for("mlb", tier)
        assert pol.reject_alt_one_sided is False
        assert pol.reject_standard_one_sided is False
        assert pol.elite_binary_override is None


# ── Vision-confidence multiplier mirrors vision_v2.py hardcode ─────
def test_vision_confidence_multiplier_matches_vision_v2():
    """The 0.5 multiplier lives hardcoded in
    `vision_v2._market_confidence_component`. Registry exposes it
    per sport; today NBA=0.5, MLB=1.0 (MLB live serving doesn't
    invoke vision_v2 today, so no penalty applies)."""
    import inspect
    from services.scoring import vision_v2
    src = inspect.getsource(vision_v2._market_confidence_component)
    # Hardcode line: `0.5 if src == "one_sided" else 0.3`
    assert "0.5 if src == \"one_sided\"" in src, (
        "vision_v2._market_confidence_component changed; update "
        "POLICY_REGISTRY vision_confidence_multiplier values."
    )
    # Registry expectations.
    assert policy_for("nba", "safe_haven").vision_confidence_multiplier == 0.5
    assert policy_for("nba", "front_lines").vision_confidence_multiplier == 0.5
    assert policy_for("nba", "war_zone").vision_confidence_multiplier == 0.5
    assert policy_for("mlb", "safe_haven").vision_confidence_multiplier == 1.0
    assert policy_for("mlb", "front_lines").vision_confidence_multiplier == 1.0
    assert policy_for("mlb", "war_zone").vision_confidence_multiplier == 1.0
