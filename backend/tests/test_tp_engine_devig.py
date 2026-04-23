"""
Tests for services/scoring/tp_engine.py — multi-book de-vigged True
Probability (2026-04-22).

Locks the user spec:
  - Multi-book de-vig (DK/FD/MGM/BOL), per-book:
      p_over_true  = p_over_raw  / (p_over_raw + p_under_raw)
      p_under_true = p_under_raw / (p_over_raw + p_under_raw)
  - Average the per-book `p_true` values for the picked side.
  - Skip any book without BOTH sides.
  - No 50% fallback — `tp = None` when no book has both sides.
  - UNDER side is picked via `p_under_true`, not by flipping the
    over-side aggregate.
"""
from __future__ import annotations

import pytest

from services.scoring.tp_engine import (
    TP_METHOD,
    compute_tp,
    build_companion_map,
    lookup_companion_sides,
    _amer_to_prob,
)


# --------------------------------------------------------------------
# _amer_to_prob — matches user's spec formulas
# --------------------------------------------------------------------

def test_amer_to_prob_negative_odds():
    assert _amer_to_prob(-110) == pytest.approx(110 / 210, rel=1e-6)
    assert _amer_to_prob(-200) == pytest.approx(200 / 300, rel=1e-6)


def test_amer_to_prob_positive_odds():
    assert _amer_to_prob(+110) == pytest.approx(100 / 210, rel=1e-6)
    assert _amer_to_prob(+150) == pytest.approx(100 / 250, rel=1e-6)


def test_amer_to_prob_none_and_zero_and_garbage():
    assert _amer_to_prob(None) is None
    assert _amer_to_prob(0) is None
    assert _amer_to_prob("junk") is None


# --------------------------------------------------------------------
# Sanity example verification
# --------------------------------------------------------------------

def test_symmetrical_market_tp_is_50():
    """User spec sanity check: -110 / -110 market → TP ≈ 50%."""
    over = {"dk_odds": -110}
    under = {"dk_odds": -110}
    out = compute_tp(over_prop=over, under_prop=under, side="OVER")
    assert out["tp"] == pytest.approx(50.0, abs=0.1)
    assert out["tp_books_used"] == 1
    assert out["tp_books_list"] == ["DK"]
    assert out["tp_method"] == "multi_book_devig_v1"


def test_minus115_minus105_market_gives_roughly_52_percent():
    """User spec sanity: -115 / -105 market → TP ≈ 51-52%."""
    over = {"dk_odds": -115}
    under = {"dk_odds": -105}
    out = compute_tp(over_prop=over, under_prop=under, side="OVER")
    # Manual: p_over_raw=115/215=.5349, p_under_raw=105/205=.5122
    # p_over_true = .5349/(.5349+.5122) = .5109 → 51.1
    assert out["tp"] == pytest.approx(51.1, abs=0.1)


def test_minus130_plus110_market_gives_roughly_55_percent():
    """User spec sanity: -130 / +110 market → TP ≈ 54-55%."""
    over = {"dk_odds": -130}
    under = {"dk_odds": +110}
    out = compute_tp(over_prop=over, under_prop=under, side="OVER")
    # p_over_raw=130/230=.5652, p_under_raw=100/210=.4762
    # p_over_true = .5652/(.5652+.4762) = .5428 → 54.3
    assert out["tp"] == pytest.approx(54.3, abs=0.2)


# --------------------------------------------------------------------
# Side handling
# --------------------------------------------------------------------

def test_under_side_picks_devigged_under_not_flipped_over():
    """Per spec: UNDER picks `p_under_true` directly (NOT
    `100 - p_over_true` of the over-side aggregate)."""
    over = {"dk_odds": -130}
    under = {"dk_odds": +110}
    over_out = compute_tp(over_prop=over, under_prop=under, side="OVER")
    under_out = compute_tp(over_prop=over, under_prop=under, side="UNDER")
    # They must sum to exactly 100 (de-vigged sides sum to 1).
    assert over_out["tp"] + under_out["tp"] == pytest.approx(100.0, abs=0.01)


def test_under_side_lower_than_over_when_line_is_fav():
    over = {"dk_odds": -200}
    under = {"dk_odds": +160}
    out = compute_tp(over_prop=over, under_prop=under, side="UNDER")
    assert out["tp"] < 50.0


# --------------------------------------------------------------------
# Multi-book aggregation
# --------------------------------------------------------------------

