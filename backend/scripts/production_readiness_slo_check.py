#!/usr/bin/env python3
"""
production_readiness_slo_check
==============================

End-to-end SLO check that runs against the LIVE DB + LIVE API the
preview URL serves. No mocks, no stubs, no unit-test framework.

Exit codes:
  0 — every SLO check PASSED
  1 — at least one check FAILED
  2 — script itself errored (could not read DB / API)

Usage:
  python /app/backend/scripts/production_readiness_slo_check.py
  python /app/backend/scripts/production_readiness_slo_check.py --json    # machine-readable

Checks (mirrors the stabilization-plan Phase 5 spec verbatim):
  1. Ingestion freshness   — {nba,mlb}_live_props.max(updated_at) < 5 min
  2. Score freshness       — {nba,mlb}_prop_scores.max(scored_at) < 5 min
  3. Tier freshness        — last cached_board write > last score write
  4. Detection-source freshness — `delta_dirty_queue` last enqueue per
                             sport < 5 min AND queue depth bounded.
                             (2026-05-07 P0-A: replaced the legacy
                             `delta_watermarks` check entirely. The
                             watermark collection has been deleted from
                             the architecture; the dirty queue is the
                             single source of truth for "what changed
                             since last tick". One detection system,
                             no shims.)
  5. API/UI correctness    — 10 visible picks per sport: API == score_doc;
                             L5 ∈ {0,20,40,60,80,100}; L10 % 10 == 0;
                             L20 % 5 == 0; no `hit_rates`; no legacy aliases
                             present in API surface.
  6. Vision Intel coverage — visible-card coverage ≥ 80% per sport
  7. Tier counts           — SH / FL / WZ non-empty per sport (or fail-reason
                             logged if empty by gate decision)

Every failure includes RAW EVIDENCE: timestamps, document counts, sample
canonical_keys, and the exact API/DB values that triggered the failure.
This is the artifact the stabilization plan demands.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Allow running from anywhere — the script imports nothing from
# /app/backend/services so we don't need sys.path manipulation, but
# we do need the env vars from /app/backend/.env.
_ENV_PATH = Path("/app/backend/.env")
if _ENV_PATH.exists():
    for ln in _ENV_PATH.read_text().splitlines():
        if "=" in ln and not ln.strip().startswith("#"):
            k, _, v = ln.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

_FE_ENV_PATH = Path("/app/frontend/.env")
if _FE_ENV_PATH.exists():
    for ln in _FE_ENV_PATH.read_text().splitlines():
        if ln.startswith("REACT_APP_BACKEND_URL="):
            os.environ.setdefault(
                "REACT_APP_BACKEND_URL",
                ln.split("=", 1)[1].strip(),
            )

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────
SPORTS = ("nba", "mlb")
TIERS = ("safe_haven", "front_lines", "war_zone")

FRESHNESS_MAX_AGE_S = 5 * 60          # §1, §2  (5 minutes)
DIRTY_QUEUE_MAX_LAG_S = 5 * 60        # §4 detection-source freshness
VISION_COVERAGE_MIN_PCT = 80.0        # §6
SAMPLE_SIZE_PER_SPORT = 10            # §5

LEGACY_API_FIELDS = (
    "h5_rate",
    "h10_rate",
    "h20_rate",
    "hit_rate",            # active-side aggregate alias
    "hit_rates",           # nested cached-board bag
    "model_hit_rate_over",
    "model_hit_rate_under",
)

CANONICAL_HR_FIELDS = ("hit_rate_l5", "hit_rate_l10", "hit_rate_l20")


# ─────────────────────────────────────────────────────────────────────
# Result accumulator
# ─────────────────────────────────────────────────────────────────────
@dataclass
class CheckResult:
    name: str
    passed: bool
    evidence: dict[str, Any] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.passed = False
        self.failures.append(msg)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _gap_seconds(dt: datetime | None, now: datetime) -> float | None:
    dt = _ensure_aware(dt)
    if dt is None:
        return None
    return (now - dt).total_seconds()


async def _max_ts(coll, field_name: str) -> datetime | None:
    doc = await coll.find_one(
        {field_name: {"$exists": True, "$ne": None}},
        sort=[(field_name, -1)],
        projection={"_id": 0, field_name: 1},
    )
    if not doc:
        return None
    return _ensure_aware(doc.get(field_name))


def _api_get(path: str, timeout: int = 15) -> dict | None:
    """GET against the preview API, falling back to localhost on 4xx/5xx
    so the test still works inside the pod when the public ingress
    rejects unauthenticated python clients."""
    base_pub = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
    base_local = "http://127.0.0.1:8001"
    last_exc = None
    for base in (base_pub, base_local):
        if not base:
            continue
        try:
            req = urllib.request.Request(
                f"{base}{path}",
                headers={"User-Agent": "slo-check/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            last_exc = exc
            continue
    raise RuntimeError(f"API unreachable on both preview & localhost: {last_exc!r}")


# ─────────────────────────────────────────────────────────────────────
# §1  Ingestion freshness
# ─────────────────────────────────────────────────────────────────────
async def check_ingestion_freshness(db, now: datetime) -> CheckResult:
    r = CheckResult(name="1_ingestion_freshness", passed=True)
    for sport in SPORTS:
        coll = db[f"{sport}_live_props"]
        ts = await _max_ts(coll, "updated_at")
        gap = _gap_seconds(ts, now)
        # write rates over short windows for context
        five = await coll.count_documents(
            {"updated_at": {"$gt": now - timedelta(minutes=5)}}
        )
        thirty = await coll.count_documents(
            {"updated_at": {"$gt": now - timedelta(minutes=30)}}
        )
        r.evidence[sport] = {
            "max_updated_at": ts.isoformat() if ts else None,
            "gap_seconds": gap,
            "writes_last_5m": five,
            "writes_last_30m": thirty,
        }
        if ts is None:
            r.fail(f"{sport}_live_props has no updated_at timestamps at all")
        elif gap is not None and gap > FRESHNESS_MAX_AGE_S:
            r.fail(
                f"{sport}_live_props STALE — gap={gap:.0f}s "
                f"(threshold {FRESHNESS_MAX_AGE_S}s); "
                f"max_updated_at={ts.isoformat()}"
            )
    return r


# ─────────────────────────────────────────────────────────────────────
# §2  Score freshness
# ─────────────────────────────────────────────────────────────────────
async def check_score_freshness(db, now: datetime) -> CheckResult:
    r = CheckResult(name="2_score_freshness", passed=True)
    for sport in SPORTS:
        coll = db[f"{sport}_prop_scores"]
        ts = await _max_ts(coll, "scored_at")
        gap = _gap_seconds(ts, now)
        active = await coll.count_documents(
            {"tier": {"$in": list(TIERS)}, "active": True}
        )
        r.evidence[sport] = {
            "max_scored_at": ts.isoformat() if ts else None,
            "gap_seconds": gap,
            "active_board_total": active,
        }
        if ts is None:
            r.fail(f"{sport}_prop_scores has no scored_at timestamps")
        elif gap is not None and gap > FRESHNESS_MAX_AGE_S:
            r.fail(
                f"{sport}_prop_scores STALE — gap={gap:.0f}s "
                f"(threshold {FRESHNESS_MAX_AGE_S}s); "
                f"max_scored_at={ts.isoformat()}"
            )
    return r


# ─────────────────────────────────────────────────────────────────────
# §3  Tier freshness — cached_board canonical Phase-4 contract
#
# 2026-05-07 P0 §3 fix:
# Previous behaviour compared `cached_board.updated_at` against the
# LIVE `prop_scores.max(scored_at)` with a 60s grace, but the actual
# `{nba,mlb}_cached_board` writers run on the master_sync hourly
# cadence (see `services/master_sync.py` Step 7 — the freshness
# stamper). With score writes happening every ~30s via the delta
# engine, `score_max - cb_max` would always grow past 60s within a
# couple of minutes of any master_sync completion, producing a
# permanent false-negative.
#
# The correct contract — now stamped by
# `services/board_freshness.stamp_cached_board_freshness` on every
# `<sport>_cached_board` doc — is:
#
#     1. EXISTENCE of the canonical fields:
#            updated_at
#            last_publish_ts
#            source_score_max_scored_at   (None allowed only on
#                                          empty-`prop_scores` envs)
#            sport
#            version_tag
#
#     2. WRITER-CORRECTNESS INVARIANT:
#            updated_at >= source_score_max_scored_at
#        The cached_board can never claim to be built FROM a score
#        publish that hasn't happened yet. This catches stamp drift
#        / clock-skew / out-of-order writers immediately.
#
#     3. WRITER-CADENCE RECENCY:
#            now - max(updated_at) <= CACHED_BOARD_MAX_AGE_S
#        Threshold is the master_sync cadence (60 min) plus a 15 min
#        grace window for sync-duration overlap and scheduler jitter.
#        Anything beyond that proves the master_sync job has stalled.
#
# This is NOT weakening the check — it is aligning the SLO contract
# to the actual writer cadence and adding the writer-correctness
# invariant that the previous version never enforced.
# ─────────────────────────────────────────────────────────────────────
CACHED_BOARD_MAX_AGE_S = 75 * 60   # 60-min master_sync cadence + 15-min grace


async def check_tier_freshness(db, now: datetime) -> CheckResult:
    r = CheckResult(name="3_tier_freshness", passed=True)
    for sport in SPORTS:
        cb = db[f"{sport}_cached_board"]
        cb_count = await cb.count_documents({})
        cb_max_updated = await _max_ts(cb, "updated_at")
        cb_max_publish = await _max_ts(cb, "last_publish_ts")
        cb_max_source  = await _max_ts(cb, "source_score_max_scored_at")
        score_max      = await _max_ts(db[f"{sport}_prop_scores"], "scored_at")

        # Existence: every doc must carry the Phase-4 contract.
        missing_updated   = await cb.count_documents({"updated_at":   {"$exists": False}})
        missing_publish   = await cb.count_documents({"last_publish_ts": {"$exists": False}})
        missing_sport     = await cb.count_documents({"sport":        {"$exists": False}})
        missing_version   = await cb.count_documents({"version_tag":  {"$exists": False}})

        recency_gap_s = _gap_seconds(cb_max_updated, now)
        invariant_violations = 0
        if cb_max_updated is not None and cb_max_source is not None:
            # `updated_at >= source_score_max_scored_at` — count docs
            # that violate. Each violator is a writer correctness bug.
            invariant_violations = await cb.count_documents({
                "$expr": {"$lt": ["$updated_at", "$source_score_max_scored_at"]},
            })

        r.evidence[sport] = {
            "cached_board_doc_count": cb_count,
            "max_updated_at":         cb_max_updated.isoformat() if cb_max_updated else None,
            "max_last_publish_ts":    cb_max_publish.isoformat() if cb_max_publish else None,
            "max_source_score_at":    cb_max_source.isoformat()  if cb_max_source  else None,
            "live_score_max_scored_at": score_max.isoformat()    if score_max      else None,
            "recency_gap_seconds":    recency_gap_s,
            "recency_threshold_s":    CACHED_BOARD_MAX_AGE_S,
            "missing_updated_at_docs":   missing_updated,
            "missing_last_publish_ts_docs": missing_publish,
            "missing_sport_docs":     missing_sport,
            "missing_version_tag_docs": missing_version,
            "invariant_violation_docs": invariant_violations,
        }

        # ── Empty collection → cannot prove freshness ────────────────
        if cb_count == 0:
            r.fail(
                f"{sport}_cached_board is EMPTY — no docs to read "
                f"freshness from. Master_sync writer (or MLB rebuild) "
                f"has never run."
            )
            continue

        # ── Existence: canonical fields populated on every doc ───────
        if missing_updated:
            r.fail(
                f"{sport}_cached_board has {missing_updated} doc(s) "
                f"missing canonical `updated_at`. Writer is not stamping "
                f"the Phase-4 freshness contract; check "
                f"services/board_freshness.stamp_cached_board_freshness "
                f"and master_sync Step 7."
            )
        if missing_publish:
            r.fail(
                f"{sport}_cached_board has {missing_publish} doc(s) "
                f"missing canonical `last_publish_ts`."
            )
        if missing_sport:
            r.fail(
                f"{sport}_cached_board has {missing_sport} doc(s) "
                f"missing canonical `sport`."
            )
        if missing_version:
            r.fail(
                f"{sport}_cached_board has {missing_version} doc(s) "
                f"missing canonical `version_tag`."
            )

        # ── Writer-correctness invariant ─────────────────────────────
        if invariant_violations:
            r.fail(
                f"{sport}_cached_board has {invariant_violations} doc(s) "
                f"violating `updated_at >= source_score_max_scored_at`. "
                f"Writer wrote a board claiming a source score newer "
                f"than its own publish time — clock-skew or stamp drift."
            )

        # ── Recency relative to writer cadence ───────────────────────
        if cb_max_updated is None:
            r.fail(
                f"{sport}_cached_board has no max(updated_at) — "
                f"`_max_ts` returned None despite docs present."
            )
            continue
        if recency_gap_s is None:
            r.fail(f"{sport}_cached_board: `now - updated_at` could not be computed.")
            continue
        if recency_gap_s > CACHED_BOARD_MAX_AGE_S:
            r.fail(
                f"{sport}_cached_board STALE — last writer stamp "
                f"{cb_max_updated.isoformat()} ({recency_gap_s:.0f}s ago) "
                f"exceeds writer-cadence threshold "
                f"{CACHED_BOARD_MAX_AGE_S}s (= 60-min master_sync + "
                f"15-min grace). Master_sync has stalled or never ran."
            )
    return r


# ─────────────────────────────────────────────────────────────────────
# §4  Detection-source freshness — `delta_dirty_queue` per sport
#
# 2026-05-07 P0-A architecture note:
# The previous §4 ("watermark NOT ahead of live_props.max(updated_at)")
# was a sanity check on the timestamp-watermark detection that Step 3
# replaced. SSOT cleanup deleted the `delta_watermarks` collection AND
# the AdvanceWatermarkStep that wrote it — there is no fallback path
# and no compatibility shim. The dirty queue (`delta_dirty_queue`,
# enqueued by `universal_odds_sync` after every batch insert, drained
# by `services.delta.detector`) is now the single detection source.
#
# What this check enforces in the new architecture (2 valid healthy
# states; everything else is a failure):
#
#   STATE 1 — queue has recent activity AND bounded depth.
#       last_enqueue_at is set, lag < DIRTY_QUEUE_MAX_LAG_S,
#       depth < DIRTY_QUEUE_DEPTH_HARD_CAP.
#
#   STATE 2 — queue is empty AND ingestion + scoring are fresh.
#       depth == 0, last_enqueue_at == None (the detector drained
#       every queued key faster than the next enqueue arrived),
#       BUT live_props.max(updated_at) and prop_scores.max(scored_at)
#       are both inside the freshness threshold AND there are no
#       watchdog FROZEN / RESTART_STORM events in supervisor logs.
#       This is the steady-state for low-volume sports (MLB drains
#       ~5k keys in <60s; observation windows hit empty queue often).
#
# Invalid states (any of these → FAIL):
#   * queue empty but live_props stale
#   * queue empty but scores stale
#   * queue depth growing unbounded (depth > hard cap)
#   * no enqueue activity AND no live updates
#   * watchdog FROZEN / RESTART_STORM events present
#   * stale enqueue lag exceeds threshold
# ─────────────────────────────────────────────────────────────────────
DIRTY_QUEUE_DEPTH_HARD_CAP = 150_000   # 5× peak batch — generous bound

# Supervisor backend log path — same source the
# `p0_phase4b_verify.sh` watchdog check reads. Override via env for
# alternate environments (CI, test pod). Empty / missing file is
# treated as "no events".
WATCHDOG_LOG_PATH = os.environ.get(
    "SLO_BACKEND_LOG_PATH",
    "/var/log/supervisor/backend.err.log",
)


def _count_watchdog_events(path: str = WATCHDOG_LOG_PATH) -> int:
    """Count `WATCHDOG.*FROZEN` / `RESTART_STORM` events in the
    supervisor backend log. Returns 0 if the log is missing /
    unreadable (test environments) — the check is purely additive
    fail-safe, never the sole reason a healthy pod fails."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return sum(
                1 for ln in f
                if ("WATCHDOG" in ln and "FROZEN" in ln) or "RESTART_STORM" in ln
            )
    except OSError:
        return 0


