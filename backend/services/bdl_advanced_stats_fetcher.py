"""
BDL Advanced Stats Fetcher
===========================

Pulls Game Advanced Stats V2 from BallDontLie API.
This gives us the REAL process stats:
- Usage Rate, Pace, True Shooting, eFG%
- Individual matchup data (who guarded who)
- Tracking stats (speed, touches, distance)

Data available from 2015 season onwards.
"""

import os
import logging
import httpx
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from pymongo import MongoClient, UpdateOne

logger = logging.getLogger(__name__)

BDL_API_KEY = os.environ.get("BDL_API_KEY", "")
BDL_BASE_URL = "https://api.balldontlie.io"

# Rate limiting for GOAT tier (600 requests/min)
REQUESTS_PER_MINUTE = 500  # Stay under limit
REQUEST_DELAY = 60 / REQUESTS_PER_MINUTE


class BDLAdvancedStatsFetcher:
    """
    Fetches advanced stats from BDL API V2.
    """
    
    def __init__(self, db):
        self.db = db
        self.advanced_stats = db['bdl_advanced_stats']
        self.request_count = 0
        self.last_request_time = 0
        
        # Create indexes
        self.advanced_stats.create_index([("player_id", 1), ("game_id", 1)], unique=True)
        self.advanced_stats.create_index([("season", 1)])
        self.advanced_stats.create_index([("player_id", 1), ("season", 1)])
    
    def _rate_limit(self):
        """Enforce rate limiting."""
        now = time.time()
        elapsed = now - self.last_request_time
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        self.last_request_time = time.time()
        self.request_count += 1
    
    def _make_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Make rate-limited request to BDL API."""
        self._rate_limit()
        
        headers = {"Authorization": BDL_API_KEY}
        url = f"{BDL_BASE_URL}{endpoint}"
        
        try:
            with httpx.Client(timeout=60) as client:
                response = client.get(url, headers=headers, params=params or {})
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"BDL request failed: {e.response.status_code} - {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"BDL request error: {e}")
            return None
    
    def fetch_advanced_stats_for_season(
        self,
        season: int,
        player_ids: List[int] = None,
        start_date: str = None,
        end_date: str = None
    ) -> Dict[str, Any]:
        """
        Fetch all advanced stats for a season.
        
        Args:
            season: Season year (e.g., 2024 for 2024-25)
            player_ids: Optional list of player IDs to filter
            start_date: Optional start date (YYYY-MM-DD)
            end_date: Optional end date (YYYY-MM-DD)
        
        Returns:
            Summary of fetched data
        """
        logger.info(f"Fetching advanced stats for season {season}...")
        
        all_stats = []
        cursor = None
        page = 0
        
        while True:
            params = {
                "seasons[]": season,
                "per_page": 100,
                "period": 0,  # Full game stats
            }
            
            if cursor:
                params["cursor"] = cursor
            if player_ids:
                for pid in player_ids[:10]:  # Limit to avoid URL length issues
                    params[f"player_ids[]"] = pid
            if start_date:
                params["start_date"] = start_date
            if end_date:
                params["end_date"] = end_date
            
            data = self._make_request("/nba/v2/stats/advanced", params)
            
            if not data:
                logger.error(f"Failed to fetch page {page}")
                break
            
            stats = data.get("data", [])
            if not stats:
                break
            
            all_stats.extend(stats)
            page += 1
            
            if page % 10 == 0:
                logger.info(f"  Fetched {len(all_stats)} stats (page {page})...")
            
            # Get next cursor
            meta = data.get("meta", {})
            cursor = meta.get("next_cursor")
            
            if not cursor:
                break
        
        logger.info(f"Fetched {len(all_stats)} advanced stats for season {season}")
        
        # Store in MongoDB
        if all_stats:
            self._store_stats(all_stats, season)
        
        return {
            "season": season,
            "total_stats": len(all_stats),
            "pages": page
        }
    
    def _store_stats(self, stats: List[Dict], season: int):
        """Store advanced stats in MongoDB."""
        operations = []
        
        for stat in stats:
            player = stat.get("player", {})
            team = stat.get("team", {})
            game = stat.get("game", {})
            
            doc = {
                "player_id": player.get("id"),
                "player_name": f"{player.get('first_name', '')} {player.get('last_name', '')}".strip(),
                "team_id": team.get("id"),
                "team_abbr": team.get("abbreviation"),
                "game_id": game.get("id"),
                "game_date": game.get("date"),
                "season": season,
                "opponent_team_id": (
                    game.get("visitor_team_id") 
                    if game.get("home_team_id") == team.get("id")
                    else game.get("home_team_id")
                ),
                "is_home": game.get("home_team_id") == team.get("id"),
                
                # Core Advanced Stats
                "usage_percentage": stat.get("usage_percentage"),
                "pace": stat.get("pace"),
                "pace_per_40": stat.get("pace_per_40"),
                "possessions": stat.get("possessions"),
                "true_shooting_percentage": stat.get("true_shooting_percentage"),
                "effective_field_goal_percentage": stat.get("effective_field_goal_percentage"),
                "offensive_rating": stat.get("offensive_rating"),
                "defensive_rating": stat.get("defensive_rating"),
                "net_rating": stat.get("net_rating"),
                "pie": stat.get("pie"),
                
                # Assist/Turnover
                "assist_percentage": stat.get("assist_percentage"),
                "assist_ratio": stat.get("assist_ratio"),
                "assist_to_turnover": stat.get("assist_to_turnover"),
                "turnover_ratio": stat.get("turnover_ratio"),
                
                # Rebounding
                "offensive_rebound_percentage": stat.get("offensive_rebound_percentage"),
                "defensive_rebound_percentage": stat.get("defensive_rebound_percentage"),
                "rebound_percentage": stat.get("rebound_percentage"),
                
                # Scoring Breakdown
                "pct_pts_2pt": stat.get("pct_pts_2pt"),
                "pct_pts_3pt": stat.get("pct_pts_3pt"),
                "pct_pts_paint": stat.get("pct_pts_paint"),
                "pct_pts_free_throw": stat.get("pct_pts_free_throw"),
                "pct_pts_fast_break": stat.get("pct_pts_fast_break"),
                "pct_assisted_fgm": stat.get("pct_assisted_fgm"),
                "pct_unassisted_fgm": stat.get("pct_unassisted_fgm"),
                
                # Individual Matchup Data (THE GOOD STUFF)
                "matchup_minutes": stat.get("matchup_minutes"),
                "matchup_fg_pct": stat.get("matchup_fg_pct"),
                "matchup_fga": stat.get("matchup_fga"),
                "matchup_fgm": stat.get("matchup_fgm"),
                "matchup_3pt_pct": stat.get("matchup_3pt_pct"),
                "matchup_3pa": stat.get("matchup_3pa"),
                "matchup_3pm": stat.get("matchup_3pm"),
                "matchup_assists": stat.get("matchup_assists"),
                "matchup_turnovers": stat.get("matchup_turnovers"),
                "matchup_player_points": stat.get("matchup_player_points"),
                "switches_on": stat.get("switches_on"),
                
                # Hustle Stats
                "deflections": stat.get("deflections"),
                "loose_balls_recovered_total": stat.get("loose_balls_recovered_total"),
                "charges_drawn": stat.get("charges_drawn"),
                "contested_shots": stat.get("contested_shots"),
                "contested_shots_2pt": stat.get("contested_shots_2pt"),
                "contested_shots_3pt": stat.get("contested_shots_3pt"),
                "screen_assists": stat.get("screen_assists"),
                
                # Tracking Stats
                "speed": stat.get("speed"),
                "distance": stat.get("distance"),
                "touches": stat.get("touches"),
                "passes": stat.get("passes"),
                
                # Shot Quality
                "contested_fga": stat.get("contested_fga"),
                "contested_fgm": stat.get("contested_fgm"),
                "contested_fg_pct": stat.get("contested_fg_pct"),
                "uncontested_fga": stat.get("uncontested_fga"),
                "uncontested_fgm": stat.get("uncontested_fgm"),
                "uncontested_fg_pct": stat.get("uncontested_fg_pct"),
                
                # Usage Percentages
                "pct_fga": stat.get("pct_fga"),
                "pct_fgm": stat.get("pct_fgm"),
                "pct_points": stat.get("pct_points"),
                "pct_rebounds_total": stat.get("pct_rebounds_total"),
                "pct_assists": stat.get("pct_assists"),
                "pct_turnovers": stat.get("pct_turnovers"),
                
                # Misc
                "points_paint": stat.get("points_paint"),
                "points_fast_break": stat.get("points_fast_break"),
                "points_second_chance": stat.get("points_second_chance"),
                "fouls_drawn": stat.get("fouls_drawn"),
                
                "updated_at": datetime.now(timezone.utc)
            }
            
            operations.append(UpdateOne(
                {"player_id": doc["player_id"], "game_id": doc["game_id"]},
                {"$set": doc},
                upsert=True
            ))
        
        if operations:
            result = self.advanced_stats.bulk_write(operations)
            logger.info(f"Stored {result.upserted_count + result.modified_count} advanced stats")
    
    def fetch_multiple_seasons(
        self,
        seasons: List[int] = None
    ) -> Dict[str, Any]:
        """
        Fetch advanced stats for multiple seasons.
        
        Args:
            seasons: List of seasons to fetch (default: 2020-2025)
        """
        if seasons is None:
            seasons = [2020, 2021, 2022, 2023, 2024, 2025]
        
        results = {
            "seasons": {},
            "total_stats": 0
        }
        
        for season in seasons:
            try:
                result = self.fetch_advanced_stats_for_season(season)
                results["seasons"][season] = result
                results["total_stats"] += result.get("total_stats", 0)
            except Exception as e:
                logger.error(f"Failed to fetch season {season}: {e}")
                results["seasons"][season] = {"error": str(e)}
        
        return results
    
    def get_player_advanced_stats(
        self,
        player_id: int,
        seasons: List[int] = None
    ) -> List[Dict]:
        """Get all advanced stats for a player."""
        query = {"player_id": player_id}
        if seasons:
            query["season"] = {"$in": seasons}
        
        return list(self.advanced_stats.find(
            query,
            {"_id": 0}
        ).sort("game_date", -1))
    
    def get_stats_summary(self) -> Dict[str, Any]:
        """Get summary of stored advanced stats."""
        pipeline = [
            {"$group": {
                "_id": "$season",
                "count": {"$sum": 1},
                "players": {"$addToSet": "$player_id"}
            }},
            {"$project": {
                "season": "$_id",
                "games": "$count",
                "players": {"$size": "$players"}
            }},
            {"$sort": {"season": 1}}
        ]
        
        results = list(self.advanced_stats.aggregate(pipeline))
        
        total_stats = self.advanced_stats.count_documents({})
        
        return {
            "total_stats": total_stats,
            "by_season": {r["season"]: {"games": r["games"], "players": r["players"]} for r in results}
        }


# =============================================================================
# QUICK FETCH FUNCTION
# =============================================================================

def fetch_all_advanced_stats(db, seasons: List[int] = None):
    """Quick function to fetch all advanced stats."""
    fetcher = BDLAdvancedStatsFetcher(db)
    return fetcher.fetch_multiple_seasons(seasons)


# =============================================================================
# EXPORT
# =============================================================================

__all__ = [
    'BDLAdvancedStatsFetcher',
    'fetch_all_advanced_stats',
]