def test_multi_book_averages_across_all_paired_books():
    over = {
        "dk_odds": -110, "fd_odds": -120, "mgm_odds": -115, "bol_odds": -105,
    }
    under = {
        "dk_odds": -110, "fd_odds": +100, "mgm_odds": -105, "bol_odds": -115,
    }
    out = compute_tp(over_prop=over, under_prop=under, side="OVER")
    assert out["tp_books_used"] == 4
    assert set(out["tp_books_list"]) == {"DK", "FD", "MGM", "BOL"}
    # TP is the straight mean of the four per-book de-vigged p_over_true.
    # DK: 52.4/(52.4+52.4)=50.0
    # FD: 54.5/(54.5+50.0)=52.2
    # MGM: 53.5/(53.5+51.2)=51.1
    # BOL: 51.2/(51.2+53.5)=48.9
    # mean ≈ 50.5
    assert out["tp"] == pytest.approx(50.5, abs=0.5)


def test_book_missing_one_side_is_skipped():
    """Per spec: if only ONE side is available for a book → skip."""
    over = {"dk_odds": -110, "fd_odds": -115}
    under = {"dk_odds": -110}  # FD under missing
    out = compute_tp(over_prop=over, under_prop=under, side="OVER")
    assert out["tp_books_used"] == 1
    assert out["tp_books_list"] == ["DK"]


def test_zero_books_gives_none_tp():
    """Per spec: if ZERO books have both sides → TP = None, no 50% fallback."""
    out = compute_tp(over_prop={}, under_prop={}, side="OVER")
    assert out["tp"] is None
    assert out["tp_books_used"] == 0
    assert out["tp_books_list"] == []
    assert out["tp_method"] == TP_METHOD


def test_zero_books_when_one_side_completely_missing():
    """Only OVER data given, no UNDER data at all → no de-vig possible."""
    over = {"dk_odds": -110, "fd_odds": -120, "mgm_odds": -115}
    out = compute_tp(over_prop=over, under_prop=None, side="OVER")
    assert out["tp"] is None


def test_one_book_only_still_valid():
    """Per spec: 'If only ONE book total remains → TP = that book's
    de-vigged value (valid)'."""
    over = {"dk_odds": -115}
    under = {"dk_odds": -105}
    out = compute_tp(over_prop=over, under_prop=under, side="OVER")
    assert out["tp"] is not None
    assert out["tp_books_used"] == 1


# --------------------------------------------------------------------
# Legacy vs universal field naming (both must work)
# --------------------------------------------------------------------

def test_legacy_nba_naming_works():
    """NBA writes `draftkings_price`/`fanduel_price` (legacy)."""
    over = {"draftkings_price": -110, "fanduel_price": -115}
    under = {"draftkings_price": -110, "fanduel_price": -105}
    out = compute_tp(over_prop=over, under_prop=under, side="OVER")
    assert out["tp_books_used"] == 2
    assert set(out["tp_books_list"]) == {"DK", "FD"}


def test_universal_mlb_naming_works():
    """MLB writes `dk_odds`/`fd_odds` (universal sync)."""
    over = {"dk_odds": -110, "mgm_odds": -115}
    under = {"dk_odds": -110, "mgm_odds": -105}
    out = compute_tp(over_prop=over, under_prop=under, side="OVER")
    assert out["tp_books_used"] == 2
    assert set(out["tp_books_list"]) == {"DK", "MGM"}


# --------------------------------------------------------------------
# Single-prop path (using *_odds + *_odds_opp)
# --------------------------------------------------------------------

def test_single_prop_path_mlb_naming():
    prop = {
        "dk_odds":  -115, "dk_odds_opp":  -105,
        "mgm_odds": -120, "mgm_odds_opp": -100,
        "recommendation": "OVER",
    }
    out = compute_tp(prop=prop, side="OVER")
    assert out["tp_books_used"] == 2
    assert set(out["tp_books_list"]) == {"DK", "MGM"}
    # Manually: DK 52.38/(52.38+51.22)=50.56; MGM 54.55/(54.55+50.00)=52.17
    # mean ≈ 51.4
    assert out["tp"] == pytest.approx(51.4, abs=0.3)


def test_single_prop_path_nba_legacy_naming():
    prop = {
        "draftkings_price": -115, "dk_odds_opp": -105,
        "fanduel_price":    -110, "fd_odds_opp": -110,
    }
    out = compute_tp(prop=prop, side="OVER")
    assert out["tp_books_used"] == 2


