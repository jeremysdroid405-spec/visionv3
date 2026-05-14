"""
Pydantic write contract for `{sport}_prop_scores` documents.
=============================================================

This schema is the single source of truth for what a score document
may contain. As of 2026-05-04 Tier F #4 the schema runs in **strict
mode** (`extra="forbid"`): any field produced by the scoring
adapter that has not been declared here causes the write batch to
raise `ValidationError`. Silent drift (the pre-Tier-F bug class
that hid `hetero_sigma_*` for two weeks) is structurally impossible
now.

The companion key-set parity test
(`tests/test_score_document_parity.py`) asserts that
`ScoreDocument.model_fields` mirrors
`_IDENTITY_FIELDS ∪ _SCORE_OUTPUT_FIELDS ∪ _UNIVERSAL_POOL_FIELDS ∪
{version_tag, computed_at, scored_at}` from
`prop_scores_store.py`. Adding a field on one side without the
other will fail CI.
"""
from __future__ import annotations

import os
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

logger = logging.getLogger(__name__)

SSOT_PYDANTIC_STRICT = os.environ.get("SSOT_PYDANTIC_STRICT", "true").lower() == "true"
# 2026-05-04 Tier F #4: with `extra="forbid"` LIVE, the schema itself
# raises `ValidationError` on any unknown field. The env flag now
# governs ONE thing: whether `validate_score_document` re-raises
# (=true, blocks the write batch) or downgrades to a WARN log line
# (=false, observation-only). Default is true so production is
# always strict; setting it to false is an emergency escape hatch
# only.


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
        # 2026-05-04 Tier F #4: flipped from `extra="allow"` to
        # `extra="forbid"`. Every projected score-doc field is now
        # explicitly declared on this model. Any adapter/projector
        # that adds a field without also declaring it here will fail
        # at write time — silent drift is impossible. The
        # `tests/test_score_document_parity.py` declared-vs-projected
        # parity guard locks this invariant in CI.
        extra="forbid",
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
    # ── Display reference odds (UI parity) ────────────────────────
    # 2026-05-15 — Frontend pick-card parity fix. NBA `tier_reference_*`
    # is a single-book pick (DK→FD→MGM→CSR→BOL) because the gate engine
    # is calibrated against single-book reference odds; MLB returns
    # ("consensus", mean(dk_p,fd_p)→amer) when DK+FD both quote, so MLB
    # cards display "CONSENSUS" while NBA cards display the book name.
    # The `display_reference_*` pair below carries the universal
    # "prefer DK+FD consensus when available, else fall back to the
    # tier reference book" pick for BOTH sports — purely for UI. Gates
    # / routing continue to read `tier_reference_*` untouched.
    display_reference_book: Optional[str]   = None
    display_reference_odds: Optional[float] = None
    routed_tier:            Optional[str] = None
    tier_gate_results:      Optional[Dict[str, Any]] = None

    # ── Phase 1 MLB context propagation (2026-05-15) ──────────────
    # Three context fields the audit identified as already-available
    # but dropped before reaching the score doc. Stamped by
    # `services/scoring/adapters/mlb_scoring._propagate_phase1_context`.
    # NBA leaves these None (not applicable).
    batter_hand:    Optional[str] = None     # 'L' | 'R' | 'S'
    batting_order:  Optional[int] = None     # 1..9 (lineup spot)
    venue:          Optional[str] = None     # stadium label

    # ── Phase 2A MLB pitcher matchup context (2026-05-15) ─────────
    # Stamped by `services/feature_hydration.py` (probable-pitcher
    # fields, source: free MLB Stats API) and by the MLB adapter's
    # `_propagate_phase1_context` (derived matchup flags). NBA leaves
    # these None.
    opp_pitcher_id:        Optional[int]   = None  # MLBAM person id
    opp_pitcher_name:      Optional[str]   = None
    opp_pitcher_throws:    Optional[str]   = None  # 'L' | 'R'
    probable_pitcher:      Optional[str]   = None  # alias of opp_pitcher_name
    opp_pitcher_era:       Optional[float] = None
    opp_pitcher_whip:      Optional[float] = None
    opp_pitcher_k9:        Optional[float] = None
    same_hand_matchup:     Optional[int]   = None  # 0/1
    opposite_hand_matchup: Optional[int]   = None  # 0/1

    # ── Universal ephemeral lifecycle (2026-05-15) ────────────────
    # Same contract as cached_board (services/boards/
    # board_lifecycle.LIFECYCLE_FIELDS). Stamped on every write by
    # `stamp_active_board_doc` in prop_scores_store._project_score_doc.
    ttl_purge_at:    Optional[datetime] = None
    stale_reason:    Optional[str]      = None
    stale_marked_at: Optional[datetime] = None
    updated_at:      Optional[datetime] = None

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
    # 2026-05-07 P0 Phase 4A: legacy `edge_pct` removed. Canonical
    # SSOT field is `edge_vs_fair` (decimal, 0.20 = 20%); previous
    # `edge_pct` was the same value × 100. Score docs no longer
    # persist the alias; this declaration was the strict-mode lock
    # that allowed the alias to live in `extra="forbid"` schemas.
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

    # ── Tier F #4 (2026-05-04): 108-field backfill for `extra="forbid"` flip ─
    # These are all fields that `_SCORE_OUTPUT_FIELDS` projects today but
    # were missing from the original Pydantic declaration set. Types are
    # derived from the inline comments in `prop_scores_store.py` and from
    # adapter output shapes. All are Optional because any given pick may
    # not compute every signal. Grouped by domain for readability — order
    # has no effect on Pydantic validation.

    # Distribution probability layer (2026-04-27)
    distribution_p_over:              Optional[float] = None
    distribution_p_under:             Optional[float] = None
    distribution_kind:                Optional[str]   = None
    distribution_selector_reason:     Optional[str]   = None
    distribution_sigma:               Optional[float] = None
    distribution_sigma_source:        Optional[str]   = None
    distribution_clamped:             Optional[bool]  = None
    distribution_effective_mu:        Optional[float] = None
    distribution_mu_floor_applied:    Optional[bool]  = None
    distribution_mu_floor_capped:     Optional[bool]  = None
    distribution_cv_floor_applied:    Optional[bool]  = None
    distribution_lambda:              Optional[float] = None
    distribution_threshold:           Optional[float] = None
    distribution_dispersion_r:        Optional[float] = None
    distribution_p_param:             Optional[float] = None

    # ECDF / calibration audit (2026-04-24)
    ecdf_p_over:                       Optional[float] = None
    ecdf_bucket:                       Optional[int]   = None
    ecdf_bucket_n:                     Optional[int]   = None
    ecdf_version:                      Optional[str]   = None
    raw_gaussian_p_over:               Optional[float] = None
    isotonic_p_over:                   Optional[float] = None
    probability_method:                Optional[str]   = None
    probability_calibration_applied:   Optional[bool]  = None
    raw_p_over:                        Optional[float] = None
    projection_intercept_applied:      Optional[bool]  = None
    projection_intercept_delta:        Optional[float] = None
    pre_intercept_projection:          Optional[float] = None

    # NBA availability guard (2026-04-27)
    availability_guard_applied:        Optional[bool]  = None
    availability_status:               Optional[str]   = None
    availability_sub_status:           Optional[str]   = None
    availability_guard_reason:         Optional[str]   = None
    dnp_risk_flag:                     Optional[bool]  = None
    injury_return_flag:                Optional[bool]  = None
    minutes_restriction_flag:          Optional[bool]  = None
    minutes_restriction_factor:        Optional[float] = None
    minutes_recovery_ratio:            Optional[float] = None
    games_missed_recently:             Optional[int]   = None
    return_game_number:                Optional[int]   = None
    normal_minutes:                    Optional[float] = None
    expected_minutes:                  Optional[float] = None
    expected_minutes_raw:              Optional[float] = None
    mu_before_availability_guard:      Optional[float] = None
    mu_after_availability_guard:       Optional[float] = None

    # NBA rate × minutes layer (2026-04-28)
    rate_model_applied:                Optional[bool]  = None
    rate_pts_per_min:                  Optional[float] = None
    rate_reb_per_min:                  Optional[float] = None
    rate_ast_per_min:                  Optional[float] = None
    mu_rate_projection:                Optional[float] = None
    mu_model_projection:               Optional[float] = None
    mu_final_projection:               Optional[float] = None
    rate_model_blend_weights:          Optional[Dict[str, Any]] = None
    rate_model_blend_mode:             Optional[str]   = None
    rate_model_trigger:                Optional[str]   = None

    # NBA recency μ blend (PTS / PRA)
    mu_recency_blended:                Optional[float] = None
    mu_recency_blend_l3:               Optional[float] = None
    mu_recency_blend_l5:               Optional[float] = None
    mu_recency_blend_l10_median:       Optional[float] = None
    mu_recency_blend_l20:              Optional[float] = None
    mu_recency_blend_weights:          Optional[Dict[str, Any]] = None
    mu_minutes_regression_applied:     Optional[bool]  = None
    mu_minutes_regression_factor:      Optional[float] = None
    mu_minutes_l3:                     Optional[float] = None
    mu_minutes_l10:                    Optional[float] = None
    mu_raw_model_projection:           Optional[float] = None

    # NBA shadow projections (E + VK2 PTS + REB/AST shadow rates) — AUDIT-ONLY
    mu_recency_E:                      Optional[float] = None
    mu_recency_E_applied:              Optional[bool]  = None
    delta_mu_E_vs_A:                   Optional[float] = None
    mu_recency_E_l3:                   Optional[float] = None
    mu_recency_E_l10:                  Optional[float] = None
    mu_recency_E_l10med:               Optional[float] = None
    mu_pts_vk2:                        Optional[float] = None
    mu_pts_vk2_applied:                Optional[bool]  = None
    delta_mu_pts_vk2_vs_vk1:           Optional[float] = None
    mu_rate_reb_shadow:                Optional[float] = None
    mu_rate_reb_shadow_applied:        Optional[bool]  = None
    delta_mu_rate_reb_shadow_vs_current: Optional[float] = None
    rate_reb_per_min_shadow:           Optional[float] = None
    mu_rate_ast_shadow:                Optional[float] = None
    mu_rate_ast_shadow_applied:        Optional[bool]  = None
    delta_mu_rate_ast_shadow_vs_current: Optional[float] = None
    rate_ast_per_min_shadow:           Optional[float] = None
    expected_minutes_shadow:           Optional[float] = None

    # NBA Phase 2 heteroscedastic σ (2026-05-02) — extends the
    # `hetero_sigma_base` / `hetero_sigma_multiplier` already declared.
    hetero_sigma_adjusted:             Optional[float] = None
    hetero_sigma_multipliers:          Optional[Dict[str, Any]] = None

    # NBA per-stat projection debias (2026-05-02)
    projection_raw_pre_debias:         Optional[float] = None
    projection_debias_amount:          Optional[float] = None
    projection_debias_source:          Optional[str]   = None

    # NBA RFA-only minutes penalty (2026-04-29)
    rfa_minutes_penalty_applied:       Optional[bool]  = None
    rfa_minutes_penalty_factor:        Optional[float] = None
    expected_minutes_before_rfa_penalty: Optional[float] = None
    expected_minutes_after_rfa_penalty:  Optional[float] = None

    # MLB Empirical-Bayes shrinkage (2026-04-24, zero-heavy stats)
    eb_shrunk_projection:              Optional[float] = None
    eb_player_career_mean:             Optional[float] = None
    eb_weight_model:                   Optional[float] = None
    eb_weight_player:                  Optional[float] = None
    eb_shrinkage_applied:              Optional[bool]  = None
    eb_skip_reason:                    Optional[str]   = None
    eb_career_sample_n:                Optional[int]   = None
    raw_hf_projection:                 Optional[float] = None

    # MLB pitcher / batter μ overrides (2026-04-27)
    mu_pitcher_workload_anchored:      Optional[float] = None
    mu_active_baseline_applied:        Optional[bool]  = None
    mu_active_baseline_value:          Optional[float] = None
    expected_ip_used:                  Optional[float] = None
    projection_model_version:          Optional[str]   = None

    # LOM audit
    lom_p_over:                        Optional[float] = None
    lom_version:                       Optional[str]   = None

    # War Zone CV scoring modifier (2026-04-22)
    war_zone_cv_modifier:              Optional[float] = None

    # Ceiling rate (PR-2, 2026-04-25, MLB war_zone)
    ceiling_rate:                      Optional[float] = None

    # ── 2026-05-04: explicit declaration of `momentum_data`. ──
    # The field already flowed through via `_SCORE_OUTPUT_FIELDS`,
    # but Tier F #4's parity guard now demands a typed declaration
    # AND the FIELD_OWNERSHIP registry registers `prop_scores.momentum_data`
    # as the owner column. Declared here as an Optional Dict to match
    # the writer in `services/master_sync.py::_enrich_nba_momentum`
    # (the dict shape is the `MomentumProfile.to_dict()` payload —
    # modifier / season_rank / l10_rank / narrative + audit fields).
    # `None` is the legitimate value for any prop where the master-hub
    # join failed (skip_reason=no_bdl_id / no_team_lookup) or where
    # the cache pair was absent — frontend suppresses the chip.
    momentum_data:                     Optional[Dict[str, Any]] = None

    # ── Matchup metadata (2026-05-13 Vision Intel diagnostic) ─────
    # These fields originate on `{sport}_live_props` from the Odds API
    # event payload. Previously dropped by `_project_score_doc` because
    # they weren't allowlisted; that bug surfaced as empty Vision Intel
    # cards because Gemini saw `opponent="TBD"`. Now preserved end-to-end
    # so the AI prompt + UI cards always have real team/opponent context.
    team:               Optional[str] = None    # 3-letter abbr
    team_full:          Optional[str] = None    # full team name
    opponent:           Optional[str] = None    # canonical abbr alias
    opponent_abbr:      Optional[str] = None    # alias of opponent
    opponent_team:      Optional[str] = None    # raw nba_live_props name
    home_team:          Optional[str] = None    # full name
    away_team:          Optional[str] = None    # full name
    is_home_team:       Optional[int] = None    # 0/1
    is_away_team:       Optional[int] = None    # 0/1
    commence_time:      Optional[Any] = None    # ISO string or datetime

    # ── Book-coverage / per-book layers (2026-05-13 MLB diagnostic) ──
    # The full per-book quote ladder + de-vig-companion `_odds_opp`
    # fields, carried from `{sport}_live_props` so de-vig, gates, UI
    # have real multi-book context.
    dk_layer:           Optional[Dict[str, Any]] = None
    fd_layer:           Optional[Dict[str, Any]] = None
    pp_layer:           Optional[Dict[str, Any]] = None
    bol_layer:          Optional[Dict[str, Any]] = None
    mgm_layer:          Optional[Dict[str, Any]] = None
    csr_layer:          Optional[Dict[str, Any]] = None
    # 2026-05-13 — "Pull from all books" expansion: ESPN BET, Hard Rock,
    # BetRivers, BetParx, BallyBet, Fliff. Short codes match
    # `universal_odds_sync` flat-field naming.
    eb_layer:           Optional[Dict[str, Any]] = None
    hrb_layer:          Optional[Dict[str, Any]] = None
    brv_layer:          Optional[Dict[str, Any]] = None
    prx_layer:          Optional[Dict[str, Any]] = None
    bly_layer:          Optional[Dict[str, Any]] = None
    flf_layer:          Optional[Dict[str, Any]] = None
    sharp_layer:        Optional[Dict[str, Any]] = None
    dk_line:   Optional[float] = None
    dk_odds:   Optional[float] = None
    dk_odds_opp: Optional[float] = None
    fd_line:   Optional[float] = None
    fd_odds:   Optional[float] = None
    fd_odds_opp: Optional[float] = None
    pp_line:   Optional[float] = None
    pp_odds:   Optional[float] = None
    pp_odds_opp: Optional[float] = None
    bol_line:  Optional[float] = None
    bol_odds:  Optional[float] = None
    bol_odds_opp: Optional[float] = None
    mgm_line:  Optional[float] = None
    mgm_odds:  Optional[float] = None
    mgm_odds_opp: Optional[float] = None
    csr_line:  Optional[float] = None
    csr_odds:  Optional[float] = None
    csr_odds_opp: Optional[float] = None
    eb_line:   Optional[float] = None
    eb_odds:   Optional[float] = None
    eb_odds_opp: Optional[float] = None
    hrb_line:  Optional[float] = None
    hrb_odds:  Optional[float] = None
    hrb_odds_opp: Optional[float] = None
    brv_line:  Optional[float] = None
    brv_odds:  Optional[float] = None
    brv_odds_opp: Optional[float] = None
    prx_line:  Optional[float] = None
    prx_odds:  Optional[float] = None
    prx_odds_opp: Optional[float] = None
    bly_line:  Optional[float] = None
    bly_odds:  Optional[float] = None
    bly_odds_opp: Optional[float] = None
    flf_line:  Optional[float] = None
    flf_odds:  Optional[float] = None
    flf_odds_opp: Optional[float] = None

    # ── Universal best-book / market-shopping edge (2026-05-13) ──────
    # Sport-agnostic exploitability layer on top of consensus TP.
    # Tells users where the actual best line lives across all books.
    best_book:                       Optional[str]   = None
    best_book_odds:                  Optional[float] = None
    best_book_implied_probability:   Optional[float] = None
    best_book_edge:                  Optional[float] = None
    # 2026-05-14 — `total_edge` = p_model - best_book_implied.
    # Combines model alpha with shopping alpha; this is the
    # actionable ROI edge UI-side. Display-only — NOT fed into
    # any gate config until distribution review is complete.
    total_edge:                      Optional[float] = None
    market_spread:                   Optional[float] = None
    market_spread_label:             Optional[str]   = None
    books_available_count:           Optional[int]   = None
    # ── Devig-basis fields + source tags (2026-05-14) ─────────────────
    # `best_book_raw_implied_probability` mirrors the legacy
    # `best_book_implied_probability` (always raw). The new
    # `best_book_devig_probability` is populated only when the best
    # book itself quoted both sides — used as the edge basis to keep
    # `best_bet_edge` and `consensus_edge` on the same devig plane.
    # `*_source` tags expose mathematical basis to the UI / audit:
    #   consensus_edge_source   → "devig" | "one_sided" / "raw_one_sided"
    #   best_bet_edge_source    → "devig" | "raw_one_sided"
    #   shopping_edge_source    → "devig_vs_devig" | "devig_vs_raw"
    best_book_raw_implied_probability:  Optional[float] = None
    best_book_devig_probability:        Optional[float] = None
    consensus_edge_source:              Optional[str]   = None
    best_bet_edge_source:               Optional[str]   = None
    shopping_edge_source:               Optional[str]   = None
    pp_payout_multiplier:  Optional[float] = None
    pp_market_key:         Optional[str] = None
    is_alternate_market:   Optional[bool] = None
    market_key:            Optional[str] = None


def validate_score_document(doc: Dict[str, Any]) -> Optional[str]:
    """Validate a single score doc against the strict Pydantic contract.

    `ScoreDocument.model_config.extra == "forbid"` is the structural
    invariant. Returns `None` on success. On failure:
      - SSOT_PYDANTIC_STRICT=true (default, production)  → re-raises
        ValidationError so the write batch aborts loudly.
      - SSOT_PYDANTIC_STRICT=false (escape hatch)        → logs WARN
        and returns a concise error string (caller tallies these
        for batch-level summary).

    The escape-hatch mode is only for emergency rollback (e.g. an
    adapter regression slipped past CI and prod is down); steady
    state is always strict.
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
