"""Pure-decision unit tests — `decide_one_sided`.

Hand-built fixtures covering each branch of the decision tree. These
do NOT touch the DB or any production code path; they only verify
that the pure function returns the right verdict for a given
(metrics, policy) pair.

Companion test file (`test_live_doc_agreement.py`) verifies that
the same function agrees with what the LIVE scoring stack
already stamped on `nba_prop_scores` / `mlb_prop_scores`.
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/app/backend")

import pytest

from services.scoring.market_structure_policy import (
    decide_one_sided, policy_for,
)


def _m(**kw):
    """Build a NormalizedMetrics-shaped dict."""
    base = dict(
        tp_source="devig",
        is_alt=False,
        stat_family="hits",
        hit_rate_l20=70.0, hit_rate_l5=80.0,
        edge_pct=3.0, cv=0.4,
    )
    base.update(kw)
    return base


# ── devig is always allowed ────────────────────────────────────────
def test_devig_props_always_pass():
    for sport, tier in (("nba","safe_haven"), ("nba","front_lines"),
                          ("mlb","safe_haven"), ("mlb","war_zone")):
        d = decide_one_sided(_m(tp_source="devig"), policy_for(sport, tier))
        assert d.passes_market_structure is True
        assert d.passes_tp_source is True
        assert d.via_elite_override is False
        assert d.audit_reason == "not_one_sided"


# ── NBA SH: only alt-line one-sided dies ───────────────────────────
def test_nba_sh_alt_one_sided_blocked():
    d = decide_one_sided(
        _m(tp_source="one_sided", is_alt=True),
        policy_for("nba", "safe_haven"),
    )
    assert d.passes_market_structure is False
    assert d.audit_reason == "one_sided_alt_blocked_market_structure"


def test_nba_sh_standard_one_sided_allowed():
    """Standard-line one-sided MUST pass on NBA SH — no tp_source_gate."""
    d = decide_one_sided(
        _m(tp_source="one_sided", is_alt=False),
        policy_for("nba", "safe_haven"),
    )
    assert d.passes_market_structure is True
    assert d.passes_tp_source is True
    assert d.audit_reason == "one_sided_pass_not_blocked"


# ── NBA FL/WZ: all one-sided allowed ──────────────────────────────
def test_nba_fl_and_wz_allow_all_one_sided():
    for tier in ("front_lines", "war_zone"):
        for is_alt in (True, False):
            d = decide_one_sided(
                _m(tp_source="one_sided", is_alt=is_alt),
                policy_for("nba", tier),
            )
            assert d.passes_market_structure is True
            assert d.passes_tp_source is True
            assert d.audit_reason == "one_sided_pass_not_blocked"


# ── MLB SH: hard reject unless elite-binary override fires ─────────
def test_mlb_sh_one_sided_blocked_without_override():
    """Non-elite one-sided is rejected (alt OR standard)."""
    for is_alt in (True, False):
        d = decide_one_sided(
            _m(tp_source="one_sided", is_alt=is_alt,
                stat_family="hits",
                hit_rate_l20=80.0,        # below 90 — override fails
                hit_rate_l5=70.0,
                edge_pct=4.0, cv=0.5),
            policy_for("mlb", "safe_haven"),
        )
        # Either the alt-gate or the tp_source-gate (or both) rejects.
        assert not (d.passes_market_structure and d.passes_tp_source)
        # When is_alt=True, alt rejection fires first (priority order).
        if is_alt:
            assert d.audit_reason == "one_sided_alt_blocked_market_structure"
        else:
            assert d.audit_reason == "one_sided_standard_blocked_tp_source"


def test_mlb_sh_elite_binary_override_rescues_standard_line():
    """Josh-Jung-style HRR with HR_L20=90, HR_L5=80, edge=15.6pp,
    cv=0.5 → rescued. is_alt=False so the alt gate doesn't fire."""
    d = decide_one_sided(
        _m(tp_source="one_sided", is_alt=False,
            stat_family="hits_runs_rbis",
            hit_rate_l20=90.0, hit_rate_l5=80.0,
            edge_pct=15.6, cv=0.5),
        policy_for("mlb", "safe_haven"),
    )
    assert d.passes_market_structure is True
    assert d.passes_tp_source is True
    assert d.via_elite_override is True
    assert d.audit_reason == "one_sided_elite_binary_override"


