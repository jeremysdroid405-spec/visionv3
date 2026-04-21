"""
Tests for services/scoring/cv_caps.py (shared multi-sport)
+ NBA scoring adapter `check_safe_haven_gates` stat-aware behavior.

Locks the contract:
  * The 13 audited CV-only rejects become eligible where AST/REB/STL/BLK
    now get up to 0.60-0.65 headroom, and PTS/PRA borderlines still fail.
  * Unknown stats (MLB "Hits", future NFL) use DEFAULT_CV_CAP (0.50).
  * Extreme outliers (CV ≥ 0.70) do NOT flood through.
"""
from __future__ import annotations

import pytest

from services.scoring.cv_caps import (
    CV_CAP_BY_STAT,
    DEFAULT_CV_CAP,
    resolve_cv_cap,
)
from services.scoring.adapters.nba_scoring import _NBAGateSorter


# --------- Cap map contract ------------------------------------------------
def test_default_cv_cap_is_0_5():
    assert DEFAULT_CV_CAP == 0.50


def test_known_stat_caps():
    assert resolve_cv_cap("PTS") == 0.50
    assert resolve_cv_cap("PRA") == 0.50
    assert resolve_cv_cap("AST") == 0.60
    assert resolve_cv_cap("REB") == 0.60
    assert resolve_cv_cap("3PM") == 0.55
    assert resolve_cv_cap("STL") == 0.65
    assert resolve_cv_cap("BLK") == 0.65
    assert resolve_cv_cap("PTS+REB") == 0.50
    assert resolve_cv_cap("REB+AST") == 0.55


def test_unknown_stat_falls_back_to_default():
    assert resolve_cv_cap("Hits") == DEFAULT_CV_CAP
    assert resolve_cv_cap("Pitcher Strikeouts") == DEFAULT_CV_CAP
    assert resolve_cv_cap("passing_yards") == DEFAULT_CV_CAP      # future NFL
    assert resolve_cv_cap("totally_fake") == DEFAULT_CV_CAP
    assert resolve_cv_cap(None) == DEFAULT_CV_CAP
    assert resolve_cv_cap("") == DEFAULT_CV_CAP


# --------- Safe Haven gate integration -------------------------------------
@pytest.fixture
def sorter():
    return _NBAGateSorter()


def _call_sh(sorter, stat, cv, hr=80.0, edge=15.0, tp=75.0):
    prop = {"stat_type": stat}
    return sorter.check_safe_haven_gates(prop, cv=cv, hit_rate=hr,
                                         edge_pct=edge, tp=tp)


# Of the 13 CV-only rejects, 2 are PRA (Vucevic 10.5/11.5) — PRA cap stays at
# 0.50 per product spec, so they MUST remain rejected. The other 11 (AST/REB
# high-HR picks plus a PTS 4.5 borderline) become eligible.
AUDITED_CV_ONLY_REJECTS_NOW_ADMITTED = [
    # (stat, cv, hit_rate, edge, tp, label)
    ("REB", 0.51, 90.0, 19.30, 74.5, "Nikola Vucevic REB 3.5"),
    ("AST", 0.56, 75.0, 22.50, 74.1, "Dyson Daniels AST 3.5"),
    ("AST", 0.58, 85.0, 14.90, 78.7, "Immanuel Quickley AST 2.5"),
    ("REB", 0.55, 85.0, 11.30, 70.0, "Jaxson Hayes REB 2.5"),
    ("REB", 0.56, 80.0,  8.00, 79.6, "Luke Kornet REB 2.5"),
    ("REB", 0.53, 75.0,  9.70, 79.0, "Keldon Johnson REB 2.5"),
    ("AST", 0.55, 95.0, 10.90, 73.7, "Ajay Mitchell AST 1.5"),
]

# PRA cap intentionally stays at 0.50 → these two MUST remain rejected
# even though they pass every other gate.
AUDITED_CV_ONLY_REJECTS_STILL_REJECTED = [
    ("PRA", 0.54, 80.0, 13.00, 71.4, "Nikola Vucevic PRA 11.5"),
    ("PRA", 0.54, 90.0, 11.00, 76.2, "Nikola Vucevic PRA 10.5"),
]


@pytest.mark.parametrize("stat,cv,hr,edge,tp,label", AUDITED_CV_ONLY_REJECTS_NOW_ADMITTED)
def test_audited_cv_only_rejects_now_admit(sorter, stat, cv, hr, edge, tp, label):
    passed, reason, gates = _call_sh(sorter, stat, cv, hr, edge, tp)
    assert passed, f"{label} must now pass Safe Haven gates (reason={reason}, gates={gates})"
    assert gates["gate_cv"]["passed"] is True


@pytest.mark.parametrize("stat,cv,hr,edge,tp,label", AUDITED_CV_ONLY_REJECTS_STILL_REJECTED)
def test_PRA_audited_rejects_remain_rejected(sorter, stat, cv, hr, edge, tp, label):
    passed, reason, _ = _call_sh(sorter, stat, cv, hr, edge, tp)
    assert not passed, f"{label} must remain CV-rejected (PRA cap stays 0.50)"
    assert "gate_cv" in reason


