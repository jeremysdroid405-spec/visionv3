"""
Board Intelligence Enrichment Service
======================================
Unified enrichment pipeline for all 3 boards with:
1. AI-Weighted Vision Score calculation
2. Waterfall selection (no player overlap)
3. Full Intel Suite enrichment (same code path for all boards)

This replaces the fragmented vision_intel_enrichment_service.py
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Set
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.vision_score_calculator import (
    calculate_vision_score, 
    SAFE_HAVEN_THRESHOLDS,
    FRONT_LINES_THRESHOLDS, 
    WAR_ZONE_THRESHOLDS
)
from services.intel_suite_calculator import IntelSuiteCalculator

logger = logging.getLogger(__name__)

GEMINI_CONCURRENT_LIMIT = 5
PLAYERS_PER_BOARD = 10


async def run_board_intelligence_enrichment(db: AsyncIOMotorDatabase) -> Dict[str, Any]:
    """
    Main entry point: Enrich all 3 boards with full intelligence data.
    
    Waterfall Selection Order:
    1. Safe Haven: Goblins with L10 HR >= 80% and Vision_Score >= 85
    2. Front Lines: Remaining props with L10 HR >= 60% and Vision_Score >= 70
    3. War Zone: Demons with L10 HR >= 50% and Vision_Score >= 60
    
    Each board gets 10 unique players (30 total, no overlap).
    """
    start = datetime.now(timezone.utc)
    logger.info("[BOARD_INTEL] Starting AI-Weighted Waterfall Enrichment...")
    
    cached_board = db.dg_cached_board
    now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    
    # ========== STEP 1: Fetch ALL eligible props ==========
    all_props = await _fetch_all_board_eligible_props(cached_board, now_iso)
    logger.info(f"[BOARD_INTEL] Fetched {len(all_props)} total eligible props")
    
    if not all_props:
        return {"total": 0, "enriched": 0, "duration": 0}
    
    # ========== STEP 2: Calculate Vision Score for each prop ==========
    for prop in all_props:
        score_result = calculate_vision_score(
            h10_rate=prop.get("h10_rate", 0),
            dvp_rank=prop.get("dvp_rank"),
            active_badges=prop.get("active_badges", []),
            is_demon=prop.get("is_demon", False),
            is_goblin=prop.get("is_goblin", False)
        )
        prop["vision_score"] = score_result["vision_score"]
        prop["vision_score_breakdown"] = score_result
    
    # ========== STEP 3: Waterfall Selection (no overlap) ==========
    used_players: Set[str] = set()
    
    # Board 1: Safe Haven (Goblins, HR >= 80%, Vision >= 85)
    safe_haven = _select_board_players(
        all_props,
        used_players,
        board_name="safe_haven",
        filter_fn=lambda p: (
            p.get("is_goblin") == True and
            (p.get("h10_rate") or 0) >= SAFE_HAVEN_THRESHOLDS["min_h10_rate"] and
            p.get("vision_score", 0) >= SAFE_HAVEN_THRESHOLDS["min_vision_score"]
        ),
        limit=PLAYERS_PER_BOARD
    )
    
    # Board 2: Front Lines (Any tier, HR >= 60%, Vision >= 70)
    front_lines = _select_board_players(
        all_props,
        used_players,
        board_name="front_lines",
        filter_fn=lambda p: (
            (p.get("h10_rate") or 0) >= FRONT_LINES_THRESHOLDS["min_h10_rate"] and
            p.get("vision_score", 0) >= FRONT_LINES_THRESHOLDS["min_vision_score"]
        ),
        limit=PLAYERS_PER_BOARD
    )
    
    # Board 3: War Zone (Demons, HR >= 50%, Vision >= 60)
    war_zone = _select_board_players(
        all_props,
        used_players,
        board_name="war_zone",
        filter_fn=lambda p: (
            p.get("is_demon") == True and
            (p.get("h10_rate") or 0) >= WAR_ZONE_THRESHOLDS["min_h10_rate"] and
            p.get("vision_score", 0) >= WAR_ZONE_THRESHOLDS["min_vision_score"]
        ),
        limit=PLAYERS_PER_BOARD
    )
    
    logger.info(f"[BOARD_INTEL] Waterfall Selection: SH={len(safe_haven)}, FL={len(front_lines)}, WZ={len(war_zone)}")
    
    # Combine all selected picks
    all_selected = safe_haven + front_lines + war_zone
    
    if not all_selected:
        logger.warning("[BOARD_INTEL] No picks selected for any board")
        return {"total": 0, "enriched": 0, "duration": 0}
    
    # ========== STEP 4: Find prop indices in cached_board ==========
    for pick in all_selected:
        pick["prop_index"] = await _find_prop_index(db, pick)
    
    # ========== STEP 5: Full Intelligence Enrichment (same for all boards) ==========
    intel_calculator = IntelSuiteCalculator(db)
    semaphore = asyncio.Semaphore(GEMINI_CONCURRENT_LIMIT)
    
    tasks = [
        _enrich_pick_full(db, pick, intel_calculator, semaphore) 
        for pick in all_selected
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    enriched = sum(1 for r in results if r is True)
    errors = sum(1 for r in results if isinstance(r, Exception))
    
    duration = (datetime.now(timezone.utc) - start).total_seconds()
    
    logger.info(f"[BOARD_INTEL] COMPLETE: {enriched}/{len(all_selected)} enriched, {errors} errors, {duration:.1f}s")
    
    return {
        "safe_haven": len(safe_haven),
        "front_lines": len(front_lines),
        "war_zone": len(war_zone),
        "total": len(all_selected),
        "enriched": enriched,
        "errors": errors,
        "duration": round(duration, 2)
    }


async def _fetch_all_board_eligible_props(cached_board, now_iso: str) -> List[Dict]:
    """
    Fetch ALL props that could qualify for any board.
    Includes both demons and goblins with h10_rate >= 50%.
    """
    pipeline = [
        {"$unwind": "$props"},
        {"$match": {
            "$or": [
                {"props.is_demon": True},
                {"props.is_goblin": True}
            ],
            "props.h10_rate": {"$gte": 50},  # Minimum for any board
            "props.commence_time": {"$gt": now_iso}
        }},
        {"$project": {
            "_id": 0,
            "player_name": 1,
            "team": 1,
            "photo_url": 1,
            "position": 1,
            "stat_type": "$props.stat_type_extracted",
            "stat_type_raw": "$props.stat_type",
            "line": "$props.line",
            "direction": "$props.direction",
            "h10_rate": "$props.h10_rate",
            "h5_rate": "$props.h5_rate",
            "l5_avg": "$props.l5_avg",
            "l10_avg": "$props.l10_avg",
            "season_avg": "$props.season_avg",
            "combined_score": "$props.combined_score",
            "opponent": "$props.opponent",
            "dvp_rank": "$props.dvp_rank",
            "game_id": "$props.game_id",
            "commence_time": "$props.commence_time",
            "is_demon": "$props.is_demon",
            "is_goblin": "$props.is_goblin",
            "active_badges": "$props.active_badges",
            "context_badges": "$props.context_badges",
            "price": "$props.price"
        }},
        {"$sort": {"h10_rate": -1, "combined_score": -1}}
    ]
    
    return await cached_board.aggregate(pipeline).to_list(500)


def _select_board_players(
    all_props: List[Dict],
    used_players: Set[str],
    board_name: str,
    filter_fn,
    limit: int
) -> List[Dict]:
    """
    Select unique players for a board using filter function.
    Marks selected players as used to prevent overlap.
    """
    selected = []
    
    # Filter and sort by vision_score
    eligible = [p for p in all_props if filter_fn(p)]
    eligible.sort(key=lambda x: (x.get("vision_score", 0), x.get("h10_rate", 0)), reverse=True)
    
    for prop in eligible:
        player_name = prop.get("player_name")
        if not player_name or player_name in used_players:
            continue
        
        # Mark as used and assign board
        used_players.add(player_name)
        prop["board"] = board_name
        selected.append(prop)
        
        if len(selected) >= limit:
            break
    
    logger.info(f"[BOARD_INTEL] {board_name}: {len(selected)} players selected")
    return selected


async def _find_prop_index(db: AsyncIOMotorDatabase, pick: Dict) -> int:
    """Find the index of this prop in the player's props array."""
    player = await db.dg_cached_board.find_one(
        {"player_name": pick["player_name"]},
        {"_id": 0, "props": 1}
    )
    if not player:
        return -1
    
    stat = pick.get("stat_type") or pick.get("stat_type_raw", "")
    line = pick.get("line", 0)
    
    for i, prop in enumerate(player.get("props", [])):
        prop_stat = prop.get("stat_type_extracted") or prop.get("stat_type", "")
        prop_line = prop.get("line", 0)
        if prop_stat == stat and abs(prop_line - line) < 0.1:
            return i
    return -1


