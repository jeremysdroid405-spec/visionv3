"""
MLB Advanced Stats Sync Service
================================
Fetches advanced stats from BDL for the VK Regression Model.

Data Sources:
1. /players/splits - vL/vR splits, park factors, opponent splits
2. /season_stats - WAR, OPS, WHIP, K/9, etc.
3. /stats - Game logs for Days of Rest calculation

Advanced Stats Collected:
- vL/vR splits (vs Left-Handed / Right-Handed pitchers)
- Home/Away splits
- Day/Night splits
- Park-specific performance
- Opponent-specific performance
- Season aggregates (WAR, OPS, WHIP, K/9, FIP)
- Days of Rest (calculated from game dates)
"""

import os
import asyncio
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from motor.motor_asyncio import AsyncIOMotorDatabase
import httpx

from config.db_config import get_collection_name

logger = logging.getLogger(__name__)

# BDL API Configuration
BDL_API_KEY = os.environ.get("BDL_API_KEY")
BDL_MLB_BASE_URL = "https://api.balldontlie.io/mlb/v1"

# Seasons to fetch
HISTORICAL_SEASONS = [2021, 2022, 2023, 2024, 2025, 2026]

# Rate limiting
RATE_LIMIT_DELAY = 0.3
BATCH_SIZE = 50


