"""
Delta Engine — In-Memory Metrics Sink (D6)
===========================================
Phase D6 (2026-04-21). Dependency-free metrics collection for the
continuous-loop delta engine. No Prometheus client library is added —
we expose the `text/plain; version=0.0.4` exposition format directly.

What we record per tick
-----------------------
  * total ticks per sport                     → counter
  * successful ticks                          → counter
  * skipped ticks (by reason bucket)          → counter
  * dirty / updated / new / retired counts    → counters (sum)
  * rescored prop count                       → counter (sum)
  * rescore batch-cap truncations             → counter
  * tick duration                             → histogram + last-N buffer

All state lives in process memory. A restart resets counters — this is
fine because the engine is a per-pod concern and Prometheus scrapes
deltas. If we ever scale to multiple pods each pod owns its own series.

Ordering constraint
-------------------
This module must not import from the upstream-fetch blacklist — it is
on the delta-engine path. Enforced by
`tests/test_delta_upstream_isolation.py::test_delta_path_has_no_upstream_imports`.
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# How many ticks to retain in the rolling history (per sport).
HISTORY_BUFFER_SIZE = 200

# Duration histogram buckets (seconds). Covers typical ticks
# (<100ms no-op, ~1s dirty rescore) up to the big post-full-sync
# rescore bursts (~20s cap).
_DURATION_BUCKETS_S: List[float] = [
    0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 60.0,
]


class _SportMetrics:
    """Per-sport counters + histogram + rolling ring buffer."""

    def __init__(self) -> None:
        # Counters
        self.ticks_total: int = 0
        self.ticks_success: int = 0
        self.ticks_skipped: Dict[str, int] = defaultdict(int)
        self.dirty_props_sum: int = 0
        self.updated_props_sum: int = 0
        self.new_props_sum: int = 0
        self.retired_props_sum: int = 0
        self.rescored_props_sum: int = 0
        self.batch_cap_truncations_total: int = 0
        self.batch_cap_keys_skipped_sum: int = 0
        # Histogram: {bucket_upper_bound: count}, plus +Inf / sum / count
        self.duration_buckets: Dict[float, int] = {b: 0 for b in _DURATION_BUCKETS_S}
        self.duration_inf: int = 0
        self.duration_sum_s: float = 0.0
        self.duration_count: int = 0
        # Rolling history
        self.history: deque = deque(maxlen=HISTORY_BUFFER_SIZE)

    def observe_duration(self, seconds: float) -> None:
        matched = False
        for ub in _DURATION_BUCKETS_S:
            if seconds <= ub:
                self.duration_buckets[ub] += 1
                matched = True
                break
        if not matched:
            self.duration_inf += 1
        self.duration_sum_s += seconds
        self.duration_count += 1


# ---------------------------------------------------------------------------
# Process-global registry (singleton)
# ---------------------------------------------------------------------------
_REGISTRY: Dict[str, _SportMetrics] = defaultdict(_SportMetrics)


def _get(sport: str) -> _SportMetrics:
    return _REGISTRY[sport.lower()]


# ---------------------------------------------------------------------------
# Recording — called by DeltaEngine.tick after each run.
# ---------------------------------------------------------------------------

def record_tick(tick: Dict[str, Any]) -> None:
    """Ingest a `DeltaTickResult.to_dict()` payload.

    Uses the flat dict (not the dataclass) so callers that serialise
    the result over the wire can replay it into metrics without
    re-importing the dataclass.
    """
    sport = (tick.get("sport") or "unknown").lower()
    m = _get(sport)
    m.ticks_total += 1

    # Duration observation
    dur = float(tick.get("total_duration_seconds") or 0.0)
    m.observe_duration(dur)

    # Skipped book-keeping
    if tick.get("skipped"):
        reason = tick.get("skipped_reason") or "unknown"
        m.ticks_skipped[reason] += 1
    else:
        m.ticks_success += 1 if tick.get("success") else 0

    # Per-step counters — safe-getters so missing steps don't crash.
    steps = tick.get("steps") or {}

    detect = steps.get("1_detect") or {}
    m.dirty_props_sum   += int(detect.get("dirty_count") or 0)
    m.updated_props_sum += int(detect.get("updated_count") or 0)
    m.new_props_sum     += int(detect.get("new_count") or 0)
    m.retired_props_sum += int(detect.get("retired_count") or 0)

    rescore = steps.get("3_rescore_dirty") or {}
    m.rescored_props_sum += int(rescore.get("written") or 0)
    if rescore.get("batch_capped"):
        m.batch_cap_truncations_total += 1
        m.batch_cap_keys_skipped_sum += int(
            rescore.get("keys_skipped_due_to_cap") or 0
        )

    gate = steps.get("2_lock_gate") or {}
    upstream_lock_held = bool(gate.get("upstream_lock_held"))

    rebalance = steps.get("4_rebalance_tiers") or {}
    retired_modified = int(rebalance.get("retired_docs_modified") or 0)

    # Rolling history entry (compact)
    m.history.append({
        "tick_id": tick.get("tick_id"),
        "timestamp": tick.get("completed_at") or tick.get("started_at"),
        "started_at": tick.get("started_at"),
        "completed_at": tick.get("completed_at"),
        "duration_seconds": round(dur, 4),
        "success": bool(tick.get("success")),
        "skipped": bool(tick.get("skipped")),
        "skipped_reason": tick.get("skipped_reason"),
        "upstream_lock_held": upstream_lock_held,
        "dirty_count": int(detect.get("dirty_count") or 0),
        "updated_count": int(detect.get("updated_count") or 0),
        "new_count": int(detect.get("new_count") or 0),
        "retired_count": int(detect.get("retired_count") or 0),
        "rescored_count": int(rescore.get("written") or 0),
        "retired_docs_modified": retired_modified,
        "batch_capped": bool(rescore.get("batch_capped")),
        "keys_skipped_due_to_cap": int(rescore.get("keys_skipped_due_to_cap") or 0),
        "batch_cap": rescore.get("batch_cap"),
        "errors": list(tick.get("errors") or []),
    })


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------

def history_snapshot(sport: str, n: int = 50) -> List[Dict[str, Any]]:
    """Return the last `n` tick records for `sport`, newest LAST."""
    m = _get(sport)
    if not m.history:
        return []
    items = list(m.history)
    return items[-n:] if n > 0 else items


def counters_snapshot(sport: Optional[str] = None) -> Dict[str, Any]:
    """Dict snapshot of counters (not histogram) for one sport or all."""
    def _for(s: str) -> Dict[str, Any]:
        m = _get(s)
        return {
            "ticks_total": m.ticks_total,
            "ticks_success": m.ticks_success,
            "ticks_skipped_total": sum(m.ticks_skipped.values()),
            "ticks_skipped_by_reason": dict(m.ticks_skipped),
            "dirty_props_sum": m.dirty_props_sum,
            "updated_props_sum": m.updated_props_sum,
            "new_props_sum": m.new_props_sum,
            "retired_props_sum": m.retired_props_sum,
            "rescored_props_sum": m.rescored_props_sum,
            "batch_cap_truncations_total": m.batch_cap_truncations_total,
            "batch_cap_keys_skipped_sum": m.batch_cap_keys_skipped_sum,
            "duration_count": m.duration_count,
            "duration_sum_s": round(m.duration_sum_s, 4),
            "duration_avg_s": (
                round(m.duration_sum_s / m.duration_count, 4)
                if m.duration_count else 0.0
            ),
        }
    if sport:
        return _for(sport.lower())
    return {s: _for(s) for s in sorted(_REGISTRY.keys())}


# ---------------------------------------------------------------------------
# Prometheus text exposition
# ---------------------------------------------------------------------------
# Minimal exposition writer — OpenMetrics-compatible subset. No external
# dependency; every line conforms to
# https://prometheus.io/docs/instrumenting/exposition_formats/.

def prometheus_text() -> str:
    lines: List[str] = []

    def _emit_counter(name: str, help_text: str, samples: List[tuple]) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} counter")
        for labels, value in samples:
            lines.append(f"{name}{labels} {value}")

    def _emit_gauge(name: str, help_text: str, samples: List[tuple]) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        for labels, value in samples:
            lines.append(f"{name}{labels} {value}")

    def _label(d: Dict[str, str]) -> str:
        if not d:
            return ""
        inner = ",".join(f'{k}="{v}"' for k, v in sorted(d.items()))
        return "{" + inner + "}"

    sports = sorted(_REGISTRY.keys())

    # Counters
    counters = [
        ("propvision_delta_ticks_total",
         "Total delta ticks run.",
         lambda m: m.ticks_total),
        ("propvision_delta_ticks_success_total",
         "Delta ticks that completed without being skipped.",
         lambda m: m.ticks_success),
        ("propvision_delta_dirty_props_total",
         "Cumulative sum of dirty props observed across ticks.",
         lambda m: m.dirty_props_sum),
        ("propvision_delta_updated_props_total",
         "Cumulative sum of updated props observed.",
         lambda m: m.updated_props_sum),
        ("propvision_delta_new_props_total",
         "Cumulative sum of new (unscored) props observed.",
         lambda m: m.new_props_sum),
        ("propvision_delta_retired_props_total",
         "Cumulative sum of retired props observed.",
         lambda m: m.retired_props_sum),
        ("propvision_delta_rescored_props_total",
         "Cumulative rescored prop writes.",
         lambda m: m.rescored_props_sum),
        ("propvision_delta_batch_cap_truncations_total",
         "Number of ticks where the rescore batch cap truncated the work.",
         lambda m: m.batch_cap_truncations_total),
        ("propvision_delta_batch_cap_keys_skipped_total",
         "Cumulative keys deferred due to rescore batch cap.",
         lambda m: m.batch_cap_keys_skipped_sum),
    ]
    for name, help_text, getter in counters:
        samples = [
            (_label({"sport": s}), getter(_get(s))) for s in sports
        ]
        _emit_counter(name, help_text, samples)

    # Skipped by reason
    skipped_samples: List[tuple] = []
    for s in sports:
        for reason, value in _get(s).ticks_skipped.items():
            skipped_samples.append(
                (_label({"sport": s, "reason": reason}), value)
            )
    _emit_counter(
        "propvision_delta_ticks_skipped_total",
        "Delta ticks skipped, split by reason.",
        skipped_samples,
    )

    # Histogram
    lines.append("# HELP propvision_delta_tick_duration_seconds Delta tick durations.")
    lines.append("# TYPE propvision_delta_tick_duration_seconds histogram")
    for s in sports:
        m = _get(s)
        cumulative = 0
        for ub in _DURATION_BUCKETS_S:
            cumulative += m.duration_buckets[ub]
            lbl = _label({"sport": s, "le": str(ub)})
            lines.append(
                f"propvision_delta_tick_duration_seconds_bucket{lbl} {cumulative}"
            )
        cumulative += m.duration_inf
        lbl_inf = _label({"sport": s, "le": "+Inf"})
        lines.append(
            f"propvision_delta_tick_duration_seconds_bucket{lbl_inf} {cumulative}"
        )
        lines.append(
            f'propvision_delta_tick_duration_seconds_sum{{sport="{s}"}} '
            f"{m.duration_sum_s}"
        )
        lines.append(
            f'propvision_delta_tick_duration_seconds_count{{sport="{s}"}} '
            f"{m.duration_count}"
        )

    # Last-tick gauges (useful for quick dashboards)
    last_dur_samples: List[tuple] = []
    for s in sports:
        last = _get(s).history[-1] if _get(s).history else None
        if last:
            last_dur_samples.append(
                (_label({"sport": s}), last["duration_seconds"])
            )
    _emit_gauge(
        "propvision_delta_last_tick_duration_seconds",
        "Duration of the most recent delta tick (seconds).",
        last_dur_samples,
    )

    return "\n".join(lines) + "\n"


__all__ = [
    "record_tick",
    "history_snapshot",
    "counters_snapshot",
    "prometheus_text",
    "HISTORY_BUFFER_SIZE",
]
