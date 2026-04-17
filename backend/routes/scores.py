"""
Scoring recompute API routes
============================
Rebuild {sport}_prop_scores from live props without triggering odds syncs.

Endpoints:
  POST /api/scores/recompute
  POST /api/scores/recompute/{sport}
"""
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Body
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field

from services.scoring.adapters import SUPPORTED_SPORTS
from services.scoring.recompute import recompute

router = APIRouter(prefix="/api/scores", tags=["scoring"])


class RecomputeRequest(BaseModel):
    sports: Optional[List[str]] = None
    version_tag: Optional[str] = None
    dry_run: bool = False
    limit: Optional[int] = None
    override_config: Optional[Dict[str, Any]] = Field(default=None)


_client: Optional[AsyncIOMotorClient] = None


def _get_db():
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return _client[os.environ["DB_NAME"]]


def _format_system_response(result: Dict[str, Any]) -> Dict[str, Any]:
    """Pass-through format with top-level system summary."""
    return {
        "status": result.get("status", "success"),
        "sports_processed": result.get("sports_processed", []),
        "processed": result.get("processed", {}),
        "written": result.get("written", {}),
        "skipped": result.get("skipped", {}),
        "version_tag": result.get("version_tag"),
        "duration_ms": result.get("duration_ms", 0),
        "dry_run": result.get("dry_run", False),
        "samples": result.get("samples", {}),
        "per_sport": result.get("per_sport", {}),
    }


def _format_single_sport_response(
    result: Dict[str, Any], sport: str
) -> Dict[str, Any]:
    """Single-sport endpoint returns just that sport's section."""
    ps = (result.get("per_sport") or {}).get(sport, {})
    return {
        "status": "success" if "error" not in ps else "error",
        "sport": sport,
        "processed": ps.get("processed", 0),
        "written": ps.get("written", 0),
        "skipped": ps.get("skipped", 0),
        "replaced": ps.get("replaced", 0),
        "version_tag": result.get("version_tag"),
        "duration_ms": result.get("duration_ms", 0),
        "dry_run": result.get("dry_run", False),
        "collection": ps.get("collection"),
        "cached_board_mutated": ps.get("cached_board_mutated"),
        "cached_board_leakage_fields": ps.get("cached_board_leakage_fields", []),
        "samples": ps.get("samples", []),
        "error": ps.get("error"),
    }


@router.post("/recompute")
async def recompute_all(req: RecomputeRequest = Body(default=None)):
    """System-level recompute. Defaults to all supported sports when
    no `sports` list is provided."""
    req = req or RecomputeRequest()
    db = _get_db()
    try:
        result = await recompute(
            db=db,
            sports=req.sports or list(SUPPORTED_SPORTS),
            version_tag=req.version_tag,
            dry_run=bool(req.dry_run),
            limit=req.limit,
            override_config=req.override_config,
        )
        return _format_system_response(result)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.post("/recompute/{sport}")
async def recompute_one(
    sport: str, req: RecomputeRequest = Body(default=None)
):
    """Sport-level recompute. Ignores request `sports` array."""
    sport = (sport or "").lower()
    if sport not in SUPPORTED_SPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported sport '{sport}'. Supported: {list(SUPPORTED_SPORTS)}",
        )
    req = req or RecomputeRequest()
    db = _get_db()
    try:
        result = await recompute(
            db=db,
            sports=[sport],
            version_tag=req.version_tag,
            dry_run=bool(req.dry_run),
            limit=req.limit,
            override_config=req.override_config,
        )
        return _format_single_sport_response(result, sport)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.get("/supported-sports")
async def supported_sports():
    return {"supported_sports": list(SUPPORTED_SPORTS)}
