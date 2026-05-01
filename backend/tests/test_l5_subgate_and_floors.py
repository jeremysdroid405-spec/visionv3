"""Universal L5 sub-gate + NBA Safe Haven 80 floor — Commit 2 (2026-05-01).

Spec recap:
  • L5 sub-gate is universal: when `hit_rate_l5` is populated (i.e. the
    adapter had ≥4 games of recent form), the gate fails iff
    `hit_rate_l5 < hit_rate_gate.min`.
  • NBA Safe Haven `hit_rate_gate.min` was lowered from 85 → 80.
  • Elite Vision override (`elite_vision.relax_hit_rate_to`, default
    75) ALSO applies the L5-≥-relaxed-floor rule, so the rescue path
    can't re-introduce the slumping-player bug.

Mutation-test compatible: each assertion locks down exactly one
constant. Flip the floor in `thresholds.py` from 80 → 75 → exactly
`test_safe_haven_floor_is_80` fails. Flip the L5 sub-gate's
`>=` comparator in `engine.py` → exactly `test_l5_below_floor_fails`
fails. Etc.
"""

from typing import Optional

import pytest

from services.scoring.gates.engine import UniversalGateEngine, get_engine
from services.scoring.gates.schema import (
    GateEvalResult, NormalizedMetrics, ReasonCode,
)
from services.scoring.gates.thresholds import resolve_thresholds


def _eval(
    *,
    sport: str = "nba",
    tier: str = "safe_haven",
    side: str = "OVER",
    stat_family: str = "pts",
    hit_rate: float = 90.0,
    hit_rate_l5: Optional[float] = None,
    hit_rate_l10: Optional[float] = None,
    cv: Optional[float] = 0.35,
    vision_score: Optional[float] = 95.0,
    edge_pct: Optional[float] = 10.0,
    tp: Optional[float] = 60.0,
    line: float = 20.0,
    projection: float = 25.0,
    p_model_pct: Optional[float] = 70.0,
    book_count: int = 4,
    reference_book: str = "dk",
    reference_odds: int = -300,
) -> GateEvalResult:
    """Build NormalizedMetrics + run the gate engine.

    Provides hand-tuned defaults that PASS every NBA Safe Haven gate
    so that individual tests can flip ONE field at a time and observe
    the consequence.
    """
    cfg = resolve_thresholds(sport, tier, stat_family, side=side)
    metrics = NormalizedMetrics(
        sport=sport,
        tier=tier,
        stat_family=stat_family,
        side=side,
        reference_book=reference_book,
        reference_odds=reference_odds,
        book_count=book_count,
        tp=tp,
        hit_rate=hit_rate,
        hit_rate_l20=hit_rate,
        hit_rate_l10=hit_rate_l10,
        hit_rate_l5=hit_rate_l5,
        hit_rate_sample_size=20,
        cv=cv,
        edge_pct=edge_pct,
        vision_score=vision_score,
        line=line,
        p_model_pct=p_model_pct,
        extras={
            "projection": projection,
            "mu_recency_blend_l20": projection,
            "tp_source": "devig",
        },
    )
    return get_engine().evaluate(metrics)


# --------------------------------------------------------------------------
# Threshold lockdown
# --------------------------------------------------------------------------
def test_safe_haven_floor_is_80():
    """Locks the NBA Safe Haven hit_rate_gate floor at 80.0."""
    cfg = resolve_thresholds("nba", "safe_haven", "pts", side="OVER")
    assert cfg["hit_rate_gate"]["min"] == 80.0


def test_safe_haven_floor_not_85():
    """Negative lockdown — the previous value (85) must NOT be active.
    Mutation guard against a silent revert."""
    cfg = resolve_thresholds("nba", "safe_haven", "pts", side="OVER")
    assert cfg["hit_rate_gate"]["min"] != 85.0


# --------------------------------------------------------------------------
# Universal L5 sub-gate behaviour
# --------------------------------------------------------------------------
def test_l5_below_floor_fails_safe_haven():
    """Tobias-style pick: L20=90% (passes), L5=40% (recent slump). MUST FAIL."""
    r = _eval(hit_rate=90.0, hit_rate_l5=40.0)
    assert not r.passed, "L5 sub-gate must reject slumping players."
    assert r.reason_code == ReasonCode.HIT_RATE_FAIL
    detail = r.gate_details["hit_rate_gate"]
    assert detail.passed is False
    assert detail.note and "l5_below_l20_floor" in detail.note


def test_l5_at_floor_passes_safe_haven():
    """Boundary: L5 == floor (80) → PASS."""
    r = _eval(hit_rate=90.0, hit_rate_l5=80.0)
    assert r.passed, f"L5=floor should pass; failed={r.failed_gates}"


def test_l5_above_floor_passes_safe_haven():
    """L5 100% with L20 90% — comfortable PASS."""
    r = _eval(hit_rate=90.0, hit_rate_l5=100.0)
    assert r.passed


