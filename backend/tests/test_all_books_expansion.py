"""Regression — 2026-05-13 "pull from all books" expansion.

Validates that:
  • coverage_filter._BOOK_FIELDS contains all 11 books (5 legacy + Caesars
    + 6 new sportsbooks).
  • tp_engine._BOOKS + _OPP_FIELDS contains all 11 books with matching
    self/opp odds keys (so the de-vig engine reads ESPN BET / Hard Rock /
    BetRivers / BetParx / BallyBet / Fliff prices when present).
  • prop_scores_store._BOOK_LAYER_FIELDS preserves every book's
    layer/line/odds/odds_opp through the score-doc projection.
  • classify_coverage correctly counts an all-11-book prop.
"""
from __future__ import annotations

import pytest

from services.scoring.coverage_filter import (
    classify_coverage, _BOOK_FIELDS,
)
from services.scoring.tp_engine import _BOOKS as TP_BOOKS, _OPP_FIELDS
from services.scoring.prop_scores_store import _BOOK_LAYER_FIELDS

EXPECTED_BOOKS = {
    "draftkings", "fanduel", "betonlineag", "betmgm", "williamhill_us",
    "espnbet", "hardrockbet", "betrivers", "betparx", "ballybet", "fliff",
}

EXPECTED_ODDS_KEYS = {
    "dk_odds", "fd_odds", "bol_odds", "mgm_odds", "csr_odds",
    "eb_odds", "hrb_odds", "brv_odds", "prx_odds", "bly_odds", "flf_odds",
}


def test_coverage_filter_knows_all_11_books():
    book_keys = {b[0] for b in _BOOK_FIELDS}
    assert book_keys == EXPECTED_BOOKS, (
        f"_BOOK_FIELDS missing books: {EXPECTED_BOOKS - book_keys}"
    )


def test_tp_engine_books_match_coverage_filter():
    """De-vig engine must enumerate every book the coverage filter counts."""
    tp_odds_keys = {b[1] for b in TP_BOOKS}
    assert tp_odds_keys == EXPECTED_ODDS_KEYS


def test_tp_engine_opp_fields_match_books():
    """Every entry in _BOOKS must have a matching _OPP_FIELDS row, so
    Path-1 (single-prop) de-vig pairing works for all books."""
    book_codes = {b[2] for b in TP_BOOKS}
    opp_codes = set(_OPP_FIELDS.keys())
    assert book_codes == opp_codes


def test_score_output_preserves_all_book_layer_fields():
    """Every book must have layer/line/odds/odds_opp fields in the
    score-doc projection allowlist — otherwise the prop_scores_store
    projector drops them silently."""
    expected_suffixes = ("_layer", "_line", "_odds", "_odds_opp")
    short_prefixes = ("dk", "fd", "bol", "mgm", "csr",
                      "eb", "hrb", "brv", "prx", "bly", "flf")
    missing = []
    for p in short_prefixes:
        for s in expected_suffixes:
            field = f"{p}{s}"
            if field not in _BOOK_LAYER_FIELDS:
                missing.append(field)
    assert not missing, f"missing from _BOOK_LAYER_FIELDS: {missing}"


def test_classify_all_eleven_books():
    """Sanity: a prop with every book's odds should yield book_count==11."""
    prop = {
        "dk_odds": -110, "fd_odds": -110, "bol_odds": -110,
        "mgm_odds": -110, "csr_odds": -110,
        "eb_odds": -110, "hrb_odds": -110, "brv_odds": -110,
        "prx_odds": -110, "bly_odds": -110, "flf_odds": -110,
    }
    bc, cov = classify_coverage(prop)
    assert bc == 11
    assert cov == "multi_book"
    assert set(prop["books_anchored"]) == EXPECTED_BOOKS


def test_classify_only_new_books_no_legacy():
    """A prop where only the 6 new sportsbooks quote (no DK/FD/MGM/BOL/CSR)
    should still be counted as multi-book."""
    prop = {
        "eb_odds": -110, "hrb_odds": -110, "brv_odds": -110,
        "prx_odds": -110,
    }
    bc, cov = classify_coverage(prop)
    assert bc == 4
    assert cov == "multi_book"
