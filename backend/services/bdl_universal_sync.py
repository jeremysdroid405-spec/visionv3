"""
BDL Universal Stats Sync Service (2026 AI-Native)
==================================================
Multi-sport stats synchronization using BallDontLie v1 API.

Endpoints:
- NBA: https://api.balldontlie.io/nba/v1/stats
- MLB: https://api.balldontlie.io/mlb/v1/stats

STRICT REQUIREMENT: Uses cursor-based pagination (next_cursor from meta object).
NO PAGE-BASED PAGINATION - This causes data loss.

Features:
- Sport-specific endpoints (nba/v1 vs mlb/v1)
- Batched player ID requests for efficiency
- Cursor-based pagination with circuit breakers
- Saves to sport-specific master_hub collections
"""

import os
import asyncio
import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorDatabase
import httpx

from config.db_config import get_collection_name, validate_sport, SPORT_CONFIG

logger = logging.getLogger(__name__)

# BDL API Configuration
BDL_API_KEY = os.environ.get("BDL_API_KEY")

# Sport-specific base URLs (2026 AI-Native endpoints)
BDL_BASE_URLS = {
    "nba": "https://api.balldontlie.io/nba/v1",
    "mlb": "https://api.balldontlie.io/mlb/v1"
}

# Season configuration
SEASONS = {
    "nba": 2025,  # 2025-26 season
    "mlb": 2026   # 2026 MLB season
}

# Batching configuration
BATCH_SIZE = 25  # Players per API request
PARALLEL_BATCHES = 2  # Concurrent batch groups
RATE_LIMIT_DELAY = 1.0  # Seconds between batch groups

# Circuit breaker - abort if too few results
MIN_EXPECTED_RESULTS = 5


