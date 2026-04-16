"""
Rebuild Coordinator
====================
Central nervous system for board refreshes.

Receives BoardEvents from all sources (odds delta, injury, game clock, manual, scheduler).
Deduplicates, classifies scope, enforces per-sport locks, and dispatches rebuilds.

Phase 1: SHADOW MODE
  - Receives and classifies all events
  - Logs what it WOULD do
  - Tracks observability counters
  - Does NOT dispatch actual pipeline runs yet
  - Existing scheduler/endpoints continue to own live publishes

Phase 2+: LIVE MODE
  - Coordinator owns all pipeline dispatching
  - Existing scheduler reduced to safety-only
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Dict, List, Optional, Set

from services.event_bus import BoardEvent, EventBus, get_event_bus

logger = logging.getLogger(__name__)

# Dedup window: ignore duplicate events within this window
DEDUP_WINDOW_SECONDS = 30

# Cooldown: minimum time between rebuilds for the same sport
REBUILD_COOLDOWN_SECONDS = 60


class RebuildScope(str, Enum):
    NO_OP = "no_op"
    TARGETED = "targeted"
    FULL = "full"


class RebuildCoordinator:
    """
    Ingests events, deduplicates, classifies scope, enforces locks.

    Phase 1: shadow mode — logs decisions, does not dispatch.
    """

    def __init__(self, shadow_mode: bool = True):
        self.shadow_mode = shadow_mode

        # Per-sport locks
        self._locks: Dict[str, asyncio.Lock] = {
            "nba": asyncio.Lock(),
            "mlb": asyncio.Lock(),
        }

        # Dedup: recent event keys with timestamps
        self._recent_events: Dict[str, datetime] = {}

        # Last rebuild timestamps per sport
        self._last_rebuild: Dict[str, datetime] = {}

        # Observability counters
        self._stats = {
            "events_received": 0,
            "events_deduped": 0,
            "scope_noop": 0,
            "scope_targeted": 0,
            "scope_full": 0,
            "rebuilds_dispatched": 0,
            "rebuilds_skipped_lock": 0,
            "rebuilds_skipped_cooldown": 0,
            "by_sport": {"nba": 0, "mlb": 0},
            "by_source": {},
            "by_event_type": {},
        }

    async def start(self, bus: Optional[EventBus] = None):
        """Subscribe to the event bus."""
        bus = bus or get_event_bus()
        bus.subscribe(self.handle_event)
        mode = "SHADOW" if self.shadow_mode else "LIVE"
        logger.info(f"[COORDINATOR] Started in {mode} mode")

    async def handle_event(self, event: BoardEvent):
        """Main event handler — dedup, classify, dispatch (or log in shadow mode)."""
        self._stats["events_received"] += 1
        self._stats["by_source"][event.source] = self._stats["by_source"].get(event.source, 0) + 1
        self._stats["by_event_type"][event.event_type] = self._stats["by_event_type"].get(event.event_type, 0) + 1

        # Dedup check
        if self._is_duplicate(event):
            self._stats["events_deduped"] += 1
            logger.debug(f"[COORDINATOR] Deduped: {event.event_type}({event.sport}) from {event.source}")
            return

        # Record for dedup
        self._recent_events[event.key] = event.timestamp
        self._prune_dedup_cache()

        # Classify scope
        scope = self._classify_scope(event)
        affected = event.affected_players[:5] if event.affected_players else ["board-wide"]

        if scope == RebuildScope.NO_OP:
            self._stats["scope_noop"] += 1
            logger.info(
                f"[COORDINATOR] NO-OP: {event.event_type}({event.sport}) "
                f"severity={event.severity} source={event.source}"
            )
            return

        if scope == RebuildScope.TARGETED:
            self._stats["scope_targeted"] += 1
        else:
            self._stats["scope_full"] += 1

        # Cooldown check
        last = self._last_rebuild.get(event.sport)
        if last:
            elapsed = (datetime.now(timezone.utc) - last).total_seconds()
            if elapsed < REBUILD_COOLDOWN_SECONDS and event.event_type != "manual":
                self._stats["rebuilds_skipped_cooldown"] += 1
                logger.info(
                    f"[COORDINATOR] COOLDOWN: {event.sport.upper()} rebuild skipped "
                    f"({elapsed:.0f}s < {REBUILD_COOLDOWN_SECONDS}s). Event: {event.event_type}"
                )
                return

        # Lock check (non-blocking)
        lock = self._locks.get(event.sport, asyncio.Lock())
        if lock.locked():
            self._stats["rebuilds_skipped_lock"] += 1
            logger.info(
                f"[COORDINATOR] LOCKED: {event.sport.upper()} rebuild in progress. "
                f"Skipping {event.event_type} from {event.source}"
            )
            return

        # Dispatch
        self._stats["by_sport"][event.sport] = self._stats["by_sport"].get(event.sport, 0) + 1

        if self.shadow_mode:
            self._stats["rebuilds_dispatched"] += 1
            logger.info(
                f"[COORDINATOR] SHADOW DISPATCH: {scope.value.upper()} rebuild for {event.sport.upper()} | "
                f"trigger={event.event_type} source={event.source} severity={event.severity} "
                f"affected={affected}"
            )
        else:
            # Phase 2+: actual dispatch
            self._stats["rebuilds_dispatched"] += 1
            logger.info(
                f"[COORDINATOR] LIVE DISPATCH: {scope.value.upper()} rebuild for {event.sport.upper()} | "
                f"trigger={event.event_type} source={event.source}"
            )
            asyncio.create_task(self._execute_rebuild(event.sport, scope, event))

    async def _execute_rebuild(self, sport: str, scope: RebuildScope, event: BoardEvent):
        """Execute an actual pipeline rebuild. Only used in LIVE mode."""
        lock = self._locks.get(sport, asyncio.Lock())
        async with lock:
            self._last_rebuild[sport] = datetime.now(timezone.utc)
            try:
                # Import here to avoid circular deps
                if sport == "nba":
                    from services.nba_master_sync import get_nba_master_sync
                    sync = get_nba_master_sync(self._db)
                    await sync.run_elite_pipeline()
                elif sport == "mlb":
                    from services.mlb_pipeline import run_mlb_pipeline
                    await run_mlb_pipeline(self._db)

                logger.info(f"[COORDINATOR] {sport.upper()} {scope.value} rebuild complete")
            except Exception as e:
                logger.error(f"[COORDINATOR] {sport.upper()} rebuild failed: {e}")

    def _is_duplicate(self, event: BoardEvent) -> bool:
        """Check if this event was seen recently."""
        key = event.key
        if key in self._recent_events:
            last_seen = self._recent_events[key]
            elapsed = (event.timestamp - last_seen).total_seconds()
            if elapsed < DEDUP_WINDOW_SECONDS:
                return True
        return False

    def _classify_scope(self, event: BoardEvent) -> RebuildScope:
        """Determine rebuild scope from event characteristics."""
        # Manual triggers always get full rebuild
        if event.event_type == "manual":
            return RebuildScope.FULL

        # Scheduled safety refresh → full
        if event.event_type == "scheduled_safety":
            return RebuildScope.FULL

        # High severity with affected board picks → full
        if event.severity == "high" and event.affected_players:
            return RebuildScope.FULL

        # Medium severity with specific affected players → targeted
        if event.severity == "medium" and event.affected_players:
            return RebuildScope.TARGETED

        # Low severity with no board impact → no-op
        if event.severity == "low" and not event.affected_props:
            return RebuildScope.NO_OP

        # Default: targeted if there are affected players, otherwise no-op
        if event.affected_players:
            return RebuildScope.TARGETED

        return RebuildScope.NO_OP

    def _prune_dedup_cache(self):
        """Remove expired entries from dedup cache."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=DEDUP_WINDOW_SECONDS * 3)
        expired = [k for k, ts in self._recent_events.items() if ts < cutoff]
        for k in expired:
            del self._recent_events[k]

    def set_db(self, db):
        """Set database reference for live mode dispatching."""
        self._db = db

    def get_stats(self) -> dict:
        """Full observability snapshot."""
        return {
            "mode": "shadow" if self.shadow_mode else "live",
            "cooldown_seconds": REBUILD_COOLDOWN_SECONDS,
            "dedup_window_seconds": DEDUP_WINDOW_SECONDS,
            "last_rebuild": {
                sport: ts.isoformat() if ts else None
                for sport, ts in self._last_rebuild.items()
            },
            "locks": {
                sport: lock.locked()
                for sport, lock in self._locks.items()
            },
            "counters": self._stats,
        }


# Singleton
_coordinator: Optional[RebuildCoordinator] = None


def get_coordinator(shadow_mode: bool = True) -> RebuildCoordinator:
    global _coordinator
    if _coordinator is None:
        _coordinator = RebuildCoordinator(shadow_mode=shadow_mode)
    return _coordinator
