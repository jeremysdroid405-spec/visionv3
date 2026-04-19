"""
Referee Scraper Service - Whistle Matrix Data
==============================================
Scrapes daily NBA referee assignments and stats for the Whistle Matrix modifier.

Data Sources:
- official.nba.com/referee-assignments/ - Daily game assignments
- covers.com/sport/basketball/nba/referees - Referee O/U and PPG stats

Whistle Matrix Criteria:
- Green Light (High Whistle): PPG > 118 OR O/U rate > 60%
- Red Light (Low Whistle): PPG < 113 OR O/U rate < 45%
"""
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone, timedelta
import logging
import re

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

# =============================================================================
# WHISTLE MATRIX CONSTANTS
# =============================================================================

# High Whistle thresholds (Green Light)
HIGH_WHISTLE_PPG = 118.0
HIGH_WHISTLE_OU_PCT = 60.0

# Low Whistle thresholds (Red Light)
LOW_WHISTLE_PPG = 113.0
LOW_WHISTLE_OU_PCT = 45.0

# League average PPG for Point Lift calculation
LEAGUE_AVG_PPG = 115.5

# Modifier values
GREEN_LIGHT_MODIFIER = 15.0  # PTS, FTM
RED_LIGHT_MODIFIER = -15.0   # PTS, FTM
PARTIAL_GREEN_MODIFIER = 7.5  # PRA
PARTIAL_RED_MODIFIER = -7.5   # PRA

# Default Point Lift values (when usage rate unavailable)
DEFAULT_PTS_LIFT = 3.5
DEFAULT_FTM_LIFT = 1.5
DEFAULT_PRA_LIFT = 2.5

# League average shooting foul rate (for tooltip)
LEAGUE_AVG_FOUL_RATE = 22.5  # fouls per game

# Team abbreviation mapping (for matching)
TEAM_CITY_TO_ABBREV = {
    "atlanta": "ATL", "boston": "BOS", "brooklyn": "BKN", "charlotte": "CHA",
    "chicago": "CHI", "cleveland": "CLE", "dallas": "DAL", "denver": "DEN",
    "detroit": "DET", "golden state": "GSW", "houston": "HOU", "indiana": "IND",
    "la clippers": "LAC", "los angeles clippers": "LAC", "la lakers": "LAL", 
    "los angeles lakers": "LAL", "memphis": "MEM", "miami": "MIA", "milwaukee": "MIL",
    "minnesota": "MIN", "new orleans": "NOP", "new york": "NYK", "oklahoma city": "OKC",
    "orlando": "ORL", "philadelphia": "PHI", "phoenix": "PHX", "portland": "POR",
    "sacramento": "SAC", "san antonio": "SAS", "toronto": "TOR", "utah": "UTA",
    "washington": "WAS",
    # Nicknames
    "hawks": "ATL", "celtics": "BOS", "nets": "BKN", "hornets": "CHA",
    "bulls": "CHI", "cavaliers": "CLE", "cavs": "CLE", "mavericks": "DAL", "mavs": "DAL",
    "nuggets": "DEN", "pistons": "DET", "warriors": "GSW", "rockets": "HOU",
    "pacers": "IND", "clippers": "LAC", "lakers": "LAL", "grizzlies": "MEM",
    "heat": "MIA", "bucks": "MIL", "timberwolves": "MIN", "wolves": "MIN",
    "pelicans": "NOP", "knicks": "NYK", "thunder": "OKC", "magic": "ORL",
    "sixers": "PHI", "76ers": "PHI", "suns": "PHX", "trail blazers": "POR",
    "blazers": "POR", "kings": "SAC", "spurs": "SAS", "raptors": "TOR",
    "jazz": "UTA", "wizards": "WAS"
}


# =============================================================================
# SCRAPER CLASS
# =============================================================================