class BDLUniversalSyncService:
    """
    Universal BallDontLie sync service for multiple sports.
    
    Uses the v1 API endpoints with strict cursor-based pagination.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with auth headers."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=45.0,
                headers={"Authorization": BDL_API_KEY},
                limits=httpx.Limits(max_connections=10)
            )
        return self._client
    
    async def close_client(self):
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    def _get_base_url(self, sport: str) -> str:
        """Get sport-specific BDL base URL."""
        sport = validate_sport(sport)
        if sport not in BDL_BASE_URLS:
            raise ValueError(f"No BDL endpoint for sport: {sport}")
        return BDL_BASE_URLS[sport]
    
    def _get_season(self, sport: str) -> int:
        """Get current season for sport."""
        return SEASONS.get(sport, 2025)
    
    # =========================================================================
    # CURSOR-BASED PAGINATION
    # =========================================================================
    
    async def _fetch_with_cursor(
        self,
        endpoint: str,
        sport: str,
        params: Dict[str, Any],
        max_pages: int = 100
    ) -> List[Dict]:
        """
        Fetch data using STRICT cursor-based pagination.
        
        CRITICAL: Uses next_cursor from meta object, NOT page numbers.
        
        Args:
            endpoint: API endpoint (e.g., '/stats', '/players')
            sport: Sport key ('nba' or 'mlb')
            params: Base query parameters
            max_pages: Maximum pages to fetch (circuit breaker)
            
        Returns:
            List of all data items across all pages
        """
        base_url = self._get_base_url(sport)
        url = f"{base_url}{endpoint}"
        client = await self._get_client()
        
        all_data = []
        cursor = None
        page_count = 0
        
        logger.info(f"[BDL_{sport.upper()}] Starting cursor-based fetch from {endpoint}")
        
        while page_count < max_pages:
            # Build request params
            request_params = {**params}
            if cursor:
                request_params["cursor"] = cursor
            
            try:
                response = await client.get(url, params=request_params)
                
                if response.status_code == 200:
                    result = response.json()
                    data = result.get("data", [])
                    all_data.extend(data)
                    page_count += 1
                    
                    # STRICT: Extract next_cursor from meta
                    meta = result.get("meta", {})
                    cursor = meta.get("next_cursor")
                    
                    logger.debug(
                        f"[BDL_{sport.upper()}] Page {page_count}: {len(data)} items, "
                        f"next_cursor={cursor is not None}"
                    )
                    
                    # Stop if no more pages
                    if not cursor:
                        logger.info(
                            f"[BDL_{sport.upper()}] Pagination complete: "
                            f"{page_count} pages, {len(all_data)} total items"
                        )
                        break
                    
                    # Rate limiting between pages
                    await asyncio.sleep(0.2)
                    
                elif response.status_code == 429:
                    # Rate limited - wait and retry
                    logger.warning(f"[BDL_{sport.upper()}] Rate limited, waiting 5s...")
                    await asyncio.sleep(5)
                    continue
                    
                elif response.status_code == 404:
                    logger.warning(f"[BDL_{sport.upper()}] Endpoint not found: {url}")
                    break
                    
                else:
                    logger.error(
                        f"[BDL_{sport.upper()}] API error {response.status_code}: "
                        f"{response.text[:200]}"
                    )
                    break
                    
            except Exception as e:
                logger.error(f"[BDL_{sport.upper()}] Request error: {e}")
                await asyncio.sleep(1)
                break
        
        return all_data
    
    # =========================================================================
    # STATS SYNC (Game Logs)
    # =========================================================================
    
    async def fetch_player_stats(
        self,
        player_ids: List[int],
        sport: str = "nba",
        season: int = None
    ) -> List[Dict]:
        """
        Fetch stats for a batch of players using cursor pagination.
        
        Args:
            player_ids: List of BDL player IDs
            sport: Sport key ('nba' or 'mlb')
            season: Season year (defaults to current)
            
        Returns:
            List of stat records
        """
        if not player_ids:
            return []
        
        season = season or self._get_season(sport)
        
        # Build params with array format for player_ids
        params = {
            "seasons[]": season,
            "per_page": 100
        }
        
        # Add player_ids as array params
        # httpx handles this correctly when we pass a list
        params["player_ids[]"] = player_ids
        
        # Fetch with cursor pagination
        stats = await self._fetch_with_cursor("/stats", sport, params)
        
        return stats
    
    async def sync_stats_batched(
        self,
        sport: str = "nba",
        player_ids: List[int] = None
    ) -> Dict[str, Any]:
        """
        Sync stats for all players in batches.
        
        If player_ids is None, fetches IDs from master_hub collection.
        
        Args:
            sport: Sport key ('nba' or 'mlb')
            player_ids: Optional list of specific player IDs
            
        Returns:
            Sync summary with counts and errors
        """
        sport = validate_sport(sport)
        season = self._get_season(sport)
        
        logger.info("=" * 70)
        logger.info(f"[BDL_SYNC] Starting {sport.upper()} Stats Sync")
        logger.info(f"[BDL_SYNC] Season: {season}")
        logger.info(f"[BDL_SYNC] Endpoint: {self._get_base_url(sport)}/stats")
        logger.info("=" * 70)
        
        sync_start = datetime.now(timezone.utc)
        
        results = {
            "success": True,
            "sport": sport,
            "season": season,
            "synced_at": sync_start.isoformat(),
            "players_requested": 0,
            "players_with_stats": 0,
            "total_game_logs": 0,
            "batches_processed": 0,
            "errors": []
        }
        
        try:
            # Get player IDs if not provided
            if player_ids is None:
                master_hub_collection = get_collection_name("master_hub", sport)
                cursor = self.db[master_hub_collection].find(
                    {"bdl_id": {"$exists": True}},
                    {"bdl_id": 1}
                )
                docs = await cursor.to_list(length=None)
                player_ids = [d["bdl_id"] for d in docs if d.get("bdl_id")]
            
            results["players_requested"] = len(player_ids)
            
            if not player_ids:
                logger.warning(f"[BDL_SYNC] No player IDs found for {sport.upper()}")
                return results
            
            logger.info(f"[BDL_SYNC] Processing {len(player_ids)} players in batches of {BATCH_SIZE}")
            
            # Process in batches
            all_stats = []
            players_with_stats = set()
            
            batches = [
                player_ids[i:i + BATCH_SIZE]
                for i in range(0, len(player_ids), BATCH_SIZE)
            ]
            
            for batch_idx, batch in enumerate(batches):
                logger.info(f"[BDL_SYNC] Processing batch {batch_idx + 1}/{len(batches)} ({len(batch)} players)")
                
                try:
                    # Fetch stats for batch
                    stats = await self.fetch_player_stats(batch, sport, season)
                    all_stats.extend(stats)
                    
                    # Track which players had stats
                    for stat in stats:
                        player = stat.get("player", {})
                        if player.get("id"):
                            players_with_stats.add(player["id"])
                    
                    results["batches_processed"] += 1
                    
                except Exception as e:
                    logger.error(f"[BDL_SYNC] Batch {batch_idx + 1} error: {e}")
                    results["errors"].append(f"Batch {batch_idx + 1}: {str(e)}")
                
                # Rate limiting between batches
                if batch_idx < len(batches) - 1:
                    await asyncio.sleep(RATE_LIMIT_DELAY)
            
            results["players_with_stats"] = len(players_with_stats)
            results["total_game_logs"] = len(all_stats)
            
            # Circuit breaker - don't wipe DB if suspiciously low results
            if len(all_stats) < MIN_EXPECTED_RESULTS and player_ids:
                logger.error(
                    f"[BDL_SYNC] CIRCUIT BREAKER: Only {len(all_stats)} stats returned "
                    f"for {len(player_ids)} players. Aborting save to prevent data wipe."
                )
                results["success"] = False
                results["errors"].append("Circuit breaker triggered - suspiciously low results")
                return results
            
            # Save to master hub
            if all_stats:
                await self._save_stats_to_master_hub(all_stats, sport)
                logger.info(f"[BDL_SYNC] Saved {len(all_stats)} game logs to master hub")
            
            duration = (datetime.now(timezone.utc) - sync_start).total_seconds()
            results["duration_seconds"] = round(duration, 2)
            
            logger.info(f"[BDL_SYNC] {sport.upper()} Sync Complete:")
            logger.info(f"  • Players: {results['players_with_stats']}/{results['players_requested']}")
            logger.info(f"  • Game Logs: {results['total_game_logs']}")
            logger.info(f"  • Duration: {duration:.2f}s")
            
        except Exception as e:
            logger.error(f"[BDL_SYNC] Sync error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results["success"] = False
            results["errors"].append(str(e))
        
        finally:
            await self.close_client()
        
        return results
    
    async def _save_stats_to_master_hub(
        self,
        stats: List[Dict],
        sport: str
    ) -> int:
        """
        Save game logs to sport-specific master_hub collection.
        
        Groups stats by player and updates the bdl_game_logs array.
        
        Args:
            stats: List of stat records
            sport: Sport key
            
        Returns:
            Number of players updated
        """
        master_hub_collection = get_collection_name("master_hub", sport)
        collection = self.db[master_hub_collection]
        
        # Group stats by player
        player_stats: Dict[int, List[Dict]] = {}
        for stat in stats:
            player = stat.get("player", {})
            player_id = player.get("id")
            if player_id:
                if player_id not in player_stats:
                    player_stats[player_id] = []
                
                # Transform to game log format
                game_log = self._transform_stat_to_game_log(stat, sport)
                player_stats[player_id].append(game_log)
        
        # Sort each player's logs by date (most recent first)
        for player_id in player_stats:
            player_stats[player_id].sort(
                key=lambda x: x.get("date") or "",
                reverse=True
            )
        
        # Update master hub
        updated_count = 0
        for player_id, logs in player_stats.items():
            result = await collection.update_one(
                {"bdl_id": player_id},
                {
                    "$set": {
                        "bdl_game_logs": logs,
                        "bdl_game_logs_count": len(logs),
                        "bdl_last_sync": datetime.now(timezone.utc).isoformat(),
                        "sport": sport
                    }
                },
                upsert=True
            )
            if result.modified_count > 0 or result.upserted_id:
                updated_count += 1
        
        return updated_count
    
    def _transform_stat_to_game_log(self, stat: Dict, sport: str) -> Dict:
        """
        Transform BDL stat object to game_log format.
        
        Handles sport-specific stat fields.
        """
        game = stat.get("game", {})
        player = stat.get("player", {})
        team = stat.get("team", {})
        
        # Common fields
        log = {
            "game_id": game.get("id"),
            "date": game.get("date"),
            "season": game.get("season"),
            "bdl_player_id": player.get("id"),
            "player_name": f"{player.get('first_name', '')} {player.get('last_name', '')}".strip(),
            "team_id": team.get("id"),
            "team_abbr": team.get("abbreviation"),
            "sport": sport
        }
        
        if sport == "nba":
            # NBA-specific stats
            log.update({
                "pts": stat.get("pts", 0),
                "reb": stat.get("reb", 0),
                "ast": stat.get("ast", 0),
                "stl": stat.get("stl", 0),
                "blk": stat.get("blk", 0),
                "turnover": stat.get("turnover", 0),
                "min": stat.get("min", "0"),
                "fgm": stat.get("fgm", 0),
                "fga": stat.get("fga", 0),
                "fg_pct": stat.get("fg_pct", 0),
                "fg3m": stat.get("fg3m", 0),
                "fg3a": stat.get("fg3a", 0),
                "fg3_pct": stat.get("fg3_pct", 0),
                "ftm": stat.get("ftm", 0),
                "fta": stat.get("fta", 0),
                "ft_pct": stat.get("ft_pct", 0),
                "oreb": stat.get("oreb", 0),
                "dreb": stat.get("dreb", 0),
                "pf": stat.get("pf", 0),
                "plus_minus": stat.get("plus_minus", 0),
            })
        elif sport == "mlb":
            # MLB-specific stats (pitcher and batter)
            # Batter stats
            log.update({
                "at_bats": stat.get("at_bats", 0),
                "hits": stat.get("hits", 0),
                "runs": stat.get("runs", 0),
                "rbis": stat.get("rbis", 0),
                "home_runs": stat.get("home_runs", 0),
                "stolen_bases": stat.get("stolen_bases", 0),
                "walks": stat.get("walks", 0),
                "strikeouts": stat.get("strikeouts", 0),
                "batting_avg": stat.get("batting_avg", 0),
                "obp": stat.get("obp", 0),
                "slg": stat.get("slg", 0),
                "total_bases": stat.get("total_bases", 0),
                # Pitcher stats
                "innings_pitched": stat.get("innings_pitched", 0),
                "pitcher_strikeouts": stat.get("pitcher_strikeouts", 0),
                "pitcher_walks": stat.get("pitcher_walks", 0),
                "hits_allowed": stat.get("hits_allowed", 0),
                "earned_runs": stat.get("earned_runs", 0),
                "era": stat.get("era", 0),
                "whip": stat.get("whip", 0),
            })
        
        return log
    
    # =========================================================================
    # PLAYERS SYNC
    # =========================================================================
    
    async def sync_players(self, sport: str = "nba") -> Dict[str, Any]:
        """
        Sync all active players for a sport.
        
        Uses /players/active endpoint with cursor pagination.
        
        Args:
            sport: Sport key ('nba' or 'mlb')
            
        Returns:
            Sync summary
        """
        sport = validate_sport(sport)
        
        logger.info(f"[BDL_SYNC] Syncing {sport.upper()} players...")
        
        params = {"per_page": 100}
        
        # Use /players/active for current roster
        players = await self._fetch_with_cursor("/players/active", sport, params)
        
        if not players:
            logger.warning(f"[BDL_SYNC] No players returned for {sport.upper()}")
            return {"success": False, "players_count": 0}
        
        # Save to master hub
        master_hub_collection = get_collection_name("master_hub", sport)
        collection = self.db[master_hub_collection]
        
        saved_count = 0
        for player in players:
            player_id = player.get("id")
            if not player_id:
                continue
            
            # Build player document
            first_name = player.get("first_name", "")
            last_name = player.get("last_name", "")
            display_name = f"{first_name} {last_name}".strip()
            
            team = player.get("team", {})
            
            doc = {
                "bdl_id": player_id,
                "display_name": display_name,
                "first_name": first_name,
                "last_name": last_name,
                "position": player.get("position"),
                "team_id": team.get("id"),
                "team_abbr": team.get("abbreviation"),
                "team_name": team.get("full_name"),
                "sport": sport,
                "bdl_last_sync": datetime.now(timezone.utc).isoformat()
            }
            
            # Sport-specific fields
            if sport == "nba":
                doc.update({
                    "height": player.get("height"),
                    "weight": player.get("weight"),
                    "jersey_number": player.get("jersey_number"),
                    "college": player.get("college"),
                    "country": player.get("country"),
                    "draft_year": player.get("draft_year"),
                    "draft_round": player.get("draft_round"),
                    "draft_number": player.get("draft_number"),
                })
            elif sport == "mlb":
                doc.update({
                    "bats": player.get("bats"),
                    "throws": player.get("throws"),
                    "primary_position": player.get("primary_position"),
                    "jersey_number": player.get("jersey_number"),
                    "birth_date": player.get("birth_date"),
                })
            
            result = await collection.update_one(
                {"bdl_id": player_id},
                {"$set": doc},
                upsert=True
            )
            
            if result.modified_count > 0 or result.upserted_id:
                saved_count += 1
        
        logger.info(f"[BDL_SYNC] Saved {saved_count} {sport.upper()} players to master hub")
        
        return {
            "success": True,
            "sport": sport,
            "players_count": len(players),
            "saved_count": saved_count,
            "collection": master_hub_collection
        }


# Singleton instance
_bdl_universal_service: Optional[BDLUniversalSyncService] = None


def get_bdl_universal_service(db: AsyncIOMotorDatabase) -> BDLUniversalSyncService:
    """Get or create the universal BDL sync service."""
    global _bdl_universal_service
    if _bdl_universal_service is None:
        _bdl_universal_service = BDLUniversalSyncService(db)
    return _bdl_universal_service


async def run_bdl_universal_sync(
    db: AsyncIOMotorDatabase,
    sport: str = "nba",
    include_players: bool = True,
    include_stats: bool = True
) -> Dict[str, Any]:
    """
    Run complete BDL sync for a sport.
    
    Args:
        db: MongoDB database
        sport: Sport key ('nba' or 'mlb')
        include_players: Sync player roster
        include_stats: Sync game logs/stats
        
    Returns:
        Combined sync results
    """
    service = get_bdl_universal_service(db)
    
    results = {
        "sport": sport,
        "synced_at": datetime.now(timezone.utc).isoformat()
    }
    
    if include_players:
        results["players"] = await service.sync_players(sport)
    
    if include_stats:
        results["stats"] = await service.sync_stats_batched(sport)
    
    return results
