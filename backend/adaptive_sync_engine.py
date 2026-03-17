"""
ADAPTIVE SYNC ENGINE - Mission-Critical Polling System
========================================================
Implements intelligent polling based on game proximity:
- Standby (>6hrs): Refresh every 60 minutes
- Active (1-6hrs): Refresh every 10 minutes  
- Mission Critical (<60mins): Refresh every 60 seconds
- Post-Tip: Cease polling for that game

Also handles:
- Stale Intel detection and alerts
- Client-side protection (all reads from MongoDB)
- Last updated timestamps on all cached data
"""

import asyncio
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Set
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from enum import Enum
import httpx

logger = logging.getLogger(__name__)

# Polling intervals in seconds
class PollInterval(Enum):
    STANDBY = 3600       # 60 minutes (>6hrs to tip)
    ACTIVE = 600         # 10 minutes (1-6hrs to tip)
    MISSION_CRITICAL = 60  # 60 seconds (<60mins to tip)
    POST_TIP = None      # Stop polling

# Thresholds in hours
STANDBY_THRESHOLD = 6       # >6 hours = Standby
ACTIVE_THRESHOLD = 1        # 1-6 hours = Active
MISSION_CRITICAL_THRESHOLD = 1  # <1 hour = Mission Critical

# Stale data threshold
STALE_DATA_THRESHOLD_SECONDS = 300  # 5 minutes


class GameStatus(Enum):
    STANDBY = "standby"
    ACTIVE = "active"
    MISSION_CRITICAL = "mission_critical"
    POST_TIP = "post_tip"