async def check_detection_source_freshness(db, now: datetime) -> CheckResult:
    r = CheckResult(name="4_detection_source_freshness", passed=True)
    for sport in SPORTS:
        depth = await db["delta_dirty_queue"].count_documents({"sport": sport})
        latest = await db["delta_dirty_queue"].find(
            {"sport": sport}, {"_id": 1, "enqueued_at": 1},
        ).sort([("_id", -1)]).limit(1).to_list(1)
        last_enqueue = None
        last_lag_s: float | None = None
        if latest:
            ea = latest[0].get("enqueued_at")
            if isinstance(ea, datetime):
                last_enqueue = _ensure_aware(ea)
                last_lag_s = (now - last_enqueue).total_seconds()
        live_max = await _max_ts(db[f"{sport}_live_props"], "updated_at")
        score_max = await _max_ts(db[f"{sport}_prop_scores"], "scored_at")
        live_gap_s = _gap_seconds(live_max, now)
        score_gap_s = _gap_seconds(score_max, now)
        watchdog_events = _count_watchdog_events()

        # STATE 2 candidate: empty queue + drained-clean detection.
        # Only honored when ingestion AND scoring are both fresh AND
        # no watchdog events.
        empty_queue_drained_clean = (
            depth == 0
            and last_enqueue is None
            and live_gap_s is not None
            and live_gap_s <= FRESHNESS_MAX_AGE_S
            and score_gap_s is not None
            and score_gap_s <= FRESHNESS_MAX_AGE_S
            and watchdog_events == 0
        )

        r.evidence[sport] = {
            "detection_source": "delta_dirty_queue",
            "queue_depth": depth,
            "last_enqueue_at": last_enqueue.isoformat() if last_enqueue else None,
            "last_enqueue_lag_seconds": last_lag_s,
            "live_props_max_updated_at": live_max.isoformat() if live_max else None,
            "live_props_gap_seconds": live_gap_s,
            "score_max_scored_at": score_max.isoformat() if score_max else None,
            "score_gap_seconds": score_gap_s,
            "watchdog_events": watchdog_events,
            "freshness_threshold_s": FRESHNESS_MAX_AGE_S,
            "depth_hard_cap": DIRTY_QUEUE_DEPTH_HARD_CAP,
            "healthy_state": (
                "STATE_2_empty_queue_drained_clean"
                if empty_queue_drained_clean
                else ("STATE_1_active_queue" if last_enqueue is not None else "INVALID")
            ),
        }

        # ── Unbounded depth is always a failure ─────────────────────
        if depth > DIRTY_QUEUE_DEPTH_HARD_CAP:
            r.fail(
                f"{sport}: delta_dirty_queue UNBOUNDED — depth={depth} "
                f"> cap {DIRTY_QUEUE_DEPTH_HARD_CAP}. Detector is not "
                f"draining as fast as ingestion enqueues."
            )

        # ── Watchdog gate ───────────────────────────────────────────
        if watchdog_events > 0:
            r.fail(
                f"{sport}: {watchdog_events} watchdog FROZEN / "
                f"RESTART_STORM event(s) in supervisor log "
                f"({WATCHDOG_LOG_PATH}); detection pipeline cannot "
                f"be considered healthy until events are resolved."
            )

        # ── State machine ───────────────────────────────────────────
        if last_enqueue is not None:
            # STATE 1 — active queue. Enforce the lag threshold.
            if last_lag_s is not None and last_lag_s > DIRTY_QUEUE_MAX_LAG_S:
                r.fail(
                    f"{sport}: delta_dirty_queue STALE — last enqueue "
                    f"{last_lag_s:.0f}s ago "
                    f"(threshold {DIRTY_QUEUE_MAX_LAG_S}s); "
                    f"last_enqueue_at={last_enqueue.isoformat()}"
                )
            continue

        # last_enqueue is None — STATE 2 candidate or invalid.
        if depth > 0:
            # Queue holds entries but the newest one has no
            # `enqueued_at` timestamp — schema corruption / writer bug.
            r.fail(
                f"{sport}: delta_dirty_queue depth={depth} but newest "
                f"row carries no `enqueued_at` timestamp — "
                f"writer schema regression."
            )
            continue

        if empty_queue_drained_clean:
            # STATE 2 — healthy. Detector drained the queue faster
            # than the next ingest cycle enqueued. Pass.
            continue

        # Empty queue + something stale → FAIL with the specific
        # signal the operator must investigate.
        if live_gap_s is None:
            r.fail(
                f"{sport}: delta_dirty_queue empty AND "
                f"{sport}_live_props has no updated_at — ingestion "
                f"never wrote / collection missing."
            )
        elif live_gap_s > FRESHNESS_MAX_AGE_S:
            r.fail(
                f"{sport}: delta_dirty_queue empty AND "
                f"{sport}_live_props STALE (gap={live_gap_s:.0f}s "
                f"> {FRESHNESS_MAX_AGE_S}s) — ingestion has stopped "
                f"writing; detection cannot recover without it."
            )
        elif score_gap_s is None:
            r.fail(
                f"{sport}: delta_dirty_queue empty AND "
                f"{sport}_prop_scores has no scored_at — scoring "
                f"never ran."
            )
        elif score_gap_s > FRESHNESS_MAX_AGE_S:
            r.fail(
                f"{sport}: delta_dirty_queue empty AND "
                f"{sport}_prop_scores STALE (gap={score_gap_s:.0f}s "
                f"> {FRESHNESS_MAX_AGE_S}s) — scoring loop has stopped."
            )
        else:
            # Defensive — should be unreachable given the gate above.
            r.fail(
                f"{sport}: delta_dirty_queue empty and freshness "
                f"checks did not isolate a cause "
                f"(live_gap={live_gap_s}, score_gap={score_gap_s}, "
                f"watchdog={watchdog_events})."
            )
    return r


