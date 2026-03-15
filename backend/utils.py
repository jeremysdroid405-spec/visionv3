"""
Server Utilities Module
=======================
Common utility functions extracted from server.py
"""
from typing import Any, Optional
from datetime import datetime, timezone, timedelta
from thefuzz import fuzz
import logging

logger = logging.getLogger(__name__)

# Cache reference (set by main app)
_cache_collection = None


def set_cache_collection(cache):
    """Set the cache collection reference."""
    global _cache_collection
    _cache_collection = cache


async def get_cached_data(cache_key: str, ttl_hours: int = 24) -> Optional[Any]:
    """Get data from cache if not expired."""
    if _cache_collection is None:
        return None
    
    cached = await _cache_collection.find_one({"key": cache_key})
    if cached:
        expires_at = cached.get("expires_at")
        if expires_at and datetime.now(timezone.utc) < expires_at:
            return cached.get("data")
    return None


async def set_cached_data(cache_key: str, data: Any, ttl_hours: int = 24):
    """Set data in cache with TTL."""
    if _cache_collection is None:
        return
    
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    await _cache_collection.update_one(
        {"key": cache_key},
        {"$set": {"key": cache_key, "data": data, "expires_at": expires_at}},
        upsert=True
    )


def fuzzy_match_player(name1: str, name2: str, threshold: int = 80) -> bool:
    """Fuzzy match two player names."""
    return fuzz.ratio(name1.lower(), name2.lower()) >= threshold


def normalize_player_name(name: str) -> str:
    """Normalize player name for comparison."""
    import re
    # Remove suffixes like Jr., Sr., III, etc.
    name = re.sub(r'\s+(Jr\.?|Sr\.?|III|II|IV)$', '', name, flags=re.IGNORECASE)
    # Remove extra whitespace
    name = ' '.join(name.split())
    return name.lower().strip()


def calculate_hit_rate(values: list, threshold: float) -> dict:
    """Calculate hit rate for a list of values against a threshold."""
    if not values:
        return {"hit_rate": 0, "hits": 0, "total": 0}
    
    hits = sum(1 for v in values if v > threshold)
    return {
        "hit_rate": round(hits / len(values) * 100, 1),
        "hits": hits,
        "total": len(values)
    }


def format_american_odds(decimal_odds: float) -> int:
    """Convert decimal odds to American format."""
    if decimal_odds >= 2.0:
        return int((decimal_odds - 1) * 100)
    else:
        return int(-100 / (decimal_odds - 1))


def parse_commence_time(time_str: str) -> Optional[datetime]:
    """Parse commence time string to datetime."""
    if not time_str:
        return None
    try:
        return datetime.fromisoformat(time_str.replace('Z', '+00:00'))
    except:
        return None


def is_game_started(commence_time_str: str) -> bool:
    """Check if a game has started based on commence time."""
    commence_time = parse_commence_time(commence_time_str)
    if not commence_time:
        return False
    return datetime.now(timezone.utc) >= commence_time
