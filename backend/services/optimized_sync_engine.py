"""
Optimized Sync Engine (Sport-Exclusive Architecture)
=====================================================
High-performance sync engine that:
1. Pre-caches ALL global data (standings, referees, momentum) ONCE at sync start
2. Uses async batching with asyncio.gather() for concurrent processing
3. Enriches ALL picks with complete data in a single pass
4. Returns unified JSON payload with all intel data
5. **Sport-Exclusive Mode**: Isolates data pipelines per sport (NBA/MLB)
   - Collection prefixes: nba_ vs mlb_
   - Locked State: Prevents cross-sport data corruption

Target: Complete sync in under 5 seconds

Author: PropVision AI
Version: 2.0.0 (Sport-Exclusive)
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple, Literal
from dataclasses import dataclass, field

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

# Batch processing limits
BATCH_SIZE = 30  # Process all picks at once
GEMINI_CONCURRENT_LIMIT = 30  # Max concurrent Gemini calls (Tier 1 paid = 1000 RPM, ~16/sec)

# Supported sports for Sport-Exclusive architecture
SUPPORTED_SPORTS = ["nba", "mlb"]
DEFAULT_SPORT = "nba"

# Sport-specific collection mappings
# Each sport has its own isolated set of collections
SPORT_COLLECTION_MAP = {
    "nba": {
        "master_hub": "nba_master_hub_2026",
        "cached_board": "nba_cached_board",  # Canonical NBA cached board (post-Wave-2)
        "live_props": "nba_live_props",
        "safe_haven": "ferrari_safe_haven",
        "front_lines": "ferrari_front_lines",
        "war_zone": "ferrari_war_zone",
        "oracle_analyzed": "oracle_apex_analyzed",
    },
    "mlb": {
        "master_hub": "mlb_master_hub_2026",
        "cached_board": "mlb_cached_board",
        "live_props": "mlb_live_props",
        "safe_haven": "mlb_ferrari_safe_haven",
        "front_lines": "mlb_ferrari_front_lines",
        "war_zone": "mlb_ferrari_war_zone",
        "oracle_analyzed": "mlb_oracle_apex_analyzed",
    }
}


def get_collection_name(sport: str, collection_key: str) -> str:
    """
    Get the sport-specific collection name.
    
    Args:
        sport: Target sport ('nba' or 'mlb')
        collection_key: Logical collection key (e.g., 'cached_board', 'safe_haven')
    
    Returns:
        Actual MongoDB collection name with sport prefix
    """
    sport = sport.lower() if sport else DEFAULT_SPORT
    if sport not in SPORT_COLLECTION_MAP:
        logger.warning(f"[SPORT_EXCLUSIVE] Unknown sport '{sport}', defaulting to {DEFAULT_SPORT}")
        sport = DEFAULT_SPORT
    
    collection_map = SPORT_COLLECTION_MAP[sport]
    if collection_key not in collection_map:
        raise ValueError(f"Unknown collection key '{collection_key}' for sport '{sport}'")
    
    return collection_map[collection_key]


def validate_sport_isolation(target_sport: str, collection_name: str) -> bool:
    """
    Validate that a collection belongs to the target sport.
    Prevents cross-sport data corruption.
    
    Args:
        target_sport: The sport currently being synced
        collection_name: The collection about to be modified
    
    Returns:
        True if collection is valid for target sport, False otherwise
    """
    target_sport = target_sport.lower() if target_sport else DEFAULT_SPORT
    
    # Get all collections for the target sport
    valid_collections = set(SPORT_COLLECTION_MAP.get(target_sport, {}).values())
    
    # Check if collection is in the valid set
    if collection_name in valid_collections:
        return True
    
    # Additional check: collection should not belong to a DIFFERENT sport
    for other_sport, other_map in SPORT_COLLECTION_MAP.items():
        if other_sport != target_sport and collection_name in other_map.values():
            logger.error(f"[LOCKED_STATE] BLOCKED: Attempted write to {other_sport.upper()} collection '{collection_name}' during {target_sport.upper()} sync")
            return False
    
    return True  # Unknown collection, allow (might be a shared utility collection)


@dataclass
class GlobalSyncCache:
    """
    Holds ALL global data fetched once at sync start.
    Passed down to all enrichment functions to avoid redundant API calls.
    """
    # Sport context
    target_sport: str = DEFAULT_SPORT
    
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
    
    def get_collection(self, key: str) -> str:
        """Get sport-specific collection name."""
        return get_collection_name(self.target_sport, key)


async def fetch_global_cache(db, target_sport: str = DEFAULT_SPORT) -> GlobalSyncCache:
    """
    Fetch ALL global data in parallel at sync start.
    This is called ONCE and the cache is passed to all subsequent functions.
    
    Args:
        db: MongoDB database connection
        target_sport: Sport to sync ('nba' or 'mlb')
    """
    cache = GlobalSyncCache(target_sport=target_sport)
    start = datetime.now(timezone.utc)
    
    logger.info(f"[SYNC_CACHE] Fetching all global data for {target_sport.upper()}...")
    
    try:
        # For NBA, fetch all standard services
        # For MLB, some services may not be available yet
        if target_sport.lower() == "nba":
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
            
            if not isinstance(results[0], Exception):
                cache.standings = results[0]
            
            if not isinstance(results[1], Exception):
                cache.referee_assignments, cache.referee_by_teams = results[1]
            
            if not isinstance(results[2], Exception):
                cache.momentum_cache = results[2]
            
            if not isinstance(results[3], Exception):
                cache.vacuum_alerts, cache.vacuum_beneficiaries = results[3]
        else:
            # MLB - services not yet implemented, use empty cache
            logger.info(f"[SYNC_CACHE] {target_sport.upper()} services not yet implemented, using empty cache")
            cache.standings = {}
            cache.referee_assignments = {}
            cache.referee_by_teams = {}
            cache.momentum_cache = {}
            cache.vacuum_alerts = []
            cache.vacuum_beneficiaries = {}
        
        cache.fetched_at = datetime.now(timezone.utc)
        
        duration = (datetime.now(timezone.utc) - start).total_seconds()
        logger.info(f"[SYNC_CACHE] Global cache populated for {target_sport.upper()} in {duration:.2f}s")
        logger.info(f"  - Standings: {len(cache.standings)} teams")
        logger.info(f"  - Referees: {len(cache.referee_by_teams)} games")
        logger.info(f"  - Momentum: {len(cache.momentum_cache)} teams")
        logger.info(f"  - Vacuums: {len(cache.vacuum_alerts)} active")
        
    except Exception as e:
        logger.error(f"[SYNC_CACHE] Error fetching global cache for {target_sport.upper()}: {e}")
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

async def _collect_nba_tier_picks_from_scores(
    db, board_name: str, limit: int = 10
) -> List[Dict[str, Any]]:
    """Source-of-truth NBA tier picks for the enrichment write path.

    Reads the top-N final-nba rows from `nba_prop_scores` for the given
    tier and overlays player-level context (team, opponent, headshot)
    from `dg_cached_board` so the downstream `enrich_pick_with_cache`
    call can resolve team/opponent-keyed data (momentum, whistle, vacuum).

    This matches the exact source the Dashboard read path uses — eliminates
    the Ferrari<>VK2 split-brain that stranded props on the legacy
    intel_suite shape.
    """
    cursor = db[COLL("prop_scores", "nba")].find(
        {"version_tag": "final-nba-rt", "tier": board_name},
        {"_id": 0},
    ).sort("vision_score", -1).limit(limit)
    scores = await cursor.to_list(length=limit)
    if not scores:
        return []

    # One-pass build of player-name -> player-doc context map
    player_lookup: Dict[str, Dict[str, Any]] = {}
    async for pd in db[COLL("board_cache", "nba")].find(
        {},
        {"_id": 0, "player_name": 1, "team": 1, "team_name": 1, "team_logo_url": 1,
         "opponent": 1, "opponent_abbr": 1, "headshot_url": 1, "photo_url": 1,
         "nba_id": 1, "bdl_id": 1, "nba_com_id": 1, "espn_id": 1, "position": 1,
         "jersey_number": 1, "injury_status": 1, "injured_teammates": 1},
    ):
        pn = pd.get("player_name")
        if pn:
            player_lookup[pn] = pd

    picks: List[Dict[str, Any]] = []
    for sc in scores:
        player_doc = player_lookup.get(sc.get("player_name"), {})
        pick: Dict[str, Any] = {
            # Canonical identity
            "canonical_key": sc.get("canonical_key"),
            "event_id": sc.get("event_id"),
            "player_name": sc.get("player_name"),
            "stat_type": sc.get("stat_type"),
            "line": sc.get("line"),
            "direction": (sc.get("recommendation") or "OVER").title(),
            "recommendation": (sc.get("recommendation") or "OVER").title(),

            # Player context (drives enrich_pick_with_cache lookups)
            "team": player_doc.get("team"),
            "team_name": player_doc.get("team_name"),
            "team_logo_url": player_doc.get("team_logo_url"),
            "opponent": player_doc.get("opponent"),
            "opponent_abbr": player_doc.get("opponent_abbr"),
            "headshot_url": player_doc.get("headshot_url"),
            "photo_url": player_doc.get("photo_url"),
            "nba_id": player_doc.get("nba_id"),
            "bdl_id": player_doc.get("bdl_id"),
            "nba_com_id": player_doc.get("nba_com_id"),
            "espn_id": player_doc.get("espn_id"),
            "position": player_doc.get("position"),
            "jersey_number": player_doc.get("jersey_number"),
            "injury_status": player_doc.get("injury_status"),
            "injured_teammates": player_doc.get("injured_teammates"),

            # Tier / scoring layer (used by the intel_suite builder)
            "tier": sc.get("tier"),
            "vision_score": sc.get("vision_score"),
            "ferrari_power_score": sc.get("vision_score"),
            "tier_reference_book": sc.get("tier_reference_book"),
            "tier_reference_odds": sc.get("tier_reference_odds"),
            "edge_vs_fair": sc.get("edge_vs_fair"),
            "fair_prob": sc.get("fair_prob"),
            "confidence_score": sc.get("confidence"),

            # PrizePicks / pricing layer
            "pp_multiplier": sc.get("pp_multiplier"),
            "pp_multiplier_label": sc.get("pp_multiplier_label"),
            "pp_multiplier_source": sc.get("pp_multiplier_source"),
            "pp_utility": sc.get("pp_utility"),
            "pp_utility_category": sc.get("pp_utility_category"),

            # Model projection layer
            "vk_predicted": (
                round(float(sc["vk2_projection"]), 2)
                if sc.get("vk2_projection") is not None else None
            ),
            "vk2_projection": sc.get("vk2_projection"),
            "vk2_sigma": sc.get("vk2_sigma"),
            "model_projection": sc.get("model_projection"),
            "model_sigma": sc.get("model_sigma"),
            "p_true_active": sc.get("p_true_active"),
            "p_true_method": sc.get("p_true_method"),

            # Recent form (feeds stability / cushion intel)
            "hit_rate_over": sc.get("hit_rate_over"),
            "hit_rate_under": sc.get("hit_rate_under"),
            "cv": sc.get("cv"),
            "l5_avg": sc.get("l5_avg"),
            "l10_avg": sc.get("l10_avg"),
            "l20_avg": sc.get("l20_avg"),
            "season_avg": sc.get("season_avg"),

            # Book layer (may be absent on score doc — persist tolerates None)
            "draftkings_price": sc.get("draftkings_price"),
            "fanduel_price": sc.get("fanduel_price"),
            "dk_odds": sc.get("draftkings_price"),
        }
        picks.append(pick)

    return picks




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


async def _attach_cached_summaries(picks: List[Dict], db, target_sport: str = DEFAULT_SPORT) -> int:
    """
    Attach cached vision summaries to picks without making API calls.
    Returns count of picks that got cached summaries.
    
    Args:
        picks: List of pick dictionaries
        db: MongoDB database connection
        target_sport: Sport context for collection lookup
    """
    from services.vision_summary_service import VisionSummaryService
    from datetime import datetime, timezone
    
    cached_count = 0
    now = datetime.now(timezone.utc)
    
    for pick in picks:
        if pick.get("vision_summary"):
            cached_count += 1
            continue
        
        # Check in-memory cache
        simple_key = f"{pick.get('player_name')}|{pick.get('stat_type')}|{pick.get('line')}"
        content_hash = VisionSummaryService._cache_keys.get(simple_key)
        
        if content_hash and content_hash in VisionSummaryService._summary_cache:
            cached_time = VisionSummaryService._cache_timestamps.get(content_hash)
            if cached_time and (now - cached_time).total_seconds() < VisionSummaryService._CACHE_TTL_SECONDS:
                pick["vision_summary"] = VisionSummaryService._summary_cache[content_hash]
                cached_count += 1
                continue
        
        # Check database cache (ferrari collections may have old summaries)
        tier = pick.get("board") or pick.get("tier", "")
        if tier:
            # Use sport-specific collection name
            collection_name = get_collection_name(target_sport, tier.replace("ferrari_", ""))
            try:
                existing = await db[collection_name].find_one(
                    {
                        "player_name": pick.get("player_name"),
                        "stat_type": pick.get("stat_type"),
                        "line": pick.get("line")
                    },
                    {"_id": 0, "vision_summary": 1}
                )
                if existing and existing.get("vision_summary"):
                    pick["vision_summary"] = existing["vision_summary"]
                    # Also cache in memory for next time
                    if content_hash:
                        VisionSummaryService._summary_cache[content_hash] = existing["vision_summary"]
                        VisionSummaryService._cache_timestamps[content_hash] = now
                    cached_count += 1
            except Exception as e:
                logger.debug(f"[SYNC_ENGINE] DB cache check failed: {e}")
    
    return cached_count


async def _batch_generate_summaries(picks: List[Dict], db) -> None:
    """
    AI summaries are now generated by vision_intel_service.py in ferrari_tier_service.py
    This function is DISABLED to prevent duplicate Gemini API calls.
    
    The Vision Intel Layer runs ONCE during Step 6 of tier building and generates:
    - vision_intel: AI-generated insight summary
    - intel_score: Confidence score (1-10)
    - intel_verdict: CHALK | TRAP | VALUE
    - composite_score: Combined VK + Intel score
    
    All Gemini calls are consolidated in vision_intel_service.py
    """
    # NO-OP: Vision Intel handles all AI generation in ferrari_tier_service.py
    logger.info("[SYNC_ENGINE] AI summaries handled by Vision Intel Layer in ferrari_tier_service")
    pass


async def run_optimized_sync(db, target_sport: str = DEFAULT_SPORT, refresh_intel: bool = False) -> Dict[str, Any]:
    """
    Run the full optimized sync pipeline for a SPECIFIC sport.
    
    **SPORT-EXCLUSIVE MODE**: This sync is isolated to `target_sport` only.
    Collections for other sports are LOCKED and cannot be modified.
    
    Pipeline:
    1. Fetch ALL global data (standings, refs, momentum, vacuums) in parallel
    2. Run Ferrari pipeline to build scored picks
    3. Enrich picks with cached global data (fast, sync)
    4. Generate AI summaries in batches (rate-limited)
    5. Update dg_cached_board with enriched intel_suite data
    
    Args:
        db: MongoDB database connection
        target_sport: Sport to sync ('nba' or 'mlb')
        refresh_intel: If True, force regenerate all Vision Intel (ignores cache)
    
    Returns:
        Complete payload with all picks enriched.
    """
    # Normalize and validate sport
    target_sport = (target_sport or DEFAULT_SPORT).lower()
    if target_sport not in SUPPORTED_SPORTS:
        logger.warning(f"[OPTIMIZED_SYNC] Unknown sport '{target_sport}', defaulting to {DEFAULT_SPORT}")
        target_sport = DEFAULT_SPORT
    
    start = datetime.now(timezone.utc)
    timings = {}  # Track timing for each phase
    
    # ============================================================
    # SPORT-EXCLUSIVE LOCK ANNOUNCEMENT
    # ============================================================
    logger.info("=" * 60)
    logger.info(f"[OPTIMIZED_SYNC] 🔒 SPORT-EXCLUSIVE MODE: {target_sport.upper()}")
    if target_sport == "mlb":
        logger.info("[OPTIMIZED_SYNC] 🛡️ Syncing MLB... NBA Data Protected.")
    else:
        logger.info("[OPTIMIZED_SYNC] 🛡️ Syncing NBA... MLB Data Protected.")
    logger.info(f"[OPTIMIZED_SYNC] Collections in scope: {list(SPORT_COLLECTION_MAP[target_sport].values())}")
    logger.info("=" * 60)
    
    # Step 0: Sync BDL Game Logs (ensures fresh hit rate data) - NBA ONLY for now
    t0 = datetime.now(timezone.utc)
    if target_sport == "nba":
        try:
            from services.bdl_game_logs_sync_batched import run_bdl_game_logs_sync_batched
            logs_result = await run_bdl_game_logs_sync_batched(db)
            timings["0_game_logs_sync"] = (datetime.now(timezone.utc) - t0).total_seconds()
            logger.info(f"[OPTIMIZED_SYNC] Step 0 (Game Logs): {timings['0_game_logs_sync']:.2f}s - synced {logs_result.get('players_synced', 0)} players")
        except Exception as e:
            logger.warning(f"[OPTIMIZED_SYNC] Game logs sync failed (non-fatal): {e}")
            timings["0_game_logs_sync"] = (datetime.now(timezone.utc) - t0).total_seconds()
    else:
        logger.info(f"[OPTIMIZED_SYNC] Step 0 (Game Logs): SKIPPED - {target_sport.upper()} uses different stat source")
        timings["0_game_logs_sync"] = 0
    
    # Step 1: Fetch global cache ONCE (parallel fetch)
    t1 = datetime.now(timezone.utc)
    cache = await fetch_global_cache(db, target_sport)
    timings["1_global_cache"] = (datetime.now(timezone.utc) - t1).total_seconds()
    logger.info(f"[OPTIMIZED_SYNC] Step 1 (Global Cache): {timings['1_global_cache']:.2f}s")
    
    # Step 2: Run Ferrari pipeline (builds ferrari_scored, ferrari_safe_haven, etc.)
    t2 = datetime.now(timezone.utc)
    
    # Route to sport-specific tier service
    if target_sport == "mlb":
        from services.mlb_tier_service import get_mlb_tier_service
        ferrari_service = get_mlb_tier_service(db)
        logger.info("[OPTIMIZED_SYNC] Using MLB Tier Service (Oracle Apex 2026 Logic)")
    else:
        from services.ferrari_tier_service import get_ferrari_tier_service
        ferrari_service = get_ferrari_tier_service(db)
        logger.info("[OPTIMIZED_SYNC] Using NBA Ferrari Tier Service")
    
    # Pass the pre-fetched cache, target_sport, and refresh_intel to Ferrari
    ferrari_result = await ferrari_service.build_ferrari_tiers(start, target_sport=target_sport, refresh_intel=refresh_intel)
    timings["2_ferrari_pipeline"] = (datetime.now(timezone.utc) - t2).total_seconds()
    logger.info(f"[OPTIMIZED_SYNC] Step 2 (Ferrari Pipeline): {timings['2_ferrari_pipeline']:.2f}s")
    
    if not ferrari_result.get("success"):
        logger.error(f"[OPTIMIZED_SYNC] Ferrari pipeline failed: {ferrari_result.get('error')}")
        return {"success": False, "error": "Ferrari pipeline failed", "details": ferrari_result}
    
    logger.info(f"[OPTIMIZED_SYNC] Ferrari complete: {ferrari_result.get('output', {}).get('total_picks', 0)} picks")
    
    # Step 3: Collect all picks from the authoritative tier source and enrich with cache.
    # ------------------------------------------------------------------
    # NBA: split-brain fix (Apr 18 2026)
    #   The read path (Dashboard / /api/v3/ferrari/*) was migrated to
    #   `nba_prop_scores` (VK2 pipeline). Previously this write path still
    #   read from `ferrari_service.get_{safe_haven,front_lines,war_zone}()`
    #   which is a different tier selection, causing surfaced picks (e.g.
    #   Christian Braun PRA 14.5 OVER) to inherit the stale legacy
    #   `intel_suite` shape with `board=None`, `momentum_data=None`, etc.
    #
    #   Write driver now reads from the SAME `nba_prop_scores` source so
    #   every pick the UI surfaces gets enriched with the modern
    #   `intel_suite` (momentum_data, vacuum_data, whistle_data, board,
    #   sport, ferrari_power_score) via `enrich_pick_with_cache`.
    #
    #   MLB path unchanged (still routed through mlb_tier_service).
    # ------------------------------------------------------------------
    t3 = datetime.now(timezone.utc)
    all_picks = []
    boards = {}

    for board_name in ["safe_haven", "front_lines", "war_zone"]:
        if target_sport == "nba":
            picks = await _collect_nba_tier_picks_from_scores(db, board_name, limit=10)
            # Enrich each pick with momentum/whistle/vacuum from the shared cache.
            # (Same enrichment fn used by the legacy path — GUARANTEES parity.)
            for pick in picks:
                pick["board"] = board_name
                pick["sport"] = target_sport
                enrich_pick_with_cache(pick, cache)
        else:
            if board_name == "safe_haven":
                board_data = await ferrari_service.get_safe_haven(10)
            elif board_name == "front_lines":
                board_data = await ferrari_service.get_front_lines(10)
            else:
                board_data = await ferrari_service.get_war_zone(10)
            picks = board_data.get("picks", [])
            for pick in picks:
                pick["board"] = board_name
                pick["sport"] = target_sport
                enrich_pick_with_cache(pick, cache)

        all_picks.extend(picks)
        boards[board_name] = {
            "picks": picks,
            "count": len(picks)
        }
    timings["3_collect_enrich"] = (datetime.now(timezone.utc) - t3).total_seconds()
    logger.info(f"[OPTIMIZED_SYNC] Step 3 (Collect & Enrich): {timings['3_collect_enrich']:.2f}s")
    
    logger.info(f"[OPTIMIZED_SYNC] Collected {len(all_picks)} {target_sport.upper()} picks for AI summary generation")
    
    # Step 4: Vision Intel is now handled by ferrari_tier_service (runs on Final Top 10)
    # This step is NO LONGER NEEDED - ferrari_tier_service writes vision_intel directly
    t4 = datetime.now(timezone.utc)
    
    # Check how many picks have vision_intel (should be all of them now)
    picks_with_intel = sum(1 for p in all_picks if p.get("vision_intel"))
    picks_missing_intel = sum(1 for p in all_picks if not p.get("vision_intel"))
    
    logger.info(f"[OPTIMIZED_SYNC] Vision Intel status: {picks_with_intel} enriched, {picks_missing_intel} missing")
    
    # If any picks are missing vision_intel, apply fallback (shouldn't happen with new pipeline)
    if picks_missing_intel > 0:
        logger.warning(f"[OPTIMIZED_SYNC] {picks_missing_intel} picks missing vision_intel - applying fallback")
        for pick in all_picks:
            if not pick.get("vision_intel"):
                # Generate fallback summary based on stats
                player = pick.get("player_name", "Player")
                stat = pick.get("stat_type", "stat")
                line = pick.get("line", 0)
                h10 = pick.get("h10_rate") or pick.get("hit_rate_l10", 0)
                edge = pick.get("vk_edge", 0)
                
                pick["vision_intel"] = f"{player} shows {h10:.0f}% L10 hit rate on {stat} {line}. Edge: {edge:+.1f}%"
                pick["intel_score"] = 5
                pick["intel_verdict"] = "VALUE"
                pick["intel_risk"] = "Medium"
                pick["adjusted_confidence"] = 0.5
    
    timings["4_vision_intel_check"] = (datetime.now(timezone.utc) - t4).total_seconds()
    logger.info(f"[OPTIMIZED_SYNC] Step 4 (Vision Intel Check): {timings['4_vision_intel_check']:.2f}s")
    
    # Step 5: Update cached_board with enriched data (sport-specific collection)
    t5 = datetime.now(timezone.utc)
    await _persist_enriched_picks(db, all_picks, cache, target_sport)
    timings["5_persist_enriched"] = (datetime.now(timezone.utc) - t5).total_seconds()
    logger.info(f"[OPTIMIZED_SYNC] Step 5 (Persist Enriched): {timings['5_persist_enriched']:.2f}s")
    
    # Step 6: SKIP - Ferrari tier collections already have vision_intel from ferrari_tier_service
    # The atomic upsert in ferrari_tier_service handles this now
    t6 = datetime.now(timezone.utc)
    logger.info(f"[OPTIMIZED_SYNC] Step 6 (Tier Update): SKIPPED - handled by ferrari_tier_service atomic upsert")
    timings["6_update_tiers"] = 0
    
    duration = (datetime.now(timezone.utc) - start).total_seconds()
    
    # Log timing breakdown
    logger.info(f"[OPTIMIZED_SYNC] === {target_sport.upper()} TIMING BREAKDOWN ===")
    for step, seconds in timings.items():
        logger.info(f"[OPTIMIZED_SYNC]   {step}: {seconds:.2f}s")
    logger.info(f"[OPTIMIZED_SYNC] 🏁 {target_sport.upper()} Pipeline complete in {duration:.2f}s")
    
    return {
        "success": True,
        "sport": target_sport,
        "safe_haven": boards.get("safe_haven", {}),
        "front_lines": boards.get("front_lines", {}),
        "war_zone": boards.get("war_zone", {}),
        "total_picks": len(all_picks),
        "sync_duration": round(duration, 2),
        "timings": {k: round(v, 2) for k, v in timings.items()},
        "ferrari_stats": ferrari_result.get("output", {}),
        "cache_stats": {
            "standings": len(cache.standings),
            "referees": len(cache.referee_by_teams),
            "momentum": len(cache.momentum_cache),
            "vacuums": len(cache.vacuum_alerts)
        }
    }


async def _update_tier_collections_with_summaries(db, picks: List[Dict], target_sport: str = DEFAULT_SPORT) -> None:
    """
    Update Ferrari tier collections (ferrari_safe_haven, ferrari_front_lines, ferrari_war_zone)
    with the AI-generated vision_summary field.
    
    **SPORT-EXCLUSIVE**: Only updates collections for the target_sport.
    
    Args:
        db: MongoDB database connection
        picks: List of pick dictionaries with vision summaries
        target_sport: Sport context for collection lookup
    """
    if not picks:
        return
    
    # Validate sport isolation
    logger.info(f"[SYNC_ENGINE] Updating tier collections for {target_sport.upper()}")
    
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
    
    # Update each tier collection (sport-specific)
    updated_count = 0
    for tier_name, tier_picks in tier_map.items():
        # Get sport-specific collection name
        collection_name = get_collection_name(target_sport, tier_name)
        
        # Validate we're not touching other sport's collections
        if not validate_sport_isolation(target_sport, collection_name):
            logger.error(f"[LOCKED_STATE] BLOCKED write to {collection_name} - not in {target_sport.upper()} scope")
            continue
        
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
                        "intel_suite.vision_summary": vision_summary,
                        "sport": target_sport  # Tag sport on the document
                    }
                }
            )
            if result.modified_count > 0:
                updated_count += 1
    
    logger.info(f"[OPTIMIZED_SYNC] Updated {updated_count} {target_sport.upper()} picks in tier collections with AI summaries")


async def _persist_enriched_picks(db, picks: List[Dict], cache: GlobalSyncCache, target_sport: str = DEFAULT_SPORT) -> None:
    """
    Persist enriched pick data back to the cached_board collection.
    
    **SPORT-EXCLUSIVE**: Uses sport-specific collection name.
    
    Strategy: Update ALL props for each player with their game-level enrichment data
    (momentum, whistle, vacuum). This ensures consistent data across all prop lines.
    
    Also updates player-level fields for fast access.
    
    Args:
        db: MongoDB database connection
        picks: List of enriched pick dictionaries
        cache: GlobalSyncCache instance
        target_sport: Sport context for collection lookup
    """
    if not picks:
        return
    
    # Get sport-specific collection name
    cached_board_collection = get_collection_name(target_sport, "cached_board")
    
    # Validate sport isolation
    if not validate_sport_isolation(target_sport, cached_board_collection):
        logger.error(f"[LOCKED_STATE] BLOCKED write to {cached_board_collection}")
        return
    
    logger.info(f"[OPTIMIZED_SYNC] Persisting enrichment data for {len(picks)} {target_sport.upper()} picks to {cached_board_collection}")
    
    # Group picks by player — collect ALL picks per player (don't dedupe).
    # Each pick's board/ferrari_power_score must land on its own matched prop
    # (stat_type, line, direction). Player-level context (momentum/whistle/
    # vacuum) is identical across a player's picks in one game, so the last
    # write wins harmlessly.
    picks_by_player: Dict[str, List[Dict[str, Any]]] = {}
    for pick in picks:
        pn = pick.get("player_name")
        if not pn:
            continue
        picks_by_player.setdefault(pn, []).append(pick)

    persisted_count = 0

    for player_name, player_picks in picks_by_player.items():
        try:
            # Get the FULL props list so we can match picks to prop indices
            # by identity (stat_type, line, direction) — this is the fix for
            # the board cross-pollution where a safe_haven pick's board was
            # being stamped onto every one of a player's prop rows.
            player_doc = await db[cached_board_collection].find_one(
                {"player_name": player_name},
                {"_id": 0, "props": 1},
            )
            if not player_doc or not player_doc.get("props"):
                logger.debug(f"[PERSIST] Player {player_name} not in {cached_board_collection}, skipping")
                continue

            props_list = player_doc["props"]
            num_props = len(props_list)

            # Build an identity-keyed lookup so each pick finds its own row
            prop_idx_by_identity: Dict[Tuple[str, float, str], int] = {}
            for idx, pr in enumerate(props_list):
                if not isinstance(pr, dict):
                    continue
                try:
                    line_f = float(pr.get("line")) if pr.get("line") is not None else None
                except (TypeError, ValueError):
                    line_f = None
                key = (
                    (pr.get("stat_type") or "").strip().upper(),
                    line_f,
                    (pr.get("direction") or "").strip().lower(),
                )
                if key[0] and key[1] is not None and key[2]:
                    prop_idx_by_identity[key] = idx

            # Player-level fields shared across ALL picks of this player.
            # Use the first pick for these (momentum/whistle/vacuum are keyed
            # by game context which is identical across a player's picks).
            primary = player_picks[0]

            player_update = {
                "enriched_at": datetime.now(timezone.utc).isoformat(),
                # board_member: retained at player level as a hint that the
                # player has AT LEAST one board placement; the authoritative
                # per-prop tier lives in props.{idx}.board and intel_suite.board
                "board_member": primary.get("board"),
                "sport": target_sport,
            }

            if primary.get("momentum_data"):
                player_update["momentum_data"] = primary["momentum_data"]
                player_update["momentum_modifier"] = primary.get("momentum_modifier", 0)
            if primary.get("crew_chief"):
                player_update["crew_chief"] = primary["crew_chief"]
                player_update["ref_ou_pct"] = primary.get("ref_ou_pct")
                player_update["ref_ppg"] = primary.get("ref_ppg")
                player_update["whistle_class"] = primary.get("whistle_class")
                player_update["whistle_modifier"] = primary.get("whistle_modifier", 0)
                player_update["point_lift"] = primary.get("point_lift", 0)
                player_update["lift_label"] = primary.get("lift_label")
                player_update["lift_type"] = primary.get("lift_type")
            if primary.get("vacuum_data"):
                player_update["vacuum_data"] = primary["vacuum_data"]
                player_update["vacuum_modifier"] = primary.get("vacuum_modifier", 0)

            # Build prop-level updates. Two passes:
            #   1) BROADCAST player-level context (momentum/whistle/vacuum) to
            #      all prop indices — these describe the GAME, not the prop.
            #   2) STAMP tier-specific fields (board, ferrari_power_score,
            #      derived intel_suite) onto the SINGLE matching prop index.
            #   3) CLEAR tier-specific fields on every other prop index so
            #      previous syncs can't leave a stale tier tag behind.
            props_update: Dict[str, Any] = {}

            # Pre-compute which indices are "matched" by at least one pick
            matched_indices: Dict[int, Dict[str, Any]] = {}
            for pick in player_picks:
                try:
                    line_f = float(pick.get("line")) if pick.get("line") is not None else None
                except (TypeError, ValueError):
                    line_f = None
                key = (
                    (pick.get("stat_type") or "").strip().upper(),
                    line_f,
                    (pick.get("direction") or pick.get("recommendation") or "").strip().lower(),
                )
                idx = prop_idx_by_identity.get(key)
                if idx is None:
                    logger.debug(
                        f"[PERSIST] no prop match for {player_name} {key} — "
                        f"player has {list(prop_idx_by_identity.keys())[:5]}..."
                    )
                    continue
                matched_indices[idx] = pick

            for idx in range(num_props):
                # -- Broadcast: player-level game context on every prop --
                if primary.get("momentum_data"):
                    props_update[f"props.{idx}.momentum_data"] = primary["momentum_data"]
                    props_update[f"props.{idx}.momentum_modifier"] = primary.get("momentum_modifier", 0)
                    props_update[f"props.{idx}.has_momentum_modifier"] = primary.get("has_momentum_modifier", False)
                if primary.get("crew_chief"):
                    props_update[f"props.{idx}.crew_chief"] = primary["crew_chief"]
                    props_update[f"props.{idx}.ref_ou_pct"] = primary.get("ref_ou_pct")
                    props_update[f"props.{idx}.ref_ppg"] = primary.get("ref_ppg")
                    props_update[f"props.{idx}.whistle_class"] = primary.get("whistle_class")
                    props_update[f"props.{idx}.whistle_modifier"] = primary.get("whistle_modifier", 0)
                    props_update[f"props.{idx}.has_whistle_modifier"] = primary.get("has_whistle_modifier", False)
                    props_update[f"props.{idx}.point_lift"] = primary.get("point_lift", 0)
                    props_update[f"props.{idx}.lift_label"] = primary.get("lift_label")
                    props_update[f"props.{idx}.lift_type"] = primary.get("lift_type")
                if primary.get("vacuum_data"):
                    props_update[f"props.{idx}.vacuum_data"] = primary["vacuum_data"]
                    props_update[f"props.{idx}.vacuum_modifier"] = primary.get("vacuum_modifier", 0)
                    props_update[f"props.{idx}.has_vacuum_modifier"] = primary.get("has_vacuum_modifier", False)

                # -- Per-prop: tier-specific stamps applied ONLY to matched idx --
                matched_pick = matched_indices.get(idx)
                if matched_pick is not None:
                    pick_for_intel = matched_pick
                else:
                    # Non-matched prop: clear any stale tier tag from prior syncs.
                    props_update[f"props.{idx}.board"] = None
                    props_update[f"props.{idx}.is_vision_enriched"] = False
                    # Wipe tier-specific intel_suite fields. Keep player-level
                    # context (momentum/whistle/vacuum) alive in intel_suite by
                    # building a reduced suite below.
                    pick_for_intel = None

                # Assemble intel_suite. For matched props include full
                # tier-specific context; for unmatched build the base
                # (player-level) shape only.
                intel_pick = pick_for_intel or primary
                opponent = intel_pick.get("opponent") or intel_pick.get("opponent_abbr")
                stat_type = (
                    props_list[idx].get("stat_type")
                    if idx < num_props and isinstance(props_list[idx], dict)
                    else intel_pick.get("stat_type", "PTS")
                )

                blowout_level = intel_pick.get("blowout_risk", "UNKNOWN")
                blowout_data = {
                    "risk_level": blowout_level,
                    "player_team_record": intel_pick.get("team_record", ""),
                    "opponent_team_record": intel_pick.get("opponent_record", ""),
                    "warning": f"Blowout risk {blowout_level}" if blowout_level in ["HIGH", "MEDIUM"] else None,
                }

                momentum = primary.get("momentum_data", {}) or {}
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
                    "stat_type": stat_type,
                }

                pace_delta_val = 0
                pace_delta = {
                    "display": f"+{abs(pace_delta_val):.1f}" if pace_delta_val > 0 else f"{pace_delta_val:.1f}",
                    "possessions": pace_delta_val,
                    "tempo_label": "Neutral Pace",
                    "expected_game_pace": "98.0",
                    "team_pace": 98.0,
                    "opp_pace": 98.0,
                    "league_avg": 98.0,
                }

                l10_rate = intel_pick.get("l10_rate", 0) or 0
                l5_rate = intel_pick.get("l5_rate", 0) or 0
                stability_score = int((l10_rate + l5_rate) / 2) if (l10_rate or l5_rate) else 50
                stability_index = {
                    "display": f"{stability_score}%",
                    "score": stability_score,
                    "consistency": "Consistent" if stability_score >= 70 else "Variable" if stability_score >= 50 else "Volatile",
                    "std_dev": None,
                }

                vacuum = primary.get("vacuum_data")
                usage_ripple = {
                    "display": "Elevated Usage" if vacuum else "Standard Volume",
                    "reasoning": vacuum.get("reason", "Based on team role") if vacuum else "Based on team role and recent minutes",
                    "bump_percent": int(vacuum.get("usage_bump", 0)) if vacuum else 0,
                    "shift_label": f"+{int(vacuum.get('usage_bump', 0))}% Usage" if vacuum else "Normal",
                    "injuries_affecting": [vacuum.get("injured_player")] if vacuum and vacuum.get("injured_player") else [],
                }

                context_badges: List[str] = []
                if blowout_level == "HIGH":
                    context_badges.append("blowout_risk")
                if intel_pick.get("trap_risk"):
                    context_badges.append("trap_risk")
                if intel_pick.get("sharp_movement"):
                    context_badges.append("sharp_movement")
                if vacuum:
                    context_badges.append("usage_boost")
                if momentum and momentum.get("is_weak"):
                    context_badges.append("soft_matchup")

                intel_suite = {
                    "momentum_data": primary.get("momentum_data"),
                    "whistle_data": {
                        "crew_chief": primary.get("crew_chief"),
                        "ref_ou_pct": primary.get("ref_ou_pct"),
                        "ref_ppg": primary.get("ref_ppg"),
                        "whistle_class": primary.get("whistle_class"),
                        "point_lift": primary.get("point_lift"),
                        "lift_label": primary.get("lift_label"),
                        "lift_type": primary.get("lift_type"),
                    } if primary.get("crew_chief") else None,
                    "vacuum_data": primary.get("vacuum_data"),
                    # Per-prop tier stamps — nulled for non-matched props
                    "board": matched_pick.get("board") if matched_pick else None,
                    "ferrari_power_score": (
                        matched_pick.get("ferrari_power_score") if matched_pick else None
                    ),
                    "blowout_risk": blowout_data,
                    "matchup_dvp": matchup_dvp,
                    "pace_delta": pace_delta,
                    "stability_index": stability_index,
                    "usage_ripple": usage_ripple,
                    "context_badges": context_badges,
                    "vision_insight": {
                        "primary": f"Analyzing {intel_pick.get('player_name', 'player')} {stat_type} @ {intel_pick.get('line', 0)}",
                        "reasons": [],
                        "confidence": "STANDARD",
                    },
                    "sport": target_sport,
                }
                props_update[f"props.{idx}.intel_suite"] = intel_suite

                if matched_pick is not None:
                    props_update[f"props.{idx}.is_vision_enriched"] = True
                    props_update[f"props.{idx}.board"] = matched_pick.get("board")
                    props_update[f"props.{idx}.sport"] = target_sport

            # Combine all updates
            full_update = {**player_update, **props_update}

            result = await COLL.handle(db, "board_cache", target_sport).update_one(
                {"player_name": player_name},
                {"$set": full_update},
            )

            if result.modified_count > 0:
                persisted_count += 1

        except Exception as e:
            logger.warning(f"[PERSIST] Error updating {player_name}: {e}")
    
    logger.info(f"[OPTIMIZED_SYNC] Persisted enrichment for {persisted_count} {target_sport.upper()} players")

    if persisted_count > 0:
        try:
            from services.rebuild_coordinator import get_coordinator
            from services.event_bus import BoardEvent
            coordinator = get_coordinator()
            if coordinator:
                event = BoardEvent(
                    sport=target_sport,
                    event_type="scored_data_refresh",
                    severity="medium",
                    source=f"cached_board_refresh_{target_sport}",
                    metadata={"persisted_count": persisted_count},
                )
                await coordinator.handle_event(event)
                logger.info(f"[OPTIMIZED_SYNC] Coordinator event emitted: cached_board_refresh_{target_sport} ({persisted_count} players)")
        except Exception as e:
            logger.warning(f"[OPTIMIZED_SYNC] Coordinator event failed (non-fatal): {e}")
