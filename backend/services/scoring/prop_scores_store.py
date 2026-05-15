"""
Per-sport, versioned prop scores store
======================================
Writes score docs to `{sport}_prop_scores` with a composite unique key
(canonical_key, version_tag) to support A/B testing, rollback,
and scoring experiments without destroying prior runs.

Contract:
 - Score docs contain ONLY scoring-stack fields + canonical identity
   + version metadata.
 - Strips scoring fields from caller's in-memory props so downstream
   writers CANNOT persist them into cached_board / tier collections.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# TTL cleanup — Tier F (2026-05-04)
# ──────────────────────────────────────────────────────────────────────
# Version tags whose docs are PRODUCTION-LIVE and must NEVER be
# expired by the TTL index. Every other version_tag (stage2-*,
# recompute-*, sh-audit-*, pick_history_*, ad-hoc audits) is eligible
# for 7-day auto-prune.
#
# SINGLE SOURCE OF TRUTH. Adding a new live tag requires adding it
# here and restarting backend so `_project_score_doc` skips the
# `ttl_at` stamp on new writes.
_LIVE_VERSION_TAGS: tuple = (
    "final-nba",
    "final-mlb",
    "final-nba-rt",
    "final-mlb-rt",
)

TTL_SECONDS:    int = 7 * 24 * 3600     # 7 days
TTL_INDEX_NAME: str = "ttl_at_7d_nonlive_ix"


async def ensure_ttl_index(db, sport: str) -> Dict[str, Any]:
    """Create (or keep) the TTL index on `{sport}_prop_scores.ttl_at`.

    Safe to call repeatedly at boot — Mongo ignores re-creates with
    identical specs. Index expires docs 7 days after `ttl_at`; docs
    without the field are untouched (live production docs never
    receive it — see `_project_score_doc`).

    Returns `{sport, index_name, ttl_seconds, scope}`. Rollback is
    `db.{sport}_prop_scores.drop_index("ttl_at_7d_nonlive_ix")`.
    """
    coll = db[f"{sport}_prop_scores"]
    try:
        await coll.create_index(
            "ttl_at",
            expireAfterSeconds=TTL_SECONDS,
            name=TTL_INDEX_NAME,
        )
        logger.info(
            "[TTL_INDEX:%s] ensured index=%s expireAfterSeconds=%d "
            "scope=docs with ttl_at set (non-live version_tags only)",
            sport, TTL_INDEX_NAME, TTL_SECONDS,
        )
        return {
            "sport":       sport,
            "index_name":  TTL_INDEX_NAME,
            "ttl_seconds": TTL_SECONDS,
            "scope":       "ttl_at-set docs (non-live version_tags)",
        }
    except Exception as exc:  # pragma: no cover
        logger.warning("[TTL_INDEX:%s] create_index failed: %s", sport, exc)
        return {"sport": sport, "error": repr(exc)}



# ──────────────────────────────────────────────────────────────────────
# Post-recompute enrichment preserve allowlist (2026-05-05, Option A).
#
# These fields are written by master_sync steps that run AFTER the
# scoring/recompute pass — they are never produced by the adapter
# (`_project_score_doc`) and therefore are absent from
# `_SCORE_OUTPUT_FIELDS`. Without explicit preservation, every full
# recompute (`mode="replace"`) wipes them via `ReplaceOne`, leaving the
# DB in a state where Vision Intel narratives, momentum trackers, etc.
# are silently lost until the next clean master_sync re-runs all
# enrichment steps end-to-end. A worker reload between step 3 (replace
# = wipe) and step 6 (re-stamp = recover) makes the loss permanent.
#
# Solution: before each `bulk_write([ReplaceOne…])`, batch-fetch these
# fields from the existing docs by `(canonical_key, version_tag)` and
# carry them forward onto the prepared replacement docs IF the new doc
# does not already set them. The new doc always wins when both are
# present — recompute output is authoritative for fields the adapter
# produces; preserve-only fields fill the gap left by the adapter.
#
# Field membership rule: include ONLY fields that are
#   1. written post-recompute (master_sync step >= 4), AND
#   2. either declared on `ScoreDocument` (`momentum_data`,
#      `intel_suite`) or live on the persisted doc as a tolerated
#      out-of-schema enrichment (`vision_intel`,
#      `vision_intel_generated_at`, `vision_intel_content_hash`).
#
# Do NOT include request-time-only fields (`scout_badges`,
# `active_badges`, `context_badges`, `pace_delta`) — confirmed 0
# coverage on persisted docs (see live DB scan 2026-05-05).
_PRESERVE_ON_REPLACE = (
    "vision_intel",
    "vision_summary",
    "vision_intel_generated_at",
    "vision_intel_content_hash",
    "momentum_data",
    "intel_suite",
)

# Exactly the fields a score doc may contain (plus canonical identity + versioning).
_SCORE_OUTPUT_FIELDS = (
    # ── Universal best-book / market-shopping edge (2026-05-13) ──────
    "best_book", "best_book_odds", "best_book_implied_probability",
    "best_book_edge", "total_edge", "market_spread", "market_spread_label",
    "books_available_count",
    # ── Devig-basis fields + source tags (2026-05-14) ────────────────
    "best_book_raw_implied_probability",
    "best_book_devig_probability",
    "consensus_edge_source",
    "best_bet_edge_source",
    "shopping_edge_source",
    # vision_score dimension
    "vision_score", "vision_score_raw", "quality_source", "fair_prob",
    "stability", "confidence", "edge_vs_fair",
    # vision_score_v2 dimension (2026-04-29) — directional, MLR-driven.
    # Always persisted; surfaced to consumers only when
    # `VISION_V2_ENABLED=true`. Never overwrites the v1 fields above.
    "vision_score_v2", "vision_v2_direction_margin",
    "vision_v2_direction_strength", "vision_direction_alignment",
    "vision_probability_component", "vision_projection_component",
    "vision_edge_component", "vision_consistency_component",
    "vision_context_component", "vision_market_confidence_component",
    "vision_volatility_penalty", "vision_v2_dir_gate", "vision_v2_weights",
    # tier dimension
    "tier", "tier_reason", "tier_reference_book", "tier_reference_odds",
    # Display reference odds (2026-05-15) — UI-only pick-card parity
    # between NBA and MLB. Prefers DK+FD consensus when both books
    # quote, else mirrors `tier_reference_*`. Gates still read
    # `tier_reference_*` untouched. See
    # `scoring_stack._pick_display_reference_odds`.
    "display_reference_book", "display_reference_odds",
    # Routed tier (2026-04-25) — odds-bucket assignment from the
    # FIRST-CLASS routing step. Stamped before any gate evaluates.
    # Universal across NBA / MLB / NFL: ref_odds <= -240 → safe_haven,
    # +150 ≤ ref_odds → war_zone, else front_lines, None → unqualified.
    # Final `tier` is constrained to {routed_tier, "unqualified"} by
    # the hard-guard in `scoring_stack.compute_tier`.
    "routed_tier",
    "tier_gate_results",
    # pp_utility dimension
    "pp_utility", "pp_utility_category", "pp_utility_components",
    "pp_multiplier", "pp_multiplier_label", "pp_multiplier_source",
    "pp_reference_source",
    "pp_playable", "pp_playability_reason",
    # p_true diagnostic panel
    "p_true_active", "p_true_method", "p_true_hit_rate", "p_true_model",
    "model_projection", "model_sigma",
    # MLB Probability Rebuild (2026-04-29) — distribution-canonical fields
    # that mirror the NBA pattern (projection μ + σ + line → P).
    "p_distribution",                  # canonical distribution-derived P (live)
    "lom_disabled",                    # always True for MLB live now
    "p_lom_shadow",                    # LOM output (no live effect)
    "probability_method_shadow",       # "lom_shadow" when LOM artifact ran
    "p_ecdf_shadow",                   # ECDF output (no live effect)
    "probability_method_shadow_ecdf",  # "ecdf_shadow" when ECDF artifact ran
    # VK2 (5-year adv-stat) diagnostics — parallel to legacy VK model_*
    "p_true_vk2", "vk2_projection", "vk2_sigma", "vk2_error",
    # Side-aware hit-rate diagnostics (the side passed to gates is in hit_rate)
    "hit_rate_over", "hit_rate_under",
    # HR sample-size (2026-04-25, HR v3). 10..20 inclusive when HR
    # was computed; None otherwise. Gate engine consumes this for
    # small-sample-aware hit_rate_gate evaluation.
    "hit_rate_sample_size",
    # 2026-05-01 — sub-window hit rates surfaced for the universal
    # L5 sub-gate (`gates/engine.py:_eval_hit_rate`) and for
    # recent-form display alongside the L20 gate input. Both NBA
    # and MLB populate these (NBA via `_compute_cv_and_hit_rate`,
    # MLB via `_calculate_subwindow_hit_rate`).
    "hit_rate_l5", "hit_rate_l10",
    # 2026-05-04 — window-explicit canonical name for the L20 OVER
    # hit rate per FIELD_OWNERSHIP.md:hit_rate_l20. Written in parallel
    # with legacy `hit_rate_over` until all readers migrate.
    "hit_rate_l20",
    # 2026-05-04 Tier F — TTL marker stamped ONLY on non-live
    # version_tags by `_project_score_doc`. See `ensure_ttl_index`
    # and `_LIVE_VERSION_TAGS` at the top of this module. Omit the
    # field on live docs so the partial TTL index never touches them.
    "ttl_at",
    # Projection-gap ranking signal (2026-02-20 shadow G1).
    # Persisted for `?sort=gap` opt-in on tier endpoints.
    "ranking_score_v2",
    # Sport-specific persisted enrichments (Stage 4 — MLB↔NBA carbon-copy).
    # MLB populates `tempo_modifier` and `intel_suite` at scoring-write time
    # via MLBScoringAdapter.enrich_score_doc(), replacing the previous
    # route-time enrichers (enrich_mlb_prop_with_tempo /
    # enrich_mlb_intel_suite). NBA leaves them None. Eliminates D11.
    "tempo_modifier", "intel_suite",
    # Canonical multi-sport DvP rank (2026-04-21). Written at pipeline
    # Phase 4b by services/defensive_rank_resolver.py — SINGLE source of
    # truth for opponent defensive rank across NBA / MLB / future NFL.
    "opponent_defensive_rank", "opponent_defensive_source",
    "opponent_defensive_stat_type",
    # 0-Book Exclusion Rule (2026-04-22). Classified by
    # services/scoring/coverage_filter.py during live_props load. A
    # prop with coverage_class=="pp_only" is filtered pre-scoring and
    # will never appear here; surfaced on every score doc so the UI /
    # read-side guards can sanity-check the invariant.
    "book_count", "coverage_class", "books_anchored",
    # 0.5-line stability metrics (2026-05). Replaces raw-CV in the
    # gate engine for binary MLB props. Persisted on every MLB doc;
    # NBA leaves these None.
    "avg_hit_margin", "avg_miss_margin",
    # Line-Outcome Model (LOM) audit trail (2026-05). When `lom_v1`
    # is the active probability_method, `lom_p_over` carries the
    # raw P(over) returned by the calibrated classifier so we can
    # diff it against `ecdf_p_over` and `raw_gaussian_p_over` in
    # observability endpoints. Sport-agnostic field — left None on
    # NBA / NFL until those LOM artifacts ship.
    "lom_p_over", "lom_version",
    # War Zone CV scoring modifier (2026-04-22). CV floor removed from
    # War Zone eligibility; CV now only contributes a small +/- to the
    # ranking score. Stamped by
    # `services/mlb_tier_sorter.war_zone_cv_modifier`.
    "war_zone_cv_modifier",
    # Universal lifecycle fields (2026-05-15). Defined in
    # services/boards/board_lifecycle.LIFECYCLE_FIELDS. Stamped on
    # every score doc via stamp_active_board_doc() so the same
    # contract holds across cached_board AND prop_scores.
    # `active` is also a legacy universal-pool field (see line ~514)
    # — listed once here is sufficient.
    "ttl_purge_at", "stale_reason", "stale_marked_at", "updated_at",
    # 2026-05-15 — Phase 1 MLB context field propagation. Source +
    # canonical name (see services/scoring/adapters/mlb_scoring.py
    # `_propagate_phase1_context` docstring).
    "batter_hand", "batting_order", "venue",
    # 2026-05-15 — Phase 2A MLB pitcher matchup context. Stamped by
    # `services/feature_hydration.py` (probable-pitcher fields) and
    # `services/scoring/adapters/mlb_scoring._propagate_phase1_context`
    # (matchup flags). Source: free MLB Stats API (no auth).
    "opp_pitcher_id", "opp_pitcher_name", "opp_pitcher_throws",
    "probable_pitcher",
    "opp_pitcher_era", "opp_pitcher_whip", "opp_pitcher_k9",
    "same_hand_matchup", "opposite_hand_matchup",
    # 2026-05-15 — Phase 2B MLB opposing-lineup context. Stamped by
    # `services/feature_hydration.py::_hydrate_opposing_lineup_for_pitcher`
    # for pitcher props. Only the size diagnostic propagates onto
    # score docs — the full lineup payload is consumed by the
    # model's pitcher-context feature builder but not persisted
    # (the lineup list is volatile and rebuildable from
    # `mlb_statcast_raw` / live BDL feed).
    "opposing_lineup_size",
    # Multi-book de-vig TP engine (2026-04-22). Replaces the legacy
    # avg(DK,FD) / avg(DK,MGM) implied-prob TP. Fields:
    #   tp               — final de-vigged true probability (0..100) or None
    #   tp_books_used    — count of books with BOTH sides available
    #   tp_books_list    — ["DK","FD","MGM","BOL"]
    #   tp_method        — "multi_book_devig_v1"
    #   tp_unavailable   — True when no book had both sides (hard-fails gate_tp)
    #
    # 2026-05-07 P0 Phase 4A: legacy `edge_pct` removed from this
    # allowlist. The value is a stale alias of canonical `edge_vs_fair`
    # (which is `edge_pct / 100`). Score docs continue to carry
    # `edge_vs_fair`; downstream readers (re-eval, debug snapshots,
    # publishers) have all been migrated to the canonical field.
    # Scoring math (`scoring_stack`, NBA/MLB adapters, `vision_v2`) is
    # unchanged — those modules continue to compute `edge_pct` as an
    # intermediate value, but it never leaves the in-process scoring
    # context now.
    "tp", "tp_books_used", "tp_books_list", "tp_method",
    "tp_unavailable",
    # Typed reason for tp=None (2026-04-24). One of:
    #   None                      – tp was successfully computed
    #   "unsupported_stat_family" – stat_type has no alias / no family
    #   "no_live_props_quote"     – no book quoted either side
    #   "alt_line_one_sided"      – at least one side priced but no book
    #                               returned the opposite side (inherent
    #                               DK/FD alt-market behaviour)
    #   "standard_line_missing_opp" – standard market that should have
    #                                 paired but didn't (upstream gap)
    "tp_unavailable_reason",
    # One-sided alt-market TP recovery (2026-04-24, spec step 5).
    #   tp_source           : "devig" | "one_sided" | None
    #   market_probability  : 0..1 scale market-implied prob (either
    #                         rigorous de-vig or raw one-sided implied);
    #                         duplicates `tp/100` for convenience — UIs
    #                         should prefer this field over `tp`.
    "tp_source",
    "market_probability",
    # Universal CV persistence (2026-04-23). CV is computed per
    # (player, stat_family) and is line-independent — the same value
    # attaches to every line (standard + alt) of the same family. The
    # `cv_status` field describes why `cv` is missing when it is None
    # (unavailable_stat_family | missing_source_distribution |
    # not_supported_yet). `cv` is no longer a derived-only
    # gate_details.cv_gate.actual value; it is a first-class field on
    # every score doc.
    "cv", "cv_status",
    # Universal HR status (2026-04-23). Like cv_status, distinguishes
    # a legitimate 0% hit rate from a null "insufficient data" case.
    "hit_rate_status",
    # Ceiling rate (PR-2, 2026-04-25). Persisted so the post-vision
    # re-evaluation can read the same `ceiling_rate` value the first
    # pass used. Required input for `ceiling_gate` (MLB war_zone) when
    # the re-eval rebuilds a NormalizedMetrics from the score doc.
    "ceiling_rate",
    # Combo projection synthesis (2026-04-23). `projection_method`
    # labels where `model_projection` / `model_sigma` came from:
    # "model" = direct VK/VK2; "combo_synth" = synthesized from two
    # component family models via empirical covariance. None means
    # no model-derived projection is available for this prop.
    "projection_method",
    # PRA dual-projection audit (2026-04-23). Persists BOTH the
    # direct model projection and the 3-way component-synth
    # projection side-by-side on PRA rows so we can evaluate them
    # against actual PRA totals once games complete. Live behaviour
    # unchanged — `model_projection` still drives scoring / ranking.
    "model_projection_direct", "model_sigma_direct",
    "model_projection_synth",  "model_sigma_synth",
    "projection_delta_abs",    "projection_delta_pct",
    "projection_compare_status", "projection_primary_method",
    # Universal Gate Engine (2026-04-22). The normalized gate output is
    # persisted on every scored prop so the UI / admin can explain the
    # gate outcome in the exact same structure regardless of sport.
    "gate_eval",
    # Global Identity Rule (2026-04-23). `bdl_player_id` is the
    # canonical join key stamped at ingest. `identity_status` is
    # "resolved" when present, "missing_bdl_id" when absent — in the
    # latter case HR / CV / model projections are skipped and their
    # *_status fields report "missing_bdl_id".
    "bdl_player_id", "identity_status",
    # Expected-minutes composition (2026-04-23). Narrow NBA rollout:
    # PTS / PRA only, only in the bench regime. Stamp the audit trail
    # so admin / eval can compare baseline vs composed.
    "minutes_composition_applied",
    "minutes_composition_baseline_projection",
    "minutes_composition_predicted_minutes",
    "minutes_composition_per_min_rate",
    # Empirical-Bayes post-shrinkage (2026-04-24, MLB zero-heavy stats).
    # When the flag MLB_HF_EB_SHRINKAGE_ENABLED is on and the stat is
    # whitelisted (home_runs / rbis / total_bases / hits+runs+rbis),
    # `model_projection` is overwritten with the shrunk value and these
    # audit fields record the raw value, the player's career mean, the
    # weights used, and whether the shrinkage actually applied.
    "raw_hf_projection",
    "eb_shrunk_projection",
    "eb_player_career_mean",
    "eb_weight_model",
    "eb_weight_player",
    "eb_shrinkage_applied",
    "eb_skip_reason",
    "eb_career_sample_n",
    # Universal ECDF probability-layer audit fields (2026-04-24).
    # Populated by adapters when the ECDF artifact for this stat
    # family is present and the selected bucket has >= min-bucket-n
    # residuals. NBA sets these via `calibration_meta`; MLB sets them
    # directly on raw_prop in `mlb_scoring.build_context`. When ECDF
    # is consumed, `probability_method="ecdf"`; when it falls back,
    # `probability_method in {"gaussian", "isotonic"}`.
    "probability_method",
    "ecdf_p_over", "ecdf_bucket", "ecdf_bucket_n", "ecdf_version",
    "raw_gaussian_p_over", "isotonic_p_over",
    "probability_calibration_applied", "raw_p_over",
    "projection_intercept_applied", "projection_intercept_delta",
    "pre_intercept_projection",
    # Distribution-based probability layer (2026-04-27) — replaces the
    # legacy `50-|z|*10` heuristic in MLB HF.predict() with a clean
    # normal-CDF on (μ from MLR, σ from CV-with-family-floor). Audit
    # fields persisted so the calibration observability stack can
    # diff distribution_p_over against ecdf_p_over and raw_gaussian.
    # Distribution-based probability layer (2026-04-27) — universal
    # probability engine routing (sport, family, line) to Normal CDF /
    # Bernoulli / Poisson / Negative Binomial. Audit fields persisted
    # so observability can diff against ecdf_p_over / raw_gaussian.
    "distribution_p_over", "distribution_p_under",
    "distribution_kind", "distribution_selector_reason",
    "distribution_sigma", "distribution_sigma_source", "distribution_clamped",
    "distribution_effective_mu", "distribution_mu_floor_applied",
    "distribution_mu_floor_capped", "distribution_cv_floor_applied",
    "distribution_lambda", "distribution_threshold",
    "distribution_dispersion_r", "distribution_p_param",
    # 2026-04-27 — μ-override audit (HF input fixes for MLB; NBA recency blend)
    "mu_raw_model_projection",         # μ before any override (MLB or NBA)
    "mu_pitcher_workload_anchored",    # MLB — 60/40 workload×model blend (K) or analytical (Outs)
    "mu_active_baseline_applied",      # MLB — batter 0.5-line baseline floor fired
    "mu_active_baseline_value",        # MLB — baseline value used when applied
    "expected_ip_used",                # MLB — workload anchor IP for pitcher props
    "projection_model_version",        # MLB — "MLB_HF_v1.0" | "MLB_HF_v1.0_pitcher_outs_analytical"
    # NBA — recency-weighted μ blend (PTS / PRA only)
    "mu_recency_blended",
    "mu_recency_blend_l3",
    "mu_recency_blend_l10_median",
    "mu_recency_blend_l20",
    "mu_recency_blend_l5",
    "mu_recency_blend_weights",
    "mu_minutes_regression_applied",
    "mu_minutes_regression_factor",
    "mu_minutes_l3",
    "mu_minutes_l10",
    # NBA — unified availability guard (2026-04-27)
    "availability_guard_applied",
    "availability_status",
    "availability_sub_status",
    "minutes_recovery_ratio",
    "availability_guard_reason",
    "dnp_risk_flag",
    "injury_return_flag",
    "minutes_restriction_flag",
    "games_missed_recently",
    "return_game_number",
    "normal_minutes",
    "expected_minutes",
    "minutes_restriction_factor",
    "mu_before_availability_guard",
    "mu_after_availability_guard",
    # NBA — rate × minutes projection layer (2026-04-28). PTS/PRA only.
    # Stamps include rate inputs, expected minutes (raw + post-restriction),
    # and the rate / model μ contributions plus the final blended μ.
    "rate_model_applied",
    "rate_pts_per_min",
    "rate_reb_per_min",
    "rate_ast_per_min",
    "expected_minutes",
    "expected_minutes_raw",
    "mu_rate_projection",
    "mu_model_projection",
    "mu_final_projection",
    # NBA per-stat projection debias (2026-05-02). Persisted so every
    # score doc carries audit of how much was subtracted from the raw
    # projection and which calibration snapshot produced the value.
    "projection_raw_pre_debias",
    "projection_debias_amount",
    "projection_debias_source",
    # NBA Phase 2 — heteroscedastic sigma (2026-05-02). Per-prop σ
    # audit captured from `_engine_p_over` so every score doc records
    # the base σ, the adjusted σ after bucket multipliers, and the
    # exact multipliers applied. Sigma is adjusted BEFORE the
    # probability engine evaluates `distribution_sigma`, so
    # `hetero_sigma_adjusted` should match `distribution_sigma` when
    # the distribution kind is Gaussian.
    "hetero_sigma_base",
    "hetero_sigma_adjusted",
    "hetero_sigma_multipliers",
    "rate_model_blend_weights",
    "rate_model_blend_mode",
    "rate_model_trigger",
    # NBA — RFA-only minutes penalty (2026-04-29). Active when
    # NBA_RFA_MINUTES_PENALTY env var < 1.0; default 1.0 = disabled.
    "rfa_minutes_penalty_applied",
    "rfa_minutes_penalty_factor",
    "expected_minutes_before_rfa_penalty",
    "expected_minutes_after_rfa_penalty",
    # NBA — Shadow Recipe E projection (2026-04-28). AUDIT-ONLY: never
    # replaces μ_current. Tracked for 7-day forward-test validation.
    "mu_recency_E",
    "mu_recency_E_applied",
    "delta_mu_E_vs_A",
    "mu_recency_E_l3",
    "mu_recency_E_l10med",
    "mu_recency_E_l10",
    # NBA — Shadow VK2 PTS projection (2026-04-28). AUDIT-ONLY: never
    # replaces μ_current. Captured for 7-day forward-test before any
    # PTS-VK2 promotion decision.
    "mu_pts_vk2",
    "mu_pts_vk2_applied",
    "delta_mu_pts_vk2_vs_vk1",
    # NBA — Shadow REB/AST rate × minutes (2026-04-29). AUDIT-ONLY:
    # never replaces μ_current. Stamped from
    # `_maybe_apply_shadow_rate_reb_ast`. Sample sizes (n=29 REB,
    # n=21 AST) are too small to flip a single pick today; tracking
    # for forward-test until a meaningful sample accumulates.
    "mu_rate_reb_shadow",
    "mu_rate_reb_shadow_applied",
    "delta_mu_rate_reb_shadow_vs_current",
    "rate_reb_per_min_shadow",
    "mu_rate_ast_shadow",
    "mu_rate_ast_shadow_applied",
    "delta_mu_rate_ast_shadow_vs_current",
    "rate_ast_per_min_shadow",
    "expected_minutes_shadow",
    # Universal SSOT canonical-pool flags (2026-04-25). Stamped at
    # ingest by `services/universal_odds_sync.py::_normalize_market_data`
    # on every prop. PrizePicks is now an overlay on top of a
    # multi-book canonical pool; these fields tell the read-side
    # whether a given canonical is PP-quoted or sportsbook-anchored.
    #   pp_available        — True iff PrizePicks quoted this canonical
    #   playable_on_pp      — alias of pp_available for filter clarity
    #   source_anchor       — "prizepicks" | "sportsbook_fallback"
    #   anchor_book         — book that seeded the canonical (the first
    #                         book seen in priority order during ingest)
    "pp_available", "playable_on_pp", "source_anchor", "anchor_book",
    # 2026-05 missing-value policy — list of features that the
    # underlying ML model received as silent defaults rather than
    # real values. Persisted on every score doc for observability /
    # downstream gating to see the data-deficit surface explicitly.
    "feature_health",
    # 2026-05 injury context — NBA team-level injury aggregates. The
    # underlying ML model is NOT trained on these features; they are
    # carried on the score doc for observability and for downstream
    # consumers (vacuum service, gating, future retrains). Live model
    # behaviour is unchanged.
    "injury_context",
)

_IDENTITY_FIELDS = (
    "canonical_key", "sport", "event_id", "player_name",
    "stat_type", "line", "recommendation",
)

# Matchup-metadata allowlist (2026-05-13 — Vision Intel diagnostic).
# These fields originate on `{sport}_live_props` and were being silently
# dropped by `_project_score_doc` because they were not in any allowlist.
# Without them on `{sport}_prop_scores`:
#   • Vision Intel saw `opponent = "TBD"` (vision_intel_service.py:247)
#   • Master-sync DvP/context badge attachment had no team to join on
#   • Player cards rendered with empty opponent / tipoff rows
# Preserving these does NOT touch scoring math; they are pure
# identity/context fields that flow downstream to the UI + AI prompt.
_MATCHUP_METADATA_FIELDS = (
    "team",            # 3-letter abbr ('CLE')
    "team_full",       # full name ('Cleveland Cavaliers')
    "opponent",        # canonical 3-letter abbr (downstream-friendly alias)
    "opponent_abbr",   # alias of opponent — frontend reads either
    "opponent_team",   # 3-letter abbr ('DET') — raw nba_live_props name
    "home_team",       # full name ('Detroit Pistons')
    "away_team",       # full name ('Cleveland Cavaliers')
    "is_home_team",    # 0/1
    "is_away_team",    # 0/1
    "commence_time",   # ISO string from Odds API event payload
)

# Universal pool lifecycle fields — present on every {sport}_prop_scores
# document regardless of sport. Used by the universal board engine
# (services/board/*) and the 60-second game-start scanner.
_UNIVERSAL_POOL_FIELDS = (
    "active", "inactive_reason", "active_changed_at", "game_start_utc",
)

# ── Book-coverage allowlist (2026-05-13 — MLB multi-book diagnostic) ──
# These fields originate on `{sport}_live_props` from `universal_odds_sync`'s
# multi-pass canonical pool builder and represent the FULL set of per-book
# quotes for a single (player, stat, line, side) tuple. Without them on
# `{sport}_prop_scores`:
#   • de-vig TP engine ran on phantom data — every score doc had
#     dk_line=None, fd_line=None, pp_line=None, … and was forced to
#     fall back to the raw anchor odds (= ~4-5pp inflated TP)
#   • multi-book σ-penalty / book_count-aware gating was effectively
#     disabled because the gating layer saw `book_count: 2` but
#     could not enumerate which 2 books quoted the prop
#   • frontend reference-odds chips showed empty for the majority of
#     MLB cards (no per-book line/odds to display)
# The fields are pure provenance — they do NOT touch scoring math
# directly. Preserving them restores the multi-book context the
# scoring/gating layer was designed around.
_BOOK_LAYER_FIELDS = (
    # Per-book layer objects (rich payload: book, line, odds, fetched_at,
    # and `_opp` companion for the opposite side used by the de-vig engine)
    "dk_layer", "fd_layer", "pp_layer", "bol_layer", "mgm_layer", "csr_layer",
    # 2026-05-13 — 6 new US sportsbooks (ESPN BET / Hard Rock / BetRivers
    # / BetParx / Bally Bet / Fliff). Free under regions=us.
    "eb_layer", "hrb_layer", "brv_layer", "prx_layer", "bly_layer", "flf_layer",
    "sharp_layer",
    # Flat per-book line + odds + odds_opp (downstream gates / UI prefer these)
    "dk_line",  "dk_odds",  "dk_odds_opp",
    "fd_line",  "fd_odds",  "fd_odds_opp",
    "pp_line",  "pp_odds",  "pp_odds_opp",
    "bol_line", "bol_odds", "bol_odds_opp",
    "mgm_line", "mgm_odds", "mgm_odds_opp",
    "csr_line", "csr_odds", "csr_odds_opp",
    "eb_line",  "eb_odds",  "eb_odds_opp",
    "hrb_line", "hrb_odds", "hrb_odds_opp",
    "brv_line", "brv_odds", "brv_odds_opp",
    "prx_line", "prx_odds", "prx_odds_opp",
    "bly_line", "bly_odds", "bly_odds_opp",
    "flf_line", "flf_odds", "flf_odds_opp",
    # Aggregate book count (universal_odds_sync may or may not stamp this
    # upstream — the de-vig engine re-derives it from the present layers
    # when missing). Carrying it through so downstream readers don't have
    # to recompute.
    "book_count",
    # PP-specific overlay metadata
    "pp_payout_multiplier", "pp_market_key",
    # Market structure fields used by the alt-line / one-sided gates
    "is_alternate_market", "market_key",
)

# Retained for backward compatibility with services.scoring.prop_scores_store callers
SCORE_FIELDS = _IDENTITY_FIELDS + _SCORE_OUTPUT_FIELDS
SCORES_COLLECTION = "mlb_prop_scores"  # legacy default; now sport-specific


def _project_score_doc(
    context_out: Dict[str, Any], version_tag: str, computed_at: str
) -> Dict[str, Any]:
    # SSOT enforcement (2026-05-03): before projecting, flag any keys
    # produced by the adapter that are NOT in our allowlists. These
    # would be silently dropped below — which has burned us multiple
    # times (hetero_sigma_*, etc.). Log at WARNING so they're visible
    # in supervisor logs and can be added to the allowlist or the
    # Pydantic schema once that lands.
    _known_keys = (
        set(_IDENTITY_FIELDS)
        | set(_SCORE_OUTPUT_FIELDS)
        | set(_MATCHUP_METADATA_FIELDS)
        | set(_UNIVERSAL_POOL_FIELDS)
        | set(_BOOK_LAYER_FIELDS)
        | {"version_tag", "computed_at", "scored_at"}
    )
    _dropped = [k for k in context_out.keys() if k not in _known_keys]
    if _dropped:
        # Sample log: one entry per unique adapter-output field that's
        # being dropped. Prevents log spam while still alerting dev.
        try:
            _seen = _project_score_doc.__dict__.setdefault("_dropped_keys_seen", set())
            new_drops = [k for k in _dropped if k not in _seen]
            if new_drops:
                _seen.update(new_drops)
                logger.warning(
                    f"[SSOT_DROP] score doc fields being silently dropped "
                    f"by allowlist (first occurrence): {new_drops}. "
                    f"Add to _SCORE_OUTPUT_FIELDS or remove from adapter."
                )
        except Exception:  # pragma: no cover — logging must never break writes
            pass

    doc = {k: context_out.get(k) for k in _IDENTITY_FIELDS if k in context_out}
    for k in _SCORE_OUTPUT_FIELDS:
        if k in context_out:
            doc[k] = context_out[k]
    # Matchup-metadata projection (2026-05-13 Vision Intel fix).
    # Carry the upstream team/opponent/event context onto the score
    # doc so master_sync, the JIT Vision Intel reaper, and the
    # frontend cards all see real values instead of None.
    for k in _MATCHUP_METADATA_FIELDS:
        if k in context_out and context_out[k] is not None:
            doc[k] = context_out[k]
    # Book-coverage projection (2026-05-13 MLB multi-book diagnostic).
    # Carry every per-book layer/line/odds from the live_props row onto
    # the score doc so de-vig, gates, and the UI all have real
    # multi-book context instead of None-laden phantoms.
    for k in _BOOK_LAYER_FIELDS:
        if k in context_out and context_out[k] is not None:
            doc[k] = context_out[k]
    # Universal pool lifecycle fields — default to "active=True" with no
    # inactivation reason. scoring/recompute.py sets game_start_utc from
    # the raw prop's commence_time so the universal scanner can flip
    # tipped-off props to active=False.
    for k in _UNIVERSAL_POOL_FIELDS:
        if k in context_out:
            doc[k] = context_out[k]
    doc.setdefault("active", True)
    doc.setdefault("inactive_reason", None)
    doc.setdefault("active_changed_at", None)
    doc.setdefault("game_start_utc", None)
    # 2026-05-15 — Universal lifecycle stamp (services/boards/
    # board_lifecycle.py). Every score doc this writer emits MUST
    # carry the same active/inactive contract as cached_board docs.
    # ``stamp_active_board_doc`` writes a fresh ``updated_at`` and
    # clears any stale TTL/reason markers a previous off-slate cycle
    # may have left on the doc, so re-appearance auto-restores.
    try:
        from services.boards.board_lifecycle import (
            stamp_active_board_doc,
        )
        stamp_active_board_doc(doc)
    except Exception:  # pragma: no cover
        # Fail-soft: lifecycle stamping must never abort scoring.
        # The startup audit + admin /normalize endpoint will catch
        # any doc that fell through this branch.
        pass
    doc["version_tag"] = version_tag
    doc["computed_at"] = computed_at
    # SSOT: `scored_at` is the ownership-declared freshness timestamp
    # consumed by /api/health/sync. Pre-2026-05-03 this field was NEVER
    # written, silently dead-ending the entire staleness monitoring
    # system. Per FIELD_OWNERSHIP.md:scored_at, write here and only
    # here. Kept equal to computed_at for this migration phase; future
    # work may distinguish them (e.g. scored_at = when we finished the
    # probability pass, computed_at = when we persisted).
    doc["scored_at"] = computed_at

    # SSOT Tier F (2026-05-04) — TTL self-prune for non-live tags.
    # The whitelist below is the EXCLUSIVE set of version_tags whose
    # docs are considered production-live and must never be expired.
    # Everything else (stage2-*, sh-recal-*, pick_history_*, ad-hoc
    # audit tags) gets `ttl_at = scored_at`; Mongo's TTL monitor
    # then deletes those rows 7 days after scored_at. Live docs NEVER
    # receive the `ttl_at` field, so the partial TTL index never
    # sees them. See `ensure_ttl_index()` below for the index spec.
    #
    # To add a new live version_tag, add it to this tuple.
    # Intentional: this is the SINGLE source of truth for "which
    # version_tags are live" — not a regex, not an env var.
    if version_tag not in _LIVE_VERSION_TAGS:
        doc["ttl_at"] = computed_at
    return doc


async def ensure_indexes(db, sport: str) -> None:
    """Create the required indexes on the sport's score collection."""
    coll_name = f"{sport}_prop_scores"
    coll = db[coll_name]
    # Drop any legacy index that conflicts with the composite unique key.
    try:
        existing = await coll.index_information()
        if "uniq_canonical" in existing:
            await coll.drop_index("uniq_canonical")
    except Exception as e:
        logger.warning(f"[SCORES_STORE:{sport}] legacy index cleanup skipped: {e}")

    try:
        await coll.create_index(
            [("canonical_key", 1), ("version_tag", 1)],
            unique=True, name="uniq_canonical_version",
        )
        await coll.create_index([("vision_score", -1)], name="idx_vision_score_desc")
        await coll.create_index([("tier", 1)], name="idx_tier")
        await coll.create_index([("pp_utility", -1)], name="idx_pp_utility_desc")
        await coll.create_index([("computed_at", -1)], name="idx_computed_at_desc")
        # Universal board-engine indexes (multi-sport lifecycle).
        # idx_tier_active_vision: covers the universal board query
        #   find({version_tag, tier, active, game_start_utc}).sort(vision_score DESC).limit(N)
        # idx_game_start_active: powers the 60-second game-start scanner
        #   update_many({active:True, game_start_utc:{$lte: now}})
        await coll.create_index(
            [("version_tag", 1), ("tier", 1), ("active", 1), ("vision_score", -1)],
            name="idx_tier_active_vision",
        )
        await coll.create_index(
            [("active", 1), ("game_start_utc", 1)],
            name="idx_game_start_active",
        )
    except Exception as e:
        logger.warning(f"[SCORES_STORE:{sport}] index create warning: {e}")


