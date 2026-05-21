"""Smoke tests for reshape_sgo_to_replay_odds.reshape_row().

The pure transform function is what the user actually cares about: given
one source doc shape, does it produce a valid output row?  These tests
lock the contract so a regression like the prior `stat_family`-only
lookup (which made 100% of rows skip with `no_market`) cannot ship again.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict

import pytest

from scripts.sgo.reshape_sgo_to_replay_odds import reshape_row


NOW = datetime(2026, 5, 21, tzinfo=timezone.utc)


def _base_doc(**overrides: Any) -> Dict[str, Any]:
    """Mirrors a real `sgo_pp_research_core_enriched` row as written by
    build_pp_research_core.py + build_historical_consensus_probabilities.py.
    No `stat_family` field — that's the bug we caught."""
    d: Dict[str, Any] = {
        "league_id":  "MLB",
        "game_date":  "2025-05-01",
        "event_id":   "evt_abc",
        "player_id":  "ply_123",
        "player_name": "Aaron Judge",
        "stat_id":    "batting_hits",
        "side":       "OVER",
        "line":       1.5,
        "period_id":  "game",
        "anchor":     {"book_id": "prizepicks", "price": -119,
                            "snapshot_time": "2025-05-01T10:30:00Z"},
        "books":      [
            {"book_id": "draftkings", "price": -110},
            {"book_id": "fanduel",    "price": -105},
        ],
        "consensus_probability": 0.52,
        "best_book_id":          "fanduel",
        "best_book_probability": 0.55,
    }
    d.update(overrides)
    return d


def test_canonical_mlb_hits_doc_reshapes_to_alt_odds_row():
    row, reason = reshape_row(_base_doc(), NOW)
    assert reason is None
    assert row is not None
    assert row["sport"] == "mlb"
    assert row["sport_key"] == "baseball_mlb"
    assert row["game_date"] == "2025-05-01"
    assert row["event_id"]  == "evt_abc"
    assert row["market"]    == "batter_hits"
    assert row["stat"]      == "batter_hits"
    assert row["side"]      == "OVER"
    assert row["line"]      == 1.5
    # best_book_id="fanduel" → look up its price in books[]
    assert row["book"] == "fanduel"
    assert row["odds"] == -105
    assert row["_odds_source"] == "books[fanduel].price"
    assert row["player_name"] == "Aaron Judge"
    assert row["player_name_normalized"] == "aaron judge"
    assert row["snapshot_iso"] == "2025-05-01T11:00:00Z"


def test_falls_back_to_anchor_price_when_best_book_missing_in_books():
    d = _base_doc(best_book_id="some_other_book")
    row, reason = reshape_row(d, NOW)
    assert reason is None
    assert row["_odds_source"] == "anchor.price"
    assert row["odds"] == -119
    # `book` always echoes best_book_id even if we ultimately took anchor price
    assert row["book"] == "some_other_book"


def test_falls_back_to_anchor_price_when_best_book_id_absent():
    d = _base_doc(); d.pop("best_book_id"); d.pop("books"); d.pop("best_book_probability")
    row, reason = reshape_row(d, NOW)
    assert reason is None
    assert row["_odds_source"] == "anchor.price"
    assert row["odds"] == -119
    assert row["book"] == "prizepicks"  # anchor.book_id


def test_skips_when_no_odds_anywhere():
    d = _base_doc(); d.pop("best_book_id"); d["books"] = []
    d["anchor"] = {"book_id": "prizepicks", "price": None}
    row, reason = reshape_row(d, NOW)
    assert row is None
    assert reason == "no_odds"


