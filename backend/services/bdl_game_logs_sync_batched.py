"""
BDL Game Logs Sync Service - BATCHED VERSION
=============================================
Uses BallDontLie's /stats endpoint with BATCHED player_ids[] requests.

PERFORMANCE OPTIMIZATION:
- Groups up to 25 player_ids per request (API limit is ~50, using 25 for safety)
- Uses asyncio.gather for parallel batch execution
- Bulk MongoDB updates after each batch
- Target: 500 players in ~30 seconds (vs 3+ minutes sequential)

API Endpoint: GET https://api.balldontlie.io/v1/stats
- player_ids[]: Filter by multiple player IDs in array format
- seasons[]: Filter by season (e.g., 2025 for 2025-26 season)
- per_page: Max 100 results per request
- cursor: Pagination cursor
"""

import httpx
import asyncio
import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorDatabase
import os

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

BDL_BASE_URL = "https://api.balldontlie.io/v1"
CURRENT_SEASON = 2025  # 2025-26 season

# BATCHING CONFIGURATION
BATCH_SIZE = 25  # Players per API request (safe limit under 50)
PARALLEL_BATCHES = 2  # Reduced to 2 to avoid rate limiting
RATE_LIMIT_DELAY = 1.0  # Increased to 1s between parallel batch groups


