"""
NBA Stats Manager with API-Sports Integration
Handles player statistics with 24hr persistent caching
GLOBAL ROSTER SYNC for all 30 NBA teams
"""

import httpx
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any
from thefuzz import fuzz
from motor.motor_asyncio import AsyncIOMotorDatabase
import asyncio

logger = logging.getLogger(__name__)

API_SPORTS_KEY = "9057bc1422b361f64cc071581dd1b240"
API_SPORTS_BASE_URL = "https://v2.nba.api-sports.io"
CACHE_TTL_HOURS = 24
CURRENT_SEASON = "2024"  # Latest available season in API-Sports (2024-25 season)
ROSTER_SYNC_INTERVAL_HOURS = 24


class StatsManager:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.stats_cache = db.stats_cache
        self.league_roster = db.league_roster
        self.last_roster_sync = None
        
    async def get_all_nba_teams(self) -> List[Dict[str, Any]]:
        """
        Fetch all NBA teams from API-Sports
        """
        try:
            url = f"{API_SPORTS_BASE_URL}/teams"
            headers = {"x-apisports-key": API_SPORTS_KEY}
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, timeout=15.0)
                
                if response.status_code == 200:
                    data = response.json()
                    teams = data.get("response", [])
                    
                    # Filter only NBA franchise teams
                    nba_teams = [t for t in teams if t.get("nbaFranchise", False)]
                    logger.info(f"✓ Found {len(nba_teams)} NBA teams")
                    return nba_teams
                else:
                    logger.error(f"Teams API error: {response.status_code}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching teams: {e}")
            return []
    
    async def sync_nba_rosters(self, force: bool = False) -> Dict[str, Any]:
        """
        Sync complete NBA rosters for all 30 teams
        Stores player_name -> team_id -> player_id mapping in league_roster collection
        
        Args:
            force: Force sync even if recently synced
            
        Returns:
            Dict with sync statistics
        """
        try:
            # Check if we need to sync
            if not force and self.last_roster_sync:
                time_since_sync = datetime.now(timezone.utc) - self.last_roster_sync
                if time_since_sync < timedelta(hours=ROSTER_SYNC_INTERVAL_HOURS):
                    logger.info(f"Roster sync skipped - last synced {time_since_sync.seconds // 3600}h ago")
                    return {"status": "skipped", "reason": "recently_synced"}
            
            logger.info("🔄 Starting global NBA roster sync...")
            
            # Get all NBA teams
            teams = await self.get_all_nba_teams()
            if not teams:
                return {"status": "error", "reason": "no_teams_found"}
            
            total_players = 0
            synced_teams = 0
            failed_teams = []
            
            url = f"{API_SPORTS_BASE_URL}/players"
            headers = {"x-apisports-key": API_SPORTS_KEY}
            
            async with httpx.AsyncClient() as client:
                for team in teams:
                    team_id = team.get("id")
                    team_name = team.get("name")
                    
                    try:
                        # Fetch all players for this team
                        params = {
                            "team": str(team_id),
                            "season": CURRENT_SEASON  # Use 2025 directly
                        }
                        
                        response = await client.get(url, params=params, headers=headers, timeout=10.0)
                        
                        if response.status_code == 200:
                            data = response.json()
                            players = data.get("response", [])
                            
                            # Store each player in league_roster
                            for player in players:
                                player_id = player.get("id")
                                firstname = player.get("firstname", "")
                                lastname = player.get("lastname", "")
                                full_name = f"{firstname} {lastname}".strip()
                                
                                if player_id and full_name:
                                    roster_entry = {
                                        "player_id": str(player_id),
                                        "player_name": full_name,
                                        "firstname": firstname,
                                        "lastname": lastname,
                                        "team_id": str(team_id),
                                        "team_name": team_name,
                                        "team_code": team.get("code", ""),
                                        "synced_at": datetime.now(timezone.utc).isoformat(),
                                        "season": CURRENT_SEASON
                                    }
                                    
                                    await self.league_roster.update_one(
                                        {"player_id": str(player_id)},
                                        {"$set": roster_entry},
                                        upsert=True
                                    )
                                    total_players += 1
                            
                            synced_teams += 1
                            logger.info(f"  ✓ {team_name}: {len(players)} players")
                            
                            # Small delay to avoid rate limiting
                            await asyncio.sleep(0.5)
                        else:
                            logger.warning(f"  ✗ {team_name}: HTTP {response.status_code}")
                            failed_teams.append(team_name)
                            
                    except Exception as e:
                        logger.error(f"  ✗ {team_name}: {str(e)}")
                        failed_teams.append(team_name)
            
            self.last_roster_sync = datetime.now(timezone.utc)
            
            result = {
                "status": "completed",
                "total_teams": len(teams),
                "synced_teams": synced_teams,
                "total_players": total_players,
                "failed_teams": failed_teams,
                "synced_at": self.last_roster_sync.isoformat()
            }
            
            logger.info(f"✅ Roster sync complete: {total_players} players from {synced_teams}/{len(teams)} teams")
            
            return result
            
        except Exception as e:
            logger.error(f"Roster sync error: {e}")
            return {"status": "error", "error": str(e)}
    
    async def search_player_in_roster(self, player_name: str) -> Optional[Dict[str, Any]]:
        """
        Search for player in local league_roster collection
        Uses fuzzy matching to handle name variations
        
        Returns:
            Dict with player_id, team_id, team_name if found
        """
        try:
            # Get all players from roster
            cursor = self.league_roster.find({})
            all_players = await cursor.to_list(length=1000)
            
            if not all_players:
                logger.warning("League roster is empty - run sync_nba_rosters() first")
                return None
            
            # Fuzzy match
            best_match = None
            best_score = 0
            
            for player in all_players:
                stored_name = player.get("player_name", "")
                score = fuzz.ratio(player_name.lower(), stored_name.lower())
                
                if score > best_score and score >= 70:
                    best_score = score
                    best_match = player
            
            if best_match:
                logger.info(
                    f"✓ Found {player_name} -> {best_match['player_name']} "
                    f"(ID: {best_match['player_id']}, Team: {best_match['team_name']}, Score: {best_score})"
                )
                return best_match
            else:
                logger.warning(f"Player {player_name} not found in roster (best score: {best_score})")
                return None
                
        except Exception as e:
            logger.error(f"Roster search error: {e}")
            return None
        
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
        Fetch player statistics from API-Sports (Direct)
        Endpoint: /players/statistics
        """
        try:
            url = f"{API_SPORTS_BASE_URL}/players/statistics"
            params = {
                "id": player_id,
                "season": CURRENT_SEASON  # Use 2025 directly
            }
            headers = {
                "x-apisports-key": API_SPORTS_KEY
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
        Search for player ID by name using local roster first (FAST)
        Falls back to roster refresh if not found
        
        Returns player_id if found
        """
        # Try local roster first
        roster_entry = await self.search_player_in_roster(player_name)
        if roster_entry:
            return roster_entry.get("player_id")
        
        # If not in roster, player might be newly added - return None
        logger.warning(f"Player {player_name} not found in roster - may need refresh")
        return None
    
    async def clear_all_cache(self) -> int:
        """
        Clear ALL cache entries (use when changing seasons)
        """
        try:
            result = await self.stats_cache.delete_many({})
            logger.info(f"✓ Cleared {result.deleted_count} total cache entries")
            return result.deleted_count
        except Exception as e:
            logger.error(f"Cache clear error: {e}")
            return 0
    
    def extract_last_10_games(self, stats_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract last 10 games from API-Sports response
        """
        try:
            response = stats_data.get("response", [])
            if not response:
                logger.warning("No games found in response")
                return []
            
            logger.info(f"Processing {len(response)} total games")
            
            # API-Sports returns games already, we just need to take last 10
            # They appear to be in reverse chronological order already
            last_10 = response[:10]
            
            formatted_games = []
            for game in last_10:
                formatted_games.append({
                    "game_id": game.get("game", {}).get("id") if isinstance(game.get("game"), dict) else game.get("game", {}).get("id", 0),
                    "points": game.get("points", 0) or 0,
                    "rebounds": game.get("totReb", 0) or 0,
                    "assists": game.get("assists", 0) or 0,
                    "threes_made": game.get("tpm", 0) or 0,
                    "minutes": game.get("min", "0")
                })
            
            logger.info(f"✓ Extracted {len(formatted_games)} games for hit rate calculation")
            return formatted_games
        except Exception as e:
            logger.error(f"Game extraction error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    async def calculate_hit_rate(
        self, 
        player_name: str, 
        prop_type: str, 
        line_value: float
    ) -> Optional[Dict[str, Any]]:
        """
        Calculate TRIPLE-VIEW hit rate: L5, L10, and Season
        Includes trend detection (🔥 Trending Up, ⏰ Heavy Minutes)
        
        Args:
            player_name: Full player name
            prop_type: 'points', 'rebounds', 'assists', or '3pt'
            line_value: The line to compare against
            
        Returns:
            Dict with L5, L10, Season stats, trends, and full game data
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
            
            # Extract ALL games for season
            response = stats_data.get("response", [])
            if not response:
                logger.warning("No games found in response")
                return None
            
            # Format all games
            all_games = []
            for game in response:
                all_games.append({
                    "game_id": game.get("game", {}).get("id") if isinstance(game.get("game"), dict) else game.get("game", {}).get("id", 0),
                    "points": game.get("points", 0) or 0,
                    "rebounds": game.get("totReb", 0) or 0,
                    "assists": game.get("assists", 0) or 0,
                    "threes_made": game.get("tpm", 0) or 0,
                    "minutes": game.get("min", "0") or "0"
                })
            
            if not all_games:
                logger.warning(f"No game data found for {player_name}")
                return None
            
            logger.info(f"Processing {len(all_games)} total games for {player_name}")
            
            # Determine which stat to check
            stat_key_map = {
                "points": "points",
                "rebounds": "rebounds",
                "assists": "assists",
                "3pt": "threes_made"
            }
            stat_key = stat_key_map.get(prop_type.lower(), "points")
            
            # Extract L5, L10, and Season
            l5_games = all_games[:5]
            l10_games = all_games[:10]
            season_games = all_games
            
            # Calculate hit rates for each window
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
            
            # Calculate average minutes for L5 and Season
            l5_minutes = sum(float(g.get("minutes", "0").replace(":", ".") if ":" not in str(g.get("minutes", "0")) else g.get("minutes", "0").split(":")[0]) for g in l5_games) / len(l5_games) if l5_games else 0
            season_minutes = sum(float(g.get("minutes", "0").replace(":", ".") if ":" not in str(g.get("minutes", "0")) else g.get("minutes", "0").split(":")[0]) for g in season_games) / len(season_games) if season_games else 0
            
            # Trend Detection
            trends = []
            
            # 🔥 Trending Up: L5 avg > Season avg by 20%+
            if l5_stats["avg"] > season_stats["avg"] * 1.20:
                trends.append("🔥 Trending Up")
            
            # ❄️ Trending Down: L5 avg < Season avg by 20%+
            elif l5_stats["avg"] < season_stats["avg"] * 0.80:
                trends.append("❄️ Trending Down")
            
            # ⏰ Heavy Minutes: L5 minutes > Season minutes by 5+
            if l5_minutes > season_minutes + 5:
                trends.append("⏰ Heavy Minutes")
            
            # 🎯 Consistent: Hit rate variation < 10% across windows
            hit_rates = [l5_stats["hit_rate"], l10_stats["hit_rate"], season_stats["hit_rate"]]
            if max(hit_rates) - min(hit_rates) < 0.10:
                trends.append("🎯 Consistent")
            
            result = {
                "player_name": player_name,
                "player_id": player_id,
                "prop_type": prop_type,
                "line_value": line_value,
                "season": CURRENT_SEASON,
                "l5": {
                    "hit_rate": l5_stats["hit_rate"],
                    "games_over": l5_stats["games_over"],
                    "total_games": l5_stats["total_games"],
                    "avg": l5_stats["avg"]
                },
                "l10": {
                    "hit_rate": l10_stats["hit_rate"],
                    "games_over": l10_stats["games_over"],
                    "total_games": l10_stats["total_games"],
                    "avg": l10_stats["avg"]
                },
                "season": {
                    "hit_rate": season_stats["hit_rate"],
                    "games_over": season_stats["games_over"],
                    "total_games": season_stats["total_games"],
                    "avg": season_stats["avg"]
                },
                "trends": trends,
                "minutes_info": {
                    "l5_avg": round(l5_minutes, 1),
                    "season_avg": round(season_minutes, 1)
                },
                "all_games": all_games[:10],  # Return L10 for detail view
                "cached_at": stats_data.get("cached_at") if isinstance(stats_data, dict) else None
            }
            
            logger.info(
                f"✓ Triple-view for {player_name} {prop_type} O{line_value}: "
                f"L5: {l5_stats['games_over']}/{l5_stats['total_games']}, "
                f"L10: {l10_stats['games_over']}/{l10_stats['total_games']}, "
                f"Season: {season_stats['games_over']}/{season_stats['total_games']} | "
                f"Trends: {', '.join(trends) if trends else 'None'}"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Hit rate calculation error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    async def validate_demon_line(
        self,
        player_name: str,
        prop_type: str,
        demon_line: float,
        min_hit_rate: float = 0.40
    ) -> Dict[str, Any]:
        """
        Validate if a Demon line qualifies using TRIPLE CHECK
        A demon must pass L5, L10, AND Season validation
        
        Args:
            player_name: Full player name
            prop_type: Type of prop
            demon_line: The boosted demon line
            min_hit_rate: Minimum hit rate to qualify (default 40%)
            
        Returns:
            Dict with validation results for all three windows
        """
        hit_rate_data = await self.calculate_hit_rate(player_name, prop_type, demon_line)
        
        if not hit_rate_data:
            return {
                "is_valid_demon": False,
                "reason": "No data available",
                "triple_check": None
            }
        
        # Triple Check: L5, L10, Season
        l5_pass = hit_rate_data["l5"]["hit_rate"] >= min_hit_rate
        l10_pass = hit_rate_data["l10"]["hit_rate"] >= min_hit_rate
        season_pass = hit_rate_data["season"]["hit_rate"] >= min_hit_rate
        
        # Demon is valid if at least 2 out of 3 windows pass
        passes = sum([l5_pass, l10_pass, season_pass])
        is_valid = passes >= 2
        
        triple_check = {
            "l5": {
                "hit_rate": hit_rate_data["l5"]["hit_rate"],
                "passed": l5_pass,
                "games": f"{hit_rate_data['l5']['games_over']}/{hit_rate_data['l5']['total_games']}"
            },
            "l10": {
                "hit_rate": hit_rate_data["l10"]["hit_rate"],
                "passed": l10_pass,
                "games": f"{hit_rate_data['l10']['games_over']}/{hit_rate_data['l10']['total_games']}"
            },
            "season": {
                "hit_rate": hit_rate_data["season"]["hit_rate"],
                "passed": season_pass,
                "games": f"{hit_rate_data['season']['games_over']}/{hit_rate_data['season']['total_games']}"
            }
        }
        
        verdict = "✅ VALID DEMON" if is_valid else "❌ REJECTED"
        confidence = "High" if passes == 3 else "Medium" if passes == 2 else "Low"
        
        logger.info(
            f"{verdict}: {player_name} {prop_type} {demon_line} | "
            f"Triple Check: L5={l5_pass}, L10={l10_pass}, Season={season_pass} | "
            f"Confidence: {confidence}"
        )
        
        return {
            "is_valid_demon": is_valid,
            "passes": passes,
            "confidence": confidence,
            "triple_check": triple_check,
            "trends": hit_rate_data.get("trends", []),
            "full_data": hit_rate_data
        }
    
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
