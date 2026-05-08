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
  - EmitDeltaTickStep       — publishes a BoardEvent for observability.

2026-05-07 P0-A SSOT cleanup — `AdvanceWatermarkStep` and the
`delta_watermarks` collection have been removed. They were a vestige
of the timestamp-watermark detection approach that Step 3 (2026-05-07)
replaced with `services.delta.dirty_queue`. The watermark step had
been demoted to "observability only" but kept writing every tick,
which created the architectural mismatch the production SLO check
flagged ("watermark ahead of live_props.max(updated_at)"). Per
stabilization-plan rule "one detection system only", every reference
to delta_watermarks has been deleted, not shimmed.
"""
from __future__ import annotations

import abc
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from services.delta.detector import detect_changed_props
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
            "updated_count": len(result.updated_keys),
            "new_count": len(result.new_keys),
            "retired_count": len(result.retired_keys),
            "dirty_count": len(result.dirty_keys),
            "live_props_count": result.live_props_count,
            "scored_rt_count": result.scored_rt_count,
            "queue_depth_remaining": result.queue_depth_remaining,
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

        # 2026-05-07 P0-A leak fix: confirm-and-delete ALL drained
        # queue rows regardless of match outcome or batch cap. The
        # previous proportional-confirm logic (delete only `keep_n =
        # drained * selected / requested`) was broken for two reasons:
        #
        #   (1) Drained rows that the cap left out had the LOWEST
        #       `_id`s. They were re-drained every subsequent tick
        #       (sort by `_id` ascending) instead of NEW enqueues,
        #       so the queue never converged.
        #   (2) `recompute_sport(only_canonical_keys=...)` filters
        #       requested keys through `coverage_filter` (priceable +
        #       pp_playable). Keys that fail the filter (alt-only,
        #       not on PrizePicks, etc.) are silently dropped — we
        #       received their queue events but never deleted them,
        #       so they accumulated forever.
        #
        # Confirm-all is safe:
        #   - It runs AFTER `recompute_sport` returned successfully,
        #     so a mid-rescore crash still leaves rows queued for
        #     next tick (the existing crash-safety guarantee).
        #   - Keys that legitimately change again will be re-enqueued
        #     by the next ingestion batch under FRESH `_id`s, so
        #     they will be picked up on the very next tick.
        #   - Keys that retired (filter-dropped, slate change) get
        #     dropped from the queue forever — which is correct;
        #     they will never need rescoring again.
        confirmed = 0
        try:
            from services.delta.dirty_queue import confirm_processed
            drained_ids = list(getattr(detection, "drained_queue_ids", []) or [])
            if drained_ids:
                confirmed = await confirm_processed(db, drained_ids)
        except Exception as _dq_err:
            logger.warning(
                f"[DELTA_STEP:{sport}] dirty_queue confirm failed "
                f"(non-fatal — rows will be re-rescored next tick): "
                f"{_dq_err}"
            )

        logger.info(
            f"[DELTA_STEP:{sport}] rescored {result.get('written', 0)} / "
            f"{len(rescore_keys)} requested in {dur:.2f}s "
            f"(batch_capped={batch_capped} skipped_due_to_cap={keys_skipped_due_to_cap}) "
            f"queue_confirmed={confirmed}"
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
            "queue_ids_confirmed": confirmed,
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


class PublishBoardSnapshotStep(DeltaStep):
    """Rebuild `{sport}_cached_board` as a materialized snapshot of
    `prop_scores[final-{sport}-rt]` after a delta tick that wrote to
    the -rt surface.

    Contract (2026-05-08 architecture fix — §3 no longer depends on
    master_sync cadence):
      * SKIPPED when upstream lock is held (master_sync is in flight —
        master_sync will publish its own snapshot at end-of-run).
      * SKIPPED when the rescore step wrote 0 docs (no content change;
        previous snapshot is still the correct materialized view).
      * SKIPPED when retired_result.modified is 0 AND written is 0
        (redundant with above, but explicit).
      * On publish failure, logs a warning and returns the error — the
        delta tick as a whole still completes (failure isolation).
    """

    name = "5_publish_board_snapshot"

    async def run(self, sport, db, context):
        if context.get("abort_remaining_steps"):
            return {"skipped": True, "reason": "upstream_lock_held"}

        rescore = context.get("rescore_result") or {}
        rebalance = context.get("rebalance_result") or {}
        written = int(rescore.get("written", 0) or 0)
        retired_modified = int(
            (rebalance.get("retired") or {}).get("modified", 0) or 0
        )

        if written == 0 and retired_modified == 0:
            return {
                "skipped": True,
                "reason": "no_rt_writes_this_tick",
                "written": 0,
                "retired_modified": 0,
            }

        try:
            from services.board_snapshot_publisher import publish_board_snapshot
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"[DELTA_STEP:{sport}] PublishBoardSnapshotStep import failed: {exc}"
            )
            return {"skipped": True, "reason": "publisher_import_failed", "error": str(exc)}

        t0 = datetime.now(timezone.utc)
        result = await publish_board_snapshot(db, sport)
        dur = (datetime.now(timezone.utc) - t0).total_seconds()
        return {
            "duration_seconds": dur,
            "trigger_written": written,
            "trigger_retired_modified": retired_modified,
            "publisher": result,
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
    PublishBoardSnapshotStep(),
    EmitDeltaTickStep(),
)


__all__ = [
    "DeltaStep",
    "DetectChangedPropsStep",
    "UpstreamLockGateStep",
    "RescoreDirtyPropsStep",
    "RebalanceTiersStep",
    "PublishBoardSnapshotStep",
    "EmitDeltaTickStep",
    "DEFAULT_DELTA_STEPS",
]
