"""
Defensive Momentum Service v4.1
================================
Weighted Composite DvP scoring with momentum tracking.

FORMULA: Composite_Rank = (Season_Rank * 0.50) + (L10_Rank * 0.35) + (L5_Rank * 0.15)

Data Sources:
- Season (50%): BDL /team_season_averages/general?type=defense → def_rating_rank
- L10 (35%): Calculated from BDL /games endpoint → avg points allowed last 10 games
- L5 (15%): Calculated from BDL /games endpoint → avg points allowed last 5 games

Features:
- Accurate season defensive ratings from official BDL endpoint
- L10, L5 rankings calculated from actual game scores (points allowed)
- Weighted composite rank for accurate matchup assessment
- Momentum tracking (improving vs regressing defenses)
- Trend alerts when L5 significantly diverges from Season
- Ferrari Score modifiers: Elite (1-5) = -15, Weak (25-30) = +15

Author: PropVision AI
Version: 4.1.0
"""
import logging
import os
import httpx
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import asyncio

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

# =============================================================================
# CONSTANTS
# =============================================================================

# Composite Weights
WEIGHT_SEASON = 0.50
WEIGHT_L10 = 0.35
WEIGHT_L5 = 0.15

# Ferrari Score Modifiers
ELITE_DEFENSE_PENALTY = -15.0  # Ranks 1-5 (elite defense = harder for Over)
WEAK_DEFENSE_BOOST = 15.0      # Ranks 25-30 (weak defense = easier for Over)

# Thresholds
ELITE_RANK_MAX = 5
WEAK_RANK_MIN = 25

# Trend Alert Thresholds
SURGE_THRESHOLD = 10  # L5 is 10+ ranks BETTER than Season = defense improving
COLLAPSE_THRESHOLD = 10  # L5 is 10+ ranks WORSE than Season = defense collapsing

# API Config
BDL_API_BASE = "https://api.balldontlie.io/nba/v1"
BDL_TEAM_SEASON_AVERAGES = f"{BDL_API_BASE}/team_season_averages/general"
BDL_GAMES = f"{BDL_API_BASE}/games"
BDL_TIMEOUT = 30.0

# Cache TTL
MOMENTUM_CACHE_TTL_MINUTES = 60

# Team ID mapping (BDL team IDs to abbreviations)
BDL_TEAM_ID_MAP = {
    1: "ATL", 2: "BOS", 3: "BKN", 4: "CHA", 5: "CHI", 6: "CLE",
    7: "DAL", 8: "DEN", 9: "DET", 10: "GSW", 11: "HOU", 12: "IND",
    13: "LAC", 14: "LAL", 15: "MEM", 16: "MIA", 17: "MIL", 18: "MIN",
    19: "NOP", 20: "NYK", 21: "OKC", 22: "ORL", 23: "PHI", 24: "PHX",
    25: "POR", 26: "SAC", 27: "SAS", 28: "TOR", 29: "UTA", 30: "WAS"
}

# Reverse mapping: abbreviation -> team_id
ABBREV_TO_ID = {v: k for k, v in BDL_TEAM_ID_MAP.items()}

ALL_TEAMS = list(BDL_TEAM_ID_MAP.values())

# =============================================================================
# STAT TYPE TO PROXY MAPPING
# =============================================================================

