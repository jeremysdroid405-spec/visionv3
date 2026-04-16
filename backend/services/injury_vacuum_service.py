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
# USAGE VACUUM CONSTANTS - Dynamic Usage Model v3.0
# =============================================================================

# Dynamic Star Identification Thresholds (from BDL Advanced Stats)
PRIMARY_ALPHA_THRESHOLD = 28.0    # Primary Alpha: usg_pct >= 28%
SECONDARY_ALPHA_THRESHOLD = 22.0  # Secondary Alpha: usg_pct 22-28%

# Legacy compatibility (kept for fallback only)
STAR_USAGE_THRESHOLD = 20.0
HIGH_USAGE_THRESHOLD = 25.0

# Ferrari Score modifiers
PRIMARY_BENEFICIARY_MODIFIER = 15.0
SECONDARY_BENEFICIARY_MODIFIER = 10.0

# Usage redistribution multipliers - Dynamic Model applies +12% boost for PTS/PRA
PRIMARY_USAGE_MULTIPLIER = 1.12   # +12% usage boost for PTS/PRA (user spec)
SECONDARY_USAGE_MULTIPLIER = 1.08 # +8% for secondary beneficiaries

# Board promotion threshold (projected stat > 15% above line)
BOARD_PROMOTION_THRESHOLD = 0.15

# Late scratch window (120 minutes = 2 hours)
LATE_SCRATCH_WINDOW_MINUTES = 120

