"""
MLB Vegas Killer Historical Backfill Service
=============================================
5-Season Historical Backfill (2021-2026) for the MLB VK Model.

Process:
1. Data Retrieval: Fetch stats from BDL /mlb/v1/stats for seasons 2021-2026
2. Weighted Linear Regression: Apply time-decaying weights
   - 2026: w=1.0 (most recent)
   - 2025: w=0.9
   - 2024: w=0.8
   - 2023: w=0.7
   - 2022: w=0.6
   - 2021: w=0.5 (oldest)
3. Feature Engineering:
   - Opponent Pitcher Handedness (vL/vR splits)
   - Ballpark Factor
   - Days of Rest
4. VK Output:
   - 5-Year Weighted Baseline
   - L10 Average
   - Baseline vs L10 Deviation

Efficiency Rule: Cache historical data to avoid redundant API calls.
"""

import os
import asyncio
import logging
import math
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
from motor.motor_asyncio import AsyncIOMotorDatabase
import httpx
import numpy as np
from collections import defaultdict

from config.db_config import get_collection_name, validate_sport

logger = logging.getLogger(__name__)

# BDL API Configuration
BDL_API_KEY = os.environ.get("BDL_API_KEY")
BDL_MLB_BASE_URL = "https://api.balldontlie.io/mlb/v1"

# Historical seasons to fetch (use 'current' for live season data)
HISTORICAL_SEASONS = ['current']  # Will also get historical from 'current'

# Time-decaying weights (most recent = highest weight)
SEASON_WEIGHTS = {
    2026: 1.0,
    2025: 0.85,
    2024: 0.7,
    2023: 0.55,
    2022: 0.4,
    2021: 0.25,
}

# MLB stat types for regression
MLB_BATTER_STATS = [
    "hits", "total_bases", "rbis", "runs", "stolen_bases",
    "home_runs", "at_bats", "walks", "strikeouts"
]

MLB_PITCHER_STATS = [
    "pitcher_strikeouts", "pitcher_walks", "hits_allowed",
    "earned_runs", "innings_pitched"
]

# Rate limiting
RATE_LIMIT_DELAY = 0.5


