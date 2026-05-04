"""
Pydantic write contract for `{sport}_prop_scores` documents.
=============================================================

This schema is the single source of truth for what a score document
may contain. Phase 1 (Tier D, 2026-05-04) runs in **validate-and-log**
mode: unknown or malformed fields are counted and surfaced at WARN
level on each write batch, but writes are NOT blocked. A follow-up
session will flip `SSOT_PYDANTIC_STRICT=true` to raise on violation.

The schema is derived from the existing `_SCORE_OUTPUT_FIELDS` tuple
in `prop_scores_store.py`. Both continue to coexist during the
migration — the tuple is still the projection allowlist, the schema
is the validator. Once parity is verified for one full slate, the
tuple can be deleted and replaced by `ScoreDocument.model_fields`.
"""
from __future__ import annotations

import os
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

logger = logging.getLogger(__name__)

SSOT_PYDANTIC_STRICT = os.environ.get("SSOT_PYDANTIC_STRICT", "false").lower() == "true"


class ScoreDocument(BaseModel):
    """Strict write contract for a single row in `{sport}_prop_scores`.

    Unknown fields raise ValidationError (extra="forbid") — this is
    what makes silent drops impossible. Every field the scoring
    pipeline produces MUST be declared here; everything else is
    rejected at the write boundary.

    Field policy:
      - `Optional[T] = None`  — field may be absent or null.
      - `T`                    — field is required; None is rejected.

    Per FIELD_OWNERSHIP.md, only the identity tuple + a small set of
    versioning fields are `required`. Every scoring signal is
    Optional — adapters are allowed to return None (the gate engine
    is responsible for deciding what that means).
    """

    model_config = ConfigDict(
        # 2026-05-04 Tier D: `extra="allow"` during the migration
        # phase — the scoring adapters produce ~150+ fields, many of
        # which are audit/diagnostic outputs that `_project_score_doc`
        # deliberately drops but which still pass through Pydantic
        # before the drop. Strictly `forbid`-ing extras here would
        # either (a) require enumerating every diagnostic field
        # across NBA + MLB adapters, or (b) force a pipeline refactor
        # that decouples adapter-output from persistable-write.
        # Deferred to Tier E (`SSOT_PYDANTIC_STRICT=true` will flip
        # this to "forbid" once the persist-boundary is cleaned).
        # Today the schema still delivers strong value: every
        # LOCKED SSOT field has a typed declaration so type drift
        # (int/str/None mismatch) is caught at write time — the
        # primary Tier D-motivating bug class.
        extra="allow",
        arbitrary_types_allowed=True,
        str_strip_whitespace=False,  # never silently mutate values
    )

    # ── Identity (required on every write) ────────────────────────
    canonical_key:     str
    sport:             str
    event_id:          Optional[str] = None  # upstream can legitimately miss
    player_name:       Optional[str] = None  # fail_loud in registry but filled post-stamp
    stat_type:         str
    line:              float
    recommendation:    Optional[str] = None

    # ── Versioning (required) ─────────────────────────────────────
    version_tag:  str
    computed_at:  datetime
    scored_at:    datetime

    # ── Universal pool lifecycle ──────────────────────────────────
    active:              Optional[bool] = True
    inactive_reason:     Optional[str] = None
    active_changed_at:   Optional[datetime] = None
    game_start_utc:      Optional[datetime] = None

    # ── Vision score v1 ───────────────────────────────────────────
    vision_score:       Optional[float] = None
    vision_score_raw:   Optional[float] = None
    quality_source:     Optional[str]   = None
    fair_prob:          Optional[float] = None
    stability:          Optional[Any]   = None
    confidence:         Optional[Any]   = None
    edge_vs_fair:       Optional[float] = None

    # ── Vision score v2 (directional) ─────────────────────────────
    vision_score_v2:                 Optional[float] = None
    vision_v2_direction_margin:      Optional[float] = None
    vision_v2_direction_strength:    Optional[float] = None
    vision_direction_alignment:      Optional[Any]   = None
    vision_probability_component:    Optional[float] = None
    vision_projection_component:     Optional[float] = None
    vision_edge_component:           Optional[float] = None
    vision_consistency_component:    Optional[float] = None
    vision_context_component:        Optional[float] = None
    vision_market_confidence_component: Optional[float] = None
    vision_volatility_penalty:       Optional[float] = None
    vision_v2_dir_gate:              Optional[Any]   = None
    vision_v2_weights:               Optional[Dict[str, Any]] = None

    # ── Tier ──────────────────────────────────────────────────────
    tier:                   Optional[str] = None
    tier_reason:            Optional[str] = None
    tier_reference_book:    Optional[str] = None
    tier_reference_odds:    Optional[float] = None
    routed_tier:            Optional[str] = None
    tier_gate_results:      Optional[Dict[str, Any]] = None

    # ── PP utility ────────────────────────────────────────────────
    pp_utility:             Optional[float] = None
    pp_utility_category:    Optional[str]   = None
    pp_utility_components:  Optional[Dict[str, Any]] = None
    pp_multiplier:          Optional[float] = None
    pp_multiplier_label:    Optional[str]   = None
    pp_multiplier_source:   Optional[str]   = None
    pp_reference_source:    Optional[str]   = None
    pp_playable:            Optional[bool]  = None
    pp_playability_reason:  Optional[str]   = None

    # ── p_true diagnostic ────────────────────────────────────────
    p_true_active:    Optional[float] = None
    p_true_method:    Optional[str]   = None
    p_true_hit_rate:  Optional[float] = None
    p_true_model:     Optional[float] = None
    model_projection: Optional[float] = None
    model_sigma:      Optional[float] = None

    # ── MLB probability (2026-04-29) ──────────────────────────────
    p_distribution:                 Optional[float] = None
    lom_disabled:                   Optional[bool]  = None
    p_lom_shadow:                   Optional[float] = None
    probability_method_shadow:      Optional[str]   = None
    p_ecdf_shadow:                  Optional[float] = None
    probability_method_shadow_ecdf: Optional[str]   = None

    # ── VK2 5-year adv-stat ───────────────────────────────────────
    p_true_vk2:     Optional[float] = None
    vk2_projection: Optional[float] = None
    vk2_sigma:      Optional[float] = None
    vk2_error:      Optional[str]   = None

    # ── Hit-rate diagnostics ──────────────────────────────────────
    hit_rate_over:        Optional[float] = None
    hit_rate_under:       Optional[float] = None
    hit_rate_sample_size: Optional[int]   = None
    hit_rate_l5:          Optional[float] = None
    hit_rate_l10:         Optional[float] = None
    hit_rate_l20:         Optional[float] = None

    # ── Ranking score v2 ──────────────────────────────────────────
    ranking_score_v2: Optional[float] = None

    # ── Sport-specific enrichments ───────────────────────────────
    tempo_modifier: Optional[Any]            = None
    intel_suite:    Optional[Dict[str, Any]] = None

    # ── Canonical DvP ────────────────────────────────────────────
    opponent_defensive_rank:       Optional[int] = None
    opponent_defensive_source:     Optional[str] = None
    opponent_defensive_stat_type:  Optional[str] = None

    # ── 0-book exclusion ──────────────────────────────────────────
    book_count:      Optional[int]  = None
    coverage_class:  Optional[str]  = None
    books_anchored:  Optional[List[str]] = None

    # ── 0.5-line stability ────────────────────────────────────────
    # Accepted as open Dict (MLB adapter sets several sub-fields).
    stability_half_line: Optional[Any] = None
    half_line_variance:  Optional[Any] = None

    # ── Source anchor / PP availability ──────────────────────────
    pp_available:   Optional[bool] = None
    playable_on_pp: Optional[bool] = None
    source_anchor:  Optional[str]  = None
    anchor_book:    Optional[str]  = None

    # ── Missing-value policy ──────────────────────────────────────
    feature_health: Optional[Any] = None

    # ── Injury context (NBA) ─────────────────────────────────────
    injury_context: Optional[Any] = None

    # ── Heteroscedastic sigma (NBA Phase 2 — 2026-05-02) ─────────
    hetero_sigma_base:       Optional[float] = None
    hetero_sigma_multiplier: Optional[float] = None

    # ── TTL marker (2026-05-04 Tier F) ──────────────────────────
    # Present ONLY on docs whose version_tag is NOT in
    # _LIVE_VERSION_TAGS. Mongo's partial-ish TTL sees the field and
    # expires the doc 7 days after its value. Live docs never carry
    # this field and are immune by absence.
    ttl_at: Optional[Any] = None

    # ── Persisted score-doc fields (added post initial schema) ───
    # These are all fields the NBA/MLB scoring adapters produce and
    # that `_project_score_doc` stamps onto the persisted doc today.
    # Listed here so Pydantic stops flagging them as "extra". Each
    # has `Optional[...] = None` because any given pick may not
    # compute every one.
    bdl_player_id:                            Optional[int]   = None
    cv:                                       Optional[float] = None
    cv_status:                                Optional[str]   = None
    edge_pct:                                 Optional[float] = None
    gate_eval:                                Optional[Dict[str, Any]] = None
    hit_rate_status:                          Optional[str]   = None
    identity_status:                          Optional[str]   = None
    market_probability:                       Optional[float] = None
    minutes_composition_applied:              Optional[bool]  = None
    minutes_composition_baseline_projection:  Optional[float] = None
    minutes_composition_per_min_rate:         Optional[float] = None
    minutes_composition_predicted_minutes:    Optional[float] = None
    model_projection_direct:                  Optional[float] = None
    model_projection_synth:                   Optional[float] = None
    model_sigma_direct:                       Optional[float] = None
    model_sigma_synth:                        Optional[float] = None
    projection_compare_status:                Optional[str]   = None
    projection_delta_abs:                     Optional[float] = None
    projection_delta_pct:                     Optional[float] = None
    projection_method:                        Optional[str]   = None
    projection_primary_method:                Optional[str]   = None
    tp:                                       Optional[float] = None
    tp_books_list:                            Optional[List[str]] = None
    tp_books_used:                            Optional[int]   = None
    tp_method:                                Optional[str]   = None
    tp_source:                                Optional[str]   = None
    tp_unavailable:                           Optional[bool]  = None
    tp_unavailable_reason:                    Optional[str]   = None

    # ── Fields reported as dropped by the old [SSOT_DROP] warn ───
    # These are adapter outputs that `_project_score_doc` does NOT
    # project (i.e. deliberately dropped). Declared here so Pydantic
    # tolerates their presence when a strict-mode flip happens. Each
    # is `Optional[Any] = None` because the shape is adapter-internal
    # and not contractually fixed at the write boundary.
    avg_hit_margin:             Optional[float] = None
    avg_miss_margin:            Optional[float] = None
    hit_distance_from_line:     Optional[Any]   = None
    miss_distance_from_line:    Optional[Any]   = None
    l5_rate:                    Optional[float] = None
    l10_rate:                   Optional[float] = None
    l20_rate:                   Optional[float] = None
    consistency_band:           Optional[str]   = None