class MLBAdvancedStatsSync:
    """
    MLB Advanced Stats Sync Service.
    
    Fetches splits, season stats, and calculates derived metrics.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self._client: Optional[httpx.AsyncClient] = None
    
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
    # SPLITS DATA (vL/vR, Home/Away, Park, Opponent)
    # =========================================================================
    
    async def fetch_player_splits(
        self,
        player_id: int,
        season: int
    ) -> Dict[str, Any]:
        """
        Fetch all splits for a player in a season.
        
        Returns splits organized by category:
        - vs_left: Stats vs left-handed pitchers
        - vs_right: Stats vs right-handed pitchers
        - home: Home game stats
        - away: Away game stats
        - day: Day game stats
        - night: Night game stats
        - by_park: Dict of park-specific stats
        - by_opponent: Dict of opponent-specific stats
        """
        client = await self._get_client()
        
        try:
            response = await client.get(
                f"{BDL_MLB_BASE_URL}/players/splits",
                params={"player_id": player_id, "season": season}
            )
            
            if response.status_code == 200:
                data = response.json().get("data", {})
                return self._parse_splits(data, player_id, season)
            elif response.status_code == 429:
                logger.warning(f"[ADV_STATS] Rate limited on splits for player {player_id}")
                await asyncio.sleep(5)
                return {}
            else:
                return {}
                
        except Exception as e:
            logger.error(f"[ADV_STATS] Splits fetch error for player {player_id}: {e}")
            return {}
    
    def _parse_splits(self, data: Dict, player_id: int, season: int) -> Dict[str, Any]:
        """Parse splits data into structured format."""
        result = {
            "player_id": player_id,
            "season": season,
            "vs_left": None,
            "vs_right": None,
            "home": None,
            "away": None,
            "day": None,
            "night": None,
            "by_park": {},
            "by_opponent": {},
            "by_count": {},
            "by_situation": {}
        }
        
        # Process byBreakdown (vL/vR, Home/Away, Day/Night)
        for split in data.get("byBreakdown", []):
            split_name = split.get("split_name", "")
            category = split.get("category", "batting")
            
            stats = self._extract_split_stats(split, category)
            
            if "vs. Left" in split_name:
                result["vs_left"] = stats
            elif "vs. Right" in split_name:
                result["vs_right"] = stats
            elif split_name == "Home":
                result["home"] = stats
            elif split_name == "Away":
                result["away"] = stats
            elif split_name == "Day":
                result["day"] = stats
            elif split_name == "Night":
                result["night"] = stats
        
        # Process byArena (Park factors)
        for split in data.get("byArena", []):
            park_name = split.get("split_name", "")
            if park_name and park_name != "All Splits":
                category = split.get("category", "batting")
                result["by_park"][park_name] = self._extract_split_stats(split, category)
        
        # Process byOpponent
        for split in data.get("byOpponent", []):
            opponent = split.get("split_name", "")
            if opponent:
                category = split.get("category", "batting")
                result["by_opponent"][opponent] = self._extract_split_stats(split, category)
        
        # Process byCount
        for split in data.get("byCount", []):
            count = split.get("split_name", "")
            if count:
                category = split.get("category", "batting")
                result["by_count"][count] = self._extract_split_stats(split, category)
        
        # Process bySituation
        for split in data.get("bySituation", []):
            situation = split.get("split_name", "")
            if situation:
                category = split.get("category", "batting")
                result["by_situation"][situation] = self._extract_split_stats(split, category)
        
        return result
    
    def _extract_split_stats(self, split: Dict, category: str) -> Dict[str, Any]:
        """Extract relevant stats from a split record."""
        if category == "batting":
            return {
                "category": "batting",
                "at_bats": split.get("at_bats"),
                "runs": split.get("runs"),
                "hits": split.get("hits"),
                "doubles": split.get("doubles"),
                "triples": split.get("triples"),
                "home_runs": split.get("home_runs"),
                "rbis": split.get("rbis"),
                "walks": split.get("walks"),
                "strikeouts": split.get("strikeouts"),
                "stolen_bases": split.get("stolen_bases"),
                "avg": split.get("avg"),
                "obp": split.get("obp"),
                "slg": split.get("slg"),
                "ops": split.get("ops"),
            }
        else:  # pitching
            return {
                "category": "pitching",
                "era": split.get("era"),
                "wins": split.get("wins"),
                "losses": split.get("losses"),
                "saves": split.get("saves"),
                "games_played": split.get("games_played"),
                "games_started": split.get("games_started"),
                "innings_pitched": split.get("innings_pitched"),
                "hits_allowed": split.get("hits_allowed"),
                "runs_allowed": split.get("runs_allowed"),
                "earned_runs": split.get("earned_runs"),
                "home_runs_allowed": split.get("home_runs_allowed"),
                "walks_allowed": split.get("walks_allowed"),
                "strikeouts_pitched": split.get("strikeouts_pitched"),
                "opponent_avg": split.get("opponent_avg"),
            }
    
    # =========================================================================
    # SEASON STATS (WAR, OPS, WHIP, K/9, etc.)
    # =========================================================================
    
    async def fetch_season_stats(
        self,
        season: int,
        player_ids: List[int] = None
    ) -> List[Dict]:
        """
        Fetch season aggregated stats with advanced metrics.
        
        Includes: WAR, OPS, WHIP, K/9, FIP, etc.
        """
        client = await self._get_client()
        all_stats = []
        cursor = None
        page_count = 0
        
        logger.info(f"[ADV_STATS] Fetching season stats for {season}...")
        
        while page_count < 100:
            params = {
                "season": season,
                "per_page": 100
            }
            if cursor:
                params["cursor"] = cursor
            if player_ids:
                # BDL may not support player_ids filter on season_stats
                pass
            
            try:
                response = await client.get(
                    f"{BDL_MLB_BASE_URL}/season_stats",
                    params=params
                )
                
                if response.status_code == 200:
                    result = response.json()
                    stats = result.get("data", [])
                    
                    for stat in stats:
                        parsed = self._parse_season_stat(stat)
                        if parsed:
                            all_stats.append(parsed)
                    
                    page_count += 1
                    cursor = result.get("meta", {}).get("next_cursor")
                    
                    if not cursor:
                        break
                    
                    await asyncio.sleep(RATE_LIMIT_DELAY)
                    
                elif response.status_code == 429:
                    logger.warning("[ADV_STATS] Rate limited on season stats")
                    await asyncio.sleep(5)
                    continue
                else:
                    logger.error(f"[ADV_STATS] Season stats error: {response.status_code}")
                    break
                    
            except Exception as e:
                logger.error(f"[ADV_STATS] Season stats fetch error: {e}")
                break
        
        logger.info(f"[ADV_STATS] Fetched {len(all_stats)} season stats for {season}")
        return all_stats
    
    def _parse_season_stat(self, stat: Dict) -> Optional[Dict]:
        """Parse season stat record with advanced metrics."""
        player = stat.get("player", {})
        if not player:
            return None
        
        return {
            "player_id": player.get("id"),
            "player_name": player.get("full_name"),
            "team_abbr": player.get("team", {}).get("abbreviation"),
            "position": player.get("position"),
            "bats_throws": player.get("bats_throws"),
            "season": stat.get("season"),
            # Batting advanced stats
            "batting": {
                "games_played": stat.get("batting_gp"),
                "at_bats": stat.get("batting_ab"),
                "runs": stat.get("batting_r"),
                "hits": stat.get("batting_h"),
                "avg": stat.get("batting_avg"),
                "doubles": stat.get("batting_2b"),
                "triples": stat.get("batting_3b"),
                "home_runs": stat.get("batting_hr"),
                "rbis": stat.get("batting_rbi"),
                "total_bases": stat.get("batting_tb"),
                "walks": stat.get("batting_bb"),
                "strikeouts": stat.get("batting_so"),
                "stolen_bases": stat.get("batting_sb"),
                "obp": stat.get("batting_obp"),
                "slg": stat.get("batting_slg"),
                "ops": stat.get("batting_ops"),
                "war": stat.get("batting_war"),
            },
            # Pitching advanced stats
            "pitching": {
                "games_played": stat.get("pitching_gp"),
                "games_started": stat.get("pitching_gs"),
                "quality_starts": stat.get("pitching_qs"),
                "wins": stat.get("pitching_w"),
                "losses": stat.get("pitching_l"),
                "era": stat.get("pitching_era"),
                "saves": stat.get("pitching_sv"),
                "holds": stat.get("pitching_hld"),
                "innings_pitched": stat.get("pitching_ip"),
                "hits_allowed": stat.get("pitching_h"),
                "earned_runs": stat.get("pitching_er"),
                "home_runs_allowed": stat.get("pitching_hr"),
                "walks": stat.get("pitching_bb"),
                "whip": stat.get("pitching_whip"),
                "strikeouts": stat.get("pitching_k"),
                "k_per_9": stat.get("pitching_k_per_9"),
                "war": stat.get("pitching_war"),
            },
            # Fielding stats
            "fielding": {
                "games_played": stat.get("fielding_gp"),
                "games_started": stat.get("fielding_gs"),
                "fip": stat.get("fielding_fip"),
                "total_chances": stat.get("fielding_tc"),
                "putouts": stat.get("fielding_po"),
                "assists": stat.get("fielding_a"),
                "fielding_pct": stat.get("fielding_fp"),
                "errors": stat.get("fielding_e"),
                "double_plays": stat.get("fielding_dp"),
                "range_factor": stat.get("fielding_rf"),
                "defensive_war": stat.get("fielding_dwar"),
            }
        }
    
    # =========================================================================
    # DAYS OF REST CALCULATION
    # =========================================================================
    
    def calculate_days_of_rest(self, game_logs: List[Dict]) -> List[Dict]:
        """
        Calculate days of rest for each game in the logs.
        
        Adds 'days_rest' field to each game log.
        """
        if not game_logs:
            return game_logs
        
        # Sort by date
        sorted_logs = sorted(
            [log for log in game_logs if log.get("date")],
            key=lambda x: x.get("date", "")
        )
        
        for i, log in enumerate(sorted_logs):
            if i == 0:
                log["days_rest"] = None  # Unknown for first game
            else:
                try:
                    current_date = datetime.fromisoformat(log["date"].replace("Z", "+00:00"))
                    prev_date = datetime.fromisoformat(sorted_logs[i-1]["date"].replace("Z", "+00:00"))
                    days_rest = (current_date - prev_date).days - 1
                    log["days_rest"] = max(0, days_rest)
                except (ValueError, TypeError):
                    log["days_rest"] = None
        
        return sorted_logs
    
    # =========================================================================
    # MAIN SYNC PROCESS
    # =========================================================================
    
    async def run_advanced_stats_sync(
        self,
        seasons: List[int] = None,
        include_splits: bool = True,
        include_season_stats: bool = True,
        player_limit: int = None
    ) -> Dict[str, Any]:
        """
        Run full advanced stats sync.
        
        Process:
        1. Get all players from master hub
        2. Fetch splits for each player (vL/vR, park, opponent)
        3. Fetch season stats (WAR, OPS, WHIP, etc.)
        4. Calculate days of rest from game logs
        5. Update master hub with advanced stats
        
        Args:
            seasons: List of seasons to sync (default: 2021-2026)
            include_splits: Fetch player splits
            include_season_stats: Fetch season aggregates
            player_limit: Limit number of players (for testing)
            
        Returns:
            Sync summary
        """
        seasons = seasons or HISTORICAL_SEASONS
        
        logger.info("=" * 70)
        logger.info("[ADV_STATS] Starting MLB Advanced Stats Sync")
        logger.info(f"[ADV_STATS] Seasons: {seasons}")
        logger.info(f"[ADV_STATS] Include Splits: {include_splits}")
        logger.info(f"[ADV_STATS] Include Season Stats: {include_season_stats}")
        logger.info("=" * 70)
        
        start_time = datetime.now(timezone.utc)
        
        results = {
            "success": True,
            "started_at": start_time.isoformat(),
            "seasons": seasons,
            "players_processed": 0,
            "splits_fetched": 0,
            "season_stats_fetched": 0,
            "days_rest_calculated": 0,
            "errors": []
        }
        
        try:
            master_hub = self.db[get_collection_name("master_hub", "mlb")]
            
            # Get all players with BDL IDs
            query = {"bdl_id": {"$exists": True, "$ne": None}}
            if player_limit:
                players = await master_hub.find(query, {"_id": 0, "bdl_id": 1, "display_name": 1, "bdl_game_logs": 1}).limit(player_limit).to_list(length=player_limit)
            else:
                players = await master_hub.find(query, {"_id": 0, "bdl_id": 1, "display_name": 1, "bdl_game_logs": 1}).to_list(length=None)
            
            logger.info(f"[ADV_STATS] Found {len(players)} players to process")
            
            # Fetch season stats for all seasons first
            season_stats_map: Dict[int, Dict[int, Dict]] = {}  # season -> player_id -> stats
            
            if include_season_stats:
                for season in seasons:
                    stats = await self.fetch_season_stats(season)
                    season_stats_map[season] = {s["player_id"]: s for s in stats if s.get("player_id")}
                    results["season_stats_fetched"] += len(stats)
                    await asyncio.sleep(1)  # Rate limit between seasons
            
            # Process each player
            for i, player in enumerate(players):
                player_id = player.get("bdl_id")
                
                if not player_id:
                    continue
                
                update_data = {
                    "advanced_stats": {
                        "synced_at": datetime.now(timezone.utc).isoformat(),
                        "seasons": seasons
                    }
                }
                
                # Fetch splits for each season
                if include_splits:
                    all_splits = {}
                    for season in seasons:
                        splits = await self.fetch_player_splits(player_id, season)
                        if splits and (splits.get("vs_left") or splits.get("vs_right")):
                            all_splits[str(season)] = splits
                            results["splits_fetched"] += 1
                        await asyncio.sleep(RATE_LIMIT_DELAY)
                    
                    if all_splits:
                        update_data["advanced_stats"]["splits"] = all_splits
                        
                        # Extract latest vL/vR for quick access
                        latest_season = str(max(int(s) for s in all_splits.keys()))
                        latest_splits = all_splits.get(latest_season, {})
                        
                        update_data["vs_left"] = latest_splits.get("vs_left")
                        update_data["vs_right"] = latest_splits.get("vs_right")
                        update_data["home_splits"] = latest_splits.get("home")
                        update_data["away_splits"] = latest_splits.get("away")
                
                # Add season stats
                if include_season_stats:
                    player_season_stats = {}
                    for season in seasons:
                        if season in season_stats_map and player_id in season_stats_map[season]:
                            player_season_stats[str(season)] = season_stats_map[season][player_id]
                    
                    if player_season_stats:
                        update_data["advanced_stats"]["season_stats"] = player_season_stats
                        
                        # Extract latest WAR, OPS for quick access
                        latest_season = str(max(int(s) for s in player_season_stats.keys()))
                        latest_stats = player_season_stats.get(latest_season, {})
                        
                        batting = latest_stats.get("batting", {})
                        pitching = latest_stats.get("pitching", {})
                        
                        update_data["war"] = batting.get("war") or pitching.get("war")
                        update_data["ops"] = batting.get("ops")
                        update_data["whip"] = pitching.get("whip")
                        update_data["k_per_9"] = pitching.get("k_per_9")
                        update_data["era"] = pitching.get("era")
                
                # Calculate days of rest from existing game logs
                game_logs = player.get("bdl_game_logs", [])
                if game_logs:
                    updated_logs = self.calculate_days_of_rest(game_logs)
                    update_data["bdl_game_logs"] = updated_logs
                    results["days_rest_calculated"] += 1
                
                # Update player in master hub
                await master_hub.update_one(
                    {"bdl_id": player_id},
                    {"$set": update_data}
                )
                
                results["players_processed"] += 1
                
                # Progress logging
                if (i + 1) % 50 == 0:
                    logger.info(f"[ADV_STATS] Processed {i + 1}/{len(players)} players...")
            
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            results["duration_seconds"] = round(duration, 2)
            results["completed_at"] = datetime.now(timezone.utc).isoformat()
            
            logger.info("[ADV_STATS] Advanced Stats Sync Complete:")
            logger.info(f"  • Players Processed: {results['players_processed']}")
            logger.info(f"  • Splits Fetched: {results['splits_fetched']}")
            logger.info(f"  • Season Stats Fetched: {results['season_stats_fetched']}")
            logger.info(f"  • Days Rest Calculated: {results['days_rest_calculated']}")
            logger.info(f"  • Duration: {duration:.2f}s")
            
        except Exception as e:
            logger.error(f"[ADV_STATS] Sync error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results["success"] = False
            results["errors"].append(str(e))
        
        finally:
            await self.close_client()
        
        return results


# Singleton
_mlb_advanced_stats: Optional[MLBAdvancedStatsSync] = None


def get_mlb_advanced_stats_sync(db: AsyncIOMotorDatabase) -> MLBAdvancedStatsSync:
    """Get or create MLB Advanced Stats Sync service."""
    global _mlb_advanced_stats
    if _mlb_advanced_stats is None:
        _mlb_advanced_stats = MLBAdvancedStatsSync(db)
    return _mlb_advanced_stats


async def run_mlb_advanced_stats_sync(
    db: AsyncIOMotorDatabase,
    seasons: List[int] = None,
    include_splits: bool = True,
    include_season_stats: bool = True,
    player_limit: int = None
) -> Dict[str, Any]:
    """
    Run MLB Advanced Stats Sync.
    
    Fetches vL/vR splits, season stats (WAR, OPS, WHIP), and calculates days of rest.
    """
    service = get_mlb_advanced_stats_sync(db)
    return await service.run_advanced_stats_sync(
        seasons=seasons,
        include_splits=include_splits,
        include_season_stats=include_season_stats,
        player_limit=player_limit
    )
