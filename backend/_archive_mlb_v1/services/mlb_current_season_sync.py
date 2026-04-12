"""
MLB Current Season Sync Service

Fetches game-by-game stats for the current MLB season with proper dates.
Uses BDL API to:
1. Fetch games by date (last N days)
2. Get player stats for those games
3. Store in mlb_historical_logs with proper dates and opponent info
"""

import asyncio
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any
import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

BDL_API_KEY = os.environ.get("BDL_API_KEY")
BDL_MLB_BASE_URL = "https://api.balldontlie.io/mlb/v1"
RATE_LIMIT_DELAY = 0.15


class MLBCurrentSeasonSync:
    """Syncs current season MLB game logs with proper dates."""
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self._client = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"Authorization": BDL_API_KEY},
                timeout=60.0
            )
        return self._client
    
    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def fetch_games_by_dates(self, dates: List[str]) -> Dict[int, Dict]:
        """
        Fetch games for specific dates.
        
        Args:
            dates: List of date strings in YYYY-MM-DD format
            
        Returns:
            Dict mapping game_id to game info (date, home, away teams)
        """
        client = await self._get_client()
        all_games = {}
        
        for date in dates:
            try:
                resp = await client.get(
                    f"{BDL_MLB_BASE_URL}/games",
                    params={"dates[]": date, "per_page": 50}
                )
                if resp.status_code == 200:
                    games = resp.json().get("data", [])
                    for g in games:
                        all_games[g["id"]] = {
                            "date": g.get("date"),
                            "home_abbr": g.get("home_team", {}).get("abbreviation"),
                            "away_abbr": g.get("away_team", {}).get("abbreviation"),
                            "home_team_id": g.get("home_team", {}).get("id"),
                            "away_team_id": g.get("away_team", {}).get("id"),
                        }
                await asyncio.sleep(RATE_LIMIT_DELAY)
            except Exception as e:
                logger.warning(f"Error fetching games for {date}: {e}")
        
        logger.info(f"[MLB_SYNC] Fetched {len(all_games)} games for {len(dates)} dates")
        return all_games
    
    async def fetch_stats_for_games(
        self, 
        game_ids: List[int],
        game_info: Dict[int, Dict]
    ) -> Dict[str, List[Dict]]:
        """
        Fetch stats for specific games, grouped by player.
        
        Args:
            game_ids: List of game IDs to fetch stats for
            game_info: Dict mapping game_id to game info (date, teams)
            
        Returns:
            Dict mapping player_name to list of game logs
        """
        client = await self._get_client()
        player_stats = {}
        
        # Fetch stats in batches
        batch_size = 25
        for i in range(0, len(game_ids), batch_size):
            batch = game_ids[i:i+batch_size]
            try:
                cursor = None
                while True:
                    params = {"game_ids[]": batch, "per_page": 100}
                    if cursor:
                        params["cursor"] = cursor
                    
                    resp = await client.get(f"{BDL_MLB_BASE_URL}/stats", params=params)
                    if resp.status_code == 200:
                        data = resp.json()
                        stats = data.get("data", [])
                        
                        for s in stats:
                            player = s.get("player", {})
                            player_name = player.get("full_name")
                            if not player_name:
                                continue
                            
                            gid = s.get("game_id")
                            ginfo = game_info.get(gid, {})
                            
                            # Determine opponent based on team
                            team_name = s.get("team_name", "")
                            if ginfo.get("home_abbr") and team_name:
                                # If player's team matches home, opponent is away
                                opponent = ginfo.get("away_abbr") if ginfo.get("home_abbr") in team_name or team_name in str(ginfo) else ginfo.get("home_abbr")
                            else:
                                opponent = ginfo.get("away_abbr") or ginfo.get("home_abbr")
                            
                            game_log = {
                                "game_id": gid,
                                "date": ginfo.get("date"),
                                "opponent_abbr": opponent,
                                "team_name": team_name,
                                "season": 2026,
                                # Batter stats
                                "at_bats": s.get("at_bats", 0) or 0,
                                "hits": s.get("hits", 0) or 0,
                                "runs": s.get("runs", 0) or 0,
                                "rbis": s.get("rbi", 0) or 0,
                                "home_runs": s.get("hr", 0) or 0,
                                "stolen_bases": s.get("stolen_bases", 0) or 0,
                                "walks": s.get("bb", 0) or 0,
                                "strikeouts": s.get("k", 0) or 0,
                                "total_bases": s.get("total_bases", 0) or 0,
                                "doubles": s.get("doubles", 0) or 0,
                                "triples": s.get("triples", 0) or 0,
                                # Pitcher stats
                                "innings_pitched": s.get("ip"),
                                "pitcher_strikeouts": s.get("p_k"),
                                "pitcher_walks": s.get("p_bb"),
                                "hits_allowed": s.get("p_hits"),
                                "earned_runs": s.get("er"),
                            }
                            
                            if player_name not in player_stats:
                                player_stats[player_name] = []
                            player_stats[player_name].append(game_log)
                        
                        cursor = data.get("meta", {}).get("next_cursor")
                        if not cursor:
                            break
                    else:
                        logger.warning(f"Stats API error: {resp.status_code}")
                        break
                    
                    await asyncio.sleep(RATE_LIMIT_DELAY)
                    
            except Exception as e:
                logger.warning(f"Error fetching stats batch: {e}")
            
            await asyncio.sleep(RATE_LIMIT_DELAY)
        
        logger.info(f"[MLB_SYNC] Fetched stats for {len(player_stats)} players")
        return player_stats
    
    async def sync_current_season(
        self,
        days_back: int = 60,
        save_to_db: bool = True
    ) -> Dict[str, Any]:
        """
        Sync current season MLB game logs.
        
        Args:
            days_back: Number of days to fetch (default 60)
            save_to_db: Whether to save to MongoDB
            
        Returns:
            Sync result summary
        """
        start_time = datetime.now()
        
        try:
            # Generate date range
            today = datetime.now()
            dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days_back)]
            
            logger.info(f"[MLB_SYNC] Starting sync for {len(dates)} days: {dates[0]} to {dates[-1]}")
            
            # Step 1: Fetch all games
            game_info = await self.fetch_games_by_dates(dates)
            game_ids = list(game_info.keys())
            
            if not game_ids:
                return {
                    "success": False,
                    "error": "No games found for date range",
                    "days_back": days_back
                }
            
            # Step 2: Fetch stats for all games
            player_stats = await self.fetch_stats_for_games(game_ids, game_info)
            
            # Step 3: Save to database
            if save_to_db and player_stats:
                collection = self.db["mlb_historical_logs"]
                
                updates = 0
                for player_name, logs in player_stats.items():
                    # Sort logs by date descending
                    sorted_logs = sorted(
                        logs, 
                        key=lambda x: x.get("date") or "", 
                        reverse=True
                    )
                    
                    # Update or insert player document
                    await collection.update_one(
                        {"player_name": player_name},
                        {
                            "$set": {
                                "player_name": player_name,
                                "game_logs": sorted_logs,
                                "total_games": len(sorted_logs),
                                "seasons": [2026],
                                "last_synced": datetime.utcnow().isoformat()
                            }
                        },
                        upsert=True
                    )
                    updates += 1
                
                logger.info(f"[MLB_SYNC] Saved {updates} player records to database")
            
            duration = (datetime.now() - start_time).total_seconds()
            
            return {
                "success": True,
                "games_fetched": len(game_ids),
                "players_synced": len(player_stats),
                "days_back": days_back,
                "date_range": f"{dates[-1]} to {dates[0]}",
                "duration_seconds": round(duration, 2)
            }
            
        except Exception as e:
            logger.error(f"[MLB_SYNC] Sync failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
        finally:
            await self.close()


async def run_mlb_current_season_sync(db: AsyncIOMotorDatabase, days_back: int = 60) -> Dict:
    """Helper function to run the sync."""
    sync = MLBCurrentSeasonSync(db)
    return await sync.sync_current_season(days_back=days_back, save_to_db=True)