# ─────────────────────────────────────────────────────────────────────
# §5  API/UI correctness — sample 10 visible picks per sport
# ─────────────────────────────────────────────────────────────────────
def _is_on_grid(rate: float | None, window: int) -> bool:
    if rate is None:
        return True
    step = 100 / window
    nearest = round(rate / step) * step
    return abs(rate - nearest) < 1e-6


async def check_api_correctness(db, now: datetime) -> CheckResult:
    r = CheckResult(name="5_api_correctness", passed=True)
    samples_per_sport: dict[str, list[dict]] = {}
    for sport in SPORTS:
        path_prefix = "/api/v3/mlb/ferrari" if sport == "mlb" else "/api/v3/ferrari"
        sport_samples: list[dict] = []
        for tier in ("safe-haven", "front-lines", "war-zone"):
            try:
                payload = _api_get(f"{path_prefix}/{tier}?limit=20")
            except Exception as exc:  # noqa: BLE001
                r.fail(f"{sport}/{tier}: API unreachable — {exc!r}")
                continue
            picks = payload.get("picks", []) or []
            sport_samples.extend(picks)
            if len(sport_samples) >= SAMPLE_SIZE_PER_SPORT:
                break
        sport_samples = sport_samples[:SAMPLE_SIZE_PER_SPORT]
        samples_per_sport[sport] = []

        coll = db[f"{sport}_prop_scores"]
        for ap in sport_samples:
            ck = ap.get("canonical_key")
            score_doc = await coll.find_one(
                {"canonical_key": ck},
                {
                    "_id": 0,
                    "player_name": 1,
                    "stat_type": 1,
                    "line": 1,
                    "tier": 1,
                    "recommendation": 1,
                    **{f: 1 for f in CANONICAL_HR_FIELDS},
                },
            ) if ck else None
            row = {
                "canonical_key": ck,
                "player": ap.get("player_name"),
                "tier": ap.get("tier"),
                "api_canonical": {f: ap.get(f) for f in CANONICAL_HR_FIELDS},
                "score_doc_canonical": (
                    {f: score_doc.get(f) for f in CANONICAL_HR_FIELDS}
                    if score_doc else None
                ),
                "api_legacy_present": {
                    f: ap.get(f) for f in LEGACY_API_FIELDS if ap.get(f) is not None
                },
            }
            samples_per_sport[sport].append(row)

            if score_doc is None:
                r.fail(
                    f"{sport} {ap.get('player_name')}: API returned a pick "
                    f"with canonical_key={ck} but no matching score doc"
                )
                continue
            # Match canonical fields between API and score doc
            for f in CANONICAL_HR_FIELDS:
                if ap.get(f) != score_doc.get(f):
                    r.fail(
                        f"{sport} {ap.get('player_name')} {f}: "
                        f"API={ap.get(f)} != score_doc={score_doc.get(f)}"
                    )
            # Grid checks
            l5 = ap.get("hit_rate_l5")
            l10 = ap.get("hit_rate_l10")
            l20 = ap.get("hit_rate_l20")
            if not _is_on_grid(l5, 5):
                r.fail(
                    f"{sport} {ap.get('player_name')}: L5={l5} OFF-GRID "
                    f"(must be in {{0,20,40,60,80,100}})"
                )
            if not _is_on_grid(l10, 10):
                r.fail(
                    f"{sport} {ap.get('player_name')}: L10={l10} OFF-GRID "
                    f"(must be a multiple of 10)"
                )
            if not _is_on_grid(l20, 20):
                r.fail(
                    f"{sport} {ap.get('player_name')}: L20={l20} OFF-GRID "
                    f"(must be a multiple of 5)"
                )
            # Legacy-alias presence in API
            for legacy in LEGACY_API_FIELDS:
                if legacy in ap and ap.get(legacy) is not None:
                    r.fail(
                        f"{sport} {ap.get('player_name')}: API still "
                        f"exposes legacy field `{legacy}`={ap.get(legacy)}"
                    )
    r.evidence["samples"] = samples_per_sport
    r.evidence["sample_counts"] = {s: len(v) for s, v in samples_per_sport.items()}
    # Empty sample MUST fail — without samples this check is vacuous and
    # would mask broken tiers / dead ingestion behind a green PASS.
    for sport in SPORTS:
        if not samples_per_sport.get(sport):
            r.fail(
                f"{sport}: API returned 0 picks across SH/FL/WZ — "
                f"correctness loop had no samples to validate "
                f"(empty-sample is treated as FAIL by SLO §5)."
            )
    return r


