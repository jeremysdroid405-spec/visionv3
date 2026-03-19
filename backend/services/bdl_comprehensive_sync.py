"""
BDL Comprehensive Sync Service
==============================
Exhaustive BallDontLie API Integration - 1:1 Data Mirror

Endpoints consumed:
- /players: Player metadata (height, weight, position, team, draft info)
- /season_averages: Complete season stats (pts, reb, ast, stl, blk, fg%, 3p%, etc.)
- /stats: Individual game logs with full box score details
- /teams: Team information and abbreviations

Data is stored EXACTLY as received from BDL - no field renaming.
Stored in: pick_vision.nba_master_hub_2026.baseline_stats

NBA.com Integration (nba_api):
- Uses nba_id for official L5/L10/L15/L20 stats from playerdashboardbylastngames
- Master hub stores both bdl_id (BDL) and nba_id (NBA.com) for each player
"""

import httpx
import logging
import os
import re
import asyncio
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any
from motor.motor_asyncio import AsyncIOMotorDatabase

# NBA.com API for L5/L10 stats
from nba_api.stats.static import players as nba_players_static
from nba_api.stats.endpoints import playerdashboardbylastngames

logger = logging.getLogger(__name__)

# BallDontLie API Configuration
BDL_API_KEY = os.environ.get("BDL_API_KEY", "ad5544be-9969-434b-9389-2b7cf658c8e0")
BDL_BASE_URL = "https://api.balldontlie.io/v1"

# Season configuration (BDL uses start year: 2025 = 2025-26 season)
CURRENT_SEASON = 2025
NBA_SEASON = "2025-26"  # NBA.com format

# Build NBA.com player ID lookup once at module load
_NBA_ID_LOOKUP: Dict[str, int] = {}


def _build_nba_id_lookup():
    """Build normalized name -> nba_id lookup from nba_api static data."""
    global _NBA_ID_LOOKUP
    if _NBA_ID_LOOKUP:
        return _NBA_ID_LOOKUP
    
    try:
        all_players = nba_players_static.get_active_players()
        for p in all_players:
            norm = _normalize_name(p['full_name'])
            _NBA_ID_LOOKUP[norm] = p['id']
        logger.info(f"[NBA] Built ID lookup with {len(_NBA_ID_LOOKUP)} players")
    except Exception as e:
        logger.error(f"[NBA] Failed to build ID lookup: {e}")
    
    return _NBA_ID_LOOKUP


def _normalize_name(name: str) -> str:
    """
    Normalize player names for consistent matching.
    Handles diacritics, periods, commas, apostrophes, and suffixes.
    """
    if not name:
        return ""
    # Remove diacritics (ć -> c, č -> c, ö -> o, etc.)
    normalized = unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode('ASCII')
    normalized = normalized.lower().strip()
    normalized = normalized.replace(".", "").replace(",", "").replace("'", "").replace("'", "").replace("-", " ")
    suffix_pattern = r'\b(jr|sr|ii|iii|iv|v)\b'
    normalized = re.sub(suffix_pattern, '', normalized, flags=re.IGNORECASE)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized


def get_nba_id_for_name(display_name: str) -> Optional[int]:
    """Get NBA.com player ID by display name."""
    lookup = _build_nba_id_lookup()
    norm = _normalize_name(display_name)
    return lookup.get(norm)


