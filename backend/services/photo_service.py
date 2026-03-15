"""
Photo Service - Player Photo URL Management
============================================
Extracted from demon_goblin_engine.py for modularity.

Handles:
- ESPN headshot URL generation
- NBA.com CDN fallback
- Team logo fallbacks
- Batch photo sync operations
"""
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import asyncio
import httpx
import logging

from config.settings import TEAM_LOGOS, NBA_PLAYER_IDS
from services.utils_service import sanitize_player_name

logger = logging.getLogger(__name__)

# Tank01 API configuration
TANK01_API_KEY = None
TANK01_BASE = "https://tank01-fantasy-stats.p.rapidapi.com"
TANK01_HOST = "tank01-fantasy-stats.p.rapidapi.com"

# Tank01 to NBA team abbreviation mapping
TANK01_TO_NBA_ABBREV = {
    "GS": "GSW",
    "NO": "NOP",
    "NY": "NYK",
    "PHO": "PHX",
    "SA": "SAS",
}


class PhotoService:
    """Service for managing player photo URLs"""
    
    def __init__(self, db):
        self.db = db
        self.master_roster = db.dg_master_roster
        self.cached_board = db.dg_cached_board
        self._espn_id_cache: Dict[str, Dict] = {}
    
    def set_api_key(self, tank01_key: str):
        """Set Tank01 API key"""
        global TANK01_API_KEY
        TANK01_API_KEY = tank01_key
    
    # ==================== GLOBAL PHOTO SYNC ====================
    
    async def sync_all_photos(self) -> Dict[str, Any]:
        """
        Global photo sync - Populate headshots for 450+ NBA players.
        
        Source Priority:
        1. ESPN CDN (via Tank01 espnID)
        2. NBA CDN (fallback)
        3. Team Logo (final fallback - NO GRAY!)
        """
        logger.info("=" * 60)
        logger.info("[GLOBAL PHOTO SYNC] Starting ESPN headshot pipeline...")
        logger.info("=" * 60)
        
        sync_start = datetime.now(timezone.utc)
        espn_photos = 0
        nba_photos = 0
        logo_fallbacks = 0
        total_processed = 0
        
        # Step 1: Fetch ESPN IDs from Tank01
        espn_id_map = await self._fetch_espn_ids_from_tank01()
        
        # Step 2: Update master roster
        roster_players = await self.master_roster.find({}).to_list(None)
        logger.info(f"[PHOTO SYNC] Processing {len(roster_players)} players in master_roster...")
        
        for player in roster_players:
            player_name = player.get("player_name", "")
            team = player.get("team_abbreviation", "")
            normalized = sanitize_player_name(player_name)
            
            photo_url, photo_source, espn_id = self._get_best_photo_url(
                player_name, team, normalized, espn_id_map
            )
            
            if photo_source == "espn_direct" or photo_source == "espn_cdn":
                espn_photos += 1
            elif photo_source == "nba_cdn":
                nba_photos += 1
            elif photo_source == "team_logo":
                logo_fallbacks += 1
            
            team_logo = TEAM_LOGOS.get(team, "")
            
            await self.master_roster.update_one(
                {"player_name": player_name},
                {
                    "$set": {
                        "photo_url": photo_url,
                        "photo_source": photo_source,
                        "espn_id": espn_id,
                        "team_logo_url": team_logo,
                        "photo_synced_at": sync_start.isoformat()
                    }
                }
            )
            total_processed += 1
        
        # Step 3: Update cached board
        active_players = await self.cached_board.find({}).to_list(None)
        active_count = 0
        
        for player in active_players:
            player_name = player.get("player_name", "")
            team = player.get("team", "")
            normalized = sanitize_player_name(player_name)
            
            photo_url, photo_source, espn_id = self._get_best_photo_url(
                player_name, team, normalized, espn_id_map
            )
            
            team_logo = TEAM_LOGOS.get(team, "")
            
            await self.cached_board.update_one(
                {"player_name": player_name},
                {
                    "$set": {
                        "photo_url": photo_url,
                        "photo_source": photo_source,
                        "espn_id": espn_id,
                        "team_logo_url": team_logo,
                        "photo_synced_at": sync_start.isoformat()
                    }
                }
            )
            active_count += 1
        
        duration = (datetime.now(timezone.utc) - sync_start).total_seconds()
        
        logger.info("=" * 60)
        logger.info(f"[GLOBAL PHOTO SYNC] COMPLETE")
        logger.info(f"  Total: {total_processed}, ESPN: {espn_photos}, NBA: {nba_photos}, Logo: {logo_fallbacks}")
        logger.info(f"  Active players updated: {active_count}")
        logger.info(f"  Duration: {duration:.1f}s")
        logger.info("=" * 60)
        
        return {
            "success": True,
            "total_processed": total_processed,
            "espn_photos": espn_photos,
            "nba_photos": nba_photos,
            "logo_fallbacks": logo_fallbacks,
            "active_players_updated": active_count,
            "gray_silhouettes": 0,
            "tank01_players_found": len(espn_id_map),
            "synced_at": sync_start.isoformat(),
            "duration_seconds": round(duration, 1)
        }
    
    async def _fetch_espn_ids_from_tank01(self) -> Dict[str, Dict]:
        """Fetch ESPN IDs from Tank01 API"""
        espn_id_map = {}
        
        if not TANK01_API_KEY:
            logger.warning("[PHOTO SYNC] No Tank01 API key - skipping ESPN ID fetch")
            return espn_id_map
        
        try:
            headers = {
                "X-RapidAPI-Key": TANK01_API_KEY,
                "X-RapidAPI-Host": TANK01_HOST
            }
            
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.get(
                    f"{TANK01_BASE}/getNBATeams",
                    headers=headers,
                    params={"rosters": "true"}
                )
                
                if response.status_code == 200:
                    data = response.json()
                    teams_data = data.get("body", [])
                    
                    for team in teams_data:
                        roster = team.get("Roster", {})
                        
                        if isinstance(roster, dict):
                            for player_id, player in roster.items():
                                player_name = player.get("longName", "")
                                espn_id = player.get("espnID")
                                espn_headshot = player.get("espnHeadshot", "")
                                nba_id = player.get("nbaComID")
                                team_abv = team.get("teamAbv", "")
                                
                                if player_name:
                                    normalized = sanitize_player_name(player_name)
                                    espn_id_map[normalized] = {
                                        "espn_id": espn_id,
                                        "espn_headshot": espn_headshot,
                                        "nba_id": nba_id,
                                        "team": team_abv,
                                        "original_name": player_name
                                    }
                    
                    logger.info(f"[PHOTO SYNC] Mapped {len(espn_id_map)} players with ESPN IDs")
                else:
                    logger.warning(f"[PHOTO SYNC] Tank01 API returned {response.status_code}")
                    
        except Exception as e:
            logger.error(f"[PHOTO SYNC] Tank01 API error: {str(e)}")
        
        self._espn_id_cache = espn_id_map
        return espn_id_map
    
    def _get_best_photo_url(
        self, 
        player_name: str, 
        team: str, 
        normalized: str,
        espn_id_map: Dict[str, Dict]
    ) -> tuple:
        """
        Get the best photo URL for a player.
        
        Returns: (photo_url, photo_source, espn_id)
        """
        photo_url = None
        photo_source = None
        espn_id = None
        
        # Source 1: ESPN CDN (from Tank01)
        if normalized in espn_id_map:
            tank_data = espn_id_map[normalized]
            espn_id = tank_data.get("espn_id")
            espn_headshot = tank_data.get("espn_headshot", "")
            
            if espn_headshot and "nophoto" not in espn_headshot.lower():
                photo_url = espn_headshot
                photo_source = "espn_direct"
            elif espn_id:
                photo_url = f"https://a.espncdn.com/i/headshots/nba/players/full/{espn_id}.png"
                photo_source = "espn_cdn"
        
        # Source 2: NBA CDN (static mapping)
        if not photo_url:
            nba_id = NBA_PLAYER_IDS.get(player_name)
            if nba_id:
                photo_url = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_id}.png"
                photo_source = "nba_cdn"
        
        # Source 3: Team Logo (NO GRAY!)
        team_logo = TEAM_LOGOS.get(team, "")
        if not photo_url and team_logo:
            photo_url = team_logo
            photo_source = "team_logo"
        
        return photo_url, photo_source, espn_id
    
    # ==================== SINGLE PLAYER PHOTO ====================
    
    def get_player_photo_url(self, player_name: str, team: str = None) -> Dict[str, str]:
        """
        Get photo URLs for a single player.
        
        Returns dict with primary and fallback URLs.
        """
        urls = {}
        
        # Check static NBA ID mapping
        nba_id = NBA_PLAYER_IDS.get(player_name)
        if nba_id:
            urls["nba_headshot"] = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_id}.png"
            urls["nba_headshot_small"] = f"https://cdn.nba.com/headshots/nba/latest/260x190/{nba_id}.png"
        
        # Check ESPN cache
        normalized = sanitize_player_name(player_name)
        if normalized in self._espn_id_cache:
            tank_data = self._espn_id_cache[normalized]
            espn_headshot = tank_data.get("espn_headshot", "")
            if espn_headshot and "nophoto" not in espn_headshot.lower():
                urls["espn"] = espn_headshot
        
        # Team logo fallback
        if team and team in TEAM_LOGOS:
            urls["team_logo"] = TEAM_LOGOS[team]
        
        # Default fallback
        urls["fallback"] = "https://cdn.nba.com/headshots/nba/latest/260x190/fallback.png"
        
        return urls
    
    async def get_photo_from_roster(self, player_name: str) -> Optional[str]:
        """Get photo URL from master roster"""
        normalized = sanitize_player_name(player_name)
        
        doc = await self.master_roster.find_one(
            {"normalized_name": normalized},
            {"_id": 0, "photo_url": 1}
        )
        
        if doc and doc.get("photo_url"):
            return doc.get("photo_url")
        
        return None
    
    # ==================== ACTIVE PLAYER SYNC ====================
    
    async def sync_active_players_with_photos(self) -> Dict[str, Any]:
        """
        Sync ONLY active NBA players from Tank01 with headshots.
        
        Tank01 returns ~530 active players with ESPN headshot URLs.
        """
        logger.info("=" * 60)
        logger.info("[ACTIVE PLAYER SYNC] Fetching current NBA rosters from Tank01...")
        logger.info("=" * 60)
        
        sync_start = datetime.now(timezone.utc)
        players_synced = 0
        teams_processed = 0
        photos_found = 0
        errors = []
        
        if not TANK01_API_KEY:
            return {"success": False, "error": "No Tank01 API key configured"}
        
        try:
            headers = {
                "X-RapidAPI-Key": TANK01_API_KEY,
                "X-RapidAPI-Host": TANK01_HOST
            }
            
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.get(
                    f"{TANK01_BASE}/getNBATeams",
                    headers=headers,
                    params={"rosters": "true"}
                )
                
                if response.status_code != 200:
                    return {"success": False, "error": f"Tank01 API returned {response.status_code}"}
                
                data = response.json()
                teams_data = data.get("body", [])
                
                await self.master_roster.delete_many({})
                
                player_docs = []
                
                for team in teams_data:
                    tank01_abv = team.get("teamAbv", "")
                    team_name = team.get("teamName", "")
                    team_city = team.get("teamCity", "")
                    roster = team.get("Roster", {})
                    
                    team_abv = TANK01_TO_NBA_ABBREV.get(tank01_abv, tank01_abv)
                    
                    if team_abv not in TEAM_LOGOS:
                        continue
                    
                    teams_processed += 1
                    
                    if isinstance(roster, dict):
                        for player_id, player in roster.items():
                            player_name = player.get("longName", "")
                            if not player_name:
                                continue
                            
                            espn_headshot = player.get("espnHeadshot", "")
                            espn_id = player.get("espnID")
                            nba_com_id = player.get("nbaComID")
                            
                            photo_url = None
                            photo_source = None
                            
                            is_real_espn_photo = (
                                espn_headshot and
                                "nophoto" not in espn_headshot.lower() and
                                "combiner" not in espn_headshot
                            )
                            
                            if is_real_espn_photo:
                                photo_url = espn_headshot
                                photo_source = "espn_direct"
                                photos_found += 1
                            elif espn_id and espn_headshot and "nophoto" not in espn_headshot.lower():
                                photo_url = f"https://a.espncdn.com/i/headshots/nba/players/full/{espn_id}.png"
                                photo_source = "espn_cdn"
                                photos_found += 1
                            elif nba_com_id:
                                photo_url = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_com_id}.png"
                                photo_source = "nba_cdn"
                                photos_found += 1
                            else:
                                photo_url = TEAM_LOGOS.get(team_abv, "")
                                photo_source = "team_logo"
                            
                            player_doc = {
                                "player_name": player_name,
                                "normalized_name": sanitize_player_name(player_name),
                                "team_abbreviation": team_abv,
                                "team_name": f"{team_city} {team_name}",
                                "position": player.get("pos", ""),
                                "jersey_number": player.get("jerseyNum", ""),
                                "height": player.get("height", ""),
                                "weight": player.get("weight", ""),
                                "college": player.get("college", ""),
                                "birth_date": player.get("bDay", ""),
                                "years_pro": player.get("exp", ""),
                                "tank01_player_id": player_id,
                                "espn_id": espn_id,
                                "nba_com_id": nba_com_id,
                                "photo_url": photo_url,
                                "photo_source": photo_source,
                                "team_logo_url": TEAM_LOGOS.get(team_abv, ""),
                                "is_active": True,
                                "synced_at": sync_start.isoformat()
                            }
                            
                            player_docs.append(player_doc)
                            players_synced += 1
                
                if player_docs:
                    await self.master_roster.insert_many(player_docs)
                    
        except Exception as e:
            logger.error(f"[ACTIVE PLAYER SYNC] Error: {str(e)}")
            errors.append(str(e))
        
        duration = (datetime.now(timezone.utc) - sync_start).total_seconds()
        
        logger.info("=" * 60)
        logger.info(f"[ACTIVE PLAYER SYNC] COMPLETE")
        logger.info(f"  Teams: {teams_processed}, Players: {players_synced}, Photos: {photos_found}")
        logger.info("=" * 60)
        
        return {
            "success": True,
            "teams_processed": teams_processed,
            "players_synced": players_synced,
            "photos_found": photos_found,
            "photo_coverage": f"{(photos_found/players_synced*100):.1f}%" if players_synced > 0 else "0%",
            "errors": errors,
            "synced_at": sync_start.isoformat(),
            "duration_seconds": round(duration, 1)
        }
    
    # ==================== PHOTO AND TEAM LOOKUP ====================
    
    async def get_photo_and_team_from_roster(self, player_name: str) -> Optional[Dict]:
        """
        Look up a player's photo_url AND team from master roster with fuzzy matching.
        
        Returns dict with photo_url, team_abbreviation, nba_com_id or None if not found.
        """
        if not player_name:
            return None
        
        normalized = sanitize_player_name(player_name)
        
        # Try exact normalized match first
        doc = await self.master_roster.find_one(
            {"normalized_name": normalized},
            {"_id": 0, "photo_url": 1, "team_abbreviation": 1, "nba_com_id": 1, "player_name": 1}
        )
        
        if doc:
            return doc
        
        # Remove common suffixes for matching
        name_without_suffix = player_name
        for suffix in [" Jr.", " Jr", " III", " II", " IV", " Sr.", " Sr"]:
            if player_name.endswith(suffix):
                name_without_suffix = player_name[:-len(suffix)]
                break
        
        # Also remove periods from initials (G.G. -> GG)
        name_cleaned = name_without_suffix.replace(".", "")
        
        # Try matching without suffix
        if name_without_suffix != player_name:
            normalized_no_suffix = sanitize_player_name(name_without_suffix)
            doc = await self.master_roster.find_one(
                {"normalized_name": normalized_no_suffix},
                {"_id": 0, "photo_url": 1, "team_abbreviation": 1, "nba_com_id": 1, "player_name": 1}
            )
            if doc:
                return doc
        
        # Try regex matching with BOTH first AND last name
        name_parts = name_cleaned.split()
        if len(name_parts) >= 2:
            first_name = name_parts[0]
            last_name = name_parts[-1]
            
            # Skip if last name is a suffix we missed
            if last_name.lower() in ["jr", "iii", "ii", "iv", "sr"]:
                last_name = name_parts[-2] if len(name_parts) > 2 else first_name
            
            # STRICT: Match must have EXACT last name at word boundary
            doc = await self.master_roster.find_one(
                {
                    "player_name": {
                        "$regex": f"^{first_name}.*\\b{last_name}\\b",
                        "$options": "i"
                    }
                },
                {"_id": 0, "photo_url": 1, "team_abbreviation": 1, "nba_com_id": 1, "player_name": 1}
            )
            
            if doc:
                return doc
            
            # Try nickname/initial expansions for first name ONLY if last name matches exactly
            first_name_variations = self._get_name_variations(first_name)
            for variation in first_name_variations:
                doc = await self.master_roster.find_one(
                    {
                        "player_name": {
                            "$regex": f"^{variation}.*\\b{last_name}\\b",
                            "$options": "i"
                        }
                    },
                    {"_id": 0, "photo_url": 1, "team_abbreviation": 1, "nba_com_id": 1, "player_name": 1}
                )
                if doc:
                    return doc
        
        return None
    
    def _get_name_variations(self, first_name: str) -> list:
        """Get common variations/expansions for a first name."""
        variations = []
        
        # Common nickname mappings
        nickname_map = {
            "gg": ["gregory", "george"],
            "jj": ["james", "john", "junior"],
            "tj": ["thomas", "timothy"],
            "pj": ["paul", "peter"],
            "cj": ["charles", "christopher"],
            "aj": ["anthony", "andrew"],
            "rj": ["robert", "richard"],
            "herb": ["herbert"],
            "mike": ["michael"],
            "chris": ["christopher"],
            "matt": ["matthew"],
            "dan": ["daniel"],
            "rob": ["robert"],
            "will": ["william"],
            "nick": ["nicholas"],
            "alex": ["alexander"],
        }
        
        # Check for nickname expansion
        name_lower = first_name.lower().replace(".", "")
        if name_lower in nickname_map:
            variations.extend(nickname_map[name_lower])
        
        return variations
