"""
Tests for War Zone CV-floor removal (2026-04-22).

Locks the pricing-integrity refactor:
  - MLB `WAR_ZONE_GATES[*]["min_cv"]` is 0 (floor neutralised).
  - `check_war_zone_gates(...)` never hard-fails on `gate1_cv`.
  - `war_zone_cv_modifier(cv)` returns a small positive for low CV,
    neutral at mid CV, and a small negative for extreme CV.
  - Odds bucket, book_count >= 1, ceiling gate, edge gate, min-TP gate
    (book_count is enforced by the upstream coverage filter, tested
    separately in `test_coverage_filter.py`).
"""
from __future__ import annotations

import pytest

from services.mlb_tier_sorter import (
    MLBTierSorter, WAR_ZONE_GATES, war_zone_cv_modifier,
)


# --------------------------------------------------------------------
# Config-level
# --------------------------------------------------------------------

def test_min_cv_floor_is_zero_for_every_stat():
    """The floor must be effectively neutralised. Any non-zero value
    would re-introduce the disqualification we are removing."""
    for stat, cfg in WAR_ZONE_GATES.items():
        assert cfg["min_cv"] == 0.0, (
            f"{stat} still has min_cv={cfg['min_cv']} — CV floor must be removed"
        )


def test_ceiling_and_edge_gates_still_required():
    """Only the CV floor was removed; ceiling + edge gates still in
    force per the user's explicit instruction."""
    for stat, cfg in WAR_ZONE_GATES.items():
        assert cfg["min_ceiling_rate"] > 0, f"{stat} missing ceiling gate"
        assert cfg["min_edge"] > 0, f"{stat} missing edge gate"


# --------------------------------------------------------------------
# CV modifier — scoring signal, never a disqualification
# --------------------------------------------------------------------

def test_cv_modifier_lower_cv_is_slightly_positive():
    """Per user spec: 'lower CV should be neutral or slightly
    positive, not disqualifying'."""
    assert war_zone_cv_modifier(0.20) == 0.10
    assert war_zone_cv_modifier(0.50) == 0.05


def test_cv_modifier_mid_cv_is_neutral():
    assert war_zone_cv_modifier(0.70) == 0.0
    assert war_zone_cv_modifier(0.80) == 0.0


def test_cv_modifier_extreme_cv_is_small_drag_not_disqualifying():
    v = war_zone_cv_modifier(1.50)
    # Must be small (≤ |0.10|) so it never dominates a real ceiling/edge
    # signal — "neutral or slightly positive", never disqualifying.
    assert -0.10 < v < 0.0


def test_cv_modifier_returns_zero_for_none_and_garbage():
    assert war_zone_cv_modifier(None) == 0.0
    assert war_zone_cv_modifier("not-a-number") == 0.0


# --------------------------------------------------------------------
# check_war_zone_gates — behavior
# --------------------------------------------------------------------

def _make_sorter():
    """Build a bare MLBTierSorter that doesn't touch Mongo."""
    # Pass a dummy db so __init__ doesn't crash; every gate-check method
    # we exercise below is stateless and doesn't hit the DB.
    class _DummyDB: pass
    return MLBTierSorter(_DummyDB())


def test_low_cv_no_longer_disqualifies_war_zone():
    """A prop with CV=0.10 but strong ceiling+edge used to fail
    `gate1_cv` (`min_cv=1.0`). Post-refactor it must pass."""
    sorter = _make_sorter()
    prop = {"stat_type": "Hits"}
    passed, reason, gates = sorter.check_war_zone_gates(
        prop, cv=0.10, ceiling_rate=40.0, edge_pct=35.0,
    )
    assert passed, f"Low-CV war-zone pick must now pass; reason={reason}"
    assert gates["gate1_cv"]["passed"] is True
    assert "not_enforced" in str(gates["gate1_cv"]["threshold"])
    # CV modifier must be set on the prop for downstream ranking.
    assert prop["war_zone_cv_modifier"] == 0.10


def test_ceiling_gate_still_rejects():
    sorter = _make_sorter()
    prop = {"stat_type": "Hits"}
    passed, reason, _ = sorter.check_war_zone_gates(
        prop, cv=0.30, ceiling_rate=10.0, edge_pct=35.0,
    )
    assert not passed
    assert "gate2_ceiling" in reason


def test_edge_gate_still_rejects():
    sorter = _make_sorter()
    prop = {"stat_type": "Hits"}
    passed, reason, _ = sorter.check_war_zone_gates(
        prop, cv=0.30, ceiling_rate=40.0, edge_pct=10.0,
    )
    assert not passed
    assert "gate3_edge" in reason


def test_cv_modifier_always_stamped_on_prop():
    """Whether the pick passes or fails the ceiling/edge gates, the CV
    modifier is always written onto the prop so ranking layers can
    consume it uniformly."""
    sorter = _make_sorter()
    prop = {"stat_type": "Total Bases"}
    sorter.check_war_zone_gates(prop, cv=0.30, ceiling_rate=0.0, edge_pct=0.0)
    assert "war_zone_cv_modifier" in prop
    assert prop["war_zone_cv_modifier"] == 0.10


def test_cv_modifier_neutral_when_no_cv():
    sorter = _make_sorter()
    prop = {"stat_type": "Hits"}
    sorter.check_war_zone_gates(prop, cv=None, ceiling_rate=40.0, edge_pct=35.0)
    assert prop["war_zone_cv_modifier"] == 0.0
