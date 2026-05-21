"""
Allowlisted Mongo CRUD endpoints.
Reads always honor the readable allowlist.
Writes are REJECTED on protected collections (fail-closed).
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from .auth import audit_log, require_admin_token, _get_db
from .policy import collection_readable, collection_writable

router = APIRouter()


def _validate_filter(filt: Dict[str, Any]) -> None:
    """Reject operators that can escape, RCE, or scan the entire DB."""
    if not isinstance(filt, dict):
        raise HTTPException(400, "filter must be an object")
    forbidden = {"$where", "$function", "$accumulator", "$out", "$merge"}
    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in forbidden:
                    raise HTTPException(400, f"forbidden operator: {k}")
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)
    walk(filt)


def _strip_id(d: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(d, dict):
        d.pop("_id", None)
    return d


# ── Find ──────────────────────────────────────────────────────────────────
class FindBody(BaseModel):
    filter:     Dict[str, Any] = Field(default_factory=dict)
    projection: Optional[Dict[str, Any]] = None
    sort:       Optional[List[List[Any]]] = None   # [["ts", -1], ...]
    limit:      int = Field(default=100, ge=1, le=2000)
    skip:       int = Field(default=0, ge=0)


@router.post("/{coll}/find")
async def find(coll: str, body: FindBody, request: Request,
                  auth=Depends(require_admin_token)):
    if not collection_readable(coll):
        raise HTTPException(403, f"collection '{coll}' not in read allowlist")
    _validate_filter(body.filter)
    db = _get_db()
    proj = body.projection or {"_id": 0}
    if "_id" not in proj:
        proj["_id"] = 0
    cur = db[coll].find(body.filter, proj).skip(body.skip).limit(body.limit)
    if body.sort:
        cur = cur.sort([(k, int(d)) for k, d in body.sort])
    docs = [_strip_id(d) async for d in cur]
    await audit_log(request, action="find",
                      params={"coll": coll, "filter": body.filter,
                                "limit": body.limit, "skip": body.skip},
                      response_summary={"returned": len(docs)}, **auth)
    return {"ok": True, "docs": docs, "count": len(docs)}


# ── Aggregate (read-only) ─────────────────────────────────────────────────
class AggregateBody(BaseModel):
    pipeline: List[Dict[str, Any]]
    limit:    int = Field(default=200, ge=1, le=2000)


@router.post("/{coll}/aggregate")
async def aggregate(coll: str, body: AggregateBody, request: Request,
                       auth=Depends(require_admin_token)):
    if not collection_readable(coll):
        raise HTTPException(403, f"collection '{coll}' not in read allowlist")
    # Validate each stage
    for stage in body.pipeline:
        _validate_filter(stage)
        # Block stages that materialize into another collection
        if any(k in stage for k in ("$out", "$merge")):
            raise HTTPException(400, "forbidden aggregation stage")
    db = _get_db()
    pipe = list(body.pipeline) + [{"$limit": body.limit}]
    docs = [_strip_id(d) async for d in
             db[coll].aggregate(pipe, allowDiskUse=True)]
    await audit_log(request, action="aggregate",
                      params={"coll": coll,
                                "stages": len(body.pipeline)},
                      response_summary={"returned": len(docs)}, **auth)
    return {"ok": True, "docs": docs, "count": len(docs)}


# ── Count ─────────────────────────────────────────────────────────────────
@router.post("/{coll}/count")
async def count(coll: str, body: FindBody, request: Request,
                  auth=Depends(require_admin_token)):
    if not collection_readable(coll):
        raise HTTPException(403, f"collection '{coll}' not in read allowlist")
    _validate_filter(body.filter)
    db = _get_db()
    n = await db[coll].count_documents(body.filter)
    await audit_log(request, action="count",
                      params={"coll": coll, "filter": body.filter},
                      response_summary={"n": n}, **auth)
    return {"ok": True, "n": n}


# ── Distinct ──────────────────────────────────────────────────────────────
class DistinctBody(BaseModel):
    field:  str
    filter: Dict[str, Any] = Field(default_factory=dict)


@router.post("/{coll}/distinct")
async def distinct(coll: str, body: DistinctBody, request: Request,
                      auth=Depends(require_admin_token)):
    if not collection_readable(coll):
        raise HTTPException(403, f"collection '{coll}' not in read allowlist")
    _validate_filter(body.filter)
    db = _get_db()
    vals = await db[coll].distinct(body.field, body.filter)
    await audit_log(request, action="distinct",
                      params={"coll": coll, "field": body.field,
                                "filter": body.filter},
                      response_summary={"values_returned": len(vals)}, **auth)
    return {"ok": True, "values": vals, "count": len(vals)}


# ── Insert (writable only) ────────────────────────────────────────────────
class InsertBody(BaseModel):
    docs: List[Dict[str, Any]] = Field(..., min_length=1, max_length=1000)


@router.post("/{coll}/insert")
async def insert(coll: str, body: InsertBody, request: Request,
                    auth=Depends(require_admin_token)):
    if not collection_writable(coll):
        raise HTTPException(403, f"collection '{coll}' is not writable "
                                    f"(protected or not in allowlist)")
    db = _get_db()
    r = await db[coll].insert_many(body.docs, ordered=False)
    await audit_log(request, action="insert",
                      params={"coll": coll, "n_docs": len(body.docs)},
                      response_summary={"inserted": len(r.inserted_ids)},
                      **auth)
    return {"ok": True, "inserted": len(r.inserted_ids)}


# ── Update ────────────────────────────────────────────────────────────────
class UpdateBody(BaseModel):
    filter: Dict[str, Any]
    update: Dict[str, Any]
    upsert: bool = False
    many:   bool = False


@router.post("/{coll}/update")
async def update(coll: str, body: UpdateBody, request: Request,
                    auth=Depends(require_admin_token)):
    if not collection_writable(coll):
        raise HTTPException(403, f"collection '{coll}' is not writable")
    _validate_filter(body.filter)
    _validate_filter(body.update)
    db = _get_db()
    if body.many:
        r = await db[coll].update_many(body.filter, body.update,
                                          upsert=body.upsert)
    else:
        r = await db[coll].update_one(body.filter, body.update,
                                         upsert=body.upsert)
    summary = {"matched": r.matched_count, "modified": r.modified_count,
                "upserted_id": str(r.upserted_id) if r.upserted_id else None}
    await audit_log(request, action="update",
                      params={"coll": coll, "many": body.many,
                                "upsert": body.upsert,
                                "filter": body.filter},
                      response_summary=summary, **auth)
    return {"ok": True, **summary}


# ── Delete ────────────────────────────────────────────────────────────────
class DeleteBody(BaseModel):
    filter: Dict[str, Any]
    many:   bool = False
    confirm_token: str = ""


@router.post("/{coll}/delete")
async def delete(coll: str, body: DeleteBody, request: Request,
                    auth=Depends(require_admin_token)):
    if not collection_writable(coll):
        raise HTTPException(403, f"collection '{coll}' is not deletable")
    if not body.filter:
        raise HTTPException(400, "refusing to delete with empty filter")
    if body.many and body.confirm_token != "I_UNDERSTAND_BULK_DELETE":
        raise HTTPException(400,
            "bulk delete requires confirm_token='I_UNDERSTAND_BULK_DELETE'")
    _validate_filter(body.filter)
    db = _get_db()
    if body.many:
        r = await db[coll].delete_many(body.filter)
    else:
        r = await db[coll].delete_one(body.filter)
    await audit_log(request, action="delete",
                      params={"coll": coll, "many": body.many,
                                "filter": body.filter},
                      response_summary={"deleted": r.deleted_count}, **auth)
    return {"ok": True, "deleted": r.deleted_count}


# ── Indexes (informational only — never destructive) ──────────────────────
@router.get("/{coll}/indexes")
async def list_indexes(coll: str, request: Request,
                          auth=Depends(require_admin_token)):
    if not collection_readable(coll):
        raise HTTPException(403, f"collection '{coll}' not readable")
    db = _get_db()
    info = await db[coll].index_information()
    await audit_log(request, action="list_indexes",
                      params={"coll": coll}, **auth)
    return {"ok": True, "indexes": info}
