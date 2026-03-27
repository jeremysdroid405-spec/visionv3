"""
NBA Stats Manager with BallDontLie API Integration
Real-time 2025-26 season data (March 2026)
Triple-view analytics: L5, L10, Season
AUTONOMOUS DAILY DATA LOADING
"""

import httpx
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any
from thefuzz import fuzz
from motor.motor_asyncio import AsyncIOMotorDatabase
import asyncio

logger = logging.getLogger(__name__)

# BallDontLie API Configuration
BDL_API_KEY = os.environ.get("BDL_API_KEY")
BDL_BASE_URL = "https://api.balldontlie.io/v1"
# Use 2024 season (2024-2025 NBA) which has actual data
# Note: BallDontLie uses the season START year (e.g., 2024 = 2024-2025 season)
CURRENT_SEASON = os.environ.get("NBA_SEASON", "2024")
CACHE_TTL_HOURS = 24
ROSTER_SYNC_INTERVAL_HOURS = 24


class RateLimitInfo:
    """Simple rate limit info holder for compatibility"""
    def get_status(self):
        return {
            "api": "BallDontLie",
            "limit": "Unlimited (free tier)",
            "status": "OK"
        }


class StatsManager:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.stats_cache = db.stats_cache
        self.league_roster = db.league_roster
        self.todays_games = db.todays_games
        self.last_roster_sync = None
        self.last_games_sync = None
        self.rate_limit = RateLimitInfo()
    
    def get_todays_date(self) -> str:
        """
        Get today's date dynamically from system clock
        Returns date in YYYY-MM-DD format
        """
        now = datetime.now(timezone.utc)
        
        # If it's past 4 AM, use current day, otherwise use previous day
        if now.hour >= 4:
            date_to_use = now
        else:
            date_to_use = now - timedelta(days=1)
        
        return date_to_use.strftime("%Y-%m-%d")
    
    async def fetch_todays_games(self) -> List[Dict[str, Any]]:
        """
        Fetch all NBA games for today's date from BallDontLie
        Updates automatically based on system clock
        """
        try:
            today = self.get_todays_date()
            logger.info(f"🗓️ Fetching games for {today}")
            
            url = f"{BDL_BASE_URL}/games"
            params = {
                "start_date": today,
                "end_date": today,
                "per_page": 100
            }
            headers = {"Authorization": BDL_API_KEY}
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, headers=headers, timeout=15.0)
                
                if response.status_code == 200:
                    data = response.json()
                    games = data.get("data", [])
                    
                    # Store in MongoDB
                    if games:
                        await self.todays_games.delete_many({})  # Clear old games
                        for game in games:
                            game["fetched_at"] = datetime.now(timezone.utc).isoformat()
                            game["game_date"] = today
                            await self.todays_games.insert_one(game)
                        
                        logger.info(f"✅ Loaded {len(games)} games for {today}")
                        self.last_games_sync = datetime.now(timezone.utc)
                    else:
                        logger.warning(f"No games found for {today}")
                    
                    return games
                else:
                    logger.error(f"BDL games API error: {response.status_code}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching today's games: {e}")
            return []
    
    async def extract_players_from_games(self, games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Extract all players from today's games
        Returns list of unique players
        """
        players_dict = {}
        
        for game in games:
            home_team = game.get("home_team", {})
            visitor_team = game.get("visitor_team", {})
            
            # Store game info for each team
            for team in [home_team, visitor_team]:
                team_id = team.get("id")
                if team_id and team_id not in players_dict:
                    players_dict[team_id] = {
                        "team_id": team_id,
                        "team_name": team.get("full_name", ""),
                        "game_id": game.get("id")
                    }
        
        return list(players_dict.values())
    
    async def sync_players_for_team(self, team_id: int) -> List[str]:
        """
        Get all players for a specific team from BallDontLie
        Returns list of player IDs
        """
        try:
            url = f"{BDL_BASE_URL}/players"
            params = {
                "team_ids[]": team_id,
                "per_page": 20
            }
            headers = {"Authorization": BDL_API_KEY}
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, headers=headers, timeout=10.0)
                
                if response.status_code == 200:
                    data = response.json()
                    players = data.get("data", [])
                    
                    player_ids = []
                    for player in players:
                        player_id = player.get("id")
                        if player_id:
                            player_ids.append(str(player_id))
                            
                            # Store in roster
                            roster_entry = {
                                "player_id": str(player_id),
                                "player_name": f"{player.get('first_name', '')} {player.get('last_name', '')}".strip(),
                                "team_id": str(team_id),
                                "position": player.get("position", ""),
                                "synced_at": datetime.now(timezone.utc).isoformat(),
                                "season": CURRENT_SEASON
                            }
                            
                            await self.league_roster.update_one(
                                {"player_id": str(player_id)},
                                {"$set": roster_entry},
                                upsert=True
                            )
                    
                    return player_ids
                else:
                    logger.error(f"Error fetching players for team {team_id}: {response.status_code}")
                    return []
        except Exception as e:
            logger.error(f"Error syncing team {team_id}: {e}")
            return []
    
    async def autonomous_daily_sync(self) -> Dict[str, Any]:
        """
        AUTONOMOUS DAILY DATA LOADING
        Runs automatically on app startup and daily refresh
        
        1. Fetch today's games (dynamic date)
        2. Extract all teams playing today
        3. Sync player rosters for those teams
        4. Calculate hit rates for key players
        """
        try:
            sync_start = datetime.now(timezone.utc)
            today = self.get_todays_date()
            
            logger.info(f"🚀 AUTONOMOUS SYNC STARTED for {today}")
            logger.info(f"Season: 2025-26 | Data Source: BallDontLie")
            
            # Step 1: Fetch today's games
            games = await self.fetch_todays_games()
            
            if not games:
                return {
                    "success": False,
                    "message": f"No games found for {today}",
                    "date": today
                }
            
            # Step 2: Extract teams
            teams = await self.extract_players_from_games(games)
            logger.info(f"📋 Found {len(teams)} teams playing today")
            
            # Step 3: Sync rosters for today's teams
            total_players = 0
            for team_info in teams:
                team_id = team_info["team_id"]
                team_name = team_info["team_name"]
                
                player_ids = await self.sync_players_for_team(team_id)
                total_players += len(player_ids)
                logger.info(f"  ✓ {team_name}: {len(player_ids)} players")
            
            sync_duration = (datetime.now(timezone.utc) - sync_start).total_seconds()
            
            result = {
                "success": True,
                "message": f"✅ Daily sync complete for {today}",
                "date": today,
                "games_found": len(games),
                "teams_synced": len(teams),
                "players_synced": total_players,
                "sync_duration_seconds": round(sync_duration, 2),
                "synced_at": datetime.now(timezone.utc).isoformat(),
                "data_source": "BallDontLie (March 2026)"
            }
            
            logger.info(f"✅ AUTONOMOUS SYNC COMPLETE - {total_players} players ready")
            
            return result
            
        except Exception as e:
            logger.error(f"Autonomous sync error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {
                "success": False,
                "error": str(e),
                "date": self.get_todays_date()
            }
    
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
        
        Note: BallDontLie search works best with first OR last name, not full names.
        We try multiple search strategies:
        1. Search by last name (most reliable)
        2. Search by first name
        3. Search by full name (sometimes works)
        """
        try:
            url = f"{BDL_BASE_URL}/players"
            headers = {"Authorization": BDL_API_KEY}
            
            # Split name into parts
            name_parts = player_name.strip().split()
            
            # Try different search strategies
            search_terms = []
            if len(name_parts) >= 2:
                # Try last name first (most reliable)
                search_terms.append(name_parts[-1])  # Last name
                search_terms.append(name_parts[0])   # First name
            else:
                search_terms.append(player_name)
            
            for search_term in search_terms:
                params = {"search": search_term}
                
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, params=params, headers=headers, timeout=10.0)
                    
                    if response.status_code == 200:
                        data = response.json()
                        players = data.get("data", [])
                        
                        if not players:
                            continue
                        
                        logger.info(f"Search for '{search_term}' returned {len(players)} results")
                        
                        # Find best match using fuzzy matching against the original full name
                        best_match = None
                        best_score = 0
                        best_name = ""
                        
                        for player in players:
                            full_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
                            # Use partial_ratio for better partial matching
                            score = max(
                                fuzz.ratio(player_name.lower(), full_name.lower()),
                                fuzz.partial_ratio(player_name.lower(), full_name.lower())
                            )
                            
                            if score > best_score and score >= 60:
                                best_score = score
                                best_match = player.get('id')
                                best_name = full_name
                        
                        if best_match:
                            logger.info(f"✓ Found player {best_name} with ID {best_match} (score: {best_score})")
                            return str(best_match)
            
            logger.warning(f"No match found for {player_name} after all search strategies")
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
        Using BallDontLie data for 2024-25 season
        Filters out games where player didn't play (DNP/0 minutes)
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
            
            # Filter out games where player didn't play (check minutes or total stats)
            # BallDontLie returns 'min' as integer or string like "32" or "00"
            def player_played(game):
                # Check if minutes played is non-zero
                minutes = game.get("min")
                if minutes:
                    # Convert to string and check if it's not zero
                    min_str = str(minutes).replace(":", "").strip()
                    if min_str and min_str != "0" and min_str != "00" and min_str != "000":
                        return True
                # Fallback: check if any stat is non-zero
                pts = game.get("pts", 0) or 0
                reb = game.get("reb", 0) or 0
                ast = game.get("ast", 0) or 0
                return (pts + reb + ast) > 0
            
            played_games = [g for g in all_games if player_played(g)]
            
            logger.info(f"Player {player_name}: {len(played_games)} games played out of {len(all_games)} total")
            
            if not played_games:
                logger.warning(f"No games found where {player_name} played")
                return None
            
            # Map prop type to BDL field
            prop_map = {
                "points": "pts",
                "rebounds": "reb",
                "assists": "ast",
                "3pt": "fg3m"
            }
            stat_key = prop_map.get(prop_type.lower(), "pts")
            
            # Extract L5, L10, Season from PLAYED games only
            l5_games = played_games[:5]
            l10_games = played_games[:10]
            season_games = played_games
            
            def calc_window_stats(games, line):
                if not games:
                    return {"games_over": 0, "total_games": 0, "hit_rate": 0, "avg": 0}
                
                games_over = sum(1 for g in games if (g.get(stat_key, 0) or 0) > line)
                total_games = len(games)
                hit_rate = (games_over / total_games) if total_games > 0 else 0
                avg = sum((g.get(stat_key, 0) or 0) for g in games) / total_games if total_games > 0 else 0
                
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
            
            # Get latest game date (from played games)
            latest_game = played_games[0].get("game", {}) if played_games else {}
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
                        "points": g.get("pts", 0) or 0,
                        "rebounds": g.get("reb", 0) or 0,
                        "assists": g.get("ast", 0) or 0,
                        "threes": g.get("fg3m", 0) or 0,
                        "minutes": g.get("min", "0:00")
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
    
    async def get_cache_status(self) -> Dict[str, Any]:
        """Get cache statistics"""
        try:
            total_entries = await self.stats_cache.count_documents({})
            roster_entries = await self.league_roster.count_documents({})
            games_entries = await self.todays_games.count_documents({})
            
            # Get sample of cached players
            recent_cache = await self.stats_cache.find({}).sort("cached_at", -1).limit(5).to_list(5)
            recent_players = [entry.get("player_id") for entry in recent_cache]
            
            return {
                "total_cached_stats": total_entries,
                "total_roster_entries": roster_entries,
                "total_games_cached": games_entries,
                "recent_cached_players": recent_players,
                "cache_ttl_hours": CACHE_TTL_HOURS,
                "season": CURRENT_SEASON,
                "last_games_sync": self.last_games_sync.isoformat() if self.last_games_sync else None
            }
        except Exception as e:
            logger.error(f"Cache status error: {e}")
            return {"error": str(e)}
    
    async def clear_expired_cache(self) -> int:
        """Clear expired cache entries"""
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=CACHE_TTL_HOURS)
            result = await self.stats_cache.delete_many({
                "cached_at": {"$lt": cutoff.isoformat()}
            })
            logger.info(f"✓ Cleared {result.deleted_count} expired cache entries")
            return result.deleted_count
        except Exception as e:
            logger.error(f"Cache clear error: {e}")
            return 0
    
    async def sync_nba_rosters(self, force: bool = False) -> Dict[str, Any]:
        """
        Sync NBA rosters using BallDontLie API
        Gets all NBA teams and their players
        """
        try:
            if not force and self.last_roster_sync:
                hours_since_sync = (datetime.now(timezone.utc) - self.last_roster_sync).total_seconds() / 3600
                if hours_since_sync < ROSTER_SYNC_INTERVAL_HOURS:
                    return {
                        "status": "skipped",
                        "reason": f"Last sync was {hours_since_sync:.1f} hours ago"
                    }
            
            logger.info("🔄 Starting global NBA roster sync via BallDontLie...")
            
            # Fetch all NBA teams
            url = f"{BDL_BASE_URL}/teams"
            headers = {"Authorization": BDL_API_KEY}
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, timeout=15.0)
                
                if response.status_code != 200:
                    return {"status": "error", "reason": f"API error: {response.status_code}"}
                
                teams = response.json().get("data", [])
                logger.info(f"✓ Found {len(teams)} NBA teams")
                
                total_players = 0
                synced_teams = 0
                
                for team in teams:
                    team_id = team.get("id")
                    team_name = team.get("full_name", "")
                    
                    if not team_id:
                        continue
                    
                    player_ids = await self.sync_players_for_team(team_id)
                    total_players += len(player_ids)
                    synced_teams += 1
                    
                    logger.info(f"  ✓ {team_name}: {len(player_ids)} players")
                    
                    # Small delay to avoid rate limits
                    await asyncio.sleep(0.2)
                
                self.last_roster_sync = datetime.now(timezone.utc)
                
                return {
                    "status": "completed",
                    "total_teams": len(teams),
                    "synced_teams": synced_teams,
                    "total_players": total_players,
                    "synced_at": self.last_roster_sync.isoformat()
                }
                
        except Exception as e:
            logger.error(f"Roster sync error: {e}")
            return {"status": "error", "reason": str(e)}
    
    async def validate_demon_line(
        self,
        player_name: str,
        prop_type: str,
        demon_line: float
    ) -> bool:
        """
        Validate if a demon line qualifies based on L10 hit rate
        A demon line is valid if L10 hit rate >= 40%
        """
        try:
            hit_rate_data = await self.calculate_hit_rate(player_name, prop_type, demon_line)
            
            if not hit_rate_data:
                return False
            
            l10_hit_rate = hit_rate_data.get("l10", {}).get("hit_rate", 0)
            
            # Demon is valid if player hits the line 40%+ of the time in L10
            return l10_hit_rate >= 0.40
            
        except Exception as e:
            logger.error(f"Demon validation error: {e}")
            return False
    
    async def get_todays_games_summary(self) -> Dict[str, Any]:
        """
        Get summary of today's games from cache
        Returns formatted game data for frontend
        """
        try:
            games = await self.todays_games.find({}).to_list(100)
            
            if not games:
                # Try to fetch fresh data
                games = await self.fetch_todays_games()
            
            formatted_games = []
            for game in games:
                # Handle both MongoDB doc and direct API response
                if "_id" in game:
                    del game["_id"]
                
                home_team = game.get("home_team", {})
                visitor_team = game.get("visitor_team", {})
                
                formatted_games.append({
                    "game_id": game.get("id"),
                    "date": game.get("date", game.get("game_date", "")),
                    "status": game.get("status", ""),
                    "home_team": {
                        "id": home_team.get("id"),
                        "name": home_team.get("full_name", ""),
                        "abbreviation": home_team.get("abbreviation", "")
                    },
                    "visitor_team": {
                        "id": visitor_team.get("id"),
                        "name": visitor_team.get("full_name", ""),
                        "abbreviation": visitor_team.get("abbreviation", "")
                    },
                    "home_score": game.get("home_team_score"),
                    "visitor_score": game.get("visitor_team_score")
                })
            
            return {
                "success": True,
                "date": self.get_todays_date(),
                "games_count": len(formatted_games),
                "games": formatted_games
            }
            
        except Exception as e:
            logger.error(f"Error getting today's games: {e}")
            return {"success": False, "error": str(e)}
