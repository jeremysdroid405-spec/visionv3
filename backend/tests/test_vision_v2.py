"""
Vision Score v2 — Validation Suite
==================================

Locks down the four mandatory validation cases plus the directional
enforcement contract.

  1. Donovan Clingan OVER REB 8.5 (proj < line) → v2 LOW, alignment < 0
  2. Strong OVER (proj > line + high p) → v2 HIGH, alignment > 0
  3. Strong UNDER (proj < line + high under-p) → v2 HIGH, alignment > 0
  4. High edge but low probability → v2 MODERATE (not elite)

Plus invariance:
  • Wrong-side picks ALWAYS scored < right-side equivalents.
  • Vision_v2 is independent of vision_score (v1) — proven by the
    Clingan example: v1 ≈ 99 (top-of-slate) BUT v2 should be ≪ 50
    because his projection sits below the line.
  • Pure function: deterministic, no hidden state.
"""
from __future__ import annotations

import pytest

from services.scoring.vision_v2 import (
    DEFAULT_WEIGHTS,
    compute_direction,
    compute_vision_v2,
)


# ─── Validation case #1 — Clingan REB 8.5 OVER ───────────────────────
def test_1_clingan_over_reb_8_5_low_v2_negative_alignment():
    """Live data:
        projection=7.86, line=8.5, sigma=2.458, p_true_active=0.80
        tp=34.1, edge_pct=45.9, cv=0.316, hit_rate=80, books=3
    Old vision_score = 99.5 (percentile of vision_raw — slate-relative).
    v2 expectation: LOW because projection (7.86) < line (8.5) for OVER.
    """
    out = compute_vision_v2(
        side="OVER", projection=7.86, line=8.5, sigma=2.458,
        p_true_active=0.80, tp=34.1, edge_pct=45.9,
        cv=0.316, hit_rate=80.0, hit_rate_sample_size=20,
        books_count=3, tp_books_used=1, tp_source="devig",
    )
    # Direction: 7.86 - 8.5 = -0.64 → margin negative → alignment negative
    assert out["vision_v2_direction_margin"] == pytest.approx(-0.64, abs=0.01)
    assert out["vision_direction_alignment"] < 0.0
    # v2 must score significantly lower than the old 99.5
    assert out["vision_score_v2"] < 50.0, (
        f"Clingan v2 should be LOW (< 50) for wrong-side pick, "
        f"got {out['vision_score_v2']}"
    )
    # Direction gate must have multiplied raw score down (asymmetric
    # quadratic gate: ds=0.435 → gate=0.757 — well below the 1.0
    # full-credit a right-side pick gets).
    assert out["vision_v2_dir_gate"] < 0.85


# ─── Validation case #2 — Strong OVER ────────────────────────────────
def test_2_strong_over_high_v2():
    """Projection well above line, high probability, low CV.
    Mirror of a textbook elite OVER pick."""
    out = compute_vision_v2(
        side="OVER", projection=22.0, line=18.5, sigma=3.0,
        p_true_active=0.78, tp=58.0, edge_pct=20.0,
        cv=0.25, hit_rate=85.0, hit_rate_sample_size=20,
        books_count=4, tp_books_used=3, tp_source="devig",
    )
    assert out["vision_direction_alignment"] > 0.5
    assert out["vision_score_v2"] >= 65.0, (
        f"Strong OVER should score HIGH, got {out['vision_score_v2']}"
    )


# ─── Validation case #3 — Strong UNDER ───────────────────────────────
def test_3_strong_under_high_v2():
    """Projection well below line + high p_true_active for UNDER side.
    Mirror of #2 but on the UNDER side."""
    out = compute_vision_v2(
        side="UNDER", projection=14.0, line=18.5, sigma=3.0,
        p_true_active=0.78, tp=58.0, edge_pct=20.0,
        cv=0.25, hit_rate=85.0, hit_rate_sample_size=20,
        books_count=4, tp_books_used=3, tp_source="devig",
    )
    assert out["vision_direction_alignment"] > 0.5
    assert out["vision_score_v2"] >= 65.0, (
        f"Strong UNDER should score HIGH, got {out['vision_score_v2']}"
    )


# ─── Validation case #4 — High edge, low probability → MODERATE ──────
def test_4_high_edge_low_probability_moderate_v2():
    """Edge is huge (40%), but the model probability is modest (0.55).
    v2 must score MODERATE — not elite — because the dominant signals
    are direction + probability, not edge."""
    out = compute_vision_v2(
        side="OVER", projection=10.5, line=10.0, sigma=3.0,
        p_true_active=0.55, tp=15.0, edge_pct=40.0,
        cv=0.40, hit_rate=70.0, hit_rate_sample_size=20,
        books_count=2, tp_books_used=1, tp_source="devig",
    )
    assert out["vision_score_v2"] < 70.0, (
        f"High-edge low-prob pick must NOT be elite, got "
        f"{out['vision_score_v2']}"
    )
    assert out["vision_score_v2"] > 25.0, (
        f"…but should still score MODERATE, got {out['vision_score_v2']}"
    )


