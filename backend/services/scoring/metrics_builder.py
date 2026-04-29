"""Single source of truth for building NormalizedMetrics records.

Both the first-pass tiering (`scoring_stack.compute_tier`) and the
post-vision re-evaluation (`recompute._reevaluate_tiers_post_vision`)
must produce IDENTICAL NormalizedMetrics for the same prop. Today
those two paths each hand-roll their own builder and drift over time
(see /app/memory/PRD.md "Architectural Audit — compute_tier vs
post-vision re-eval", 2026-04-25). This module owns both builders so
adding a new gate input is a one-place change.

Sport-agnostic. No threshold logic. No gate logic. No engine calls.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.scoring.gates import NormalizedMetrics
from services.scoring.gates.thresholds import resolve_stat_family


def _resolve_cv_cap_override(sport: str, target_tier: str,
                             stat_raw: Optional[str]) -> Optional[float]:
    """Replicates `compute_tier`'s NBA-SH cv-cap override resolution."""
    if sport == "nba" and target_tier == "safe_haven":
        try:
            from services.scoring.cv_caps import resolve_cv_cap
            return resolve_cv_cap(stat_raw)
        except Exception:
            return None
    return None


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
    avg_hit_margin: Optional[float] = None,
    avg_miss_margin: Optional[float] = None,
) -> NormalizedMetrics:
    """First-pass builder. Field-for-field equivalent to the inline
    block previously in `compute_tier` (lines 362-385 pre-PR-1).
    Strictly behaviour-preserving — every field uses the same
    expression the inline builder used.
    """
    stat_family = resolve_stat_family(sport, stat_raw)
    extras: Dict[str, Any] = {"cv_cap_override": cv_cap_override}
    # Pipe the L20 mean through `extras` for the Safe-Haven override
    # layer (Rule 4 — PTS dominance CV bypass requires `L20_avg`).
    # Source priority mirrors the production scoring stack: prefer the
    # blended L20 mean when available, fall back to the smoothed
    # variants. Read-only — no scoring formula touched.
    for key in ("mu_recency_blend_l20", "l20_avg",
                "mu_recency_blend_l5", "mu_recency_E"):
        if key in prop and isinstance(prop[key], (int, float)):
            extras["mu_recency_blend_l20"] = float(prop[key])
            break

    # Pipe `projection` (preferring VK2, falling back to legacy model
    # projection or the availability-guarded mu) for FL-OVER override
    # rules + the direction_gate (NBA Front Lines OVER only).
    # Read-only — no scoring formula touched.
    for key in ("vk2_projection", "model_projection",
                "mu_after_availability_guard", "mu_recency_blend_l20"):
        v = prop.get(key) if isinstance(prop, dict) else None
        if isinstance(v, (int, float)):
            extras["projection"] = float(v)
            break

    # Pipe `vision_score_v2` so the gate engine's vision_score_gate
    # may be configured with `use_v2: True` (NBA War Zone). v1 is
    # left untouched on the metrics record. Read-only.
    v2 = prop.get("vision_score_v2") if isinstance(prop, dict) else None
    if isinstance(v2, (int, float)):
        extras["vision_score_v2"] = float(v2)

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
        # Sample size (2026-04-25, HR v3) — read from prop/doc; gate
        # engine consumes for small-sample / insufficient-sample
        # behaviour. NBA leaves this None (always 20 by construction).
        hit_rate_sample_size=prop.get("hit_rate_sample_size"),
        ceiling_rate=ceiling_rate,
        cv=cv,
        edge_pct=edge_pct,
        p_model_pct=p_model_pct,
        extras=extras,
        line=prop.get("line"),
        avg_hit_margin=avg_hit_margin,
        avg_miss_margin=avg_miss_margin,
    )


def build_metrics_from_score_doc(
    doc: Dict[str, Any],
    *,
    override_tier: Optional[str] = None,
) -> NormalizedMetrics:
    """Re-eval builder (PR-2). Reconstructs `NormalizedMetrics` from a
    persisted score doc with FULL field parity to the first-pass
    builder. Sport-aware HR resolution: NBA persists `hit_rate_over`
    and `hit_rate_under` per side; MLB persists only the side-aware
    `p_true_hit_rate`. We use whichever exists and fall back to
    `p_true_hit_rate × 100` so MLB's re-eval sees the same value the
    first pass did (replaces the inline Fix A fallback in
    `_reevaluate_tiers_post_vision`).

    The `cv_cap_override` is derived here from the same predicate the
    first pass uses (replaces the inline Fix B re-iteration logic).
    Any future override added to `compute_tier` need only be mirrored
    once, here.

    Delegates to `build_metrics_from_context` so the two paths share
    every field-derivation expression except the side-aware HR
    resolution (which is intrinsically doc-specific).
    """
    sport = (doc.get("sport") or "").lower()
    target_tier = override_tier or doc.get("tier")
    stat_raw = doc.get("stat_type")
    side = (doc.get("recommendation") or "OVER").upper()

    # Side-aware hit rate with sport-agnostic fallback for adapters
    # that don't persist hit_rate_over / hit_rate_under (currently MLB).
    hr = doc.get("hit_rate_over") if side == "OVER" else doc.get("hit_rate_under")
    if hr is None:
        p_true_hr = doc.get("p_true_hit_rate")
        hr = (p_true_hr * 100.0) if p_true_hr is not None else None

    # `p_model_pct` derivation — first pass uses
    # round(p_model * 100, 1); the persisted equivalent is
    # `p_true_active` (= p_model resolved by the ladder).
    p_true_active = doc.get("p_true_active")
    p_model_pct = round(p_true_active * 100.0, 1) if p_true_active is not None else None

    return build_metrics_from_context(
        prop=doc,
        sport=sport,
        target_tier=target_tier,
        stat_raw=stat_raw,
        side=side,
        ref_book=doc.get("tier_reference_book"),
        ref_odds=doc.get("tier_reference_odds"),
        book_count=doc.get("book_count"),
        cv=doc.get("cv"),
        hit_rate=hr,
        edge_pct=doc.get("edge_pct"),
        tp=doc.get("tp"),
        ceiling_rate=doc.get("ceiling_rate"),
        p_model_pct=p_model_pct,
        cv_cap_override=_resolve_cv_cap_override(sport, target_tier, stat_raw),
        avg_hit_margin=doc.get("avg_hit_margin"),
        avg_miss_margin=doc.get("avg_miss_margin"),
    )


__all__ = ["build_metrics_from_context", "build_metrics_from_score_doc"]