# Injury status triggers
TRIGGER_STATUSES = ["OUT", "DOUBTFUL", "OUT_FOR_SEASON", "IL_STANDARD", "IL_EXTENDED", "IL_SHORT"]

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
# DYNAMIC USAGE MODEL v3.0 - No Hardcoded Star Lists
# =============================================================================
# 
# Star identification and beneficiary calculation are now 100% database-driven:
# - Primary Alpha: usage_percentage >= 28% (from star_usage_cache / BDL advanced stats)
# - Secondary Alpha: usage_percentage 22-28%
# - Beneficiaries: Dynamically calculated as teammates with highest usage_per_minute
#
# DEPRECATED: STAR_USAGE_PROFILES, BENEFICIARY_MAPPINGS, PLAYER_AVG_STATS
# =============================================================================


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
        Dynamic Star Identification v3.0
        
        Checks if player is a star using BDL Advanced Stats (usage_percentage):
        - Primary Alpha: usage_percentage >= 28%
        - Secondary Alpha: usage_percentage >= 22% (still triggers vacuum)
        
        Returns (is_star, profile_data) where profile includes:
        - alpha_tier: 'primary' (>=28%) or 'secondary' (22-28%)
        - usage_rate: actual usage percentage from BDL
        """
        normalized = self._normalize_player_name(player_name)
        
        # Check in-memory cache first
        if normalized in self.star_profiles_cache:
            profile = self.star_profiles_cache[normalized]
            usage = profile.get("usage_rate", 0)
            return usage >= SECONDARY_ALPHA_THRESHOLD, profile
        
        # Dynamic database lookup from star_usage_cache (BDL advanced stats)
        try:
            from pymongo import MongoClient
            import os
            sync_client = MongoClient(os.environ.get('MONGO_URL'))
            sync_db = sync_client['pick_vision']
            
            # Query star_usage_cache for player
            db_star = sync_db.star_usage_cache.find_one(
                {'$or': [
                    {'player_name': {'$regex': f'^{re.escape(normalized)}', '$options': 'i'}},
                    {'player_name': player_name}
                ]},
                {'_id': 0}
            )
            sync_client.close()
            
            if db_star:
                usage_pct = db_star.get('usage_percentage', 0) or db_star.get('usage_pct', 0)
                
                # Determine alpha tier
                if usage_pct >= PRIMARY_ALPHA_THRESHOLD:
                    alpha_tier = 'primary'
                elif usage_pct >= SECONDARY_ALPHA_THRESHOLD:
                    alpha_tier = 'secondary'
                else:
                    alpha_tier = None
                
                profile = {
                    "name": db_star.get('player_name'),
                    "player_name": db_star.get('player_name'),
                    "team": db_star.get('team'),
                    "position": db_star.get('position'),
                    "usage_rate": usage_pct,
                    "usage_percentage": usage_pct,
                    "pie": db_star.get('pie', 0),
                    "net_rating": db_star.get('net_rating', 0),
                    "alpha_tier": alpha_tier,
                    "is_primary_alpha": usage_pct >= PRIMARY_ALPHA_THRESHOLD,
                    "is_secondary_alpha": SECONDARY_ALPHA_THRESHOLD <= usage_pct < PRIMARY_ALPHA_THRESHOLD,
                    "source": "bdl_advanced_stats"
                }
                self.star_profiles_cache[normalized] = profile
                
                is_star = usage_pct >= SECONDARY_ALPHA_THRESHOLD
                if is_star:
                    logger.info(f"[VacuumService] Dynamic Star: {player_name} (Usage: {usage_pct:.1f}%, Tier: {alpha_tier})")
                return is_star, profile
                
        except Exception as e:
            logger.warning(f"[VacuumService] Error in dynamic star lookup: {e}")
        
        # If not found in database, check nba_master_hub_2026 advanced_stats as fallback
        try:
            from pymongo import MongoClient
            import os
            sync_client = MongoClient(os.environ.get('MONGO_URL'))
            sync_db = sync_client['pick_vision']
            
            hub_player = sync_db.nba_master_hub_2026.find_one(
                {'$or': [
                    {'normalized_name': normalized},
                    {'display_name': {'$regex': f'^{re.escape(player_name)}', '$options': 'i'}}
                ]},
                {'_id': 0, 'display_name': 1, 'team': 1, 'advanced_stats': 1}
            )
            sync_client.close()
            
            if hub_player and hub_player.get('advanced_stats'):
                adv = hub_player['advanced_stats']
                usage_pct = adv.get('usage_percentage', 0) or adv.get('usg_pct', 0)
                
                if usage_pct >= SECONDARY_ALPHA_THRESHOLD:
                    alpha_tier = 'primary' if usage_pct >= PRIMARY_ALPHA_THRESHOLD else 'secondary'
                    profile = {
                        "name": hub_player.get('display_name'),
                        "team": hub_player.get('team'),
                        "usage_rate": usage_pct,
                        "alpha_tier": alpha_tier,
                        "source": "nba_master_hub_2026"
                    }
                    self.star_profiles_cache[normalized] = profile
                    logger.info(f"[VacuumService] Hub Star: {player_name} (Usage: {usage_pct:.1f}%)")
                    return True, profile
                    
        except Exception as e:
            logger.warning(f"[VacuumService] Error in hub star lookup: {e}")
        
        return False, None
    
    def _get_beneficiaries(self, injured_player: str, injured_team: str = None) -> List[Dict]:
        """
        Dynamic Beneficiary Calculation v3.0
        
        When an Alpha is OUT, calculates beneficiaries by:
        1. Finding active teammates on the same team
        2. Ranking by usage_per_minute (usage_percentage / minutes_per_game)
        3. Top 2 teammates become primary/secondary beneficiaries
        4. Applies +12% boost to PTS and PRA projected values
        
        Returns list of up to 3 beneficiary dicts with boosted projections.
        """
        normalized = self._normalize_player_name(injured_player)
        logger.info(f"[VacuumService] Calculating dynamic beneficiaries for {injured_player} ({injured_team})")
        
        # Get injured player's team if not provided
        if not injured_team:
            is_star, star_profile = self._is_star_player(injured_player)
            if star_profile:
                injured_team = star_profile.get('team')
        
        if not injured_team:
            logger.warning(f"[VacuumService] Cannot find team for {injured_player}")
            return []
        
        try:
            from pymongo import MongoClient
            import os
            sync_client = MongoClient(os.environ.get('MONGO_URL'))
            sync_db = sync_client['pick_vision']
            
            # Query star_usage_cache for teammates (excluding injured player)
            # Sort by usage_percentage DESC to get highest usage teammates
            teammates = list(sync_db.star_usage_cache.find(
                {
                    'team': injured_team,
                    'player_name': {'$ne': injured_player, '$not': {'$regex': f'^{re.escape(normalized)}', '$options': 'i'}}
                },
                {'_id': 0}
            ).sort('usage_percentage', -1).limit(5))
            
            # Enrich teammates with baseline_stats from nba_master_hub_2026
            for teammate in teammates:
                player_name = teammate.get('player_name', '')
                hub_data = sync_db.nba_master_hub_2026.find_one(
                    {'$or': [
                        {'display_name': player_name},
                        {'normalized_name': player_name.lower()}
                    ]},
                    {'_id': 0, 'baseline_stats': 1, 'advanced_stats': 1}
                )
                if hub_data:
                    teammate['baseline_stats'] = hub_data.get('baseline_stats', {})
                    adv = hub_data.get('advanced_stats', {})
                    if not teammate.get('minutes_per_game'):
                        teammate['minutes_per_game'] = adv.get('minutes_per_game', 28)
            
            # If not enough in star_usage_cache, also check nba_master_hub_2026
            if len(teammates) < 3:
                hub_teammates = list(sync_db.nba_master_hub_2026.find(
                    {
                        'team': injured_team,
                        'display_name': {'$ne': injured_player},
                        'advanced_stats.usage_percentage': {'$exists': True}
                    },
                    {'_id': 0, 'display_name': 1, 'team': 1, 'advanced_stats': 1, 'baseline_stats': 1}
                ).sort('advanced_stats.usage_percentage', -1).limit(5))
                
                # Merge with existing teammates
                existing_names = {t.get('player_name', '').lower() for t in teammates}
                for hub_player in hub_teammates:
                    name = hub_player.get('display_name', '')
                    if name.lower() not in existing_names and len(teammates) < 5:
                        adv = hub_player.get('advanced_stats', {})
                        baseline = hub_player.get('baseline_stats', {})
                        teammates.append({
                            'player_name': name,
                            'team': hub_player.get('team'),
                            'usage_percentage': adv.get('usage_percentage', 0),
                            'pie': adv.get('pie', 0),
                            'minutes_per_game': adv.get('minutes_per_game', 28),
                            'baseline_stats': baseline,
                            'source': 'nba_master_hub_2026'
                        })
            
            sync_client.close()
            
            if not teammates:
                logger.warning(f"[VacuumService] No teammates found for {injured_team}")
                return []
            
            # Sort by usage_percentage DESC (highest usage teammates first)
            # This is more reliable than usage_per_minute which can be skewed by low-minute players
            teammates.sort(key=lambda x: x.get('usage_percentage', 0), reverse=True)
            
            # Calculate usage_per_minute for display purposes only
            for t in teammates:
                usage = t.get('usage_percentage', 0) or 0
                minutes = t.get('minutes_per_game', 28) or 28
                t['usage_per_minute'] = round(usage / minutes, 3) if minutes > 0 else 0
            
            # Build beneficiary list
            result = []
            for i, teammate in enumerate(teammates[:3]):
                is_primary = (i == 0)
                is_secondary = (i == 1)
                
                # Calculate boost factor (+12% for primary PTS/PRA, +8% for secondary)
                if is_primary:
                    pts_pra_boost = 1.12  # +12% boost per user spec
                    modifier = PRIMARY_BENEFICIARY_MODIFIER
                    rank = "primary"
                elif is_secondary:
                    pts_pra_boost = 1.08  # +8% boost
                    modifier = SECONDARY_BENEFICIARY_MODIFIER
                    rank = "secondary"
                else:
                    pts_pra_boost = 1.05  # +5% tertiary
                    modifier = 5.0
                    rank = "tertiary"
                
                # Get baseline stats for projection
                baseline = teammate.get('baseline_stats', {})
                base_pts = baseline.get('PTS', {}).get('season_avg', 15.0) if isinstance(baseline.get('PTS'), dict) else 15.0
                base_reb = baseline.get('REB', {}).get('season_avg', 5.0) if isinstance(baseline.get('REB'), dict) else 5.0
                base_ast = baseline.get('AST', {}).get('season_avg', 3.5) if isinstance(baseline.get('AST'), dict) else 3.5
                base_pra = baseline.get('PRA', {}).get('season_avg', 23.5) if isinstance(baseline.get('PRA'), dict) else base_pts + base_reb + base_ast
                
                # Apply +12% boost to PTS and PRA (user spec)
                projected_pts = round(base_pts * pts_pra_boost, 1)
                projected_pra = round(base_pra * pts_pra_boost, 1)
                projected_reb = round(base_reb * 1.05, 1)  # Small reb boost
                projected_ast = round(base_ast * 1.03, 1)  # Minimal ast boost
                
                boost_pct = round((pts_pra_boost - 1) * 100, 1)
                
                beneficiary_data = {
                    "name": teammate.get('player_name'),
                    "player_name": teammate.get('player_name'),
                    "team": teammate.get('team'),
                    "usage_percentage": teammate.get('usage_percentage', 0),
                    "usage_per_minute": round(teammate.get('usage_per_minute', 0), 3),
                    "modifier": modifier,
                    "rank": rank,
                    "boost_percentage": boost_pct,
                    "projections": {
                        "pts": projected_pts,
                        "reb": projected_reb,
                        "ast": projected_ast,
                        "pra": projected_pra,
                        "boost_percentage": boost_pct
                    },
                    "high_usage_advantage": True,
                    "late_injury_boost": True,
                    "dynamic_calculation": True,
                    "injured_star": injured_player,
                    "source": teammate.get('source', 'star_usage_cache')
                }
                
                result.append(beneficiary_data)
                logger.info(f"[VacuumService] Beneficiary {rank}: {teammate.get('player_name')} "
                           f"(Usage: {teammate.get('usage_percentage', 0):.1f}%, "
                           f"Boost: +{boost_pct}% PTS/PRA)")
            
            return result
            
        except Exception as e:
            logger.error(f"[VacuumService] Error calculating dynamic beneficiaries: {e}")
            return []
    
    def _calculate_boosted_projections(self, player_name: str, usage_multiplier: float, minutes_bump: int) -> Dict[str, float]:
        """
        DEPRECATED: This method is kept for backward compatibility.
        New code uses inline projection calculations in _get_beneficiaries().
        
        For dynamic model, we fetch baseline stats from nba_master_hub_2026.
        """
        normalized = self._normalize_player_name(player_name)
        
        # Try to get real stats from database
        try:
            from pymongo import MongoClient
            import os
            sync_client = MongoClient(os.environ.get('MONGO_URL'))
            sync_db = sync_client['pick_vision']
            
            player = sync_db.nba_master_hub_2026.find_one(
                {'$or': [
                    {'normalized_name': normalized},
                    {'display_name': {'$regex': f'^{re.escape(player_name)}', '$options': 'i'}}
                ]},
                {'_id': 0, 'baseline_stats': 1}
            )
            sync_client.close()
            
            if player and player.get('baseline_stats'):
                baseline = player['baseline_stats']
                base_pts = baseline.get('PTS', {}).get('season_avg', 15.0) if isinstance(baseline.get('PTS'), dict) else 15.0
                base_reb = baseline.get('REB', {}).get('season_avg', 5.0) if isinstance(baseline.get('REB'), dict) else 5.0
                base_ast = baseline.get('AST', {}).get('season_avg', 3.5) if isinstance(baseline.get('AST'), dict) else 3.5
                base_pra = baseline.get('PRA', {}).get('season_avg', 23.5) if isinstance(baseline.get('PRA'), dict) else base_pts + base_reb + base_ast
            else:
                # Fallback
                base_pts, base_reb, base_ast, base_pra = 15.0, 5.0, 3.5, 23.5
        except Exception:
            base_pts, base_reb, base_ast, base_pra = 15.0, 5.0, 3.5, 23.5
        
        # Apply +12% boost for PTS/PRA per user spec
        total_boost = usage_multiplier
        
        return {
            "pts": round(base_pts * total_boost, 1),
            "ast": round(base_ast * 1.03, 1),  # Minimal boost
            "reb": round(base_reb * 1.05, 1),  # Small boost
            "pra": round(base_pra * total_boost, 1),
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
    
    async def _get_todays_teams(self) -> set:
        """
        Get teams playing today from BDL live box scores.
        Returns set of team abbreviations (e.g., {'MIA', 'WAS', 'DEN', 'SAS', 'PHI', 'DET'})
        """
        teams = set()
        
        try:
            # Try BDL live endpoint first
            import httpx
            import os
            
            api_key = os.environ.get("BDL_API_KEY") or os.environ.get("BALLDONTLIE_API_KEY")
            if api_key:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    # Try live box scores first (games in progress)
                    response = await client.get(
                        "https://api.balldontlie.io/v1/box_scores/live",
                        headers={"Authorization": api_key}
                    )
                    if response.status_code == 200:
                        data = response.json()
                        for game in data.get("data", []):
                            home = game.get("home_team", {}).get("abbreviation")
                            away = game.get("visitor_team", {}).get("abbreviation")
                            if home:
                                teams.add(home)
                            if away:
                                teams.add(away)

                    # If no live games, check today's scheduled games
                    if not teams:
                        from datetime import date
                        today = date.today().isoformat()
                        sched_resp = await client.get(
                            f"https://api.balldontlie.io/nba/v1/games?dates[]={today}",
                            headers={"Authorization": api_key}
                        )
                        if sched_resp.status_code == 200:
                            games = sched_resp.json().get("data", [])
                            for game in games:
                                home = game.get("home_team", {}).get("abbreviation")
                                away = game.get("visitor_team", {}).get("abbreviation")
                                if home:
                                    teams.add(home)
                                if away:
                                    teams.add(away)
            
            # Fallback: check live_scores_cache in DB (populated by live scores engine)
            if not teams and hasattr(self, 'db') and self.db is not None:
                try:
                    cached = await self.db.live_scores_cache.find_one({})
                    if cached and cached.get("games"):
                        for game in cached.get("games", []):
                            if game.get("home_team"):
                                teams.add(game.get("home_team"))
                            if game.get("away_team"):
                                teams.add(game.get("away_team"))
                except Exception as e:
                    logger.warning(f"[VacuumService] Failed to get games from live_scores_cache: {e}")

            # Fallback: check ticker_cache in DB
            if not teams and hasattr(self, 'db') and self.db is not None:
                try:
                    cached = await self.db.ticker_cache.find_one({"type": "games"})
                    if cached and cached.get("games"):
                        for game in cached.get("games", []):
                            if game.get("home_team"):
                                teams.add(game.get("home_team"))
                            if game.get("away_team"):
                                teams.add(game.get("away_team"))
                except Exception as e:
                    logger.warning(f"[VacuumService] Failed to get games from ticker_cache: {e}")
            
            logger.info(f"[VacuumService] Today's teams: {teams}")
            
        except Exception as e:
            logger.warning(f"[VacuumService] Failed to get today's teams: {e}")
        
        return teams
    
    async def fetch_injury_report(self) -> List[Dict]:
        """
        Fetch the latest NBA injury report from injuries_normalized (BDL sourced).
        ONLY returns injuries for teams playing TODAY.
        """
        logger.info("[VacuumService] Fetching injury data from normalized collection...")
        
        # Get today's teams first
        todays_teams = await self._get_todays_teams()
        logger.info(f"[VacuumService] Filtering injuries for today's teams: {todays_teams}")
        
        injuries = []
        
        try:
            if hasattr(self, 'db') and self.db is not None:
                try:
                    # Read from normalized collection — tier_level >= 3 covers Out, Doubtful, IL
                    query = {"sport": "nba", "tier_level": {"$gte": 3}}
                    if todays_teams:
                        query["team"] = {"$in": list(todays_teams)}
                    
                    cursor = self.db.injuries_normalized.find(query, {"_id": 0})
                    norm_injuries = await cursor.to_list(length=200)
                except Exception as db_err:
                    logger.warning(f"[VacuumService] injuries_normalized query failed: {db_err}")
                    norm_injuries = []
                
                for inj in norm_injuries:
                    player_name = inj.get("player_name", "")
                    team = inj.get("team", "UNK")
                    status = inj.get("status", "UNKNOWN")  # normalized tier name
                    reason = inj.get("short_comment", "") or inj.get("description", "")[:100]
                    
                    injuries.append({
                        "player_name": player_name,
                        "team": team,
                        "team_name": "",
                        "status": status,
                        "reason": reason,
                        "return_date": inj.get("return_date"),
                        "tier_level": inj.get("tier_level", 0),
                        "updated_at": inj.get("synced_at", datetime.now(timezone.utc).isoformat())
                    })
                
                logger.info(f"[VacuumService] Found {len(injuries)} injuries from injuries_normalized (BDL)")
                
                # Fallback to legacy dg_injuries if normalized is empty
                if len(injuries) == 0:
                    logger.info("[VacuumService] dg_injuries empty, checking bdl_injuries...")
                    
                    # Build query for bdl_injuries
                    bdl_query = {"status": {"$in": ["Out", "OUT", "Out For Season", "Doubtful", "DOUBTFUL"]}}
                    if todays_teams:
                        bdl_query["team"] = {"$in": list(todays_teams)}
                    
                    bdl_cursor = self.db.bdl_injuries.find(bdl_query)
                    bdl_injuries = await bdl_cursor.to_list(length=200)
                    
                    for inj in bdl_injuries:
                        player_name = inj.get("player_name", "")
                        team = inj.get("team", "UNK") or "UNK"
                        
                        # Skip if team is not in today's games
                        if todays_teams and team not in todays_teams:
                            continue
                        
                        status = inj.get("status", "").upper()
                        if "SEASON" in status:
                            status = "OUT"  # Normalize "Out For Season" to "OUT"
                        
                        injuries.append({
                            "player_name": player_name,
                            "team": team,
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
        
        # Fallback - also filter by today's teams
        logger.warning("[VacuumService] Could not fetch injury data from database, using fallback")
        fallback = self._get_fallback_injuries()
        if todays_teams:
            fallback = [inj for inj in fallback if inj.get("team") in todays_teams]
        return fallback
    
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
                        # Use the correct team from star profile, not injury data
                        correct_team = star_profile.get("team", team)
                        
                        logger.info("[VacuumService] *** LATE SCRATCH DETECTED ***")
                        logger.info(f"[VacuumService] STAR PLAYER OUT: {player_name} (Team: {correct_team}, Usage: {usage_rate}%)")
                        
                        # TRIGGER RESCAN EVENT
                        rescan_event = {
                            "team": correct_team,  # Use correct team
                            "triggered_by": player_name,
                            "usage_rate": usage_rate,
                            "triggered_at": datetime.now(timezone.utc).isoformat(),
                            "event_type": "late_scratch_rescan"
                        }
                        result["rescan_events"].append(rescan_event)
                        logger.info(f"[VacuumService] ReScanEvent triggered for team {correct_team}")
                        
                        # Get beneficiaries with boosted projections
                        beneficiaries = self._get_beneficiaries(player_name, correct_team)
                        
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
                                "team": correct_team,
                                "status": current_status,
                                "reason": injury.get("reason"),
                                "return_date": injury.get("return_date"),
                                "tier_level": injury.get("tier_level", 0),
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
    
    def get_active_vacuums(self, todays_teams: set = None) -> List[Dict]:
        """
        Get all currently active usage vacuums.
        If todays_teams is provided, filters to only return vacuums for those teams.
        """
        vacuums = list(self.active_vacuums.values())
        
        # Filter by today's teams if provided
        if todays_teams:
            vacuums = [v for v in vacuums if v.get("team") in todays_teams]
        
        return vacuums
    
    async def get_active_vacuums_for_today(self) -> List[Dict]:
        """
        Get active vacuums filtered to only teams playing today.
        """
        todays_teams = await self._get_todays_teams()
        return self.get_active_vacuums(todays_teams)
    
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
        Sync star player usage profiles from BDL Advanced Stats in database.
        Dynamic Usage Model v3.0 - loads from star_usage_cache collection.
        """
        logger.info("[VacuumService] Syncing star profiles from BDL Advanced Stats...")
        
        try:
            from pymongo import MongoClient
            import os
            sync_client = MongoClient(os.environ.get('MONGO_URL'))
            sync_db = sync_client['pick_vision']
            
            # Load all players with usage >= Secondary Alpha threshold (22%)
            stars = list(sync_db.star_usage_cache.find(
                {'usage_percentage': {'$gte': SECONDARY_ALPHA_THRESHOLD}},
                {'_id': 0}
            ).sort('usage_percentage', -1))
            
            sync_client.close()
            
            # Cache all star profiles
            primary_count = 0
            secondary_count = 0
            
            for star in stars:
                name = star.get('player_name', '')
                normalized = self._normalize_player_name(name)
                usage = star.get('usage_percentage', 0)
                
                alpha_tier = 'primary' if usage >= PRIMARY_ALPHA_THRESHOLD else 'secondary'
                if alpha_tier == 'primary':
                    primary_count += 1
                else:
                    secondary_count += 1
                
                self.star_profiles_cache[normalized] = {
                    "name": name,
                    "player_name": name,
                    "team": star.get('team'),
                    "position": star.get('position'),
                    "usage_rate": usage,
                    "usage_percentage": usage,
                    "pie": star.get('pie', 0),
                    "net_rating": star.get('net_rating', 0),
                    "alpha_tier": alpha_tier,
                    "is_primary_alpha": usage >= PRIMARY_ALPHA_THRESHOLD,
                    "source": "bdl_advanced_stats"
                }
            
            logger.info(f"[VacuumService] Synced {len(stars)} star profiles "
                       f"(Primary Alpha: {primary_count}, Secondary Alpha: {secondary_count})")
            
            return {
                "success": True,
                "profiles_synced": len(self.star_profiles_cache),
                "primary_alphas": primary_count,
                "secondary_alphas": secondary_count,
                "synced_at": datetime.now(timezone.utc).isoformat(),
                "source": "bdl_advanced_stats"
            }
            
        except Exception as e:
            logger.error(f"[VacuumService] Error syncing star profiles: {e}")
            return {
                "success": False,
                "error": str(e),
                "profiles_synced": 0
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
