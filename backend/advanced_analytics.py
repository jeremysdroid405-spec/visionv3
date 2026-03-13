"""
Advanced Analytics Engine - Daily Insights Calculator

This module calculates and stores advanced predictive metrics for NBA player props:
- Schedule Density Factor (fatigue from B2B, 3-in-4 games)
- Pace Adjustment Factor (matchup tempo impact)
- Usage Ripple Effect (redistribution when key players are out)
- Volatility Score (consistency rating from recent games)
- Template-based Insight Summaries

Data Sources:
- nba_api: Schedule, pace data, player stats
- Tank01: Injury reports, player status
- BallDontLie: Historical game logs

Storage: Supabase daily_insights table
"""

import os
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional, Tuple
import statistics
import httpx

# NBA API imports
try:
    from nba_api.stats.endpoints import (
        playergamelog, 
        leaguegamefinder,
        teamgamelogs,
        scoreboardv2
    )
    from nba_api.stats.static import players as nba_players, teams as nba_teams
    NBA_API_AVAILABLE = True
except ImportError:
    NBA_API_AVAILABLE = False

# Supabase
try:
    from supabase import create_client, Client
    SUPABASE_AVAILABLE = True
except ImportError:
    SUPABASE_AVAILABLE = False

logger = logging.getLogger(__name__)

# Constants
LEAGUE_AVG_PACE_2026 = 100.0  # League average pace (possessions per 48 min)
BACK_TO_BACK_PENALTY = 0.95   # 5% reduction for B2B
THREE_IN_FOUR_PENALTY = 0.92  # 8% reduction for 3-in-4
HIGH_USAGE_THRESHOLD = 25.0   # Player considered high-usage if >25% usage rate
USAGE_REDISTRIBUTION_BASE = 12.0  # Base % increase when star is out
VOLATILITY_HIGH_THRESHOLD = 10.0  # Std dev > 10 = high volatility
VOLATILITY_MED_THRESHOLD = 5.0    # Std dev > 5 = medium volatility

# Tank01 API config
TANK01_API_KEY = os.environ.get("TANK01_API_KEY", "")
TANK01_HOST = "tank01-fantasy-stats.p.rapidapi.com"


