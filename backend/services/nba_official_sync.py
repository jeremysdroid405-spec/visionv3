"""
NBA Official Sync Service
=========================
Primary data engine for NBA Master Hub using official NBA API.

REPLACES: tank01_stats_service.py (deprecated due to data quality issues)

This service fetches game logs directly from the official NBA stats API,
ensuring accurate and reliable data for hit rate calculations.

Key Features:
- Uses nba_api package (official NBA stats endpoint)
- Fetches PlayerGameLog for current season
- Properly sorted by GAME_DATE descending
- Rate-limited to respect NBA API (0.6s between requests)
- Maps official NBA columns to master hub schema
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

# NBA Official API
from nba_api.stats.static import players as nba_players
from nba_api.stats.endpoints import playergamelog, commonplayerinfo

logger = logging.getLogger(__name__)

# Current NBA Season
CURRENT_SEASON = "2024-25"  # Format for nba_api

# Rate limiting (NBA API requires ~0.6s between requests)
REQUEST_DELAY = 0.6


class NBAOfficialSyncService:
    """
    Official NBA API sync service for master hub game logs.
    
    This replaces Tank01 as the primary data source for player statistics.
    Uses the official NBA stats API via the nba_api package.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.hub = db.nba_master_hub_2026
        self._last_request_time = 0
    
    def _rate_limit(self):
        """Enforce rate limiting between NBA API requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        self._last_request_time = time.time()
    
    def _get_nba_player_id(self, player_name: str) -> Optional[int]:
        """
        Find NBA player ID by name using nba_api static data.
        
        Returns the official NBA player ID or None if not found.
        """
        try:
            # Search for player by name
            matches = nba_players.find_players_by_full_name(player_name)
            if matches:
                # Return first active player match
                for match in matches:
                    if match.get('is_active', False):
                        return match['id']
                # If no active, return first match
                return matches[0]['id']
            
            # Try partial match
            name_parts = player_name.lower().split()
            if len(name_parts) >= 2:
                last_name = name_parts[-1]
                matches = nba_players.find_players_by_last_name(last_name)
                for match in matches:
                    if name_parts[0] in match.get('full_name', '').lower():
                        return match['id']
            
            return None
        except Exception as e:
            logger.error(f"[NBA_API] Error finding player ID for {player_name}: {e}")
            return None
    
    def _fetch_game_logs(self, nba_player_id: int, season: str = CURRENT_SEASON) -> List[Dict]:
        """
        Fetch game logs from official NBA API.
        
        Returns list of game log dictionaries sorted by date descending.
        """
        try:
            self._rate_limit()
            
            # Fetch player game log
            game_log = playergamelog.PlayerGameLog(
                player_id=nba_player_id,
                season=season,
                season_type_all_star="Regular Season"
            )
            
            df = game_log.get_data_frames()[0]
            
            if df.empty:
                return []
            
            # Sort by GAME_DATE descending (most recent first)
            df = df.sort_values('GAME_DATE', ascending=False)
            
            # Map NBA columns to our schema
            games = []
            for _, row in df.iterrows():
                game = {
                    "gameID": str(row.get('Game_ID', '')),
                    "game_date": row.get('GAME_DATE', ''),
                    "matchup": row.get('MATCHUP', ''),
                    "wl": row.get('WL', ''),
                    # Core stats
                    "pts": int(row.get('PTS', 0)),
                    "reb": int(row.get('REB', 0)),
                    "ast": int(row.get('AST', 0)),
                    "stl": int(row.get('STL', 0)),
                    "blk": int(row.get('BLK', 0)),
                    "tov": int(row.get('TOV', 0)),
                    "tptfgm": int(row.get('FG3M', 0)),  # 3-pointers made
                    # Minutes
                    "mins": int(row.get('MIN', 0)),
                    # Shooting
                    "fgm": int(row.get('FGM', 0)),
                    "fga": int(row.get('FGA', 0)),
                    "fg_pct": float(row.get('FG_PCT', 0)),
                    "fg3a": int(row.get('FG3A', 0)),
                    "fg3_pct": float(row.get('FG3_PCT', 0)),
                    "ftm": int(row.get('FTM', 0)),
                    "fta": int(row.get('FTA', 0)),
                    "ft_pct": float(row.get('FT_PCT', 0)),
                    # Advanced
                    "plus_minus": int(row.get('PLUS_MINUS', 0)),
                    # Source marker
                    "source": "nba_official"
                }
                games.append(game)
            
            return games
            
        except Exception as e:
            logger.error(f"[NBA_API] Error fetching game logs for player {nba_player_id}: {e}")
            return []
    
    async def sync_all_players(self, batch_size: int = 50) -> Dict[str, Any]:
        """
        Sync game logs for all players in master hub using official NBA API.
        
        This is the main entry point for the 0400 EST CRON job.
        
        Args:
            batch_size: Number of players to process per batch
            
        Returns:
            Sync results summary
        """
        started_at = datetime.now(timezone.utc)
        logger.info(f"[NBA_OFFICIAL_SYNC] Starting full sync at {started_at.isoformat()}")
        
        results = {
            "started_at": started_at.isoformat(),
            "players_processed": 0,
            "players_updated": 0,
            "players_skipped": 0,
            "players_failed": 0,
            "errors": []
        }
        
        # Get all players from master hub
        cursor = self.hub.find({}, {"display_name": 1, "nba_player_id": 1, "team": 1})
        players = await cursor.to_list(length=2000)
        
        total_players = len(players)
        logger.info(f"[NBA_OFFICIAL_SYNC] Found {total_players} players to sync")
        
        for i, player in enumerate(players):
            player_name = player.get("display_name", "Unknown")
            nba_id = player.get("nba_player_id")
            
            try:
                # Find NBA player ID if not cached
                if not nba_id:
                    nba_id = self._get_nba_player_id(player_name)
                    if nba_id:
                        # Cache the NBA ID
                        await self.hub.update_one(
                            {"_id": player["_id"]},
                            {"$set": {"nba_player_id": nba_id}}
                        )
                
                if not nba_id:
                    logger.warning(f"[NBA_OFFICIAL_SYNC] No NBA ID found for: {player_name}")
                    results["players_skipped"] += 1
                    continue
                
                # Fetch game logs from official NBA API
                game_logs = self._fetch_game_logs(nba_id)
                
                if not game_logs:
                    logger.debug(f"[NBA_OFFICIAL_SYNC] No game logs for: {player_name}")
                    results["players_skipped"] += 1
                    continue
                
                # Calculate baseline stats from game logs
                baseline_stats = self._calculate_baseline_stats(game_logs)
                
                # Update master hub
                await self.hub.update_one(
                    {"_id": player["_id"]},
                    {
                        "$set": {
                            "game_logs": game_logs,
                            "game_logs_source": "nba_official",
                            "game_logs_updated_at": datetime.now(timezone.utc).isoformat(),
                            "baseline_stats": baseline_stats,
                            "baseline_stats_source": "nba_official"
                        }
                    }
                )
                
                results["players_updated"] += 1
                results["players_processed"] += 1
                
                # Progress logging
                if (i + 1) % 25 == 0:
                    logger.info(
                        f"[NBA_OFFICIAL_SYNC] Progress: {i + 1}/{total_players} "
                        f"({results['players_updated']} updated)"
                    )
                
            except Exception as e:
                logger.error(f"[NBA_OFFICIAL_SYNC] Error processing {player_name}: {e}")
                results["players_failed"] += 1
                results["errors"].append({"player": player_name, "error": str(e)})
        
        # Finalize
        results["completed_at"] = datetime.now(timezone.utc).isoformat()
        results["duration_seconds"] = (
            datetime.now(timezone.utc) - started_at
        ).total_seconds()
        
        logger.info(
            f"[NBA_OFFICIAL_SYNC] Completed: "
            f"{results['players_updated']} updated, "
            f"{results['players_skipped']} skipped, "
            f"{results['players_failed']} failed "
            f"in {results['duration_seconds']:.1f}s"
        )
        
        return results
    
    async def sync_single_player(self, player_name: str) -> Dict[str, Any]:
        """
        Sync game logs for a single player.
        
        Useful for on-demand updates or testing.
        """
        logger.info(f"[NBA_OFFICIAL_SYNC] Syncing single player: {player_name}")
        
        # Find player in master hub
        player = await self.hub.find_one({
            "display_name": {"$regex": player_name, "$options": "i"}
        })
        
        if not player:
            return {"success": False, "error": f"Player not found: {player_name}"}
        
        nba_id = player.get("nba_player_id")
        
        # Find NBA ID if not cached
        if not nba_id:
            nba_id = self._get_nba_player_id(player.get("display_name", player_name))
            if not nba_id:
                return {"success": False, "error": "Could not find NBA player ID"}
        
        # Fetch game logs
        game_logs = self._fetch_game_logs(nba_id)
        
        if not game_logs:
            return {"success": False, "error": "No game logs returned from NBA API"}
        
        # Calculate baseline stats
        baseline_stats = self._calculate_baseline_stats(game_logs)
        
        # Update master hub
        await self.hub.update_one(
            {"_id": player["_id"]},
            {
                "$set": {
                    "nba_player_id": nba_id,
                    "game_logs": game_logs,
                    "game_logs_source": "nba_official",
                    "game_logs_updated_at": datetime.now(timezone.utc).isoformat(),
                    "baseline_stats": baseline_stats,
                    "baseline_stats_source": "nba_official"
                }
            }
        )
        
        return {
            "success": True,
            "player_name": player.get("display_name"),
            "nba_player_id": nba_id,
            "games_synced": len(game_logs),
            "last_5_pts": [g.get("pts") for g in game_logs[:5]],
            "l5_avg": baseline_stats.get("PTS", {}).get("l5_avg")
        }
    
    def _calculate_baseline_stats(self, game_logs: List[Dict]) -> Dict[str, Any]:
        """
        Calculate L5, L10, Season averages from game logs.
        
        Only includes games where player actually played (mins > 0).
        """
        # Filter to played games
        played = [g for g in game_logs if g.get("mins", 0) > 0]
        
        if not played:
            return {}
        
        # Slices for different periods
        l5 = played[:5]
        l10 = played[:10]
        season = played
        
        def calc_avg(games: List[Dict], stat: str) -> float:
            values = [g.get(stat, 0) for g in games]
            return round(sum(values) / len(values), 1) if values else 0.0
        
        def calc_std_dev(games: List[Dict], stat: str) -> float:
            values = [g.get(stat, 0) for g in games]
            if len(values) < 2:
                return 0.0
            avg = sum(values) / len(values)
            variance = sum((v - avg) ** 2 for v in values) / len(values)
            return round(variance ** 0.5, 2)
        
        # Core stats
        stats = {
            "PTS": {
                "l5_avg": calc_avg(l5, "pts"),
                "l10_avg": calc_avg(l10, "pts"),
                "season_avg": calc_avg(season, "pts"),
                "std_dev_l10": calc_std_dev(l10, "pts")
            },
            "REB": {
                "l5_avg": calc_avg(l5, "reb"),
                "l10_avg": calc_avg(l10, "reb"),
                "season_avg": calc_avg(season, "reb"),
                "std_dev_l10": calc_std_dev(l10, "reb")
            },
            "AST": {
                "l5_avg": calc_avg(l5, "ast"),
                "l10_avg": calc_avg(l10, "ast"),
                "season_avg": calc_avg(season, "ast"),
                "std_dev_l10": calc_std_dev(l10, "ast")
            },
            "3PM": {
                "l5_avg": calc_avg(l5, "tptfgm"),
                "l10_avg": calc_avg(l10, "tptfgm"),
                "season_avg": calc_avg(season, "tptfgm"),
                "std_dev_l10": calc_std_dev(l10, "tptfgm")
            },
            "STL": {
                "l5_avg": calc_avg(l5, "stl"),
                "l10_avg": calc_avg(l10, "stl"),
                "season_avg": calc_avg(season, "stl")
            },
            "BLK": {
                "l5_avg": calc_avg(l5, "blk"),
                "l10_avg": calc_avg(l10, "blk"),
                "season_avg": calc_avg(season, "blk")
            },
            "TOV": {
                "l5_avg": calc_avg(l5, "tov"),
                "l10_avg": calc_avg(l10, "tov"),
                "season_avg": calc_avg(season, "tov")
            }
        }
        
        # Combo stats
        for period, games in [("l5", l5), ("l10", l10), ("season", season)]:
            pts_avg = stats["PTS"].get(f"{period}_avg", 0)
            reb_avg = stats["REB"].get(f"{period}_avg", 0)
            ast_avg = stats["AST"].get(f"{period}_avg", 0)
            
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
        
        stats["synced_from"] = "nba_official"
        stats["synced_at"] = datetime.now(timezone.utc).isoformat()
        
        return stats


# ============================================
# SERVICE SINGLETON
# ============================================

_nba_sync_service: Optional[NBAOfficialSyncService] = None


def get_nba_official_sync_service(db: AsyncIOMotorDatabase) -> NBAOfficialSyncService:
    """Get or create NBA Official sync service instance."""
    global _nba_sync_service
    if _nba_sync_service is None:
        _nba_sync_service = NBAOfficialSyncService(db)
    return _nba_sync_service
