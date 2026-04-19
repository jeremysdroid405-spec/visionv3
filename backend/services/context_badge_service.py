"""
Context Badge Population Service
================================
Populates context badges for all players in master_hub based on:
- Game schedule (home/away, back-to-back)
- Travel distance
- Former team matchups (revenge)
- Contract status
- Performance trends (hot streaks)

Badges:
- home_cookin: Playing at home
- gassed: Back-to-back game (2nd night)
- jet_lag: Long travel distance (>1000 miles)
- revenge: Playing former team
- pay_day: Contract year
- locked_in: Hot streak (L5 avg > season avg by 15%+)
- milestone: Near career milestone (manual/news-based)
- distraction: Trade rumors (manual/news-based)
- legal_noise: Legal issues (manual/news-based)
- deep_water: Playoff pressure (manual/season-based)
"""
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

# Team city coordinates for travel distance calculation (lat, lon)
TEAM_LOCATIONS = {
    "ATL": (33.7573, -84.3963),  # Atlanta
    "BOS": (42.3662, -71.0621),  # Boston
    "BKN": (40.6826, -73.9754),  # Brooklyn
    "CHA": (35.2251, -80.8392),  # Charlotte
    "CHI": (41.8807, -87.6742),  # Chicago
    "CLE": (41.4965, -81.6882),  # Cleveland
    "DAL": (32.7905, -96.8103),  # Dallas
    "DEN": (39.7487, -105.0077), # Denver
    "DET": (42.3410, -83.0553),  # Detroit
    "GSW": (37.7680, -122.3877), # Golden State (San Francisco)
    "HOU": (29.7508, -95.3621),  # Houston
    "IND": (39.7640, -86.1555),  # Indianapolis
    "LAC": (34.0430, -118.2673), # LA Clippers
    "LAL": (34.0430, -118.2673), # LA Lakers
    "MEM": (35.1382, -90.0505),  # Memphis
    "MIA": (25.7814, -80.1870),  # Miami
    "MIL": (43.0451, -87.9172),  # Milwaukee
    "MIN": (44.9795, -93.2760),  # Minnesota
    "NOP": (29.9490, -90.0821),  # New Orleans
    "NYK": (40.7505, -73.9934),  # New York
    "OKC": (35.4634, -97.5151),  # Oklahoma City
    "ORL": (28.5392, -81.3839),  # Orlando
    "PHI": (39.9012, -75.1720),  # Philadelphia
    "PHX": (33.4457, -112.0712), # Phoenix
    "POR": (45.5316, -122.6668), # Portland
    "SAC": (38.5802, -121.4997), # Sacramento
    "SAS": (29.4270, -98.4375),  # San Antonio
    "TOR": (43.6435, -79.3791),  # Toronto
    "UTA": (40.7683, -111.9011), # Utah (Salt Lake City)
    "WAS": (38.8981, -77.0209),  # Washington
}

# Known player former teams for revenge games
# Format: player_name -> list of former team abbreviations
PLAYER_FORMER_TEAMS = {
    "LeBron James": ["CLE", "MIA"],
    "James Harden": ["OKC", "HOU", "BKN"],
    "Russell Westbrook": ["OKC", "HOU", "WAS", "LAL", "LAC", "DEN"],
    "Chris Paul": ["NOP", "LAC", "HOU", "OKC", "PHX", "GSW"],
    "Kevin Durant": ["OKC", "GSW", "BKN"],
    "Kyrie Irving": ["CLE", "BOS", "BKN"],
    "Paul George": ["IND", "OKC", "LAC"],
    "Jimmy Butler": ["CHI", "MIN", "PHI"],
    "DeMar DeRozan": ["TOR", "SAS", "CHI"],
    "Kyle Lowry": ["MEM", "HOU", "TOR"],
    "Pascal Siakam": ["TOR"],
    "OG Anunoby": ["TOR", "NYK"],
    "Dejounte Murray": ["SAS", "ATL"],
    "Jerami Grant": ["OKC", "DEN", "DET"],
    "Jrue Holiday": ["NOP", "MIL", "POR"],
    "Khris Middleton": ["DET"],
    "Marcus Smart": ["BOS", "MEM"],
    "Draymond Green": [],  # GSW lifer
    "Klay Thompson": ["GSW"],
    "D'Angelo Russell": ["LAL", "BKN", "GSW", "MIN"],
    "Zach LaVine": ["MIN"],
    "Nikola Vucevic": ["ORL"],
    "Terry Rozier": ["BOS", "CHA"],
    "Gordon Hayward": ["UTA", "BOS", "CHA"],
    "Tobias Harris": ["ORL", "DET", "LAC"],
    "De'Aaron Fox": [],
    "Domantas Sabonis": ["OKC", "IND"],
    "Tyrese Haliburton": ["SAC"],
    "Myles Turner": [],
    "Fred VanVleet": ["TOR"],
    "Cade Cunningham": [],
    "Jalen Brunson": ["DAL"],
    "Julius Randle": ["LAL", "NOP", "NYK"],
    "Karl-Anthony Towns": ["MIN"],
    "Mikal Bridges": ["PHX", "BKN"],
    "Cam Johnson": ["PHX", "BKN"],
    "Dorian Finney-Smith": ["DAL", "BKN"],
    "Spencer Dinwiddie": ["BKN", "WAS", "DAL"],
    "Dennis Schroder": ["ATL", "OKC", "LAL", "BOS", "HOU", "LAL", "TOR", "BKN"],
    "Montrezl Harrell": ["HOU", "LAC", "LAL", "WAS", "CHA", "PHI"],
    "Patrick Beverley": ["HOU", "LAC", "MIN", "LAL", "CHI", "PHI"],
}


