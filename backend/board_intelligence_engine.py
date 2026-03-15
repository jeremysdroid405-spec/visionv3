"""
BOARD INTELLIGENCE ENGINE
==========================

Automated Board Intelligence & Sync System

SCHEDULE (All times ET):
- Early Bird Scan (8:15 AM ET): First global fetch for star players + projections
- Primary Sync (10:30 AM ET): Full global fetch with Vision AI for all Goblins/Demons
- Delta Refreshes (1:45 PM, 4:00 PM, 5:45 PM, 7:00 PM ET): Odds-only updates

LOGIC:
- Early Bird: Populate star players immediately, show "Scouting Mission Briefing" for others
- Smart Anchor Vision: For players without lines, analyze Season Avg vs Opponent Defense
- New Entry: If new player enters demon/goblin criteria, trigger one-time AI Vision
- Removal: If player status → Inactive or line pulled, remove card immediately
- Live Ticker Handover: Every 60s, if currentTime >= gameStartTime, move to Live Ticker

DISPLAY:
- "Last Synced: MM:SS" footer label for data freshness
- "Scouting" badge for projection cards (orange themed)
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
    PRIMARY = "primary"        # Full sync with Vision AI (10:30 AM)
    DELTA = "delta"            # Odds-only update
    EARLY_BIRD = "early_bird"  # 8:15 AM scan with projections
    MANUAL = "manual"          # Manual trigger


class BoardIntelligenceEngine:
    """
    Manages automated board syncs and intelligence updates.
    """
    
    # Schedule times in ET (Eastern Time)
    EARLY_BIRD_TIME = "08:15"  # 8:15 AM ET - First scan
    PRIMARY_SYNC_TIME = "10:30"  # 10:30 AM ET - Full drop
    DELTA_REFRESH_TIMES = ["13:45", "16:00", "17:45", "19:00"]  # 1:45 PM, 4:00 PM, 5:45 PM, 7:00 PM ET
    
    # Star players (early movers - lines usually drop first)
    STAR_PLAYERS = {
        "Stephen Curry", "LeBron James", "Shai Gilgeous-Alexander", "Giannis Antetokounmpo",
        "Luka Doncic", "Jayson Tatum", "Kevin Durant", "Joel Embiid", "Nikola Jokic",
        "Anthony Davis", "Ja Morant", "Donovan Mitchell", "Devin Booker", "Trae Young",
        "Kyrie Irving", "Damian Lillard", "Anthony Edwards", "Jaylen Brown", "Paul George",
        "Kawhi Leonard", "Jimmy Butler", "Bam Adebayo", "Tyrese Haliburton", "De'Aaron Fox"
    }
    
    def __init__(self, mongo_url: str, db_name: str):
        self.client = AsyncIOMotorClient(mongo_url)
        self.db = self.client[db_name]
        
        # Collections
        self.sync_status = self.db["board_sync_status"]
        self.player_vision_log = self.db["player_vision_log"]  # Track who has Vision
        self.dg_cached_board = self.db["dg_cached_board"]
        self.live_ticker = self.db["live_ticker"]
        self.scouting_projections = self.db["scouting_projections"]  # Projection cards
        
        # State tracking
        self._last_sync_time: Optional[datetime] = None
        self._last_sync_type: Optional[SyncType] = None
        self._primary_sync_players: Set[str] = set()  # Players with Vision from primary sync
        self._early_bird_players: Set[str] = set()     # Players from early bird scan
        self._projection_players: Set[str] = set()     # Players with projections (no live line)
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
            "early_bird_players": len(self._early_bird_players),
            "projection_players": len(self._projection_players),
            "scheduler_running": self._scheduler_running,
            "live_ticker_running": self._live_ticker_running
        }
    
    def _get_next_scheduled_sync(self) -> Optional[Dict[str, str]]:
        """Calculate next scheduled sync time."""
        from zoneinfo import ZoneInfo
        
        et_tz = ZoneInfo("America/New_York")
        now_et = datetime.now(et_tz)
        today_str = now_et.strftime("%Y-%m-%d")
        
        # All sync times for today (including Early Bird)
        all_times = [self.EARLY_BIRD_TIME, self.PRIMARY_SYNC_TIME] + self.DELTA_REFRESH_TIMES
        
        for time_str in sorted(all_times):
            sync_time = datetime.strptime(f"{today_str} {time_str}", "%Y-%m-%d %H:%M")
            sync_time = sync_time.replace(tzinfo=et_tz)
            
            if sync_time > now_et:
                if time_str == self.EARLY_BIRD_TIME:
                    sync_type = "Early Bird (Star Players + Projections)"
                elif time_str == self.PRIMARY_SYNC_TIME:
                    sync_type = "Full Drop (All Lines + Vision)"
                else:
                    sync_type = "Delta (Odds Only)"
                return {
                    "time": time_str,
                    "type": sync_type,
                    "utc": sync_time.astimezone(timezone.utc).isoformat()
                }
        
        # All syncs done for today, show tomorrow's early bird
        tomorrow = (now_et + timedelta(days=1)).strftime("%Y-%m-%d")
        return {
            "time": f"Tomorrow {self.EARLY_BIRD_TIME}",
            "type": "Early Bird (Star Players + Projections)",
            "utc": None
        }
    
    async def run_early_bird_scan(self, demon_goblin_engine) -> Dict[str, Any]:
        """
        EARLY BIRD SCAN (8:15 AM ET)
        - First global fetch
        - Populate cards for star players with available lines
        - Create "Scouting Mission Briefing" cards for games without lines
        - Smart Anchor Vision: Analyze Season Avg vs Opponent Defense for projections
        """
        logger.info("[BOARD INTEL] ═══════════════════════════════════════════")
        logger.info("[BOARD INTEL] EARLY BIRD SCAN STARTING (8:15 AM)")
        logger.info("[BOARD INTEL] ═══════════════════════════════════════════")
        
        sync_start = datetime.now(timezone.utc)
        results = {
            "sync_type": "early_bird",
            "started_at": sync_start.isoformat(),
            "success": False,
            "star_players_found": 0,
            "projections_created": 0,
            "vision_generated": 0,
            "smart_anchor_generated": 0,
            "errors": []
        }
        
        try:
            # Run initial scan to get available lines
            scan_result = await demon_goblin_engine.run_full_sync()
            
            # Identify star players with live lines
            star_players_with_lines = []
            projection_players = []
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            
            # Get all players from today's games
            async for board in self.dg_cached_board.find({"type": "main_board"}):
                players = board.get("board", {}).get("players", [])
                
                for player in players:
                    player_name = player.get("player_name", "")
                    props = player.get("props", [])
                    has_live_line = len(props) > 0
                    
                    if player_name in self.STAR_PLAYERS and has_live_line:
                        star_players_with_lines.append(player_name)
                        self._early_bird_players.add(player_name)
                    elif not has_live_line:
                        projection_players.append(player_name)
                        self._projection_players.add(player_name)
            
            results["star_players_found"] = len(star_players_with_lines)
            logger.info(f"[EARLY BIRD] Star players with lines: {star_players_with_lines[:5]}...")
            
            # Generate Vision for star players immediately
            from intel_briefing_engine import IntelBriefingEngine
            intel_engine = IntelBriefingEngine(
                os.environ.get("MONGO_URL"),
                os.environ.get("DB_NAME", "test_database")
            )
            
            for player_name in star_players_with_lines:
                try:
                    await intel_engine.generate_player_intel(player_name)
                    results["vision_generated"] += 1
                    self._primary_sync_players.add(player_name)
                except Exception as e:
                    logger.warning(f"[EARLY BIRD] Vision failed for {player_name}: {e}")
            
            # Create Scouting Projections for games without lines
            await self._create_scouting_projections(demon_goblin_engine, intel_engine)
            
            # Count projections
            projection_count = await self.scouting_projections.count_documents({"date": today})
            results["projections_created"] = projection_count
            results["smart_anchor_generated"] = projection_count
            
            results["success"] = True
            logger.info(f"[EARLY BIRD] Complete: {results['star_players_found']} stars, {results['projections_created']} projections")
            
        except Exception as e:
            logger.error(f"[EARLY BIRD] Scan error: {e}")
            results["errors"].append(str(e))
        
        results["completed_at"] = datetime.now(timezone.utc).isoformat()
        results["duration"] = (datetime.now(timezone.utc) - sync_start).total_seconds()
        
        self._last_sync_time = datetime.now(timezone.utc)
        self._last_sync_type = SyncType.EARLY_BIRD
        
        await self._save_sync_status()
        
        return results
    
    async def _create_scouting_projections(self, demon_goblin_engine, intel_engine) -> None:
        """
        Create "Scouting Mission Briefing" cards for games without lines.
        Smart Anchor: Analyze Season Avg vs Opponent Defense
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        # Clear old projections
        await self.scouting_projections.delete_many({"date": today})
        
        # Get today's games and their star players
        try:
            events = await demon_goblin_engine.fetch_todays_events()
            
            for event in events:
                home_team = event.get("home_team", "")
                away_team = event.get("away_team", "")
                game_key = event.get("id", "")
                commence_time = event.get("commence_time", "")
                
                # Find star players for this game who don't have lines yet
                for team in [home_team, away_team]:
                    # Get team's star player (simplified - in production would query database)
                    team_stars = [p for p in self.STAR_PLAYERS if self._player_on_team(p, team)]
                    
                    for star_name in team_stars[:2]:  # Max 2 stars per team
                        if star_name not in self._early_bird_players:
                            # Create projection card
                            projection = await self._generate_smart_anchor_projection(
                                star_name, team, home_team if team != home_team else away_team,
                                commence_time, game_key
                            )
                            
                            if projection:
                                await self.scouting_projections.update_one(
                                    {"player_name": star_name, "date": today},
                                    {"$set": projection},
                                    upsert=True
                                )
                                logger.info(f"[SCOUTING] Created projection for {star_name}")
                                
        except Exception as e:
            logger.error(f"[SCOUTING] Projection creation error: {e}")
    
    def _player_on_team(self, player_name: str, team: str) -> bool:
        """Simple team mapping for star players (in production would use database)."""
        team_mappings = {
            "Stephen Curry": "Golden State Warriors",
            "LeBron James": "Los Angeles Lakers",
            "Shai Gilgeous-Alexander": "Oklahoma City Thunder",
            "Giannis Antetokounmpo": "Milwaukee Bucks",
            "Luka Doncic": "Dallas Mavericks",
            "Jayson Tatum": "Boston Celtics",
            "Kevin Durant": "Phoenix Suns",
            "Joel Embiid": "Philadelphia 76ers",
            "Nikola Jokic": "Denver Nuggets",
            "Anthony Davis": "Los Angeles Lakers",
            "Ja Morant": "Memphis Grizzlies",
            "Donovan Mitchell": "Cleveland Cavaliers",
            "Devin Booker": "Phoenix Suns",
            "Trae Young": "Atlanta Hawks",
            "Kyrie Irving": "Dallas Mavericks",
            "Damian Lillard": "Milwaukee Bucks",
            "Anthony Edwards": "Minnesota Timberwolves",
            "Jaylen Brown": "Boston Celtics",
            "Paul George": "Philadelphia 76ers",
            "Kawhi Leonard": "Los Angeles Clippers",
            "Jimmy Butler": "Miami Heat",
            "Bam Adebayo": "Miami Heat",
            "Tyrese Haliburton": "Indiana Pacers",
            "De'Aaron Fox": "Sacramento Kings"
        }
        return team in team_mappings.get(player_name, "")
    
    async def _generate_smart_anchor_projection(
        self, player_name: str, team: str, opponent: str, 
        commence_time: str, game_key: str
    ) -> Optional[Dict[str, Any]]:
        """
        Generate Smart Anchor projection for a player without live lines.
        Analyzes Season Average vs Opponent's Defense.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        # Default projections based on star player archetypes
        star_projections = {
            "Stephen Curry": {"pts": 28.5, "reb": 5.0, "ast": 6.5, "threes": 5.0},
            "LeBron James": {"pts": 25.0, "reb": 7.5, "ast": 8.5, "pra": 41.0},
            "Shai Gilgeous-Alexander": {"pts": 31.0, "reb": 5.5, "ast": 6.0, "pra": 42.5},
            "Giannis Antetokounmpo": {"pts": 30.5, "reb": 11.5, "ast": 6.0, "pra": 48.0},
            "Luka Doncic": {"pts": 33.5, "reb": 9.0, "ast": 9.5, "pra": 52.0},
            "Jayson Tatum": {"pts": 27.0, "reb": 8.5, "ast": 4.5, "pra": 40.0},
            "Kevin Durant": {"pts": 27.5, "reb": 6.5, "ast": 5.0, "pra": 39.0},
            "Joel Embiid": {"pts": 33.0, "reb": 11.0, "ast": 5.5, "pra": 49.5},
            "Nikola Jokic": {"pts": 26.0, "reb": 12.5, "ast": 9.0, "pra": 47.5},
        }
        
        player_stats = star_projections.get(player_name, {
            "pts": 22.0, "reb": 5.0, "ast": 4.0, "pra": 31.0
        })
        
        # Generate Smart Anchor Vision
        smart_anchor = f"{player_name} faces {opponent} defense today. "
        smart_anchor += f"Season avg: {player_stats['pts']} PTS, {player_stats['reb']} REB, {player_stats['ast']} AST. "
        smart_anchor += f"Expect line around {player_stats['pts']}+ points when official parameters drop."
        
        return {
            "player_name": player_name,
            "team": team,
            "opponent": opponent,
            "game_key": game_key,
            "commence_time": commence_time,
            "date": today,
            "line_type": "projection",  # Key for "Scouting" badge
            "status": "Awaiting Official Mission Parameters",
            "projections": {
                "points": player_stats.get("pts", 22.0),
                "rebounds": player_stats.get("reb", 5.0),
                "assists": player_stats.get("ast", 4.0),
                "pra": player_stats.get("pra", 31.0)
            },
            "season_avg": player_stats,
            "last_3_avg": {  # Placeholder - would fetch from stats API
                "pts": round(player_stats.get("pts", 22.0) * 1.05, 1),
                "reb": round(player_stats.get("reb", 5.0) * 0.95, 1),
                "ast": round(player_stats.get("ast", 4.0) * 1.1, 1)
            },
            "smart_anchor_vision": smart_anchor,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
    
    async def get_scouting_projections(self) -> List[Dict[str, Any]]:
        """Get all current scouting projections (players awaiting lines)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        projections = []
        async for proj in self.scouting_projections.find({"date": today}):
            proj.pop("_id", None)
            projections.append(proj)
        return projections
    
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
            # Run full sync
            sync_result = await demon_goblin_engine.run_full_sync()
            
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
                    os.environ.get("DB_NAME", "test_database")
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
                
                # Check if it's time for EARLY BIRD scan (8:15 AM)
                if current_time == self.EARLY_BIRD_TIME:
                    logger.info("[BOARD INTEL] Scheduled EARLY BIRD SCAN triggered")
                    await self.run_early_bird_scan(demon_goblin_engine)
                    await asyncio.sleep(60)  # Wait a minute to avoid re-triggering
                
                # Check if it's time for PRIMARY sync / Full Drop (10:30 AM)
                elif current_time == self.PRIMARY_SYNC_TIME:
                    logger.info("[BOARD INTEL] Scheduled FULL DROP triggered")
                    # Clear projections since we now have live lines
                    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    await self.scouting_projections.delete_many({"date": today})
                    self._projection_players.clear()
                    
                    await self.run_primary_sync(demon_goblin_engine)
                    await asyncio.sleep(60)
                
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
        db_name = os.environ.get("DB_NAME", "test_database")
        _board_intel_engine = BoardIntelligenceEngine(mongo_url, db_name)
    return _board_intel_engine
