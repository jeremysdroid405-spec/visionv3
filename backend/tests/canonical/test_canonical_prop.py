"""Unit tests for the canonical prop engine.

Validates sport-agnostic behaviour, std↔alt collapse, cross-book
devig, no-silent-defaults, identity uniqueness.
"""
from __future__ import annotations
import pytest

from services.canonical.canonical_prop import (
    CanonicalProp, build_canonical_props,
)
from services.canonical.market_normalizer import normalize_market


def _row(**kw):
    base = dict(event_id="evt1", player_name="Test Player",
                 player_name_normalized="test player",
                 market="batter_hits", line=0.5, side="OVER",
                 book="draftkings", odds=-200)
    base.update(kw); return base


# ── Market normalizer ─────────────────────────────────────────────
def test_normalize_market_batter_hits():
    fam, key, is_alt = normalize_market("mlb", "batter_hits")
    assert fam == "hits"; assert key == "batter_hits"; assert is_alt is False

def test_normalize_market_alt_collapses_to_root():
    fam, key, is_alt = normalize_market("mlb", "batter_hits_alternate")
    assert fam == "hits"; assert key == "batter_hits"; assert is_alt is True

def test_normalize_market_alias_player_hits():
    fam, key, _ = normalize_market("mlb", "player_hits")
    assert fam == "hits"; assert key == "batter_hits"

def test_normalize_market_unknown_returns_none():
    fam, _, _ = normalize_market("mlb", "not_a_market")
    assert fam is None

def test_normalize_market_nfl_unknown_ok():
    # NFL has no alias table; unknown market returns None (no silent default)
    fam, _, _ = normalize_market("nfl", "anything")
    assert fam is None


# ── Builder: identity ──────────────────────────────────────────────
def test_one_canonical_per_player_stat_line():
    rows = [
        _row(book="draftkings", odds=-180),
        _row(book="fanduel", odds=-185),
        _row(book="betmgm", odds=-175),
    ]
    props = build_canonical_props(rows, sport="mlb")
    assert len(props) == 1
    p = props[0]
    assert p.stat_family == "hits"
    assert p.canonical_line == 0.5
    assert p.book_count_over == 3
    assert p.book_count_under == 0

def test_std_and_alt_collapse_to_same_canonical():
    rows = [
        _row(market="batter_hits",            book="draftkings", odds=-180),
        _row(market="batter_hits_alternate",  book="fanduel",    odds=-200),
    ]
    props = build_canonical_props(rows, sport="mlb")
    assert len(props) == 1
    p = props[0]
    assert p.canonical_market_key == "batter_hits"
    assert set(p.over_prices) == {"draftkings", "fanduel"}
    assert "batter_hits" in p.source_market_keys
    assert "batter_hits_alternate" in p.source_market_keys


# ── Cross-book devig ───────────────────────────────────────────────
def test_cross_book_devig_supported():
    rows = [
        _row(book="draftkings", side="OVER",  odds=-180),
        _row(book="fanduel",    side="UNDER", odds=+170),
    ]
    props = build_canonical_props(rows, sport="mlb")
    p = props[0]
    assert p.has_cross_book_devig is True
    assert p.has_same_book_devig is False
    assert p.devig_over_probability is not None
    assert p.devig_under_probability is not None
    # Probabilities sum to 1
    assert abs(p.devig_over_probability + p.devig_under_probability - 1.0) < 1e-9

def test_same_book_pair_marks_both_flags():
    rows = [
        _row(book="draftkings", side="OVER",  odds=-180),
        _row(book="draftkings", side="UNDER", odds=+155),
    ]
    p = build_canonical_props(rows, sport="mlb")[0]
    assert p.has_same_book_devig is True
    assert p.has_cross_book_devig is True
    assert p.book_count_both_sides_same_book == 1

def test_one_sided_no_devig():
    rows = [_row(book="draftkings", side="OVER", odds=-180)]
    p = build_canonical_props(rows, sport="mlb")[0]
    assert p.has_cross_book_devig is False
    assert p.has_same_book_devig is False
    assert p.devig_over_probability is None
    assert p.devig_under_probability is None


# ── Best book + consensus ──────────────────────────────────────────
def test_best_over_book_picks_highest_american():
    rows = [
        _row(book="draftkings", odds=-200),
        _row(book="fanduel",    odds=-175),  # better for bettor
        _row(book="betmgm",     odds=-220),
    ]
    p = build_canonical_props(rows, sport="mlb")[0]
    assert p.best_over_book == "fanduel"
    assert p.best_over_price == -175

def test_consensus_is_mean_implied():
    # Mean of implied probs of (-180, -150) ≈ (0.6429 + 0.6) / 2 = 0.6214
    rows = [
        _row(book="draftkings", odds=-180),
        _row(book="fanduel",    odds=-150),
    ]
    p = build_canonical_props(rows, sport="mlb")[0]
    # Re-implied → American close to -164ish
    assert -170 <= p.consensus_over_price <= -160


# ── Source row count ───────────────────────────────────────────────
def test_source_rows_count_collapsed_correctly():
    rows = [
        _row(book="draftkings", odds=-180),
        _row(book="fanduel", odds=-175),
        _row(book="betmgm", odds=-170),
        _row(book="draftkings", market="batter_hits_alternate", odds=-185),
    ]
    p = build_canonical_props(rows, sport="mlb")[0]
    assert p.source_rows_count == 4
    # Same-book duplicate (DK std + DK alt) collapsed to best price:
    # std=-180, alt=-185 → keep -180 (less negative)
    assert p.over_prices["draftkings"] == -180


# ── Missing identity components → silent skip (NOT silent default) ─
def test_missing_event_id_skipped():
    rows = [_row(event_id="")]
    assert build_canonical_props(rows, sport="mlb") == []

def test_missing_line_skipped():
    rows = [_row(line=None)]
    assert build_canonical_props(rows, sport="mlb") == []

def test_unknown_market_skipped():
    rows = [_row(market="not_a_real_market")]
    assert build_canonical_props(rows, sport="mlb") == []
