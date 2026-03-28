"""
NBA MASTER HUB 2026
===================

SINGLE SOURCE OF TRUTH (SSOT) for all NBA player data.

This is THE ONLY location for player information in the entire system.
All components MUST use fetchPlayerIntel() to access player data.

Structure:
- One entry per active player on 2025-26 NBA roster
- Fields: player_id, display_name, team, position, headshot_url, stats{}
- Daily sync at 4:00 AM ET overwrites with fresh API data

CONSTRAINT: Only fetchPlayerIntel() may read from this hub.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from motor.motor_asyncio import AsyncIOMotorClient
import aiohttp

logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================

DAILY_SYNC_TIME = "04:00"  # 4:00 AM ET
BALLDONTLIE_API_KEY = os.environ.get("BALLDONTLIE_API_KEY", "")

# NOTE: BDL is the only stats source from this application. BDL is the only stats source.

# NBA CDN headshot URL template
NBA_HEADSHOT_URL = "https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_id}.png"
ESPN_HEADSHOT_URL = "https://a.espncdn.com/i/headshots/nba/players/full/{espn_id}.png"

# Patterns that indicate a team logo (NOT a player headshot)
TEAM_LOGO_PATTERNS = [
    "cdn.nba.com/logos/nba/",
    "/logos/nba/",
    "/global/L/logo",
    "/global/D/logo",
    "team-logos",
]


def is_team_logo_url(url: str) -> bool:
    """
    Check if a URL is a team logo instead of a player headshot.
    Team logos should never be used as player photo_url.
    """
    if not url:
        return False
    url_lower = url.lower()
    return any(pattern.lower() in url_lower for pattern in TEAM_LOGO_PATTERNS)


def sanitize_photo_url(photo_url: str, nba_id: int = None) -> str:
    """
    Sanitize photo URL - replace team logos with proper headshot proxy.
    
    Args:
        photo_url: The URL to sanitize
        nba_id: NBA.com player ID for constructing proxy URL
    
    Returns:
        A valid player headshot URL (proxy if original was a team logo)
    """
    # If it's a team logo and we have nba_id, use proxy
    if is_team_logo_url(photo_url) and nba_id:
        return f"/api/proxy/nba-headshot/{nba_id}"
    
    # If no photo_url but we have nba_id, use proxy
    if not photo_url and nba_id:
        return f"/api/proxy/nba-headshot/{nba_id}"
    
    return photo_url


# ==================== MASTER HUB CLASS ====================

class NBAMasterHub:
    """
    THE SINGLE SOURCE OF TRUTH for all NBA player data.
    
    CONSTRAINT: All player data access MUST go through fetchPlayerIntel().
    No other function in the codebase may directly query player data.
    """
    
    COLLECTION_NAME = "nba_master_hub_2026"
    
    def __init__(self, mongo_url: str, db_name: str):
        # MongoDB Atlas-compatible connection settings
        is_atlas = 'mongodb.net' in mongo_url or 'mongodb+srv' in mongo_url
        
        connection_opts = {
            'serverSelectionTimeoutMS': 30000,
            'connectTimeoutMS': 30000,
            'socketTimeoutMS': 60000,
            'maxPoolSize': 20,
            'retryWrites': True,
        }
        if is_atlas:
            connection_opts['tls'] = True
        
        self.client = AsyncIOMotorClient(mongo_url, **connection_opts)
        self.db = self.client[db_name]
        self.hub = self.db[self.COLLECTION_NAME]
        self._scheduler_running = False
        self._last_sync: Optional[datetime] = None
        
    # ==================== THE VALET FUNCTION ====================
    # This is THE ONLY function allowed to read from the Master Hub
    
    async def fetchPlayerIntel(self, player_id) -> Optional[Dict[str, Any]]:
        """
        THE MASTER VALET FUNCTION - STRICT BDL_ID BASED
        
        This is the ONLY function in the entire backend allowed to read 
        from NBA_MASTER_HUB_2026.
        
        Args:
            player_id: Player's bdl_id (integer) - STRICTLY NUMBER BASED
            
        Returns:
            Complete, clean player object with all fields
        """
        # Convert to int if string
        try:
            bdl_id = int(player_id)
        except (ValueError, TypeError):
            logger.warning(f"[MASTER HUB] Invalid bdl_id (must be number): {player_id}")
            return None
        
        player = await self.hub.find_one({"bdl_id": bdl_id}, {"_id": 0})
        
        if not player:
            logger.warning(f"[MASTER HUB] Player not found by bdl_id: {bdl_id}")
            return None
            
        return player
    
    async def fetchPlayerByName(self, display_name: str) -> Optional[Dict[str, Any]]:
        """
        Lookup player by display_name - returns bdl_id record.
        Use this only when you don't have the bdl_id yet.
        """
        player = await self.hub.find_one({
            "display_name": {"$regex": f"^{display_name}$", "$options": "i"}
        }, {"_id": 0})
        
        if not player:
            # Try normalized name
            from services.bdl_comprehensive_sync import _normalize_name
            normalized = _normalize_name(display_name)
            player = await self.hub.find_one({
                "normalized_name": normalized
            }, {"_id": 0})
        
        return player
    
    async def fetchPlayerIntelByName(self, display_name: str) -> Optional[Dict[str, Any]]:
        """Lookup player by name - delegates to fetchPlayerByName."""
        return await self.fetchPlayerByName(display_name)
    
    async def fetchMultiplePlayersIntel(self, bdl_ids: List[int]) -> List[Dict[str, Any]]:
        """Batch fetch multiple players by bdl_id."""
        players = []
        for bdl_id in bdl_ids:
            player = await self.fetchPlayerIntel(bdl_id)
            if player:
                players.append(player)
        return players
    
    async def searchPlayers(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search players by name."""
        cursor = self.hub.find(
            {"display_name": {"$regex": query, "$options": "i"}},
            {"_id": 0}
        ).limit(limit)
        return await cursor.to_list(length=limit)
    
    async def getAllActivePlayers(self) -> List[Dict[str, Any]]:
        """Get all players with bdl_id (all records are active BDL players)."""
        cursor = self.hub.find({"bdl_id": {"$exists": True}}, {"_id": 0})
        return await cursor.to_list(length=1000)
    
    # ==================== HUB STATISTICS ====================
    
    async def getHubStats(self) -> Dict[str, Any]:
        """Get hub statistics."""
        total_players = await self.hub.count_documents({})
        active_players = await self.hub.count_documents({"status": "active"})
        with_photos = await self.hub.count_documents({"headshot_url": {"$ne": None}})
        with_stats = await self.hub.count_documents({"stats": {"$exists": True}})
        
        return {
            "total_players": total_players,
            "active_players": active_players,
            "with_photos": with_photos,
            "with_stats": with_stats,
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "collection": self.COLLECTION_NAME
        }
    
    # ==================== DAILY SYNC PROTOCOL ====================
    
    async def runDailySync(self) -> Dict[str, Any]:
        """
        DAILY SYNC PROTOCOL (4:00 AM ET)
        
        Syncs player data from:
        1. BallDontLie API (BDL) - Season averages for ALL active players
        2. NBA.com API (nba_api) - Official L5/L10/L15/L20 pre-calculated stats
        
        This ensures every player has up-to-date stats from official sources.
        """
        logger.info("=" * 60)
        logger.info("[MASTER HUB] DAILY SYNC STARTING (BDL + NBA.com Protocol)")
        logger.info("=" * 60)
        
        sync_start = datetime.now(timezone.utc)
        results = {
            "started_at": sync_start.isoformat(),
            "players_synced": 0,
            "nba_enriched": 0,
            "players_removed": 0,
            "photos_updated": 0,
            "stats_updated": 0,
            "errors": []
        }
        
        try:
            # Use BDL Comprehensive Sync which includes NBA.com L5/L10 enrichment
            from services.bdl_comprehensive_sync import get_bdl_sync_service
            bdl_service = get_bdl_sync_service(self.hub.database)
            
            logger.info("[MASTER HUB] Step 1: Syncing ALL active players (BDL + NBA.com L5/L10)...")
            sync_result = await bdl_service.sync_all_active_players()
            
            results["players_synced"] = sync_result.get("success", 0)
            results["nba_enriched"] = sync_result.get("nba_enriched", 0)
            results["players_removed"] = 0
            
            if sync_result.get("failed", 0) > 0:
                results["errors"].append(f"{sync_result['failed']} players failed to sync")
            
            results["success"] = True
            self._last_sync = sync_start
            
            logger.info(f"[MASTER HUB] Sync complete: {results['players_synced']} synced, {results['nba_enriched']} enriched with NBA.com L5/L10")
            
        except Exception as e:
            logger.error(f"[MASTER HUB] Daily sync error: {e}")
            results["success"] = False
            results["errors"].append(str(e))
        
        results["completed_at"] = datetime.now(timezone.utc).isoformat()
        results["duration"] = (datetime.now(timezone.utc) - sync_start).total_seconds()
        
        logger.info(f"[MASTER HUB] Daily sync complete in {results['duration']:.1f}s")
        logger.info("=" * 60)
        
        return results
    
    async def _enrichPhotosFromBDL(self) -> int:
        """
        Enrich hub players with photos using NBA CDN proxy URLs.
        
        NOTE: BDL is the only stats source. Photos are now constructed from nba_id
        using the /api/proxy/nba-headshot/{nba_id} pattern.
        """
        photos_added = 0
        
        try:
            # Get players without photos but with nba_id
            cursor = self.hub.find({
                "$and": [
                    {"nba_id": {"$exists": True, "$ne": None}},
                    {"$or": [
                        {"photo_url": None},
                        {"photo_url": ""},
                        {"photo_url": {"$exists": False}},
                        {"headshot_url": None},
                        {"headshot_url": ""},
                        {"headshot_url": {"$exists": False}}
                    ]}
                ]
            }, {"player_id": 1, "display_name": 1, "nba_id": 1})
            players_without_photos = await cursor.to_list(length=2000)
            logger.info(f"[MASTER HUB] Found {len(players_without_photos)} players without photos")
            
            # Construct photo URLs from nba_id
            for player in players_without_photos:
                nba_id = player.get("nba_id")
                if nba_id:
                    photo_url = f"/api/proxy/nba-headshot/{nba_id}"
                    await self.hub.update_one(
                        {"player_id": player["player_id"]},
                        {"$set": {
                            "headshot_url": photo_url,
                            "photo_url": photo_url
                        }}
                    )
                    photos_added += 1
            
            logger.info(f"[MASTER HUB] Enriched {photos_added} players with photos from nba_id")
            
        except Exception as e:
            logger.warning(f"[MASTER HUB] Photo enrichment error: {e}")
        
        return photos_added
    
    async def _fetchActiveRosterFromBDL(self) -> List[Dict[str, Any]]:
        """
        NOTE: BDL is the only stats source. Active roster is now maintained via BDL sync.
        
        This method is kept for compatibility but the roster is populated by
        the BDL comprehensive sync service (bdl_comprehensive_sync.py).
        
        Returns empty list - roster comes from BDL sync.
        """
        logger.info("[MASTER HUB] Roster fetch skipped - using BDL sync instead")
        return []
    
    def _buildPlayerEntryFromBDL(self, bdl_player: Dict) -> Optional[Dict[str, Any]]:
        """Build a clean player entry from BDL player data."""
        try:
            bdl_id = bdl_player.get("id")
            if not bdl_id:
                return None
            
            # Get names
            first_name = bdl_player.get("first_name", "")
            last_name = bdl_player.get("last_name", "")
            display_name = f"{first_name} {last_name}".strip()
            
            # Get team
            team_data = bdl_player.get("team", {})
            team = team_data.get("abbreviation") if isinstance(team_data, dict) else None
            
            # Get position
            position = bdl_player.get("position")
            
            # Build photo URL from nba_id if available, otherwise use bdl_id proxy
            nba_id = bdl_player.get("nba_id")
            if nba_id:
                photo_url = f"/api/proxy/nba-headshot/{nba_id}"
            else:
                photo_url = None
            
            return {
                "player_id": str(bdl_id),
                "bdl_id": bdl_id,
                "nba_id": nba_id,
                "display_name": display_name,
                "team": team,
                "position": position,
                "jersey": bdl_player.get("jersey_number"),
                "headshot_url": photo_url,
                "photo_url": photo_url,
                "height": bdl_player.get("height"),
                "weight": bdl_player.get("weight"),
                "college": bdl_player.get("college"),
                "country": bdl_player.get("country"),
                "draft_year": bdl_player.get("draft_year"),
                "draft_round": bdl_player.get("draft_round"),
                "draft_number": bdl_player.get("draft_number"),
                "stats": {},
                "status": "active"
            }
        except Exception as e:
            logger.warning(f"[MASTER HUB] Error building player entry: {e}")
            return None
    
    async def _fetchPlayerStats(self, player_id: str) -> Dict[str, Any]:
        """
        Fetch stats for a single player.
        
        NOTE: BDL is the only stats source. Stats come from BDL sync service.
        This method returns empty stats - actual stats are populated by
        bdl_stats_calculator.py and bdl_comprehensive_sync.py
        """
        # Stats are populated by BDL sync, not fetched here
        return {
            "season_avg": {},
            "l10_games": [],
            "fatigue_data": {}
        }
    
    # ==================== SCHEDULER ====================
    
    async def startDailyScheduler(self):
        """Start the 4:00 AM ET daily sync scheduler."""
        if self._scheduler_running:
            logger.warning("[MASTER HUB] Scheduler already running")
            return
        
        self._scheduler_running = True
        logger.info("[MASTER HUB] Daily sync scheduler started (4:00 AM ET)")
        
        asyncio.create_task(self._schedulerLoop())
    
    async def _schedulerLoop(self):
        """Main scheduler loop."""
        from zoneinfo import ZoneInfo
        
        et_tz = ZoneInfo("America/New_York")
        
        while self._scheduler_running:
            try:
                now_et = datetime.now(et_tz)
                current_time = now_et.strftime("%H:%M")
                
                if current_time == DAILY_SYNC_TIME:
                    logger.info("[MASTER HUB] 4:00 AM ET - Running daily sync...")
                    await self.runDailySync()
                    await asyncio.sleep(60)  # Prevent re-trigger
                
                await asyncio.sleep(30)
                
            except Exception as e:
                logger.error(f"[MASTER HUB] Scheduler error: {e}")
                await asyncio.sleep(60)
    
    def stopScheduler(self):
        """Stop the scheduler."""
        self._scheduler_running = False
        logger.info("[MASTER HUB] Scheduler stopped")


