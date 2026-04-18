"""
Real-time vs Full-Rebuild Drift Audit
======================================

Per-sport in-memory ledger of every real-time upsert produced by
`services/board/engine.py::on_new_props()`. When the legacy
full-rebuild coordinator runs and overwrites the same canonical_keys,
we can diff the ledger against the current score docs and flag any
field-level divergence — tier changes, vision_score drift, quality
source flips, etc.

This is the live A/B observation mechanism for the 48h Step 6 window:
legacy and real-time both write `{sport}_prop_scores`, drift audit
proves they converge before legacy writers are retired.

Two storage layers, kept coherent so the observability endpoint can
surface both side-by-side:

  1. In-memory ring buffer per sport (fast, restart-transient, capped
     at 500 entries). Used by `record_realtime_upsert()` / `audit()` /
     `snapshot()`.
  2. Persistent MongoDB collection `board_drift_ledger` (durable,
     survives restarts, 72-h TTL). Used by `persist_entries()` /
     `audit_persisted()`.

The engine's hot path writes BOTH layers synchronously — one
in-memory append + one `insert_many` per event. Budget measured at
≤5 ms per event in the E2E verifier.

Adding a new sport: nothing to do here; the `registered_sports()`
helper iterates the board-adapter registry and this module follows.
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone, timedelta
from threading import RLock
from typing import Any, Deque, Dict, List, Optional

from services.board.adapters import get_adapter, registered_sports

logger = logging.getLogger(__name__)

# Ledger size per sport. Each entry is ~200 bytes → 500 entries ≈ 100 KB
# per sport. Safe for long-running backend.
_MAX_ENTRIES_PER_SPORT = 500

# Vision-score drift threshold — anything larger than this counts as
# a statistical divergence, not floating-point noise.
_VS_DRIFT_EPS = 1.0  # on a 0-100 scale

# Persistent ledger: collection name + 72-h TTL
DRIFT_COLLECTION = "board_drift_ledger"
_TTL_SECONDS = 72 * 3600  # 72 hours

# Rolling windows surfaced by the observability endpoint.
# (label → seconds). Matches the 48-h Step 6 observation window +
# shorter windows so operators can eyeball recent convergence.
ROLLING_WINDOWS: List[tuple] = [
    ("1h", 3600),
    ("6h", 6 * 3600),
    ("24h", 24 * 3600),
    ("48h", 48 * 3600),
]

_LOCK = RLock()
_LEDGERS: Dict[str, Deque[Dict[str, Any]]] = {
    sport: deque(maxlen=_MAX_ENTRIES_PER_SPORT)
    for sport in registered_sports()
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_realtime_upsert(
    sport: str,
    score_docs: List[Dict[str, Any]],
    source: str = "unknown",
) -> int:
    """Called by the engine after every scoped upsert. Appends one
    ledger entry per score doc capturing a SNAPSHOT of the RT write
    (tier, vision_score, quality_source, computed_at, source).

    Returns the count appended. Never raises.
    """
    s = (sport or "").strip().lower()
    if not score_docs:
        return 0
    try:
        with _LOCK:
            ledger = _LEDGERS.setdefault(s, deque(maxlen=_MAX_ENTRIES_PER_SPORT))
            appended = 0
            for d in score_docs:
                ck = d.get("canonical_key")
                if not ck:
                    continue
                ledger.append({
                    "canonical_key": ck,
                    "tier_rt": d.get("tier"),
                    "vision_score_rt": d.get("vision_score"),
                    "quality_source_rt": d.get("quality_source"),
                    "computed_at_rt": d.get("computed_at"),
                    "active_rt": d.get("active"),
                    "recorded_at": _now_iso(),
                    "source": source,
                })
                appended += 1
            return appended
    except Exception as e:
        logger.exception(f"[DRIFT_AUDIT] {s} record failed: {e}")
        return 0


def snapshot(sport: Optional[str] = None) -> Dict[str, Any]:
    """Read-only copy of ledger state. When `sport` is None, returns
    a per-sport summary + counts. Otherwise returns the full ring
    buffer for the requested sport."""
    with _LOCK:
        if sport:
            s = sport.strip().lower()
            entries = list(_LEDGERS.get(s, deque()))
            return {
                "sport": s,
                "count": len(entries),
                "max": _MAX_ENTRIES_PER_SPORT,
                "entries": entries,
            }
        return {
            "by_sport": {
                s: {
                    "count": len(q),
                    "max": _MAX_ENTRIES_PER_SPORT,
                    "oldest_recorded_at": q[0]["recorded_at"] if q else None,
                    "newest_recorded_at": q[-1]["recorded_at"] if q else None,
                }
                for s, q in _LEDGERS.items()
            }
        }


async def audit(
    db,
    sport: str,
    limit: Optional[int] = None,
    include_converged_samples: int = 3,
    include_divergence_samples: int = 20,
) -> Dict[str, Any]:
    """Compare every ledger entry against the CURRENT score doc in
    `{sport}_prop_scores`. Returns a structured drift report.

    Classification per ledger entry:
      - `converged`          : tier and vision_score still match RT.
      - `tier_changed`       : coordinator flipped the tier after RT.
      - `vision_score_drift` : tier same, VS differs by > eps.
      - `missing`            : canonical_key has no score doc (probably
                               replaced by a coordinator run using a
                               different `version_tag`, or dropped).
      - `inactive`           : score doc now has active=False (scanner
                               flipped it — not drift, still
                               informative).
    """
    s = (sport or "").strip().lower()
    report: Dict[str, Any] = {
        "sport": s,
        "audited_at": _now_iso(),
        "ledger_size": 0,
        "audited": 0,
        "converged": 0,
        "tier_changed": 0,
        "vision_score_drift": 0,
        "missing": 0,
        "inactive": 0,
        "divergence_samples": [],
        "converged_samples": [],
    }

    try:
        adapter = get_adapter(s)
    except Exception as e:
        report["error"] = f"unknown_sport: {e}"
        return report

    with _LOCK:
        entries = list(_LEDGERS.get(s, deque()))

    report["ledger_size"] = len(entries)
    if limit:
        entries = entries[-int(limit):]

    scores_coll = db[adapter.scores_collection]
    version_tag = adapter.version_tag

    # Fetch all current docs for the canonical_keys in the ledger
    # in ONE round-trip.
    unique_keys = {e["canonical_key"] for e in entries}
    current_docs: Dict[str, Dict[str, Any]] = {}
    if unique_keys:
        cursor = scores_coll.find(
            {
                "version_tag": version_tag,
                "canonical_key": {"$in": list(unique_keys)},
            },
            {"_id": 0},
        )
        async for d in cursor:
            current_docs[d.get("canonical_key")] = d

    for e in entries:
        report["audited"] += 1
        ck = e["canonical_key"]
        cur = current_docs.get(ck)
        if cur is None:
            report["missing"] += 1
            if len(report["divergence_samples"]) < include_divergence_samples:
                report["divergence_samples"].append({
                    "canonical_key": ck,
                    "class": "missing",
                    "rt": {"tier": e.get("tier_rt"), "vision_score": e.get("vision_score_rt")},
                    "current": None,
                })
            continue

        if cur.get("active") is False:
            report["inactive"] += 1
            continue

        tier_rt = e.get("tier_rt")
        vs_rt = e.get("vision_score_rt")
        tier_cur = cur.get("tier")
        vs_cur = cur.get("vision_score")

        if tier_rt != tier_cur:
            report["tier_changed"] += 1
            if len(report["divergence_samples"]) < include_divergence_samples:
                report["divergence_samples"].append({
                    "canonical_key": ck,
                    "class": "tier_changed",
                    "rt": {
                        "tier": tier_rt, "vision_score": vs_rt,
                        "quality_source": e.get("quality_source_rt"),
                        "computed_at": e.get("computed_at_rt"),
                    },
                    "current": {
                        "tier": tier_cur, "vision_score": vs_cur,
                        "quality_source": cur.get("quality_source"),
                        "computed_at": cur.get("computed_at"),
                    },
                })
            continue

        # Tier matches — check vision_score drift
        try:
            if vs_rt is None and vs_cur is None:
                vs_diff = 0.0
            elif vs_rt is None or vs_cur is None:
                vs_diff = float("inf")
            else:
                vs_diff = abs(float(vs_cur) - float(vs_rt))
        except Exception:
            vs_diff = float("inf")

        if vs_diff > _VS_DRIFT_EPS:
            report["vision_score_drift"] += 1
            if len(report["divergence_samples"]) < include_divergence_samples:
                report["divergence_samples"].append({
                    "canonical_key": ck,
                    "class": "vision_score_drift",
                    "vs_diff": round(vs_diff, 3) if vs_diff != float("inf") else None,
                    "rt": {"tier": tier_rt, "vision_score": vs_rt,
                           "computed_at": e.get("computed_at_rt")},
                    "current": {"tier": tier_cur, "vision_score": vs_cur,
                                "computed_at": cur.get("computed_at")},
                })
            continue

        report["converged"] += 1
        if len(report["converged_samples"]) < include_converged_samples:
            report["converged_samples"].append({
                "canonical_key": ck,
                "tier": tier_cur,
                "vision_score": vs_cur,
                "rt_computed_at": e.get("computed_at_rt"),
                "current_computed_at": cur.get("computed_at"),
            })

    total_divergent = (
        report["tier_changed"] + report["vision_score_drift"] + report["missing"]
    )
    report["divergence_ratio"] = (
        round(total_divergent / report["audited"], 3)
        if report["audited"] else 0.0
    )
    return report


__all__ = [
    "record_realtime_upsert",
    "snapshot",
    "audit",
    "persist_entries",
    "audit_persisted",
    "ensure_persistent_indexes",
    "DRIFT_COLLECTION",
    "ROLLING_WINDOWS",
]


# =============================================================================
# Persistent ledger (MongoDB) — survives restarts, 72 h TTL
# =============================================================================

async def ensure_persistent_indexes(db) -> None:
    """Create the TTL + secondary indexes on the drift ledger
    collection. Idempotent — safe to call on every boot. Logs and
    never raises so a malformed index history can't block startup."""
    coll = db[DRIFT_COLLECTION]
    try:
        existing = await coll.index_information()
        # TTL index on `observed_at`, 72 h. If an existing index on the
        # same field has a DIFFERENT expireAfterSeconds we drop it so
        # TTL semantics never drift.
        ttl_name = "ttl_observed_at_72h"
        for name, info in existing.items():
            if name == ttl_name:
                continue
            keys = info.get("key") or []
            if keys and keys[0][0] == "observed_at" and "expireAfterSeconds" in info:
                if info.get("expireAfterSeconds") != _TTL_SECONDS:
                    await coll.drop_index(name)
        await coll.create_index(
            [("observed_at", 1)],
            expireAfterSeconds=_TTL_SECONDS,
            name=ttl_name,
        )
        # Rolling-window queries filter on (sport, observed_at) so this
        # compound index covers the hot path.
        await coll.create_index(
            [("sport", 1), ("observed_at", -1)],
            name="idx_sport_observed_at_desc",
        )
        # Per-key drift history (for ad-hoc /debug lookups).
        await coll.create_index(
            [("canonical_key", 1), ("observed_at", -1)],
            name="idx_ck_observed_at_desc",
        )
        logger.info(
            f"[DRIFT_AUDIT] persistent ledger indexes ensured on "
            f"{DRIFT_COLLECTION} (ttl={_TTL_SECONDS}s)"
        )
    except Exception as e:
        logger.warning(f"[DRIFT_AUDIT] ensure_persistent_indexes failed: {e}")