async def _enrich_pick_full(
    db: AsyncIOMotorDatabase, 
    pick: Dict, 
    intel_calculator: IntelSuiteCalculator,
    semaphore: asyncio.Semaphore
) -> bool:
    """
    Enrich a single pick with FULL Intelligence Suite.
    Same code path for ALL boards (Safe Haven, Front Lines, War Zone).
    """
    idx = pick.get("prop_index", -1)
    player_name = pick.get("player_name", "Unknown")
    board = pick.get("board", "unknown")
    
    if idx < 0:
        logger.warning(f"[BOARD_INTEL] Skipping {player_name} - no prop_index")
        return False
    
    try:
        async with semaphore:
            # ========== FULL INTEL SUITE (usage_ripple, matchup_dvp, pace_delta, stability_index, vision_insight) ==========
            intel_suite = await intel_calculator.calculate_intel_suite(
                player_name=player_name,
                stat_type=pick.get("stat_type") or pick.get("stat_type_raw", "PTS"),
                line=pick.get("line", 0),
                direction=pick.get("direction", "over"),
                opponent=pick.get("opponent"),
                board_pick=pick
            )
            
            # Add board identifier and vision score
            intel_suite["board"] = board
            intel_suite["vision_score"] = pick.get("vision_score", 0)
            intel_suite["vision_score_breakdown"] = pick.get("vision_score_breakdown", {})
            
            # ========== AI VISION SUMMARY (Gemini) ==========
            ai_summary = await _generate_ai_summary(pick)
            
            # Merge AI summary into vision_insight
            if intel_suite.get("vision_insight"):
                intel_suite["vision_insight"]["ai_summary"] = ai_summary
                intel_suite["vision_insight"]["status"] = "ready" if ai_summary else "pending"
        
        # ========== UPDATE MONGODB ==========
        update_result = await db.dg_cached_board.update_one(
            {"player_name": player_name},
            {"$set": {
                f"props.{idx}.vision_summary": ai_summary,
                f"props.{idx}.intel_suite": intel_suite,
                f"props.{idx}.is_vision_enriched": True,
                f"props.{idx}.vision_enriched_at": datetime.now(timezone.utc).isoformat(),
                f"props.{idx}.board": board,
                f"props.{idx}.vision_score": pick.get("vision_score", 0)
            }}
        )
        
        if update_result.modified_count > 0:
            logger.debug(f"[BOARD_INTEL] Enriched {player_name} for {board}")
            return True
        else:
            logger.warning(f"[BOARD_INTEL] No update for {player_name} idx={idx}")
            return False
            
    except Exception as e:
        logger.error(f"[BOARD_INTEL] Error enriching {player_name}: {e}")
        return False


