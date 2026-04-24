"""NBA War Zone gating — tested natively via UniversalGateEngine.

All War Zone logic lives in `services/scoring/gates/thresholds.py`
under `_NBA_WAR_ZONE_BASE`. These tests call the engine directly so
nothing can bypass it (2026-04-24 spec).
"""
from __future__ import annotations

import pytest

from services.scoring.gates import NormalizedMetrics, ReasonCode, get_engine


def _metrics(
    *,
    stat_family: str = "pts",
    cv: float = 0.30,
    hit_rate: float = 70.0,
    vision_score: float = 92.0,
    tp_source: str = "devig",
    reference_odds: int = 250,
    book_count: int = 3,
) -> NormalizedMetrics:
    return NormalizedMetrics(
        sport="nba",
        tier="war_zone",
        stat_family=stat_family,
        side="OVER",
        reference_book="draftkings",
        reference_odds=reference_odds,
        book_count=book_count,
        cv=cv,
        hit_rate=hit_rate,
        hit_rate_l20=hit_rate,
        vision_score=vision_score,
        tp_source=tp_source,
    )


engine = get_engine()


# -------------------- CV caps (stat-aware) -------------------------------

def test_pts_cv_cap_exceeded_rejects():
    result = engine.evaluate(_metrics(stat_family="pts", cv=0.50))
    assert result.passed is False
    assert result.reason_code == ReasonCode.CV_FAIL


def test_pts_cv_cap_at_boundary_passes():
    result = engine.evaluate(_metrics(stat_family="pts", cv=0.45))
    assert result.passed is True


def test_reb_cv_cap_is_0_55():
    assert engine.evaluate(_metrics(stat_family="reb", cv=0.55)).passed is True
    assert engine.evaluate(_metrics(stat_family="reb", cv=0.56)).passed is False


def test_threes_cv_cap_is_0_75():
    assert engine.evaluate(_metrics(stat_family="threes", cv=0.75)).passed is True
    assert engine.evaluate(_metrics(stat_family="threes", cv=0.76)).passed is False


def test_combo_pra_uses_0_45_cap():
    assert engine.evaluate(_metrics(stat_family="pra", cv=0.45)).passed is True
    assert engine.evaluate(_metrics(stat_family="pra", cv=0.46)).passed is False


def test_combo_reb_ast_uses_0_55_cap():
    assert engine.evaluate(_metrics(stat_family="reb_ast", cv=0.55)).passed is True
    assert engine.evaluate(_metrics(stat_family="reb_ast", cv=0.56)).passed is False


def test_unknown_stat_family_fails_closed_on_cv_gate():
    result = engine.evaluate(_metrics(stat_family="player_first_basket", cv=0.10))
    assert result.passed is False
    assert result.reason_code == ReasonCode.CV_FAIL


# -------------------- Vision-score gate (per-tp_source) ------------------

def test_devig_vs_below_85_rejects():
    result = engine.evaluate(
        _metrics(tp_source="devig", vision_score=84.9, reference_odds=250)
    )
    assert result.passed is False
    assert result.reason_code == ReasonCode.VISION_SCORE_FAIL


def test_devig_vs_at_85_passes():
    result = engine.evaluate(
        _metrics(tp_source="devig", vision_score=85.0, reference_odds=250)
    )
    assert result.passed is True


def test_one_sided_requires_vs_90_or_hr_60():
    # Both below → fail
    result = engine.evaluate(_metrics(
        tp_source="one_sided", vision_score=89.9, hit_rate=59.9,
        reference_odds=300,
    ))
    assert result.passed is False
    assert result.reason_code == ReasonCode.VISION_SCORE_FAIL


def test_one_sided_passes_with_vs_90():
    result = engine.evaluate(_metrics(
        tp_source="one_sided", vision_score=90.0, hit_rate=55.0,
        reference_odds=300,
    ))
    assert result.passed is True


