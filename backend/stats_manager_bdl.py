"""
NBA Stats Manager with BallDontLie API Integration
Real-time 2025-26 season data (March 2026)
Triple-view analytics: L5, L10, Season
"""

import httpx
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any
from thefuzz import fuzz
from motor.motor_asyncio import AsyncIOMotorDatabase
import asyncio

logger = logging.getLogger(__name__)

# BallDontLie API Configuration
BDL_API_KEY = "ad5544be-9969-434b-9389-2b7cf658c8e0"
BDL_BASE_URL = "https://api.balldontlie.io/v1"
CURRENT_SEASON = "2025"  # 2025-26 NBA season
CACHE_TTL_HOURS = 24
ROSTER_SYNC_INTERVAL_HOURS = 24


class StatsManager:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.stats_cache = db.stats_cache
        self.league_roster = db.league_roster
        self.last_roster_sync = None
    
    async def get_cached_stats(self, player_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached player stats if within TTL"""
        try:
            cached = await self.stats_cache.find_one({"player_id": player_id})
            if cached:
                cached_time = datetime.fromisoformat(cached["cached_at"])
                if datetime.now(timezone.utc) - cached_time < timedelta(hours=CACHE_TTL_HOURS):
                    logger.info(f"✓ Using cached stats for player {player_id}")
                    return cached.get("data")
        except Exception as e:
            logger.error(f"Cache retrieval error: {e}")
        return None
    
    async def set_cached_stats(self, player_id: str, data: Dict[str, Any]):
        """Store player stats in cache with timestamp"""
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
    
    async def search_player_by_name(self, player_name: str) -> Optional[str]:
        """
        Search for player by name using BallDontLie API
        Returns player_id if found
        """
        try:
            url = f"{BDL_BASE_URL}/players"
            params = {"search": player_name}
            headers = {"Authorization": BDL_API_KEY}
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, headers=headers, timeout=10.0)
                
                if response.status_code == 200:
                    data = response.json()
                    players = data.get("data", [])
                    
                    if not players:
                        logger.warning(f"No players found for '{player_name}'")
                        return None
                    
                    # Fuzzy match to find best player
                    best_match = None
                    best_score = 0
                    
                    for player in players:
                        full_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
                        score = fuzz.ratio(player_name.lower(), full_name.lower())
                        
                        if score > best_score and score >= 70:
                            best_score = score
                            best_match = player.get('id')
                    
                    if best_match:
                        logger.info(f"✓ Found player {player_name} with ID {best_match} (score: {best_score})")
                        return str(best_match)
                    else:
                        logger.warning(f"No match found for {player_name}")
                        return None
                else:
                    logger.error(f"BDL API error: {response.status_code}")
                    return None
        except Exception as e:
            logger.error(f"Player search error: {e}")
            return None
    
    async def fetch_player_stats(self, player_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch player statistics from BallDontLie for season 2025
        Returns list of game stats sorted by date (most recent first)
        """
        try:
            # Check cache first
            cached_data = await self.get_cached_stats(player_id)
            if cached_data:
                return cached_data
            
            url = f"{BDL_BASE_URL}/stats"
            params = {
                "player_ids[]": player_id,
                "seasons[]": CURRENT_SEASON,
                "per_page": 100  # Get all games
            }
            headers = {"Authorization": BDL_API_KEY}
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, headers=headers, timeout=15.0)
                
                if response.status_code == 200:
                    data = response.json()
                    games = data.get("data", [])
                    
                    # Sort by date descending (most recent first)
                    games_sorted = sorted(
                        games,
                        key=lambda x: x.get("game", {}).get("date", ""),
                        reverse=True
                    )
                    
                    logger.info(f"✓ Fetched {len(games_sorted)} games for player {player_id} (season {CURRENT_SEASON})")
                    
                    # Cache the data
                    await self.set_cached_stats(player_id, games_sorted)
                    
                    return games_sorted
                else:
                    logger.error(f"BDL API error: {response.status_code}")
                    return None
        except Exception as e:
            logger.error(f"Stats fetch error: {e}")
            return None
    
    async def calculate_hit_rate(
        self,
        player_name: str,
        prop_type: str,
        line_value: float
    ) -> Optional[Dict[str, Any]]:
        """
        Calculate triple-view hit rate: L5, L10, Season
        Using BallDontLie data for 2025-26 season
        """
        try:
            # Search for player
            player_id = await self.search_player_by_name(player_name)
            if not player_id:
                logger.warning(f"Could not find player: {player_name}")
                return None
            
            # Fetch all stats
            all_games = await self.fetch_player_stats(player_id)
            if not all_games or len(all_games) == 0:
                logger.warning(f"No stats found for player {player_name}")
                return None
            
            # Map prop type to BDL field
            prop_map = {
                "points": "pts",
                "rebounds": "reb",
                "assists": "ast",
                "3pt": "fg3m"
            }
            stat_key = prop_map.get(prop_type.lower(), "pts")
            
            # Extract L5, L10, Season
            l5_games = all_games[:5]
            l10_games = all_games[:10]
            season_games = all_games
            
            def calc_window_stats(games, line):
                if not games:
                    return {"games_over": 0, "total_games": 0, "hit_rate": 0, "avg": 0}
                
                games_over = sum(1 for g in games if g.get(stat_key, 0) > line)
                total_games = len(games)
                hit_rate = (games_over / total_games) if total_games > 0 else 0
                avg = sum(g.get(stat_key, 0) for g in games) / total_games if total_games > 0 else 0
                
                return {
                    "games_over": games_over,
                    "total_games": total_games,
                    "hit_rate": round(hit_rate, 3),
                    "avg": round(avg, 1)
                }
            
            l5_stats = calc_window_stats(l5_games, line_value)
            l10_stats = calc_window_stats(l10_games, line_value)
            season_stats = calc_window_stats(season_games, line_value)
            
            # Trend detection
            trends = []
            if l5_stats["avg"] > season_stats["avg"] * 1.20:
                trends.append("🔥 Trending Up")
            elif l5_stats["avg"] < season_stats["avg"] * 0.80:
                trends.append("❄️ Trending Down")
            
            # Get latest game date
            latest_game = all_games[0].get("game", {}) if all_games else {}
            latest_date = latest_game.get("date", "")
            
            result = {
                "player_name": player_name,
                "player_id": player_id,
                "prop_type": prop_type,
                "line_value": line_value,
                "season": CURRENT_SEASON,
                "latest_game_date": latest_date,
                "l5": l5_stats,
                "l10": l10_stats,
                "season": season_stats,
                "trends": trends,
                "last_5_games": [
                    {
                        "date": g.get("game", {}).get("date", "")[:10],
                        "points": g.get("pts", 0),
                        "rebounds": g.get("reb", 0),
                        "assists": g.get("ast", 0),
                        "threes": g.get("fg3m", 0)
                    }
                    for g in l5_games
                ]
            }
            
            logger.info(
                f"✓ Triple-view for {player_name}: "
                f"L5: {l5_stats['games_over']}/{l5_stats['total_games']}, "
                f"L10: {l10_stats['games_over']}/{l10_stats['total_games']}, "
                f"Season: {season_stats['games_over']}/{season_stats['total_games']}"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Hit rate calculation error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    async def clear_all_cache(self) -> int:
        """Clear all cache (for API migration)"""
        try:
            result = await self.stats_cache.delete_many({})
            logger.info(f"✓ Cleared {result.deleted_count} cache entries (API migration)")
            return result.deleted_count
        except Exception as e:
            logger.error(f"Cache clear error: {e}")
            return 0