async def _generate_ai_summary(pick: Dict) -> Optional[str]:
    """Generate AI summary using Gemini Vision Summary Service."""
    try:
        from services.vision_summary_service import VisionSummaryService
        service = VisionSummaryService()
        
        board = pick.get("board", "")
        is_demon = board == "war_zone" or pick.get("is_demon", False)
        is_goblin = board in ["safe_haven", "front_lines"] or pick.get("is_goblin", False)
        
        return await service.generate_pick_summary(
            player_name=pick["player_name"],
            stat_type=pick.get("stat_type") or pick.get("stat_type_raw", "PTS"),
            line=pick.get("line", 0),
            season_avg=pick.get("season_avg") or pick.get("l5_avg") or 0,
            h10_rate=pick.get("h10_rate", 0) or 0,
            badges=pick.get("active_badges") or [],
            opponent=pick.get("opponent", ""),
            is_demon=is_demon,
            is_goblin=is_goblin,
            dvp_rank=pick.get("dvp_rank"),
            dvp_friction=None,
            player_team=pick.get("team", "")
        )
    except Exception as e:
        logger.warning(f"[BOARD_INTEL] Gemini error for {pick.get('player_name')}: {e}")
        return None


# ========== LEGACY COMPATIBILITY ==========
# Keep the old function name for backward compatibility
async def run_vision_intel_enrichment(db: AsyncIOMotorDatabase) -> Dict[str, Any]:
    """Legacy wrapper - calls the new unified enrichment."""
    return await run_board_intelligence_enrichment(db)
