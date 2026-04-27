"""
Sport-Agnostic Scoring Recompute Orchestrator
==============================================
Rebuilds `{sport}_prop_scores` from existing live prop collections
WITHOUT triggering any sportsbook sync or mutating cached boards.

Entry points:
  recompute(db, sports, version_tag, dry_run=False, limit=None,
            override_config=None) → dict

Constraints enforced:
 - Read-only access to live props (find only; no updates/inserts)
 - Read-only access to cached_board (used only for leak-check audit)
 - Scoring-stack fields live ONLY in {sport}_prop_scores
 - Sport-specific ScoringAdapter provides the ScoringContext
"""
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import logging
logger = logging.getLogger(__name__)

from services.scoring.scoring_stack import compute_scoring_stack
from services.scoring.adapters import (
    SCORING_ADAPTERS, SUPPORTED_SPORTS, get_scoring_adapter,
)
from services.scoring.prop_scores_store import write_versioned_scores, _SCORE_OUTPUT_FIELDS


def _default_version_tag() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"recompute-{ts}-{uuid.uuid4().hex[:6]}"


def _build_pra_audit_snapshots(score_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build PRA dual-projection audit rows from a batch of scored docs.

    Filters to NBA PRA-family rows where at least one projection
    (direct or synth) is non-null, and returns lean rows suitable for
    upsert into `nba_pra_projection_audit`. The audit collection
    stores a snapshot of (direct, synth, deltas) keyed by
    (event_id, player_name, line, recommendation) so actuals can be
    joined in later once games complete. No-op for non-PRA families
    and for rows where BOTH projections are missing (neither).
    """
    rows: List[Dict[str, Any]] = []
    for d in score_docs:
        gate_eval = d.get("gate_eval") or {}
        fam = (gate_eval.get("stat_family") or "").lower()
        # Resolve family from stat_type as a fallback for rows where
        # gate_eval wasn't fully populated (e.g., pre-gate-screen).
        if fam != "pra" and (d.get("stat_type") or "").upper() != "PRA":
            continue
        if (d.get("projection_compare_status") or "neither") == "neither":
            continue
        if not d.get("event_id") or not d.get("player_name") or d.get("line") is None:
            continue
        rows.append({
            "event_id": d["event_id"],
            "player_name": d["player_name"],
            "stat_type": d.get("stat_type"),
            "stat_family": fam or "pra",
            "line": float(d["line"]),
            "recommendation": d.get("recommendation"),
            "game_start_utc": d.get("game_start_utc"),
            "model_projection_direct": d.get("model_projection_direct"),
            "model_sigma_direct": d.get("model_sigma_direct"),
            "model_projection_synth": d.get("model_projection_synth"),
            "model_sigma_synth": d.get("model_sigma_synth"),
            "projection_delta_abs": d.get("projection_delta_abs"),
            "projection_delta_pct": d.get("projection_delta_pct"),
            "projection_compare_status": d.get("projection_compare_status"),
            "projection_primary_method": d.get("projection_primary_method"),
            "tier": d.get("tier"),
            "tier_reference_book": d.get("tier_reference_book"),
            "tier_reference_odds": d.get("tier_reference_odds"),
            "hit_rate_over": d.get("hit_rate_over"),
            "hit_rate_under": d.get("hit_rate_under"),
            "cv": d.get("cv"),
            "snapshot_at": datetime.now(timezone.utc),
            # Settlement fields — filled by the audit endpoint when
            # bdl_game_logs show the game concluded.
            "actual_pts": None,
            "actual_reb": None,
            "actual_ast": None,
            "actual_pra": None,
            "settled": False,
            "settled_at": None,
        })
    return rows




# ----------------------------------------------------------------------------
# Stat-aware α for ranking_score_v2 (2026-04-21)
# ----------------------------------------------------------------------------
# The single-α=0.40 constant compressed low-line props (AST/REB @ lines 1.5-5)
# out of Top-10 slates even when their raw edge + p_model were strong, because
# `line^0.40` barely shrinks for small lines.  A stat-aware α normalizes each
# stat regime to its own scale:
#   * PTS / PRA / combo : raw-gap is the real signal → α stays low (0.40-0.50)
#   * AST / REB / 3PM   : mid-line regime, needs modest normalization (0.60)
#   * STL / BLK         : tiny-line regime, needs strong normalization (0.70)
#   * MLB / NFL / future: fall back to neutral 0.50 until tuned per-stat
#
# The map is keyed by normalized NBA stat codes (the same codes that show up
# in {sport}_prop_scores.stat_type).  Unknown keys → _DEFAULT_ALPHA.  This
# keeps the function multi-sport: MLB stats ("Hits", "Pitcher Strikeouts") and
# future NFL stats naturally land on the default until explicitly tuned.
_DEFAULT_ALPHA: float = 0.50
ALPHA_BY_STAT: Dict[str, float] = {
    # --- NBA scoring regimes ---
    "PTS": 0.40,
    "PRA": 0.40,
    "PTS+REB": 0.50,
    "PTS+AST": 0.50,
    "REB+AST": 0.50,
    "AST": 0.60,
    "REB": 0.60,
    "3PM": 0.60,
    "STL": 0.70,
    "BLK": 0.70,
    # MLB / NFL entries can be added here without touching callers.
}


def _resolve_alpha(stat_type: Optional[str]) -> float:
    """Return stat-aware α, falling back to _DEFAULT_ALPHA on miss."""
    if not stat_type:
        return _DEFAULT_ALPHA
    return ALPHA_BY_STAT.get(stat_type, _DEFAULT_ALPHA)


def _compute_ranking_score_v2(
    projection: Optional[float],
    line: Optional[float],
    recommendation: Optional[str],
    p_model: Optional[float] = None,
    stat_type: Optional[str] = None,
) -> Optional[float]:
    """Projection-gap ranking with stat-aware α (2026-04-21).

    Blended formula:
        raw_gap = projection - line   (OVER)
                = line - projection   (UNDER)
        α       = ALPHA_BY_STAT.get(stat_type, 0.50)
        ranking_score_v2 = (raw_gap / max(line, 1.0) ** α) * p_model

    Previously α was a single 0.40 constant, which suppressed low-line
    stats (AST 1.5, REB 2.5, STL/BLK) because `line^0.40` barely shrinks
    for small lines and their absolute gaps are tiny.  Stat-aware α lets
    each regime be normalized on its own curve without reshuffling the
    tier thresholds or the model weighting.  Returns None when
    projection/line/p_model missing or recommendation is neither OVER
    nor UNDER.
    """
    if projection is None or line is None or p_model is None:
        return None
    rec = (recommendation or "").strip().upper()
    if rec not in ("OVER", "UNDER"):
        return None
    try:
        proj_f = float(projection)
        line_f = float(line)
        p_f = float(p_model)
    except (TypeError, ValueError):
        return None
    raw_gap = (proj_f - line_f) if rec == "OVER" else (line_f - proj_f)
    alpha = _resolve_alpha(stat_type)
    denom = max(line_f, 1.0) ** alpha
    return round((raw_gap / denom) * p_f, 6)


def _apply_vision_score_normalization(score_docs: List[Dict[str, Any]]) -> None:
    """Populate `vision_score` (0-100) via percentile rank of vision_score_raw.
    Props with quality_source='insufficient_market' keep vision_score=None."""
    raw = sorted([
        d["vision_score_raw"] for d in score_docs
        if d.get("vision_score_raw") is not None and d["vision_score_raw"] > 0
    ])
    if not raw:
        for d in score_docs:
            d["vision_score"] = (
                None if d.get("quality_source") == "insufficient_market" else 0.0
            )
        return

    for d in score_docs:
        if d.get("quality_source") == "insufficient_market":
            d["vision_score"] = None
            continue
        vr = d.get("vision_score_raw")
        if vr is None or vr <= 0:
            d["vision_score"] = 0.0
        else:
            rank = sum(1 for s in raw if s <= vr)
            d["vision_score"] = round((rank / len(raw)) * 100.0, 1)


def _reevaluate_tiers_post_vision(score_docs: List[Dict[str, Any]]) -> None:
    """Re-run the UniversalGateEngine on every doc in a tier that uses
    a slate-percentile field (`vision_score`). These gates
    (`vision_score_gate`, `market_trap_gate`) defer on the first pass
    because `vision_score` is populated only after
    `_apply_vision_score_normalization`. This second pass is
    authoritative: passing docs retain their tier, failing docs are
    demoted to `unqualified` with the engine's canonical reason_code.
    Mutates `tier`, `tier_reason`, `tier_gate_results`, and
    `gate_eval` in place. Sport-agnostic — runs wherever a tier's
    config references a deferred gate.

    PR-2 (2026-04-25): metrics-builder + tier-evaluator extracted to
    `services.scoring.metrics_builder` /
    `services.scoring.tier_evaluator`. The previous inline NormalizedMetrics
    construction (incl. Fix A's `p_true_hit_rate` fallback and Fix B's
    inline MLB SH goblin override) is now produced by those shared
    helpers, eliminating the parallel implementations the first pass
    and re-eval each carried.
    """
    from services.scoring.gates import ReasonCode
    from services.scoring.metrics_builder import build_metrics_from_score_doc
    from services.scoring.tier_evaluator import evaluate_tier_with_overrides

    TIERS_TO_REEVAL = ("safe_haven", "war_zone")
    for doc in score_docs:
        current_tier = doc.get("tier")
        if current_tier not in TIERS_TO_REEVAL:
            continue

        metrics = build_metrics_from_score_doc(doc, override_tier=current_tier)
        result = evaluate_tier_with_overrides(metrics)

        legacy_gate_results = {
            name: {
                "threshold": d.threshold,
                "value": d.actual,
                "passed": bool(d.passed),
                **({"note": d.note} if d.note else {}),
                "reason_code": d.reason_code,
            }
            for name, d in result.gate_details.items()
        }
        doc["tier_gate_results"] = legacy_gate_results
        doc["gate_eval"] = result.to_dict()
        if result.passed:
            doc["tier_reason"] = ReasonCode.GATES_PASSED
        else:
            doc["tier"] = "unqualified"
            doc["tier_reason"] = f"{current_tier}_failed: {result.reason_code}"


async def recompute_sport(
    db,
    sport: str,
    version_tag: str,
    dry_run: bool = False,
    limit: Optional[int] = None,
    override_config: Optional[Dict[str, Any]] = None,
    write_mode: str = "replace",
    props: Optional[List[Dict[str, Any]]] = None,
    only_canonical_keys: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Recompute scoring stack for a single sport.

    `write_mode`:
      - "replace" (default, used by the hourly full recompute): wipes
        every doc with `version_tag` then bulk-inserts the new docs.
      - "upsert" (Step 5 real-time scoped ingest): per-doc upsert keyed
        on `(canonical_key, version_tag)`. Never destroys existing rows
        outside the supplied key set.

    `props`: optional pre-loaded list of raw live props. When supplied,
    `adapter.load_live_props` is bypassed entirely — the caller is
    responsible for filtering. Used by `services/board/engine.py` for
    scoped real-time ingest.

    `only_canonical_keys` (Phase D2, 2026-04-21, Delta Engine):
        Optional filter restricting the scoring pass to a specific set of
        canonical keys. When provided:
          - Only props whose `canonical_key` is in the set are scored and
            written. Everything else is silently skipped.
          - `write_mode` is FORCED to "upsert" — a "replace" full rescore
            with a filtered subset would delete every untouched RT doc,
            which would break the plan's "additive, not replacement"
            invariant (§2). If the caller passes a subset filter with
            mode="replace" we override to "upsert" and log a warning.
          - Backwards compatible: when None (default), behaviour is
            IDENTICAL to pre-D2 — full-sync callers need no changes.
    """
    t0 = time.monotonic()
    adapter = get_scoring_adapter(sport)
    config = {
        "version_tag": version_tag,
        "limit": limit,
        "override_config": override_config or {},
    }

    # Snapshot cached_board BEFORE recompute (for leak-check audit).
    cached_coll = db[adapter.cached_board_collection]
    cached_before_count = await cached_coll.count_documents({})
    cached_before_sample = await cached_coll.find_one({}, {"_id": 0})

    # 1. Load live props (read-only) — unless caller supplied them
    if props is None:
        props = await adapter.load_live_props(db, limit=limit)

    # Phase D2 (2026-04-21) — Delta Engine scoped-rescore filter.
    # When a canonical-key subset is supplied, restrict the scoring pass
    # to that subset and force `upsert` mode so untouched RT docs are
    # preserved (plan §2: "additive, not replacement").
    pre_filter_count = len(props)
    filter_applied = False
    if only_canonical_keys is not None:
        only_set = set(only_canonical_keys)
        # Sport-agnostic key resolution — MLB persists canonical_key on
        # the raw prop, NBA computes it from raw fields. Both paths go
        # through the adapter.
        props = [
            p for p in props
            if adapter.canonical_key_from_raw(p) in only_set
        ]
        filter_applied = True
        if write_mode != "upsert":
            logger.warning(
                f"[RECOMPUTE:{sport}] only_canonical_keys supplied with "
                f"write_mode={write_mode!r}; forcing write_mode='upsert' to "
                f"preserve untouched RT docs (Delta Engine invariant)."
            )
            write_mode = "upsert"
        logger.info(
            f"[RECOMPUTE:{sport}] only_canonical_keys filter: "
            f"{len(only_set)} requested / {pre_filter_count} live "
            f"/ {len(props)} matched"
        )

    # 2. Build scoring contexts + compute stack — no sorter plumbing
    # needed; Universal Gate Engine reads `sport` directly.
    score_docs: List[Dict[str, Any]] = []
    samples: List[Dict[str, Any]] = []
    skipped = 0

    # Canonical multi-sport DvP rank (2026-04-21). Warm the sport's provider
    # ONCE before the per-prop loop so `get_opponent_defensive_rank` has a
    # hot BDL cache to read from. No-op for sports whose provider does not
    # need external data.
    try:
        from services.defensive_rank_resolver import (
            ensure_provider_warm as _ensure_def_warm,
            get_opponent_defensive_rank as _get_def_rank,
        )
        await _ensure_def_warm(sport)
    except Exception as _warm_err:
        logger.warning(f"[RECOMPUTE:{sport}] def-rank prewarm failed: {_warm_err}")
        _get_def_rank = lambda *_: (None, "unavailable")  # noqa: E731

    for prop in props:
        ctx = await adapter.build_context(db, prop, config)
        if ctx is None:
            skipped += 1
            continue

        # Run the three independent scoring functions through the composed entry.
        stack = compute_scoring_stack(
            prop={
                "pp_layer": ctx.pp_layer, "dk_layer": ctx.dk_layer,
                "fd_layer": ctx.fd_layer,
                "mgm_layer": ctx.mgm_layer, "sharp_layer": ctx.sharp_layer,
                # multiplier hints for pp_utility
                "pp_combo_multiplier": ctx.pp_combo_multiplier,
                "pp_label": ctx.pp_label,
                "pp_multiplier_model": ctx.pp_multiplier_model,
                # raw fields the engine reads from NormalizedMetrics.extras
                **ctx.raw_prop,
                # Authoritative direction for side-aware gate paths — placed
                # AFTER the raw_prop splat so nothing can shadow it.
                "recommendation": ctx.recommendation,
            },
            p_model=ctx.p_model,
            cv=ctx.cv,
            hit_rate=ctx.hit_rate,
            edge_pct=ctx.edge_pct,
            tp=ctx.tp,
            ceiling_rate=ctx.ceiling_rate,
            books_available_count=ctx.books_available_count,
            sport=sport,
            avg_hit_margin=ctx.avg_hit_margin,
            avg_miss_margin=ctx.avg_miss_margin,
        )
        # -------- Universal pool fields (multi-sport lifecycle) --------
        # Every {sport}_prop_scores document carries the same universal
        # lifecycle fields so the board engine and scanner can operate on
        # any sport without sport-specific branches. `game_start_utc` is
        # pulled from the raw prop's commence_time so the 60s scanner can
        # flip tipped-off props to active=False via an indexed update.
        raw = ctx.raw_prop or {}
        commence = raw.get("commence_time") or raw.get("event_start_utc") or raw.get("game_time")
        if isinstance(commence, str):
            try:
                game_start_utc = datetime.fromisoformat(commence.replace("Z", "+00:00"))
            except Exception:
                game_start_utc = None
        elif isinstance(commence, datetime):
            game_start_utc = commence if commence.tzinfo else commence.replace(tzinfo=timezone.utc)
        else:
            game_start_utc = None

        doc = {
            "canonical_key": ctx.canonical_key,
            "sport": ctx.sport,
            "event_id": ctx.event_id,
            "player_name": ctx.player_name,
            "stat_type": ctx.stat_type,
            "line": ctx.line,
            "recommendation": ctx.recommendation,
            # ---- Universal pool lifecycle fields ----
            "active": True,
            "inactive_reason": None,
            "active_changed_at": None,
            "game_start_utc": game_start_utc,
            # p_true diagnostic panel (all paths always computed)
            "p_true_active": ctx.p_model,
            "p_true_method": ctx.p_true_method,
            "p_true_hit_rate": ctx.p_true_hit_rate,
            "p_true_model": ctx.p_true_model,
            "model_projection": ctx.model_projection,
            "model_sigma": ctx.model_sigma,
            "p_true_vk2": ctx.p_true_vk2,
            "vk2_projection": ctx.vk2_projection,
            "vk2_sigma": ctx.vk2_sigma,
            "vk2_error": ctx.vk2_error,
            "hit_rate_over": ctx.hit_rate_over,
            "hit_rate_under": ctx.hit_rate_under,
            # HR sample-size telemetry (2026-04-25, HR v3). Sample
            # size in [10, 20] when HR is computed; None when HR is
            # None (insufficient sample / missing identity / etc).
            # Gate engine reads this for small-sample penalty.
            "hit_rate_sample_size": ctx.hit_rate_sample_size,
            # Ceiling rate (PR-2, 2026-04-25). Persisted so the
            # post-vision re-eval has the exact value the first pass
            # used as input to `ceiling_gate` (MLB war_zone). Without
            # this the re-eval reads None and `_eval_ceiling` silently
            # passes via `ceiling_missing_skipped`, masking a real
            # config-mismatch class.
            "ceiling_rate": ctx.ceiling_rate,
            # Global Identity Rule (2026-04-23) — persist identity
            # decision on every score doc so observability /
            # diagnostics can partition by identity_status.
            "bdl_player_id": ctx.bdl_player_id,
            "identity_status": ctx.identity_status,
            # 2026-05 missing-value policy — persist the feature_health
            # block so observability / downstream gating can see which
            # features the underlying ML model received as silent
            # defaults vs real values. Live behaviour unchanged.
            "feature_health": ctx.feature_health,
            # 2026-05 NBA team injury context. Carried for
            # observability; not fed into the trained VK model.
            "injury_context": ctx.injury_context,
            # ranking_score_v2 (2026-02-20 shadow G1):
            #   For OVER:  gap = model_projection - line
            #   For UNDER: gap = line - model_projection
            #   ranking_score_v2 = gap / max(line, 1.0)
            # Persisted on every scored prop so endpoints can opt-in to
            # projection-gap sort via `?sort=gap`. Default sort unchanged.
            "ranking_score_v2": _compute_ranking_score_v2(
                ctx.model_projection, ctx.line, ctx.recommendation, ctx.p_model,
                stat_type=ctx.stat_type,
            ),
            **stack,
        }

        # Stage 4 (2026-04-21, MLB↔NBA carbon-copy): sport-specific
        # enrichment hook — moves previously route-time MLB enrichers
        # (enrich_mlb_prop_with_tempo, enrich_mlb_intel_suite) into the
        # scoring-write path. NBA adapter is a no-op. Eliminates D11.
        try:
            extra = adapter.enrich_score_doc(ctx.raw_prop or {}, ctx) or {}
            for k, v in extra.items():
                if k in _SCORE_OUTPUT_FIELDS:
                    doc[k] = v
        except Exception as _enrich_err:
            logger.warning(
                f"[RECOMPUTE:{sport}] enrich_score_doc failed for "
                f"{ctx.canonical_key}: {_enrich_err}"
            )

        # Canonical multi-sport DvP rank — written at scoring time so every
        # downstream layer (board reader, Gemini payload, UI) reads the
        # same value from the {sport}_prop_scores system of record.  Never
        # falls back to the static DVP_RANKINGS table.  See
        # services/defensive_rank_resolver.py for provider routing.
        _opp = (
            (ctx.raw_prop or {}).get("opponent")
            or (ctx.raw_prop or {}).get("opponent_abbr")
            or (ctx.raw_prop or {}).get("away_team")
            or (ctx.raw_prop or {}).get("home_team")
        )
        _rank, _source = _get_def_rank(sport, _opp, ctx.stat_type)
        doc["opponent_defensive_rank"] = _rank
        doc["opponent_defensive_source"] = _source
        doc["opponent_defensive_stat_type"] = ctx.stat_type

        # 0-Book Exclusion Rule (2026-04-22). The adapter already marks
        # every prop with `book_count`/`coverage_class`/`books_anchored`
        # during `load_live_props`. Propagate those into the score doc
        # so the API + UI can surface the coverage signal. Pp_only
        # props never reach recompute (they're filtered pre-scoring),
        # so any doc persisted here is guaranteed book_count >= 1.
        raw = ctx.raw_prop or {}
        if "book_count" in raw:
            doc["book_count"] = raw["book_count"]
        if "coverage_class" in raw:
            doc["coverage_class"] = raw["coverage_class"]
        if "books_anchored" in raw:
            doc["books_anchored"] = raw["books_anchored"]
        # Empirical-Bayes post-shrinkage audit fields (2026-04-24).
        # Stamped by mlb_scoring.build_context onto raw_prop regardless of
        # whether shrinkage applied; surface them on the score doc so
        # observability can partition (raw vs shrunk) without rescoring.
        for _eb_k in (
            "raw_hf_projection", "eb_shrunk_projection",
            "eb_player_career_mean", "eb_weight_model", "eb_weight_player",
            "eb_shrinkage_applied", "eb_skip_reason", "eb_career_sample_n",
        ):
            if _eb_k in raw:
                doc[_eb_k] = raw[_eb_k]
        # Universal ECDF probability-layer audit (2026-04-24). Mirror from
        # raw_prop onto the score doc so observability can count method
        # distribution without re-running the scoring pass. MLB sets
        # these directly on raw_prop; NBA currently writes them via
        # calibration_meta → extras, so they will arrive through the
        # stack splat above — duplicate-safe write here is fine.
        for _prob_k in (
            "probability_method", "ecdf_p_over", "ecdf_bucket",
            "ecdf_bucket_n", "ecdf_version", "raw_gaussian_p_over",
            "isotonic_p_over", "probability_calibration_applied",
            "raw_p_over", "projection_intercept_applied",
            "projection_intercept_delta", "pre_intercept_projection",
        ):
            if _prob_k in raw:
                doc[_prob_k] = raw[_prob_k]
        # War Zone CV modifier (2026-04-22) — stamped on the raw prop by
        # `check_war_zone_gates`; mirror onto the score doc so ranking
        # layers / UI can read it without re-running the sorter.
        if "war_zone_cv_modifier" in raw:
            doc["war_zone_cv_modifier"] = raw["war_zone_cv_modifier"]

        # Multi-book de-vig TP engine (2026-04-22). `tp`/`edge_pct` come
        # from `ctx` (authoritative); the meta fields are stamped on the
        # raw prop by the adapter. Persisting these on the score doc is
        # what makes TP/edge visible to the API + UI (previously
        # computed-but-not-saved).
        doc["tp"] = ctx.tp
        doc["edge_pct"] = ctx.edge_pct
        # Universal CV (2026-04-23): line-independent, derived from the
        # player's stat-family distribution. See
        # NBAScoringAdapter._compute_cv_and_hit_rate.
        doc["cv"] = ctx.cv
        doc["cv_status"] = ctx.cv_status
        # 0.5-line margin metrics (2026-05). Engine swaps cv_gate →
        # margin_gate when sport==mlb && line==0.5.
        doc["avg_hit_margin"] = ctx.avg_hit_margin
        doc["avg_miss_margin"] = ctx.avg_miss_margin
        # Universal HR status (2026-04-23) — mirror of cv_status.
        doc["hit_rate_status"] = ctx.hit_rate_status
        # Combo projection synthesis (2026-04-23): label where
        # model_projection / model_sigma came from ("model",
        # "combo_synth", or None).
        doc["projection_method"] = ctx.projection_method
        # PRA dual-projection audit (2026-04-23): both projections
        # side-by-side so we can evaluate direct vs synth against
        # actual PRA totals once games complete.
        doc["model_projection_direct"] = ctx.model_projection_direct
        doc["model_sigma_direct"] = ctx.model_sigma_direct
        doc["model_projection_synth"] = ctx.model_projection_synth
        doc["model_sigma_synth"] = ctx.model_sigma_synth
        doc["projection_delta_abs"] = ctx.projection_delta_abs
        doc["projection_delta_pct"] = ctx.projection_delta_pct
        doc["projection_compare_status"] = ctx.projection_compare_status
        doc["projection_primary_method"] = ctx.projection_primary_method
        # Expected-minutes composition audit (2026-04-23).
        doc["minutes_composition_applied"] = ctx.minutes_composition_applied
        doc["minutes_composition_baseline_projection"] = (
            ctx.minutes_composition_baseline_projection
        )
        doc["minutes_composition_predicted_minutes"] = (
            ctx.minutes_composition_predicted_minutes
        )
        doc["minutes_composition_per_min_rate"] = (
            ctx.minutes_composition_per_min_rate
        )
        if "tp_books_used" in raw:
            doc["tp_books_used"] = raw["tp_books_used"]
        if "tp_books_list" in raw:
            doc["tp_books_list"] = raw["tp_books_list"]
        if "tp_method" in raw:
            doc["tp_method"] = raw["tp_method"]
        if "tp_unavailable" in raw:
            doc["tp_unavailable"] = raw["tp_unavailable"]
        if "tp_unavailable_reason" in raw:
            doc["tp_unavailable_reason"] = raw["tp_unavailable_reason"]
        if "tp_source" in raw:
            doc["tp_source"] = raw["tp_source"]
        if "market_probability" in raw:
            doc["market_probability"] = raw["market_probability"]

        # Universal SSOT canonical-pool flags (2026-04-25). Stamped on
        # every raw prop by universal_odds_sync. Mirror onto the score
        # doc so read-side endpoints can filter by `playable_on_pp`
        # without re-joining live_props.
        for _ssot_k in (
            "pp_available", "playable_on_pp",
            "source_anchor", "anchor_book",
        ):
            if _ssot_k in raw:
                doc[_ssot_k] = raw[_ssot_k]

        score_docs.append(doc)

    # 3. Percentile-normalize vision_score across the sport's slate.
    _apply_vision_score_normalization(score_docs)

    # 3a. Post-vision re-evaluation (2026-04-24). Tiers whose configs
    # reference slate-percentile gates (`vision_score_gate`,
    # `market_trap_gate`) defer those gates on the first pass. Re-run
    # the UniversalGateEngine so the authoritative decision is
    # recorded. All gating config lives in
    # `services/scoring/gates/thresholds.py` — no sport-specific
    # logic here.
    _reevaluate_tiers_post_vision(score_docs)

    # 3b. Multi-book de-vig TP engine summary (2026-04-22).
    # Per user spec: log `[TP Engine] props_with_tp / props_missing_tp /
    # avg_books_used` once per recompute run.
    if score_docs:
        _props_with_tp = sum(1 for d in score_docs if d.get("tp") is not None)
        _props_missing_tp = len(score_docs) - _props_with_tp
        _books_vals = [d.get("tp_books_used") or 0 for d in score_docs if d.get("tp") is not None]
        _avg_books = (sum(_books_vals) / len(_books_vals)) if _books_vals else 0.0
        logger.info(
            f"[TP_ENGINE] [{sport.upper()}] props_with_tp={_props_with_tp} "
            f"props_missing_tp={_props_missing_tp} "
            f"avg_books_used={_avg_books:.2f} method=multi_book_devig_v1"
        )

    # 4. Persist (unless dry_run)
    write_result = await write_versioned_scores(
        db=db, sport=sport, score_docs=score_docs,
        version_tag=version_tag, dry_run=dry_run,
        mode=write_mode,
    )

    # 4b. PRA dual-projection audit snapshot (NBA only, 2026-04-23).
    # Persist every PRA row where either direct or synth produced a
    # projection into a standalone audit collection, idempotently
    # keyed by (event_id, player_name, line). Survives `final-nba-rt`
    # overwrites so we can backtest against actuals once games
    # conclude. Live scoring behaviour is unaffected by this block.
    if not dry_run and sport == "nba":
        try:
            audit_rows = _build_pra_audit_snapshots(score_docs)
            if audit_rows:
                audit_coll = db["nba_pra_projection_audit"]
                for row in audit_rows:
                    await audit_coll.update_one(
                        {
                            "event_id": row["event_id"],
                            "player_name": row["player_name"],
                            "line": row["line"],
                            "recommendation": row["recommendation"],
                        },
                        {"$set": row},
                        upsert=True,
                    )
                logger.info(
                    f"[PRA_AUDIT] upserted {len(audit_rows)} PRA dual-"
                    f"projection rows into nba_pra_projection_audit"
                )
        except Exception as exc:
            # Audit is non-critical; never fail the recompute because
            # of it. Log and continue.
            logger.warning(f"[PRA_AUDIT] snapshot write failed: {exc!r}")

    # 5. Leak-check: cached_board must NOT be mutated AND must not contain
    #    recompute-produced scoring data (check for non-null scoring values).
    cached_after_count = await cached_coll.count_documents({})
    cached_after_sample = await cached_coll.find_one({}, {"_id": 0})
    leakage_fields = []
    if cached_after_sample:
        candidate = cached_after_sample
        if isinstance(candidate.get("props"), list) and candidate["props"]:
            candidate = candidate["props"][0]
        scoring_field_names = (
            "vision_score", "vision_score_raw", "tier_reason", "tier_gate_results",
            "pp_utility", "pp_utility_components", "quality_source",
        )
        # Only flag as leakage if the field is present AND carries a non-null
        # value — empty pre-existing keys set to None by legacy pipelines
        # are NOT recompute leakage.
        leakage_fields = [
            f for f in scoring_field_names
            if f in candidate and candidate[f] is not None and candidate[f] != {}
        ]

    # Sample outputs for response
    for d in score_docs[:3]:
        samples.append({k: v for k, v in d.items() if k != "_id"})

    # Tier distribution + top samples for simulation / diagnostic use
    tier_distribution: Dict[str, int] = {}
    quality_distribution: Dict[str, int] = {}
    pp_category_distribution: Dict[str, int] = {}
    tier_canonical_keys: Dict[str, List[str]] = {}
    for d in score_docs:
        t = d.get("tier") or "unknown"
        tier_distribution[t] = tier_distribution.get(t, 0) + 1
        q = d.get("quality_source") or "unknown"
        quality_distribution[q] = quality_distribution.get(q, 0) + 1
        c = d.get("pp_utility_category") or "unknown"
        pp_category_distribution[c] = pp_category_distribution.get(c, 0) + 1
        ck = d.get("canonical_key")
        if ck:
            tier_canonical_keys.setdefault(t, []).append(ck)

    # Top 10 by vision_score (nulls sorted last)
    def _vs_key(d):
        v = d.get("vision_score")
        return (0 if v is None else 1, v if v is not None else -1.0)
    top_samples = sorted(score_docs, key=_vs_key, reverse=True)[:10]
    top_samples = [{k: v for k, v in d.items() if k != "_id"} for d in top_samples]

    duration_ms = int((time.monotonic() - t0) * 1000)
    # Expose the FULL list of score_docs ONLY when we're in the small-
    # batch upsert path — the real-time engine uses this to feed the
    # drift audit ledger without re-reading from Mongo. For "replace"
    # mode (full rebuild, thousands of docs) we keep the payload
    # compact to avoid ballooning the response.
    full_score_docs = (
        [{k: v for k, v in d.items() if k != "_id"} for d in score_docs]
        if write_mode == "upsert" else None
    )
    return {
        "sport": sport,
        "processed": len(props),
        "written": write_result["written"],
        "skipped": skipped,
        "replaced": write_result.get("replaced", 0),
        "collection": write_result["collection"],
        "version_tag": version_tag,
        "dry_run": dry_run,
        "duration_ms": duration_ms,
        # Phase D2 — surface the canonical-key filter diagnostics so
        # the Delta Engine can assert exactly-N behaviour from
        # integration tests / admin endpoints.
        "only_canonical_keys_applied": filter_applied,
        "only_canonical_keys_requested": (
            len(only_canonical_keys) if only_canonical_keys is not None else None
        ),
        "only_canonical_keys_matched": len(props) if filter_applied else None,
        "cached_board_before_count": cached_before_count,
        "cached_board_after_count": cached_after_count,
        "cached_board_mutated": cached_before_count != cached_after_count
            or cached_before_sample != cached_after_sample,
        "cached_board_leakage_fields": leakage_fields,
        "tier_distribution": tier_distribution,
        "quality_source_distribution": quality_distribution,
        "pp_category_distribution": pp_category_distribution,
        "tier_canonical_keys": tier_canonical_keys,
        "samples": samples,
        "top_samples": top_samples,
        "score_docs": full_score_docs,
    }


async def recompute(
    db,
    sports: Optional[List[str]] = None,
    version_tag: Optional[str] = None,
    dry_run: bool = False,
    limit: Optional[int] = None,
    override_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Orchestrate recompute across one or more sports."""
    t0 = time.monotonic()
    if not sports:
        sports = list(SUPPORTED_SPORTS)
    # Validate sport list
    unknown = [s for s in sports if s not in SCORING_ADAPTERS]
    if unknown:
        raise ValueError(f"Unknown sports: {unknown}. Supported: {SUPPORTED_SPORTS}")

    version_tag = version_tag or _default_version_tag()

    per_sport: Dict[str, Dict[str, Any]] = {}
    for sport in sports:
        try:
            per_sport[sport] = await recompute_sport(
                db=db, sport=sport, version_tag=version_tag,
                dry_run=dry_run, limit=limit,
                override_config=override_config,
            )
        except Exception as e:
            logger.exception(f"[RECOMPUTE] {sport} failed: {e}")
            per_sport[sport] = {
                "sport": sport, "error": str(e),
                "processed": 0, "written": 0, "skipped": 0,
                "version_tag": version_tag, "dry_run": dry_run,
            }

    duration_ms = int((time.monotonic() - t0) * 1000)
    return {
        "status": "success",
        "sports_processed": sports,
        "processed": {s: per_sport[s].get("processed", 0) for s in sports},
        "written": {s: per_sport[s].get("written", 0) for s in sports},
        "skipped": {s: per_sport[s].get("skipped", 0) for s in sports},
        "version_tag": version_tag,
        "duration_ms": duration_ms,
        "dry_run": dry_run,
        "per_sport": per_sport,
        "samples": {s: per_sport[s].get("samples", []) for s in sports},
    }
