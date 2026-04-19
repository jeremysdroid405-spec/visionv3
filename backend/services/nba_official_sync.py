"""
NBA Official Sync Service
=========================
Primary data engine for NBA Master Hub using official NBA API.

REPLACES: bdl_stats_service.py (deprecated due to data quality issues)

This service fetches game logs directly from the official NBA stats API,
ensuring accurate and reliable data for hit rate calculations.

Key Features:
- Uses nba_api package (official NBA stats endpoint)
- Fetches PlayerGameLog for current season
- Properly sorted by GAME_DATE descending
- Rate-limited to respect NBA API (0.6s between requests)
- Maps official NBA columns to master hub schema

FILTERING (v2 - 2026-03-16):
- Only processes players on active NBA teams (30 franchises)
- Filters out G-League, free agents, and overseas players
- Skips players with 0 minutes in current season
- Reduces sync from ~1100 to ~450 active players
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from motor.motor_asyncio import AsyncIOMotorDatabase

from services.config.collection_names import COLL

# NBA Official API
from nba_api.stats.static import players as nba_players
from nba_api.stats.static import teams as nba_teams
from nba_api.stats.endpoints import playergamelog, commonplayerinfo, commonteamroster

logger = logging.getLogger(__name__)

# Current NBA Season
CURRENT_SEASON = "2025-26"  # Format for nba_api

# Rate limiting (NBA API requires ~0.6s between requests)
REQUEST_DELAY = 0.6

# ============================================
# ACTIVE NBA TEAM IDS (30 Franchises)
# Excludes G-League, historical teams, etc.
# ============================================
ACTIVE_NBA_TEAM_IDS: Set[int] = {
    1610612737,  # Atlanta Hawks
    1610612738,  # Boston Celtics
    1610612739,  # Cleveland Cavaliers
    1610612740,  # New Orleans Pelicans
    1610612741,  # Chicago Bulls
    1610612742,  # Dallas Mavericks
    1610612743,  # Denver Nuggets
    1610612744,  # Golden State Warriors
    1610612745,  # Houston Rockets
    1610612746,  # LA Clippers
    1610612747,  # Los Angeles Lakers
    1610612748,  # Miami Heat
    1610612749,  # Milwaukee Bucks
    1610612750,  # Minnesota Timberwolves
    1610612751,  # Brooklyn Nets
    1610612752,  # New York Knicks
    1610612753,  # Orlando Magic
    1610612754,  # Indiana Pacers
    1610612755,  # Philadelphia 76ers
    1610612756,  # Phoenix Suns
    1610612757,  # Portland Trail Blazers
    1610612758,  # Sacramento Kings
    1610612759,  # San Antonio Spurs
    1610612760,  # Oklahoma City Thunder
    1610612761,  # Toronto Raptors
    1610612762,  # Utah Jazz
    1610612763,  # Memphis Grizzlies
    1610612764,  # Washington Wizards
    1610612765,  # Detroit Pistons
    1610612766,  # Charlotte Hornets
}

# Team abbreviation to ID mapping
TEAM_ABBREV_TO_ID = {team['abbreviation']: team['id'] for team in nba_teams.get_teams()}


class NBAOfficialSyncService:
    """
    Official NBA API sync service for master hub game logs.
    
    This replaces BDL as the primary data source for player statistics.
    Uses the official NBA stats API via the nba_api package.
    
    FILTERING RULES:
    1. Player must be on an active NBA team (30 franchises)
    2. Player must have >0 minutes in current season
    3. G-League and overseas players are excluded
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.hub = db[COLL("master_hub", "nba")]
        self._last_request_time = 0
        self._active_players_cache: Optional[Set[int]] = None
    
    def _rate_limit(self):
        """Enforce rate limiting between NBA API requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        self._last_request_time = time.time()
    
    def _build_active_players_list(self) -> Set[int]:
        """
        Build list of active NBA player IDs by fetching rosters from all 30 teams.
        
        FILTERING LOGIC:
        - Only includes players currently on an NBA roster
        - Excludes G-League, free agents, and overseas players
        
        Returns:
            Set of active NBA player IDs
        """
        if self._active_players_cache is not None:
            return self._active_players_cache
        
        logger.info("[NBA_OFFICIAL_SYNC] Building active players list from team rosters...")
        
        active_player_ids: Set[int] = set()
        
        for team_id in ACTIVE_NBA_TEAM_IDS:
            try:
                self._rate_limit()
                roster = commonteamroster.CommonTeamRoster(
                    team_id=team_id,
                    season=CURRENT_SEASON
                )
                df = roster.get_data_frames()[0]
                
                for _, row in df.iterrows():
                    player_id = row.get('PLAYER_ID')
                    if player_id:
                        active_player_ids.add(int(player_id))
                
            except Exception as e:
                logger.warning(f"[NBA_OFFICIAL_SYNC] Failed to fetch roster for team {team_id}: {e}")
        
        self._active_players_cache = active_player_ids
        logger.info(f"[NBA_OFFICIAL_SYNC] Found {len(active_player_ids)} active NBA players across 30 teams")
        
        return active_player_ids
    
    def _is_active_nba_player(self, nba_player_id: int) -> bool:
        """
        Check if player is on an active NBA roster.
        
        FILTER: Excludes G-League, free agents, overseas players.
        """
        active_players = self._build_active_players_list()
        return nba_player_id in active_players
    
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
        Returns empty list if player has 0 minutes (FILTER).
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
            
            # FILTER: Check if player has any minutes this season
            total_minutes = df['MIN'].sum() if 'MIN' in df.columns else 0
            if total_minutes == 0:
                logger.debug(f"[NBA_OFFICIAL_SYNC] Skipping player {nba_player_id}: 0 minutes played")
                return []
            
            # Sort by GAME_DATE descending (most recent first)
            df = df.sort_values('GAME_DATE', ascending=False)
            
            # Map NBA columns to our schema
            games = []
            for _, row in df.iterrows():
                mins = int(row.get('MIN', 0))
                
                # Skip games where player didn't play
                if mins == 0:
                    continue
                
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
                    "mins": mins,
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
        Sync game logs for ACTIVE NBA players only.
        
        FILTERING APPLIED:
        1. Only players on active NBA rosters (30 teams)
        2. Only players with >0 minutes this season
        
        This reduces sync from ~1100 to ~450 players.
        
        Args:
            batch_size: Number of players to process per batch
            
        Returns:
            Sync results summary
        """
        started_at = datetime.now(timezone.utc)
        logger.info(f"[NBA_OFFICIAL_SYNC] Starting filtered sync at {started_at.isoformat()}")
        
        results = {
            "started_at": started_at.isoformat(),
            "filter_mode": "active_nba_roster_only",
            "players_in_db": 0,
            "players_on_active_roster": 0,
            "players_processed": 0,
            "players_updated": 0,
            "players_skipped_not_on_roster": 0,
            "players_skipped_no_minutes": 0,
            "players_skipped_no_nba_id": 0,
            "players_failed": 0,
            "errors": []
        }
        
        # STEP 1: Build active players list from team rosters
        active_player_ids = self._build_active_players_list()
        results["players_on_active_roster"] = len(active_player_ids)
        
        # STEP 2: Query ONLY players on active rosters (filtered at DB level)
        # Support both nba_player_id and nba_id fields
        cursor = self.hub.find(
            {"$or": [
                {"nba_player_id": {"$in": list(active_player_ids)}},
                {"nba_id": {"$in": list(active_player_ids)}}
            ]},
            {"display_name": 1, "nba_player_id": 1, "nba_id": 1, "team": 1}
        )
        players = await cursor.to_list(length=600)
        
        results["players_in_db"] = len(players)
        logger.info(f"[NBA_OFFICIAL_SYNC] Queried {len(players)} active roster players (filtered at DB level)")
        
        for i, player in enumerate(players):
            player_name = player.get("display_name", "Unknown")
            # Support both field names
            nba_id = player.get("nba_player_id") or player.get("nba_id")
            
            if not nba_id:
                results["players_failed"] += 1
                continue
            
            try:
                
                # Fetch game logs from official NBA API
                game_logs = self._fetch_game_logs(nba_id)
                
                # ===== FILTER 2: Minutes Played Check =====
                # _fetch_game_logs returns [] if 0 minutes
                if not game_logs:
                    results["players_skipped_no_minutes"] += 1
                    await self.hub.update_one(
                        {"_id": player["_id"]},
                        {"$set": {"roster_status": "active_zero_minutes"}}
                    )
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
                            "baseline_stats_source": "nba_official",
                            "roster_status": "active_playing"
                        }
                    }
                )
                
                results["players_updated"] += 1
                results["players_processed"] += 1
                
                # Progress logging
                if (i + 1) % 50 == 0:
                    logger.info(
                        f"[NBA_OFFICIAL_SYNC] Progress: {i + 1}/{len(players)} "
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
            f"{results['players_skipped_not_on_roster']} filtered (not on roster), "
            f"{results['players_skipped_no_minutes']} filtered (0 mins), "
            f"{results['players_failed']} failed "
            f"in {results['duration_seconds']:.1f}s"
        )
        
        return results
    
    async def sync_single_player(self, player_name: str) -> Dict[str, Any]:
        """
        Sync game logs for a single player.
        
        Bypasses roster filter for single player requests.
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
        
        # Fetch game logs (bypasses roster filter for single player)
        game_logs = self._fetch_game_logs(nba_id)
        
        if not game_logs:
            return {"success": False, "error": "No game logs returned (0 minutes played)"}
        
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
