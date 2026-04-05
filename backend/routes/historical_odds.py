"""
Historical Odds API Routes
===========================
Endpoints for fetching and managing historical player prop odds.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel
from typing import Optional, List
import os
import logging
from datetime import datetime
from pymongo import MongoClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v3/historical-odds", tags=["Historical Odds"])

# MongoDB
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "pick_vision")
client = MongoClient(MONGO_URL)
db = client[DB_NAME]

from services.historical_odds_fetcher import HistoricalOddsFetcher

_fetcher: Optional[HistoricalOddsFetcher] = None


def get_fetcher() -> HistoricalOddsFetcher:
    global _fetcher
    if _fetcher is None:
        _fetcher = HistoricalOddsFetcher(db)
    return _fetcher


class FetchDateRangeRequest(BaseModel):
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD


@router.get("/status")
async def get_status():
    """Get status of stored historical odds."""
    api_key = os.environ.get("ODDS_API_KEY", "")
    
    fetcher = get_fetcher()
    summary = fetcher.get_stats_summary()
    
    return {
        "success": True,
        "api_key_configured": bool(api_key),
        "collection": "historical_odds",
        "summary": summary,
    }


@router.post("/fetch-date")
async def fetch_single_date(
    date: str = Query(..., description="Date YYYY-MM-DD"),
    background_tasks: BackgroundTasks = None,
):
    """
    Fetch historical player props for all games on a specific date.
    
    Historical data available from May 3, 2023 onwards.
    """
    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="ODDS_API_KEY not configured")
    
    fetcher = get_fetcher()
    
    # Validate date format
    try:
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    
    # Fetch synchronously for single date
    result = fetcher.fetch_date_range(date, date, delay_seconds=0.5)
    
    return {
        "success": True,
        "date": date,
        "games_processed": result.get('total_games', 0),
        "props_stored": result.get('total_props', 0),
        "errors": len(result.get('errors', [])),
    }


@router.post("/fetch-range")
async def fetch_date_range(
    request: FetchDateRangeRequest,
    background_tasks: BackgroundTasks,
):
    """
    Fetch historical player props for a date range.
    
    Runs in background due to potentially long runtime.
    """
    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="ODDS_API_KEY not configured")
    
    # Validate dates
    try:
        start = datetime.strptime(request.start_date, '%Y-%m-%d')
        end = datetime.strptime(request.end_date, '%Y-%m-%d')
        
        if start > end:
            raise HTTPException(status_code=400, detail="start_date must be before end_date")
        
        if (end - start).days > 30:
            raise HTTPException(status_code=400, detail="Max 30 day range per request")
            
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    
    # Run in background
    background_tasks.add_task(
        run_fetch_range,
        request.start_date,
        request.end_date,
    )
    
    return {
        "success": True,
        "message": "Fetch started in background",
        "start_date": request.start_date,
        "end_date": request.end_date,
    }


def run_fetch_range(start_date: str, end_date: str):
    """Background task to fetch date range."""
    try:
        fetcher = get_fetcher()
        result = fetcher.fetch_date_range(start_date, end_date, delay_seconds=1.0)
        logger.info(f"Fetch complete: {result.get('total_games')} games, {result.get('total_props')} props")
    except Exception as e:
        logger.error(f"Fetch failed: {e}")


@router.get("/player/{player_name}")
async def get_player_historical_lines(
    player_name: str,
    stat_type: Optional[str] = Query(None, description="PTS, REB, AST, 3PM, PRA"),
    limit: int = Query(20, description="Max results"),
):
    """Get historical lines for a specific player."""
    query = {"player_name": {"$regex": player_name, "$options": "i"}}
    
    if stat_type:
        query["stat_type"] = stat_type
    
    # Only get "Over" lines (the actual line value)
    query["direction"] = "Over"
    
    docs = list(db['historical_odds'].find(
        query,
        {"_id": 0}
    ).sort("game_date", -1).limit(limit))
    
    return {
        "success": True,
        "player_name": player_name,
        "total": len(docs),
        "lines": docs,
    }


@router.get("/sample")
async def get_sample_odds():
    """Get sample of stored historical odds."""
    docs = list(db['historical_odds'].aggregate([
        {"$match": {"direction": "Over"}},
        {"$sample": {"size": 10}}
    ]))
    
    for d in docs:
        if '_id' in d:
            d['_id'] = str(d['_id'])
        if 'game_date' in d:
            d['game_date'] = d['game_date'].isoformat()
        if 'snapshot_time' in d:
            d['snapshot_time'] = d['snapshot_time'].isoformat()
        if 'fetched_at' in d:
            d['fetched_at'] = d['fetched_at'].isoformat()
    
    return {
        "success": True,
        "samples": docs,
    }