def test_l5_none_does_not_block_gate():
    """When L5 is None (insufficient recent sample), gate ignores L5."""
    r = _eval(hit_rate=90.0, hit_rate_l5=None)
    assert r.passed


def test_l5_subgate_universal_across_tiers_front_lines():
    """Universal — applies to NBA Front Lines too. FL floor is 70."""
    r = _eval(
        tier="front_lines",
        hit_rate=80.0,
        hit_rate_l5=50.0,    # 50 < 70 → fail
        edge_pct=8.0,
        tp=70.0,
        cv=0.35,
        line=20.0,
        projection=22.0,
        reference_odds=-150,
        p_model_pct=78.0,
    )
    assert not r.passed
    assert "hit_rate_gate" in r.failed_gates
    assert r.gate_details["hit_rate_gate"].note and \
        "l5_below_l20_floor" in r.gate_details["hit_rate_gate"].note


def test_l5_subgate_does_not_fire_when_window_is_l5_explicit():
    """If a config explicitly asked for `window=l5`, the sub-gate must
    NOT double-evaluate (would otherwise compare L5 to itself)."""
    cfg = {"min": 80.0, "window": "l5"}
    metrics = NormalizedMetrics(
        sport="nba", tier="safe_haven", stat_family="pts",
        hit_rate=70.0, hit_rate_l20=70.0, hit_rate_l5=85.0,
        cv=0.3, edge_pct=10.0, vision_score=95.0,
        line=20.0, p_model_pct=70.0, book_count=4,
        reference_book="dk", reference_odds=-300, tp=60.0,
    )
    detail = UniversalGateEngine._eval_hit_rate(cfg, metrics)
    # actual = L5 = 85 ≥ 80 → pass; sub-gate was NOT consulted.
    assert detail.actual == 85.0
    assert detail.passed


def test_l5_subgate_can_be_disabled_per_tier():
    """`enforce_l5_subgate: False` opts out of the sub-gate."""
    cfg = {"min": 80.0, "window": "default", "enforce_l5_subgate": False}
    metrics = NormalizedMetrics(
        sport="nba", tier="safe_haven", stat_family="pts",
        hit_rate=90.0, hit_rate_l20=90.0, hit_rate_l5=10.0,
        cv=0.3, edge_pct=10.0, vision_score=95.0,
        line=20.0, p_model_pct=70.0, book_count=4,
        reference_book="dk", reference_odds=-300, tp=60.0,
    )
    detail = UniversalGateEngine._eval_hit_rate(cfg, metrics)
    assert detail.passed


# --------------------------------------------------------------------------
# Elite Vision override + L5 sub-gate interaction
# --------------------------------------------------------------------------
def test_elite_vision_override_still_requires_l5_above_relaxed_floor():
    """Elite Vision relaxes HR floor 80 → 75. L5 must ALSO be ≥75.
    A pick with L20=78 (passes relaxed floor) but L5=60 must NOT be
    rescued."""
    r = _eval(
        hit_rate=78.0,        # below 80 base, would trigger override
        hit_rate_l5=60.0,     # below relaxed 75
        cv=0.30,              # ≤ 0.35 elite_vision precondition
        vision_score=92.0,    # ≥ 90 elite_vision precondition
    )
    assert not r.passed, (
        f"Override must respect L5 floor; failed={r.failed_gates}"
    )


def test_elite_vision_override_passes_when_l5_clears_relaxed_floor():
    """Override fires correctly when ALL preconditions including L5
    relaxed-floor are satisfied."""
    r = _eval(
        hit_rate=78.0,        # below 80, override needed
        hit_rate_l5=80.0,     # ≥ relaxed 75 ✅
        cv=0.30,              # ≤ 0.35 ✅
        vision_score=92.0,    # ≥ 90 ✅
    )
    assert r.passed, f"Should pass via override; failed={r.failed_gates}"
    assert r.gate_details["hit_rate_gate"].note and \
        "elite_vision" in r.gate_details["hit_rate_gate"].note


# --------------------------------------------------------------------------
# Cross-tier: MLB inherits the universal sub-gate when L5 is populated
# --------------------------------------------------------------------------
def test_l5_subgate_universal_for_mlb_too():
    """MLB Safe Haven hits configured hr_min via stat-family map. The
    L5 sub-gate is universal — populating L5 below the L20 floor must
    also fail MLB."""
    metrics = NormalizedMetrics(
        sport="mlb", tier="safe_haven", stat_family="hits",
        hit_rate=85.0, hit_rate_l20=85.0,
        hit_rate_l5=20.0,  # tanking → must fail
        cv=0.5, edge_pct=10.0, vision_score=95.0,
        line=0.5, p_model_pct=80.0, book_count=4,
        reference_book="dk", reference_odds=-300, tp=70.0,
        avg_hit_margin=0.6, avg_miss_margin=0.4,
    )
    cfg = {"min": 70.0, "window": "default"}
    detail = UniversalGateEngine._eval_hit_rate(cfg, metrics)
    assert detail.passed is False
    assert detail.note and "l5_below_l20_floor" in detail.note
