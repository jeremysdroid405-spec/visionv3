"""
Game Lock Engine v1.0
======================

Automatic game-start cleanup system that:
1. Compares current_time vs commence_time every 60 seconds
2. Flags games as "locked" when they start
3. Removes locked props from active feeds
4. Triggers "next best" re-sorting
5. Integrates with Live Score Ticker

Tonight's Slate (March 13, 2026):
- 7:30 PM ET: NYK vs IND, PHX vs TOR, CLE vs DAL, MEM vs DET
- 8:00 PM ET: NOP vs HOU
- 10:00 PM ET: MIN vs GSW (The "Curry Return" game)
- 10:30 PM ET: CHI vs LAC
"""

import os
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Set
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.config.collection_names import COLL
from services.observability import log_silent_failure

logger = logging.getLogger(__name__)

# Lock buffer - games are locked 60 seconds before tip (to account for early tips)
LOCK_BUFFER_SECONDS = 60

# T-Minus threshold - show countdown for games starting in next 15 minutes
T_MINUS_THRESHOLD_MINUTES = 15


class GameLockEngine:
    """
    Manages automatic game-start cleanup and prop locking.
    
    Core Functions:
    1. check_and_lock_games() - Called every 60s to lock started games
    2. get_active_props() - Returns only non-locked props
    3. get_locked_games() - Returns games that are in progress
    4. validate_parlay() - Pre-lock-in validation
    5. get_t_minus_games() - Games starting in <15 minutes
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.cached_board = COLL.handle(db, "board_cache", "nba")
        self.locked_games = db.dg_locked_games
        self._lock_check_task: Optional[asyncio.Task] = None
        self._running = False
        self._last_check = None
        
        logger.info("[GAME LOCK] Engine initialized")
    
    async def start(self):
        """Start the background lock-check loop (60-second interval)."""
        if self._running:
            logger.warning("[GAME LOCK] Already running")
            return
        
        self._running = True
        self._lock_check_task = asyncio.create_task(self._lock_check_loop())
        logger.info("[GAME LOCK] Background lock-check started (60s interval)")
    
    async def stop(self):
        """Stop the background lock-check loop."""
        self._running = False
        if self._lock_check_task:
            self._lock_check_task.cancel()
            try:
                await self._lock_check_task
            except asyncio.CancelledError as _swept_exc:
                log_silent_failure("services.engines.game_lock_engine.stop", _swept_exc)  # sweep-auto-converted
        logger.info("[GAME LOCK] Background lock-check stopped")
    
    async def _lock_check_loop(self):
        """Background loop that checks for games to lock every 60 seconds."""
        while self._running:
            try:
                result = await self.check_and_lock_games()
                if result.get("newly_locked", 0) > 0:
                    logger.info(f"[GAME LOCK] Locked {result['newly_locked']} games")
                self._last_check = datetime.now(timezone.utc)
            except Exception as e:
                logger.error(f"[GAME LOCK] Lock check error: {e}")
            
            await asyncio.sleep(60)  # Check every 60 seconds
    
    async def check_and_lock_games(self) -> Dict[str, Any]:
        """
        Main lock check function - compares current time with commence_time.
        
        Returns:
            Dict with: checked, newly_locked, already_locked, locked_events
        """
        current_time = datetime.now(timezone.utc)
        
        # Get all unique events from the cached board
        # The cached_board stores player documents with nested props arrays
        # We need to unwind the props to get event-level info
        pipeline = [
            {"$unwind": "$props"},
            {"$group": {
                "_id": "$props.event_id",
                "commence_time": {"$first": "$props.commence_time"},
                "home_team": {"$first": "$props.home_team"},
                "away_team": {"$first": "$props.away_team"},
                "player_count": {"$sum": 1}
            }},
            {"$match": {"_id": {"$ne": None}}}
        ]
        
        events = await self.cached_board.aggregate(pipeline).to_list(length=100)
        
        checked = 0
        newly_locked = 0
        already_locked = 0
        locked_events = []
        
        for event in events:
            event_id = event["_id"]
            commence_time_str = event.get("commence_time", "")
            
            if not commence_time_str or not event_id:
                continue
            
            checked += 1
            
            try:
                # Parse commence_time (ISO format)
                commence_time = datetime.fromisoformat(commence_time_str.replace("Z", "+00:00"))
                
                # Check if already locked
                existing_lock = await self.locked_games.find_one({"event_id": event_id})
                
                if existing_lock:
                    already_locked += 1
                    continue
                
                # Check if game should be locked (current time >= commence_time - buffer)
                if current_time >= commence_time - timedelta(seconds=LOCK_BUFFER_SECONDS):
                    # Lock the game
                    lock_record = {
                        "event_id": event_id,
                        "home_team": event.get("home_team", ""),
                        "away_team": event.get("away_team", ""),
                        "commence_time": commence_time_str,
                        "locked_at": current_time.isoformat(),
                        "player_count": event.get("player_count", 0),
                        "status": "in_play"
                    }
                    
                    await self.locked_games.insert_one(lock_record)
                    
                    # Mark all players that have props for this event as locked
                    # We use $elemMatch to find players with props in this event
                    await self.cached_board.update_many(
                        {"props.event_id": event_id},
                        {"$set": {"locked": True, "locked_at": current_time.isoformat(), "locked_event_id": event_id}}
                    )
                    
                    newly_locked += 1
                    locked_events.append({
                        "event_id": event_id,
                        "matchup": f"{event.get('away_team', '')} @ {event.get('home_team', '')}",
                        "locked_at": current_time.isoformat()
                    })
                    
                    logger.info(f"[GAME LOCK] Locked: {event.get('away_team')} @ {event.get('home_team')}")
            
            except Exception as e:
                logger.warning(f"[GAME LOCK] Error processing event {event_id}: {e}")
                continue
        
        return {
            "success": True,
            "checked": checked,
            "newly_locked": newly_locked,
            "already_locked": already_locked,
            "locked_events": locked_events,
            "checked_at": current_time.isoformat()
        }
    
    async def get_active_props(self, filter_locked: bool = True) -> List[Dict[str, Any]]:
        """
        Get all non-locked props from the cached board.
        
        Args:
            filter_locked: If True, excludes locked props (default: True)
        
        Returns:
            List of active (non-locked) player props
        """
        query = {}
        if filter_locked:
            query["locked"] = {"$ne": True}
        
        props = await self.cached_board.find(query, {"_id": 0}).to_list(length=1000)
        
        return props
    
    async def get_locked_games(self) -> List[Dict[str, Any]]:
        """
        Get all locked/in-progress games for the Live Score Ticker.
        
        Returns:
            List of locked game records
        """
        locked = await self.locked_games.find(
            {},
            {"_id": 0}
        ).sort("locked_at", -1).to_list(length=50)
        
        return locked
    
    async def validate_parlay(self, player_names: List[str]) -> Dict[str, Any]:
        """
        Pre-lock-in validation for parlay picks.
        
        Checks if any games in the parlay have started in the last 60 seconds.
        
        Args:
            player_names: List of player names in the parlay
        
        Returns:
            Dict with: valid, invalid_picks, message
        """
        current_time = datetime.now(timezone.utc)
        invalid_picks = []
        valid_picks = []
        
        for player_name in player_names:
            # Get the player document from cached board (player has nested props)
            player_doc = await self.cached_board.find_one(
                {"player_name": player_name},
                {"_id": 0, "player_name": 1, "locked": 1, "props": 1}
            )
            
            if not player_doc:
                continue
            
            # Check if already locked
            if player_doc.get("locked"):
                # Try to get game info from props
                props = player_doc.get("props", [])
                first_prop = props[0] if props else {}
                invalid_picks.append({
                    "player_name": player_name,
                    "reason": "Game already started",
                    "matchup": f"{first_prop.get('away_team', '')} @ {first_prop.get('home_team', '')}"
                })
                continue
            
            # Check if any game is about to start (within 60 seconds)
            props = player_doc.get("props", [])
            is_valid = True
            for prop in props:
                commence_time_str = prop.get("commence_time", "")
                if commence_time_str:
                    try:
                        commence_time = datetime.fromisoformat(commence_time_str.replace("Z", "+00:00"))
                        if current_time >= commence_time - timedelta(seconds=60):
                            invalid_picks.append({
                                "player_name": player_name,
                                "reason": "Game starting in <60 seconds",
                                "matchup": f"{prop.get('away_team', '')} @ {prop.get('home_team', '')}"
                            })
                            is_valid = False
                            break
                    except Exception as _swept_exc:
                        log_silent_failure("services.engines.game_lock_engine.validate_parlay", _swept_exc)  # sweep-auto-converted
            
            if is_valid:
                valid_picks.append(player_name)
        
        is_parlay_valid = len(invalid_picks) == 0
        
        return {
            "valid": is_parlay_valid,
            "valid_picks": valid_picks,
            "invalid_picks": invalid_picks,
            "message": "All picks valid" if is_parlay_valid else f"{len(invalid_picks)} pick(s) have games that started or are starting soon",
            "checked_at": current_time.isoformat()
        }
    
    async def get_t_minus_games(self) -> List[Dict[str, Any]]:
        """
        Get games starting within the T-Minus threshold (15 minutes).
        
        Returns games with countdown timers for high-stakes feel.
        
        Returns:
            List of games with t_minus_seconds field
        """
        current_time = datetime.now(timezone.utc)
        threshold = current_time + timedelta(minutes=T_MINUS_THRESHOLD_MINUTES)
        
        # Get unique events from cached board using nested props
        pipeline = [
            {"$match": {"locked": {"$ne": True}}},
            {"$unwind": "$props"},
            {"$group": {
                "_id": "$props.event_id",
                "commence_time": {"$first": "$props.commence_time"},
                "home_team": {"$first": "$props.home_team"},
                "away_team": {"$first": "$props.away_team"},
                "player_count": {"$sum": 1}
            }},
            {"$match": {"_id": {"$ne": None}}}
        ]
        
        events = await self.cached_board.aggregate(pipeline).to_list(length=100)
        
        t_minus_games = []
        
        for event in events:
            commence_time_str = event.get("commence_time", "")
            if not commence_time_str:
                continue
            
            try:
                commence_time = datetime.fromisoformat(commence_time_str.replace("Z", "+00:00"))
                
                # Check if within T-Minus threshold and not yet started
                if current_time < commence_time <= threshold:
                    seconds_until = int((commence_time - current_time).total_seconds())
                    minutes_until = seconds_until // 60
                    
                    t_minus_games.append({
                        "event_id": event["_id"],
                        "home_team": event.get("home_team", ""),
                        "away_team": event.get("away_team", ""),
                        "commence_time": commence_time_str,
                        "t_minus_seconds": seconds_until,
                        "t_minus_display": f"T-{minutes_until}:{seconds_until % 60:02d}",
                        "player_count": event.get("player_count", 0)
                    })
            except Exception:
                continue
        
        # Sort by soonest first
        t_minus_games.sort(key=lambda x: x["t_minus_seconds"])
        
        return t_minus_games
    
    async def get_lock_status(self) -> Dict[str, Any]:
        """
        Get overall lock status for the dashboard.
        
        Returns:
            Dict with: active_games, locked_games, t_minus_games, last_check
        """
        # Count active (non-locked) events using nested props
        active_pipeline = [
            {"$match": {"locked": {"$ne": True}}},
            {"$unwind": "$props"},
            {"$group": {"_id": "$props.event_id"}},
            {"$match": {"_id": {"$ne": None}}}
        ]
        active_events = await self.cached_board.aggregate(active_pipeline).to_list(length=100)
        
        # Count locked games
        locked_count = await self.locked_games.count_documents({})
        
        # Get T-Minus games
        t_minus = await self.get_t_minus_games()
        
        return {
            "active_games": len(active_events),
            "locked_games": locked_count,
            "t_minus_games": len(t_minus),
            "t_minus_details": t_minus[:5],  # Top 5 soonest
            "last_check": self._last_check.isoformat() if self._last_check else None,
            "engine_running": self._running
        }
    
    async def clear_old_locks(self, hours_old: int = 24):
        """
        Clear lock records older than specified hours.
        Run daily to clean up old game data.
        
        Args:
            hours_old: Remove locks older than this many hours (default: 24)
        """
        threshold = datetime.now(timezone.utc) - timedelta(hours=hours_old)
        
        result = await self.locked_games.delete_many({
            "locked_at": {"$lt": threshold.isoformat()}
        })
        
        logger.info(f"[GAME LOCK] Cleared {result.deleted_count} old lock records")
        
        return {"cleared": result.deleted_count}


# Singleton instance
_game_lock_engine: Optional[GameLockEngine] = None


def get_game_lock_engine(db: AsyncIOMotorDatabase = None) -> Optional[GameLockEngine]:
    """Get or create the Game Lock Engine singleton."""
    global _game_lock_engine
    
    if _game_lock_engine is None and db is not None:
        _game_lock_engine = GameLockEngine(db)
    
    return _game_lock_engine


def init_game_lock_engine(db: AsyncIOMotorDatabase) -> GameLockEngine:
    """Initialize the Game Lock Engine."""
    global _game_lock_engine
    _game_lock_engine = GameLockEngine(db)
    return _game_lock_engine
