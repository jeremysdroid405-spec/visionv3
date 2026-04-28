"""
NBA Safe Haven Conditional Override — FINAL Validation Suite
============================================================

Locks down the 6 spec proofs:

1. Deni Avdija PTS 19.5  → PASS via Vision override
2. Mitchell Robinson REB 3.5  → PASS via REB CV override
3. Stephon Castle 3PM 0.5  → PASS via 3PM CV override
4. Joel Embiid AST 2.5  → PASS via AST CV override
5. Paul George PTS 9.5  → PASS via PTS dominance CV bypass
6. A random low-HR / low-vision prop  → still FAIL

Plus invariance lockdowns:
  • Hard fails (TP / edge / market_structure) are NEVER overridden
  • One pick fires AT MOST one rule (no stacking)
  • Tiers without `__safe_haven_overrides__` see zero behaviour change
  • The override config produces an `__override_applied__` audit row
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from services.scoring.gates import NormalizedMetrics
from services.scoring.gates.engine import get_engine


def _eval(*, sport="nba", tier="safe_haven", stat_family,
          side="OVER", hit_rate, cv, vision_score, edge_pct=10.0,
          tp=70.0, line=10.5, mu_l20=None, is_alt=False,
          tp_source="devig", book_count=3,
          reference_book="dk", reference_odds=-300):
    extras: Dict[str, Any] = {}
    if mu_l20 is not None:
        extras["mu_recency_blend_l20"] = mu_l20

    m = NormalizedMetrics(
        sport=sport, tier=tier, stat_family=stat_family,
        side=side, reference_book=reference_book,
        reference_odds=reference_odds,
        book_count=book_count,
        tp=tp, hit_rate=hit_rate, hit_rate_l20=hit_rate,
        cv=cv, edge_pct=edge_pct,
        line=line, vision_score=vision_score,
        tp_source=tp_source, is_alt=is_alt,
        extras=extras,
    )
    return get_engine().evaluate(m)


def _override_name(result):
    od = result.gate_details.get("__override_applied__")
    if od is None:
        return None
    return (od.threshold or {}).get("name")


# ─── Spec proof 1: Avdija PTS 19.5 — Vision override ─────────────────
def test_1_avdija_pts_19_5_passes_via_vision_override():
    """Real reject row from snapshot:
        VS=93.8, CV=0.263, HR=75 (HR fails base 85 → vision rule
        relaxes to 75)."""
    r = _eval(stat_family="pts", hit_rate=75.0, cv=0.263,
              vision_score=93.8, line=19.5, mu_l20=25.4,
              edge_pct=22.6, tp=62.0)
    assert r.passed, f"Avdija should pass, failed_gates={r.failed_gates}"
    assert _override_name(r) == "elite_vision"
    # Confirm the rescued gate shows the override note.
    note = r.gate_details["hit_rate_gate"].note or ""
    assert "elite_vision" in note


# ─── Spec proof 2: M. Robinson REB 3.5 — REB CV override ─────────────
def test_2_robinson_reb_3_5_passes_via_reb_cv_override():
    """Real reject:
        Stat=REB, HR=100, CV=0.509 (base cap 0.45 fails).
        REB rule raises cap to 0.60."""
    r = _eval(stat_family="reb", hit_rate=100.0, cv=0.509,
              vision_score=97.7, line=3.5, mu_l20=7.5,
              edge_pct=22.2, tp=77.8, is_alt=True, tp_source="devig")
    assert r.passed, f"M. Robinson should pass, failed={r.failed_gates}"
    assert _override_name(r) == "stat_structure:reb"


# ─── Spec proof 3: Castle 3PM 0.5 — 3PM CV override ──────────────────
def test_3_castle_3pm_0_5_passes_via_3pm_cv_override():
    """Stat=3PM (family 'threes'), HR=90, CV=0.589 (base cap 0.55).
    3PM rule raises cap to 0.60. Vision must independently pass the
    base 85 floor — the override module is strictly HR / CV only."""
    r = _eval(stat_family="threes", hit_rate=90.0, cv=0.589,
              vision_score=87.0, line=0.5, mu_l20=1.6,
              edge_pct=15.3, tp=74.7, is_alt=True, tp_source="devig")
    assert r.passed, f"Castle should pass, failed={r.failed_gates}"
    assert _override_name(r) == "stat_structure:threes"


# ─── Spec proof 4: Embiid AST 2.5 — AST CV override ──────────────────
def test_4_embiid_ast_2_5_passes_via_ast_cv_override():
    """Stat=AST, HR=90, CV=0.474 (base cap 0.45).
    AST rule raises cap to 0.50. Vision passes base 85 floor."""
    r = _eval(stat_family="ast", hit_rate=90.0, cv=0.474,
              vision_score=86.0, line=2.5, mu_l20=5.0,
              edge_pct=13.9, tp=76.1, is_alt=True, tp_source="devig")
    assert r.passed, f"Embiid should pass, failed={r.failed_gates}"
    assert _override_name(r) == "stat_structure:ast"


# ─── Spec proof 5: George PTS 9.5 — PTS dominance CV bypass ──────────
def test_5_george_pts_9_5_passes_via_pts_dominance_bypass():
    """Real reject:
        Stat=PTS, HR=90, CV=0.419, line=9.5, L20_avg=17.61 (≈1.85x line).
        Bypass: HR>=90 AND L20_avg/line >= 1.75 → CV failure ignored.
    Vision still must pass base 85 floor — the override module is
    strictly CV-bypass only."""
    r = _eval(stat_family="pts", hit_rate=90.0, cv=0.419,
              vision_score=86.0, line=9.5, mu_l20=17.61,
              edge_pct=12.7, tp=79.2, is_alt=True, tp_source="devig")
    assert r.passed, f"George should pass, failed={r.failed_gates}"
    assert _override_name(r) == "pts_dominance"


def test_5b_george_pts_with_below_vision_floor_still_fails():
    """Confirms PTS-dominance rule does NOT silently waive
    vision_score_gate (per spec — only CV is bypassed)."""
    r = _eval(stat_family="pts", hit_rate=90.0, cv=0.419,
              vision_score=80.0, line=9.5, mu_l20=17.61,
              edge_pct=12.7, tp=79.2, is_alt=True, tp_source="devig")
    assert not r.passed
    assert "vision_score_gate" in r.failed_gates
    assert _override_name(r) is None


# ─── Spec proof 6: random low-HR / low-vision prop still fails ───────
def test_6_low_hr_low_vision_still_fails():
    """HR=60, VS=50, CV=0.6, PTS line=10.5, l20_avg=12. None of the
    override rules apply; the pick must remain a hard reject."""
    r = _eval(stat_family="pts", hit_rate=60.0, cv=0.60,
              vision_score=50.0, line=10.5, mu_l20=12.0,
              edge_pct=2.0, tp=55.0)
    assert not r.passed
    # Override shouldn't have fired
    assert _override_name(r) is None
    # All three relevant gates should be in failed list
    assert "hit_rate_gate" in r.failed_gates
    assert "vision_score_gate" in r.failed_gates


# ─── Hard fails (market_structure / TP / edge) are NEVER overridden ──
def test_market_structure_fail_is_never_overridden():
    """is_alt=True + tp_source=one_sided → market_structure_gate fails.
    Even though all override conditions could match, the pick MUST
    remain rejected."""
    r = _eval(stat_family="reb", hit_rate=100.0, cv=0.509,
              vision_score=97.7, line=3.5, mu_l20=7.5,
              edge_pct=22.2, tp=77.8, is_alt=True, tp_source="one_sided")
    assert not r.passed
    assert "market_structure_gate" in r.failed_gates
    assert _override_name(r) is None


def test_vision_score_fail_alone_is_not_an_overridable_gate():
    """If only vision_score_gate fails (HR pass, CV pass), no rule
    applies — the override module touches HR / CV ONLY."""
    r = _eval(stat_family="pts", hit_rate=90.0, cv=0.30,
              vision_score=70.0, line=10.5, mu_l20=20.0,
              edge_pct=5.0, tp=70.0)
    assert not r.passed
    assert r.failed_gates == ["vision_score_gate"]
    assert _override_name(r) is None


# ─── Single-rule rule: no stacking ───────────────────────────────────
def test_only_one_override_fires_per_pick():
    """Pick that could match BOTH elite_vision AND a stat-family CV
    rule. Spec says only ONE path applies. With CV already passing
    (0.30 ≤ 0.45 PTS cap), the vision rule fires ONLY if HR was the
    single failure — meaning CV did NOT fail → no stat-family rule
    needed. Verify: only one __override_applied__ entry."""
    r = _eval(stat_family="reb", hit_rate=78.0, cv=0.30,
              vision_score=92.0, line=4.5, mu_l20=8.0,
              edge_pct=12.0, tp=70.0)
    assert r.passed
    # Only HR was failing; CV was passing → only elite_vision should
    # fire. The stat-family rule does NOT apply (CV wasn't failing).
    assert _override_name(r) == "elite_vision"
    # No CV override note should be set.
    cv_note = r.gate_details["cv_gate"].note or ""
    assert "stat_structure" not in cv_note


# ─── Tiers without override config see zero behaviour change ─────────
def test_front_lines_no_override_config_no_behaviour_change():
    """Front Lines doesn't have `__safe_haven_overrides__`. A pick
    that would match the elite_vision rule there must still fail HR
    if HR < 70 (FL's base floor)."""
    r = _eval(tier="front_lines", stat_family="pts",
              hit_rate=65.0, cv=0.30, vision_score=92.0,
              line=10.5, mu_l20=20.0, edge_pct=8.0, tp=60.0)
    # FL HR floor is 70 → 65 fails → no override available → still fail.
    assert not r.passed
    assert "hit_rate_gate" in r.failed_gates
    # No override audit row should be present.
    assert "__override_applied__" not in r.gate_details


# ─── PTS dominance requires BOTH HR>=90 AND L20/line ratio ───────────
def test_pts_dominance_requires_both_conditions():
    """HR=88 (below 90 threshold) → does NOT trigger bypass."""
    r = _eval(stat_family="pts", hit_rate=88.0, cv=0.50,
              vision_score=87.0, line=10.5, mu_l20=20.0,
              edge_pct=10.0, tp=72.0)
    assert not r.passed
    assert _override_name(r) is None

    """l20_avg/line = 1.5 < 1.75 → does NOT trigger bypass."""
    r2 = _eval(stat_family="pts", hit_rate=92.0, cv=0.50,
               vision_score=87.0, line=10.5, mu_l20=15.75,
               edge_pct=10.0, tp=72.0)
    assert not r2.passed
    assert _override_name(r2) is None


def test_pts_dominance_missing_l20_avg_fails_closed():
    """No mu_recency_blend_l20 in extras → bypass cannot evaluate
    its primary condition → fails closed."""
    r = _eval(stat_family="pts", hit_rate=92.0, cv=0.50,
              vision_score=87.0, line=10.5, mu_l20=None,
              edge_pct=10.0, tp=72.0)
    assert not r.passed
    assert _override_name(r) is None


# ─── Audit row stamped on every override ─────────────────────────────
def test_override_stamps_audit_row():
    r = _eval(stat_family="reb", hit_rate=90.0, cv=0.55,
              vision_score=86.0, line=4.5, mu_l20=8.0,
              edge_pct=12.0, tp=70.0)
    assert r.passed
    audit = r.gate_details.get("__override_applied__")
    assert audit is not None
    assert audit.threshold == {"name": "stat_structure:reb"}


# ─── Universal (cross-sport) safety ──────────────────────────────────
def test_mlb_safe_haven_unaffected_by_override_layer():
    """MLB SH config doesn't include `__safe_haven_overrides__`. A
    failing MLB SH pick MUST stay failing."""
    from services.scoring.gates.engine import get_engine
    m = NormalizedMetrics(
        sport="mlb", tier="safe_haven", stat_family="hits",
        side="OVER", reference_book="dk", reference_odds=-260,
        book_count=2, tp=68.0, hit_rate=75.0, hit_rate_l20=75.0,
        cv=0.55, edge_pct=2.0, line=0.5, vision_score=92.0,
        tp_source="devig", is_alt=False,
    )
    r = get_engine().evaluate(m)
    assert "__override_applied__" not in r.gate_details