# ==================== SINGLETON INSTANCE ====================

_master_hub: Optional[NBAMasterHub] = None

def get_master_hub() -> NBAMasterHub:
    """Get or create the NBAMasterHub singleton."""
    global _master_hub
    if _master_hub is None:
        mongo_url = os.environ.get("MONGO_URL")
        db_name = os.environ.get("DB_NAME", "pick_vision")
        _master_hub = NBAMasterHub(mongo_url, db_name)
    return _master_hub


# ==================== PUBLIC API ====================
# These are the ONLY functions external code should use

async def fetchPlayerIntel(player_id: str) -> Optional[Dict[str, Any]]:
    """
    THE VALET FUNCTION - Public API
    
    This is the ONLY way to access player data from the Master Hub.
    """
    hub = get_master_hub()
    return await hub.fetchPlayerIntel(player_id)


async def fetchPlayerIntelByName(display_name: str) -> Optional[Dict[str, Any]]:
    """Fetch player by display name."""
    hub = get_master_hub()
    return await hub.fetchPlayerIntelByName(display_name)


async def searchPlayers(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Search players by name."""
    hub = get_master_hub()
    return await hub.searchPlayers(query, limit)


async def getHubStats() -> Dict[str, Any]:
    """Get hub statistics."""
    hub = get_master_hub()
    return await hub.getHubStats()


async def runDailySync() -> Dict[str, Any]:
    """Manually trigger daily sync."""
    hub = get_master_hub()
    return await hub.runDailySync()
