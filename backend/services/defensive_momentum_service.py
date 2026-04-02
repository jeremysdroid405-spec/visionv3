"""
Defensive Momentum Service
===========================
Weighted Composite DvP scoring with momentum tracking.

FORMULA: Composite_Rank = (Season_Rank * 0.50) + (L10_Rank * 0.35) + (L5_Rank * 0.15)

Features:
- Season, L10, L5 defensive rankings calculated from real game data
- Weighted composite rank for more accurate matchup assessment
- Momentum tracking (improving vs regressing defenses)
- Trend alerts when L5 significantly diverges from Season
- Ferrari Score modifiers: Elite (1-5) = -15, Weak (25-30) = +15

Data Source: BallDontLie API box_scores endpoint

Author: PropVision AI
Version: 2.0.0
"""
import logging
import os
import httpx
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import asyncio

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
BDL_BOX_SCORES = f"{BDL_API_BASE}/box_scores"
BDL_TIMEOUT = 30.0

# Cache TTL
MOMENTUM_CACHE_TTL_MINUTES = 60

# Team ID mapping
BDL_TEAM_ID_MAP = {
    1: "ATL", 2: "BOS", 3: "BKN", 4: "CHA", 5: "CHI", 6: "CLE",
    7: "DAL", 8: "DEN", 9: "DET", 10: "GSW", 11: "HOU", 12: "IND",
    13: "LAC", 14: "LAL", 15: "MEM", 16: "MIA", 17: "MIL", 18: "MIN",
    19: "NOP", 20: "NYK", 21: "OKC", 22: "ORL", 23: "PHI", 24: "PHX",
    25: "POR", 26: "SAC", 27: "SAS", 28: "TOR", 29: "UTA", 30: "WAS"
}

ALL_TEAMS = list(BDL_TEAM_ID_MAP.values())


# =============================================================================
# MOMENTUM DATA STRUCTURES
# =============================================================================

class DefensiveMomentumProfile:
    """Represents a team's defensive momentum profile for a specific stat."""
    
    def __init__(
        self,
        team: str,
        stat_type: str,
        season_rank: int,
        l10_rank: int,
        l5_rank: int,
        composite_rank: float,
        season_allowed: float,
        l10_allowed: float,
        l5_allowed: float,
        momentum: str,  # "improving", "stable", "regressing"
        trend_alert: Optional[str] = None
    ):
        self.team = team
        self.stat_type = stat_type
        self.season_rank = season_rank
        self.l10_rank = l10_rank
        self.l5_rank = l5_rank
        self.composite_rank = composite_rank
        self.season_allowed = season_allowed
        self.l10_allowed = l10_allowed
        self.l5_allowed = l5_allowed
        self.momentum = momentum
        self.trend_alert = trend_alert
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "team": self.team,
            "stat_type": self.stat_type,
            "season_rank": self.season_rank,
            "l10_rank": self.l10_rank,
            "l5_rank": self.l5_rank,
            "composite_rank": round(self.composite_rank, 1),
            "season_allowed": round(self.season_allowed, 1) if self.season_allowed else None,
            "l10_allowed": round(self.l10_allowed, 1) if self.l10_allowed else None,
            "l5_allowed": round(self.l5_allowed, 1) if self.l5_allowed else None,
            "momentum": self.momentum,
            "trend_alert": self.trend_alert,
            "is_elite": self.composite_rank <= ELITE_RANK_MAX,
            "is_weak": self.composite_rank >= WEAK_RANK_MIN
        }


# =============================================================================
# DEFENSIVE MOMENTUM SERVICE
# =============================================================================

