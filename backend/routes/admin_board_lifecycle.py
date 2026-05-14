"""Admin routes for the universal cached board lifecycle contract.

GET  /api/v3/admin/board-lifecycle/status
POST /api/v3/admin/board-lifecycle/normalize?dry_run=true

Behind ``X-Admin-Token`` (env: ``ADMIN_DEBUG_TOKEN``).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query

from services.boards.board_lifecycle import (
    LIFECYCLE_FIELDS,
    normalize_board_doc,
)
from services.cleanup.ephemeral_collections import (
    EPHEMERAL_CLEANUP_CONFIG,
    iter_collections,
)

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v3/admin/board-lifecycle",
    tags=["Admin / Board Lifecycle"],
)

_db = None


def set_db(db) -> None:
    global _db
    _db = db


def _require_admin_token(provided: Optional[str]) -> None:
    expected = os.environ.get("ADMIN_DEBUG_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_DEBUG_TOKEN not configured; "
                   "admin endpoint disabled",
        )
    if not provided or provided != expected:
        raise HTTPException(status_code=401, detail="invalid admin token")


def _board_collections() -> List[str]:
    """Every cached_board-style collection across all configured
    sports. (Filters out *_prop_scores — those are handled by the
    ephemeral cleanup utility, not board lifecycle.)"""
    out: List[str] = []
    for sport, block in EPHEMERAL_CLEANUP_CONFIG.items():
        if not block.get("enabled"):
            continue
        for entry in iter_collections(sport):
            if "cached_board" in entry["name"]:
                out.append(entry["name"])
    return out


# ──────────────────────────────────────────────────────────────────────
# GET /status
# ──────────────────────────────────────────────────────────────────────
@router.get("/status")
async def status(
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
) -> Dict[str, Any]:
    _require_admin_token(x_admin_token)
    if _db is None:
        raise HTTPException(status_code=500, detail="DB not wired")
    collections = _board_collections()
    out: Dict[str, Any] = {
        "collections_scanned": collections,
        "per_collection": {},
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
    for cname in collections:
        coll = _db[cname]
        total = await coll.estimated_document_count()
        n_active = await coll.count_documents({"active": True})
        n_inactive = await coll.count_documents({"active": False})
        n_missing_active = await coll.count_documents({
            "$or": [{"active": {"$exists": False}}, {"active": None}],
        })
        n_missing_ttl = await coll.count_documents({
            "ttl_purge_at": {"$exists": False},
        })
        n_missing_reason = await coll.count_documents({
            "stale_reason": {"$exists": False},
        })
        n_missing_marked = await coll.count_documents({
            "stale_marked_at": {"$exists": False},
        })
        n_pending_purge = await coll.count_documents({
            "ttl_purge_at": {"$ne": None, "$exists": True},
        })
        n_compliant = await coll.count_documents({
            "active": {"$exists": True},
            "ttl_purge_at": {"$exists": True},
            "stale_reason": {"$exists": True},
            "stale_marked_at": {"$exists": True},
        })
        out["per_collection"][cname] = {
            "total": total,
            "active": n_active,
            "inactive": n_inactive,
            "missing_active_field": n_missing_active,
            "missing_ttl_purge_at": n_missing_ttl,
            "missing_stale_reason": n_missing_reason,
            "missing_stale_marked_at": n_missing_marked,
            "pending_purge": n_pending_purge,
            "lifecycle_compliant": n_compliant,
            "compliance_pct": round(
                100.0 * n_compliant / max(total, 1), 2,
            ),
        }
    return out


# ──────────────────────────────────────────────────────────────────────
# POST /normalize
# ──────────────────────────────────────────────────────────────────────
@router.post("/normalize")
async def normalize(
    dry_run: bool = Query(True),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
) -> Dict[str, Any]:
    _require_admin_token(x_admin_token)
    if _db is None:
        raise HTTPException(status_code=500, detail="DB not wired")
    collections = _board_collections()
    summary: Dict[str, Any] = {
        "dry_run": dry_run,
        "collections": [],
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    for cname in collections:
        coll = _db[cname]
        total = await coll.estimated_document_count()
        # Find every doc missing at least one lifecycle field.
        missing_filter = {
            "$or": [
                {f: {"$exists": False}} for f in LIFECYCLE_FIELDS
            ],
        }
        scanned = 0
        normalized = 0
        sample_ids: List[str] = []
        cursor = coll.find(missing_filter, {"_id": 1, **{f: 1 for f in LIFECYCLE_FIELDS}})
        async for d in cursor:
            scanned += 1
            if len(sample_ids) < 5:
                sample_ids.append(str(d.get("_id")))
            if dry_run:
                normalized += 1
                continue
            # Read full doc, normalize, write back lifecycle fields.
            patch = {f: d.get(f) for f in LIFECYCLE_FIELDS if f in d}
            # Hydrate active state from what's present so
            # normalize_board_doc picks the right branch.
            scratch = {**patch}
            normalize_board_doc(scratch)
            # Push only the lifecycle fields.
            update = {f: scratch[f] for f in LIFECYCLE_FIELDS}
            await coll.update_one(
                {"_id": d["_id"]},
                {"$set": update},
            )
            normalized += 1
        already_compliant = total - scanned
        summary["collections"].append({
            "name": cname,
            "total": total,
            "missing_lifecycle_fields": scanned,
            "would_normalize" if dry_run else "normalized": normalized,
            "already_compliant": already_compliant,
            "sample_ids": sample_ids,
            "applied": (not dry_run) and normalized > 0,
        })
        logger.info(
            "[BOARD_LIFECYCLE:%s] normalize dry_run=%s "
            "scanned=%d normalized=%d total=%d",
            cname, dry_run, scanned, normalized, total,
        )
    return summary


# ──────────────────────────────────────────────────────────────────────
# Startup validation — called from server.py
# ──────────────────────────────────────────────────────────────────────
async def startup_validate(db) -> Dict[str, Any]:
    """Read-only audit at server startup. Logs warnings; never
    mutates."""
    collections = _board_collections()
    findings: List[Dict[str, Any]] = []
    for cname in collections:
        try:
            total = await db[cname].estimated_document_count()
            n_missing = await db[cname].count_documents({
                "active": {"$exists": False},
            })
            findings.append({
                "collection": cname,
                "total": total,
                "missing_active_field": n_missing,
                "compliant": n_missing == 0,
            })
            if n_missing:
                logger.warning(
                    "[BOARD_LIFECYCLE_VALIDATE] %s: %d/%d docs "
                    "missing `active` field — publisher bypass. "
                    "Run POST /api/v3/admin/board-lifecycle/normalize",
                    cname, n_missing, total,
                )
            else:
                logger.info(
                    "[BOARD_LIFECYCLE_VALIDATE] %s: 100%% compliant "
                    "(%d docs)", cname, total,
                )
        except Exception as exc:  # noqa: BLE001
            findings.append({"collection": cname, "error": repr(exc)})
            logger.warning(
                "[BOARD_LIFECYCLE_VALIDATE] %s: validation failed: %s",
                cname, exc,
            )
    return {"findings": findings}
