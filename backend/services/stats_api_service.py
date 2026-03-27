"""
Stats API Service (BallDontLie Integration)
============================================
Extracted from services.engines.demon_goblin_engine.py for modularity.

Handles all BallDontLie API interactions:
- Player search and mapping
- Season stats fetching
- Hit rate calculations
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone, timedelta
import httpx
import os
import logging

from motor.motor_asyncio import AsyncIOMotorDatabase
from thefuzz import fuzz

logger = logging.getLogger(__name__)

# API Configuration
BDL_BASE_URL = "https://api.balldontlie.io/v1"
BDL_API_KEY = os.environ.get("BDL_API_KEY", "3f6f0659-c8bb-4222-8abb-62fce91eef2c")
CURRENT_SEASON = os.environ.get("NBA_SEASON", "2025")


class StatsApiService:
    """
    Service for BallDontLie API interactions and hit rate calculations.
    
    Responsibilities:
    - Player name to BDL ID mapping
    - Fetching player season stats
    - Calculating L5/L10/Season hit rates
    - Trend detection
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.stats_cache = db.dg_stats_cache
        
        # In-memory caches
        self._player_name_map: Dict[str, Dict] = {}
    
    async def search_player(self, player_name: str) -> Optional[Dict[str, Any]]:
        """
        Map player name to BallDontLie player data.
        Uses fuzzy matching for name variations.
        """
        if player_name in self._player_name_map:
            return self._player_name_map[player_name]
        
        try:
            name_parts = player_name.strip().split()
            search_terms = [name_parts[-1]] if len(name_parts) >= 2 else [player_name]
            if len(name_parts) >= 2:
                search_terms.append(name_parts[0])
            
            url = f"{BDL_BASE_URL}/players"
            headers = {"Authorization": BDL_API_KEY}
            
            for search_term in search_terms:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        url,
                        params={"search": search_term},
                        headers=headers,
                        timeout=10.0
                    )
                    
                    if response.status_code == 200:
                        players = response.json().get("data", [])
                        
                        if not players:
                            continue
                        
                        best_match = None
                        best_score = 0
                        
                        for player in players:
                            full_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
                            score = max(
                                fuzz.ratio(player_name.lower(), full_name.lower()),
                                fuzz.partial_ratio(player_name.lower(), full_name.lower())
                            )
                            
                            if score > best_score and score >= 60:
                                best_score = score
                                best_match = player
                        
                        if best_match:
                            self._player_name_map[player_name] = best_match
                            return best_match
            
        except Exception as e:
            logger.error(f"[STATS_API] BDL search error for {player_name}: {e}")
        
        return None
    
    async def fetch_player_season_stats(self, player_id: int) -> List[Dict[str, Any]]:
        """
        Fetch season stats for hit rate calculation.
        Caches results for 4 hours to reduce API calls.
        """
        try:
            # Check cache first
            cached = await self.stats_cache.find_one({"player_id": str(player_id)})
            if cached:
                cached_time = datetime.fromisoformat(cached["cached_at"])
                if datetime.now(timezone.utc) - cached_time < timedelta(hours=4):
                    return cached.get("games", [])
            
            url = f"{BDL_BASE_URL}/stats"
            params = {
                "player_ids[]": player_id,
                "seasons[]": CURRENT_SEASON,
                "per_page": 100
            }
            headers = {"Authorization": BDL_API_KEY}
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, headers=headers, timeout=15.0)
                
                if response.status_code == 200:
                    games = response.json().get("data", [])
                    
                    # Sort by date (most recent first)
                    games_sorted = sorted(
                        games,
                        key=lambda x: x.get("game", {}).get("date", ""),
                        reverse=True
                    )
                    
                    # Filter out DNP games
                    def player_played(game):
                        minutes = game.get("min")
                        if minutes:
                            min_str = str(minutes).replace(":", "").strip()
                            if min_str and min_str != "0" and min_str != "00":
                                return True
                        return (game.get("pts", 0) or 0) + (game.get("reb", 0) or 0) + (game.get("ast", 0) or 0) > 0
                    
                    played_games = [g for g in games_sorted if player_played(g)]
                    
                    # Cache results
                    await self.stats_cache.update_one(
                        {"player_id": str(player_id)},
                        {"$set": {
                            "player_id": str(player_id),
                            "games": played_games,
                            "cached_at": datetime.now(timezone.utc).isoformat()
                        }},
                        upsert=True
                    )
                    
                    return played_games
                    
        except Exception as e:
            logger.error(f"[STATS_API] Stats fetch error for player {player_id}: {e}")
        
        return []
    
    def calculate_hit_rates(self, games: List[Dict], market: str, line: float) -> Dict[str, Any]:
        """
        Calculate L5, L10, and Season hit rates with source verification.
        
        TRUTH ENGINE V3.1:
        - All stat keys MUST be lowercase (pts, reb, ast - not PTS, REB, AST)
        - Manual PRA check for each game
        - Returns raw values for verification
        """
        stat_keys = self._get_stat_keys(market)
        
        def get_stat_value(game):
            """Extract stat value with case-insensitive key lookup."""
            total = 0
            for key in stat_keys:
                # Try lowercase first (standard)
                value = game.get(key, None)
                # Fallback to uppercase if not found
                if value is None:
                    value = game.get(key.upper(), None)
                # Fallback to title case
                if value is None:
                    value = game.get(key.title(), None)
                total += (value or 0)
            return total
        
        def calc_window(game_list, line_val):
            if not game_list:
                return {"games_over": 0, "total_games": 0, "hit_rate": 0, "avg": 0, "values": [], "floor": 0, "ceiling": 0}
            
            values = [get_stat_value(g) for g in game_list]
            games_over = sum(1 for v in values if v > line_val)
            total = len(game_list)
            hit_rate = games_over / total if total > 0 else 0
            avg = sum(values) / total if total > 0 else 0
            
            return {
                "games_over": games_over,
                "total_games": total,
                "hit_rate": round(hit_rate, 3),
                "avg": round(avg, 1),
                "values": values,  # V3.1: Store raw values for verification
                "floor": min(values) if values else 0,
                "ceiling": max(values) if values else 0
            }
        
        l5 = calc_window(games[:5], line)
        l10 = calc_window(games[:10], line)
        season = calc_window(games, line)
        
        # Trend detection
        trends = []
        if l5["total_games"] >= 3 and season["total_games"] >= 10:
            if l5["avg"] > season["avg"] * 1.15:
                trends.append("HOT")
            elif l5["avg"] < season["avg"] * 0.85:
                trends.append("COLD")
        
        return {
            "l5": l5,
            "l10": l10,
            "season": season,
            "trends": trends
        }
    
    def extract_l10_values(self, games: List[Dict], market: str) -> List[float]:
        """
        Extract raw stat values from last 10 games for verification.
        Uses case-insensitive key lookup.
        """
        stat_keys = self._get_stat_keys(market)
        
        def get_stat_value(game):
            total = 0
            for key in stat_keys:
                value = game.get(key, None)
                if value is None:
                    value = game.get(key.upper(), None)
                if value is None:
                    value = game.get(key.title(), None)
                total += (value or 0)
            return total
        
        if not games:
            return []
            
        return [get_stat_value(g) for g in games[:10]]
    
    def _get_stat_keys(self, market: str) -> List[str]:
        """Get stat keys for a market type."""
        market_to_stat = {
            # Primary markets (lowercase keys)
            "player_points": ["pts"],
            "player_rebounds": ["reb"],
            "player_assists": ["ast"],
            "player_threes": ["fg3m"],
            "player_blocks": ["blk"],
            "player_steals": ["stl"],
            "player_turnovers": ["turnover", "tov"],
            # Alternate markets
            "alternate_player_points": ["pts"],
            "alternate_player_rebounds": ["reb"],
            "alternate_player_assists": ["ast"],
            "alternate_player_threes": ["fg3m"],
            # Combo stats - MANUAL PRA CHECK
            "player_points_rebounds": ["pts", "reb"],
            "player_points_assists": ["pts", "ast"],
            "player_rebounds_assists": ["reb", "ast"],
            "player_points_rebounds_assists": ["pts", "reb", "ast"],
            "player_steals_blocks": ["stl", "blk"],
        }
        return market_to_stat.get(market, ["pts"])
    
    def get_player_name_map(self) -> Dict[str, Dict]:
        """Get the in-memory player name map."""
        return self._player_name_map
    
    def clear_name_map(self) -> None:
        """Clear the in-memory player name map."""
        self._player_name_map = {}
