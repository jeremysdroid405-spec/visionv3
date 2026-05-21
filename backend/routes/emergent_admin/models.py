"""
Universal Model Registry — research-side bookkeeping for production model
artifacts, candidate models, and feature schemas across all sports.

Backing store: `emergent_model_registry` (writable). Documents are pure
metadata — actual pickles still live on disk under /app/backend/models/.

Document shape:
{
  model_id:        "MLB:HF:v3.2_phase2b:research",   # unique
  sport:           "MLB",
  family:          "HF",                             # MLB-HF, NBA-VK2, NFL-…
  version:         "v3.2_phase2b",
  mode:            "production" | "research" | "candidate" | "archived",
  artifact_path:   "/app/backend/models/mlb_hf/_phase2a_workdir/...",
  feature_schema_version: "phase2b_lineup_v1",
  compatible_stat_families: ["hits", "total_bases", ...],
  validation_status: "untested" | "passed" | "failed",
  last_validation_at: ISO,
  last_replay_run_id: str | null,
  notes: str,
  created_at: ISO, updated_at: ISO,
}

Endpoints:
  GET  /                       list (?sport=&mode=&family=)
  POST /                       register / upsert
  POST /{model_id}/validate    surface a validation result (status-only)
  POST /{model_id}/clone       clone an entry (production → research)
  POST /{model_id}/activate    set this entry to mode=research
  POST /{model_id}/archive     soft-archive (mode=archived)
  DELETE /{model_id}           HARD delete (requires confirm token)

No mutation of underlying pickle files. The registry is metadata only —
it does NOT load or run models. That guarantees production isolation.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .auth import audit_log, require_admin_token, _get_db

router = APIRouter()
COLL = "emergent_model_registry"

ALLOWED_MODES = {"production", "research", "candidate", "archived"}


class RegisterBody(BaseModel):
    model_id:               str   = Field(..., min_length=3, max_length=200)
    sport:                  str   = Field(..., min_length=2, max_length=8)
    family:                 str   = Field(default="HF", max_length=32)
    version:                str   = Field(..., min_length=1, max_length=64)
    mode:                   str   = Field(default="candidate")
    artifact_path:          str   = Field(default="", max_length=400)
    feature_schema_version: str   = Field(default="", max_length=64)
    compatible_stat_families: List[str] = Field(default_factory=list)
    notes:                  str   = Field(default="", max_length=2000)
    upsert:                 bool  = Field(default=True)


class ValidateBody(BaseModel):
    passed:    bool
    notes:     str = ""
    sample_n:  Optional[int] = None
    last_replay_run_id: Optional[str] = None


class CloneBody(BaseModel):
    new_model_id:    str
    new_mode:        str = "research"
    new_version:     Optional[str] = None
    notes:           str = ""


class DeleteBody(BaseModel):
    confirm_token: str


def _strip(d: Dict[str, Any]) -> Dict[str, Any]:
    if not d:
        return d
    d.pop("_id", None)
    return d


@router.get("")
@router.get("/")
async def list_models(request: Request,
                          sport: Optional[str] = Query(None),
                          mode:  Optional[str] = Query(None),
                          family: Optional[str] = Query(None),
                          limit: int = Query(200, ge=1, le=1000),
                          auth=Depends(require_admin_token)):
    db = _get_db()
    q: Dict[str, Any] = {}
    if sport:  q["sport"]  = sport.upper()
    if mode:   q["mode"]   = mode
    if family: q["family"] = family
    docs: List[Dict[str, Any]] = []
    async for d in db[COLL].find(q).sort([("updated_at", -1)]).limit(limit):
        docs.append(_strip(d))
    await audit_log(request, action="model_registry_list",
                      params={"sport": sport, "mode": mode, "family": family},
                      response_summary={"n": len(docs)}, **auth)
    return {"ok": True, "models": docs}


@router.post("")
@router.post("/")
async def register_model(body: RegisterBody, request: Request,
                              auth=Depends(require_admin_token)):
    if body.mode not in ALLOWED_MODES:
        raise HTTPException(400,
                              f"mode must be one of {sorted(ALLOWED_MODES)}")
    db = _get_db()
    now = datetime.now(timezone.utc)
    doc = body.model_dump()
    doc.update({
        "sport":      doc["sport"].upper(),
        "updated_at": now,
    })
    existing = await db[COLL].find_one({"model_id": doc["model_id"]})
    if existing and not body.upsert:
        raise HTTPException(409,
                              f"model_id already exists: {doc['model_id']}")
    if existing:
        await db[COLL].update_one(
            {"model_id": doc["model_id"]},
            {"$set": {k: v for k, v in doc.items() if k != "upsert"}})
        action = "updated"
    else:
        doc["created_at"] = now
        doc["validation_status"] = "untested"
        await db[COLL].insert_one({k: v for k, v in doc.items() if k != "upsert"})
        action = "created"
    await audit_log(request, action="model_registry_register",
                      params={"model_id": doc["model_id"], "mode": doc["mode"]},
                      response_summary={"action": action}, **auth)
    saved = _strip(await db[COLL].find_one({"model_id": doc["model_id"]}))
    return {"ok": True, "action": action, "model": saved}


@router.post("/{model_id}/validate")
async def validate_model(model_id: str, body: ValidateBody, request: Request,
                              auth=Depends(require_admin_token)):
    db = _get_db()
    existing = await db[COLL].find_one({"model_id": model_id})
    if not existing:
        raise HTTPException(404, f"model_id not found: {model_id}")
    now = datetime.now(timezone.utc)
    update: Dict[str, Any] = {
        "validation_status": "passed" if body.passed else "failed",
        "last_validation_at": now,
        "last_validation_notes": body.notes,
        "updated_at": now,
    }
    if body.sample_n is not None:
        update["last_validation_sample_n"] = body.sample_n
    if body.last_replay_run_id:
        update["last_replay_run_id"] = body.last_replay_run_id
    await db[COLL].update_one({"model_id": model_id}, {"$set": update})
    await audit_log(request, action="model_registry_validate",
                      params={"model_id": model_id, "passed": body.passed},
                      response_summary=update, **auth)
    return {"ok": True, "model": _strip(await db[COLL].find_one({"model_id": model_id}))}


@router.post("/{model_id}/clone")
async def clone_model(model_id: str, body: CloneBody, request: Request,
                          auth=Depends(require_admin_token)):
    if body.new_mode not in ALLOWED_MODES:
        raise HTTPException(400, f"new_mode must be in {sorted(ALLOWED_MODES)}")
    db = _get_db()
    src = await db[COLL].find_one({"model_id": model_id})
    if not src:
        raise HTTPException(404, f"source model_id not found: {model_id}")
    if await db[COLL].find_one({"model_id": body.new_model_id}):
        raise HTTPException(409,
                              f"new_model_id already exists: {body.new_model_id}")
    now = datetime.now(timezone.utc)
    cloned = _strip(dict(src))
    cloned.update({
        "model_id":   body.new_model_id,
        "mode":       body.new_mode,
        "version":    body.new_version or cloned.get("version") + "_cloned",
        "notes":      body.notes or f"Cloned from {model_id} at {now.isoformat()}",
        "created_at": now,
        "updated_at": now,
        "validation_status": "untested",
        "cloned_from": model_id,
    })
    await db[COLL].insert_one(cloned)
    await audit_log(request, action="model_registry_clone",
                      params={"src": model_id, "dst": body.new_model_id},
                      response_summary={"mode": body.new_mode}, **auth)
    return {"ok": True, "model": _strip(await db[COLL].find_one(
        {"model_id": body.new_model_id}))}


@router.post("/{model_id}/activate")
async def activate_model(model_id: str, request: Request,
                              auth=Depends(require_admin_token)):
    """Mark the given model_id as the active research model for its sport.
    Demotes any other 'research'-mode entry of the same sport to 'candidate'.
    NEVER touches production mode."""
    db = _get_db()
    src = await db[COLL].find_one({"model_id": model_id})
    if not src:
        raise HTTPException(404, f"model_id not found: {model_id}")
    if src["mode"] == "production":
        raise HTTPException(403,
                              "Cannot activate a production-mode entry "
                              "as research. Clone it first.")
    now = datetime.now(timezone.utc)
    # Demote prior actives
    await db[COLL].update_many(
        {"sport": src["sport"], "mode": "research",
          "model_id": {"$ne": model_id}},
        {"$set": {"mode": "candidate", "updated_at": now}})
    await db[COLL].update_one(
        {"model_id": model_id},
        {"$set": {"mode": "research", "updated_at": now}})
    await audit_log(request, action="model_registry_activate",
                      params={"model_id": model_id, "sport": src["sport"]},
                      response_summary={"mode": "research"}, **auth)
    return {"ok": True, "model": _strip(await db[COLL].find_one(
        {"model_id": model_id}))}


@router.post("/{model_id}/archive")
async def archive_model(model_id: str, request: Request,
                            auth=Depends(require_admin_token)):
    db = _get_db()
    existing = await db[COLL].find_one({"model_id": model_id})
    if not existing:
        raise HTTPException(404, f"model_id not found: {model_id}")
    if existing["mode"] == "production":
        raise HTTPException(403, "Refusing to archive a production entry.")
    now = datetime.now(timezone.utc)
    await db[COLL].update_one(
        {"model_id": model_id},
        {"$set": {"mode": "archived", "updated_at": now,
                    "archived_at": now}})
    await audit_log(request, action="model_registry_archive",
                      params={"model_id": model_id}, **auth)
    return {"ok": True, "model": _strip(await db[COLL].find_one(
        {"model_id": model_id}))}


@router.delete("/{model_id}")
async def delete_model(model_id: str, body: DeleteBody, request: Request,
                            auth=Depends(require_admin_token)):
    if body.confirm_token != "I_UNDERSTAND_MODEL_REGISTRY_DELETE":
        raise HTTPException(400, "missing confirm_token")
    db = _get_db()
    existing = await db[COLL].find_one({"model_id": model_id})
    if not existing:
        raise HTTPException(404, f"model_id not found: {model_id}")
    if existing.get("mode") == "production":
        raise HTTPException(403, "Refusing to delete a production entry.")
    await db[COLL].delete_one({"model_id": model_id})
    await audit_log(request, action="model_registry_delete",
                      params={"model_id": model_id}, **auth)
    return {"ok": True, "deleted": model_id}
