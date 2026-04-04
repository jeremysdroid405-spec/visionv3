"""
Optimized Sync Engine
=====================
High-performance sync engine that:
1. Pre-caches ALL global data (standings, referees, momentum) ONCE at sync start
2. Uses async batching with asyncio.gather() for concurrent processing
3. Enriches ALL picks with complete data in a single pass
4. Returns unified JSON payload with all intel data

Target: Complete sync in under 5 seconds

Author: PropVision AI
Version: 1.0.0
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Batch processing limits
BATCH_SIZE = 10  # Process 10 picks concurrently
GEMINI_CONCURRENT_LIMIT = 5  # Max concurrent Gemini calls


@dataclass
class GlobalSyncCache:
    """
    Holds ALL global data fetched once at sync start.
    Passed down to all enrichment functions to avoid redundant API calls.
    """
    # Standings data
    standings: Dict[str, Dict] = field(default_factory=dict)
    
    # Referee/Officiating data for today's games
    referee_assignments: Dict[str, Dict] = field(default_factory=dict)  # {game_id: ref_info}
    referee_by_teams: Dict[str, Dict] = field(default_factory=dict)  # {team_abbr: ref_info}
    
    # Defensive Momentum cache (all teams)
    momentum_cache: Dict[str, Dict] = field(default_factory=dict)  # {team: profile}
    
    # Usage Vacuum cache (active vacuums)
    vacuum_alerts: List[Dict] = field(default_factory=list)
    vacuum_beneficiaries: Dict[str, Dict] = field(default_factory=dict)  # {player_name: vacuum_data}
    
    # Timestamps
    fetched_at: Optional[datetime] = None
    
    def is_valid(self) -> bool:
        """Check if cache is populated."""
        return self.fetched_at is not None


async def fetch_global_cache(db) -> GlobalSyncCache:
    """
    Fetch ALL global data in parallel at sync start.
    This is called ONCE and the cache is passed to all subsequent functions.
    """
    cache = GlobalSyncCache()
    start = datetime.now(timezone.utc)
    
    logger.info("[SYNC_CACHE] Fetching all global data...")
    
    try:
        # Fetch all global data concurrently
        standings_task = _fetch_standings_cached(db)
        refs_task = _fetch_referee_assignments_cached(db)
        momentum_task = _fetch_momentum_cached(db)
        vacuum_task = _fetch_vacuum_cached(db)
        
        results = await asyncio.gather(
            standings_task,
            refs_task,
            momentum_task,
            vacuum_task,
            return_exceptions=True
        )
        
        # Unpack results
        if not isinstance(results[0], Exception):
            cache.standings = results[0]
        
        if not isinstance(results[1], Exception):
            cache.referee_assignments, cache.referee_by_teams = results[1]
        
        if not isinstance(results[2], Exception):
            cache.momentum_cache = results[2]
        
        if not isinstance(results[3], Exception):
            cache.vacuum_alerts, cache.vacuum_beneficiaries = results[3]
        
        cache.fetched_at = datetime.now(timezone.utc)
        
        duration = (datetime.now(timezone.utc) - start).total_seconds()
        logger.info(f"[SYNC_CACHE] Global cache populated in {duration:.2f}s")
        logger.info(f"  - Standings: {len(cache.standings)} teams")
        logger.info(f"  - Referees: {len(cache.referee_by_teams)} games")
        logger.info(f"  - Momentum: {len(cache.momentum_cache)} teams")
        logger.info(f"  - Vacuums: {len(cache.vacuum_alerts)} active")
        
    except Exception as e:
        logger.error(f"[SYNC_CACHE] Error fetching global cache: {e}")
        import traceback
        logger.error(traceback.format_exc())
    
    return cache


async def _fetch_standings_cached(db) -> Dict[str, Dict]:
    """Fetch standings data."""
    try:
        from services.standings_service import StandingsService
        return await StandingsService.get_standings()
    except Exception as e:
        logger.warning(f"[SYNC_CACHE] Standings fetch failed: {e}")
        return {}


async def _fetch_referee_assignments_cached(db) -> Tuple[Dict, Dict]:
    """Fetch today's referee assignments."""
    try:
        from services.referee_scraper_service import get_referee_service
        ref_service = get_referee_service(db)
        
        # Get today's assignments
        assignments = await ref_service.get_todays_assignments()
        
        # Build lookup maps
        by_game = {}
        by_team = {}
        
        for assignment in assignments:
            game_id = assignment.get("game_id")
            if game_id:
                by_game[game_id] = assignment
            
            # Map both teams to this assignment
            home = assignment.get("home_team")
            away = assignment.get("away_team")
            if home:
                by_team[home] = assignment
            if away:
                by_team[away] = assignment
        
        return by_game, by_team
        
    except Exception as e:
        logger.warning(f"[SYNC_CACHE] Referee fetch failed: {e}")
        return {}, {}


