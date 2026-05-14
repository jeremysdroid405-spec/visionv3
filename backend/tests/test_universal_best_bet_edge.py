"""Universal Consensus Edge + Best Bet Edge — 2026-05-14 contract tests.

The math already exists from the 2026-05-13 best-book engine:
   edge_vs_fair = p_model − consensus_fair        → Consensus Edge
   total_edge   = p_model − best_book_implied     → Best Bet Edge
   best_book / best_book_odds / best_book_edge    → the better-priced book

These tests pin the universal contract:
   • is_better_american_odds works for every American odds case
   • the same engine produces identical output across sports
   • OVER and UNDER both pick the best-payout book (lowest implied)
   • side-aware fair_prob inversion is the caller's job
   • missing / partial book coverage degrades gracefully
"""
from __future__ import annotations

import pytest

from services.scoring.best_book import (
    american_to_implied,
    better_american_odds,
    compute_best_book_metrics,
    is_better_american_odds,
)


# ────────────────────────────────────────────────────────────────────
# is_better_american_odds — universal odds predicate
# ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("candidate, current_best, expected", [
    (+140, +110, True),    # bigger plus wins
    (+110, +140, False),
    (-105, -130, True),    # smaller minus wins
    (-130, -105, False),
    (-300, -400, True),    # -300 ⇒ 75%, -400 ⇒ 80%
    (-400, -300, False),
    (+150, -110, True),    # plus beats minus
    (-110, +150, False),
    (-110, -110, False),   # tie is NOT strictly better
])
def test_is_better_american_odds_canonical_cases(candidate, current_best, expected):
    assert is_better_american_odds(candidate, current_best) is expected


def test_is_better_american_odds_anything_beats_none():
    for c in (+110, -110, +500, -500):
        assert is_better_american_odds(c, None) is True


def test_is_better_american_odds_none_candidate_never_wins():
    for cur in (+110, -110, None):
        assert is_better_american_odds(None, cur) is False


def test_is_better_american_odds_zero_is_invalid():
    assert is_better_american_odds(0, -110) is False
    assert is_better_american_odds(-110, 0) is True


def test_better_american_odds_value_form_agrees():
    assert better_american_odds(+140, +110) == 140
    assert better_american_odds(-105, -130) == -105
    assert better_american_odds(-300, -400) == -300
    assert better_american_odds(None, -110) == -110
    assert better_american_odds(-110, None) == -110


# ────────────────────────────────────────────────────────────────────
# Side-aware selection — OVER + UNDER
# ────────────────────────────────────────────────────────────────────


def test_over_side_picks_highest_payout_book():
    """OVER prop: {book}_odds carries OVER prices. Best = lowest implied."""
    over_prop = {"dk_odds": -130, "fd_odds": -110, "mgm_odds": -150}
    out = compute_best_book_metrics(over_prop, fair_prob=0.50, p_model=0.55)
    assert out["best_book"] == "fanduel"
    assert out["best_book_odds"] == -110
    # No opposite-side odds on the winner → raw_one_sided fallback.
    # total_edge = p_model − raw_best_book_implied = 0.55 − 0.5238 ≈ 0.026
    assert out["total_edge"] == pytest.approx(0.0262, abs=0.001)
    assert out["best_bet_edge_source"] == "raw_one_sided"


def test_under_side_picks_highest_payout_book():
    """UNDER prop: {book}_odds carries UNDER prices. Same lowest-implied rule."""
    under_prop = {"dk_odds": +120, "fd_odds": -105, "mgm_odds": -120}
    out = compute_best_book_metrics(under_prop, fair_prob=0.50, p_model=0.42)
    assert out["best_book"] == "draftkings"
    assert out["best_book_odds"] == 120
    assert out["best_bet_edge_source"] == "raw_one_sided"  # no _opp fields supplied


def test_side_specific_fair_prob_inversion_is_callers_responsibility():
    """Caller passes fair_prob for THIS SIDE. Flipping side flips
    best_book_edge but never best_book_implied."""
    prop = {"dk_odds": -110}
    out_over  = compute_best_book_metrics(prop, fair_prob=0.55, p_model=0.55)
    out_under = compute_best_book_metrics(prop, fair_prob=0.45, p_model=0.45)
    assert out_over["best_book_implied_probability"] == out_under["best_book_implied_probability"]
    assert out_over["best_book_edge"] != out_under["best_book_edge"]


# ────────────────────────────────────────────────────────────────────
# 2026-05-14 — devig-basis fields + source tags
# ────────────────────────────────────────────────────────────────────


def test_devig_basis_when_best_book_has_both_sides():
    """When the winning book quotes BOTH sides, edge math runs on
    devigged probability — NOT raw implied."""
    prop = {
        # FanDuel quotes both sides → eligible for per-book devig.
        "fd_odds":     -110,    # raw 52.4%
        "fd_odds_opp": -110,    # opp 52.4%  → devig = 0.50 each
        "dk_odds":     -130,    # one-sided
    }
    out = compute_best_book_metrics(prop, fair_prob=0.55, p_model=0.60,
                                    fair_prob_source="devig")
    assert out["best_book"] == "fanduel"
    # raw 0.5238 but devig 0.50 → edges run on 0.50.
    assert out["best_book_raw_implied_probability"] == pytest.approx(0.5238, abs=0.001)
    assert out["best_book_devig_probability"] == pytest.approx(0.50, abs=0.001)
    assert out["best_bet_edge_source"] == "devig"
    assert out["shopping_edge_source"] == "devig_vs_devig"
    # total_edge = p_model (0.60) − devig (0.50) = +0.10
    assert out["total_edge"] == pytest.approx(0.10, abs=0.001)
    # best_book_edge (shopping) = fair (0.55) − devig (0.50) = +0.05
    assert out["best_book_edge"] == pytest.approx(0.05, abs=0.001)


