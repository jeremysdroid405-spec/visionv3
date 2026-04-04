"""
InjuryVacuumService - Usage Vacuum Microservice
================================================
Event-driven service that monitors NBA injury reports and calculates
"Usage Vacuum" beneficiaries when star players are ruled OUT.

Architecture:
- Event-Driven Model: Monitors for state changes and broadcasts updates
- Redis Cache: Stores "Star Usage Profiles" (Usage > 25%)
- MongoDB: injury_log collection for status history
- Latency Goal: < 30 seconds from source update to Ferrari Score recalculation

REACTIVE RE-SCANNING (v2.0):
- When a player with usage > 20% is marked "OUT", triggers ReScanEvent
- Redistributes minutes to next-man-up in rotation
- Applies +15% to +25% usage multiplier to primary/secondary ball-handlers
- Auto-promotes players to board if projected stat is >15% above line
- Tags promoted players with "high_usage_advantage" badge

Modifiers:
- Primary Beneficiary: +15 points to Ferrari Score
- Secondary Beneficiary: +10 points to Ferrari Score

Author: PropVision AI
Version: 2.0.0
"""
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import logging
import json
import re
import hashlib

logger = logging.getLogger(__name__)

# =============================================================================
# USAGE VACUUM CONSTANTS
# =============================================================================

# Star player threshold (Usage Rate > 20% for late scratch detection)
STAR_USAGE_THRESHOLD = 20.0
HIGH_USAGE_THRESHOLD = 25.0

# Ferrari Score modifiers
PRIMARY_BENEFICIARY_MODIFIER = 15.0
SECONDARY_BENEFICIARY_MODIFIER = 10.0

# Usage redistribution multipliers
PRIMARY_USAGE_MULTIPLIER = 1.25  # +25% usage boost
SECONDARY_USAGE_MULTIPLIER = 1.15  # +15% usage boost

# Board promotion threshold (projected stat > 15% above line)
BOARD_PROMOTION_THRESHOLD = 0.15

# Late scratch window (120 minutes = 2 hours)
LATE_SCRATCH_WINDOW_MINUTES = 120

# Injury status triggers
TRIGGER_STATUSES = ["OUT", "DOUBTFUL"]

# Cache TTL (seconds)
STAR_PROFILE_TTL = 3600 * 24  # 24 hours
INJURY_CACHE_TTL = 60  # 1 minute for injury status

# NBA Injury Report URL pattern
NBA_INJURY_URL = "https://cdn.nba.com/static/json/liveData/injuries/injuries.json"
NBA_INJURY_PDF_PATTERN = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{date}_{time}.pdf"

# ESPN Injury API (more reliable fallback)
ESPN_INJURY_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"

# Team abbreviations for matching
TEAM_NAME_TO_ABBREV = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "LA Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP", "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR", "Utah Jazz": "UTA", "Washington Wizards": "WAS"
}


# =============================================================================
# STAR USAGE PROFILES (Fallback Data)
# =============================================================================