def test_single_prop_path_skips_books_with_only_one_side():
    prop = {
        "dk_odds": -115, "dk_odds_opp": -105,  # both → used
        "fd_odds": -120,                        # opp missing → skipped
        "mgm_odds_opp": -100,                   # this missing → skipped
    }
    out = compute_tp(prop=prop, side="OVER")
    assert out["tp_books_list"] == ["DK"]


def test_single_prop_path_returns_none_with_no_paired_books():
    prop = {"dk_odds": -110}  # only one side
    out = compute_tp(prop=prop, side="OVER")
    assert out["tp"] is None
    assert out["tp_books_used"] == 0


def test_single_prop_path_under_side_is_devigged_correctly():
    """Picking UNDER must yield p_under_true on the single-prop path.
    With -130 vs +110, UNDER's de-vigged prob should be ~45.7%."""
    prop = {
        "dk_odds": +110, "dk_odds_opp": -130,  # over_odds=+110, under_odds=-130
    }
    # In the single-prop path, prop's own side is OVER. To model UNDER,
    # we pass a prop whose this-side = under price, opp = over price.
    prop_under_pick = {
        "dk_odds": -130, "dk_odds_opp": +110,
    }
    out = compute_tp(prop=prop_under_pick, side="UNDER")
    # This_odds = -130 → p_under_raw = 130/230 = .5652
    # Opp_odds  = +110 → p_over_raw  = 100/210 = .4762
    # p_under_true = .5652/(.5652+.4762) = .5428 → 54.3
    assert out["tp"] == pytest.approx(54.3, abs=0.2)


# --------------------------------------------------------------------
# Companion map (legacy path used by tests + fallback)
# --------------------------------------------------------------------

def test_build_companion_map_keys_by_player_stat_line():
    props = [
        {"player_name": "X", "stat_type": "PTS", "line": 22.5, "recommendation": "OVER", "dk_odds": -110},
        {"player_name": "X", "stat_type": "PTS", "line": 22.5, "recommendation": "UNDER", "dk_odds": -110},
        {"player_name": "Y", "stat_type": "AST", "line": 6.5,  "recommendation": "OVER", "dk_odds": -120},
    ]
    m = build_companion_map(props)
    assert ("X", "PTS", 22.5) in m
    assert m[("X", "PTS", 22.5)]["OVER"]["dk_odds"] == -110
    assert m[("X", "PTS", 22.5)]["UNDER"]["dk_odds"] == -110
    assert "UNDER" not in m[("Y", "AST", 6.5)]


def test_lookup_companion_sides_returns_both_sides():
    props = [
        {"player_name": "X", "stat_type": "PTS", "line": 22.5, "recommendation": "OVER", "dk_odds": -110},
        {"player_name": "X", "stat_type": "PTS", "line": 22.5, "recommendation": "UNDER", "dk_odds": -110},
    ]
    m = build_companion_map(props)
    over, under = lookup_companion_sides(props[0], m)
    assert over["recommendation"] == "OVER"
    assert under["recommendation"] == "UNDER"


def test_lookup_companion_sides_uses_stat_type_extracted():
    """MLB `live_props` writes `stat_type_extracted` (not `stat_type`).
    Both paths must resolve identically."""
    props = [
        {"player_name": "X", "stat_type_extracted": "Hits", "line": 1.5, "recommendation": "OVER", "dk_odds": -110},
        {"player_name": "X", "stat_type_extracted": "Hits", "line": 1.5, "recommendation": "UNDER", "dk_odds": -110},
    ]
    m = build_companion_map(props)
    assert ("X", "Hits", 1.5) in m


# --------------------------------------------------------------------
# Persistence contract (tp + edge + meta in _SCORE_OUTPUT_FIELDS)
# --------------------------------------------------------------------

def test_tp_fields_are_in_score_output_projection():
    from services.scoring.prop_scores_store import _SCORE_OUTPUT_FIELDS
    for field in (
        "tp", "edge_pct", "tp_books_used", "tp_books_list",
        "tp_method", "tp_unavailable",
    ):
        assert field in _SCORE_OUTPUT_FIELDS, (
            f"{field} missing from _SCORE_OUTPUT_FIELDS — TP result will "
            f"be dropped at persistence time"
        )
