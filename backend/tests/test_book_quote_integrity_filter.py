"""
Pre-Scoring MLB Book Quote Integrity Filter — pytest suite (2026-05-17)
=======================================================================

Covers the 8 scenarios specified by the PRE-SCORING MLB BOOK QUOTE
INTEGRITY FILTER prompt. Each test exercises the pure filter via
``apply_book_quote_integrity_filter`` (or the batch wrapper
``apply_to_prop_list``) so the assertions are independent of the
broader scoring pipeline.

The rule under test:
    IF sport == "mlb"
    AND market_class == "alternate"
    AND line == 0.5
    AND american_odds >= +500
    THEN eject ONLY that specific book quote.

The prop stays alive on partial ejection; on FULL ejection (every
alt-bucket book quote ejected) the filter signals ``rejected=True``
and the batch wrapper drops the prop entirely.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from services.scoring.book_quote_integrity_filter import (
    apply_book_quote_integrity_filter,
    apply_to_prop_list,
)


def _make_prop(
    *,
    sport: str = "mlb",
    market_class: str = "alternate",
    line: float = 0.5,
    alt_odds: Dict[str, int] | None = None,
    std_odds: Dict[str, int] | None = None,
    layers: Dict[str, Dict[str, Any]] | None = None,
    flats: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Minimal canonical-ish prop dict for filter tests."""
    prop: Dict[str, Any] = {
        "sport": sport,
        "market_class": market_class,
        "line": line,
        "canonical_key": (
            f"{sport}|evt|player|stat|{float(line)}|OVER"
        ),
        "all_odds_alternate": dict(alt_odds or {}),
        "all_odds_standard":  dict(std_odds or {}),
        "all_lines_alternate": {b: float(line) for b in (alt_odds or {})},
        "all_lines_standard":  {b: float(line) for b in (std_odds or {})},
    }
    if layers:
        for k, v in layers.items():
            prop[k] = v
    if flats:
        prop.update(flats)
    return prop


# ─────────────────────────────────────────────────────────────────────
# Test 1 — Single absurd alt book quote is ejected; prop survives.
# ─────────────────────────────────────────────────────────────────────
def test_single_book_ejection_alt_05_long_odds() -> None:
    prop = _make_prop(
        alt_odds={"draftkings": 1500, "fanduel": -125},
    )
    _, excluded, rejected = apply_book_quote_integrity_filter(prop)

    assert rejected is False, "Prop must survive when ≥1 alt book remains"
    assert len(excluded) == 1
    assert excluded[0]["book"] == "draftkings"
    assert excluded[0]["odds"] == 1500
    assert excluded[0]["reason"] == "mlb_alt_05line_long_odds"
    assert excluded[0]["market_class"] == "alternate"
    # `draftkings` removed from alt container; fanduel preserved.
    assert "draftkings" not in prop["all_odds_alternate"]
    assert prop["all_odds_alternate"]["fanduel"] == -125
    # Mirrored bookkeeping fields persisted on the prop.
    assert prop["integrity_filter_applied"] is True
    assert prop["excluded_book_quotes"] == excluded


# ─────────────────────────────────────────────────────────────────────
# Test 2 — Multi-book partial ejection: two bad / two good.
# ─────────────────────────────────────────────────────────────────────
def test_multi_book_partial_ejection() -> None:
    prop = _make_prop(
        alt_odds={
            "draftkings": 1000,    # eject
            "fanduel":    -135,    # keep
            "betmgm":     +900,    # eject
            "espnbet":    -110,    # keep
        },
    )
    _, excluded, rejected = apply_book_quote_integrity_filter(prop)

    assert rejected is False
    ejected_books = {e["book"] for e in excluded}
    assert ejected_books == {"draftkings", "betmgm"}
    survivors = set(prop["all_odds_alternate"].keys())
    assert survivors == {"fanduel", "espnbet"}
    assert prop["integrity_filter_applied"] is True


# ─────────────────────────────────────────────────────────────────────
# Test 3 — Standard market is NEVER filtered, even at +1000 on 0.5.
# ─────────────────────────────────────────────────────────────────────
def test_standard_market_not_filtered() -> None:
    prop = _make_prop(
        market_class="standard",
        alt_odds={},
        std_odds={"draftkings": 1000, "fanduel": -125},
    )
    snapshot = dict(prop["all_odds_standard"])
    _, excluded, rejected = apply_book_quote_integrity_filter(prop)

    assert excluded == []
    assert rejected is False
    # Standard container untouched.
    assert prop["all_odds_standard"] == snapshot
    assert "integrity_filter_applied" not in prop


# ─────────────────────────────────────────────────────────────────────
# Test 4 — Non-MLB sports are never filtered (sport scope).
# ─────────────────────────────────────────────────────────────────────
def test_non_mlb_sport_not_filtered() -> None:
    prop = _make_prop(sport="nba", alt_odds={"draftkings": 1500})
    _, excluded, rejected = apply_book_quote_integrity_filter(prop)
    assert excluded == []
    assert rejected is False
    assert prop["all_odds_alternate"] == {"draftkings": 1500}
    assert "integrity_filter_applied" not in prop


# ─────────────────────────────────────────────────────────────────────
# Test 5 — Line != 0.5 is never filtered, even on absurd odds.
# ─────────────────────────────────────────────────────────────────────
def test_non_05_line_not_filtered() -> None:
    prop = _make_prop(line=1.5, alt_odds={"draftkings": 2000})
    _, excluded, rejected = apply_book_quote_integrity_filter(prop)
    assert excluded == []
    assert rejected is False
    assert prop["all_odds_alternate"] == {"draftkings": 2000}
    assert "integrity_filter_applied" not in prop


