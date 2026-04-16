"""
Rebuild Coordinator
====================
Central nervous system for board refreshes.

Receives BoardEvents from all sources (odds delta, injury, game clock, manual, scheduler).
Deduplicates, classifies scope, enforces per-sport locks, and dispatches rebuilds.

Phase 1: SHADOW MODE (MLB) — logs decisions, does not dispatch
Phase 2: LIVE MODE (NBA) — coordinator owns NBA pipeline dispatching
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Dict, List, Optional, Set

from services.event_bus import BoardEvent, EventBus, get_event_bus

logger = logging.getLogger(__name__)

DEDUP_WINDOW_SECONDS = 30
REBUILD_COOLDOWN_SECONDS = 60


class RebuildScope(str, Enum):
    NO_OP = "no_op"
    TARGETED = "targeted"
    FULL = "full"


class RebuildCoordinator:
    """
    Ingests events, deduplicates, classifies scope, enforces locks, dispatches.

    Per-sport mode: each sport can be shadow or live independently.
    """

    def __init__(self):
        self._db = None

        # Per-sport mode: "shadow" or "live"
        self._sport_mode: Dict[str, str] = {
            "nba": "shadow",
            "mlb": "shadow",
        }

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
            "rebuilds_completed": 0,
            "rebuilds_failed": 0,
            "rebuilds_skipped_lock": 0,
            "rebuilds_skipped_cooldown": 0,
            "by_sport": {"nba": 0, "mlb": 0},
            "by_source": {},
            "by_event_type": {},
            "last_publish_counts": {},
        }

    def set_sport_mode(self, sport: str, mode: str):
        """Set a sport to 'live' or 'shadow'."""
        self._sport_mode[sport] = mode
        logger.info(f"[COORDINATOR] {sport.upper()} mode set to {mode.upper()}")

    @property
    def shadow_mode(self) -> bool:
        """Legacy compat: True if ALL sports are shadow."""
        return all(m == "shadow" for m in self._sport_mode.values())

    async def start(self, bus: Optional[EventBus] = None):
        """Subscribe to the event bus."""
        bus = bus or get_event_bus()
        bus.subscribe(self.handle_event)
        modes = ", ".join(f"{s.upper()}={m.upper()}" for s, m in self._sport_mode.items())
        logger.info(f"[COORDINATOR] Started — {modes}")

    async def handle_event(self, event: BoardEvent):
        """Main event handler — dedup, classify, dispatch."""
        self._stats["events_received"] += 1
        self._stats["by_source"][event.source] = self._stats["by_source"].get(event.source, 0) + 1
        self._stats["by_event_type"][event.event_type] = self._stats["by_event_type"].get(event.event_type, 0) + 1

        sport = event.sport
        mode = self._sport_mode.get(sport, "shadow")

        # Dedup check
        if self._is_duplicate(event):
            self._stats["events_deduped"] += 1
            logger.debug(f"[COORDINATOR] Deduped: {event.event_type}({sport}) from {event.source}")
            return

        self._recent_events[event.key] = event.timestamp
        self._prune_dedup_cache()

        # Classify scope
        scope = self._classify_scope(event)
        affected = event.affected_players[:5] if event.affected_players else ["board-wide"]

        if scope == RebuildScope.NO_OP:
            self._stats["scope_noop"] += 1
            logger.info(f"[COORDINATOR] NO-OP: {event.event_type}({sport}) severity={event.severity} source={event.source}")
            return

        if scope == RebuildScope.TARGETED:
            self._stats["scope_targeted"] += 1
        else:
            self._stats["scope_full"] += 1

        # Cooldown check (bypass for manual triggers)
        last = self._last_rebuild.get(sport)
        if last and event.event_type != "manual":
            elapsed = (datetime.now(timezone.utc) - last).total_seconds()
            if elapsed < REBUILD_COOLDOWN_SECONDS:
                self._stats["rebuilds_skipped_cooldown"] += 1
                logger.info(f"[COORDINATOR] COOLDOWN: {sport.upper()} skipped ({elapsed:.0f}s < {REBUILD_COOLDOWN_SECONDS}s)")
                return

        # Lock check (non-blocking)
        lock = self._locks.get(sport, asyncio.Lock())
        if lock.locked():
            self._stats["rebuilds_skipped_lock"] += 1
            logger.info(f"[COORDINATOR] LOCKED: {sport.upper()} rebuild in progress, skipping {event.event_type}")
            return

        # Dispatch
        self._stats["by_sport"][sport] = self._stats["by_sport"].get(sport, 0) + 1
        self._stats["rebuilds_dispatched"] += 1

        if mode == "shadow":
            logger.info(
                f"[COORDINATOR] SHADOW: {scope.value.upper()} {sport.upper()} | "
                f"trigger={event.event_type} source={event.source} affected={affected}"
            )
        else:
            logger.info(
                f"[COORDINATOR] LIVE DISPATCH: {scope.value.upper()} {sport.upper()} | "
                f"trigger={event.event_type} source={event.source}"
            )
            asyncio.create_task(self._execute_rebuild(sport, scope, event))

    async def _execute_rebuild(self, sport: str, scope: RebuildScope, event: BoardEvent):
        """
        Execute a pipeline rebuild via UnifiedPipeline.

        Verification logging:
        1. Trigger received
        2. Rebuild scope chosen
        3. Pipeline executed
        4. elite_* counts after publish
        5. Market Moves diff count
        """
        lock = self._locks.get(sport, asyncio.Lock())
        async with lock:
            self._last_rebuild[sport] = datetime.now(timezone.utc)
            start = datetime.now(timezone.utc)

            logger.info("=" * 70)
            logger.info(f"[COORDINATOR] REBUILD START: {sport.upper()}")
            logger.info(f"  Trigger: {event.event_type} from {event.source}")
            logger.info(f"  Scope: {scope.value.upper()}")
            logger.info(f"  Affected: {event.affected_players[:5] if event.affected_players else 'board-wide'}")
            logger.info("=" * 70)

            try:
                from services.unified_pipeline import UnifiedPipeline

                if sport == "nba":
                    from services.adapters.nba_adapter import NBAAdapter
                    adapter = NBAAdapter()
                elif sport == "mlb":
                    from services.adapters.mlb_adapter import MLBAdapter
                    adapter = MLBAdapter()
                else:
                    raise ValueError(f"Unknown sport: {sport}")

                pipeline = UnifiedPipeline(adapter, self._db)
                result = await pipeline.run()

                elapsed = (datetime.now(timezone.utc) - start).total_seconds()

                # Read final collection counts for verification
                col_map = adapter.tier_collections
                counts = {}
                for tier_name, col_name in col_map.items():
                    counts[col_name] = await self._db[col_name].count_documents({})

                # Read market moves generated in this run
                mm_count = 0
                try:
                    from services.market_moves_engine import get_recent_events
                    mm_events = await get_recent_events(self._db, sport=sport, limit=20)
                    mm_count = len(mm_events)
                except Exception:
                    pass

                self._stats["rebuilds_completed"] += 1
                self._stats["last_publish_counts"][sport] = {
                    "collections": counts,
                    "market_moves": mm_count,
                    "duration_s": round(elapsed, 1),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "run_id": result.run_id,
                    "success": result.success,
                }

                logger.info("=" * 70)
                logger.info(f"[COORDINATOR] REBUILD COMPLETE: {sport.upper()} ({elapsed:.1f}s)")
                logger.info(f"  Success: {result.success}")
                logger.info(f"  Pipeline run_id: {result.run_id}")
                for col_name, count in counts.items():
                    logger.info(f"  {col_name}: {count} picks")
                logger.info(f"  Market Moves events (active): {mm_count}")
                if result.errors:
                    logger.warning(f"  Errors: {result.errors}")
                logger.info("=" * 70)

            except Exception as e:
                self._stats["rebuilds_failed"] += 1
                elapsed = (datetime.now(timezone.utc) - start).total_seconds()
                logger.error(f"[COORDINATOR] REBUILD FAILED: {sport.upper()} ({elapsed:.1f}s) — {e}")
                import traceback
                traceback.print_exc()

    def _is_duplicate(self, event: BoardEvent) -> bool:
        key = event.key
        if key in self._recent_events:
            last_seen = self._recent_events[key]
            elapsed = (event.timestamp - last_seen).total_seconds()
            if elapsed < DEDUP_WINDOW_SECONDS:
                return True
        return False

    def _classify_scope(self, event: BoardEvent) -> RebuildScope:
        if event.event_type in ("manual", "scheduled_safety"):
            return RebuildScope.FULL
        if event.severity == "high" and event.affected_players:
            return RebuildScope.FULL
        if event.severity == "medium" and event.affected_players:
            return RebuildScope.TARGETED
        if event.severity == "low" and not event.affected_props:
            return RebuildScope.NO_OP
        if event.affected_players:
            return RebuildScope.TARGETED
        return RebuildScope.NO_OP

    def _prune_dedup_cache(self):
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=DEDUP_WINDOW_SECONDS * 3)
        expired = [k for k, ts in self._recent_events.items() if ts < cutoff]
        for k in expired:
            del self._recent_events[k]

    def set_db(self, db):
        self._db = db

    def get_stats(self) -> dict:
        return {
            "sport_modes": dict(self._sport_mode),
            "cooldown_seconds": REBUILD_COOLDOWN_SECONDS,
            "dedup_window_seconds": DEDUP_WINDOW_SECONDS,
            "last_rebuild": {
                sport: ts.isoformat() if ts else None
                for sport, ts in self._last_rebuild.items()
            },
            "locks": {sport: lock.locked() for sport, lock in self._locks.items()},
            "counters": self._stats,
            "last_publish": self._stats.get("last_publish_counts", {}),
        }


# Singleton
_coordinator: Optional[RebuildCoordinator] = None


def get_coordinator() -> RebuildCoordinator:
    global _coordinator
    if _coordinator is None:
        _coordinator = RebuildCoordinator()
    return _coordinator
