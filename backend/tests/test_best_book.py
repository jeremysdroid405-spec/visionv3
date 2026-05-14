"""Regression — universal best-book / market-shopping edge (2026-05-13)."""
from __future__ import annotations

import pytest

from services.scoring.best_book import (
    american_to_implied,
    better_american_odds,
    compute_best_book_metrics,
)


# ── american_to_implied ─────────────────────────────────────────────

def test_american_to_implied_negative():
    assert american_to_implied(-110) == pytest.approx(0.5238, abs=0.001)
    assert american_to_implied(-300) == pytest.approx(0.7500, abs=0.001)
    assert american_to_implied(-400) == pytest.approx(0.8000, abs=0.001)


def test_american_to_implied_positive():
    assert american_to_implied(+100) == 0.5
    assert american_to_implied(+150) == pytest.approx(0.4, abs=0.001)
    assert american_to_implied(+200) == pytest.approx(0.3333, abs=0.001)


def test_american_to_implied_handles_invalid_input():
    assert american_to_implied(None) is None
    assert american_to_implied("foo") is None
    assert american_to_implied(0) is None


# ── better_american_odds ────────────────────────────────────────────

def test_better_odds_negative_pair():
    """-300 (0.75 implied) is better than -400 (0.80)."""
    assert better_american_odds(-300, -400) == -300
    assert better_american_odds(-400, -300) == -300


def test_better_odds_positive_better_than_negative():
    """+120 (0.45) beats -110 (0.524)."""
    assert better_american_odds(120, -110) == 120


def test_better_odds_none_fallback():
    assert better_american_odds(None, -110) == -110
    assert better_american_odds(-110, None) == -110
    assert better_american_odds(None, None) is None


def test_better_odds_picks_higher_payout_positive():
    """+250 pays more than +150."""
    assert better_american_odds(250, 150) == 250


# ── compute_best_book_metrics (OVER) ────────────────────────────────

def test_best_book_metrics_over_basic_three_books():
    prop = {"dk_odds": -300, "fd_odds": -400, "mgm_odds": -350}
    out = compute_best_book_metrics(prop, fair_prob=0.80)
    assert out["best_book"] == "draftkings"
    assert out["best_book_odds"] == -300
    assert out["best_book_implied_probability"] == pytest.approx(0.75, abs=0.001)
    assert out["books_available_count"] == 3
    # spread = 0.80 - 0.75 = 0.05
    assert out["market_spread"] == pytest.approx(0.05, abs=0.001)
    assert out["market_spread_label"] == "moderate"
    # edge = fair (0.80) - best_implied (0.75) = +0.05
    assert out["best_book_edge"] == pytest.approx(0.05, abs=0.0001)


def test_best_book_metrics_picks_plus_money_over_chalk():
    """+120 is better for the bettor than -110, even though +120 is
    further from zero."""
    prop = {"dk_odds": -110, "fd_odds": 120, "mgm_odds": -130}
    out = compute_best_book_metrics(prop, fair_prob=0.55)
    assert out["best_book"] == "fanduel"
    assert out["best_book_odds"] == 120


# ── compute_best_book_metrics (UNDER) ───────────────────────────────

def test_best_book_metrics_under_side_works_identically():
    """`{book}_odds` is always THIS side's price by canonical
    contract, so UNDER works through the same code path."""
    prop = {  # UNDER row: each field is the UNDER price
        "dk_odds": -110,
        "fd_odds": -125,
        "mgm_odds": -105,
        "csr_odds": +110,
    }
    out = compute_best_book_metrics(prop, fair_prob=0.52)
    # +110 = 0.476 implied (lowest). The legacy-field stem strips
    # "_price" so Caesars' display key is "caesars" (rebranded
    # public-facing name; the Odds-API anchor key "williamhill_us"
    # is preserved separately in `books_anchored`).
    assert out["best_book"] == "caesars"
    assert out["best_book_odds"] == 110
    assert out["books_available_count"] == 4
    # edge = 0.52 - 0.476 = +0.044
    assert out["best_book_edge"] == pytest.approx(0.044, abs=0.001)


# ── compute_best_book_metrics — degenerate inputs ──────────────────

def test_best_book_no_books_quoting():
    out = compute_best_book_metrics({"foo": "bar"}, fair_prob=0.70)
    assert out["best_book"] is None
    assert out["best_book_odds"] is None
    assert out["best_book_implied_probability"] is None
    assert out["best_book_edge"] is None
    assert out["market_spread"] is None
    assert out["market_spread_label"] == "unknown"
    assert out["books_available_count"] == 0


def test_best_book_no_fair_prob_still_returns_best_book():
    prop = {"dk_odds": -300, "fd_odds": -400}
    out = compute_best_book_metrics(prop, fair_prob=None)
    assert out["best_book"] == "draftkings"
    assert out["best_book_edge"] is None  # no fair_prob → no edge
    assert out["books_available_count"] == 2


# ── Market-spread bucketing ─────────────────────────────────────────

