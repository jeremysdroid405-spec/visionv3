"""
mlb_prop_scores writer
======================
Persists the three scoring dimensions to a dedicated collection.

Collection: mlb_prop_scores
Key:        canonical_key  (sport|event_id|player|stat|line|side)

Per spec: scoring must NOT be embedded in mlb_cached_board or tier
collections. This is the single source of truth for scoring outputs;
other pipelines may look up by canonical_key.
"""
from datetime import datetime, timezone
from typing import Dict, List, Any
import logging

logger = logging.getLogger(__name__)

SCORES_COLLECTION = "mlb_prop_scores"

# Exactly the fields produced by the scoring stack — nothing else.
SCORE_FIELDS = (
    # identity
    "canonical_key", "sport", "event_id", "player_name", "stat_type", "line",
    "recommendation",
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
)


def _project_score_doc(prop: Dict[str, Any]) -> Dict[str, Any]:
    """Extract ONLY the scoring-stack fields + canonical identity."""
    doc = {k: prop.get(k) for k in SCORE_FIELDS if k in prop}
    doc["scored_at"] = datetime.now(timezone.utc).isoformat()
    return doc


async def write_prop_scores(db, scored_props: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Drop-and-replace the mlb_prop_scores collection with the current slate's
    scoring outputs. Returns counts.
    """
    coll = db[SCORES_COLLECTION]

    # Build score-only docs keyed by canonical_key
    docs = []
    missing_canon = 0
    for p in scored_props:
        canon = p.get("canonical_key")
        if not canon:
            # Rebuild a synthetic key for legacy rows so we never lose scores.
            canon = (
                f"{p.get('sport','mlb')}|{p.get('event_id','?')}|"
                f"{p.get('player_name','?')}|{p.get('stat_type','?')}|"
                f"{p.get('line','?')}|{p.get('recommendation','?')}"
            )
            p["canonical_key"] = canon
            missing_canon += 1
        docs.append(_project_score_doc(p))

    stale = await coll.count_documents({})
    await coll.delete_many({})
    if docs:
        # Strip _id defensively
        clean = [{k: v for k, v in d.items() if k != "_id"} for d in docs]
        await coll.insert_many(clean)

    # Ensure unique index on canonical_key for future upsert paths.
    try:
        await coll.create_index("canonical_key", unique=True, name="uniq_canonical")
    except Exception as e:
        logger.warning(f"[PROP_SCORES] index create skipped: {e}")

    logger.info(
        f"[PROP_SCORES] Wrote {len(docs)} score docs "
        f"(purged {stale} stale, {missing_canon} needed synthetic canonical keys)"
    )
    return {"inserted": len(docs), "purged": stale, "synthetic_keys": missing_canon}


# Fields that must be STRIPPED from the in-memory prop before downstream
# writers persist to cached_board / tier collections. This enforces the
# "do NOT embed" rule.
STRIPPED_FROM_PROPS = tuple(
    f for f in SCORE_FIELDS
    if f not in ("canonical_key", "sport", "event_id",
                 "player_name", "stat_type", "line", "recommendation")
)


def strip_score_fields(props: List[Dict[str, Any]]) -> None:
    """Mutate props in-place: remove scoring-stack fields post-persist."""
    for p in props:
        for f in STRIPPED_FROM_PROPS:
            if f in p:
                del p[f]
