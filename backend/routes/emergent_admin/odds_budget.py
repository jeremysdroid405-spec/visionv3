"""
/api/emergent-admin/odds-budget — Odds API call-budget telemetry.

Read-only. Gated by X-Admin-Token like the rest of the admin surface.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from .auth import _get_db, require_admin_token
from services.odds_api_budget import snapshot, MAX_CALLS_PER_HOUR

router = APIRouter(tags=["emergent-admin"])


@router.get("/snapshot")
async def get_budget_snapshot(
    auth=Depends(require_admin_token),
) -> Dict[str, Any]:
    """Current in-process budget state.

    Returns rolling counts by caller/sport/endpoint plus the configured
    limits. NOTE: this is per-process state; if multiple backend workers
    are running, query each in turn or rely on the persisted
    `odds_api_call_log` for the cross-process truth.
    """
    snap = snapshot()
    snap["limit"] = MAX_CALLS_PER_HOUR
    snap["pct_used"] = (
        round(100.0 * snap["hour_count"] / MAX_CALLS_PER_HOUR, 1)
        if MAX_CALLS_PER_HOUR > 0 else 0.0)
    return {"ok": True, "snapshot": snap}


@router.get("/recent")
async def list_recent_calls(
    limit: int = Query(50, ge=1, le=500),
    caller: Optional[str] = None,
    sport: Optional[str] = None,
    sync_mode: Optional[str] = None,
    auth=Depends(require_admin_token),
) -> Dict[str, Any]:
    """Tail of `odds_api_call_log`. Cross-process truth."""
    db = _get_db()
    q: Dict[str, Any] = {}
    if caller:    q["caller"] = caller
    if sport:     q["sport"] = sport
    if sync_mode: q["sync_mode"] = sync_mode
    rows = []
    async for r in db["odds_api_call_log"].find(q,
        projection={"_id": 0}).sort("ts", -1).limit(limit):
        if isinstance(r.get("ts"), datetime):
            r["ts"] = r["ts"].isoformat()
        rows.append(r)
    return {"ok": True, "count": len(rows), "calls": rows}


@router.get("/aggregate")
async def aggregate_calls(
    hours: int = Query(24, ge=1, le=168),
    auth=Depends(require_admin_token),
) -> Dict[str, Any]:
    """Cross-process aggregate over the last `hours` hours."""
    db = _get_db()
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    pipe = [
        {"$match": {"ts": {"$gte": since}}},
        {"$facet": {
            "by_caller":   [{"$group": {"_id": "$caller",   "n": {"$sum": 1}}},
                             {"$sort": {"n": -1}}],
            "by_sport":    [{"$group": {"_id": "$sport",    "n": {"$sum": 1}}},
                             {"$sort": {"n": -1}}],
            "by_endpoint": [{"$group": {"_id": "$endpoint", "n": {"$sum": 1}}},
                             {"$sort": {"n": -1}}],
            "by_sync_mode":[{"$group": {"_id": "$sync_mode", "n": {"$sum": 1}}},
                             {"$sort": {"n": -1}}],
            "by_hour": [
                {"$group": {
                    "_id": {"$dateTrunc": {"date": "$ts", "unit": "hour"}},
                    "n": {"$sum": 1}}},
                {"$sort": {"_id": 1}},
            ],
            "totals": [{"$count": "n"}],
        }},
    ]
    out: List[Dict[str, Any]] = []
    async for r in db["odds_api_call_log"].aggregate(pipe):
        out.append(r)
    if not out:
        return {"ok": True, "hours": hours, "total": 0}
    f = out[0]
    total = (f.get("totals") or [{"n": 0}])[0].get("n", 0)
    # Convert ObjectIds / datetimes for the by_hour bucket.
    for b in f.get("by_hour") or []:
        if isinstance(b.get("_id"), datetime):
            b["_id"] = b["_id"].isoformat()
    return {"ok": True, "hours": hours, "total": total,
              "by_caller":    f.get("by_caller"),
              "by_sport":     f.get("by_sport"),
              "by_endpoint":  f.get("by_endpoint"),
              "by_sync_mode": f.get("by_sync_mode"),
              "by_hour":      f.get("by_hour")}


@router.get("/event-cache")
async def get_event_cache_stats(
    sport: Optional[str] = Query(None),
    auth=Depends(require_admin_token),
) -> Dict[str, Any]:
    """Per-event odds cache state. Shows TTL window + per-sport
    sync_count vs fresh_count so operators can verify the TTL gate is
    actually engaging (fresh_count >> sync_count is healthy)."""
    from services.odds_event_props_cache import stats as _evcache_stats
    db = _get_db()
    return {"ok": True, **(await _evcache_stats(db, sport=sport))}


@router.delete("/event-cache")
async def bust_event_cache(
    sport: Optional[str] = Query(None),
    event_id: Optional[str] = Query(None),
    auth=Depends(require_admin_token),
) -> Dict[str, Any]:
    """Delete cache entries (forces refetch on next sync). Pass
    `sport` to wipe one sport, both `sport` and `event_id` for a
    single row, or neither to wipe everything."""
    from services.odds_event_props_cache import CACHE_COLL
    db = _get_db()
    q: Dict[str, Any] = {}
    if sport:    q["sport"] = sport.lower()
    if event_id: q["event_id"] = event_id
    res = await db[CACHE_COLL].delete_many(q)
    return {"ok": True, "deleted": res.deleted_count, "filter": q}