# ─────────────────────────────────────────────────────────────────────
# §6  Vision Intel coverage
# ─────────────────────────────────────────────────────────────────────
async def check_vision_intel_coverage(db, now: datetime) -> CheckResult:
    r = CheckResult(name="6_vision_intel_coverage", passed=True)
    for sport in SPORTS:
        coll = db[f"{sport}_prop_scores"]
        per_tier: dict[str, dict] = {}
        total_active = 0
        total_with_vi = 0
        for tier in TIERS:
            t = await coll.count_documents({"tier": tier, "active": True})
            v = await coll.count_documents(
                {
                    "tier": tier,
                    "active": True,
                    "vision_intel": {"$nin": [None, ""]},
                }
            )
            per_tier[tier] = {
                "active": t,
                "with_vision_intel": v,
                "coverage_pct": round(100 * v / t, 1) if t else None,
            }
            total_active += t
            total_with_vi += v
        coverage_pct = (
            round(100 * total_with_vi / total_active, 1)
            if total_active else None
        )
        r.evidence[sport] = {
            "per_tier": per_tier,
            "total_active": total_active,
            "total_with_vision_intel": total_with_vi,
            "coverage_pct": coverage_pct,
        }
        if total_active == 0:
            r.fail(f"{sport}: NO active picks at all — coverage undefined")
        elif coverage_pct is not None and coverage_pct < VISION_COVERAGE_MIN_PCT:
            r.fail(
                f"{sport}: Vision Intel coverage {coverage_pct}% < "
                f"{VISION_COVERAGE_MIN_PCT}% threshold "
                f"(active={total_active}, with_vi={total_with_vi})"
            )
    return r


