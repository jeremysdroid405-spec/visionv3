"""
BDL Game Logs Sync Service

Uses BallDontLie's /stats endpoint to fetch complete game-by-game stats for all players.
This provides accurate L5/L10 hit rate calculations.

API Endpoint: GET https://api.balldontlie.io/v1/stats
- player_ids[]: Filter by player IDs
- seasons[]: Filter by season (e.g., 2024 for 2024-25 season)
- per_page: Max 100 results per request
- cursor: Pagination cursor
"""

import httpx
import asyncio
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorDatabase
import os

logger = logging.getLogger(__name__)

BDL_BASE_URL = "https://api.balldontlie.io/v1"
CURRENT_SEASON = 2024  # 2024-25 season


class BDLGameLogsSync:
    """Sync game logs from BallDontLie API for all players."""
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.hub = db["nba_master_hub_2026"]
        self.api_key = os.environ.get("BDL_API_KEY", "ad5544be-9969-434b-9389-2b7cf658c8e0")
        self.rate_limit_delay = 0.5  # 500ms between requests
        
    async def _make_request(self, endpoint: str, params: Dict[str, Any]) -> Optional[Dict]:
        """Make a request to the BDL API with rate limiting."""
        url = f"{BDL_BASE_URL}/{endpoint}"
        headers = {"Authorization": self.api_key}
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, params=params, headers=headers)
                
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 429:
                    logger.warning("[BDL_SYNC] Rate limited, waiting 5 seconds...")
                    await asyncio.sleep(5)
                    return None
                else:
                    logger.error(f"[BDL_SYNC] API error: {response.status_code} - {response.text[:200]}")
                    return None
        except Exception as e:
            logger.error(f"[BDL_SYNC] Request error: {e}")
            return None
    
    async def fetch_player_game_logs(self, bdl_player_id: int, season: int = CURRENT_SEASON) -> List[Dict]:
        """Fetch all game logs for a player for a given season."""
        all_stats = []
        cursor = None
        
        while True:
            params = {
                "player_ids[]": bdl_player_id,
                "seasons[]": season,
                "per_page": 100
            }
            if cursor:
                params["cursor"] = cursor
            
            data = await self._make_request("stats", params)
            
            if not data:
                break
            
            stats = data.get("data", [])
            all_stats.extend(stats)
            
            # Check for pagination
            meta = data.get("meta", {})
            cursor = meta.get("next_cursor")
            
            if not cursor or len(stats) < 100:
                break
            
            await asyncio.sleep(self.rate_limit_delay)
        
        return all_stats
    
    def _transform_stat_to_game_log(self, stat: Dict) -> Dict:
        """Transform BDL stat object to game_log format."""
        game = stat.get("game", {})
        
        return {
            "game_id": game.get("id"),
            "date": game.get("date"),
            "season": game.get("season"),
            "pts": stat.get("pts", 0),
            "reb": stat.get("reb", 0),
            "ast": stat.get("ast", 0),
            "fg3m": stat.get("fg3m", 0),  # 3PM
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
    
    async def sync_player(self, bdl_player_id: int, player_name: str) -> Dict[str, Any]:
        """Sync game logs for a single player."""
        result = {
            "player_name": player_name,
            "bdl_id": bdl_player_id,
            "games_fetched": 0,
            "success": False,
            "error": None
        }
        
        try:
            stats = await self.fetch_player_game_logs(bdl_player_id)
            
            if not stats:
                result["error"] = "No stats returned from API"
                return result
            
            # Filter out games with 0 minutes (DNP)
            valid_stats = []
            for stat in stats:
                min_str = stat.get("min", "0")
                try:
                    # Parse minutes (format is like "32" or "32:45")
                    if isinstance(min_str, str):
                        mins = int(min_str.split(":")[0]) if ":" in min_str else int(min_str) if min_str else 0
                    else:
                        mins = int(min_str) if min_str else 0
                    
                    if mins > 0:
                        valid_stats.append(stat)
                except:
                    continue
            
            # Transform to game_log format
            game_logs = [self._transform_stat_to_game_log(s) for s in valid_stats]
            
            # Sort by date (most recent first)
            game_logs = sorted(game_logs, key=lambda x: x.get("date", ""), reverse=True)
            
            # Update the player in master_hub
            update_result = await self.hub.update_one(
                {"bdl_id": bdl_player_id},
                {
                    "$set": {
                        "bdl_game_logs": game_logs,
                        "bdl_game_logs_count": len(game_logs),
                        "bdl_game_logs_updated_at": datetime.now(timezone.utc).isoformat(),
                        "bdl_game_logs_source": "bdl_stats_api"
                    }
                }
            )
            
            result["games_fetched"] = len(game_logs)
            result["success"] = update_result.modified_count > 0 or update_result.matched_count > 0
            
            logger.info(f"[BDL_SYNC] {player_name}: {len(game_logs)} games synced")
            
        except Exception as e:
            result["error"] = str(e)
            logger.error(f"[BDL_SYNC] Error syncing {player_name}: {e}")
        
        return result
    
    async def sync_all_players(self, batch_size: int = 10) -> Dict[str, Any]:
        """Sync game logs for all players with bdl_id."""
        results = {
            "total_players": 0,
            "players_synced": 0,
            "players_failed": 0,
            "total_games": 0,
            "errors": [],
            "start_time": datetime.now(timezone.utc).isoformat()
        }
        
        # Get all players with bdl_id
        players = await self.hub.find(
            {"bdl_id": {"$exists": True, "$ne": None}},
            {"_id": 0, "display_name": 1, "bdl_id": 1}
        ).to_list(1000)
        
        results["total_players"] = len(players)
        logger.info(f"[BDL_SYNC] Starting sync for {len(players)} players")
        
        # Process in batches to avoid overwhelming the API
        for i in range(0, len(players), batch_size):
            batch = players[i:i+batch_size]
            
            for player in batch:
                bdl_id = player.get("bdl_id")
                name = player.get("display_name", "Unknown")
                
                if not bdl_id:
                    continue
                
                result = await self.sync_player(bdl_id, name)
                
                if result["success"]:
                    results["players_synced"] += 1
                    results["total_games"] += result["games_fetched"]
                else:
                    results["players_failed"] += 1
                    if result["error"]:
                        results["errors"].append(f"{name}: {result['error']}")
                
                # Rate limiting
                await asyncio.sleep(self.rate_limit_delay)
            
            # Log progress
            logger.info(f"[BDL_SYNC] Progress: {min(i+batch_size, len(players))}/{len(players)} players processed")
        
        results["end_time"] = datetime.now(timezone.utc).isoformat()
        results["duration_seconds"] = (
            datetime.fromisoformat(results["end_time"]) - 
            datetime.fromisoformat(results["start_time"])
        ).total_seconds()
        
        logger.info(f"[BDL_SYNC] Complete: {results['players_synced']}/{results['total_players']} synced, {results['total_games']} total games")
        
        return results
    
    async def sync_players_missing_logs(self, min_games: int = 10) -> Dict[str, Any]:
        """Sync only players that have fewer than min_games in bdl_game_logs."""
        results = {
            "total_players_needing_sync": 0,
            "players_synced": 0,
            "players_failed": 0,
            "total_games": 0,
            "errors": [],
            "start_time": datetime.now(timezone.utc).isoformat()
        }
        
        # Find players with bdl_id but insufficient game logs
        players = await self.hub.find(
            {
                "bdl_id": {"$exists": True, "$ne": None},
                "$or": [
                    {"bdl_game_logs": {"$exists": False}},
                    {"bdl_game_logs": []},
                    {"bdl_game_logs_count": {"$lt": min_games}}
                ]
            },
            {"_id": 0, "display_name": 1, "bdl_id": 1, "bdl_game_logs_count": 1}
        ).to_list(1000)
        
        results["total_players_needing_sync"] = len(players)
        logger.info(f"[BDL_SYNC] Found {len(players)} players needing game log sync")
        
        for i, player in enumerate(players):
            bdl_id = player.get("bdl_id")
            name = player.get("display_name", "Unknown")
            current_count = player.get("bdl_game_logs_count", 0)
            
            if not bdl_id:
                continue
            
            logger.info(f"[BDL_SYNC] [{i+1}/{len(players)}] Syncing {name} (currently {current_count} games)")
            
            result = await self.sync_player(bdl_id, name)
            
            if result["success"]:
                results["players_synced"] += 1
                results["total_games"] += result["games_fetched"]
            else:
                results["players_failed"] += 1
                if result["error"]:
                    results["errors"].append(f"{name}: {result['error']}")
            
            # Rate limiting
            await asyncio.sleep(self.rate_limit_delay)
        
        results["end_time"] = datetime.now(timezone.utc).isoformat()
        
        logger.info(f"[BDL_SYNC] Complete: {results['players_synced']}/{results['total_players_needing_sync']} synced")
        
        return results


async def run_bdl_game_logs_sync(db: AsyncIOMotorDatabase, full_sync: bool = False) -> Dict[str, Any]:
    """Entry point for running the BDL game logs sync."""
    sync_service = BDLGameLogsSync(db)
    
    if full_sync:
        return await sync_service.sync_all_players()
    else:
        return await sync_service.sync_players_missing_logs()