async def _fetch_momentum_cached(db) -> Dict[str, Dict]:
    """Fetch defensive momentum cache for all teams."""
    try:
        from services.defensive_momentum_service import get_momentum_service
        momentum_service = get_momentum_service(db)
        await momentum_service.ensure_cache()
        
        # Get all PTS momentum profiles
        profiles = momentum_service.get_all_team_momentum("PTS")
        return {p["team"]: p for p in profiles}
        
    except Exception as e:
        logger.warning(f"[SYNC_CACHE] Momentum fetch failed: {e}")
        return {}


async def _fetch_vacuum_cached(db) -> Tuple[List, Dict]:
    """Fetch active usage vacuums."""
    try:
        from services.injury_vacuum_service import get_vacuum_service
        vacuum_service = get_vacuum_service(db)
        
        # Get active vacuums
        vacuums = await vacuum_service.get_active_vacuums()
        
        # Build beneficiary lookup
        beneficiaries = {}
        for vacuum in vacuums:
            for ben in vacuum.get("beneficiaries", []):
                player_name = ben.get("name")
                if player_name:
                    beneficiaries[player_name] = {
                        "injured_player": vacuum.get("injured_player"),
                        "injured_team": vacuum.get("team"),
                        "injured_usage": vacuum.get("usage_rate"),
                        "beneficiary_rank": ben.get("rank"),
                        "usage_bump": ben.get("usage_bump"),
                        "modifier": ben.get("modifier", 15 if ben.get("rank") == "primary" else 10)
                    }
        
        return vacuums, beneficiaries
        
    except Exception as e:
        logger.warning(f"[SYNC_CACHE] Vacuum fetch failed: {e}")
        return [], {}


