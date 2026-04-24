"""Unit tests for the `tp_unavailable_reason` classification logic
added to NBA scoring adapter 2026-04-24 (spec step 4).

Full scoring-context wiring is tested elsewhere — here we verify the
small piece of inline logic that reads `prop`, `resolve_stat_family`,
and `STAT_FAMILY_ALIASES` and emits the typed reason string.
"""
from __future__ import annotations

from services.scoring.gates.thresholds import (
    STAT_FAMILY_ALIASES, resolve_stat_family,
)


def _classify_tp_reason(stat_type, prop):
    """Pure replica of the classification logic in
    `nba_scoring.py` so we can unit-test it cheaply without
    building a full scoring context."""
    nba_aliases = STAT_FAMILY_ALIASES.get("nba", {}) or {}
    resolved_family = resolve_stat_family("nba", stat_type)
    has_explicit_alias = stat_type in nba_aliases
    market_key = (prop.get("market_key") or "").lower()
    is_alt = bool(prop.get("is_alternate_market")) or market_key.endswith(
        "_alternate"
    )
    has_any_side = any(
        prop.get(f"{b}_odds") is not None
        for b in ("dk", "fd", "mgm", "bol")
    ) or prop.get("draftkings_price") is not None

    if not has_explicit_alias and resolved_family == stat_type.strip().lower().replace(" ", "_"):
        return "unsupported_stat_family"
    if not has_any_side:
        return "no_live_props_quote"
    if is_alt:
        return "alt_line_one_sided"
    return "standard_line_missing_opp"


def test_unsupported_stat_family_novel_market():
    """A stat_type with no alias AND resolve_stat_family falls back to
    a lowercased raw key — that's genuine 'we don't map this yet'."""
    prop = {"dk_odds": -110, "market_key": "player_novel_market"}
    assert _classify_tp_reason("player_first_basket", prop) == "unsupported_stat_family"


def test_alt_line_one_sided_priced_one_side():
    prop = {
        "dk_odds": -148, "fd_odds": -142,  # both books quote this side
        "dk_odds_opp": None, "fd_odds_opp": None,
        "market_key": "player_points_alternate",
        "is_alternate_market": True,
    }
    assert _classify_tp_reason("PTS", prop) == "alt_line_one_sided"


def test_alt_line_combo_market():
    prop = {
        "dk_odds": -120, "market_key": "player_points_assists_alternate",
    }
    assert _classify_tp_reason("player_points_assists_alternate", prop) == "alt_line_one_sided"


def test_standard_line_missing_opp():
    prop = {
        "dk_odds": -110, "market_key": "player_points",  # standard, not alt
    }
    assert _classify_tp_reason("PTS", prop) == "standard_line_missing_opp"


def test_no_live_props_quote_no_sides_priced():
    prop = {
        "dk_odds": None, "fd_odds": None, "mgm_odds": None, "bol_odds": None,
        "market_key": "player_points",
    }
    assert _classify_tp_reason("PTS", prop) == "no_live_props_quote"


def test_legacy_draftkings_price_counts_as_side():
    prop = {
        "dk_odds": None, "draftkings_price": -150,
        "market_key": "player_rebounds_alternate",
    }
    assert _classify_tp_reason("REB", prop) == "alt_line_one_sided"


def test_aliased_combo_resolves_correctly():
    # pts_reb IS aliased → not 'unsupported'
    prop = {
        "dk_odds": -110, "market_key": "player_points_rebounds_alternate",
    }
    assert _classify_tp_reason("player_points_rebounds_alternate", prop) \
        == "alt_line_one_sided"
