"""
Tank01 Stats Service
====================
Primary data engine for NBA Master Hub using Tank01 Fantasy Stats API (RapidAPI).

Replaces legacy BallDontLie integration. Provides:
- Real game logs for accurate L5, L10, Season averages
- Player info with team, position, injury status
- Game-by-game stats calculation (only games with minutes > 0)

Endpoints used:
- getNBAPlayerInfo: Player metadata and season averages
- getNBAGamesForPlayer: Game logs for L5/L10/Season calculation
- getNBATeams: Team rosters with player IDs
"""
import os
import logging
import asyncio
import httpx
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# Tank01 RapidAPI Configuration
TANK01_API_KEY = os.environ.get("TANK01_API_KEY", "402edbcac6mshd04997e7ca01d17p1879eajsn65ab176cdb1e")
TANK01_HOST = "tank01-fantasy-stats.p.rapidapi.com"
TANK01_BASE_URL = f"https://{TANK01_HOST}"

# Current NBA season
CURRENT_SEASON = "2025"


class Tank01StatsService:
    """
    Primary stats service using Tank01 Fantasy Stats API.
    Calculates real L5, L10, and season averages from game logs.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.master_hub = db.nba_master_hub_2026
        self.headers = {
            "X-RapidAPI-Key": TANK01_API_KEY,
            "X-RapidAPI-Host": TANK01_HOST
        }
    
    async def sync_all_player_stats(self, limit: Optional[int] = None) -> Dict[str, Any]:
        """
        Sync baseline_stats for all players in master hub using Tank01 game logs.
        
        Args:
            limit: Optional limit on number of players to sync
            
        Returns:
            Summary of sync operation
        """
        start_time = datetime.now(timezone.utc)
        logger.info("[TANK01] Starting full stats sync")
        
        # Get all players with tank01_id or playerID
        query = {
            "$or": [
                {"tank01_id": {"$exists": True, "$ne": None}},
                {"playerID": {"$exists": True, "$ne": None}}
            ]
        }
        
        players = await self.master_hub.find(
            query,
            {"_id": 0, "display_name": 1, "tank01_id": 1, "playerID": 1}
        ).to_list(limit or 700)
        
        logger.info(f"[TANK01] Found {len(players)} players with Tank01 IDs")
        
        updated = 0
        no_games = 0
        errors = 0
        
        async with httpx.AsyncClient(timeout=30) as http:
            for i, player in enumerate(players):
                player_name = player.get("display_name", "")
                tank01_id = player.get("tank01_id") or player.get("playerID")
                
                if not tank01_id:
                    continue
                
                try:
                    # Fetch game logs and calculate stats
                    baseline_stats = await self._fetch_and_calculate_stats(http, tank01_id)
                    
                    if baseline_stats:
                        # Fetch and store game logs for coupled stat calculations
                        game_logs = await self._fetch_game_logs(http, tank01_id)
                        
                        update_data = {
                            "baseline_stats": baseline_stats,
                            "baseline_stats_updated_at": datetime.now(timezone.utc).isoformat(),
                            "stats_source": "tank01"
                        }
                        
                        # Store game logs for on-the-fly coupled stat calculations
                        if game_logs:
                            update_data["game_logs"] = game_logs
                            update_data["game_logs_updated_at"] = datetime.now(timezone.utc).isoformat()
                        
                        await self.master_hub.update_one(
                            {"display_name": player_name},
                            {"$set": update_data}
                        )
                        updated += 1
                    else:
                        no_games += 1
                    
                    # Progress logging
                    if (i + 1) % 50 == 0:
                        logger.info(f"[TANK01] Progress: {i+1}/{len(players)} - Updated: {updated}")
                    
                    # Rate limiting (Tank01 allows ~5 req/sec)
                    await asyncio.sleep(0.2)
                    
                except Exception as e:
                    errors += 1
                    if errors < 10:
                        logger.error(f"[TANK01] Error for {player_name}: {e}")
        
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        
        result = {
            "success": True,
            "players_processed": len(players),
            "updated": updated,
            "no_games": no_games,
            "errors": errors,
            "duration_seconds": round(duration, 1),
            "source": "tank01",
            "completed_at": datetime.now(timezone.utc).isoformat()
        }
        
        logger.info(f"[TANK01] Sync complete: {updated} updated, {no_games} no games, {errors} errors")
        return result
    
    async def _fetch_and_calculate_stats(
        self,
        http: httpx.AsyncClient,
        player_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch game logs from Tank01 and calculate L5, L10, Season averages.
        
        Only counts games where minutes > 0 (actually played).
        """
        try:
            response = await http.get(
                f"{TANK01_BASE_URL}/getNBAGamesForPlayer",
                params={"playerID": str(player_id), "season": CURRENT_SEASON},
                headers=self.headers
            )
            
            if response.status_code != 200:
                logger.warning(f"[TANK01] API error for {player_id}: {response.status_code}")
                return None
            
            data = response.json()
            body = data.get("body", {})
            
            if not body:
                return None
            
            # Body is dict with gameID as keys
            games = list(body.values())
            
            if not games:
                return None
            
            # Filter to games with minutes > 0 (actually played)
            played_games = []
            for g in games:
                mins_str = g.get("mins", "0") or "0"
                try:
                    mins = float(mins_str) if mins_str else 0
                    if mins > 0:
                        played_games.append(g)
                except (ValueError, TypeError):
                    pass
            
            if not played_games:
                return None
            
            # Sort by gameID (format: YYYYMMDD_TEAM@TEAM) - newest first
            played_games.sort(key=lambda g: g.get("gameID", ""), reverse=True)
            
            # Calculate L5, L10, Season averages
            baseline_stats = self._calculate_averages(played_games)
            baseline_stats["games_played"] = len(played_games)
            baseline_stats["synced_from"] = "tank01_game_logs"
            baseline_stats["synced_at"] = datetime.now(timezone.utc).isoformat()
            
            return baseline_stats
            
        except Exception as e:
            logger.error(f"[TANK01] Error fetching stats for {player_id}: {e}")
            return None
    
    async def _fetch_game_logs(
        self,
        http: httpx.AsyncClient,
        player_id: str
    ) -> Optional[List[Dict]]:
        """
        Fetch raw game logs from Tank01 for storage.
        
        These logs are stored in the master hub and used for on-the-fly
        coupled stat calculations (hit rate + average from same games).
        
        Only includes games where minutes > 0 (actually played).
        """
        try:
            response = await http.get(
                f"{TANK01_BASE_URL}/getNBAGamesForPlayer",
                params={"playerID": str(player_id), "season": CURRENT_SEASON},
                headers=self.headers
            )
            
            if response.status_code != 200:
                return None
            
            data = response.json()
            body = data.get("body", {})
            
            if not body:
                return None
            
            # Body is dict with gameID as keys - convert to list
            games = []
            for game_id, game_data in body.items():
                game_data["gameID"] = game_id  # Ensure gameID is stored
                games.append(game_data)
            
            if not games:
                return None
            
            # Filter to games with minutes > 0
            played_games = []
            for g in games:
                mins_str = g.get("mins", "0") or "0"
                try:
                    mins = float(mins_str) if mins_str else 0
                    if mins > 0:
                        played_games.append(g)
                except (ValueError, TypeError):
                    pass
            
            # Sort by gameID (newest first)
            played_games.sort(key=lambda g: g.get("gameID", ""), reverse=True)
            
            # Return only essential fields to minimize storage
            return [
                {
                    "gameID": g.get("gameID"),
                    "pts": g.get("pts"),
                    "reb": g.get("reb"),
                    "ast": g.get("ast"),
                    "tptfgm": g.get("tptfgm"),  # 3PM
                    "stl": g.get("stl"),
                    "blk": g.get("blk"),
                    "TOV": g.get("TOV"),  # Turnovers
                    "mins": g.get("mins")
                }
                for g in played_games
            ]
            
        except Exception as e:
            logger.error(f"[TANK01] Error fetching game logs for {player_id}: {e}")
            return None
    
    def _calculate_averages(self, games: List[Dict]) -> Dict[str, Any]:
        """
        Calculate L5, L10, and Season averages from game logs.
        
        Stat mapping from Tank01:
        - pts: Points
        - reb: Total Rebounds
        - ast: Assists
        - tptfgm: 3-Point Field Goals Made
        - stl: Steals
        - blk: Blocks
        - TOV: Turnovers
        """
        def safe_float(val):
            try:
                return float(val) if val else 0
            except (ValueError, TypeError):
                return 0
        
        def calc_avg(game_list: List[Dict], stat_key: str) -> float:
            if not game_list:
                return 0
            values = [safe_float(g.get(stat_key, 0)) for g in game_list]
            return round(sum(values) / len(values), 1)
        
        def calc_std_dev(game_list: List[Dict], stat_key: str) -> float:
            if len(game_list) < 2:
                return 0
            values = [safe_float(g.get(stat_key, 0)) for g in game_list]
            avg = sum(values) / len(values)
            variance = sum((v - avg) ** 2 for v in values) / len(values)
            return round(variance ** 0.5, 2)
        
        l5_games = games[:5]
        l10_games = games[:10]
        all_games = games
        
        # Core stats
        stats = {
            "PTS": {
                "l5_avg": calc_avg(l5_games, "pts"),
                "l10_avg": calc_avg(l10_games, "pts"),
                "season_avg": calc_avg(all_games, "pts"),
                "std_dev_l10": calc_std_dev(l10_games, "pts")
            },
            "REB": {
                "l5_avg": calc_avg(l5_games, "reb"),
                "l10_avg": calc_avg(l10_games, "reb"),
                "season_avg": calc_avg(all_games, "reb"),
                "std_dev_l10": calc_std_dev(l10_games, "reb")
            },
            "AST": {
                "l5_avg": calc_avg(l5_games, "ast"),
                "l10_avg": calc_avg(l10_games, "ast"),
                "season_avg": calc_avg(all_games, "ast"),
                "std_dev_l10": calc_std_dev(l10_games, "ast")
            },
            "3PM": {
                "l5_avg": calc_avg(l5_games, "tptfgm"),
                "l10_avg": calc_avg(l10_games, "tptfgm"),
                "season_avg": calc_avg(all_games, "tptfgm"),
                "std_dev_l10": calc_std_dev(l10_games, "tptfgm")
            },
            "STL": {
                "l5_avg": calc_avg(l5_games, "stl"),
                "l10_avg": calc_avg(l10_games, "stl"),
                "season_avg": calc_avg(all_games, "stl"),
                "std_dev_l10": calc_std_dev(l10_games, "stl")
            },
            "BLK": {
                "l5_avg": calc_avg(l5_games, "blk"),
                "l10_avg": calc_avg(l10_games, "blk"),
                "season_avg": calc_avg(all_games, "blk"),
                "std_dev_l10": calc_std_dev(l10_games, "blk")
            },
            "TO": {
                "l5_avg": calc_avg(l5_games, "TOV"),
                "l10_avg": calc_avg(l10_games, "TOV"),
                "season_avg": calc_avg(all_games, "TOV"),
                "std_dev_l10": calc_std_dev(l10_games, "TOV")
            }
        }
        
        # Combined stats (PRA, PR, PA, RA)
        for period, game_list in [("l5", l5_games), ("l10", l10_games), ("season", all_games)]:
            pts_avg = stats["PTS"][f"{period}_avg"]
            reb_avg = stats["REB"][f"{period}_avg"]
            ast_avg = stats["AST"][f"{period}_avg"]
            
            if "PRA" not in stats:
                stats["PRA"] = {}
            if "PR" not in stats:
                stats["PR"] = {}
            if "PA" not in stats:
                stats["PA"] = {}
            if "RA" not in stats:
                stats["RA"] = {}
            
            stats["PRA"][f"{period}_avg"] = round(pts_avg + reb_avg + ast_avg, 1)
            stats["PR"][f"{period}_avg"] = round(pts_avg + reb_avg, 1)
            stats["PA"][f"{period}_avg"] = round(pts_avg + ast_avg, 1)
            stats["RA"][f"{period}_avg"] = round(reb_avg + ast_avg, 1)
        
        return stats
    
    async def sync_single_player(self, player_name: str) -> Optional[Dict[str, Any]]:
        """
        Sync stats for a single player by name.
        """
        player = await self.master_hub.find_one(
            {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0, "display_name": 1, "tank01_id": 1, "playerID": 1}
        )
        
        if not player:
            logger.warning(f"[TANK01] Player not found: {player_name}")
            return None
        
        tank01_id = player.get("tank01_id") or player.get("playerID")
        if not tank01_id:
            logger.warning(f"[TANK01] No Tank01 ID for: {player_name}")
            return None
        
        async with httpx.AsyncClient(timeout=30) as http:
            baseline_stats = await self._fetch_and_calculate_stats(http, tank01_id)
            
            if baseline_stats:
                await self.master_hub.update_one(
                    {"display_name": player.get("display_name")},
                    {"$set": {
                        "baseline_stats": baseline_stats,
                        "baseline_stats_updated_at": datetime.now(timezone.utc).isoformat(),
                        "stats_source": "tank01"
                    }}
                )
                return baseline_stats
        
        return None
    
    async def fetch_player_by_name(self, player_name: str) -> Optional[Dict[str, Any]]:
        """
        Search for a player by name using Tank01 API.
        Returns player info including Tank01 playerID.
        """
        async with httpx.AsyncClient(timeout=30) as http:
            response = await http.get(
                f"{TANK01_BASE_URL}/getNBAPlayerInfo",
                params={"playerName": player_name, "statsToGet": "averages"},
                headers=self.headers
            )
            
            if response.status_code != 200:
                return None
            
            data = response.json()
            body = data.get("body", [])
            
            if isinstance(body, list) and body:
                return body[0]
            
            return None
    
    async def populate_tank01_ids(self) -> Dict[str, Any]:
        """
        Populate tank01_id for players who don't have it by searching Tank01 API.
        """
        # Get players without tank01_id
        players = await self.master_hub.find(
            {"tank01_id": {"$exists": False}},
            {"_id": 0, "display_name": 1}
        ).to_list(700)
        
        logger.info(f"[TANK01] Populating IDs for {len(players)} players")
        
        found = 0
        not_found = 0
        
        async with httpx.AsyncClient(timeout=30) as http:
            for i, player in enumerate(players):
                player_name = player.get("display_name", "")
                if not player_name:
                    continue
                
                try:
                    response = await http.get(
                        f"{TANK01_BASE_URL}/getNBAPlayerInfo",
                        params={"playerName": player_name, "statsToGet": "averages"},
                        headers=self.headers
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        body = data.get("body", [])
                        
                        if isinstance(body, list) and body:
                            tank01_player = body[0]
                            tank01_id = tank01_player.get("playerID")
                            
                            if tank01_id:
                                await self.master_hub.update_one(
                                    {"display_name": player_name},
                                    {"$set": {"tank01_id": tank01_id}}
                                )
                                found += 1
                        else:
                            not_found += 1
                    
                    if (i + 1) % 50 == 0:
                        logger.info(f"[TANK01] ID Population: {i+1}/{len(players)} - Found: {found}")
                    
                    await asyncio.sleep(0.2)
                    
                except Exception as e:
                    logger.error(f"[TANK01] Error finding ID for {player_name}: {e}")
        
        return {"found": found, "not_found": not_found, "total": len(players)}


# Singleton instance
_tank01_service: Optional[Tank01StatsService] = None


def get_tank01_service(db: AsyncIOMotorDatabase) -> Tank01StatsService:
    """Get or create Tank01 stats service instance."""
    global _tank01_service
    if _tank01_service is None:
        _tank01_service = Tank01StatsService(db)
    return _tank01_service


async def run_tank01_sync(db: AsyncIOMotorDatabase) -> Dict[str, Any]:
    """Entry point for CRON job to sync all player stats."""
    service = get_tank01_service(db)
    return await service.sync_all_player_stats()
