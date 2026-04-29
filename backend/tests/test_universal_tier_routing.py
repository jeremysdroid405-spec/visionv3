"""
Universal Tier Routing — Final Spec Validation
==============================================

Validates the 2026-04-29 routing rules (cascade REMOVED):

    Safe Haven  : ref_odds <= -300
    Front Lines : -299 <= ref_odds <= +149
    War Zone    : ref_odds >= +150

Hard rule: each pick is evaluated ONLY within its routed tier. Failing
the routed tier's gate block → REJECTED (tier="unqualified"). NO
fallback to a lower-strictness tier.

Spec validation cases:
  1. Paul George OVER PTS 11.5 at -286 → routes to Front Lines
  2. Props at -300 or lower → Safe Haven
  3. Props in [-299, +149] → Front Lines
  4. Props at +150 or higher → War Zone
  5. A pick that fails its routed tier's gates is unqualified, NOT
     moved to another tier
"""
from __future__ import annotations

import pytest

from services.scoring.gates.thresholds import (
    UNIVERSAL_SAFE_HAVEN_MAX,
    UNIVERSAL_WAR_ZONE_MIN,
    resolve_target_tier,
)


# ─── Boundary lockdown ───────────────────────────────────────────────
def test_boundaries_match_spec():
    """The constants ARE the contract. Changing them must always come
    with a CHANGELOG bump and a new validation test."""
    assert UNIVERSAL_SAFE_HAVEN_MAX == -300
    assert UNIVERSAL_WAR_ZONE_MIN == 150


# ─── Spec case 1 — Paul George -286 routes to Front Lines ────────────
def test_pg_pts_11_5_at_minus_286_routes_to_front_lines():
    assert resolve_target_tier("nba", -286) == "front_lines"


# ─── Spec case 2 — −300 or lower → Safe Haven ────────────────────────
def test_minus_300_routes_to_safe_haven():
    assert resolve_target_tier("nba", -300) == "safe_haven"


def test_minus_500_routes_to_safe_haven():
    assert resolve_target_tier("nba", -500) == "safe_haven"


def test_minus_2000_routes_to_safe_haven():
    assert resolve_target_tier("nba", -2000) == "safe_haven"


# ─── Spec case 3 — [−299, +149] → Front Lines ────────────────────────
@pytest.mark.parametrize("odds", [-299, -250, -200, -150, -110, +100, +120, +149])
def test_front_lines_band(odds):
    assert resolve_target_tier("nba", odds) == "front_lines"


# ─── Spec case 4 — +150 or higher → War Zone ─────────────────────────
def test_plus_150_routes_to_war_zone():
    assert resolve_target_tier("nba", 150) == "war_zone"


def test_plus_500_routes_to_war_zone():
    assert resolve_target_tier("nba", 500) == "war_zone"


# ─── Universality across sports ──────────────────────────────────────
@pytest.mark.parametrize("sport", ["nba", "mlb", "nfl", "nhl", "wnba"])
def test_routing_is_universal_across_sports(sport):
    assert resolve_target_tier(sport, -300) == "safe_haven"
    assert resolve_target_tier(sport, -150) == "front_lines"
    assert resolve_target_tier(sport, +200) == "war_zone"


# ─── None ref_odds returns None (no implicit bucket) ─────────────────
def test_none_ref_odds_returns_none():
    assert resolve_target_tier("nba", None) is None


def _call_compute_tier(*, sport="nba", ref_odds, cv, hit_rate, edge_pct, tp,
                       p_model=None, side="OVER", stat="PTS", line=11.5,
                       ref_book="dk"):
    from services.scoring.scoring_stack import compute_tier
    prop = {
        "stat_type": stat, "line": line,
        "recommendation": side, "direction": side,
        "book_count": 3,
    }
    return compute_tier(
        prop=prop, sport=sport,
        cv=cv, hit_rate=hit_rate, edge_pct=edge_pct, tp=tp,
        ceiling_rate=None, p_model=p_model,
        avg_hit_margin=None, avg_miss_margin=None,
        dk_layer={"line": line, "odds": ref_odds, "side": side, "decimal": None}
                  if ref_book == "dk" else None,
        mgm_layer=None,
        fd_layer={"line": line, "odds": ref_odds, "side": side, "decimal": None}
                  if ref_book == "fd" else None,
        bol_layer=None,
    )


# ─── No-cascade hard contract ────────────────────────────────────────
def test_sh_routed_failing_sh_is_unqualified_not_fl():
    """A Safe-Haven-routed pick (odds ≤ -300) failing SH gates must NOT
    leak into Front Lines. Cascade is removed."""
    res = _call_compute_tier(
        ref_odds=-400, cv=0.30, hit_rate=80.0, edge_pct=8.0, tp=68.0,
        p_model=0.76,
    )
    assert res["routed_tier"] == "safe_haven"
    assert res["tier"] in ("safe_haven", "unqualified")
    assert res["tier"] != "front_lines"
    assert res["tier"] != "war_zone"
    assert "tier_cascade_chain" not in res


def test_fl_routed_failing_fl_is_unqualified_not_wz():
    """A Front-Lines-routed pick failing FL gates must NOT leak into
    War Zone."""
    res = _call_compute_tier(
        ref_odds=-200, cv=0.95, hit_rate=10.0, edge_pct=-30.0, tp=40.0,
        p_model=0.20,
    )
    assert res["routed_tier"] == "front_lines"
    assert res["tier"] in ("front_lines", "unqualified")
    assert res["tier"] != "war_zone"
    assert res["tier"] != "safe_haven"
    assert "tier_cascade_chain" not in res


def test_war_zone_routed_failing_is_unqualified():
    res = _call_compute_tier(
        ref_odds=+250, cv=0.95, hit_rate=10.0, edge_pct=-30.0, tp=40.0,
        p_model=0.20,
    )
    assert res["routed_tier"] == "war_zone"
    assert res["tier"] in ("war_zone", "unqualified")
    assert "tier_cascade_chain" not in res


def test_no_promotion_from_fl_to_sh():
    """A pick at -200 (FL band) with elite metrics still cannot land
    in Safe Haven — routing is locked by odds, not by quality."""
    res = _call_compute_tier(
        ref_odds=-200, cv=0.20, hit_rate=95.0, edge_pct=15.0, tp=60.0,
        p_model=0.80,
    )
    assert res["routed_tier"] == "front_lines"
    assert res["tier"] != "safe_haven"


def test_pick_in_fl_band_does_not_appear_in_war_zone():
    """Validation rule from spec: 'Picks in Front Lines do NOT appear
    in War Zone.' Sweep across the FL band confirms tier ∈
    {front_lines, unqualified} only."""
    for odds in (-299, -250, -150, -110, +100, +120, +149):
        res = _call_compute_tier(
            ref_odds=odds, cv=0.40, hit_rate=70.0, edge_pct=10.0, tp=55.0,
            p_model=0.65,
        )
        assert res["routed_tier"] == "front_lines", odds
        assert res["tier"] != "war_zone", (odds, res["tier"])
        assert res["tier"] != "safe_haven", (odds, res["tier"])


def test_war_zone_only_contains_plus_150_or_higher():
    """Validation rule from spec: 'War Zone only contains +150 or
    higher odds.'"""
    for odds in (+150, +200, +500, +1000):
        res = _call_compute_tier(
            ref_odds=odds, cv=0.40, hit_rate=70.0, edge_pct=10.0, tp=40.0,
            p_model=0.55,
        )
        assert res["routed_tier"] == "war_zone", odds
        assert res["tier"] in ("war_zone", "unqualified"), (odds, res["tier"])
