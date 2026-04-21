"""
Delta Engine — Pipeline Steps (D3)
===================================
Phase D3 (2026-04-21). Composable pipeline steps that a delta tick
chains together. Sport-agnostic; each step runs identically for NBA
and MLB.

Architecture contract (mirrors the full-sync `PipelineStep` ABC in
`services/pipeline/master_steps.py`):
  - Stateless instances. Per-tick data lives in the `context` dict
    threaded through the chain.
  - Each step returns a metrics dict the engine accumulates into the
    tick response.
  - Exceptions are logged and appended to `context["errors"]`; they do
    NOT abort subsequent steps in the chain.

Steps defined here:
  - DetectChangedPropsStep  — runs the D1 detector.
  - UpstreamLockGateStep    — checks `UpstreamSyncLock.try_acquire_tick`,
                              skips the rest of the chain on conflict.
  - RescoreDirtyPropsStep   — rescores updated ∪ new keys via
                              `recompute_sport(..., only_canonical_keys=...)`.
  - RebalanceTiersStep      — marks RT docs inactive for retired keys
                              (opens tier slots for the next query).
  - AdvanceWatermarkStep    — bumps the persistent cursor.
  - EmitDeltaTickStep       — publishes a BoardEvent for observability.
"""
from __future__ import annotations

import abc
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from services.delta.detector import detect_changed_props
from services.delta_watermarks import advance_watermark
from services.scoring.recompute import recompute_sport
from services.scoring.tiering import mark_retired_inactive, get_tier_distribution
from services.upstream_sync_lock import get_upstream_sync_lock

logger = logging.getLogger(__name__)


class DeltaStep(abc.ABC):
    """Base class for sport-agnostic delta-tick pipeline steps."""

    name: str = "unnamed_delta_step"

    @abc.abstractmethod
    async def run(self, sport: str, db, context: Dict[str, Any]) -> Dict[str, Any]:
        ...


# ---------------------------------------------------------------------------
# Concrete steps
# ---------------------------------------------------------------------------

class DetectChangedPropsStep(DeltaStep):
    """Run the read-only D1 detector and stash the result in `context`."""

    name = "1_detect"

    async def run(self, sport, db, context):
        t0 = datetime.now(timezone.utc)
        result = await detect_changed_props(db, sport)
        context["detection"] = result
        return {
            "duration_seconds": (datetime.now(timezone.utc) - t0).total_seconds(),
            "watermark_utc": (
                result.watermark_utc.isoformat() if result.watermark_utc else None
            ),
            "updated_count": len(result.updated_keys),
            "new_count": len(result.new_keys),
            "retired_count": len(result.retired_keys),
            "dirty_count": len(result.dirty_keys),
            "live_props_count": result.live_props_count,
            "scored_rt_count": result.scored_rt_count,
        }


class UpstreamLockGateStep(DeltaStep):
    """Abort the rest of the chain if a full sync holds the sport's lock."""

    name = "2_lock_gate"

    async def run(self, sport, db, context):
        lock = get_upstream_sync_lock()
        held = lock.is_held(sport)
        context["skipped_due_to_full_sync"] = held
        if held:
            # Flag short-circuit; subsequent steps check this and no-op.
            context["abort_remaining_steps"] = True
            logger.info(
                f"[DELTA_STEP:{sport}] UpstreamLockGateStep: full sync in flight, "
                f"skipping remaining steps."
            )
        return {
            "upstream_lock_held": held,
            "lock_state": lock.describe(sport),
            "aborted": held,
        }


class RescoreDirtyPropsStep(DeltaStep):
    """Rescore updated ∪ new canonical keys via the scoped recompute filter."""

    name = "3_rescore_dirty"

    async def run(self, sport, db, context):
        if context.get("abort_remaining_steps"):
            return {"skipped": True, "reason": "upstream_lock_held"}

        detection = context.get("detection")
        if detection is None:
            return {"skipped": True, "reason": "no_detection_result"}

        # RETIRED keys are handled by RebalanceTiersStep — do NOT rescore
        # them (their live_props row is inactive; rescoring would
        # reactivate them via the recompute's unconditional active=True).
        rescore_keys = set(detection.updated_keys) | set(detection.new_keys)
        if not rescore_keys:
            return {
                "skipped": False,
                "keys_requested": 0,
                "written": 0,
                "reason": "no_dirty_props_to_rescore",
            }

        version_tag = f"final-{sport}-rt"
        t0 = datetime.now(timezone.utc)
        result = await recompute_sport(
            db=db,
            sport=sport,
            version_tag=version_tag,
            write_mode="upsert",
            only_canonical_keys=rescore_keys,
        )
        dur = (datetime.now(timezone.utc) - t0).total_seconds()
        logger.info(
            f"[DELTA_STEP:{sport}] rescored {result.get('written', 0)} / "
            f"{len(rescore_keys)} requested in {dur:.2f}s"
        )
        context["rescore_result"] = result
        return {
            "duration_seconds": dur,
            "keys_requested": len(rescore_keys),
            "matched": result.get("only_canonical_keys_matched"),
            "written": result.get("written", 0),
            "skipped": result.get("skipped", 0),
            "version_tag": version_tag,
        }


