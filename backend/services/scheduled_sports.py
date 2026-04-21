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
from typing import Dict, Optional

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
    ),
    "mlb": ScheduledSportConfig(
        sport="mlb",
        master_sync_interval_minutes=60,
        # MLB daily refresh at 4:23 AM EST (09:23 UTC during EDT) —
        # 3 minutes after NBA to give daily odds APIs breathing room.
        daily_refresh_cron_utc={"hour": 9, "minute": 23},
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
