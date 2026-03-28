"""
Vision Intel Enrichment Service - SIMPLIFIED v2
================================================
Generates Vision Intel + AI Summaries for the ~30-50 props displayed on the 3 tier boards.

The key insight: We use the SAME queries as the board endpoints to get the exact picks
that users will see. This ensures we only enrich what's actually displayed.

Flow:
1. Run same queries as /api/v3/war-zone, /api/v3/goblin-vault, /api/v3/front-lines
2. Dedupe to get ~30-50 unique props
3. Generate AI summaries with Semaphore(5)
4. Update props in dg_cached_board
5. Done in <5 seconds
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

GEMINI_CONCURRENT_LIMIT = 5


async def run_vision_intel_enrichment(db: AsyncIOMotorDatabase) -> Dict[str, Any]:
    """
    Enrich Vision Intel for the exact picks shown on tier boards.
    Should complete in <5 seconds.
    """
    start = datetime.now(timezone.utc)
    logger.info("[VISION_INTEL] Starting board pick enrichment...")
    
    # Get the exact picks from each board (same queries as the endpoints)
    war_zone = await _get_war_zone_picks(db)
    safe_haven = await _get_safe_haven_picks(db)
    front_lines = await _get_front_lines_picks(db)
    
    # Combine and dedupe
    all_picks = []
    seen = set()
    
    for pick in war_zone + safe_haven + front_lines:
        key = f"{pick['player_name']}|{pick['stat_type']}|{pick['line']}"
        if key not in seen:
            seen.add(key)
            all_picks.append(pick)
    
    logger.info(f"[VISION_INTEL] {len(all_picks)} unique picks (WZ:{len(war_zone)}, SH:{len(safe_haven)}, FL:{len(front_lines)})")
    
    if not all_picks:
        return {"picks": 0, "enriched": 0, "duration": 0}
    
    # Enrich with AI summaries (rate limited)
    semaphore = asyncio.Semaphore(GEMINI_CONCURRENT_LIMIT)
    tasks = [_enrich_pick(db, pick, semaphore) for pick in all_picks]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    enriched = sum(1 for r in results if r is True)
    duration = (datetime.now(timezone.utc) - start).total_seconds()
    
    logger.info(f"[VISION_INTEL] DONE: {enriched}/{len(all_picks)} enriched in {duration:.1f}s")
    
    return {"picks": len(all_picks), "enriched": enriched, "duration": round(duration, 2)}


async def _get_war_zone_picks(db: AsyncIOMotorDatabase) -> List[Dict]:
    """Get War Zone picks (demons) - top 10."""
    now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    
    picks = await db.dg_cached_board.aggregate([
        {"$unwind": "$props"},
        {"$match": {"props.is_demon": True, "props.commence_time": {"$gt": now_iso}}},
        {"$project": {
            "_id": 0,
            "player_name": 1,
            "team": 1,
            "stat_type": "$props.stat_type_extracted",
            "line": "$props.line",
            "h10_rate": "$props.h10_rate",
            "l5_avg": "$props.l5_avg",
            "season_avg": "$props.season_avg",
            "opponent": "$props.opponent",
            "dvp_rank": "$props.dvp_rank",
            "is_demon": "$props.is_demon",
            "is_goblin": "$props.is_goblin",
            "board": {"$literal": "war_zone"}
        }},
        {"$sort": {"h10_rate": -1}},
        {"$limit": 10}
    ]).to_list(10)
    
    # Find prop indices for updating
    for pick in picks:
        pick["prop_index"] = await _find_prop_index(db, pick)
    
    return picks


async def _get_safe_haven_picks(db: AsyncIOMotorDatabase) -> List[Dict]:
    """Get Safe Haven picks (goblins) - top 10."""
    now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    
    picks = await db.dg_cached_board.aggregate([
        {"$unwind": "$props"},
        {"$match": {"props.is_goblin": True, "props.commence_time": {"$gt": now_iso}}},
        {"$project": {
            "_id": 0,
            "player_name": 1,
            "team": 1,
            "stat_type": "$props.stat_type_extracted",
            "line": "$props.line",
            "h10_rate": "$props.h10_rate",
            "l5_avg": "$props.l5_avg",
            "season_avg": "$props.season_avg",
            "opponent": "$props.opponent",
            "dvp_rank": "$props.dvp_rank",
            "is_demon": "$props.is_demon",
            "is_goblin": "$props.is_goblin",
            "board": {"$literal": "safe_haven"}
        }},
        {"$sort": {"h10_rate": -1}},
        {"$limit": 10}
    ]).to_list(10)
    
    for pick in picks:
        pick["prop_index"] = await _find_prop_index(db, pick)
    
    return picks


async def _get_front_lines_picks(db: AsyncIOMotorDatabase) -> List[Dict]:
    """Get Front Lines picks (mixed high-value) - top 10."""
    now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    
    picks = await db.dg_cached_board.aggregate([
        {"$unwind": "$props"},
        {"$match": {
            "props.commence_time": {"$gt": now_iso},
            "$or": [{"props.is_demon": True}, {"props.is_goblin": True}]
        }},
        {"$project": {
            "_id": 0,
            "player_name": 1,
            "team": 1,
            "stat_type": "$props.stat_type_extracted",
            "line": "$props.line",
            "h10_rate": "$props.h10_rate",
            "l5_avg": "$props.l5_avg",
            "season_avg": "$props.season_avg",
            "opponent": "$props.opponent",
            "dvp_rank": "$props.dvp_rank",
            "is_demon": "$props.is_demon",
            "is_goblin": "$props.is_goblin",
            "board": {"$literal": "front_lines"}
        }},
        {"$sort": {"h10_rate": -1}},
        {"$limit": 10}
    ]).to_list(10)
    
    for pick in picks:
        pick["prop_index"] = await _find_prop_index(db, pick)
    
    return picks


async def _find_prop_index(db: AsyncIOMotorDatabase, pick: Dict) -> int:
    """Find the index of this prop in the player's props array."""
    player = await db.dg_cached_board.find_one(
        {"player_name": pick["player_name"]},
        {"_id": 0, "props": 1}
    )
    
    if not player:
        return -1
    
    stat = pick.get("stat_type", "")
    line = pick.get("line", 0)
    
    for i, prop in enumerate(player.get("props", [])):
        prop_stat = prop.get("stat_type_extracted") or prop.get("stat_type", "")
        prop_line = prop.get("line", 0)
        if prop_stat == stat and abs(prop_line - line) < 0.1:
            return i
    
    return -1