@pytest.mark.parametrize("stat_id,expected_market", [
    ("batting_hits",            "batter_hits"),
    ("batting_runs",            "batter_runs_scored"),
    ("batting_RBI",             "batter_rbis"),
    ("batting_rbi",             "batter_rbis"),
    ("batting_homeRuns",        "batter_home_runs"),
    ("batting_totalBases",      "batter_total_bases"),
    ("batting_strikeouts",      "batter_strikeouts"),
    ("batting_basesOnBalls",    "batter_walks"),
    ("batting_stolenBases",     "batter_stolen_bases"),
    ("batting_singles",         "batter_singles"),
    ("batting_doubles",         "batter_doubles"),
    ("batting_triples",         "batter_triples"),
    ("batting_hits+runs+rbi",   "batter_hits_runs_rbis"),
    ("pitcher_strikeouts",      "pitcher_strikeouts"),
    ("pitching_strikeouts",     "pitcher_strikeouts"),
    ("pitching_earnedRuns",     "pitcher_earned_runs"),
    ("pitching_hits",           "pitcher_hits_allowed"),
    ("pitching_basesOnBalls",   "pitcher_walks"),
    ("pitching_outs",           "pitcher_outs"),
])
def test_stat_id_to_market_mapping(stat_id, expected_market):
    """The regression that caused 0 rows: every common MLB stat_id MUST
    map to a market. Without this, the whole pipeline halts at preflight."""
    row, reason = reshape_row(_base_doc(stat_id=stat_id), NOW)
    assert reason is None, f"{stat_id} unexpectedly skipped: {reason}"
    assert row["market"] == expected_market


def test_skips_with_no_market_for_unknown_stat_id():
    row, reason = reshape_row(_base_doc(stat_id="something_we_dont_know"), NOW)
    assert row is None
    assert reason == "no_market"


def test_stat_family_fallback_still_works():
    """If an upstream job DOES write `stat_family` (older shape), we still
    map it. New code prefers `stat_id` but `stat_family` is a fallback."""
    d = _base_doc(); d.pop("stat_id")
    d["stat_family"] = "total_bases"
    row, reason = reshape_row(d, NOW)
    assert reason is None
    assert row["market"] == "batter_total_bases"


@pytest.mark.parametrize("side_input,expected", [
    ("OVER",  "OVER"),
    ("over",  "OVER"),
    ("UNDER", "UNDER"),
    ("under", "UNDER"),
])
def test_side_normalized_uppercase(side_input, expected):
    row, reason = reshape_row(_base_doc(side=side_input), NOW)
    assert reason is None
    assert row["side"] == expected


def test_skips_for_invalid_side():
    row, reason = reshape_row(_base_doc(side="moneyline"), NOW)
    assert row is None
    assert reason == "bad_side"


def test_skips_for_missing_required_fields():
    for missing in ("league_id", "game_date", "event_id", "player_name"):
        d = _base_doc(); d.pop(missing)
        row, reason = reshape_row(d, NOW)
        assert row is None, f"should skip when {missing} is missing"
        # reason maps to the field name (loosely)
        assert missing.replace("_", "") in reason.replace("_", "") or reason in (
            "no_league", "no_game_date", "no_event_id", "no_player_name")


def test_skips_for_bad_line():
    row, reason = reshape_row(_base_doc(line=None), NOW)
    assert row is None and reason == "bad_line"
    row, reason = reshape_row(_base_doc(line="not-a-number"), NOW)
    assert row is None and reason == "bad_line"


def test_unicode_player_name_normalized():
    row, _ = reshape_row(_base_doc(player_name="José Ramírez"), NOW)
    assert row["player_name"] == "José Ramírez"
    assert row["player_name_normalized"] == "jose ramirez"


def test_upsert_key_fields_are_all_present():
    """The destination collection's unique index covers exactly these
    fields. None of them may be absent on a successful reshape."""
    row, _ = reshape_row(_base_doc(), NOW)
    for k in ("sport", "game_date", "event_id", "player_name_normalized",
                "market", "line", "side", "book", "snapshot_iso"):
        assert k in row and row[k] is not None, f"missing upsert-key field: {k}"


def test_doc_level_best_book_odds_preferred_when_present():
    """If a future upstream job writes a doc-level `best_book_odds` we honor
    it directly instead of digging into books[]."""
    d = _base_doc(best_book_odds=-125)
    row, _ = reshape_row(d, NOW)
    assert row["odds"] == -125
    assert row["_odds_source"] == "best_book_odds"