# ─────────────────────────────────────────────────────────────────────
# Test 6 — Odds below threshold (+499) are NOT ejected.
# ─────────────────────────────────────────────────────────────────────
def test_odds_below_threshold_not_filtered() -> None:
    prop = _make_prop(
        alt_odds={"draftkings": 499, "fanduel": 100},
    )
    _, excluded, rejected = apply_book_quote_integrity_filter(prop)
    assert excluded == []
    assert rejected is False
    assert prop["all_odds_alternate"] == {"draftkings": 499, "fanduel": 100}


# ─────────────────────────────────────────────────────────────────────
# Test 7 — Boundary: exactly +500 IS ejected (inclusive threshold).
# ─────────────────────────────────────────────────────────────────────
def test_boundary_plus_500_is_ejected() -> None:
    prop = _make_prop(
        alt_odds={"draftkings": 500, "fanduel": -110},
    )
    _, excluded, rejected = apply_book_quote_integrity_filter(prop)
    assert rejected is False
    assert {e["book"] for e in excluded} == {"draftkings"}
    assert "draftkings" not in prop["all_odds_alternate"]
    assert prop["all_odds_alternate"] == {"fanduel": -110}


# ─────────────────────────────────────────────────────────────────────
# Test 8 — ALL alt book quotes ejected → batch wrapper drops the prop.
# ─────────────────────────────────────────────────────────────────────
def test_all_books_ejected_drops_prop_via_batch() -> None:
    rejected_prop = _make_prop(
        alt_odds={"draftkings": 1200, "fanduel": 900, "betmgm": 600},
    )
    survivor_prop = _make_prop(
        alt_odds={"draftkings": 800, "fanduel": -120},
    )
    survivor_prop["canonical_key"] = "mlb|evt|other|stat|0.5|OVER"

    surviving, stats = apply_to_prop_list([rejected_prop, survivor_prop])

    # Single-prop low-level signal first.
    _, excluded, rejected = apply_book_quote_integrity_filter(
        _make_prop(alt_odds={"draftkings": 1200, "fanduel": 900}),
    )
    assert rejected is True, "Filter must signal rejected=True when all alt books ejected"
    assert len(excluded) == 2

    # Batch-level dropping behaviour.
    canon_keys = [p["canonical_key"] for p in surviving]
    assert "mlb|evt|player|stat|0.5|OVER" not in canon_keys, \
        "Prop with all alt books ejected must be removed from batch"
    assert "mlb|evt|other|stat|0.5|OVER" in canon_keys
    assert stats["rejected"] == 1
    assert stats["mutated"] == 2  # both eligible props had ≥1 quote ejected


# ─────────────────────────────────────────────────────────────────────
# Bonus structural checks — not part of the 8 required scenarios, but
# cheap to assert and lock the per-layer cross-class safety invariant.
# ─────────────────────────────────────────────────────────────────────
def test_alt_layer_cleared_standard_layer_preserved() -> None:
    """A book with a parallel standard quote must keep its standard
    layer intact even when its alt quote is ejected. The filter is
    class-pure by construction."""
    # Two alt-bucket books so DK can be ejected without triggering the
    # "all ejected → rejected" branch; FD only exists in the standard
    # container plus a parallel standard layer that MUST survive.
    prop = _make_prop(
        alt_odds={"draftkings": 1500, "betmgm": -130},
        std_odds={"fanduel": -125},
        layers={
            "dk_layer": {
                "book": "draftkings",
                "line": 0.5,
                "odds": 1500,
                "market_class": "alternate",
                "source_market_key": "batter_total_bases_alternate",
            },
            "fd_layer": {
                "book": "fanduel",
                "line": 0.5,
                "odds": -125,
                "market_class": "standard",
                "source_market_key": "batter_total_bases",
            },
        },
        flats={
            "dk_line": 0.5, "dk_odds": 1500, "dk_odds_opp": -3000,
            "fd_line": 0.5, "fd_odds": -125, "fd_odds_opp": +105,
        },
    )
    _, excluded, rejected = apply_book_quote_integrity_filter(prop)
    assert rejected is False
    assert {e["book"] for e in excluded} == {"draftkings"}
    # DK alt layer cleared; FD standard layer preserved.
    assert prop["dk_layer"] is None
    assert prop["fd_layer"]["market_class"] == "standard"
    # DK flat fields cleared; FD flats preserved.
    assert "dk_line" not in prop and "dk_odds" not in prop and "dk_odds_opp" not in prop
    assert prop["fd_line"] == 0.5 and prop["fd_odds"] == -125


def test_excluded_book_quotes_payload_shape() -> None:
    """Audit endpoints and forensic replays consume this payload —
    lock the schema."""
    prop = _make_prop(alt_odds={"draftkings": 2500, "fanduel": -120})
    _, excluded, _ = apply_book_quote_integrity_filter(prop)
    assert len(excluded) == 1
    rec = excluded[0]
    assert set(rec.keys()) == {"book", "odds", "line", "market_class", "reason"}
    assert rec["book"] == "draftkings"
    assert rec["odds"] == 2500
    assert rec["line"] == 0.5
    assert rec["market_class"] == "alternate"
    assert rec["reason"] == "mlb_alt_05line_long_odds"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
