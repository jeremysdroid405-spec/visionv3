"""
Forward-Testing Routes
======================
API endpoints for the Forward-Testing Infrastructure.

Endpoints:
- POST /api/v3/forward-test/capture - Capture daily prop snapshots
- POST /api/v3/forward-test/resolve - Resolve outcomes
- GET /api/v3/forward-test/performance - Get performance summary
- GET /api/v3/forward-test/daily - Get daily breakdown
- GET /api/v3/forward-test/calibration - Get calibration report
- GET /api/v3/forward-test/status - Get system status
"""

from fastapi import APIRouter, HTTPException, Response, Query
from typing import Optional
from datetime import datetime, timezone
import logging

from services.forward_testing_service import get_forward_testing_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Forward Testing"])

# Database reference
_db = None


def set_forward_test_db(db):
    """Set the database reference."""
    global _db
    _db = db


def get_service():
    """Get the forward-testing service instance."""
    if _db is None:
        raise HTTPException(status_code=500, detail="Forward-testing service not initialized")
    return get_forward_testing_service(_db)


@router.post("/v3/forward-test/capture")
async def capture_props(
    sport: Optional[str] = Query(None, description="Sport to capture (nba/mlb). If not provided, captures all."),
    reason: str = Query("manual", description="Capture reason (manual/scheduled/pre_game)"),
    phase: str = Query(
        "manual",
        description=(
            "capture_phase tag (manual / morning / afternoon / lineup_window / pre_lock). "
            "Required to be unique-per-day so multiple snapshots/day do not overwrite each other."
        ),
    )
):
    """
    Capture current tier props for forward-testing.
    
    This snapshots all props from Safe Haven, Front Lines, and War Zone
    for later outcome tracking and performance analysis.
    """
    service = get_service()
    
    if sport:
        result = await service.capture_daily_snapshot(sport, reason, phase)
    else:
        result = await service.capture_all_sports(reason, phase)
    
    return result


@router.post("/v3/forward-test/resolve")
async def resolve_outcomes(
    sport: str = Query(..., description="Sport to resolve (nba/mlb)"),
    date: Optional[str] = Query(None, description="Date to resolve (YYYY-MM-DD). Defaults to yesterday.")
):
    """
    Resolve outcomes for captured props.
    
    Matches captured props against actual game results to determine
    hit/miss outcomes for performance tracking.
    """
    service = get_service()
    
    result = await service.resolve_outcomes(sport, date)
    
    return result


@router.get("/v3/forward-test/performance")
async def get_performance(
    response: Response,
    sport: Optional[str] = Query(None, description="Filter by sport"),
    days: int = Query(30, description="Number of days to analyze"),
    tier: Optional[str] = Query(None, description="Filter by tier (safe_haven/front_lines/war_zone)")
):
    """
    Get aggregated performance summary.
    
    Returns hit rates by sport and tier over the specified period.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    
    result = await service.get_performance_summary(sport, days, tier)
    
    return result


@router.get("/v3/forward-test/daily")
async def get_daily_breakdown(
    response: Response,
    sport: str = Query(..., description="Sport (nba/mlb)"),
    days: int = Query(14, description="Number of days")
):
    """
    Get day-by-day performance breakdown.
    
    Returns daily hit rates for tracking trends over time.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    
    result = await service.get_daily_breakdown(sport, days)
    
    return {
        "sport": sport,
        "days": days,
        "data": result
    }


@router.get("/v3/forward-test/calibration")
async def get_calibration(
    response: Response,
    sport: Optional[str] = Query(None, description="Filter by sport")
):
    """
    Get model calibration report.
    
    Compares predicted hit rates vs actual hit rates across probability
    buckets to detect systematic over/under-prediction.
    
    A well-calibrated model should show:
    - 70% predicted → ~70% actual
    - 80% predicted → ~80% actual
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    
    result = await service.get_calibration_report(sport)
    
    return result


@router.get("/v3/forward-test/status")
async def get_status(response: Response):
    """
    Get forward-testing system status.
    
    Returns snapshot counts, date ranges, and unresolved prop counts.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    
    result = await service.get_snapshot_status()
    
    return result


@router.get("/v3/forward-test/capture-summary")
async def get_capture_summary(
    response: Response,
    days: int = Query(14, ge=1, le=180, description="Number of days back to include"),
    sport: Optional[str] = Query(None, description="Optional sport filter (nba/mlb)"),
):
    """
    Lightweight capture-coverage report for the multi-phase capture rollout.

    Returns counts grouped by `(capture_date, sport, capture_phase, tier)`
    so we can see at a glance which phases have actually fired on which
    days. Reads `forward_test_snapshots` directly via aggregation;
    no heavy joins, no enrichment.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    if _db is None:
        raise HTTPException(status_code=500, detail="forward-testing DB not initialized")

    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    match: dict = {"capture_date": {"$gte": cutoff}}
    if sport:
        match["sport"] = sport.lower()

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": {
                "date": "$capture_date",
                "sport": "$sport",
                "phase": {"$ifNull": ["$capture_phase", "legacy_single_capture"]},
                "tier": "$tier",
            },
            "n": {"$sum": 1},
        }},
        {"$sort": {"_id.date": -1, "_id.sport": 1, "_id.phase": 1, "_id.tier": 1}},
    ]
    rows = await _db["forward_test_snapshots"].aggregate(pipeline).to_list(length=None)
    return {
        "since_date": cutoff,
        "filters": {"sport": sport, "days": days},
        "groups": [
            {
                "date": r["_id"]["date"],
                "sport": r["_id"]["sport"],
                "capture_phase": r["_id"]["phase"],
                "tier": r["_id"]["tier"],
                "count": r["n"],
            }
            for r in rows
        ],
    }


@router.delete("/v3/forward-test/clear")
async def clear_old_data(
    days_to_keep: int = Query(90, description="Number of days of data to keep")
):
    """
    Clear old forward-testing data.
    
    Removes snapshots and outcomes older than the specified retention period.
    """
    service = get_service()
    
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_to_keep)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    
    # Delete old data
    snapshots_deleted = await service.snapshots.delete_many({"capture_date": {"$lt": cutoff_str}})
    outcomes_deleted = await service.outcomes.delete_many({"capture_date": {"$lt": cutoff_str}})
    metrics_deleted = await service.metrics.delete_many({"date": {"$lt": cutoff_str}})
    
    return {
        "cutoff_date": cutoff_str,
        "deleted": {
            "snapshots": snapshots_deleted.deleted_count,
            "outcomes": outcomes_deleted.deleted_count,
            "metrics": metrics_deleted.deleted_count
        }
    }
