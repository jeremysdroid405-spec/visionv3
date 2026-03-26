"""
Standings Service
=================
Fetches NBA team standings from BallDontLie API and calculates blowout risk.

Blowout Risk Logic:
- Compares win percentages of two teams playing
- High risk if difference > 20% (e.g., .700 vs .450)
- Medium risk if difference > 15%
- Flags games where starters may get pulled early
"""

import httpx
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Tuple

logger = logging.getLogger(__name__)

BDL_API_KEY = os.environ.get("BDL_API_KEY")
BDL_BASE_URL = "https://api.balldontlie.io/v1"
CURRENT_SEASON = 2025  # 2025-26 season


class StandingsService:
    """Service for fetching team standings and calculating blowout risk."""
    
    # Class-level cache for standings (refresh every 6 hours)
    _standings_cache: Dict[str, Dict] = {}
    _cache_timestamp: datetime = None
    _CACHE_TTL = timedelta(hours=6)
    
    # Team abbreviation to ID mapping
    TEAM_ABBR_TO_ID = {
        'ATL': 1, 'BOS': 2, 'BKN': 3, 'CHA': 4, 'CHI': 5,
        'CLE': 6, 'DAL': 7, 'DEN': 8, 'DET': 9, 'GSW': 10,
        'HOU': 11, 'IND': 12, 'LAC': 13, 'LAL': 14, 'MEM': 15,
        'MIA': 16, 'MIL': 17, 'MIN': 18, 'NOP': 19, 'NYK': 20,
        'OKC': 21, 'ORL': 22, 'PHI': 23, 'PHX': 24, 'POR': 25,
        'SAC': 26, 'SAS': 27, 'TOR': 28, 'UTA': 29, 'WAS': 30
    }
    
    TEAM_ID_TO_ABBR = {v: k for k, v in TEAM_ABBR_TO_ID.items()}
    
    @classmethod
    async def _fetch_standings(cls) -> Dict[str, Dict]:
        """Fetch current standings from BallDontLie API."""
        if not BDL_API_KEY:
            logger.warning("[STANDINGS] No BDL_API_KEY configured")
            return {}
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{BDL_BASE_URL}/standings",
                    params={"season": CURRENT_SEASON},
                    headers={"Authorization": BDL_API_KEY}
                )
                
                if response.status_code != 200:
                    logger.warning(f"[STANDINGS] API returned {response.status_code}")
                    return {}
                
                data = response.json()
                standings = {}
                
                for team_data in data.get("data", []):
                    team = team_data.get("team", {})
                    team_id = team.get("id")
                    abbr = team.get("abbreviation") or cls.TEAM_ID_TO_ABBR.get(team_id)
                    
                    if abbr:
                        wins = team_data.get("wins", 0)
                        losses = team_data.get("losses", 0)
                        total_games = wins + losses
                        win_pct = wins / total_games if total_games > 0 else 0.5
                        
                        standings[abbr] = {
                            "team_id": team_id,
                            "abbreviation": abbr,
                            "wins": wins,
                            "losses": losses,
                            "win_pct": win_pct,
                            "conference": team_data.get("conference"),
                            "conference_rank": team_data.get("conference_rank"),
                            "home_record": team_data.get("home_record"),
                            "away_record": team_data.get("road_record"),
                        }
                
                logger.info(f"[STANDINGS] Fetched standings for {len(standings)} teams")
                return standings
                
        except Exception as e:
            logger.error(f"[STANDINGS] Error fetching standings: {e}")
            return {}
    
    @classmethod
    async def get_standings(cls) -> Dict[str, Dict]:
        """Get cached standings or fetch fresh if expired."""
        now = datetime.now(timezone.utc)
        
        # Check if cache is valid
        if cls._standings_cache and cls._cache_timestamp:
            if now - cls._cache_timestamp < cls._CACHE_TTL:
                return cls._standings_cache
        
        # Fetch fresh standings
        standings = await cls._fetch_standings()
        if standings:
            cls._standings_cache = standings
            cls._cache_timestamp = now
        
        return cls._standings_cache or {}
    
    @classmethod
    async def get_team_record(cls, team_abbr: str) -> Optional[Dict]:
        """Get record for a specific team."""
        standings = await cls.get_standings()
        return standings.get(team_abbr.upper())
    
    @classmethod
    async def calculate_blowout_risk(
        cls, 
        player_team: str, 
        opponent_team: str
    ) -> Dict:
        """
        Calculate blowout risk based on team record differential.
        
        Returns:
            {
                "risk_level": "HIGH" | "MEDIUM" | "LOW" | "NONE",
                "risk_reason": str,
                "player_team_record": "45-27",
                "opponent_team_record": "20-52",
                "win_pct_diff": 0.35,
                "favored_team": "LAL",
                "warning": str or None
            }
        """
        standings = await cls.get_standings()
        
        player_record = standings.get(player_team.upper())
        opponent_record = standings.get(opponent_team.upper())
        
        # Default response if no data
        if not player_record or not opponent_record:
            return {
                "risk_level": "UNKNOWN",
                "risk_reason": "Standings data unavailable",
                "warning": None
            }
        
        player_pct = player_record.get("win_pct", 0.5)
        opponent_pct = opponent_record.get("win_pct", 0.5)
        
        win_pct_diff = abs(player_pct - opponent_pct)
        favored_team = player_team if player_pct > opponent_pct else opponent_team
        underdog_team = opponent_team if player_pct > opponent_pct else player_team
        
        # Determine if player is on the favored or underdog team
        player_is_favored = player_pct > opponent_pct
        
        # Format records
        player_record_str = f"{player_record.get('wins', 0)}-{player_record.get('losses', 0)}"
        opponent_record_str = f"{opponent_record.get('wins', 0)}-{opponent_record.get('losses', 0)}"
        
        result = {
            "player_team_record": player_record_str,
            "opponent_team_record": opponent_record_str,
            "player_win_pct": round(player_pct, 3),
            "opponent_win_pct": round(opponent_pct, 3),
            "win_pct_diff": round(win_pct_diff, 3),
            "favored_team": favored_team,
            "player_is_favored": player_is_favored,
        }
        
        # Calculate risk level
        if win_pct_diff >= 0.25:
            # 25%+ difference = HIGH risk (e.g., .700 vs .450 or worse)
            result["risk_level"] = "HIGH"
            if player_is_favored:
                result["risk_reason"] = f"Heavy favorite ({player_team} {player_record_str}) vs weak opponent ({opponent_team} {opponent_record_str})"
                result["warning"] = f"⚠️ BLOWOUT RISK: {player_team} heavily favored - starters may rest in 4th quarter if up big"
            else:
                result["risk_reason"] = f"Heavy underdog ({player_team} {player_record_str}) vs strong opponent ({opponent_team} {opponent_record_str})"
                result["warning"] = f"⚠️ BLOWOUT RISK: {player_team} big underdog - may get blown out, garbage time minutes"
                
        elif win_pct_diff >= 0.18:
            # 18-25% difference = MEDIUM risk
            result["risk_level"] = "MEDIUM"
            if player_is_favored:
                result["risk_reason"] = f"Clear favorite ({player_team} {player_record_str}) vs weaker opponent ({opponent_team} {opponent_record_str})"
                result["warning"] = f"⚡ Blowout possible: {player_team} solidly favored - monitor game flow"
            else:
                result["risk_reason"] = f"Underdog ({player_team} {player_record_str}) vs better opponent ({opponent_team} {opponent_record_str})"
                result["warning"] = f"⚡ Blowout possible: {player_team} is underdog - reduced minutes if game gets away"
                
        elif win_pct_diff >= 0.10:
            # 10-18% difference = LOW risk
            result["risk_level"] = "LOW"
            result["risk_reason"] = f"Slight mismatch: {player_team} ({player_record_str}) vs {opponent_team} ({opponent_record_str})"
            result["warning"] = None
            
        else:
            # Less than 10% difference = competitive game
            result["risk_level"] = "NONE"
            result["risk_reason"] = f"Competitive matchup: {player_team} ({player_record_str}) vs {opponent_team} ({opponent_record_str})"
            result["warning"] = None
        
        return result
    
    @classmethod
    def format_blowout_context(cls, blowout_data: Dict) -> str:
        """Format blowout risk data for AI prompt context."""
        if not blowout_data or blowout_data.get("risk_level") == "UNKNOWN":
            return "Game competitiveness data unavailable"
        
        risk_level = blowout_data.get("risk_level", "NONE")
        reason = blowout_data.get("risk_reason", "")
        
        if risk_level == "HIGH":
            return f"⚠️ HIGH BLOWOUT RISK - {reason}. Starters likely to see reduced 4th quarter minutes."
        elif risk_level == "MEDIUM":
            return f"⚡ MODERATE BLOWOUT RISK - {reason}. Monitor game flow for early benching."
        elif risk_level == "LOW":
            return f"Slight favorite/underdog situation - {reason}. Minor blowout concern."
        else:
            return f"Competitive game expected - {reason}. Full minutes likely."
