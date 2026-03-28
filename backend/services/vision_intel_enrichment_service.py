"""
Vision Intel Enrichment - EXACT COPY of board endpoint queries
===============================================================
Uses the EXACT SAME MongoDB queries as the 3 board endpoints.
No interpretation, just copy-paste the query logic.
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
    Enrich Vision Intel for the exact picks on all 3 boards.
    """
    start = datetime.now(timezone.utc)
    logger.info("[VISION_INTEL] Starting enrichment...")
    
    cached_board = db.dg_cached_board
    now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    
    # ========== WAR ZONE: is_demon=True ==========
    wz_picks = await cached_board.aggregate([
        {"$unwind": "$props"},
        {"$match": {
            "props.is_demon": True,
            "props.commence_time": {"$gt": now_iso}
        }},
        {"$project": {
            "_id": 0,
            "player_name": 1,
            "team": 1,
            "stat_type": "$props.stat_type_extracted",
            "line": "$props.line",
            "h10_rate": "$props.h10_rate",
            "combined_score": "$props.combined_score",
            "l5_avg": "$props.l5_avg",
            "season_avg": "$props.season_avg",
            "opponent": "$props.opponent",
            "dvp_rank": "$props.dvp_rank",
            "board": {"$literal": "war_zone"}
        }},
        {"$sort": {"h10_rate": -1, "combined_score": -1}},
        {"$limit": 100}
    ]).to_list(100)
    
    # Dedupe by player, top 10
    war_zone = _dedupe_by_player(wz_picks, 10)
    
    # ========== SAFE HAVEN: is_goblin=True AND h10_rate >= 80 ==========
    sh_picks = await cached_board.aggregate([
        {"$unwind": "$props"},
        {"$match": {
            "props.is_goblin": True,
            "props.h10_rate": {"$gte": 80},
            "props.commence_time": {"$gt": now_iso}
        }},
        {"$project": {
            "_id": 0,
            "player_name": 1,
            "team": 1,
            "stat_type": "$props.stat_type_extracted",
            "line": "$props.line",
            "h10_rate": "$props.h10_rate",
            "combined_score": "$props.combined_score",
            "l5_avg": "$props.l5_avg",
            "season_avg": "$props.season_avg",
            "opponent": "$props.opponent",
            "dvp_rank": "$props.dvp_rank",
            "board": {"$literal": "safe_haven"}
        }},
        {"$sort": {"h10_rate": -1, "combined_score": -1}},
        {"$limit": 100}
    ]).to_list(100)
    
    safe_haven = _dedupe_by_player(sh_picks, 10)
    
    # ========== FRONT LINES: is_goblin=True AND 60 <= h10_rate < 80 ==========
    fl_picks = await cached_board.aggregate([
        {"$unwind": "$props"},
        {"$match": {
            "props.is_goblin": True,
            "props.h10_rate": {"$gte": 60, "$lt": 80},
            "props.commence_time": {"$gt": now_iso}
        }},
        {"$project": {
            "_id": 0,
            "player_name": 1,
            "team": 1,
            "stat_type": "$props.stat_type_extracted",
            "line": "$props.line",
            "h10_rate": "$props.h10_rate",
            "combined_score": "$props.combined_score",
            "l5_avg": "$props.l5_avg",
            "season_avg": "$props.season_avg",
            "opponent": "$props.opponent",
            "dvp_rank": "$props.dvp_rank",
            "board": {"$literal": "front_lines"}
        }},
        {"$sort": {"h10_rate": -1, "combined_score": -1}},
        {"$limit": 100}
    ]).to_list(100)
    
    front_lines = _dedupe_by_player(fl_picks, 10)
    
    # Combine all
    all_picks = war_zone + safe_haven + front_lines
    logger.info(f"[VISION_INTEL] WZ:{len(war_zone)} SH:{len(safe_haven)} FL:{len(front_lines)} = {len(all_picks)} total")
    
    if not all_picks:
        return {"picks": 0, "enriched": 0, "duration": 0}
    
    # Find prop indices
    for pick in all_picks:
        pick["prop_index"] = await _find_prop_index(db, pick)
    
    # Enrich with AI summaries
    semaphore = asyncio.Semaphore(GEMINI_CONCURRENT_LIMIT)
    tasks = [_enrich_pick(db, pick, semaphore) for pick in all_picks]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    enriched = sum(1 for r in results if r is True)
    duration = (datetime.now(timezone.utc) - start).total_seconds()
    
    logger.info(f"[VISION_INTEL] DONE: {enriched}/{len(all_picks)} in {duration:.1f}s")
    
    return {
        "war_zone": len(war_zone),
        "safe_haven": len(safe_haven),
        "front_lines": len(front_lines),
        "total": len(all_picks),
        "enriched": enriched,
        "duration": round(duration, 2)
    }


def _dedupe_by_player(picks: List[Dict], limit: int) -> List[Dict]:
    """Dedupe picks by player name, return top N."""
    seen = set()
    unique = []
    for p in picks:
        name = p.get("player_name")
        if name and name not in seen:
            seen.add(name)
            unique.append(p)
            if len(unique) >= limit:
                break
    return unique


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
    player_name = pick.get("player_name", "Unknown")
    
    if idx < 0:
        logger.warning(f"[VISION_INTEL] Skipping {player_name} - no prop_index")
        return False
    
    try:
        async with semaphore:
            ai_summary = await _generate_ai_summary(pick)
        
        intel_suite = {
            "matchup_dvp": {
                "opponent": pick.get("opponent", ""),
                "dvp_rank": pick.get("dvp_rank", 15) or 15,
            },
            "stability_index": {
                "score": int(pick.get("h10_rate", 50) or 50),
            },
            "vision_insight": {
                "ai_summary": ai_summary,
                "status": "ready" if ai_summary else "loading"
            },
            "board": pick.get("board"),
            "cached_at": datetime.now(timezone.utc).isoformat()
        }
        
        update_result = await db.dg_cached_board.update_one(
            {"player_name": player_name},
            {"$set": {
                f"props.{idx}.vision_summary": ai_summary,
                f"props.{idx}.intel_suite": intel_suite,
                f"props.{idx}.is_vision_enriched": True,
                f"props.{idx}.vision_enriched_at": datetime.now(timezone.utc).isoformat(),
                f"props.{idx}.board": pick.get("board")
            }}
        )
        
        if update_result.modified_count == 0:
            logger.warning(f"[VISION_INTEL] No update for {player_name} idx={idx}")
        
        return True
    except Exception as e:
        logger.error(f"[VISION_INTEL] Error: {player_name} - {e}")
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
            is_demon=pick.get("board") == "war_zone",
            is_goblin=pick.get("board") in ["safe_haven", "front_lines"],
            dvp_rank=pick.get("dvp_rank"),
            dvp_friction=None,
            player_team=pick.get("team", "")
        )
    except Exception as e:
        logger.warning(f"[VISION_INTEL] Gemini error: {e}")
        return None
