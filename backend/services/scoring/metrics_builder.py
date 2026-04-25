"""Single source of truth for building NormalizedMetrics records.

Both the first-pass tiering (`scoring_stack.compute_tier`) and the
post-vision re-evaluation (`recompute._reevaluate_tiers_post_vision`)
must produce IDENTICAL NormalizedMetrics for the same prop. Today
those two paths each hand-roll their own builder and drift over time
(see /app/memory/PRD.md "Architectural Audit — compute_tier vs
post-vision re-eval", 2026-04-25). This module owns the first-pass
builder so adding a new gate input is a one-place change. The
re-eval builder lands here in PR-2.

Sport-agnostic. No threshold logic. No gate logic. No engine calls.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.scoring.gates import NormalizedMetrics
from services.scoring.gates.thresholds import resolve_stat_family


def build_metrics_from_context(
    *,
    prop: Dict[str, Any],
    sport: str,
    target_tier: str,
    stat_raw: Optional[str],
    side: str,
    ref_book: Optional[str],
    ref_odds: Optional[int],
    book_count: Optional[int],
    cv: Optional[float],
    hit_rate: Optional[float],
    edge_pct: Optional[float],
    tp: Optional[float],
    ceiling_rate: Optional[float],
    p_model_pct: Optional[float],
    cv_cap_override: Optional[float],
    mlb_goblin_override: Optional[Dict[str, Any]],
) -> NormalizedMetrics:
    """First-pass builder. Field-for-field equivalent to the inline
    block previously in `compute_tier` (lines 362-385 pre-PR-1).
    Strictly behaviour-preserving — every field uses the same
    expression the inline builder used.
    """
    stat_family = resolve_stat_family(sport, stat_raw)
    extras: Dict[str, Any] = {"cv_cap_override": cv_cap_override}
    if mlb_goblin_override:
        extras["mlb_goblin_override"] = mlb_goblin_override

    return NormalizedMetrics(
        sport=sport,
        tier=target_tier,
        stat_family=stat_family,
        side=side,
        reference_book=ref_book,
        reference_odds=ref_odds,
        book_count=book_count,
        tp=tp,
        tp_source=prop.get("tp_source"),
        is_alt="alternate" in (stat_raw or "").lower(),
        vision_score=prop.get("vision_score"),
        hit_rate=hit_rate,
        hit_rate_l20=prop.get("hit_rate_over") or prop.get("hit_rate_l20"),
        hit_rate_l10=prop.get("hit_rate_l10"),
        hit_rate_l5=prop.get("hit_rate_l5"),
        ceiling_rate=ceiling_rate,
        cv=cv,
        edge_pct=edge_pct,
        p_model_pct=p_model_pct,
        extras=extras,
    )


__all__ = ["build_metrics_from_context"]