STAT_PROXY_MAP = {
    "PTS": {"proxy": "DRTG", "label": "Defensive Rating", "description": "Team Defensive Rating (lower = better defense)"},
    "POINTS": {"proxy": "DRTG", "label": "Defensive Rating", "description": "Team Defensive Rating (lower = better defense)"},
    "AST": {"proxy": "DRTG", "label": "Defensive Rating", "description": "Proxy: Defensive Rating (offensive flow correlates with assists)"},
    "ASSISTS": {"proxy": "DRTG", "label": "Defensive Rating", "description": "Proxy: Defensive Rating (offensive flow correlates with assists)"},
    "REB": {"proxy": "DRTG", "label": "Defensive Rating", "description": "Proxy: Defensive Rating (overall defensive presence)"},
    "REBOUNDS": {"proxy": "DRTG", "label": "Defensive Rating", "description": "Proxy: Defensive Rating (overall defensive presence)"},
    "3PM": {"proxy": "DRTG", "label": "Defensive Rating", "description": "Proxy: Defensive Rating for 3PM trend"},
    "THREES": {"proxy": "DRTG", "label": "Defensive Rating", "description": "Proxy: Defensive Rating for 3PM trend"},
    "PRA": {"proxy": "DRTG", "label": "Defensive Rating", "description": "Proxy: Defensive Rating for PRA trend"},
    "P+R": {"proxy": "DRTG", "label": "Defensive Rating", "description": "Proxy: Defensive Rating for P+R trend"},
    "P+A": {"proxy": "DRTG", "label": "Defensive Rating", "description": "Proxy: Defensive Rating for P+A trend"},
    "R+A": {"proxy": "DRTG", "label": "Defensive Rating", "description": "Proxy: Defensive Rating for R+A trend"},
    "PR": {"proxy": "DRTG", "label": "Defensive Rating", "description": "Proxy: Defensive Rating"},
    "PA": {"proxy": "DRTG", "label": "Defensive Rating", "description": "Proxy: Defensive Rating"},
    "RA": {"proxy": "DRTG", "label": "Defensive Rating", "description": "Proxy: Defensive Rating"},
    "BLK": {"proxy": "DRTG", "label": "Defensive Rating", "description": "Proxy: Defensive Rating for defensive activity"},
    "STL": {"proxy": "DRTG", "label": "Defensive Rating", "description": "Proxy: Defensive Rating for defensive activity"},
}

DEFAULT_PROXY = {"proxy": "DRTG", "label": "Defensive Rating", "description": "Default: Using Defensive Rating"}


# =============================================================================
# MOMENTUM DATA STRUCTURES
# =============================================================================

class DefensiveMomentumProfile:
    """Represents a team's defensive momentum profile."""
    
    def __init__(
        self,
        team: str,
        stat_type: str,
        season_rank: int,
        l10_rank: int,
        l5_rank: int,
        composite_rank: float,
        season_def_rating: float,
        l10_pts_allowed: float,
        l5_pts_allowed: float,
        momentum: str,
        trend_alert: Optional[str] = None,
        proxy_type: Optional[str] = None,
        proxy_label: Optional[str] = None,
        proxy_description: Optional[str] = None
    ):
        self.team = team
        self.stat_type = stat_type
        self.season_rank = season_rank
        self.l10_rank = l10_rank
        self.l5_rank = l5_rank
        self.composite_rank = composite_rank
        self.season_def_rating = season_def_rating
        self.l10_pts_allowed = l10_pts_allowed
        self.l5_pts_allowed = l5_pts_allowed
        self.momentum = momentum
        self.trend_alert = trend_alert
        self.proxy_type = proxy_type
        self.proxy_label = proxy_label
        self.proxy_description = proxy_description
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "team": self.team,
            "stat_type": self.stat_type,
            "season_rank": self.season_rank,
            "l10_rank": self.l10_rank,
            "l5_rank": self.l5_rank,
            "composite_rank": round(self.composite_rank, 1),
            "season_def_rating": round(self.season_def_rating, 1) if self.season_def_rating else None,
            "l10_pts_allowed": round(self.l10_pts_allowed, 1) if self.l10_pts_allowed else None,
            "l5_pts_allowed": round(self.l5_pts_allowed, 1) if self.l5_pts_allowed else None,
            # Backward compatibility
            "season_allowed": round(self.season_def_rating, 1) if self.season_def_rating else None,
            "l10_allowed": round(self.l10_pts_allowed, 1) if self.l10_pts_allowed else None,
            "l5_allowed": round(self.l5_pts_allowed, 1) if self.l5_pts_allowed else None,
            "momentum": self.momentum,
            "trend_alert": self.trend_alert,
            "is_elite": self.composite_rank <= ELITE_RANK_MAX,
            "is_weak": self.composite_rank >= WEAK_RANK_MIN
        }
        
        if self.proxy_type:
            result["using_proxy"] = True
            result["proxy_type"] = self.proxy_type
            result["proxy_label"] = self.proxy_label
            result["proxy_description"] = self.proxy_description
        else:
            result["using_proxy"] = False
        
        return result


