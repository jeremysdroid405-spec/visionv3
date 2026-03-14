"""
BOARD INTELLIGENCE ENGINE
==========================

Automated Board Intelligence & Sync System

SCHEDULE (All times ET):
- Primary Sync (10:30 AM ET): Full global fetch with Vision AI for all Goblins/Demons
- Delta Refreshes (1:45 PM, 4:00 PM, 5:45 PM, 7:00 PM ET): Odds-only updates

LOGIC:
- New Entry: If new player enters demon/goblin criteria, trigger one-time AI Vision
- Removal: If player status → Inactive or line pulled, remove card immediately
- Live Ticker Handover: Every 60s, if currentTime >= gameStartTime, move to Live Ticker

DISPLAY:
- "Last Synced: MM:SS" footer label for data freshness
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set, Any
from motor.motor_asyncio import AsyncIOMotorClient
import os
from enum import Enum

logger = logging.getLogger(__name__)


class SyncType(Enum):
    PRIMARY = "primary"      # Full sync with Vision AI
    DELTA = "delta"          # Odds-only update
    MANUAL = "manual"        # Manual trigger


class BoardIntelligenceEngine:
    """
    Manages automated board syncs and intelligence updates.
    """
    
    # Schedule times in ET (Eastern Time)
    PRIMARY_SYNC_TIME = "10:30"  # 10:30 AM ET
    DELTA_REFRESH_TIMES = ["13:45", "16:00", "17:45", "19:00"]  # 1:45 PM, 4:00 PM, 5:45 PM, 7:00 PM ET
    
    def __init__(self, mongo_url: str, db_name: str):
        self.client = AsyncIOMotorClient(mongo_url)
        self.db = self.client[db_name]
        
        # Collections
        self.sync_status = self.db["board_sync_status"]
        self.player_vision_log = self.db["player_vision_log"]  # Track who has Vision
        self.dg_cached_board = self.db["dg_cached_board"]
        self.live_ticker = self.db["live_ticker"]
        
        # State tracking
        self._last_sync_time: Optional[datetime] = None
        self._last_sync_type: Optional[SyncType] = None
        self._primary_sync_players: Set[str] = set()  # Players with Vision from primary sync
        self._scheduler_running = False
        self._live_ticker_running = False
        
    async def initialize(self):
        """Initialize the engine and load state."""
        # Load last sync status
        status = await self.sync_status.find_one({"_id": "current"})
        if status:
            self._last_sync_time = datetime.fromisoformat(status.get("last_sync_time", "")) if status.get("last_sync_time") else None
            self._last_sync_type = SyncType(status.get("last_sync_type", "manual"))
            
        # Load players who have Vision from today's primary sync
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        vision_log = await self.player_vision_log.find_one({"date": today})
        if vision_log:
            self._primary_sync_players = set(vision_log.get("players_with_vision", []))
            
        logger.info(f"[BOARD INTEL] Initialized. Last sync: {self._last_sync_time}, Players with Vision: {len(self._primary_sync_players)}")
        
    async def get_sync_status(self) -> Dict[str, Any]:
        """Get current sync status for API/frontend."""
        now = datetime.now(timezone.utc)
        
        # Calculate time since last sync
        time_since_sync = None
        if self._last_sync_time:
            delta = now - self._last_sync_time
            minutes = int(delta.total_seconds() // 60)
            seconds = int(delta.total_seconds() % 60)
            time_since_sync = f"{minutes:02d}:{seconds:02d}"
        
        # Get next scheduled sync
        next_sync = self._get_next_scheduled_sync()
        
        return {
            "last_sync_time": self._last_sync_time.isoformat() if self._last_sync_time else None,
            "last_sync_type": self._last_sync_type.value if self._last_sync_type else None,
            "time_since_sync": time_since_sync,
            "time_since_sync_display": f"Last Synced: {time_since_sync}" if time_since_sync else "Not synced yet",
            "next_scheduled_sync": next_sync,
            "players_with_vision": len(self._primary_sync_players),
            "scheduler_running": self._scheduler_running,
            "live_ticker_running": self._live_ticker_running
        }
    
    def _get_next_scheduled_sync(self) -> Optional[Dict[str, str]]:
        """Calculate next scheduled sync time."""
        from zoneinfo import ZoneInfo
        
        et_tz = ZoneInfo("America/New_York")
        now_et = datetime.now(et_tz)
        today_str = now_et.strftime("%Y-%m-%d")
        
        # All sync times for today
        all_times = [self.PRIMARY_SYNC_TIME] + self.DELTA_REFRESH_TIMES
        
        for time_str in sorted(all_times):
            sync_time = datetime.strptime(f"{today_str} {time_str}", "%Y-%m-%d %H:%M")
            sync_time = sync_time.replace(tzinfo=et_tz)
            
            if sync_time > now_et:
                sync_type = "Primary (Full + Vision)" if time_str == self.PRIMARY_SYNC_TIME else "Delta (Odds Only)"
                return {
                    "time": time_str,
                    "type": sync_type,
                    "utc": sync_time.astimezone(timezone.utc).isoformat()
                }
        
        # All syncs done for today, show tomorrow's primary
        tomorrow = (now_et + timedelta(days=1)).strftime("%Y-%m-%d")
        return {
            "time": f"Tomorrow {self.PRIMARY_SYNC_TIME}",
            "type": "Primary (Full + Vision)",
            "utc": None
        }
    
    async def run_primary_sync(self, demon_goblin_engine) -> Dict[str, Any]:
        """
        PRIMARY SYNC (10:30 AM ET)
        - Full global fetch
        - Map all player lines
        - Trigger Vision Insight Engine for ALL Goblins and Demons
        """
        logger.info("[BOARD INTEL] ═══════════════════════════════════════════")
        logger.info("[BOARD INTEL] PRIMARY SYNC STARTING (Full + Vision AI)")
        logger.info("[BOARD INTEL] ═══════════════════════════════════════════")
        
        sync_start = datetime.now(timezone.utc)
        results = {
            "sync_type": "primary",
            "started_at": sync_start.isoformat(),
            "success": False,
            "players_processed": 0,
            "vision_generated": 0,
            "errors": []
        }
        
        try:
            # Run full sync with Vision generation
            sync_result = await demon_goblin_engine.run_full_sync(generate_vision=True)
            
            # Track players who got Vision
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            players_with_vision = []
            
            # Get all players from cached board
            async for player in self.dg_cached_board.find({"board.players": {"$exists": True}}):
                board = player.get("board", {})
                for p in board.get("players", []):
                    player_name = p.get("player_name")
                    if player_name:
                        players_with_vision.append(player_name)
            
            # Update Vision log
            await self.player_vision_log.update_one(
                {"date": today},
                {"$set": {
                    "date": today,
                    "players_with_vision": players_with_vision,
                    "primary_sync_time": sync_start.isoformat(),
                    "count": len(players_with_vision)
                }},
                upsert=True
            )
            
            self._primary_sync_players = set(players_with_vision)
            
            results["success"] = True
            results["players_processed"] = sync_result.get("players_count", 0)
            results["vision_generated"] = len(players_with_vision)
            
            logger.info(f"[BOARD INTEL] PRIMARY SYNC COMPLETE: {results['players_processed']} players, {results['vision_generated']} with Vision")
            
        except Exception as e:
            logger.error(f"[BOARD INTEL] Primary sync error: {e}")
            results["errors"].append(str(e))
        
        # Update sync status
        results["completed_at"] = datetime.now(timezone.utc).isoformat()
        results["duration"] = (datetime.now(timezone.utc) - sync_start).total_seconds()
        
        self._last_sync_time = datetime.now(timezone.utc)
        self._last_sync_type = SyncType.PRIMARY
        
        await self._save_sync_status()
        
        return results
    
    async def run_delta_refresh(self, demon_goblin_engine) -> Dict[str, Any]:
        """
        DELTA REFRESH (1:45 PM, 4:00 PM, 5:45 PM, 7:00 PM ET)
        - Odds-only update (line and price values)
        - NEW ENTRY: If new player enters demon/goblin criteria, trigger one-time Vision
        - REMOVAL: If player status → Inactive or line pulled, remove immediately
        """
        logger.info("[BOARD INTEL] ───────────────────────────────────────────")
        logger.info("[BOARD INTEL] DELTA REFRESH STARTING (Odds Only)")
        logger.info("[BOARD INTEL] ───────────────────────────────────────────")
        
        sync_start = datetime.now(timezone.utc)
        results = {
            "sync_type": "delta",
            "started_at": sync_start.isoformat(),
            "success": False,
            "lines_updated": 0,
            "new_entries": 0,
            "removed": 0,
            "new_vision_generated": 0,
            "errors": []
        }
        
        try:
            # Get current player set before refresh
            current_players = set()
            async for player in self.dg_cached_board.find({"board.players": {"$exists": True}}):
                board = player.get("board", {})
                for p in board.get("players", []):
                    player_name = p.get("player_name")
                    if player_name:
                        current_players.add(player_name)
            
            # Run delta sync (odds only, no full stats refresh)
            sync_result = await demon_goblin_engine.run_delta_sync()
            
            # Get new player set after refresh
            new_players = set()
            async for player in self.dg_cached_board.find({"board.players": {"$exists": True}}):
                board = player.get("board", {})
                for p in board.get("players", []):
                    player_name = p.get("player_name")
                    if player_name:
                        new_players.add(player_name)
            
            # Identify NEW entries (not in primary sync players)
            new_entries = new_players - self._primary_sync_players
            removed_entries = current_players - new_players
            
            results["new_entries"] = len(new_entries)
            results["removed"] = len(removed_entries)
            
            # Generate Vision for new entries only
            if new_entries:
                logger.info(f"[BOARD INTEL] New entries detected: {list(new_entries)[:5]}...")
                # Trigger Vision for new entries
                from intel_briefing_engine import IntelBriefingEngine
                intel_engine = IntelBriefingEngine(
                    os.environ.get("MONGO_URL"),
                    os.environ.get("DB_NAME", "pickvision")
                )
                
                for player_name in new_entries:
                    try:
                        await intel_engine.generate_player_intel(player_name)
                        results["new_vision_generated"] += 1
                        self._primary_sync_players.add(player_name)
                    except Exception as e:
                        logger.warning(f"[BOARD INTEL] Vision generation failed for {player_name}: {e}")
                
                # Update Vision log
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                await self.player_vision_log.update_one(
                    {"date": today},
                    {"$set": {"players_with_vision": list(self._primary_sync_players)}},
                    upsert=True
                )
            
            if removed_entries:
                logger.info(f"[BOARD INTEL] Removed entries: {list(removed_entries)[:5]}...")
            
            results["success"] = True
            results["lines_updated"] = sync_result.get("lines_updated", 0)
            
            logger.info(f"[BOARD INTEL] DELTA REFRESH COMPLETE: {results['lines_updated']} lines, {results['new_entries']} new, {results['removed']} removed")
            
        except Exception as e:
            logger.error(f"[BOARD INTEL] Delta refresh error: {e}")
            results["errors"].append(str(e))
        
        results["completed_at"] = datetime.now(timezone.utc).isoformat()
        results["duration"] = (datetime.now(timezone.utc) - sync_start).total_seconds()
        
        self._last_sync_time = datetime.now(timezone.utc)
        self._last_sync_type = SyncType.DELTA
        
        await self._save_sync_status()
        
        return results
    
    async def check_live_ticker_handover(self, game_lock_engine) -> Dict[str, Any]:
        """
        LIVE TICKER HANDOVER
        Every 60 seconds: if currentTime >= gameStartTime, move game to Live Ticker
        """
        results = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "games_moved_to_ticker": 0,
            "active_games": [],
            "locked_games": []
        }
        
        try:
            # Get games that just started (from game lock engine)
            lock_status = await game_lock_engine.check_game_starts()
            
            newly_locked = lock_status.get("newly_locked", [])
            
            for game in newly_locked:
                # Move to live ticker
                ticker_entry = {
                    "game_key": game.get("game_key"),
                    "home_team": game.get("home_team"),
                    "away_team": game.get("away_team"),
                    "commence_time": game.get("commence_time"),
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "status": "LIVE",
                    "score": {"home": 0, "away": 0}  # Will be updated by live scores engine
                }
                
                await self.live_ticker.update_one(
                    {"game_key": game.get("game_key")},
                    {"$set": ticker_entry},
                    upsert=True
                )
                
                results["games_moved_to_ticker"] += 1
                results["active_games"].append(game.get("game_key"))
            
            results["locked_games"] = lock_status.get("locked_games", [])
            
        except Exception as e:
            logger.error(f"[BOARD INTEL] Live ticker handover error: {e}")
            results["error"] = str(e)
        
        return results
    
    async def get_live_ticker_games(self) -> List[Dict[str, Any]]:
        """Get all games currently in the live ticker."""
        games = []
        async for game in self.live_ticker.find({"status": "LIVE"}):
            game.pop("_id", None)
            games.append(game)
        return games
    
    async def start_scheduler(self, demon_goblin_engine, game_lock_engine):
        """Start the background scheduler for automated syncs."""
        if self._scheduler_running:
            logger.warning("[BOARD INTEL] Scheduler already running")
            return
        
        self._scheduler_running = True
        logger.info("[BOARD INTEL] Starting automated scheduler...")
        
        asyncio.create_task(self._scheduler_loop(demon_goblin_engine, game_lock_engine))
        asyncio.create_task(self._live_ticker_loop(game_lock_engine))
    
    async def _scheduler_loop(self, demon_goblin_engine, game_lock_engine):
        """Main scheduler loop for timed syncs."""
        from zoneinfo import ZoneInfo
        
        et_tz = ZoneInfo("America/New_York")
        
        while self._scheduler_running:
            try:
                now_et = datetime.now(et_tz)
                current_time = now_et.strftime("%H:%M")
                
                # Check if it's time for primary sync
                if current_time == self.PRIMARY_SYNC_TIME:
                    logger.info("[BOARD INTEL] Scheduled PRIMARY SYNC triggered")
                    await self.run_primary_sync(demon_goblin_engine)
                    await asyncio.sleep(60)  # Wait a minute to avoid re-triggering
                
                # Check if it's time for delta refresh
                elif current_time in self.DELTA_REFRESH_TIMES:
                    logger.info("[BOARD INTEL] Scheduled DELTA REFRESH triggered")
                    await self.run_delta_refresh(demon_goblin_engine)
                    await asyncio.sleep(60)
                
                # Sleep for 30 seconds between checks
                await asyncio.sleep(30)
                
            except Exception as e:
                logger.error(f"[BOARD INTEL] Scheduler error: {e}")
                await asyncio.sleep(60)
    
    async def _live_ticker_loop(self, game_lock_engine):
        """Live ticker handover loop - runs every 60 seconds."""
        self._live_ticker_running = True
        
        while self._scheduler_running:
            try:
                await self.check_live_ticker_handover(game_lock_engine)
                await asyncio.sleep(60)  # Check every 60 seconds
            except Exception as e:
                logger.error(f"[BOARD INTEL] Live ticker loop error: {e}")
                await asyncio.sleep(60)
        
        self._live_ticker_running = False
    
    def stop_scheduler(self):
        """Stop the background scheduler."""
        self._scheduler_running = False
        logger.info("[BOARD INTEL] Scheduler stopped")
    
    async def _save_sync_status(self):
        """Save current sync status to database."""
        await self.sync_status.update_one(
            {"_id": "current"},
            {"$set": {
                "last_sync_time": self._last_sync_time.isoformat() if self._last_sync_time else None,
                "last_sync_type": self._last_sync_type.value if self._last_sync_type else None,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }},
            upsert=True
        )


# Singleton instance
_board_intel_engine: Optional[BoardIntelligenceEngine] = None


def get_board_intel_engine() -> BoardIntelligenceEngine:
    """Get or create the BoardIntelligenceEngine singleton."""
    global _board_intel_engine
    if _board_intel_engine is None:
        mongo_url = os.environ.get("MONGO_URL")
        db_name = os.environ.get("DB_NAME", "pickvision")
        _board_intel_engine = BoardIntelligenceEngine(mongo_url, db_name)
    return _board_intel_engine
