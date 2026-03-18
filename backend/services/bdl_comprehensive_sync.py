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
"""

import httpx
import logging
import os
import re
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# BallDontLie API Configuration
BDL_API_KEY = os.environ.get("BDL_API_KEY", "ad5544be-9969-434b-9389-2b7cf658c8e0")
BDL_BASE_URL = "https://api.balldontlie.io/v1"

# Season configuration (BDL uses start year: 2025 = 2025-26 season)
CURRENT_SEASON = 2025


def _normalize_name(name: str) -> str:
    """
    Normalize player names for consistent matching.
    Strips periods, commas, and suffixes (Jr, Sr, II, III, IV, V).
    """
    if not name:
        return ""
    normalized = name.lower().strip()
    normalized = normalized.replace(".", "").replace(",", "")
    suffix_pattern = r'\b(jr|sr|ii|iii|iv|v)\b'
    normalized = re.sub(suffix_pattern, '', normalized, flags=re.IGNORECASE)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized


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
        Fetch ALL players from /players endpoint with pagination.
        Returns complete player metadata including:
        - id, first_name, last_name
        - position, height, weight
        - team info, jersey_number
        - college, draft_year, draft_round, draft_number
        - country
        """
        logger.info("[BDL] Fetching all players...")
        
        all_players = []
        next_cursor = cursor
        
        while True:
            params = {"per_page": 100}
            if next_cursor:
                params["cursor"] = next_cursor
            
            data = await self._make_request("/players", params)
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
        
        logger.info(f"[BDL] Fetched {len(all_players)} total players")
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
    async def fetch_player_game_logs(self, player_id: int, season: int = CURRENT_SEASON, limit: int = 15) -> List[Dict]:
        """
        Fetch individual game logs from /stats endpoint.
        
        Returns last {limit} games with FULL box score:
        - id, game (game details), player
        - min (minutes), pts, reb, ast, stl, blk, turnover
        - fgm, fga, fg_pct
        - fg3m, fg3a, fg3_pct
        - ftm, fta, ft_pct
        - oreb, dreb, pf
        
        NO field renaming - stored exactly as received from BDL.
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
        
        return data.get("data", [])
    
    # ==================== COMPREHENSIVE PLAYER SYNC ====================
    async def sync_player_complete(self, player_id: int) -> Optional[Dict]:
        """
        Sync ALL available data for a single player.
        Combines: profile, season_averages, and game_logs.
        
        Returns complete document to be stored in nba_master_hub_2026.
        """
        # Fetch profile
        profile = await self.fetch_player_by_id(player_id)
        if not profile:
            return None
        
        # Fetch season averages
        season_avg = await self.fetch_season_averages(player_id)
        
        # Fetch game logs (last 100 games to cover the full season)
        game_logs = await self.fetch_player_game_logs(player_id, limit=100)
        
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
            # Season averages come DIRECTLY from BDL /season_averages (official stats)
            # L5/L10 averages calculated from game_logs
            "baseline_stats": self._transform_bdl_stats(season_avg, game_logs) if season_avg else {},
            
            # Raw BDL stats (preserved for reference)
            "bdl_raw_stats": season_avg if season_avg else {},
            
            # Individual game logs (EXACTLY as received from BDL /stats)
            # Last 15 games with full box scores
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
        
        BDL: {pts: 19.3, reb: 4.8, ast: 7.1, games_played: 57, ...}
        Our: {PTS: {season_avg: 19.3, l5_avg: X, l10_avg: Y}, games_played: 57, ...}
        """
        if not bdl_stats:
            return {}
        
        # Helper to calculate averages from game logs
        def calc_avg_from_logs(logs: List[Dict], stat_key: str, num_games: int) -> float:
            if not logs or len(logs) < num_games:
                return None
            recent = logs[:num_games]
            values = [float(g.get(stat_key, 0) or 0) for g in recent]
            return round(sum(values) / len(values), 1) if values else None
        
        # Sort game logs by date (most recent first)
        sorted_logs = []
        if game_logs:
            sorted_logs = sorted(
                game_logs,
                key=lambda x: x.get('game', {}).get('date', '') if isinstance(x.get('game'), dict) else x.get('date', ''),
                reverse=True
            )
        
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
    
    async def sync_prizepicks_players(self) -> Dict[str, Any]:
        """
        Sync only players currently on the PrizePicks board.
        More efficient than syncing all 500+ NBA players.
        """
        logger.info("[BDL] Syncing players from PrizePicks board...")
        
        # Get unique players from cached board
        pipeline = [
            {"$group": {"_id": "$player_name"}},
            {"$limit": 200}
        ]
        
        cursor = self.db.dg_cached_board.aggregate(pipeline)
        player_names = [doc["_id"] async for doc in cursor]
        
        logger.info(f"[BDL] Found {len(player_names)} unique players on PrizePicks board")
        
        results = {
            "success": 0,
            "failed": 0,
            "not_found": 0,
            "total": len(player_names)
        }
        
        for name in player_names:
            # Search for player in BDL
            player = await self.search_player(name)
            
            if player:
                player_id = player.get("id")
                if player_id:
                    success = await self.sync_player_to_master_hub(player_id)
                    if success:
                        results["success"] += 1
                    else:
                        results["failed"] += 1
                else:
                    results["not_found"] += 1
            else:
                results["not_found"] += 1
                logger.warning(f"[BDL] Player not found: {name}")
            
            # Rate limit protection
            await asyncio.sleep(0.3)
        
        results["synced_at"] = datetime.now(timezone.utc).isoformat()
        logger.info(f"[BDL] PrizePicks sync complete: {results['success']}/{results['total']}")
        
        return results


# ==================== SINGLETON INSTANCE ====================
_bdl_sync_service: Optional[BDLComprehensiveSyncService] = None


def get_bdl_sync_service(db: AsyncIOMotorDatabase) -> BDLComprehensiveSyncService:
    """Get or create BDL sync service singleton."""
    global _bdl_sync_service
    if _bdl_sync_service is None:
        _bdl_sync_service = BDLComprehensiveSyncService(db)
    return _bdl_sync_service
