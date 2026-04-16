"""
mlb_pipeline.py
================
MLB Pipeline Runner - Uses restored tier_sorter with new collection names.

This bridges the existing MLB tier sorting logic with the new collection structure
(mlb_safe_haven, mlb_front_lines, mlb_war_zone).
"""

import logging
from typing import Dict, Any
from datetime import datetime, timezone
from pymongo import UpdateOne

logger = logging.getLogger(__name__)


async def run_mlb_pipeline(db, save_to_db: bool = True) -> Dict[str, Any]:
    """
    Run the MLB pipeline via UnifiedPipeline framework.
    
    Same output contract: populates mlb_safe_haven, mlb_front_lines, mlb_war_zone.
    Now uses shared pipeline architecture with atomic writes and validation metadata.
    """
    from services.unified_pipeline import UnifiedPipeline
    from services.adapters.mlb_adapter import MLBAdapter

    adapter = MLBAdapter()
    pipeline = UnifiedPipeline(adapter, db)
    result = await pipeline.run()

    return {
        "success": result.success,
        "output": result.tiers,
        "timings": {k: v.get("duration_s", 0) for k, v in result.phases.items()},
        "validation_stats": result.validation_stats,
        "run_id": result.run_id,
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "errors": result.errors,
    }


async def _atomic_upsert_with_delta_check(
    db, collection_name: str, picks: list, tier_name: str
):
    """
    JIT Delta Check + Atomic Upsert.
    
    1. Query existing collection for cached vision_intel
    2. Identify delta picks (new or missing intel)
    3. Generate vision_intel for delta picks
    4. Merge cached intel for returning picks
    5. Atomic upsert (collection never empty)
    """
    collection = db[collection_name]
    
    if not picks:
        await collection.delete_many({})
        logger.info(f"  [{tier_name}] Cleared (no picks)")
        return
    
    # Step 1: Query existing for cached intel
    existing_docs = await collection.find(
        {},
        {"player_name": 1, "stat_type": 1, "line": 1, "vision_intel": 1, 
         "oracle_summary": 1, "intel_score": 1}
    ).to_list(length=50)
    
    cache_map = {}
    for doc in existing_docs:
        key = f"{doc.get('player_name')}|{doc.get('stat_type')}|{doc.get('line')}"
        intel = doc.get("vision_intel") or doc.get("oracle_summary")
        if intel:
            cache_map[key] = {
                "vision_intel": doc.get("vision_intel"),
                "oracle_summary": doc.get("oracle_summary"),
                "intel_score": doc.get("intel_score")
            }
    
    logger.info(f"  [{tier_name}] Cached intel: {len(cache_map)} picks")
    
    # Step 2: Identify delta and merge cached
    delta_count = 0
    for pick in picks:
        key = f"{pick.get('player_name')}|{pick.get('stat_type')}|{pick.get('line')}"
        if key in cache_map:
            # Merge cached intel
            pick.update(cache_map[key])
        else:
            delta_count += 1
            # Generate fallback intel if no Gemini
            if not pick.get("vision_intel") and not pick.get("oracle_summary"):
                player = pick.get("player_name", "Player")
                stat = pick.get("stat_type", "stat")
                line = pick.get("line", 0)
                h10 = pick.get("l10_hit_rate") or pick.get("hit_rate_l10", 0)
                edge = pick.get("vk_edge", 0)
                
                pick["vision_intel"] = f"{player}: {h10:.0f}% L10 hit rate on {stat} {line}. Edge: {edge:+.1f}%"
                pick["intel_score"] = 6
    
    logger.info(f"  [{tier_name}] Delta: {delta_count} new picks")
    
    # Step 3: Atomic upsert
    current_keys = set()
    operations = []
    
    for pick in picks:
        # Remove _id if present
        pick_clean = {k: v for k, v in pick.items() if k != "_id"}
        
        key = f"{pick_clean.get('player_name')}|{pick_clean.get('stat_type')}|{pick_clean.get('line')}"
        current_keys.add(key)
        
        operations.append(UpdateOne(
            {
                "player_name": pick_clean.get("player_name"),
                "stat_type": pick_clean.get("stat_type"),
                "line": pick_clean.get("line")
            },
            {"$set": pick_clean},
            upsert=True
        ))
    
    if operations:
        await collection.bulk_write(operations, ordered=False)
    
    # Clean stale picks
    all_docs = await collection.find(
        {}, {"player_name": 1, "stat_type": 1, "line": 1}
    ).to_list(length=100)
    
    stale_ids = []
    for doc in all_docs:
        key = f"{doc.get('player_name')}|{doc.get('stat_type')}|{doc.get('line')}"
        if key not in current_keys:
            stale_ids.append(doc["_id"])
    
    if stale_ids:
        await collection.delete_many({"_id": {"$in": stale_ids}})
        logger.info(f"  [{tier_name}] Cleaned {len(stale_ids)} stale picks")
    
    logger.info(f"  [{tier_name}] Saved {len(picks)} picks (atomic upsert)")
