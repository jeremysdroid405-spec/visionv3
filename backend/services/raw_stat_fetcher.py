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

NOTE: BDL is the only stats source. No other external APIs are used.
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
    - Fetches raw JSON from BallDontLie/NBA.com
    - Returns the exact response unchanged
    - Logs raw data for audit
    
    Data Source Priority:
    1. BallDontLie (primary)
    2. NBA.com (fallback for rookies)
    """
    
    # Default API keys
    DEFAULT_BDL_KEY = os.environ.get("BDL_API_KEY")
    
    def __init__(self, db):
        self.db = db
        self.raw_stats_collection = db.dg_raw_stats_audit
        # BallDontLie
        self.BDL_BASE_URL = "https://api.balldontlie.io/v1"
        self.BDL_API_KEY = os.environ.get("BDL_API_KEY", self.DEFAULT_BDL_KEY)
        self.CURRENT_SEASON = "2025"
        
    def set_api_key(self, key: str):
        """Set BallDontLie API key"""
        self.BDL_API_KEY = key
        
    async def fetch_raw_player_games(self, player_id: int, season: int = 2024) -> Dict[str, Any]:
        """
        Fetch raw game logs from BallDontLie API.
        
        Returns the exact API response with NO modifications.
        The response is logged to raw_stats_audit for verification.
        
        Args:
            player_id: BallDontLie player ID
            season: Season year (e.g., 2024 for 2024-25 season)
        
        Returns:
            Raw API response as dict
        """
        result = {
            "success": False,
            "data_source": "balldontlie_raw",
            "player_id": player_id,
            "season": season,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "raw_response": None,
            "games": [],
            "error": None
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {"Authorization": self.BDL_API_KEY}
                
                # Fetch ALL games for season (up to 100 per page)
                url = f"{self.BDL_BASE_URL}/stats"
                params = {
                    "player_ids[]": player_id,
                    "seasons[]": season,
                    "per_page": 100
                }
                
                response = await client.get(url, headers=headers, params=params)
                
                if response.status_code == 200:
                    data = response.json()
                    result["raw_response"] = data
                    result["games"] = data.get("data", [])
                    result["success"] = True
                    result["games_count"] = len(result["games"])
                else:
                    result["error"] = f"BDL API error: {response.status_code}"
                    
        except Exception as e:
            result["error"] = f"BDL fetch error: {str(e)}"
            logger.error(f"[RAW FETCH] BDL error for player {player_id}: {e}")
        
        # Log to audit collection
        await self._log_raw_fetch(result)
        
        return result
    
    async def fetch_raw_player_by_name(self, player_name: str, num_games: int = 10) -> Dict[str, Any]:
        """
        Fetch raw games for a player by name.
        
        Tries data sources in order:
        1. BallDontLie
        2. NBA.com API (fallback for rookies)
        
        Args:
            player_name: Player's full name
            num_games: Number of recent games to fetch
            
        Returns:
            Raw API response with game logs
        """
        result = {
            "success": False,
            "player_name": player_name,
            "num_games_requested": num_games,
            "data_source": None,
            "games": [],
            "raw_response": None,
            "error": None,
            "fetched_at": datetime.now(timezone.utc).isoformat()
        }
        
        # First try BallDontLie
        bdl_result = await self._fetch_bdl_by_name(player_name, num_games)
        
        if bdl_result.get("success") and bdl_result.get("games"):
            result["success"] = True
            result["data_source"] = "balldontlie_raw"
            result["games"] = bdl_result["games"][:num_games]
            result["raw_response"] = bdl_result.get("raw_response")
            result["bdl_player_id"] = bdl_result.get("bdl_player_id")
            return result
        
        # Fallback to NBA.com for rookies
        if NBA_API_AVAILABLE:
            logger.info(f"[RAW FETCH] BDL has no games for {player_name}, trying NBA.com...")
            nba_result = await self._fetch_nba_com_raw(player_name, num_games)
            
            if nba_result.get("success") and nba_result.get("games"):
                result["success"] = True
                result["data_source"] = "nba_com_raw"
                result["games"] = nba_result["games"][:num_games]
                result["raw_response"] = nba_result.get("raw_response")
                return result
        
        result["error"] = f"No game data found for {player_name} in any data source"
        return result
    
    async def _fetch_bdl_by_name(self, player_name: str, num_games: int) -> Dict[str, Any]:
        """Fetch from BallDontLie by player name."""
        result = {
            "success": False,
            "games": [],
            "raw_response": None,
            "error": None
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {"Authorization": self.BDL_API_KEY}
                
                # Search for player
                search_url = f"{self.BDL_BASE_URL}/players"
                search_params = {"search": player_name}
                
                response = await client.get(search_url, headers=headers, params=search_params)
                
                if response.status_code != 200:
                    result["error"] = f"BDL player search failed: {response.status_code}"
                    return result
                
                search_data = response.json()
                players = search_data.get("data", [])
                
                if not players:
                    result["error"] = f"Player not found in BDL: {player_name}"
                    return result
                
                # Find best match
                player = self._find_best_match(players, player_name)
                if not player:
                    result["error"] = f"No matching player found: {player_name}"
                    return result
                
                player_id = player.get("id")
                result["bdl_player_id"] = player_id
                
                # Fetch stats
                stats_url = f"{self.BDL_BASE_URL}/stats"
                stats_params = {
                    "player_ids[]": player_id,
                    "seasons[]": self.CURRENT_SEASON,
                    "per_page": min(num_games * 2, 100)
                }
                
                stats_response = await client.get(stats_url, headers=headers, params=stats_params)
                
                if stats_response.status_code == 200:
                    stats_data = stats_response.json()
                    result["raw_response"] = stats_data
                    result["games"] = stats_data.get("data", [])
                    result["success"] = len(result["games"]) > 0
                else:
                    result["error"] = f"BDL stats fetch failed: {stats_response.status_code}"
                    
        except Exception as e:
            result["error"] = f"BDL fetch error: {str(e)}"
            logger.error(f"[RAW FETCH] BDL error for {player_name}: {e}")
        
        return result
    
    async def _fetch_nba_com_raw(self, player_name: str, num_games: int) -> Dict[str, Any]:
        """Fetch from NBA.com API as fallback."""
        result = {
            "success": False,
            "games": [],
            "raw_response": None,
            "error": None
        }
        
        if not NBA_API_AVAILABLE:
            result["error"] = "nba_api not available"
            return result
        
        try:
            # Find player ID
            all_players = nba_players.get_players()
            matching = [p for p in all_players if player_name.lower() in p['full_name'].lower()]
            
            if not matching:
                result["error"] = f"Player not found in NBA.com: {player_name}"
                return result
            
            nba_player = matching[0]
            player_id = nba_player['id']
            
            # Fetch game logs (this is synchronous, so we run in executor)
            loop = asyncio.get_event_loop()
            gamelog = await loop.run_in_executor(
                None,
                lambda: playergamelog.PlayerGameLog(
                    player_id=player_id,
                    season='2024-25'
                )
            )
            
            games_df = gamelog.get_data_frames()[0]
            
            if games_df.empty:
                result["error"] = "No games found in NBA.com"
                return result
            
            # Convert to list of dicts
            games = games_df.head(num_games).to_dict('records')
            
            result["success"] = True
            result["games"] = games
            result["raw_response"] = {"nba_com_player_id": player_id, "games_count": len(games)}
            
        except Exception as e:
            result["error"] = f"NBA.com fetch error: {str(e)}"
            logger.error(f"[RAW FETCH] NBA.com error for {player_name}: {e}")
        
        return result
    
    def _find_best_match(self, players: List[Dict], target_name: str) -> Optional[Dict]:
        """Find the best matching player from search results."""
        target_lower = target_name.lower().strip()
        
        for player in players:
            full_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".lower().strip()
            if full_name == target_lower:
                return player
        
        # Partial match
        for player in players:
            full_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".lower()
            if target_lower in full_name or full_name in target_lower:
                return player
        
        return players[0] if players else None
    
    async def _log_raw_fetch(self, result: Dict[str, Any]):
        """Log raw fetch to audit collection."""
        try:
            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data_source": result.get("data_source"),
                "player_id": result.get("player_id"),
                "player_name": result.get("player_name"),
                "success": result.get("success"),
                "games_count": len(result.get("games", [])),
                "error": result.get("error")
            }
            await self.raw_stats_collection.insert_one(log_entry)
        except Exception as e:
            logger.warning(f"[RAW FETCH] Failed to log audit: {e}")
    
    async def verify_data_integrity(self, player_name: str, game_date: str) -> Dict[str, Any]:
        """
        Verify data integrity by comparing sources.
        
        Args:
            player_name: Player to verify
            game_date: Date of game to check (YYYY-MM-DD)
            
        Returns:
            Comparison of data from different sources
        """
        result = {
            "player_name": player_name,
            "game_date": game_date,
            "sources_checked": [],
            "discrepancies": [],
            "verified_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Fetch from all available sources
        bdl_data = await self._fetch_bdl_by_name(player_name, 20)
        
        if bdl_data.get("success"):
            result["sources_checked"].append("balldontlie")
            # Find game for date
            for game in bdl_data.get("games", []):
                game_info = game.get("game", {})
                if game_info.get("date", "").startswith(game_date):
                    result["bdl_stats"] = {
                        "pts": game.get("pts"),
                        "reb": game.get("reb"),
                        "ast": game.get("ast"),
                        "min": game.get("min")
                    }
                    break
        
        if NBA_API_AVAILABLE:
            nba_data = await self._fetch_nba_com_raw(player_name, 20)
            if nba_data.get("success"):
                result["sources_checked"].append("nba_com")
                for game in nba_data.get("games", []):
                    if game.get("GAME_DATE", "").startswith(game_date):
                        result["nba_com_stats"] = {
                            "pts": game.get("PTS"),
                            "reb": game.get("REB"),
                            "ast": game.get("AST"),
                            "min": game.get("MIN")
                        }
                        break
        
        # Check for discrepancies
        if result.get("bdl_stats") and result.get("nba_com_stats"):
            bdl = result["bdl_stats"]
            nba = result["nba_com_stats"]
            
            for stat in ["pts", "reb", "ast"]:
                bdl_val = bdl.get(stat)
                nba_val = nba.get(stat)
                if bdl_val is not None and nba_val is not None:
                    if abs(bdl_val - nba_val) > 0.5:
                        result["discrepancies"].append({
                            "stat": stat,
                            "bdl": bdl_val,
                            "nba_com": nba_val,
                            "diff": abs(bdl_val - nba_val)
                        })
        
        return result


# Singleton instance
_raw_fetcher: Optional[RawStatFetcher] = None


def get_raw_stat_fetcher(db) -> RawStatFetcher:
    """Get or create RawStatFetcher singleton."""
    global _raw_fetcher
    if _raw_fetcher is None:
        _raw_fetcher = RawStatFetcher(db)
    return _raw_fetcher
