"""
Raw Validation Routes
=====================
DATA INTEGRITY CRISIS RESPONSE - Zero Processing, Raw API Data Only
Endpoints for raw stat validation and ESPN verification.
"""
from fastapi import APIRouter, HTTPException
from datetime import datetime, timezone
from typing import List
import logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Validation"])

# Reference to raw_stat_fetcher (set via dependency injection)
_raw_stat_fetcher = None


def set_raw_stat_fetcher(fetcher):
    """Set the raw stat fetcher reference."""
    global _raw_stat_fetcher
    _raw_stat_fetcher = fetcher


def get_raw_stat_fetcher():
    """Get the raw stat fetcher instance."""
    if _raw_stat_fetcher is None:
        raise HTTPException(status_code=500, detail="Raw Stat Fetcher not initialized")
    return _raw_stat_fetcher


@router.get("/v3/raw-validation/{player_name}")
async def get_raw_validation_for_player(player_name: str):
    """
    RAW STAT VALIDATION - Fetch unprocessed stats for manual ESPN verification.
    
    This endpoint returns EXACTLY what BallDontLie API returns.
    NO processing, NO adjustments, NO interpretation.
    
    Compare these values directly against ESPN box scores.
    If they don't match, we have an API data issue.
    
    Returns:
    - player_name: str
    - bdl_player_id: int
    - last_5_games: [
        { date, vs, pts (RAW), reb (RAW), ast (RAW) }
      ]
    """
    fetcher = get_raw_stat_fetcher()
    result = await fetcher.fetch_and_validate_player(player_name)
    
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "Player not found"))
    
    return result


@router.post("/v3/raw-validation/batch")
async def batch_raw_validation(player_names: List[str]):
    """
    Fetch raw validation data for multiple players at once.
    
    Use this to populate the validation table UI.
    
    Request body: ["Luka Doncic", "Anthony Edwards", "Naji Marshall"]
    """
    fetcher = get_raw_stat_fetcher()
    
    results = []
    for name in player_names[:20]:  # Limit to 20 players
        try:
            result = await fetcher.fetch_and_validate_player(name)
            if result.get("success"):
                results.append(result["validation_entry"])
            else:
                results.append({
                    "player_name": name,
                    "error": result.get("error", "Failed to fetch")
                })
        except Exception as e:
            results.append({
                "player_name": name,
                "error": str(e)
            })
    
    return {
        "success": True,
        "validation_entries": results,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "bdl_stats_api"
    }


@router.get("/v3/raw-validation-table")
async def get_raw_validation_table():
    """
    Get the full validation table for the UI.
    
    Returns all players that have been fetched for validation,
    with their RAW stats for manual ESPN comparison.
    """
    fetcher = get_raw_stat_fetcher()
    result = await fetcher.get_validation_table()
    return result


@router.get("/v3/raw-player-games/{player_name}")
async def get_raw_player_games(player_name: str, num_games: int = 10):
    """
    Get raw game logs for a player - FULL DETAIL.
    
    This returns the complete raw API response for deep inspection.
    Use this to debug data issues.
    """
    fetcher = get_raw_stat_fetcher()
    result = await fetcher.get_raw_recent_games(player_name, num_games)
    return result
