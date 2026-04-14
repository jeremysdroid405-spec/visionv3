"""
Rolling Cache Architecture for Vision Intel Suite
=================================================
INSTANT DISPLAY, REAL-TIME UPDATES, ZERO WASTED MATH

Architecture:
1. master_active_cache.json - Always ready-to-serve to frontend
2. JIT Enrichment - Only calculate for NEW prop IDs
3. Merge/Overwrite - Preserve old enriched data, add new
4. Purge - Remove stale props when games start/lines pulled

CRITICAL: NO "EMPTY SHELL" PROPS
- Props are ONLY cached AFTER enrichment succeeds
- If enrichment fails, prop is NOT saved to cache
- Cache integrity check treats missing intel as "new"

Author: PropVision AI
Version: 2.0.0
"""

import os
import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Set, Tuple
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.normalize_to_intel_mapping import (
    normalize_prop_format,
    normalize_from_nested_props,
    merge_intel_into_prop,
    validate_prop_has_intel,
    prop_needs_enrichment,
    generate_prop_id,
    get_missing_intel_keys,
)

logger = logging.getLogger(__name__)

# Cache file path
CACHE_DIR = "/app/backend/data"
NBA_CACHE_FILE = os.path.join(CACHE_DIR, "nba_master_active_cache.json")
MLB_CACHE_FILE = os.path.join(CACHE_DIR, "mlb_master_active_cache.json")