async def persist_entries(
    db,
    sport: str,
    score_docs: List[Dict[str, Any]],
    source: str = "unknown",
) -> int:
    """Synchronously append one MongoDB doc per score_doc into
    `board_drift_ledger`. Mirrors the in-memory ring-buffer entry
    shape plus a native-typed `observed_at` so the TTL index works.

    One `insert_many` per batch → one network round-trip regardless
    of batch size. Typical batch is 1–50 docs (scoped real-time
    upserts). Measured ≤ 5 ms / event in the E2E verifier.

    Never raises; returns the count actually inserted."""
    s = (sport or "").strip().lower()
    if not score_docs:
        return 0
    now = datetime.now(timezone.utc)
    docs: List[Dict[str, Any]] = []
    for d in score_docs:
        ck = d.get("canonical_key")
        if not ck:
            continue
        docs.append({
            "sport": s,
            "canonical_key": ck,
            "source": source,
            "observed_at": now,   # TTL field — native datetime
            "tier_rt": d.get("tier"),
            "vision_score_rt": d.get("vision_score"),
            "quality_source_rt": d.get("quality_source"),
            "computed_at_rt": d.get("computed_at"),
            "active_rt": d.get("active"),
            "version_tag": d.get("version_tag"),
        })
    if not docs:
        return 0
    try:
        res = await db[DRIFT_COLLECTION].insert_many(docs, ordered=False)
        return len(res.inserted_ids)
    except Exception as e:
        logger.warning(
            f"[DRIFT_AUDIT] persist_entries {s} failed (n={len(docs)}): {e}"
        )
        return 0


