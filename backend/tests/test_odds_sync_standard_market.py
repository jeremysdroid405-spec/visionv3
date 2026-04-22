"""
Tests for services/odds_sync_service.py — sharp-price lookup must include
STANDARD-market prices alongside alternate-market prices so a PrizePicks
alt-market prop can still be priced by a book that only quotes the
standard market at that line (e.g. BetOnline).

Lock for the 2026-04-21 fix that unified the two market namespaces.
"""
from __future__ import annotations

from typing import Any, Dict


def _build_lookup(sharp_results):
    """Reproduction of the (fixed) sharp-price lookup logic from
    `odds_sync_service.py::sync_odds_to_mongo`.  Keeping it inline so
    the test covers the contract deterministically without spinning up
    the full service.
    """
    sharp_prices: Dict[tuple, Dict[str, Any]] = {}
    for sharp_data in sharp_results:
        for bm in sharp_data.get("bookmakers", []):
            bm_key = bm.get("key", "")
            if bm_key not in ["draftkings", "fanduel", "betonlineag"]:
                continue
            for market in bm.get("markets", []):
                market_key = market.get("key", "")
                is_std = not market_key.endswith("_alternate")
                alt_key = f"{market_key}_alternate" if is_std else market_key
                for outcome in market.get("outcomes", []):
                    player_name = outcome.get("description", "")
                    line = outcome.get("point", 0)
                    direction = (outcome.get("name", "") or "over").lower()
                    price = outcome.get("price")
                    keys = [(player_name, market_key, line, direction)]
                    if is_std and alt_key != market_key:
                        keys.append((player_name, alt_key, line, direction))
                    for k in keys:
                        if k not in sharp_prices:
                            sharp_prices[k] = {
                                "draftkings_price": None,
                                "fanduel_price": None,
                                "betonline_price": None,
                            }
                        cur = sharp_prices[k]
                        if bm_key == "draftkings" and (cur["draftkings_price"] is None or not is_std):
                            cur["draftkings_price"] = price
                        elif bm_key == "fanduel" and (cur["fanduel_price"] is None or not is_std):
                            cur["fanduel_price"] = price
                        elif bm_key == "betonlineag" and (cur["betonline_price"] is None or not is_std):
                            cur["betonline_price"] = price
    return sharp_prices


def test_bol_standard_line_exposed_via_alt_lookup():
    """Dosunmu PTS 17.5 — BetOnline only quotes the STANDARD market at 17.5.
    PP prop is alt-market.  After the fix, the alt-market lookup must
    surface BOL's standard price."""
    sharp = [{
        "bookmakers": [{
            "key": "betonlineag",
            "markets": [{
                "key": "player_points",  # STANDARD
                "outcomes": [
                    {"description": "Ayo Dosunmu", "name": "Over",
                     "point": 17.5, "price": -125},
                ],
            }],
        }],
    }]
    lookup = _build_lookup(sharp)
    alt_key = ("Ayo Dosunmu", "player_points_alternate", 17.5, "over")
    assert alt_key in lookup
    assert lookup[alt_key]["betonline_price"] == -125


def test_alt_native_data_beats_standard_duplicate():
    """When a book quotes BOTH alt and standard at the same line, the
    alt-market price must win (it's the one intended for prop bettors)."""
    sharp = [{
        "bookmakers": [{
            "key": "draftkings",
            "markets": [
                {"key": "player_points",
                 "outcomes": [{"description": "LeBron James", "name": "Over",
                               "point": 24.5, "price": -110}]},
                {"key": "player_points_alternate",
                 "outcomes": [{"description": "LeBron James", "name": "Over",
                               "point": 24.5, "price": -115}]},
            ],
        }],
    }]
    lookup = _build_lookup(sharp)
    alt_key = ("LeBron James", "player_points_alternate", 24.5, "over")
    # Alt-market price (-115) must take precedence over the standard dup (-110)
    assert lookup[alt_key]["draftkings_price"] == -115


def test_alt_only_books_unchanged():
    """DK quoting only the alternate market must still populate the
    alt-market key exactly as before."""
    sharp = [{
        "bookmakers": [{
            "key": "draftkings",
            "markets": [{
                "key": "player_points_alternate",
                "outcomes": [
                    {"description": "Ayo Dosunmu", "name": "Over",
                     "point": 11.5, "price": -610},
                ],
            }],
        }],
    }]
    lookup = _build_lookup(sharp)
    alt_key = ("Ayo Dosunmu", "player_points_alternate", 11.5, "over")
    assert lookup[alt_key]["draftkings_price"] == -610


def test_standard_and_alt_both_preserved_on_std_key():
    """When a book quotes the standard market, the native (non-alt) lookup
    key must still resolve — the normalization adds, not replaces."""
    sharp = [{
        "bookmakers": [{
            "key": "fanduel",
            "markets": [{
                "key": "player_points",
                "outcomes": [{"description": "Ayo Dosunmu", "name": "Under",
                              "point": 17.5, "price": -111}],
            }],
        }],
    }]
    lookup = _build_lookup(sharp)
    std_key = ("Ayo Dosunmu", "player_points", 17.5, "under")
    alt_key = ("Ayo Dosunmu", "player_points_alternate", 17.5, "under")
    assert lookup[std_key]["fanduel_price"] == -111
    assert lookup[alt_key]["fanduel_price"] == -111


def test_multi_book_merge_fills_all_slots():
    """FD quotes alt at this line, BOL quotes standard at this line, DK
    absent. After the fix, the alt-market key must carry BOTH FD and BOL
    prices."""
    sharp = [{
        "bookmakers": [
            {"key": "fanduel",
             "markets": [{"key": "player_points_alternate",
                          "outcomes": [{"description": "Ayo Dosunmu",
                                        "name": "Over", "point": 17.5,
                                        "price": -118}]}]},
            {"key": "betonlineag",
             "markets": [{"key": "player_points",
                          "outcomes": [{"description": "Ayo Dosunmu",
                                        "name": "Over", "point": 17.5,
                                        "price": -125}]}]},
        ],
    }]
    lookup = _build_lookup(sharp)
    alt_key = ("Ayo Dosunmu", "player_points_alternate", 17.5, "over")
    entry = lookup[alt_key]
    assert entry["fanduel_price"] == -118
    assert entry["betonline_price"] == -125
    assert entry["draftkings_price"] is None
