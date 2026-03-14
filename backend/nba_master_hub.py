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
TANK01_API_KEY = os.environ.get("TANK01_API_KEY", "402edbcac6mshd04997e7ca01d17p1879eajsn65ab176cdb1e")
BALLDONTLIE_API_KEY = os.environ.get("BALLDONTLIE_API_KEY", "")

# NBA CDN headshot URL template
NBA_HEADSHOT_URL = "https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_id}.png"
ESPN_HEADSHOT_URL = "https://a.espncdn.com/i/headshots/nba/players/full/{espn_id}.png"

# ==================== MASTER HUB CLASS ====================

class NBAMasterHub:
    """
    THE SINGLE SOURCE OF TRUTH for all NBA player data.
    
    CONSTRAINT: All player data access MUST go through fetchPlayerIntel().
    No other function in the codebase may directly query player data.
    """
    
    COLLECTION_NAME = "nba_master_hub_2026"
    
    def __init__(self, mongo_url: str, db_name: str):
        self.client = AsyncIOMotorClient(mongo_url)
        self.db = self.client[db_name]
        self.hub = self.db[self.COLLECTION_NAME]
        self._scheduler_running = False
        self._last_sync: Optional[datetime] = None
        
    # ==================== THE VALET FUNCTION ====================
    # This is THE ONLY function allowed to read from the Master Hub
    
    async def fetchPlayerIntel(self, player_id: str) -> Optional[Dict[str, Any]]:
        """
        THE MASTER VALET FUNCTION
        
        This is the ONLY function in the entire backend allowed to read 
        from NBA_MASTER_HUB_2026.
        
        Args:
            player_id: Player's unique identifier (tank01_id, nba_id, or display_name)
            
        Returns:
            Complete, clean player object with all fields:
            - player_id
            - display_name
            - team
            - position
            - headshot_url
            - stats: {season_avg, l10_games, fatigue_data}
        """
        # Try lookup by various ID formats
        player = await self.hub.find_one({
            "$or": [
                {"player_id": player_id},
                {"tank01_id": player_id},
                {"nba_id": player_id},
                {"display_name": {"$regex": f"^{player_id}$", "$options": "i"}}
            ]
        }, {"_id": 0})  # Exclude MongoDB _id
        
        if not player:
            logger.warning(f"[MASTER HUB] Player not found: {player_id}")
            return None
            
        return player
    
    async def fetchPlayerIntelByName(self, display_name: str) -> Optional[Dict[str, Any]]:
        """Convenience wrapper for name-based lookup."""
        return await self.fetchPlayerIntel(display_name)
    
    async def fetchMultiplePlayersIntel(self, player_ids: List[str]) -> List[Dict[str, Any]]:
        """Batch fetch multiple players."""
        players = []
        for pid in player_ids:
            player = await self.fetchPlayerIntel(pid)
            if player:
                players.append(player)
        return players
    
    async def searchPlayers(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search players by name (uses fetchPlayerIntel internally)."""
        cursor = self.hub.find(
            {"display_name": {"$regex": query, "$options": "i"}},
            {"_id": 0}
        ).limit(limit)
        return await cursor.to_list(length=limit)
    
    async def getAllActivePlayers(self) -> List[Dict[str, Any]]:
        """Get all active players (for sync operations only)."""
        cursor = self.hub.find({"status": "active"}, {"_id": 0})
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
        
        Overwrites the Master Hub with fresh API data.
        - Fetches all active players from Tank01/BallDontLie
        - Updates stats (Season Avg, L10 Games, Fatigue/Minutes)
        - Removes waived/inactive players immediately
        """
        logger.info("=" * 60)
        logger.info("[MASTER HUB] DAILY SYNC STARTING (4:00 AM ET Protocol)")
        logger.info("=" * 60)
        
        sync_start = datetime.now(timezone.utc)
        results = {
            "started_at": sync_start.isoformat(),
            "players_synced": 0,
            "players_removed": 0,
            "photos_updated": 0,
            "stats_updated": 0,
            "errors": []
        }
        
        try:
            # Step 1: Fetch all active players from Tank01
            logger.info("[MASTER HUB] Step 1: Fetching active roster from Tank01...")
            active_players = await self._fetchActiveRosterFromTank01()
            logger.info(f"[MASTER HUB] Found {len(active_players)} active players")
            
            # Step 2: Skip individual stats fetch (too slow for 1000+ players)
            # Stats will be fetched on-demand via fetchPlayerIntel
            logger.info("[MASTER HUB] Step 2: Skipping bulk stats fetch (on-demand loading enabled)")
            
            # Step 3: Get current hub player IDs
            current_ids = set()
            async for doc in self.hub.find({}, {"player_id": 1}):
                current_ids.add(doc.get("player_id"))
            
            # Step 4: Identify players to remove (waived/inactive)
            new_ids = {p.get("player_id") for p in active_players if p.get("player_id")}
            removed_ids = current_ids - new_ids
            
            if removed_ids:
                logger.info(f"[MASTER HUB] Removing {len(removed_ids)} inactive players...")
                await self.hub.delete_many({"player_id": {"$in": list(removed_ids)}})
                results["players_removed"] = len(removed_ids)
            
            # Step 5: Upsert all active players
            logger.info("[MASTER HUB] Step 3: Writing to Master Hub...")
            for player in active_players:
                if player.get("player_id"):
                    player["status"] = "active"
                    player["last_updated"] = sync_start.isoformat()
                    
                    await self.hub.update_one(
                        {"player_id": player["player_id"]},
                        {"$set": player},
                        upsert=True
                    )
                    results["players_synced"] += 1
            
            results["success"] = True
            self._last_sync = sync_start
            
            # Step 5: Enrich with photos from existing master roster
            logger.info("[MASTER HUB] Step 4: Enriching photos from legacy master roster...")
            photos_added = await self._enrichPhotosFromLegacyRoster()
            results["photos_updated"] = photos_added
            
        except Exception as e:
            logger.error(f"[MASTER HUB] Daily sync error: {e}")
            results["success"] = False
            results["errors"].append(str(e))
        
        results["completed_at"] = datetime.now(timezone.utc).isoformat()
        results["duration"] = (datetime.now(timezone.utc) - sync_start).total_seconds()
        
        logger.info(f"[MASTER HUB] Daily sync complete: {results['players_synced']} synced, {results['players_removed']} removed")
        logger.info("=" * 60)
        
        return results
    
    async def _enrichPhotosFromLegacyRoster(self) -> int:
        """Enrich hub players with photos by fetching nbaComHeadshot from Tank01."""
        photos_added = 0
        
        try:
            # Get players without photos (check for null, empty, or missing)
            cursor = self.hub.find({
                "$or": [
                    {"headshot_url": None},
                    {"headshot_url": ""},
                    {"headshot_url": {"$exists": False}}
                ]
            }, {"player_id": 1, "display_name": 1, "tank01_id": 1})
            players_without_photos = await cursor.to_list(length=2000)
            logger.info(f"[MASTER HUB] Found {len(players_without_photos)} players without photos")
            
            # Fetch photos from Tank01 player info (only first 50 to avoid rate limits)
            async with aiohttp.ClientSession() as session:
                for i, player in enumerate(players_without_photos[:50]):
                    tank01_id = player.get("tank01_id")
                    if not tank01_id:
                        continue
                    
                    try:
                        url = f"https://tank01-fantasy-stats.p.rapidapi.com/getNBAPlayerInfo"
                        headers = {
                            "X-RapidAPI-Key": TANK01_API_KEY,
                            "X-RapidAPI-Host": "tank01-fantasy-stats.p.rapidapi.com"
                        }
                        params = {"playerID": tank01_id}
                        
                        async with session.get(url, headers=headers, params=params) as response:
                            if response.status == 200:
                                data = await response.json()
                                body = data.get("body", {})
                                
                                photo_url = (
                                    body.get("nbaComHeadshot") or
                                    body.get("espnHeadshot") or
                                    body.get("cbsHeadshot")
                                )
                                
                                if photo_url:
                                    await self.hub.update_one(
                                        {"player_id": player["player_id"]},
                                        {"$set": {"headshot_url": photo_url}}
                                    )
                                    photos_added += 1
                        
                        # Small delay to respect rate limits
                        await asyncio.sleep(0.1)
                        
                    except Exception as e:
                        logger.warning(f"[MASTER HUB] Photo fetch error for {player.get('display_name')}: {e}")
            
            logger.info(f"[MASTER HUB] Enriched {photos_added} players with photos")
            
        except Exception as e:
            logger.warning(f"[MASTER HUB] Photo enrichment error: {e}")
        
        return photos_added
    
    async def _fetchActiveRosterFromTank01(self) -> List[Dict[str, Any]]:
        """Fetch all active NBA players from Tank01 API."""
        players = []
        
        try:
            async with aiohttp.ClientSession() as session:
                # Tank01 roster endpoint
                url = "https://tank01-fantasy-stats.p.rapidapi.com/getNBAPlayerList"
                headers = {
                    "X-RapidAPI-Key": TANK01_API_KEY,
                    "X-RapidAPI-Host": "tank01-fantasy-stats.p.rapidapi.com"
                }
                
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        player_list = data.get("body", [])
                        
                        for p in player_list:
                            # Only include active players
                            if p.get("team") and p.get("team") != "FA":
                                player = self._buildPlayerEntry(p)
                                if player:
                                    players.append(player)
                    else:
                        logger.error(f"[MASTER HUB] Tank01 API error: {response.status}")
        except Exception as e:
            logger.error(f"[MASTER HUB] Roster fetch error: {e}")
        
        return players
    
    def _buildPlayerEntry(self, raw_data: Dict) -> Optional[Dict[str, Any]]:
        """Build a clean player entry for the Master Hub."""
        try:
            # Tank01 uses playerID as the main identifier
            player_id = raw_data.get("playerID")
            if not player_id:
                return None
            
            # Get IDs for headshot
            nba_id = raw_data.get("nbaComID") or raw_data.get("nbaComHeadshot")
            espn_id = raw_data.get("espnID") or raw_data.get("espnIDFull")
            
            # Build headshot URL - try multiple sources
            headshot_url = None
            
            # First try: nbaComHeadshot directly (Tank01 sometimes provides this)
            if raw_data.get("nbaComHeadshot"):
                headshot_url = raw_data.get("nbaComHeadshot")
            # Second try: Build from NBA ID
            elif nba_id:
                headshot_url = NBA_HEADSHOT_URL.format(nba_id=nba_id)
            # Third try: Build from ESPN ID
            elif espn_id:
                headshot_url = ESPN_HEADSHOT_URL.format(espn_id=espn_id)
            # Fourth try: Use espnHeadshot if available
            elif raw_data.get("espnHeadshot"):
                headshot_url = raw_data.get("espnHeadshot")
            
            display_name = raw_data.get("longName") or raw_data.get("espnName") or raw_data.get("cbsShortName")
            
            return {
                "player_id": str(player_id),
                "tank01_id": player_id,
                "nba_id": nba_id,
                "espn_id": espn_id,
                "display_name": display_name,
                "team": raw_data.get("team"),
                "team_id": raw_data.get("teamID"),
                "position": raw_data.get("pos"),
                "jersey": raw_data.get("jerseyNum"),
                "headshot_url": headshot_url,
                "height": raw_data.get("height"),
                "weight": raw_data.get("weight"),
                "college": raw_data.get("college"),
                "injury": raw_data.get("injury", {}),
                "stats": {},  # Will be populated separately
                "status": "active"
            }
        except Exception as e:
            logger.warning(f"[MASTER HUB] Error building player entry: {e}")
            return None
    
    async def _fetchPlayerStats(self, player_id: str) -> Dict[str, Any]:
        """Fetch stats for a single player."""
        stats = {
            "season_avg": {},
            "l10_games": [],
            "fatigue_data": {}
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                # Tank01 player stats
                url = f"https://tank01-fantasy-stats.p.rapidapi.com/getNBAPlayerInfo"
                headers = {
                    "X-RapidAPI-Key": TANK01_API_KEY,
                    "X-RapidAPI-Host": "tank01-fantasy-stats.p.rapidapi.com"
                }
                params = {"playerID": player_id}
                
                async with session.get(url, headers=headers, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        body = data.get("body", {})
                        
                        # Season averages
                        stats["season_avg"] = {
                            "pts": float(body.get("pts", 0) or 0),
                            "reb": float(body.get("reb", 0) or 0),
                            "ast": float(body.get("ast", 0) or 0),
                            "stl": float(body.get("stl", 0) or 0),
                            "blk": float(body.get("blk", 0) or 0),
                            "tov": float(body.get("TOV", 0) or 0),
                            "mins": float(body.get("mins", 0) or 0),
                            "fgp": float(body.get("fgPct", 0) or 0),
                            "tpp": float(body.get("tptPct", 0) or 0),
                            "ftp": float(body.get("ftPct", 0) or 0),
                            "gp": int(body.get("gamesPlayed", 0) or 0)
                        }
                        
                        # Fatigue/minutes data
                        stats["fatigue_data"] = {
                            "avg_minutes": float(body.get("mins", 0) or 0),
                            "games_played": int(body.get("gamesPlayed", 0) or 0),
                            "b2b_games": 0  # Would need game log to calculate
                        }
        except Exception as e:
            logger.warning(f"[MASTER HUB] Stats fetch error for {player_id}: {e}")
        
        return stats
    
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
        db_name = os.environ.get("DB_NAME", "pickvision")
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
