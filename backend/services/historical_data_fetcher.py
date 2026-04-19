"""
Historical Data Fetcher - Multi-Season BDL Data
=================================================

Fetches historical game logs from BallDontLie API for multiple seasons.
BDL has data from 1946 to present - we'll use 2020-2025 for training.

This gives us:
- 5+ seasons of data (~75K+ samples)
- Historical context for model training
- Proper backtesting capability
"""

import os
import logging
import httpx
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from pymongo import MongoClient, UpdateOne
import time

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

BDL_API_KEY = os.environ.get("BDL_API_KEY", "")
BDL_BASE_URL = "https://api.balldontlie.io/v1"

# Seasons to fetch (BDL uses start year, so 2024 = 2024-25 season)
HISTORICAL_SEASONS = [2020, 2021, 2022, 2023, 2024]  # 5 seasons
CURRENT_SEASON = 2025  # Already have this

# Rate limiting
REQUESTS_PER_MINUTE = 50
REQUEST_DELAY = 60 / REQUESTS_PER_MINUTE  # 1.2 seconds between requests


class HistoricalDataFetcher:
    """
    Fetches multi-season historical data from BDL API.
    """
    
    def __init__(self, db):
        self.db = db
        self.hub = db[COLL("master_hub", "nba")]
        self.historical_logs = db['historical_game_logs']
        self.request_count = 0
        self.last_request_time = 0
    
    def _rate_limit(self):
        """Enforce rate limiting."""
        now = time.time()
        elapsed = now - self.last_request_time
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        self.last_request_time = time.time()
        self.request_count += 1
    
    def _make_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Make rate-limited request to BDL API."""
        self._rate_limit()
        
        headers = {"Authorization": BDL_API_KEY}
        url = f"{BDL_BASE_URL}{endpoint}"
        
        try:
            with httpx.Client(timeout=30) as client:
                response = client.get(url, headers=headers, params=params or {})
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"BDL request failed: {e}")
            return None
    
    def get_player_stats_for_season(
        self,
        player_id: int,
        season: int
    ) -> List[Dict]:
        """
        Fetch all game logs for a player in a specific season.
        
        Args:
            player_id: BDL player ID
            season: Season start year (e.g., 2023 for 2023-24)
        
        Returns:
            List of game log dictionaries
        """
        all_games = []
        cursor = None
        
        while True:
            params = {
                "player_ids[]": player_id,
                "seasons[]": season,
                "per_page": 100,
            }
            if cursor:
                params["cursor"] = cursor
            
            data = self._make_request("/stats", params)
            
            if not data or not data.get("data"):
                break
            
            games = data["data"]
            all_games.extend(games)
            
            # Check for next page
            meta = data.get("meta", {})
            cursor = meta.get("next_cursor")
            
            if not cursor:
                break
        
        return all_games
    
    def fetch_historical_data_for_player(
        self,
        player_id: int,
        player_name: str,
        seasons: List[int] = None
    ) -> Dict[str, Any]:
        """
        Fetch historical game logs for a player across multiple seasons.
        
        Returns summary of fetched data.
        """
        seasons = seasons or HISTORICAL_SEASONS
        total_games = 0
        season_counts = {}
        
        for season in seasons:
            logger.info(f"Fetching {player_name} ({player_id}) season {season}...")
            
            games = self.get_player_stats_for_season(player_id, season)
            
            if games:
                # Process and store games
                processed_games = []
                for game in games:
                    processed = self._process_game_log(game, season)
                    if processed:
                        processed_games.append(processed)
                
                # Store in historical collection
                if processed_games:
                    self.historical_logs.update_one(
                        {
                            "player_id": player_id,
                            "season": season
                        },
                        {
                            "$set": {
                                "player_name": player_name,
                                "player_id": player_id,
                                "season": season,
                                "games": processed_games,
                                "game_count": len(processed_games),
                                "updated_at": datetime.now(timezone.utc)
                            }
                        },
                        upsert=True
                    )
                
                season_counts[season] = len(processed_games)
                total_games += len(processed_games)
                logger.info(f"  -> {len(processed_games)} games")
        
        return {
            "player_id": player_id,
            "player_name": player_name,
            "total_games": total_games,
            "seasons": season_counts
        }
    
    def _process_game_log(self, game: Dict, season: int) -> Optional[Dict]:
        """Process raw BDL game log into our format."""
        try:
            game_data = game.get("game", {})
            
            # Parse minutes
            min_str = game.get("min", "0")
            if isinstance(min_str, str) and ":" in min_str:
                parts = min_str.split(":")
                minutes = int(parts[0]) + int(parts[1]) / 60
            else:
                minutes = float(min_str) if min_str else 0
            
            return {
                "game_id": game_data.get("id"),
                "date": game_data.get("date"),
                "season": season,
                "home_game": game_data.get("home_team_id") == game.get("team", {}).get("id"),
                "opponent_team_id": (
                    game_data.get("visitor_team_id") 
                    if game_data.get("home_team_id") == game.get("team", {}).get("id")
                    else game_data.get("home_team_id")
                ),
                "min": minutes,
                "pts": game.get("pts", 0),
                "reb": game.get("reb", 0),
                "ast": game.get("ast", 0),
                "stl": game.get("stl", 0),
                "blk": game.get("blk", 0),
                "turnover": game.get("turnover", 0),
                "fgm": game.get("fgm", 0),
                "fga": game.get("fga", 0),
                "fg_pct": game.get("fg_pct", 0),
                "fg3m": game.get("fg3m", 0),
                "fg3a": game.get("fg3a", 0),
                "fg3_pct": game.get("fg3_pct", 0),
                "ftm": game.get("ftm", 0),
                "fta": game.get("fta", 0),
                "ft_pct": game.get("ft_pct", 0),
                "oreb": game.get("oreb", 0),
                "dreb": game.get("dreb", 0),
                "pf": game.get("pf", 0),
            }
        except Exception as e:
            logger.error(f"Error processing game log: {e}")
            return None
    
    def fetch_all_historical_data(
        self,
        limit: int = None,
        seasons: List[int] = None
    ) -> Dict[str, Any]:
        """
        Fetch historical data for all players in the hub.
        
        Args:
            limit: Max number of players to fetch (for testing)
            seasons: Seasons to fetch (default: 2020-2024)
        
        Returns:
            Summary of fetched data
        """
        seasons = seasons or HISTORICAL_SEASONS
        
        # Get all players with BDL IDs
        players = list(self.hub.find(
            {"bdl_player_id": {"$exists": True, "$ne": None}},
            {"bdl_player_id": 1, "display_name": 1, "player_name": 1}
        ))
        
        if limit:
            players = players[:limit]
        
        logger.info(f"Fetching historical data for {len(players)} players across seasons {seasons}")
        
        results = {
            "total_players": len(players),
            "total_games": 0,
            "seasons": {s: 0 for s in seasons},
            "players_fetched": 0,
            "errors": []
        }
        
        for i, player in enumerate(players):
            player_id = player.get("bdl_player_id")
            player_name = player.get("display_name") or player.get("player_name")
            
            if not player_id:
                continue
            
            try:
                summary = self.fetch_historical_data_for_player(
                    player_id=player_id,
                    player_name=player_name,
                    seasons=seasons
                )
                
                results["total_games"] += summary["total_games"]
                for season, count in summary["seasons"].items():
                    results["seasons"][season] = results["seasons"].get(season, 0) + count
                results["players_fetched"] += 1
                
                if (i + 1) % 10 == 0:
                    logger.info(f"Progress: {i + 1}/{len(players)} players, {results['total_games']} total games")
                
            except Exception as e:
                logger.error(f"Error fetching {player_name}: {e}")
                results["errors"].append({"player": player_name, "error": str(e)})
        
        logger.info(f"Historical fetch complete: {results['total_games']} games across {results['players_fetched']} players")
        
        return results
    
    def get_combined_game_logs(
        self,
        player_id: int,
        include_current: bool = True
    ) -> List[Dict]:
        """
        Get all game logs for a player (historical + current season).
        
        Returns games sorted by date (newest first).
        """
        all_games = []
        
        # Get historical games
        historical = list(self.historical_logs.find(
            {"player_id": player_id},
            {"games": 1}
        ))
        
        for doc in historical:
            all_games.extend(doc.get("games", []))
        
        # Get current season games
        if include_current:
            player = self.hub.find_one({"bdl_player_id": player_id})
            if player:
                current_games = player.get("bdl_game_logs", [])
                all_games.extend(current_games)
        
        # Sort by date (newest first)
        all_games.sort(key=lambda x: x.get("date", ""), reverse=True)
        
        return all_games
    
    def get_training_data_stats(self) -> Dict[str, Any]:
        """Get statistics on available training data."""
        # Count historical games
        pipeline = [
            {"$group": {
                "_id": "$season",
                "players": {"$sum": 1},
                "games": {"$sum": "$game_count"}
            }},
            {"$sort": {"_id": 1}}
        ]
        
        historical_stats = list(self.historical_logs.aggregate(pipeline))
        
        # Count current season
        current_count = self.hub.count_documents({"bdl_game_logs.0": {"$exists": True}})
        
        # Estimate current season games
        sample = self.hub.find_one({"bdl_game_logs.10": {"$exists": True}})
        avg_games = len(sample.get("bdl_game_logs", [])) if sample else 50
        
        return {
            "historical_seasons": {
                str(s["_id"]): {"players": s["players"], "games": s["games"]}
                for s in historical_stats
            },
            "current_season": {
                "players": current_count,
                "estimated_games": current_count * avg_games
            },
            "total_estimated_samples": sum(s["games"] for s in historical_stats) + (current_count * avg_games)
        }


# =============================================================================
# EXPORT
# =============================================================================

__all__ = [
    'HistoricalDataFetcher',
    'HISTORICAL_SEASONS',
    'CURRENT_SEASON',
]
