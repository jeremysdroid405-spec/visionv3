"""
BDL Advanced Stats Routes
==========================
Endpoints for fetching and managing V2 Advanced Stats from BallDontLie API.

Requires GOAT tier API key for access to:
- Usage Rate, True Shooting, eFG%
- Individual matchup data
- Tracking stats (speed, touches, distance)
- Hustle stats (deflections, contested shots)
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel
from typing import Optional, List
import os
import logging
from pymongo import MongoClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v3/bdl-advanced", tags=["BDL Advanced Stats"])

# MongoDB
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "pick_vision")
client = MongoClient(MONGO_URL)
db = client[DB_NAME]


# Import the fetcher
from services.bdl_advanced_stats_fetcher import BDLAdvancedStatsFetcher, fetch_all_advanced_stats

_fetcher: Optional[BDLAdvancedStatsFetcher] = None


def get_fetcher() -> BDLAdvancedStatsFetcher:
    global _fetcher
    if _fetcher is None:
        _fetcher = BDLAdvancedStatsFetcher(db)
    return _fetcher


class FetchSeasonsRequest(BaseModel):
    seasons: Optional[List[int]] = None


@router.get("/status")
async def advanced_stats_status():
    """Get status of stored advanced stats."""
    fetcher = get_fetcher()
    summary = fetcher.get_stats_summary()
    
    bdl_key = os.environ.get("BDL_API_KEY", "")
    key_status = "configured" if bdl_key else "missing"
    
    return {
        "success": True,
        "api_key_status": key_status,
        "collection": "bdl_advanced_stats",
        "summary": summary
    }


@router.post("/fetch-season/{season}")
async def fetch_season_stats(
    season: int,
    background_tasks: BackgroundTasks,
    player_ids: Optional[str] = Query(None, description="Comma-separated player IDs"),
    start_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
):
    """
    Fetch V2 Advanced Stats for a single season.
    
    Season format: 2024 for 2024-25 season
    """
    bdl_key = os.environ.get("BDL_API_KEY", "")
    if not bdl_key:
        raise HTTPException(status_code=500, detail="BDL_API_KEY not configured")
    
    fetcher = get_fetcher()
    
    # Parse player IDs if provided
    pids = None
    if player_ids:
        try:
            pids = [int(p.strip()) for p in player_ids.split(",")]
        except:
            raise HTTPException(status_code=400, detail="Invalid player_ids format")
    
    try:
        result = fetcher.fetch_advanced_stats_for_season(
            season=season,
            player_ids=pids,
            start_date=start_date,
            end_date=end_date
        )
        return {
            "success": True,
            "result": result
        }
    except Exception as e:
        logger.error(f"Failed to fetch season {season}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/fetch-multiple")
async def fetch_multiple_seasons(
    request: FetchSeasonsRequest,
    background_tasks: BackgroundTasks
):
    """
    Fetch V2 Advanced Stats for multiple seasons.
    
    Default: 2020-2025 (6 seasons for ML training)
    """
    bdl_key = os.environ.get("BDL_API_KEY", "")
    if not bdl_key:
        raise HTTPException(status_code=500, detail="BDL_API_KEY not configured")
    
    fetcher = get_fetcher()
    
    try:
        result = fetcher.fetch_multiple_seasons(request.seasons)
        return {
            "success": True,
            "result": result
        }
    except Exception as e:
        logger.error(f"Failed to fetch multiple seasons: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/player/{player_id}")
async def get_player_advanced_stats(
    player_id: int,
    seasons: Optional[str] = Query(None, description="Comma-separated seasons (e.g., 2024,2025)")
):
    """Get all advanced stats for a specific player."""
    fetcher = get_fetcher()
    
    season_list = None
    if seasons:
        try:
            season_list = [int(s.strip()) for s in seasons.split(",")]
        except:
            raise HTTPException(status_code=400, detail="Invalid seasons format")
    
    stats = fetcher.get_player_advanced_stats(player_id, season_list)
    
    if not stats:
        return {
            "success": True,
            "player_id": player_id,
            "stats": [],
            "message": "No advanced stats found. Try fetching data first."
        }
    
    return {
        "success": True,
        "player_id": player_id,
        "total_games": len(stats),
        "stats": stats[:50]  # Limit response size
    }


@router.get("/player-by-name/{player_name}")
async def get_player_stats_by_name(player_name: str):
    """Get advanced stats by player name (searches master hub first)."""
    # Find player in master hub
    hub = db['nba_master_hub_2026']
    player = hub.find_one({
        '$or': [
            {'player_name': {'$regex': player_name, '$options': 'i'}},
            {'display_name': {'$regex': player_name, '$options': 'i'}},
        ]
    })
    
    if not player:
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found")
    
    bdl_id = player.get('bdl_id')
    if not bdl_id:
        raise HTTPException(status_code=404, detail=f"No BDL ID for {player_name}")
    
    fetcher = get_fetcher()
    stats = fetcher.get_player_advanced_stats(bdl_id)
    
    return {
        "success": True,
        "player_name": player.get('display_name') or player.get('player_name'),
        "bdl_id": bdl_id,
        "team": player.get('team'),
        "total_games": len(stats),
        "stats": stats[:20]  # Last 20 games
    }


@router.get("/sample")
async def get_sample_stats():
    """Get a sample of stored advanced stats for inspection."""
    fetcher = get_fetcher()
    
    # Get 5 random samples
    samples = list(fetcher.advanced_stats.aggregate([
        {"$sample": {"size": 5}}
    ]))
    
    # Clean _id
    for s in samples:
        if '_id' in s:
            s['_id'] = str(s['_id'])
    
    return {
        "success": True,
        "samples": samples
    }


@router.delete("/clear")
async def clear_advanced_stats():
    """Clear all stored advanced stats (use with caution)."""
    fetcher = get_fetcher()
    
    result = fetcher.advanced_stats.delete_many({})
    
    return {
        "success": True,
        "deleted": result.deleted_count
    }


# =============================================================================
# FEATURE EXTRACTION FOR VEGAS KILLER
# =============================================================================

@router.get("/features/{player_name}/{stat_type}")
async def extract_features_for_prediction(
    player_name: str,
    stat_type: str,
    line: Optional[float] = Query(None),
    opponent_team: Optional[str] = Query(None)
):
    """
    Extract advanced features for Vegas Killer model prediction.
    
    Combines:
    - V2 Advanced Stats (Usage, TS%, Pace, Matchup data)
    - Game log baseline stats
    - Market data (line, team totals)
    """
    # Find player
    hub = db['nba_master_hub_2026']
    player = hub.find_one({
        '$or': [
            {'player_name': {'$regex': player_name, '$options': 'i'}},
            {'display_name': {'$regex': player_name, '$options': 'i'}},
        ]
    })
    
    if not player:
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found")
    
    bdl_id = player.get('bdl_id')
    display_name = player.get('display_name') or player.get('player_name')
    
    # Get advanced stats
    fetcher = get_fetcher()
    advanced = fetcher.get_player_advanced_stats(bdl_id, seasons=[2024, 2025]) if bdl_id else []
    
    # Calculate rolling advanced stats
    features = {
        "player_name": display_name,
        "bdl_id": bdl_id,
        "stat_type": stat_type,
        "line": line,
        "opponent_team": opponent_team,
    }
    
    if advanced:
        # L5 Advanced Stats
        l5 = advanced[:5]
        
        features["v2_advanced"] = {
            "games_available": len(advanced),
            "usage_rate_l5": round(sum(g.get('usage_percentage') or 0 for g in l5) / len(l5), 1) if l5 else None,
            "true_shooting_l5": round(sum(g.get('true_shooting_percentage') or 0 for g in l5) / len(l5), 1) if l5 else None,
            "efg_l5": round(sum(g.get('effective_field_goal_percentage') or 0 for g in l5) / len(l5), 1) if l5 else None,
            "pace_l5": round(sum(g.get('pace') or 0 for g in l5) / len(l5), 1) if l5 else None,
            "offensive_rating_l5": round(sum(g.get('offensive_rating') or 0 for g in l5) / len(l5), 1) if l5 else None,
            "assist_percentage_l5": round(sum(g.get('assist_percentage') or 0 for g in l5) / len(l5), 1) if l5 else None,
            "rebound_percentage_l5": round(sum(g.get('rebound_percentage') or 0 for g in l5) / len(l5), 1) if l5 else None,
        }
        
        # L10 for stability
        l10 = advanced[:10]
        if len(l10) >= 10:
            features["v2_advanced"]["usage_rate_l10"] = round(sum(g.get('usage_percentage') or 0 for g in l10) / len(l10), 1)
            features["v2_advanced"]["true_shooting_l10"] = round(sum(g.get('true_shooting_percentage') or 0 for g in l10) / len(l10), 1)
    else:
        features["v2_advanced"] = {
            "games_available": 0,
            "message": "No V2 advanced stats. Run /fetch-season/{season} first."
        }
    
    return {
        "success": True,
        "features": features
    }
