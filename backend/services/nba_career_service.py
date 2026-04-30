"""
NBA Career Stats Service
========================
Fetches career stats from NBA.com via nba_api library.
Caches results in MongoDB to avoid rate limiting.

Usage:
    from services.nba_career_service import get_career_stats, sync_career_stats
    
    # Get cached career stats for a player
    stats = await get_career_stats(db, "LeBron James")
    
    # Sync career stats for all tracked players
    await sync_career_stats(db)
"""

import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
import time

from services.config.collection_names import COLL
from services.observability import log_silent_failure

logger = logging.getLogger(__name__)

# Rate limiting - NBA API is sensitive
NBA_API_DELAY = 0.6  # seconds between requests

# Cache duration - career stats don't change frequently
CACHE_DURATION_HOURS = 24

# All-time rankings thresholds
MILESTONE_THRESHOLDS = {
    "pts": [10000, 15000, 20000, 25000, 30000, 35000, 40000, 45000],
    "reb": [5000, 7500, 10000, 12500, 15000, 17500, 20000],
    "ast": [3000, 5000, 7500, 10000, 12000, 15000],
    "stl": [1000, 1500, 2000, 2500, 3000],
    "blk": [1000, 1500, 2000, 2500, 3000],
    "3pm": [1000, 1500, 2000, 2500, 3000, 3500, 4000],
}


def _fetch_career_stats_sync(player_name: str) -> Optional[Dict]:
    """
    Synchronously fetch career stats from NBA API.
    
    This is called in a thread pool to avoid blocking the event loop.
    
    Error Handling:
    - JSON decode errors ("char 0"): Graceful skip with warning
    - Timeout errors: Graceful skip with warning
    - Player not found: Returns None silently
    """
    import json
    
    try:
        from nba_api.stats.static import players
        from nba_api.stats.endpoints import playercareerstats
        
        # Find player ID
        found = players.find_players_by_full_name(player_name)
        if not found:
            # Try partial match
            all_players = players.get_players()
            name_lower = player_name.lower()
            for p in all_players:
                if p['full_name'].lower() == name_lower:
                    found = [p]
                    break
        
        if not found:
            logger.debug(f"[NBA_API] Player not found: {player_name}")
            return None
        
        player_id = found[0]['id']
        player_info = found[0]
        
        time.sleep(NBA_API_DELAY)  # Rate limiting
        
        # Fetch career stats with enhanced error handling
        try:
            career = playercareerstats.PlayerCareerStats(player_id=player_id)
            totals_df = career.career_totals_regular_season.get_data_frame()
        except json.JSONDecodeError as jde:
            # Handle "char 0" JSON decode errors - NBA API returns invalid JSON
            logger.warning(f"[NBA_API] JSON decode error for {player_name} (char {jde.pos}): {jde.msg}")
            return None
        except Exception as api_err:
            # Handle other API errors (timeout, connection, etc.)
            err_str = str(api_err).lower()
            if "timeout" in err_str or "connection" in err_str or "expecting value" in err_str:
                logger.warning(f"[NBA_API] API error for {player_name} (graceful skip): {api_err}")
                return None
            raise  # Re-raise unexpected errors
        
        if totals_df.empty:
            logger.debug(f"[NBA_API] No career totals for: {player_name}")
            return None
        
        row = totals_df.iloc[0]
        
        stats = {
            "player_name": player_info['full_name'],
            "nba_id": player_id,
            "is_active": player_info.get('is_active', False),
            "games_played": int(row.get('GP', 0)),
            "career_pts": int(row.get('PTS', 0)),
            "career_reb": int(row.get('REB', 0)),
            "career_ast": int(row.get('AST', 0)),
            "career_stl": int(row.get('STL', 0)),
            "career_blk": int(row.get('BLK', 0)),
            "career_3pm": int(row.get('FG3M', 0)),
            "career_tov": int(row.get('TOV', 0)),
            "career_min": int(row.get('MIN', 0)),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": "nba_api"
        }
        
        logger.info(f"[NBA_API] Fetched career stats for {player_name}: {stats['career_pts']:,} PTS")
        return stats
        
    except json.JSONDecodeError as jde:
        # Catch any JSON errors that slip through
        logger.warning(f"[NBA_API] JSON error for {player_name} (graceful skip): char {jde.pos}")
        return None
    except Exception as e:
        # Log but don't crash the sync pipeline
        err_str = str(e).lower()
        if "json" in err_str or "decode" in err_str or "char 0" in err_str:
            logger.warning(f"[NBA_API] Parse error for {player_name} (graceful skip): {e}")
            return None
        logger.error(f"[NBA_API] Error fetching {player_name}: {e}")
        return None


