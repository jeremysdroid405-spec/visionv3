"""
Regression: BetMGM must be wired into the NBA sharp-price lookup merge
so its quotes populate alongside DK/FD/BOL. 2026-04-22 follow-up to
"what about BetMGM?" — the first pass silently dropped every MGM
outcome because `bm_key not in [...]` excluded it.
"""
from __future__ import annotations

from tests.test_odds_sync_standard_market import _build_lookup


def test_betmgm_standard_line_populates_via_alt_lookup():
    """MGM only quotes the STANDARD market at a line — alt-market lookup
    must surface it."""
    sharp = [{
        "bookmakers": [{
            "key": "betmgm",
            "markets": [{
                "key": "player_points",
                "outcomes": [
                    {"description": "Tyrese Haliburton", "name": "Over",
                     "point": 21.5, "price": -119},
                ],
            }],
        }],
    }]
    lookup = _build_lookup(sharp)
    alt_key = ("Tyrese Haliburton", "player_points_alternate", 21.5, "over")
    assert alt_key in lookup
    assert lookup[alt_key]["betmgm_price"] == -119


def test_betmgm_alt_line_populates_on_native_key():
    sharp = [{
        "bookmakers": [{
            "key": "betmgm",
            "markets": [{
                "key": "player_rebounds_alternate",
                "outcomes": [
                    {"description": "Anthony Davis", "name": "Over",
                     "point": 12.5, "price": +165},
                ],
            }],
        }],
    }]
    lookup = _build_lookup(sharp)
    k = ("Anthony Davis", "player_rebounds_alternate", 12.5, "over")
    assert lookup[k]["betmgm_price"] == 165


def test_all_four_books_fill_all_four_slots_at_same_line():
    """DK/FD/BOL/MGM all quoting the same alt line — every slot fills."""
    sharp = [{
        "bookmakers": [
            {"key": "draftkings",
             "markets": [{"key": "player_points_alternate",
                          "outcomes": [{"description": "Luka Doncic",
                                        "name": "Over", "point": 30.5,
                                        "price": -210}]}]},
            {"key": "fanduel",
             "markets": [{"key": "player_points_alternate",
                          "outcomes": [{"description": "Luka Doncic",
                                        "name": "Over", "point": 30.5,
                                        "price": -220}]}]},
            {"key": "betonlineag",
             "markets": [{"key": "player_points_alternate",
                          "outcomes": [{"description": "Luka Doncic",
                                        "name": "Over", "point": 30.5,
                                        "price": -200}]}]},
            {"key": "betmgm",
             "markets": [{"key": "player_points_alternate",
                          "outcomes": [{"description": "Luka Doncic",
                                        "name": "Over", "point": 30.5,
                                        "price": -215}]}]},
        ],
    }]
    lookup = _build_lookup(sharp)
    k = ("Luka Doncic", "player_points_alternate", 30.5, "over")
    entry = lookup[k]
    assert entry["draftkings_price"] == -210
    assert entry["fanduel_price"] == -220
    assert entry["betonline_price"] == -200
    assert entry["betmgm_price"] == -215
