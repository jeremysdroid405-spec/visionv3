"""Field Ownership Registry — PropVision

Single source of truth for which collection + field owns every
user-visible piece of data in the product. All reads of owned fields
must go through `accessors.get_owned_field()`.

Contract:
- `owner_collection.owner_field` is the ONLY authoritative value
- `writers` lists the ONLY functions allowed to write this field
- `readers_allowed` is the enforcement surface (future: AST-scan for illegal reads)
- `null_policy`:
    * "return_null": missing value → `None` returned to caller (display-only fields)
    * "fail_loud":   missing value → raise FieldOwnershipError (calculation-critical)
- `status`:
    * "enforced":   accessor in use, writers deleted, contract test passing
    * "locked":     accessor ready, migration in progress
    * "documented": spec frozen, migration not started

Adding a new field:
1. Add entry here with status="documented"
2. Migrate writers per the plan
3. Switch status to "locked"
4. Verify contract test passes
5. Switch status to "enforced"

This file is the governance layer. It does NOT execute policy — see
`accessors.py` for runtime enforcement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Literal, Optional


NullPolicy = Literal["return_null", "fail_loud"]
Status = Literal["enforced", "locked", "documented"]


@dataclass(frozen=True)
class FieldSpec:
    """Declarative ownership spec for one user-visible field."""
    name: str
    owner_collection: str
    owner_field: str
    writers: List[str]          # file.py:function form; allow-list
    readers_allowed: List[str]  # file.py:function form; informational
    fallback_policy: str        # always "NONE" under SSOT rules
    null_policy: NullPolicy
    frontend_display: str       # what the UI shows when value is None
    status: Status
    notes: str = ""


# Canonical registry. Alphabetized by field name for stable diffs.
FIELD_REGISTRY: Dict[str, FieldSpec] = {
    "active": FieldSpec(
        name="active",
        owner_collection="prop_scores",
        owner_field="active",
        writers=[
            "services/board/set_active.py:set_active",  # PLANNED helper; 8 writers route through it
        ],
        readers_allowed=[
            "services/board/reader.py:*",
            "services/delta/detector.py:detect_deltas",
            "routes/ferrari_tiers.py:*",
        ],
        fallback_policy="NONE",
        null_policy="fail_loud",
        frontend_display="n/a — filter field, not user-visible",
        status="documented",
        notes="8 writers today with no contract. Today's watcher bug lives here. Migration = single set_active() helper + audit collection + strict {active: True} queries only.",
    ),
    "computed_at": FieldSpec(
        name="computed_at",
        owner_collection="prop_scores",
        owner_field="computed_at",
        writers=["services/scoring/prop_scores_store.py:write_versioned_scores"],
        readers_allowed=[
            "services/board/drift_audit.py:*",
            "services/shadow/shadow_capture_service.py:*",
            "routes/health_sync.py:_probe_prop_scores",
        ],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display="— (en dash) when missing",
        status="enforced",
        notes="Clean. Only written in one place.",
    ),
    "cv": FieldSpec(
        name="cv",
        owner_collection="prop_scores",
        owner_field="cv",
        writers=[
            "services/scoring/adapters/nba_scoring.py:score",
            "services/scoring/adapters/mlb_scoring.py:score",
        ],
        readers_allowed=[
            "services/scoring/gates/engine.py:*",
            "frontend/src/components/dashboard/PlayerDetailPage.jsx:*",
        ],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display="— when missing",
        status="documented",
        notes="Parallel computation in intel_suite_calculator._calculate_stability_index disagrees with cv on composite MLB stats. Delete stability_index; bind UI to cv only.",
    ),
    "edge": FieldSpec(
        name="edge",
        owner_collection="prop_scores",
        owner_field="edge_vs_fair",
        writers=["services/scoring/scoring_stack.py:compute_vision_score"],
        readers_allowed=[
            "services/scoring/gates/engine.py:*",
            "routes/ferrari_tiers.py:*",
        ],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display="— when missing; never show 0.0%",
        status="documented",
        notes="Aliases vk_edge, true_edge, edge_pct must be deleted from score doc.",
    ),
    "event_id": FieldSpec(
        name="event_id",
        owner_collection="live_props",
        owner_field="event_id",
        writers=["services/universal_odds_sync.py:_persist_prop"],
        readers_allowed=["services/scoring/*", "routes/*"],
        fallback_policy="NONE",
        null_policy="fail_loud",
        frontend_display="n/a — identity field",
        status="enforced",
        notes="Clean. Single writer, propagated through pipeline.",
    ),
    "game_start_utc": FieldSpec(
        name="game_start_utc",
        owner_collection="live_props",
        owner_field="game_start_utc",
        writers=["services/universal_odds_sync.py:_persist_prop"],
        readers_allowed=[
            "services/scoring/recompute.py:_coerce_score_ctx_from_live",
            "services/board/scanner.py:*",
            "routes/ferrari_tiers.py:_merge_score_with_board",
        ],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display="hidden when missing",
        status="documented",
        notes="Aliases commence_time + start_time should be removed from score docs.",
    ),
    "hit_rate_l5": FieldSpec(
        name="hit_rate_l5",
        owner_collection="prop_scores",
        owner_field="l5_rate",
        writers=[
            "services/scoring/adapters/nba_scoring.py:score",
            "services/scoring/adapters/mlb_scoring.py:score",
        ],
        readers_allowed=[
            "services/scoring/gates/engine.py:*",
            "routes/ferrari_tiers.py:_generate_vision_fallback",
        ],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display="— when missing",
        status="documented",
        notes="Use window-explicit names: hit_rate_l5 / l10 / l20. Drop generic hit_rate_over.",
    ),
    "hit_rate_l10": FieldSpec(
        name="hit_rate_l10",
        owner_collection="prop_scores",
        owner_field="l10_rate",
        writers=[
            "services/scoring/adapters/nba_scoring.py:score",
            "services/scoring/adapters/mlb_scoring.py:score",
        ],
        readers_allowed=[
            "services/scoring/gates/engine.py:*",
            "routes/ferrari_tiers.py:_generate_vision_fallback",
        ],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display="— when missing",
        status="documented",
    ),
    "hit_rate_l20": FieldSpec(
        name="hit_rate_l20",
        owner_collection="prop_scores",
        owner_field="hit_rate_over_l20",  # planned — currently stored as hit_rate_over
        writers=[
            "services/scoring/adapters/nba_scoring.py:score",
            "services/scoring/adapters/mlb_scoring.py:score",
        ],
        readers_allowed=["services/scoring/gates/engine.py:*"],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display="— when missing",
        status="documented",
        notes="Current field name `hit_rate_over` is ambiguous; migration renames to hit_rate_over_l20.",
    ),
    "line": FieldSpec(
        name="line",
        owner_collection="live_props",
        owner_field="line",
        writers=["services/universal_odds_sync.py:_persist_prop"],
        readers_allowed=["*"],
        fallback_policy="NONE",
        null_policy="fail_loud",
        frontend_display="required field; fail_loud if missing",
        status="enforced",
    ),
    "odds_type": FieldSpec(
        name="odds_type",
        owner_collection="pp_projection_id_cache",
        owner_field="odds_type",
        writers=["services/pp_multiplier_lab.py:build_projection"],
        readers_allowed=["services/pp_multiplier_lab.py:*"],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display="— when missing (do NOT default to 'standard')",
        status="documented",
        notes="Current fallback to 'standard' hides scraper failures. Must be null.",
    ),
    "opponent": FieldSpec(
        name="opponent",
        owner_collection="live_props",
        owner_field="opponent_team",
        writers=["services/universal_odds_sync.py:_build_prop_record"],
        readers_allowed=[
            "routes/ferrari_tiers.py:_get_nba_tier_picks_from_scores",
            "routes/ferrari_tiers.py:_get_mlb_tier_picks_from_scores",
            "routes/player.py:get_player_with_badges",
            "services/dvp_service.py:apply_dvp_to_prop",
            "services/vegas_regression_model.py:predict_batch",
            "services/simulation_service.py:_process_leg",
            "services/mlb_vision_intel.py:_build_batch_prompt",
        ],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display="— when missing",
        status="locked",
        notes=(
            "Migration in progress (2026-05-03). Writer in "
            "mlb_cached_board_builder.py:470 + context_badge_service.py:159 "
            "must be deleted. Readers switch to get_owned_field()."
        ),
    ),
    "p_true": FieldSpec(
        name="p_true",
        owner_collection="prop_scores",
        owner_field="p_true_active",
        writers=["services/scoring/recompute.py:recompute_sport"],
        readers_allowed=["*"],
        fallback_policy="NONE",
        null_policy="fail_loud",
        frontend_display="required for display; fail_loud if missing",
        status="enforced",
        notes="Clean. Single writer, null handled explicitly.",
    ),
    "photo_url": FieldSpec(
        name="photo_url",
        owner_collection="master_hub",
        owner_field="photo_url",
        writers=["services/bdl_universal_sync.py:sync_players"],
        readers_allowed=[
            "services/picks_getter_service.py:_resolve_photo",  # PLANNED
            "routes/player.py:*",
        ],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display="generic silhouette avatar when missing",
        status="documented",
        notes="Must delete module-global _photo_cache in picks_getter_service.py:237.",
    ),
    "player_name": FieldSpec(
        name="player_name",
        owner_collection="master_hub",
        owner_field="display_name",
        writers=["services/bdl_universal_sync.py:sync_players"],
        readers_allowed=["*"],
        fallback_policy="NONE",
        null_policy="fail_loud",
        frontend_display="required field",
        status="documented",
        notes="Canonical by bdl_player_id. 9 write sites in picks_getter_service.py must be deleted.",
    ),
    "pp_projection_id": FieldSpec(
        name="pp_projection_id",
        owner_collection="pp_projection_id_cache",
        owner_field="projection_id",
        writers=["services/pp_multiplier_lab.py:seed_projection_ids_from_scraper"],
        readers_allowed=["services/pp_multiplier_lab.py:*"],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display="n/a — internal",
        status="documented",
    ),
    "ranking_score_v2": FieldSpec(
        name="ranking_score_v2",
        owner_collection="prop_scores",
        owner_field="ranking_score_v2",
        writers=["services/scoring/recompute.py:recompute_sport"],
        readers_allowed=[
            "routes/player.py:*",
            "routes/ferrari_tiers.py:*",
            "routes/vacuum.py:*",
            "services/market_moves_engine.py:get_market_moves",
        ],
        fallback_policy="NONE",
        null_policy="fail_loud",
        frontend_display="required for tier sort",
        status="documented",
        notes="Current fallback to vision_score produces inconsistent sort. Drop fallback.",
    ),
    "scored_at": FieldSpec(
        name="scored_at",
        owner_collection="prop_scores",
        owner_field="scored_at",
        writers=["services/scoring/prop_scores_store.py:write_versioned_scores"],
        readers_allowed=["routes/health_sync.py:_probe_prop_scores"],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display="— when missing (indicates never-scored)",
        status="locked",
        notes=(
            "Migration 2026-05-03: writing scored_at = computed_at at the "
            "same call site. Unblocks /api/health/sync freshness probe "
            "which has been silently dead."
        ),
    ),
    "side": FieldSpec(
        name="side",
        owner_collection="live_props",
        owner_field="recommendation",
        writers=["services/universal_odds_sync.py:_build_prop_record"],
        readers_allowed=["*"],
        fallback_policy="NONE",
        null_policy="fail_loud",
        frontend_display="required field (OVER or UNDER enum)",
        status="documented",
        notes="Aliases direction + side coexist. Must canonicalize.",
    ),
    "stat_type": FieldSpec(
        name="stat_type",
        owner_collection="live_props",
        owner_field="stat_type",
        writers=["services/universal_odds_sync.py:_build_prop_record"],
        readers_allowed=["*"],
        fallback_policy="NONE",
        null_policy="fail_loud",
        frontend_display="required",
        status="documented",
        notes="Composite stat types (H+R+RBI) need explicit splitter in intel_suite_calculator.",
    ),
    "team": FieldSpec(
        name="team",
        owner_collection="live_props",
        owner_field="team",
        writers=["services/universal_odds_sync.py:_build_prop_record"],
        readers_allowed=["*"],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display="— when missing",
        status="documented",
        notes="Aliases team / team_abbr / team_name coexist. Canonicalize to team (3-letter abbr).",
    ),
    "tier": FieldSpec(
        name="tier",
        owner_collection="prop_scores",
        owner_field="tier",
        writers=[
            "services/scoring/tiering.py:*",
            "services/scoring/gates/engine.py:*",
            "services/scoring/recompute.py:recompute_sport",
        ],
        readers_allowed=["*"],
        fallback_policy="NONE",
        null_policy="fail_loud",
        frontend_display="required for tier endpoints",
        status="enforced",
    ),
    "vision_intel": FieldSpec(
        name="vision_intel",
        owner_collection="prop_scores",
        owner_field="vision_intel",
        writers=["services/vision_intel/engine.py:enrich"],  # PLANNED — doesn't exist yet
        readers_allowed=["routes/ferrari_tiers.py:*", "frontend/*"],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display='"Vision unavailable" when null; NEVER show template text',
        status="documented",
        notes=(
            "4 parallel writers + 3-level fallback cascade. Full refactor "
            "scoped in /app/memory/VISION_INTEL_REFACTOR_SCOPE.md. "
            "Until shipped: MLB must return null, not template."
        ),
    ),
}


# Class of errors raised by field_ownership enforcement
class FieldOwnershipError(RuntimeError):
    """Raised when a fail_loud field is missing from a source document.

    Do NOT catch this to substitute a fallback value — that defeats the
    entire enforcement contract. Either fix the source or change the
    field's null_policy to `return_null`."""


def get_spec(field_name: str) -> FieldSpec:
    """Lookup helper. Raises KeyError if field is not registered —
    this is intentional: an unregistered field has no ownership."""
    return FIELD_REGISTRY[field_name]


def list_fields_by_status(status: Status) -> List[str]:
    return sorted(f for f, s in FIELD_REGISTRY.items() if s.status == status)


__all__ = ["FIELD_REGISTRY", "FieldSpec", "FieldOwnershipError", "get_spec", "list_fields_by_status"]
