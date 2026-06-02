"""NBA league-aware reshape tests.

Covers the 2026-06-02 fix that made
`scripts/sgo/reshape_sgo_to_replay_odds.py` league-aware. Validates
that NBA stat_id → market mapping, sport/sport_key/league tagging, and
NBA-specific market exclusions all behave correctly.
"""
from __future__ import annotations
from datetime import datetime, timezone

import pytest

from scripts.sgo.reshape_sgo_to_replay_odds import (
    _STAT_ID_TO_MARKET_NBA,
    _STAT_ID_TO_MARKET_MLB,
    _STAT_ID_TO_MARKET_BY_LEAGUE,
    LEAGUE_CONFIG,
    _resolve_market,
    _league_dest,
    reshape_row,
    reshape_rows,
)

NOW = datetime(2026, 6, 2, tzinfo=timezone.utc)


def _nba_doc(**over):
    """Minimal NBA source doc shaped like sgo_pp_research_core_enriched."""
    base = {
        "league_id":    "NBA",
        "game_date":    "2026-02-01",
        "event_id":     "evt_nba_001",
        "player_id":    "lebron-james",
        "player_name":  "LeBron James",
        "stat_id":      "points",
        "stat_family":  None,
        "side":         "OVER",
        "line":         25.5,
        "books": [
            {"book_id": "draftkings", "price": -110},
            {"book_id": "fanduel",    "price": -115},
            {"book_id": "prizepicks", "price": -100},
        ],
        "best_book_id": "fanduel",
        "best_price":   -115,
        "commence_time": "2026-02-01T19:30:00Z",
        "snapshot_iso": "2026-02-01T11:00:00Z",
    }
    base.update(over)
    return base


# ─── league_dest / config sanity ────────────────────────────────────
class TestLeagueConfig:
    def test_mlb_dest(self):
        assert _league_dest("MLB") == {
            "sport": "mlb", "sport_key": "baseball_mlb"}

    def test_nba_dest(self):
        assert _league_dest("NBA") == {
            "sport": "nba", "sport_key": "basketball_nba"}

    def test_nfl_dest(self):
        assert _league_dest("NFL")["sport"] == "nfl"

    def test_case_insensitive(self):
        assert _league_dest("nba")["sport"] == "nba"

    def test_unknown_league_raises(self):
        with pytest.raises(ValueError):
            _league_dest("MLS")


# ─── NBA stat_id mapping ────────────────────────────────────────────
class TestNbaStatIdMap:
    @pytest.mark.parametrize("stat_id,market", [
        ("points",                          "player_points"),
        ("rebounds",                        "player_rebounds"),
        ("assists",                         "player_assists"),
        ("points+rebounds+assists",         "player_points_rebounds_assists"),
        ("points+rebounds",                 "player_points_rebounds"),
        ("points+assists",                  "player_points_assists"),
        ("rebounds+assists",                "player_rebounds_assists"),
        ("threePointersMade",               "player_threes"),
        ("steals",                          "player_steals"),
        ("blocks",                          "player_blocks"),
        ("blocks+steals",                   "player_blocks_steals"),
        ("turnovers",                       "player_turnovers"),
        ("fantasyScore",                    "fantasy_score"),
    ])
    def test_resolves_each_nba_stat_id(self, stat_id, market):
        d = {"stat_id": stat_id}
        assert _resolve_market(d, league="NBA") == market

    def test_unknown_nba_stat_returns_none(self):
        assert _resolve_market({"stat_id": "deflections"}, league="NBA") is None


