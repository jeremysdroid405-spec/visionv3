"""
Rebuild Coordinator v2
=======================
Central nervous system for board refreshes.

Phase 4: Event-driven with per-trigger-class toggles, hard rate limits,
and comprehensive metrics.

Features:
  - Per-sport mode (live/shadow)
  - Per-trigger-class enable/disable (injury, game_lock, odds_delta, manual, scheduled)
  - Dedup within configurable window
  - Hard cooldown per sport (prevents thrashing)
  - Max rebuilds per hour per sport
  - Full observability: events by type, scope counts, avg duration, publish counts
"""

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from services.event_bus import BoardEvent, EventBus, get_event_bus

logger = logging.getLogger(__name__)

DEDUP_WINDOW_SECONDS = 30
DEFAULT_COOLDOWN_SECONDS = 60
MAX_REBUILDS_PER_HOUR = 12  # hard cap: max 12 rebuilds/hr/sport = 1 every 5 min avg


class RebuildScope(str, Enum):
    NO_OP = "no_op"
    TARGETED = "targeted"
    FULL = "full"


class RebuildCoordinator:

    def __init__(self):
        self._db = None

        # Per-sport mode
        self._sport_mode: Dict[str, str] = {"nba": "shadow", "mlb": "shadow"}

        # Per-trigger-class toggles (all default ON except odds_delta)
        self._trigger_enabled: Dict[str, bool] = {
            "injury_change": True,
            "game_lock": True,
            "odds_delta": False,      # starts disabled until observed
            "manual": True,           # always on
            "scheduled_safety": True,  # always on
        }

        # Per-sport locks and cooldowns
        self._locks: Dict[str, asyncio.Lock] = {"nba": asyncio.Lock(), "mlb": asyncio.Lock()}
        self._cooldown_seconds: Dict[str, int] = {"nba": DEFAULT_COOLDOWN_SECONDS, "mlb": DEFAULT_COOLDOWN_SECONDS}

        # Dedup cache
        self._recent_events: Dict[str, datetime] = {}

        # Rate limit tracking: rolling window of rebuild timestamps per sport
        self._rebuild_history: Dict[str, deque] = {
            "nba": deque(maxlen=MAX_REBUILDS_PER_HOUR),
            "mlb": deque(maxlen=MAX_REBUILDS_PER_HOUR),
        }

        # Last rebuild timestamps
        self._last_rebuild: Dict[str, datetime] = {}

        # ---- Stage 1 (2026-04-20): unified master-sync state tracker.
        # Replaces route-level _mlb_master_sync_state. Identical semantics
        # for every sport — NBA and MLB share this one dict, NFL will too.
        self._master_sync_state: Dict[str, Dict[str, Any]] = {
            "nba": {"in_progress": False, "run_id": None, "started_at": None, "last_run": None},
            "mlb": {"in_progress": False, "run_id": None, "started_at": None, "last_run": None},
        }

        # ---- Metrics ----
        self._metrics = {
            "events_received": 0,
            "events_deduped": 0,
            "events_trigger_disabled": 0,
            "events_rate_limited": 0,
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
            "pipeline_durations": {"nba": [], "mlb": []},  # last 20 durations
        }

    # ---- Configuration ----

    def set_sport_mode(self, sport: str, mode: str):
        self._sport_mode[sport] = mode
        logger.info(f"[COORDINATOR] {sport.upper()} mode set to {mode.upper()}")

    def set_trigger_enabled(self, trigger_type: str, enabled: bool):
        self._trigger_enabled[trigger_type] = enabled
        state = "ENABLED" if enabled else "DISABLED"
        logger.info(f"[COORDINATOR] Trigger class '{trigger_type}' {state}")

    def set_cooldown(self, sport: str, seconds: int):
        self._cooldown_seconds[sport] = seconds
        logger.info(f"[COORDINATOR] {sport.upper()} cooldown set to {seconds}s")

    @property
    def shadow_mode(self) -> bool:
        return all(m == "shadow" for m in self._sport_mode.values())

    async def start(self, bus: Optional[EventBus] = None):
        bus = bus or get_event_bus()
        bus.subscribe(self.handle_event)
        modes = ", ".join(f"{s.upper()}={m.upper()}" for s, m in self._sport_mode.items())
        triggers = ", ".join(f"{t}={'ON' if e else 'OFF'}" for t, e in self._trigger_enabled.items())
        logger.info(f"[COORDINATOR] Started — {modes}")
        logger.info(f"[COORDINATOR] Triggers — {triggers}")

    def set_db(self, db):
        self._db = db

    # ---- Event Handler ----

    async def handle_event(self, event: BoardEvent):
        self._metrics["events_received"] += 1
        self._metrics["by_source"][event.source] = self._metrics["by_source"].get(event.source, 0) + 1
        self._metrics["by_event_type"][event.event_type] = self._metrics["by_event_type"].get(event.event_type, 0) + 1

        sport = event.sport
        mode = self._sport_mode.get(sport, "shadow")

        # Check trigger class enabled
        if not self._trigger_enabled.get(event.event_type, True):
            self._metrics["events_trigger_disabled"] += 1
            logger.debug(f"[COORDINATOR] TRIGGER DISABLED: {event.event_type}({sport})")
            return

        # Dedup
        if self._is_duplicate(event):
            self._metrics["events_deduped"] += 1
            return

        self._recent_events[event.key] = event.timestamp
        self._prune_dedup_cache()

        # Classify scope
        scope = self._classify_scope(event)

        if scope == RebuildScope.NO_OP:
            self._metrics["scope_noop"] += 1
            logger.info(f"[COORDINATOR] NO-OP: {event.event_type}({sport}) severity={event.severity}")
            return

        if scope == RebuildScope.TARGETED:
            self._metrics["scope_targeted"] += 1
        else:
            self._metrics["scope_full"] += 1

        # Cooldown check (bypass for manual)
        if event.event_type != "manual":
            last = self._last_rebuild.get(sport)
            cooldown = self._cooldown_seconds.get(sport, DEFAULT_COOLDOWN_SECONDS)
            if last:
                elapsed = (datetime.now(timezone.utc) - last).total_seconds()
                if elapsed < cooldown:
                    self._metrics["rebuilds_skipped_cooldown"] += 1
                    logger.info(f"[COORDINATOR] COOLDOWN: {sport.upper()} ({elapsed:.0f}s < {cooldown}s)")
                    return

        # Rate limit check (bypass for manual)
        if event.event_type != "manual":
            history = self._rebuild_history.get(sport, deque())
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=1)
            recent = sum(1 for t in history if t > cutoff)
            if recent >= MAX_REBUILDS_PER_HOUR:
                self._metrics["events_rate_limited"] += 1
                logger.warning(f"[COORDINATOR] RATE LIMITED: {sport.upper()} ({recent}/{MAX_REBUILDS_PER_HOUR} rebuilds/hr)")
                return

        # Lock check
        lock = self._locks.get(sport, asyncio.Lock())
        if lock.locked():
            self._metrics["rebuilds_skipped_lock"] += 1
            logger.info(f"[COORDINATOR] LOCKED: {sport.upper()} rebuild in progress")
            return

        # Dispatch
        self._metrics["by_sport"][sport] = self._metrics["by_sport"].get(sport, 0) + 1
        self._metrics["rebuilds_dispatched"] += 1
        affected = event.affected_players[:5] if event.affected_players else ["board-wide"]

        if mode == "shadow":
            logger.info(f"[COORDINATOR] SHADOW: {scope.value.upper()} {sport.upper()} | {event.event_type} from {event.source} affected={affected}")
        else:
            logger.info(f"[COORDINATOR] LIVE DISPATCH: {scope.value.upper()} {sport.upper()} | {event.event_type} from {event.source}")
            asyncio.create_task(self._execute_rebuild(sport, scope, event))

    # ---- Rebuild Execution ----

    async def _execute_rebuild(self, sport: str, scope: RebuildScope, event: BoardEvent):
        lock = self._locks.get(sport, asyncio.Lock())
        async with lock:
            now = datetime.now(timezone.utc)
            self._last_rebuild[sport] = now
            self._rebuild_history[sport].append(now)
            start_time = time.monotonic()

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

                duration = time.monotonic() - start_time

                # Collection counts
                counts = {}
                for tier_name, col_name in adapter.tier_collections.items():
                    counts[col_name] = await self._db[col_name].count_documents({})

                # Market Moves count
                mm_count = 0
                try:
                    from services.market_moves_engine import get_recent_events
                    mm_events = await get_recent_events(self._db, sport=sport, limit=20)
                    mm_count = len(mm_events)
                except Exception:
                    pass

                # Update metrics
                self._metrics["rebuilds_completed"] += 1
                durations = self._metrics["pipeline_durations"][sport]
                durations.append(round(duration, 1))
                if len(durations) > 20:
                    self._metrics["pipeline_durations"][sport] = durations[-20:]

                self._metrics["last_publish_counts"][sport] = {
                    "collections": counts,
                    "market_moves": mm_count,
                    "duration_s": round(duration, 1),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "run_id": result.run_id,
                    "success": result.success,
                    "trigger": event.event_type,
                    "source": event.source,
                }

                logger.info("=" * 70)
                logger.info(f"[COORDINATOR] REBUILD COMPLETE: {sport.upper()} ({duration:.1f}s)")
                logger.info(f"  Success: {result.success}")
                logger.info(f"  Pipeline run_id: {result.run_id}")
                for col_name, count in counts.items():
                    logger.info(f"  {col_name}: {count} picks")
                logger.info(f"  Market Moves events (active): {mm_count}")
                if result.errors:
                    logger.warning(f"  Errors: {result.errors}")
                logger.info("=" * 70)

            except Exception as e:
                duration = time.monotonic() - start_time
                self._metrics["rebuilds_failed"] += 1
                logger.error(f"[COORDINATOR] REBUILD FAILED: {sport.upper()} ({duration:.1f}s) — {e}")
                import traceback
                traceback.print_exc()

    # ---- Helpers ----

    def _is_duplicate(self, event: BoardEvent) -> bool:
        key = event.key
        if key in self._recent_events:
            elapsed = (event.timestamp - self._recent_events[key]).total_seconds()
            if elapsed < DEDUP_WINDOW_SECONDS:
                return True
        return False

    def _classify_scope(self, event: BoardEvent) -> RebuildScope:
        if event.event_type in ("manual", "scheduled_safety", "scored_data_refresh"):
            return RebuildScope.FULL
        if event.severity == "high":
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

    # ---- Observability ----

    def get_stats(self) -> dict:
        # Compute avg durations
        avg_durations = {}
        for sport, durations in self._metrics["pipeline_durations"].items():
            if durations:
                avg_durations[sport] = round(sum(durations) / len(durations), 1)

        # Rate limit status
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=1)
        rate_status = {}
        for sport, history in self._rebuild_history.items():
            recent = sum(1 for t in history if t > cutoff)
            rate_status[sport] = f"{recent}/{MAX_REBUILDS_PER_HOUR}"

        return {
            "sport_modes": dict(self._sport_mode),
            "trigger_classes": dict(self._trigger_enabled),
            "cooldowns": dict(self._cooldown_seconds),
            "max_rebuilds_per_hour": MAX_REBUILDS_PER_HOUR,
            "rate_limit_status": rate_status,
            "last_rebuild": {s: ts.isoformat() if ts else None for s, ts in self._last_rebuild.items()},
            "locks": {s: lock.locked() for s, lock in self._locks.items()},
            "avg_pipeline_duration_s": avg_durations,
            "counters": self._metrics,
            "last_publish": self._metrics.get("last_publish_counts", {}),
            "master_sync_state": dict(self._master_sync_state),
        }

    # ------------------------------------------------------------------
    # Stage 1 (2026-04-20): Unified per-sport master-sync dispatcher.
    #
    # Single entrypoint for /api/{sport}/sync/master. Spawns the sport's
    # master-sync class (NBAMasterSync / MLBMasterSync) as an asyncio
    # background task so the HTTP caller gets an immediate 202-style
    # response (no proxy timeout), while the actual pipeline runs to
    # completion. State is tracked per-sport in `_master_sync_state`;
    # both sports share identical response shape and state semantics.
    # ------------------------------------------------------------------
    async def dispatch_master_sync(self, sport: str) -> Dict[str, Any]:
        sport = (sport or "").lower()
        if sport not in self._master_sync_state:
            raise ValueError(f"Unknown sport for master_sync: {sport!r}")

        state = self._master_sync_state[sport]
        if state.get("in_progress"):
            return {
                "accepted": False,
                "reason": "already_running",
                "sport": sport,
                "run_id": state.get("run_id"),
                "started_at": state.get("started_at"),
                "last_run": state.get("last_run"),
            }

        import uuid
        run_id = str(uuid.uuid4())[:8]
        state["in_progress"] = True
        state["started_at"] = datetime.now(timezone.utc).isoformat()
        state["run_id"] = run_id

        async def _runner():
            try:
                if sport == "nba":
                    from services.nba_master_sync import get_nba_master_sync
                    metrics = await get_nba_master_sync(self._db).run_full_pipeline()
                elif sport == "mlb":
                    # Final carbon-copy enforcement (2026-04-21):
                    # `services/mlb_master_sync.py` is deleted. MLB master
                    # sync now runs through the sport-agnostic
                    # `UnifiedPipeline.run_master_sync()` using the
                    # `PipelineStep` chain registered on `MLBAdapter`.
                    # Eliminates the D1 residual class dependency.
                    from services.unified_pipeline import UnifiedPipeline
                    from services.adapters.mlb_adapter import MLBAdapter
                    pipeline = UnifiedPipeline(MLBAdapter(), self._db)
                    metrics = await pipeline.run_master_sync()
                else:
                    raise ValueError(f"Unsupported sport: {sport}")
                state["last_run"] = {
                    "run_id": run_id,
                    "success": metrics.get("success", True),
                    "total_duration_seconds": metrics.get("total_duration_seconds"),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "step_durations_s": {
                        k: round((v or {}).get("duration_seconds", 0), 2)
                        for k, v in (metrics.get("steps") or metrics.get("phases") or {}).items()
                    },
                    "errors": metrics.get("errors", []),
                }
            except Exception as exc:
                logger.exception(f"[COORDINATOR] master_sync({sport}) run_id={run_id} failed")
                state["last_run"] = {
                    "run_id": run_id,
                    "success": False,
                    "error": str(exc),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
            finally:
                state["in_progress"] = False

        asyncio.create_task(_runner())

        return {
            "accepted": True,
            "sport": sport,
            "dispatch": "coordinator.dispatch_master_sync()",
            "run_id": run_id,
            "started_at": state["started_at"],
            "last_run": state.get("last_run"),
        }


# Singleton
_coordinator: Optional[RebuildCoordinator] = None


def get_coordinator() -> RebuildCoordinator:
    global _coordinator
    if _coordinator is None:
        _coordinator = RebuildCoordinator()
    return _coordinator
