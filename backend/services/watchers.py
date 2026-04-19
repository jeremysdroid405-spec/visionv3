"""
Watchers — Event-driven trigger sources for the Rebuild Coordinator.

Three independent watcher classes, each independently toggleable:
  1. InjuryWatcher — detects injury status changes, emits high-severity events
  2. GameClockWatcher — detects games approaching lock window, emits medium events
  3. OddsDeltaWatcher — detects meaningful odds/line changes (Phase 4c, starts disabled)

All watchers:
  - Run as background asyncio tasks
  - Emit BoardEvents to the shared Event Bus
  - Can be enabled/disabled independently at runtime
  - Share no state between each other
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set

from services.event_bus import BoardEvent, get_event_bus
from services.config.collection_names import COLL

logger = logging.getLogger(__name__)


# ==========================================================================
# 1. INJURY WATCHER
# ==========================================================================

class InjuryWatcher:
    """
    Polls BDL injuries via normalization layer for BOTH sports.
    Emits events only on meaningful changes:
      - tier_level changed (status escalation/de-escalation)
      - return_date shifted
      - new injury appeared

    Interval: 120s (every 2 min)
    """

    POLL_INTERVAL = 120  # seconds

    def __init__(self, db):
        self.db = db
        self._enabled = False
        self._task: Optional[asyncio.Task] = None
        # Previous state: {sport: {bdl_id: {tier_level, return_date}}}
        self._previous: Dict[str, Dict[int, dict]] = {"nba": {}, "mlb": {}}
        self._stats = {"polls": 0, "changes_detected": 0, "events_emitted": 0, "new_injuries": 0, "status_changes": 0, "return_shifts": 0}

    async def start(self):
        if self._enabled:
            return
        self._enabled = True
        self._task = asyncio.create_task(self._loop())
        logger.info("[INJURY_WATCHER] Started (120s interval, BDL source, both sports)")

    async def stop(self):
        self._enabled = False
        if self._task:
            self._task.cancel()
        logger.info("[INJURY_WATCHER] Stopped")

    async def _loop(self):
        await self._seed_baseline()
        while self._enabled:
            try:
                await asyncio.sleep(self.POLL_INTERVAL)
                await self._check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[INJURY_WATCHER] Poll error: {e}")
                await asyncio.sleep(30)

    async def _seed_baseline(self):
        """Fetch current injuries from BDL and store as baseline (no events)."""
        from services.injury_normalization import sync_all, get_injuries

        try:
            await sync_all(self.db)
        except Exception as e:
            logger.warning(f"[INJURY_WATCHER] Baseline BDL fetch failed: {e}")

        for sport in ["nba", "mlb"]:
            records = await get_injuries(self.db, sport=sport)
            snap = {}
            for r in records:
                bid = r.get("bdl_id")
                if bid:
                    snap[bid] = {"tier_level": r.get("tier_level", 0), "return_date": r.get("return_date")}
            self._previous[sport] = snap
        total = sum(len(v) for v in self._previous.values())
        logger.info(f"[INJURY_WATCHER] Baseline: NBA={len(self._previous['nba'])}, MLB={len(self._previous['mlb'])} ({total} total)")

    async def _check(self):
        self._stats["polls"] += 1
        from services.injury_normalization import sync_injuries as norm_sync, get_injuries, is_meaningful_change

        bus = get_event_bus()

        for sport in ["nba", "mlb"]:
            try:
                await norm_sync(self.db, sport)
            except Exception as e:
                logger.warning(f"[INJURY_WATCHER] {sport.upper()} BDL fetch failed: {e}")
                continue

            records = await get_injuries(self.db, sport=sport)
            new_snap = {}
            meaningful_players = []

            for r in records:
                bid = r.get("bdl_id")
                if not bid:
                    continue
                new_entry = {"tier_level": r.get("tier_level", 0), "return_date": r.get("return_date")}
                new_snap[bid] = new_entry

                old_entry = self._previous[sport].get(bid)
                changed, reason = is_meaningful_change(old_entry, new_entry)
                if changed:
                    meaningful_players.append(r.get("player_name", ""))
                    self._stats["changes_detected"] += 1
                    if reason == "new_injury":
                        self._stats["new_injuries"] += 1
                    elif reason.startswith("status_"):
                        self._stats["status_changes"] += 1
                    elif reason == "return_date_shifted":
                        self._stats["return_shifts"] += 1

            self._previous[sport] = new_snap

            if meaningful_players:
                await bus.publish(BoardEvent(
                    sport=sport,
                    event_type="injury_change",
                    severity="high",
                    affected_players=meaningful_players[:10],
                    source="injury_watcher",
                    metadata={"change_count": len(meaningful_players)},
                ))
                self._stats["events_emitted"] += 1
                logger.info(f"[INJURY_WATCHER] {sport.upper()}: {len(meaningful_players)} meaningful changes → {meaningful_players[:5]}")

    def get_stats(self) -> dict:
        tracked = sum(len(v) for v in self._previous.values())
        return {"enabled": self._enabled, "tracked_players": tracked, "tracked_nba": len(self._previous.get("nba", {})), "tracked_mlb": len(self._previous.get("mlb", {})), **self._stats}


# ==========================================================================
# 2. GAME CLOCK WATCHER
# ==========================================================================

class GameClockWatcher:
    """
    Detects games approaching their lock window.
    Triggers a rebuild when a game is within LOCK_WINDOW_MINUTES of start
    and board picks exist for that game.

    Interval: 300s (every 5 min)
    """

    POLL_INTERVAL = 300  # seconds
    LOCK_WINDOW_MINUTES = 30  # emit event when game starts within 30 min

    def __init__(self, db):
        self.db = db
        self._enabled = False
        self._task: Optional[asyncio.Task] = None
        self._alerted_games: Set[str] = set()  # game keys already alerted
        self._stats = {"polls": 0, "locks_detected": 0, "events_emitted": 0}

    async def start(self):
        if self._enabled:
            return
        self._enabled = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"[GAME_CLOCK] Started (300s interval, {self.LOCK_WINDOW_MINUTES}min lock window)")

    async def stop(self):
        self._enabled = False
        if self._task:
            self._task.cancel()
        logger.info("[GAME_CLOCK] Stopped")

    async def _loop(self):
        while self._enabled:
            try:
                await asyncio.sleep(self.POLL_INTERVAL)
                await self._check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[GAME_CLOCK] Poll error: {e}")
                await asyncio.sleep(60)

    async def _check(self):
        self._stats["polls"] += 1
        now = datetime.now(timezone.utc)
        lock_threshold = now + timedelta(minutes=self.LOCK_WINDOW_MINUTES)
        bus = get_event_bus()

        for sport in ("nba", "mlb"):
            col_name = COLL("live_props", sport)
            # Find distinct commence_times
            pipeline = [
                {"$match": {"commence_time": {"$exists": True, "$ne": None}}},
                {"$group": {"_id": "$commence_time"}},
            ]
            times = set()
            async for doc in self.db[col_name].aggregate(pipeline):
                ct = doc["_id"]
                if isinstance(ct, str):
                    try:
                        ct = datetime.fromisoformat(ct.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        continue
                if isinstance(ct, datetime):
                    times.add(ct)

            approaching = [t for t in times if now < t <= lock_threshold]
            for game_time in approaching:
                game_key = f"{sport}|{game_time.isoformat()}"
                if game_key in self._alerted_games:
                    continue

                self._alerted_games.add(game_key)
                self._stats["locks_detected"] += 1
                self._stats["events_emitted"] += 1

                await bus.publish(BoardEvent(
                    sport=sport,
                    event_type="game_lock",
                    severity="medium",
                    source="game_clock_watcher",
                    metadata={"commence_time": game_time.isoformat()},
                ))
                logger.info(f"[GAME_CLOCK] {sport.upper()}: Game at {game_time.isoformat()} approaching lock")

        # Prune old alerted games (older than 3 hours)
        cutoff = now - timedelta(hours=3)
        self._alerted_games = {
            k for k in self._alerted_games
            if datetime.fromisoformat(k.split("|")[1]) > cutoff
        }

    def get_stats(self) -> dict:
        return {"enabled": self._enabled, "alerted_games": len(self._alerted_games), **self._stats}


# ==========================================================================
# 3. ODDS DELTA WATCHER (starts DISABLED — Phase 4c)
# ==========================================================================

class OddsDeltaWatcher:
    """
    Polls odds snapshots and detects meaningful line/odds changes.
    Only emits events when changes are large enough to affect the board.

    Starts DISABLED. Enable after observability confirms trigger volume is safe.

    Interval: 600s (every 10 min) — adjustable via budget manager
    Meaningful thresholds:
      - Line change >= 0.5
      - Prop appeared/disappeared
    """

    POLL_INTERVAL = 600  # seconds (default, overridden by budget manager)
    LINE_CHANGE_THRESHOLD = 0.5

    def __init__(self, db):
        self.db = db
        self._enabled = False
        self._task: Optional[asyncio.Task] = None
        self._previous_snapshots: Dict[str, Dict[str, float]] = {}  # sport → {pick_id: line}
        self._stats = {"polls": 0, "deltas_detected": 0, "events_emitted": 0}

    async def start(self):
        if self._enabled:
            return
        self._enabled = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"[ODDS_DELTA] Started ({self.POLL_INTERVAL}s interval)")

    async def stop(self):
        self._enabled = False
        if self._task:
            self._task.cancel()
        logger.info("[ODDS_DELTA] Stopped")

    async def _loop(self):
        # Seed baseline
        for sport in ["nba", "mlb"]:
            self._previous_snapshots[sport] = await self._snapshot(sport)
        logger.info(f"[ODDS_DELTA] Baseline: NBA={len(self._previous_snapshots.get('nba',{}))} props, MLB={len(self._previous_snapshots.get('mlb',{}))} props")

        while self._enabled:
            try:
                await asyncio.sleep(self.POLL_INTERVAL)
                await self._check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[ODDS_DELTA] Poll error: {e}")
                await asyncio.sleep(60)

    async def _snapshot(self, sport: str) -> Dict[str, float]:
        """Read current board lines from cached board."""
        col = "dg_cached_board" if sport == "nba" else "mlb_cached_board"
        snap = {}
        cursor = self.db[col].find({}, {"_id": 0, "player_name": 1, "stat_type": 1, "line": 1})
        async for doc in cursor:
            key = f"{doc.get('player_name','')}|{doc.get('stat_type','')}"
            snap[key] = doc.get("line", 0) or 0
        return snap

    async def _check(self):
        self._stats["polls"] += 1
        bus = get_event_bus()

        for sport in ["nba", "mlb"]:
            new_snap = await self._snapshot(sport)
            old_snap = self._previous_snapshots.get(sport, {})
            self._previous_snapshots[sport] = new_snap

            if not old_snap:
                continue

            changed_players = []
            # Check for meaningful line changes
            for key, new_line in new_snap.items():
                old_line = old_snap.get(key)
                if old_line is None:
                    # New prop appeared
                    changed_players.append(key.split("|")[0])
                    self._stats["deltas_detected"] += 1
                elif abs(new_line - old_line) >= self.LINE_CHANGE_THRESHOLD:
                    changed_players.append(key.split("|")[0])
                    self._stats["deltas_detected"] += 1

            # Check for disappeared props
            for key in old_snap:
                if key not in new_snap:
                    changed_players.append(key.split("|")[0])
                    self._stats["deltas_detected"] += 1

            if changed_players:
                unique = list(set(changed_players))[:10]
                await bus.publish(BoardEvent(
                    sport=sport,
                    event_type="odds_delta",
                    severity="medium" if len(unique) <= 5 else "high",
                    affected_players=unique,
                    source="odds_delta_watcher",
                ))
                self._stats["events_emitted"] += 1
                logger.info(f"[ODDS_DELTA] {sport.upper()}: {len(changed_players)} line changes → {unique[:5]}")

    def get_stats(self) -> dict:
        snap_sizes = {s: len(v) for s, v in self._previous_snapshots.items()}
        return {"enabled": self._enabled, "snapshot_sizes": snap_sizes, **self._stats}
