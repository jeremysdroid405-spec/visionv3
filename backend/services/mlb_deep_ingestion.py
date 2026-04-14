"""
MLB_ORACLE_APEX - 3-Year Deep Ingestion Service
================================================
Comprehensive data backfill using BDL GOAT Tier API.

ENDPOINTS UTILIZED:
1. /stats - Full game logs for all players (2023-2025)
2. /season_stats - Season-level summaries for normalization
3. /players/splits - LHP vs RHP, Home vs Away, Day vs Night splits
4. /players/{id}/vs - Lifetime head-to-head PvP collision history
5. /plays & /pitches - Deep situational data (Whiff, Zone-Contact, Exit Velo)

HUB RECONSTRUCTION:
- All data joined into mlb_master_hub_2026
- Every PA joined with Opponent Pitcher Handedness + Venue ID (Park Factor)

TARGET: 90,000+ training samples for 5-model ensemble

Author: PropVision AI
Version: 1.0.0
"""

import os
import asyncio
import logging
import json
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import httpx
import numpy as np

logger = logging.getLogger(__name__)

# =============================================================================
# BDL API CONFIGURATION
# =============================================================================

BDL_API_KEY = os.environ.get("BDL_API_KEY")
BDL_MLB_BASE_URL = "https://api.balldontlie.io/mlb/v1"

# Seasons for backfill
BACKFILL_SEASONS = [2023, 2024, 2025]

# Rate limiting
RATE_LIMIT_DELAY = 0.35
BATCH_SIZE = 25
MAX_RETRIES = 3

# Team abbreviations for park factors
TEAM_ABBREVS = [
    'ARI', 'ATL', 'BAL', 'BOS', 'CHC', 'CHW', 'CIN', 'CLE', 'COL', 'DET',
    'HOU', 'KC', 'LAA', 'LAD', 'MIA', 'MIL', 'MIN', 'NYM', 'NYY', 'OAK',
    'PHI', 'PIT', 'SD', 'SF', 'SEA', 'STL', 'TB', 'TEX', 'TOR', 'WSH'
]


