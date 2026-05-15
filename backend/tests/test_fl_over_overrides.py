"""Tests for the NBA Front Lines OVER conditional override layer.

Spec (user, 2026-04-29):

    Apply ONLY to:
      sport == "nba", tier == "front_lines", side == "OVER"

    Rules (rescue specific tp/cv failures only):
      Direction rule: projection >= line is required (else direction_gate fail).
      3PM TP override:  HR > 75 + projection >= line → tp floor relaxed to 45.
      AST CV override:  HR > 85 + projection >= line → cv cap relaxed to 0.95.
      PTS dominance:    HR >= 75 + L20/line >= 1.5 + projection >= line
                        → bypass tp_gate / cv_gate failures.

    Hard rules:
      - REB / PRA / pts_reb / pts_ast / reb_ast / STL / BLK / combos NOT touched.
      - UNDER side NOT touched.
      - Safe Haven / War Zone NOT touched.
      - market_structure / direction / hit_rate / vision_score / coverage / edge
        failures are NOT overridable.
"""
from __future__ import annotations

from services.scoring.gates.engine import get_engine
from services.scoring.gates.schema import NormalizedMetrics


def _m(**overrides):
    base = dict(
        sport="nba",
        tier="front_lines",
        stat_family="threes",
        side="OVER",
        reference_book="dk",
        reference_odds=-105,
        book_count=3,
        tp=58.0,
        hit_rate=80.0,
        hit_rate_l20=80.0,
        cv=0.40,
        edge_pct=10.0,
        line=1.5,
        extras={"projection": 1.86},
    )
    base.update(overrides)
    # 2026-04-29 — Universal tp_gate semantics: tp_gate reads
    # p_model_pct, so mirror tp into p_model_pct unless explicitly
    # overridden by the caller. Preserves test intent (the prop has
    # X% true probability) without reshaping every call site.
    base.setdefault("p_model_pct", base.get("tp"))
    return NormalizedMetrics(**base)


# ─── 3PM TP override ────────────────────────────────────────────
def test_3pm_tp_override_rescues_pick():
    m = _m(stat_family="threes", hit_rate=80.0, hit_rate_l20=80.0,
           tp=46.0,  # below FL floor 50, but above relaxed 45
           extras={"projection": 1.86}, line=1.5)
    res = get_engine().evaluate(m)
    assert res.passed, f"reason={res.reason_code} failed={res.failed_gates}"
    aud = res.gate_details.get("__override_applied__")
    assert aud is not None
    assert aud.threshold == {"name": "fl_over:threes_tp_relax"}


def test_3pm_tp_override_does_not_rescue_below_45():
    m = _m(stat_family="threes", hit_rate=80.0, hit_rate_l20=80.0,
           tp=44.0,  # below the relaxed floor 45 — must still fail
           extras={"projection": 1.86})
    res = get_engine().evaluate(m)
    assert not res.passed
    assert "tp_gate" in res.failed_gates


def test_3pm_tp_override_requires_strict_hr_above_75():
    m = _m(stat_family="threes", hit_rate=75.0, hit_rate_l20=75.0,  # NOT > 75
           tp=46.0, extras={"projection": 1.86})
    res = get_engine().evaluate(m)
    assert not res.passed, "HR=75 (not strictly > 75) must NOT trigger override"


def test_3pm_override_requires_projection_ge_line():
    m = _m(stat_family="threes", hit_rate=80.0, hit_rate_l20=80.0,
           tp=46.0, line=2.5, extras={"projection": 1.86})  # proj < line
    res = get_engine().evaluate(m)
    assert not res.passed
    # direction_gate fails → override layer aborts.
    assert "direction_gate" in res.failed_gates


# ─── AST CV override ────────────────────────────────────────────
def test_ast_cv_override_rescues_pick():
    m = _m(stat_family="ast", hit_rate=88.0, hit_rate_l20=88.0,
           cv=0.85,  # above FL cap 0.75, but below relaxed 0.95
           tp=60.0, edge_pct=10.0,
           extras={"projection": 5.0}, line=4.5)
    res = get_engine().evaluate(m)
    assert res.passed, f"reason={res.reason_code} failed={res.failed_gates}"
    aud = res.gate_details.get("__override_applied__")
    assert aud is not None
    assert aud.threshold == {"name": "fl_over:ast_cv_relax"}


