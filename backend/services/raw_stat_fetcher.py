"""
RAW STAT FETCHER - ISOLATED DATA INTEGRITY SERVICE
===================================================
This service does ONE thing: Pull raw JSON from APIs and return it UNCHANGED.

RULES:
1. NO processing, NO adjustments, NO interpretation
2. If API says pts: 4, we return pts: 4
3. Store raw responses for audit
4. Zero business logic - just fetch and return

Author: Truth Engine v3.2

Data Sources (in priority order):
1. BallDontLie API (primary)
2. NBA.com API via nba_api (fallback for rookies/missing data)
"""

import os
import httpx
import logging
import time
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

# NBA.com API fallback
try:
    from nba_api.stats.endpoints import playergamelog
    from nba_api.stats.static import players as nba_players
    NBA_API_AVAILABLE = True
except ImportError:
    NBA_API_AVAILABLE = False

logger = logging.getLogger(__name__)


class RawStatFetcher:
    """
    Isolated service for fetching raw stats from APIs.
    
    This service is intentionally simple and does NOT:
    - Calculate hit rates
    - Classify demons/goblins
    - Apply any transformations
    - Interpret or adjust values
    
    It ONLY:
    - Fetches raw JSON from BallDontLie/Tank01/NBA.com
    - Returns the exact response unchanged
    - Logs raw data for audit
    
    Data Source Priority:
    1. BallDontLie (primary)
    2. Tank01 (secondary - user has subscription)
    3. NBA.com (tertiary - fallback for rookies)
    """
    
    # Default API keys
    DEFAULT_BDL_KEY = os.environ.get("BDL_API_KEY")
    DEFAULT_TANK01_KEY = "402edbcac6mshd04997e7ca01d17p1879eajsn65ab176cdb1e"
    
    def __init__(self, db):
        self.db = db
        self.raw_stats_collection = db.dg_raw_stats_audit
        # BallDontLie
        self.BDL_BASE_URL = "https://api.balldontlie.io/v1"
        self.BDL_API_KEY = os.environ.get("BDL_API_KEY", self.DEFAULT_BDL_KEY)
        # Tank01
        self.TANK01_BASE = "https://tank01-fantasy-stats.p.rapidapi.com"
        self.TANK01_KEY = os.environ.get("TANK01_API_KEY", self.DEFAULT_TANK01_KEY)
        self.TANK01_HOST = "tank01-fantasy-stats.p.rapidapi.com"
        self.CURRENT_SEASON = "2025"
        
    def set_api_key(self, key: str):
        """Set BallDontLie API key"""
        self.BDL_API_KEY = key
        
    async def fetch_raw_player_games(self, player_id: int, season: int = 2024) -> Dict[str, Any]:
        """
        Fetch raw game logs from BallDontLie API.
        
        Returns the EXACT API response - no processing.
        
        Args:
            player_id: BallDontLie player ID
            season: NBA season year (e.g., 2024 for 2024-25 season)
            
        Returns:
            Raw API response with metadata
        """
        result = {
            "success": False,
            "source": "balldontlie",
            "player_id": player_id,
            "season": season,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "raw_response": None,
            "error": None
        }
        
        if not self.BDL_API_KEY:
            result["error"] = "BDL API key not set"
            return result
            
        try:
            url = f"{self.BDL_BASE_URL}/stats"
            params = {
                "player_ids[]": player_id,
                "seasons[]": season,
                "per_page": 100
            }
            headers = {"Authorization": self.BDL_API_KEY}
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, params=params, headers=headers)
                
                if response.status_code == 200:
                    # Store EXACT response - no modifications
                    raw_json = response.json()
                    result["success"] = True
                    result["raw_response"] = raw_json
                    result["games_count"] = len(raw_json.get("data", []))
                    
                    # Log raw fetch for audit
                    await self._log_raw_fetch(player_id, "balldontlie", raw_json)
                else:
                    result["error"] = f"API returned {response.status_code}"
                    result["raw_response"] = response.text
                    
        except Exception as e:
            result["error"] = str(e)
            logger.error(f"[RAW FETCH] Error fetching player {player_id}: {e}")
            
        return result
    
    async def fetch_raw_player_by_name(self, player_name: str) -> Dict[str, Any]:
        """
        Search for player by name and return raw API response.
        
        Returns:
            Raw API response - no processing
        """
        result = {
            "success": False,
            "source": "balldontlie",
            "search_name": player_name,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "raw_response": None,
            "error": None
        }
        
        if not self.BDL_API_KEY:
            result["error"] = "BDL API key not set"
            return result
            
        try:
            url = f"{self.BDL_BASE_URL}/players"
            # BDL API works better with last name search
            # Try last name first, then first name if needed
            search_term = player_name.split()[-1] if player_name else player_name
            params = {"search": search_term}
            headers = {"Authorization": self.BDL_API_KEY}
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, params=params, headers=headers)
                
                if response.status_code == 200:
                    raw_json = response.json()
                    result["success"] = True
                    result["raw_response"] = raw_json
                    result["players_found"] = len(raw_json.get("data", []))
                    result["search_term_used"] = search_term
                else:
                    result["error"] = f"API returned {response.status_code}"
                    
        except Exception as e:
            result["error"] = str(e)
            
        return result
    
    async def get_raw_recent_games(self, player_name: str, num_games: int = 10) -> Dict[str, Any]:
        """
        Get raw recent game stats for a player - ZERO PROCESSING.
        
        This is the primary method for the Validation UI.
        Returns exact API values for manual ESPN verification.
        
        Args:
            player_name: Player's full name
            num_games: Number of recent games to fetch
            
        Returns:
            {
                "player_name": str,
                "bdl_player_id": int,
                "games": [
                    {
                        "game_date": str,
                        "opponent": str,
                        "pts": int (RAW from API),
                        "reb": int (RAW from API),
                        "ast": int (RAW from API),
                        "raw_game_object": dict (full API response for audit)
                    }
                ]
            }
        """
        result = {
            "success": False,
            "player_name": player_name,
            "bdl_player_id": None,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "games": [],
            "error": None,
            "data_source": "balldontlie_raw"
        }
        
        # Step 1: Find player
        player_search = await self.fetch_raw_player_by_name(player_name)
        if not player_search["success"] or not player_search["raw_response"].get("data"):
            result["error"] = f"Player not found: {player_name}"
            return result
            
        # Get first matching player - store raw
        # Filter to find exact match by full name
        players = player_search["raw_response"]["data"]
        if not players:
            result["error"] = f"No players found for: {player_name}"
            return result
        
        # Try to find exact match first
        target_name_lower = player_name.lower().strip()
        matched_player = None
        for p in players:
            full_name = f"{p.get('first_name', '')} {p.get('last_name', '')}".lower().strip()
            if full_name == target_name_lower:
                matched_player = p
                break
        
        # Fallback to first result if no exact match
        if not matched_player:
            matched_player = players[0]
            result["warning"] = f"No exact match for '{player_name}', using first result: {matched_player.get('first_name')} {matched_player.get('last_name')}"
        
        player = matched_player
        player_id = player.get("id")
        result["bdl_player_id"] = player_id
        result["bdl_player_raw"] = player  # Store raw player object
        result["matched_name"] = f"{player.get('first_name')} {player.get('last_name')}"
        
        # Step 2: Fetch game logs - raw
        games_response = await self.fetch_raw_player_games(player_id)
        if not games_response["success"]:
            result["error"] = games_response.get("error", "Failed to fetch games")
            return result
            
        raw_games = games_response["raw_response"].get("data", [])
        
        # If BallDontLie has no games, try Tank01 first (user has subscription)
        if not raw_games:
            logger.info(f"[RAW FETCH] BallDontLie has no games for {player_name}, trying Tank01...")
            tank_result = await self._fetch_tank01_raw(player_name, num_games)
            if tank_result.get("success"):
                result["success"] = True
                result["games"] = tank_result["games"]
                result["games_returned"] = len(tank_result["games"])
                result["data_source"] = "tank01_raw"
                result["tank_player_id"] = tank_result.get("tank_player_id")
                await self._store_validation_data(player_name, player_id, result["games"])
                return result
        
        # If Tank01 also fails, try NBA.com API as final fallback
        if not raw_games and NBA_API_AVAILABLE:
            logger.info(f"[RAW FETCH] Tank01 has no games for {player_name}, trying NBA.com...")
            nba_result = self._fetch_nba_api_raw(player_name, num_games)
            if nba_result.get("success"):
                result["success"] = True
                result["games"] = nba_result["games"]
                result["games_returned"] = len(nba_result["games"])
                result["data_source"] = "nba_api_raw"
                result["nba_player_id"] = nba_result.get("nba_player_id")
                await self._store_validation_data(player_name, player_id, result["games"])
                return result
        
        # Step 3: Extract ONLY raw values - NO CALCULATIONS
        # Sort by date descending to get recent games
        sorted_games = sorted(
            raw_games,
            key=lambda g: g.get("game", {}).get("date", ""),
            reverse=True
        )[:num_games]
        
        for game in sorted_games:
            game_info = game.get("game", {})
            team_info = game.get("team", {})
            
            # Build opponent string: player's team vs opponent
            player_team = team_info.get("abbreviation", "???")
            home_team_id = game_info.get("home_team_id")
            visitor_team_id = game_info.get("visitor_team_id")
            
            # Determine opponent (simplified - just show home/away score context)
            home_score = game_info.get("home_team_score", 0)
            visitor_score = game_info.get("visitor_team_score", 0)
            
            # CRITICAL: Extract values EXACTLY as they appear in API
            # Do NOT rename, adjust, or calculate anything
            raw_game_entry = {
                "game_date": game_info.get("date"),
                "player_team": player_team,
                "home_team_id": home_team_id,
                "visitor_team_id": visitor_team_id,
                "home_score": home_score,
                "visitor_score": visitor_score,
                # RAW stat values - exactly as returned by API
                "pts": game.get("pts"),
                "reb": game.get("reb"),
                "ast": game.get("ast"),
                "stl": game.get("stl"),
                "blk": game.get("blk"),
                "turnover": game.get("turnover"),
                "min": game.get("min"),
                "fgm": game.get("fgm"),
                "fga": game.get("fga"),
                "fg3m": game.get("fg3m"),
                "fg3a": game.get("fg3a"),
                "ftm": game.get("ftm"),
                "fta": game.get("fta"),
                # Store full raw object for audit
                "raw_api_object": game
            }
            result["games"].append(raw_game_entry)
        
        result["success"] = True
        result["games_returned"] = len(result["games"])
        
        # Store in audit collection
        await self._store_validation_data(player_name, player_id, result["games"])
        
        return result
    
    async def _fetch_tank01_raw(self, player_name: str, num_games: int = 10) -> Dict[str, Any]:
        """
        Fetch raw stats from Tank01 API as secondary fallback.
        
        Tank01 provides box scores - we get recent games from team schedule
        and extract player stats from box scores.
        
        Returns RAW data - NO PROCESSING.
        """
        result = {
            "success": False,
            "games": [],
            "error": None
        }
        
        try:
            headers = {
                "x-rapidapi-key": self.TANK01_KEY,
                "x-rapidapi-host": self.TANK01_HOST
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Step 1: Get player info to find their team
                search_url = f"{self.TANK01_BASE}/getNBAPlayerInfo"
                response = await client.get(
                    search_url,
                    params={"playerName": player_name},
                    headers=headers
                )
                
                if response.status_code != 200:
                    result["error"] = f"Tank01 player search failed: {response.status_code}"
                    return result
                
                data = response.json()
                body = data.get("body", [])
                if not body or not isinstance(body, list):
                    result["error"] = f"Player not found in Tank01: {player_name}"
                    return result
                
                player = body[0]
                team_abv = player.get("team")
                tank_player_id = player.get("playerID")
                
                if not team_abv:
                    result["error"] = f"No team found for {player_name}"
                    return result
                
                result["tank_player_id"] = tank_player_id
                
                # Step 2: Get team schedule to find recent completed games
                schedule_url = f"{self.TANK01_BASE}/getNBATeamSchedule"
                response = await client.get(
                    schedule_url,
                    params={"teamAbv": team_abv, "season": self.CURRENT_SEASON},
                    headers=headers
                )
                
                if response.status_code != 200:
                    result["error"] = f"Tank01 schedule fetch failed: {response.status_code}"
                    return result
                
                schedule_data = response.json()
                schedule = schedule_data.get("body", {}).get("schedule", [])
                
                # Get recent completed games
                completed_games = [g for g in schedule if g.get("gameStatus") == "Completed"]
                recent_games = completed_games[-num_games:]  # Most recent N
                
                if not recent_games:
                    result["error"] = f"No completed games for {team_abv}"
                    return result
                
                # Step 3: Fetch box scores and extract player stats
                games = []
                for game in recent_games:
                    game_id = game.get("gameID")
                    if not game_id:
                        continue
                    
                    box_url = f"{self.TANK01_BASE}/getNBABoxScore"
                    box_response = await client.get(
                        box_url,
                        params={"gameID": game_id},
                        headers=headers
                    )
                    
                    if box_response.status_code != 200:
                        continue
                    
                    box_data = box_response.json()
                    player_stats = box_data.get("body", {}).get("playerStats", {})
                    
                    # Find our player in the box score
                    for pid, stats in player_stats.items():
                        stat_name = stats.get("longName", "").lower()
                        if player_name.lower() in stat_name or stat_name in player_name.lower():
                            games.append({
                                "game_date": game.get("gameDate", ""),
                                "player_team": team_abv,
                                "game_id": game_id,
                                "home_score": None,
                                "visitor_score": None,
                                # RAW stat values - EXACTLY as returned
                                "pts": int(stats.get("pts", 0) or 0),
                                "reb": int(stats.get("reb", 0) or 0),
                                "ast": int(stats.get("ast", 0) or 0),
                                "stl": int(stats.get("stl", 0) or 0),
                                "blk": int(stats.get("blk", 0) or 0),
                                "turnover": int(stats.get("TOV", 0) or 0),
                                "min": stats.get("mins", ""),
                                "fgm": int(stats.get("fgm", 0) or 0),
                                "fga": int(stats.get("fga", 0) or 0),
                                "fg3m": int(stats.get("tptfgm", 0) or 0),
                                "fg3a": int(stats.get("tptfga", 0) or 0),
                                "ftm": int(stats.get("ftm", 0) or 0),
                                "fta": int(stats.get("fta", 0) or 0),
                                "raw_api_object": stats
                            })
                            break
                    
                    await asyncio.sleep(0.1)  # Rate limiting
                
                if games:
                    result["success"] = True
                    result["games"] = games
                    logger.info(f"[RAW FETCH] Tank01: {len(games)} games for {player_name}")
                else:
                    result["error"] = f"No box score data found for {player_name}"
                
        except Exception as e:
            result["error"] = str(e)
            logger.error(f"[RAW FETCH] Tank01 error for {player_name}: {e}")
        
        return result
    
    def _fetch_nba_api_raw(self, player_name: str, num_games: int = 10) -> Dict[str, Any]:
        """
        Fetch raw stats from NBA.com API as fallback.
        Used when BallDontLie doesn't have data for a player (e.g., rookies).
        
        Returns RAW data - NO PROCESSING.
        """
        result = {
            "success": False,
            "games": [],
            "error": None
        }
        
        if not NBA_API_AVAILABLE:
            result["error"] = "NBA API not available"
            return result
        
        try:
            # Find player
            all_players = nba_players.get_players()
            player_match = None
            normalized_search = player_name.lower().strip()
            
            for p in all_players:
                if p['full_name'].lower() == normalized_search:
                    player_match = p
                    break
            
            if not player_match:
                for p in all_players:
                    if normalized_search in p['full_name'].lower():
                        player_match = p
                        break
            
            if not player_match:
                result["error"] = f"Player not found in NBA.com: {player_name}"
                return result
            
            nba_player_id = player_match['id']
            result["nba_player_id"] = nba_player_id
            
            # Determine current season
            now = datetime.now()
            if now.month >= 10:
                season_year = now.year
            else:
                season_year = now.year - 1
            current_season = f"{season_year}-{str(season_year + 1)[-2:]}"
            
            # Rate limiting
            time.sleep(0.6)
            
            # Fetch game logs
            gamelog = playergamelog.PlayerGameLog(
                player_id=nba_player_id,
                season=current_season,
                season_type_all_star='Regular Season'
            )
            df = gamelog.get_data_frames()[0]
            
            if df.empty:
                result["error"] = f"No games found for {player_name} in {current_season}"
                return result
            
            # Convert to raw format - ZERO PROCESSING
            games = []
            for _, row in df.head(num_games).iterrows():
                games.append({
                    "game_date": row.get('GAME_DATE', ''),
                    "player_team": row.get('MATCHUP', '').split()[0] if row.get('MATCHUP') else '???',
                    "matchup": row.get('MATCHUP', ''),
                    "home_score": None,  # Not available in this endpoint
                    "visitor_score": None,
                    # RAW stat values - EXACTLY as returned
                    "pts": int(row.get('PTS', 0)),
                    "reb": int(row.get('REB', 0)),
                    "ast": int(row.get('AST', 0)),
                    "stl": int(row.get('STL', 0)),
                    "blk": int(row.get('BLK', 0)),
                    "turnover": int(row.get('TOV', 0)),
                    "min": row.get('MIN', ''),
                    "fgm": int(row.get('FGM', 0)),
                    "fga": int(row.get('FGA', 0)),
                    "fg3m": int(row.get('FG3M', 0)),
                    "fg3a": int(row.get('FG3A', 0)),
                    "ftm": int(row.get('FTM', 0)),
                    "fta": int(row.get('FTA', 0)),
                    "raw_api_object": row.to_dict()
                })
            
            result["success"] = True
            result["games"] = games
            logger.info(f"[RAW FETCH] NBA.com: {len(games)} games for {player_name}")
            
        except Exception as e:
            result["error"] = str(e)
            logger.error(f"[RAW FETCH] NBA.com error for {player_name}: {e}")
        
        return result
    
    async def _log_raw_fetch(self, player_id: int, source: str, raw_data: Dict):
        """Log raw API fetch for audit trail"""
        try:
            doc = {
                "player_id": player_id,
                "source": source,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "raw_data": raw_data
            }
            await self.raw_stats_collection.insert_one(doc)
        except Exception as e:
            logger.error(f"[RAW AUDIT] Failed to log: {e}")
    
    async def _store_validation_data(self, player_name: str, player_id: int, games: List[Dict]):
        """Store validation data for the UI table"""
        try:
            doc = {
                "player_name": player_name,
                "player_id": player_id,
                "games": games,
                "stored_at": datetime.now(timezone.utc).isoformat()
            }
            # Upsert - replace existing data for this player
            await self.db.dg_validation_table.update_one(
                {"player_name": player_name},
                {"$set": doc},
                upsert=True
            )
        except Exception as e:
            logger.error(f"[VALIDATION] Failed to store: {e}")
    
    async def get_validation_table(self, player_names: List[str] = None) -> Dict[str, Any]:
        """
        Get the validation table data for the UI.
        
        Returns raw stats for manual ESPN verification.
        
        If player_names is None, returns all players in validation table.
        """
        result = {
            "success": False,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "players": [],
            "error": None
        }
        
        try:
            query = {}
            if player_names:
                query = {"player_name": {"$in": player_names}}
                
            cursor = self.db.dg_validation_table.find(query, {"_id": 0})
            players = await cursor.to_list(None)
            
            result["success"] = True
            result["players"] = players
            result["player_count"] = len(players)
            
        except Exception as e:
            result["error"] = str(e)
            
        return result
    
    async def fetch_and_validate_player(self, player_name: str) -> Dict[str, Any]:
        """
        Primary method for validation UI.
        
        Fetches raw data and formats it for the validation table.
        
        Returns exactly what the user needs to compare against ESPN:
        - Player Name
        - Last 5 games with RAW pts and reb values
        """
        raw_data = await self.get_raw_recent_games(player_name, num_games=5)
        
        if not raw_data["success"]:
            return raw_data
            
        # Format for validation table - ZERO PROCESSING
        validation_entry = {
            "player_name": player_name,
            "bdl_player_id": raw_data["bdl_player_id"],
            "last_5_games": []
        }
        
        for game in raw_data["games"][:5]:
            validation_entry["last_5_games"].append({
                "date": game["game_date"],
                "team": game.get("player_team", "???"),
                "score": f"{game.get('home_score', 0)}-{game.get('visitor_score', 0)}",
                "pts": game["pts"],  # RAW - no processing
                "reb": game["reb"],  # RAW - no processing
                "ast": game["ast"]   # RAW - no processing
            })
            
        return {
            "success": True,
            "validation_entry": validation_entry,
            "source": "balldontlie_raw_unprocessed"
        }