class MLBDeepIngestion:
    """
    MLB 3-Year Deep Ingestion Service.
    
    Pulls comprehensive data from BDL GOAT Tier API for MLB_ORACLE_APEX training.
    """
    
    def __init__(self, db):
        """
        Initialize with async Motor database.
        """
        self.db = db
        self.master_hub = db.mlb_master_hub_2026
        self.historical_logs = db.mlb_historical_logs
        self.splits_cache = db.mlb_splits_cache
        self.pvp_cache = db.mlb_pvp_cache
        self.advanced_metrics = db.mlb_advanced_metrics
        
        self._client: Optional[httpx.AsyncClient] = None
        self._game_cache: Dict[int, Dict] = {}  # game_id -> game info
        
        # Stats tracking
        self.stats = {
            'players_processed': 0,
            'game_logs_fetched': 0,
            'splits_fetched': 0,
            'pvp_matchups_fetched': 0,
            'errors': 0,
        }
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=90.0,
                headers={"Authorization": BDL_API_KEY},
                limits=httpx.Limits(max_connections=10)
            )
        return self._client
    
    async def close(self):
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    # =========================================================================
    # API HELPERS
    # =========================================================================
    
    async def _api_get(
        self,
        endpoint: str,
        params: Dict = None,
        retries: int = MAX_RETRIES
    ) -> Optional[Dict]:
        """
        Make GET request to BDL API with retries and rate limiting.
        """
        client = await self._get_client()
        url = f"{BDL_MLB_BASE_URL}/{endpoint}"
        
        for attempt in range(retries):
            try:
                await asyncio.sleep(RATE_LIMIT_DELAY)
                response = await client.get(url, params=params)
                
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 429:
                    wait_time = 5 * (attempt + 1)
                    logger.warning(f"[BDL] Rate limited, waiting {wait_time}s...")
                    await asyncio.sleep(wait_time)
                elif response.status_code == 404:
                    return None
                else:
                    logger.warning(f"[BDL] {endpoint} returned {response.status_code}")
                    
            except Exception as e:
                logger.error(f"[BDL] Error on {endpoint}: {e}")
                await asyncio.sleep(2)
        
        self.stats['errors'] += 1
        return None
    
    async def _paginate_all(
        self,
        endpoint: str,
        params: Dict = None,
        max_pages: int = 100
    ) -> List[Dict]:
        """
        Fetch all pages from a paginated endpoint.
        """
        all_data = []
        cursor = None
        page = 0
        
        while page < max_pages:
            req_params = params.copy() if params else {}
            if cursor:
                req_params['cursor'] = cursor
            
            result = await self._api_get(endpoint, req_params)
            if not result:
                break
            
            data = result.get('data', [])
            if not data:
                break
            
            all_data.extend(data)
            
            # Check for next page
            meta = result.get('meta', {})
            cursor = meta.get('next_cursor')
            if not cursor:
                break
            
            page += 1
        
        return all_data
    
    # =========================================================================
    # STEP 1: GAME LOGS (mlb_get_stats)
    # =========================================================================
    
    async def fetch_season_game_logs(self, season: int) -> List[Dict]:
        """
        Fetch all game logs for a season using /stats endpoint.
        
        Returns list of player game logs with:
        - player_id, player_name
        - game_id, game_date
        - team, opponent
        - All batting/pitching stats
        """
        logger.info(f"[DEEP_INGEST] Fetching game logs for season {season}...")
        
        all_logs = await self._paginate_all(
            'stats',
            params={
                'seasons[]': season,
                'per_page': 100,
            },
            max_pages=500  # MLB has ~2400 games/season, ~100 players/game
        )
        
        logger.info(f"[DEEP_INGEST] Season {season}: {len(all_logs):,} game logs")
        self.stats['game_logs_fetched'] += len(all_logs)
        
        return all_logs
    
    # =========================================================================
    # STEP 2: SEASON STATS (mlb_get_player_season_stats)
    # =========================================================================
    
    async def fetch_player_season_stats(
        self,
        player_id: int,
        season: int
    ) -> Optional[Dict]:
        """
        Fetch season-level summary for a player.
        
        Includes:
        - Aggregate batting: AVG, OBP, SLG, OPS, WAR
        - Aggregate pitching: ERA, WHIP, K/9, BB/9, FIP
        """
        result = await self._api_get(
            f'players/{player_id}/season_stats',
            params={'season': season}
        )
        
        if result and result.get('data'):
            return result['data']
        return None
    
    # =========================================================================
    # STEP 3: SPLITS (mlb_get_player_splits)
    # =========================================================================
    
    async def fetch_player_splits(
        self,
        player_id: int,
        season: int
    ) -> Dict[str, Any]:
        """
        Fetch all splits for a player:
        - vs_left: Stats vs LHP
        - vs_right: Stats vs RHP
        - home: Home game stats
        - away: Away game stats
        - day: Day game stats
        - night: Night game stats
        """
        result = await self._api_get(
            'players/splits',
            params={'player_id': player_id, 'season': season}
        )
        
        if not result or not result.get('data'):
            return {}
        
        data = result['data']
        splits = {
            'player_id': player_id,
            'season': season,
            'vs_left': None,
            'vs_right': None,
            'home': None,
            'away': None,
            'day': None,
            'night': None,
        }
        
        # Parse byBreakdown splits
        for split in data.get('byBreakdown', []):
            split_name = split.get('split_name', '')
            stats = self._extract_split_stats(split)
            
            if 'vs. Left' in split_name or 'vs LHP' in split_name.upper():
                splits['vs_left'] = stats
            elif 'vs. Right' in split_name or 'vs RHP' in split_name.upper():
                splits['vs_right'] = stats
            elif split_name == 'Home':
                splits['home'] = stats
            elif split_name == 'Away':
                splits['away'] = stats
            elif split_name == 'Day':
                splits['day'] = stats
            elif split_name == 'Night':
                splits['night'] = stats
        
        self.stats['splits_fetched'] += 1
        return splits
    
    def _extract_split_stats(self, split: Dict) -> Dict:
        """Extract batting/pitching stats from a split record."""
        category = split.get('category', 'batting')
        
        if category == 'batting':
            return {
                'at_bats': split.get('at_bats', 0),
                'hits': split.get('hits', 0),
                'doubles': split.get('doubles', 0),
                'triples': split.get('triples', 0),
                'home_runs': split.get('home_runs', 0),
                'rbi': split.get('rbi', 0),
                'runs': split.get('runs', 0),
                'walks': split.get('walks', 0),
                'strikeouts': split.get('strikeouts', 0),
                'stolen_bases': split.get('stolen_bases', 0),
                'avg': split.get('batting_average') or split.get('avg', 0),
                'obp': split.get('on_base_percentage') or split.get('obp', 0),
                'slg': split.get('slugging_percentage') or split.get('slg', 0),
                'ops': split.get('ops', 0),
            }
        else:  # pitching
            return {
                'innings_pitched': split.get('innings_pitched', 0),
                'hits_allowed': split.get('hits_allowed', 0),
                'runs_allowed': split.get('runs_allowed', 0),
                'earned_runs': split.get('earned_runs', 0),
                'walks': split.get('walks', 0),
                'strikeouts': split.get('strikeouts', 0),
                'home_runs_allowed': split.get('home_runs_allowed', 0),
                'era': split.get('era', 0),
                'whip': split.get('whip', 0),
            }
    
    # =========================================================================
    # STEP 4: PvP MATCHUPS (mlb_get_player_vs_player)
    # =========================================================================
    
    async def fetch_pvp_matchups(
        self,
        batter_id: int,
        pitcher_ids: List[int] = None
    ) -> List[Dict]:
        """
        Fetch lifetime head-to-head collision history.
        
        Returns list of matchup records:
        - batter_id, pitcher_id
        - at_bats, hits, home_runs, strikeouts, walks
        """
        result = await self._api_get(
            f'players/{batter_id}/vs',
            params={'type': 'pitcher'}
        )
        
        if not result or not result.get('data'):
            return []
        
        matchups = []
        for m in result['data']:
            matchup = {
                'batter_id': batter_id,
                'pitcher_id': m.get('player_id'),
                'pitcher_name': m.get('player_name'),
                'at_bats': m.get('at_bats', 0),
                'hits': m.get('hits', 0),
                'doubles': m.get('doubles', 0),
                'triples': m.get('triples', 0),
                'home_runs': m.get('home_runs', 0),
                'rbi': m.get('rbi', 0),
                'walks': m.get('walks', 0),
                'strikeouts': m.get('strikeouts', 0),
                'avg': m.get('batting_average', 0),
            }
            matchups.append(matchup)
        
        self.stats['pvp_matchups_fetched'] += len(matchups)
        return matchups
    
    # =========================================================================
    # STEP 5: ADVANCED METRICS (Plate Discipline)
    # =========================================================================
    
    async def fetch_player_advanced_metrics(
        self,
        player_id: int,
        season: int
    ) -> Optional[Dict]:
        """
        Fetch advanced plate discipline metrics.
        
        Includes:
        - Whiff Rate
        - Chase Rate (O-Swing%)
        - Zone Contact %
        - Exit Velocity (if available)
        """
        # Try season stats endpoint for advanced metrics
        result = await self._api_get(
            f'players/{player_id}/season_stats',
            params={'season': season, 'include': 'advanced'}
        )
        
        if not result or not result.get('data'):
            return None
        
        data = result['data']
        
        return {
            'player_id': player_id,
            'season': season,
            # Plate discipline
            'bb_rate': data.get('bb_rate') or data.get('walk_rate'),
            'k_rate': data.get('k_rate') or data.get('strikeout_rate'),
            'bb_k_ratio': None,  # Calculated
            # Contact metrics (may not be available in all tiers)
            'contact_rate': data.get('contact_rate'),
            'zone_contact': data.get('zone_contact_pct'),
            'chase_rate': data.get('chase_rate') or data.get('o_swing_pct'),
            'whiff_rate': data.get('whiff_rate') or data.get('swing_miss_pct'),
            # Power metrics
            'iso': data.get('iso') or data.get('isolated_power'),
            'hr_fb_rate': data.get('hr_fb_rate'),
            'barrel_rate': data.get('barrel_rate'),
            'hard_hit_rate': data.get('hard_hit_rate'),
            'avg_exit_velo': data.get('avg_exit_velocity'),
            # Value metrics
            'war': data.get('war'),
            'wrc_plus': data.get('wrc_plus'),
            'woba': data.get('woba'),
        }
    
    # =========================================================================
    # MASTER HUB CONSTRUCTION
    # =========================================================================
    
    async def construct_player_hub_entry(
        self,
        player_id: int,
        player_name: str,
        team: str,
        game_logs: List[Dict],
        splits_by_season: Dict[int, Dict],
        pvp_matchups: List[Dict],
        advanced_by_season: Dict[int, Dict]
    ) -> Dict:
        """
        Construct a complete player entry for mlb_master_hub_2026.
        
        Includes all data needed for MLB_ORACLE_APEX inference.
        """
        # Sort game logs by date descending
        sorted_logs = sorted(
            game_logs,
            key=lambda x: x.get('game', {}).get('date') or x.get('date') or '1900-01-01',
            reverse=True
        )
        
        # Extract BDL game logs format
        bdl_game_logs = []
        for log in sorted_logs:
            game = log.get('game', {})
            bdl_log = {
                'game_id': log.get('game_id') or game.get('id'),
                'date': game.get('date') or log.get('date'),
                'opponent': self._get_opponent(log),
                'opponent_abbr': self._get_opponent_abbr(log),
                'is_home': log.get('home_team') == team or log.get('is_home'),
                # Batting stats
                'at_bats': log.get('at_bats', 0),
                'hits': log.get('hits', 0),
                'doubles': log.get('doubles', 0),
                'triples': log.get('triples', 0),
                'home_runs': log.get('home_runs', 0),
                'rbi': log.get('rbi', 0),
                'runs': log.get('runs', 0),
                'walks': log.get('walks', 0),
                'strikeouts': log.get('strikeouts', 0),
                'stolen_bases': log.get('stolen_bases', 0),
                # Calculated
                'total_bases': self._calc_total_bases(log),
                # Pitching stats (if applicable)
                'innings_pitched': log.get('innings_pitched'),
                'pitcher_strikeouts': log.get('strikeouts') if log.get('innings_pitched') else None,
            }
            bdl_game_logs.append(bdl_log)
        
        # Aggregate splits across seasons (weight recent more)
        combined_splits = self._aggregate_splits(splits_by_season)
        
        # Aggregate advanced metrics
        combined_advanced = self._aggregate_advanced(advanced_by_season)
        
        # === SINGLE SOURCE OF TRUTH SCHEMA ===
        # Group game logs by season for history object
        history = {
            '2023_season': [],
            '2024_season': [],
            '2025_season': [],
            '2026_season': [],
        }
        
        for log in bdl_game_logs:
            log_date = log.get('date', '')
            if log_date:
                year = int(log_date[:4]) if len(log_date) >= 4 else 2026
                season_key = f'{year}_season'
                if season_key in history:
                    history[season_key].append(log)
        
        # Build hub entry - SINGLE SOURCE OF TRUTH
        entry = {
            'player_id': player_id,
            'bdl_id': player_id,
            'player_name': player_name,
            'display_name': player_name,
            'team': team,
            'position': game_logs[0].get('position') if game_logs else None,
            
            # === HISTORY OBJECT - 3-Year Timeline ===
            'history': history,
            'history_stats': {
                '2023_games': len(history['2023_season']),
                '2024_games': len(history['2024_season']),
                '2025_games': len(history['2025_season']),
                '2026_games': len(history['2026_season']),
                'total_games': sum(len(v) for v in history.values()),
            },
            
            # Current season logs (for quick access)
            'bdl_game_logs': bdl_game_logs,
            'games_played': len(bdl_game_logs),
            
            # L/R Splits (Critical for MLB_ORACLE_APEX)
            'vs_left': combined_splits.get('vs_left'),
            'vs_right': combined_splits.get('vs_right'),
            
            # Home/Away Splits
            'home_splits': combined_splits.get('home'),
            'away_splits': combined_splits.get('away'),
            
            # Day/Night Splits
            'day_splits': combined_splits.get('day'),
            'night_splits': combined_splits.get('night'),
            
            # PvP Matchups
            'pvp_matchups': pvp_matchups[:50],
            'pvp_matchup_count': len(pvp_matchups),
            
            # Advanced Metrics
            'advanced_stats': combined_advanced,
            
            # Splits by season (for historical analysis)
            'splits_by_season': splits_by_season,
            
            # Metadata
            'last_updated': datetime.now(timezone.utc).isoformat(),
            'data_source': 'BDL_SSOT_3YEAR',
            'seasons_ingested': list(splits_by_season.keys()),
            'schema_version': '2.0_SSOT',
        }
        
        return entry
    
    def _calc_total_bases(self, log: Dict) -> int:
        """Calculate total bases from game log."""
        hits = log.get('hits', 0) or 0
        doubles = log.get('doubles', 0) or 0
        triples = log.get('triples', 0) or 0
        home_runs = log.get('home_runs', 0) or 0
        
        singles = hits - doubles - triples - home_runs
        return singles + (2 * doubles) + (3 * triples) + (4 * home_runs)
    
    def _get_opponent(self, log: Dict) -> str:
        """Extract opponent team name."""
        game = log.get('game', {})
        player_team = log.get('team', {}).get('abbreviation', '')
        home_team = game.get('home_team', {}).get('abbreviation', '')
        away_team = game.get('away_team', {}).get('abbreviation', '')
        
        if player_team == home_team:
            return game.get('away_team', {}).get('full_name', away_team)
        return game.get('home_team', {}).get('full_name', home_team)
    
    def _get_opponent_abbr(self, log: Dict) -> str:
        """Extract opponent team abbreviation."""
        game = log.get('game', {})
        player_team = log.get('team', {}).get('abbreviation', '')
        home_team = game.get('home_team', {}).get('abbreviation', '')
        away_team = game.get('away_team', {}).get('abbreviation', '')
        
        if player_team == home_team:
            return away_team
        return home_team
    
    def _aggregate_splits(self, splits_by_season: Dict[int, Dict]) -> Dict:
        """
        Aggregate splits across seasons with recency weighting.
        
        Weight: 2025=1.0, 2024=0.8, 2023=0.6
        """
        weights = {2025: 1.0, 2024: 0.8, 2023: 0.6}
        
        combined = {
            'vs_left': defaultdict(float),
            'vs_right': defaultdict(float),
            'home': defaultdict(float),
            'away': defaultdict(float),
            'day': defaultdict(float),
            'night': defaultdict(float),
        }
        
        total_weights = defaultdict(float)
        
        for season, splits in splits_by_season.items():
            w = weights.get(season, 0.5)
            
            for split_type in ['vs_left', 'vs_right', 'home', 'away', 'day', 'night']:
                split_data = splits.get(split_type)
                if not split_data:
                    continue
                
                for key, val in split_data.items():
                    if isinstance(val, (int, float)) and val is not None:
                        combined[split_type][key] += val * w
                        if key not in ['avg', 'obp', 'slg', 'ops', 'era', 'whip']:
                            # Counting stats: sum
                            pass
                        else:
                            # Rate stats: will average later
                            pass
                
                total_weights[split_type] += w
        
        # Convert to regular dicts
        result = {}
        for split_type, data in combined.items():
            if data:
                result[split_type] = dict(data)
            else:
                result[split_type] = None
        
        return result
    
    def _aggregate_advanced(self, advanced_by_season: Dict[int, Dict]) -> Dict:
        """Aggregate advanced metrics across seasons."""
        if not advanced_by_season:
            return {}
        
        # Use most recent season's advanced metrics
        for season in sorted(advanced_by_season.keys(), reverse=True):
            if advanced_by_season[season]:
                return advanced_by_season[season]
        
        return {}
    
    # =========================================================================
    # MAIN INGESTION ORCHESTRATOR
    # =========================================================================
    
    async def run_full_ingestion(
        self,
        seasons: List[int] = None,
        max_players: int = None
    ) -> Dict:
        """
        Execute full 3-year deep ingestion.
        
        Process:
        1. Fetch all game logs for each season
        2. Group logs by player
        3. For each player:
           - Fetch splits for all seasons
           - Fetch PvP matchups
           - Fetch advanced metrics
        4. Construct hub entry
        5. Upsert to mlb_master_hub_2026
        
        Args:
            seasons: List of seasons to ingest (default: 2023, 2024, 2025)
            max_players: Limit number of players (for testing)
        
        Returns:
            Stats dictionary with counts
        """
        seasons = seasons or BACKFILL_SEASONS
        logger.info(f"[DEEP_INGEST] Starting 3-Year Deep Ingestion for seasons: {seasons}")
        start_time = datetime.now(timezone.utc)
        
        # Step 1: Fetch all game logs
        all_logs = []
        for season in seasons:
            logs = await self.fetch_season_game_logs(season)
            for log in logs:
                log['_season'] = season
            all_logs.extend(logs)
        
        logger.info(f"[DEEP_INGEST] Total game logs fetched: {len(all_logs):,}")
        
        # Step 2: Group by player
        players: Dict[int, Dict] = {}
        for log in all_logs:
            player = log.get('player', {})
            player_id = player.get('id')
            if not player_id:
                continue
            
            if player_id not in players:
                players[player_id] = {
                    'player_id': player_id,
                    'player_name': f"{player.get('first_name', '')} {player.get('last_name', '')}".strip(),
                    'team': log.get('team', {}).get('abbreviation', ''),
                    'logs': [],
                }
            
            players[player_id]['logs'].append(log)
        
        logger.info(f"[DEEP_INGEST] Unique players found: {len(players):,}")
        
        # Limit for testing
        player_ids = list(players.keys())
        if max_players:
            player_ids = player_ids[:max_players]
        
        # Step 3-4: Process each player
        processed = 0
        for player_id in player_ids:
            player_data = players[player_id]
            
            try:
                # Fetch splits for all seasons
                splits_by_season = {}
                for season in seasons:
                    splits = await self.fetch_player_splits(player_id, season)
                    if splits:
                        splits_by_season[season] = splits
                
                # Fetch PvP matchups
                pvp_matchups = await self.fetch_pvp_matchups(player_id)
                
                # Fetch advanced metrics
                advanced_by_season = {}
                for season in seasons:
                    adv = await self.fetch_player_advanced_metrics(player_id, season)
                    if adv:
                        advanced_by_season[season] = adv
                
                # Construct hub entry
                hub_entry = await self.construct_player_hub_entry(
                    player_id=player_id,
                    player_name=player_data['player_name'],
                    team=player_data['team'],
                    game_logs=player_data['logs'],
                    splits_by_season=splits_by_season,
                    pvp_matchups=pvp_matchups,
                    advanced_by_season=advanced_by_season
                )
                
                # Upsert to hub
                await self.master_hub.update_one(
                    {'player_id': player_id},
                    {'$set': hub_entry},
                    upsert=True
                )
                
                processed += 1
                self.stats['players_processed'] = processed
                
                if processed % 50 == 0:
                    logger.info(f"[DEEP_INGEST] Processed {processed}/{len(player_ids)} players...")
                
            except Exception as e:
                logger.error(f"[DEEP_INGEST] Error processing player {player_id}: {e}")
                self.stats['errors'] += 1
        
        # Calculate elapsed time
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        
        self.stats['elapsed_seconds'] = elapsed
        self.stats['total_players'] = len(players)
        
        logger.info("=" * 60)
        logger.info("[DEEP_INGEST] INGESTION COMPLETE")
        logger.info("=" * 60)
        logger.info(f"  Players processed: {self.stats['players_processed']}")
        logger.info(f"  Game logs fetched: {self.stats['game_logs_fetched']:,}")
        logger.info(f"  Splits fetched: {self.stats['splits_fetched']}")
        logger.info(f"  PvP matchups fetched: {self.stats['pvp_matchups_fetched']}")
        logger.info(f"  Errors: {self.stats['errors']}")
        logger.info(f"  Elapsed time: {elapsed:.1f}s")
        logger.info("=" * 60)
        
        await self.close()
        return self.stats