# ─── Directional enforcement — wrong-side ALWAYS underscores right-side
def test_wrong_side_always_underscores_right_side():
    """Same metrics except direction flipped → wrong-side score MUST
    be strictly less than right-side score."""
    common = dict(
        projection=22.0, line=18.5, sigma=3.0,
        p_true_active=0.78, tp=58.0, edge_pct=20.0,
        cv=0.25, hit_rate=85.0, hit_rate_sample_size=20,
        books_count=4, tp_books_used=3, tp_source="devig",
    )
    right = compute_vision_v2(side="OVER",  **common)
    wrong = compute_vision_v2(side="UNDER", **common)
    assert wrong["vision_score_v2"] < right["vision_score_v2"]
    assert wrong["vision_direction_alignment"] < 0.0
    assert right["vision_direction_alignment"] > 0.0


# ─── Independence: v2 is NOT a transform of v1 / TP / edge ───────────
def test_v2_clingan_low_when_v1_would_be_high():
    """Sanity-check: a real production pick can have huge edge but
    wrong-side projection. v1's percentile would put this near the top
    of the slate (99.5); v2 must NOT.

    The Clingan #1 case proves this: v1 was 99.5, v2 should be < 50."""
    out = compute_vision_v2(
        side="OVER", projection=7.86, line=8.5, sigma=2.458,
        p_true_active=0.80, tp=34.1, edge_pct=45.9,
        cv=0.316, hit_rate=80.0, hit_rate_sample_size=20,
        books_count=3, tp_books_used=1, tp_source="devig",
    )
    # The most important decoupling: v2 must differ from v1 by ≥ 30 pts
    # on this exact pick. v1=99.5, v2 is computed below 50.
    assert abs(out["vision_score_v2"] - 99.5) > 40.0
    # And v2 must NOT trivially equal TP (a naive composite would).
    assert abs(out["vision_score_v2"] - 34.1) > 5.0


# ─── Direction-margin sign convention ────────────────────────────────
def test_direction_margin_sign_convention():
    over = compute_direction(projection=20.0, line=15.0,
                             sigma=3.0, side="OVER")
    under = compute_direction(projection=20.0, line=15.0,
                              sigma=3.0, side="UNDER")
    # OVER: projection - line = +5 → strong agreement
    assert over["direction_margin"] == 5.0
    assert over["direction_alignment"] > 0.5
    # UNDER same projection: line - projection = -5 → strong disagreement
    assert under["direction_margin"] == -5.0
    assert under["direction_alignment"] < -0.5


# ─── Volatility penalty bites high-CV picks ──────────────────────────
def test_high_cv_picks_get_volatility_penalty():
    base = compute_vision_v2(
        side="OVER", projection=22.0, line=18.5, sigma=3.0,
        p_true_active=0.78, tp=58.0, edge_pct=20.0,
        cv=0.25, hit_rate=85.0, hit_rate_sample_size=20,
        books_count=4, tp_books_used=3, tp_source="devig",
    )
    noisy = compute_vision_v2(
        side="OVER", projection=22.0, line=18.5, sigma=3.0,
        p_true_active=0.78, tp=58.0, edge_pct=20.0,
        cv=0.95, hit_rate=85.0, hit_rate_sample_size=20,
        books_count=4, tp_books_used=3, tp_source="devig",
    )
    assert noisy["vision_score_v2"] < base["vision_score_v2"]
    assert noisy["vision_volatility_penalty"] > base["vision_volatility_penalty"]


# ─── Sub-coinflip probability scores 0 on the prob component ─────────
def test_sub_coinflip_probability_zero_component():
    out = compute_vision_v2(
        side="OVER", projection=22.0, line=18.5, sigma=3.0,
        p_true_active=0.45, tp=58.0, edge_pct=0.0,
        cv=0.25, hit_rate=50.0, hit_rate_sample_size=20,
        books_count=2, tp_books_used=2, tp_source="devig",
    )
    assert out["vision_probability_component"] == 0.0


# ─── Edge component capped — outlier edge can't dominate ─────────────
def test_extreme_edge_does_not_dominate():
    """Edge=200 (impossible in production but tests the cap) MUST not
    push v2 above what a 30%-edge pick with same other inputs scores."""
    capped = compute_vision_v2(
        side="OVER", projection=22.0, line=18.5, sigma=3.0,
        p_true_active=0.78, tp=58.0, edge_pct=30.0,
        cv=0.25, hit_rate=85.0, hit_rate_sample_size=20,
        books_count=4, tp_books_used=3, tp_source="devig",
    )
    extreme = compute_vision_v2(
        side="OVER", projection=22.0, line=18.5, sigma=3.0,
        p_true_active=0.78, tp=58.0, edge_pct=200.0,
        cv=0.25, hit_rate=85.0, hit_rate_sample_size=20,
        books_count=4, tp_books_used=3, tp_source="devig",
    )
    assert capped["vision_edge_component"] == extreme["vision_edge_component"] == 1.0


# ─── Component sum matches stamped weights ───────────────────────────
def test_default_weights_sum_to_one():
    """Lockdown: positive components weights MUST sum to 1.00 so the
    score is naturally bounded to [0, 100] (modulo penalty)."""
    pos = sum(v for k, v in DEFAULT_WEIGHTS.items()
              if k != "volatility_penalty")
    assert abs(pos - 1.0) < 1e-9