def test_spread_label_wide():
    # implied 0.85 (-560) ↔ 0.75 (-300) → spread 0.10
    prop = {"dk_odds": -300, "fd_odds": -560}
    out = compute_best_book_metrics(prop, fair_prob=0.80)
    assert out["market_spread_label"] == "wide"


def test_spread_label_moderate():
    # 0.75 (-300) ↔ 0.80 (-400) → spread 0.05
    prop = {"dk_odds": -300, "fd_odds": -400}
    out = compute_best_book_metrics(prop, fair_prob=0.80)
    assert out["market_spread_label"] == "moderate"


def test_spread_label_tight():
    # 0.524 (-110) ↔ 0.55 (-122) → spread ~0.025
    prop = {"dk_odds": -110, "fd_odds": -122}
    out = compute_best_book_metrics(prop, fair_prob=0.54)
    assert out["market_spread_label"] == "tight"


def test_spread_label_single_book_is_zero():
    """Single quoting book → spread = 0.0 → tight."""
    prop = {"dk_odds": -300}
    out = compute_best_book_metrics(prop, fair_prob=0.80)
    assert out["market_spread"] == 0.0
    assert out["market_spread_label"] == "tight"


# ── Data-driven sport-agnostic ─────────────────────────────────────

def test_works_with_legacy_field_names():
    """The probe also reads legacy `{book}_price` form."""
    prop = {"draftkings_price": -300, "fanduel_price": -400}
    out = compute_best_book_metrics(prop, fair_prob=0.80)
    assert out["best_book"] == "draftkings"
    assert out["books_available_count"] == 2


def test_all_11_books_recognised():
    """Every book in tp_engine._BOOKS must be queryable."""
    prop = {
        "dk_odds": -110, "fd_odds": -112, "mgm_odds": -115,
        "bol_odds": -116, "csr_odds": -118,
        "eb_odds":  -120, "hrb_odds": -122, "brv_odds": -124,
        "prx_odds": -126, "bly_odds": -128, "flf_odds": -130,
    }
    out = compute_best_book_metrics(prop, fair_prob=0.55)
    assert out["books_available_count"] == 11
    assert out["best_book"] == "draftkings"  # -110 is lowest implied


def test_immutable_returns_no_mutation_of_prop():
    """Helper must not mutate the input dict."""
    prop = {"dk_odds": -300, "fd_odds": -400}
    snapshot = dict(prop)
    compute_best_book_metrics(prop, fair_prob=0.80)
    assert prop == snapshot


# ── total_edge (model alpha + shopping alpha combined, 2026-05-14) ─

def test_total_edge_basic_positive():
    """total_edge = p_model - best_book_implied. Big when model
    disagrees with the cheapest book."""
    prop = {"dk_odds": -300, "fd_odds": -400}
    # best implied = 0.75. p_model = 0.90 → total_edge = +0.15
    out = compute_best_book_metrics(prop, fair_prob=0.80, p_model=0.90)
    assert out["total_edge"] == pytest.approx(0.15, abs=0.001)
    # And shopping edge (best_book_edge) is +0.05 (0.80 - 0.75).
    assert out["best_book_edge"] == pytest.approx(0.05, abs=0.001)


def test_total_edge_negative_when_model_lower_than_book():
    """Model probability lower than book's implied → negative total
    edge → avoid the bet."""
    prop = {"dk_odds": -300}  # implied 0.75
    out = compute_best_book_metrics(prop, fair_prob=0.75, p_model=0.60)
    # 0.60 - 0.75 = -0.15
    assert out["total_edge"] == pytest.approx(-0.15, abs=0.001)


def test_total_edge_none_without_p_model():
    """No p_model passed → total_edge is None (display-safe)."""
    prop = {"dk_odds": -110, "fd_odds": -120}
    out = compute_best_book_metrics(prop, fair_prob=0.55)
    assert out["total_edge"] is None
    # Other fields unaffected.
    assert out["best_book"] == "draftkings"
    assert out["best_book_edge"] is not None


def test_total_edge_none_when_no_books_available():
    out = compute_best_book_metrics({}, fair_prob=0.5, p_model=0.6)
    assert out["total_edge"] is None
    assert out["best_book"] is None


def test_total_edge_independent_of_fair_prob():
    """Mathematical contract: total_edge MUST be derived from p_model
    and best_book_implied only — NOT from fair_prob. Two calls with
    identical p_model + odds but different fair_prob values produce
    identical total_edge."""
    prop = {"dk_odds": -110, "fd_odds": -130}
    out_a = compute_best_book_metrics(prop, fair_prob=0.50, p_model=0.70)
    out_b = compute_best_book_metrics(prop, fair_prob=0.60, p_model=0.70)
    assert out_a["total_edge"] == out_b["total_edge"]
    # And shopping edge IS dependent on fair_prob (proves they're
    # measuring different things).
    assert out_a["best_book_edge"] != out_b["best_book_edge"]