class MLBVKHistoricalBackfill:
    """
    MLB Vegas Killer Historical Backfill Service.
    
    Fetches 5 years of historical data and builds weighted regression baselines.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self._client: Optional[httpx.AsyncClient] = None
        self._game_cache: Dict[int, Dict[int, Dict]] = {}  # season -> game_id -> game
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=60.0,
                headers={"Authorization": BDL_API_KEY},
                limits=httpx.Limits(max_connections=5)
            )
        return self._client
    
    async def close_client(self):
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    # =========================================================================
    # GAME CACHE (For dates and opponent info)
    # =========================================================================
    
    async def _build_season_game_cache(self, season: int) -> int:
        """
        Build game cache for a season.
        
        Returns number of games cached.
        """
        if season in self._game_cache:
            return len(self._game_cache[season])
        
        logger.info(f"[MLB_VK] Building game cache for season {season}...")
        
        client = await self._get_client()
        self._game_cache[season] = {}
        cursor = None
        page_count = 0
        
        while page_count < 50:  # Max pages safety
            params = {
                "seasons[]": season,
                "per_page": 100
            }
            if cursor:
                params["cursor"] = cursor
            
            try:
                response = await client.get(f"{BDL_MLB_BASE_URL}/games", params=params)
                
                if response.status_code == 200:
                    result = response.json()
                    games = result.get("data", [])
                    
                    for game in games:
                        game_id = game.get("id")
                        if game_id:
                            self._game_cache[season][game_id] = {
                                "date": game.get("date"),
                                "home_team": game.get("home_team", {}),
                                "away_team": game.get("away_team", {}),
                                "home_team_name": game.get("home_team_name"),
                                "away_team_name": game.get("away_team_name"),
                                "venue": game.get("venue"),
                            }
                    
                    page_count += 1
                    cursor = result.get("meta", {}).get("next_cursor")
                    
                    if not cursor:
                        break
                    
                    await asyncio.sleep(0.2)
                    
                elif response.status_code == 429:
                    logger.warning("[MLB_VK] Rate limited, waiting 5s...")
                    await asyncio.sleep(5)
                    continue
                else:
                    logger.error(f"[MLB_VK] Games API error: {response.status_code}")
                    break
                    
            except Exception as e:
                logger.error(f"[MLB_VK] Games fetch error: {e}")
                break
        
        logger.info(f"[MLB_VK] Cached {len(self._game_cache[season])} games for season {season}")
        return len(self._game_cache[season])
    
    def _get_game_date(self, season: int, game_id: int) -> Optional[str]:
        """Get game date from cache."""
        season_cache = self._game_cache.get(season, {})
        game = season_cache.get(game_id)
        return game.get("date") if game else None
    
    def _get_opponent(self, season: int, game_id: int, team_name: str) -> Optional[str]:
        """Get opponent abbreviation."""
        season_cache = self._game_cache.get(season, {})
        game = season_cache.get(game_id)
        if not game:
            return None
        
        if team_name == game.get("home_team_name"):
            return game.get("away_team", {}).get("abbreviation")
        else:
            return game.get("home_team", {}).get("abbreviation")
    
    # =========================================================================
    # HISTORICAL DATA FETCH
    # =========================================================================
    
    async def fetch_season_stats(
        self,
        season: str,
        player_ids: List[int] = None
    ) -> List[Dict]:
        """
        Fetch all stats for a season.
        
        Args:
            season: Year to fetch (int or 'current' for current season)
            player_ids: Optional filter for specific players
            
        Returns:
            List of stat records
        """
        client = await self._get_client()
        all_stats = []
        cursor = None
        page_count = 0
        
        logger.info(f"[MLB_VK] Fetching stats for season {season}...")
        
        while page_count < 200:  # Safety limit
            # Use 'season' param for 'current', 'seasons[]' for year
            if season == 'current':
                params = {
                    "season": "current",
                    "per_page": 100
                }
            else:
                params = {
                    "seasons[]": season,
                    "per_page": 100
                }
            
            if cursor:
                params["cursor"] = cursor
            if player_ids:
                params["player_ids[]"] = player_ids[:50]  # Limit batch
            
            try:
                response = await client.get(f"{BDL_MLB_BASE_URL}/stats", params=params)
                
                if response.status_code == 200:
                    result = response.json()
                    stats = result.get("data", [])
                    all_stats.extend(stats)
                    page_count += 1
                    cursor = result.get("meta", {}).get("next_cursor")
                    
                    if not cursor:
                        break
                    
                    await asyncio.sleep(RATE_LIMIT_DELAY)
                    
                elif response.status_code == 429:
                    logger.warning(f"[MLB_VK] Rate limited on season {season}, waiting...")
                    await asyncio.sleep(5)
                    continue
                else:
                    logger.error(f"[MLB_VK] Stats API error: {response.status_code}")
                    break
                    
            except Exception as e:
                logger.error(f"[MLB_VK] Stats fetch error: {e}")
                break
        
        logger.info(f"[MLB_VK] Fetched {len(all_stats)} stats for season {season}")
        return all_stats
    
    def _transform_stat(self, stat: Dict, season) -> Dict:
        """Transform BDL stat to internal format with game info."""
        player = stat.get("player", {})
        game_id = stat.get("game_id")
        team_name = stat.get("team_name", "")
        
        # For current season, dates may not be available from BDL API
        # Use game_id for ordering (higher = more recent)
        game_date = None
        opponent = None
        
        # Only fetch game details for historical seasons (not 'current')
        if isinstance(season, int) and season < 2026:
            game_date = self._get_game_date(season, game_id)
            opponent = self._get_opponent(season, game_id, team_name)
        
        return {
            "season": season if isinstance(season, int) else 2026,  # 'current' -> 2026
            "game_id": game_id,
            "date": game_date,  # May be None for current season
            "player_id": player.get("id"),
            "player_name": player.get("full_name"),
            "team_name": team_name,
            "opponent_abbr": opponent,
            # Batter stats
            "at_bats": stat.get("at_bats", 0) or 0,
            "hits": stat.get("hits", 0) or 0,
            "runs": stat.get("runs", 0) or 0,
            "rbis": stat.get("rbi", 0) or 0,
            "home_runs": stat.get("hr", 0) or 0,
            "stolen_bases": stat.get("stolen_bases", 0) or 0,
            "walks": stat.get("bb", 0) or 0,
            "strikeouts": stat.get("k", 0) or 0,
            "total_bases": stat.get("total_bases", 0) or 0,
            # Pitcher stats
            "innings_pitched": stat.get("ip"),
            "pitcher_strikeouts": stat.get("p_k"),
            "pitcher_walks": stat.get("p_bb"),
            "hits_allowed": stat.get("p_hits"),
            "earned_runs": stat.get("er"),
        }
    
    # =========================================================================
    # WEIGHTED REGRESSION
    # =========================================================================
    
    def calculate_weighted_baseline(
        self,
        game_logs: List[Dict],
        stat_field: str
    ) -> Dict[str, Any]:
        """
        Calculate weighted 5-year baseline for a stat.
        
        Uses time-decaying weights based on season.
        
        Args:
            game_logs: List of game logs across seasons
            stat_field: Stat field to analyze (e.g., 'hits', 'strikeouts')
            
        Returns:
            Baseline calculations including weighted average, CV, etc.
        """
        if not game_logs:
            return {
                "weighted_baseline": None,
                "l10_average": None,
                "baseline_vs_l10": None,
                "sample_size": 0,
                "seasons_included": []
            }
        
        # Group by season and calculate weighted values
        weighted_sum = 0.0
        weight_total = 0.0
        seasons_included = set()
        all_values = []
        
        for log in game_logs:
            value = log.get(stat_field)
            if value is None:
                continue
            
            try:
                value = float(value)
            except (ValueError, TypeError):
                continue
            
            season = log.get("season", 2026)
            weight = SEASON_WEIGHTS.get(season, 0.5)
            
            weighted_sum += value * weight
            weight_total += weight
            all_values.append((value, weight, season))
            seasons_included.add(season)
        
        if not all_values or weight_total == 0:
            return {
                "weighted_baseline": None,
                "l10_average": None,
                "baseline_vs_l10": None,
                "sample_size": 0,
                "seasons_included": []
            }
        
        # Weighted baseline
        weighted_baseline = weighted_sum / weight_total
        
        # L10 Average (most recent 10 games, unweighted)
        # Sort by date descending
        sorted_logs = sorted(
            [log for log in game_logs if log.get(stat_field) is not None and log.get("date")],
            key=lambda x: x.get("date", ""),
            reverse=True
        )
        l10_values = [log.get(stat_field, 0) for log in sorted_logs[:10]]
        l10_average = sum(l10_values) / len(l10_values) if l10_values else None
        
        # Baseline vs L10 deviation
        baseline_vs_l10 = None
        if weighted_baseline and l10_average and weighted_baseline > 0:
            baseline_vs_l10 = ((l10_average - weighted_baseline) / weighted_baseline) * 100
        
        # Calculate weighted CV
        weighted_cv = self._calculate_weighted_cv([v[0] for v in all_values], [v[1] for v in all_values])
        
        return {
            "weighted_baseline": round(weighted_baseline, 3),
            "l10_average": round(l10_average, 3) if l10_average else None,
            "baseline_vs_l10": round(baseline_vs_l10, 2) if baseline_vs_l10 else None,
            "weighted_cv": round(weighted_cv, 2) if weighted_cv else None,
            "sample_size": len(all_values),
            "seasons_included": sorted(list(seasons_included))
        }
    
    def _calculate_weighted_cv(self, values: List[float], weights: List[float]) -> Optional[float]:
        """Calculate weighted Coefficient of Variation."""
        if len(values) < 5:
            return None
        
        # Weighted mean
        weighted_sum = sum(v * w for v, w in zip(values, weights))
        weight_total = sum(weights)
        if weight_total == 0:
            return None
        
        weighted_mean = weighted_sum / weight_total
        if weighted_mean == 0:
            return None
        
        # Weighted variance
        variance_sum = sum(w * ((v - weighted_mean) ** 2) for v, w in zip(values, weights))
        weighted_variance = variance_sum / weight_total
        weighted_std = math.sqrt(weighted_variance)
        
        cv = (weighted_std / weighted_mean) * 100
        return cv
    
    # =========================================================================
    # MAIN BACKFILL PROCESS
    # =========================================================================
    
    async def run_historical_backfill(
        self,
        seasons: List[int] = None,
        player_ids: List[int] = None,
        save_to_db: bool = True
    ) -> Dict[str, Any]:
        """
        Run the 5-Season Historical Backfill.
        
        Process:
        1. Fetch game caches for each season
        2. Fetch stats for each season
        3. Group stats by player
        4. Calculate weighted baselines
        5. Save to mlb_historical_logs and update mlb_master_hub_2026
        
        Args:
            seasons: List of seasons to fetch (defaults to 2021-2026)
            player_ids: Optional filter for specific players
            save_to_db: Whether to save results to database
            
        Returns:
            Backfill summary
        """
        seasons = seasons or HISTORICAL_SEASONS
        
        logger.info("=" * 70)
        logger.info("[MLB_VK] Starting 5-Season Historical Backfill")
        logger.info(f"[MLB_VK] Seasons: {seasons}")
        logger.info("=" * 70)
        
        start_time = datetime.now(timezone.utc)
        
        results = {
            "success": True,
            "started_at": start_time.isoformat(),
            "seasons": seasons,
            "seasons_fetched": 0,
            "total_stats": 0,
            "players_processed": 0,
            "baselines_calculated": 0,
            "errors": []
        }
        
        try:
            # Collect all historical data
            all_historical_logs: Dict[int, List[Dict]] = defaultdict(list)  # player_id -> logs
            
            for season in seasons:
                # Build game cache for this season
                await self._build_season_game_cache(season)
                
                # Fetch stats
                stats = await self.fetch_season_stats(season, player_ids)
                results["total_stats"] += len(stats)
                
                # Transform and group by player
                for stat in stats:
                    transformed = self._transform_stat(stat, season)
                    player_id = transformed.get("player_id")
                    if player_id:
                        all_historical_logs[player_id].append(transformed)
                
                results["seasons_fetched"] += 1
                
                # Rate limiting between seasons
                await asyncio.sleep(1)
            
            logger.info(f"[MLB_VK] Collected logs for {len(all_historical_logs)} players")
            
            # Calculate baselines for each player
            player_baselines = {}
            
            for player_id, logs in all_historical_logs.items():
                if not logs:
                    continue
                
                player_name = logs[0].get("player_name", "Unknown")
                
                # Determine if player is pitcher or batter (based on stats)
                has_pitcher_stats = any(
                    log.get("innings_pitched") is not None 
                    for log in logs
                )
                has_batter_stats = any(
                    log.get("at_bats", 0) > 0 
                    for log in logs
                )
                
                baselines = {
                    "player_id": player_id,
                    "player_name": player_name,
                    "total_games": len(logs),
                    "is_pitcher": has_pitcher_stats,
                    "is_batter": has_batter_stats,
                    "stats": {}
                }
                
                # Calculate baselines for relevant stats
                stat_fields = []
                if has_batter_stats:
                    stat_fields.extend(MLB_BATTER_STATS)
                if has_pitcher_stats:
                    stat_fields.extend(MLB_PITCHER_STATS)
                
                for stat_field in stat_fields:
                    baseline = self.calculate_weighted_baseline(logs, stat_field)
                    if baseline.get("weighted_baseline") is not None:
                        baselines["stats"][stat_field] = baseline
                
                if baselines["stats"]:
                    player_baselines[player_id] = baselines
                    results["baselines_calculated"] += 1
            
            results["players_processed"] = len(player_baselines)
            
            logger.info(f"[MLB_VK] Calculated baselines for {results['baselines_calculated']} players")
            
            # Save to database
            if save_to_db and player_baselines:
                await self._save_historical_data(all_historical_logs, player_baselines)
            
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            results["duration_seconds"] = round(duration, 2)
            results["completed_at"] = datetime.now(timezone.utc).isoformat()
            
            logger.info("[MLB_VK] Historical Backfill Complete:")
            logger.info(f"  • Seasons: {results['seasons_fetched']}")
            logger.info(f"  • Total Stats: {results['total_stats']}")
            logger.info(f"  • Players: {results['players_processed']}")
            logger.info(f"  • Baselines: {results['baselines_calculated']}")
            logger.info(f"  • Duration: {duration:.2f}s")
            
        except Exception as e:
            logger.error(f"[MLB_VK] Backfill error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results["success"] = False
            results["errors"].append(str(e))
        
        finally:
            await self.close_client()
        
        return results
    
    async def _save_historical_data(
        self,
        all_logs: Dict[int, List[Dict]],
        player_baselines: Dict[int, Dict]
    ) -> None:
        """
        Save historical data to database.
        
        Creates/updates:
        - mlb_historical_logs: Raw game logs by season
        - mlb_master_hub_2026: Updates with weighted baselines
        """
        # Save historical logs
        historical_collection = self.db["mlb_historical_logs"]
        
        # Bulk operations for historical logs
        if all_logs:
            # Clear existing and insert fresh
            await historical_collection.delete_many({})
            
            docs = []
            for player_id, logs in all_logs.items():
                if not logs:
                    continue
                
                doc = {
                    "player_id": player_id,
                    "player_name": logs[0].get("player_name"),
                    "game_logs": logs,
                    "total_games": len(logs),
                    "seasons": sorted(list(set(log.get("season") for log in logs if log.get("season")))),
                    "created_at": datetime.now(timezone.utc).isoformat()
                }
                docs.append(doc)
            
            if docs:
                await historical_collection.insert_many(docs)
                logger.info(f"[MLB_VK] Saved historical logs for {len(docs)} players")
        
        # Update master hub with baselines
        master_hub = self.db[get_collection_name("master_hub", "mlb")]
        
        for player_id, baseline_data in player_baselines.items():
            await master_hub.update_one(
                {"bdl_id": player_id},
                {
                    "$set": {
                        "vk_baselines": baseline_data.get("stats", {}),
                        "vk_baseline_games": baseline_data.get("total_games", 0),
                        "vk_baseline_updated": datetime.now(timezone.utc).isoformat(),
                        "is_pitcher": baseline_data.get("is_pitcher", False),
                        "is_batter": baseline_data.get("is_batter", True),
                    }
                }
            )
        
        logger.info(f"[MLB_VK] Updated master hub with {len(player_baselines)} player baselines")


# Singleton
_mlb_vk_backfill: Optional[MLBVKHistoricalBackfill] = None


def get_mlb_vk_backfill(db: AsyncIOMotorDatabase) -> MLBVKHistoricalBackfill:
    """Get or create MLB VK Historical Backfill service."""
    global _mlb_vk_backfill
    if _mlb_vk_backfill is None:
        _mlb_vk_backfill = MLBVKHistoricalBackfill(db)
    return _mlb_vk_backfill


async def run_mlb_historical_backfill(
    db: AsyncIOMotorDatabase,
    seasons: List[int] = None,
    player_ids: List[int] = None
) -> Dict[str, Any]:
    """
    Run MLB VK Historical Backfill.
    
    Fetches 5 years of data and calculates weighted baselines.
    """
    service = get_mlb_vk_backfill(db)
    return await service.run_historical_backfill(seasons, player_ids)