def enrich_pick_with_cache(
    pick: Dict[str, Any],
    cache: GlobalSyncCache
) -> Dict[str, Any]:
    """
    Enrich a single pick with all cached global data.
    This is a FAST, synchronous operation using pre-fetched data.
    """
    player_name = pick.get("player_name", "")
    opponent = pick.get("opponent") or pick.get("opponent_abbr", "")
    team = pick.get("team", "")
    stat_type = pick.get("stat_type", "PTS")
    
    # 1. Add Standings/Blowout Risk
    if cache.standings:
        team_standings = cache.standings.get(team, {})
        opp_standings = cache.standings.get(opponent, {})
        
        if team_standings and opp_standings:
            team_record = f"{team_standings.get('wins', 0)}-{team_standings.get('losses', 0)}"
            opp_record = f"{opp_standings.get('wins', 0)}-{opp_standings.get('losses', 0)}"
            win_diff = abs(team_standings.get('win_pct', 0.5) - opp_standings.get('win_pct', 0.5))
            
            pick["team_record"] = team_record
            pick["opponent_record"] = opp_record
            
            if win_diff > 0.20:
                pick["blowout_risk"] = "HIGH"
            elif win_diff > 0.15:
                pick["blowout_risk"] = "MEDIUM"
            else:
                pick["blowout_risk"] = "LOW"
    
    # 2. Add Referee/Officiating Data
    if cache.referee_by_teams:
        ref_info = cache.referee_by_teams.get(team) or cache.referee_by_teams.get(opponent)
        if ref_info:
            pick["crew_chief"] = ref_info.get("crew_chief")
            pick["ref_ou_pct"] = ref_info.get("ou_pct")
            pick["ref_ppg"] = ref_info.get("ppg")
            pick["whistle_class"] = ref_info.get("whistle_class", "neutral")
            
            # Calculate whistle modifier
            whistle_class = pick["whistle_class"]
            stat_upper = stat_type.upper()
            
            if whistle_class == "high" and stat_upper in ["PTS", "FTM", "POINTS"]:
                pick["whistle_modifier"] = 15.0
                pick["has_whistle_modifier"] = True
                pick["point_lift"] = ref_info.get("ppg", 115.5) - 115.5  # Above avg
                pick["lift_label"] = f"+{pick['point_lift']:.1f} Projected PTS Boost"
                pick["lift_type"] = "tailwind"
            elif whistle_class == "low" and stat_upper in ["PTS", "FTM", "POINTS"]:
                pick["whistle_modifier"] = -10.0
                pick["has_whistle_modifier"] = True
                pick["point_lift"] = ref_info.get("ppg", 115.5) - 115.5
                pick["lift_label"] = f"{pick['point_lift']:.1f} Projected PTS Impact"
                pick["lift_type"] = "headwind"
            else:
                pick["whistle_modifier"] = 0.0
                pick["has_whistle_modifier"] = False
                pick["point_lift"] = 0
                pick["lift_label"] = "Neutral officiating impact"
                pick["lift_type"] = "neutral"
            
            pick["foul_rate_diff"] = ref_info.get("foul_rate_diff", 0)
    
    # 3. Add Defensive Momentum Data
    if cache.momentum_cache and opponent:
        momentum_profile = cache.momentum_cache.get(opponent)
        if momentum_profile:
            # Add proxy info based on stat type
            from services.defensive_momentum_service import STAT_PROXY_MAP, DEFAULT_PROXY
            proxy_config = STAT_PROXY_MAP.get(stat_type.upper(), DEFAULT_PROXY)
            
            pick["momentum_data"] = {
                **momentum_profile,
                "stat_type": stat_type,
                "proxy_type": proxy_config["proxy"],
                "proxy_label": proxy_config["label"],
                "proxy_description": proxy_config["description"] if stat_type.upper() != "PTS" else None,
                "using_proxy": stat_type.upper() != "PTS"
            }
            
            # Calculate modifier
            composite = momentum_profile.get("composite_rank", 15)
            if composite <= 5:
                pick["momentum_modifier"] = -15.0
                pick["has_momentum_modifier"] = True
            elif composite >= 25:
                pick["momentum_modifier"] = 15.0
                pick["has_momentum_modifier"] = True
            else:
                pick["momentum_modifier"] = 0.0
                pick["has_momentum_modifier"] = False
    
    # 4. Add Usage Vacuum Data
    if cache.vacuum_beneficiaries and player_name:
        vacuum_data = cache.vacuum_beneficiaries.get(player_name)
        if vacuum_data:
            pick["vacuum_data"] = vacuum_data
            pick["vacuum_modifier"] = vacuum_data.get("modifier", 0)
            pick["has_vacuum_modifier"] = True
    
    return pick


async def enrich_picks_batch(
    picks: List[Dict[str, Any]],
    cache: GlobalSyncCache,
    db=None,
    include_ai_summary: bool = True
) -> List[Dict[str, Any]]:
    """
    Enrich a batch of picks concurrently.
    
    1. First pass: Add all cached data (sync, fast)
    2. Second pass: Generate AI summaries (async, batched)
    """
    start = datetime.now(timezone.utc)
    
    # Pass 1: Enrich with cached data (fast, synchronous)
    for pick in picks:
        enrich_pick_with_cache(pick, cache)
    
    # Pass 2: Generate AI summaries if needed
    if include_ai_summary and db:
        await _batch_generate_summaries(picks, db)
    
    duration = (datetime.now(timezone.utc) - start).total_seconds()
    logger.info(f"[SYNC_ENGINE] Enriched {len(picks)} picks in {duration:.2f}s")
    
    return picks


