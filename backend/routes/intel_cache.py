"""
Vision Intel Cache API Routes
==============================
Instant-serve endpoints for Vision Intel Suite.

Frontend loads from cache FIRST - no database hits for display.

STRICT BOARD LOCKDOWN: Only live board props get enriched.
"""

from fastapi import APIRouter, Query, BackgroundTasks
from typing import Optional
import logging
import os

from services.rolling_cache_manager import get_cached_props, get_cached_prop_by_id, DeltaManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v3/intel-cache", tags=["Intel Cache"])

# Database reference (set by main app)
_db = None

def set_db(db):
    global _db
    _db = db


@router.get("/nba")
async def get_nba_intel_cache():
    """
    Get all NBA props from cache for instant display.
    
    Frontend should call this on page load - returns instantly from JSON file.
    """
    return get_cached_props("NBA")


@router.get("/mlb")
async def get_mlb_intel_cache():
    """
    Get all MLB props from cache for instant display.
    
    Frontend should call this on page load - returns instantly from JSON file.
    """
    return get_cached_props("MLB")


@router.get("/prop/{prop_id}")
async def get_single_prop_from_cache(
    prop_id: str,
    sport: str = Query("NBA", description="Sport: NBA or MLB")
):
    """
    Get a single prop from cache by ID.
    
    Used for player detail page - instant response.
    """
    prop = get_cached_prop_by_id(prop_id, sport)
    
    if prop:
        return {
            'success': True,
            'prop_id': prop_id,
            'sport': sport,
            'data': prop
        }
    
    return {
        'success': False,
        'prop_id': prop_id,
        'sport': sport,
        'error': 'Prop not found in cache'
    }


@router.get("/status")
async def get_cache_status():
    """Get cache status for both sports."""
    nba_cache = get_cached_props("NBA")
    mlb_cache = get_cached_props("MLB")
    
    return {
        'nba': {
            'available': nba_cache.get('success', False),
            'prop_count': nba_cache.get('prop_count', 0),
            'last_updated': nba_cache.get('last_updated')
        },
        'mlb': {
            'available': mlb_cache.get('success', False),
            'prop_count': mlb_cache.get('prop_count', 0),
            'last_updated': mlb_cache.get('last_updated')
        }
    }


@router.post("/refresh/{sport}")
async def trigger_cache_refresh(sport: str, background_tasks: BackgroundTasks):
    """
    STRICT BOARD LOCKDOWN v2.0 - Manual trigger to refresh cache.
    
    This fetches ONLY Ferrari Tier picks (Safe Haven, Front Lines, War Zone)
    and enriches them. MAX ~30 props. Everything else is BANNED.
    """
    global _db
    
    if _db is None:
        return {'success': False, 'error': 'Database not initialized'}
    
    sport_upper = sport.upper()
    if sport_upper not in ['NBA', 'MLB']:
        return {'success': False, 'error': 'Invalid sport. Use NBA or MLB'}
    
    # Get the backend URL from environment
    backend_url = os.environ.get('REACT_APP_BACKEND_URL', 'http://localhost:8001')
    if backend_url.startswith('https://'):
        # Use internal URL for server-to-server calls
        backend_url = 'http://localhost:8001'
    
    async def run_refresh():
        try:
            delta_manager = DeltaManager(_db, sport_upper, backend_url)
            
            # Run STRICT BOARD LOCKDOWN - Ferrari Tiers Only
            result = await delta_manager.process_ferrari_tiers()
            
            logger.info(f"[MANUAL_REFRESH] {sport_upper} complete: {result}")
            
        except Exception as e:
            logger.error(f"[MANUAL_REFRESH] {sport_upper} error: {e}")
    
    background_tasks.add_task(run_refresh)
    
    return {
        'success': True,
        'message': f'Cache refresh triggered for {sport_upper} (Ferrari Tiers ONLY)',
        'note': 'Max ~30 props. Safe Haven + Front Lines + War Zone.'
    }