def test_mlb_sh_override_does_not_rescue_disallowed_family():
    """Override allow-list excludes pitcher_strikeouts even with
    elite stats."""
    d = decide_one_sided(
        _m(tp_source="one_sided", is_alt=False,
            stat_family="pitcher_strikeouts",
            hit_rate_l20=95.0, hit_rate_l5=90.0,
            edge_pct=8.0, cv=0.4),
        policy_for("mlb", "safe_haven"),
    )
    assert d.passes_tp_source is False
    assert d.via_elite_override is False
    assert d.audit_reason == "one_sided_standard_blocked_tp_source"


def test_mlb_sh_override_alt_priority():
    """When is_alt=True, the alt-rejection reason wins even if the
    elite override would have fired for the standard-line gate."""
    d = decide_one_sided(
        _m(tp_source="one_sided", is_alt=True,
            stat_family="hits", hit_rate_l20=95.0, hit_rate_l5=90.0,
            edge_pct=10.0, cv=0.3),
        policy_for("mlb", "safe_haven"),
    )
    assert d.passes_market_structure is False
    assert d.audit_reason == "one_sided_alt_blocked_market_structure"


# ── MLB FL/WZ: all one-sided allowed ──────────────────────────────
def test_mlb_fl_and_wz_allow_all_one_sided():
    for tier in ("front_lines", "war_zone"):
        for is_alt in (True, False):
            d = decide_one_sided(
                _m(tp_source="one_sided", is_alt=is_alt),
                policy_for("mlb", tier),
            )
            assert d.passes_market_structure is True
            assert d.passes_tp_source is True


# ── Edge cases — override threshold sweep ──────────────────────────
@pytest.mark.parametrize("hr20,hr5,edge,cv,expected_override", [
    (90.0, 80.0, 5.0, 0.70, True),    # exactly at thresholds
    (89.9, 80.0, 5.0, 0.70, False),   # hr20 0.1pp below
    (90.0, 79.9, 5.0, 0.70, False),   # hr5 0.1pp below
    (90.0, 80.0, 4.9, 0.70, False),   # edge 0.1pp below
    (90.0, 80.0, 5.0, 0.71, False),   # cv 0.01 above
    (None, 80.0, 5.0, 0.70, False),   # missing hr20
    (90.0, None, 5.0, 0.70, False),   # missing hr5
])
def test_mlb_sh_override_threshold_strictness(
    hr20, hr5, edge, cv, expected_override,
):
    d = decide_one_sided(
        _m(tp_source="one_sided", is_alt=False, stat_family="hits",
            hit_rate_l20=hr20, hit_rate_l5=hr5, edge_pct=edge, cv=cv),
        policy_for("mlb", "safe_haven"),
    )
    if expected_override:
        assert d.via_elite_override is True
        assert d.passes_tp_source is True
    else:
        assert d.via_elite_override is False
        assert d.passes_tp_source is False


# ── Unknown sport/tier — permissive fallback ───────────────────────
def test_unknown_sport_tier_falls_open_permissively():
    """Module is read-only today; unknown (sport, tier) lookups
    must return a permissive policy (no rejection) so production
    behaviour cannot regress on a future tier addition."""
    pol = policy_for("nhl", "phantom_tier")
    d = decide_one_sided(
        _m(tp_source="one_sided", is_alt=True), pol,
    )
    assert d.passes_market_structure is True
    assert d.passes_tp_source is True