def calculate_distance(team1: str, team2: str) -> float:
    """Calculate distance in miles between two team cities using Haversine formula."""
    from math import radians, sin, cos, sqrt, atan2
    
    loc1 = TEAM_LOCATIONS.get(team1)
    loc2 = TEAM_LOCATIONS.get(team2)
    
    if not loc1 or not loc2:
        return 0
    
    lat1, lon1 = radians(loc1[0]), radians(loc1[1])
    lat2, lon2 = radians(loc2[0]), radians(loc2[1])
    
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    
    # Earth's radius in miles
    R = 3959
    return R * c


class ContextBadgeService:
    """
    Service to populate and sync context badges for players.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.master_hub = db[COLL("master_hub", "nba")]
        self.cached_board = COLL.handle(db, "board_cache", "nba")
        self.schedule_cache = db.schedule_cache
    
    async def get_todays_games(self) -> List[Dict]:
        """Get today's games from the schedule or cached board."""
        # Try to get from cached board (has today's props)
        games = []
        
        async for doc in self.cached_board.find({}, {"player_name": 1, "team": 1, "opponent": 1, "props": 1}):
            if doc.get("props"):
                prop = doc["props"][0]  # First prop has game info
                game_key = f"{doc.get('team')}_{prop.get('opponent', doc.get('opponent'))}"
                games.append({
                    "player_name": doc.get("player_name"),
                    "team": doc.get("team"),
                    "opponent": prop.get("opponent") or doc.get("opponent"),
                    "home_team": prop.get("home_team"),
                    "away_team": prop.get("away_team"),
                    "game_id": prop.get("event_id"),
                    "commence_time": prop.get("commence_time")
                })
        
        return games
    
    async def get_player_performance(self, player_name: str) -> Dict:
        """Get player's L5 vs season performance for locked_in badge."""
        player = await self.master_hub.find_one(
            {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0, "baseline_stats": 1}
        )
        
        if not player:
            return {}
        
        baseline = player.get("baseline_stats", {})
        pts = baseline.get("PTS", {})
        
        return {
            "l5_avg": pts.get("l5_avg"),
            "l10_avg": pts.get("l10_avg"),
            "season_avg": pts.get("season_avg")
        }
    
    async def compute_badges_for_player(
        self,
        player_name: str,
        team: str,
        opponent: str,
        home_team: str = None,
        away_team: str = None
    ) -> List[Dict]:
        """
        Compute all applicable badges for a player based on today's game context.
        
        Returns list of badge dictionaries.
        """
        badges = []
        
        # 1. HOME_COOKIN - Playing at home
        if home_team and team:
            is_home = team.upper() == home_team.upper()
            if is_home:
                badges.append({
                    "badge_key": "home_cookin",
                    "display": "Home Cookin'",
                    "icon": "Home",
                    "color": "#22c55e",
                    "description": "Home game advantage",
                    "auto_generated": True
                })
        
        # 2. JET_LAG - Long travel distance (road game > 1000 miles)
        if away_team and team and team.upper() == away_team.upper():
            # Player is on the away team
            if opponent:
                distance = calculate_distance(team.upper(), opponent.upper())
                if distance > 1000:
                    badges.append({
                        "badge_key": "jet_lag",
                        "display": "Jet Lag",
                        "icon": "Plane",
                        "color": "#a855f7",
                        "description": f"Traveled {int(distance)} miles",
                        "travel_miles": int(distance),
                        "auto_generated": True
                    })
        
        # 3. REVENGE - Playing former team
        former_teams = PLAYER_FORMER_TEAMS.get(player_name, [])
        if opponent and opponent.upper() in [t.upper() for t in former_teams]:
            badges.append({
                "badge_key": "revenge",
                "display": "Revenge",
                "icon": "Swords",
                "color": "#ef4444",
                "description": f"Former team matchup vs {opponent}",
                "auto_generated": True
            })
        
        # 4. LOCKED_IN - Hot streak (L5 avg > season avg by 15%+)
        perf = await self.get_player_performance(player_name)
        l5_avg = perf.get("l5_avg")
        season_avg = perf.get("season_avg")
        
        if l5_avg and season_avg and season_avg > 0:
            improvement = (l5_avg - season_avg) / season_avg
            if improvement >= 0.15:  # 15% improvement
                badges.append({
                    "badge_key": "locked_in",
                    "display": "Locked In",
                    "icon": "Target",
                    "color": "#06b6d4",
                    "description": f"L5 avg {l5_avg:.1f} vs season {season_avg:.1f} (+{improvement*100:.0f}%)",
                    "auto_generated": True
                })
        
        # 5. TODO: GASSED - Back-to-back (requires schedule data)
        # Would need yesterday's games to determine B2B
        
        # 6. TODO: PAY_DAY - Contract year (requires contract data)
        # Already have some in spotrac_contracts_cache
        
        return badges
    
    async def sync_badges_for_all_players(self, limit: int = 500) -> Dict[str, int]:
        """
        Sync context badges for all players with active props.
        
        Fetches game context and computes badges, then stores in master_hub.
        """
        logger.info("[BADGE_SYNC] Starting badge population...")
        
        games = await self.get_todays_games()
        logger.info(f"[BADGE_SYNC] Found {len(games)} player-game combinations")
        
        # Group by player
        player_games = {}
        for g in games:
            pn = g.get("player_name")
            if pn and pn not in player_games:
                player_games[pn] = g
        
        updated = 0
        skipped = 0
        
        for player_name, game_info in list(player_games.items())[:limit]:
            try:
                badges = await self.compute_badges_for_player(
                    player_name=player_name,
                    team=game_info.get("team"),
                    opponent=game_info.get("opponent"),
                    home_team=game_info.get("home_team"),
                    away_team=game_info.get("away_team")
                )
                
                if badges:
                    # Store in master_hub - use exact match for display_name
                    result = await self.master_hub.update_one(
                        {"display_name": player_name},
                        {"$set": {
                            "context_badges": badges,
                            "badges_synced_at": datetime.now(timezone.utc).isoformat()
                        }}
                    )
                    
                    if result.modified_count > 0 or result.matched_count > 0:
                        updated += 1
                        badge_keys = [b["badge_key"] for b in badges]
                        logger.info(f"[BADGE_SYNC] {player_name}: {badge_keys}")
                    else:
                        # Try case-insensitive fallback
                        import re
                        result = await self.master_hub.update_one(
                            {"display_name": {"$regex": f"^{re.escape(player_name)}$", "$options": "i"}},
                            {"$set": {
                                "context_badges": badges,
                                "badges_synced_at": datetime.now(timezone.utc).isoformat()
                            }}
                        )
                        if result.modified_count > 0:
                            updated += 1
                            logger.info(f"[BADGE_SYNC] {player_name} (regex): {[b['badge_key'] for b in badges]}")
                        else:
                            skipped += 1
                else:
                    skipped += 1
                    
            except Exception as e:
                logger.error(f"[BADGE_SYNC] Error for {player_name}: {e}")
                skipped += 1
        
        # Also add badges to cached_board for frontend access
        await self._sync_badges_to_cached_board()
        
        logger.info(f"[BADGE_SYNC] Complete: {updated} updated, {skipped} skipped")
        return {"updated": updated, "skipped": skipped}
    
    async def _sync_badges_to_cached_board(self):
        """Copy badges from master_hub to cached_board for faster frontend access."""
        sync_count = 0
        # Get all players with badges
        async for player in self.master_hub.find(
            {"context_badges": {"$exists": True, "$ne": []}},
            {"_id": 0, "display_name": 1, "context_badges": 1}
        ):
            player_name = player.get("display_name")
            badges = player.get("context_badges", [])
            
            if player_name and badges:
                result = await self.cached_board.update_one(
                    {"player_name": player_name},
                    {"$set": {"context_badges": badges}}
                )
                if result.modified_count > 0:
                    sync_count += 1
        
        logger.info(f"[BADGE_SYNC] Synced {sync_count} players' badges to cached_board")
    
    async def add_manual_badge(
        self,
        player_name: str,
        badge_key: str,
        description: str = None
    ) -> bool:
        """
        Manually add a badge to a player (for news-based badges like distraction, milestone).
        """
        from services.badge_resolver import BADGE_DEFINITIONS
        
        badge_def = BADGE_DEFINITIONS.get(badge_key)
        if not badge_def:
            logger.warning(f"[BADGE_SYNC] Unknown badge key: {badge_key}")
            return False
        
        badge = {
            "badge_key": badge_key,
            "display": badge_def.get("display", badge_key),
            "icon": badge_def.get("icon", "Info"),
            "color": badge_def.get("color", "#6b7280"),
            "description": description or badge_def.get("description", ""),
            "manual": True,
            "added_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Add to existing badges
        result = await self.master_hub.update_one(
            {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"$addToSet": {"context_badges": badge}}
        )
        
        return result.modified_count > 0


def get_badge_service(db) -> ContextBadgeService:
    """Get or create badge service instance."""
    return ContextBadgeService(db)
