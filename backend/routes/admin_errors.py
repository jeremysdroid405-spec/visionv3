"""Admin routes for the structured error log.

One triage endpoint:

    GET /api/v3/admin/errors/summary?hours=24
    GET /api/v3/admin/errors/recent?subsystem=...

Returns aggregated counts by subsystem × exception_type so you can see
the top-N regressions at a glance. The rows themselves live in
`error_log` with a 14-day TTL (see `services/observability/error_log.py`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from services.observability import ERROR_LOG_COLLECTION

router = APIRouter(prefix="/api/v3/admin/errors", tags=["admin"])

_db = None


def set_admin_errors_db(db) -> None:
    global _db
    _db = db


def _require_db():
    if _db is None:
        raise HTTPException(
            status_code=500,
            detail="admin_errors db not initialized (call set_admin_errors_db)",
        )
    return _db


@router.get("/summary")
async def errors_summary(
    hours: int = Query(24, ge=1, le=168),
    subsystem: Optional[str] = Query(None),
) -> Dict[str, Any]:
    """Aggregate the last N hours of caught exceptions by subsystem x type."""
    db = _require_db()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    match: Dict[str, Any] = {"ts": {"$gte": cutoff}}
    if subsystem:
        match["subsystem"] = subsystem

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": {
                "subsystem": "$subsystem",
                "exception_type": "$exception_type",
            },
            "count": {"$sum": 1},
            "last_ts": {"$max": "$ts"},
            "last_message": {"$last": "$message"},
            "sports": {"$addToSet": "$sport"},
        }},
        {"$sort": {"count": -1}},
        {"$limit": 200},
    ]
    rows: List[Dict[str, Any]] = []
    async for r in db[ERROR_LOG_COLLECTION].aggregate(pipeline):
        last_ts = r.get("last_ts")
        rows.append({
            "subsystem": r["_id"]["subsystem"],
            "exception_type": r["_id"]["exception_type"],
            "count": r["count"],
            "last_ts": last_ts.isoformat() if hasattr(last_ts, "isoformat") else None,
            "last_message": r.get("last_message"),
            "sports": [s for s in (r.get("sports") or []) if s],
        })
    total = sum(r["count"] for r in rows)
    return {
        "window_hours": hours,
        "subsystem_filter": subsystem,
        "total_errors": total,
        "rows": rows,
    }


@router.get("/recent")
async def errors_recent(
    subsystem: Optional[str] = Query(None),
    exception_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> Dict[str, Any]:
    """Return the most recent N error rows for drill-down debugging."""
    db = _require_db()
    q: Dict[str, Any] = {}
    if subsystem:
        q["subsystem"] = subsystem
    if exception_type:
        q["exception_type"] = exception_type
    cursor = db[ERROR_LOG_COLLECTION].find(
        q, {"_id": 0, "traceback": 0},
    ).sort("ts", -1).limit(limit)
    rows = await cursor.to_list(limit)
    for r in rows:
        if "ts" in r and hasattr(r["ts"], "isoformat"):
            r["ts"] = r["ts"].isoformat()
    return {"count": len(rows), "rows": rows}
