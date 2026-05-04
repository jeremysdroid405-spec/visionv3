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
            "services/board/set_active.py:set_active",
        ],
        readers_allowed=[
            "services/board/reader.py:*",
            "services/delta/detector.py:detect_deltas",
            "routes/ferrari_tiers.py:*",
        ],
        fallback_policy="NONE",
        null_policy="fail_loud",
        frontend_display="n/a — filter field, not user-visible",
        status="locked",
        notes=(
            "2026-05-04 migration: all active-flip writers route through "
            "services.board.set_active.set_active(). `active_transitions` "
            "audit collection records every transition (TTL 30d). "
            "Legacy direct-update sites removed: tiering.mark_retired_inactive "
            "and scanner.scan_sport now delegate. Initial active=True on "
            "first-time score doc persistence is an insert default (not a "
            "transition) and lives in prop_scores_store._project_score_doc."
        ),
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
            "services/intel_suite_calculator.py:_calculate_stability_index",
            "frontend/src/components/dashboard/PlayerDetailPage.jsx:*",
            "frontend/src/components/dashboard/UniversalPlayerCard.jsx:*",
        ],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display="— when missing",
        status="locked",
        notes=(
            "2026-05-04: parallel computation in "
            "intel_suite_calculator._calculate_stability_index now "
            "binds to canonical cv via σ = cv × model_projection. "
            "Prior behaviour computed std_dev from raw game logs, "
            "which returned ≈0 on composite MLB stat_types (H+R+RBI) "
            "— producing \"100% Elite\" labels that contradicted the "
            "canonical cv-derived variance shown on the Variance "
            "tile. Local game-log std_dev retained as a last-resort "
            "fallback only (identity-failed picks, legacy docs). "
            "Frontend was already migrated to read cv (2026-05-03)."
        ),
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
        status="locked",
        notes=(
            "2026-05-04: canonical owner = edge_vs_fair. "
            "Aliases `edge_pct` (percentage form) and `vk_edge` "
            "(raw unit form) remain stamped on API pick responses "
            "for backwards compat with 20+ frontend readers; the "
            "scorer stamps `edge_vs_fair` once and the alias values "
            "are derived from it. `true_edge` / `edge_percentage` "
            "legacy aliases are flagged for deletion in a follow-up "
            "cleanup pass (see FIELD_OWNERSHIP.md Tier C)."
        ),
    ),
    "hit_rate_l5": FieldSpec(
        name="hit_rate_l5",
        owner_collection="prop_scores",
        owner_field="hit_rate_l5",
        writers=[
            "services/scoring/adapters/nba_scoring.py:score",
            "services/scoring/adapters/mlb_scoring.py:score",
        ],
        readers_allowed=[
            "services/scoring/gates/engine.py:*",
            "routes/ferrari_tiers.py:*",
        ],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display="— when missing",
        status="locked",
        notes=(
            "2026-05-04: storage field already uses the canonical "
            "window-explicit name `hit_rate_l5` (written by both "
            "adapters, surfaced by gate engine and cards). No rename "
            "required."
        ),
    ),
    "hit_rate_l10": FieldSpec(
        name="hit_rate_l10",
        owner_collection="prop_scores",
        owner_field="hit_rate_l10",
        writers=[
            "services/scoring/adapters/nba_scoring.py:score",
            "services/scoring/adapters/mlb_scoring.py:score",
        ],
        readers_allowed=[
            "services/scoring/gates/engine.py:*",
            "routes/ferrari_tiers.py:*",
        ],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display="— when missing",
        status="locked",
        notes=(
            "2026-05-04: storage field already uses canonical name "
            "`hit_rate_l10`. No rename required."
        ),
    ),
    "hit_rate_l20": FieldSpec(
        name="hit_rate_l20",
        owner_collection="prop_scores",
        owner_field="hit_rate_l20",
        writers=[
            "services/scoring/recompute.py:recompute_sport",
        ],
        readers_allowed=["services/scoring/gates/engine.py:*"],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display="— when missing",
        status="locked",
        notes=(
            "2026-05-04: window-explicit canonical name now "
            "dual-written alongside legacy `hit_rate_over` in "
            "recompute_sport. `hit_rate_l20` is the forward-facing "
            "name; `hit_rate_over` will be deleted after all readers "
            "migrate (tracked in FIELD_OWNERSHIP.md Tier C). "
            "Accessor storage map updated so `get_owned_field(doc, "
            "'hit_rate_l20')` reads the canonical key directly."
        ),
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
        status="locked",
        notes=(
            "2026-05-04 Tier C: `commence_time` alias is now pinned "
            "to the canonical `game_start_utc` in "
            "routes/ferrari_tiers._merge_score_with_board so any "
            "backend reader still reaching for `commence_time` gets "
            "the canonical value. Frontend already reads "
            "`game_start_utc` exclusively. Pre-Tier-C, picks could "
            "carry a 10-day-stale `commence_time` next to a fresh "
            "`game_start_utc` (Tyrese Maxey case, 2026-05-04). "
            "Adapter-level reads of raw-prop "
            "`commence_time || event_start_utc || game_time` are "
            "ingest-boundary shims (3rd-party odds scraper compat), "
            "not score-doc fallbacks — allowed."
        ),
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
        owner_collection="pp_multiplier_lab",
        owner_field="selected_projections.odds_type",
        writers=["services/pp_multiplier_lab.py:build_projection"],
        readers_allowed=["*"],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display='"standard" default when missing',
        status="locked",
        notes=(
            "2026-05-04 Tier C: odds_type_mix surfaced on the "
            "/api/health/sync PP probe so a sudden drop of "
            "demon/goblin → only standard is visible at a glance. "
            "Normaliser `_norm_odds_type` maps any non-enum value to "
            "\"standard\" (not a fallback — normalisation at the "
            "writer boundary). Coverage query filters "
            "pp_multiplier_lab.selected_projections by league_id."
        ),
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
            "services/picks_getter_service.py:_load_photo_cache",
            "services/dashboard_card_contract.py:*",
            "routes/player.py:*",
        ],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display="initials placeholder when null",
        status="locked",
        notes=(
            "2026-05-04 Tier C: `picks_getter_service._load_photo_cache` "
            "no longer synthesises `/static/player-headshots/{nba_id}.png` "
            "when master_hub has no photo_url, and no longer backfills "
            "from the secondary `master_roster` collection. Reads "
            "master_hub.photo_url || master_hub.headshot_url only "
            "(same-owner aliases). Missing photo → None → frontend "
            "initials placeholder."
        ),
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
        status="locked",
        notes=(
            "2026-05-04: route-layer fallback chains removed. Card "
            "contract (dashboard_card_contract.to_card_contract) now "
            "reads `pick.get('player_name')` only — aliases `player` "
            "and `name` were silent-rename footguns with no owning "
            "writer and are removed. Canonical path still flows "
            "master_hub.display_name → universal_odds_sync → live_props "
            "→ picks_getter."
        ),
    ),
    "pp_projection_id": FieldSpec(
        name="pp_projection_id",
        owner_collection="pp_projection_id_cache",
        owner_field="projection_ids[]",
        writers=["services/pp_multiplier_lab.py:seed_projection_ids_from_scraper"],
        readers_allowed=[
            "services/pp_multiplier_lab.py:*",
            "routes/health_sync.py:_probe_pp_projection_ids",
        ],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display="— when missing (shows standard odds only)",
        status="locked",
        notes=(
            "2026-05-04 Tier C: staleness surfaced via "
            "/api/health/sync.sports.<sport>.pp_projection_ids. "
            "Probe returns `source_available=false` + concrete "
            "`last_refresh_age_sec` when scraper stops refreshing "
            "(>60 min threshold). Never synthesises a projection_id. "
            "Schema: one doc per league_id (NBA=7, MLB=2) with "
            "`projection_ids[]` array + `fetched_at`."
        ),
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
            "services/board/publisher.py:_rank_score",
        ],
        fallback_policy="NONE",
        null_policy="return_null",
        frontend_display="sinks to last when null; vision_score used as secondary sort only",
        status="locked",
        notes=(
            "2026-05-04: status flipped to `locked`; null_policy "
            "revised from `fail_loud` to `return_null`. The field is "
            "legitimately None when projection/line/p_model is "
            "missing (identity-failed picks, 0-book MLB, etc.) — "
            "that's a valid scoring outcome, not a data bug. "
            "Board publisher `_rank_score` dropped the legacy "
            "`ranking_score` alias (rename-era leftover with no live "
            "writer) but retains `vision_score` as a pinned "
            "secondary-sort fallback WITH a one-time-per-process "
            "SSOT warning so regression is still observable."
        ),
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
        status="locked",
        notes=(
            "2026-05-04 Tier C: canonical `side` enum is OVER|UNDER, "
            "owned by live_props.recommendation. Card contract "
            "(dashboard_card_contract.to_card_contract) now stamps "
            "`side` explicitly (was only stamping `direction` legacy "
            "alias). The lowercase `direction` alias remains on "
            "response picks for back-compat with ~8 backend call "
            "sites (picks_getter_service, market_moves_engine, "
            "mlb_cached_board_builder, sharp_edge_calculator); full "
            "deletion deferred until those migrate. Tolerance "
            "fallback in card contract (reading direction if "
            "recommendation missing) is an upstream-adapter-compat "
            "shim, not a cross-collection fallback — allowed."
        ),
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
        status="locked",
        notes=(
            "2026-05-04 Tier C: canonical stat_type is the "
            "upstream-scraper value (PTS, AST, REB, PRA, H+R+RBI, "
            "etc.). Display labels (e.g. PTS → \"Points\") are "
            "derived at render time via `_stat_short()` in "
            "dashboard_card_contract — never mutates the canonical. "
            "Composite splitter in intel_suite_calculator remains "
            "the one place that decomposes composites (H+R+RBI → "
            "[H, R, RBI]) for variance calc; no other decision "
            "logic reads the decomposed form. Alias `alt_stat` was "
            "never written by a live writer; no action needed."
        ),
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
        status="locked",
        notes=(
            "2026-05-04: route-layer fallback chains removed. Card "
            "contract (dashboard_card_contract.to_card_contract) now "
            "reads `pick.get('team')` only. Aliases team_abbr / "
            "player_team / home_team_abbr / away_team_abbr are no "
            "longer consulted — they were the #1 source of team/"
            "opponent contradictions after an offseason trade hit hub "
            "before live_props re-synced. Hub-level backfill in "
            "_stamp_hit_profile_on_picks is also disabled; a missing "
            "team now surfaces as `None` (UI renders `—`)."
        ),
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
        status="locked",
        notes=(
            "2026-05-04: NULLIFICATION PHASE shipped. Two fake-data "
            "sources neutralised ahead of the full Universal Vision "
            "Intel engine refactor: (1) `_generate_vision_fallback` "
            "in routes/ferrari_tiers.py now returns None instead of a "
            "templated \"Player stat at line — model sees X\" string; "
            "(2) `overlay_enrichment_cache` no longer reads from the "
            "stale JSON cache at /app/backend/data/{sport}_master_active_cache.json "
            "(it now stamps only the locally-computed volatility "
            "profile). The legacy JSON-reading body is preserved as "
            "`_overlay_enrichment_cache_legacy` strictly for archaeology. "
            "Full refactor — single universal writer at "
            "services/vision_intel/engine.py:enrich — scoped in "
            "/app/memory/VISION_INTEL_REFACTOR_SCOPE.md."
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
