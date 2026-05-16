"""Admin: Statcast ingest health endpoint — Operational PR C.

GET /api/admin/mlb/statcast-health
    Returns:
      - overall_status: ok | warning | error  (worst of last 3 runs)
      - consecutive_failures: int
      - newest_raw_game_date / newest_feature_game_date
      - recent_heartbeats: last 10 heartbeat rows

GET /api/admin/mlb/statcast-heartbeats?n=N
    Returns the last N heartbeat rows verbatim.

Both endpoints are read-only. Unauthenticated (project has no auth yet).
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Query

from services.scheduled.statcast_heartbeat import (
    get_health_summary,
    get_recent_heartbeats,
)

router = APIRouter(prefix="/api/admin/mlb", tags=["admin-mlb-statcast"])

_db = None


def set_db(db) -> None:
    global _db
    _db = db


@router.get("/statcast-health")
async def statcast_health() -> Dict[str, Any]:
    if _db is None:
        return {"error": "db_not_initialised"}
    return await get_health_summary(_db)


@router.get("/statcast-heartbeats")
async def statcast_heartbeats(
    n: int = Query(default=20, ge=1, le=200),
) -> Dict[str, Any]:
    if _db is None:
        return {"error": "db_not_initialised"}
    rows = await get_recent_heartbeats(_db, n=n)
    return {"count": len(rows), "heartbeats": rows}