async def _enrich_pick(db: AsyncIOMotorDatabase, pick: Dict, semaphore: asyncio.Semaphore) -> bool:
    """Enrich a single pick with AI summary."""
    idx = pick.get("prop_index", -1)
    if idx < 0:
        return False
    
    try:
        async with semaphore:
            ai_summary = await _generate_ai_summary(pick)
        
        intel_suite = _build_intel_suite(pick, ai_summary)
        
        await db.dg_cached_board.update_one(
            {"player_name": pick["player_name"]},
            {"$set": {
                f"props.{idx}.vision_summary": ai_summary,
                f"props.{idx}.intel_suite": intel_suite,
                f"props.{idx}.is_vision_enriched": True,
                f"props.{idx}.vision_enriched_at": datetime.now(timezone.utc).isoformat()
            }}
        )
        return True
    except Exception as e:
        logger.error(f"[VISION_INTEL] Error: {pick['player_name']} - {e}")
        return False


async def _generate_ai_summary(pick: Dict) -> Optional[str]:
    """Generate AI summary using Gemini."""
    try:
        from services.vision_summary_service import VisionSummaryService
        service = VisionSummaryService()
        return await service.generate_pick_summary(
            player_name=pick["player_name"],
            stat_type=pick.get("stat_type", "PTS"),
            line=pick.get("line", 0),
            season_avg=pick.get("season_avg") or pick.get("l5_avg") or 0,
            h10_rate=pick.get("h10_rate", 0) or 0,
            badges=[],
            opponent=pick.get("opponent", ""),
            is_demon=pick.get("is_demon", False),
            is_goblin=pick.get("is_goblin", False),
            dvp_rank=pick.get("dvp_rank"),
            dvp_friction=None,
            player_team=pick.get("team", "")
        )
    except Exception as e:
        logger.warning(f"[VISION_INTEL] Gemini error: {e}")
        return None


def _build_intel_suite(pick: Dict, ai_summary: Optional[str]) -> Dict[str, Any]:
    """Build intel suite object."""
    h10 = pick.get("h10_rate", 0) or 0
    dvp = pick.get("dvp_rank", 15) or 15
    
    return {
        "matchup_dvp": {
            "opponent": pick.get("opponent", ""),
            "dvp_rank": dvp,
            "friction_level": "Low" if dvp >= 20 else "Medium" if dvp >= 10 else "High"
        },
        "stability_index": {
            "score": int(h10) if h10 else 50,
            "consistency": "HIGH" if h10 >= 70 else "MEDIUM" if h10 >= 50 else "LOW"
        },
        "vision_insight": {
            "ai_summary": ai_summary,
            "status": "ready" if ai_summary else "loading"
        },
        "board": pick.get("board"),
        "cached_at": datetime.now(timezone.utc).isoformat()
    }
