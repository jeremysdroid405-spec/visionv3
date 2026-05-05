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
        updated_keys = sorted(set(detection.updated_keys))
        new_keys = sorted(set(detection.new_keys) - set(detection.updated_keys))
        total_requested = len(updated_keys) + len(new_keys)

        # Phase D6 (2026-04-21) — apply the batch cap. Priority order:
        #   1) UPDATED (line moves affecting existing tier incumbents)
        #   2) NEW     (unscored props; overflow is deterministically
        #               re-detected next tick via set-diff → natural
        #               convergence)
        batch_cap = context.get("rescore_batch_cap") or 0
        batch_capped = False
        keys_skipped_due_to_cap = 0
        if batch_cap and batch_cap > 0 and total_requested > batch_cap:
            batch_capped = True
            remaining = batch_cap
            selected_updated = updated_keys[:remaining]
            remaining -= len(selected_updated)
            selected_new = new_keys[:max(remaining, 0)]
            selected = selected_updated + selected_new
            keys_skipped_due_to_cap = total_requested - len(selected)
            rescore_keys = set(selected)
        else:
            rescore_keys = set(updated_keys) | set(new_keys)

        if not rescore_keys:
            return {
                "skipped": False,
                "keys_requested": 0,
                "written": 0,
                "reason": "no_dirty_props_to_rescore",
                "batch_cap": batch_cap if batch_cap > 0 else None,
                "batch_capped": False,
                "keys_skipped_due_to_cap": 0,
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
            f"{len(rescore_keys)} requested in {dur:.2f}s "
            f"(batch_capped={batch_capped} skipped_due_to_cap={keys_skipped_due_to_cap})"
        )
        context["rescore_result"] = result
        return {
            "duration_seconds": dur,
            "keys_requested": len(rescore_keys),
            "matched": result.get("only_canonical_keys_matched"),
            "written": result.get("written", 0),
            "skipped": result.get("skipped", 0),
            "version_tag": version_tag,
            "batch_cap": batch_cap if batch_cap > 0 else None,
            "batch_capped": batch_capped,
            "keys_skipped_due_to_cap": keys_skipped_due_to_cap,
            "total_dirty_requested": total_requested,
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
    """Bump the per-sport cursor forward, but never past `now − SAFE_LAG`.

    History (2026-05-05): the original implementation advanced the
    watermark unconditionally to `context["tick_started_at"]`. That
    races late-arriving upstream writes — when an upstream sync stamps
    a batch with `updated_at = T0` but the bulk-write commits at
    `T0 + Δ` (Δ can easily exceed the existing 5-second grace), the
    detect tick that runs at `T0 + 1s` sees nothing AND advances the
    watermark to `T0 + 1s`. The next tick then queries
    `updated_at > T0 + 1s − 5s` and STILL misses the writes whose
    timestamps are `T0`.  Result: the writes are lost forever, the
    detector returns `dirty=0` every tick, and tier rebalances never
    fire (real failure observed: MLB tiers frozen at 15:20 UTC while
    `mlb_live_props` had been receiving fresh writes every minute).

    Fix: cap the advance at `now − SAFE_LAG_SECONDS` so the watermark
    trails real time by a window large enough to absorb upstream
    commit latency. Rescore is idempotent — a slightly-overlapping
    detect window costs nothing and prevents data loss.

    The cap intentionally uses `now()` (NOT `tick_started_at`) so the
    LAG is measured against the wall clock at the moment we commit,
    not against the moment the tick began.
    """

    name = "5_advance_watermark"

    # Maximum tolerated upstream-write commit latency. 90s comfortably
    # absorbs typical bulk-write commit lag for 10K+ row upserts on a
    # busy mongod, while keeping the rescore overlap window small.
    SAFE_LAG_SECONDS = 90

    async def run(self, sport, db, context):
        if context.get("abort_remaining_steps"):
            return {"skipped": True, "reason": "upstream_lock_held"}

        tick_started_at = context.get("tick_started_at") or datetime.now(timezone.utc)
        now_utc = datetime.now(timezone.utc)
        from datetime import timedelta
        safe_ceiling = now_utc - timedelta(seconds=self.SAFE_LAG_SECONDS)
        # Advance only as far as `min(tick_started_at, now − SAFE_LAG)`.
        ts = min(tick_started_at, safe_ceiling)

        t0 = datetime.now(timezone.utc)
        advanced_to = await advance_watermark(db, sport, ts)
        dur = (datetime.now(timezone.utc) - t0).total_seconds()
        return {
            "duration_seconds": dur,
            "advanced_to": advanced_to.isoformat(),
            "tick_started_at": tick_started_at.isoformat(),
            "safe_lag_seconds": self.SAFE_LAG_SECONDS,
            "capped_by_safe_lag": ts is safe_ceiling,
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