# Top usage players by team with their usage rates
STAR_USAGE_PROFILES = {
    # Format: "player_name": {"team": "ABBR", "usage_rate": XX.X, "position": "POS"}
    "Luka Doncic": {"team": "DAL", "usage_rate": 36.5, "position": "PG"},
    "Giannis Antetokounmpo": {"team": "MIL", "usage_rate": 35.2, "position": "PF"},
    "Joel Embiid": {"team": "PHI", "usage_rate": 34.8, "position": "C"},
    "Shai Gilgeous-Alexander": {"team": "OKC", "usage_rate": 33.5, "position": "PG"},
    "LeBron James": {"team": "LAL", "usage_rate": 31.2, "position": "SF"},
    "Anthony Davis": {"team": "LAL", "usage_rate": 28.5, "position": "PF"},
    "Kevin Durant": {"team": "PHX", "usage_rate": 32.1, "position": "SF"},
    "Devin Booker": {"team": "PHX", "usage_rate": 28.8, "position": "SG"},
    "Jayson Tatum": {"team": "BOS", "usage_rate": 30.5, "position": "SF"},
    "Jaylen Brown": {"team": "BOS", "usage_rate": 26.2, "position": "SG"},
    "Donovan Mitchell": {"team": "CLE", "usage_rate": 29.8, "position": "SG"},
    "Trae Young": {"team": "ATL", "usage_rate": 32.5, "position": "PG"},
    "De'Aaron Fox": {"team": "SAC", "usage_rate": 30.2, "position": "PG"},
    "Domantas Sabonis": {"team": "SAC", "usage_rate": 25.8, "position": "C"},
    "Ja Morant": {"team": "MEM", "usage_rate": 31.5, "position": "PG"},
    "Paolo Banchero": {"team": "ORL", "usage_rate": 28.2, "position": "PF"},
    "Anthony Edwards": {"team": "MIN", "usage_rate": 30.8, "position": "SG"},
    "Karl-Anthony Towns": {"team": "MIN", "usage_rate": 26.5, "position": "C"},
    "Tyrese Haliburton": {"team": "IND", "usage_rate": 27.5, "position": "PG"},
    "Tyrese Maxey": {"team": "PHI", "usage_rate": 26.8, "position": "PG"},
    "Jalen Brunson": {"team": "NYK", "usage_rate": 29.5, "position": "PG"},
    "Julius Randle": {"team": "NYK", "usage_rate": 27.2, "position": "PF"},
    "Damian Lillard": {"team": "MIL", "usage_rate": 28.5, "position": "PG"},
    "Stephen Curry": {"team": "GSW", "usage_rate": 30.2, "position": "PG"},
    "Kawhi Leonard": {"team": "LAC", "usage_rate": 28.5, "position": "SF"},
    "Paul George": {"team": "PHI", "usage_rate": 26.5, "position": "SF"},
    "Jimmy Butler": {"team": "MIA", "usage_rate": 27.8, "position": "SF"},
    "Bam Adebayo": {"team": "MIA", "usage_rate": 25.2, "position": "C"},
}

# Beneficiary mappings (who benefits when star is OUT)
# Format: "injured_star": [("primary_beneficiary", usage_bump, minutes_bump), ("secondary_beneficiary", usage_bump, minutes_bump)]
# Usage bump = percentage point increase, Minutes bump = additional minutes per game
BENEFICIARY_MAPPINGS = {
    "LeBron James": [("Anthony Davis", 5.2, 4), ("Austin Reaves", 4.8, 6)],
    "Anthony Davis": [("LeBron James", 4.5, 3), ("Austin Reaves", 3.8, 5)],
    "Kevin Durant": [("Devin Booker", 5.5, 4), ("Bradley Beal", 4.2, 5)],
    "Devin Booker": [("Kevin Durant", 4.8, 3), ("Bradley Beal", 3.5, 5)],
    "Joel Embiid": [("Tyrese Maxey", 6.2, 4), ("Paul George", 4.5, 3)],
    "Tyrese Maxey": [("Joel Embiid", 3.8, 2), ("Paul George", 3.2, 4)],
    "Giannis Antetokounmpo": [("Damian Lillard", 5.8, 4), ("Khris Middleton", 4.2, 5)],
    "Damian Lillard": [("Giannis Antetokounmpo", 4.5, 3), ("Khris Middleton", 3.5, 5)],
    "Jayson Tatum": [("Jaylen Brown", 5.5, 4), ("Derrick White", 3.8, 5)],
    "Jaylen Brown": [("Jayson Tatum", 4.2, 3), ("Derrick White", 3.5, 5)],
    "Shai Gilgeous-Alexander": [("Jalen Williams", 6.5, 5), ("Chet Holmgren", 4.2, 4)],
    "Luka Doncic": [("Kyrie Irving", 6.8, 4), ("PJ Washington", 3.5, 5)],
    "Stephen Curry": [("Andrew Wiggins", 5.2, 4), ("Klay Thompson", 4.5, 3)],
    "Donovan Mitchell": [("Darius Garland", 5.8, 4), ("Evan Mobley", 3.5, 4)],
    "Trae Young": [("Dejounte Murray", 5.5, 4), ("Jalen Johnson", 4.2, 5)],
    "Ja Morant": [("Desmond Bane", 6.2, 5), ("Jaren Jackson Jr.", 4.8, 3)],
    "Anthony Edwards": [("Karl-Anthony Towns", 4.5, 3), ("Rudy Gobert", 2.8, 2)],
    "Jalen Brunson": [("Julius Randle", 5.2, 3), ("RJ Barrett", 4.5, 5)],
    "Jimmy Butler": [("Bam Adebayo", 5.5, 4), ("Tyler Herro", 4.8, 5)],
    "Kawhi Leonard": [("Paul George", 5.8, 4), ("James Harden", 4.2, 3)],
}