def test_one_sided_passes_with_hr_60():
    result = engine.evaluate(_metrics(
        tp_source="one_sided", vision_score=80.0, hit_rate=60.0,
        reference_odds=300,
    ))
    assert result.passed is True


def test_missing_tp_source_fails_closed():
    result = engine.evaluate(_metrics(
        tp_source="", vision_score=95.0, hit_rate=80.0,
        reference_odds=300,
    ))
    assert result.passed is False
    assert result.reason_code == ReasonCode.VISION_SCORE_FAIL


def test_vision_score_deferred_when_missing():
    """First-pass behaviour: vision_score=None pass-through with note."""
    m = NormalizedMetrics(
        sport="nba", tier="war_zone", stat_family="pts", side="OVER",
        reference_book="draftkings", reference_odds=250, book_count=2,
        cv=0.30, hit_rate=70.0, vision_score=None, tp_source="devig",
    )
    result = engine.evaluate(m)
    vs_detail = result.gate_details["vision_score_gate"]
    assert vs_detail.passed is True
    assert vs_detail.note == "vision_score_deferred_to_slate_pass"


# -------------------- Market-trap gate -----------------------------------

def test_market_trap_rejects_mid_odds_weak_signal():
    # Odds in [150, 220] AND HR < 60 AND VS < 90 → trap
    result = engine.evaluate(_metrics(
        reference_odds=175, hit_rate=58.0, vision_score=88.0,
        tp_source="devig",
    ))
    # devig-vs-85 passes, but market_trap fails
    assert result.passed is False
    assert result.reason_code == ReasonCode.MARKET_TRAP_FAIL


def test_market_trap_boundary_odds_150_rejects():
    result = engine.evaluate(_metrics(
        reference_odds=150, hit_rate=58.0, vision_score=88.0,
        tp_source="devig",
    ))
    assert result.passed is False
    assert result.reason_code == ReasonCode.MARKET_TRAP_FAIL


def test_market_trap_boundary_odds_220_rejects():
    result = engine.evaluate(_metrics(
        reference_odds=220, hit_rate=58.0, vision_score=88.0,
        tp_source="devig",
    ))
    assert result.passed is False
    assert result.reason_code == ReasonCode.MARKET_TRAP_FAIL


def test_market_trap_bypassed_above_band():
    result = engine.evaluate(_metrics(
        reference_odds=225, hit_rate=58.0, vision_score=88.0,
        tp_source="devig",
    ))
    assert result.passed is True


def test_market_trap_bypassed_by_strong_hr():
    # Odds in band but HR >= 60 → passes
    result = engine.evaluate(_metrics(
        reference_odds=175, hit_rate=60.0, vision_score=88.0,
        tp_source="devig",
    ))
    assert result.passed is True


def test_market_trap_bypassed_by_strong_vs():
    # Odds in band but VS >= 90 → passes
    result = engine.evaluate(_metrics(
        reference_odds=175, hit_rate=58.0, vision_score=90.0,
        tp_source="devig",
    ))
    assert result.passed is True


# -------------------- Happy path + longshot ------------------------------

def test_devig_longshot_with_strong_signal_passes():
    result = engine.evaluate(_metrics(
        stat_family="pra", cv=0.40, hit_rate=65.0,
        vision_score=93.0, tp_source="devig", reference_odds=700,
    ))
    assert result.passed is True


def test_one_sided_longshot_with_strong_signal_passes():
    result = engine.evaluate(_metrics(
        stat_family="pts", cv=0.30, hit_rate=65.0,
        vision_score=93.0, tp_source="one_sided", reference_odds=700,
    ))
    assert result.passed is True


def test_full_ordering_cv_first_rejects_before_vision():
    # CV fails → reason_code is CV_FAIL even if vision would pass.
    result = engine.evaluate(_metrics(
        stat_family="pts", cv=0.99, hit_rate=70.0,
        vision_score=95.0, tp_source="devig", reference_odds=300,
    ))
    assert result.passed is False
    assert result.reason_code == ReasonCode.CV_FAIL