def test_PTS_0_54_still_rejected(sorter):
    """PTS/PRA kept at the tight 0.50 cap — the same CV that admits REB
    must still reject a borderline PTS pick."""
    passed, reason, gates = _call_sh(sorter, "PTS", cv=0.54, hr=80.0, edge=10.0, tp=75.0)
    assert not passed
    assert "gate_cv" in reason


def test_PRA_0_54_still_rejected(sorter):
    passed, reason, _ = _call_sh(sorter, "PRA", cv=0.54, hr=80.0, edge=10.0, tp=75.0)
    assert not passed and "gate_cv" in reason


def test_high_CV_outlier_ast_still_rejected(sorter):
    """AST cap is 0.60 — so CV 0.68 / 0.72 / 0.77 must still fail,
    proving that loosening is bounded, not a flood."""
    for cv in (0.61, 0.68, 0.72, 0.77):
        passed, reason, _ = _call_sh(sorter, "AST", cv=cv)
        assert not passed, f"AST CV={cv} must still be rejected"
        assert "gate_cv" in reason


def test_high_CV_outlier_reb_still_rejected(sorter):
    for cv in (0.62, 0.70, 0.79):
        passed, reason, _ = _call_sh(sorter, "REB", cv=cv)
        assert not passed, f"REB CV={cv} must still be rejected"
        assert "gate_cv" in reason


def test_STL_admits_up_to_0_65(sorter):
    # Within cap → pass
    passed, _, gates = _call_sh(sorter, "STL", cv=0.64, hr=80.0, edge=12.0, tp=72.0)
    assert passed and gates["gate_cv"]["threshold"] == 0.65
    # Over cap → fail
    passed, reason, _ = _call_sh(sorter, "STL", cv=0.66, hr=80.0, edge=12.0, tp=72.0)
    assert not passed and "gate_cv" in reason


def test_unknown_stat_uses_default_cap_in_safe_haven(sorter):
    """MLB / future NFL stats hit the 0.50 default — no accidental relaxation."""
    passed, reason, _ = _call_sh(sorter, "Hits", cv=0.52, hr=80.0, edge=12.0, tp=75.0)
    assert not passed and "gate_cv" in reason
    passed, _, _ = _call_sh(sorter, "Hits", cv=0.49, hr=80.0, edge=12.0, tp=75.0)
    assert passed


def test_other_gates_still_enforced(sorter):
    """Relaxing CV doesn't short-circuit hit-rate / edge / tp gates."""
    # AST CV is fine, but hit_rate too low -> still fail on hit_rate
    passed, reason, gates = _call_sh(sorter, "AST", cv=0.55, hr=60.0, edge=15.0, tp=75.0)
    assert not passed
    assert "gate_hit_rate" in reason
    assert gates["gate_cv"]["passed"] is True  # cv axis independently passed


def test_none_cv_still_fails(sorter):
    passed, reason, _ = _call_sh(sorter, "AST", cv=None)
    assert not passed and "gate_cv" in reason


def test_none_stat_uses_default_cap(sorter):
    prop = {}  # no stat_type
    passed, _, gates = sorter.check_safe_haven_gates(
        prop, cv=0.49, hit_rate=80.0, edge_pct=12.0, tp=75.0,
    )
    assert passed  # default cap = 0.50, CV 0.49 passes
    assert gates["gate_cv"]["threshold"] == 0.50
    passed, reason, _ = sorter.check_safe_haven_gates(
        prop, cv=0.52, hit_rate=80.0, edge_pct=12.0, tp=75.0,
    )
    assert not passed and "gate_cv" in reason


# --------- Non-interference with other tiers -------------------------------
def test_front_lines_unchanged(sorter):
    """Front Lines CV cap is 0.75 and untouched by this change."""
    prop = {"stat_type": "AST"}
    passed, _, gates = sorter.check_front_lines_gates(
        prop, cv=0.70, hit_rate=65.0, edge_pct=10.0, tp=60.0,
    )
    assert passed
    assert gates["gate_cv"]["threshold"] == 0.75


def test_war_zone_unchanged(sorter):
    prop = {"stat_type": "AST"}
    passed, _, gates = sorter.check_war_zone_gates(
        prop, cv=0.55, ceiling_rate=25.0, edge_pct=12.0,
    )
    assert passed
    assert gates["gate_cv"]["threshold"] == 0.45       # WAR_ZONE min_cv


# --------- Multi-sport plug-in proof ---------------------------------------
def test_adding_nfl_caps_is_config_change_only():
    try:
        CV_CAP_BY_STAT["receptions"] = 0.55
        assert resolve_cv_cap("receptions") == 0.55
        sorter = _NBAGateSorter()
        prop = {"stat_type": "receptions"}
        passed, _, gates = sorter.check_safe_haven_gates(
            prop, cv=0.54, hit_rate=80.0, edge_pct=12.0, tp=75.0,
        )
        assert passed
        assert gates["gate_cv"]["threshold"] == 0.55
    finally:
        CV_CAP_BY_STAT.pop("receptions", None)