# =============================================================================
# DEFENSIVE MOMENTUM SERVICE
# =============================================================================

class DefensiveMomentumService:
    """
    Service for calculating weighted composite defensive rankings with momentum tracking.
    
    Data Sources:
    - Season (50%): BDL /team_season_averages/general?type=defense → def_rating_rank
    - L10 (35%): Calculated from /games endpoint → avg points allowed last 10 games
    - L5 (15%): Calculated from /games endpoint → avg points allowed last 5 games
    """
    
    def __init__(self, db):
        self.db = db
        self.momentum_cache = db[COLL("defensive_momentum_cache", "nba")]
        
        # In-memory cache
        self._cache: Dict[str, Dict[str, DefensiveMomentumProfile]] = {}
        self._cache_updated_at: Optional[datetime] = None
        self._is_building = False
        self._season_data: Dict[str, Dict] = {}
        self._game_data: Dict[str, List[Dict]] = {}
    
    def _get_api_key(self) -> Optional[str]:
        """Get BallDontLie API key from environment."""
        return os.environ.get("BALLDONTLIE_API_KEY") or os.environ.get("BDL_API_KEY")
    
    async def _fetch_season_defensive_ratings(self, season: int = 2025) -> Dict[str, Dict]:
        """
        Fetch official team defensive ratings from BDL team_season_averages endpoint.
        
        Endpoint: GET /team_season_averages/general?season=2025&season_type=regular&type=defense
        
        Returns:
            {team_abbrev: {def_rating: float, def_rating_rank: int, games_played: int}}
        """
        api_key = self._get_api_key()
        if not api_key:
            logger.warning("[Momentum] No BDL API key found")
            return {}
        
        team_data = {}
        
        try:
            async with httpx.AsyncClient(timeout=BDL_TIMEOUT) as client:
                headers = {"Authorization": api_key}
                params = {
                    "season": season,
                    "season_type": "regular",
                    "type": "defense"
                }
                
                response = await client.get(
                    BDL_TEAM_SEASON_AVERAGES,
                    params=params,
                    headers=headers
                )
                
                if response.status_code != 200:
                    logger.error(f"[Momentum] BDL team_season_averages failed: {response.status_code}")
                    logger.error(f"[Momentum] Response: {response.text[:500]}")
                    return {}
                
                data = response.json()
                teams = data.get("data", [])
                
                logger.info(f"[Momentum] Received {len(teams)} teams from season averages endpoint")
                
                for team_entry in teams:
                    team_info = team_entry.get("team", {})
                    abbrev = team_info.get("abbreviation")
                    stats = team_entry.get("stats", {})
                    
                    if not abbrev:
                        continue
                    
                    team_data[abbrev] = {
                        "def_rating": stats.get("def_rating", 115.0),
                        "def_rating_rank": stats.get("def_rating_rank", 15),
                        "games_played": stats.get("gp", 0)
                    }
                
                logger.info(f"[Momentum] Parsed season defensive ratings for {len(team_data)} teams")
                
        except Exception as e:
            logger.error(f"[Momentum] Error fetching season defensive ratings: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        return team_data
    
    async def _fetch_team_games(self, team_id: int, per_page: int = 15) -> List[Dict]:
        """
        Fetch recent games for a specific team.
        
        Returns list of games sorted by date descending.
        """
        api_key = self._get_api_key()
        if not api_key:
            return []
        
        try:
            async with httpx.AsyncClient(timeout=BDL_TIMEOUT) as client:
                headers = {"Authorization": api_key}
                params = {
                    "seasons[]": 2025,
                    "team_ids[]": team_id,
                    "per_page": per_page
                }
                
                response = await client.get(
                    BDL_GAMES,
                    params=params,
                    headers=headers
                )
                
                if response.status_code != 200:
                    logger.warning(f"[Momentum] Games fetch failed for team {team_id}: {response.status_code}")
                    return []
                
                data = response.json()
                games = data.get("data", [])
                
                # Filter for final games and sort by date descending
                final_games = [g for g in games if g.get("status") == "Final"]
                final_games.sort(key=lambda x: x.get("date", ""), reverse=True)
                
                return final_games
                
        except Exception as e:
            logger.error(f"[Momentum] Error fetching games for team {team_id}: {e}")
            return []
    
    def _calculate_pts_allowed_from_games(self, games: List[Dict], team_id: int, limit: int) -> float:
        """
        Calculate average points allowed from a list of games.
        
        For each game:
        - If team is home: pts_allowed = visitor_team_score
        - If team is visitor: pts_allowed = home_team_score
        """
        if not games:
            return 115.0  # League average default
        
        pts_allowed_list = []
        
        for game in games[:limit]:
            home_team_id = game.get("home_team", {}).get("id")
            home_score = game.get("home_team_score", 0)
            visitor_score = game.get("visitor_team_score", 0)
            
            if home_team_id == team_id:
                # Team is home, allowed visitor score
                pts_allowed_list.append(visitor_score)
            else:
                # Team is visitor, allowed home score
                pts_allowed_list.append(home_score)
        
        if not pts_allowed_list:
            return 115.0
        
        return sum(pts_allowed_list) / len(pts_allowed_list)
    
    def _rank_teams_by_value(self, team_values: Dict[str, float]) -> Dict[str, int]:
        """
        Rank teams by value (lower = better = rank 1).
        """
        sorted_teams = sorted(team_values.items(), key=lambda x: x[1])
        return {team: rank + 1 for rank, (team, _) in enumerate(sorted_teams)}
    
    def _calculate_momentum(
        self,
        season_rank: int,
        l10_rank: int,
        l5_rank: int
    ) -> Tuple[str, Optional[str]]:
        """
        Calculate momentum direction and any trend alert.
        
        Negative diff = L5 rank is LOWER (better) than Season
        Positive diff = L5 rank is HIGHER (worse) than Season
        """
        diff = l5_rank - season_rank
        trend_alert = None
        
        if diff <= -SURGE_THRESHOLD:
            momentum = "improving"
            trend_alert = f"SURGE: Defense improved {abs(diff)} spots in L5 vs Season"
        elif diff >= COLLAPSE_THRESHOLD:
            momentum = "regressing"
            trend_alert = f"COLLAPSE: Defense dropped {diff} spots in L5 vs Season"
        else:
            # Check L10 vs L5 for micro-trends
            micro_diff = l5_rank - l10_rank
            if micro_diff <= -3:
                momentum = "improving"
            elif micro_diff >= 3:
                momentum = "regressing"
            else:
                momentum = "stable"
        
        return momentum, trend_alert
    
    def _calculate_composite_rank(
        self,
        season_rank: int,
        l10_rank: int,
        l5_rank: int
    ) -> float:
        """
        Calculate weighted composite rank.
        Formula: (Season * 0.50) + (L10 * 0.35) + (L5 * 0.15)
        """
        return (
            (season_rank * WEIGHT_SEASON) +
            (l10_rank * WEIGHT_L10) +
            (l5_rank * WEIGHT_L5)
        )
    
    async def build_momentum_rankings(self) -> Dict[str, Any]:
        """
        Build complete momentum rankings for all teams.
        
        1. Fetch season defensive ratings from BDL /team_season_averages/general?type=defense
        2. For each team, fetch last 10 games via /games endpoint
        3. Calculate L10 (35%) and L5 (15%) from actual points allowed
        4. Apply weighted composite formula
        """
        if self._is_building:
            logger.warning("[Momentum] Build already in progress, skipping")
            return {"success": False, "reason": "build_in_progress"}
        
        self._is_building = True
        result = {
            "success": True,
            "teams_processed": 0,
            "games_fetched": 0,
            "built_at": datetime.now(timezone.utc).isoformat()
        }
        
        try:
            logger.info("=" * 60)
            logger.info("[MOMENTUM SERVICE] BUILDING DEFENSIVE RANKINGS v4.1")
            logger.info("=" * 60)
            
            # Step 1: Fetch official season defensive ratings
            logger.info("[Momentum] Step 1: Fetching season defensive ratings...")
            season_data = await self._fetch_season_defensive_ratings(season=2025)
            
            if not season_data:
                logger.error("[Momentum] Failed to fetch season defensive ratings")
                result["success"] = False
                result["reason"] = "no_season_data"
                return result
            
            self._season_data = season_data
            logger.info(f"[Momentum] Got season data for {len(season_data)} teams")
            
            # Log top 5 season defenses
            sorted_season = sorted(season_data.items(), key=lambda x: x[1]["def_rating_rank"])
            logger.info("[Momentum] Top 5 Season Defenses (from BDL endpoint):")
            for team, data in sorted_season[:5]:
                logger.info(f"  #{data['def_rating_rank']} {team}: DRtg {data['def_rating']}")
            
            # Step 2: Fetch recent games for each team to calculate L5/L10
            logger.info("[Momentum] Step 2: Fetching L10 games for each team...")
            
            l5_avgs = {}
            l10_avgs = {}
            total_games = 0
            
            # IMPORTANT: Process ALL 30 teams, not just those in season_data
            # BDL API sometimes doesn't return all teams from team_season_averages
            all_teams_to_process = set(ALL_TEAMS)  # All 30 NBA teams
            
            # Add any teams from season_data that might have different abbreviations
            all_teams_to_process.update(season_data.keys())
            
            logger.info(f"[Momentum] Processing {len(all_teams_to_process)} teams...")
            
            for abbrev in all_teams_to_process:
                team_id = ABBREV_TO_ID.get(abbrev)
                if not team_id:
                    logger.warning(f"[Momentum] No team_id mapping for {abbrev}")
                    continue
                
                # If team is missing from season_data, add a default entry
                if abbrev not in season_data:
                    logger.info(f"[Momentum] Adding missing team: {abbrev} (ID: {team_id})")
                    season_data[abbrev] = {
                        "def_rating": 115.0,  # League average default
                        "def_rating_rank": 15,  # Middle rank default
                        "games_played": 0
                    }
                
                games = await self._fetch_team_games(team_id, per_page=15)
                total_games += len(games)
                
                if games:
                    l5_avgs[abbrev] = self._calculate_pts_allowed_from_games(games, team_id, limit=5)
                    l10_avgs[abbrev] = self._calculate_pts_allowed_from_games(games, team_id, limit=10)
                    
                    # Update season_data def_rating if we have game data
                    if abbrev in season_data and season_data[abbrev].get("games_played", 0) == 0:
                        # Calculate approximate def_rating from L10 avg
                        season_data[abbrev]["def_rating"] = l10_avgs[abbrev]
                        season_data[abbrev]["games_played"] = len(games)
                else:
                    # Fallback to league average
                    l5_avgs[abbrev] = 115.0
                    l10_avgs[abbrev] = 115.0
                
                # Small delay to respect rate limits
                await asyncio.sleep(0.05)
            
            result["games_fetched"] = total_games
            logger.info(f"[Momentum] Fetched {total_games} total games for L5/L10 calculation")
            
            # Step 3: Calculate L5 and L10 rankings
            logger.info("[Momentum] Step 3: Calculating L5/L10 rankings...")
            l5_ranks = self._rank_teams_by_value(l5_avgs)
            l10_ranks = self._rank_teams_by_value(l10_avgs)
            
            # Also recalculate season rankings based on L10 for teams that were missing
            # This ensures all 30 teams have consistent rankings
            season_ratings = {team: data.get("def_rating", 115.0) for team, data in season_data.items()}
            season_ranks = self._rank_teams_by_value(season_ratings)
            
            # Update season_data with recalculated ranks
            for team in season_data:
                if team in season_ranks:
                    season_data[team]["def_rating_rank"] = season_ranks[team]
            
            logger.info(f"[Momentum] Calculated rankings for {len(l5_ranks)} teams")
            
            # Log some L5 rankings
            sorted_l5 = sorted(l5_avgs.items(), key=lambda x: x[1])
            logger.info("[Momentum] Top 5 L5 Defenses (by pts allowed):")
            for i, (team, avg) in enumerate(sorted_l5[:5], 1):
                logger.info(f"  #{i} {team}: {avg:.1f} PPG allowed")
            
            # Step 4: Build profiles using 50/35/15 weighted composite
            logger.info("[Momentum] Step 4: Building momentum profiles with 50/35/15 weights...")
            self._cache = {"DRTG": {}}
            
            for team, data in season_data.items():
                season_rank = data.get("def_rating_rank", 15)
                season_def_rating = data.get("def_rating", 115.0)
                
                l10_rank = l10_ranks.get(team, 15)
                l5_rank = l5_ranks.get(team, 15)
                
                # Apply weighted composite formula
                composite = self._calculate_composite_rank(season_rank, l10_rank, l5_rank)
                momentum, trend_alert = self._calculate_momentum(season_rank, l10_rank, l5_rank)
                
                profile = DefensiveMomentumProfile(
                    team=team,
                    stat_type="DRTG",
                    season_rank=season_rank,
                    l10_rank=l10_rank,
                    l5_rank=l5_rank,
                    composite_rank=composite,
                    season_def_rating=season_def_rating,
                    l10_pts_allowed=l10_avgs.get(team, 115.0),
                    l5_pts_allowed=l5_avgs.get(team, 115.0),
                    momentum=momentum,
                    trend_alert=trend_alert,
                    proxy_type="DRTG",
                    proxy_label="Defensive Rating",
                    proxy_description="Official BDL Defensive Rating"
                )
                
                self._cache["DRTG"][team] = profile
            
            result["teams_processed"] = len(season_data)
            self._cache_updated_at = datetime.now(timezone.utc)
            
            # Persist to MongoDB
            await self._persist_cache()
            
            # Log final composite rankings
            logger.info("=" * 50)
            logger.info("[Momentum] FINAL COMPOSITE RANKINGS (50% Season + 35% L10 + 15% L5):")
            sorted_profiles = sorted(
                self._cache["DRTG"].values(),
                key=lambda p: p.composite_rank
            )
            for i, p in enumerate(sorted_profiles[:10], 1):
                marker = "ELITE" if p.composite_rank <= 5 else ""
                logger.info(
                    f"  #{i} {p.team}: Composite {p.composite_rank:.1f} "
                    f"(Season #{p.season_rank}, L10 #{p.l10_rank}, L5 #{p.l5_rank}) {marker}"
                )
            logger.info("=" * 50)
            
        except Exception as e:
            logger.error(f"[Momentum] Build error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            result["success"] = False
            result["error"] = str(e)
        finally:
            self._is_building = False
        
        return result
    
    async def _persist_cache(self):
        """Persist momentum cache to MongoDB."""
        try:
            docs = []
            for stat_type, team_profiles in self._cache.items():
                for team, profile in team_profiles.items():
                    doc = profile.to_dict()
                    doc["updated_at"] = datetime.now(timezone.utc)
                    docs.append(doc)
            
            if docs:
                await self.momentum_cache.delete_many({})
                await self.momentum_cache.insert_many(docs)
                logger.info(f"[Momentum] Persisted {len(docs)} profiles to MongoDB")
        except Exception as e:
            logger.error(f"[Momentum] Persist error: {e}")
    
    async def _load_cache(self):
        """Load momentum cache from MongoDB."""
        try:
            cursor = self.momentum_cache.find({}, {"_id": 0})
            docs = await cursor.to_list(length=None)
            
            self._cache = {}
            for doc in docs:
                stat_type = doc.get("stat_type", "DRTG")
                team = doc.get("team")
                
                if not team:
                    continue
                
                cache_key = "DRTG"
                
                if cache_key not in self._cache:
                    self._cache[cache_key] = {}
                
                self._cache[cache_key][team] = DefensiveMomentumProfile(
                    team=team,
                    stat_type=stat_type,
                    season_rank=doc.get("season_rank", 15),
                    l10_rank=doc.get("l10_rank", 15),
                    l5_rank=doc.get("l5_rank", 15),
                    composite_rank=doc.get("composite_rank", 15.0),
                    season_def_rating=doc.get("season_def_rating") or doc.get("season_allowed", 115.0),
                    l10_pts_allowed=doc.get("l10_pts_allowed") or doc.get("l10_allowed", 115.0),
                    l5_pts_allowed=doc.get("l5_pts_allowed") or doc.get("l5_allowed", 115.0),
                    momentum=doc.get("momentum", "stable"),
                    trend_alert=doc.get("trend_alert"),
                    proxy_type=doc.get("proxy_type", "DRTG"),
                    proxy_label=doc.get("proxy_label", "Defensive Rating"),
                    proxy_description=doc.get("proxy_description")
                )
            
            if docs:
                self._cache_updated_at = datetime.now(timezone.utc)
                logger.info(f"[Momentum] Loaded {len(docs)} profiles from MongoDB")
        except Exception as e:
            logger.error(f"[Momentum] Load error: {e}")
    
    async def ensure_cache(self):
        """Ensure cache is loaded, build if needed."""
        if not self._cache or self._cache_updated_at is None:
            await self._load_cache()
        
        if not self._cache:
            await self.build_momentum_rankings()
    
    def get_momentum_profile(
        self,
        opponent_team: str,
        stat_type: str
    ) -> Optional[DefensiveMomentumProfile]:
        """
        Get momentum profile for an opponent/stat combination.
        
        All stat types use Defensive Rating as the base metric.
        """
        stat_upper = stat_type.upper().strip()
        proxy_config = STAT_PROXY_MAP.get(stat_upper, DEFAULT_PROXY)
        
        cache_key = "DRTG"
        
        if cache_key not in self._cache:
            return None
        
        base_profile = self._cache[cache_key].get(opponent_team)
        if not base_profile:
            return None
        
        return DefensiveMomentumProfile(
            team=base_profile.team,
            stat_type=stat_upper,
            season_rank=base_profile.season_rank,
            l10_rank=base_profile.l10_rank,
            l5_rank=base_profile.l5_rank,
            composite_rank=base_profile.composite_rank,
            season_def_rating=base_profile.season_def_rating,
            l10_pts_allowed=base_profile.l10_pts_allowed,
            l5_pts_allowed=base_profile.l5_pts_allowed,
            momentum=base_profile.momentum,
            trend_alert=base_profile.trend_alert,
            proxy_type=proxy_config["proxy"],
            proxy_label=proxy_config["label"],
            proxy_description=proxy_config["description"]
        )
    
    def calculate_momentum_modifier(
        self,
        opponent_team: str,
        stat_type: str
    ) -> Tuple[float, Optional[Dict[str, Any]]]:
        """
        Calculate Ferrari Score modifier based on defensive momentum.
        
        Returns:
            (modifier, momentum_data)
            
        Modifier:
        - Elite Composite (Rank 1-5): -15 penalty to "Over" props
        - Weak Composite (Rank 25-30): +15 boost to "Over" props
        - Middle (6-24): 0 modifier
        """
        profile = self.get_momentum_profile(opponent_team, stat_type)
        
        if not profile:
            return 0.0, None
        
        composite = profile.composite_rank
        
        if composite <= ELITE_RANK_MAX:
            modifier = ELITE_DEFENSE_PENALTY
        elif composite >= WEAK_RANK_MIN:
            modifier = WEAK_DEFENSE_BOOST
        else:
            modifier = 0.0
        
        momentum_data = profile.to_dict()
        momentum_data["modifier"] = modifier
        momentum_data["tooltip"] = self._generate_tooltip(profile)
        
        return modifier, momentum_data
    
    def _generate_tooltip(self, profile: DefensiveMomentumProfile) -> str:
        """Generate tooltip text showing the math."""
        return (
            f"{int(WEIGHT_SEASON * 100)}% Season (#{profile.season_rank}, DRtg: {profile.season_def_rating:.1f}) | "
            f"{int(WEIGHT_L10 * 100)}% L10 (#{profile.l10_rank}, {profile.l10_pts_allowed:.1f} PPG) | "
            f"{int(WEIGHT_L5 * 100)}% L5 (#{profile.l5_rank}, {profile.l5_pts_allowed:.1f} PPG)"
        )
    
    def get_all_team_momentum(self, stat_type: str = "DRTG") -> List[Dict[str, Any]]:
        """Get momentum profiles for all teams, sorted by composite rank."""
        cache_key = "DRTG"
        
        if cache_key not in self._cache:
            return []
        
        return [
            profile.to_dict()
            for profile in sorted(
                self._cache[cache_key].values(),
                key=lambda p: p.composite_rank
            )
        ]
    
    async def get_status(self) -> Dict[str, Any]:
        """Get service status."""
        top_teams = []
        if self._cache and "DRTG" in self._cache:
            sorted_profiles = sorted(
                self._cache["DRTG"].values(),
                key=lambda p: p.composite_rank
            )[:5]
            top_teams = [
                {
                    "team": p.team,
                    "composite": round(p.composite_rank, 1),
                    "season_rank": p.season_rank,
                    "l10_rank": p.l10_rank,
                    "l5_rank": p.l5_rank
                }
                for p in sorted_profiles
            ]
        
        return {
            "cache_loaded": bool(self._cache),
            "stat_types_cached": list(self._cache.keys()) if self._cache else [],
            "teams_cached": len(self._cache.get("DRTG", {})) if self._cache else 0,
            "cache_updated_at": self._cache_updated_at.isoformat() if self._cache_updated_at else None,
            "is_building": self._is_building,
            "top_5_defenses": top_teams,
            "formula": "Composite = (Season * 50%) + (L10 * 35%) + (L5 * 15%)",
            "weights": {
                "season": WEIGHT_SEASON,
                "l10": WEIGHT_L10,
                "l5": WEIGHT_L5
            },
            "modifiers": {
                "elite_penalty": ELITE_DEFENSE_PENALTY,
                "weak_boost": WEAK_DEFENSE_BOOST,
                "elite_threshold": ELITE_RANK_MAX,
                "weak_threshold": WEAK_RANK_MIN
            }
        }


# =============================================================================
# SINGLETON
# =============================================================================

_momentum_service: Optional[DefensiveMomentumService] = None


def get_momentum_service(db=None) -> DefensiveMomentumService:
    """Get or create the DefensiveMomentumService singleton."""
    global _momentum_service
    if _momentum_service is None and db is not None:
        _momentum_service = DefensiveMomentumService(db)
    return _momentum_service