async def _classify_entry(
    entry: Dict[str, Any],
    current: Optional[Dict[str, Any]],
) -> str:
    """Pure classifier used by both in-memory `audit()` and
    `audit_persisted()`. Input keys for `entry`:
      tier_rt, vision_score_rt (everything else optional).
    Input keys for `current`:
      tier, vision_score, active (optional)."""
    if current is None:
        return "missing"
    if current.get("active") is False:
        return "inactive"
    tier_rt = entry.get("tier_rt")
    tier_cur = current.get("tier")
    if tier_rt != tier_cur:
        return "tier_changed"
    vs_rt = entry.get("vision_score_rt")
    vs_cur = current.get("vision_score")
    try:
        if vs_rt is None and vs_cur is None:
            vs_diff = 0.0
        elif vs_rt is None or vs_cur is None:
            vs_diff = float("inf")
        else:
            vs_diff = abs(float(vs_cur) - float(vs_rt))
    except Exception:
        vs_diff = float("inf")
    if vs_diff > _VS_DRIFT_EPS:
        return "vision_score_drift"
    return "converged"


async def _audit_one_window(
    db,
    sport: str,
    window_seconds: int,
    include_divergence_samples: int = 10,
) -> Dict[str, Any]:
    """Audit every persisted ledger entry within the last
    `window_seconds` seconds. Returns a compact classification report."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    adapter = get_adapter(sport)
    coll = db[DRIFT_COLLECTION]
    entries: List[Dict[str, Any]] = []
    cursor = coll.find(
        {"sport": sport, "observed_at": {"$gte": cutoff}},
        {"_id": 0},
    ).sort("observed_at", 1)
    async for d in cursor:
        entries.append(d)
    report: Dict[str, Any] = {
        "window_seconds": window_seconds,
        "entries": len(entries),
        "converged": 0,
        "tier_changed": 0,
        "vision_score_drift": 0,
        "missing": 0,
        "inactive": 0,
        "divergence_ratio": 0.0,
        "divergence_samples": [],
    }
    if not entries:
        return report

    unique_keys = {e["canonical_key"] for e in entries}
    current_docs: Dict[str, Dict[str, Any]] = {}
    scores_coll = db[adapter.scores_collection]
    async for d in scores_coll.find(
        {
            "version_tag": adapter.version_tag,
            "canonical_key": {"$in": list(unique_keys)},
        },
        {"_id": 0},
    ):
        current_docs[d.get("canonical_key")] = d

    for e in entries:
        cur = current_docs.get(e["canonical_key"])
        cls = await _classify_entry(e, cur)
        report[cls] += 1
        if (cls in ("tier_changed", "vision_score_drift", "missing")
                and len(report["divergence_samples"]) < include_divergence_samples):
            report["divergence_samples"].append({
                "canonical_key": e["canonical_key"],
                "class": cls,
                "observed_at": (
                    e["observed_at"].isoformat()
                    if isinstance(e.get("observed_at"), datetime)
                    else e.get("observed_at")
                ),
                "source": e.get("source"),
                "rt": {
                    "tier": e.get("tier_rt"),
                    "vision_score": e.get("vision_score_rt"),
                    "quality_source": e.get("quality_source_rt"),
                    "computed_at": e.get("computed_at_rt"),
                },
                "current": (
                    {
                        "tier": cur.get("tier"),
                        "vision_score": cur.get("vision_score"),
                        "quality_source": cur.get("quality_source"),
                        "computed_at": cur.get("computed_at"),
                    } if cur else None
                ),
            })

    total_divergent = (
        report["tier_changed"] + report["vision_score_drift"] + report["missing"]
    )
    report["divergence_ratio"] = (
        round(total_divergent / report["entries"], 3)
        if report["entries"] else 0.0
    )
    return report


async def audit_persisted(
    db,
    sport: str,
    windows: Optional[List[tuple]] = None,
) -> Dict[str, Any]:
    """Rolling-window convergence report sourced from
    `board_drift_ledger`. Returns per-window classification plus a
    total 72-h count and the latest-observed-at timestamp.

    Never raises — on error returns a skeleton with `error` set."""
    s = (sport or "").strip().lower()
    windows = windows or ROLLING_WINDOWS
    try:
        coll = db[DRIFT_COLLECTION]
        total = await coll.count_documents({"sport": s})
        latest = await coll.find_one(
            {"sport": s}, {"_id": 0, "observed_at": 1},
            sort=[("observed_at", -1)],
        )
        window_reports: Dict[str, Dict[str, Any]] = {}
        for label, secs in windows:
            window_reports[label] = await _audit_one_window(db, s, secs)
        return {
            "sport": s,
            "collection": DRIFT_COLLECTION,
            "ttl_seconds": _TTL_SECONDS,
            "total_entries_72h": total,
            "latest_observed_at": (
                latest["observed_at"].isoformat()
                if latest and isinstance(latest.get("observed_at"), datetime)
                else None
            ),
            "windows": window_reports,
        }
    except Exception as e:
        logger.exception(f"[DRIFT_AUDIT] audit_persisted({s}) failed: {e}")
        return {"sport": s, "error": str(e)}
