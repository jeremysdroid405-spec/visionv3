"""
CV-cap contract tests — post Universal Gate Engine cleanup (2026-04-22).

The stat-specific CV cap is now applied as an adapter-side override
via `NormalizedMetrics.extras['cv_cap_override']`. The engine honors it
through its single `cv_gate` path; there is no sport-specific gate
evaluator to test here.

Contracts locked:
  * The audited PTS+REB/AST borderline behaves per the cap map.
  * Unknown stats fall back to DEFAULT_CV_CAP (0.50).
  * Extreme outliers (CV >= cap+headroom) still fail.
"""
from __future__ import annotations

import pytest

from services.scoring.cv_caps import (
    CV_CAP_BY_STAT,
    DEFAULT_CV_CAP,
    resolve_cv_cap,
)
from services.scoring.gates import (
    NormalizedMetrics,
    ReasonCode,
    get_engine,
)


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
    assert resolve_cv_cap(None) == DEFAULT_CV_CAP
    assert resolve_cv_cap("") == DEFAULT_CV_CAP
    assert resolve_cv_cap("future_stat_xyz") == DEFAULT_CV_CAP


# --------- Engine honours the per-stat CV override -------------------------
def _eval(stat_family: str, cv: float, cv_override: float | None = None):
    return get_engine().evaluate(NormalizedMetrics(
        sport="nba", tier="safe_haven", stat_family=stat_family, side="OVER",
        reference_book="dk", reference_odds=-300,
        book_count=2, tp=80.0, hit_rate=80.0, cv=cv, edge_pct=12.0,
        extras={"cv_cap_override": cv_override},
    ))


def test_pts_hard_fails_above_0_50():
    # PTS has default cap (0.50) — a CV of 0.60 must fail.
    r = _eval("pts", cv=0.60, cv_override=resolve_cv_cap("PTS"))
    assert r.passed is False
    assert "cv_gate" in r.failed_gates
    assert r.gate_details["cv_gate"].threshold == 0.50


def test_ast_reb_pass_with_elevated_cap():
    # AST / REB get cap 0.60 via override; 0.58 should now pass.
    for stat, family in (("AST", "ast"), ("REB", "reb")):
        r = _eval(family, cv=0.58, cv_override=resolve_cv_cap(stat))
        assert r.passed is True, f"{stat} should pass with elevated cap"
        assert r.gate_details["cv_gate"].threshold == 0.60


def test_stl_blk_pass_at_0_64():
    for stat, family in (("STL", "stl"), ("BLK", "blk")):
        r = _eval(family, cv=0.64, cv_override=resolve_cv_cap(stat))
        assert r.passed is True
        assert r.gate_details["cv_gate"].threshold == 0.65


def test_extreme_cv_still_fails_even_with_elevated_cap():
    # AST cap is 0.60; CV of 0.75 must still fail.
    r = _eval("ast", cv=0.75, cv_override=resolve_cv_cap("AST"))
    assert r.passed is False
    assert r.gate_details["cv_gate"].reason_code == ReasonCode.CV_FAIL


def test_engine_uses_cap_override_note():
    r = _eval("ast", cv=0.55, cv_override=resolve_cv_cap("AST"))
    assert r.gate_details["cv_gate"].note == "cv_cap_override_from_adapter"


def test_no_override_uses_config_default():
    # With cv_override=None the engine uses the nba safe_haven cv cap of 0.50.
    r = _eval("pts", cv=0.48, cv_override=None)
    assert r.gate_details["cv_gate"].threshold == 0.50
    assert r.gate_details["cv_gate"].passed is True


def test_cap_map_keys_are_uppercase_nba_stats():
    # Defensive: the map should only contain NBA stat identifiers so we don't
    # accidentally collide with MLB stat names.
    for k in CV_CAP_BY_STAT:
        assert isinstance(k, str)
        assert k.isupper() or "+" in k
