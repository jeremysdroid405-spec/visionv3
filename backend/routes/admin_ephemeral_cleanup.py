"""Admin routes for the system-wide ephemeral data cleanup utility.

All endpoints sit behind the existing ADMIN_DEBUG_TOKEN header check
(`X-Admin-Token`). Default to dry-run.

Endpoints
─────────
GET  /api/v3/admin/ephemeral-cleanup/status
POST /api/v3/admin/ephemeral-cleanup/run?sport=<sport>&dry_run=<bool>&force=<bool>
POST /api/v3/admin/ephemeral-cleanup/ensure-indexes?sport=<sport>
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query

from services.cleanup import ephemeral_cleanup

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v3/admin/ephemeral-cleanup",
    tags=["Admin / Ephemeral Cleanup"],
)

# DB is wired up at app startup via ``set_db()`` (mirrors
# admin_diagnostics.py pattern).
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


# ──────────────────────────────────────────────────────────────────────
# GET /status — read-only inventory
# ──────────────────────────────────────────────────────────────────────
@router.get("/status")
async def status(
    sport: Optional[str] = Query(None),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    _require_admin_token(x_admin_token)
    if _db is None:
        raise HTTPException(status_code=500, detail="DB not wired")
    return await ephemeral_cleanup.status_report(_db, sport=sport)


# ──────────────────────────────────────────────────────────────────────
# POST /run — dry-run by default
# ──────────────────────────────────────────────────────────────────────
@router.post("/run")
async def run(
    sport: Optional[str] = Query(None,
        description="Single sport (mlb/nba). Omit to run all enabled."),
    dry_run: bool = Query(True,
        description="When True (default) no documents are modified."),
    force: bool = Query(False,
        description="Override the 'live_props empty' safety abort. "
                    "Use ONLY when knowingly cleaning post-season."),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    _require_admin_token(x_admin_token)
    if _db is None:
        raise HTTPException(status_code=500, detail="DB not wired")
    logger.info(
        "[EPHEMERAL_CLEANUP_API] run sport=%s dry_run=%s force=%s",
        sport, dry_run, force,
    )
    return await ephemeral_cleanup.run_ephemeral_cleanup(
        _db, sport=sport, dry_run=dry_run, force=force,
    )


# ──────────────────────────────────────────────────────────────────────
# POST /ensure-indexes — idempotent
# ──────────────────────────────────────────────────────────────────────
@router.post("/ensure-indexes")
async def ensure_indexes(
    sport: Optional[str] = Query(None),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    _require_admin_token(x_admin_token)
    if _db is None:
        raise HTTPException(status_code=500, detail="DB not wired")
    return await ephemeral_cleanup.ensure_ttl_indexes(_db, sport=sport)