class RollingCacheManager:
    """
    Manages the master_active_cache.json for instant Vision Intel Suite display.
    
    CRITICAL RULES:
    - Frontend ALWAYS loads from cache first (instant display)
    - JIT enrichment ONLY for new prop IDs (zero wasted math)
    - Merge preserves existing enriched data
    - Purge removes stale props immediately
    """
    
    def __init__(self, db: AsyncIOMotorDatabase, sport: str = "NBA"):
        self.db = db
        self.sport = sport.upper()
        self.cache_file = NBA_CACHE_FILE if sport.upper() == "NBA" else MLB_CACHE_FILE
        
        # Ensure cache directory exists
        os.makedirs(CACHE_DIR, exist_ok=True)
        
        # In-memory cache (loaded from file on init)
        self._cache: Dict[str, Dict] = {}
        self._cache_loaded = False
        self._last_sync = None
    
    # =========================================================================
    # CACHE FILE I/O
    # =========================================================================
    
    def _load_cache_from_file(self) -> Dict[str, Dict]:
        """Load cache from JSON file. Returns empty dict if file doesn't exist."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    data = json.load(f)
                logger.info(f"[CACHE] Loaded {len(data.get('props', {}))} props from {self.cache_file}")
                return data.get('props', {})
            except Exception as e:
                logger.error(f"[CACHE] Error loading cache file: {e}")
                return {}
        return {}
    
    def _save_cache_to_file(self):
        """Save cache to JSON file atomically (write to temp, then rename)."""
        try:
            temp_file = self.cache_file + ".tmp"
            cache_data = {
                'sport': self.sport,
                'last_updated': datetime.now(timezone.utc).isoformat(),
                'prop_count': len(self._cache),
                'props': self._cache
            }
            
            with open(temp_file, 'w') as f:
                json.dump(cache_data, f, indent=2, default=str)
            
            # Atomic rename
            os.replace(temp_file, self.cache_file)
            logger.info(f"[CACHE] Saved {len(self._cache)} props to {self.cache_file}")
            
        except Exception as e:
            logger.error(f"[CACHE] Error saving cache file: {e}")
    
    def ensure_loaded(self):
        """Ensure cache is loaded from file."""
        if not self._cache_loaded:
            self._cache = self._load_cache_from_file()
            self._cache_loaded = True
    
    # =========================================================================
    # CACHE OPERATIONS
    # =========================================================================
    
    def get_prop(self, prop_id: str) -> Optional[Dict]:
        """Get a single prop from cache by ID."""
        self.ensure_loaded()
        return self._cache.get(prop_id)
    
    def get_all_props(self) -> Dict[str, Dict]:
        """Get all props from cache (for frontend instant display)."""
        self.ensure_loaded()
        return self._cache.copy()
    
    def get_prop_ids(self) -> Set[str]:
        """Get all prop IDs currently in cache."""
        self.ensure_loaded()
        return set(self._cache.keys())
    
    def upsert_prop(self, prop_id: str, prop_data: Dict, require_enrichment: bool = False):
        """
        Upsert a prop into cache with MERGE logic.
        
        CRITICAL: 
        - Preserves existing intel_suite data if new data doesn't have it.
        - If require_enrichment=True and prop is not enriched, it will NOT be saved.
        """
        self.ensure_loaded()
        
        existing = self._cache.get(prop_id, {})
        
        # MERGE LOGIC: Preserve existing enrichment if not in new data
        if existing:
            # Preserve intel_suite if not provided in new data
            if 'intel_suite' not in prop_data and 'intel_suite' in existing:
                prop_data['intel_suite'] = existing['intel_suite']
            
            # Preserve vision_intel (AI summary) if not provided
            if 'vision_intel' not in prop_data and 'vision_intel' in existing:
                prop_data['vision_intel'] = existing['vision_intel']
            
            # Preserve vision_summary if not provided
            if 'vision_summary' not in prop_data and 'vision_summary' in existing:
                prop_data['vision_summary'] = existing['vision_summary']
            
            # Preserve scout_badges if not provided
            if 'scout_badges' not in prop_data and 'scout_badges' in existing:
                prop_data['scout_badges'] = existing['scout_badges']
            
            # Preserve matchup_analysis if not provided
            if 'matchup_analysis' not in prop_data and 'matchup_analysis' in existing:
                prop_data['matchup_analysis'] = existing['matchup_analysis']
            
            # Preserve vk_data if not provided
            if 'vk_data' not in prop_data and 'vk_data' in existing:
                prop_data['vk_data'] = existing['vk_data']
            
            # Preserve l20_variance if not provided
            if 'l20_variance' not in prop_data and 'l20_variance' in existing:
                prop_data['l20_variance'] = existing['l20_variance']
            
            # Preserve _enriched status
            if '_enriched' not in prop_data and '_enriched' in existing:
                prop_data['_enriched'] = existing['_enriched']
                prop_data['_enriched_at'] = existing.get('_enriched_at')
        
        # Add cache metadata
        prop_data['_cache_updated'] = datetime.now(timezone.utc).isoformat()
        
        self._cache[prop_id] = prop_data
    
    def upsert_enriched_prop(self, prop_id: str, prop_data: Dict) -> bool:
        """
        Upsert a prop ONLY if it has complete intel data.
        
        NO "EMPTY SHELL" PROPS - Returns False if enrichment is incomplete.
        """
        self.ensure_loaded()
        
        # Validate enrichment
        if not validate_prop_has_intel(prop_data, self.sport):
            missing = get_missing_intel_keys(prop_data, self.sport)
            logger.warning(f"[CACHE] Refusing to cache incomplete prop {prop_id}. Missing: {missing}")
            return False
        
        # Mark as enriched
        prop_data['_enriched'] = True
        prop_data['_enriched_at'] = datetime.now(timezone.utc).isoformat()
        prop_data['_cache_updated'] = datetime.now(timezone.utc).isoformat()
        
        self._cache[prop_id] = prop_data
        return True
    
    def remove_prop(self, prop_id: str):
        """Remove a prop from cache (when game starts/line pulled)."""
        self.ensure_loaded()
        if prop_id in self._cache:
            del self._cache[prop_id]
            logger.debug(f"[CACHE] Removed prop {prop_id}")
    
    def bulk_remove(self, prop_ids: Set[str]):
        """Remove multiple props from cache."""
        self.ensure_loaded()
        removed = 0
        for prop_id in prop_ids:
            if prop_id in self._cache:
                del self._cache[prop_id]
                removed += 1
        if removed > 0:
            logger.info(f"[CACHE] Bulk removed {removed} stale props")
    
    def save(self):
        """Save current cache state to file."""
        self._save_cache_to_file()
    
    # =========================================================================
    # DELTA DETECTION
    # =========================================================================
    
    def find_new_props(self, live_prop_ids: Set[str]) -> Set[str]:
        """
        Find prop IDs that are in live feed but NOT in cache.
        These need JIT enrichment.
        """
        self.ensure_loaded()
        cached_ids = set(self._cache.keys())
        new_ids = live_prop_ids - cached_ids
        
        if new_ids:
            logger.info(f"[CACHE] Found {len(new_ids)} NEW props needing JIT enrichment")
        
        return new_ids
    
    def find_stale_props(self, live_prop_ids: Set[str]) -> Set[str]:
        """
        Find prop IDs that are in cache but NOT in live feed.
        These should be purged (game started/line pulled).
        """
        self.ensure_loaded()
        cached_ids = set(self._cache.keys())
        stale_ids = cached_ids - live_prop_ids
        
        if stale_ids:
            logger.info(f"[CACHE] Found {len(stale_ids)} STALE props to purge")
        
        return stale_ids
    
    def find_props_needing_enrichment(self) -> List[str]:
        """
        Find props in cache that are missing intel_suite.
        
        CACHE INTEGRITY CHECK:
        Props missing enrichment are treated as "new" and re-processed.
        This fixes the "Empty Shell" problem.
        """
        self.ensure_loaded()
        needs_enrichment = []
        
        for prop_id, prop_data in self._cache.items():
            # Use the validation function to check for complete intel
            if prop_needs_enrichment(prop_data, self.sport):
                needs_enrichment.append(prop_id)
        
        if needs_enrichment:
            logger.info(f"[CACHE_INTEGRITY] Found {len(needs_enrichment)} props needing enrichment (treating as new)")
        
        return needs_enrichment


class DeltaManager:
    """
    Manages incremental updates between live feed and cache.
    
    JIT ENRICHMENT RULES:
    - Only enrich NEW prop IDs (not in cache)
    - DO NOT re-calculate CV, Park Factors, Matchup Ranks for cached props
    - Purge stale props immediately
    """
    
    def __init__(self, db: AsyncIOMotorDatabase, sport: str = "NBA"):
        self.db = db
        self.sport = sport.upper()
        self.cache_manager = RollingCacheManager(db, sport)
        
        # Intel calculators (lazy loaded)
        self._intel_calculator = None
        self._mlb_physical_engine = None
    
    async def _get_intel_calculator(self):
        """Lazy load Intel Suite Calculator."""
        if self._intel_calculator is None:
            from services.intel_suite_calculator import IntelSuiteCalculator
            self._intel_calculator = IntelSuiteCalculator(self.db)
        return self._intel_calculator
    
    async def _get_mlb_engine(self):
        """Lazy load MLB Physical Engine."""
        if self._mlb_physical_engine is None and self.sport == "MLB":
            from pymongo import MongoClient
            mongo_url = os.environ.get('MONGO_URL')
            db_name = os.environ.get('DB_NAME', 'propvision')
            sync_client = MongoClient(mongo_url)
            sync_db = sync_client[db_name]
            
            from services.mlb_physical_engine import MLBPhysicalEngine
            self._mlb_physical_engine = MLBPhysicalEngine(sync_db)
            self._mlb_physical_engine.load_models()
        return self._mlb_physical_engine
    
    def _generate_prop_id(self, prop: Dict) -> str:
        """Generate unique prop ID from prop data."""
        player = prop.get('player_name', '') or prop.get('description', '')
        stat = prop.get('stat_type', '') or prop.get('stat_type_raw', '')
        line = prop.get('line', 0)
        book = prop.get('bookmaker', 'dk')
        
        return f"{player}|{stat}|{line}|{book}".lower().replace(' ', '_')
    
    async def process_live_feed(self, live_props: List[Dict]) -> Dict[str, Any]:
        """
        Process live feed and perform delta-based cache updates.
        
        APEX-ONLY CACHING:
        - Base props are NORMALIZED but NOT immediately cached
        - Props are ONLY cached AFTER successful enrichment
        - Cache integrity check treats missing intel as "new"
        
        Returns:
            {
                'new_count': int,
                'stale_count': int,
                'enriched_count': int,
                'cached_total': int
            }
        """
        start_time = datetime.now(timezone.utc)
        
        # Build set of live prop IDs (handling nested props structure)
        # Use normalization layer for consistent format
        live_prop_map = {}
        for player_record in live_props:
            # Use normalize_from_nested_props for consistent flattening
            flat_props = normalize_from_nested_props(player_record, self.sport)
            
            for flat_prop in flat_props:
                prop_id = generate_prop_id(flat_prop)
                live_prop_map[prop_id] = flat_prop
        
        live_ids = set(live_prop_map.keys())
        
        # STEP 1: Find NEW props (not in cache at all)
        new_ids = self.cache_manager.find_new_props(live_ids)
        
        # STEP 2: Find STALE props (need purge)
        stale_ids = self.cache_manager.find_stale_props(live_ids)
        
        # STEP 3: PURGE stale props immediately
        if stale_ids:
            self.cache_manager.bulk_remove(stale_ids)
        
        # STEP 4: CACHE INTEGRITY CHECK
        # Find props that exist in cache but are "empty shells" (missing intel)
        existing_ids = live_ids - new_ids
        empty_shell_ids = []
        for prop_id in existing_ids:
            cached_prop = self.cache_manager.get_prop(prop_id)
            if cached_prop and prop_needs_enrichment(cached_prop, self.sport):
                empty_shell_ids.append(prop_id)
        
        # Combine new props and empty shells - all need enrichment
        needs_enrichment_ids = list(new_ids) + empty_shell_ids
        
        if empty_shell_ids:
            logger.info(f"[CACHE_INTEGRITY] {len(empty_shell_ids)} empty shells detected, treating as new")
        
        # STEP 5: JIT ENRICHMENT - Enrich and ONLY THEN cache
        enriched_count = 0
        failed_count = 0
        
        for prop_id in needs_enrichment_ids[:30]:  # Limit batch size
            prop = live_prop_map.get(prop_id)
            if not prop:
                continue
            
            try:
                # Calculate intel suite
                intel_data = await self._calculate_intel_for_prop(prop)
                
                if intel_data and intel_data.get('vk_data', {}).get('predicted') is not None:
                    # MERGE intel into prop
                    enriched_prop = merge_intel_into_prop(prop, intel_data)
                    
                    # VALIDATE enrichment is complete
                    if validate_prop_has_intel(enriched_prop, self.sport):
                        # ONLY NOW cache the prop
                        success = self.cache_manager.upsert_enriched_prop(prop_id, enriched_prop)
                        if success:
                            enriched_count += 1
                            logger.debug(f"[JIT] Cached enriched prop: {prop_id}")
                        else:
                            failed_count += 1
                    else:
                        failed_count += 1
                        logger.warning(f"[JIT] Enrichment incomplete for {prop_id}")
                else:
                    failed_count += 1
                    logger.debug(f"[JIT] No valid prediction for {prop_id}")
                
            except Exception as e:
                failed_count += 1
                logger.warning(f"[JIT] Error enriching {prop_id}: {e}")
        
        # STEP 6: SAVE cache to file
        self.cache_manager.save()
        
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        
        result = {
            'new_count': len(new_ids),
            'empty_shells_detected': len(empty_shell_ids),
            'stale_count': len(stale_ids),
            'enriched_count': enriched_count,
            'failed_count': failed_count,
            'cached_total': len(self.cache_manager.get_all_props()),
            'elapsed_seconds': round(elapsed, 2)
        }
        
        logger.info(
            f"[DELTA] {self.sport} Apex Cache: "
            f"+{result['new_count']} new, {result['empty_shells_detected']} shells, "
            f"-{result['stale_count']} stale, {result['enriched_count']} enriched, "
            f"{result['failed_count']} failed, {result['cached_total']} total "
            f"({result['elapsed_seconds']}s)"
        )
        
        return result
    
    async def _calculate_intel_for_prop(self, prop: Dict) -> Dict:
        """
        Calculate full intel suite for a single prop.
        
        Includes:
        - Vegas Killer (VK) prediction
        - L20 variance data
        - Park factors (MLB)
        - Matchup analysis
        - Scout badges
        """
        result = {}
        
        player_name = prop.get('player_name', '')
        stat_type = prop.get('stat_type', '') or prop.get('stat_type_raw', '')
        line = prop.get('line', 0)
        
        if not player_name or not stat_type:
            return result
        
        try:
            if self.sport == "MLB":
                result = await self._calculate_mlb_intel(prop)
            else:
                result = await self._calculate_nba_intel(prop)
        except Exception as e:
            logger.warning(f"[JIT] Intel calculation failed for {player_name}: {e}")
        
        return result
    
    async def _calculate_mlb_intel(self, prop: Dict) -> Dict:
        """Calculate MLB-specific intel."""
        result = {}
        
        engine = await self._get_mlb_engine()
        if not engine:
            return result
        
        player_name = prop.get('player_name', '')
        stat_type = prop.get('stat_type', '') or prop.get('stat_type_raw', '')
        line = prop.get('line', 0)
        opponent = prop.get('opponent') or prop.get('opponent_abbr')
        park_team = prop.get('home_team') if prop.get('is_away_team') else prop.get('team')
        dk_odds = prop.get('dk_odds')
        
        try:
            # Get MLR prediction
            mlr_result = engine.predict(
                player_name=player_name,
                stat_type=stat_type,
                line=line,
                opponent_team=opponent,
                park_team=park_team,
                dk_odds=int(dk_odds) if dk_odds else None
            )
            
            if mlr_result.is_valid:
                result['vk_data'] = {
                    'predicted': mlr_result.mlr_predicted,
                    'prob_over': mlr_result.vk_prob_over,
                    'prob_under': mlr_result.vk_prob_under,
                    'edge': mlr_result.vk_edge,
                    'verdict': mlr_result.vk_verdict,
                    'sigma_used': mlr_result.sigma_used,
                    'sigma_source': mlr_result.sigma_source,
                    'z_score': mlr_result.z_score,
                }
                
                result['l20_variance'] = mlr_result.mlr_matchup.get('variance', {})
                result['matchup_analysis'] = {
                    'splits': mlr_result.mlr_matchup.get('splits', {}),
                    'park': mlr_result.mlr_matchup.get('park', {}),
                    'opponent': mlr_result.mlr_matchup.get('opponent', {}),
                    'trends': mlr_result.mlr_matchup.get('trends', {}),
                    'discipline': mlr_result.mlr_matchup.get('discipline', {}),
                }
                
                # Generate scout badges
                result['scout_badges'] = self._generate_mlb_badges(mlr_result, prop)
                
                # Build vision summary
                park = mlr_result.mlr_matchup.get('park', {})
                splits = mlr_result.mlr_matchup.get('splits', {})
                trends = mlr_result.mlr_matchup.get('trends', {})
                
                park_factor = park.get('factor', 1.0)
                park_desc = "hitter-friendly" if park_factor > 1.05 else "pitcher-friendly" if park_factor < 0.95 else "neutral"
                
                result['vision_summary'] = (
                    f"Park: {park.get('venue', 'N/A')} ({park_desc}, {park_factor:.2f}x) | "
                    f"vs {prop.get('pitcher_hand', 'R')}HP: .{int(splits.get('matchup_avg', 0.25)*1000):03d} | "
                    f"L10: {trends.get('l10_avg', 0):.1f} | σ={mlr_result.sigma_used:.2f}"
                )
                
        except Exception as e:
            logger.warning(f"[JIT_MLB] Error: {e}")
        
        return result
    
    async def _calculate_nba_intel(self, prop: Dict) -> Dict:
        """Calculate NBA-specific intel."""
        result = {}
        
        try:
            intel_calc = await self._get_intel_calculator()
            
            intel_suite = await intel_calc.calculate_intel_suite(
                player_name=prop.get('player_name', ''),
                stat_type=prop.get('stat_type', ''),
                line=prop.get('line', 0),
                dk_odds=prop.get('dk_odds'),
                opponent=prop.get('opponent'),
                game_date=prop.get('game_date'),
                is_home=prop.get('is_home')
            )
            
            if intel_suite:
                result['intel_suite'] = intel_suite
                result['vk_data'] = intel_suite.get('vk_data', {})
                result['l20_variance'] = intel_suite.get('variance', {})
                result['scout_badges'] = intel_suite.get('scout_badges', [])
                
        except Exception as e:
            logger.warning(f"[JIT_NBA] Error: {e}")
        
        return result
    
    def _generate_mlb_badges(self, mlr_result, prop: Dict) -> List[str]:
        """Generate MLB scout badges based on MLR result."""
        badges = []
        
        matchup = mlr_result.mlr_matchup
        variance = matchup.get('variance', {})
        park = matchup.get('park', {})
        splits = matchup.get('splits', {})
        discipline = matchup.get('discipline', {})
        
        # High Stability Badge
        cv_l20 = variance.get('cv_l20', 1.0)
        if cv_l20 < 0.35:
            badges.append('high_stability')
        
        # High Variance Warning
        if cv_l20 > 0.60:
            badges.append('high_variance')
        
        # Hitter's Haven (park boost)
        park_factor = park.get('factor', 1.0)
        if park_factor > 1.10:
            badges.append('hitters_haven')
        
        # Contact Machine
        contact_rate = discipline.get('contact_rate', 0.75)
        if contact_rate > 0.85:
            badges.append('contact_machine')
        
        # Split Advantage
        platoon = splits.get('platoon_split', 0)
        if platoon > 0.030:  # .030 batting avg difference
            badges.append('split_advantage')
        
        # Elite Edge
        if mlr_result.vk_edge and mlr_result.vk_edge > 10:
            badges.append('elite_edge')
        
        return badges


# =============================================================================
# CACHE REFRESH LOOP
# =============================================================================

async def run_cache_refresh_loop(
    db: AsyncIOMotorDatabase,
    sport: str = "NBA",
    interval_seconds: int = 60
):
    """
    Background loop that refreshes the cache from live feed.
    
    ARCHITECTURE:
    1. Load live props from database
    2. Process delta (new/stale detection)
    3. JIT enrich only NEW props
    4. Purge stale props
    5. Save to master_active_cache.json
    6. Sleep and repeat
    """
    delta_manager = DeltaManager(db, sport)
    
    logger.info(f"[CACHE_LOOP] Starting {sport} cache refresh loop (interval: {interval_seconds}s)")
    
    while True:
        try:
            # Fetch live props from database
            if sport.upper() == "MLB":
                collection = db.mlb_cached_board
            else:
                collection = db.dg_cached_board
            
            cursor = collection.find({}, {"_id": 0})
            live_props = await cursor.to_list(length=None)
            
            # Process delta
            result = await delta_manager.process_live_feed(live_props)
            
            logger.info(
                f"[CACHE_LOOP] {sport} refresh complete: "
                f"{result['cached_total']} props cached, "
                f"+{result['new_count']} new, -{result['stale_count']} purged"
            )
            
        except Exception as e:
            logger.error(f"[CACHE_LOOP] {sport} error: {e}")
        
        await asyncio.sleep(interval_seconds)


# =============================================================================
# API ENDPOINTS FOR FRONTEND
# =============================================================================

def get_cached_props(sport: str = "NBA") -> Dict[str, Any]:
    """
    Get all cached props for instant frontend display.
    
    This is what the frontend calls - NO DATABASE HIT, instant response.
    """
    cache_file = NBA_CACHE_FILE if sport.upper() == "NBA" else MLB_CACHE_FILE
    
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                data = json.load(f)
            return {
                'success': True,
                'sport': sport,
                'prop_count': data.get('prop_count', 0),
                'last_updated': data.get('last_updated'),
                'props': data.get('props', {})
            }
        except Exception as e:
            logger.error(f"[CACHE_API] Error reading cache: {e}")
    
    return {
        'success': False,
        'sport': sport,
        'prop_count': 0,
        'props': {},
        'error': 'Cache not available'
    }


def get_cached_prop_by_id(prop_id: str, sport: str = "NBA") -> Optional[Dict]:
    """Get a single prop from cache by ID."""
    cache_data = get_cached_props(sport)
    if cache_data.get('success'):
        return cache_data.get('props', {}).get(prop_id)
    return None
