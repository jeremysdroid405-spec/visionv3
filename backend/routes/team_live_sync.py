"""
Team Live Sync routes.

POST /api/v3/team-live-sync/{sport}
    Pulls live h2h/spreads/totals/team_totals from The Odds API,
    writes to team_live_props, passthroughs to team_prop_scores.

GET /api/v3/team-live-sync/runs
    Lists the most recent team_odds_ingest_runs entries.

Live providers only. No SGO. No player-prop touchpoints.
"""
from __future__ import annotations
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from services.team_live_sync_service import sync_team_live_for_sport

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Team Live Sync"])

_db = None


def init_router(db) -> None:
    global _db
    _db = db


_SUPPORTED_SPORTS = ("mlb", "nba", "nfl")


def _validate_sport(s: str) -> str:
    s = (s or "").lower()
    if s not in _SUPPORTED_SPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"sport={s!r} not supported (mlb|nba|nfl)")
    return s


@router.post("/v3/team-live-sync/{sport}")
async def trigger_team_live_sync(
    sport: str,
    max_events: Optional[int] = Query(
        None, description="Optional cap on events to fetch."),
    do_passthrough: bool = Query(
        True, description="Run team_live_props → team_prop_scores "
                          "passthrough after writes."),
):
    if _db is None:
        raise HTTPException(status_code=500,
                              detail="Database not initialized")
    sport = _validate_sport(sport)
    audit = await sync_team_live_for_sport(
        _db, sport=sport,
        max_events=max_events,
        do_passthrough=do_passthrough,
    )
    return audit


@router.get("/v3/team-live-sync/runs")
async def list_team_live_runs(limit: int = Query(10, ge=1, le=100)):
    if _db is None:
        raise HTTPException(status_code=500,
                              detail="Database not initialized")
    cur = _db["team_odds_ingest_runs"].find(
        {"kind": "team_live_sync"},
        projection={"_id": 0}).sort(
        "started_at", -1).limit(limit)
    return {"runs": [r async for r in cur]}
