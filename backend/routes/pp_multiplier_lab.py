"""
Admin routes for the PrizePicks Multiplier Lab.

All endpoints sit behind the existing ADMIN_DEBUG_TOKEN header check
(`X-Admin-Token`). Not exposed to the public frontend.
"""
from __future__ import annotations

import os
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from services import pp_multiplier_lab as lab

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/admin/pp-multiplier-lab",
    tags=["Admin / PP Multiplier Lab"],
)


# ─── Auth gate (re-uses existing ADMIN_DEBUG_TOKEN env var) ─────────
def _require_admin_token(provided: Optional[str]) -> None:
    expected = os.environ.get("ADMIN_DEBUG_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_DEBUG_TOKEN not configured; admin endpoint disabled",
        )
    if not provided or provided != expected:
        raise HTTPException(status_code=401, detail="invalid admin token")


# ─── Request models ─────────────────────────────────────────────────
class RunBatchRequest(BaseModel):
    """Body for POST /run-batch."""
    sport: Optional[str] = None
    league_id: Optional[str] = None
    state_code: Optional[str] = None
    game_mode: Optional[str] = "power"
    leg_count: int = Field(default=2, ge=2, le=6)
    projection_ids: List[str] = Field(
        ..., description="PrizePicks projection IDs to combine. "
                         "Caller-supplied (no auto-pull from PP).",
        min_length=2,
    )
    batch_size: int = Field(default=lab.DEFAULT_BATCH_SIZE,
                            ge=1, le=lab.MAX_BATCH_SIZE)
    min_delay: float = Field(default=lab.DEFAULT_MIN_DELAY, ge=1.0, le=120.0)
    max_delay: float = Field(default=lab.DEFAULT_MAX_DELAY, ge=1.0, le=120.0)
    dry_run: bool = Field(
        default=True,
        description="When true, NO outbound HTTP. Persists "
                    "stub records for pipeline verification only.",
    )


# ─── Endpoints ──────────────────────────────────────────────────────
@router.get("/recent")
async def recent_tests(
    limit: int = 25,
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
) -> Dict[str, Any]:
    """Return the most-recent persisted tests."""
    _require_admin_token(x_admin_token)
    limit = max(1, min(int(limit), 200))
    return {"ok": True, "limit": limit, "tests": lab.get_recent_tests(limit)}


@router.get("/stats")
async def stats(
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
) -> Dict[str, Any]:
    """Aggregate stats for review."""
    _require_admin_token(x_admin_token)
    out = lab.get_stats()
    return {"ok": True, **out}


@router.post("/run-batch")
async def run_batch(
    body: RunBatchRequest,
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
) -> Dict[str, Any]:
    """Run a small batch (≤ 50). Defaults to dry_run=True.

    Live (read-only) mode bails immediately on any 401/403/429 and
    never touches PerimeterX / px-cloud / entries / auth endpoints.
    """
    _require_admin_token(x_admin_token)
    summary = await lab.run_batch(
        sport=body.sport,
        league_id=body.league_id,
        state_code=body.state_code,
        game_mode=body.game_mode,
        leg_count=body.leg_count,
        projection_ids=body.projection_ids,
        batch_size=body.batch_size,
        min_delay=body.min_delay,
        max_delay=body.max_delay,
        dry_run=body.dry_run,
    )
    return summary
