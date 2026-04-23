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

# Exactly the fields a score doc may contain (plus canonical identity + versioning).
_SCORE_OUTPUT_FIELDS = (
    # vision_score dimension
    "vision_score", "vision_score_raw", "quality_source", "fair_prob",
    "stability", "confidence", "edge_vs_fair",
    # tier dimension
    "tier", "tier_reason", "tier_reference_book", "tier_reference_odds",
    "tier_gate_results",
    # pp_utility dimension
    "pp_utility", "pp_utility_category", "pp_utility_components",
    "pp_multiplier", "pp_multiplier_label", "pp_multiplier_source",
    "pp_reference_source",
    "pp_playable", "pp_playability_reason",
    # p_true diagnostic panel
    "p_true_active", "p_true_method", "p_true_hit_rate", "p_true_model",
    "model_projection", "model_sigma",
    # VK2 (5-year adv-stat) diagnostics — parallel to legacy VK model_*
    "p_true_vk2", "vk2_projection", "vk2_sigma", "vk2_error",
    # Side-aware hit-rate diagnostics (the side passed to gates is in hit_rate)
    "hit_rate_over", "hit_rate_under",
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
    # War Zone CV scoring modifier (2026-04-22). CV floor removed from
    # War Zone eligibility; CV now only contributes a small +/- to the
    # ranking score. Stamped by
    # `services/mlb_tier_sorter.war_zone_cv_modifier`.
    "war_zone_cv_modifier",
    # Multi-book de-vig TP engine (2026-04-22). Replaces the legacy
    # avg(DK,FD) / avg(DK,MGM) implied-prob TP. Fields:
    #   tp               — final de-vigged true probability (0..100) or None
    #   edge_pct         — p_model*100 − tp; None if tp is None
    #   tp_books_used    — count of books with BOTH sides available
    #   tp_books_list    — ["DK","FD","MGM","BOL"]
    #   tp_method        — "multi_book_devig_v1"
    #   tp_unavailable   — True when no book had both sides (hard-fails gate_tp)
    "tp", "edge_pct", "tp_books_used", "tp_books_list", "tp_method",
    "tp_unavailable",
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
)

_IDENTITY_FIELDS = (
    "canonical_key", "sport", "event_id", "player_name",
    "stat_type", "line", "recommendation",
)

# Universal pool lifecycle fields — present on every {sport}_prop_scores
# document regardless of sport. Used by the universal board engine
# (services/board/*) and the 60-second game-start scanner.
_UNIVERSAL_POOL_FIELDS = (
    "active", "inactive_reason", "active_changed_at", "game_start_utc",
)

# Retained for backward compatibility with services.scoring.prop_scores_store callers
SCORE_FIELDS = _IDENTITY_FIELDS + _SCORE_OUTPUT_FIELDS
SCORES_COLLECTION = "mlb_prop_scores"  # legacy default; now sport-specific


def _project_score_doc(
    context_out: Dict[str, Any], version_tag: str, computed_at: str
) -> Dict[str, Any]:
    doc = {k: context_out.get(k) for k in _IDENTITY_FIELDS if k in context_out}
    for k in _SCORE_OUTPUT_FIELDS:
        if k in context_out:
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
    doc["version_tag"] = version_tag
    doc["computed_at"] = computed_at
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
        }

    await ensure_indexes(db, sport)

    if mode == "upsert":
        upserted = 0
        modified = 0
        for doc in prepared:
            clean = {k: v for k, v in doc.items() if k != "_id"}
            ck = clean.get("canonical_key")
            if not ck:
                continue
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
        return {
            "sport": sport,
            "collection": coll_name,
            "version_tag": version_tag,
            "computed_at": computed_at,
            "prepared": len(prepared),
            "written": upserted + modified,
            "upserted": upserted,
            "modified": modified,
            "mode": "upsert",
            "dry_run": False,
        }

    # Default: replace
    # Replace docs with same (canonical_key, version_tag) deterministically.
    deleted = await coll.delete_many({"version_tag": version_tag})
    inserted = 0
    if prepared:
        # Strip any _id that may have leaked in from the raw prop dict.
        clean = [{k: v for k, v in d.items() if k != "_id"} for d in prepared]
        # Deduplicate by canonical_key (last write wins). Upstream can
        # produce multiple rows with the same canonical_key when
        # stat_type mapping falls back (e.g. unknown market → empty
        # stat_type causes collisions).
        seen: Dict[str, Dict[str, Any]] = {}
        for d in clean:
            ck = d.get("canonical_key")
            if ck:
                seen[ck] = d
        deduped = list(seen.values())
        if deduped:
            await coll.insert_many(deduped, ordered=False)
            inserted = len(deduped)

    logger.info(
        f"[SCORES_STORE:{sport}] mode=replace version='{version_tag}' "
        f"inserted={inserted} replaced={deleted.deleted_count} → {coll_name}"
    )
    return {
        "sport": sport,
        "collection": coll_name,
        "version_tag": version_tag,
        "computed_at": computed_at,
        "prepared": len(prepared),
        "written": inserted,
        "replaced": deleted.deleted_count,
        "mode": "replace",
        "dry_run": False,
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

    Step 6 cleanup: writes to the canonical `final-mlb` tag so the active
    board isn't populated with a separate `live` tag that evades the Step 6
    observation window. See /app/memory/ROADMAP.md §1b.
    """
    result = await write_versioned_scores(
        db=db, sport="mlb", score_docs=scored_props,
        version_tag="final-mlb", dry_run=False,
    )
    return {
        "inserted": result["written"],
        "purged": result.get("replaced", 0),
        "synthetic_keys": 0,
    }