class BDLComprehensiveSyncService:
    """
    Comprehensive BallDontLie API sync service.
    Pulls ALL available data and stores it in nba_master_hub_2026.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.master_hub = db.nba_master_hub_2026
        self.teams_cache: Dict[int, Dict] = {}
        self.players_cache: Dict[int, Dict] = {}
        
    async def _make_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Make authenticated request to BDL API with retry logic."""
        url = f"{BDL_BASE_URL}{endpoint}"
        headers = {"Authorization": BDL_API_KEY}
        
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(url, params=params, headers=headers)
                    
                    if response.status_code == 200:
                        return response.json()
                    elif response.status_code == 429:
                        # Rate limited - wait and retry
                        wait_time = (attempt + 1) * 2
                        logger.warning(f"[BDL] Rate limited, waiting {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"[BDL] {endpoint} returned {response.status_code}")
                        return None
            except Exception as e:
                logger.error(f"[BDL] Request error for {endpoint}: {e}")
                await asyncio.sleep(1)
        
        return None
    
    # ==================== TEAMS ENDPOINT ====================
    async def sync_all_teams(self) -> Dict[int, Dict]:
        """
        Fetch all NBA teams from /teams endpoint.
        Returns dict mapping team_id -> team data.
        """
        logger.info("[BDL] Syncing all teams...")
        
        data = await self._make_request("/teams")
        if not data:
            return {}
        
        teams = data.get("data", [])
        for team in teams:
            team_id = team.get("id")
            if team_id:
                self.teams_cache[team_id] = team
        
        logger.info(f"[BDL] Synced {len(self.teams_cache)} teams")
        return self.teams_cache
    
    # ==================== PLAYERS ENDPOINT ====================
    async def fetch_all_players(self, cursor: int = 0) -> List[Dict]:
        """
        Fetch ACTIVE players only from /players/active endpoint.
        Returns complete player metadata for current NBA roster (~530 players).
        """
        logger.info("[BDL] Fetching active players only...")
        
        all_players = []
        next_cursor = cursor
        
        while True:
            params = {"per_page": 100}
            if next_cursor:
                params["cursor"] = next_cursor
            
            # Use /players/active endpoint - only returns current NBA players
            data = await self._make_request("/players/active", params)
            if not data:
                break
            
            players = data.get("data", [])
            all_players.extend(players)
            
            # Check for more pages
            meta = data.get("meta", {})
            next_cursor = meta.get("next_cursor")
            
            if not next_cursor or not players:
                break
            
            # Rate limit protection
            await asyncio.sleep(0.2)
        
        logger.info(f"[BDL] Fetched {len(all_players)} active players")
        return all_players
    
    async def fetch_player_by_id(self, player_id: int) -> Optional[Dict]:
        """
        Fetch single player profile from /players/{id}.
        Returns FULL metadata payload as-is from BDL.
        """
        data = await self._make_request(f"/players/{player_id}")
        if data and "data" in data:
            return data["data"]
        return data
    
    async def search_player(self, name: str) -> Optional[Dict]:
        """
        Search for player by name with normalization.
        Returns player profile if found.
        """
        # Try last name search (most reliable)
        name_parts = name.strip().split()
        search_term = name_parts[-1] if name_parts else name
        
        params = {"search": search_term, "per_page": 25}
        data = await self._make_request("/players", params)
        
        if not data:
            return None
        
        players = data.get("data", [])
        normalized_search = _normalize_name(name)
        
        for player in players:
            full_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
            if _normalize_name(full_name) == normalized_search:
                return player
        
        return None
    
    # ==================== SEASON AVERAGES ENDPOINT ====================
    async def fetch_season_averages(self, player_id: int, season: int = CURRENT_SEASON) -> Optional[Dict]:
        """
        Fetch season averages from /season_averages endpoint.
        
        Returns COMPLETE payload with ALL fields:
        - games_played, min (minutes per game)
        - pts, reb, ast, stl, blk, turnover
        - fgm, fga, fg_pct (field goals)
        - fg3m, fg3a, fg3_pct (3-pointers)
        - ftm, fta, ft_pct (free throws)
        - oreb, dreb (offensive/defensive rebounds)
        - pf (personal fouls)
        
        NO field renaming - stored exactly as received from BDL.
        """
        params = {
            "season": season,
            "player_id": player_id  # BDL uses singular player_id, not player_ids[]
        }
        
        data = await self._make_request("/season_averages", params)
        if not data:
            return None
        
        averages = data.get("data", [])
        if averages:
            return averages[0]  # Returns first (only) entry for this player
        
        return None
    
    # ==================== STATS (GAME LOGS) ENDPOINT ====================
    async def fetch_player_game_logs(self, player_id: int, season: int = CURRENT_SEASON, limit: int = 100) -> List[Dict]:
        """
        Fetch individual game logs from /stats endpoint.
        
        IMPORTANT: BDL API returns games in ASCENDING order (oldest first).
        We fetch more games (100) and sort by date descending to get recent games.
        
        Returns last {limit} games with FULL box score:
        - id, game (game details), player
        - min (minutes), pts, reb, ast, stl, blk, turnover
        - fgm, fga, fg_pct
        - fg3m, fg3a, fg3_pct
        - ftm, fta, ft_pct
        - oreb, dreb, pf
        
        NO field renaming - stored exactly as received from BDL.
        Games are sorted by date (most recent first) before returning.
        """
        params = {
            "seasons[]": season,
            "player_ids[]": player_id,
            "per_page": limit,
            "postseason": "false"
        }
        
        data = await self._make_request("/stats", params)
        if not data:
            return []
        
        games = data.get("data", [])
        
        # CRITICAL: Sort by game date descending (most recent first)
        # BDL API returns games in ascending order by default
        sorted_games = sorted(
            games,
            key=lambda x: x.get("game", {}).get("date", "") if isinstance(x.get("game"), dict) else "",
            reverse=True
        )
        
        return sorted_games
    
    # ==================== COMPREHENSIVE PLAYER SYNC ====================
    async def sync_player_complete(self, player_id: int) -> Optional[Dict]:
        """
        Sync ALL available data for a single player.
        Combines: profile, season_averages, and game_logs.
        Uses parallel fetching for speed.
        
        Returns complete document to be stored in nba_master_hub_2026.
        """
        # Fetch all data in parallel
        results = await asyncio.gather(
            self.fetch_player_by_id(player_id),
            self.fetch_season_averages(player_id),
            self.fetch_player_game_logs(player_id, limit=100),
            return_exceptions=True
        )
        
        profile, season_avg, game_logs = results
        
        # Handle exceptions
        if isinstance(profile, Exception):
            logger.error(f"[BDL] Profile fetch error for {player_id}: {profile}")
            return None
        if not profile:
            logger.warning(f"[BDL] No profile for player {player_id}")
            return None
        if isinstance(season_avg, Exception):
            logger.debug(f"[BDL] Season avg error for {player_id}: {season_avg}")
            season_avg = None
        if isinstance(game_logs, Exception):
            logger.debug(f"[BDL] Game logs error for {player_id}: {game_logs}")
            game_logs = []
        
        # Build display name
        first_name = profile.get("first_name", "")
        last_name = profile.get("last_name", "")
        display_name = f"{first_name} {last_name}".strip()
        
        # Get team info
        team_data = profile.get("team", {})
        team_abbrev = team_data.get("abbreviation", "")
        
        # Build complete document
        doc = {
            # Identity
            "bdl_id": player_id,
            "display_name": display_name,
            "normalized_name": _normalize_name(display_name),
            
            # Profile metadata (EXACTLY as received from BDL)
            "profile": {
                "id": profile.get("id"),
                "first_name": profile.get("first_name"),
                "last_name": profile.get("last_name"),
                "position": profile.get("position"),
                "height": profile.get("height"),
                "weight": profile.get("weight"),
                "jersey_number": profile.get("jersey_number"),
                "college": profile.get("college"),
                "country": profile.get("country"),
                "draft_year": profile.get("draft_year"),
                "draft_round": profile.get("draft_round"),
                "draft_number": profile.get("draft_number"),
                "team": team_data
            },
            
            # Team info (for quick access)
            "team": team_abbrev,
            "team_full_name": team_data.get("full_name", ""),
            "team_id": team_data.get("id"),
            
            # Baseline stats - Transform BDL format to our expected format
            "baseline_stats": self._transform_bdl_stats(season_avg, game_logs) if season_avg else {},
            
            # Raw BDL stats (preserved for reference)
            "bdl_raw_stats": season_avg if season_avg else {},
            
            # Individual game logs (EXACTLY as received from BDL /stats)
            "bdl_game_logs": game_logs,
            
            # Sync metadata
            "last_bdl_sync": datetime.now(timezone.utc),
            "bdl_sync_source": "comprehensive_sync_v1",
            "season": CURRENT_SEASON
        }
        
        return doc
    
    def _transform_bdl_stats(self, bdl_stats: Dict, game_logs: List[Dict] = None) -> Dict:
        """
        Transform BDL season averages to our expected format.
        
        OFFICIAL SEASON AVERAGES come directly from BDL /season_averages endpoint.
        L5 and L10 averages are calculated from game_logs (if provided).
        
        IMPORTANT: DNP (Did Not Play) games are filtered out for L5/L10 calculations.
        A game is considered DNP if minutes played is 0 or "00".
        
        BDL: {pts: 19.3, reb: 4.8, ast: 7.1, games_played: 57, ...}
        Our: {PTS: {season_avg: 19.3, l5_avg: X, l10_avg: Y}, games_played: 57, ...}
        """
        if not bdl_stats:
            return {}
        
        # Helper to check if player actually played (not DNP)
        def did_play(game: Dict) -> bool:
            mins = game.get("min", "0") or "0"
            if isinstance(mins, str):
                # Handle "MM:SS" format or "00"
                mins = mins.split(":")[0] if ":" in mins else mins
                try:
                    return int(mins) > 0
                except ValueError:
                    return False
            return float(mins) > 0 if mins else False
        
        # Helper to calculate averages from game logs (excluding DNPs)
        def calc_avg_from_logs(logs: List[Dict], stat_key: str, num_games: int) -> float:
            # Filter to games where player actually played
            played_games = [g for g in logs if did_play(g)]
            if not played_games or len(played_games) < num_games:
                # If we don't have enough games, use what we have
                if not played_games:
                    return None
                recent = played_games[:num_games]
            else:
                recent = played_games[:num_games]
            values = [float(g.get(stat_key, 0) or 0) for g in recent]
            return round(sum(values) / len(values), 1) if values else None
        
        # Sort game logs by date (most recent first) - already sorted from fetch_player_game_logs
        sorted_logs = game_logs if game_logs else []
        
        # Mapping from BDL keys to our uppercase keys
        stat_map = {
            'pts': 'PTS',
            'reb': 'REB',
            'ast': 'AST',
            'stl': 'STL',
            'blk': 'BLK',
            'turnover': 'TO',
            'fg3m': '3PM',
            'fgm': 'FGM',
            'ftm': 'FTM',
        }
        
        transformed = {}
        
        for bdl_key, our_key in stat_map.items():
            if bdl_key in bdl_stats:
                season_avg = round(bdl_stats[bdl_key], 1) if bdl_stats[bdl_key] else None
                
                # Calculate L5 and L10 from game logs
                l5_avg = calc_avg_from_logs(sorted_logs, bdl_key, 5) if sorted_logs else None
                l10_avg = calc_avg_from_logs(sorted_logs, bdl_key, 10) if sorted_logs else None
                
                transformed[our_key] = {
                    'season_avg': season_avg,
                    'l5_avg': l5_avg,
                    'l10_avg': l10_avg
                }
        
        # Add games_played from official BDL stats
        if 'games_played' in bdl_stats:
            transformed['games_played'] = bdl_stats['games_played']
        
        # Add synced_from marker
        transformed['synced_from'] = 'bdl_season_averages'
        transformed['synced_at'] = datetime.now(timezone.utc).isoformat()
        
        # Add shooting percentages directly
        if 'fg_pct' in bdl_stats:
            transformed['fg_pct'] = bdl_stats['fg_pct']
        if 'fg3_pct' in bdl_stats:
            transformed['fg3_pct'] = bdl_stats['fg3_pct']
        if 'ft_pct' in bdl_stats:
            transformed['ft_pct'] = bdl_stats['ft_pct']
        
        # Add combo stats
        if 'pts' in bdl_stats and 'reb' in bdl_stats and 'ast' in bdl_stats:
            pts = bdl_stats.get('pts', 0) or 0
            reb = bdl_stats.get('reb', 0) or 0
            ast = bdl_stats.get('ast', 0) or 0
            
            transformed['PRA'] = {
                'season_avg': round(pts + reb + ast, 1),
                'l5_avg': round((transformed.get('PTS', {}).get('l5_avg') or 0) + 
                               (transformed.get('REB', {}).get('l5_avg') or 0) + 
                               (transformed.get('AST', {}).get('l5_avg') or 0), 1) if sorted_logs else None,
                'l10_avg': round((transformed.get('PTS', {}).get('l10_avg') or 0) + 
                                (transformed.get('REB', {}).get('l10_avg') or 0) + 
                                (transformed.get('AST', {}).get('l10_avg') or 0), 1) if sorted_logs else None
            }
            
        if 'pts' in bdl_stats and 'reb' in bdl_stats:
            pts = bdl_stats.get('pts', 0) or 0
            reb = bdl_stats.get('reb', 0) or 0
            transformed['PR'] = {
                'season_avg': round(pts + reb, 1),
                'l5_avg': round((transformed.get('PTS', {}).get('l5_avg') or 0) + 
                               (transformed.get('REB', {}).get('l5_avg') or 0), 1) if sorted_logs else None,
                'l10_avg': round((transformed.get('PTS', {}).get('l10_avg') or 0) + 
                                (transformed.get('REB', {}).get('l10_avg') or 0), 1) if sorted_logs else None
            }
            
        if 'pts' in bdl_stats and 'ast' in bdl_stats:
            pts = bdl_stats.get('pts', 0) or 0
            ast = bdl_stats.get('ast', 0) or 0
            transformed['PA'] = {
                'season_avg': round(pts + ast, 1),
                'l5_avg': round((transformed.get('PTS', {}).get('l5_avg') or 0) + 
                               (transformed.get('AST', {}).get('l5_avg') or 0), 1) if sorted_logs else None,
                'l10_avg': round((transformed.get('PTS', {}).get('l10_avg') or 0) + 
                                (transformed.get('AST', {}).get('l10_avg') or 0), 1) if sorted_logs else None
            }
            
        if 'reb' in bdl_stats and 'ast' in bdl_stats:
            reb = bdl_stats.get('reb', 0) or 0
            ast = bdl_stats.get('ast', 0) or 0
            transformed['RA'] = {
                'season_avg': round(reb + ast, 1),
                'l5_avg': round((transformed.get('REB', {}).get('l5_avg') or 0) + 
                               (transformed.get('AST', {}).get('l5_avg') or 0), 1) if sorted_logs else None,
                'l10_avg': round((transformed.get('REB', {}).get('l10_avg') or 0) + 
                                (transformed.get('AST', {}).get('l10_avg') or 0), 1) if sorted_logs else None
            }
        
        return transformed
    
    async def sync_player_to_master_hub(self, player_id: int) -> bool:
        """
        Sync a single player's complete data to nba_master_hub_2026.
        Updates existing document or creates new one.
        """
        doc = await self.sync_player_complete(player_id)
        if not doc:
            return False
        
        try:
            # Upsert by bdl_id
            await self.master_hub.update_one(
                {"bdl_id": player_id},
                {"$set": doc},
                upsert=True
            )
            logger.debug(f"[BDL] Synced player {doc.get('display_name')} (ID: {player_id})")
            return True
        except Exception as e:
            logger.error(f"[BDL] Error saving player {player_id}: {e}")
            return False
    
    # ==================== BULK SYNC ====================
    async def sync_active_players(self, player_ids: List[int] = None) -> Dict[str, Any]:
        """
        Sync all active players or a specific list.
        
        If player_ids is None, fetches all players from BDL.
        """
        sync_start = datetime.now(timezone.utc)
        results = {
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "total": 0
        }
        
        # Get player list
        if player_ids:
            players_to_sync = [{"id": pid} for pid in player_ids]
        else:
            all_players = await self.fetch_all_players()
            # Filter to active players (those with a team)
            players_to_sync = [p for p in all_players if p.get("team")]
        
        results["total"] = len(players_to_sync)
        logger.info(f"[BDL] Starting bulk sync for {results['total']} players...")
        
        # Process in batches to avoid overwhelming API
        batch_size = 10
        for i in range(0, len(players_to_sync), batch_size):
            batch = players_to_sync[i:i+batch_size]
            
            tasks = []
            for player in batch:
                player_id = player.get("id")
                if player_id:
                    tasks.append(self.sync_player_to_master_hub(player_id))
            
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in batch_results:
                if result is True:
                    results["success"] += 1
                elif isinstance(result, Exception):
                    results["failed"] += 1
                    logger.error(f"[BDL] Batch error: {result}")
                else:
                    results["failed"] += 1
            
            # Progress logging
            processed = i + len(batch)
            if processed % 50 == 0:
                logger.info(f"[BDL] Progress: {processed}/{results['total']} players synced")
            
            # Rate limit protection
            await asyncio.sleep(0.5)
        
        sync_duration = (datetime.now(timezone.utc) - sync_start).total_seconds()
        
        results["duration_seconds"] = round(sync_duration, 2)
        results["synced_at"] = datetime.now(timezone.utc).isoformat()
        
        logger.info(f"[BDL] Bulk sync complete: {results['success']}/{results['total']} succeeded in {results['duration_seconds']}s")
        
        return results
    
    async def sync_all_active_players(self) -> Dict[str, Any]:
        """
        Sync ALL active NBA players to master hub.
        
        Flow:
        1. Fetch all active players from BDL /players/active (~530 players)
        2. BATCH call /season_averages/general for official season stats
        3. Fetch L5/L10 from NBA.com API (official pre-calculated stats)
        4. Update nba_master_hub_2026 for all players
        
        This is the MASTER sync - runs daily to keep all player data fresh.
        """
        sync_start = datetime.now(timezone.utc)
        logger.info("[SYNC] === MASTER SYNC: All Active NBA Players ===")
        
        # Step 1: Get all active players from BDL
        logger.info("[SYNC] Step 1: Fetching all active players from BDL...")
        all_players = await self.fetch_all_players()
        
        # Filter to players with teams (truly active)
        active_players = [p for p in all_players if p.get("team")]
        bdl_ids = [p["id"] for p in active_players]
        
        logger.info(f"[SYNC] Found {len(bdl_ids)} active players with teams")
        
        if not bdl_ids:
            return {"success": 0, "failed": 0, "total": 0, "duration_seconds": 0}
        
        # Step 2: BATCH fetch season averages from BDL (GOAT tier)
        logger.info(f"[SYNC] Step 2: BATCH fetching season averages from BDL...")
        season_avgs_map = await self._batch_fetch_season_averages(bdl_ids)
        logger.info(f"[SYNC] Got season averages for {len([k for k,v in season_avgs_map.items() if v])} players")
        
        # Step 3: Update master hub for ALL players (with NBA.com L5/L10 fetch)
        logger.info("[SYNC] Step 3: Updating master hub with NBA.com L5/L10 stats...")
        success = 0
        nba_enriched = 0
        
        for idx, player in enumerate(active_players):
            bdl_id = player["id"]
            season_avg = season_avgs_map.get(bdl_id, {})
            
            # Build display name
            first_name = player.get("first_name", "")
            last_name = player.get("last_name", "")
            display_name = f"{first_name} {last_name}".strip()
            team_data = player.get("team", {})
            
            # Get NBA.com ID for this player
            nba_id = get_nba_id_for_name(display_name)
            
            # Fetch L5/L10 from NBA.com if we have nba_id
            nba_stats = None
            if nba_id:
                nba_stats = await self.fetch_nba_last_n_games(nba_id)
                if nba_stats:
                    nba_enriched += 1
            
            # Build baseline stats combining BDL season avg + NBA.com L5/L10
            baseline_stats = self._build_baseline_stats(season_avg, nba_stats)
            
            update_doc = {
                "bdl_id": bdl_id,
                "display_name": display_name,
                "normalized_name": _normalize_name(display_name),
                "team": team_data.get("abbreviation", ""),
                "team_full_name": team_data.get("full_name", ""),
                "team_id": team_data.get("id"),
                "profile": {
                    "id": player.get("id"),
                    "first_name": first_name,
                    "last_name": last_name,
                    "position": player.get("position"),
                    "height": player.get("height"),
                    "weight": player.get("weight"),
                    "jersey_number": player.get("jersey_number"),
                    "college": player.get("college"),
                    "country": player.get("country"),
                    "draft_year": player.get("draft_year"),
                    "draft_round": player.get("draft_round"),
                    "draft_number": player.get("draft_number"),
                    "team": team_data
                },
                "baseline_stats": baseline_stats,
                "last_bdl_sync": datetime.now(timezone.utc),
                "season": CURRENT_SEASON
            }
            
            # Add nba_id if matched
            if nba_id:
                update_doc["nba_id"] = nba_id
            
            # Store raw stats for reference
            if season_avg:
                update_doc["bdl_raw_stats"] = season_avg
            if nba_stats:
                update_doc["nba_stats_raw"] = nba_stats
            
            try:
                await self.master_hub.update_one(
                    {"bdl_id": bdl_id},
                    {"$set": update_doc},
                    upsert=True
                )
                success += 1
            except Exception as e:
                logger.error(f"[SYNC] Error updating {display_name}: {e}")
            
            # Progress logging every 50 players
            if (idx + 1) % 50 == 0:
                logger.info(f"[SYNC] Progress: {idx + 1}/{len(active_players)} players synced")
        
        duration = round((datetime.now(timezone.utc) - sync_start).total_seconds(), 2)
        logger.info(f"[SYNC] === MASTER SYNC COMPLETE: {success}/{len(active_players)} players, {nba_enriched} with NBA.com L5/L10, in {duration}s ===")
        
        return {
            "success": success,
            "failed": len(active_players) - success,
            "total": len(active_players),
            "nba_enriched": nba_enriched,
            "duration_seconds": duration,
            "sync_type": "all_active_players_with_nba"
        }
    
    def _build_baseline_stats(self, bdl_season_avg: Dict, nba_stats: Optional[Dict]) -> Dict:
        """
        Build baseline stats combining BDL season averages with NBA.com L5/L10.
        
        BDL provides: Official season averages
        NBA.com provides: Pre-calculated L5/L10/L15/L20 averages
        """
        baseline = {}
        
        # Mapping from BDL keys to our keys
        bdl_to_our = {
            'pts': 'PTS', 'reb': 'REB', 'ast': 'AST', 'stl': 'STL',
            'blk': 'BLK', 'fg3m': '3PM', 'fgm': 'FGM', 'ftm': 'FTM', 'turnover': 'TO'
        }
        
        # NBA.com key mapping
        nba_to_our = {
            'PTS': 'PTS', 'REB': 'REB', 'AST': 'AST', 'STL': 'STL',
            'BLK': 'BLK', 'FG3M': '3PM', 'FGM': 'FGM', 'FTM': 'FTM', 'TOV': 'TO'
        }
        
        # Build stats for each category
        for bdl_key, our_key in bdl_to_our.items():
            stat_entry = {}
            
            # Season average from BDL
            if bdl_season_avg and bdl_key in bdl_season_avg:
                val = bdl_season_avg[bdl_key]
                if val is not None:
                    stat_entry['season_avg'] = round(float(val), 1)
            
            # L5/L10 from NBA.com
            if nba_stats:
                # Find the NBA.com key for this stat
                nba_key = None
                for nk, ok in nba_to_our.items():
                    if ok == our_key:
                        nba_key = nk
                        break
                
                if nba_key:
                    if 'last5' in nba_stats and nba_key in nba_stats['last5']:
                        stat_entry['l5_avg'] = nba_stats['last5'][nba_key]
                    if 'last10' in nba_stats and nba_key in nba_stats['last10']:
                        stat_entry['l10_avg'] = nba_stats['last10'][nba_key]
                    if 'overall' in nba_stats and nba_key in nba_stats['overall']:
                        # Use NBA.com overall if BDL doesn't have it
                        if 'season_avg' not in stat_entry:
                            stat_entry['season_avg'] = nba_stats['overall'][nba_key]
            
            if stat_entry:
                baseline[our_key] = stat_entry
        
        # Add shooting percentages from BDL
        if bdl_season_avg:
            for pct_key in ['fg_pct', 'fg3_pct', 'ft_pct']:
                if pct_key in bdl_season_avg and bdl_season_avg[pct_key] is not None:
                    baseline[pct_key] = bdl_season_avg[pct_key]
            if 'games_played' in bdl_season_avg:
                baseline['games_played'] = bdl_season_avg['games_played']
        
        # Combo stats (PRA, PR, PA, RA)
        pts = baseline.get('PTS', {}).get('season_avg', 0) or 0
        reb = baseline.get('REB', {}).get('season_avg', 0) or 0
        ast = baseline.get('AST', {}).get('season_avg', 0) or 0
        
        pts_l5 = baseline.get('PTS', {}).get('l5_avg', 0) or 0
        reb_l5 = baseline.get('REB', {}).get('l5_avg', 0) or 0
        ast_l5 = baseline.get('AST', {}).get('l5_avg', 0) or 0
        
        pts_l10 = baseline.get('PTS', {}).get('l10_avg', 0) or 0
        reb_l10 = baseline.get('REB', {}).get('l10_avg', 0) or 0
        ast_l10 = baseline.get('AST', {}).get('l10_avg', 0) or 0
        
        baseline['PRA'] = {
            'season_avg': round(pts + reb + ast, 1),
            'l5_avg': round(pts_l5 + reb_l5 + ast_l5, 1) if nba_stats else None,
            'l10_avg': round(pts_l10 + reb_l10 + ast_l10, 1) if nba_stats else None
        }
        baseline['PR'] = {
            'season_avg': round(pts + reb, 1),
            'l5_avg': round(pts_l5 + reb_l5, 1) if nba_stats else None,
            'l10_avg': round(pts_l10 + reb_l10, 1) if nba_stats else None
        }
        baseline['PA'] = {
            'season_avg': round(pts + ast, 1),
            'l5_avg': round(pts_l5 + ast_l5, 1) if nba_stats else None,
            'l10_avg': round(pts_l10 + ast_l10, 1) if nba_stats else None
        }
        baseline['RA'] = {
            'season_avg': round(reb + ast, 1),
            'l5_avg': round(reb_l5 + ast_l5, 1) if nba_stats else None,
            'l10_avg': round(reb_l10 + ast_l10, 1) if nba_stats else None
        }
        
        baseline['synced_from'] = 'bdl_season_avg_plus_nba_l5l10'
        baseline['synced_at'] = datetime.now(timezone.utc).isoformat()
        
        return baseline
    
    async def _batch_fetch_season_averages(self, bdl_ids: List[int]) -> Dict[int, Dict]:
        """
        GOAT tier batch endpoint for official season averages.
        Uses /season_averages/general with player_ids[] array.
        
        Fetches in batches of 100 to handle pagination.
        """
        result = {pid: {} for pid in bdl_ids}
        
        # BDL has pagination, fetch in batches of 100
        batch_size = 100
        for i in range(0, len(bdl_ids), batch_size):
            batch = bdl_ids[i:i+batch_size]
            
            # Build URL with player_ids
            url = f"{BDL_BASE_URL}/season_averages/general?season={CURRENT_SEASON}&season_type=regular&type=base&per_page=100"
            for pid in batch:
                url += f"&player_ids[]={pid}"
            
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.get(url, headers={"Authorization": BDL_API_KEY})
                    
                    if response.status_code == 200:
                        data = response.json().get("data", [])
                        
                        for record in data:
                            # Player ID is nested inside player object
                            player = record.get("player", {})
                            player_id = player.get("id") if isinstance(player, dict) else None
                            stats = record.get("stats", {})
                            
                            if player_id and player_id in result:
                                # Transform stats to expected format
                                result[player_id] = {
                                    "games_played": stats.get("gp"),
                                    "pts": stats.get("pts"),
                                    "reb": stats.get("reb"),
                                    "ast": stats.get("ast"),
                                    "stl": stats.get("stl"),
                                    "blk": stats.get("blk"),
                                    "turnover": stats.get("tov"),
                                    "fg3m": stats.get("fg3m"),
                                    "fgm": stats.get("fgm"),
                                    "ftm": stats.get("ftm"),
                                    "fg_pct": stats.get("fg_pct"),
                                    "fg3_pct": stats.get("fg3_pct"),
                                    "ft_pct": stats.get("ft_pct"),
                                    "oreb": stats.get("oreb"),
                                    "dreb": stats.get("dreb"),
                                    "min": stats.get("min")
                                }
                    else:
                        logger.error(f"[BDL] Season averages batch error: {response.status_code} - {response.text[:200]}")
            except Exception as e:
                logger.error(f"[BDL] Batch season_averages error: {e}")
            
            # Small delay between batches
            if i + batch_size < len(bdl_ids):
                await asyncio.sleep(0.2)
        
        return result
    
    def _merge_season_avg_with_logs(self, season_avg: Dict, game_logs: List[Dict]) -> Dict:
        """
        Merge official season averages with L5/L10 calculated from game logs.
        
        Season averages come from BDL /season_averages/general (official).
        L5/L10 calculated from game logs.
        """
        if not season_avg and not game_logs:
            return {}
        
        # Filter out DNP games
        played_games = [g for g in game_logs if g.get("min") and g.get("min") != "00" and g.get("min") != "0"]
        
        stat_keys = {
            "PTS": "pts",
            "REB": "reb", 
            "AST": "ast",
            "STL": "stl",
            "BLK": "blk",
            "3PM": "fg3m",
            "FGM": "fgm",
            "FTM": "ftm",
            "TO": "turnover",
        }
        
        baseline = {}
        
        for our_key, bdl_key in stat_keys.items():
            # Official season average from BDL
            season_val = season_avg.get(bdl_key) if season_avg else None
            if season_val is not None:
                season_val = round(float(season_val), 1)
            
            # L5/L10 from game logs
            l5_avg = None
            l10_avg = None
            l10_values = []
            
            if played_games:
                values = [float(g.get(bdl_key, 0) or 0) for g in played_games]
                l10 = values[:10]
                l5 = values[:5]
                l10_avg = round(sum(l10) / len(l10), 1) if l10 else None
                l5_avg = round(sum(l5) / len(l5), 1) if l5 else None
                l10_values = [round(v, 1) for v in l10]
            
            # Use calculated season avg from logs if no official one
            if season_val is None and played_games:
                values = [float(g.get(bdl_key, 0) or 0) for g in played_games]
                season_val = round(sum(values) / len(values), 1) if values else None
            
            if season_val is not None or l5_avg is not None:
                baseline[our_key] = {
                    "season_avg": season_val,
                    "l5_avg": l5_avg,
                    "l10_avg": l10_avg,
                    "l10_values": l10_values,
                    "games_played": len(played_games)
                }
        
        # Add percentages directly
        if season_avg:
            if season_avg.get("fg_pct"):
                baseline["fg_pct"] = season_avg["fg_pct"]
            if season_avg.get("fg3_pct"):
                baseline["fg3_pct"] = season_avg["fg3_pct"]
            if season_avg.get("ft_pct"):
                baseline["ft_pct"] = season_avg["ft_pct"]
            if season_avg.get("games_played"):
                baseline["games_played"] = season_avg["games_played"]
        
        # Combo stats
        pts = baseline.get("PTS", {}).get("season_avg") or 0
        reb = baseline.get("REB", {}).get("season_avg") or 0
        ast = baseline.get("AST", {}).get("season_avg") or 0
        
        pts_l5 = baseline.get("PTS", {}).get("l5_avg") or 0
        reb_l5 = baseline.get("REB", {}).get("l5_avg") or 0
        ast_l5 = baseline.get("AST", {}).get("l5_avg") or 0
        
        pts_l10 = baseline.get("PTS", {}).get("l10_avg") or 0
        reb_l10 = baseline.get("REB", {}).get("l10_avg") or 0
        ast_l10 = baseline.get("AST", {}).get("l10_avg") or 0
        
        baseline["PRA"] = {
            "season_avg": round(pts + reb + ast, 1),
            "l5_avg": round(pts_l5 + reb_l5 + ast_l5, 1) if played_games else None,
            "l10_avg": round(pts_l10 + reb_l10 + ast_l10, 1) if played_games else None
        }
        baseline["PR"] = {
            "season_avg": round(pts + reb, 1),
            "l5_avg": round(pts_l5 + reb_l5, 1) if played_games else None,
            "l10_avg": round(pts_l10 + reb_l10, 1) if played_games else None
        }
        baseline["PA"] = {
            "season_avg": round(pts + ast, 1),
            "l5_avg": round(pts_l5 + ast_l5, 1) if played_games else None,
            "l10_avg": round(pts_l10 + ast_l10, 1) if played_games else None
        }
        baseline["RA"] = {
            "season_avg": round(reb + ast, 1),
            "l5_avg": round(reb_l5 + ast_l5, 1) if played_games else None,
            "l10_avg": round(reb_l10 + ast_l10, 1) if played_games else None
        }
        
        baseline["synced_from"] = "bdl_goat_tier_batch"
        baseline["synced_at"] = datetime.now(timezone.utc).isoformat()
        
        return baseline
    
    def _calculate_baseline_from_logs(self, game_logs: List[Dict]) -> Dict:
        """Calculate baseline stats from game logs."""
        if not game_logs:
            return {}
        
        # Filter out DNP games (0 minutes)
        played_games = [g for g in game_logs if g.get("min") and g.get("min") != "00" and g.get("min") != "0"]
        
        if not played_games:
            return {}
        
        stat_keys = {
            "PTS": "pts",
            "REB": "reb", 
            "AST": "ast",
            "STL": "stl",
            "BLK": "blk",
            "FG3M": "fg3m",
            "FGM": "fgm",
            "FTM": "ftm",
            "OREB": "oreb",
            "DREB": "dreb",
            "TO": "turnover",
            "PF": "pf",
            "MIN": "min"
        }
        
        baseline = {}
        
        for our_key, bdl_key in stat_keys.items():
            values = []
            for game in played_games:
                val = game.get(bdl_key)
                if val is not None:
                    # Handle MIN specially (convert "33:18" to 33.3)
                    if bdl_key == "min" and isinstance(val, str) and ":" in val:
                        parts = val.split(":")
                        val = int(parts[0]) + int(parts[1])/60 if len(parts) == 2 else 0
                    values.append(float(val) if val else 0)
            
            if values:
                l10 = values[:10]
                l5 = values[:5]
                baseline[our_key] = {
                    "season_avg": round(sum(values) / len(values), 2),
                    "l10_avg": round(sum(l10) / len(l10), 2) if l10 else None,
                    "l5_avg": round(sum(l5) / len(l5), 2) if l5 else None,
                    "l10_values": [round(v, 1) for v in l10],
                    "games_played": len(values)
                }
        
        return baseline
    
    async def _batch_fetch_game_logs(self, bdl_ids: List[int]) -> Dict[int, List[Dict]]:
        """Fetch game logs for multiple players in batches."""
        result = {pid: [] for pid in bdl_ids}
        
        batch_size = 30
        for i in range(0, len(bdl_ids), batch_size):
            batch = bdl_ids[i:i+batch_size]
            
            url = f"{BDL_BASE_URL}/stats?seasons[]={CURRENT_SEASON}&per_page=100&postseason=false"
            for pid in batch:
                url += f"&player_ids[]={pid}"
            
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.get(url, headers={"Authorization": BDL_API_KEY})
                    if response.status_code == 200:
                        data = response.json().get("data", [])
                        for game in data:
                            player = game.get("player", {})
                            pid = player.get("id") if isinstance(player, dict) else None
                            if pid and pid in result:
                                result[pid].append(game)
                    else:
                        logger.error(f"[BDL] Game logs batch error: {response.status_code}")
            except Exception as e:
                logger.error(f"[BDL] Batch game_logs error: {e}")
            
            await asyncio.sleep(0.2)
        
        # Sort by date descending
        for pid in result:
            result[pid] = sorted(
                result[pid],
                key=lambda x: x.get("game", {}).get("date", "") if isinstance(x.get("game"), dict) else "",
                reverse=True
            )
        
        return result
    
    # ==================== NBA.COM API METHODS ====================
    async def fetch_nba_last_n_games(self, nba_id: int, retry_count: int = 2) -> Optional[Dict]:
        """
        Fetch pre-calculated L5/L10/L15/L20 averages from NBA.com.
        
        Uses playerdashboardbylastngames endpoint which provides official
        aggregated stats directly from NBA.com - no manual calculation needed.
        
        Tries current season first, falls back to previous season if needed.
        
        Returns dict with keys: overall, last5, last10, last15, last20
        Each contains: PTS, REB, AST, STL, BLK, FG3M, FGM, FTM, TOV, MIN, GP
        """
        # Try current season first, then previous
        seasons_to_try = [NBA_SEASON, "2024-25"]
        
        for season in seasons_to_try:
            for attempt in range(retry_count):
                try:
                    # Run in thread pool to avoid blocking async
                    loop = asyncio.get_event_loop()
                    dashboard = await loop.run_in_executor(
                        None,
                        lambda s=season: playerdashboardbylastngames.PlayerDashboardByLastNGames(
                            player_id=nba_id,
                            season=s,
                            per_mode_detailed='PerGame',
                            timeout=60
                        )
                    )
                    
                    result_sets = dashboard.get_dict().get('resultSets', [])
                    
                    # Map result set names to our keys
                    name_map = {
                        'OverallPlayerDashboard': 'overall',
                        'Last5PlayerDashboard': 'last5',
                        'Last10PlayerDashboard': 'last10',
                        'Last15PlayerDashboard': 'last15',
                        'Last20PlayerDashboard': 'last20'
                    }
                    
                    stats = {}
                    for rs in result_sets:
                        key = name_map.get(rs['name'])
                        if key and rs['rowSet']:
                            headers = rs['headers']
                            row = rs['rowSet'][0]
                            
                            # Extract the stats we care about
                            stats[key] = {}
                            stat_cols = ['GP', 'PTS', 'REB', 'AST', 'STL', 'BLK', 'FG3M', 'FGM', 'FTM', 'TOV', 'MIN']
                            for col in stat_cols:
                                if col in headers:
                                    idx = headers.index(col)
                                    stats[key][col] = row[idx]
                    
                    if stats:
                        stats['season'] = season
                        logger.debug(f"[NBA] Got L5/L10 for nba_id={nba_id} from {season}")
                        return stats
                        
                except Exception as e:
                    logger.debug(f"[NBA] Attempt {attempt+1} failed for nba_id={nba_id}, season={season}: {e}")
                    if attempt < retry_count - 1:
                        await asyncio.sleep(1)  # Brief delay before retry
        
        logger.debug(f"[NBA] All attempts failed for nba_id={nba_id}")
        return None
    
    async def enrich_baseline_with_nba_stats(self, bdl_id: int) -> bool:
        """
        Enrich a player's baseline_stats with official NBA.com L5/L10 data.
        
        Looks up the player by bdl_id, fetches NBA.com stats if nba_id exists,
        and updates the baseline_stats with official L5/L10 averages.
        """
        # Get player from master hub
        player = await self.master_hub.find_one({"bdl_id": bdl_id})
        if not player:
            logger.warning(f"[NBA] Player bdl_id={bdl_id} not found in master hub")
            return False
        
        nba_id = player.get("nba_id")
        if not nba_id:
            logger.debug(f"[NBA] No nba_id for {player.get('display_name')}")
            return False
        
        # Fetch NBA.com stats
        nba_stats = await self.fetch_nba_last_n_games(nba_id)
        if not nba_stats:
            return False
        
        # Get existing BDL season averages
        bdl_raw = player.get("bdl_raw_stats", {})
        
        # Rebuild baseline_stats using the new method
        baseline = self._build_baseline_stats(bdl_raw, nba_stats)
        baseline['nba_enriched_at'] = datetime.now(timezone.utc).isoformat()
        
        # Update in database
        await self.master_hub.update_one(
            {"bdl_id": bdl_id},
            {"$set": {"baseline_stats": baseline, "nba_stats_raw": nba_stats}}
        )
        
        logger.info(f"[NBA] Enriched {player.get('display_name')} with NBA.com L5/L10")
        return True


# ==================== SINGLETON INSTANCE ====================
_bdl_sync_service: Optional[BDLComprehensiveSyncService] = None


def get_bdl_sync_service(db: AsyncIOMotorDatabase) -> BDLComprehensiveSyncService:
    """Get or create BDL sync service singleton."""
    global _bdl_sync_service
    if _bdl_sync_service is None:
        _bdl_sync_service = BDLComprehensiveSyncService(db)
    return _bdl_sync_service
