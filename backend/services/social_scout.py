"""
Social Scout Service - Social Signals & Sentiment Analysis
===========================================================
Handles social media signals, injury alerts, and contextual factors.
"""
from typing import Dict, List, Any, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


# Team pace rankings (possessions per game)
TEAM_PACE = {
    "IND": 104.5, "ATL": 103.8, "MIL": 102.9, "SAC": 102.5, "MIN": 102.2,
    "DAL": 101.8, "DEN": 101.5, "PHX": 101.2, "LAL": 100.9, "BOS": 100.7,
    "OKC": 100.5, "CLE": 100.2, "NYK": 99.9, "GSW": 99.6, "HOU": 99.3,
    "TOR": 99.0, "NOP": 98.7, "MIA": 98.4, "CHI": 98.1, "POR": 97.8,
    "BKN": 97.5, "SAS": 97.2, "DET": 96.9, "WAS": 96.6, "CHA": 96.3,
    "LAC": 96.0, "PHI": 95.7, "ORL": 95.4, "MEM": 95.1, "UTA": 94.8
}

# High-usage players by team (starters likely to get bulk of touches)
HIGH_USAGE_PLAYERS = {
    "LAL": ["LeBron James", "Anthony Davis", "Austin Reaves"],
    "BOS": ["Jayson Tatum", "Jaylen Brown", "Derrick White"],
    "DEN": ["Nikola Jokic", "Jamal Murray", "Michael Porter Jr."],
    "PHX": ["Kevin Durant", "Devin Booker", "Bradley Beal"],
    "MIL": ["Giannis Antetokounmpo", "Damian Lillard", "Khris Middleton"],
    "GSW": ["Stephen Curry", "Draymond Green", "Andrew Wiggins"],
    "DAL": ["Luka Doncic", "Kyrie Irving", "PJ Washington"],
    "MIA": ["Jimmy Butler", "Bam Adebayo", "Tyler Herro"],
    "CLE": ["Donovan Mitchell", "Darius Garland", "Evan Mobley"],
    "OKC": ["Shai Gilgeous-Alexander", "Chet Holmgren", "Jalen Williams"],
    "NYK": ["Jalen Brunson", "Julius Randle", "OG Anunoby"],
    "MIN": ["Anthony Edwards", "Karl-Anthony Towns", "Rudy Gobert"],
    # Add more teams as needed
}


def get_team_pace(team: str) -> float:
    """Get team's pace rating"""
    return TEAM_PACE.get(team, 100.0)


def calculate_pace_factor(team: str, opponent: str) -> float:
    """
    Calculate pace factor for a matchup.
    Returns multiplier (>1 = faster pace, <1 = slower pace)
    """
    team_pace = get_team_pace(team)
    opp_pace = get_team_pace(opponent)
    avg_pace = (team_pace + opp_pace) / 2
    league_avg = 100.0
    
    return round(avg_pace / league_avg, 3)


def get_high_usage_players(team: str) -> List[str]:
    """Get list of high-usage players for a team"""
    return HIGH_USAGE_PLAYERS.get(team, [])


def calculate_usage_bump(player_name: str, team: str, is_star_out: bool = False) -> float:
    """
    Calculate usage bump when a star player is out.
    """
    high_usage = get_high_usage_players(team)
    
    if player_name in high_usage:
        return 0.0  # They ARE the star
    
    if is_star_out:
        return 0.15  # 15% usage bump when star is out
    
    return 0.0


def calculate_volatility(game_values: List[float]) -> tuple:
    """
    Calculate volatility flag and coefficient of variation.
    
    Returns:
        tuple: (volatility_flag, cv_value)
        - volatility_flag: "HIGH", "MEDIUM", "LOW"
        - cv_value: coefficient of variation (std/mean)
    """
    if not game_values or len(game_values) < 3:
        return ("LOW", 0.0)
    
    import statistics
    
    mean = statistics.mean(game_values)
    if mean == 0:
        return ("LOW", 0.0)
    
    std = statistics.stdev(game_values)
    cv = std / mean
    
    if cv > 0.5:
        return ("HIGH", round(cv, 3))
    elif cv > 0.3:
        return ("MEDIUM", round(cv, 3))
    else:
        return ("LOW", round(cv, 3))