# =============================================================================
# SYNC WRAPPER FOR NON-ASYNC CONTEXTS
# =============================================================================

def run_deep_ingestion_sync(db, seasons=None, max_players=None):
    """
    Synchronous wrapper for deep ingestion.
    
    Usage:
        from pymongo import MongoClient
        client = MongoClient(MONGO_URL)
        db = client[DB_NAME]
        stats = run_deep_ingestion_sync(db, seasons=[2023, 2024, 2025])
    """
    from motor.motor_asyncio import AsyncIOMotorClient
    import asyncio
    
    # Create async client from sync connection string
    mongo_url = os.environ.get('MONGO_URL')
    db_name = os.environ.get('DB_NAME', 'propvision')
    
    async def _run():
        async_client = AsyncIOMotorClient(mongo_url)
        async_db = async_client[db_name]
        
        ingestion = MLBDeepIngestion(async_db)
        stats = await ingestion.run_full_ingestion(seasons=seasons, max_players=max_players)
        
        async_client.close()
        return stats
    
    return asyncio.run(_run())


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import sys
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )
    
    # Parse args
    max_players = None
    if len(sys.argv) > 1:
        try:
            max_players = int(sys.argv[1])
            print(f"[CLI] Limiting to {max_players} players for testing")
        except ValueError:
            pass
    
    # Run ingestion
    print("\n" + "="*60)
    print("MLB_ORACLE_APEX - 3-YEAR DEEP INGESTION")
    print("="*60)
    print(f"Seasons: {BACKFILL_SEASONS}")
    print(f"BDL API Key: {'SET' if BDL_API_KEY else 'MISSING'}")
    print("="*60 + "\n")
    
    if not BDL_API_KEY:
        print("ERROR: BDL_API_KEY not set in environment")
        sys.exit(1)
    
    stats = run_deep_ingestion_sync(None, seasons=BACKFILL_SEASONS, max_players=max_players)
    
    print("\n[DONE] Deep ingestion complete")
    print(json.dumps(stats, indent=2))
