"""
NBA Stats Manager with API-Sports Integration
Handles player statistics with 24hr persistent caching
"""

import httpx
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any
from thefuzz import fuzz
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

API_SPORTS_KEY = "9057bc1422b361f64cc071581dd1b240"
API_SPORTS_HOST = "api-nba-v1.p.rapidapi.com"
CACHE_TTL_HOURS = 24
CURRENT_SEASON = "2024-2025"


class StatsManager:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.stats_cache = db.stats_cache
        
    async def get_cached_stats(self, player_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached player stats if within TTL
        """
        try:
            cached = await self.stats_cache.find_one({"player_id": player_id})
            if cached:
                cached_time = datetime.fromisoformat(cached["cached_at"])
                if datetime.now(timezone.utc) - cached_time < timedelta(hours=CACHE_TTL_HOURS):
                    logger.info(f"✓ Using cached stats for player {player_id}")
                    return cached.get("data")
                else:
                    logger.info(f"Cache expired for player {player_id}")
        except Exception as e:
            logger.error(f"Cache retrieval error: {e}")
        return None
    
    async def set_cached_stats(self, player_id: str, data: Dict[str, Any]):
        """
        Store player stats in cache with timestamp
        """
        try:
            cache_entry = {
                "player_id": player_id,
                "data": data,
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "season": CURRENT_SEASON
            }
            await self.stats_cache.update_one(
                {"player_id": player_id},
                {"$set": cache_entry},
                upsert=True
            )
            logger.info(f"✓ Cached stats for player {player_id}")
        except Exception as e:
            logger.error(f"Cache storage error: {e}")
    
    async def fetch_player_stats_from_api(self, player_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch player statistics from API-Sports
        Endpoint: /players/statistics
        """
        try:
            url = f"https://{API_SPORTS_HOST}/players/statistics"
            params = {
                "id": player_id,
                "season": CURRENT_SEASON.split("-")[0]  # 2024
            }
            headers = {
                "X-RapidAPI-Key": API_SPORTS_KEY,
                "X-RapidAPI-Host": API_SPORTS_HOST
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, headers=headers, timeout=15.0)
                
                if response.status_code == 200:
                    data = response.json()
                    logger.info(f"✓ Fetched stats from API-Sports for player {player_id}")
                    return data
                elif response.status_code == 429:
                    logger.error("⚠️ API-Sports rate limit hit (429)")
                    return None
                else:
                    logger.error(f"API-Sports error: {response.status_code}")
                    return None
        except Exception as e:
            logger.error(f"API fetch error: {e}")
            return None
    
    async def get_player_stats(self, player_id: str) -> Optional[Dict[str, Any]]:
        """
        Get player stats with cache-first strategy
        """
        # Check cache first
        cached_data = await self.get_cached_stats(player_id)
        if cached_data:
            return cached_data
        
        # If not in cache, fetch from API
        api_data = await self.fetch_player_stats_from_api(player_id)
        if api_data:
            await self.set_cached_stats(player_id, api_data)
            return api_data
        
        return None
    
    async def search_player_by_name(self, player_name: str) -> Optional[str]:
        """
        Search for player ID by name using API-Sports
        Returns player_id if found
        """
        try:
            url = f"https://{API_SPORTS_HOST}/players"
            params = {
                "search": player_name,
                "season": CURRENT_SEASON.split("-")[0]
            }
            headers = {
                "X-RapidAPI-Key": API_SPORTS_KEY,
                "X-RapidAPI-Host": API_SPORTS_HOST
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, headers=headers, timeout=10.0)
                
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("response", [])
                    
                    # Fuzzy match to find best player
                    best_match = None
                    best_score = 0
                    
                    for player in results:
                        full_name = f"{player.get('firstname', '')} {player.get('lastname', '')}".strip()
                        score = fuzz.ratio(player_name.lower(), full_name.lower())
                        
                        if score > best_score and score >= 80:
                            best_score = score
                            best_match = player.get('id')
                    
                    if best_match:
                        logger.info(f"✓ Found player {player_name} with ID {best_match} (score: {best_score})")
                        return str(best_match)
                    else:
                        logger.warning(f"No match found for {player_name}")
                        
        except Exception as e:
            logger.error(f"Player search error: {e}")
        
        return None
    
    def extract_last_10_games(self, stats_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract last 10 games from API-Sports response
        """
        try:
            response = stats_data.get("response", [])
            if not response:
                return []
            
            # Sort by date (most recent first)
            games = sorted(response, key=lambda x: x.get("game", {}).get("date", ""), reverse=True)
            
            # Take last 10 games
            last_10 = games[:10]
            
            formatted_games = []
            for game in last_10:
                stats = game.get("statistics", [])
                if stats:
                    stat = stats[0]  # First stat block (usually the main one)
                    formatted_games.append({
                        "date": game.get("game", {}).get("date", ""),
                        "points": stat.get("points", 0) or 0,
                        "rebounds": stat.get("totReb", 0) or 0,
                        "assists": stat.get("assists", 0) or 0,
                        "threes_made": stat.get("tpm", 0) or 0,
                        "minutes": stat.get("min", "0")
                    })
            
            return formatted_games
        except Exception as e:
            logger.error(f"Game extraction error: {e}")
            return []
    
    async def calculate_hit_rate(
        self, 
        player_name: str, 
        prop_type: str, 
        line_value: float
    ) -> Optional[Dict[str, Any]]:
        """
        Calculate real hit rate from last 10 games
        
        Args:
            player_name: Full player name
            prop_type: 'points', 'rebounds', 'assists', or '3pt'
            line_value: The line to compare against
            
        Returns:
            Dict with hit_rate, games_over, total_games, and game_data
        """
        try:
            # Search for player
            player_id = await self.search_player_by_name(player_name)
            if not player_id:
                logger.warning(f"Could not find player ID for {player_name}")
                return None
            
            # Get player stats (cached or fresh)
            stats_data = await self.get_player_stats(player_id)
            if not stats_data:
                logger.warning(f"Could not fetch stats for {player_name}")
                return None
            
            # Extract last 10 games
            last_10_games = self.extract_last_10_games(stats_data)
            if not last_10_games:
                logger.warning(f"No game data found for {player_name}")
                return None
            
            # Determine which stat to check
            stat_key_map = {
                "points": "points",
                "rebounds": "rebounds",
                "assists": "assists",
                "3pt": "threes_made"
            }
            stat_key = stat_key_map.get(prop_type.lower(), "points")
            
            # Count games where player went OVER the line
            games_over = 0
            for game in last_10_games:
                if game.get(stat_key, 0) > line_value:
                    games_over += 1
            
            total_games = len(last_10_games)
            hit_rate = (games_over / total_games) if total_games > 0 else 0
            
            result = {
                "player_name": player_name,
                "player_id": player_id,
                "prop_type": prop_type,
                "line_value": line_value,
                "hit_rate": round(hit_rate, 3),
                "games_over": games_over,
                "total_games": total_games,
                "last_10_games": last_10_games,
                "cached_at": stats_data.get("cached_at") if isinstance(stats_data, dict) else None
            }
            
            logger.info(
                f"✓ Hit rate for {player_name} {prop_type} O{line_value}: "
                f"{games_over}/{total_games} = {hit_rate:.1%}"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Hit rate calculation error: {e}")
            return None
    
    async def validate_demon_line(
        self,
        player_name: str,
        prop_type: str,
        demon_line: float,
        min_hit_rate: float = 0.40
    ) -> bool:
        """
        Validate if a Demon line qualifies based on real L10 data
        
        Args:
            player_name: Full player name
            prop_type: Type of prop
            demon_line: The boosted demon line
            min_hit_rate: Minimum hit rate to qualify (default 40%)
            
        Returns:
            True if demon line is valid, False otherwise
        """
        hit_rate_data = await self.calculate_hit_rate(player_name, prop_type, demon_line)
        
        if not hit_rate_data:
            return False
        
        is_valid = hit_rate_data["hit_rate"] >= min_hit_rate
        
        if is_valid:
            logger.info(
                f"✓ DEMON VALIDATED: {player_name} {prop_type} {demon_line} "
                f"({hit_rate_data['hit_rate']:.1%} hit rate)"
            )
        else:
            logger.info(
                f"✗ Demon rejected: {player_name} {prop_type} {demon_line} "
                f"({hit_rate_data['hit_rate']:.1%} < {min_hit_rate:.1%})"
            )
        
        return is_valid
    
    async def get_cache_status(self) -> Dict[str, Any]:
        """
        Get statistics about the cache
        """
        try:
            total_cached = await self.stats_cache.count_documents({})
            
            # Count expired entries
            cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=CACHE_TTL_HOURS)).isoformat()
            expired = await self.stats_cache.count_documents({
                "cached_at": {"$lt": cutoff_time}
            })
            
            return {
                "total_players_cached": total_cached,
                "expired_entries": expired,
                "active_entries": total_cached - expired,
                "cache_ttl_hours": CACHE_TTL_HOURS,
                "current_season": CURRENT_SEASON
            }
        except Exception as e:
            logger.error(f"Cache status error: {e}")
            return {}
    
    async def clear_expired_cache(self):
        """
        Remove expired cache entries
        """
        try:
            cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=CACHE_TTL_HOURS)).isoformat()
            result = await self.stats_cache.delete_many({
                "cached_at": {"$lt": cutoff_time}
            })
            logger.info(f"✓ Cleared {result.deleted_count} expired cache entries")
            return result.deleted_count
        except Exception as e:
            logger.error(f"Cache cleanup error: {e}")
            return 0
