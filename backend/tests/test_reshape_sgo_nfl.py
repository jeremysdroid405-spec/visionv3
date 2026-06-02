"""Tests for the NFL stat_id → market mapping in
`scripts.sgo.reshape_sgo_to_replay_odds`.

Mirrors the NBA test added on 2026-06-01 (`test_reshape_sgo_nba.py`).
Verifies:
  • Every NFL canonical family the codebase ships with
    (`services/replay/nfl_stat_family_map.NFL_FAMILIES`) has at least
    one SGO stat_id variant that resolves to an Odds-API-canonical
    `player_*` market name.
  • Case-insensitive lookup fallback resolves camelCase variants the
    explicit table doesn't enumerate.
  • Per-league destination block stamps `sport="nfl"` /
    `sport_key="americanfootball_nfl"` / `league="NFL"` correctly.
  • MLB / NBA paths still work (no regression).
"""
from __future__ import annotations
import sys

sys.path.insert(0, "/app/backend")

from services.replay.nfl_stat_family_map import NFL_FAMILIES
from scripts.sgo.reshape_sgo_to_replay_odds import (
    _STAT_ID_TO_MARKET_NFL,
    _STAT_ID_TO_MARKET_BY_LEAGUE,
    _resolve_market,
    _league_dest,
    LEAGUE_CONFIG,
)


# Expected NFL canonical family → Odds API market name. Matches the
# values in `_STAT_ID_TO_MARKET_NFL` for the primary canonical key.
EXPECTED_NFL_MARKETS = {
    "pass_yards":           "player_pass_yds",
    "pass_attempts":        "player_pass_attempts",
    "pass_completions":     "player_pass_completions",
    "pass_touchdowns":      "player_pass_tds",
    "interceptions":        "player_pass_interceptions",
    "rush_yards":           "player_rush_yds",
    "rush_attempts":        "player_rush_attempts",
    "rush_touchdowns":      "player_rush_tds",
    "receptions":           "player_receptions",
    "receiving_yards":      "player_reception_yds",
    "receiving_touchdowns": "player_reception_tds",
    "receiving_targets":    "player_targets",
    "longest_reception":    "player_reception_longest",
    "field_goals_made":     "player_field_goals",
    "extra_points_made":    "player_extra_points",
}


def test_nfl_map_registered_in_league_table():
    assert "NFL" in _STAT_ID_TO_MARKET_BY_LEAGUE
    assert _STAT_ID_TO_MARKET_BY_LEAGUE["NFL"] is _STAT_ID_TO_MARKET_NFL


def test_every_canonical_family_has_a_mapping():
    """Every family in `nfl_stat_family_map.NFL_FAMILIES` must have at
    least one SGO stat_id pointing at a recognisable Odds API market."""
    families_seen: set[str] = set()
    for family, market in EXPECTED_NFL_MARKETS.items():
        # Find at least one SGO stat_id that resolves to this market.
        sgo_ids = [sid for sid, mk in _STAT_ID_TO_MARKET_NFL.items()
                    if mk == market]
        assert sgo_ids, (
            f"family {family!r} has no SGO stat_id variant mapping to "
            f"market {market!r} in _STAT_ID_TO_MARKET_NFL")
        families_seen.add(family)
    # Every canonical family is represented.
    missing = set(NFL_FAMILIES) - families_seen
    assert not missing, (
        f"NFL canonical families with NO reshape mapping: {missing}")


def test_resolve_market_exact_match_snake_case():
    for sgo_id, expected in (
        ("passing_yards",       "player_pass_yds"),
        ("rushing_attempts",    "player_rush_attempts"),
        ("receiving_yards",     "player_reception_yds"),
        ("field_goals_made",    "player_field_goals"),
    ):
        got = _resolve_market({"stat_id": sgo_id}, league="NFL")
        assert got == expected, (
            f"{sgo_id} → expected {expected!r} got {got!r}")


def test_resolve_market_camel_case_variants():
    for sgo_id, expected in (
        ("passingYards",        "player_pass_yds"),
        ("passingTouchdowns",   "player_pass_tds"),
        ("rushingYards",        "player_rush_yds"),
        ("receivingTargets",    "player_targets"),
        ("longestReception",    "player_reception_longest"),
        ("fieldGoalsMade",      "player_field_goals"),
    ):
        got = _resolve_market({"stat_id": sgo_id}, league="NFL")
        assert got == expected, (
            f"{sgo_id} → expected {expected!r} got {got!r}")


def test_resolve_market_case_insensitive_fallback():
    """The case-insensitive resolver should catch arbitrary casing
    variants not enumerated explicitly (e.g. `PassingYards` upper-camel)
    so SGO payload drift doesn't silently drop NFL rows."""
    for sgo_id, expected in (
        ("PassingYards",        "player_pass_yds"),
        ("PASS_YARDS",          "player_pass_yds"),
        ("Rushing_Attempts",    "player_rush_attempts"),
        ("RECEIVINGYARDS",      "player_reception_yds"),
    ):
        got = _resolve_market({"stat_id": sgo_id}, league="NFL")
        assert got == expected, (
            f"{sgo_id} (mixed case) → expected {expected!r} got {got!r}")


def test_resolve_market_unknown_stat_id_returns_none():
    got = _resolve_market(
        {"stat_id": "some_brand_new_stat_we_dont_have_yet"},
        league="NFL",
    )
    assert got is None


def test_resolve_market_falls_through_to_upstream_market_field():
    """When `stat_id` isn't in the map but `market` is set upstream,
    the resolver should still surface it."""
    got = _resolve_market(
        {"stat_id": "unknown_thing", "market": "player_pass_yds"},
        league="NFL",
    )
    assert got == "player_pass_yds"


def test_nfl_league_destination_block():
    cfg = _league_dest("NFL")
    assert cfg["sport"] == "nfl"
    assert cfg["sport_key"] == "americanfootball_nfl"
    # Sanity: LEAGUE_CONFIG carries the same record.
    assert LEAGUE_CONFIG["NFL"]["sport"] == "nfl"
    assert LEAGUE_CONFIG["NFL"]["sport_key"] == "americanfootball_nfl"


def test_nba_mlb_regression():
    """Adding NFL must not break the existing NBA / MLB resolvers."""
    assert (_resolve_market({"stat_id": "points"}, league="NBA")
            == "player_points")
    assert (_resolve_market({"stat_id": "batting_hits"}, league="MLB")
            == "batter_hits")


def test_nfl_never_falls_through_to_mlb_legacy_stat_family():
    """The legacy `stat_family` fallback is MLB-only. Sending an MLB-
    shaped `stat_family` value through the NFL resolver must NOT
    accidentally return an MLB market."""
    got = _resolve_market(
        {"stat_id": "unknown", "stat_family": "Hits"},
        league="NFL",
    )
    # No match by stat_id; no NFL-side stat_family fallback. None.
    assert got is None


if __name__ == "__main__":
    import traceback
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  ✓ {name}")
        except AssertionError as e:
            failures += 1
            print(f"  ✗ {name}: {e}")
            traceback.print_exc(limit=2)
    print()
    if failures:
        print(f"  {failures} test(s) FAILED")
        sys.exit(1)
    print(f"  All NFL reshape tests PASSED")