def test_raw_one_sided_fallback_when_best_book_lacks_opposite():
    """If the best book has no opposite-side quote, edge math falls
    back to raw implied with explicit source tags."""
    prop = {"dk_odds": -110, "fd_odds": -150}  # no _opp fields
    out = compute_best_book_metrics(prop, fair_prob=0.50, p_model=0.55,
                                    fair_prob_source="devig")
    assert out["best_book"] == "draftkings"
    assert out["best_book_devig_probability"] is None
    assert out["best_book_raw_implied_probability"] == pytest.approx(0.5238, abs=0.001)
    assert out["best_bet_edge_source"] == "raw_one_sided"
    assert out["shopping_edge_source"] == "devig_vs_raw"


def test_consensus_edge_source_echoes_caller_fair_prob_source():
    """`consensus_edge_source` is whatever the caller passed
    (the scoring stack passes `tp_source`). Echo verifies the wiring."""
    prop = {"dk_odds": -110, "dk_odds_opp": -110}
    for src in ("devig", "one_sided", "raw_one_sided"):
        out = compute_best_book_metrics(prop, fair_prob=0.5, p_model=0.5,
                                        fair_prob_source=src)
        assert out["consensus_edge_source"] == src


def test_one_sided_consensus_passes_through_when_no_fair_prob():
    out = compute_best_book_metrics({"dk_odds": -110}, fair_prob=None,
                                    p_model=0.6, fair_prob_source="devig")
    assert out["consensus_edge_source"] is None  # no fair_prob → no edge
    assert out["best_book_edge"] is None
    assert out["shopping_edge_source"] is None


def test_best_book_selection_uses_raw_not_devig_for_bettor_payout():
    """Selection picks the book with the *best displayed price* (lowest
    raw implied), even if a different book has a tighter devig.
    Rationale: the bettor places the bet at the actual price; the
    devig is only a math device for edge basis."""
    prop = {
        # DK: raw 52.4%, devig 50%
        "dk_odds":     -110, "dk_odds_opp": -110,
        # FD: raw 47.6% (higher payout!), but no opposite → no devig
        "fd_odds":     +110,
    }
    out = compute_best_book_metrics(prop, fair_prob=0.50, p_model=0.55)
    # FanDuel wins on raw payout despite missing devig.
    assert out["best_book"] == "fanduel"
    assert out["best_book_odds"] == 110
    assert out["best_bet_edge_source"] == "raw_one_sided"


# ────────────────────────────────────────────────────────────────────
# Missing data & one-sided coverage
# ────────────────────────────────────────────────────────────────────


def test_missing_all_book_odds_returns_safe_nulls():
    out = compute_best_book_metrics({}, fair_prob=0.5, p_model=0.6)
    assert out["best_book"] is None
    assert out["best_book_odds"] is None
    assert out["best_book_implied_probability"] is None
    assert out["best_book_raw_implied_probability"] is None
    assert out["best_book_devig_probability"] is None
    assert out["best_book_edge"] is None
    assert out["total_edge"] is None
    assert out["market_spread"] is None
    assert out["market_spread_label"] == "unknown"
    assert out["books_available_count"] == 0
    assert out["best_bet_edge_source"] is None
    assert out["shopping_edge_source"] is None


def test_one_sided_prop_only_one_book_still_resolves_best_book():
    out = compute_best_book_metrics(
        {"dk_odds": -300}, fair_prob=0.78, p_model=0.80,
    )
    assert out["best_book"] == "draftkings"
    assert out["best_book_odds"] == -300
    # No opposite → raw_one_sided → total_edge = p_model − 0.75 = 0.05
    assert out["total_edge"] == pytest.approx(0.05, abs=0.001)
    assert out["best_bet_edge_source"] == "raw_one_sided"
    assert out["market_spread"] == pytest.approx(0.0, abs=1e-6)
    assert out["market_spread_label"] == "tight"


def test_partial_book_coverage_skips_missing_quotes():
    prop = {"dk_odds": None, "fd_odds": -120, "mgm_odds": -200, "caesars_odds": None}
    out = compute_best_book_metrics(prop, fair_prob=0.55, p_model=0.58)
    assert out["best_book"] == "fanduel"
    assert out["books_available_count"] == 2


# ────────────────────────────────────────────────────────────────────
# Sport-agnostic — no sport parameter, same inputs → same outputs
# ────────────────────────────────────────────────────────────────────


def test_engine_is_sport_agnostic():
    """Identical inputs must produce identical outputs across NBA, MLB,
    NFL, or any future sport — the engine has no sport branch."""
    prop = {"dk_odds": -110, "fd_odds": +105, "mgm_odds": -120}
    args = {"fair_prob": 0.50, "p_model": 0.55}
    a = compute_best_book_metrics(prop, **args)
    b = compute_best_book_metrics(prop, **args)
    c = compute_best_book_metrics(prop, **args)
    assert a == b == c


# ────────────────────────────────────────────────────────────────────
# american_to_implied math sanity
# ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("american, implied", [
    (+100, 0.50), (+110, 0.4762), (+200, 0.3333),
    (-110, 0.5238), (-300, 0.75), (-400, 0.80),
])
def test_american_to_implied_is_correct(american, implied):
    assert american_to_implied(american) == pytest.approx(implied, abs=0.001)


def test_american_to_implied_handles_invalids():
    assert american_to_implied(None) is None
    assert american_to_implied(0) is None
    assert american_to_implied("not-a-number") is None