async def _batch_generate_summaries(picks: List[Dict], db) -> None:
    """
    Generate AI summaries for picks in batches.
    Uses semaphore to limit concurrent Gemini calls.
    """
    from services.vision_summary_service import generate_vision_summary
    
    semaphore = asyncio.Semaphore(GEMINI_CONCURRENT_LIMIT)
    
    async def generate_with_limit(pick: Dict) -> None:
        async with semaphore:
            try:
                if not pick.get("vision_summary"):
                    summary = await generate_vision_summary(pick)
                    pick["vision_summary"] = summary
            except Exception as e:
                logger.warning(f"[SYNC_ENGINE] AI summary failed for {pick.get('player_name')}: {e}")
    
    # Process in batches
    for i in range(0, len(picks), BATCH_SIZE):
        batch = picks[i:i + BATCH_SIZE]
        tasks = [generate_with_limit(pick) for pick in batch]
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_optimized_sync(db) -> Dict[str, Any]:
    """
    Run the full optimized sync pipeline.
    
    Pipeline:
    1. Fetch ALL global data (standings, refs, momentum, vacuums) in parallel
    2. Run Ferrari pipeline to build scored picks
    3. Enrich picks with cached global data (fast, sync)
    4. Generate AI summaries in batches (rate-limited)
    5. Update dg_cached_board with enriched intel_suite data
    
    Returns complete payload with all picks enriched.
    """
    start = datetime.now(timezone.utc)
    
    logger.info("[OPTIMIZED_SYNC] Starting unified pipeline...")
    
    # Step 1: Fetch global cache ONCE (parallel fetch)
    cache = await fetch_global_cache(db)
    
    # Step 2: Run Ferrari pipeline (builds ferrari_scored, ferrari_safe_haven, etc.)
    from services.ferrari_tier_service import get_ferrari_tier_service
    ferrari_service = get_ferrari_tier_service(db)
    
    # Pass the pre-fetched cache to Ferrari for use during scoring
    ferrari_result = await ferrari_service.build_ferrari_tiers(start)
    
    if not ferrari_result.get("success"):
        logger.error(f"[OPTIMIZED_SYNC] Ferrari pipeline failed: {ferrari_result.get('error')}")
        return {"success": False, "error": "Ferrari pipeline failed", "details": ferrari_result}
    
    logger.info(f"[OPTIMIZED_SYNC] Ferrari complete: {ferrari_result.get('output', {}).get('total', 0)} picks")
    
    # Step 3: Collect all picks from Ferrari collections and enrich with cache
    all_picks = []
    boards = {}
    
    for board_name in ["safe_haven", "front_lines", "war_zone"]:
        # Get picks from getter methods (already in memory)
        if board_name == "safe_haven":
            board_data = await ferrari_service.get_safe_haven(10)
        elif board_name == "front_lines":
            board_data = await ferrari_service.get_front_lines(10)
        else:
            board_data = await ferrari_service.get_war_zone(10)
        
        picks = board_data.get("picks", [])
        
        # Enrich each pick with cached data (momentum, vacuum, whistle already in Ferrari)
        for pick in picks:
            pick["board"] = board_name
            # Add any missing cache data
            enrich_pick_with_cache(pick, cache)
        
        all_picks.extend(picks)
        boards[board_name] = {
            "picks": picks,
            "count": len(picks)
        }
    
    logger.info(f"[OPTIMIZED_SYNC] Collected {len(all_picks)} picks for AI summary generation")
    
    # Step 4: Generate AI summaries in batch (with rate limiting)
    await _batch_generate_summaries(all_picks, db)
    
    # Step 5: Update dg_cached_board with enriched data
    await _persist_enriched_picks(db, all_picks, cache)
    
    # Step 6: Update Ferrari tier collections with vision summaries
    await _update_tier_collections_with_summaries(db, all_picks)
    
    duration = (datetime.now(timezone.utc) - start).total_seconds()
    
    logger.info(f"[OPTIMIZED_SYNC] Pipeline complete in {duration:.2f}s")
    
    return {
        "success": True,
        "safe_haven": boards.get("safe_haven", {}),
        "front_lines": boards.get("front_lines", {}),
        "war_zone": boards.get("war_zone", {}),
        "total_picks": len(all_picks),
        "sync_duration": round(duration, 2),
        "ferrari_stats": ferrari_result.get("output", {}),
        "cache_stats": {
            "standings": len(cache.standings),
            "referees": len(cache.referee_by_teams),
            "momentum": len(cache.momentum_cache),
            "vacuums": len(cache.vacuum_alerts)
        }
    }