class RebalanceTiersStep(DeltaStep):
    """Mark retired RT docs inactive → opens tier slots for next query."""

    name = "4_rebalance_tiers"

    async def run(self, sport, db, context):
        if context.get("abort_remaining_steps"):
            return {"skipped": True, "reason": "upstream_lock_held"}

        detection = context.get("detection")
        if detection is None:
            return {"skipped": True, "reason": "no_detection_result"}

        version_tag = f"final-{sport}-rt"
        t0 = datetime.now(timezone.utc)
        retired_result = await mark_retired_inactive(
            db=db,
            sport=sport,
            version_tag=version_tag,
            retired_keys=detection.retired_keys,
        )
        tier_dist = await get_tier_distribution(db, sport, version_tag)
        dur = (datetime.now(timezone.utc) - t0).total_seconds()
        context["rebalance_result"] = {
            "retired": retired_result,
            "tier_distribution_after": tier_dist,
        }
        return {
            "duration_seconds": dur,
            "retired_keys_processed": retired_result["keys_processed"],
            "retired_docs_modified": retired_result["modified"],
            "tier_distribution_after": tier_dist,
            "version_tag": version_tag,
        }


class AdvanceWatermarkStep(DeltaStep):
    """Bump the per-sport cursor forward to the tick-start timestamp.

    IMPORTANT: we advance to `context["tick_started_at"]` (set by the
    engine BEFORE detection runs), NOT to `now()` — this guarantees
    that any prop `updated_at` stamped DURING detection / rescore will
    still be visible as dirty on the next tick (idempotent by design).
    """

    name = "5_advance_watermark"

    async def run(self, sport, db, context):
        if context.get("abort_remaining_steps"):
            return {"skipped": True, "reason": "upstream_lock_held"}

        ts = context.get("tick_started_at") or datetime.now(timezone.utc)
        t0 = datetime.now(timezone.utc)
        advanced_to = await advance_watermark(db, sport, ts)
        dur = (datetime.now(timezone.utc) - t0).total_seconds()
        return {
            "duration_seconds": dur,
            "advanced_to": advanced_to.isoformat(),
        }


class EmitDeltaTickStep(DeltaStep):
    """Publish a `delta_tick_complete` BoardEvent for observability."""

    name = "6_emit"

    async def run(self, sport, db, context):
        detection = context.get("detection")
        try:
            from services.event_bus import BoardEvent, get_event_bus
            await get_event_bus().publish(BoardEvent(
                sport=sport,
                event_type="delta_tick_complete",
                severity="low",
                source="delta_engine",
            ))
            published = True
        except Exception as exc:
            logger.warning(f"[DELTA_STEP:{sport}] event publish failed: {exc}")
            published = False

        return {
            "published": published,
            "dirty_count": (len(detection.dirty_keys) if detection else 0),
            "aborted": bool(context.get("abort_remaining_steps")),
        }


# ---------------------------------------------------------------------------
# Ordered default chain
# ---------------------------------------------------------------------------
DEFAULT_DELTA_STEPS = (
    DetectChangedPropsStep(),
    UpstreamLockGateStep(),
    RescoreDirtyPropsStep(),
    RebalanceTiersStep(),
    AdvanceWatermarkStep(),
    EmitDeltaTickStep(),
)


__all__ = [
    "DeltaStep",
    "DetectChangedPropsStep",
    "UpstreamLockGateStep",
    "RescoreDirtyPropsStep",
    "RebalanceTiersStep",
    "AdvanceWatermarkStep",
    "EmitDeltaTickStep",
    "DEFAULT_DELTA_STEPS",
]