class AdvancedAnalyticsEngine:
    """
    Calculates advanced predictive metrics for NBA player props.
    Stores results in Supabase daily_insights table.
    """
    
    def __init__(self, supabase_client: Optional[Client] = None):
        """Initialize with optional Supabase client."""
        self.supabase = supabase_client
        self._team_pace_cache: Dict[str, float] = {}
        self._injury_cache: Dict[str, Dict] = {}
        self._schedule_cache: Dict[str, List[Dict]] = {}
        
        # Team name mappings
        self.TEAM_ABBREV_TO_ID = {
            "ATL": 1610612737, "BOS": 1610612738, "BKN": 1610612751,
            "CHA": 1610612766, "CHI": 1610612741, "CLE": 1610612739,
            "DAL": 1610612742, "DEN": 1610612743, "DET": 1610612765,
            "GSW": 1610612744, "HOU": 1610612745, "IND": 1610612754,
            "LAC": 1610612746, "LAL": 1610612747, "MEM": 1610612763,
            "MIA": 1610612748, "MIL": 1610612749, "MIN": 1610612750,
            "NOP": 1610612740, "NYK": 1610612752, "OKC": 1610612760,
            "ORL": 1610612753, "PHI": 1610612755, "PHX": 1610612756,
            "POR": 1610612757, "SAC": 1610612758, "SAS": 1610612759,
            "TOR": 1610612761, "UTA": 1610612762, "WAS": 1610612764
        }
    
    async def initialize_supabase_schema(self) -> Dict[str, Any]:
        """
        Create the daily_insights table in Supabase if it doesn't exist.
        Returns status of schema creation.
        """
        if not self.supabase:
            return {"success": False, "error": "Supabase client not initialized"}
        
        try:
            # Check if table exists by trying to query it
            result = self.supabase.table("daily_insights").select("id").limit(1).execute()
            logger.info("[ANALYTICS] daily_insights table exists")
            return {"success": True, "message": "Table already exists", "exists": True}
        except Exception as e:
            logger.info(f"[ANALYTICS] Table may not exist, attempting to create: {e}")
            
            # Table doesn't exist - note: Supabase doesn't support CREATE TABLE via client
            # The schema should be created via Supabase dashboard or migration
            return {
                "success": False, 
                "error": "Table doesn't exist. Please create via Supabase dashboard.",
                "schema": self._get_schema_sql()
            }
    
    def _get_schema_sql(self) -> str:
        """Return the SQL schema for daily_insights table."""
        return """
CREATE TABLE IF NOT EXISTS public.daily_insights (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  player_id text NOT NULL,
  player_name text NOT NULL,
  team text,
  game_id text NOT NULL,
  game_date date NOT NULL,
  opponent text,
  last_updated timestamptz DEFAULT now(),
  
  -- Advanced Analytics Data
  schedule_density_factor float4 DEFAULT 1.0,
  pace_adjustment_factor float4 DEFAULT 1.0,
  usage_bump_percent float4 DEFAULT 0.0,
  volatility_score text DEFAULT 'Low',
  volatility_stddev float4,
  
  -- Template Strings
  insight_summary text,
  ai_confidence_rating int2 DEFAULT 50,
  
  -- Metadata
  is_goblin_certified boolean DEFAULT false,
  is_demon_certified boolean DEFAULT false,
  days_rest int2 DEFAULT 1,
  is_back_to_back boolean DEFAULT false,
  is_three_in_four boolean DEFAULT false,
  injured_teammates text[],
  
  UNIQUE(player_id, game_id)
);

CREATE INDEX IF NOT EXISTS idx_daily_insights_player_id ON public.daily_insights (player_id);
CREATE INDEX IF NOT EXISTS idx_daily_insights_game_date ON public.daily_insights (game_date);
CREATE INDEX IF NOT EXISTS idx_daily_insights_team ON public.daily_insights (team);
"""
    
    # ==================== SCHEDULE DENSITY ====================
    
    async def calculate_schedule_density(
        self, 
        team: str, 
        game_date: datetime
    ) -> Tuple[float, bool, bool, int]:
        """
        Calculate schedule density factor based on recent games.
        
        Returns:
            (density_factor, is_back_to_back, is_three_in_four, days_rest)
        """
        if not NBA_API_AVAILABLE:
            logger.warning("[ANALYTICS] nba_api not available for schedule density")
            return (1.0, False, False, 2)
        
        try:
            team_id = self.TEAM_ABBREV_TO_ID.get(team)
            if not team_id:
                return (1.0, False, False, 2)
            
            # Get team's recent games
            import time
            time.sleep(0.6)  # Rate limiting
            
            game_finder = leaguegamefinder.LeagueGameFinder(
                team_id_nullable=team_id,
                season_nullable="2024-25",
                season_type_nullable="Regular Season"
            )
            games_df = game_finder.get_data_frames()[0]
            
            if games_df.empty:
                return (1.0, False, False, 2)
            
            # Parse game dates
            games_df['GAME_DATE'] = pd.to_datetime(games_df['GAME_DATE'])
            games_df = games_df.sort_values('GAME_DATE', ascending=False)
            
            # Find games in last 4 days
            four_days_ago = game_date - timedelta(days=4)
            recent_games = games_df[games_df['GAME_DATE'] >= four_days_ago]
            
            # Calculate metrics
            is_back_to_back = False
            is_three_in_four = False
            days_rest = 2  # Default
            
            if len(recent_games) > 0:
                last_game_date = recent_games['GAME_DATE'].iloc[0]
                days_rest = (game_date - last_game_date).days
                
                if days_rest == 1:
                    is_back_to_back = True
                
                # Count games in last 4 days
                games_in_window = len(recent_games[recent_games['GAME_DATE'] > four_days_ago])
                if games_in_window >= 3:
                    is_three_in_four = True
            
            # Calculate density factor
            density_factor = 1.0
            if is_three_in_four:
                density_factor = THREE_IN_FOUR_PENALTY
            elif is_back_to_back:
                density_factor = BACK_TO_BACK_PENALTY
            
            return (density_factor, is_back_to_back, is_three_in_four, days_rest)
            
        except Exception as e:
            logger.error(f"[ANALYTICS] Error calculating schedule density: {e}")
            return (1.0, False, False, 2)
    
    # ==================== PACE FACTOR ====================
    
    async def calculate_pace_factor(
        self, 
        team: str, 
        opponent: str
    ) -> float:
        """
        Calculate pace adjustment factor based on team and opponent tempo.
        
        Formula: (Team_Pace + Opponent_Pace) / (2 * League_Avg_Pace)
        """
        try:
            team_pace = await self._get_team_pace(team)
            opp_pace = await self._get_team_pace(opponent)
            
            if team_pace and opp_pace:
                combined_pace = (team_pace + opp_pace) / 2
                pace_factor = combined_pace / LEAGUE_AVG_PACE_2026
                return round(pace_factor, 3)
            
            return 1.0
            
        except Exception as e:
            logger.error(f"[ANALYTICS] Error calculating pace factor: {e}")
            return 1.0
    
    async def _get_team_pace(self, team: str) -> Optional[float]:
        """Get team's pace (possessions per 48 minutes)."""
        # Check cache first
        if team in self._team_pace_cache:
            return self._team_pace_cache[team]
        
        # Hardcoded 2024-25 pace values (from NBA.com)
        # Updated for 2026 projections
        TEAM_PACE_2026 = {
            "IND": 103.5, "ATL": 102.8, "MIL": 102.2, "SAC": 101.9, "MIN": 101.5,
            "DEN": 101.2, "BOS": 100.8, "PHX": 100.6, "GSW": 100.4, "LAL": 100.2,
            "DAL": 100.0, "OKC": 99.8, "NOP": 99.6, "POR": 99.4, "HOU": 99.2,
            "TOR": 99.0, "CHI": 98.8, "WAS": 98.6, "BKN": 98.4, "CHA": 98.2,
            "SAS": 98.0, "UTA": 97.8, "DET": 97.6, "ORL": 97.4, "MEM": 97.2,
            "PHI": 97.0, "CLE": 96.8, "MIA": 96.6, "NYK": 96.4, "LAC": 96.2
        }
        
        pace = TEAM_PACE_2026.get(team, LEAGUE_AVG_PACE_2026)
        self._team_pace_cache[team] = pace
        return pace
    
    # ==================== USAGE RIPPLE EFFECT ====================
    
    async def calculate_usage_bump(
        self, 
        player_name: str, 
        team: str,
        injured_teammates: List[str]
    ) -> Tuple[float, List[str]]:
        """
        Calculate usage bump when high-usage teammates are out.
        
        Returns:
            (usage_bump_percent, list of injured high-usage teammates)
        """
        try:
            # Get list of high-usage players on the team
            high_usage_players = await self._get_team_high_usage_players(team)
            
            # Check which high-usage players are injured
            injured_high_usage = []
            for injured in injured_teammates:
                if injured in high_usage_players and injured != player_name:
                    injured_high_usage.append(injured)
            
            if not injured_high_usage:
                return (0.0, [])
            
            # Calculate usage bump
            # Base 12% per star out, diminishing returns for multiple
            usage_bump = 0.0
            for i, _ in enumerate(injured_high_usage):
                multiplier = 1.0 / (i + 1)  # Diminishing returns
                usage_bump += USAGE_REDISTRIBUTION_BASE * multiplier
            
            return (round(usage_bump, 1), injured_high_usage)
            
        except Exception as e:
            logger.error(f"[ANALYTICS] Error calculating usage bump: {e}")
            return (0.0, [])
    
    async def _get_team_high_usage_players(self, team: str) -> List[str]:
        """Get list of high-usage players (>25% usage rate) on a team."""
        # Hardcoded high-usage players by team for 2024-25
        HIGH_USAGE_BY_TEAM = {
            "ATL": ["Trae Young", "Dejounte Murray"],
            "BOS": ["Jayson Tatum", "Jaylen Brown", "Derrick White"],
            "BKN": ["Cam Thomas", "Dennis Schroder"],
            "CHA": ["LaMelo Ball", "Brandon Miller"],
            "CHI": ["Zach LaVine", "Coby White"],
            "CLE": ["Donovan Mitchell", "Darius Garland"],
            "DAL": ["Luka Doncic", "Kyrie Irving"],
            "DEN": ["Nikola Jokic", "Jamal Murray"],
            "DET": ["Cade Cunningham", "Jaden Ivey"],
            "GSW": ["Stephen Curry", "Andrew Wiggins"],
            "HOU": ["Jalen Green", "Alperen Sengun", "Kevin Durant"],
            "IND": ["Tyrese Haliburton", "Pascal Siakam"],
            "LAC": ["James Harden", "Kawhi Leonard"],
            "LAL": ["LeBron James", "Anthony Davis"],
            "MEM": ["Ja Morant", "Desmond Bane"],
            "MIA": ["Jimmy Butler", "Bam Adebayo"],
            "MIL": ["Giannis Antetokounmpo", "Damian Lillard"],
            "MIN": ["Anthony Edwards", "Karl-Anthony Towns"],
            "NOP": ["Zion Williamson", "Brandon Ingram", "Trey Murphy III"],
            "NYK": ["Jalen Brunson", "Julius Randle"],
            "OKC": ["Shai Gilgeous-Alexander", "Jalen Williams"],
            "ORL": ["Paolo Banchero", "Franz Wagner"],
            "PHI": ["Joel Embiid", "Tyrese Maxey"],
            "PHX": ["Devin Booker", "Bradley Beal"],
            "POR": ["Anfernee Simons", "Scoot Henderson"],
            "SAC": ["De'Aaron Fox", "Domantas Sabonis"],
            "SAS": ["Victor Wembanyama", "Devin Vassell"],
            "TOR": ["Scottie Barnes", "RJ Barrett"],
            "UTA": ["Lauri Markkanen", "Collin Sexton"],
            "WAS": ["Jordan Poole", "Kyle Kuzma"]
        }
        
        return HIGH_USAGE_BY_TEAM.get(team, [])
    
    async def fetch_team_injuries(self, team: str) -> List[str]:
        """Fetch current injuries for a team from Tank01 API."""
        if not TANK01_API_KEY:
            return []
        
        try:
            # Use Tank01 injury endpoint
            url = f"https://{TANK01_HOST}/getNBATeams"
            headers = {
                "X-RapidAPI-Key": TANK01_API_KEY,
                "X-RapidAPI-Host": TANK01_HOST
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, timeout=15.0)
                
                if response.status_code == 200:
                    data = response.json()
                    # Parse injury data from response
                    # This is simplified - actual implementation depends on Tank01 response format
                    injured_players = []
                    for team_data in data.get("body", []):
                        if team_data.get("teamAbv") == team:
                            injuries = team_data.get("injury", [])
                            for inj in injuries:
                                if inj.get("designation") in ["Out", "Doubtful"]:
                                    injured_players.append(inj.get("longName", ""))
                    return injured_players
                    
        except Exception as e:
            logger.error(f"[ANALYTICS] Error fetching injuries: {e}")
        
        return []
    
    # ==================== VOLATILITY SCORE ====================
    
    def calculate_volatility(self, game_values: List[float]) -> Tuple[str, float]:
        """
        Calculate volatility score from recent game values.
        
        Args:
            game_values: List of stat values from recent games (e.g., points)
        
        Returns:
            (volatility_label, standard_deviation)
        """
        if not game_values or len(game_values) < 3:
            return ("Low", 0.0)
        
        try:
            stddev = statistics.stdev(game_values)
            
            if stddev > VOLATILITY_HIGH_THRESHOLD:
                return ("High", round(stddev, 2))
            elif stddev > VOLATILITY_MED_THRESHOLD:
                return ("Med", round(stddev, 2))
            else:
                return ("Low", round(stddev, 2))
                
        except Exception as e:
            logger.error(f"[ANALYTICS] Error calculating volatility: {e}")
            return ("Low", 0.0)
    
    # ==================== TEMPLATE SUMMARIES ====================
    
    def generate_insight_summary(
        self,
        player_name: str,
        schedule_density: float,
        pace_factor: float,
        usage_bump: float,
        volatility: str,
        days_rest: int,
        is_b2b: bool,
        is_3in4: bool,
        injured_teammates: List[str],
        opponent: str
    ) -> str:
        """
        Generate a template-based insight summary.
        Prioritizes the most impactful factor.
        """
        insights = []
        
        # Priority 1: Usage Bump (most impactful)
        if usage_bump > 10:
            teammates_str = " & ".join(injured_teammates[:2])
            insights.append(f"🚀 Usage Spike: With {teammates_str} out, usage is up +{usage_bump:.0f}%")
        
        # Priority 2: Schedule Fatigue
        if is_3in4:
            insights.append("⚠️ Fatigue Alert: 3-in-4 night; expect 8% performance dip")
        elif is_b2b:
            insights.append("⚠️ Back-to-Back: Minor fatigue factor (-5%)")
        
        # Priority 3: Pace Matchup
        if pace_factor > 1.05:
            insights.append(f"🏃 Fast Pace: vs {opponent} favors high-volume scorers (+{(pace_factor-1)*100:.0f}%)")
        elif pace_factor < 0.95:
            insights.append(f"🐢 Slow Pace: vs {opponent} limits possessions ({(pace_factor-1)*100:.0f}%)")
        
        # Priority 4: Rest Advantage
        if days_rest >= 3:
            insights.append(f"😴 Well Rested: {days_rest} days off; fresh legs advantage")
        
        # Priority 5: Volatility Warning
        if volatility == "High":
            insights.append("📊 High Variance: Inconsistent recent performances; proceed with caution")
        
        # Combine insights (max 2 for readability)
        if not insights:
            return f"📈 Standard projection for {player_name}. No significant modifiers detected."
        
        return " | ".join(insights[:2])
    
    # ==================== MAIN SYNC FUNCTION ====================
    
    async def calculate_daily_insights(
        self,
        player_data: Dict[str, Any],
        game_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Calculate all advanced analytics for a single player/game.
        
        Args:
            player_data: {player_name, player_id, team, recent_games: [values]}
            game_data: {game_id, game_date, opponent}
        
        Returns:
            Complete insights dictionary ready for storage
        """
        player_name = player_data.get("player_name", "")
        team = player_data.get("team", "")
        opponent = game_data.get("opponent", "")
        game_date = game_data.get("game_date", datetime.now(timezone.utc))
        
        # 1. Schedule Density
        density, is_b2b, is_3in4, days_rest = await self.calculate_schedule_density(team, game_date)
        
        # 2. Pace Factor
        pace = await self.calculate_pace_factor(team, opponent)
        
        # 3. Injuries & Usage Bump
        injured = await self.fetch_team_injuries(team)
        usage_bump, injured_stars = await self.calculate_usage_bump(player_name, team, injured)
        
        # 4. Volatility
        recent_values = player_data.get("recent_games", [])
        volatility, stddev = self.calculate_volatility(recent_values)
        
        # 5. Generate Summary
        summary = self.generate_insight_summary(
            player_name=player_name,
            schedule_density=density,
            pace_factor=pace,
            usage_bump=usage_bump,
            volatility=volatility,
            days_rest=days_rest,
            is_b2b=is_b2b,
            is_3in4=is_3in4,
            injured_teammates=injured_stars,
            opponent=opponent
        )
        
        # 6. Calculate confidence rating
        confidence = self._calculate_confidence(density, volatility, len(recent_values))
        
        return {
            "player_id": player_data.get("player_id", ""),
            "player_name": player_name,
            "team": team,
            "game_id": game_data.get("game_id", ""),
            "game_date": game_date.strftime("%Y-%m-%d") if isinstance(game_date, datetime) else game_date,
            "opponent": opponent,
            "schedule_density_factor": density,
            "pace_adjustment_factor": pace,
            "usage_bump_percent": usage_bump,
            "volatility_score": volatility,
            "volatility_stddev": stddev,
            "insight_summary": summary,
            "ai_confidence_rating": confidence,
            "is_back_to_back": is_b2b,
            "is_three_in_four": is_3in4,
            "days_rest": days_rest,
            "injured_teammates": injured_stars,
            "last_updated": datetime.now(timezone.utc).isoformat()
        }
    
    def _calculate_confidence(
        self, 
        density: float, 
        volatility: str, 
        sample_size: int
    ) -> int:
        """Calculate AI confidence rating (0-100)."""
        confidence = 70  # Base confidence
        
        # Reduce for fatigue
        if density < 0.95:
            confidence -= 10
        
        # Reduce for high volatility
        if volatility == "High":
            confidence -= 20
        elif volatility == "Med":
            confidence -= 10
        
        # Boost for large sample size
        if sample_size >= 10:
            confidence += 10
        elif sample_size < 5:
            confidence -= 15
        
        return max(0, min(100, confidence))
    
    async def store_insight(self, insight: Dict[str, Any]) -> bool:
        """Store a single insight in Supabase."""
        if not self.supabase:
            logger.warning("[ANALYTICS] Supabase not available, storing to MongoDB only")
            return False
        
        try:
            # Upsert to Supabase
            result = self.supabase.table("daily_insights").upsert(
                insight,
                on_conflict="player_id,game_id"
            ).execute()
            return True
        except Exception as e:
            logger.error(f"[ANALYTICS] Error storing insight: {e}")
            return False


# Pandas import for schedule density calculation
try:
    import pandas as pd
except ImportError:
    pd = None
    logger.warning("[ANALYTICS] pandas not available, schedule density limited")
