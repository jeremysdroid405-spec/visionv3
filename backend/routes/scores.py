"""
Scoring recompute + query API routes
====================================
Recompute rebuilds {sport}_prop_scores from live props without triggering odds syncs.
Query reads directly from {sport}_prop_scores (read-only, filtered) for QA.

Endpoints:
  POST /api/scores/recompute
  POST /api/scores/recompute/{sport}
  GET  /api/scores/supported-sports
  GET  /api/scores/{sport}
"""
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Body, Query
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


# =============================================================================
# Query endpoint — read-only QA inspection of {sport}_prop_scores
# =============================================================================

_VALID_SORTS = {
    "vision_score", "vision_score_raw", "pp_utility", "tier",
    "edge_vs_fair", "fair_prob", "computed_at", "player_name", "stat_type",
}


def _summarize(docs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summary counts by tier + pp_utility_category."""
    tiers: Dict[str, int] = {}
    categories: Dict[str, int] = {}
    quality_sources: Dict[str, int] = {}
    vision_null = 0
    for d in docs:
        t = d.get("tier") or "unknown"
        tiers[t] = tiers.get(t, 0) + 1
        c = d.get("pp_utility_category") or "unknown"
        categories[c] = categories.get(c, 0) + 1
        q = d.get("quality_source") or "unknown"
        quality_sources[q] = quality_sources.get(q, 0) + 1
        if d.get("vision_score") is None:
            vision_null += 1
    return {
        "by_tier": tiers,
        "by_pp_utility_category": categories,
        "by_quality_source": quality_sources,
        "vision_score_null": vision_null,
    }


@router.get("/{sport}")
async def query_scores(
    sport: str,
    version_tag: Optional[str] = Query(
        default=None, description="If omitted, uses the latest version for the sport."
    ),
    min_vision: Optional[float] = Query(default=None, ge=0, le=100),
    max_vision: Optional[float] = Query(default=None, ge=0, le=100),
    tier: Optional[str] = Query(default=None),
    pp_utility_category: Optional[str] = Query(default=None),
    quality_source: Optional[str] = Query(default=None),
    player_name: Optional[str] = Query(default=None),
    stat_type: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(default="vision_score"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
):
    """Read-only query against `{sport}_prop_scores` for QA inspection."""
    sport = (sport or "").lower()
    if sport not in SUPPORTED_SPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported sport '{sport}'. Supported: {list(SUPPORTED_SPORTS)}",
        )
    if sort_by not in _VALID_SORTS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid sort_by '{sort_by}'. Allowed: {sorted(_VALID_SORTS)}",
        )

    db = _get_db()
    coll = db[f"{sport}_prop_scores"]

    # Resolve latest version_tag if not provided
    if not version_tag:
        pipeline = [
            {"$group": {"_id": "$version_tag", "computed_at": {"$max": "$computed_at"}}},
            {"$sort": {"computed_at": -1}},
            {"$limit": 1},
        ]
        cursor = coll.aggregate(pipeline)
        async for doc in cursor:
            version_tag = doc["_id"]
            break
        if not version_tag:
            return {
                "sport": sport, "version_tag": None,
                "filters_applied": {}, "total_matching": 0,
                "returned": 0, "summary": _summarize([]), "results": [],
            }

    # Build filter
    q: Dict[str, Any] = {"version_tag": version_tag}
    if min_vision is not None or max_vision is not None:
        vrange: Dict[str, Any] = {}
        if min_vision is not None: vrange["$gte"] = min_vision
        if max_vision is not None: vrange["$lte"] = max_vision
        q["vision_score"] = vrange
    if tier:
        q["tier"] = tier
    if pp_utility_category:
        q["pp_utility_category"] = pp_utility_category
    if quality_source:
        q["quality_source"] = quality_source
    if player_name:
        q["player_name"] = {"$regex": player_name, "$options": "i"}
    if stat_type:
        q["stat_type"] = stat_type

    # Totals + summary computed over ALL matching docs
    total_matching = await coll.count_documents(q)

    # Summary: aggregate over full filter (cap at 20k for safety)
    summary_cursor = coll.find(q, {
        "_id": 0, "tier": 1, "pp_utility_category": 1,
        "quality_source": 1, "vision_score": 1,
    }).limit(20000)
    summary_docs = await summary_cursor.to_list(length=20000)
    summary = _summarize(summary_docs)

    # Paged result set — lean projection
    sort_spec = [(sort_by, 1 if sort_dir == "asc" else -1)]
    cursor = (
        coll.find(q, {"_id": 0})
        .sort(sort_spec)
        .skip(offset)
        .limit(limit)
    )
    results = await cursor.to_list(length=limit)

    return {
        "sport": sport,
        "version_tag": version_tag,
        "filters_applied": {
            "min_vision": min_vision, "max_vision": max_vision,
            "tier": tier, "pp_utility_category": pp_utility_category,
            "quality_source": quality_source,
            "player_name": player_name, "stat_type": stat_type,
            "sort_by": sort_by, "sort_dir": sort_dir,
            "limit": limit, "offset": offset,
        },
        "total_matching": total_matching,
        "returned": len(results),
        "summary": summary,
        "results": results,
    }
