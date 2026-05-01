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


class RunNowRequest(BaseModel):
    """Body for POST /run-now (auto-source projection_ids)."""
    sport: Optional[str] = "NBA"
    league_id: Optional[str] = None
    state_code: Optional[str] = None
    game_mode: Optional[str] = "power"
    leg_count: int = Field(default=2, ge=2, le=6)
    batch_size: int = Field(default=lab.DEFAULT_BATCH_SIZE,
                            ge=1, le=lab.RUN_NOW_HARD_CAP)
    min_delay: float = Field(default=lab.DEFAULT_MIN_DELAY, ge=1.0, le=120.0)
    max_delay: float = Field(default=lab.DEFAULT_MAX_DELAY, ge=1.0, le=120.0)
    dry_run: bool = Field(default=False)
    max_candidates: int = Field(default=25, ge=2, le=100)


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


@router.post("/run-now")
async def run_now(
    body: RunNowRequest,
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
) -> Dict[str, Any]:
    """Auto-source projection IDs and run a small batch.

    Operator does NOT need to provide `projection_ids`. The endpoint:
      1. Reads cached PP projection IDs from `pp_projection_id_cache`
         (TTL 15 min) when available.
      2. Otherwise issues ONE read-only `GET /projections?league_id=…`
         to PrizePicks and caches the IDs.
      3. Builds 2-leg candidate lineups and runs the existing
         `run_batch` pipeline (same safety guarantees).

    Hard-capped at `RUN_NOW_HARD_CAP` (25). All other PP Lab
    safety properties apply unchanged: no entries, no auth, no
    PerimeterX/px-cloud, hard-stop on 401/403/429, randomized
    8-15 s delays, admin-token gated.
    """
    _require_admin_token(x_admin_token)
    return await lab.run_now(
        sport=body.sport,
        league_id=body.league_id,
        state_code=body.state_code,
        game_mode=body.game_mode,
        leg_count=body.leg_count,
        batch_size=body.batch_size,
        min_delay=body.min_delay,
        max_delay=body.max_delay,
        dry_run=body.dry_run,
        max_candidates=body.max_candidates,
    )