# ─── league isolation — MLB map MUST NOT leak into NBA resolution ──
class TestLeagueIsolation:
    def test_nba_does_not_inherit_mlb_stat_id(self):
        # `batting_hits` is in the MLB map. Resolving it as NBA should
        # return None — we are not silently coercing.
        assert _resolve_market(
            {"stat_id": "batting_hits"}, league="NBA") is None

    def test_mlb_does_not_inherit_nba_stat_id(self):
        assert _resolve_market(
            {"stat_id": "threePointersMade"}, league="MLB") is None

    def test_stat_family_fallback_is_mlb_only(self):
        # Even with a stat_family that the legacy MLB fallback knows,
        # NBA resolution should not use it.
        d = {"stat_family": "hits"}    # MLB fallback would say batter_hits
        assert _resolve_market(d, league="MLB") == "batter_hits"
        assert _resolve_market(d, league="NBA") is None

    def test_nba_overlap_with_mlb_fantasyScore(self):
        # `fantasyScore` happens to be present in both maps. Each map
        # owns its own mapping. Currently both return "fantasy_score" —
        # this test just pins the contract so a future change is loud.
        assert _resolve_market(
            {"stat_id": "fantasyScore"}, league="NBA") == "fantasy_score"
        assert _resolve_market(
            {"stat_id": "fantasyScore"}, league="MLB") == "fantasy_score"


# ─── End-to-end NBA reshape ────────────────────────────────────────
class TestNbaReshape:
    def test_basic_nba_reshape_tags_sport_correctly(self):
        d = _nba_doc()
        rows, reason = reshape_rows(d, NOW, league="NBA")
        assert reason is None
        assert len(rows) >= 1
        r = rows[0]
        assert r["sport"]     == "nba"
        assert r["sport_key"] == "basketball_nba"
        assert r["league"]    == "NBA"
        assert r["market"]    == "player_points"
        assert r["side"]      == "OVER"
        assert r["line"]      == 25.5

    def test_nba_reshape_emits_one_row_per_book(self):
        d = _nba_doc()
        rows, _ = reshape_rows(d, NOW, league="NBA")
        # 3 books in input — current contract: emit one row per book
        # in books[] (the dedup/exclusion of reference-only books is
        # handled at a later stage by the optimizer, not here).
        books_emitted = {r["book"] for r in rows}
        assert books_emitted == {"draftkings", "fanduel", "prizepicks"}
        assert len(rows) == 3

    def test_nba_pra_combo_stat_resolves(self):
        d = _nba_doc(stat_id="points+rebounds+assists", line=42.5)
        rows, _ = reshape_rows(d, NOW, league="NBA")
        assert rows[0]["market"] == "player_points_rebounds_assists"
        assert rows[0]["line"]   == 42.5

    def test_nba_bad_side_skipped(self):
        d = _nba_doc(side="YES")    # spec rejects yes/no for player props
        rows, reason = reshape_rows(d, NOW, league="NBA")
        assert rows == []
        assert reason == "bad_side"

    def test_nba_home_side_skipped(self):
        d = _nba_doc(side="HOME")
        rows, reason = reshape_rows(d, NOW, league="NBA")
        assert reason == "bad_side"

    def test_nba_unmapped_stat_skipped_with_reason(self):
        d = _nba_doc(stat_id="deflections")
        rows, reason = reshape_rows(d, NOW, league="NBA")
        assert rows == []
        assert reason == "no_market"

    def test_default_league_still_mlb(self):
        """Backwards-compat: a caller that omits league= must still
        behave like MLB so existing pipelines don't regress."""
        d = {
            "league_id":    "MLB",
            "game_date":    "2025-08-01",
            "event_id":     "evt_mlb_001",
            "player_id":    "aaron-judge",
            "player_name":  "Aaron Judge",
            "stat_id":      "batting_hits",
            "side":         "OVER",
            "line":         1.5,
            "books":        [{"book_id": "draftkings", "price": -110}],
            "best_book_id": "draftkings",
            "best_price":   -110,
        }
        rows, reason = reshape_rows(d, NOW)    # no league kwarg
        assert reason is None
        assert rows[0]["sport"]  == "mlb"
        assert rows[0]["market"] == "batter_hits"

    def test_reshape_row_single_legacy_signature(self):
        """The legacy reshape_row (singular) must accept league kwarg."""
        d = _nba_doc()
        row, _ = reshape_row(d, NOW, league="NBA")
        assert row is not None
        assert row["sport"] == "nba"
