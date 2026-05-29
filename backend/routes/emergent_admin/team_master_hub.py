"""
Team Master Hub admin endpoints (Phase 1.A.1).

Two endpoints — both token-gated, both preview-safe:

    POST /api/emergent-admin/team-master-hub/seed
        body: { "dry_run": bool = false }
        → runs (or simulates) the deterministic seeder against
          MongoDB and returns matched / modified / upserted counts
          plus the post-seed audit payload.

    GET  /api/emergent-admin/team-master-hub/audit
        → returns the read-only coverage report:
          total, by_sport, missing_sgo, duplicates, inactive,
          indexes_present.

Hard constraints (per Phase 1.A.1 brief):
    - PREVIEW POD ONLY. No SGO API calls, no production touch.
    - Uses the shared `services.team_master_hub` package as the
      single source of truth — the CLI script calls the same
      `seed_and_audit` / `audit_team_master_hub` functions.
    - Every action audit-logged.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from services.team_master_hub import (
    audit_team_master_hub,
    seed_and_audit,
)

from .auth import _get_db, audit_log, require_admin_token

logger = logging.getLogger("emergent_admin.team_master_hub")
router = APIRouter()


class SeedBody(BaseModel):
    """Seed-endpoint request body. `dry_run=True` skips index +
    bulk_write and returns the audit of the current collection
    state alongside a `seed_preview` block describing what a real
    run would do.
    """
    dry_run: bool = Field(default=False)


@router.post("/seed")
async def seed_endpoint(
    body: SeedBody,
    request: Request,
    auth: Dict[str, Any] = Depends(require_admin_token),
) -> Dict[str, Any]:
    db = _get_db()
    try:
        result = await seed_and_audit(db, dry_run=body.dry_run)
    except FileNotFoundError as exc:
        raise HTTPException(500, f"seed file missing: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("[team_master_hub.seed] failed")
        raise HTTPException(500, f"seed failed: {exc}") from exc

    # Audit summary — compact, never leaks the full team_id list
    seed_block = result.get("seed") or {}
    audit_block = result.get("audit") or {}
    await audit_log(
        request,
        action="team_master_hub_seed",
        params={"dry_run": body.dry_run},
        response_summary={
            "dry_run":     body.dry_run,
            "matched":     seed_block.get("matched"),
            "modified":    seed_block.get("modified"),
            "upserted":    seed_block.get("upserted"),
            "total_after": audit_block.get("total"),
        },
        **auth,
    )
    return result


@router.get("/audit")
async def audit_endpoint(
    request: Request,
    auth: Dict[str, Any] = Depends(require_admin_token),
) -> Dict[str, Any]:
    db = _get_db()
    try:
        report = await audit_team_master_hub(db)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[team_master_hub.audit] failed")
        raise HTTPException(500, f"audit failed: {exc}") from exc

    await audit_log(
        request,
        action="team_master_hub_audit",
        params={},
        response_summary={
            "total":             report["total"],
            "by_sport":          report["by_sport"],
            "missing_sgo_count": report["missing_sgo_count"],
            "duplicates_count":  report["duplicates_count"],
            "inactive_count":    report["inactive_count"],
            "indexes_present":   report["indexes_present"],
        },
        **auth,
    )
    return report
