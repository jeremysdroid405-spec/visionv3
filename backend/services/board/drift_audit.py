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

Design:
  - Bounded ring buffer per sport (default 500 entries). Overflow
    drops oldest. Zero persistence; restart resets.
  - `record_realtime_upsert(sport, score_docs)` appends (no
    deduplication by key — a key can appear multiple times across
    distinct real-time events, and every snapshot is informative).
  - `audit(db, sport, since, limit)` reads the current score docs
    for every canonical_key in the ledger and classifies each as
    `converged | tier_changed | vision_score_drift | missing`.
  - `snapshot(sport)` returns the raw ledger for inspection.

Audit output is sport-agnostic so the observability endpoint can
just loop `for sport in registered_sports()`.
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
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
]
