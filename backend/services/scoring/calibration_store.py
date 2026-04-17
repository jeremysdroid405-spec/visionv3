"""
Calibration snapshot persistence
=================================
Persists lean compare-experiment summaries into per-sport collections
`{sport}_calibration_runs` for long-term audit trail.

Schema (per doc):
  snapshot_id         : ObjectId string (uuid4 hex)
  sport               : "mlb" | "nba"
  created_at          : ISO8601 UTC
  label               : optional human label
  notes               : optional notes
  source_timestamps   : {live_props_fetched_at, score_collection_latest}
  baseline_variant    : name of first variant
  variants_meta       : [{name, override_config, processed, duration_ms}]
  tier_counts_table   : rows=tier, cols=variant
  tier_deltas_vs_baseline
  war_zone_movers_vs_baseline : entered_count, left_count only (no keys)
  tier_canonical_key_overlap_vs_baseline
  top_sample_overlap_vs_baseline
  summary             : summary flags block
  top_movers_sample   : <= 5 lean canonical_keys + tier transitions
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

_LIVE_PROPS_BY_SPORT = {"mlb": "mlb_live_props", "nba": "dg_live_props"}


def _lean_top_samples(variant_data: Dict[str, Any], k: int = 3) -> List[Dict[str, Any]]:
    out = []
    for s in (variant_data.get("top_samples") or [])[:k]:
        out.append({
            "canonical_key": s.get("canonical_key"),
            "player_name": s.get("player_name"),
            "stat_type": s.get("stat_type"),
            "line": s.get("line"),
            "recommendation": s.get("recommendation"),
            "vision_score": s.get("vision_score"),
            "tier": s.get("tier"),
            "pp_utility": s.get("pp_utility"),
            "pp_utility_category": s.get("pp_utility_category"),
        })
    return out


def _lean_variants_meta(
    variant_results: Dict[str, Any],
    variant_overrides: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    out = []
    for name, vd in variant_results.items():
        out.append({
            "name": name,
            "override_config": variant_overrides.get(name, {}),
            "processed": vd.get("processed", 0),
            "skipped": vd.get("skipped", 0),
            "duration_ms": vd.get("duration_ms", 0),
            "tier_distribution": vd.get("tier_distribution", {}),
            "quality_source_distribution": vd.get("quality_source_distribution", {}),
            "pp_category_distribution": vd.get("pp_category_distribution", {}),
            "top_samples_lean": _lean_top_samples(vd, k=3),
        })
    return out


def _lean_war_zone_movers(war_zone_movers: Dict[str, Any]) -> Dict[str, Any]:
    """Store only counts + first 10 canonical_keys for audit; drop long lists."""
    out = {}
    for variant, info in (war_zone_movers or {}).items():
        out[variant] = {
            "entered_count": info.get("entered_count", 0),
            "left_count": info.get("left_count", 0),
            "entered_sample": (info.get("entered") or [])[:10],
            "left_sample": (info.get("left") or [])[:10],
        }
    return out


async def _source_timestamps(db, sport: str) -> Dict[str, Any]:
    live_coll = _LIVE_PROPS_BY_SPORT.get(sport)
    lp_fetched = None
    if live_coll:
        d = await db[live_coll].find_one({}, {"fetched_at": 1, "_id": 0})
        if d:
            lp_fetched = d.get("fetched_at")
    latest_score = None
    d2 = await db[f"{sport}_prop_scores"].find_one(
        {}, sort=[("computed_at", -1)], projection={"computed_at": 1, "_id": 0},
    )
    if d2:
        latest_score = d2.get("computed_at")
    return {
        "live_props_fetched_at": str(lp_fetched) if lp_fetched else None,
        "score_collection_latest": str(latest_score) if latest_score else None,
    }


async def write_snapshot(
    db,
    sport: str,
    comparison: Dict[str, Any],
    variant_overrides: Dict[str, Dict[str, Any]],
    label: Optional[str],
    notes: Optional[str],
    limit: Optional[int],
) -> Dict[str, Any]:
    """Persist a lean snapshot of a compare run. Returns the stored doc (minus _id)."""
    coll = db[f"{sport}_calibration_runs"]

    # Ensure indexes (lazy)
    try:
        await coll.create_index([("created_at", -1)], name="idx_created_desc")
        await coll.create_index([("label", 1)], name="idx_label")
        await coll.create_index([("snapshot_id", 1)], unique=True, name="uniq_snap_id")
    except Exception as e:
        logger.warning(f"[CALIBRATION:{sport}] index create skipped: {e}")

    snapshot_id = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()
    sources = await _source_timestamps(db, sport)

    variant_results = comparison.get("variant_results", {})
    doc = {
        "snapshot_id": snapshot_id,
        "sport": sport,
        "created_at": created_at,
        "label": label,
        "notes": notes,
        "limit_applied": limit,
        "source_timestamps": sources,
        "baseline_variant": comparison.get("variant_results")
            and next(iter(variant_results.keys()), None),
        "variants_meta": _lean_variants_meta(variant_results, variant_overrides),
        "tier_counts_table": comparison.get("tier_counts_table", {}),
        "tier_deltas_vs_baseline": comparison.get("tier_deltas_vs_baseline", {}),
        "war_zone_movers_vs_baseline": _lean_war_zone_movers(
            comparison.get("war_zone_movers_vs_baseline", {})
        ),
        "tier_canonical_key_overlap_vs_baseline": comparison.get(
            "tier_canonical_key_overlap_vs_baseline", {}
        ),
        "top_sample_overlap_vs_baseline": comparison.get(
            "top_sample_overlap_vs_baseline", {}
        ),
        "summary": comparison.get("summary", {}),
    }

    await coll.insert_one(dict(doc))  # copy — defensively strip any _id leak
    doc.pop("_id", None)
    logger.info(
        f"[CALIBRATION:{sport}] snapshot {snapshot_id} label='{label}' "
        f"variants={len(doc['variants_meta'])}"
    )
    return doc
