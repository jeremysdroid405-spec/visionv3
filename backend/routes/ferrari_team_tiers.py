"""
Team Prop Tier Routes
=====================
Sibling endpoints to `/api/v3/ferrari/{safe-haven,front-lines,war-zone}`
that return the SAME envelope shape but with `prop_type="team"` rows
sourced from `team_live_props` (with `team_historical_props` fallback).

Phase 1: ODDS-ROUTED ONLY. No model. Rows are flagged
`odds_routed=true` and `team_model_pending=true` so the UI renders the
"team_model_pending" affordance.

Routes added:
    GET /api/v3/ferrari/team/safe-haven
    GET /api/v3/ferrari/team/front-lines
    GET /api/v3/ferrari/team/war-zone

All accept `?sport=mlb|nba|nfl` and `?limit=<int>` like their player
counterparts. `sort` is accepted for FE parity (the team service
sorts by tier-appropriate odds; the param is currently a no-op).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response

from services.team_prop_tier_service import get_team_prop_picks

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Ferrari Tiers — Team"])

# Engine reference — wired by main.py:wire_routers(), exactly like the
# player ferrari_tiers module.
_db = None


def init_router(db) -> None:
    """Wire the DB handle exactly like ferrari_tiers does."""
    global _db
    _db = db


# 2026-06-02 — NCAAF removed; not built yet. See team_live_sync.py
# for the rationale. Re-add when NCAAF team-prop pipeline ships.
_SUPPORTED_SPORTS = ("mlb", "nba", "nfl")


def _validate_sport(s: str) -> str:
    s = (s or "").lower()
    if s not in _SUPPORTED_SPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"sport={s!r} not supported (mlb|nba|nfl)",
        )
    return s


@router.get("/v3/ferrari/team/safe-haven")
async def get_team_safe_haven(
    response: Response,
    limit: int = Query(10, ge=1, le=50),
    sport: str = Query("mlb", description="Sport (mlb|nba|nfl)"),
    sort: Optional[str] = Query(None,
                                  description="(accepted for parity, "
                                              "no-op in odds-routed phase)"),
):
    response.headers["Cache-Control"] = \
        "no-cache, no-store, must-revalidate"
    if _db is None:
        raise HTTPException(status_code=500,
                              detail="Database not initialized")
    sport = _validate_sport(sport)
    return await get_team_prop_picks(
        _db, sport=sport, tier_name="safe_haven",
        limit=limit, sort=sort)


@router.get("/v3/ferrari/team/front-lines")
async def get_team_front_lines(
    response: Response,
    limit: int = Query(10, ge=1, le=50),
    sport: str = Query("mlb", description="Sport (mlb|nba|nfl)"),
    sort: Optional[str] = Query(None),
):
    response.headers["Cache-Control"] = \
        "no-cache, no-store, must-revalidate"
    if _db is None:
        raise HTTPException(status_code=500,
                              detail="Database not initialized")
    sport = _validate_sport(sport)
    return await get_team_prop_picks(
        _db, sport=sport, tier_name="front_lines",
        limit=limit, sort=sort)


@router.get("/v3/ferrari/team/war-zone")
async def get_team_war_zone(
    response: Response,
    limit: int = Query(10, ge=1, le=50),
    sport: str = Query("mlb", description="Sport (mlb|nba|nfl)"),
    sort: Optional[str] = Query(None),
):
    response.headers["Cache-Control"] = \
        "no-cache, no-store, must-revalidate"
    if _db is None:
        raise HTTPException(status_code=500,
                              detail="Database not initialized")
    sport = _validate_sport(sport)
    return await get_team_prop_picks(
        _db, sport=sport, tier_name="war_zone",
        limit=limit, sort=sort)
