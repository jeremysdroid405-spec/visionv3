"""
Delta Engine — Per-Sport Tick Driver (D4)
==========================================
Phase D4 (2026-04-21). Orchestrates the D3 step chain on a per-sport
schedule. Same framework for NBA, MLB, and future sports.

Public surface:
    engine = get_delta_engine(db)
    result = await engine.tick("nba")          # one manual tick
    task   = asyncio.create_task(
        engine.run_forever("nba", interval_seconds=20)
    )                                           # continuous loop

The engine does NOT auto-start — scheduler registration is D5's job.
D3+D4 exposes a manual admin trigger (`POST /api/v3/admin/delta/run-once/{sport}`)
and the `run_forever` loop for future use.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from services.pipeline.delta_steps import DEFAULT_DELTA_STEPS, DeltaStep

logger = logging.getLogger(__name__)


@dataclass
class DeltaTickResult:
    sport: str
    tick_id: str
    success: bool
    started_at: datetime
    completed_at: datetime
    total_duration_seconds: float
    steps: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    skipped: bool = False
    skipped_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sport": self.sport,
            "tick_id": self.tick_id,
            "success": self.success,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "total_duration_seconds": round(self.total_duration_seconds, 4),
            "steps": self.steps,
            "errors": self.errors,
            "skipped": self.skipped,
            "skipped_reason": self.skipped_reason,
        }


class DeltaEngine:
    """Per-sport tick driver."""

    def __init__(self, db, steps: Sequence[DeltaStep] = DEFAULT_DELTA_STEPS):
        self._db = db
        self._steps: Sequence[DeltaStep] = tuple(steps)
        # Per-sport tick-serialisation lock — prevents overlapping ticks
        # for the SAME sport. Different sports run in parallel.
        self._tick_locks: Dict[str, asyncio.Lock] = {}
        # Observability: last tick result per sport + running totals.
        self._last_tick: Dict[str, DeltaTickResult] = {}
        self._total_ticks: Dict[str, int] = {}
        self._skipped_ticks: Dict[str, int] = {}
        self._running: Dict[str, bool] = {}

    def _tick_lock(self, sport: str) -> asyncio.Lock:
        if sport not in self._tick_locks:
            self._tick_locks[sport] = asyncio.Lock()
        return self._tick_locks[sport]

    async def tick(self, sport: str) -> DeltaTickResult:
        """Run one delta tick for `sport`. Serialised per-sport via
        `_tick_lock(sport)` — if a tick is already running, this call
        immediately returns a skipped result."""
        sport = (sport or "").lower()
        tick_id = uuid.uuid4().hex[:8]
        lock = self._tick_lock(sport)
        started = datetime.now(timezone.utc)

        if lock.locked():
            self._skipped_ticks[sport] = self._skipped_ticks.get(sport, 0) + 1
            logger.info(f"[DELTA:{sport}] tick={tick_id} SKIPPED — prior tick still running")
            return DeltaTickResult(
                sport=sport, tick_id=tick_id, success=True,
                started_at=started, completed_at=datetime.now(timezone.utc),
                total_duration_seconds=0.0,
                skipped=True, skipped_reason="prior_tick_in_progress",
            )

        async with lock:
            self._total_ticks[sport] = self._total_ticks.get(sport, 0) + 1
            context: Dict[str, Any] = {
                "tick_started_at": started,
                "tick_id": tick_id,
                "errors": [],
            }
            result = DeltaTickResult(
                sport=sport, tick_id=tick_id, success=True,
                started_at=started, completed_at=started,
                total_duration_seconds=0.0,
            )

            t0 = datetime.now(timezone.utc)
            for step in self._steps:
                try:
                    step_metrics = await step.run(sport, self._db, context)
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        f"[DELTA:{sport}] tick={tick_id} step={step.name} FAILED"
                    )
                    context["errors"].append(f"{step.name}: {exc}")
                    result.errors.append(f"{step.name}: {exc}")
                    result.success = False
                    result.steps[step.name] = {"error": str(exc)}
                    # Continue the chain (matches PipelineStep behaviour).
                    continue
                result.steps[step.name] = step_metrics

            if context.get("skipped_due_to_full_sync"):
                result.skipped = True
                result.skipped_reason = "upstream_lock_held"

            result.completed_at = datetime.now(timezone.utc)
            result.total_duration_seconds = (
                result.completed_at - t0
            ).total_seconds()
            self._last_tick[sport] = result
            return result

    async def run_forever(
        self,
        sport: str,
        interval_seconds: int = 20,
    ) -> None:
        """Long-running tick loop. Cancellation-safe.

        Not auto-started in D4 — scheduler registration (D5) will launch
        one of these tasks per registered sport at server startup.
        """
        sport = (sport or "").lower()
        self._running[sport] = True
        logger.info(
            f"[DELTA:{sport}] run_forever START interval={interval_seconds}s"
        )
        try:
            while True:
                try:
                    await self.tick(sport)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Last-resort safety: never let a single tick kill the loop.
                    logger.exception(f"[DELTA:{sport}] unexpected tick failure")
                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info(f"[DELTA:{sport}] run_forever CANCELLED (clean shutdown)")
            raise
        finally:
            self._running[sport] = False

    # -- Observability ---------------------------------------------------

    def describe(self, sport: Optional[str] = None) -> Dict[str, Any]:
        """Engine status — safe to call from admin endpoints."""
        def _for(s: str) -> Dict[str, Any]:
            last = self._last_tick.get(s)
            return {
                "sport": s,
                "running": bool(self._running.get(s)),
                "total_ticks": self._total_ticks.get(s, 0),
                "skipped_ticks": self._skipped_ticks.get(s, 0),
                "last_tick": last.to_dict() if last else None,
            }
        if sport:
            return _for(sport.lower())
        sports = set(
            list(self._last_tick.keys())
            + list(self._total_ticks.keys())
            + list(self._running.keys())
        )
        return {"per_sport": {s: _for(s) for s in sorted(sports)}}


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_engine: Optional[DeltaEngine] = None


def get_delta_engine(db=None) -> DeltaEngine:
    global _engine
    if _engine is None:
        if db is None:
            raise RuntimeError(
                "DeltaEngine has not been initialised; pass `db` on first call."
            )
        _engine = DeltaEngine(db)
    return _engine


__all__ = ["DeltaEngine", "DeltaTickResult", "get_delta_engine"]