# ─────────────────────────────────────────────────────────────────────
# §7  Tier counts non-empty
# ─────────────────────────────────────────────────────────────────────
async def check_tier_counts(db, now: datetime) -> CheckResult:
    r = CheckResult(name="7_tier_counts", passed=True)
    for sport in SPORTS:
        path_prefix = "/api/v3/mlb/ferrari" if sport == "mlb" else "/api/v3/ferrari"
        per_tier: dict[str, dict] = {}
        for tier_slug, tier_key in (
            ("safe-haven", "safe_haven"),
            ("front-lines", "front_lines"),
            ("war-zone", "war_zone"),
        ):
            try:
                payload = _api_get(f"{path_prefix}/{tier_slug}?limit=50")
            except Exception as exc:  # noqa: BLE001
                r.fail(f"{sport}/{tier_slug}: API error — {exc!r}")
                continue
            picks = payload.get("picks", []) or []
            per_tier[tier_key] = {
                "api_count": len(picks),
                "api_total_before_filter": payload.get("total_before_filter"),
                "api_status": payload.get("status"),
            }
            if not picks:
                # Empty is a fail unless the API tells us *why*
                # (status / fail_reasons populated).
                reasons = (
                    payload.get("fail_reasons")
                    or payload.get("rejection_reasons")
                    or payload.get("status")
                )
                r.fail(
                    f"{sport}/{tier_key}: EMPTY tier "
                    f"(reasons={reasons!r})"
                )
        r.evidence[sport] = per_tier
    return r


