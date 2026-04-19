"""
Team Stats Service - Pace, Offensive/Defensive Ratings
========================================================

Fetches and caches team-level statistics needed for Vegas Killer model:
- Pace (possessions per 48 minutes)
- Offensive Rating (points per 100 possessions)
- Defensive Rating (points allowed per 100 possessions)
- Points per game (PPG)
- Points allowed per game (PAPG)

Data Sources:
- BallDontLie API for base stats
- Calculated from team game logs
"""

import os
import logging
import httpx
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from pymongo import MongoClient

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

# NBA Team abbreviations mapping
NBA_TEAMS = {
    1: {"abbr": "ATL", "name": "Atlanta Hawks"},
    2: {"abbr": "BOS", "name": "Boston Celtics"},
    3: {"abbr": "BKN", "name": "Brooklyn Nets"},
    4: {"abbr": "CHA", "name": "Charlotte Hornets"},
    5: {"abbr": "CHI", "name": "Chicago Bulls"},
    6: {"abbr": "CLE", "name": "Cleveland Cavaliers"},
    7: {"abbr": "DAL", "name": "Dallas Mavericks"},
    8: {"abbr": "DEN", "name": "Denver Nuggets"},
    9: {"abbr": "DET", "name": "Detroit Pistons"},
    10: {"abbr": "GSW", "name": "Golden State Warriors"},
    11: {"abbr": "HOU", "name": "Houston Rockets"},
    12: {"abbr": "IND", "name": "Indiana Pacers"},
    13: {"abbr": "LAC", "name": "LA Clippers"},
    14: {"abbr": "LAL", "name": "Los Angeles Lakers"},
    15: {"abbr": "MEM", "name": "Memphis Grizzlies"},
    16: {"abbr": "MIA", "name": "Miami Heat"},
    17: {"abbr": "MIL", "name": "Milwaukee Bucks"},
    18: {"abbr": "MIN", "name": "Minnesota Timberwolves"},
    19: {"abbr": "NOP", "name": "New Orleans Pelicans"},
    20: {"abbr": "NYK", "name": "New York Knicks"},
    21: {"abbr": "OKC", "name": "Oklahoma City Thunder"},
    22: {"abbr": "ORL", "name": "Orlando Magic"},
    23: {"abbr": "PHI", "name": "Philadelphia 76ers"},
    24: {"abbr": "PHX", "name": "Phoenix Suns"},
    25: {"abbr": "POR", "name": "Portland Trail Blazers"},
    26: {"abbr": "SAC", "name": "Sacramento Kings"},
    27: {"abbr": "SAS", "name": "San Antonio Spurs"},
    28: {"abbr": "TOR", "name": "Toronto Raptors"},
    29: {"abbr": "UTA", "name": "Utah Jazz"},
    30: {"abbr": "WAS", "name": "Washington Wizards"},
}

# Reverse lookup
TEAM_ABBR_TO_ID = {v["abbr"]: k for k, v in NBA_TEAMS.items()}

# 2025-26 Season Pace Data (estimated from season trends)
# Pace = possessions per 48 minutes
TEAM_PACE_2026 = {
    "ATL": 100.2,
    "BOS": 99.8,
    "BKN": 98.5,
    "CHA": 99.1,
    "CHI": 97.8,
    "CLE": 96.5,
    "DAL": 99.3,
    "DEN": 98.7,
    "DET": 99.9,
    "GSW": 101.2,
    "HOU": 100.5,
    "IND": 102.8,  # Fastest team
    "LAC": 97.2,
    "LAL": 99.4,
    "MEM": 100.1,
    "MIA": 95.8,  # Slowest team
    "MIL": 98.9,
    "MIN": 99.6,
    "NOP": 98.4,
    "NYK": 97.5,
    "OKC": 100.8,
    "ORL": 96.8,
    "PHI": 97.9,
    "PHX": 99.0,
    "POR": 99.7,
    "SAC": 101.5,
    "SAS": 100.3,
    "TOR": 98.2,
    "UTA": 99.4,
    "WAS": 100.6,
}

# League average pace
LEAGUE_AVG_PACE = 99.0