# Player average stats for projection calculation (fallback data)
PLAYER_AVG_STATS = {
    "Anthony Davis": {"pts": 24.5, "ast": 3.2, "reb": 12.5, "pra": 40.2},
    "Austin Reaves": {"pts": 15.8, "ast": 5.2, "reb": 4.3, "pra": 25.3},
    "Devin Booker": {"pts": 27.2, "ast": 6.8, "reb": 4.5, "pra": 38.5},
    "Bradley Beal": {"pts": 18.5, "ast": 5.2, "reb": 4.2, "pra": 27.9},
    "Tyrese Maxey": {"pts": 25.5, "ast": 6.2, "reb": 3.8, "pra": 35.5},
    "Paul George": {"pts": 22.8, "ast": 5.2, "reb": 5.8, "pra": 33.8},
    "Damian Lillard": {"pts": 24.8, "ast": 7.2, "reb": 4.5, "pra": 36.5},
    "Khris Middleton": {"pts": 15.2, "ast": 5.5, "reb": 4.8, "pra": 25.5},
    "Jaylen Brown": {"pts": 23.5, "ast": 3.8, "reb": 5.5, "pra": 32.8},
    "Derrick White": {"pts": 15.8, "ast": 5.2, "reb": 4.2, "pra": 25.2},
    "Jalen Williams": {"pts": 20.2, "ast": 5.5, "reb": 5.8, "pra": 31.5},
    "Chet Holmgren": {"pts": 16.5, "ast": 2.8, "reb": 8.2, "pra": 27.5},
    "Kyrie Irving": {"pts": 25.5, "ast": 5.2, "reb": 5.0, "pra": 35.7},
    "PJ Washington": {"pts": 12.5, "ast": 2.2, "reb": 7.2, "pra": 21.9},
    "Andrew Wiggins": {"pts": 17.2, "ast": 2.8, "reb": 5.2, "pra": 25.2},
    "Klay Thompson": {"pts": 17.8, "ast": 2.2, "reb": 3.8, "pra": 23.8},
    "Darius Garland": {"pts": 21.5, "ast": 6.8, "reb": 2.8, "pra": 31.1},
    "Evan Mobley": {"pts": 18.2, "ast": 3.2, "reb": 9.2, "pra": 30.6},
    "Dejounte Murray": {"pts": 22.5, "ast": 6.5, "reb": 5.2, "pra": 34.2},
    "Jalen Johnson": {"pts": 16.2, "ast": 3.8, "reb": 8.5, "pra": 28.5},
    "Desmond Bane": {"pts": 23.2, "ast": 4.8, "reb": 4.5, "pra": 32.5},
    "Jaren Jackson Jr.": {"pts": 22.5, "ast": 2.2, "reb": 5.8, "pra": 30.5},
    "Karl-Anthony Towns": {"pts": 25.2, "ast": 3.2, "reb": 8.8, "pra": 37.2},
    "Rudy Gobert": {"pts": 14.2, "ast": 1.5, "reb": 12.8, "pra": 28.5},
    "Julius Randle": {"pts": 24.2, "ast": 5.2, "reb": 9.2, "pra": 38.6},
    "RJ Barrett": {"pts": 19.5, "ast": 3.2, "reb": 5.5, "pra": 28.2},
    "Bam Adebayo": {"pts": 19.8, "ast": 4.5, "reb": 10.2, "pra": 34.5},
    "Tyler Herro": {"pts": 20.8, "ast": 4.8, "reb": 5.2, "pra": 30.8},
    "James Harden": {"pts": 16.8, "ast": 8.5, "reb": 5.2, "pra": 30.5},
}


# =============================================================================
# INJURY VACUUM SERVICE CLASS
# =============================================================================

