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
        await coll.insert_many(clean)
        inserted = len(clean)

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
    Kept as thin wrapper around write_versioned_scores with version='live'.
    """
    result = await write_versioned_scores(
        db=db, sport="mlb", score_docs=scored_props,
        version_tag="live", dry_run=False,
    )
    return {
        "inserted": result["written"],
        "purged": result.get("replaced", 0),
        "synthetic_keys": 0,
    }
