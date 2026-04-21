"""
SCHEDULED_SPORTS registry — unified scheduler config for every live sport.

Stage 8 (2026-04-21, MLB↔NBA carbon-copy): single point of truth for which
sports participate in scheduled master-sync orchestration and on what cadence.
Adding a new sport (NFL) to the scheduler is a ONE-LINE registry entry —
no hand-written `scheduled_hourly_<sport>_full_sync` function, no
per-sport APScheduler registration edits. Eliminates D7.

Every registered sport receives:
  * An interval master-sync job (`{sport}_master_sync_interval`) that
    publishes a BoardEvent → RebuildCoordinator.dispatch_master_sync,
    identical to what the public `/api/{sport}/sync/master` endpoint does.
  * A daily deep-refresh cron (`{sport}_daily_refresh`) that additionally
    runs the sport's full daily pipeline at the configured time.

Sport-specific ingest workflows (NBA.com L5/L10 scrapers, BDL enrichment,
per-league batches) remain as separate scheduler jobs — those are not
master-sync orchestration and therefore outside Stage 8's scope.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduledSportConfig:
    """Per-sport scheduler configuration. Immutable: sports are registered
    once at import time via the `SCHEDULED_SPORTS` module-level dict."""
    sport: str
    master_sync_interval_minutes: int = 60
    # Daily deep-refresh cron in UTC (hour/minute). None → skip daily cron.
    daily_refresh_cron_utc: Optional[Dict[str, int]] = None
    # Severity for the scheduler-driven BoardEvent. Medium by default.
    event_severity: str = "medium"
    # Phase D5 (2026-04-21) — Delta Engine near-real-time cadence.
    # When `delta_enabled=True`, server startup spawns one
    # `asyncio.create_task(DeltaEngine.run_forever(sport, delta_interval_seconds))`.
    # Eliminating a sport from the delta loop = one flag flip here.
    delta_enabled: bool = True
    delta_interval_seconds: int = 20
    # Phase D6 (2026-04-21) — rescore batch cap per tick. Prevents a
    # single tick from rescoring thousands of newly-synced props post-
    # full-sync and breaching the tick interval. Overflow NEW keys
    # naturally re-surface next tick via the set-diff detector; overflow
    # UPDATED keys are deferred to the next tick that sees them (their
    # `updated_at` is already > watermark). Set to 0 / negative to
    # disable the cap.
    delta_rescore_batch_cap: int = 500


# ---------------------------------------------------------------------------
# REGISTRY
# ---------------------------------------------------------------------------
# Adding a new sport is one line below. No server.py edits required.
# ---------------------------------------------------------------------------
SCHEDULED_SPORTS: Dict[str, ScheduledSportConfig] = {
    "nba": ScheduledSportConfig(
        sport="nba",
        master_sync_interval_minutes=60,
        # NBA daily refresh at 4:20 AM EST (09:20 UTC during EDT).
        daily_refresh_cron_utc={"hour": 9, "minute": 20},
        # D5 delta-engine cadence for NBA: 20s ticks.
        delta_enabled=True,
        delta_interval_seconds=20,
    ),
    "mlb": ScheduledSportConfig(
        sport="mlb",
        master_sync_interval_minutes=60,
        # MLB daily refresh at 4:23 AM EST (09:23 UTC during EDT) —
        # 3 minutes after NBA to give daily odds APIs breathing room.
        daily_refresh_cron_utc={"hour": 9, "minute": 23},
        # D5 delta-engine cadence for MLB: 30s ticks (prop churn is
        # lower than NBA; extra 10s of slack is fine).
        delta_enabled=True,
        delta_interval_seconds=30,
    ),
}


async def run_scheduled_master_sync(sport: str) -> None:
    """Canonical scheduled-master-sync entry point for every registered sport.

    Publishes a `scheduled_safety` BoardEvent which the
    `RebuildCoordinator` consumes and dispatches to
    `UnifiedPipeline({SportAdapter})` — the same code path manually
    triggered by `POST /api/{sport}/sync/master`.

    This replaces the former per-sport `scheduled_hourly_full_sync` and
    `scheduled_hourly_mlb_full_sync` functions, eliminating D7.
    """
    config = SCHEDULED_SPORTS.get(sport)
    if config is None:
        logger.warning(f"[SCHEDULER] sport={sport!r} not in SCHEDULED_SPORTS; skipping")
        return

    logger.info("=" * 70)
    logger.info(f"[SCHEDULER] SCHEDULED MASTER SYNC — sport={sport}")
    logger.info(f"[SCHEDULER] Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 70)

    try:
        from services.event_bus import BoardEvent, get_event_bus
        await get_event_bus().publish(BoardEvent(
            sport=sport,
            event_type="scheduled_safety",
            severity=config.event_severity,
            source=f"scheduler_master_sync_{sport}",
        ))
    except Exception as e:
        logger.error(f"[SCHEDULER] {sport} scheduled master sync dispatch failed: {e}")


def make_sport_interval_job(sport: str):
    """DEPRECATED in favour of the pre-built module-level functions below.
    Retained so external callers/tests that imported the factory still
    work, but module-level callables are preferred for APScheduler +
    MongoDBJobStore (which requires textual references)."""
    async def _interval_job():
        await run_scheduled_master_sync(sport)
    _interval_job.__name__ = f"scheduled_master_sync_{sport}"
    return _interval_job


def make_sport_daily_cron_job(sport: str):
    """DEPRECATED — see `make_sport_interval_job`."""
    async def _daily_job():
        await run_scheduled_master_sync(sport)
    _daily_job.__name__ = f"scheduled_daily_refresh_{sport}"
    return _daily_job


# ---------------------------------------------------------------------------
# Module-level per-sport job callables.
# ---------------------------------------------------------------------------
# APScheduler's MongoDBJobStore serialises a *textual reference*
# (`module:function_name`) rather than pickling the callable, so every
# registered job must resolve to a module-level attribute. Factory
# closures fail serialisation. The canonical pattern is to pre-build
# one small module-level function per sport that simply delegates to
# `run_scheduled_master_sync(sport)`. Adding a new sport below + one
# entry in `SCHEDULED_SPORTS` is the whole extension surface.
# ---------------------------------------------------------------------------

async def scheduled_master_sync_nba() -> None:
    """NBA scheduled master-sync entry point (module-level, serialisable)."""
    await run_scheduled_master_sync("nba")


async def scheduled_master_sync_mlb() -> None:
    """MLB scheduled master-sync entry point (module-level, serialisable)."""
    await run_scheduled_master_sync("mlb")


# Lookup table keyed by sport: `SPORT_INTERVAL_CALLABLES[sport]` returns
# the serialisable module-level callable for APScheduler registration.
# server.py iterates this at startup. Extending to NFL = add one line here
# plus one line in `SCHEDULED_SPORTS`.
SPORT_INTERVAL_CALLABLES: Dict[str, callable] = {
    "nba": scheduled_master_sync_nba,
    "mlb": scheduled_master_sync_mlb,
}


# ---------------------------------------------------------------------------
# D5 (2026-04-21) — Delta Engine continuous-loop startup helpers.
# ---------------------------------------------------------------------------
# Sport-agnostic startup + shutdown. `server.py` calls these once at
# `@app.on_event("startup")` and `@app.on_event("shutdown")`. Adding a
# new sport to the delta loop = one entry in SCHEDULED_SPORTS above
# (with `delta_enabled=True`); zero `server.py` edits needed.
# ---------------------------------------------------------------------------
import asyncio as _asyncio

# Tracks per-sport background tasks so shutdown can cancel them cleanly.
_DELTA_TASKS: Dict[str, "_asyncio.Task"] = {}


def start_delta_engine_loops(db) -> Dict[str, Any]:
    """Spawn one `DeltaEngine.run_forever(sport)` task per delta-enabled sport.

    Idempotent: if a task for a given sport is already running, it is
    NOT restarted. Returns a summary dict for logging.
    """
    from services.delta_engine import get_delta_engine
    engine = get_delta_engine(db)
    started: Dict[str, Any] = {}
    for sport, cfg in SCHEDULED_SPORTS.items():
        if not cfg.delta_enabled:
            started[sport] = {"started": False, "reason": "delta_disabled"}
            continue
        existing = _DELTA_TASKS.get(sport)
        if existing is not None and not existing.done():
            started[sport] = {
                "started": False,
                "reason": "already_running",
                "interval_s": cfg.delta_interval_seconds,
            }
            continue
        task = _asyncio.create_task(
            engine.run_forever(sport, interval_seconds=cfg.delta_interval_seconds),
            name=f"delta_engine_{sport}",
        )
        _DELTA_TASKS[sport] = task
        started[sport] = {
            "started": True,
            "interval_s": cfg.delta_interval_seconds,
        }
        logger.info(
            f"[DELTA_STARTUP] sport={sport} run_forever task spawned "
            f"(interval={cfg.delta_interval_seconds}s)"
        )
    return started


async def stop_delta_engine_loops() -> Dict[str, Any]:
    """Cancel all running delta-engine tasks. Called at FastAPI shutdown."""
    results: Dict[str, Any] = {}
    for sport, task in list(_DELTA_TASKS.items()):
        if task.done():
            results[sport] = {"stopped": False, "reason": "already_done"}
            continue
        task.cancel()
        try:
            await task
        except _asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            results[sport] = {"stopped": True, "error_on_shutdown": str(exc)}
            continue
        results[sport] = {"stopped": True}
        logger.info(f"[DELTA_SHUTDOWN] sport={sport} run_forever task cancelled")
    _DELTA_TASKS.clear()
    return results


def describe_delta_engine_loops() -> Dict[str, Any]:
    """Introspection helper — returns per-sport task state for diagnostics."""
    out: Dict[str, Any] = {}
    for sport, cfg in SCHEDULED_SPORTS.items():
        task = _DELTA_TASKS.get(sport)
        out[sport] = {
            "delta_enabled": cfg.delta_enabled,
            "delta_interval_seconds": cfg.delta_interval_seconds,
            "running": bool(task and not task.done()),
            "task_name": task.get_name() if task else None,
        }
    return out