class InjuryVacuumService:
    """
    Event-driven microservice for monitoring NBA injuries and calculating
    usage vacuum beneficiaries for Ferrari Score adjustments.
    """
    
    def __init__(self, db=None, redis_client=None):
        self.db = db
        self.redis = redis_client
        
        # In-memory caches (fallback if Redis unavailable)
        self.star_profiles_cache: Dict[str, Dict] = {}
        self.injury_status_cache: Dict[str, Dict] = {}
        self.beneficiary_cache: Dict[str, List[Dict]] = {}
        self.active_vacuums: Dict[str, Dict] = {}  # Currently active usage vacuums
        
        # Timestamps
        self.last_injury_check: Optional[datetime] = None
        self.last_vacuum_update: Optional[datetime] = None
        
        # MongoDB collections
        if db is not None:
            self.injury_log = db.injury_log
            self.vacuum_alerts = db.vacuum_alerts
    
    async def _fetch_json(self, url: str, timeout: int = 15) -> Optional[Dict]:
        """Fetch JSON from URL with error handling."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=timeout) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.warning(f"[VacuumService] HTTP {response.status} for {url}")
                        return None
        except asyncio.TimeoutError:
            logger.error(f"[VacuumService] Timeout fetching {url}")
            return None
        except Exception as e:
            logger.error(f"[VacuumService] Error fetching {url}: {e}")
            return None
    
    def _normalize_player_name(self, name: str) -> str:
        """Normalize player name for matching."""
        if not name:
            return ""
        # Remove suffixes like Jr., III, etc.
        name = re.sub(r'\s+(Jr\.?|Sr\.?|III|II|IV)$', '', name, flags=re.IGNORECASE)
        return " ".join(name.strip().split())
    
    def _is_star_player(self, player_name: str) -> Tuple[bool, Optional[Dict]]:
        """
        Check if player is a star (Usage > 25%).
        Returns (is_star, profile_data).
        """
        normalized = self._normalize_player_name(player_name)
        
        # Check cache first
        if normalized in self.star_profiles_cache:
            profile = self.star_profiles_cache[normalized]
            return profile.get("usage_rate", 0) >= STAR_USAGE_THRESHOLD, profile
        
        # Check fallback data
        for star_name, profile in STAR_USAGE_PROFILES.items():
            if self._normalize_player_name(star_name) == normalized:
                self.star_profiles_cache[normalized] = {**profile, "name": star_name}
                return True, {**profile, "name": star_name}
        
        return False, None
    
    def _get_beneficiaries(self, injured_player: str) -> List[Dict]:
        """
        Get the top 2 beneficiaries for an injured star player.
        Returns list of beneficiary dicts with name, usage_bump, minutes_bump, modifier, and projections.
        """
        normalized = self._normalize_player_name(injured_player)
        
        # Check beneficiary mappings
        for star_name, beneficiaries in BENEFICIARY_MAPPINGS.items():
            if self._normalize_player_name(star_name) == normalized:
                result = []
                for i, beneficiary_data in enumerate(beneficiaries[:2]):
                    beneficiary_name = beneficiary_data[0]
                    usage_bump = beneficiary_data[1]
                    minutes_bump = beneficiary_data[2] if len(beneficiary_data) > 2 else 4
                    
                    modifier = PRIMARY_BENEFICIARY_MODIFIER if i == 0 else SECONDARY_BENEFICIARY_MODIFIER
                    usage_multiplier = PRIMARY_USAGE_MULTIPLIER if i == 0 else SECONDARY_USAGE_MULTIPLIER
                    
                    # Calculate projected stats with usage boost
                    projections = self._calculate_boosted_projections(beneficiary_name, usage_multiplier, minutes_bump)
                    
                    result.append({
                        "name": beneficiary_name,
                        "usage_bump": usage_bump,
                        "minutes_bump": minutes_bump,
                        "modifier": modifier,
                        "rank": "primary" if i == 0 else "secondary",
                        "usage_multiplier": usage_multiplier,
                        "projections": projections,
                        "high_usage_advantage": True,  # Badge flag
                        "late_injury_boost": True
                    })
                return result
        
        return []
    
    def _calculate_boosted_projections(self, player_name: str, usage_multiplier: float, minutes_bump: int) -> Dict[str, float]:
        """
        Calculate projected stats with usage boost applied.
        
        Uses the usage multiplier to increase projected stats:
        - Primary beneficiary: +25% usage = ~15-20% stat increase
        - Secondary beneficiary: +15% usage = ~10-12% stat increase
        """
        normalized = self._normalize_player_name(player_name)
        
        # Get base stats
        base_stats = None
        for name, stats in PLAYER_AVG_STATS.items():
            if self._normalize_player_name(name) == normalized:
                base_stats = stats
                break
        
        if not base_stats:
            # Fallback: use generic averages
            base_stats = {"pts": 15.0, "ast": 3.5, "reb": 5.0, "pra": 23.5}
        
        # Calculate boost factor (usage multiplier affects stats proportionally but not 1:1)
        # Typically 25% usage increase = ~15% stat increase
        stat_boost_factor = 1 + ((usage_multiplier - 1) * 0.6)  # Dampen the boost slightly
        
        # Add minutes-based boost (more minutes = proportionally more stats)
        # Assume average 32 mins/game, each additional minute adds ~3% stats
        minutes_boost = 1 + (minutes_bump * 0.03)
        
        total_boost = stat_boost_factor * minutes_boost
        
        return {
            "pts": round(base_stats.get("pts", 15) * total_boost, 1),
            "ast": round(base_stats.get("ast", 3.5) * total_boost, 1),
            "reb": round(base_stats.get("reb", 5) * total_boost, 1),
            "pra": round(base_stats.get("pra", 23.5) * total_boost, 1),
            "boost_percentage": round((total_boost - 1) * 100, 1)
        }
    
    def check_board_promotion(self, beneficiary: Dict, current_lines: Dict[str, float] = None) -> Dict[str, Any]:
        """
        Check if a beneficiary should be promoted to the board.
        
        Promotion criteria: projected stat is >15% higher than current line.
        
        Args:
            beneficiary: Beneficiary dict with projections
            current_lines: Dict of current prop lines {stat_type: line_value}
            
        Returns:
            Dict with promotion status and eligible props
        """
        projections = beneficiary.get("projections", {})
        
        if not current_lines:
            # Default lines for testing (typical Vegas lines)
            current_lines = {
                "pts": projections.get("pts", 15) * 0.9,  # Assume line is ~90% of projection
                "ast": projections.get("ast", 3.5) * 0.9,
                "reb": projections.get("reb", 5) * 0.9,
                "pra": projections.get("pra", 23.5) * 0.9
            }
        
        eligible_props = []
        
        for stat_type, projected in projections.items():
            if stat_type == "boost_percentage":
                continue
                
            line = current_lines.get(stat_type, projected * 0.9)
            
            if line > 0:
                edge = (projected - line) / line
                
                if edge >= BOARD_PROMOTION_THRESHOLD:
                    eligible_props.append({
                        "stat_type": stat_type.upper(),
                        "projected": projected,
                        "line": line,
                        "edge_percentage": round(edge * 100, 1),
                        "promote": True
                    })
        
        return {
            "should_promote": len(eligible_props) > 0,
            "eligible_props": eligible_props,
            "top_edge_stat": eligible_props[0] if eligible_props else None
        }
    
    async def fetch_injury_report(self) -> List[Dict]:
        """
        Fetch the latest NBA injury report from dg_injuries collection (ESPN sourced).
        Falls back to bdl_injuries if dg_injuries is empty.
        """
        logger.info("[VacuumService] Fetching injury data from database...")
        
        injuries = []
        
        try:
            # Primary source: dg_injuries (ESPN data, more complete with team info)
            if hasattr(self, 'db') and self.db is not None:
                try:
                    dg_cursor = self.db.dg_injuries.find({
                        "status": {"$in": ["Out", "OUT", "Doubtful", "DOUBTFUL", "Day-To-Day"]}
                    })
                    dg_injuries = await dg_cursor.to_list(length=200)
                except Exception as db_err:
                    logger.warning(f"[VacuumService] dg_injuries query failed: {db_err}")
                    dg_injuries = []
                
                for inj in dg_injuries:
                    player_name = inj.get("player_name", "")
                    team = inj.get("team", "UNK")
                    status = inj.get("status", "").upper()
                    reason = inj.get("short_comment", "") or inj.get("description", "")[:100]
                    
                    injuries.append({
                        "player_name": player_name,
                        "team": team,
                        "team_name": inj.get("team_full", ""),
                        "status": status,
                        "reason": reason,
                        "updated_at": inj.get("synced_at", datetime.now(timezone.utc).isoformat())
                    })
                
                logger.info(f"[VacuumService] Found {len(injuries)} injuries from dg_injuries")
                
                # If dg_injuries is empty, try bdl_injuries
                if len(injuries) == 0:
                    logger.info("[VacuumService] dg_injuries empty, checking bdl_injuries...")
                    bdl_cursor = self.db.bdl_injuries.find({
                        "status": {"$in": ["Out", "OUT", "Out For Season", "Doubtful", "DOUBTFUL"]}
                    })
                    bdl_injuries = await bdl_cursor.to_list(length=200)
                    
                    for inj in bdl_injuries:
                        player_name = inj.get("player_name", "")
                        status = inj.get("status", "").upper()
                        if "SEASON" in status:
                            status = "OUT"  # Normalize "Out For Season" to "OUT"
                        
                        injuries.append({
                            "player_name": player_name,
                            "team": inj.get("team", "UNK") or "UNK",
                            "team_name": "",
                            "status": status,
                            "reason": inj.get("injury_type", ""),
                            "updated_at": str(inj.get("synced_at", datetime.now(timezone.utc).isoformat()))
                        })
                    
                    logger.info(f"[VacuumService] Found {len(injuries)} injuries from bdl_injuries")
        except Exception as e:
            logger.error(f"[VacuumService] Error fetching from database: {e}")
        
        if len(injuries) > 0:
            return injuries
        
        logger.warning("[VacuumService] Could not fetch injury data from database, using fallback")
        return self._get_fallback_injuries()
    
    def _get_fallback_injuries(self) -> List[Dict]:
        """Return fallback injury data for testing - includes current known injuries."""
        return [
            # High-usage stars currently out (from BDL data)
            {
                "player_name": "Joel Embiid",
                "team": "PHI",
                "status": "OUT",
                "reason": "Knee - Injury Management",
                "updated_at": datetime.now(timezone.utc).isoformat()
            },
            {
                "player_name": "Anthony Davis",
                "team": "LAL",
                "status": "OUT",
                "reason": "Injury Management",
                "updated_at": datetime.now(timezone.utc).isoformat()
            },
            {
                "player_name": "Kyrie Irving",
                "team": "DAL",
                "status": "OUT",
                "reason": "Injury",
                "updated_at": datetime.now(timezone.utc).isoformat()
            },
            {
                "player_name": "Damian Lillard",
                "team": "MIL",
                "status": "OUT",
                "reason": "Injury",
                "updated_at": datetime.now(timezone.utc).isoformat()
            },
            {
                "player_name": "Tyrese Haliburton",
                "team": "IND",
                "status": "OUT",
                "reason": "Injury",
                "updated_at": datetime.now(timezone.utc).isoformat()
            },
            {
                "player_name": "Domantas Sabonis",
                "team": "SAC",
                "status": "OUT",
                "reason": "Injury",
                "updated_at": datetime.now(timezone.utc).isoformat()
            },
            {
                "player_name": "Fred VanVleet",
                "team": "HOU",
                "status": "OUT",
                "reason": "Injury",
                "updated_at": datetime.now(timezone.utc).isoformat()
            },
            {
                "player_name": "D'Angelo Russell",
                "team": "BKN",
                "status": "OUT",
                "reason": "Injury",
                "updated_at": datetime.now(timezone.utc).isoformat()
            },
            {
                "player_name": "Jimmy Butler",
                "team": "MIA",
                "status": "OUT",
                "reason": "Injury",
                "updated_at": datetime.now(timezone.utc).isoformat()
            },
            {
                "player_name": "Ja Morant",
                "team": "MEM",
                "status": "OUT",
                "reason": "Injury",
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
        ]
    
    async def check_injuries(self) -> Dict[str, Any]:
        """
        Main injury check task with REACTIVE RE-SCANNING.
        
        When a player with usage > 20% is marked "OUT":
        1. Triggers ReScanEvent for that team
        2. Redistributes minutes and usage to beneficiaries
        3. Calculates boosted projections
        4. Checks for board promotion (projected > 15% above line)
        5. Tags promoted players with "high_usage_advantage" badge
        
        Returns:
            Dict with triggered vacuums, board promotions, and status changes.
        """
        logger.info("=" * 60)
        logger.info("[VACUUM SERVICE] REACTIVE INJURY SCAN v2.0")
        logger.info("=" * 60)
        
        result = {
            "success": True,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "injuries_found": 0,
            "status_changes": [],
            "vacuums_triggered": [],
            "beneficiaries": [],
            "board_promotions": [],
            "rescan_events": []
        }
        
        try:
            # Fetch current injury report
            current_injuries = await self.fetch_injury_report()
            result["injuries_found"] = len(current_injuries)
            
            # Check for status changes
            for injury in current_injuries:
                player_name = injury.get("player_name", "")
                current_status = injury.get("status", "").upper()
                team = injury.get("team", "")
                
                # Get cached status
                cached = self.injury_status_cache.get(player_name, {})
                previous_status = cached.get("status", "")
                
                # Check if status changed to OUT or DOUBTFUL
                if current_status in TRIGGER_STATUSES and current_status != previous_status:
                    logger.info(f"[VacuumService] Status change: {player_name} -> {current_status}")
                    
                    # Check if this is a star player (usage > 20%)
                    is_star, star_profile = self._is_star_player(player_name)
                    
                    if is_star:
                        usage_rate = star_profile.get("usage_rate", 0)
                        logger.info(f"[VacuumService] *** LATE SCRATCH DETECTED ***")
                        logger.info(f"[VacuumService] STAR PLAYER OUT: {player_name} (Usage: {usage_rate}%)")
                        
                        # TRIGGER RESCAN EVENT
                        rescan_event = {
                            "team": team,
                            "triggered_by": player_name,
                            "usage_rate": usage_rate,
                            "triggered_at": datetime.now(timezone.utc).isoformat(),
                            "event_type": "late_scratch_rescan"
                        }
                        result["rescan_events"].append(rescan_event)
                        logger.info(f"[VacuumService] ReScanEvent triggered for team {team}")
                        
                        # Get beneficiaries with boosted projections
                        beneficiaries = self._get_beneficiaries(player_name)
                        
                        if beneficiaries:
                            # Check for board promotions for each beneficiary
                            for beneficiary in beneficiaries:
                                promotion = self.check_board_promotion(beneficiary)
                                
                                if promotion.get("should_promote"):
                                    beneficiary["board_promotion"] = promotion
                                    result["board_promotions"].append({
                                        "player_name": beneficiary.get("name"),
                                        "injured_star": player_name,
                                        "eligible_props": promotion.get("eligible_props", []),
                                        "top_edge": promotion.get("top_edge_stat"),
                                        "high_usage_advantage": True
                                    })
                                    logger.info(f"[VacuumService] BOARD PROMOTION: {beneficiary.get('name')} - {promotion.get('eligible_props')}")
                            
                            vacuum_alert = {
                                "injured_player": player_name,
                                "team": team,
                                "status": current_status,
                                "reason": injury.get("reason"),
                                "usage_rate": usage_rate,
                                "beneficiaries": beneficiaries,
                                "triggered_at": datetime.now(timezone.utc).isoformat(),
                                "confirmed_at": datetime.now(timezone.utc).isoformat(),
                                "is_late_scratch": True,
                                "rescan_triggered": True
                            }
                            
                            # Store in active vacuums
                            self.active_vacuums[player_name] = vacuum_alert
                            
                            result["vacuums_triggered"].append(vacuum_alert)
                            result["beneficiaries"].extend(beneficiaries)
                            
                            # Log to MongoDB
                            if hasattr(self, 'db') and self.db is not None:
                                try:
                                    await self._log_vacuum_alert(vacuum_alert)
                                except Exception as log_err:
                                    logger.warning(f"[VacuumService] Failed to log vacuum: {log_err}")
                    
                    result["status_changes"].append({
                        "player": player_name,
                        "from": previous_status,
                        "to": current_status,
                        "is_star": is_star,
                        "team": team
                    })
                
                # Update cache
                self.injury_status_cache[player_name] = {
                    "status": current_status,
                    "reason": injury.get("reason"),
                    "team": injury.get("team"),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
            
            self.last_injury_check = datetime.now(timezone.utc)
            
            logger.info(f"[VacuumService] Check complete: {len(result['vacuums_triggered'])} vacuums triggered")
            
        except Exception as e:
            logger.error(f"[VacuumService] Error checking injuries: {e}")
            result["success"] = False
            result["error"] = str(e)
        
        return result
    
    async def _log_vacuum_alert(self, alert: Dict):
        """Log vacuum alert to MongoDB (optional, non-blocking)."""
        # Skip MongoDB logging for now to avoid serialization issues
        logger.info(f"[VacuumService] Vacuum alert: {alert.get('injured_player')} - {len(alert.get('beneficiaries', []))} beneficiaries")
    
    async def _log_injury_change(self, player: str, from_status: str, to_status: str, team: str):
        """Log injury status change to MongoDB."""
        try:
            await self.injury_log.insert_one({
                "player_name": player,
                "team": team,
                "from_status": from_status,
                "to_status": to_status,
                "changed_at": datetime.now(timezone.utc).isoformat()
            })
        except Exception as e:
            logger.error(f"[VacuumService] Error logging injury change: {e}")
    
    def get_active_vacuums(self) -> List[Dict]:
        """Get all currently active usage vacuums."""
        return list(self.active_vacuums.values())
    
    def get_vacuum_for_player(self, player_name: str) -> Optional[Dict]:
        """Check if a player is a beneficiary of any active vacuum."""
        normalized = self._normalize_player_name(player_name)
        
        for vacuum in self.active_vacuums.values():
            for beneficiary in vacuum.get("beneficiaries", []):
                if self._normalize_player_name(beneficiary.get("name", "")) == normalized:
                    return {
                        "injured_player": vacuum.get("injured_player"),
                        "injured_team": vacuum.get("team"),
                        "injured_usage": vacuum.get("usage_rate"),
                        "beneficiary_rank": beneficiary.get("rank"),
                        "usage_bump": beneficiary.get("usage_bump"),
                        "modifier": beneficiary.get("modifier"),
                        "triggered_at": vacuum.get("triggered_at"),
                        "confirmed_at": vacuum.get("confirmed_at"),
                        "reason": vacuum.get("reason")
                    }
        
        return None
    
    def calculate_vacuum_modifier(self, player_name: str) -> Tuple[float, Optional[Dict]]:
        """
        Calculate the Ferrari Score modifier for a player due to usage vacuum.
        
        Returns:
            (modifier_value, vacuum_details) or (0.0, None) if no vacuum applies.
        """
        vacuum_data = self.get_vacuum_for_player(player_name)
        
        if vacuum_data:
            modifier = vacuum_data.get("modifier", 0.0)
            return modifier, vacuum_data
        
        return 0.0, None
    
    async def get_vacuum_updates(self) -> Dict[str, Any]:
        """
        Get the current vacuum state for the Ferrari Engine.
        This is the payload sent to /api/v3/vacuum/updates.
        """
        active_vacuums = self.get_active_vacuums()
        
        # Build beneficiary list with modifiers
        all_beneficiaries = []
        for vacuum in active_vacuums:
            for beneficiary in vacuum.get("beneficiaries", []):
                all_beneficiaries.append({
                    "player_name": beneficiary.get("name"),
                    "injured_star": vacuum.get("injured_player"),
                    "injured_team": vacuum.get("team"),
                    "modifier": beneficiary.get("modifier"),
                    "usage_bump": beneficiary.get("usage_bump"),
                    "rank": beneficiary.get("rank")
                })
        
        return {
            "has_updates": len(active_vacuums) > 0,
            "active_vacuums": active_vacuums,
            "beneficiaries": all_beneficiaries,
            "total_beneficiaries": len(all_beneficiaries),
            "last_check": self.last_injury_check.isoformat() if self.last_injury_check else None,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    def clear_vacuum(self, injured_player: str) -> bool:
        """Clear an active vacuum (when player returns to lineup)."""
        normalized = self._normalize_player_name(injured_player)
        
        for player_name in list(self.active_vacuums.keys()):
            if self._normalize_player_name(player_name) == normalized:
                del self.active_vacuums[player_name]
                logger.info(f"[VacuumService] Cleared vacuum for {injured_player}")
                return True
        
        return False
    
    async def sync_star_profiles(self) -> Dict[str, Any]:
        """
        Sync star player usage profiles from NBA API or fallback data.
        This would normally query leaguedashplayerstats for real usage rates.
        """
        logger.info("[VacuumService] Syncing star usage profiles...")
        
        # For now, use fallback data
        for player_name, profile in STAR_USAGE_PROFILES.items():
            self.star_profiles_cache[self._normalize_player_name(player_name)] = {
                **profile,
                "name": player_name
            }
        
        return {
            "success": True,
            "profiles_synced": len(self.star_profiles_cache),
            "synced_at": datetime.now(timezone.utc).isoformat()
        }


# =============================================================================
# SINGLETON INSTANCE
# =============================================================================

_vacuum_service: Optional[InjuryVacuumService] = None


def get_vacuum_service(db=None) -> InjuryVacuumService:
    """Get or create the InjuryVacuumService singleton."""
    global _vacuum_service
    if _vacuum_service is None:
        _vacuum_service = InjuryVacuumService(db)
    elif db is not None and not hasattr(_vacuum_service, 'db'):
        _vacuum_service.db = db
        _vacuum_service.injury_log = db.injury_log
        _vacuum_service.vacuum_alerts = db.vacuum_alerts
    elif db is not None and _vacuum_service.db is None:
        _vacuum_service.db = db
        _vacuum_service.injury_log = db.injury_log
        _vacuum_service.vacuum_alerts = db.vacuum_alerts
    return _vacuum_service