async def get_career_stats(db, player_name: str, force_refresh: bool = False) -> Optional[Dict]:
    """
    Get career stats for a player, using cache if available.
    
    Args:
        db: MongoDB database instance
        player_name: Player's full name
        force_refresh: If True, bypass cache and fetch fresh data
        
    Returns:
        Dict with career stats or None if not found
    """
    collection = db[COLL("career_backstop", "nba")]
    
    # Check cache first
    if not force_refresh:
        cached = await collection.find_one(
            {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0}
        )
        
        if cached:
            # Check if cache is still valid
            fetched_at = cached.get("fetched_at")
            if fetched_at:
                try:
                    fetch_time = datetime.fromisoformat(fetched_at.replace('Z', '+00:00'))
                    age_hours = (datetime.now(timezone.utc) - fetch_time).total_seconds() / 3600
                    if age_hours < CACHE_DURATION_HOURS:
                        logger.debug(f"[NBA_API] Using cached stats for {player_name} (age: {age_hours:.1f}h)")
                        return cached
                except Exception as _swept_exc:
                    log_silent_failure("services.nba_career_service.get_career_stats", _swept_exc)  # sweep-auto-converted
    
    # Fetch fresh data
    loop = asyncio.get_event_loop()
    stats = await loop.run_in_executor(None, _fetch_career_stats_sync, player_name)
    
    if stats:
        # Update cache
        await collection.update_one(
            {"player_name": stats["player_name"]},
            {"$set": stats},
            upsert=True
        )
    
    return stats


async def sync_career_stats_for_players(db, player_names: List[str]) -> Dict[str, Any]:
    """
    Sync career stats for a list of players.
    
    Args:
        db: MongoDB database instance
        player_names: List of player names to sync
        
    Returns:
        Dict with sync results
    """
    results = {
        "synced": 0,
        "failed": 0,
        "skipped": 0,
        "players": []
    }
    
    for player_name in player_names:
        try:
            stats = await get_career_stats(db, player_name, force_refresh=True)
            if stats:
                results["synced"] += 1
                results["players"].append({
                    "name": player_name,
                    "pts": stats.get("career_pts", 0)
                })
            else:
                results["failed"] += 1
        except Exception as e:
            logger.error(f"[NBA_API] Sync error for {player_name}: {e}")
            results["failed"] += 1
    
    return results


async def get_milestone_for_player(db, player_name: str) -> Optional[Dict]:
    """
    Get milestone information for a player based on their career stats.
    
    Returns the most significant milestone (all-time ranking or approaching threshold).
    """
    stats = await get_career_stats(db, player_name)
    if not stats:
        return None
    
    milestones = []
    
    # Map stat fields to display names
    stat_map = {
        "career_pts": ("pts", "career points"),
        "career_reb": ("reb", "career rebounds"),
        "career_ast": ("ast", "career assists"),
        "career_stl": ("stl", "career steals"),
        "career_blk": ("blk", "career blocks"),
        "career_3pm": ("3pm", "career 3-pointers"),
    }
    
    for field, (key, display) in stat_map.items():
        current = stats.get(field, 0)
        if not current:
            continue
        
        thresholds = MILESTONE_THRESHOLDS.get(key, [])
        
        # Check approaching thresholds
        for threshold in thresholds:
            distance = threshold - current
            if 0 < distance <= 500:
                threshold_display = f"{threshold // 1000}K" if threshold >= 1000 else str(threshold)
                milestones.append({
                    "type": "approaching",
                    "stat": key,
                    "stat_display": display,
                    "current": current,
                    "target": threshold,
                    "distance": distance,
                    "headline": f"{threshold_display} WATCH",
                    "description": f"{distance:,} away from {threshold:,} {display}",
                    "severity": 7
                })
                break
    
    # Return most significant milestone
    if milestones:
        return sorted(milestones, key=lambda x: x["distance"])[0]
    
    return None


# List of players to track for career milestones
TRACKED_PLAYERS = [
    # All-time greats
    "LeBron James", "Kevin Durant", "Stephen Curry", "James Harden",
    "Chris Paul", "Russell Westbrook", "Carmelo Anthony",
    
    # Stars
    "Damian Lillard", "Kyrie Irving", "Anthony Davis", "Nikola Jokic",
    "Giannis Antetokounmpo", "Kawhi Leonard", "Paul George", "Jimmy Butler",
    "Jayson Tatum", "Luka Doncic", "Trae Young", "Devin Booker",
    "Donovan Mitchell", "Ja Morant", "Zion Williamson",
    
    # Veterans
    "DeMar DeRozan", "Klay Thompson", "Draymond Green", "Kyle Lowry",
    "Al Horford", "Brook Lopez", "Rudy Gobert",
    
    # Rising stars
    "Shai Gilgeous-Alexander", "Anthony Edwards", "Tyrese Haliburton",
    "LaMelo Ball", "Cade Cunningham", "Evan Mobley", "Scottie Barnes",
    
    # Others
    "Karl-Anthony Towns", "Bam Adebayo", "Pascal Siakam", "Julius Randle",
    "Jalen Brunson", "De'Aaron Fox", "CJ McCollum", "Brandon Ingram",
    "Domantas Sabonis", "Jaren Jackson Jr.", "Bradley Beal", "Zach LaVine",
]