def test_ast_cv_override_does_not_rescue_above_0_95():
    m = _m(stat_family="ast", hit_rate=88.0, hit_rate_l20=88.0,
           cv=0.96, tp=60.0, extras={"projection": 5.0}, line=4.5)
    res = get_engine().evaluate(m)
    assert not res.passed
    assert "cv_gate" in res.failed_gates


def test_ast_override_requires_strict_hr_above_85():
    m = _m(stat_family="ast", hit_rate=85.0, hit_rate_l20=85.0,  # NOT > 85
           cv=0.85, tp=60.0, extras={"projection": 5.0}, line=4.5)
    res = get_engine().evaluate(m)
    assert not res.passed


# ─── PTS dominance override ─────────────────────────────────────
def test_pts_dominance_bypasses_tp_failure():
    m = _m(stat_family="pts", hit_rate=80.0, hit_rate_l20=80.0,
           tp=49.0,  # fails FL tp floor 50
           cv=0.40, edge_pct=10.0,
           reference_odds=-110, line=11.5,
           extras={"projection": 12.4, "mu_recency_blend_l20": 18.0})
    res = get_engine().evaluate(m)
    assert res.passed, f"reason={res.reason_code} failed={res.failed_gates}"
    aud = res.gate_details.get("__override_applied__")
    assert aud is not None
    assert aud.threshold == {"name": "fl_over:pts_dominance"}


def test_pts_dominance_bypasses_cv_failure():
    m = _m(stat_family="pts", hit_rate=80.0, hit_rate_l20=80.0,
           tp=60.0, cv=0.80,  # fails FL cv cap 0.75
           edge_pct=10.0, reference_odds=-110, line=11.5,
           extras={"projection": 12.4, "mu_recency_blend_l20": 18.0})
    res = get_engine().evaluate(m)
    assert res.passed
    aud = res.gate_details.get("__override_applied__")
    assert aud is not None and aud.threshold == {"name": "fl_over:pts_dominance"}


def test_pts_dominance_requires_l20_to_line_15x():
    m = _m(stat_family="pts", hit_rate=80.0, hit_rate_l20=80.0,
           tp=49.0, cv=0.40, edge_pct=10.0,
           reference_odds=-110, line=11.5,
           extras={"projection": 12.4, "mu_recency_blend_l20": 16.0})  # ratio 1.39
    res = get_engine().evaluate(m)
    assert not res.passed


def test_pts_dominance_does_not_bypass_edge_failure():
    """PTS dom is TP/CV only — an edge_gate failure stays a hard reject."""
    m = _m(stat_family="pts", hit_rate=90.0, hit_rate_l20=90.0,
           tp=60.0, cv=0.40, edge_pct=2.0,  # fails edge floor 5
           reference_odds=-110, line=11.5,
           extras={"projection": 12.4, "mu_recency_blend_l20": 18.0})
    res = get_engine().evaluate(m)
    assert not res.passed
    assert "edge_gate" in res.failed_gates


# ─── Scope guards ───────────────────────────────────────────────
def test_under_side_never_uses_fl_over_overrides():
    m = _m(side="UNDER", stat_family="threes", hit_rate=80.0, hit_rate_l20=80.0,
           tp=46.0, line=1.5, extras={"projection": 1.86})
    res = get_engine().evaluate(m)
    # UNDER tp_gate uses under_floor=65 against p_model_pct (None here),
    # so it fails for a different reason than the OVER floor of 50.
    assert not res.passed


def test_reb_family_not_eligible_for_fl_over_overrides():
    m = _m(stat_family="reb", hit_rate=95.0, hit_rate_l20=95.0,
           tp=46.0, cv=0.85, edge_pct=10.0,
           extras={"projection": 5.0, "mu_recency_blend_l20": 8.0},
           line=4.5)
    res = get_engine().evaluate(m)
    assert not res.passed, "REB family must not be rescued"


def test_pra_family_not_eligible_for_fl_over_overrides():
    m = _m(stat_family="pra", hit_rate=95.0, hit_rate_l20=95.0,
           tp=46.0, cv=0.85, edge_pct=10.0,
           extras={"projection": 50.0, "mu_recency_blend_l20": 80.0},
           line=42.5)
    res = get_engine().evaluate(m)
    assert not res.passed