class RefereeScraperService:
    """
    Scrapes referee assignments and stats for the Whistle Matrix.
    """
    
    def __init__(self, db=None):
        self.db = db
        self.referee_stats_cache: Dict[str, Dict] = {}
        self.daily_assignments_cache: Dict[str, Dict] = {}
        self.last_stats_fetch: Optional[datetime] = None
        self.last_assignments_fetch: Optional[datetime] = None
        
        if db is not None:
            self.referee_assignments = COLL.handle(db, "referee_assignments", "nba")
            self.referee_stats = db.referee_stats
    
    async def _fetch_html(self, url: str, timeout: int = 30) -> Optional[str]:
        """Fetch HTML content from URL with error handling."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=timeout) as response:
                    if response.status == 200:
                        return await response.text()
                    else:
                        logger.warning(f"[RefScraper] HTTP {response.status} for {url}")
                        return None
        except asyncio.TimeoutError:
            logger.error(f"[RefScraper] Timeout fetching {url}")
            return None
        except Exception as e:
            logger.error(f"[RefScraper] Error fetching {url}: {e}")
            return None
    
    def _normalize_ref_name(self, name: str) -> str:
        """Normalize referee name for matching."""
        if not name:
            return ""
        # Remove jersey number in parentheses (e.g., "Ben Taylor (#46)" -> "Ben Taylor")
        name = re.sub(r'\s*\(#?\d+\)\s*', '', name)
        # Remove extra spaces, lowercase
        normalized = " ".join(name.strip().split()).lower()
        return normalized
    
    def _parse_team_abbrev(self, team_str: str) -> Optional[str]:
        """Extract team abbreviation from game string."""
        if not team_str:
            return None
        
        team_lower = team_str.lower().strip()
        
        # Direct match
        if team_lower in TEAM_CITY_TO_ABBREV:
            return TEAM_CITY_TO_ABBREV[team_lower]
        
        # Partial match
        for city, abbrev in TEAM_CITY_TO_ABBREV.items():
            if city in team_lower or team_lower in city:
                return abbrev
        
        return None
    
    def _parse_ou_record(self, ou_str: str) -> Dict[str, Any]:
        """Parse O/U record string like '27-15' into wins, losses, pct."""
        if not ou_str or ou_str == '-':
            return {"wins": 0, "losses": 0, "pct": 50.0}
        
        try:
            # Handle formats: "27-15", "27-15-0"
            parts = ou_str.replace(' ', '').split('-')
            wins = int(parts[0])
            losses = int(parts[1]) if len(parts) > 1 else 0
            total = wins + losses
            pct = (wins / total * 100) if total > 0 else 50.0
            return {"wins": wins, "losses": losses, "pct": round(pct, 1)}
        except (ValueError, IndexError):
            return {"wins": 0, "losses": 0, "pct": 50.0}
    
    async def fetch_referee_stats_from_covers(self) -> Dict[str, Dict]:
        """
        Scrape referee stats from Covers.com.
        Returns dict keyed by normalized referee name.
        """
        url = "https://www.covers.com/sport/basketball/nba/referees/statistics/2025-2026"
        logger.info("[RefScraper] Fetching referee stats from Covers.com...")
        
        html = await self._fetch_html(url)
        if not html:
            logger.warning("[RefScraper] Could not fetch Covers.com stats, using fallback data")
            return self._get_fallback_referee_stats()
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
            stats = {}
            
            # Find the main stats table
            tables = soup.find_all('table')
            
            for table in tables:
                rows = table.find_all('tr')
                
                for row in rows:
                    cells = row.find_all(['td', 'th'])
                    if len(cells) < 4:
                        continue
                    
                    # Try to extract referee name (usually first cell with link)
                    name_cell = cells[0] if cells else None
                    if not name_cell:
                        continue
                    
                    # Get name from link or text
                    name_link = name_cell.find('a')
                    name = name_link.get_text(strip=True) if name_link else name_cell.get_text(strip=True)
                    
                    if not name or name.lower() in ['name', 'referee', '#']:
                        continue
                    
                    # Extract stats from cells
                    try:
                        # Typical columns: Name, ATS, O/U, PPG, Pts-A/G, Total
                        ou_str = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                        ppg_str = cells[3].get_text(strip=True) if len(cells) > 3 else ""
                        total_str = cells[5].get_text(strip=True) if len(cells) > 5 else ""
                        
                        ou_data = self._parse_ou_record(ou_str)
                        ppg = float(ppg_str) if ppg_str and ppg_str != '-' else 0.0
                        total = float(total_str) if total_str and total_str != '-' else 0.0
                        
                        normalized_name = self._normalize_ref_name(name)
                        
                        # Determine whistle classification
                        whistle_class = self._classify_whistle(ppg, ou_data["pct"])
                        
                        stats[normalized_name] = {
                            "name": name,
                            "ou_record": ou_str,
                            "ou_wins": ou_data["wins"],
                            "ou_losses": ou_data["losses"],
                            "ou_pct": ou_data["pct"],
                            "ppg": ppg,
                            "total": total,
                            "whistle_class": whistle_class,
                            "fetched_at": datetime.now(timezone.utc).isoformat()
                        }
                    except Exception:
                        continue
            
            if stats:
                logger.info(f"[RefScraper] Parsed {len(stats)} referees from Covers.com")
                self.referee_stats_cache = stats
                self.last_stats_fetch = datetime.now(timezone.utc)
                
                # Store in DB if available
                if self.db is not None:
                    await self._store_referee_stats(stats)
                
                return stats
            else:
                logger.warning("[RefScraper] No stats parsed, using fallback")
                return self._get_fallback_referee_stats()
                
        except Exception as e:
            logger.error(f"[RefScraper] Error parsing Covers.com: {e}")
            return self._get_fallback_referee_stats()
    
    def _classify_whistle(self, ppg: float, ou_pct: float) -> str:
        """
        Classify referee as high/low/neutral whistle.
        
        Green Light (High Whistle): PPG > 118 OR O/U rate > 60%
        Red Light (Low Whistle): PPG < 113 OR O/U rate < 45%
        """
        if ppg > HIGH_WHISTLE_PPG or ou_pct > HIGH_WHISTLE_OU_PCT:
            return "high_whistle"
        elif ppg < LOW_WHISTLE_PPG or ou_pct < LOW_WHISTLE_OU_PCT:
            return "low_whistle"
        else:
            return "neutral"
    
    async def fetch_daily_assignments(self) -> List[Dict]:
        """
        Scrape today's referee assignments from official.nba.com.
        """
        url = "https://official.nba.com/referee-assignments/"
        logger.info("[RefScraper] Fetching daily assignments from NBA.com...")
        
        html = await self._fetch_html(url)
        if not html:
            logger.warning("[RefScraper] Could not fetch NBA.com assignments")
            return []
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
            assignments = []
            
            # Find assignment tables
            tables = soup.find_all('table')
            
            for table in tables:
                rows = table.find_all('tr')
                
                for row in rows:
                    cells = row.find_all(['td', 'th'])
                    if len(cells) < 4:
                        continue
                    
                    # Typical columns: Game, Crew Chief, Referee, Umpire, Alternate
                    game_cell = cells[0].get_text(strip=True) if cells else ""
                    crew_chief = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                    referee = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                    umpire = cells[3].get_text(strip=True) if len(cells) > 3 else ""
                    
                    # Skip header rows
                    if not game_cell or game_cell.lower() in ['game', 'matchup', '']:
                        continue
                    if crew_chief.lower() in ['crew chief', 'cc', '']:
                        continue
                    
                    # Parse game string (e.g., "Lakers @ Thunder", "NYK vs BKN")
                    teams = self._parse_game_string(game_cell)
                    
                    if teams and crew_chief:
                        assignment = {
                            "game": game_cell,
                            "away_team": teams.get("away"),
                            "home_team": teams.get("home"),
                            "crew_chief": crew_chief,
                            "referee": referee,
                            "umpire": umpire,
                            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                            "fetched_at": datetime.now(timezone.utc).isoformat()
                        }
                        assignments.append(assignment)
            
            if assignments:
                logger.info(f"[RefScraper] Found {len(assignments)} game assignments")
                self.last_assignments_fetch = datetime.now(timezone.utc)
                
                # Store in DB
                if self.db is not None:
                    await self._store_daily_assignments(assignments)
                
                # Build team-to-ref lookup
                for assignment in assignments:
                    away = assignment.get("away_team")
                    home = assignment.get("home_team")
                    if away:
                        self.daily_assignments_cache[away] = assignment
                    if home:
                        self.daily_assignments_cache[home] = assignment
                
                return assignments
            else:
                logger.warning("[RefScraper] No assignments parsed from NBA.com")
                return []
                
        except Exception as e:
            logger.error(f"[RefScraper] Error parsing NBA.com: {e}")
            return []
    
    def _parse_game_string(self, game_str: str) -> Optional[Dict[str, str]]:
        """Parse game string like 'Lakers @ Thunder' into away/home teams."""
        if not game_str:
            return None
        
        # Handle various formats: "Lakers @ Thunder", "NYK vs BKN", "New York at Memphis"
        game_lower = game_str.lower()
        
        # Split by @ or vs or at
        for delimiter in [' @ ', ' vs ', ' at ', ' vs. ']:
            if delimiter in game_lower:
                parts = game_lower.split(delimiter)
                if len(parts) == 2:
                    away = self._parse_team_abbrev(parts[0].strip())
                    home = self._parse_team_abbrev(parts[1].strip())
                    if away and home:
                        return {"away": away, "home": home}
        
        return None
    
    async def _store_referee_stats(self, stats: Dict[str, Dict]):
        """Store referee stats in MongoDB."""
        try:
            # Upsert each referee
            for name, data in stats.items():
                await self.referee_stats.update_one(
                    {"normalized_name": name},
                    {"$set": {**data, "normalized_name": name}},
                    upsert=True
                )
            logger.info(f"[RefScraper] Stored {len(stats)} referee stats in DB")
        except Exception as e:
            logger.error(f"[RefScraper] Error storing referee stats: {e}")
    
    async def _store_daily_assignments(self, assignments: List[Dict]):
        """Store daily assignments in MongoDB."""
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            
            # Clear today's old assignments and insert new
            await self.referee_assignments.delete_many({"date": today})
            
            if assignments:
                await self.referee_assignments.insert_many(assignments)
            
            logger.info(f"[RefScraper] Stored {len(assignments)} assignments for {today}")
        except Exception as e:
            logger.error(f"[RefScraper] Error storing assignments: {e}")
    
    def _get_fallback_referee_stats(self) -> Dict[str, Dict]:
        """
        Fallback referee stats based on known high/low whistle refs.
        Data from Covers.com 2025-2026 season.
        
        April 2, 2026 Directive:
        - High Whistle (+15/+7.5): James Capers, Bill Kennedy, Ben Taylor
        - Low Whistle (-15/-7.5): Josh Tiven, JB DeRosa
        """
        fallback = {
            # ================================================================
            # HIGH WHISTLE REFS (Green Light) - PPG > 118 OR O/U > 60%
            # Apply +15 to PTS/FTM, +7.5 to PRA
            # ================================================================
            "james capers": {"name": "James Capers", "ppg": 116.6, "ou_pct": 60.7, "whistle_class": "high_whistle"},
            "bill kennedy": {"name": "Bill Kennedy", "ppg": 118.2, "ou_pct": 58.5, "whistle_class": "high_whistle"},
            "ben taylor": {"name": "Ben Taylor", "ppg": 118.5, "ou_pct": 61.2, "whistle_class": "high_whistle"},
            "che flores": {"name": "Che Flores", "ppg": 117.1, "ou_pct": 64.3, "whistle_class": "high_whistle"},
            "danielle scott": {"name": "Danielle Scott", "ppg": 118.5, "ou_pct": 64.3, "whistle_class": "high_whistle"},
            "phenizee ransom": {"name": "Phenizee Ransom", "ppg": 116.9, "ou_pct": 61.5, "whistle_class": "high_whistle"},
            "karl lane": {"name": "Karl Lane", "ppg": 116.7, "ou_pct": 61.5, "whistle_class": "high_whistle"},
            "matt kallio": {"name": "Matt Kallio", "ppg": 118.9, "ou_pct": 55.3, "whistle_class": "high_whistle"},
            "pat fraher": {"name": "Pat Fraher", "ppg": 118.1, "ou_pct": 54.4, "whistle_class": "high_whistle"},
            "mitchell ervin": {"name": "Mitchell Ervin", "ppg": 118.3, "ou_pct": 53.4, "whistle_class": "high_whistle"},
            
            # ================================================================
            # LOW WHISTLE REFS (Red Light) - PPG < 113 OR O/U < 45%
            # Apply -15 to PTS/FTM, -7.5 to PRA
            # ================================================================
            "josh tiven": {"name": "Josh Tiven", "ppg": 112.5, "ou_pct": 44.0, "whistle_class": "low_whistle"},
            "jb derosa": {"name": "JB DeRosa", "ppg": 112.8, "ou_pct": 43.5, "whistle_class": "low_whistle"},
            "simone jelks": {"name": "Simone Jelks", "ppg": 113.7, "ou_pct": 45.9, "whistle_class": "low_whistle"},
            "curtis blair": {"name": "Curtis Blair", "ppg": 114.0, "ou_pct": 30.0, "whistle_class": "low_whistle"},
            "kevin scott": {"name": "Kevin Scott", "ppg": 112.8, "ou_pct": 42.1, "whistle_class": "low_whistle"},
            "eric lewis": {"name": "Eric Lewis", "ppg": 112.5, "ou_pct": 44.0, "whistle_class": "low_whistle"},
            "mark ayotte": {"name": "Mark Ayotte", "ppg": 111.9, "ou_pct": 43.5, "whistle_class": "low_whistle"},
            "ed malloy": {"name": "Ed Malloy", "ppg": 112.2, "ou_pct": 41.8, "whistle_class": "low_whistle"},
            
            # ================================================================
            # NEUTRAL REFS - No modifier applied
            # ================================================================
            "scott foster": {"name": "Scott Foster", "ppg": 115.2, "ou_pct": 52.0, "whistle_class": "neutral"},
            "tony brothers": {"name": "Tony Brothers", "ppg": 115.8, "ou_pct": 51.5, "whistle_class": "neutral"},
            "marc davis": {"name": "Marc Davis", "ppg": 114.5, "ou_pct": 50.0, "whistle_class": "neutral"},
            "zach zarba": {"name": "Zach Zarba", "ppg": 115.0, "ou_pct": 52.5, "whistle_class": "neutral"},
            "john goble": {"name": "John Goble", "ppg": 114.8, "ou_pct": 51.0, "whistle_class": "neutral"},
            "nick buchert": {"name": "Nick Buchert", "ppg": 115.0, "ou_pct": 50.0, "whistle_class": "neutral"},
        }
        
        # Add metadata
        for name, data in fallback.items():
            data["normalized_name"] = name
            data["ou_record"] = "N/A"
            data["ou_wins"] = 0
            data["ou_losses"] = 0
            data["total"] = 230.0
            data["fetched_at"] = datetime.now(timezone.utc).isoformat()
            data["is_fallback"] = True
        
        self.referee_stats_cache = fallback
        return fallback
    
    def get_ref_for_team(self, team_abbrev: str) -> Optional[Dict]:
        """
        Get the crew chief assignment for a team's game today.
        Returns ref info with whistle classification.
        """
        if not team_abbrev:
            return None
        
        team_upper = team_abbrev.upper()
        assignment = self.daily_assignments_cache.get(team_upper)
        
        if not assignment:
            return None
        
        crew_chief = assignment.get("crew_chief", "")
        normalized_name = self._normalize_ref_name(crew_chief)
        
        # Look up stats
        ref_stats = self.referee_stats_cache.get(normalized_name, {})
        
        return {
            "crew_chief": crew_chief,
            "referee": assignment.get("referee"),
            "umpire": assignment.get("umpire"),
            "game": assignment.get("game"),
            "ppg": ref_stats.get("ppg", 115.0),
            "ou_pct": ref_stats.get("ou_pct", 50.0),
            "ou_record": ref_stats.get("ou_record", "N/A"),
            "whistle_class": ref_stats.get("whistle_class", "neutral"),
            "is_fallback": ref_stats.get("is_fallback", False)
        }
    
    def calculate_whistle_modifier(self, stat_type: str, whistle_class: str) -> float:
        """
        Calculate the Whistle Matrix modifier for a prop.
        
        Green Light (+15 PTS/FTM, +7.5 PRA): High whistle crew
        Red Light (-15 PTS/FTM, -7.5 PRA): Low whistle crew
        """
        if whistle_class == "neutral":
            return 0.0
        
        stat_upper = stat_type.upper() if stat_type else ""
        
        # Full modifier for PTS and FTM
        if stat_upper in ["PTS", "POINTS", "FTM", "FREE THROWS MADE"]:
            if whistle_class == "high_whistle":
                return GREEN_LIGHT_MODIFIER
            elif whistle_class == "low_whistle":
                return RED_LIGHT_MODIFIER
        
        # Partial modifier for PRA
        elif stat_upper in ["PRA", "POINTS REBOUNDS ASSISTS", "PTS+REB+AST"]:
            if whistle_class == "high_whistle":
                return PARTIAL_GREEN_MODIFIER
            elif whistle_class == "low_whistle":
                return PARTIAL_RED_MODIFIER
        
        return 0.0
    
    def calculate_point_lift(
        self, 
        stat_type: str, 
        ref_ppg: float, 
        whistle_class: str,
        player_usage_rate: Optional[float] = None,
        team_usage_avg: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Calculate the Point Lift translation for the Whistle Matrix.
        
        Formula: Point_Lift = (Ref_PPG_Avg - League_PPG_Avg) * (Player_Usage_Rate / Team_Usage_Avg)
        
        If usage unavailable, use flat defaults:
        - PTS: ±3.5
        - FTM: ±1.5
        - PRA: ±2.5
        
        Returns:
        - point_lift: The projected stat boost/ceiling
        - lift_label: Human-readable label (e.g., "+3.5 Projected PTS Boost")
        - lift_type: "boost" or "ceiling"
        - foul_rate_diff: Percentage difference from league average
        """
        if whistle_class == "neutral":
            return {
                "point_lift": 0.0,
                "lift_label": "Neutral Impact",
                "lift_type": "neutral",
                "foul_rate_diff": 0
            }
        
        stat_upper = stat_type.upper() if stat_type else ""
        
        # Determine base lift value based on stat type
        if stat_upper in ["PTS", "POINTS"]:
            base_lift = DEFAULT_PTS_LIFT
            stat_label = "PTS"
        elif stat_upper in ["FTM", "FREE THROWS MADE"]:
            base_lift = DEFAULT_FTM_LIFT
            stat_label = "FTM"
        elif stat_upper in ["PRA", "POINTS REBOUNDS ASSISTS", "PTS+REB+AST"]:
            base_lift = DEFAULT_PRA_LIFT
            stat_label = "PRA"
        else:
            # Non-scoring stat, no lift
            return {
                "point_lift": 0.0,
                "lift_label": "No Scoring Impact",
                "lift_type": "neutral",
                "foul_rate_diff": 0
            }
        
        # Calculate usage-adjusted lift if available
        if player_usage_rate and team_usage_avg and team_usage_avg > 0:
            ppg_diff = ref_ppg - LEAGUE_AVG_PPG if ref_ppg else 0
            usage_factor = player_usage_rate / team_usage_avg
            point_lift = ppg_diff * usage_factor * 0.15  # Scale factor
            point_lift = max(-5.0, min(5.0, point_lift))  # Cap at ±5
        else:
            point_lift = base_lift
        
        # Calculate foul rate difference (for tooltip)
        if ref_ppg:
            foul_rate_diff = round(((ref_ppg - LEAGUE_AVG_PPG) / LEAGUE_AVG_PPG) * 100, 0)
        else:
            foul_rate_diff = 0
        
        # Determine lift type and label
        if whistle_class == "high_whistle":
            lift_type = "boost"
            lift_label = f"+{abs(point_lift):.1f} Projected {stat_label} Boost"
            point_lift = abs(point_lift)
        else:  # low_whistle
            lift_type = "ceiling"
            lift_label = f"-{abs(point_lift):.1f} Projected {stat_label} Ceiling"
            point_lift = -abs(point_lift)
        
        return {
            "point_lift": round(point_lift, 1),
            "lift_label": lift_label,
            "lift_type": lift_type,
            "foul_rate_diff": int(foul_rate_diff)
        }
    
    async def sync_all(self) -> Dict[str, Any]:
        """
        Full sync: fetch both referee stats and daily assignments.
        """
        results = {
            "success": True,
            "stats_count": 0,
            "assignments_count": 0,
            "synced_at": datetime.now(timezone.utc).isoformat()
        }
        
        try:
            # Fetch referee stats
            stats = await self.fetch_referee_stats_from_covers()
            results["stats_count"] = len(stats)
            
            # Fetch daily assignments
            assignments = await self.fetch_daily_assignments()
            results["assignments_count"] = len(assignments)
            
            logger.info(f"[RefScraper] Sync complete: {len(stats)} refs, {len(assignments)} games")
            
        except Exception as e:
            logger.error(f"[RefScraper] Sync error: {e}")
            results["success"] = False
            results["error"] = str(e)
        
        return results


# Singleton instance
_referee_service: Optional[RefereeScraperService] = None

def get_referee_service(db=None) -> RefereeScraperService:
    """Get or create the referee scraper service singleton."""
    global _referee_service
    if _referee_service is None:
        _referee_service = RefereeScraperService(db)
    elif db is not None and _referee_service.db is None:
        _referee_service.db = db
        _referee_service.referee_assignments = COLL.handle(db, "referee_assignments", "nba")
        _referee_service.referee_stats = db.referee_stats
    return _referee_service