async def _update_tier_collections_with_summaries(db, picks: List[Dict]) -> None:
    """
    Update Ferrari tier collections (ferrari_safe_haven, ferrari_front_lines, ferrari_war_zone)
    with the AI-generated vision_summary field.
    """
    if not picks:
        return
    
    # Group picks by tier/board
    tier_map = {
        "safe_haven": [],
        "front_lines": [],
        "war_zone": []
    }
    
    for pick in picks:
        board = pick.get("board") or pick.get("tier", "")
        if board in tier_map:
            tier_map[board].append(pick)
    
    # Update each tier collection
    updated_count = 0
    for tier_name, tier_picks in tier_map.items():
        collection_name = f"ferrari_{tier_name}"
        collection = db[collection_name]
        
        for pick in tier_picks:
            vision_summary = pick.get("vision_summary")
            if not vision_summary:
                continue
            
            # Update the pick in the collection
            result = await collection.update_one(
                {
                    "player_name": pick.get("player_name"),
                    "stat_type": pick.get("stat_type"),
                    "line": pick.get("line")
                },
                {
                    "$set": {
                        "vision_summary": vision_summary,
                        "intel_suite.vision_summary": vision_summary
                    }
                }
            )
            if result.modified_count > 0:
                updated_count += 1
    
    logger.info(f"[OPTIMIZED_SYNC] Updated {updated_count} picks in tier collections with AI summaries")