class BDLGameLogsSyncBatched:
    """Batched sync of game logs from BallDontLie API."""
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.hub = db[COLL("master_hub", "nba")]
        self.api_key = os.environ.get("BDL_API_KEY", "ad5544be-9969-434b-9389-2b7cf658c8e0")
        self._client: Optional[httpx.AsyncClient] = None
        
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=45.0,
                headers={"Authorization": self.api_key},
                limits=httpx.Limits(max_connections=10)
            )
        return self._client
    
    async def _close_client(self):
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    async def _fetch_batch_stats(
        self, 
        player_ids: List[int], 
        season: int = CURRENT_SEASON
    ) -> Tuple[List[Dict], List[int]]:
        """
        Fetch stats for a BATCH of players in a single API call.
        
        Args:
            player_ids: List of BDL player IDs (max ~50)
            season: NBA season year
            
        Returns:
            Tuple of (all_stats, successful_player_ids)
        """
        all_stats = []
        cursor = None
        client = await self._get_client()
        
        # Build player_ids[] params - BDL expects array format
        params = {
            "seasons[]": season,
            "per_page": 100
        }
        
        # Add each player_id as separate param (array format)
        for pid in player_ids:
            if "player_ids[]" not in params:
                params["player_ids[]"] = []
            # httpx handles array params correctly
        
        try:
            while True:
                # Build URL with player_ids array
                url = f"{BDL_BASE_URL}/stats"
                
                # Construct query string manually for array params
                query_parts = [f"seasons[]={season}", f"per_page=100"]
                for pid in player_ids:
                    query_parts.append(f"player_ids[]={pid}")
                if cursor:
                    query_parts.append(f"cursor={cursor}")
                
                full_url = f"{url}?{'&'.join(query_parts)}"
                
                response = await client.get(full_url)
                
                if response.status_code == 200:
                    data = response.json()
                    stats = data.get("data", [])
                    all_stats.extend(stats)
                    
                    # Check for pagination
                    meta = data.get("meta", {})
                    cursor = meta.get("next_cursor")
                    
                    if not cursor or len(stats) < 100:
                        break
                        
                elif response.status_code == 429:
                    logger.warning("[BDL_BATCH] Rate limited, waiting 5 seconds...")
                    await asyncio.sleep(5)
                    continue
                else:
                    logger.error(f"[BDL_BATCH] API error: {response.status_code}")
                    break
                    
        except Exception as e:
            logger.error(f"[BDL_BATCH] Request error: {e}")
        
        # Identify which player_ids had stats returned
        successful_ids = set()
        for stat in all_stats:
            player = stat.get("player", {})
            if player.get("id"):
                successful_ids.add(player["id"])
        
        return all_stats, list(successful_ids)
    
    def _transform_stat_to_game_log(self, stat: Dict) -> Dict:
        """Transform BDL stat object to game_log format."""
        game = stat.get("game", {})
        player = stat.get("player", {})
        
        return {
            "game_id": game.get("id"),
            "date": game.get("date"),
            "season": game.get("season"),
            "bdl_player_id": player.get("id"),
            "pts": stat.get("pts", 0),
            "reb": stat.get("reb", 0),
            "ast": stat.get("ast", 0),
            "fg3m": stat.get("fg3m", 0),
            "stl": stat.get("stl", 0),
            "blk": stat.get("blk", 0),
            "turnover": stat.get("turnover", 0),
            "min": stat.get("min", "0"),
            "fgm": stat.get("fgm", 0),
            "fga": stat.get("fga", 0),
            "fg_pct": stat.get("fg_pct", 0),
            "fg3a": stat.get("fg3a", 0),
            "fg3_pct": stat.get("fg3_pct", 0),
            "ftm": stat.get("ftm", 0),
            "fta": stat.get("fta", 0),
            "ft_pct": stat.get("ft_pct", 0),
            "oreb": stat.get("oreb", 0),
            "dreb": stat.get("dreb", 0),
            "pf": stat.get("pf", 0),
            "plus_minus": stat.get("plus_minus", 0),
            "opponent_team_id": game.get("visitor_team_id") if stat.get("team", {}).get("id") == game.get("home_team_id") else game.get("home_team_id"),
            "home_game": stat.get("team", {}).get("id") == game.get("home_team_id"),
        }
    
    def _filter_valid_games(self, stats: List[Dict]) -> List[Dict]:
        """Filter out games with 0 minutes (DNP)."""
        valid = []
        for stat in stats:
            min_str = stat.get("min", "0")
            try:
                if isinstance(min_str, str):
                    mins = int(min_str.split(":")[0]) if ":" in min_str else int(min_str) if min_str else 0
                else:
                    mins = int(min_str) if min_str else 0
                
                if mins > 0:
                    valid.append(stat)
            except:
                continue
        return valid
    
    async def _bulk_update_players(
        self, 
        player_id_to_logs: Dict[int, List[Dict]]
    ) -> int:
        """
        Bulk update multiple players in MongoDB.
        
        Args:
            player_id_to_logs: Dict mapping bdl_player_id to their game_logs
            
        Returns:
            Number of players updated
        """
        if not player_id_to_logs:
            return 0
        
        from pymongo import UpdateOne
        
        now = datetime.now(timezone.utc).isoformat()
        operations = []
        
        for bdl_id, logs in player_id_to_logs.items():
            # Sort logs by date (most recent first)
            sorted_logs = sorted(logs, key=lambda x: x.get("date", ""), reverse=True)
            
            operations.append(UpdateOne(
                {"bdl_id": bdl_id},
                {
                    "$set": {
                        "bdl_game_logs": sorted_logs,
                        "bdl_game_logs_count": len(sorted_logs),
                        "bdl_game_logs_updated_at": now,
                        "bdl_game_logs_source": "bdl_stats_api_batched"
                    }
                }
            ))
        
        if operations:
            result = await self.hub.bulk_write(operations, ordered=False)
            return result.modified_count
        
        return 0
    
    async def _process_batch(
        self, 
        batch_players: List[Dict],
        batch_num: int,
        total_batches: int
    ) -> Dict[str, Any]:
        """
        Process a single batch of players.
        
        Returns batch result stats.
        """
        player_ids = [p["bdl_id"] for p in batch_players]
        id_to_name = {p["bdl_id"]: p.get("display_name", "Unknown") for p in batch_players}
        
        # Fetch stats for entire batch in one API call
        all_stats, successful_ids = await self._fetch_batch_stats(player_ids)
        
        # Filter valid games and transform to game_log format
        valid_stats = self._filter_valid_games(all_stats)
        
        # Group by player
        player_id_to_logs: Dict[int, List[Dict]] = {}
        for stat in valid_stats:
            player = stat.get("player", {})
            bdl_id = player.get("id")
            if bdl_id:
                if bdl_id not in player_id_to_logs:
                    player_id_to_logs[bdl_id] = []
                player_id_to_logs[bdl_id].append(self._transform_stat_to_game_log(stat))
        
        # Bulk update MongoDB
        updated_count = await self._bulk_update_players(player_id_to_logs)
        
        total_games = sum(len(logs) for logs in player_id_to_logs.values())
        
        logger.info(
            f"[BDL_BATCH] Batch {batch_num}/{total_batches}: "
            f"{len(player_ids)} players, {total_games} games, "
            f"{updated_count} updated"
        )
        
        return {
            "batch_num": batch_num,
            "players_in_batch": len(player_ids),
            "stats_fetched": len(all_stats),
            "valid_games": len(valid_stats),
            "players_with_data": len(player_id_to_logs),
            "total_games": total_games,
            "db_updated": updated_count
        }
    
    async def sync_all_players_batched(self) -> Dict[str, Any]:
        """
        Sync game logs for ALL players using BATCHED requests.
        
        Performance target: 500 players in ~30 seconds.
        """
        start_time = datetime.now(timezone.utc)
        
        results = {
            "total_players": 0,
            "players_synced": 0,
            "total_games": 0,
            "batches_processed": 0,
            "api_calls": 0,
            "errors": [],
            "start_time": start_time.isoformat()
        }
        
        try:
            # Get all players with bdl_id
            players = await self.hub.find(
                {"bdl_id": {"$exists": True, "$ne": None}},
                {"_id": 0, "display_name": 1, "bdl_id": 1}
            ).to_list(1000)
            
            results["total_players"] = len(players)
            
            if not players:
                logger.warning("[BDL_BATCH] No players with bdl_id found")
                return results
            
            logger.info(f"[BDL_BATCH] Starting BATCHED sync for {len(players)} players")
            logger.info(f"[BDL_BATCH] Config: batch_size={BATCH_SIZE}, parallel={PARALLEL_BATCHES}")
            
            # Split into batches
            batches = [
                players[i:i + BATCH_SIZE] 
                for i in range(0, len(players), BATCH_SIZE)
            ]
            total_batches = len(batches)
            
            # Process batches in parallel groups
            for group_start in range(0, len(batches), PARALLEL_BATCHES):
                group_end = min(group_start + PARALLEL_BATCHES, len(batches))
                batch_group = batches[group_start:group_end]
                
                # Run parallel batches
                tasks = [
                    self._process_batch(batch, group_start + i + 1, total_batches)
                    for i, batch in enumerate(batch_group)
                ]
                
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for br in batch_results:
                    if isinstance(br, Exception):
                        results["errors"].append(str(br))
                    else:
                        results["batches_processed"] += 1
                        results["players_synced"] += br.get("players_with_data", 0)
                        results["total_games"] += br.get("total_games", 0)
                        results["api_calls"] += 1
                
                # Rate limiting between parallel groups
                if group_end < len(batches):
                    await asyncio.sleep(RATE_LIMIT_DELAY)
            
            # Cleanup
            await self._close_client()
            
        except Exception as e:
            logger.error(f"[BDL_BATCH] Sync error: {e}")
            results["errors"].append(str(e))
        
        end_time = datetime.now(timezone.utc)
        results["end_time"] = end_time.isoformat()
        results["duration_seconds"] = (end_time - start_time).total_seconds()
        
        logger.info(
            f"[BDL_BATCH] COMPLETE: {results['players_synced']}/{results['total_players']} players, "
            f"{results['total_games']} games in {results['duration_seconds']:.1f}s "
            f"({results['api_calls']} API calls)"
        )
        
        return results


# Entry point
async def run_bdl_game_logs_sync_batched(db: AsyncIOMotorDatabase) -> Dict[str, Any]:
    """Run the batched BDL game logs sync."""
    sync_service = BDLGameLogsSyncBatched(db)
    return await sync_service.sync_all_players_batched()
