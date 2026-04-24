"""
CV-cap contract tests.

Two resolution paths exist:
  1. `cv_gate.caps` dict in the tier config — stat-family-keyed hard
     caps (used by NBA Safe Haven rebuild 2026-04-24 and NBA War Zone).
  2. Adapter-supplied `extras['cv_cap_override']` scalar — used by
     tiers whose config has scalar `cv_gate.max` and no `caps` dict
     (e.g. NBA Front Lines).

`services.scoring.cv_caps.resolve_cv_cap` drives path (2) via the
adapter. Its values remain the canonical NBA reference for that path.
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


# --------- cv_caps module contract ----------------------------------------
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


def test_cap_map_keys_are_uppercase_nba_stats():
    for k in CV_CAP_BY_STAT:
        assert isinstance(k, str)
        assert k.isupper() or "+" in k


# --------- Path 1: stat-family `caps` dict (NBA Safe Haven 2026-04-24) ----
def _sh(stat_family: str, cv: float):
    return get_engine().evaluate(NormalizedMetrics(
        sport="nba", tier="safe_haven", stat_family=stat_family, side="OVER",
        reference_book="dk", reference_odds=-300,
        book_count=2, hit_rate=90.0, cv=cv,
        vision_score=92.0, tp_source="devig", is_alt=False,
    ))


def test_safe_haven_pts_cap_is_0_40():
    assert _sh("pts", 0.40).gate_details["cv_gate"].threshold == 0.40
    assert _sh("pts", 0.40).passed is True
    assert _sh("pts", 0.41).gate_details["cv_gate"].passed is False


def test_safe_haven_pra_cap_is_0_40():
    assert _sh("pra", 0.40).passed is True
    assert _sh("pra", 0.41).gate_details["cv_gate"].passed is False


def test_safe_haven_reb_ast_cap_is_0_45():
    for fam in ("reb", "ast"):
        assert _sh(fam, 0.45).gate_details["cv_gate"].threshold == 0.45
        assert _sh(fam, 0.46).gate_details["cv_gate"].passed is False


def test_safe_haven_threes_cap_preserved_at_0_55():
    assert _sh("threes", 0.55).gate_details["cv_gate"].threshold == 0.55
    assert _sh("threes", 0.55).passed is True
    assert _sh("threes", 0.56).gate_details["cv_gate"].passed is False


# --------- Path 2: adapter override via `extras['cv_cap_override']` -------
# NBA Front Lines still uses scalar `cv_gate: {"max": 0.75}` and no caps
# dict, so the override path is live there.
def _fl(stat_family: str, cv: float, cv_override: float | None = None):
    return get_engine().evaluate(NormalizedMetrics(
        sport="nba", tier="front_lines", stat_family=stat_family, side="OVER",
        reference_book="dk", reference_odds=-100,
        book_count=2, tp=60.0, hit_rate=70.0, cv=cv, edge_pct=10.0,
        extras={"cv_cap_override": cv_override},
    ))


def test_front_lines_cap_override_uses_note():
    r = _fl("ast", cv=0.55, cv_override=resolve_cv_cap("AST"))
    assert r.gate_details["cv_gate"].note == "cv_cap_override_from_adapter"
    assert r.gate_details["cv_gate"].threshold == 0.60


def test_front_lines_no_override_uses_config_default():
    r = _fl("pts", cv=0.60, cv_override=None)
    assert r.gate_details["cv_gate"].threshold == 0.75
    assert r.gate_details["cv_gate"].passed is True


def test_front_lines_extreme_cv_still_fails():
    r = _fl("ast", cv=0.80, cv_override=resolve_cv_cap("AST"))
    assert r.gate_details["cv_gate"].passed is False
    assert r.gate_details["cv_gate"].reason_code == ReasonCode.CV_FAIL