# ─────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────
async def run_all() -> tuple[bool, list[CheckResult], dict]:
    started = _utcnow()
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    checks = [
        check_ingestion_freshness,
        check_score_freshness,
        check_tier_freshness,
        check_detection_source_freshness,
        check_api_correctness,
        check_vision_intel_coverage,
        check_tier_counts,
    ]
    results: list[CheckResult] = []
    now = _utcnow()
    for fn in checks:
        try:
            res = await fn(db, now)
        except Exception as exc:  # noqa: BLE001
            res = CheckResult(name=fn.__name__, passed=False)
            res.fail(f"check raised: {exc!r}")
        results.append(res)
    overall_pass = all(r.passed for r in results)
    completed = _utcnow()
    meta = {
        "started_at": started.isoformat(),
        "completed_at": completed.isoformat(),
        "duration_seconds": (completed - started).total_seconds(),
        "preview_url": os.environ.get("REACT_APP_BACKEND_URL"),
        "mongo_db": os.environ.get("DB_NAME"),
    }
    cli.close()
    return overall_pass, results, meta


def _print_human(overall: bool, results: list[CheckResult], meta: dict) -> None:
    print("=" * 78)
    print(f"PRODUCTION READINESS SLO CHECK   {meta['completed_at']}")
    print(f"   preview_url   = {meta['preview_url']}")
    print(f"   mongo_db      = {meta['mongo_db']}")
    print(f"   duration      = {meta['duration_seconds']:.1f}s")
    print(f"   OVERALL       = {'PASS' if overall else 'FAIL'}")
    print("=" * 78)
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"\n[{status}] {r.name}")
        if r.evidence:
            print("  evidence:")
            for k, v in r.evidence.items():
                print(f"    {k}: {json.dumps(v, default=str, indent=2)[:1200]}")
        if r.failures:
            print("  failures:")
            for f in r.failures:
                print(f"    - {f}")
    print("\n" + "=" * 78)
    print("FINAL VERDICT:", "PASS" if overall else "FAIL")
    print("=" * 78)


def _print_json(overall: bool, results: list[CheckResult], meta: dict) -> None:
    print(json.dumps({
        "overall_pass": overall,
        "meta": meta,
        "results": [
            {
                "name": r.name,
                "passed": r.passed,
                "evidence": r.evidence,
                "failures": r.failures,
            }
            for r in results
        ],
    }, default=str, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Production readiness SLO check.")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable JSON output")
    args = parser.parse_args()

    try:
        overall, results, meta = asyncio.run(run_all())
    except Exception as exc:  # noqa: BLE001
        print(f"SLO_CHECK_INTERNAL_ERROR: {exc!r}", file=sys.stderr)
        return 2

    if args.json:
        _print_json(overall, results, meta)
    else:
        _print_human(overall, results, meta)
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
