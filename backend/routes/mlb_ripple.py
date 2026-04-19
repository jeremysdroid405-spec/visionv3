"""
MLB Lineup Ripple API Routes
==============================
REST endpoints for MLB Lineup Ripple Engine.

Endpoints:
- GET /api/v3/mlb/ripple/alerts - Get lineup ripple alerts for UI
- POST /api/v3/mlb/ripple/check - Trigger lineup ripple check
- GET /api/v3/mlb/ripple/top-gainers - Get Top 3 PA gainers
- GET /api/v3/mlb/ripple/player/{player_name} - Check ripple for specific player
- POST /api/v3/mlb/ripple/sync-anchors - Sync Lineup Anchor profiles

Author: PropVision AI
Version: 1.0.0
"""
from fastapi import APIRouter, Response
from datetime import datetime, timezone
import logging

from services.mlb_lineup_ripple_service import get_mlb_ripple_service

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)
router = APIRouter(tags=["mlb-ripple"])

# Database reference
_db = None


def set_mlb_ripple_db(db):
    """Set the database reference for the ripple service."""
    global _db
    _db = db


def get_service():
    """Get the ripple service with DB."""
    return get_mlb_ripple_service(_db)


@router.get("/v3/mlb/ripple/alerts")
async def get_mlb_ripple_alerts(response: Response, refresh: bool = False):
    """
    Get MLB lineup ripple alerts for frontend display.
    
    Shows players benefiting from Lineup Anchors being OUT:
    - PA Bump: +10% expected PAs for lineup movers
    - Protection Penalty: -5% for adjacent hitters
    
    Returns:
        List of formatted alerts with PA bump percentages.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    return await service.get_ripple_alerts(refresh=refresh)


@router.post("/v3/mlb/ripple/check")
async def check_mlb_lineups(response: Response):
    """
    Trigger MLB lineup ripple check.
    
    Process:
    1. Identifies missing Lineup Anchors (OPS > .850 or wRC+ > 125)
    2. Calculates PA bumps for lineup movers (+10%)
    3. Applies protection penalties to adjacent hitters (-5%)
    
    Returns:
        Dict with triggered ripples and top PA gainers.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    return await service.check_lineup_changes()


@router.get("/v3/mlb/ripple/top-gainers")
async def get_top_pa_gainers(response: Response, refresh: bool = False):
    """
    Get Top 3 players who gained the most Expected PAs due to teammate sitting.
    
    Returns:
        List of top 3 PA gainers with their boost percentages.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    
    if refresh:
        result = await service.check_lineup_changes()
        top_gainers = result.get("top_3_pa_gainers", [])
    else:
        alerts = await service.get_ripple_alerts()
        top_gainers = alerts.get("top_3_pa_gainers", [])
    
    return {
        "has_gainers": len(top_gainers) > 0,
        "count": len(top_gainers),
        "top_3_pa_gainers": top_gainers,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.get("/v3/mlb/ripple/player/{player_name}")
async def get_player_ripple(player_name: str, response: Response):
    """
    Check if a specific player is affected by any lineup ripple.
    
    Args:
        player_name: The player to check
        
    Returns:
        Ripple data if player is affected, or null.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    ripple_data = service.get_ripple_for_player(player_name)
    
    return {
        "player_name": player_name,
        "has_ripple_effect": ripple_data is not None,
        "ripple_data": ripple_data,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.post("/v3/mlb/ripple/sync-anchors")
async def sync_lineup_anchors(response: Response):
    """
    Sync Lineup Anchor profiles from database.
    
    Identifies all players with OPS > .850 or wRC+ > 125.
    
    Returns:
        Sync status with anchor count.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    service = get_service()
    return await service.sync_anchor_profiles()


@router.get("/v3/mlb/ripple/anchors")
async def get_lineup_anchors(response: Response, team: str = None):
    """
    Get all Lineup Anchors (optionally filtered by team).
    
    Lineup Anchor = OPS > .850 OR wRC+ > 125
    
    Returns:
        List of Lineup Anchors with their OPS/wRC+.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    
    from pymongo import MongoClient
    import os
    
    try:
        sync_client = MongoClient(os.environ.get('MONGO_URL'))
        sync_db = sync_client['pick_vision']
        
        query = {'advanced_stats.season_stats.2026.batting.ops': {'$gt': 0.850}}
        if team:
            query['team_abbr'] = team
        
        anchors = list(sync_db[COLL("master_hub", "mlb")].find(
            query,
            {'_id': 0, 'display_name': 1, 'team_abbr': 1, 'primary_position': 1,
             'advanced_stats.season_stats.2026.batting': 1}
        ).sort('advanced_stats.season_stats.2026.batting.ops', -1).limit(50))
        
        sync_client.close()
        
        result = []
        for anchor in anchors:
            batting = anchor.get('advanced_stats', {}).get('season_stats', {}).get('2026', {}).get('batting', {})
            ops = batting.get('ops', 0) or 0
            
            result.append({
                "name": anchor.get('display_name'),
                "team": anchor.get('team_abbr'),
                "position": anchor.get('primary_position'),
                "ops": round(ops, 3),
                "avg": batting.get('avg', 0),
                "war": batting.get('war', 0)
            })
        
        return {
            "count": len(result),
            "anchors": result,
            "threshold": {"ops": 0.850, "wrc_plus": 125},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        logger.error(f"[RippleRoutes] Error fetching anchors: {e}")
        return {"error": str(e), "count": 0, "anchors": []}
