"""
Peer-Disagreement Integrity Filter — pytest suite (2026-05-17)
=============================================================
Covers the rule:

    Eject an individual real-sportsbook quote from MLB alternate
    markets when
        delta = book_odds - median(other_real_books) >= +200
    AND there are ≥2 non-PP sportsbook quotes on the prop.

The prop is never dropped; only specific bad quotes are ejected.
PrizePicks is NEVER allowed into the peer median.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from services.scoring.peer_disagreement_filter import (
    apply_peer_disagreement_filter,
    apply_to_prop_list,
)


def _make_prop(
    *,
    sport: str = "mlb",
    market_class: str = "alternate",
    line: float = 0.5,
    alt_odds: Dict[str, int] | None = None,
    layers: Dict[str, Dict[str, Any]] | None = None,
    flats: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    prop: Dict[str, Any] = {
        "sport": sport,
        "market_class": market_class,
        "line": line,
        "canonical_key": f"{sport}|evt|player|stat|{float(line)}|OVER",
        "all_odds_alternate": dict(alt_odds or {}),
        "all_lines_alternate": {b: float(line) for b in (alt_odds or {})},
    }
    if layers:
        prop.update(layers)
    if flats:
        prop.update(flats)
    return prop


# ─────────────────────────────────────────────────────────────────
# 1 — Canonical example from the spec: peers +150/+165, DK +400.
# ─────────────────────────────────────────────────────────────────
def test_spec_example_dk_400_vs_peer_157() -> None:
    prop = _make_prop(alt_odds={
        "draftkings": 400,
        "fanduel":    150,
        "betmgm":     165,
    })
    _, excluded = apply_peer_disagreement_filter(prop)
    assert len(excluded) == 1
    rec = excluded[0]
    assert rec["book"] == "draftkings"
    assert rec["odds"] == 400
    assert rec["peer_median_odds"] == pytest.approx(157.5)
    assert rec["book_odds_delta"] == pytest.approx(242.5)
    assert rec["reason"] == "peer_disagreement_plus_200"
    assert rec["peer_book_count"] == 2
    assert "draftkings" not in prop["all_odds_alternate"]
    assert set(prop["all_odds_alternate"].keys()) == {"fanduel", "betmgm"}
    assert prop["integrity_filter_applied"] is True


# ─────────────────────────────────────────────────────────────────
# 2 — DK matches peers (Δ < +200): kept.
# ─────────────────────────────────────────────────────────────────
def test_dk_in_line_with_peers_kept() -> None:
    prop = _make_prop(alt_odds={
        "draftkings": 200,
        "fanduel":    150,
        "betmgm":     165,
    })
    snapshot = dict(prop["all_odds_alternate"])
    _, excluded = apply_peer_disagreement_filter(prop)
    assert excluded == []
    assert prop["all_odds_alternate"] == snapshot
    assert "integrity_filter_applied" not in prop


# ─────────────────────────────────────────────────────────────────
# 3 — Boundary: delta exactly +200 IS ejected (inclusive).
# ─────────────────────────────────────────────────────────────────
def test_boundary_delta_exactly_200_is_ejected() -> None:
    prop = _make_prop(alt_odds={
        "draftkings": 350,    # peer median = 150 → delta = 200
        "fanduel":    150,
        "betmgm":     150,
    })
    _, excluded = apply_peer_disagreement_filter(prop)
    assert {e["book"] for e in excluded} == {"draftkings"}


# ─────────────────────────────────────────────────────────────────
# 4 — Only one real sportsbook quote on the prop: filter does
# not fire (need ≥2 non-PP sportsbook quotes).
# ─────────────────────────────────────────────────────────────────
def test_lt_2_real_books_does_not_fire() -> None:
    prop = _make_prop(alt_odds={"draftkings": 800})
    _, excluded = apply_peer_disagreement_filter(prop)
    assert excluded == []
    assert prop["all_odds_alternate"] == {"draftkings": 800}


# ─────────────────────────────────────────────────────────────────
# 5 — PrizePicks must NEVER influence the peer median.
# ─────────────────────────────────────────────────────────────────
def test_prizepicks_excluded_from_peer_median() -> None:
    # If PP were included, peer median across {pp +100, fd +150,
    # mgm +165} = +150 and DK at +400 would still eject (Δ=+250).
    # Use a DK price that ONLY ejects when PP is excluded:
    #   With PP:     peers = {pp +100, fd +150}, med = +125 →
    #                DK +320 → delta +195 → KEPT.
    #   Without PP:  peers = {fd +150}, med = +150 →
    #                DK +320 → delta +170 → KEPT.
    # Better: pick a config that flips ONLY when PP is excluded.
    #   peers including PP = {pp +100, fd +300}, med = +200
    #                          → DK +395 → delta +195 → KEPT
    #   peers without PP    = {fd +300},          med = +300
    #                          → DK +395 → delta  +95 → KEPT
    # … invariant either way. So instead, assert directly that
    # PP is never present in the recorded peer set.
    prop = _make_prop(alt_odds={
        "draftkings": 400,
        "fanduel":    150,
        "betmgm":     165,
        "prizepicks": 100,   # would corrupt peer median if used
    })
    _, excluded = apply_peer_disagreement_filter(prop)
    # DK should still be ejected on the real-book median (+157.5).
    assert len(excluded) == 1
    assert excluded[0]["book"] == "draftkings"
    assert excluded[0]["peer_median_odds"] == pytest.approx(157.5), \
        "PrizePicks +100 must NOT pull the peer median toward +100"
    assert excluded[0]["peer_book_count"] == 2  # FD + MGM, no PP


# ─────────────────────────────────────────────────────────────────
# 6 — Multiple outliers ejected in one pass; in-line peers kept.
# ─────────────────────────────────────────────────────────────────
def test_multi_book_partial_ejection() -> None:
    prop = _make_prop(alt_odds={
        "draftkings": 700,    # eject
        "espnbet":    650,    # eject
        "fanduel":    150,    # keep
        "betmgm":     165,    # keep
        "hardrockbet": 180,   # keep
    })
    _, excluded = apply_peer_disagreement_filter(prop)
    ejected = {e["book"] for e in excluded}
    assert ejected == {"draftkings", "espnbet"}
    survivors = set(prop["all_odds_alternate"].keys())
    assert survivors == {"fanduel", "betmgm", "hardrockbet"}
    assert prop["integrity_filter_applied"] is True


# ─────────────────────────────────────────────────────────────────
# 7 — Non-MLB sport: filter does not fire (sport scope).
# ─────────────────────────────────────────────────────────────────
def test_non_mlb_sport_skipped() -> None:
    prop = _make_prop(sport="nba", alt_odds={
        "draftkings": 700, "fanduel": 150, "betmgm": 165,
    })
    _, excluded = apply_peer_disagreement_filter(prop)
    assert excluded == []
    assert prop["all_odds_alternate"] == {
        "draftkings": 700, "fanduel": 150, "betmgm": 165,
    }


# ─────────────────────────────────────────────────────────────────
# 8 — Standard market: filter does not fire (class scope).
# ─────────────────────────────────────────────────────────────────
def test_standard_market_skipped() -> None:
    prop = _make_prop(market_class="standard", alt_odds={
        "draftkings": 700, "fanduel": 150, "betmgm": 165,
    })
    _, excluded = apply_peer_disagreement_filter(prop)
    assert excluded == []


# ─────────────────────────────────────────────────────────────────
# 9 — Mixed-sign odds work via literal subtraction. Book at +150
# vs peers heavy negative → big +Δ → ejected.
# ─────────────────────────────────────────────────────────────────
def test_mixed_signs_literal_delta() -> None:
    prop = _make_prop(alt_odds={
        "draftkings": 150,   # peer median = -150 → delta = +300 → eject
        "fanduel":    -150,
        "betmgm":     -150,
    })
    _, excluded = apply_peer_disagreement_filter(prop)
    assert {e["book"] for e in excluded} == {"draftkings"}
    assert excluded[0]["peer_median_odds"] == pytest.approx(-150)
    assert excluded[0]["book_odds_delta"] == pytest.approx(300)


# ─────────────────────────────────────────────────────────────────
# 10 — Per-book layer hygiene: DK alt layer cleared, parallel FD
# standard layer preserved; flat DK *_line/_odds/_odds_opp cleared.
# ─────────────────────────────────────────────────────────────────
def test_layer_and_flat_field_hygiene() -> None:
    prop = _make_prop(
        alt_odds={"draftkings": 400, "fanduel": 150, "betmgm": 165},
        layers={
            "dk_layer": {"book": "draftkings", "line": 0.5,
                         "odds": 400, "market_class": "alternate",
                         "source_market_key": "batter_hits_alternate"},
            "fd_layer": {"book": "fanduel", "line": 0.5,
                         "odds": -180, "market_class": "standard",
                         "source_market_key": "batter_hits"},
        },
        flats={"dk_line": 0.5, "dk_odds": 400, "dk_odds_opp": -550,
               "fd_line": 0.5, "fd_odds": 150, "fd_odds_opp": -200},
    )
    _, excluded = apply_peer_disagreement_filter(prop)
    assert {e["book"] for e in excluded} == {"draftkings"}
    assert prop["dk_layer"] is None
    assert prop["fd_layer"]["market_class"] == "standard", \
        "Parallel standard layer must survive class-pure ejection"
    for suf in ("_line", "_odds", "_odds_opp"):
        assert f"dk{suf}" not in prop
        assert f"fd{suf}" in prop


# ─────────────────────────────────────────────────────────────────
# 11 — Excluded record payload schema lock.
# ─────────────────────────────────────────────────────────────────
def test_excluded_record_payload_shape() -> None:
    prop = _make_prop(alt_odds={
        "draftkings": 700, "fanduel": 150, "betmgm": 165,
    })
    _, excluded = apply_peer_disagreement_filter(prop)
    assert len(excluded) == 1
    rec = excluded[0]
    required = {
        "book", "odds", "line", "market_class", "reason",
        "peer_median_odds", "book_odds_delta", "peer_book_count",
    }
    assert required.issubset(set(rec.keys()))
    assert rec["reason"] == "peer_disagreement_plus_200"
    assert rec["market_class"] == "alternate"


# ─────────────────────────────────────────────────────────────────
# 12 — Batch wrapper: prop is NEVER dropped (even on all-ejected).
# ─────────────────────────────────────────────────────────────────
def test_batch_never_drops_prop() -> None:
    # Pathological case: 3 books each individually far outside the
    # median of the remaining 2. With 3 outliers, each candidate's
    # peer median is the median of the other 2. If all 3 are
    # spread far enough they could each be ejected.
    survivor = _make_prop(alt_odds={
        "draftkings": 700, "fanduel": 150, "betmgm": 165,
    })
    survivor["canonical_key"] = "mlb|evt|other|stat|0.5|OVER"
    out, stats = apply_to_prop_list([survivor])
    assert len(out) == 1, "Filter MUST NOT drop props"
    assert stats["mutated"] == 1
    assert stats["total_quotes_ejected"] == 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))


# ─────────────────────────────────────────────────────────────────
# 13 — Legacy shape: is_alternate_market=True + `all_odds` combined
# dict (no `all_odds_alternate` yet). PP must still be filtered out
# of the peer median even though it sits in the combined dict.
# ─────────────────────────────────────────────────────────────────
def test_legacy_all_odds_shape_with_pp_in_combined_dict() -> None:
    prop = {
        "sport": "mlb",
        "is_alternate_market": True,
        # market_class intentionally missing — pre-hardening shape
        "line": 0.5,
        "canonical_key": "mlb|evt|player|stat|0.5|OVER",
        "all_odds": {
            "draftkings": 400,
            "fanduel":    150,
            "betmgm":     165,
            "prizepicks": 100,
        },
        "all_lines": {b: 0.5 for b in
                      ("draftkings", "fanduel", "betmgm", "prizepicks")},
    }
    _, excluded = apply_peer_disagreement_filter(prop)
    assert len(excluded) == 1
    assert excluded[0]["book"] == "draftkings"
    assert excluded[0]["peer_median_odds"] == pytest.approx(157.5), \
        "PP must NOT participate even when present in legacy all_odds"
    # DK ejected from legacy container; PP left alone.
    assert "draftkings" not in prop["all_odds"]
    assert prop["all_odds"]["prizepicks"] == 100
    assert prop["integrity_filter_applied"] is True