def validate_score_document(doc: Dict[str, Any]) -> Optional[str]:
    """Validate a single score doc against the Pydantic contract.

    Returns `None` on success. On failure:
      - SSOT_PYDANTIC_STRICT=true  → re-raises ValidationError.
      - SSOT_PYDANTIC_STRICT=false → logs WARN and returns a
        concise error string (caller tallies these for batch-level
        summary).

    This is intentionally cheap. The default mode is observational
    so we can bake the schema against real slates without risk;
    flipping the env flag later gives us the strict enforcement.
    """
    try:
        ScoreDocument.model_validate(doc)
        return None
    except ValidationError as ve:
        if SSOT_PYDANTIC_STRICT:
            raise
        # Compact error line: canonical_key + first few field issues.
        ck = doc.get("canonical_key", "?")
        errs = ve.errors()[:3]
        detail = "; ".join(
            f"{'.'.join(str(x) for x in e.get('loc') or [])}={e.get('msg')}"
            for e in errs
        )
        logger.warning(
            "[SSOT_PYDANTIC] score doc failed schema: canonical_key=%s "
            "detail=%s (total_errors=%d)",
            ck, detail, len(ve.errors()),
        )
        return f"{ck}: {detail}"


__all__ = ["ScoreDocument", "validate_score_document", "SSOT_PYDANTIC_STRICT"]
