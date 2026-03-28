"""
Roster Service - Master Roster and Player Data Management
==========================================================
Extracted from services.engines.demon_goblin_engine.py for modularity.

Handles:
- Master roster sync from BallDontLie
- Player stats sync from multiple sources
- Player photo URL management
- Player team lookups with caching
"""
import os
from typing import Dict, List, Optional, Any, Set
from datetime import datetime, timezone, timedelta
import asyncio
import httpx
import logging

from repositories import RepositoryManager
from services.utils_service import sanitize_player_name, normalize_team_name

logger = logging.getLogger(__name__)

# API Configuration - BDL ONLY (BDL Only)
BDL_API_KEY = os.environ.get("BDL_API_KEY")
BDL_BASE_URL = "https://api.balldontlie.io/v1"
CURRENT_SEASON = "2025"

# BDL to NBA team abbreviation mapping
BDL_TO_NBA_ABBREV = {
    "GS": "GSW",
    "NO": "NOP", 
    "NY": "NYK",
    "PHO": "PHX",
    "SA": "SAS",
}

# Known player-team overrides (fixes incorrect API data)
KNOWN_PLAYER_TEAMS = {
    "Derrick White": "BOS", "Jayson Tatum": "BOS", "Jaylen Brown": "BOS",
    "LeBron James": "LAL", "Anthony Davis": "LAL", "Austin Reaves": "LAL",
    "Nikola Jokic": "DEN", "Jamal Murray": "DEN", "Michael Porter Jr.": "DEN",
    "Giannis Antetokounmpo": "MIL", "Damian Lillard": "MIL",
    "Devin Booker": "PHX", "Bradley Beal": "PHX",
    "Luka Doncic": "DAL", "Kyrie Irving": "DAL", "Klay Thompson": "DAL",
    "Stephen Curry": "GSW", "Draymond Green": "GSW",
    "Shai Gilgeous-Alexander": "OKC", "Chet Holmgren": "OKC", "Jalen Williams": "OKC",
    "Joel Embiid": "PHI", "Tyrese Maxey": "PHI",
    "Victor Wembanyama": "SAS", "Devin Vassell": "SAS",
    "Paolo Banchero": "ORL", "Franz Wagner": "ORL",
    "Ja Morant": "MEM", "Desmond Bane": "MEM",
    "Anthony Edwards": "MIN",
    "Donovan Mitchell": "CLE", "Darius Garland": "CLE",
    "Jimmy Butler": "MIA", "Bam Adebayo": "MIA", "Tyler Herro": "MIA",
    "Karl-Anthony Towns": "NYK", "Jalen Brunson": "NYK",
    "Kevin Durant": "HOU", "Jalen Green": "HOU", "Alperen Sengun": "HOU",
    "Zion Williamson": "NOP", "Brandon Ingram": "NOP", "Trey Murphy III": "NOP",
}