def test_pts_ast_family_not_eligible_for_pts_dominance():
    m = _m(stat_family="pts_ast", hit_rate=80.0, hit_rate_l20=80.0,
           tp=49.0, cv=0.80, edge_pct=10.0,
           extras={"projection": 18.0, "mu_recency_blend_l20": 30.0},
           line=15.5)
    res = get_engine().evaluate(m)
    assert not res.passed


def test_safe_haven_never_consults_fl_over_overrides():
    m = _m(tier="safe_haven", reference_odds=-400, stat_family="threes",
           hit_rate=80.0, hit_rate_l20=80.0,
           tp=46.0, cv=0.40, line=1.5, extras={"projection": 1.86})
    res = get_engine().evaluate(m)
    # SH config doesn't include the FL-OVER block; rescues that
    # require it must not fire here.
    if "__override_applied__" in res.gate_details:
        nm = res.gate_details["__override_applied__"].threshold or {}
        assert "fl_over" not in str(nm.get("name", ""))


def test_war_zone_never_consults_fl_over_overrides():
    m = _m(tier="war_zone", reference_odds=+250, stat_family="threes",
           hit_rate=80.0, hit_rate_l20=80.0,
           tp=46.0, cv=0.40, line=1.5,
           extras={"projection": 1.86}, vision_score=92.0,
           tp_source="devig", ceiling_rate=40.0, edge_pct=20.0)
    res = get_engine().evaluate(m)
    if "__override_applied__" in res.gate_details:
        nm = res.gate_details["__override_applied__"].threshold or {}
        assert "fl_over" not in str(nm.get("name", ""))


# ─── Direction gate (OVER-only) ─────────────────────────────────
def test_direction_gate_fails_when_projection_below_line_over():
    m = _m(stat_family="ast", hit_rate=80.0, hit_rate_l20=80.0,
           tp=60.0, cv=0.40, edge_pct=10.0,
           line=4.5, extras={"projection": 4.0})  # proj < line
    res = get_engine().evaluate(m)
    assert not res.passed
    assert "direction_gate" in res.failed_gates


def test_direction_gate_fails_when_projection_equals_line_over():
    """2026-05-15 — Universal direction-gate refactor: equality fails.
    OVER passes iff projection > line (strict); equality is no side-lean
    and must fail (the engine no longer applies a positive cushion).
    """
    m = _m(stat_family="ast", hit_rate=80.0, hit_rate_l20=80.0,
           tp=60.0, cv=0.40, edge_pct=10.0,
           line=4.5, extras={"projection": 4.5})  # proj == line
    res = get_engine().evaluate(m)
    assert not res.passed
    assert "direction_gate" in res.failed_gates


def test_direction_gate_under_side_fails_on_proj_above_line():
    """2026-05-15 — Universal direction-gate refactor: pure side-lean
    semantics. UNDER picks pass iff projection < line; an UNDER with
    projection > line strictly fails. The `actual` payload now
    surfaces `{projection, line, diff}` only — no legacy gap ratio.
    """
    m = _m(side="UNDER", stat_family="ast", hit_rate=80.0,
           hit_rate_l20=80.0, tp=70.0, cv=0.40, edge_pct=10.0,
           line=4.5, extras={"projection": 5.0},  # proj > line — bad for UNDER
           p_model_pct=70.0)
    res = get_engine().evaluate(m)
    assert "direction_gate" in res.failed_gates
    dg = res.gate_details["direction_gate"]
    actual = dg.actual or {}
    assert set(actual.keys()) == {"projection", "line", "diff"}
    assert actual["diff"] == 0.5
    assert dg.comparator == "<"


def test_direction_gate_universal_across_tiers():
    """Post 2026-04-29: direction_gate is a UNIVERSAL OVER-side rule
    applied across SH / FL / WZ. Verify it fires on Safe Haven too —
    a proj<line OVER pick fails direction at any tier.
    """
    sh = _m(tier="safe_haven", reference_odds=-400, stat_family="ast",
            hit_rate=90.0, hit_rate_l20=90.0, cv=0.30,
            line=4.5, extras={"projection": 3.0})  # proj < line
    res = get_engine().evaluate(sh)
    assert "direction_gate" in res.gate_details
    assert "direction_gate" in res.failed_gates
