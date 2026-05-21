"""
Read-only audit-log viewer for the Emergent Admin API.

Lets an authorized agent inspect what has been done through the admin API.
NEVER allows writes/deletes/redactions — it is genuinely read-only.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from .auth import audit_log, require_admin_token, _get_db, AUDIT_COLL

router = APIRouter()


def _strip(d: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(d, dict):
        d.pop("_id", None)
    return d


@router.get("")
@router.get("/")
async def list_audit(request: Request,
                       action: Optional[str] = Query(None),
                       agent_id: Optional[str] = Query(None),
                       status_code: Optional[int] = Query(None),
                       limit: int = Query(100, ge=1, le=1000),
                       skip: int = Query(0, ge=0),
                       auth=Depends(require_admin_token)):
    q: Dict[str, Any] = {}
    if action:      q["action"] = action
    if agent_id:    q["agent_id"] = agent_id
    if status_code is not None: q["status_code"] = status_code
    db = _get_db()
    cur = db[AUDIT_COLL].find(q, {"_id": 0}).sort(
        [("ts", -1)]).skip(skip).limit(limit)
    docs: List[Dict[str, Any]] = []
    async for d in cur:
        docs.append(_strip(d))
    await audit_log(request, action="audit_list",
                     params={"q": q, "limit": limit, "skip": skip},
                     response_summary={"returned": len(docs)}, **auth)
    return {"ok": True, "entries": docs, "count": len(docs)}


@router.get("/summary")
async def summary(request: Request,
                     hours: int = Query(24, ge=1, le=24 * 30),
                     auth=Depends(require_admin_token)):
    """Action-count + status-code rollups for the last N hours."""
    from datetime import datetime, timedelta, timezone
    db = _get_db()
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    pipe = [
        {"$match": {"ts": {"$gte": since}}},
        {"$group": {
            "_id": {"action": "$action", "status_code": "$status_code"},
            "n": {"$sum": 1},
        }},
        {"$sort": {"n": -1}},
        {"$limit": 200},
    ]
    rows: List[Dict[str, Any]] = []
    async for d in db[AUDIT_COLL].aggregate(pipe):
        rows.append({
            "action": d["_id"].get("action"),
            "status_code": d["_id"].get("status_code"),
            "n": d["n"],
        })
    await audit_log(request, action="audit_summary",
                     params={"hours": hours},
                     response_summary={"rows": len(rows)}, **auth)
    return {"ok": True, "hours": hours, "rows": rows}