class RosterService:
    """Service for managing NBA roster and player data"""
    
    def __init__(self, repo: RepositoryManager, db):
        self.repo = repo
        self.db = db
        self._team_cache: Dict[str, str] = {}
        self._roster_loaded = False
        
        # Legacy direct collection access (gradual migration)
        self.master_roster = db.dg_master_roster
        self.player_stats = db.dg_player_stats
        self.flagged_players = db.dg_flagged_players
    
    def set_api_keys(self, bdl_key: str = None):
        """Set API keys for external services"""
        global BDL_API_KEY
        if bdl_key:
            BDL_API_KEY = bdl_key
    
    # ==================== MASTER ROSTER SYNC ====================
    
    async def sync_master_roster(self) -> Dict[str, Any]:
        """
        Weekly roster sync from BallDontLie API.
        
        Establishes the source of truth for player-to-team mapping.
        Should run every Sunday at midnight.
        """
        logger.info("=" * 60)
        logger.info("[MASTER ROSTER] Starting weekly roster sync...")
        logger.info("=" * 60)
        
        sync_start = datetime.now(timezone.utc)
        players_synced = 0
        teams_found: Set[str] = set()
        errors = []
        
        try:
            headers = {"Authorization": BDL_API_KEY}
            all_players = []
            cursor = None
            page = 1
            max_pages = 50
            
            while page <= max_pages:
                url = f"{BDL_BASE_URL}/players?per_page=100"
                if cursor:
                    url += f"&cursor={cursor}"
                
                logger.info(f"[MASTER ROSTER] Fetching page {page}...")
                
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.get(url, headers=headers)
                    
                    if response.status_code != 200:
                        logger.error(f"[MASTER ROSTER] API error: {response.status_code}")
                        errors.append(f"Page {page}: HTTP {response.status_code}")
                        break
                    
                    data = response.json()
                    players = data.get("data", [])
                    
                    if not players:
                        break
                    
                    all_players.extend(players)
                    
                    meta = data.get("meta", {})
                    cursor = meta.get("next_cursor")
                    
                    if not cursor:
                        break
                    
                    page += 1
                    await asyncio.sleep(0.2)
            
            logger.info(f"[MASTER ROSTER] Fetched {len(all_players)} total players")
            
            roster_docs = []
            for player in all_players:
                player_id = player.get("id")
                first_name = player.get("first_name", "")
                last_name = player.get("last_name", "")
                full_name = f"{first_name} {last_name}".strip()
                
                team_data = player.get("team", {})
                team_abbrev = team_data.get("abbreviation", "") if team_data else ""
                team_full = team_data.get("full_name", "") if team_data else ""
                
                if not team_abbrev or not full_name:
                    continue
                
                normalized_name = sanitize_player_name(full_name)
                
                roster_doc = {
                    "player_name": full_name,
                    "normalized_name": normalized_name,
                    "bdl_player_id": player_id,
                    "team_abbreviation": team_abbrev.upper(),
                    "team_full_name": team_full,
                    "position": player.get("position", ""),
                    "height": player.get("height", ""),
                    "weight": player.get("weight", ""),
                    "jersey_number": player.get("jersey_number", ""),
                    "college": player.get("college", ""),
                    "country": player.get("country", ""),
                    "draft_year": player.get("draft_year"),
                    "draft_round": player.get("draft_round"),
                    "draft_number": player.get("draft_number"),
                    "synced_at": sync_start.isoformat(),
                    "source": "balldontlie"
                }
                
                roster_docs.append(roster_doc)
                teams_found.add(team_abbrev.upper())
                players_synced += 1
            
            # Replace roster
            await self.master_roster.delete_many({})
            if roster_docs:
                await self.master_roster.insert_many(roster_docs)
                await self.master_roster.create_index("player_name")
                await self.master_roster.create_index("normalized_name")
                await self.master_roster.create_index("team_abbreviation")
            
            # Update cache
            await self._rebuild_team_cache()
            
            logger.info(f"[MASTER ROSTER] SYNC COMPLETE: {players_synced} players, {len(teams_found)} teams")
            
            return {
                "success": True,
                "players_synced": players_synced,
                "teams_found": len(teams_found),
                "teams": sorted(teams_found),
                "errors": errors,
                "synced_at": sync_start.isoformat()
            }
            
        except Exception as e:
            logger.error(f"[MASTER ROSTER] Sync failed: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "players_synced": players_synced,
                "errors": errors
            }
    
    # ==================== PLAYER STATS SYNC ====================
    
    async def sync_player_stats(self, player_names: List[str] = None) -> Dict[str, Any]:
        """
        Sync player game logs to MongoDB for cached hit rate calculations.
        
        Data Source Priority:
        1. BallDontLie (primary)
        2. NBA.com API (secondary for rookies)
        """
        sync_start = datetime.now(timezone.utc)
        logger.info("[STATS SYNC] Starting player stats sync...")
        
        stats_synced = 0
        stats_from_bdl = 0
        stats_from_nba = 0
        errors = []
        
        try:
            if player_names is None:
                # Get players from cached board
                cached_board = self.db.dg_cached_board
                players = await cached_board.distinct("player_name")
                player_names = list(players) if players else []
            
            if not player_names:
                return {"success": True, "stats_synced": 0, "message": "No players to sync"}
            
            logger.info(f"[STATS SYNC] Syncing stats for {len(player_names)} players...")
            
            batch_size = 10
            for i in range(0, len(player_names), batch_size):
                batch = player_names[i:i+batch_size]
                
                for player_name in batch:
                    try:
                        stats = await self._fetch_bdl_stats(player_name)
                        source = "balldontlie" if stats and stats.get("games") else None
                        
                        if not source:
                            stats = self._fetch_nba_api_stats(player_name)
                            source = "nba_api" if stats and stats.get("games") else None
                        
                        if stats and stats.get("games"):
                            games = stats.get("games", [])
                            sorted_games = sorted(
                                games,
                                key=lambda g: g.get("game", {}).get("date", "") if isinstance(g.get("game"), dict) else g.get("GAME_DATE", ""),
                                reverse=True
                            )
                            
                            doc = {
                                "player_name": player_name,
                                "normalized_name": sanitize_player_name(player_name),
                                "games": sorted_games,
                                "total_games": len(sorted_games),
                                "source": source,
                                "synced_at": sync_start.isoformat()
                            }
                            
                            await self.player_stats.update_one(
                                {"normalized_name": doc["normalized_name"]},
                                {"$set": doc},
                                upsert=True
                            )
                            
                            stats_synced += 1
                            if source == "balldontlie":
                                stats_from_bdl += 1
                            else:
                                stats_from_nba += 1
                                
                    except Exception as e:
                        errors.append(f"{player_name}: {str(e)}")
                    
                    await asyncio.sleep(0.15)
                
                if i % 50 == 0 and i > 0:
                    logger.info(f"[STATS SYNC] Progress: {i}/{len(player_names)} players")
            
            await self.player_stats.create_index("normalized_name", unique=True)
            await self.player_stats.create_index("player_name")
            
            duration = (datetime.now(timezone.utc) - sync_start).total_seconds()
            logger.info(f"[STATS SYNC] Completed: {stats_synced} players in {duration:.1f}s")
            
            return {
                "success": True,
                "stats_synced": stats_synced,
                "from_balldontlie": stats_from_bdl,
                "from_nba_api": stats_from_nba,
                "errors": errors[:10],
                "duration_seconds": duration,
                "synced_at": sync_start.isoformat()
            }
            
        except Exception as e:
            logger.error(f"[STATS SYNC] Failed: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "stats_synced": stats_synced,
                "errors": errors
            }
    
    async def _fetch_bdl_stats(self, player_name: str) -> Dict[str, Any]:
        """Fetch player stats from BallDontLie"""
        try:
            player_id = await self._get_bdl_player_id(player_name)
            if not player_id:
                return {}
            
            url = f"{BDL_BASE_URL}/stats"
            params = {
                "player_ids[]": player_id,
                "seasons[]": CURRENT_SEASON,
                "per_page": 100
            }
            headers = {"Authorization": BDL_API_KEY}
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, headers=headers, timeout=15.0)
                
                if response.status_code == 200:
                    data = response.json()
                    games = data.get("data", [])
                    
                    if games:
                        return {
                            "player_name": player_name,
                            "games": games,
                            "total_games": len(games),
                            "source": "balldontlie"
                        }
        except Exception as e:
            logger.debug(f"[BDL] Error fetching {player_name}: {e}")
        
        return {}
    
    async def _get_bdl_player_id(self, player_name: str) -> Optional[int]:
        """Get BallDontLie player ID by name"""
        try:
            url = f"{BDL_BASE_URL}/players"
            params = {"search": player_name}
            headers = {"Authorization": BDL_API_KEY}
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, headers=headers, timeout=10.0)
                
                if response.status_code == 200:
                    data = response.json()
                    players = data.get("data", [])
                    
                    if players:
                        return players[0].get("id")
        except Exception:
            pass
        return None
    
    def _fetch_nba_api_stats(self, player_name: str) -> Dict[str, Any]:
        """Fetch stats from NBA.com API (fallback for rookies)"""
        try:
            from nba_api.stats.endpoints import playergamelog
            from nba_api.stats.static import players as nba_players
            
            all_players = nba_players.get_players()
            normalized_search = player_name.lower().strip()
            
            player_match = None
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
                return {}
            
            player_id = player_match['id']
            
            import time
            time.sleep(0.6)
            
            now = datetime.now()
            if now.month >= 10:
                season_year = now.year
            else:
                season_year = now.year - 1
            current_season = f"{season_year}-{str(season_year + 1)[-2:]}"
            
            gamelog = playergamelog.PlayerGameLog(
                player_id=player_id,
                season=current_season,
                season_type_all_star='Regular Season'
            )
            df = gamelog.get_data_frames()[0]
            
            if df.empty:
                return {}
            
            games = []
            for _, row in df.iterrows():
                games.append({
                    "pts": row.get('PTS', 0),
                    "reb": row.get('REB', 0),
                    "ast": row.get('AST', 0),
                    "fg3m": row.get('FG3M', 0),
                    "blk": row.get('BLK', 0),
                    "stl": row.get('STL', 0),
                    "turnover": row.get('TOV', 0),
                    "game": {
                        "date": row.get('GAME_DATE', ''),
                        "matchup": row.get('MATCHUP', '')
                    }
                })
            
            return {
                "games": games,
                "player_name": player_name,
                "source": "nba_api"
            }
            
        except ImportError:
            return {}
        except Exception as e:
            logger.debug(f"[NBA_API] Error fetching stats for {player_name}: {e}")
            return {}
    
    # ==================== PLAYER TEAM LOOKUPS ====================
    
    async def get_player_team(self, player_name: str) -> Optional[str]:
        """
        Look up player's team with priority order:
        1. Known player overrides
        2. In-memory cache
        3. Database lookup
        4. Fuzzy matching
        """
        # Priority 1: Manual overrides
        if player_name in KNOWN_PLAYER_TEAMS:
            return KNOWN_PLAYER_TEAMS[player_name]
        
        normalized = sanitize_player_name(player_name)
        
        # Check normalized in overrides
        for known_name, team in KNOWN_PLAYER_TEAMS.items():
            if sanitize_player_name(known_name) == normalized:
                return team
        
        # Priority 2: In-memory cache
        if not self._roster_loaded:
            await self._rebuild_team_cache()
        
        if normalized in self._team_cache:
            return self._team_cache[normalized]
        
        # Priority 3: Database lookup
        doc = await self.master_roster.find_one(
            {"normalized_name": normalized},
            {"_id": 0, "team_abbreviation": 1}
        )
        
        if doc:
            team = doc.get("team_abbreviation")
            self._team_cache[normalized] = team
            return team
        
        # Priority 4: Fuzzy match
        return await self._fuzzy_match_team(player_name, normalized)
    
    async def _fuzzy_match_team(self, player_name: str, normalized: str) -> Optional[str]:
        """Fuzzy match player name to find team"""
        all_players = await self.master_roster.find(
            {},
            {"_id": 0, "player_name": 1, "normalized_name": 1, "team_abbreviation": 1}
        ).to_list(None)
        
        best_match = None
        best_ratio = 0
        
        for p in all_players:
            p_normalized = p.get("normalized_name", "")
            p_full = p.get("player_name", "").lower()
            
            if normalized == p_normalized:
                best_match = p
                break
            
            ratio = 0
            if normalized in p_normalized or p_normalized in normalized:
                ratio = 0.8
            elif p_full in player_name.lower() or player_name.lower() in p_full:
                ratio = 0.7
            
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = p
        
        if best_match and best_ratio >= 0.7:
            team = best_match.get("team_abbreviation")
            self._team_cache[normalized] = team
            return team
        
        return None
    
    async def _rebuild_team_cache(self):
        """Rebuild in-memory team cache from database"""
        self._team_cache.clear()
        
        roster = await self.master_roster.find(
            {},
            {"_id": 0, "normalized_name": 1, "team_abbreviation": 1}
        ).to_list(None)
        
        for doc in roster:
            if doc.get("normalized_name") and doc.get("team_abbreviation"):
                self._team_cache[doc["normalized_name"]] = doc["team_abbreviation"]
        
        self._roster_loaded = True
        logger.info(f"[ROSTER CACHE] Loaded {len(self._team_cache)} players")
    
    # ==================== PLAYER STATS CACHE ====================
    
    async def get_cached_player_stats(self, player_name: str) -> Dict[str, Any]:
        """Get player stats from MongoDB cache"""
        normalized = sanitize_player_name(player_name)
        doc = await self.player_stats.find_one(
            {"normalized_name": normalized},
            {"_id": 0}
        )
        return doc if doc else {}
    
    # ==================== FLAGGED PLAYERS ====================
    
    async def flag_unknown_player(self, player_name: str, odds_api_team: str, game_info: Dict):
        """Flag a player not found in master roster for manual review"""
        normalized = sanitize_player_name(player_name)
        
        await self.flagged_players.update_one(
            {"normalized_name": normalized},
            {
                "$set": {
                    "player_name": player_name,
                    "normalized_name": normalized,
                    "odds_api_team": odds_api_team,
                    "home_team": game_info.get("home_team", ""),
                    "away_team": game_info.get("away_team", ""),
                    "game_date": game_info.get("game_date", ""),
                    "flagged_at": datetime.now(timezone.utc).isoformat(),
                    "reviewed": False
                }
            },
            upsert=True
        )
        
        logger.warning(f"[FLAGGED] Unknown player: {player_name} (Odds API says: {odds_api_team})")
    
    def clear_cache(self):
        """Clear all in-memory caches"""
        self._team_cache.clear()
        self._roster_loaded = False
