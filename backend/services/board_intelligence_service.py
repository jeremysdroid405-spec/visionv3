"""
Board Intelligence Enrichment Service
======================================
Unified enrichment pipeline for all 3 boards with:
1. AI-Weighted Vision Score calculation
2. Waterfall selection (no player overlap)
3. Full Intel Suite enrichment (same code path for all boards)

PHASE 2: Predictive Market Edge & Game Scripting
- Sharp Sniper Engine: Pinnacle arbitrage for Front Lines
- Anti-Trap Game Script: Blowout filter + DvP veto for Safe Haven
- Usage Spike Detector: Vacated usage boost for War Zone

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

# Phase 2: Predictive Market Edge
from services.sharp_edge_calculator import SharpEdgeCalculator, PRIMARY_EDGE_THRESHOLD, FALLBACK_EDGE_THRESHOLD
from services.game_script_service import GameScriptService, apply_blowout_filter, apply_dvp_veto, apply_shootout_boost
from services.usage_spike_detector import UsageSpikeDetector, apply_usage_spike_boost

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

GEMINI_CONCURRENT_LIMIT = 3  # Reduced from 5 to avoid rate limiting
PLAYERS_PER_BOARD = 10


async def run_board_intelligence_enrichment(db: AsyncIOMotorDatabase) -> Dict[str, Any]:
    """
    Main entry point: Enrich all 3 boards with full intelligence data.
    
    PHASE 2: Predictive Market Edge & Game Scripting
    =================================================
    1. Sharp Sniper (Front Lines): Prioritize +EV plays where Pinnacle is heavily juiced
    2. Anti-Trap (Safe Haven): Filter blowout games + DvP Top-5 veto
    3. Usage Spike (War Zone): Boost top 2 usage leaders when primary player is OUT
    
    Waterfall Selection Order:
    1. Safe Haven: Goblins with L10 HR >= 80%, Vision_Score >= 70, NO blowout risk, NO Top-5 defense
    2. Front Lines: Sharp plays with +EV edge >= 3.5% (fallback to 2.0% if < 10)
    3. War Zone: Demons with HR >= 50%, Vision >= 45, shootout boost + usage spike boost
    
    Each board gets 10 unique players (30 total, no overlap).
    """
    start = datetime.now(timezone.utc)
    logger.info("[BOARD_INTEL] Starting Phase 2: Predictive Market Edge Enrichment...")
    
    cached_board = db[COLL("board_cache", "nba")]
    ferrari_scored = db.ferrari_scored
    now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    
    # ========== STEP 0: Load Ferrari Picks for momentum/whistle/vacuum data ==========
    # Build a lookup map: {player_name|stat_type|line -> ferrari_pick}
    ferrari_picks_map = {}
    try:
        cursor = ferrari_scored.find({}, {"_id": 0})
        ferrari_picks = await cursor.to_list(length=None)
        for fp in ferrari_picks:
            pname = fp.get("player_name", "")
            stat = fp.get("stat_type", "")
            line = fp.get("line", 0)
            key = f"{pname}|{stat}|{line}"
            ferrari_picks_map[key] = fp
        logger.info(f"[BOARD_INTEL] Loaded {len(ferrari_picks_map)} Ferrari picks for enrichment data")
    except Exception as e:
        logger.warning(f"[BOARD_INTEL] Could not load Ferrari picks: {e}")
    
    # ========== STEP 1: Fetch ALL eligible props ==========
    all_props = await _fetch_all_board_eligible_props(cached_board, now_iso)
    logger.info(f"[BOARD_INTEL] Fetched {len(all_props)} total eligible props")
    
    if not all_props:
        return {"total": 0, "enriched": 0, "duration": 0}
    
    # ========== STEP 1.5: Merge Ferrari data (momentum/whistle/vacuum) ==========
    merged_count = 0
    for prop in all_props:
        pname = prop.get("player_name", "")
        stat = prop.get("stat_type") or prop.get("stat_type_raw", "")
        line = prop.get("line", 0)
        key = f"{pname}|{stat}|{line}"
        
        ferrari_pick = ferrari_picks_map.get(key)
        if ferrari_pick:
            # Merge momentum data
            if ferrari_pick.get("momentum_data"):
                prop["momentum_data"] = ferrari_pick["momentum_data"]
                prop["momentum_modifier"] = ferrari_pick.get("momentum_modifier", 0)
                prop["has_momentum_modifier"] = ferrari_pick.get("has_momentum_modifier", False)
            
            # Merge whistle data
            if ferrari_pick.get("crew_chief"):
                prop["crew_chief"] = ferrari_pick["crew_chief"]
                prop["ref_ou_pct"] = ferrari_pick.get("ref_ou_pct")
                prop["ref_ppg"] = ferrari_pick.get("ref_ppg")
                prop["whistle_class"] = ferrari_pick.get("whistle_class")
                prop["whistle_modifier"] = ferrari_pick.get("whistle_modifier", 0)
                prop["has_whistle_modifier"] = ferrari_pick.get("has_whistle_modifier", False)
                prop["point_lift"] = ferrari_pick.get("point_lift", 0)
                prop["lift_label"] = ferrari_pick.get("lift_label")
                prop["lift_type"] = ferrari_pick.get("lift_type")
                prop["foul_rate_diff"] = ferrari_pick.get("foul_rate_diff", 0)
            
            # Merge vacuum data
            if ferrari_pick.get("vacuum_data"):
                prop["vacuum_data"] = ferrari_pick["vacuum_data"]
                prop["vacuum_modifier"] = ferrari_pick.get("vacuum_modifier", 0)
                prop["has_vacuum_modifier"] = ferrari_pick.get("has_vacuum_modifier", False)
            
            merged_count += 1
    
    logger.info(f"[BOARD_INTEL] Merged Ferrari data into {merged_count} props")
    
    # ========== STEP 2: Load Phase 2 Market Edge Data ==========
    # This runs in parallel to minimize latency
    game_scripts, usage_spikes, sharp_calculator = await _load_market_edge_data(db, all_props)
    
    # ========== STEP 3: Calculate Vision Score for each prop ==========
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
    
    # ========== STEP 4: Apply Phase 2 Filters & Boosts ==========
    # 4a. Usage Spike Boost (for War Zone)
    if usage_spikes:
        all_props = apply_usage_spike_boost(all_props, usage_spikes)
    
    # 4b. Shootout Boost (for War Zone)
    if game_scripts:
        all_props = apply_shootout_boost(all_props, game_scripts)
    
    # ========== STEP 5: Waterfall Selection (no overlap) ==========
    used_players: Set[str] = set()
    
    # Board 1: Safe Haven (Anti-Trap: Blowout filter + DvP veto)
    safe_haven_candidates = [p for p in all_props if (
        p.get("is_goblin") is True and
        (p.get("h10_rate") or 0) >= SAFE_HAVEN_THRESHOLDS["min_h10_rate"]
    )]
    
    # Apply Anti-Trap filters
    if game_scripts:
        safe_haven_candidates = apply_blowout_filter(safe_haven_candidates, game_scripts)
    safe_haven_candidates = apply_dvp_veto(safe_haven_candidates, {})  # DVP already in prop
    
    safe_haven = _select_board_players(
        safe_haven_candidates,
        used_players,
        board_name="safe_haven",
        filter_fn=lambda p: p.get("vision_score", 0) >= SAFE_HAVEN_THRESHOLDS["min_vision_score"],
        limit=PLAYERS_PER_BOARD
    )
    
    # Board 2: Front Lines (Sharp Sniper: +EV arbitrage)
    front_lines = await _select_front_lines_sharp(
        all_props,
        used_players,
        sharp_calculator,
        db
    )
    
    # Board 3: War Zone (Usage Spike + Shootout boost already applied)
    war_zone = _select_board_players(
        all_props,
        used_players,
        board_name="war_zone",
        filter_fn=lambda p: (
            p.get("is_demon") is True and
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
    
    # ========== STEP 6: Find prop indices in cached_board ==========
    for pick in all_selected:
        pick["prop_index"] = await _find_prop_index(db, pick)
    
    # ========== STEP 7: Full Intelligence Enrichment (same for all boards) ==========
    intel_calculator = IntelSuiteCalculator(db)
    semaphore = asyncio.Semaphore(GEMINI_CONCURRENT_LIMIT)
    
    tasks = [
        _enrich_pick_full(db, pick, intel_calculator, semaphore) 
        for pick in all_selected
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    enriched = sum(1 for r in results if r is True)
    errors = sum(1 for r in results if isinstance(r, Exception))
    
    # Cleanup
    if sharp_calculator:
        await sharp_calculator.close()
    
    duration = (datetime.now(timezone.utc) - start).total_seconds()
    
    logger.info(f"[BOARD_INTEL] COMPLETE: {enriched}/{len(all_selected)} enriched, {errors} errors, {duration:.1f}s")
    
    return {
        "safe_haven": len(safe_haven),
        "front_lines": len(front_lines),
        "war_zone": len(war_zone),
        "total": len(all_selected),
        "enriched": enriched,
        "errors": errors,
        "duration": round(duration, 2),
        "phase2_stats": {
            "usage_spikes_detected": len(usage_spikes),
            "games_with_scripts": len(game_scripts),
            "blowout_games_filtered": sum(1 for g in game_scripts.values() if g.get("is_blowout_risk")),
            "shootout_games_boosted": sum(1 for g in game_scripts.values() if g.get("is_shootout"))
        }
    }


async def _load_market_edge_data(
    db: AsyncIOMotorDatabase,
    props: List[Dict]
) -> tuple:
    """
    Load Phase 2 market edge data in parallel.
    
    Returns:
        (game_scripts, usage_spikes, sharp_calculator)
    """
    logger.info("[BOARD_INTEL] Loading Phase 2 market edge data...")
    
    game_scripts = {}
    usage_spikes = {}
    sharp_calculator = None
    
    try:
        # Initialize services
        game_script_service = GameScriptService(db)
        usage_detector = UsageSpikeDetector(db)
        sharp_calculator = SharpEdgeCalculator(db)
        
        # Fetch events from odds_cache (more reliable than events_cache)
        # Get unique event_ids from cached board props
        event_ids = set()
        async for player in db[COLL("board_cache", "nba")].find({}, {"props.event_id": 1}):
            for prop in player.get("props", []):
                eid = prop.get("event_id")
                if eid:
                    event_ids.add(eid)
        
        # Build event info from odds_cache
        events = []
        for event_id in event_ids:
            odds_doc = await db[COLL("odds_cache", "nba")].find_one({"event_id": event_id})
            if odds_doc:
                events.append({
                    "id": event_id,
                    "home_team": odds_doc.get("home_team", ""),
                    "away_team": odds_doc.get("away_team", ""),
                    "commence_time": odds_doc.get("commence_time", "")
                })
        
        logger.info(f"[BOARD_INTEL] Found {len(events)} events from odds cache")
        
        # Run in parallel
        results = await asyncio.gather(
            game_script_service.fetch_spreads_and_totals(events),
            usage_detector.detect_usage_spikes(),
            return_exceptions=True
        )
        
        # Unpack results
        if not isinstance(results[0], Exception):
            game_scripts = results[0]
        else:
            logger.warning(f"[BOARD_INTEL] Game scripts failed: {results[0]}")
        
        if not isinstance(results[1], Exception):
            usage_spikes = results[1]
        else:
            logger.warning(f"[BOARD_INTEL] Usage spike detection failed: {results[1]}")
        
        # Close game script service
        await game_script_service.close()
        
        logger.info(f"[BOARD_INTEL] Phase 2 data loaded: {len(game_scripts)} games, {len(usage_spikes)} usage spikes")
        
    except Exception as e:
        logger.error(f"[BOARD_INTEL] Error loading Phase 2 data: {e}")
    
    return game_scripts, usage_spikes, sharp_calculator


async def _select_front_lines_sharp(
    all_props: List[Dict],
    used_players: Set[str],
    sharp_calculator: SharpEdgeCalculator,
    db: AsyncIOMotorDatabase
) -> List[Dict]:
    """
    Select Front Lines using Sharp Sniper logic.
    
    Prioritizes +EV plays where Pinnacle/DraftKings is heavily juiced.
    - Primary threshold: +3.5% edge
    - Fallback threshold: +2.0% if < 10 players
    
    Uses sharp_books data cached during sync (no additional API calls).
    """
    logger.info("[BOARD_INTEL] Running Sharp Sniper for Front Lines...")
    
    # Filter candidates (not already used, meets basic HR threshold)
    candidates = [
        p for p in all_props 
        if p.get("player_name") not in used_players and
        (p.get("h10_rate") or 0) >= FRONT_LINES_THRESHOLDS["min_h10_rate"]
    ]
    
    selected = []
    
    try:
        if sharp_calculator:
            # Get unique event_ids from candidates
            event_ids = set()
            for prop in candidates:
                eid = prop.get("game_id") or prop.get("event_id")
                if eid:
                    event_ids.add(eid)
            
            # Build events list from cached data
            events = []
            for event_id in event_ids:
                # Check if we have sharp_books cached for this event
                cached = await db[COLL("odds_cache", "nba")].find_one({
                    "event_id": event_id,
                    "source": "sharp_books"
                })
                if cached:
                    events.append({
                        "id": event_id,
                        "home_team": cached.get("home_team", ""),
                        "away_team": cached.get("away_team", "")
                    })
            
            logger.info(f"[SHARP_SNIPER] {len(events)} events with sharp_books cached, {len(candidates)} candidates")
            
            # Calculate sharp edges using cached data
            sharp_edges = await sharp_calculator.calculate_sharp_edges_for_props(candidates, events)
            logger.info(f"[SHARP_SNIPER] Calculated {len(sharp_edges)} sharp edges")
            
            # Attach sharp edge data to candidates
            matched_count = 0
            for prop in candidates:
                player_name = prop.get("player_name", "")
                stat_type = prop.get("stat_type") or prop.get("stat_type_extracted") or "PTS"
                line = prop.get("line", 0)
                
                key = f"{player_name}|{stat_type}|{line}"
                if key in sharp_edges:
                    prop["sharp_edge_data"] = sharp_edges[key]
                    prop["sharp_edge"] = sharp_edges[key].get("sharp_edge", 0)
                    matched_count += 1
            
            logger.info(f"[SHARP_SNIPER] Matched {matched_count} props to Pinnacle lines")
            
            # Try primary threshold first (+3.5%)
            primary_candidates = [
                p for p in candidates 
                if p.get("sharp_edge", 0) >= PRIMARY_EDGE_THRESHOLD
            ]
            
            if len(primary_candidates) >= PLAYERS_PER_BOARD:
                # Enough +EV plays, select top 10 by sharp edge
                primary_candidates.sort(key=lambda x: x.get("sharp_edge", 0), reverse=True)
                selected = _select_board_players(
                    primary_candidates,
                    used_players,
                    board_name="front_lines",
                    filter_fn=lambda p: True,  # Already filtered
                    limit=PLAYERS_PER_BOARD
                )
                logger.info(f"[SHARP_SNIPER] Primary selection: {len(selected)} players with +{PRIMARY_EDGE_THRESHOLD}%+ edge")
            else:
                # Fallback to +2.0% threshold
                fallback_candidates = [
                    p for p in candidates 
                    if p.get("sharp_edge", 0) >= FALLBACK_EDGE_THRESHOLD
                ]
                fallback_candidates.sort(key=lambda x: x.get("sharp_edge", 0), reverse=True)
                
                # Fill with sharp plays first, then standard vision score
                selected = _select_board_players(
                    fallback_candidates,
                    used_players,
                    board_name="front_lines",
                    filter_fn=lambda p: True,
                    limit=PLAYERS_PER_BOARD
                )
                logger.info(f"[SHARP_SNIPER] Fallback selection: {len(selected)} players with +{FALLBACK_EDGE_THRESHOLD}%+ edge")
    
    except Exception as e:
        logger.error(f"[SHARP_SNIPER] Error in sharp selection: {e}")
    
    # If sharp selection didn't fill the board, fall back to standard vision score
    if len(selected) < PLAYERS_PER_BOARD:
        remaining_needed = PLAYERS_PER_BOARD - len(selected)
        logger.info(f"[SHARP_SNIPER] Need {remaining_needed} more players, falling back to vision score")
        
        # Standard selection for remaining slots
        additional = _select_board_players(
            candidates,
            used_players,
            board_name="front_lines",
            filter_fn=lambda p: p.get("vision_score", 0) >= FRONT_LINES_THRESHOLDS["min_vision_score"],
            limit=remaining_needed
        )
        selected.extend(additional)
    
    return selected


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
            "opponent": 1,  # Opponent is at player level, not prop level
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
            "dvp_rank": "$props.dvp_rank",
            "game_id": "$props.event_id",
            "event_id": "$props.event_id",
            "commence_time": "$props.commence_time",
            "is_demon": "$props.is_demon",
            "is_goblin": "$props.is_goblin",
            "active_badges": "$props.active_badges",
            "context_badges": 1,  # Player-level field, not prop-level
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
    player = await db[COLL("board_cache", "nba")].find_one(
        {"player_name": pick["player_name"]},
        {"_id": 0, "props": 1}
    )
    if not player:
        logger.warning(f"[BOARD_INTEL] Player {pick['player_name']} not found in dg_cached_board")
        return -1
    
    stat = pick.get("stat_type") or pick.get("stat_type_raw", "")
    line = pick.get("line", 0)
    
    # First try exact match
    for i, prop in enumerate(player.get("props", [])):
        prop_stat = prop.get("stat_type_extracted") or prop.get("stat_type", "")
        prop_line = prop.get("line", 0)
        if prop_stat == stat and abs(prop_line - line) < 0.1:
            return i
    
    # If no exact match, find the prop with matching stat_type and closest line
    best_idx = -1
    best_diff = float('inf')
    for i, prop in enumerate(player.get("props", [])):
        prop_stat = prop.get("stat_type_extracted") or prop.get("stat_type", "")
        prop_line = prop.get("line", 0)
        if prop_stat == stat:
            diff = abs(prop_line - line)
            if diff < best_diff:
                best_diff = diff
                best_idx = i
    
    if best_idx >= 0:
        logger.debug(f"[BOARD_INTEL] Fuzzy match for {pick['player_name']} {stat}: wanted {line}, found index {best_idx}")
        return best_idx
    
    logger.warning(f"[BOARD_INTEL] No matching prop for {pick['player_name']} {stat}@{line}")
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
            
            # Phase 2: Add market edge data to intel suite
            if pick.get("sharp_edge_data"):
                intel_suite["sharp_edge"] = pick["sharp_edge_data"]
            if pick.get("has_usage_spike"):
                intel_suite["usage_spike"] = pick.get("usage_spike_data")
            if pick.get("is_shootout"):
                intel_suite["shootout"] = {
                    "is_shootout": True,
                    "total": pick.get("shootout_total"),
                    "spread": pick.get("shootout_spread")
                }
            
            # Extract DVP data from intel_suite for AI summary
            matchup_dvp = intel_suite.get("matchup_dvp", {})
            dvp_rank = matchup_dvp.get("rank")  # Key is "rank" not "dvp_rank"
            dvp_friction = matchup_dvp.get("friction_level")
            
            # ========== AI VISION SUMMARY (Gemini) ==========
            # Pass the DVP data we just calculated to the AI summary generator
            ai_summary = await _generate_ai_summary(pick, dvp_rank=dvp_rank, dvp_friction=dvp_friction)
            
            # Merge AI summary into vision_insight
            if intel_suite.get("vision_insight"):
                intel_suite["vision_insight"]["ai_summary"] = ai_summary
                intel_suite["vision_insight"]["status"] = "ready" if ai_summary else "pending"
        
        # ========== UPDATE MONGODB ==========
        # Build update dict with all enrichment data
        update_data = {
            f"props.{idx}.vision_summary": ai_summary,
            f"props.{idx}.intel_suite": intel_suite,
            f"props.{idx}.is_vision_enriched": True,
            f"props.{idx}.vision_enriched_at": datetime.now(timezone.utc).isoformat(),
            f"props.{idx}.board": board,
            f"props.{idx}.vision_score": pick.get("vision_score", 0)
        }
        
        # Add momentum data from Ferrari picks if available
        if pick.get("momentum_data"):
            update_data[f"props.{idx}.momentum_data"] = pick["momentum_data"]
            update_data[f"props.{idx}.momentum_modifier"] = pick.get("momentum_modifier", 0)
            update_data[f"props.{idx}.has_momentum_modifier"] = pick.get("has_momentum_modifier", False)
        
        # Add whistle/officiating data from Ferrari picks if available
        if pick.get("crew_chief"):
            update_data[f"props.{idx}.crew_chief"] = pick["crew_chief"]
            update_data[f"props.{idx}.ref_ou_pct"] = pick.get("ref_ou_pct")
            update_data[f"props.{idx}.ref_ppg"] = pick.get("ref_ppg")
            update_data[f"props.{idx}.whistle_class"] = pick.get("whistle_class")
            update_data[f"props.{idx}.whistle_modifier"] = pick.get("whistle_modifier", 0)
            update_data[f"props.{idx}.has_whistle_modifier"] = pick.get("has_whistle_modifier", False)
            update_data[f"props.{idx}.point_lift"] = pick.get("point_lift", 0)
            update_data[f"props.{idx}.lift_label"] = pick.get("lift_label")
            update_data[f"props.{idx}.lift_type"] = pick.get("lift_type")
            update_data[f"props.{idx}.foul_rate_diff"] = pick.get("foul_rate_diff", 0)
        
        # Add vacuum data from Ferrari picks if available
        if pick.get("vacuum_data"):
            update_data[f"props.{idx}.vacuum_data"] = pick["vacuum_data"]
            update_data[f"props.{idx}.vacuum_modifier"] = pick.get("vacuum_modifier", 0)
            update_data[f"props.{idx}.has_vacuum_modifier"] = pick.get("has_vacuum_modifier", False)
        
        # Phase 2: Add market edge data at prop level for easy querying
        if pick.get("sharp_edge_data"):
            update_data[f"props.{idx}.sharp_edge_data"] = pick["sharp_edge_data"]
            update_data[f"props.{idx}.sharp_edge"] = pick.get("sharp_edge", 0)
        if pick.get("has_usage_spike"):
            update_data[f"props.{idx}.has_usage_spike"] = True
            update_data[f"props.{idx}.usage_spike_data"] = pick.get("usage_spike_data")
        if pick.get("is_shootout"):
            update_data[f"props.{idx}.is_shootout"] = True
            update_data[f"props.{idx}.shootout_total"] = pick.get("shootout_total")
        
        update_result = await db[COLL("board_cache", "nba")].update_one(
            {"player_name": player_name},
            {"$set": update_data}
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


async def _generate_ai_summary(pick: Dict, dvp_rank: int = None, dvp_friction: str = None) -> Optional[str]:
    """
    AI summary is now generated by vision_intel_service.py in ferrari_tier_service.py
    This function is disabled to prevent duplicate Gemini API calls.
    """
    # Vision Intel summaries are generated during tier building in ferrari_tier_service.py
    # The 'vision_intel' field will be populated there
    return None


# ========== LEGACY COMPATIBILITY ==========
# Keep the old function name for backward compatibility
async def run_vision_intel_enrichment(db: AsyncIOMotorDatabase) -> Dict[str, Any]:
    """Legacy wrapper - calls the new unified enrichment."""
    return await run_board_intelligence_enrichment(db)