class TeamStatsService:
    """
    Service for team-level statistics.
    """
    
    def __init__(self, db):
        self.db = db
        self.collection = db['team_stats_cache']
        self._cache = {}
        self._load_cache()
    
    def _load_cache(self):
        """Load cached team stats into memory."""
        try:
            for doc in self.collection.find({}):
                team = doc.get('team_abbr')
                if team:
                    self._cache[team] = doc
            logger.info(f"Loaded {len(self._cache)} team stats from cache")
        except Exception as e:
            logger.error(f"Failed to load team stats cache: {e}")
    
    def get_team_pace(self, team_abbr: str) -> float:
        """Get team's pace (possessions per 48 minutes)."""
        # First check cache
        if team_abbr in self._cache:
            return self._cache[team_abbr].get('pace', TEAM_PACE_2026.get(team_abbr, LEAGUE_AVG_PACE))
        
        # Fall back to static data
        return TEAM_PACE_2026.get(team_abbr, LEAGUE_AVG_PACE)
    
    def get_expected_game_pace(self, team1_abbr: str, team2_abbr: str) -> float:
        """
        Calculate expected game pace based on both teams.
        
        Formula: (Team1_Pace + Team2_Pace) / 2
        """
        pace1 = self.get_team_pace(team1_abbr)
        pace2 = self.get_team_pace(team2_abbr)
        return (pace1 + pace2) / 2
    
    def get_pace_delta(self, player_team: str, opponent_team: str) -> Dict[str, Any]:
        """
        Calculate pace differential for a matchup.
        
        Returns:
            Dict with pace delta, tempo label, and expected possessions
        """
        player_pace = self.get_team_pace(player_team)
        opp_pace = self.get_team_pace(opponent_team)
        expected_pace = self.get_expected_game_pace(player_team, opponent_team)
        
        # Delta from league average
        delta = expected_pace - LEAGUE_AVG_PACE
        
        # Tempo label
        if delta >= 2:
            tempo = "Fast"
        elif delta >= 0.5:
            tempo = "Above Average"
        elif delta <= -2:
            tempo = "Slow"
        elif delta <= -0.5:
            tempo = "Below Average"
        else:
            tempo = "Neutral"
        
        return {
            "player_team_pace": round(player_pace, 1),
            "opponent_pace": round(opp_pace, 1),
            "expected_game_pace": round(expected_pace, 1),
            "pace_delta": round(delta, 1),
            "tempo_label": tempo,
            "league_avg": LEAGUE_AVG_PACE,
        }
    
    def get_team_stats(self, team_abbr: str) -> Dict[str, Any]:
        """Get all team stats."""
        if team_abbr in self._cache:
            return self._cache[team_abbr]
        
        # Build from available data
        return {
            "team_abbr": team_abbr,
            "team_name": NBA_TEAMS.get(TEAM_ABBR_TO_ID.get(team_abbr, 0), {}).get("name", team_abbr),
            "pace": self.get_team_pace(team_abbr),
            "off_rating": 112.0,  # Default
            "def_rating": 112.0,  # Default
            "ppg": 112.0,
            "papg": 112.0,
        }
    
    def calculate_team_stats_from_games(self, team_abbr: str) -> Dict[str, Any]:
        """
        Calculate team stats from player game logs.
        
        Uses aggregate player stats to estimate team performance.
        """
        hub = self.db[COLL("master_hub", "nba")]
        
        # Find players on this team
        team_players = list(hub.find({'team': team_abbr}))
        
        if not team_players:
            return self.get_team_stats(team_abbr)
        
        # Aggregate recent game stats
        team_pts = []
        team_fga = []
        team_fta = []
        
        for player in team_players:
            logs = player.get('bdl_game_logs', [])[:10]
            for log in logs:
                # Only count if it looks like the player played
                mins = log.get('min', 0)
                if isinstance(mins, str):
                    try:
                        mins = int(mins.split(':')[0]) if ':' in mins else float(mins)
                    except:
                        mins = 0
                
                if mins > 0:
                    team_pts.append(log.get('pts', 0))
                    team_fga.append(log.get('fga', 0))
                    team_fta.append(log.get('fta', 0))
        
        # Estimate pace from FGA + FTA (possession indicators)
        if team_fga and team_fta:
            avg_fga = sum(team_fga) / len(team_fga)
            avg_fta = sum(team_fta) / len(team_fta)
            # Rough pace estimate: more attempts = faster pace
            estimated_pace = 95 + (avg_fga * 0.8) + (avg_fta * 0.3)
            estimated_pace = max(94, min(105, estimated_pace))  # Clamp to reasonable range
        else:
            estimated_pace = TEAM_PACE_2026.get(team_abbr, LEAGUE_AVG_PACE)
        
        return {
            "team_abbr": team_abbr,
            "pace": round(estimated_pace, 1),
            "ppg": round(sum(team_pts) / max(len(team_pts), 1), 1) if team_pts else 112,
            "calculated_at": datetime.now(timezone.utc).isoformat(),
        }
    
    def sync_all_teams(self):
        """Calculate and cache stats for all teams."""
        for team_abbr in TEAM_PACE_2026.keys():
            try:
                stats = self.calculate_team_stats_from_games(team_abbr)
                stats['pace'] = TEAM_PACE_2026.get(team_abbr, LEAGUE_AVG_PACE)  # Use known pace data
                
                # Upsert to cache
                self.collection.update_one(
                    {"team_abbr": team_abbr},
                    {"$set": stats},
                    upsert=True
                )
                self._cache[team_abbr] = stats
                
            except Exception as e:
                logger.error(f"Failed to sync team {team_abbr}: {e}")
        
        logger.info(f"Synced stats for {len(TEAM_PACE_2026)} teams")


