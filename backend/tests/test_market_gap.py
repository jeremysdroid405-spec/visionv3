"""
Unit tests for services/market_gap.py (sport-agnostic disagreement signal).
"""
from __future__ import annotations

import pytest

from services import market_gap as mg


def test_single_book_returns_none_level():
    pick = {"draftkings_price": -150}
    out = mg.compute_market_gap(pick)
    assert out["market_gap_level"] == "none"
    assert out["market_books_compared"] == 1
    assert out["market_gap_points"] == 0
    assert out["market_best_book"] == "DK"  # short label, never raw key
    assert out["market_best_price"] == -150
    assert out["market_price_map"] == {"DK": -150}


def test_no_books_returns_none_level():
    out = mg.compute_market_gap({})
    assert out["market_gap_level"] == "none"
    assert out["market_books_compared"] == 0
    assert out["market_best_book"] is None


def test_gap_below_medium_is_none():
    # 116 - 122 = 6 -> far below 50 threshold
    pick = {"draftkings_price": -116, "fanduel_price": -122}
    out = mg.compute_market_gap(pick)
    assert out["market_gap_level"] == "none"
    assert out["market_gap_points"] == 6


def test_gap_medium_range():
    # -234 vs -285 = 51 -> medium
    pick = {"draftkings_price": -234, "fanduel_price": -285}
    out = mg.compute_market_gap(pick)
    assert out["market_gap_level"] == "medium"
    assert out["market_gap_points"] == 51
    assert out["market_best_book"] == "DK"
    assert out["market_best_price"] == -234


def test_gap_high_threshold():
    # -234 vs -350 = 116 -> high
    pick = {"draftkings_price": -234, "fanduel_price": -350}
    out = mg.compute_market_gap(pick)
    assert out["market_gap_level"] == "high"
    assert out["market_gap_points"] == 116
    assert out["market_best_book"] == "DK"
    assert out["market_price_map"] == {"DK": -234, "FD": -350}


def test_mixed_sign_prices_best_is_highest_numeric():
    # +150 vs -100 -> gap = 250, best = +150 (FD)
    pick = {"draftkings_price": -100, "fanduel_price": 150}
    out = mg.compute_market_gap(pick)
    assert out["market_gap_points"] == 250
    assert out["market_best_book"] == "FD"
    assert out["market_best_price"] == 150
    assert out["market_gap_level"] == "high"


def test_three_books_picks_extremes():
    pick = {
        "draftkings_price": -222,
        "fanduel_price": -230,
        "betonline_price": -192,
    }
    out = mg.compute_market_gap(pick)
    assert out["market_books_compared"] == 3
    # Best (highest numeric) = -192 (BOL); worst = -230 (FD); gap = 38
    assert out["market_best_book"] == "BOL"
    assert out["market_gap_points"] == 38
    assert out["market_gap_level"] == "none"  # below 50


def test_sharp_market_fallback_used_when_top_level_missing():
    pick = {
        "sharp_market": {
            "draftkings_price": -234,
            "fanduel_price": -350,
        }
    }
    out = mg.compute_market_gap(pick)
    assert out["market_gap_level"] == "high"
    assert out["market_gap_points"] == 116


def test_annotate_list_in_place():
    picks = [
        {"draftkings_price": -150, "fanduel_price": -155},   # none
        {"draftkings_price": -200, "fanduel_price": -260},   # medium
        {"draftkings_price": -120, "fanduel_price": -250},   # high
        {"draftkings_price": None},                           # none
    ]
    mg.annotate_market_gap(picks)
    assert [p["market_gap_level"] for p in picks] == ["none", "medium", "high", "none"]


def test_malformed_pick_never_raises():
    picks = [{"draftkings_price": "not-a-number"}, "garbage", None, 42]  # noqa: E501
    # Must not raise
    result = mg.annotate_market_gap(picks)
    assert result is picks
    assert picks[0].get("market_gap_level") == "none"


def test_thresholds_are_configurable():
    pick = {"draftkings_price": -200, "fanduel_price": -230}  # gap 30
    out = mg.compute_market_gap(pick, medium_threshold=25, high_threshold=100)
    assert out["market_gap_level"] == "medium"
    out2 = mg.compute_market_gap(pick, medium_threshold=25, high_threshold=30)
    assert out2["market_gap_level"] == "high"


def test_books_config_is_respected():
    pick = {
        "draftkings_price": -150,
        "fanduel_price": -300,
        "pinnacle_price": -160,
    }
    # Restrict to DK+pinnacle only -> gap = 10
    out = mg.compute_market_gap(pick, books=("draftkings", "pinnacle"))
    assert out["market_books_compared"] == 2
    assert out["market_gap_points"] == 10
    assert out["market_gap_level"] == "none"


def test_contract_fields_present_for_all_levels():
    required = {
        "market_gap_points",
        "market_books_compared",
        "market_best_book",
        "market_best_price",
        "market_price_map",
        "market_gap_level",
    }
    for pick in [
        {},
        {"draftkings_price": -150},
        {"draftkings_price": -150, "fanduel_price": -160},
        {"draftkings_price": -150, "fanduel_price": -300},
    ]:
        out = mg.compute_market_gap(pick)
        assert required.issubset(out.keys())
