"""
Vision Intel Cache API Routes
==============================
Instant-serve endpoints for Vision Intel Suite.

Frontend loads from cache FIRST - no database hits for display.

STRICT BOARD LOCKDOWN: Only live board props get enriched.

D7 (2026-04-21): The `POST /refresh/{sport}` endpoint and the
`DeltaManager`-driven manual refresh path were retired. The Delta
Engine (services/delta_engine.py) now maintains prop freshness
continuously; this module is read-only.
"""

from fastapi import APIRouter, Query
import logging

from services.rolling_cache_manager import get_cached_props, get_cached_prop_by_id

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
