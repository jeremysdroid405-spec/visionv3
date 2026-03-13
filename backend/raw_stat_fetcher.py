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
"""

import os
import httpx
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

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
    - Fetches raw JSON from BallDontLie/Tank01
    - Returns the exact response unchanged
    - Logs raw data for audit
    """
    
    # Default BDL API key (same as demon_goblin_engine)
    DEFAULT_BDL_KEY = "ad5544be-9969-434b-9389-2b7cf658c8e0"
    
    def __init__(self, db):
        self.db = db
        self.raw_stats_collection = db.dg_raw_stats_audit
        self.BDL_BASE_URL = "https://api.balldontlie.io/v1"
        self.BDL_API_KEY = os.environ.get("BDL_API_KEY", self.DEFAULT_BDL_KEY)
        
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
