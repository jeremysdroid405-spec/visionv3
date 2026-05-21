"""
Candidate-config workflow.

Lifecycle:
    draft        — first save (immutable once activated)
    active       — currently in use; only one per (kind, scope)
    archived     — superseded by another active

Kinds (free-form, but examples we expect):
    "gate_config" | "threshold_set" | "model_config" | "feature_pipeline" |
    "replay_script_params"

Scope is a free-text grouping key (e.g. "MLB-overall", "MLB-pitching_strikeouts",
"NFL-week-9").
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .auth import audit_log, require_admin_token, _get_db

CFG_COLL = "emergent_candidate_configs"
router = APIRouter()


class DraftBody(BaseModel):
    kind:    str = Field(..., min_length=1, max_length=64)
    scope:   str = Field(..., min_length=1, max_length=128)
    config:  Dict[str, Any] = Field(..., description="The actual config blob")
    note:    str = Field(default="", max_length=2000)
    parent_id: Optional[str] = None


@router.post("/draft")
async def save_draft(body: DraftBody, request: Request,
                        auth=Depends(require_admin_token)):
    db = _get_db()
    cid = str(uuid.uuid4())
    doc = {
        "config_id":   cid,
        "kind":        body.kind,
        "scope":       body.scope,
        "config":      body.config,
        "note":        body.note,
        "parent_id":   body.parent_id,
        "status":      "draft",
        "created_at":  datetime.now(timezone.utc),
        "created_by":  auth["agent_id"],
        "activated_at": None, "archived_at": None,
    }
    await db[CFG_COLL].insert_one(doc)
    await audit_log(request, action="cfg_draft",
                      params={"kind": body.kind, "scope": body.scope,
                                "config_id": cid}, **auth)
    return {"ok": True, "config_id": cid, "status": "draft"}


@router.get("")
@router.get("/")
async def list_configs(request: Request,
                          kind: Optional[str] = Query(None),
                          scope: Optional[str] = Query(None),
                          status: Optional[str] = Query(None),
                          limit: int = Query(50, ge=1, le=500),
                          auth=Depends(require_admin_token)):
    q: Dict[str, Any] = {}
    if kind:   q["kind"] = kind
    if scope:  q["scope"] = scope
    if status: q["status"] = status
    db = _get_db()
    docs = []
    cur = db[CFG_COLL].find(q, {"_id": 0}).sort(
        [("created_at", -1)]).limit(limit)
    async for d in cur: docs.append(d)
    await audit_log(request, action="cfg_list",
                      params={"q": q, "limit": limit}, **auth)
    return {"ok": True, "configs": docs}


@router.get("/{config_id}")
async def get_config(config_id: str, request: Request,
                        auth=Depends(require_admin_token)):
    db = _get_db()
    d = await db[CFG_COLL].find_one({"config_id": config_id}, {"_id": 0})
    if not d:
        raise HTTPException(404, f"config {config_id} not found")
    await audit_log(request, action="cfg_get",
                      params={"config_id": config_id}, **auth)
    return {"ok": True, "config": d}


@router.post("/{config_id}/activate")
async def activate(config_id: str, request: Request,
                      auth=Depends(require_admin_token)):
    db = _get_db()
    d = await db[CFG_COLL].find_one({"config_id": config_id})
    if not d:
        raise HTTPException(404, f"config {config_id} not found")
    if d["status"] == "archived":
        raise HTTPException(400, "cannot activate an archived config")
    now = datetime.now(timezone.utc)
    # Archive any current active for the same (kind, scope)
    await db[CFG_COLL].update_many(
        {"kind": d["kind"], "scope": d["scope"], "status": "active",
          "config_id": {"$ne": config_id}},
        {"$set": {"status": "archived", "archived_at": now}})
    await db[CFG_COLL].update_one(
        {"config_id": config_id},
        {"$set": {"status": "active", "activated_at": now}})
    await audit_log(request, action="cfg_activate",
                      params={"config_id": config_id,
                                "kind": d["kind"], "scope": d["scope"]}, **auth)
    return {"ok": True, "config_id": config_id, "status": "active"}


class RollbackBody(BaseModel):
    target_config_id: str
    confirm: bool = False


@router.post("/{config_id}/rollback")
async def rollback(config_id: str, body: RollbackBody, request: Request,
                      auth=Depends(require_admin_token)):
    """Rollback means: archive `config_id`, activate `target_config_id`."""
    if not body.confirm:
        raise HTTPException(400, "set confirm=true to perform a rollback")
    db = _get_db()
    cur = await db[CFG_COLL].find_one({"config_id": config_id})
    tgt = await db[CFG_COLL].find_one({"config_id": body.target_config_id})
    if not cur or not tgt:
        raise HTTPException(404, "either current or target config not found")
    if cur["kind"] != tgt["kind"] or cur["scope"] != tgt["scope"]:
        raise HTTPException(400, "rollback target must share kind+scope")
    now = datetime.now(timezone.utc)
    await db[CFG_COLL].update_one(
        {"config_id": config_id},
        {"$set": {"status": "archived", "archived_at": now}})
    await db[CFG_COLL].update_one(
        {"config_id": body.target_config_id},
        {"$set": {"status": "active", "activated_at": now,
                    "archived_at": None}})
    await audit_log(request, action="cfg_rollback",
                      params={"from": config_id,
                                "to": body.target_config_id}, **auth)
    return {"ok": True, "active": body.target_config_id}