async def _persist_enriched_picks(db, picks: List[Dict], cache: GlobalSyncCache) -> None:
    """
    Persist enriched pick data back to dg_cached_board.
    
    Strategy: Update ALL props for each player with their game-level enrichment data
    (momentum, whistle, vacuum). This ensures consistent data across all prop lines.
    
    Also updates player-level fields for fast access.
    """
    if not picks:
        return
    
    logger.info(f"[OPTIMIZED_SYNC] Persisting enrichment data for {len(picks)} picks")
    
    # Group picks by player to avoid duplicate updates
    players_processed = set()
    persisted_count = 0
    
    for pick in picks:
        player_name = pick.get("player_name")
        if not player_name or player_name in players_processed:
            continue
        
        players_processed.add(player_name)
        
        try:
            # Get the player document
            player_doc = await db.dg_cached_board.find_one(
                {"player_name": player_name},
                {"_id": 0, "props": 1}
            )
            
            if not player_doc or not player_doc.get("props"):
                logger.debug(f"[PERSIST] Player {player_name} not in dg_cached_board, skipping")
                continue
            
            # Build player-level update with shared enrichment data
            player_update = {
                "enriched_at": datetime.now(timezone.utc).isoformat(),
                "board_member": pick.get("board"),
            }
            
            # Add momentum data at player level
            if pick.get("momentum_data"):
                player_update["momentum_data"] = pick["momentum_data"]
                player_update["momentum_modifier"] = pick.get("momentum_modifier", 0)
            
            # Add whistle data at player level
            if pick.get("crew_chief"):
                player_update["crew_chief"] = pick["crew_chief"]
                player_update["ref_ou_pct"] = pick.get("ref_ou_pct")
                player_update["ref_ppg"] = pick.get("ref_ppg")
                player_update["whistle_class"] = pick.get("whistle_class")
                player_update["whistle_modifier"] = pick.get("whistle_modifier", 0)
                player_update["point_lift"] = pick.get("point_lift", 0)
                player_update["lift_label"] = pick.get("lift_label")
                player_update["lift_type"] = pick.get("lift_type")
            
            # Add vacuum data at player level
            if pick.get("vacuum_data"):
                player_update["vacuum_data"] = pick["vacuum_data"]
                player_update["vacuum_modifier"] = pick.get("vacuum_modifier", 0)
            
            # Build prop-level updates for ALL props of this player
            props_update = {}
            num_props = len(player_doc.get("props", []))
            
            for idx in range(num_props):
                # Copy enrichment data to each prop
                if pick.get("momentum_data"):
                    props_update[f"props.{idx}.momentum_data"] = pick["momentum_data"]
                    props_update[f"props.{idx}.momentum_modifier"] = pick.get("momentum_modifier", 0)
                    props_update[f"props.{idx}.has_momentum_modifier"] = pick.get("has_momentum_modifier", False)
                
                if pick.get("crew_chief"):
                    props_update[f"props.{idx}.crew_chief"] = pick["crew_chief"]
                    props_update[f"props.{idx}.ref_ou_pct"] = pick.get("ref_ou_pct")
                    props_update[f"props.{idx}.ref_ppg"] = pick.get("ref_ppg")
                    props_update[f"props.{idx}.whistle_class"] = pick.get("whistle_class")
                    props_update[f"props.{idx}.whistle_modifier"] = pick.get("whistle_modifier", 0)
                    props_update[f"props.{idx}.has_whistle_modifier"] = pick.get("has_whistle_modifier", False)
                    props_update[f"props.{idx}.point_lift"] = pick.get("point_lift", 0)
                    props_update[f"props.{idx}.lift_label"] = pick.get("lift_label")
                    props_update[f"props.{idx}.lift_type"] = pick.get("lift_type")
                
                if pick.get("vacuum_data"):
                    props_update[f"props.{idx}.vacuum_data"] = pick["vacuum_data"]
                    props_update[f"props.{idx}.vacuum_modifier"] = pick.get("vacuum_modifier", 0)
                    props_update[f"props.{idx}.has_vacuum_modifier"] = pick.get("has_vacuum_modifier", False)
                
                # Build comprehensive intel_suite for each prop
                # This includes all fields the frontend Vision Intel Suite expects
                opponent = pick.get("opponent") or pick.get("opponent_abbr")
                team = pick.get("team")
                stat_type = pick.get("stat_type", "PTS")
                
                # Get blowout risk data
                blowout_level = pick.get("blowout_risk", "UNKNOWN")
                blowout_data = {
                    "risk_level": blowout_level,
                    "player_team_record": pick.get("team_record", ""),
                    "opponent_team_record": pick.get("opponent_record", ""),
                    "warning": f"Blowout risk {blowout_level}" if blowout_level in ["HIGH", "MEDIUM"] else None
                }
                
                # Build matchup_dvp from momentum_data if available
                momentum = pick.get("momentum_data", {})
                dvp_rank = momentum.get("composite_rank", 15) if momentum else 15
                friction_level = "Low" if dvp_rank <= 10 else "Medium" if dvp_rank <= 20 else "High"
                friction_color = "green" if dvp_rank <= 10 else "yellow" if dvp_rank <= 20 else "red"
                
                matchup_dvp = {
                    "display": f"vs {opponent}",
                    "opponent": opponent,
                    "opponent_abbr": opponent,
                    "friction_level": friction_level,
                    "friction_label": f"Rank #{int(dvp_rank)}" if dvp_rank else "Unknown",
                    "color": friction_color,
                    "dvp_rank": dvp_rank,
                    "stat_type": stat_type
                }
                
                # Build pace_delta 
                pace_delta_val = 0
                pace_delta = {
                    "display": f"+{abs(pace_delta_val):.1f}" if pace_delta_val > 0 else f"{pace_delta_val:.1f}",
                    "possessions": pace_delta_val,
                    "tempo_label": "Neutral Pace",
                    "expected_game_pace": "98.0",
                    "team_pace": 98.0,
                    "opp_pace": 98.0,
                    "league_avg": 98.0
                }
                
                # Build stability_index from hit rates
                l10_rate = pick.get("l10_rate", 0) or 0
                l5_rate = pick.get("l5_rate", 0) or 0
                stability_score = int((l10_rate + l5_rate) / 2) if (l10_rate or l5_rate) else 50
                
                stability_index = {
                    "display": f"{stability_score}%",
                    "score": stability_score,
                    "consistency": "Consistent" if stability_score >= 70 else "Variable" if stability_score >= 50 else "Volatile",
                    "std_dev": None
                }
                
                # Build usage_ripple from vacuum_data
                vacuum = pick.get("vacuum_data")
                usage_ripple = {
                    "display": "Elevated Usage" if vacuum else "Standard Volume",
                    "reasoning": vacuum.get("reason", "Based on team role") if vacuum else "Based on team role and recent minutes",
                    "bump_percent": int(vacuum.get("usage_bump", 0)) if vacuum else 0,
                    "shift_label": f"+{int(vacuum.get('usage_bump', 0))}% Usage" if vacuum else "Normal",
                    "injuries_affecting": [vacuum.get("injured_player")] if vacuum and vacuum.get("injured_player") else []
                }
                
                # Build context_badges from available data
                context_badges = []
                if blowout_level == "HIGH":
                    context_badges.append("blowout_risk")
                if pick.get("trap_risk"):
                    context_badges.append("trap_risk")
                if pick.get("sharp_movement"):
                    context_badges.append("sharp_movement")
                if vacuum:
                    context_badges.append("usage_boost")
                if momentum and momentum.get("is_weak"):
                    context_badges.append("soft_matchup")
                
                intel_suite = {
                    "momentum_data": pick.get("momentum_data"),
                    "whistle_data": {
                        "crew_chief": pick.get("crew_chief"),
                        "ref_ou_pct": pick.get("ref_ou_pct"),
                        "ref_ppg": pick.get("ref_ppg"),
                        "whistle_class": pick.get("whistle_class"),
                        "point_lift": pick.get("point_lift"),
                        "lift_label": pick.get("lift_label"),
                        "lift_type": pick.get("lift_type")
                    } if pick.get("crew_chief") else None,
                    "vacuum_data": pick.get("vacuum_data"),
                    "board": pick.get("board"),
                    "ferrari_power_score": pick.get("ferrari_power_score"),
                    # NEW: Full Vision Intel Suite fields
                    "blowout_risk": blowout_data,
                    "matchup_dvp": matchup_dvp,
                    "pace_delta": pace_delta,
                    "stability_index": stability_index,
                    "usage_ripple": usage_ripple,
                    "context_badges": context_badges,
                    "vision_insight": {
                        "primary": f"Analyzing {pick.get('player_name', 'player')} {stat_type} @ {pick.get('line', 0)}",
                        "reasons": [],
                        "confidence": "STANDARD"
                    }
                }
                props_update[f"props.{idx}.intel_suite"] = intel_suite
                props_update[f"props.{idx}.is_vision_enriched"] = True
                props_update[f"props.{idx}.board"] = pick.get("board")  # Set board at prop level too
            
            # Combine all updates
            full_update = {**player_update, **props_update}
            
            result = await db.dg_cached_board.update_one(
                {"player_name": player_name},
                {"$set": full_update}
            )
            
            if result.modified_count > 0:
                persisted_count += 1
            
        except Exception as e:
            logger.warning(f"[PERSIST] Error updating {player_name}: {e}")
    
    logger.info(f"[OPTIMIZED_SYNC] Persisted enrichment for {persisted_count} players")