async def write_versioned_scores(
    db,
    sport: str,
    score_docs: List[Dict[str, Any]],
    version_tag: str,
    dry_run: bool = False,
    mode: str = "replace",
) -> Dict[str, Any]:
    """
    Persist score docs for a single sport and version_tag.

    Modes:
      - "replace": wipe every doc with the same version_tag, then bulk
        insert. Used by the full recompute path.
      - "upsert": per-doc upsert keyed on (canonical_key, version_tag).
        Used by Step 5 real-time ingest so one prop landing does not
        blow away the other 2,999 scored props in the pool.
    In dry_run mode, does not write anything.
    """
    coll_name = f"{sport}_prop_scores"
    coll = db[coll_name]
    computed_at = datetime.now(timezone.utc).isoformat()

    prepared = [
        _project_score_doc(d, version_tag=version_tag, computed_at=computed_at)
        for d in score_docs
    ]

    # ── Tier D Pydantic write contract (2026-05-04) ────────────────
    # Validate every doc against `ScoreDocument`. Default mode is
    # log-and-count (SSOT_PYDANTIC_STRICT=false) so we bake the
    # contract against real slates without blocking writes. Set the
    # env var to true to flip strict and raise on any violation.
    from services.scoring.score_document_schema import (
        validate_score_document, SSOT_PYDANTIC_STRICT,
    )
    pydantic_failures: List[str] = []
    for p in prepared:
        err = validate_score_document(p)
        if err is not None:
            pydantic_failures.append(err)
    if pydantic_failures:
        logger.warning(
            "[SCORES_STORE:%s] Pydantic validation: %d/%d docs failed "
            "schema (strict=%s). First 3: %s",
            sport, len(pydantic_failures), len(prepared),
            SSOT_PYDANTIC_STRICT, pydantic_failures[:3],
        )

    if dry_run:
        return {
            "sport": sport,
            "collection": coll_name,
            "version_tag": version_tag,
            "computed_at": computed_at,
            "prepared": len(prepared),
            "written": 0,
            "mode": mode,
            "dry_run": True,
            "pydantic_failures": len(pydantic_failures),
        }

    await ensure_indexes(db, sport)

    if mode == "upsert":
        upserted = 0
        modified = 0
        upsert_cks: list = []
        for doc in prepared:
            clean = {k: v for k, v in doc.items() if k != "_id"}
            ck = clean.get("canonical_key")
            if not ck:
                continue
            upsert_cks.append(ck)
            res = await coll.update_one(
                {"canonical_key": ck, "version_tag": version_tag},
                {"$set": clean},
                upsert=True,
            )
            if getattr(res, "upserted_id", None) is not None:
                upserted += 1
            elif getattr(res, "modified_count", 0):
                modified += 1
        logger.info(
            f"[SCORES_STORE:{sport}] mode=upsert version='{version_tag}' "
            f"upserted={upserted} modified={modified} → {coll_name}"
        )

        # 2026-05-14 — Cross-tag active=True sweep for the upsert path.
        # The chunked recompute (see routes/scores.py) writes via this
        # branch. Same SSOT invariant as the replace path: after writing
        # to the canonical live tag, flip active=False on any other-tag
        # row sharing a written canonical_key. Without this, shadow /
        # legacy tags can carry stale active=True rows that pollute the
        # duplicate-prop audit.
        cross_tag_deactivated_upsert = 0
        live_tag_for_sport = f"final-{sport}-rt"
        if upsert_cks and version_tag == live_tag_for_sport:
            try:
                _res = await coll.update_many(
                    {
                        "canonical_key": {"$in": upsert_cks},
                        "version_tag": {"$ne": live_tag_for_sport},
                        "active": True,
                    },
                    {"$set": {
                        "active": False,
                        "inactive_reason": "stale_tag_active_sweep_upsert",
                        "active_changed_at": datetime.now(timezone.utc),
                    }},
                )
                cross_tag_deactivated_upsert = _res.modified_count or 0
                if cross_tag_deactivated_upsert:
                    logger.info(
                        f"[SCORES_STORE:{sport}] mode=upsert "
                        f"version='{version_tag}' cross_tag_deactivated="
                        f"{cross_tag_deactivated_upsert} (stale-tag SSOT sweep)"
                    )
            except Exception as _xtag_exc:
                logger.warning(
                    f"[SCORES_STORE:{sport}] mode=upsert cross-tag active "
                    f"sweep failed ({_xtag_exc}); continuing — best-effort."
                )

        return {
            "sport": sport,
            "collection": coll_name,
            "version_tag": version_tag,
            "computed_at": computed_at,
            "prepared": len(prepared),
            "written": upserted + modified,
            "upserted": upserted,
            "modified": modified,
            "cross_tag_deactivated": cross_tag_deactivated_upsert,
            "mode": "upsert",
            "dry_run": False,
            "pydantic_failures": len(pydantic_failures),
        }

    # Default: replace
    # Race-safe bulk replace (2026-04-30 fix, 75/76 sync failures root cause):
    #
    # The original pattern was:
    #     delete_many({"version_tag": tag}) → insert_many(new_docs, ordered=False)
    # Between the delete and the insert, the realtime engine
    # (`services/board/engine.py::on_new_props`) could upsert a doc with
    # the same (canonical_key, version_tag) pair. The subsequent
    # insert_many hit `uniq_canonical_version` and raised BulkWriteError,
    # failing the WHOLE sync.
    #
    # New pattern is race-safe:
    #   1. Bulk ReplaceOne(upsert=True) for every prepared doc. Any
    #      concurrent realtime upsert for the same key just produces a
    #      replace here (no conflict).
    #   2. delete_many for keys NOT in the new set — sweeps stale rows
    #      from the previous rebuild without a destructive bulk delete
    #      that creates a race window.
    inserted_or_replaced = 0
    stale_deleted = 0

    if not prepared:
        # ── 2026-05-08 P0 0-write guard ────────────────────────────────
        # Pre-fix this branch wiped the whole tag whenever the score
        # batch was empty. Verified blackout incident on 2026-05-08
        # 01:15:55Z: master_sync ran during an upstream odds outage,
        # the scoring loop produced 0 docs, and `delete_many({"version_tag":
        # version_tag})` swept 2,154 `final-nba` + 2,277 `final-nba-rt`
        # rows. The next master_sync at 02:15 confirmed the empty
        # state (`stale_swept=0`). This left the entire NBA board
        # invisible until ingestion recovered.
        #
        # Replace mode now treats `prepared==0` as a NO-OP (preserve
        # existing state). Rationale: an empty batch from the scoring
        # loop is structurally indistinguishable from "upstream odds
        # provider is silent right now"; in either case the safer
        # action is to keep what we have, since stale data still has
        # `active=False/inactive_reason="game_started"` lifecycle
        # gates downstream that prevent it from being shown to users
        # past tip-off. A truly empty slate (e.g. off-season) would
        # let the existing TTL / age-based cleanup paths drain the
        # collection naturally — no need for the catastrophic sweep.
        #
        # If the historic wipe-on-empty contract is ever needed again
        # (e.g. for a manual full-collection reset), call
        # `coll.delete_many({"version_tag": version_tag})` explicitly
        # from the caller — do not re-introduce it here.
        logger.warning(
            f"[SCORES_STORE:{sport}] mode=replace version='{version_tag}' "
            f"replace skipped stale_sweep because written=0 — preserving "
            f"existing docs to survive upstream blackouts. "
            f"(0-write guard, services/scoring/prop_scores_store.py)"
        )
        return {
            "sport":                sport,
            "collection":           coll_name,
            "version_tag":          version_tag,
            "computed_at":          computed_at,
            "prepared":             0,
            "written":              0,
            "stale_swept":          0,
            "mode":                 "replace",
            "dry_run":              False,
            "skipped_stale_sweep":  True,
            "skipped_reason":       "empty_batch_zero_write_guard",
            "pydantic_failures":    len(pydantic_failures),
        }
    else:
        from pymongo import ReplaceOne
        clean = [{k: v for k, v in d.items() if k != "_id"} for d in prepared]
        seen: Dict[str, Dict[str, Any]] = {}
        for d in clean:
            ck = d.get("canonical_key")
            if ck:
                seen[ck] = d
        deduped = list(seen.values())

        # ── Option A preserve pass (2026-05-05) ─────────────────────────
        # Carry post-recompute enrichments (vision_intel, momentum_data,
        # intel_suite, …) forward across the destructive ReplaceOne so a
        # full recompute does NOT wipe them. The new doc always wins
        # when both are present — only fields ABSENT on the new doc are
        # filled from the existing record. See `_PRESERVE_ON_REPLACE`.
        if deduped:
            cks = [d["canonical_key"] for d in deduped if d.get("canonical_key")]
            existing_map: Dict[str, Dict[str, Any]] = {}
            try:
                projection = {"_id": 0, "canonical_key": 1}
                projection.update({f: 1 for f in _PRESERVE_ON_REPLACE})
                cursor = coll.find(
                    {"canonical_key": {"$in": cks}, "version_tag": version_tag},
                    projection,
                )
                async for ex in cursor:
                    eck = ex.get("canonical_key")
                    if eck:
                        existing_map[eck] = ex
            except Exception as preserve_exc:
                # Best-effort: if the read fails, log and proceed with
                # unmodified replace (preserves existing failure mode,
                # never blocks a write).
                logger.warning(
                    f"[SCORES_STORE:{sport}] preserve-pass read failed: "
                    f"{preserve_exc}. Falling back to non-preserving replace."
                )
                existing_map = {}
            preserved_fields = 0
            for d in deduped:
                ex = existing_map.get(d.get("canonical_key"))
                if not ex:
                    continue
                for f in _PRESERVE_ON_REPLACE:
                    if d.get(f) is None and ex.get(f) is not None:
                        d[f] = ex[f]
                        preserved_fields += 1
            if preserved_fields:
                logger.info(
                    f"[SCORES_STORE:{sport}] mode=replace preserve_pass "
                    f"version='{version_tag}' carried_forward={preserved_fields} "
                    f"fields across {len(deduped)} docs"
                )

        ops = [
            ReplaceOne(
                {"canonical_key": d["canonical_key"], "version_tag": version_tag},
                d,
                upsert=True,
            )
            for d in deduped
            if d.get("canonical_key")
        ]
        if ops:
            # 2026-05-13 OOM defence: bulk_write a 16k+ op batch with
            # multi-book layer docs (each ~8 KB after the "all books"
            # expansion) holds 4-5x the doc set in memory simultaneously
            # (`ops` + driver buffer + MongoDB wire frame). Chunk to 500
            # ops at a time so peak memory stays bounded.
            _CHUNK = 500
            try:
                for _i in range(0, len(ops), _CHUNK):
                    _batch = ops[_i:_i + _CHUNK]
                    res = await coll.bulk_write(_batch, ordered=False)
                    inserted_or_replaced += (
                        (res.upserted_count or 0)
                        + (res.modified_count or 0)
                    )
            except Exception as e:
                # Race-safe fallback: log and attempt best-effort count
                # from the BulkWriteError.details attribute.
                from services.observability import log_caught_exception
                await log_caught_exception(
                    db, e,
                    subsystem="services.scoring.prop_scores_store.write_versioned_scores",
                    sport=sport,
                    context={
                        "version_tag": version_tag,
                        "op_count": len(ops),
                        "mode": "replace",
                    },
                )
                details = getattr(e, "details", None) or {}
                inserted_or_replaced += (
                    (details.get("nUpserted") or 0)
                    + (details.get("nModified") or 0)
                )
        # Sweep stale: anything tagged `version_tag` whose canonical_key
        # isn't in the new set is a leftover from a previous rebuild.
        #
        # 2026-05-13 — SHRINKAGE GUARD added.
        # Symptom: master_sync ran with a transient slate (only 136 docs
        # scored vs 5,400 typical) and wiped 5,459 valid props from the
        # `final-mlb-rt` tag. Cause: scoring pipeline can return a
        # partial batch when upstream odds / model features are
        # intermittently unavailable, but the stale-sweep treats every
        # canonical_key not in the partial batch as "deleted."
        #
        # Guard: if the new batch is < 50% of the existing tag size,
        # skip the destructive sweep and surface a warning. Real
        # collection shrinkage (off-season, slate completion) will
        # drain naturally via `set_inactive_for_started_games` +
        # age-based cleanup paths. The 0-write empty-batch guard above
        # handles the total-blackout case (`prepared==0`); this guard
        # handles the partial-blackout case where SOME props score.
        new_cks = list(seen.keys())
        if new_cks:
            existing_count_for_tag = await coll.count_documents({"version_tag": version_tag})
            new_size = len(new_cks)
            shrinkage_ratio = (
                (new_size / existing_count_for_tag)
                if existing_count_for_tag else 1.0
            )
            if existing_count_for_tag >= 500 and shrinkage_ratio < 0.5:
                logger.warning(
                    f"[SCORES_STORE:{sport}] mode=replace SHRINKAGE GUARD "
                    f"version='{version_tag}' new_batch={new_size} "
                    f"existing={existing_count_for_tag} "
                    f"ratio={shrinkage_ratio:.2%} → SKIPPING stale_sweep "
                    f"(partial-blackout protection, 2026-05-13)."
                )
                stale_deleted = 0
            else:
                sweep = await coll.delete_many({
                    "version_tag": version_tag,
                    "canonical_key": {"$nin": new_cks},
                })
                stale_deleted = sweep.deleted_count or 0

    # ── Cross-tag active=True sweep (2026-05-14) ───────────────────
    # ACTIVE-POOL SSOT INVARIANT: only one version_tag per sport may
    # carry `active=True` for a given canonical_key. The canonical
    # live tag is `final-{sport}-rt` (see `_LIVE_VERSION_TAGS`).
    #
    # Bug repro: 678/1350 (50.2%) of MLB canonical_keys had multiple
    # active=True rows across stale tags (`final-mlb`, transient
    # `recompute-…`, `final-mlb-rt-shadow`). Every Andy Pages /
    # Heliot Ramos reject seen in the FL audit had 3-4 active rows.
    #
    # Fix: after a successful write to the SSOT live tag, flip
    # active=False on every other-tag row for the same canonical_key.
    # Scoped narrowly (only when writing the live tag) so audit /
    # backtest / shadow runs don't disturb the live pool, and only
    # when written>0 (zero-write guard already short-circuits above).
    cross_tag_deactivated = 0
    live_tag_for_sport = f"final-{sport}-rt"
    # 2026-05-14 — DO NOT gate on `inserted_or_replaced`. A re-run that
    # writes IDENTICAL docs produces modified_count=0 (Mongo's `replace
    # with same content` no-op semantics), even though every live-tag
    # row was re-validated. Stale shadow / legacy tags MUST still be
    # flipped even when nothing physically changed. Gate purely on:
    #   1. there are canonical_keys to scope against, AND
    #   2. we're writing the canonical live tag.
    if new_cks and version_tag == live_tag_for_sport:
        try:
            res = await coll.update_many(
                {
                    "canonical_key": {"$in": new_cks},
                    "version_tag": {"$ne": live_tag_for_sport},
                    "active": True,
                },
                {"$set": {
                    "active": False,
                    "inactive_reason": "stale_tag_active_sweep",
                    "active_changed_at": datetime.now(timezone.utc),
                }},
            )
            cross_tag_deactivated = res.modified_count or 0
            if cross_tag_deactivated:
                logger.info(
                    f"[SCORES_STORE:{sport}] mode=replace "
                    f"version='{version_tag}' cross_tag_deactivated="
                    f"{cross_tag_deactivated} (stale-tag SSOT sweep)"
                )
        except Exception as _xtag_exc:
            logger.warning(
                f"[SCORES_STORE:{sport}] cross-tag active sweep failed "
                f"({_xtag_exc}); continuing — sweep is best-effort."
            )

    logger.info(
        f"[SCORES_STORE:{sport}] mode=replace version='{version_tag}' "
        f"written={inserted_or_replaced} stale_swept={stale_deleted} → {coll_name}"
    )
    return {
        "sport": sport,
        "collection": coll_name,
        "version_tag": version_tag,
        "computed_at": computed_at,
        "prepared": len(prepared),
        "written": inserted_or_replaced,
        "replaced": stale_deleted,  # legacy key name, preserved for callers
        "mode": "replace",
        "dry_run": False,
        "pydantic_failures": len(pydantic_failures),
    }


# -----------------------------------------------------------------------------
# Backward-compatible helpers (used by mlb_adapter.enrich_and_score)
# -----------------------------------------------------------------------------

STRIPPED_FROM_PROPS = tuple(
    f for f in SCORE_FIELDS
    if f not in _IDENTITY_FIELDS
)


def strip_score_fields(props: List[Dict[str, Any]]) -> None:
    """Mutate props in-place: remove scoring-stack fields post-persist."""
    for p in props:
        for f in STRIPPED_FROM_PROPS:
            if f in p:
                del p[f]


async def write_prop_scores(db, scored_props: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Legacy single-version writer for MLB `enrich_and_score`.
    Thin wrapper around `write_versioned_scores`.

    Step 6 cleanup: writes to the canonical `MLB_BASELINE` tag so the
    active board isn't populated with a separate `live` tag that evades
    the Step 6 observation window. See /app/memory/ROADMAP.md §1b.
    """
    from config.version_tags import MLB_BASELINE
    result = await write_versioned_scores(
        db=db, sport="mlb", score_docs=scored_props,
        version_tag=MLB_BASELINE, dry_run=False,
    )
    return {
        "inserted": result["written"],
        "purged": result.get("replaced", 0),
        "synthetic_keys": 0,
    }