class DefensiveMomentumService:
    """
    Service for calculating weighted composite DvP rankings with momentum tracking.
    
    Uses BallDontLie box_scores API to get game-by-game results and calculate
    how many points each team allows over different time windows (Season, L10, L5).
    """
    
    def __init__(self, db):
        self.db = db
        self.momentum_cache = db.defensive_momentum_cache
        
        # In-memory cache
        self._cache: Dict[str, Dict[str, DefensiveMomentumProfile]] = {}
        self._cache_updated_at: Optional[datetime] = None
        self._is_building = False
        self._game_data: Dict[str, List[Dict]] = {}  # {team: [games]}
    
    def _get_api_key(self) -> Optional[str]:
        """Get BallDontLie API key from environment."""
        return os.environ.get("BALLDONTLIE_API_KEY") or os.environ.get("BDL_API_KEY")
    
    async def _fetch_box_scores(self, start_date: str, end_date: str) -> List[Dict]:
        """
        Fetch box scores from BallDontLie API for a date range.
        
        Args:
            start_date: YYYY-MM-DD format
            end_date: YYYY-MM-DD format
            
        Returns:
            List of game data with scores
        """
        api_key = self._get_api_key()
        if not api_key:
            logger.warning("[Momentum] No BDL API key found")
            return []
        
        all_games = []
        
        try:
            # Parse dates
            start = datetime.strptime(start_date, "%Y-%m-%d").date()
            end = datetime.strptime(end_date, "%Y-%m-%d").date()
            
            async with httpx.AsyncClient(timeout=BDL_TIMEOUT) as client:
                headers = {"Authorization": api_key}
                
                # Fetch in batches of 7 days
                current = start
                while current <= end:
                    batch_end = min(current + timedelta(days=6), end)
                    
                    # Fetch each day in the batch
                    for day_offset in range((batch_end - current).days + 1):
                        fetch_date = current + timedelta(days=day_offset)
                        date_str = fetch_date.strftime("%Y-%m-%d")
                        
                        cursor = None
                        for page in range(10):  # Max 10 pages per day
                            params = {"date": date_str, "per_page": 100}
                            if cursor:
                                params["cursor"] = cursor
                            
                            response = await client.get(
                                BDL_BOX_SCORES,
                                params=params,
                                headers=headers
                            )
                            
                            if response.status_code != 200:
                                logger.debug(f"[Momentum] BDL API returned {response.status_code} for {date_str}")
                                break
                            
                            data = response.json()
                            games = data.get("data", [])
                            all_games.extend(games)
                            
                            meta = data.get("meta", {})
                            next_cursor = meta.get("next_cursor")
                            
                            if not next_cursor or len(games) == 0:
                                break
                            cursor = next_cursor
                    
                    current = batch_end + timedelta(days=1)
                    
                    # Rate limit between batches
                    await asyncio.sleep(0.1)
                    
            logger.info(f"[Momentum] Fetched {len(all_games)} games from {start_date} to {end_date}")
            return all_games
            
        except Exception as e:
            logger.error(f"[Momentum] Error fetching box scores: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    def _process_games_to_defensive_stats(self, games: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Process box score games into per-team defensive stats.
        
        For each game, each team's defense is measured by what the OPPONENT scored.
        
        Returns:
            {team_abbrev: [{date, pts_allowed, ...}, ...]}
        """
        team_games = defaultdict(list)
        
        for game in games:
            if game.get("status") != "Final":
                continue
                
            date = game.get("date")
            home_team = game.get("home_team", {})
            visitor_team = game.get("visitor_team", {})
            
            home_abbrev = home_team.get("abbreviation")
            visitor_abbrev = visitor_team.get("abbreviation")
            
            home_score = game.get("home_team_score", 0)
            visitor_score = game.get("visitor_team_score", 0)
            
            if not home_abbrev or not visitor_abbrev:
                continue
            
            # Home team allowed visitor_score points (home team's defense)
            team_games[home_abbrev].append({
                "date": date,
                "pts_allowed": visitor_score,
                "opponent": visitor_abbrev
            })
            
            # Visitor team allowed home_score points (visitor team's defense)
            team_games[visitor_abbrev].append({
                "date": date,
                "pts_allowed": home_score,
                "opponent": home_abbrev
            })
        
        # Sort each team's games by date descending
        for team in team_games:
            team_games[team] = sorted(
                team_games[team],
                key=lambda g: g.get("date", ""),
                reverse=True
            )
        
        return dict(team_games)
    
    def _calculate_avg_allowed(self, games: List[Dict], limit: Optional[int] = None) -> float:
        """Calculate average points allowed over N games."""
        if not games:
            return 115.0  # Default avg
        
        subset = games[:limit] if limit else games
        values = [g.get("pts_allowed", 0) for g in subset]
        
        if not values:
            return 115.0
        
        return sum(values) / len(values)
    
    def _rank_teams_by_stat(self, team_values: Dict[str, float]) -> Dict[str, int]:
        """
        Rank teams by allowed points.
        Lower allowed = better defense = rank 1.
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
        
        Returns:
            (momentum_direction, trend_alert)
        """
        # Negative diff means L5 rank is LOWER (better) than Season
        # Positive diff means L5 rank is HIGHER (worse) than Season
        diff = l5_rank - season_rank
        
        trend_alert = None
        
        if diff <= -SURGE_THRESHOLD:
            # L5 rank is at least 10 spots BETTER than season
            momentum = "improving"
            trend_alert = f"SURGE ALERT: Defense improved {abs(diff)} spots in last 5 games."
        elif diff >= COLLAPSE_THRESHOLD:
            # L5 rank is at least 10 spots WORSE than season
            momentum = "regressing"
            trend_alert = f"TREND ALERT: Defense collapsed {diff} spots in last 5 games."
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
        Fetches game data from BDL API and calculates Season/L10/L5 rankings.
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
            logger.info("[MOMENTUM SERVICE] BUILDING DEFENSIVE RANKINGS FROM BDL")
            logger.info("=" * 60)
            
            # Fetch last 45 days of games (enough for L10/L5 + season context)
            today = datetime.now(timezone.utc).date()
            start_date = (today - timedelta(days=45)).strftime("%Y-%m-%d")
            end_date = today.strftime("%Y-%m-%d")
            
            # Fetch games
            games = await self._fetch_box_scores(
                start_date=start_date,
                end_date=end_date
            )
            
            if not games:
                logger.warning("[Momentum] No games fetched from BDL API")
                result["success"] = False
                result["reason"] = "no_games_fetched"
                return result
            
            result["games_fetched"] = len(games)
            
            # Process games into per-team defensive stats
            team_games = self._process_games_to_defensive_stats(games)
            
            if not team_games:
                logger.warning("[Momentum] No team game data extracted")
                result["success"] = False
                result["reason"] = "no_team_data"
                return result
            
            # Calculate averages for each window
            season_avgs = {}
            l10_avgs = {}
            l5_avgs = {}
            
            for team, games_list in team_games.items():
                season_avgs[team] = self._calculate_avg_allowed(games_list)
                l10_avgs[team] = self._calculate_avg_allowed(games_list, limit=10)
                l5_avgs[team] = self._calculate_avg_allowed(games_list, limit=5)
            
            # Calculate rankings for each window
            season_ranks = self._rank_teams_by_stat(season_avgs)
            l10_ranks = self._rank_teams_by_stat(l10_avgs)
            l5_ranks = self._rank_teams_by_stat(l5_avgs)
            
            # Clear old cache and build new profiles
            self._cache = {"PTS": {}}
            
            for team in team_games.keys():
                s_rank = season_ranks.get(team, 15)
                l10_rank = l10_ranks.get(team, 15)
                l5_rank = l5_ranks.get(team, 15)
                
                composite = self._calculate_composite_rank(s_rank, l10_rank, l5_rank)
                momentum, trend_alert = self._calculate_momentum(s_rank, l10_rank, l5_rank)
                
                profile = DefensiveMomentumProfile(
                    team=team,
                    stat_type="PTS",
                    season_rank=s_rank,
                    l10_rank=l10_rank,
                    l5_rank=l5_rank,
                    composite_rank=composite,
                    season_allowed=season_avgs.get(team),
                    l10_allowed=l10_avgs.get(team),
                    l5_allowed=l5_avgs.get(team),
                    momentum=momentum,
                    trend_alert=trend_alert
                )
                
                self._cache["PTS"][team] = profile
                
                # Log notable teams
                if trend_alert:
                    logger.info(f"[Momentum] {team}: Season #{s_rank}, L10 #{l10_rank}, L5 #{l5_rank} - {momentum.upper()}")
            
            result["teams_processed"] = len(team_games)
            self._cache_updated_at = datetime.now(timezone.utc)
            self._game_data = team_games
            
            # Persist to MongoDB
            await self._persist_cache()
            
            logger.info(f"[Momentum] Built rankings for {result['teams_processed']} teams from {result['games_fetched']} games")
            
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
                stat_type = doc.get("stat_type")
                team = doc.get("team")
                
                if not stat_type or not team:
                    continue
                
                if stat_type not in self._cache:
                    self._cache[stat_type] = {}
                
                self._cache[stat_type][team] = DefensiveMomentumProfile(
                    team=team,
                    stat_type=stat_type,
                    season_rank=doc.get("season_rank", 15),
                    l10_rank=doc.get("l10_rank", 15),
                    l5_rank=doc.get("l5_rank", 15),
                    composite_rank=doc.get("composite_rank", 15.0),
                    season_allowed=doc.get("season_allowed"),
                    l10_allowed=doc.get("l10_allowed"),
                    l5_allowed=doc.get("l5_allowed"),
                    momentum=doc.get("momentum", "stable"),
                    trend_alert=doc.get("trend_alert")
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
        
        # If still no cache, build from scratch
        if not self._cache:
            await self.build_momentum_rankings()
    
    def get_momentum_profile(
        self,
        opponent_team: str,
        stat_type: str
    ) -> Optional[DefensiveMomentumProfile]:
        """
        Get momentum profile for an opponent/stat combination.
        
        Args:
            opponent_team: 3-letter team abbreviation
            stat_type: Stat type (PTS, AST, REB, 3PM, etc.)
        
        Returns:
            DefensiveMomentumProfile or None
        """
        stat_upper = stat_type.upper()
        
        # Normalize stat type
        if stat_upper in ["POINTS", "POINTS_ALTERNATE"]:
            stat_upper = "PTS"
        elif stat_upper in ["ASSISTS", "ASSISTS_ALTERNATE"]:
            stat_upper = "AST"
        elif stat_upper in ["REBOUNDS", "REBOUNDS_ALTERNATE"]:
            stat_upper = "REB"
        elif stat_upper in ["THREES", "THREES_ALTERNATE", "THREE_POINTERS_MADE"]:
            stat_upper = "3PM"
        elif stat_upper in ["STEALS", "STEALS_ALTERNATE"]:
            stat_upper = "STL"
        elif stat_upper in ["BLOCKS", "BLOCKS_ALTERNATE"]:
            stat_upper = "BLK"
        
        if stat_upper not in self._cache:
            return None
        
        return self._cache[stat_upper].get(opponent_team)
    
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
        
        # Calculate modifier based on composite rank
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
            f"{int(WEIGHT_SEASON * 100)}% Season (Rank {profile.season_rank}) | "
            f"{int(WEIGHT_L10 * 100)}% L10 (Rank {profile.l10_rank}) | "
            f"{int(WEIGHT_L5 * 100)}% L5 (Rank {profile.l5_rank})"
        )
    
    def get_all_team_momentum(self, stat_type: str = "PTS") -> List[Dict[str, Any]]:
        """Get momentum profiles for all teams for a given stat."""
        if stat_type not in self._cache:
            return []
        
        return [
            profile.to_dict()
            for profile in sorted(
                self._cache[stat_type].values(),
                key=lambda p: p.composite_rank
            )
        ]
    
    async def get_status(self) -> Dict[str, Any]:
        """Get service status."""
        return {
            "cache_loaded": bool(self._cache),
            "stat_types_cached": list(self._cache.keys()),
            "teams_cached": len(self._cache.get("PTS", {})) if self._cache else 0,
            "cache_updated_at": self._cache_updated_at.isoformat() if self._cache_updated_at else None,
            "is_building": self._is_building,
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
