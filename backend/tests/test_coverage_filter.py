"""
Tests for services/scoring/coverage_filter.py — 0-Book Exclusion Rule
(2026-04-22). Locks the hard-gate contract:

    book_count == 0  → pp_only   → EXCLUDED
    book_count == 1  → single_book → kept
    book_count >= 2  → multi_book  → kept

No fuzzy matching, no probability inference, no fallback.
"""
from __future__ import annotations

import logging

import pytest

from services.scoring.coverage_filter import (
    classify_coverage,
    filter_priceable,
)


# --------------------------------------------------------------------
# classify_coverage — single prop
# --------------------------------------------------------------------

def test_classify_pp_only_no_book_fields():
    prop = {"player_name": "X", "stat_type": "PTS", "line": 10.5}
    bc, cov = classify_coverage(prop)
    assert bc == 0
    assert cov == "pp_only"
    assert prop["book_count"] == 0
    assert prop["coverage_class"] == "pp_only"
    assert prop["books_anchored"] == []


def test_classify_single_book_legacy_field():
    """NBA-path naming: `draftkings_price`."""
    prop = {"draftkings_price": -402}
    bc, cov = classify_coverage(prop)
    assert bc == 1
    assert cov == "single_book"
    assert prop["books_anchored"] == ["draftkings"]


def test_classify_single_book_universal_field():
    """MLB-path naming: `dk_odds`."""
    prop = {"dk_odds": -137}
    bc, cov = classify_coverage(prop)
    assert bc == 1
    assert cov == "single_book"


def test_classify_multi_book_mixed_naming():
    """Legacy + universal fields co-exist without double-counting DK."""
    prop = {"draftkings_price": -150, "dk_odds": -150,
            "fanduel_price": -145, "betmgm_price": -140}
    bc, cov = classify_coverage(prop)
    # DK must only count ONCE even though both legacy and universal
    # fields are set.
    assert bc == 3
    assert cov == "multi_book"
    assert set(prop["books_anchored"]) == {"draftkings", "fanduel", "betmgm"}


def test_classify_all_four_books():
    prop = {"dk_odds": -120, "fd_odds": -125, "bol_odds": -115, "mgm_odds": -118}
    bc, cov = classify_coverage(prop)
    assert bc == 4
    assert cov == "multi_book"


def test_classify_sharp_market_nested_prices():
    """NBA demon_goblin path stores prices in a nested `sharp_market` dict."""
    prop = {"sharp_market": {"draftkings_price": -180, "fanduel_price": None}}
    bc, cov = classify_coverage(prop)
    assert bc == 1
    assert cov == "single_book"
    assert prop["books_anchored"] == ["draftkings"]


def test_classify_rejects_zero_price():
    """American odds of 0 is not a real quote — must not count as an anchor."""
    prop = {"draftkings_price": 0, "fanduel_price": None}
    bc, cov = classify_coverage(prop)
    assert bc == 0
    assert cov == "pp_only"


def test_classify_rejects_none_and_missing_keys():
    prop = {"draftkings_price": None, "fanduel_price": None,
            "betonline_price": None, "betmgm_price": None}
    bc, _ = classify_coverage(prop)
    assert bc == 0


# --------------------------------------------------------------------
# filter_priceable — batch behavior
# --------------------------------------------------------------------

def test_filter_priceable_drops_pp_only_returns_stats():
    props = [
        {"id": "a", "dk_odds": -120},                         # single_book → kept
        {"id": "b"},                                          # pp_only → dropped
        {"id": "c", "fd_odds": -110, "bol_odds": -108},       # multi_book → kept
        {"id": "d", "draftkings_price": None},                # pp_only → dropped
    ]
    kept, stats = filter_priceable(props, sport="nba")
    assert [p["id"] for p in kept] == ["a", "c"]
    assert stats["total_props_seen"] == 4
    assert stats["total_props_excluded_pp_only"] == 2
    assert stats["total_props_remaining"] == 2
    assert stats["multi_book"] == 1
    assert stats["single_book"] == 1
    assert stats["pp_only"] == 2
    assert stats["coverage_rate"] == 0.5


def test_filter_priceable_mutates_props_in_place():
    """Every prop — kept OR dropped — gets book_count / coverage_class
    stamped on it, so downstream diagnostics can still inspect dropped
    props before/after filtering."""
    a = {"id": "a", "dk_odds": -120}
    b = {"id": "b"}
    filter_priceable([a, b], sport="mlb")
    assert a["book_count"] == 1
    assert a["coverage_class"] == "single_book"
    assert b["book_count"] == 0
    assert b["coverage_class"] == "pp_only"


def test_filter_priceable_empty_list_zero_coverage_rate():
    kept, stats = filter_priceable([], sport="nba")
    assert kept == []
    assert stats["total_props_seen"] == 0
    assert stats["coverage_rate"] == 0.0


def test_filter_priceable_logs_coverage_line_once(caplog):
    props = [{"dk_odds": -120}, {}, {}]
    with caplog.at_level(logging.INFO, logger="services.scoring.coverage_filter"):
        filter_priceable(props, sport="nba", run_id="abc123")
    matching = [r for r in caplog.records if "COVERAGE_FILTER" in r.getMessage()]
    # Exactly one coverage-filter line per run.
    assert len(matching) == 1
    msg = matching[0].getMessage()
    assert "total=3" in msg
    assert "excluded_pp_only=2" in msg
    assert "remaining=1" in msg
    assert "NBA" in msg
    assert "abc123" in msg