class AdaptiveSyncEngine:
    """
    Mission-Critical Adaptive Sync Engine
    
    Manages polling frequency based on game start times to:
    1. Conserve API credits during quiet periods
    2. Maximize freshness during critical betting windows
    3. Track and display last_updated timestamps
    4. Detect and alert on stale intel
    """
    
    def __init__(self, db: AsyncIOMotorDatabase, odds_api_key: str):
        self.db = db
        self.odds_api_key = odds_api_key
        self.base_url = "https://api.the-odds-api.com/v4"
        
        # Track active games and their status
        self.game_registry: Dict[str, Dict[str, Any]] = {}
        self.active_polling_tasks: Dict[str, asyncio.Task] = {}
        
        # Background worker state
        self.is_running = False
        self.main_task: Optional[asyncio.Task] = None
        
        # Collections
        self.cached_board_collection = "dg_cached_board"
        self.sync_status_collection = "dg_sync_status"
        self.game_schedule_collection = "dg_game_schedule"
        
        logger.info("[ADAPTIVE_SYNC] Engine initialized")
    
    def _get_game_status(self, commence_time: str) -> GameStatus:
        """
        Determine game status based on time to tip-off.
        
        Args:
            commence_time: ISO format game start time
            
        Returns:
            GameStatus enum value
        """
        if not commence_time:
            return GameStatus.STANDBY
        
        try:
            game_time = datetime.fromisoformat(commence_time.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            time_to_tip = (game_time - now).total_seconds() / 3600  # Hours
            
            if time_to_tip < 0:
                return GameStatus.POST_TIP
            elif time_to_tip < ACTIVE_THRESHOLD:
                return GameStatus.MISSION_CRITICAL
            elif time_to_tip < STANDBY_THRESHOLD:
                return GameStatus.ACTIVE
            else:
                return GameStatus.STANDBY
                
        except Exception as e:
            logger.error(f"[ADAPTIVE_SYNC] Error parsing commence_time: {e}")
            return GameStatus.STANDBY
    
    def _get_poll_interval(self, status: GameStatus) -> Optional[int]:
        """Get polling interval in seconds based on game status."""
        intervals = {
            GameStatus.STANDBY: PollInterval.STANDBY.value,
            GameStatus.ACTIVE: PollInterval.ACTIVE.value,
            GameStatus.MISSION_CRITICAL: PollInterval.MISSION_CRITICAL.value,
            GameStatus.POST_TIP: None
        }
        return intervals.get(status)
    
    async def _fetch_live_odds(self) -> List[Dict[str, Any]]:
        """
        Fetch PrizePicks odds from The Odds API.
        
        PRIZEPICKS-SPECIFIC FETCH:
        - Uses regions=us_dfs (Daily Fantasy Sports region for PrizePicks)
        - Uses bookmakers=prizepicks (specifically target PrizePicks)
        - Fetches both standard and alternate markets for proper tier classification
        
        PrizePicks Classification:
        - STANDARD (Gray): Main market lines (player_points, player_rebounds, player_assists)
        - GOBLIN (Green): Alternate market lines with odds != +100 (discount/promo lines)
        - DEMON (Red): Alternate market lines with +100 odds (boosted/hard lines)
        
        Returns raw odds data for processing.
        """
        if not self.odds_api_key:
            logger.warning("[ADAPTIVE_SYNC] No Odds API key configured")
            return []
        
        # Step 1: Fetch list of NBA events
        events_url = f"{self.base_url}/sports/basketball_nba/events"
        events_params = {
            "apiKey": self.odds_api_key,
            "dateFormat": "iso"
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                events_response = await client.get(events_url, params=events_params)
                events_response.raise_for_status()
                events = events_response.json()
                logger.info(f"[PRIZEPICKS_SYNC] Found {len(events)} NBA events")
                
                if not events:
                    return []
                
                # Step 2: Fetch PrizePicks odds for each event
                # PrizePicks-specific markets (standard + alternate for tier classification)
                prizepicks_markets = ",".join([
                    # Standard markets (STANDARD tier)
                    "player_points", "player_rebounds", "player_assists",
                    "player_threes", "player_blocks", "player_steals",
                    "player_points_rebounds_assists", "player_points_rebounds",
                    "player_points_assists", "player_rebounds_assists",
                    # Alternate markets (GOBLIN/DEMON tiers based on odds)
                    "player_points_alternate", "player_rebounds_alternate", "player_assists_alternate",
                    "player_threes_alternate", "player_blocks_alternate", "player_steals_alternate",
                    "player_points_rebounds_assists_alternate", "player_points_rebounds_alternate",
                    "player_points_assists_alternate", "player_rebounds_assists_alternate"
                ])
                
                enriched_events = []
                
                for event in events:
                    event_id = event.get("id")
                    if not event_id:
                        continue
                    
                    # Fetch PrizePicks odds for this event
                    odds_url = f"{self.base_url}/sports/basketball_nba/events/{event_id}/odds"
                    odds_params = {
                        "apiKey": self.odds_api_key,
                        "regions": "us_dfs",  # PRIZEPICKS: Daily Fantasy Sports region
                        "bookmakers": "prizepicks",  # PRIZEPICKS: Target only PrizePicks
                        "markets": prizepicks_markets,
                        "oddsFormat": "american"
                    }
                    
                    try:
                        odds_response = await client.get(odds_url, params=odds_params, timeout=15.0)
                        if odds_response.status_code == 200:
                            odds_data = odds_response.json()
                            # Merge event info with odds data
                            odds_data["event_id"] = event_id
                            enriched_events.append(odds_data)
                            
                            # Log PrizePicks data found
                            pp_markets = 0
                            for bm in odds_data.get("bookmakers", []):
                                if bm.get("key") == "prizepicks":
                                    pp_markets = len(bm.get("markets", []))
                            if pp_markets > 0:
                                logger.info(f"  [PRIZEPICKS] {event.get('away_team')} @ {event.get('home_team')}: {pp_markets} markets")
                                
                        elif odds_response.status_code == 422:
                            # Try with core markets only
                            odds_params["markets"] = "player_points,player_points_alternate,player_rebounds,player_rebounds_alternate,player_assists,player_assists_alternate"
                            odds_response = await client.get(odds_url, params=odds_params, timeout=15.0)
                            if odds_response.status_code == 200:
                                odds_data = odds_response.json()
                                odds_data["event_id"] = event_id
                                enriched_events.append(odds_data)
                    except Exception as e:
                        logger.warning(f"[PRIZEPICKS_SYNC] Failed to fetch odds for event {event_id}: {e}")
                        continue
                
                logger.info(f"[PRIZEPICKS_SYNC] Fetched PrizePicks odds for {len(enriched_events)} events")
                return enriched_events
                
        except httpx.HTTPStatusError as e:
            logger.error(f"[PRIZEPICKS_SYNC] Odds API HTTP error: {e.response.status_code}")
            return []
        except Exception as e:
            logger.error(f"[PRIZEPICKS_SYNC] Odds API error: {e}")
            return []
    
    async def _update_game_registry(self, events: List[Dict[str, Any]]) -> None:
        """
        Update the game registry with current events and their statuses.
        """
        current_game_ids = set()
        
        for event in events:
            game_id = event.get("id")
            if not game_id:
                continue
            
            current_game_ids.add(game_id)
            commence_time = event.get("commence_time", "")
            status = self._get_game_status(commence_time)
            
            self.game_registry[game_id] = {
                "id": game_id,
                "home_team": event.get("home_team", ""),
                "away_team": event.get("away_team", ""),
                "commence_time": commence_time,
                "status": status.value,
                "poll_interval": self._get_poll_interval(status),
                "last_updated": datetime.now(timezone.utc).isoformat()
            }
        
        # Remove games that are no longer in the feed
        stale_games = set(self.game_registry.keys()) - current_game_ids
        for game_id in stale_games:
            if self.game_registry[game_id]["status"] == GameStatus.POST_TIP.value:
                del self.game_registry[game_id]
                logger.info(f"[ADAPTIVE_SYNC] Removed completed game: {game_id}")
    
    async def _update_cached_board(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Update the dg_cached_board collection with PrizePicks odds data.
        
        PRIZEPICKS TIER CLASSIFICATION:
        - STANDARD (Gray): Main market lines (no "_alternate" suffix)
          These are the standard PrizePicks lines with no multiplier/glow.
        - GOBLIN (Green): Alternate market lines with odds != +100
          These are PrizePicks "Discount/Promo" lines - easier to hit.
        - DEMON (Red): Alternate market lines with +100 odds (even)
          These are PrizePicks "Boosted/Hard" lines - higher risk, higher reward.
        
        Note: The Odds API doesn't expose explicit "Glow" or "Multiplier" metadata,
        but PrizePicks' classification is encoded in:
        1. Market type (standard vs alternate)
        2. Price (+100 = Demon/Hard, other = Goblin/Discount)
        """
        if not events:
            return {"updated": 0, "errors": 0}
        
        now = datetime.now(timezone.utc)
        updated_count = 0
        error_count = 0
        goblin_count = 0
        demon_count = 0
        standard_count = 0
        
        for event in events:
            game_id = event.get("id")
            commence_time = event.get("commence_time", "")
            home_team = event.get("home_team", "")
            away_team = event.get("away_team", "")
            
            # Extract bookmakers - specifically looking for PrizePicks
            bookmakers = event.get("bookmakers", [])
            
            for bookmaker in bookmakers:
                bookmaker_key = bookmaker.get("key", "")
                
                # Skip non-PrizePicks bookmakers (we specifically requested prizepicks)
                if bookmaker_key != "prizepicks":
                    continue
                    
                markets = bookmaker.get("markets", [])
                
                for market in markets:
                    market_key = market.get("key", "")
                    outcomes = market.get("outcomes", [])
                    
                    # PRIZEPICKS CLASSIFICATION based on market type
                    is_alternate_market = "_alternate" in market_key
                    
                    # Extract base stat type (remove "_alternate" suffix)
                    stat_type_extracted = market_key.replace("_alternate", "").replace("player_", "").upper()
                    # Normalize combined stats
                    stat_type_extracted = stat_type_extracted.replace("POINTS_REBOUNDS_ASSISTS", "PRA")
                    stat_type_extracted = stat_type_extracted.replace("POINTS_REBOUNDS", "P+R")
                    stat_type_extracted = stat_type_extracted.replace("POINTS_ASSISTS", "P+A")
                    stat_type_extracted = stat_type_extracted.replace("REBOUNDS_ASSISTS", "R+A")
                    stat_type_extracted = stat_type_extracted.replace("THREES", "3PM")
                    stat_type_extracted = stat_type_extracted.replace("BLOCKS", "BLK")
                    stat_type_extracted = stat_type_extracted.replace("STEALS", "STL")
                    stat_type_extracted = stat_type_extracted.replace("TURNOVERS", "TO")
                    stat_type_extracted = stat_type_extracted.replace("POINTS", "PTS")
                    stat_type_extracted = stat_type_extracted.replace("REBOUNDS", "REB")
                    stat_type_extracted = stat_type_extracted.replace("ASSISTS", "AST")
                    
                    for outcome in outcomes:
                        player_name = outcome.get("description", "")
                        if not player_name:
                            continue
                        
                        price = outcome.get("price", 0)
                        line = outcome.get("point", 0)
                        direction = (outcome.get("name", "") or "over").lower()
                        
                        # PRIZEPICKS TIER CLASSIFICATION
                        # Based on market type and price:
                        if is_alternate_market:
                            if price == 100:
                                # DEMON: Alternate with +100 odds = "Boosted/Hard" line
                                is_demon = True
                                is_goblin = False
                                tier_style = "red"
                                tier_label = "DEMON"
                                prizepicks_type = "boosted_hard"
                                demon_count += 1
                            else:
                                # GOBLIN: Alternate with other odds = "Discount/Promo" line
                                is_demon = False
                                is_goblin = True
                                tier_style = "green"
                                tier_label = "GOBLIN"
                                prizepicks_type = "discount_promo"
                                goblin_count += 1
                        else:
                            # STANDARD: Main market line (no glow/multiplier)
                            is_demon = False
                            is_goblin = False
                            tier_style = "standard"
                            tier_label = "STANDARD"
                            prizepicks_type = "standard_line"
                            standard_count += 1
                        
                        try:
                            # Build the update document with PrizePicks classification
                            update_doc = {
                                "game_id": game_id,
                                "commence_time": commence_time,
                                "home_team": home_team,
                                "away_team": away_team,
                                "bookmaker": bookmaker_key,
                                "market": market_key,
                                "player_name": player_name,
                                "line": line,
                                "price": price,
                                "direction": direction,
                                "name": outcome.get("name", ""),  # Over/Under
                                "last_updated": now,
                                "last_updated_iso": now.isoformat(),
                                "sync_source": "prizepicks_sync",
                                # PrizePicks tier classification
                                "is_alternate_market": is_alternate_market,
                                "is_demon": is_demon,
                                "is_goblin": is_goblin,
                                "tier_style": tier_style,
                                "tier_label": tier_label,
                                "stat_type_extracted": stat_type_extracted,
                                "prizepicks_type": prizepicks_type
                            }
                            
                            # Upsert to collection
                            await self.db[self.cached_board_collection].update_one(
                                {
                                    "game_id": game_id,
                                    "player_name": player_name,
                                    "market": market_key,
                                    "line": line,
                                    "direction": direction,
                                    "bookmaker": bookmaker_key
                                },
                                {"$set": update_doc},
                                upsert=True
                            )
                            updated_count += 1
                            
                        except Exception as e:
                            error_count += 1
                            logger.error(f"[PRIZEPICKS_SYNC] Error updating {player_name}: {e}")
        
        # Log PrizePicks tier distribution
        if updated_count > 0:
            logger.info(f"[PRIZEPICKS_SYNC] PrizePicks lines: {goblin_count} Goblin (Discount), {demon_count} Demon (Boosted), {standard_count} Standard")
        
        # Update sync status
        await self._update_sync_status(now, updated_count, error_count)
        
        return {"updated": updated_count, "errors": error_count, "timestamp": now.isoformat()}
    
    async def _update_sync_status(self, timestamp: datetime, updated: int, errors: int) -> None:
        """
        Update the sync status document for frontend consumption.
        """
        await self.db[self.sync_status_collection].update_one(
            {"_id": "adaptive_sync"},
            {
                "$set": {
                    "last_sync": timestamp,
                    "last_sync_iso": timestamp.isoformat(),
                    "records_updated": updated,
                    "errors": errors,
                    "next_sync_estimate": (timestamp + timedelta(seconds=60)).isoformat(),
                    "engine_status": "running" if self.is_running else "stopped",
                    "active_games": len([g for g in self.game_registry.values() if g["status"] != "post_tip"]),
                    "mission_critical_games": len([g for g in self.game_registry.values() if g["status"] == "mission_critical"]),
                    "game_registry": list(self.game_registry.values())
                }
            },
            upsert=True
        )
    
    async def get_sync_status(self) -> Dict[str, Any]:
        """
        Get current sync status for API endpoint.
        """
        status = await self.db[self.sync_status_collection].find_one({"_id": "adaptive_sync"})
        if status:
            status.pop("_id", None)
            
            # Calculate time since last sync
            if status.get("last_sync"):
                last_sync = status["last_sync"]
                if isinstance(last_sync, datetime):
                    # Make sure both datetimes are timezone-aware
                    if last_sync.tzinfo is None:
                        last_sync = last_sync.replace(tzinfo=timezone.utc)
                    now = datetime.now(timezone.utc)
                    seconds_ago = (now - last_sync).total_seconds()
                    status["seconds_since_sync"] = int(seconds_ago)
                    status["sync_age_display"] = self._format_time_ago(seconds_ago)
        
        return status or {}
    
    def _format_time_ago(self, seconds: float) -> str:
        """Format seconds into human-readable time ago string."""
        if seconds < 60:
            return f"{int(seconds)}s ago"
        elif seconds < 3600:
            return f"{int(seconds / 60)}m ago"
        else:
            return f"{int(seconds / 3600)}h ago"
    
    async def check_stale_intel(self, game_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Check for stale intel during mission-critical windows.
        Returns stale entries that need immediate refresh.
        """
        stale_entries = []
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(seconds=STALE_DATA_THRESHOLD_SECONDS)
        
        # Find mission-critical games
        critical_games = [
            g for g in self.game_registry.values() 
            if g["status"] == "mission_critical"
        ]
        
        if game_id:
            critical_games = [g for g in critical_games if g["id"] == game_id]
        
        for game in critical_games:
            # Check if any data for this game is stale
            stale_count = await self.db[self.cached_board_collection].count_documents({
                "game_id": game["id"],
                "last_updated": {"$lt": threshold}
            })
            
            if stale_count > 0:
                stale_entries.append({
                    "game_id": game["id"],
                    "home_team": game["home_team"],
                    "away_team": game["away_team"],
                    "stale_records": stale_count,
                    "status": "STALE_INTEL",
                    "threshold_seconds": STALE_DATA_THRESHOLD_SECONDS
                })
        
        return {
            "has_stale_intel": len(stale_entries) > 0,
            "stale_games": stale_entries,
            "checked_at": now.isoformat()
        }
    
    async def trigger_priority_refresh(self, game_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Trigger an immediate high-priority refresh.
        Used when stale intel is detected during mission-critical windows.
        """
        logger.info(f"[ADAPTIVE_SYNC] PRIORITY REFRESH triggered for game: {game_id or 'ALL'}")
        
        events = await self._fetch_live_odds()
        
        if game_id:
            events = [e for e in events if e.get("id") == game_id]
        
        result = await self._update_cached_board(events)
        result["trigger"] = "priority_refresh"
        result["game_id"] = game_id
        
        return result
    
    async def _adaptive_poll_loop(self) -> None:
        """
        Main adaptive polling loop.
        Adjusts polling frequency based on game proximity.
        """
        logger.info("[ADAPTIVE_SYNC] Starting adaptive poll loop")
        
        while self.is_running:
            try:
                # Fetch current odds
                events = await self._fetch_live_odds()
                
                # Update game registry with current statuses
                await self._update_game_registry(events)
                
                # Update cached board
                await self._update_cached_board(events)
                
                # Determine next poll interval based on most urgent game
                min_interval = PollInterval.STANDBY.value  # Default to 60 minutes
                
                for game in self.game_registry.values():
                    if game["status"] == "mission_critical":
                        min_interval = min(min_interval, PollInterval.MISSION_CRITICAL.value)
                    elif game["status"] == "active":
                        min_interval = min(min_interval, PollInterval.ACTIVE.value)
                
                # Check for stale intel in mission-critical windows
                stale_check = await self.check_stale_intel()
                if stale_check["has_stale_intel"]:
                    logger.warning(f"[ADAPTIVE_SYNC] STALE INTEL DETECTED: {len(stale_check['stale_games'])} games")
                    # Don't wait, do immediate refresh
                    min_interval = 5  # 5 second emergency refresh
                
                logger.info(f"[ADAPTIVE_SYNC] Poll complete. Next refresh in {min_interval}s. "
                           f"Games: {len(self.game_registry)} | "
                           f"Critical: {len([g for g in self.game_registry.values() if g['status'] == 'mission_critical'])}")
                
                # Wait for next poll
                await asyncio.sleep(min_interval)
                
            except asyncio.CancelledError:
                logger.info("[ADAPTIVE_SYNC] Poll loop cancelled")
                break
            except Exception as e:
                logger.error(f"[ADAPTIVE_SYNC] Poll loop error: {e}")
                await asyncio.sleep(60)  # Wait 1 minute on error
    
    async def start(self) -> None:
        """Start the adaptive sync engine."""
        if self.is_running:
            logger.warning("[ADAPTIVE_SYNC] Engine already running")
            return
        
        self.is_running = True
        self.main_task = asyncio.create_task(self._adaptive_poll_loop())
        logger.info("[ADAPTIVE_SYNC] Engine started")
    
    async def stop(self) -> None:
        """Stop the adaptive sync engine."""
        self.is_running = False
        
        if self.main_task:
            self.main_task.cancel()
            try:
                await self.main_task
            except asyncio.CancelledError:
                pass
        
        # Update status
        await self.db[self.sync_status_collection].update_one(
            {"_id": "adaptive_sync"},
            {"$set": {"engine_status": "stopped", "stopped_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True
        )
        
        logger.info("[ADAPTIVE_SYNC] Engine stopped")
    
    async def get_board_with_freshness(self, limit: int = 100) -> Dict[str, Any]:
        """
        Get cached board data with freshness indicators.
        Used by frontend to display last_updated timestamps.
        """
        now = datetime.now(timezone.utc)
        
        # Get board entries
        cursor = self.db[self.cached_board_collection].find(
            {},
            {"_id": 0}
        ).sort("last_updated", -1).limit(limit)
        
        entries = await cursor.to_list(length=limit)
        
        # Add freshness indicators
        for entry in entries:
            last_updated = entry.get("last_updated")
            if last_updated:
                if isinstance(last_updated, datetime):
                    seconds_ago = (now - last_updated).total_seconds()
                    entry["freshness"] = {
                        "seconds_ago": int(seconds_ago),
                        "display": self._format_time_ago(seconds_ago),
                        "is_stale": seconds_ago > STALE_DATA_THRESHOLD_SECONDS
                    }
        
        return {
            "entries": entries,
            "count": len(entries),
            "retrieved_at": now.isoformat()
        }


# Singleton instance
_adaptive_sync_engine: Optional[AdaptiveSyncEngine] = None


def get_adaptive_sync_engine() -> Optional[AdaptiveSyncEngine]:
    """Get the singleton adaptive sync engine instance."""
    return _adaptive_sync_engine


def init_adaptive_sync_engine(db: AsyncIOMotorDatabase, odds_api_key: str) -> AdaptiveSyncEngine:
    """Initialize the adaptive sync engine singleton."""
    global _adaptive_sync_engine
    _adaptive_sync_engine = AdaptiveSyncEngine(db, odds_api_key)
    return _adaptive_sync_engine