def generate_insight_summary(
    player_name: str,
    stat_type: str,
    line: float,
    h10_rate: float,
    season_avg: float,
    is_demon: bool = False,
    is_goblin: bool = False,
    volatility: str = "LOW",
    dvp_label: str = "NEUTRAL"
) -> str:
    """
    Generate a human-readable insight summary for a prop.
    """
    # Base insight
    if is_demon:
        insight = f"High-upside {stat_type} play. "
    elif is_goblin:
        insight = f"High-floor {stat_type} play. "
    else:
        insight = f"Standard {stat_type} line. "
    
    # Hit rate context
    if h10_rate >= 90:
        insight += "Excellent recent form with elite hit rate. "
    elif h10_rate >= 70:
        insight += "Strong historical performance. "
    elif h10_rate >= 50:
        insight += "Solid consistency in recent games. "
    else:
        insight += "Below-average recent performance. "
    
    # Season average context
    if season_avg > 0:
        gap = ((season_avg - line) / line) * 100 if line > 0 else 0
        if gap > 20:
            insight += f"Season avg ({season_avg:.1f}) well above line. "
        elif gap > 5:
            insight += f"Season avg ({season_avg:.1f}) slightly above line. "
        elif gap < -20:
            insight += f"Line above season avg ({season_avg:.1f}). "
    
    # Volatility context
    if volatility == "HIGH":
        insight += "High game-to-game variance. "
    
    # Matchup context
    if dvp_label == "FAVORABLE":
        insight += "Favorable defensive matchup."
    elif dvp_label == "TOUGH":
        insight += "Tough defensive matchup."
    
    return insight.strip()


def calculate_confidence_rating(
    h10_rate: float,
    h5_rate: float,
    volatility: str,
    dvp_modifier: float,
    vegas_prob: float = 0.5
) -> int:
    """
    Calculate AI confidence rating (0-100).
    
    Weights:
    - L10 hit rate: 35%
    - L5 hit rate: 25%
    - Volatility: 20% (inverted - high volatility = lower confidence)
    - DvP matchup: 10%
    - Vegas probability: 10%
    """
    # Hit rate component (60%)
    hit_rate_score = (h10_rate * 0.35 + h5_rate * 0.25)
    
    # Volatility component (20%) - inverted
    volatility_scores = {"LOW": 20, "MEDIUM": 10, "HIGH": 0}
    volatility_score = volatility_scores.get(volatility, 10)
    
    # DvP component (10%)
    dvp_score = dvp_modifier * 10
    
    # Vegas component (10%)
    vegas_score = vegas_prob * 10
    
    total = hit_rate_score + volatility_score + dvp_score + vegas_score
    return min(100, max(0, int(total)))


class SocialSignalAnalyzer:
    """Analyzes social signals for players"""
    
    def __init__(self, db=None):
        self.db = db
        self.injury_cache = {}
        self.news_cache = {}
    
    async def get_player_signals(self, player_name: str) -> Dict[str, Any]:
        """Get all social signals for a player"""
        return {
            "player_name": player_name,
            "volatility_flag": None,
            "volatility_reason": None,
            "revenge_game": False,
            "revenge_opponent": None,
            "injury_status": await self._get_injury_status(player_name),
            "recent_news": await self._get_recent_news(player_name),
            "teammate_out": False,
            "usage_bump": 0.0,
            "analyzed_at": datetime.utcnow().isoformat()
        }
    
    async def _get_injury_status(self, player_name: str) -> Optional[str]:
        """Get player injury status"""
        if player_name in self.injury_cache:
            return self.injury_cache[player_name]
        
        if self.db:
            injury = await self.db["injuries"].find_one({"player_name": player_name})
            if injury:
                status = injury.get("status")
                self.injury_cache[player_name] = status
                return status
        
        return None
    
    async def _get_recent_news(self, player_name: str) -> List[str]:
        """Get recent news about a player"""
        if player_name in self.news_cache:
            return self.news_cache[player_name]
        
        if self.db:
            news = await self.db["news"].find(
                {"player_name": player_name}
            ).sort("timestamp", -1).limit(3).to_list(3)
            
            headlines = [n.get("headline", "") for n in news]
            self.news_cache[player_name] = headlines
            return headlines
        
        return []
    
    def detect_revenge_game(self, player_name: str, opponent_team: str, player_history: Dict) -> bool:
        """Detect if this is a revenge game for the player"""
        former_teams = player_history.get("former_teams", [])
        return opponent_team in former_teams
    
    def analyze_teammate_impact(
        self, 
        player_name: str, 
        team: str, 
        injured_players: List[str]
    ) -> Dict[str, Any]:
        """Analyze impact of teammate injuries on player's usage"""
        high_usage = get_high_usage_players(team)
        
        # Check if any high-usage teammates are injured
        stars_out = [p for p in high_usage if p in injured_players and p != player_name]
        
        usage_bump = 0.0
        if stars_out and player_name not in high_usage:
            usage_bump = 0.10 * len(stars_out)  # 10% per star out
        
        return {
            "stars_out": stars_out,
            "usage_bump": min(0.25, usage_bump),  # Cap at 25%
            "impacts_player": len(stars_out) > 0
        }