# =============================================================================
# VEGAS TOTALS SERVICE
# =============================================================================

class VegasTotalsService:
    """
    Extract team totals and spreads from props data.
    """
    
    def __init__(self, db):
        self.db = db
        self._totals_cache = {}
    
    def get_game_totals(self, home_team: str, away_team: str) -> Dict[str, Any]:
        """
        Get team totals for a game.
        
        Team total is calculated from Over/Under line divided by 2,
        adjusted by spread.
        
        Example: Game total 220, Spread -5.5 for home team
        Home total = (220 / 2) + (5.5 / 2) = 112.75
        Away total = (220 / 2) - (5.5 / 2) = 107.25
        """
        # Check if we have cached totals
        cache_key = f"{home_team}_{away_team}"
        if cache_key in self._totals_cache:
            cached = self._totals_cache[cache_key]
            # Cache for 1 hour
            if (datetime.now(timezone.utc) - cached.get('cached_at', datetime.min.replace(tzinfo=timezone.utc))).seconds < 3600:
                return cached
        
        # Default totals (league average)
        default = {
            "game_total": 224.0,
            "home_total": 114.0,
            "away_total": 110.0,
            "spread": -4.0,
            "home_team": home_team,
            "away_team": away_team,
            "source": "default",
        }
        
        # Try to find from cached board
        try:
            cached_board = self.db[COLL("board_cache", "nba")]
            
            # Find any prop for this game
            prop = cached_board.find_one({
                '$or': [
                    {'home_team': home_team, 'away_team': away_team},
                    {'home_team': away_team, 'away_team': home_team},
                ]
            })
            
            if prop and prop.get('intel_suite'):
                intel = prop['intel_suite']
                
                # Extract pace data if available
                pace_data = intel.get('pace_delta', {})
                expected_pace = float(pace_data.get('expected_game_pace', 99.0))
                
                # Estimate game total from pace
                # Higher pace = more possessions = more points
                # League avg pace ~99, league avg total ~224
                pace_factor = expected_pace / 99.0
                estimated_total = 224.0 * pace_factor
                
                default["game_total"] = round(estimated_total, 1)
                default["home_total"] = round(estimated_total / 2 + 2, 1)  # Home advantage
                default["away_total"] = round(estimated_total / 2 - 2, 1)
                default["source"] = "calculated_from_pace"
                default["expected_pace"] = expected_pace
        
        except Exception as e:
            logger.error(f"Error getting game totals: {e}")
        
        # Cache the result
        default["cached_at"] = datetime.now(timezone.utc)
        self._totals_cache[cache_key] = default
        
        return default
    
    def get_player_share(
        self,
        player_name: str,
        team_abbr: str,
        stat_type: str,
        team_total: float
    ) -> Dict[str, Any]:
        """
        Calculate player's expected share of team total.
        
        Uses player's usage rate and historical share.
        """
        hub = self.db[COLL("master_hub", "nba")]
        
        player = hub.find_one({
            '$or': [
                {'player_name': player_name},
                {'display_name': player_name},
            ],
            'team': team_abbr
        })
        
        if not player:
            return {"share_pct": 20.0, "expected_value": team_total * 0.2}
        
        logs = player.get('bdl_game_logs', [])[:10]
        
        if not logs:
            return {"share_pct": 20.0, "expected_value": team_total * 0.2}
        
        # Get player's average for the stat
        stat_map = {
            'PTS': 'pts',
            'REB': 'reb',
            'AST': 'ast',
            '3PM': 'fg3m',
        }
        field = stat_map.get(stat_type.upper(), 'pts')
        
        player_avg = sum(log.get(field, 0) for log in logs) / len(logs)
        
        # Estimate share (player avg / team total)
        # This is simplified - in reality would use actual team totals
        share_pct = (player_avg / team_total) * 100 if team_total > 0 else 20
        
        # Adjust expected value based on team total
        # If team is projected for 120 pts and player usually scores 20% = 24 pts
        expected_value = team_total * (share_pct / 100)
        
        return {
            "share_pct": round(share_pct, 1),
            "expected_value": round(expected_value, 1),
            "player_avg": round(player_avg, 1),
            "team_total": team_total,
        }


# =============================================================================
# EXPORT
# =============================================================================

__all__ = [
    'TeamStatsService',
    'VegasTotalsService',
    'TEAM_PACE_2026',
    'LEAGUE_AVG_PACE',
    'NBA_TEAMS',
    'TEAM_ABBR_TO_ID',
]
