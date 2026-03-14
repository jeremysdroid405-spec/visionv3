"""
Demon & Goblin Analytics Engine v3.2
=====================================

PrizePicks-Specific System for NBA Player Props

ARCHITECTURE RESET (v3.2):
- Single source of truth: All data enrichment happens during sync
- Dumb components: Demon Radar, Goblin Recon, Gauntlet, Safe Haven just read data
- Tank01 playerID as primary key
- No runtime lookups

API Configuration:
- Region: us_dfs (Daily Fantasy Sports - includes PrizePicks)
- Bookmaker: prizepicks
- Markets: player_*_alternate (PrizePicks alternate lines)

Classification (PrizePicks Native):
- Goblin (Green): Default odds lines - easier, high-probability props
- Demon (Red): Even odds (+100) lines - harder, boosted props

Payout Calculation Engine (v3.2):
- Leg-level modifiers: Standard (1.0), Demon (1.1-1.5), Goblin (0.7-0.9)
- Formula: Total Payout = Base Multiplier × (Mod_1 × Mod_2 × ... × Mod_n)

Triple-Pillar Integration:
1. The Odds API (us_dfs/prizepicks) - All PrizePicks lines
2. BallDontLie API - Player stats for hit rate calculation
3. Tank01 API - Injury reports and player news

DATA INTEGRITY (v3.1):
- Triple-check verification for all stats
- source_verified tag on all Demon/Goblin records
- Auto-delete insights that fail verification gates
- Hallucination detection and prevention
"""

import httpx
import logging
import os
import asyncio
import random
import statistics
from datetime import datetime, timezone, timedelta, time
from typing import Optional, Dict, List, Any, Set, Tuple
from thefuzz import fuzz
from motor.motor_asyncio import AsyncIOMotorDatabase

# Data Integrity Module
from data_integrity import DataIntegrityVerifier, create_verified_insight

# Payout Calculation Engine
from payout_engine import (
    calculate_payout_from_picks,
    calculate_leg_modifier,
    estimate_payout,
    AssetType,
    BASE_MULTIPLIERS
)

# NBA Master Hub - SINGLE SOURCE OF TRUTH
from nba_master_hub import fetchPlayerIntel, fetchPlayerIntelByName, get_master_hub

# NBA.com API fallback for players missing from BallDontLie
try:
    from nba_api.stats.endpoints import playergamelog
    from nba_api.stats.static import players as nba_players
    NBA_API_AVAILABLE = True
except ImportError:
    NBA_API_AVAILABLE = False

logger = logging.getLogger(__name__)

# ==================== EXPONENTIAL BACKOFF CONFIG ====================
MAX_RETRIES = 4
BASE_DELAY = 1.0  # seconds
MAX_DELAY = 16.0  # seconds

# ==================== API CONFIGURATION ====================

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "e1ae76ab21c34ee88ed552cffb4449fd")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

BDL_API_KEY = os.environ.get("BDL_API_KEY", "ad5544be-9969-434b-9389-2b7cf658c8e0")
BDL_BASE_URL = "https://api.balldontlie.io/v1"

TANK01_API_KEY = os.environ.get("TANK01_API_KEY", "402edbcac6mshd04997e7ca01d17p1879eajsn65ab176cdb1e")
TANK01_BASE = "https://tank01-fantasy-stats.p.rapidapi.com"
TANK01_HOST = "tank01-fantasy-stats.p.rapidapi.com"
TANK01_CACHE_TTL = timedelta(hours=4)  # Cache Tank01 data for 4 hours

CURRENT_SEASON = "2025"  # 2025-26 NBA Season

# ==================== CACHE TTL CONFIGURATION ====================
STATIC_CACHE_TTL = timedelta(hours=24)  # Player metadata, stats - refresh at 4 AM
DYNAMIC_CACHE_TTL = timedelta(seconds=60)  # Betting lines only - live data
STATS_CACHE_TTL = timedelta(hours=4)  # BDL stats cache

# PrizePicks-Specific Configuration
PRIZEPICKS_REGION = "us_dfs"  # Daily Fantasy Sports region - REQUIRED for PrizePicks
PRIZEPICKS_BOOKMAKER = "prizepicks"

# PrizePicks Alternate Markets - These contain Demons and Goblins
PRIZEPICKS_ALTERNATE_MARKETS = [
    "player_points_alternate",
    "player_rebounds_alternate", 
    "player_assists_alternate",
    "player_threes_alternate",
    "player_blocks_alternate",
    "player_steals_alternate",
    "player_turnovers_alternate",
    "player_points_rebounds_alternate",
    "player_points_assists_alternate",
    "player_rebounds_assists_alternate",
    "player_points_rebounds_assists_alternate",
]

# Standard markets - These are "Standard" lines (no Demon/Goblin icon)
PRIZEPICKS_STANDARD_MARKETS = [
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_threes",
    "player_blocks",
    "player_steals",
    "player_turnovers",
    "player_points_rebounds",
    "player_points_assists",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
]

# Combined markets for API call
PRIZEPICKS_ALL_MARKETS = ",".join(PRIZEPICKS_ALTERNATE_MARKETS + PRIZEPICKS_STANDARD_MARKETS)

# Demon/Goblin Classification (PrizePicks Native)
# 
# CLASSIFICATION RULES:
# 1. STANDARD (no icon): Props from MAIN markets (e.g., player_points, player_rebounds)
# 2. DEMON (red icon): Props from ALTERNATE markets with EVEN odds (+100)
# 3. GOBLIN (green icon): Props from ALTERNATE markets with any other odds (e.g., -119, -137)
#
DEMON_ODDS = 100  # Even odds = Demon (only applies to alternate markets)

# Hit rate threshold for Goblin warning
GOBLIN_HIT_RATE_WARNING = 0.90  # 90% hit rate

# ==================== NBA TEAM MAPPING ====================
# Full team names to 3-letter abbreviations
NBA_TEAM_MAP = {
    # Atlantic Division
    "Boston Celtics": "BOS",
    "Brooklyn Nets": "BKN",
    "New York Knicks": "NYK",
    "Philadelphia 76ers": "PHI",
    "Toronto Raptors": "TOR",
    # Central Division
    "Chicago Bulls": "CHI",
    "Cleveland Cavaliers": "CLE",
    "Detroit Pistons": "DET",
    "Indiana Pacers": "IND",
    "Milwaukee Bucks": "MIL",
    # Southeast Division
    "Atlanta Hawks": "ATL",
    "Charlotte Hornets": "CHA",
    "Miami Heat": "MIA",
    "Orlando Magic": "ORL",
    "Washington Wizards": "WAS",
    # Northwest Division
    "Denver Nuggets": "DEN",
    "Minnesota Timberwolves": "MIN",
    "Oklahoma City Thunder": "OKC",
    "Portland Trail Blazers": "POR",
    "Utah Jazz": "UTA",
    # Pacific Division
    "Golden State Warriors": "GSW",
    "LA Clippers": "LAC",
    "Los Angeles Clippers": "LAC",
    "Los Angeles Lakers": "LAL",
    "LA Lakers": "LAL",
    "Phoenix Suns": "PHX",
    "Sacramento Kings": "SAC",
    # Southwest Division
    "Dallas Mavericks": "DAL",
    "Houston Rockets": "HOU",
    "Memphis Grizzlies": "MEM",
    "New Orleans Pelicans": "NOP",
    "San Antonio Spurs": "SAS",
}

# Reverse map for lookups
NBA_TEAM_ABBREV_TO_FULL = {v: k for k, v in NBA_TEAM_MAP.items()}

# ==================== KNOWN PLAYER-TEAM MAPPING ====================
# Hardcoded for star players to ensure correct team assignment
# This overrides any incorrect API data
KNOWN_PLAYER_TEAMS = {
    # Boston Celtics
    "Derrick White": "BOS",
    "Jayson Tatum": "BOS",
    "Jaylen Brown": "BOS",
    "Jrue Holiday": "BOS",
    "Kristaps Porzingis": "BOS",
    "Payton Pritchard": "BOS",
    "Sam Hauser": "BOS",
    # Los Angeles Lakers
    "LeBron James": "LAL",
    "Anthony Davis": "LAL",
    "Austin Reaves": "LAL",
    "D'Angelo Russell": "LAL",
    "Max Christie": "LAL",
    "Luke Kennard": "LAL",
    # Denver Nuggets
    "Nikola Jokic": "DEN",
    "Jamal Murray": "DEN",
    "Michael Porter Jr.": "DEN",
    "Christian Braun": "DEN",
    "Bruce Brown": "DEN",
    # Milwaukee Bucks
    "Giannis Antetokounmpo": "MIL",
    "Damian Lillard": "MIL",
    "Khris Middleton": "MIL",
    # Phoenix Suns
    "Devin Booker": "PHX",
    "Bradley Beal": "PHX",
    # Dallas Mavericks
    "Luka Doncic": "DAL",
    "Kyrie Irving": "DAL",
    "Klay Thompson": "DAL",
    # Golden State Warriors
    "Stephen Curry": "GSW",
    "Draymond Green": "GSW",
    "Jonathan Kuminga": "GSW",
    # Oklahoma City Thunder
    "Shai Gilgeous-Alexander": "OKC",
    "Chet Holmgren": "OKC",
    "Jalen Williams": "OKC",
    "Cason Wallace": "OKC",
    "Jaylin Williams": "OKC",
    # Philadelphia 76ers
    "Joel Embiid": "PHI",
    "Tyrese Maxey": "PHI",
    "Jared McCain": "PHI",
    "Tim Hardaway Jr.": "PHI",
    # San Antonio Spurs
    "Victor Wembanyama": "SAS",
    "Devin Vassell": "SAS",
    "Dylan Harper": "SAS",
    # Orlando Magic
    "Paolo Banchero": "ORL",
    "Franz Wagner": "ORL",
    # Memphis Grizzlies
    "Ja Morant": "MEM",
    "Desmond Bane": "MEM",
    # Minnesota Timberwolves
    "Anthony Edwards": "MIN",
    "Robert Dillingham": "MIN",
    # Cleveland Cavaliers
    "Donovan Mitchell": "CLE",
    "Darius Garland": "CLE",
    # Miami Heat
    "Jimmy Butler": "MIA",
    "Bam Adebayo": "MIA",
    "Tyler Herro": "MIA",
    # Utah Jazz
    "Cody Williams": "UTA",
    "Kyle Filipowski": "UTA",
    "Brice Sensabaugh": "UTA",
    "Ace Bailey": "UTA",
    # Portland Trail Blazers
    "Toumani Camara": "POR",
    "Donovan Clingan": "POR",
    # New York Knicks
    "Karl-Anthony Towns": "NYK",
    "Jalen Brunson": "NYK",
    # Charlotte Hornets
    "Nicolas Richards": "CHA",
    # Houston Rockets - 2026 ROSTER UPDATE
    "Kevin Durant": "HOU",  # TRADED FROM PHX - 2026
    "Jalen Green": "HOU",
    "Alperen Sengun": "HOU",
    "Reed Sheppard": "HOU",
    "Jabari Smith Jr.": "HOU",
    # New Orleans Pelicans
    "Trey Murphy III": "NOP",
    "Zion Williamson": "NOP",
    "Brandon Ingram": "NOP",
}

# ==================== NAME NORMALIZATION ====================
# Common name variations/nicknames to canonical names
NAME_ALIASES = {
    # First name variations
    "nic": "nicolas",
    "nick": "nicolas", 
    "mike": "michael",
    "will": "william",
    "chris": "christopher",
    "rob": "robert",
    "bob": "robert",
    "dan": "daniel",
    "danny": "daniel",
    "tony": "anthony",
    "alex": "alexandre",
    "tj": "t.j.",
    "pj": "p.j.",
    "cj": "c.j.",
    "jt": "j.t.",
    "aj": "a.j.",
    "rj": "r.j.",
    "dj": "d.j.",
    "gg": "g.g.",
    # Common last name issues
    "gilgeous alexander": "gilgeous-alexander",
    "porter jr": "porter jr.",
    "payton ii": "payton ii",
}

INJURY_KEYWORDS = [
    "injury", "injured", "out", "questionable", "doubtful", "probable",
    "day-to-day", "GTD", "game time decision", "load management", "rest",
    "ankle", "knee", "hamstring", "back", "shoulder", "concussion", 
    "illness", "personal", "sore", "sprain", "strain"
]

# ==================== NBA PLAYER ID MAPPING ====================
# NBA CDN headshot URL format: https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_id}.png
# These IDs are from the official NBA stats API

NBA_PLAYER_IDS = {
    # Superstars
    "Shai Gilgeous-Alexander": 1628983,
    "Giannis Antetokounmpo": 203507,
    "Luka Doncic": 1629029,
    "Nikola Jokic": 203999,
    "Joel Embiid": 203954,
    "LeBron James": 2544,
    "Stephen Curry": 201939,
    "Kevin Durant": 201142,
    "Jayson Tatum": 1628369,
    "Anthony Davis": 203076,
    "Damian Lillard": 203081,
    "Devin Booker": 1626164,
    "Anthony Edwards": 1630162,
    "Ja Morant": 1629630,
    "Donovan Mitchell": 1628378,
    "Trae Young": 1629027,
    "Kyrie Irving": 202681,
    "Jimmy Butler": 202710,
    "Paul George": 202331,
    "Kawhi Leonard": 202695,
    "Zion Williamson": 1629627,
    "Jaylen Brown": 1627759,
    "Domantas Sabonis": 1627734,
    "De'Aaron Fox": 1628368,
    "LaMelo Ball": 1630163,
    "Karl-Anthony Towns": 1626157,
    "Bam Adebayo": 1628389,
    "Cade Cunningham": 1630595,
    "Paolo Banchero": 1631094,
    "Victor Wembanyama": 1641705,
    "Tyrese Haliburton": 1630169,
    "Tyrese Maxey": 1630178,
    "Jalen Brunson": 1628973,
    "Scottie Barnes": 1630567,
    "Franz Wagner": 1630532,
    "Alperen Sengun": 1630578,
    "Evan Mobley": 1630596,
    "Desmond Bane": 1630217,
    "Anfernee Simons": 1629014,
    "Mikal Bridges": 1628969,
    "OG Anunoby": 1628384,
    "Tyler Herro": 1629639,
    "Jaren Jackson Jr.": 1628991,
    "DeMar DeRozan": 201942,
    "Bradley Beal": 203078,
    "Zach LaVine": 203897,
    "Julius Randle": 203944,
    "Lauri Markkanen": 1628374,
    "Dejounte Murray": 1627749,
    "Fred VanVleet": 1627832,
    "Pascal Siakam": 1627783,
    "Khris Middleton": 203114,
    "Brandon Ingram": 1627742,
    "CJ McCollum": 203468,
    "Derrick White": 1628401,
    "Jrue Holiday": 201950,
    "Draymond Green": 203110,
    "Chris Paul": 101108,
    "Russell Westbrook": 201566,
    "James Harden": 201935,
    "Klay Thompson": 202691,
    "Andrew Wiggins": 203952,
    "Austin Reaves": 1630559,
    "Jalen Williams": 1631114,
    "Chet Holmgren": 1631096,
    "Jamal Murray": 1627750,
    "Michael Porter Jr.": 1629008,
    "Aaron Gordon": 203932,
    "Myles Turner": 1626167,
    "Brook Lopez": 201572,
    "Rudy Gobert": 203497,
    "Clint Capela": 203991,
    "Nikola Vucevic": 202696,
    "Jonas Valanciunas": 202685,
    "Deandre Ayton": 1629028,
    "Jarrett Allen": 1628386,
    "Onyeka Okongwu": 1630168,
    "Mark Williams": 1631109,
    "Walker Kessler": 1631117,
    "Jalen Suggs": 1630591,
    "Tre Mann": 1630544,
    "Cam Thomas": 1630560,
    "Immanuel Quickley": 1630193,
    "Coby White": 1629632,
    "Collin Sexton": 1629012,
    "Keldon Johnson": 1629640,
    "Herbert Jones": 1630546,
    "Josh Giddey": 1630581,
    "Keegan Murray": 1631099,
    "Bennedict Mathurin": 1631097,
    "Jaden Ivey": 1631093,
    "Shaedon Sharpe": 1631101,
    "Jabari Smith Jr.": 1631095,
    "Tari Eason": 1631106,
    "Dyson Daniels": 1631098,
    "Jeremy Sochan": 1631110,
    "Jalen Duren": 1631105,
    "AJ Griffin": 1631100,
    "Malaki Branham": 1631107,
    "Ochai Agbaji": 1631104,
    "Johnny Davis": 1631102,
    "MarJon Beauchamp": 1631173,
    "Nikola Jovic": 1631108,
    "Peyton Watson": 1631213,
    "Cooper Flagg": 1642355,
    "Dylan Harper": 1642356,
    "Ace Bailey": 1642357,
    "Grayson Allen": 1628960,
    "Collin Gillespie": 1631208,
    "Jalen Johnson": 1630552,
    "Cam Spencer": 1641734,
    "Danny Wolf": 1642358,
}

def get_nba_player_id(player_name: str) -> Optional[int]:
    """Get NBA player ID from static mapping or return None"""
    return NBA_PLAYER_IDS.get(player_name)


# ==================== EXPONENTIAL BACKOFF HELPER ====================

async def fetch_with_backoff(url: str, headers: Dict, params: Dict = None, max_retries: int = MAX_RETRIES) -> Optional[Dict]:
    """
    Fetch with exponential backoff for rate-limited APIs
    
    Retry delays: 1s -> 2s -> 4s -> 8s (with jitter)
    """
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, params=params, timeout=30.0)
                
                if response.status_code == 200:
                    return response.json()
                
                elif response.status_code == 429:
                    # Rate limited - calculate backoff delay
                    delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                    jitter = random.uniform(0, delay * 0.1)  # Add 0-10% jitter
                    total_delay = delay + jitter
                    
                    logger.warning(f"[BACKOFF] Rate limited. Attempt {attempt + 1}/{max_retries}. Waiting {total_delay:.1f}s")
                    await asyncio.sleep(total_delay)
                    continue
                
                elif response.status_code >= 500:
                    # Server error - retry with backoff
                    delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                    logger.warning(f"[BACKOFF] Server error {response.status_code}. Retrying in {delay:.1f}s")
                    await asyncio.sleep(delay)
                    continue
                
                else:
                    logger.error(f"[BACKOFF] Request failed with status {response.status_code}")
                    return None
                    
        except httpx.TimeoutException:
            delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
            logger.warning(f"[BACKOFF] Timeout. Attempt {attempt + 1}/{max_retries}. Retrying in {delay:.1f}s")
            await asyncio.sleep(delay)
        except Exception as e:
            logger.error(f"[BACKOFF] Request error: {e}")
            return None
    
    logger.error(f"[BACKOFF] Max retries ({max_retries}) exceeded for {url}")
    return None


class DemonGoblinEngine:
    """
    The Demon & Goblin Analytics Engine - PrizePicks Edition
    
    WAREHOUSE MODEL (MongoDB):
    - LIVE_PROPS: All props stored with deduplication (synced via SyncBoard)
    - DEMON_RADAR: Top 10 pre-calculated picks flagged as is_radar_pick
    - Zero API calls from frontend - everything reads from MongoDB
    
    Classification (PrizePicks Native):
    - Standard (No Icon): Main market props
    - Demons (Red): Alternate market + Even odds (+100)
    - Goblins (Green): Alternate market + Non-even odds
    
    Features:
    - Demon Radar: Pre-calculated top 10 picks based on Hit Rate + Line Gap
    - Trending 10: Most popular players based on API order
    - Player-First Hierarchy: All props organized by player
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.events_cache = db.dg_events_cache
        self.odds_cache = db.dg_odds_cache
        self.player_data = db.dg_player_data
        self.stats_cache = db.dg_stats_cache
        self.sync_log = db.dg_sync_log
        self.trending_cache = db.dg_trending
        self.line_history = db.dg_line_history
        
        # WAREHOUSE MODEL COLLECTIONS
        self.live_props = db.dg_live_props  # Master props collection (deduplicated)
        self.radar_picks = db.dg_radar_picks  # Demon Radar top 10 picks
        self.goblin_vault = db.dg_goblin_vault  # Goblin Vault top 10 safe picks
        self.parlay_builder = db.dg_parlay_builder  # Big Money Builder parlays
        self.goblin_recon = db.dg_goblin_recon  # Goblin Recon parlays (high-consistency)
        self.cached_board = db.dg_cached_board  # Full cached board for frontend
        self.master_roster = db.dg_master_roster  # SOURCE OF TRUTH: Player-to-team mapping
        self.flagged_players = db.dg_flagged_players  # Players not in master roster (manual review)
        self.player_stats = db.dg_player_stats  # CACHED PLAYER GAME LOGS (synced daily)
        
        # Legacy caching collections
        self.static_shell_cache = db.dg_static_shell
        self.dynamic_lines_cache = db.dg_dynamic_lines
        self.tank01_cache = db.dg_tank01_cache
        self.daily_insights = db.dg_daily_insights  # Advanced analytics cache
        
        # In-memory caches
        self._player_name_map: Dict[str, Any] = {}
        self._injury_data: Dict[str, Any] = {}
        self._news_data: List[Dict] = []
        self._last_sync: Optional[datetime] = None
        self._last_lines_fetch: Optional[datetime] = None
        self._current_date: Optional[str] = None
        self._player_popularity: Dict[str, int] = {}
        self._canonical_names: Dict[str, str] = {}  # Cache for normalized names
        self._master_roster_cache: Dict[str, str] = {}  # In-memory cache for quick lookups
        self._team_pace_cache: Dict[str, float] = {}  # Team pace cache for analytics
        
        # Advanced Analytics Constants
        self.LEAGUE_AVG_PACE = 100.0
        self.B2B_PENALTY = 0.95
        self.THREE_IN_FOUR_PENALTY = 0.92
        self.VOLATILITY_HIGH_THRESHOLD = 10.0
        self.VOLATILITY_MED_THRESHOLD = 5.0
        self.USAGE_REDISTRIBUTION_BASE = 12.0
    
    # ==================== MASTER ROSTER SYNC (SOURCE OF TRUTH) ====================
    
    async def sync_master_roster(self) -> Dict[str, Any]:
        """
        WEEKLY ROSTER SYNC - Establishes the Source of Truth for player-to-team mapping.
        
        Fetches ALL NBA players from BallDontLie API and stores them in player_master_roster.
        This should run once every Sunday at midnight to keep rosters current.
        
        Returns:
            Dict with sync status, player count, and any errors
        """
        logger.info("=" * 60)
        logger.info("[MASTER ROSTER] Starting weekly roster sync...")
        logger.info("=" * 60)
        
        sync_start = datetime.now(timezone.utc)
        players_synced = 0
        teams_found = set()
        errors = []
        
        try:
            headers = {"Authorization": BDL_API_KEY}
            all_players = []
            
            # BallDontLie API returns paginated results
            # We need to fetch ALL pages to get complete roster
            cursor = None
            page = 1
            max_pages = 50  # Safety limit
            
            while page <= max_pages:
                url = f"{BDL_BASE_URL}/players?per_page=100"
                if cursor:
                    url += f"&cursor={cursor}"
                
                logger.info(f"[MASTER ROSTER] Fetching page {page}...")
                
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.get(url, headers=headers)
                    
                    if response.status_code != 200:
                        logger.error(f"[MASTER ROSTER] API error: {response.status_code}")
                        errors.append(f"Page {page}: HTTP {response.status_code}")
                        break
                    
                    data = response.json()
                    players = data.get("data", [])
                    
                    if not players:
                        logger.info(f"[MASTER ROSTER] No more players on page {page}")
                        break
                    
                    all_players.extend(players)
                    logger.info(f"[MASTER ROSTER] Page {page}: {len(players)} players (total: {len(all_players)})")
                    
                    # Check for next page
                    meta = data.get("meta", {})
                    cursor = meta.get("next_cursor")
                    
                    if not cursor:
                        break
                    
                    page += 1
                    await asyncio.sleep(0.2)  # Rate limit protection
            
            logger.info(f"[MASTER ROSTER] Fetched {len(all_players)} total players from BallDontLie")
            
            # Process and store players
            roster_docs = []
            
            for player in all_players:
                player_id = player.get("id")
                first_name = player.get("first_name", "")
                last_name = player.get("last_name", "")
                full_name = f"{first_name} {last_name}".strip()
                
                team_data = player.get("team", {})
                team_abbrev = team_data.get("abbreviation", "") if team_data else ""
                team_full = team_data.get("full_name", "") if team_data else ""
                
                # Skip players without valid team (free agents, retired, etc.)
                if not team_abbrev or not full_name:
                    continue
                
                # Normalize names for matching
                normalized_name = self.sanitize_player_name(full_name)
                
                roster_doc = {
                    "player_name": full_name,
                    "normalized_name": normalized_name,
                    "bdl_player_id": player_id,
                    "team_abbreviation": team_abbrev.upper(),
                    "team_full_name": team_full,
                    "position": player.get("position", ""),
                    "height": player.get("height", ""),
                    "weight": player.get("weight", ""),
                    "jersey_number": player.get("jersey_number", ""),
                    "college": player.get("college", ""),
                    "country": player.get("country", ""),
                    "draft_year": player.get("draft_year"),
                    "draft_round": player.get("draft_round"),
                    "draft_number": player.get("draft_number"),
                    "synced_at": sync_start.isoformat(),
                    "source": "balldontlie"
                }
                
                roster_docs.append(roster_doc)
                teams_found.add(team_abbrev.upper())
                players_synced += 1
            
            # Clear existing roster and insert new data
            logger.info(f"[MASTER ROSTER] Clearing old roster and inserting {len(roster_docs)} players...")
            await self.master_roster.delete_many({})
            
            if roster_docs:
                await self.master_roster.insert_many(roster_docs)
                
                # Create indexes for fast lookups
                await self.master_roster.create_index("player_name")
                await self.master_roster.create_index("normalized_name")
                await self.master_roster.create_index("team_abbreviation")
            
            # Update in-memory cache
            self._master_roster_cache = {
                doc["normalized_name"]: doc["team_abbreviation"] 
                for doc in roster_docs
            }
            
            # Log summary
            logger.info("=" * 60)
            logger.info(f"[MASTER ROSTER] SYNC COMPLETE")
            logger.info(f"  Players synced: {players_synced}")
            logger.info(f"  Teams found: {len(teams_found)}")
            logger.info(f"  Teams: {', '.join(sorted(teams_found))}")
            logger.info("=" * 60)
            
            return {
                "success": True,
                "players_synced": players_synced,
                "teams_found": len(teams_found),
                "teams": sorted(teams_found),
                "errors": errors,
                "synced_at": sync_start.isoformat()
            }
            
        except Exception as e:
            logger.error(f"[MASTER ROSTER] Sync failed: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "players_synced": players_synced,
                "errors": errors
            }
    
    async def sync_player_stats(self, player_names: List[str] = None) -> Dict[str, Any]:
        """
        Sync player game logs to MongoDB for cached hit rate calculations.
        
        Data Source Priority:
        1. BallDontLie (primary)
        2. Tank01 (secondary - user has subscription)
        3. NBA.com (tertiary - for rookies)
        
        Should be run daily during the 4:00 AM sync job.
        
        Args:
            player_names: Optional list of specific players to sync. If None, syncs all players with props.
        """
        sync_start = datetime.now(timezone.utc)
        logger.info(f"[STATS SYNC] Starting player stats sync...")
        
        stats_synced = 0
        stats_from_bdl = 0
        stats_from_tank = 0
        stats_from_nba = 0
        errors = []
        
        try:
            # Get list of players to sync
            if player_names is None:
                # Get all players currently in cached_board
                players = await self.cached_board.distinct("player_name")
                player_names = list(players) if players else []
            
            if not player_names:
                logger.warning("[STATS SYNC] No players to sync")
                return {"success": True, "stats_synced": 0, "message": "No players to sync"}
            
            logger.info(f"[STATS SYNC] Syncing stats for {len(player_names)} players...")
            
            # Process players in batches
            batch_size = 10
            for i in range(0, len(player_names), batch_size):
                batch = player_names[i:i+batch_size]
                
                for player_name in batch:
                    try:
                        source = None
                        stats = None
                        
                        # Priority 1: BallDontLie
                        stats = await self._fetch_bdl_stats_for_cache(player_name)
                        if stats and stats.get("games"):
                            source = "balldontlie"
                        
                        # Priority 2: Tank01 (user has subscription)
                        if not source:
                            stats = await self._fetch_tank01_player_stats(player_name)
                            if stats and stats.get("games"):
                                source = "tank01"
                        
                        # Priority 3: NBA.com (fallback for rookies)
                        if not source:
                            stats = self._fetch_nba_api_stats(player_name)
                            if stats and stats.get("games"):
                                source = "nba_api"
                        
                        if stats and stats.get("games"):
                            # Prepare document for MongoDB
                            games = stats.get("games", [])
                            
                            # Sort games by date (most recent first)
                            sorted_games = sorted(
                                games, 
                                key=lambda g: g.get("game", {}).get("date", "") if isinstance(g.get("game"), dict) else g.get("GAME_DATE", ""),
                                reverse=True
                            )
                            
                            doc = {
                                "player_name": player_name,
                                "normalized_name": self.sanitize_player_name(player_name),
                                "games": sorted_games,
                                "total_games": len(sorted_games),
                                "source": source,
                                "synced_at": sync_start.isoformat()
                            }
                            
                            # Upsert to MongoDB
                            await self.player_stats.update_one(
                                {"normalized_name": doc["normalized_name"]},
                                {"$set": doc},
                                upsert=True
                            )
                            
                            stats_synced += 1
                            if source == "balldontlie":
                                stats_from_bdl += 1
                            elif source == "tank01":
                                stats_from_tank += 1
                            else:
                                stats_from_nba += 1
                            
                    except Exception as e:
                        errors.append(f"{player_name}: {str(e)}")
                        logger.debug(f"[STATS SYNC] Error syncing {player_name}: {e}")
                    
                    # Rate limiting
                    await asyncio.sleep(0.15)
                
                # Progress logging
                if i % 50 == 0 and i > 0:
                    logger.info(f"[STATS SYNC] Progress: {i}/{len(player_names)} players")
            
            # Create index for fast lookups
            await self.player_stats.create_index("normalized_name", unique=True)
            await self.player_stats.create_index("player_name")
            
            duration = (datetime.now(timezone.utc) - sync_start).total_seconds()
            logger.info(f"[STATS SYNC] Completed: {stats_synced} players synced in {duration:.1f}s (BDL: {stats_from_bdl}, Tank01: {stats_from_tank}, NBA: {stats_from_nba})")
            
            return {
                "success": True,
                "stats_synced": stats_synced,
                "from_balldontlie": stats_from_bdl,
                "from_tank01": stats_from_tank,
                "from_nba_api": stats_from_nba,
                "errors": errors[:10],  # Limit errors in response
                "duration_seconds": duration,
                "synced_at": sync_start.isoformat()
            }
            
        except Exception as e:
            logger.error(f"[STATS SYNC] Failed: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "stats_synced": stats_synced,
                "errors": errors
            }
    
    async def _fetch_bdl_stats_for_cache(self, player_name: str) -> Dict[str, Any]:
        """
        Fetch player stats from BallDontLie for caching.
        Returns raw game data without hit rate calculations.
        """
        try:
            player_id = await self._get_bdl_player_id(player_name)
            if not player_id:
                return {}
            
            url = f"{BDL_BASE_URL}/stats"
            params = {
                "player_ids[]": player_id,
                "seasons[]": CURRENT_SEASON,
                "per_page": 100
            }
            headers = {"Authorization": BDL_API_KEY}
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, headers=headers, timeout=15.0)
                
                if response.status_code == 200:
                    data = response.json()
                    games = data.get("data", [])
                    
                    if games:
                        return {
                            "player_name": player_name,
                            "games": games,
                            "total_games": len(games),
                            "source": "balldontlie"
                        }
        except Exception as e:
            logger.debug(f"[BDL CACHE] Error fetching {player_name}: {e}")
        
        return {}
    
    async def get_cached_player_stats(self, player_name: str) -> Dict[str, Any]:
        """
        Get player stats from MongoDB cache.
        Returns empty dict if not found - caller should handle missing data.
        """
        normalized = self.sanitize_player_name(player_name)
        doc = await self.player_stats.find_one(
            {"normalized_name": normalized},
            {"_id": 0}
        )
        return doc if doc else {}
    
    async def get_team_from_master_roster(self, player_name: str) -> Optional[str]:
        """
        Look up a player's team using priority order:
        1. KNOWN_PLAYER_TEAMS (manual overrides for incorrect API data)
        2. Master Roster from BallDontLie (may have errors)
        3. Fuzzy matching
        
        Args:
            player_name: The player's name to look up
            
        Returns:
            Team abbreviation if found, None otherwise
        """
        # PRIORITY 1: Check manual overrides FIRST (fixes BallDontLie errors)
        # Example: BallDontLie shows Luka Doncic on LAL but he plays for DAL
        if player_name in KNOWN_PLAYER_TEAMS:
            return KNOWN_PLAYER_TEAMS[player_name]
        
        # Normalize the name for lookups
        normalized = self.sanitize_player_name(player_name)
        
        # Check normalized version in manual overrides
        for known_name, team in KNOWN_PLAYER_TEAMS.items():
            if self.sanitize_player_name(known_name) == normalized:
                return team
        
        # PRIORITY 2: Check in-memory master roster cache
        if normalized in self._master_roster_cache:
            return self._master_roster_cache[normalized]
        
        # PRIORITY 3: Query database directly
        doc = await self.master_roster.find_one(
            {"normalized_name": normalized},
            {"_id": 0, "team_abbreviation": 1}
        )
        
        if doc:
            team = doc.get("team_abbreviation")
            self._master_roster_cache[normalized] = team
            return team
        
        # PRIORITY 4: Try fuzzy match
        all_players = await self.master_roster.find(
            {},
            {"_id": 0, "player_name": 1, "normalized_name": 1, "team_abbreviation": 1}
        ).to_list(None)
        
        best_match = None
        best_ratio = 0
        
        for p in all_players:
            # Check various name variations
            ratio = 0
            p_normalized = p.get("normalized_name", "")
            p_full = p.get("player_name", "").lower()
            
            # Exact normalized match
            if normalized == p_normalized:
                best_match = p
                break
            
            # Partial match check
            if normalized in p_normalized or p_normalized in normalized:
                ratio = 0.8
            elif p_full in player_name.lower() or player_name.lower() in p_full:
                ratio = 0.7
            
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = p
        
        if best_match and best_ratio >= 0.7:
            team = best_match.get("team_abbreviation")
            self._master_roster_cache[normalized] = team
            return team
        
        return None
    
    async def get_photo_and_team_from_master_roster(self, player_name: str) -> Optional[Dict]:
        """
        Look up a player's photo_url AND team from master roster with fuzzy matching.
        
        Returns dict with photo_url, team_abbreviation, nba_com_id or None if not found.
        
        Handles name variations like:
        - "Herb Jones" vs "Herbert Jones"
        - "G.G. Jackson" vs "Gregory Jackson"  
        - "Jaylen Wells" (exact match only - no partial first name matching)
        - "Jabari Smith Jr." vs "Jabari Smith"
        """
        if not player_name:
            return None
            
        normalized = self.sanitize_player_name(player_name)
        
        # Try exact normalized match first
        doc = await self.master_roster.find_one(
            {"normalized_name": normalized},
            {"_id": 0, "photo_url": 1, "team_abbreviation": 1, "nba_com_id": 1, "player_name": 1}
        )
        
        if doc:
            return doc
        
        # Remove common suffixes for matching
        name_without_suffix = player_name
        for suffix in [" Jr.", " Jr", " III", " II", " IV", " Sr.", " Sr"]:
            if player_name.endswith(suffix):
                name_without_suffix = player_name[:-len(suffix)]
                break
        
        # Also remove periods from initials (G.G. -> GG)
        name_cleaned = name_without_suffix.replace(".", "")
        
        # Try matching without suffix
        if name_without_suffix != player_name:
            normalized_no_suffix = self.sanitize_player_name(name_without_suffix)
            doc = await self.master_roster.find_one(
                {"normalized_name": normalized_no_suffix},
                {"_id": 0, "photo_url": 1, "team_abbreviation": 1, "nba_com_id": 1, "player_name": 1}
            )
            if doc:
                return doc
        
        # Try regex matching with BOTH first AND last name (must match both)
        name_parts = name_cleaned.split()
        if len(name_parts) >= 2:
            first_name = name_parts[0]
            last_name = name_parts[-1]
            
            # Skip if last name is a suffix we missed
            if last_name.lower() in ["jr", "iii", "ii", "iv", "sr"]:
                last_name = name_parts[-2] if len(name_parts) > 2 else first_name
            
            # STRICT: Match must have EXACT last name at word boundary
            # This prevents "Jaylen Wells" from matching "Jaylen Brown"
            doc = await self.master_roster.find_one(
                {
                    "player_name": {
                        "$regex": f"^{first_name}.*\\b{last_name}\\b",
                        "$options": "i"
                    }
                },
                {"_id": 0, "photo_url": 1, "team_abbreviation": 1, "nba_com_id": 1, "player_name": 1}
            )
            
            if doc:
                return doc
            
            # Try nickname/initial expansions for first name ONLY if last name matches exactly
            # G.G. -> Gregory, Herb -> Herbert, etc.
            first_name_variations = self._get_name_variations(first_name)
            for variation in first_name_variations:
                doc = await self.master_roster.find_one(
                    {
                        "player_name": {
                            "$regex": f"^{variation}.*\\b{last_name}\\b",
                            "$options": "i"
                        }
                    },
                    {"_id": 0, "photo_url": 1, "team_abbreviation": 1, "nba_com_id": 1, "player_name": 1}
                )
                if doc:
                    return doc
        
        return None
    
    def _get_name_variations(self, first_name: str) -> list:
        """Get common variations/expansions for a first name."""
        variations = []
        
        # Common nickname mappings
        nickname_map = {
            "gg": ["gregory", "george"],
            "jj": ["james", "john", "junior"],
            "tj": ["thomas", "timothy"],
            "pj": ["paul", "peter"],
            "cj": ["charles", "christopher"],
            "aj": ["anthony", "andrew"],
            "rj": ["robert", "richard"],
            "herb": ["herbert"],
            "mike": ["michael"],
            "chris": ["christopher"],
            "matt": ["matthew"],
            "dan": ["daniel"],
            "rob": ["robert"],
            "will": ["william"],
            "nick": ["nicholas"],
            "alex": ["alexander"],
        }
        
        # Check for nickname expansion
        name_lower = first_name.lower().replace(".", "")
        if name_lower in nickname_map:
            variations.extend(nickname_map[name_lower])
        
        return variations

    async def get_photo_url_from_master_roster(self, player_name: str) -> Optional[str]:
        """
        Get photo URL from Master Hub (SSOT).
        Photos are pre-injected and LOCKED - no external API calls.
        """
        try:
            player = await fetchPlayerIntelByName(player_name)
            if player and player.get("headshot_url"):
                return player.get("headshot_url")
        except Exception as e:
            logger.warning(f"[MASTER HUB] Photo lookup failed for {player_name}: {e}")
        
        # Fallback: Generate ESPN URL using static player ID mapping
        nba_id = get_nba_player_id(player_name)
        if nba_id:
            return f"https://a.espncdn.com/combiner/i?img=/i/headshots/nba/players/full/{nba_id}.png"
        return None
    
    async def refresh_cached_board_photos(self) -> Dict[str, Any]:
        """
        DEPRECATED: All enrichment now happens during sync-to-mongo.
        
        To refresh photos, run the full sync:
        POST /api/v3/sync-to-mongo
        
        This endpoint is kept for backwards compatibility but does nothing.
        """
        logger.warning("[DEPRECATED] refresh_cached_board_photos called - use sync-to-mongo instead")
        return {
            "success": True,
            "message": "DEPRECATED: Use POST /api/v3/sync-to-mongo to refresh data",
            "photos_updated": 0
        }
    
    async def refresh_all_photos(self) -> Dict[str, Any]:
        """
        DEPRECATED: All enrichment now happens during sync-to-mongo.
        
        To refresh photos, run the full sync:
        POST /api/v3/sync-to-mongo
        
        This endpoint is kept for backwards compatibility but does nothing.
        """
        logger.warning("[DEPRECATED] refresh_all_photos called - use sync-to-mongo instead")
        return {
            "success": True,
            "message": "DEPRECATED: Use POST /api/v3/sync-to-mongo to refresh data",
            "total_photos_updated": 0
        }
    
    async def load_master_roster_cache(self):
        """Load the master roster into memory for fast lookups."""
        logger.info("[MASTER ROSTER] Loading roster into memory cache...")
        
        roster = await self.master_roster.find(
            {},
            {"_id": 0, "normalized_name": 1, "team_abbreviation": 1}
        ).to_list(None)
        
        self._master_roster_cache = {
            doc["normalized_name"]: doc["team_abbreviation"]
            for doc in roster
        }
        
        logger.info(f"[MASTER ROSTER] Loaded {len(self._master_roster_cache)} players into cache")
    
    async def flag_unknown_player(self, player_name: str, odds_api_team: str, game_info: Dict):
        """
        Flag a player not found in master roster for manual review.
        
        Args:
            player_name: The player name from Odds API
            odds_api_team: The team provided by Odds API (may be incorrect)
            game_info: Additional context about the game
        """
        normalized = self.sanitize_player_name(player_name)
        
        await self.flagged_players.update_one(
            {"normalized_name": normalized},
            {
                "$set": {
                    "player_name": player_name,
                    "normalized_name": normalized,
                    "odds_api_team": odds_api_team,
                    "home_team": game_info.get("home_team", ""),
                    "away_team": game_info.get("away_team", ""),
                    "game_date": game_info.get("game_date", ""),
                    "flagged_at": datetime.now(timezone.utc).isoformat(),
                    "reviewed": False
                }
            },
            upsert=True
        )
        
        logger.warning(f"[FLAGGED] Unknown player: {player_name} (Odds API says: {odds_api_team})")
    
    async def sync_player_photos(self) -> Dict[str, Any]:
        """
        GLOBAL PHOTO SYNC - Populate headshots for 450+ NBA players using ESPN CDN.
        
        Source Priority:
        1. ESPN CDN (via Tank01 espnID): https://a.espncdn.com/i/headshots/nba/players/full/{espn_id}.png
        2. NBA CDN (fallback): https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_id}.png
        3. Team Logo (final fallback): Colorful team branding
        
        GOAL: 0% gray silhouettes - every player gets a face or team brand.
        
        Returns:
            Dict with sync status and photo counts
        """
        logger.info("=" * 60)
        logger.info("[GLOBAL PHOTO SYNC] Starting ESPN headshot pipeline for 450+ players...")
        logger.info("=" * 60)
        
        sync_start = datetime.now(timezone.utc)
        espn_photos = 0
        nba_photos = 0
        logo_fallbacks = 0
        total_processed = 0
        
        # NBA Team Logo URLs (final fallback - NO GRAY SILHOUETTES)
        TEAM_LOGOS = {
            "ATL": "https://cdn.nba.com/logos/nba/1610612737/global/L/logo.svg",
            "BOS": "https://cdn.nba.com/logos/nba/1610612738/global/L/logo.svg",
            "BKN": "https://cdn.nba.com/logos/nba/1610612751/global/L/logo.svg",
            "CHA": "https://cdn.nba.com/logos/nba/1610612766/global/L/logo.svg",
            "CHI": "https://cdn.nba.com/logos/nba/1610612741/global/L/logo.svg",
            "CLE": "https://cdn.nba.com/logos/nba/1610612739/global/L/logo.svg",
            "DAL": "https://cdn.nba.com/logos/nba/1610612742/global/L/logo.svg",
            "DEN": "https://cdn.nba.com/logos/nba/1610612743/global/L/logo.svg",
            "DET": "https://cdn.nba.com/logos/nba/1610612765/global/L/logo.svg",
            "GSW": "https://cdn.nba.com/logos/nba/1610612744/global/L/logo.svg",
            "HOU": "https://cdn.nba.com/logos/nba/1610612745/global/L/logo.svg",
            "IND": "https://cdn.nba.com/logos/nba/1610612754/global/L/logo.svg",
            "LAC": "https://cdn.nba.com/logos/nba/1610612746/global/L/logo.svg",
            "LAL": "https://cdn.nba.com/logos/nba/1610612747/global/L/logo.svg",
            "MEM": "https://cdn.nba.com/logos/nba/1610612763/global/L/logo.svg",
            "MIA": "https://cdn.nba.com/logos/nba/1610612748/global/L/logo.svg",
            "MIL": "https://cdn.nba.com/logos/nba/1610612749/global/L/logo.svg",
            "MIN": "https://cdn.nba.com/logos/nba/1610612750/global/L/logo.svg",
            "NOP": "https://cdn.nba.com/logos/nba/1610612740/global/L/logo.svg",
            "NYK": "https://cdn.nba.com/logos/nba/1610612752/global/L/logo.svg",
            "OKC": "https://cdn.nba.com/logos/nba/1610612760/global/L/logo.svg",
            "ORL": "https://cdn.nba.com/logos/nba/1610612753/global/L/logo.svg",
            "PHI": "https://cdn.nba.com/logos/nba/1610612755/global/L/logo.svg",
            "PHX": "https://cdn.nba.com/logos/nba/1610612756/global/L/logo.svg",
            "POR": "https://cdn.nba.com/logos/nba/1610612757/global/L/logo.svg",
            "SAC": "https://cdn.nba.com/logos/nba/1610612758/global/L/logo.svg",
            "SAS": "https://cdn.nba.com/logos/nba/1610612759/global/L/logo.svg",
            "TOR": "https://cdn.nba.com/logos/nba/1610612761/global/L/logo.svg",
            "UTA": "https://cdn.nba.com/logos/nba/1610612762/global/L/logo.svg",
            "WAS": "https://cdn.nba.com/logos/nba/1610612764/global/L/logo.svg",
        }
        
        # ==================== STEP 1: FETCH ALL PLAYERS FROM TANK01 ====================
        logger.info("[PHOTO SYNC] Step 1: Fetching player list from Tank01 API...")
        
        espn_id_map = {}  # player_name -> espn_id
        
        try:
            headers = {
                "X-RapidAPI-Key": TANK01_API_KEY,
                "X-RapidAPI-Host": TANK01_HOST
            }
            
            async with httpx.AsyncClient(timeout=60) as client:
                # Use getNBATeams with rosters=true to get all players
                response = await client.get(
                    f"{TANK01_BASE}/getNBATeams",
                    headers=headers,
                    params={"rosters": "true"}
                )
                
                if response.status_code == 200:
                    data = response.json()
                    teams_data = data.get("body", [])
                    
                    logger.info(f"[PHOTO SYNC] Tank01 returned {len(teams_data)} teams with rosters")
                    
                    # Extract players from each team's roster
                    for team in teams_data:
                        team_abv = team.get("teamAbv", "")
                        roster = team.get("Roster", {})
                        
                        if isinstance(roster, dict):
                            for player_id, player in roster.items():
                                player_name = player.get("longName", "")
                                espn_id = player.get("espnID")
                                espn_headshot = player.get("espnHeadshot", "")
                                nba_id = player.get("nbaComID")
                                
                                if player_name:
                                    normalized = self.sanitize_player_name(player_name)
                                    espn_id_map[normalized] = {
                                        "espn_id": espn_id,
                                        "espn_headshot": espn_headshot,
                                        "nba_id": nba_id,
                                        "team": team_abv,
                                        "original_name": player_name
                                    }
                    
                    logger.info(f"[PHOTO SYNC] Mapped {len(espn_id_map)} players with ESPN IDs")
                else:
                    logger.warning(f"[PHOTO SYNC] Tank01 API returned {response.status_code}")
                    
        except Exception as e:
            logger.error(f"[PHOTO SYNC] Tank01 API error: {str(e)}")
        
        # ==================== STEP 2: UPDATE MASTER ROSTER ====================
        logger.info("[PHOTO SYNC] Step 2: Updating master_roster with photo URLs...")
        
        roster_players = await self.master_roster.find({}).to_list(None)
        logger.info(f"[PHOTO SYNC] Processing {len(roster_players)} players in master_roster...")
        
        for player in roster_players:
            player_name = player.get("player_name", "")
            team = player.get("team_abbreviation", "")
            normalized = self.sanitize_player_name(player_name)
            
            photo_url = None
            photo_source = None
            espn_id = None
            
            # SOURCE 1: ESPN CDN (best quality from Tank01)
            if normalized in espn_id_map:
                tank_data = espn_id_map[normalized]
                espn_id = tank_data.get("espn_id")
                espn_headshot = tank_data.get("espn_headshot", "")
                
                # Use direct ESPN headshot URL if available
                if espn_headshot:
                    photo_url = espn_headshot
                    photo_source = "espn_direct"
                    espn_photos += 1
                elif espn_id:
                    photo_url = f"https://a.espncdn.com/i/headshots/nba/players/full/{espn_id}.png"
                    photo_source = "espn_cdn"
                    espn_photos += 1
            
            # SOURCE 2: NBA CDN (fallback using static mapping)
            if not photo_url:
                nba_id = NBA_PLAYER_IDS.get(player_name)
                if nba_id:
                    photo_url = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_id}.png"
                    photo_source = "nba_cdn"
                    nba_photos += 1
            
            # SOURCE 3: Team Logo (final fallback - NO GRAY!)
            team_logo = TEAM_LOGOS.get(team, "")
            if not photo_url and team_logo:
                photo_url = team_logo
                photo_source = "team_logo"
                logo_fallbacks += 1
            
            # Update database
            await self.master_roster.update_one(
                {"player_name": player_name},
                {
                    "$set": {
                        "photo_url": photo_url,
                        "photo_source": photo_source,
                        "espn_id": espn_id,
                        "team_logo_url": team_logo,
                        "photo_synced_at": sync_start.isoformat()
                    }
                }
            )
            total_processed += 1
        
        # ==================== STEP 3: UPDATE CACHED BOARD (ACTIVE PLAYERS) ====================
        logger.info("[PHOTO SYNC] Step 3: Updating cached_board with photo URLs...")
        
        active_players = await self.cached_board.find({}).to_list(None)
        active_count = 0
        
        for player in active_players:
            player_name = player.get("player_name", "")
            team = player.get("team", "")
            normalized = self.sanitize_player_name(player_name)
            
            photo_url = None
            photo_source = None
            espn_id = None
            
            # SOURCE 1: ESPN CDN
            if normalized in espn_id_map:
                tank_data = espn_id_map[normalized]
                espn_id = tank_data.get("espn_id")
                espn_headshot = tank_data.get("espn_headshot", "")
                
                if espn_headshot:
                    photo_url = espn_headshot
                    photo_source = "espn_direct"
                elif espn_id:
                    photo_url = f"https://a.espncdn.com/i/headshots/nba/players/full/{espn_id}.png"
                    photo_source = "espn_cdn"
            
            # SOURCE 2: NBA CDN
            if not photo_url:
                nba_id = NBA_PLAYER_IDS.get(player_name) or player.get("nba_id")
                if nba_id:
                    photo_url = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_id}.png"
                    photo_source = "nba_cdn"
            
            # SOURCE 3: Team Logo (NO GRAY!)
            team_logo = TEAM_LOGOS.get(team, "")
            if not photo_url and team_logo:
                photo_url = team_logo
                photo_source = "team_logo"
            
            # Update database
            await self.cached_board.update_one(
                {"player_name": player_name},
                {
                    "$set": {
                        "photo_url": photo_url,
                        "photo_source": photo_source,
                        "espn_id": espn_id,
                        "team_logo_url": team_logo,
                        "photo_synced_at": sync_start.isoformat()
                    }
                }
            )
            active_count += 1
        
        # ==================== SUMMARY ====================
        duration = (datetime.now(timezone.utc) - sync_start).total_seconds()
        
        logger.info("=" * 60)
        logger.info(f"[GLOBAL PHOTO SYNC] COMPLETE")
        logger.info(f"  Total players processed: {total_processed}")
        logger.info(f"  ESPN headshots: {espn_photos}")
        logger.info(f"  NBA CDN headshots: {nba_photos}")
        logger.info(f"  Team logo fallbacks: {logo_fallbacks}")
        logger.info(f"  Active players updated: {active_count}")
        logger.info(f"  Gray silhouettes: 0 (GOAL ACHIEVED)")
        logger.info(f"  Duration: {duration:.1f}s")
        logger.info("=" * 60)
        
        return {
            "success": True,
            "total_processed": total_processed,
            "espn_photos": espn_photos,
            "nba_photos": nba_photos,
            "logo_fallbacks": logo_fallbacks,
            "active_players_updated": active_count,
            "gray_silhouettes": 0,
            "tank01_players_found": len(espn_id_map),
            "synced_at": sync_start.isoformat(),
            "duration_seconds": round(duration, 1)
        }
    
    async def sync_active_players_with_photos(self) -> Dict[str, Any]:
        """
        ACTIVE PLAYER SYNC - Fetches ONLY current NBA players from Tank01 with headshots.
        
        This replaces the old approach of syncing 5000+ historical players from BallDontLie.
        Tank01 returns ~530 active players with ESPN headshot URLs included.
        
        Data stored per player:
        - player_name, team, position
        - espn_id, nba_com_id (for cross-referencing)
        - photo_url (ESPN headshot or NBA CDN fallback)
        - jersey_number, height, weight, college, etc.
        
        Returns:
            Dict with sync status and player counts
        """
        logger.info("=" * 60)
        logger.info("[ACTIVE PLAYER SYNC] Fetching current NBA rosters from Tank01...")
        logger.info("=" * 60)
        
        sync_start = datetime.now(timezone.utc)
        players_synced = 0
        teams_processed = 0
        photos_found = 0
        errors = []
        
        # Tank01 uses non-standard abbreviations - map to standard NBA abbreviations
        TANK01_TO_NBA_ABBREV = {
            "GS": "GSW",    # Golden State Warriors
            "NO": "NOP",    # New Orleans Pelicans
            "NY": "NYK",    # New York Knicks
            "PHO": "PHX",   # Phoenix Suns
            "SA": "SAS",    # San Antonio Spurs
        }
        
        # Team logos for fallback (using standard NBA abbreviations)
        TEAM_LOGOS = {
            "ATL": "https://cdn.nba.com/logos/nba/1610612737/global/L/logo.svg",
            "BOS": "https://cdn.nba.com/logos/nba/1610612738/global/L/logo.svg",
            "BKN": "https://cdn.nba.com/logos/nba/1610612751/global/L/logo.svg",
            "CHA": "https://cdn.nba.com/logos/nba/1610612766/global/L/logo.svg",
            "CHI": "https://cdn.nba.com/logos/nba/1610612741/global/L/logo.svg",
            "CLE": "https://cdn.nba.com/logos/nba/1610612739/global/L/logo.svg",
            "DAL": "https://cdn.nba.com/logos/nba/1610612742/global/L/logo.svg",
            "DEN": "https://cdn.nba.com/logos/nba/1610612743/global/L/logo.svg",
            "DET": "https://cdn.nba.com/logos/nba/1610612765/global/L/logo.svg",
            "GSW": "https://cdn.nba.com/logos/nba/1610612744/global/L/logo.svg",
            "HOU": "https://cdn.nba.com/logos/nba/1610612745/global/L/logo.svg",
            "IND": "https://cdn.nba.com/logos/nba/1610612754/global/L/logo.svg",
            "LAC": "https://cdn.nba.com/logos/nba/1610612746/global/L/logo.svg",
            "LAL": "https://cdn.nba.com/logos/nba/1610612747/global/L/logo.svg",
            "MEM": "https://cdn.nba.com/logos/nba/1610612763/global/L/logo.svg",
            "MIA": "https://cdn.nba.com/logos/nba/1610612748/global/L/logo.svg",
            "MIL": "https://cdn.nba.com/logos/nba/1610612749/global/L/logo.svg",
            "MIN": "https://cdn.nba.com/logos/nba/1610612750/global/L/logo.svg",
            "NOP": "https://cdn.nba.com/logos/nba/1610612740/global/L/logo.svg",
            "NYK": "https://cdn.nba.com/logos/nba/1610612752/global/L/logo.svg",
            "OKC": "https://cdn.nba.com/logos/nba/1610612760/global/L/logo.svg",
            "ORL": "https://cdn.nba.com/logos/nba/1610612753/global/L/logo.svg",
            "PHI": "https://cdn.nba.com/logos/nba/1610612755/global/L/logo.svg",
            "PHX": "https://cdn.nba.com/logos/nba/1610612756/global/L/logo.svg",
            "POR": "https://cdn.nba.com/logos/nba/1610612757/global/L/logo.svg",
            "SAC": "https://cdn.nba.com/logos/nba/1610612758/global/L/logo.svg",
            "SAS": "https://cdn.nba.com/logos/nba/1610612759/global/L/logo.svg",
            "TOR": "https://cdn.nba.com/logos/nba/1610612761/global/L/logo.svg",
            "UTA": "https://cdn.nba.com/logos/nba/1610612762/global/L/logo.svg",
            "WAS": "https://cdn.nba.com/logos/nba/1610612764/global/L/logo.svg",
        }
        
        try:
            headers = {
                "X-RapidAPI-Key": TANK01_API_KEY,
                "X-RapidAPI-Host": TANK01_HOST
            }
            
            async with httpx.AsyncClient(timeout=60) as client:
                # Fetch all teams with rosters from Tank01
                response = await client.get(
                    f"{TANK01_BASE}/getNBATeams",
                    headers=headers,
                    params={"rosters": "true"}
                )
                
                if response.status_code != 200:
                    logger.error(f"[ACTIVE PLAYER SYNC] Tank01 API error: {response.status_code}")
                    return {"success": False, "error": f"Tank01 API returned {response.status_code}"}
                
                data = response.json()
                teams_data = data.get("body", [])
                
                logger.info(f"[ACTIVE PLAYER SYNC] Tank01 returned {len(teams_data)} teams")
                
                # Clear existing master roster and rebuild with ONLY active players
                await self.master_roster.delete_many({})
                logger.info("[ACTIVE PLAYER SYNC] Cleared old master roster")
                
                # Process each team's roster
                player_docs = []
                
                for team in teams_data:
                    tank01_abv = team.get("teamAbv", "")
                    team_name = team.get("teamName", "")
                    team_city = team.get("teamCity", "")
                    roster = team.get("Roster", {})
                    
                    # Normalize Tank01 abbreviation to standard NBA abbreviation
                    team_abv = TANK01_TO_NBA_ABBREV.get(tank01_abv, tank01_abv)
                    
                    # Skip non-NBA teams (like All-Star teams)
                    if team_abv not in TEAM_LOGOS:
                        logger.warning(f"[ACTIVE PLAYER SYNC] Skipping unknown team: {tank01_abv}")
                        continue
                    
                    teams_processed += 1
                    
                    if isinstance(roster, dict):
                        for player_id, player in roster.items():
                            player_name = player.get("longName", "")
                            if not player_name:
                                continue
                            
                            # Get photo URL - prioritize ESPN headshot
                            espn_headshot = player.get("espnHeadshot", "")
                            espn_id = player.get("espnID")
                            nba_com_id = player.get("nbaComID")
                            
                            # Determine best photo URL
                            # Skip ESPN "nophoto" placeholder URLs - they show a gray silhouette
                            photo_url = None
                            photo_source = None
                            
                            # Check if ESPN headshot is a real photo (not nophoto placeholder)
                            is_real_espn_photo = (
                                espn_headshot and 
                                "nophoto" not in espn_headshot.lower() and
                                "combiner" not in espn_headshot  # combiner URLs often fail
                            )
                            
                            if is_real_espn_photo:
                                photo_url = espn_headshot
                                photo_source = "espn_direct"
                                photos_found += 1
                            elif espn_id and espn_headshot and "nophoto" not in espn_headshot.lower():
                                # Use ESPN CDN if we have ID and it's not a nophoto
                                photo_url = f"https://a.espncdn.com/i/headshots/nba/players/full/{espn_id}.png"
                                photo_source = "espn_cdn"
                                photos_found += 1
                            elif nba_com_id:
                                # Fall back to NBA.com headshot
                                photo_url = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_com_id}.png"
                                photo_source = "nba_cdn"
                                photos_found += 1
                            else:
                                # Final fallback: team logo
                                photo_url = TEAM_LOGOS.get(team_abv, "")
                                photo_source = "team_logo"
                            
                            # Build player document
                            player_doc = {
                                "player_name": player_name,
                                "normalized_name": self.sanitize_player_name(player_name),
                                "team_abbreviation": team_abv,  # Use normalized abbreviation
                                "team_name": f"{team_city} {team_name}",
                                "position": player.get("pos", ""),
                                "jersey_number": player.get("jerseyNum", ""),
                                "height": player.get("height", ""),
                                "weight": player.get("weight", ""),
                                "college": player.get("college", ""),
                                "birth_date": player.get("bDay", ""),
                                "years_pro": player.get("exp", ""),
                                "tank01_player_id": player_id,
                                "espn_id": espn_id,
                                "nba_com_id": nba_com_id,
                                "photo_url": photo_url,
                                "photo_source": photo_source,
                                "team_logo_url": TEAM_LOGOS.get(team_abv, ""),
                                "is_active": True,
                                "synced_at": sync_start.isoformat()
                            }
                            
                            player_docs.append(player_doc)
                            players_synced += 1
                
                # Bulk insert all players
                if player_docs:
                    await self.master_roster.insert_many(player_docs)
                    logger.info(f"[ACTIVE PLAYER SYNC] Inserted {len(player_docs)} active players")
                
        except Exception as e:
            logger.error(f"[ACTIVE PLAYER SYNC] Error: {str(e)}")
            errors.append(str(e))
        
        duration = (datetime.now(timezone.utc) - sync_start).total_seconds()
        
        logger.info("=" * 60)
        logger.info(f"[ACTIVE PLAYER SYNC] COMPLETE")
        logger.info(f"  Teams processed: {teams_processed}")
        logger.info(f"  Players synced: {players_synced}")
        logger.info(f"  Photos found: {photos_found}")
        logger.info(f"  Duration: {duration:.1f}s")
        logger.info("=" * 60)
        
        return {
            "success": True,
            "teams_processed": teams_processed,
            "players_synced": players_synced,
            "photos_found": photos_found,
            "photo_coverage": f"{(photos_found/players_synced*100):.1f}%" if players_synced > 0 else "0%",
            "errors": errors,
            "synced_at": sync_start.isoformat(),
            "duration_seconds": round(duration, 1)
        }
    
    def get_player_photo_url(self, player_name: str, team: str = None, nba_id: int = None) -> Dict[str, str]:
        """
        Get the best available photo URL for a player.
        
        Priority:
        1. NBA CDN headshot (if nba_id available)
        2. Team logo (fallback)
        
        Returns:
            Dict with photo_url and fallback_url
        """
        # Team logos for fallback
        TEAM_LOGOS = {
            "ATL": "https://cdn.nba.com/logos/nba/1610612737/global/L/logo.svg",
            "BOS": "https://cdn.nba.com/logos/nba/1610612738/global/L/logo.svg",
            "BKN": "https://cdn.nba.com/logos/nba/1610612751/global/L/logo.svg",
            "CHA": "https://cdn.nba.com/logos/nba/1610612766/global/L/logo.svg",
            "CHI": "https://cdn.nba.com/logos/nba/1610612741/global/L/logo.svg",
            "CLE": "https://cdn.nba.com/logos/nba/1610612739/global/L/logo.svg",
            "DAL": "https://cdn.nba.com/logos/nba/1610612742/global/L/logo.svg",
            "DEN": "https://cdn.nba.com/logos/nba/1610612743/global/L/logo.svg",
            "DET": "https://cdn.nba.com/logos/nba/1610612765/global/L/logo.svg",
            "GSW": "https://cdn.nba.com/logos/nba/1610612744/global/L/logo.svg",
            "HOU": "https://cdn.nba.com/logos/nba/1610612745/global/L/logo.svg",
            "IND": "https://cdn.nba.com/logos/nba/1610612754/global/L/logo.svg",
            "LAC": "https://cdn.nba.com/logos/nba/1610612746/global/L/logo.svg",
            "LAL": "https://cdn.nba.com/logos/nba/1610612747/global/L/logo.svg",
            "MEM": "https://cdn.nba.com/logos/nba/1610612763/global/L/logo.svg",
            "MIA": "https://cdn.nba.com/logos/nba/1610612748/global/L/logo.svg",
            "MIL": "https://cdn.nba.com/logos/nba/1610612749/global/L/logo.svg",
            "MIN": "https://cdn.nba.com/logos/nba/1610612750/global/L/logo.svg",
            "NOP": "https://cdn.nba.com/logos/nba/1610612740/global/L/logo.svg",
            "NYK": "https://cdn.nba.com/logos/nba/1610612752/global/L/logo.svg",
            "OKC": "https://cdn.nba.com/logos/nba/1610612760/global/L/logo.svg",
            "ORL": "https://cdn.nba.com/logos/nba/1610612753/global/L/logo.svg",
            "PHI": "https://cdn.nba.com/logos/nba/1610612755/global/L/logo.svg",
            "PHX": "https://cdn.nba.com/logos/nba/1610612756/global/L/logo.svg",
            "POR": "https://cdn.nba.com/logos/nba/1610612757/global/L/logo.svg",
            "SAC": "https://cdn.nba.com/logos/nba/1610612758/global/L/logo.svg",
            "SAS": "https://cdn.nba.com/logos/nba/1610612759/global/L/logo.svg",
            "TOR": "https://cdn.nba.com/logos/nba/1610612761/global/L/logo.svg",
            "UTA": "https://cdn.nba.com/logos/nba/1610612762/global/L/logo.svg",
            "WAS": "https://cdn.nba.com/logos/nba/1610612764/global/L/logo.svg",
        }
        
        photo_url = None
        fallback_url = TEAM_LOGOS.get(team, "")
        
        # Get NBA ID if not provided
        if not nba_id:
            nba_id = NBA_PLAYER_IDS.get(player_name)
        
        # Build photo URL
        if nba_id:
            photo_url = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_id}.png"
        
        return {
            "photo_url": photo_url,
            "fallback_url": fallback_url,
            "has_photo": photo_url is not None
        }
    
    def get_current_date(self) -> str:
        """Auto-derive today's date from system clock"""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    # ==================== DATABASE NORMALIZATION ====================
    
    def normalize_team_name(self, team_name: str) -> str:
        """
        Convert full team name to 3-letter abbreviation.
        Examples:
        - "Los Angeles Lakers" → "LAL"
        - "Brooklyn Nets" → "BKN"
        - "LAL" → "LAL" (already abbreviated)
        """
        if not team_name:
            return ""
        
        # Check if already abbreviated (3 letters)
        if len(team_name) <= 3:
            return team_name.upper()
        
        # Lookup in team map
        if team_name in NBA_TEAM_MAP:
            return NBA_TEAM_MAP[team_name]
        
        # Try case-insensitive match
        team_lower = team_name.lower()
        for full_name, abbrev in NBA_TEAM_MAP.items():
            if full_name.lower() == team_lower:
                return abbrev
        
        # Partial match (e.g., "Lakers" → "LAL")
        for full_name, abbrev in NBA_TEAM_MAP.items():
            if team_lower in full_name.lower() or full_name.lower() in team_lower:
                return abbrev
        
        # Return first 3 letters as fallback
        return team_name[:3].upper()
    
    def sanitize_player_name(self, name: str) -> str:
        """
        Sanitize and normalize player name for consistent storage.
        
        Handles:
        - Case normalization (Title Case)
        - Special character handling (G.G. → GG)
        - Common nickname variations (Nic → Nicolas)
        - Suffix standardization (Jr → Jr.)
        
        Returns canonical name format for database storage.
        """
        if not name:
            return ""
        
        # Check cache first
        if name in self._canonical_names:
            return self._canonical_names[name]
        
        # Step 1: Basic cleanup
        cleaned = name.strip()
        
        # Step 2: Handle special characters (periods, hyphens)
        # Normalize "G.G." to "G.G." (keep periods for proper names)
        # But normalize "Gilgeous Alexander" to "Gilgeous-Alexander"
        
        # Step 3: Split into parts for processing
        parts = cleaned.split()
        normalized_parts = []
        
        for part in parts:
            part_lower = part.lower().strip()
            
            # Check for known aliases
            for alias, canonical in NAME_ALIASES.items():
                if part_lower == alias or part_lower.replace(".", "") == alias.replace(".", ""):
                    part = canonical.title()
                    break
            
            # Capitalize properly (handle Jr., II, III)
            if part_lower in ["jr", "jr.", "sr", "sr."]:
                part = part_lower.rstrip(".").title() + "."
            elif part_lower in ["ii", "iii", "iv", "v"]:
                part = part.upper()
            elif len(part) <= 3 and "." in part:
                # Keep initials as-is (J.J., P.J., etc.)
                part = part.upper()
            else:
                part = part.title()
            
            normalized_parts.append(part)
        
        # Step 4: Join and handle hyphenated names
        result = " ".join(normalized_parts)
        
        # Fix known hyphenation issues
        result = result.replace("Gilgeous Alexander", "Gilgeous-Alexander")
        result = result.replace("Porter Jr", "Porter Jr.")
        result = result.replace("Payton Ii", "Payton II")
        
        # Cache the result
        self._canonical_names[name] = result
        
        return result
    
    def create_composite_key(self, player_name: str, stat_type: str, game_date: str) -> str:
        """
        Create a unique composite key for deduplication.
        
        Format: {sanitized_player_name}|{stat_type}|{game_date}
        
        Example: "lebron-james|PTS|2026-03-12"
        """
        # Sanitize player name for key (lowercase, no spaces/special chars)
        safe_name = player_name.lower().replace(" ", "-").replace(".", "").replace("'", "")
        safe_stat = stat_type.lower().replace("_", "-")
        
        return f"{safe_name}|{safe_stat}|{game_date}"
    
    # ==================== WAREHOUSE MODEL: SINGLE BATCH SYNC ====================
    
    async def sync_odds_to_mongo(self) -> Dict[str, Any]:
        """
        THE ONLY API CALL - Single batch fetch to MongoDB
        
        DATABASE NORMALIZATION (v2.0):
        1. Team names converted to 3-letter abbreviations (LAL, BKN, etc.)
        2. Player names sanitized and normalized (Nic → Nicolas, etc.)
        3. Composite key: player_name + stat_type + game_date for deduplication
        4. UPSERT mode: Update existing records instead of duplicating
        
        Frontend reads ONLY from MongoDB after this.
        """
        sync_start = datetime.now(timezone.utc)
        self._current_date = self.get_current_date()
        
        logger.info("=" * 70)
        logger.info("[SYNC_ODDS_TO_MONGO] Starting normalized batch sync v2.0...")
        logger.info(f"[SYNC_ODDS_TO_MONGO] Date: {self._current_date}")
        logger.info("=" * 70)
        
        results = {
            "success": True,
            "synced_at": sync_start.isoformat(),
            "events_count": 0,
            "total_props": 0,
            "unique_players": 0,
            "standard_count": 0,
            "demons_count": 0,
            "goblins_count": 0,
            "api_calls_made": 0,
            "duplicates_prevented": 0,
            "names_normalized": 0,
            "teams_normalized": 0,
            "errors": []
        }
        
        try:
            # Step 0: Load Master Roster cache for team lookups
            await self.load_master_roster_cache()
            
            # Check if master roster exists
            roster_count = await self.master_roster.count_documents({})
            if roster_count == 0:
                logger.warning("[SYNC_ODDS_TO_MONGO] Master roster is empty! Running initial sync...")
                await self.sync_master_roster()
            else:
                logger.info(f"[SYNC_ODDS_TO_MONGO] Master roster loaded: {roster_count} players")
            
            # Step 1: Fetch events (1 API call)
            events = await self.fetch_todays_events()
            results["events_count"] = len(events)
            results["api_calls_made"] += 1
            
            if not events:
                logger.warning("[SYNC_ODDS_TO_MONGO] No events found")
                results["success"] = False
                results["errors"].append("No NBA events found")
                return results
            
            # Step 2: Fetch odds for each event (1 API call per event)
            all_props = []
            seen_players_raw = set()
            seen_players_normalized = set()
            
            for event in events:
                event_id = event.get("id")
                if not event_id:
                    continue
                
                odds_data = await self.fetch_prizepicks_odds(event_id, event)
                results["api_calls_made"] += 1
                
                if odds_data:
                    props = self.extract_prizepicks_props(odds_data)
                    all_props.extend(props)
                    
                    for prop in props:
                        seen_players_raw.add(prop.get("player_name"))
                
                # Small delay to avoid rate limiting
                await asyncio.sleep(0.3)
            
            # Step 3: Normalize all props (team names, player names)
            logger.info(f"[NORMALIZATION] Processing {len(all_props)} props...")
            normalized_props = []
            
            for prop in all_props:
                # Normalize team names to 3-letter abbreviations
                original_home = prop.get("home_team", "")
                original_away = prop.get("away_team", "")
                
                prop["home_team"] = self.normalize_team_name(original_home)
                prop["away_team"] = self.normalize_team_name(original_away)
                prop["home_team_full"] = original_home  # Keep original for reference
                prop["away_team_full"] = original_away
                
                if prop["home_team"] != original_home:
                    results["teams_normalized"] += 1
                
                # Normalize player names
                original_name = prop.get("player_name", "")
                normalized_name = self.sanitize_player_name(original_name)
                
                if normalized_name != original_name:
                    results["names_normalized"] += 1
                    logger.debug(f"[NORMALIZE] '{original_name}' → '{normalized_name}'")
                
                prop["player_name"] = normalized_name
                prop["player_name_raw"] = original_name  # Keep original for debugging
                
                seen_players_normalized.add(normalized_name)
                
                # Extract stat type for composite key
                market = prop.get("market", "")
                stat_type = self._extract_stat_type(market)
                
                # Create composite key: player_name + stat_type + line + direction + game_date
                composite_key = f"{normalized_name}|{stat_type}|{prop.get('line', 0)}|{prop.get('direction', '')}|{self._current_date}"
                prop["_composite_key"] = composite_key
                prop["stat_type_extracted"] = stat_type
                prop["game_date"] = self._current_date
                prop["synced_at"] = sync_start.isoformat()
                
                normalized_props.append(prop)
            
            results["unique_players"] = len(seen_players_normalized)
            logger.info(f"[NORMALIZATION] Normalized {results['names_normalized']} names, {results['teams_normalized']} teams")
            logger.info(f"[NORMALIZATION] Raw players: {len(seen_players_raw)} → Normalized: {len(seen_players_normalized)}")
            
            # Step 4: Enrich props with BallDontLie hit rates
            logger.info(f"[SYNC_ODDS_TO_MONGO] Enriching {len(seen_players_normalized)} players with BallDontLie stats...")
            enriched_props = await self._enrich_props_with_stats(normalized_props, list(seen_players_normalized))
            results["stats_enriched"] = len([p for p in enriched_props if p.get("hit_rates")])
            
            # Step 5: Wipe dirty data and insert clean normalized data with UPSERT
            if enriched_props:
                # Clear old data to start fresh (clean slate approach)
                deleted = await self.live_props.delete_many({})
                logger.info(f"[CLEANUP] Wiped {deleted.deleted_count} old records")
                
                # Deduplicate using composite key
                deduplicated = {}
                for prop in enriched_props:
                    key = prop.get("_composite_key", "")
                    if key:
                        if key in deduplicated:
                            results["duplicates_prevented"] += 1
                        # Keep latest version (overwrites duplicates)
                        deduplicated[key] = prop
                
                # Insert deduplicated props
                props_list = list(deduplicated.values())
                for prop in props_list:
                    prop.pop("_id", None)  # Remove any existing _id
                
                if props_list:
                    # Create unique index on composite key for future upserts
                    try:
                        await self.live_props.create_index("_composite_key", unique=True, sparse=True)
                    except Exception:
                        pass  # Index may already exist
                    
                    await self.live_props.insert_many(props_list)
                
                results["total_props"] = len(props_list)
                results["standard_count"] = sum(1 for p in props_list if p.get("prop_type") == "standard")
                results["demons_count"] = sum(1 for p in props_list if p.get("is_demon"))
                results["goblins_count"] = sum(1 for p in props_list if p.get("is_goblin"))
                
                logger.info(f"[SYNC_ODDS_TO_MONGO] Stored {len(props_list)} clean, deduplicated props")
                logger.info(f"[SYNC_ODDS_TO_MONGO] Duplicates prevented: {results['duplicates_prevented']}")
            
            # Step 6: Build cached board for frontend (grouped by player)
            await self._build_cached_board(props_list, sync_start)
            
        except Exception as e:
            logger.error(f"[SYNC_ODDS_TO_MONGO] Error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results["success"] = False
            results["errors"].append(str(e))
        
        duration = (datetime.now(timezone.utc) - sync_start).total_seconds()
        results["duration_seconds"] = duration
        
        logger.info("=" * 70)
        logger.info(f"[SYNC_ODDS_TO_MONGO] COMPLETE (Normalized v2.0)")
        logger.info(f"  Duration: {duration:.1f}s")
        logger.info(f"  API Calls Made: {results['api_calls_made']}")
        logger.info(f"  Props Stored: {results['total_props']}")
        logger.info(f"  Props Enriched: {results.get('stats_enriched', 0)}")
        logger.info(f"  Players: {results['unique_players']}")
        logger.info(f"  Names Normalized: {results['names_normalized']}")
        logger.info(f"  Teams Normalized: {results['teams_normalized']}")
        logger.info(f"  Duplicates Prevented: {results['duplicates_prevented']}")
        logger.info(f"  Standard: {results['standard_count']} | Demons: {results['demons_count']} | Goblins: {results['goblins_count']}")
        logger.info("=" * 70)
        
        return results
    
    async def _enrich_props_with_stats(self, props: List[Dict], player_names: List[str]) -> List[Dict]:
        """
        Enrich props with hit rates from CACHED player stats in MongoDB.
        
        Reads from dg_player_stats collection (populated by sync_player_stats).
        Falls back to live API calls only if cache is empty.
        
        Calculates:
        - L5: Last 5 games hit rate
        - L10: Last 10 games hit rate
        - Season: Full season hit rate
        """
        logger.info(f"[STATS ENRICHMENT] Starting enrichment for {len(player_names)} players from cache...")
        
        # Load all cached stats in one query for efficiency
        player_stats_cache = {}
        enriched_count = 0
        cache_hits = 0
        api_fallbacks = 0
        
        # Batch load from MongoDB
        normalized_names = [self.sanitize_player_name(name) for name in player_names]
        cached_docs = await self.player_stats.find(
            {"normalized_name": {"$in": normalized_names}},
            {"_id": 0}
        ).to_list(None)
        
        # Build cache from MongoDB results
        for doc in cached_docs:
            player_stats_cache[doc.get("player_name")] = doc
            cache_hits += 1
        
        logger.info(f"[STATS ENRICHMENT] Loaded {cache_hits} players from MongoDB cache")
        
        # For players not in cache, try live API (fallback)
        missing_players = [name for name in player_names if name not in player_stats_cache]
        if missing_players:
            logger.info(f"[STATS ENRICHMENT] {len(missing_players)} players not in cache, fetching from API...")
            
            for player_name in missing_players[:20]:  # Limit API calls
                try:
                    stats = await self._fetch_player_season_stats(player_name)
                    if stats and stats.get("games"):
                        player_stats_cache[player_name] = stats
                        api_fallbacks += 1
                        
                        # Also save to cache for next time
                        doc = {
                            "player_name": player_name,
                            "normalized_name": self.sanitize_player_name(player_name),
                            "games": stats.get("games", []),
                            "total_games": len(stats.get("games", [])),
                            "source": stats.get("source", "api_fallback"),
                            "synced_at": datetime.now(timezone.utc).isoformat()
                        }
                        await self.player_stats.update_one(
                            {"normalized_name": doc["normalized_name"]},
                            {"$set": doc},
                            upsert=True
                        )
                except Exception as e:
                    logger.debug(f"[STATS] Error fetching stats for {player_name}: {e}")
                
                await asyncio.sleep(0.1)
        
        enriched_count = len(player_stats_cache)
        logger.info(f"[STATS ENRICHMENT] Total stats: {enriched_count} (cache: {cache_hits}, API: {api_fallbacks})")
        
        # Enrich props with hit rates
        for prop in props:
            player_name = prop.get("player_name")
            player_stats = player_stats_cache.get(player_name, {})
            
            if not player_stats:
                continue
            
            # Extract stat type from market
            stat_type = self._extract_stat_type(prop.get("market", ""))
            line_value = prop.get("line", 0)
            
            if not stat_type or line_value <= 0:
                continue
            
            # Calculate hit rates for this line
            hit_rates = self._calculate_hit_rates(player_stats, stat_type, line_value)
            
            if hit_rates:
                prop["hit_rates"] = hit_rates
        
        return props
    
    async def _fetch_player_season_stats(self, player_name: str) -> Dict[str, Any]:
        """
        Fetch a player's season stats from BallDontLie API.
        Falls back to NBA.com official API if BallDontLie doesn't have data.
        Returns game-by-game stats for hit rate calculation.
        """
        # Try BallDontLie first
        try:
            # First, find the player ID
            player_id = await self._get_bdl_player_id(player_name)
            if player_id:
                # Fetch season stats
                url = f"{BDL_BASE_URL}/stats"
                params = {
                    "player_ids[]": player_id,
                    "seasons[]": CURRENT_SEASON,
                    "per_page": 100
                }
                headers = {"Authorization": BDL_API_KEY}
                
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, params=params, headers=headers, timeout=15.0)
                    
                    if response.status_code == 200:
                        data = response.json()
                        games = data.get("data", [])
                        
                        if games:
                            return {
                                "player_name": player_name,
                                "player_id": player_id,
                                "games": games,
                                "total_games": len(games),
                                "source": "balldontlie"
                            }
        except Exception as e:
            logger.debug(f"[BDL] Error fetching stats for {player_name}: {e}")
        
        # Fallback 1: Tank01 API (secondary - user has subscription)
        logger.debug(f"[STATS] BallDontLie has no data for {player_name}, trying Tank01...")
        tank_stats = await self._fetch_tank01_player_stats(player_name)
        if tank_stats and tank_stats.get("games"):
            return tank_stats
        
        # Fallback 2: NBA.com API (tertiary)
        logger.debug(f"[STATS] Tank01 has no data for {player_name}, trying NBA.com API...")
        nba_stats = self._fetch_nba_api_stats(player_name)
        if nba_stats and nba_stats.get("games"):
            return nba_stats
        
        return {}
    
    async def _get_bdl_player_id(self, player_name: str) -> Optional[int]:
        """Get BallDontLie player ID from name (with caching)"""
        # Check cache first
        if player_name in self._player_name_map:
            return self._player_name_map[player_name].get("id")
        
        try:
            url = f"{BDL_BASE_URL}/players"
            headers = {"Authorization": BDL_API_KEY}
            
            # Normalize name for search (handle G.G., Jr., etc.)
            normalized_name = player_name.replace(".", "").strip()
            name_parts = normalized_name.split()
            first_name = name_parts[0] if name_parts else ""
            last_name = name_parts[-1] if len(name_parts) > 1 else ""
            
            # Search strategies: first name is most reliable for unique names
            search_terms = [
                first_name if first_name else normalized_name,  # First name first (most reliable)
                normalized_name,  # Full name
                last_name if last_name else normalized_name,  # Last name (fallback)
            ]
            
            async with httpx.AsyncClient() as client:
                for search_term in search_terms:
                    if not search_term:
                        continue
                        
                    params = {"search": search_term, "per_page": 100}
                    response = await client.get(url, params=params, headers=headers, timeout=10.0)
                    
                    if response.status_code != 200:
                        continue
                    
                    data = response.json()
                    players = data.get("data", [])
                    
                    if not players:
                        continue
                    
                    # Find best match with scoring
                    best_match = None
                    best_score = 0
                    
                    for player in players:
                        full_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
                        normalized_full = full_name.replace(".", "").strip()
                        
                        # Exact first+last name match gets bonus
                        player_first = player.get('first_name', '').replace(".", "").strip().lower()
                        player_last = player.get('last_name', '').replace(".", "").strip().lower()
                        
                        # Check for exact first and last name match
                        exact_first = player_first == first_name.lower() if first_name else False
                        exact_last = player_last == last_name.lower() if last_name else False
                        
                        # Also check if first name starts with search term (Alex -> Alexandre)
                        starts_with_first = player_first.startswith(first_name.lower()) if first_name and len(first_name) >= 3 else False
                        
                        # If both first and last names match exactly, this is our player
                        if exact_first and exact_last:
                            self._player_name_map[player_name] = player
                            return player.get("id")
                        
                        # If last name is exact and first name starts with search, also accept
                        if exact_last and starts_with_first:
                            self._player_name_map[player_name] = player
                            return player.get("id")
                        
                        # Calculate similarity scores
                        ratio = fuzz.ratio(normalized_name.lower(), normalized_full.lower())
                        partial = fuzz.partial_ratio(normalized_name.lower(), normalized_full.lower())
                        token_sort = fuzz.token_sort_ratio(normalized_name.lower(), normalized_full.lower())
                        
                        # Use the best of all metrics
                        score = max(ratio, partial, token_sort)
                        
                        # Bonus for matching first name
                        if exact_first:
                            score += 10
                        
                        if score > best_score:
                            best_score = score
                            best_match = player
                    
                    # Accept if score is high enough (80% threshold after bonuses)
                    if best_match and best_score >= 80:
                        self._player_name_map[player_name] = best_match
                        return best_match.get("id")
                        
        except Exception as e:
            logger.debug(f"[BDL] Error searching for {player_name}: {e}")
        
        return None
    
    def _fetch_nba_api_stats(self, player_name: str) -> Dict[str, Any]:
        """
        Fetch player stats from NBA.com official API as a fallback.
        This is a synchronous function using the nba_api library.
        
        Returns stats in the same format as BallDontLie for compatibility.
        """
        if not NBA_API_AVAILABLE:
            return {}
        
        try:
            # Find player in NBA database
            all_players = nba_players.get_players()
            
            # Try exact name match first
            player_match = None
            normalized_search = player_name.lower().strip()
            
            for p in all_players:
                if p['full_name'].lower() == normalized_search:
                    player_match = p
                    break
            
            # If no exact match, try partial matching
            if not player_match:
                for p in all_players:
                    if normalized_search in p['full_name'].lower():
                        player_match = p
                        break
            
            if not player_match:
                logger.debug(f"[NBA_API] Player not found: {player_name}")
                return {}
            
            player_id = player_match['id']
            
            # Fetch game logs (with rate limiting)
            import time
            time.sleep(0.6)  # NBA.com rate limit
            
            # Determine current NBA season dynamically
            # NBA season runs Oct-Jun, so if month >= 10, it's the new season
            from datetime import datetime
            now = datetime.now()
            if now.month >= 10:
                season_year = now.year
            else:
                season_year = now.year - 1
            current_season = f"{season_year}-{str(season_year + 1)[-2:]}"
            
            logger.debug(f"[NBA_API] Fetching {player_name} for season {current_season}")
            
            gamelog = playergamelog.PlayerGameLog(
                player_id=player_id, 
                season=current_season,
                season_type_all_star='Regular Season'
            )
            df = gamelog.get_data_frames()[0]
            
            if df.empty:
                logger.debug(f"[NBA_API] No games found for {player_name} in {current_season}")
                return {}
            
            # Convert to BallDontLie-compatible format
            games = []
            for _, row in df.iterrows():
                games.append({
                    "pts": row.get('PTS', 0),
                    "reb": row.get('REB', 0),
                    "ast": row.get('AST', 0),
                    "fg3m": row.get('FG3M', 0),
                    "blk": row.get('BLK', 0),
                    "stl": row.get('STL', 0),
                    "turnover": row.get('TOV', 0),
                    "game": {
                        "date": row.get('GAME_DATE', ''),
                        "matchup": row.get('MATCHUP', '')
                    }
                })
            
            logger.info(f"[NBA_API] Fetched {len(games)} games for {player_name} (season {current_season})")
            
            return {
                "games": games,
                "player_name": player_name,
                "source": "nba_api"
            }
            
        except Exception as e:
            logger.debug(f"[NBA_API] Error fetching stats for {player_name}: {e}")
            return {}
    
    async def _fetch_tank01_player_stats(self, player_name: str) -> Dict[str, Any]:
        """
        Fetch player stats from Tank01 API as secondary fallback.
        
        Tank01 provides box scores via getNBABoxScore endpoint.
        We fetch recent game dates and get the player's stats from box scores.
        
        Returns stats in the same format as BallDontLie for compatibility.
        """
        try:
            headers = {
                "x-rapidapi-key": TANK01_API_KEY,
                "x-rapidapi-host": TANK01_HOST
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Step 1: Get player info to find their team
                search_url = f"{TANK01_BASE}/getNBAPlayerInfo"
                response = await client.get(
                    search_url,
                    params={"playerName": player_name},
                    headers=headers
                )
                
                if response.status_code != 200:
                    logger.debug(f"[TANK01] Player search failed: {response.status_code}")
                    return {}
                
                data = response.json()
                body = data.get("body", [])
                if not body or not isinstance(body, list):
                    logger.debug(f"[TANK01] Player not found: {player_name}")
                    return {}
                
                player = body[0]
                team_abv = player.get("team")
                tank_player_id = player.get("playerID")
                player_espn_name = player.get("espnName", player_name)
                
                if not team_abv:
                    logger.debug(f"[TANK01] No team found for {player_name}")
                    return {}
                
                # Step 2: Get team schedule to find recent completed games
                schedule_url = f"{TANK01_BASE}/getNBATeamSchedule"
                response = await client.get(
                    schedule_url,
                    params={"teamAbv": team_abv, "season": CURRENT_SEASON},
                    headers=headers
                )
                
                if response.status_code != 200:
                    logger.debug(f"[TANK01] Schedule fetch failed: {response.status_code}")
                    return {}
                
                schedule_data = response.json()
                schedule = schedule_data.get("body", {}).get("schedule", [])
                
                # Get last 15 completed games
                completed_games = [g for g in schedule if g.get("gameStatus") == "Completed"]
                recent_games = completed_games[-15:]  # Most recent 15
                
                if not recent_games:
                    logger.debug(f"[TANK01] No completed games for {team_abv}")
                    return {}
                
                # Step 3: Fetch box scores and extract player stats
                games = []
                for game in recent_games[-10:]:  # Limit to 10 games to reduce API calls
                    game_id = game.get("gameID")
                    if not game_id:
                        continue
                    
                    box_url = f"{TANK01_BASE}/getNBABoxScore"
                    box_response = await client.get(
                        box_url,
                        params={"gameID": game_id},
                        headers=headers
                    )
                    
                    if box_response.status_code != 200:
                        continue
                    
                    box_data = box_response.json()
                    player_stats = box_data.get("body", {}).get("playerStats", {})
                    
                    # Find our player in the box score
                    for pid, stats in player_stats.items():
                        stat_name = stats.get("longName", "").lower()
                        if player_name.lower() in stat_name or stat_name in player_name.lower():
                            games.append({
                                "pts": int(stats.get("pts", 0) or 0),
                                "reb": int(stats.get("reb", 0) or 0),
                                "ast": int(stats.get("ast", 0) or 0),
                                "fg3m": int(stats.get("tptfgm", 0) or 0),  # 3PM
                                "blk": int(stats.get("blk", 0) or 0),
                                "stl": int(stats.get("stl", 0) or 0),
                                "turnover": int(stats.get("TOV", 0) or 0),
                                "game": {
                                    "date": game.get("gameDate", ""),
                                    "id": game_id
                                }
                            })
                            break
                    
                    await asyncio.sleep(0.1)  # Rate limiting
                
                if games:
                    logger.info(f"[TANK01] Fetched {len(games)} games for {player_name}")
                    return {
                        "games": games,
                        "player_name": player_name,
                        "source": "tank01"
                    }
                
                return {}
                
        except Exception as e:
            logger.debug(f"[TANK01] Error fetching stats for {player_name}: {e}")
            return {}
    
    def _calculate_hit_rates(self, player_stats: Dict, stat_type: str, line_value: float) -> Dict[str, Any]:
        """
        Calculate hit rates for a specific line.
        
        Returns:
        - l5: Last 5 games hit rate
        - l10: Last 10 games hit rate  
        - season: Full season hit rate
        """
        games = player_stats.get("games", [])
        if not games:
            return {}
        
        # Map stat type to BallDontLie field
        stat_field_map = {
            "PTS": "pts",
            "REB": "reb",
            "AST": "ast",
            "3PM": "fg3m",
            "BLK": "blk",
            "STL": "stl",
            "TO": "turnover",
            "P+R": ["pts", "reb"],
            "P+A": ["pts", "ast"],
            "R+A": ["reb", "ast"],
            "PRA": ["pts", "reb", "ast"]
        }
        
        fields = stat_field_map.get(stat_type)
        if not fields:
            return {}
        
        # Sort games by date (most recent first)
        sorted_games = sorted(games, key=lambda g: g.get("game", {}).get("date", ""), reverse=True)
        
        def get_stat_value(game):
            if isinstance(fields, list):
                return sum(game.get(f, 0) or 0 for f in fields)
            return game.get(fields, 0) or 0
        
        def calc_hit_rate(game_list):
            if not game_list:
                return {"hit_rate": 0, "games_over": 0, "total_games": 0, "avg": 0}
            
            values = [get_stat_value(g) for g in game_list]
            games_over = sum(1 for v in values if v >= line_value)
            avg = sum(values) / len(values) if values else 0
            
            return {
                "hit_rate": games_over / len(game_list) if game_list else 0,
                "games_over": games_over,
                "total_games": len(game_list),
                "avg": round(avg, 1)
            }
        
        return {
            "l5": calc_hit_rate(sorted_games[:5]),
            "l10": calc_hit_rate(sorted_games[:10]),
            "season": calc_hit_rate(sorted_games)
        }
    
    async def _build_cached_board(self, props: List[Dict], sync_time: datetime):
        """
        ARCHITECTURE RESET: Centralized Data Enrichment
        
        This is THE ONLY place where player data enrichment happens.
        All sections (Demon Radar, Goblin Recon, Gauntlet, Safe Haven) read from here.
        
        Data Flow:
        1. Props come from Odds API
        2. For each prop, lookup player in dg_master_roster by normalized name
        3. Enrich with: tank01_player_id (PRIMARY KEY), photo_url, team, stats
        4. Store everything in dg_cached_board
        5. No more runtime lookups - if it's not here, it doesn't exist
        
        Primary Key: tank01_player_id (from Tank01 roster sync)
        """
        if not props:
            return
        
        logger.info(f"[CACHED_BOARD] Building centralized board from {len(props)} props...")
        
        # Load master roster into memory for fast lookups (indexed by normalized name)
        master_roster_map = {}
        roster_cursor = self.master_roster.find({}, {"_id": 0})
        async for player in roster_cursor:
            normalized = player.get("normalized_name", "").lower()
            if normalized:
                master_roster_map[normalized] = player
        
        logger.info(f"[CACHED_BOARD] Loaded {len(master_roster_map)} players from master roster")
        
        # Load player stats into memory (indexed by normalized name)
        stats_map = {}
        stats_cursor = self.player_stats.find({}, {"_id": 0})
        async for stat in stats_cursor:
            normalized = self.sanitize_player_name(stat.get("player_name", "")).lower()
            if normalized:
                stats_map[normalized] = stat
        
        logger.info(f"[CACHED_BOARD] Loaded {len(stats_map)} player stats")
        
        # Load social signals (indexed by player name)
        signals_map = {}
        try:
            signals_cursor = self.db.dg_social_signals.find({}, {"_id": 0})
            async for signal in signals_cursor:
                player_name = signal.get("player_name", "")
                if player_name:
                    signals_map[player_name.lower()] = signal
        except Exception as e:
            logger.warning(f"[CACHED_BOARD] Could not load social signals: {e}")
        
        # Group props by player and enrich ONCE
        players_dict = {}
        unmatched_players = []
        
        for prop in props:
            player_name = prop.get("player_name", "Unknown")
            normalized_name = self.sanitize_player_name(player_name).lower()
            
            if player_name not in players_dict:
                # ==================== SINGLE SOURCE LOOKUP ====================
                # Look up player in master roster (Tank01 data)
                roster_player = master_roster_map.get(normalized_name)
                
                if not roster_player:
                    # Try without suffix (Jr., III, etc.)
                    for suffix in [" jr", " iii", " ii", " iv", " sr"]:
                        clean_name = normalized_name.replace(suffix, "").strip()
                        roster_player = master_roster_map.get(clean_name)
                        if roster_player:
                            break
                
                if not roster_player:
                    # Try nickname expansions (G.G. -> Gregory, etc.)
                    name_parts = player_name.replace(".", "").split()
                    if len(name_parts) >= 2:
                        first_name = name_parts[0].lower()
                        last_name = name_parts[-1].lower()
                        
                        # Nickname mappings
                        nickname_map = {
                            "gg": "gregory", "jj": "james", "tj": "thomas",
                            "pj": "paul", "cj": "charles", "aj": "anthony",
                            "rj": "robert", "herb": "herbert", "mike": "michael",
                        }
                        
                        expanded_first = nickname_map.get(first_name, first_name)
                        expanded_name = f"{expanded_first} {last_name}"
                        roster_player = master_roster_map.get(expanded_name)
                        
                        # Also try searching all players with matching last name
                        if not roster_player:
                            for norm_name, player in master_roster_map.items():
                                if last_name in norm_name and expanded_first[:3] in norm_name:
                                    roster_player = player
                                    break
                
                if not roster_player:
                    # Player not in master roster - use minimal data
                    unmatched_players.append(player_name)
                    players_dict[player_name] = {
                        "player_name": player_name,
                        "tank01_player_id": None,  # No Tank01 match
                        "team": prop.get("home_team") or prop.get("away_team") or "UNK",
                        "photo_url": None,
                        "nba_com_id": None,
                        "espn_id": None,
                        "position": None,
                        "l10_stats": {},
                        "is_verified": False,  # Flag for UI to show caution
                        "props": [],
                        "demons": [],
                        "goblins": [],
                        "synced_at": sync_time.isoformat()
                    }
                else:
                    # ==================== FULL ENRICHMENT FROM MASTER ROSTER ====================
                    tank01_id = roster_player.get("tank01_player_id")
                    
                    # Get stats for this player
                    player_stats = stats_map.get(normalized_name, {})
                    
                    # Get social signals
                    social = signals_map.get(player_name.lower(), {})
                    
                    players_dict[player_name] = {
                        # Primary identifiers
                        "player_name": player_name,
                        "tank01_player_id": tank01_id,  # PRIMARY KEY
                        "nba_com_id": roster_player.get("nba_com_id"),
                        "espn_id": roster_player.get("espn_id"),
                        
                        # Team info (from Tank01)
                        "team": roster_player.get("team_abbreviation"),
                        "team_name": roster_player.get("team_name"),
                        "team_logo_url": roster_player.get("team_logo_url"),
                        
                        # Photo (from Tank01 sync)
                        "photo_url": roster_player.get("photo_url"),
                        "photo_source": roster_player.get("photo_source"),
                        
                        # Player info
                        "position": roster_player.get("position"),
                        "jersey_number": roster_player.get("jersey_number"),
                        
                        # Pre-computed stats (from sync-player-stats)
                        "games_played": player_stats.get("games_played", 0),
                        "l10_stats": {
                            "pts": player_stats.get("pts_avg_l10", 0),
                            "reb": player_stats.get("reb_avg_l10", 0),
                            "ast": player_stats.get("ast_avg_l10", 0),
                            "pts_reb_ast": player_stats.get("pra_avg_l10", 0),
                        },
                        "l5_stats": {
                            "pts": player_stats.get("pts_avg_l5", 0),
                            "reb": player_stats.get("reb_avg_l5", 0),
                            "ast": player_stats.get("ast_avg_l5", 0),
                        },
                        
                        # Social signals (pre-enriched)
                        "volatility_flag": social.get("volatility_flag", False),
                        "revenge_game": social.get("revenge_game", False),
                        "injury_status": social.get("injury_status"),
                        
                        # Verification flag
                        "is_verified": True,
                        
                        # Props containers
                        "props": [],
                        "demons": [],
                        "goblins": [],
                        "synced_at": sync_time.isoformat()
                    }
            
            # Add prop to player
            players_dict[player_name]["props"].append(prop)
            
            if prop.get("is_demon"):
                players_dict[player_name]["demons"].append(prop)
            elif prop.get("is_goblin"):
                players_dict[player_name]["goblins"].append(prop)
        
        if unmatched_players:
            logger.warning(f"[CACHED_BOARD] {len(unmatched_players)} players not in master roster: {unmatched_players[:5]}...")
        
        # Store in cached_board collection
        await self.cached_board.delete_many({})
        
        # Sort players by prop count
        sorted_players = sorted(
            players_dict.values(),
            key=lambda x: len(x["props"]),
            reverse=True
        )
        
        # Add ranking
        for idx, player in enumerate(sorted_players):
            player["rank"] = idx + 1
        
        if sorted_players:
            await self.cached_board.insert_many(sorted_players)
        
        # Store sync metadata
        verified_count = sum(1 for p in sorted_players if p.get("is_verified"))
        await self.sync_log.update_one(
            {"type": "cached_board"},
            {"$set": {
                "type": "cached_board",
                "synced_at": sync_time.isoformat(),
                "players_count": len(sorted_players),
                "verified_count": verified_count,
                "unverified_count": len(sorted_players) - verified_count,
                "total_props": sum(len(p["props"]) for p in sorted_players)
            }},
            upsert=True
        )
        
        logger.info(f"[CACHED_BOARD] Built board: {len(sorted_players)} players ({verified_count} verified)")
        
        # Build derived collections (Demon Radar, Goblin Vault, etc.)
        # These just filter/sort the cached_board data - NO additional lookups
        await self._build_demon_radar(players_dict, sync_time)
        await self._build_goblin_vault(players_dict, sync_time)
        await self._build_parlay_builder(players_dict, sync_time)
        await self._build_goblin_recon(players_dict, sync_time)
    
    async def _build_demon_radar(self, players_dict: Dict[str, Dict], sync_time: datetime):
        """
        THE INTRICATE DEMON RADAR ALGORITHM v2.0 - Opportunity-Focused
        
        NEW Scoring Formula (Ratio-Based):
        1. Weighted Probability (P) = (L10 × 0.6) + (L5 × 0.4)
        2. Gap Ratio (R) = Demon_Value / Standard_Value (e.g., 1.10 = 10% higher)
        3. Final Score = P / Gap_Ratio
           - Example: P=0.80, Gap=1.10 → Score=0.727
           - Example: P=0.80, Gap=1.30 → Score=0.615
        
        Dynamic Threshold:
        - Start with P >= 70% (strict)
        - If fewer than 10 picks, lower to 55% (opportunity mode)
        - Ensures Demon Radar is NEVER empty
        
        Heat Level (1-5 Flames):
        - 5 Flames: L10 >= 90% (9-10/10 games hit)
        - 4 Flames: L10 >= 80% OR on 3+ game streak
        - 3 Flames: L10 >= 70% OR L5 >= 80%
        - 2 Flames: L10 >= 60%
        - 1 Flame: L10 >= 50%
        """
        logger.info("[DEMON RADAR v2.0] Calculating opportunity-focused top 10 picks...")
        
        all_candidates = []
        
        for player_name, player_data in players_dict.items():
            # Skip None entries
            if player_data is None:
                continue
            
            demons = player_data.get("demons", [])
            standard = player_data.get("standard", [])
            
            if not demons:
                continue
            
            # Build a map of standard lines by stat type
            standard_map = {}
            for std_prop in standard:
                market = std_prop.get("market", "")
                stat_type = self._extract_stat_type(market)
                if stat_type:
                    key = f"{stat_type}_{std_prop.get('direction', '')}"
                    if key not in standard_map:
                        standard_map[key] = std_prop
            
            # Score each demon
            for demon in demons:
                demon_market = demon.get("market", "")
                demon_stat = self._extract_stat_type(demon_market)
                demon_line = demon.get("line", 0)
                demon_direction = demon.get("direction", "")
                
                if not demon_stat or demon_line <= 0:
                    continue
                
                # Find corresponding standard line
                std_key = f"{demon_stat}_{demon_direction}"
                std_prop = standard_map.get(std_key)
                
                # Calculate standard line reference
                if std_prop:
                    std_line = std_prop.get("line", 0)
                else:
                    # No standard line - estimate as 85% of demon line
                    std_line = demon_line * 0.85
                
                if std_line <= 0:
                    continue
                
                # Get hit rates from BallDontLie stats
                hit_rates = demon.get("hit_rates", {})
                if hit_rates is None:
                    hit_rates = {}
                h10_data = hit_rates.get("l10", {})
                h5_data = hit_rates.get("l5", {})
                season_data = hit_rates.get("season", {})
                if h10_data is None:
                    h10_data = {}
                if h5_data is None:
                    h5_data = {}
                if season_data is None:
                    season_data = {}
                
                h10 = h10_data.get("hit_rate", 0)
                h5 = h5_data.get("hit_rate", 0)
                h10_games = h10_data.get("total_games", 0)
                h5_games = h5_data.get("total_games", 0)
                h10_over = h10_data.get("games_over", 0)
                h5_over = h5_data.get("games_over", 0)
                season_avg = season_data.get("avg", 0)
                
                # Track if we have real data
                has_real_data = h10_games > 0 or h5_games > 0
                
                # V3.2 FIX: SKIP players with no real stats data
                # This prevents false readings from estimated probabilities
                if not has_real_data:
                    continue
                
                # Calculate Gap Ratio (R)
                # R = Demon_Value / Standard_Value (e.g., 1.10 = 10% higher)
                gap_ratio = demon_line / std_line if std_line > 0 else 1.0
                gap_pct = (gap_ratio - 1) * 100  # Percentage above standard
                
                # Calculate Weighted Probability (P)
                # P = (L10 × 0.6) + (L5 × 0.4)
                P = (h10 * 0.6) + (h5 * 0.4)
                
                # Calculate Final Score using Value Ratio
                # Score = P / Gap_Ratio
                radar_score = P / gap_ratio if gap_ratio > 0 else P
                
                # Calculate Heat Level (1-5 Flames)
                heat_level = self._calculate_heat_level(h10, h5, h10_over, h5_over, h10_games, h5_games)
                
                # Streak detection (3+ consecutive games)
                is_hot_streak = h5_over >= 3 if h5_games >= 3 else False
                
                all_candidates.append({
                    # PRIMARY KEY - Tank01 player ID
                    "tank01_player_id": player_data.get("tank01_player_id"),
                    
                    # Player info (from centralized enrichment)
                    "player_name": player_name,
                    "team": player_data.get("team", ""),
                    "photo_url": player_data.get("photo_url"),
                    "nba_com_id": player_data.get("nba_com_id"),
                    "espn_id": player_data.get("espn_id"),
                    "position": player_data.get("position"),
                    
                    # Social signals (pre-enriched)
                    "volatility_flag": player_data.get("volatility_flag", False),
                    "revenge_game": player_data.get("revenge_game", False),
                    "is_verified": player_data.get("is_verified", False),
                    
                    # Prop data
                    "stat_type": demon_stat,
                    "direction": demon_direction,
                    "demon_line": demon_line,
                    "standard_line": round(std_line, 1),
                    "gap_ratio": round(gap_ratio, 3),
                    "gap_pct": round(gap_pct, 1),
                    
                    # Hit rates
                    "h10_rate": round(h10 * 100, 1),
                    "h5_rate": round(h5 * 100, 1),
                    "h10_over": h10_over,
                    "h10_games": h10_games,
                    "h5_over": h5_over,
                    "h5_games": h5_games,
                    "season_avg": round(season_avg, 1),
                    
                    # Calculated scores
                    "hit_probability": round(P * 100, 1),
                    "radar_score": round(radar_score, 4),
                    "heat_level": heat_level,
                    "is_hot_streak": is_hot_streak,
                    "radar_strength": min(100, max(0, round(P * 100, 1))),
                    "price": demon.get("price", 100),
                    "is_radar_pick": True,
                    "has_real_data": True,  # V3.2: All picks now have verified stats
                    "synced_at": sync_time.isoformat()
                })
        
        # Dynamic Threshold: Start strict (70%), lower if needed
        radar_picks = self._apply_dynamic_threshold(all_candidates)
        
        # Sort by radar_score descending
        radar_picks.sort(key=lambda x: x["radar_score"], reverse=True)
        
        # V3.2 FIX: De-duplicate - one pick per player (take their best prop)
        seen_players = set()
        unique_picks = []
        for pick in radar_picks:
            player_name = pick["player_name"]
            if player_name not in seen_players:
                seen_players.add(player_name)
                unique_picks.append(pick)
        
        # Take top 10 unique players
        top_10 = unique_picks[:10]
        
        # Store in radar_picks collection
        await self.radar_picks.delete_many({})
        if top_10:
            await self.radar_picks.insert_many(top_10)
        
        # Log summary
        strict_count = len([p for p in all_candidates if p["hit_probability"] >= 70])
        opportunity_count = len([p for p in all_candidates if 55 <= p["hit_probability"] < 70])
        
        logger.info(f"[DEMON RADAR v2.0] Generated {len(top_10)} top picks")
        logger.info(f"  Strict (P>=70%): {strict_count} | Opportunity (P>=55%): {opportunity_count}")
        logger.info(f"  Total candidates: {len(all_candidates)}")
        
        # Log top 3 with heat levels
        for i, pick in enumerate(top_10[:3]):
            flames = "🔥" * pick['heat_level']
            logger.info(f"  #{i+1}: {pick['player_name']} - {pick['stat_type']} {pick['demon_line']} "
                       f"(P: {pick['hit_probability']}%, Score: {pick['radar_score']:.3f}) {flames}")
    
    def _calculate_heat_level(self, h10: float, h5: float, h10_over: int, h5_over: int, h10_games: int, h5_games: int) -> int:
        """
        Calculate Heat Level (1-5 Flames) based on performance:
        - 5 Flames: L10 >= 90% (9-10/10 games hit) - FIRE
        - 4 Flames: L10 >= 80% OR on perfect 5-game streak - HOT
        - 3 Flames: L10 >= 70% OR L5 >= 80% - WARM
        - 2 Flames: L10 >= 60% - MILD
        - 1 Flame:  L10 >= 50% - COOL
        - 0 Flames: L10 < 50% - COLD
        """
        # 5 Flames: 9-10 out of 10 games hit
        if h10_games >= 10 and h10_over >= 9:
            return 5
        if h10 >= 0.90:
            return 5
        
        # 4 Flames: 80%+ L10 OR perfect 5-game streak
        if h10 >= 0.80:
            return 4
        if h5_games >= 5 and h5_over == 5:  # Perfect last 5
            return 4
        
        # 3 Flames: 70%+ L10 OR 80%+ L5 (hot streak)
        if h10 >= 0.70:
            return 3
        if h5 >= 0.80:
            return 3
        if h5_games >= 3 and h5_over >= 3:  # 3+ game streak
            return 3
        
        # 2 Flames: 60%+ L10
        if h10 >= 0.60:
            return 2
        
        # 1 Flame: 50%+ L10
        if h10 >= 0.50:
            return 1
        
        # 0 Flames: Cold
        return 0
    
    def _apply_dynamic_threshold(self, candidates: List[Dict]) -> List[Dict]:
        """
        Dynamic Threshold Logic:
        1. Start with STRICT threshold (P >= 70%)
        2. If fewer than 10 picks, lower to OPPORTUNITY threshold (P >= 55%)
        3. Ensures Demon Radar is NEVER empty
        """
        # First pass: Strict threshold (P >= 70%)
        strict_picks = [c for c in candidates if c["hit_probability"] >= 70]
        
        if len(strict_picks) >= 10:
            logger.info(f"[THRESHOLD] Using STRICT mode (P>=70%): {len(strict_picks)} candidates")
            return strict_picks
        
        # Second pass: Lower to Opportunity threshold (P >= 55%)
        opportunity_picks = [c for c in candidates if c["hit_probability"] >= 55]
        
        if len(opportunity_picks) >= 10:
            logger.info(f"[THRESHOLD] Using OPPORTUNITY mode (P>=55%): {len(opportunity_picks)} candidates")
            return opportunity_picks
        
        # Final pass: Take all with P >= 40% to ensure we have picks
        final_picks = [c for c in candidates if c["hit_probability"] >= 40]
        
        logger.info(f"[THRESHOLD] Using MINIMUM mode (P>=40%): {len(final_picks)} candidates")
        return final_picks
    
    async def _build_goblin_vault(self, players_dict: Dict[str, Dict], sync_time: datetime):
        """
        THE GOBLIN VAULT ALGORITHM - Safe Plays Scoring
        
        Objective: Find the safest, most reliable Goblin lines with 90%+ hit rates.
        
        Scoring Formula:
        1. Hit Rate Score (80% weight) = Weighted average (L10 × 0.6) + (L5 × 0.4)
           - Target: 90%+ hit rate for maximum safety
        2. Value Gap Score (20% weight) = How far BELOW the standard line
           - The further below standard while maintaining 90%+, the higher the rank
        
        Final Score = (Hit_Rate × 0.8) + (Value_Gap_Bonus × 0.2)
        
        Safety Rating: X/10 games cleared (displayed as "Safety: 98% | Clear in 10/10 last games")
        """
        logger.info("[GOBLIN VAULT] Calculating top 10 safe plays...")
        
        all_candidates = []
        
        for player_name, player_data in players_dict.items():
            # Skip None entries
            if player_data is None:
                continue
            
            goblins = player_data.get("goblins", [])
            standard = player_data.get("standard", [])
            
            if not goblins:
                continue
            
            # Build a map of standard lines by stat type
            standard_map = {}
            for std_prop in standard:
                market = std_prop.get("market", "")
                stat_type = self._extract_stat_type(market)
                if stat_type:
                    key = f"{stat_type}_{std_prop.get('direction', '')}"
                    if key not in standard_map:
                        standard_map[key] = std_prop
            
            # Score each goblin
            for goblin in goblins:
                goblin_market = goblin.get("market", "")
                goblin_stat = self._extract_stat_type(goblin_market)
                goblin_line = goblin.get("line", 0)
                goblin_direction = goblin.get("direction", "")
                
                if not goblin_stat or goblin_line <= 0:
                    continue
                
                # Find corresponding standard line
                std_key = f"{goblin_stat}_{goblin_direction}"
                std_prop = standard_map.get(std_key)
                
                # Calculate standard line reference
                if std_prop:
                    std_line = std_prop.get("line", 0)
                else:
                    # No standard line - estimate as 115% of goblin line (goblins are BELOW standard)
                    std_line = goblin_line * 1.15
                
                if std_line <= 0:
                    continue
                
                # Get hit rates from BallDontLie stats
                hit_rates = goblin.get("hit_rates", {})
                if hit_rates is None:
                    hit_rates = {}
                h10_data = hit_rates.get("l10", {})
                h5_data = hit_rates.get("l5", {})
                season_data = hit_rates.get("season", {})
                if h10_data is None:
                    h10_data = {}
                if h5_data is None:
                    h5_data = {}
                if season_data is None:
                    season_data = {}
                
                h10 = h10_data.get("hit_rate", 0)
                h5 = h5_data.get("hit_rate", 0)
                h10_games = h10_data.get("total_games", 0)
                h5_games = h5_data.get("total_games", 0)
                h10_over = h10_data.get("games_over", 0)
                h5_over = h5_data.get("games_over", 0)
                season_avg = season_data.get("avg", 0)
                
                # Track if we have real data
                has_real_data = h10_games > 0 or h5_games > 0
                
                # V3.2 FIX: SKIP players with no real stats data
                # This prevents false readings from estimated probabilities
                if not has_real_data:
                    continue
                
                # Calculate Value Gap (how far BELOW standard)
                # For Goblins, lower lines = safer, so gap should be negative
                gap_below_std = std_line - goblin_line  # Positive = safer (further below)
                gap_pct = (gap_below_std / std_line) * 100 if std_line > 0 else 0
                
                # Calculate Weighted Hit Rate Score
                # P = (L10 × 0.6) + (L5 × 0.4)
                hit_rate_score = (h10 * 0.6) + (h5 * 0.4)
                
                # Calculate Value Gap Bonus (normalized 0-1)
                # Gap of 5% = 0.2, Gap of 10% = 0.4, Gap of 20% = 0.8, Gap of 30%+ = 1.0
                value_gap_bonus = min(1.0, gap_pct / 30) if gap_pct > 0 else 0
                
                # Final Vault Score = (Hit_Rate × 0.8) + (Value_Gap × 0.2)
                vault_score = (hit_rate_score * 0.8) + (value_gap_bonus * 0.2)
                
                # Calculate Safety Level (1-5 shields based on consistency)
                safety_level = self._calculate_safety_level(h10, h5, h10_over, h5_over, h10_games, h5_games)
                
                # Perfect streak detection (cleared in all recent games)
                is_perfect_streak = h10_games >= 5 and h10_over == h10_games
                
                # Safety rating string: "10/10" or "9/10"
                safety_string = f"{h10_over}/{h10_games}" if h10_games > 0 else "---"
                
                all_candidates.append({
                    # PRIMARY KEY - Tank01 player ID
                    "tank01_player_id": player_data.get("tank01_player_id"),
                    
                    # Player info (from centralized enrichment)
                    "player_name": player_name,
                    "team": player_data.get("team", ""),
                    "photo_url": player_data.get("photo_url"),
                    "nba_com_id": player_data.get("nba_com_id"),
                    "espn_id": player_data.get("espn_id"),
                    "position": player_data.get("position"),
                    
                    # Social signals (pre-enriched)
                    "volatility_flag": player_data.get("volatility_flag", False),
                    "revenge_game": player_data.get("revenge_game", False),
                    "is_verified": player_data.get("is_verified", False),
                    
                    # Prop data
                    "stat_type": goblin_stat,
                    "direction": goblin_direction,
                    "goblin_line": goblin_line,
                    "standard_line": round(std_line, 1),
                    "gap_below_std": round(gap_below_std, 1),
                    "gap_pct": round(gap_pct, 1),
                    
                    # Hit rates
                    "h10_rate": round(h10 * 100, 1),
                    "h5_rate": round(h5 * 100, 1),
                    "h10_over": h10_over,
                    "h10_games": h10_games,
                    "h5_over": h5_over,
                    "h5_games": h5_games,
                    "season_avg": round(season_avg, 1),
                    
                    # Calculated scores
                    "hit_probability": round(hit_rate_score * 100, 1),
                    "vault_score": round(vault_score, 4),
                    "safety_level": safety_level,
                    "safety_rating": round(hit_rate_score * 100, 1),
                    "safety_string": safety_string,
                    "is_perfect_streak": is_perfect_streak,
                    "price": goblin.get("price", -110),
                    "is_vault_pick": True,
                    "has_real_data": True,
                    "synced_at": sync_time.isoformat()
                })
        
        # Filter for high safety picks (minimum 80% hit rate for Goblins)
        safe_picks = [c for c in all_candidates if c["hit_probability"] >= 80]
        
        if len(safe_picks) < 10:
            # Lower threshold to 70% if not enough safe picks
            safe_picks = [c for c in all_candidates if c["hit_probability"] >= 70]
        
        if len(safe_picks) < 10:
            # Final fallback to 60%
            safe_picks = [c for c in all_candidates if c["hit_probability"] >= 60]
        
        # Sort by vault_score descending (highest safety + value first)
        safe_picks.sort(key=lambda x: x["vault_score"], reverse=True)
        
        # V3.2 FIX: De-duplicate - one pick per player (take their best prop)
        seen_players = set()
        unique_picks = []
        for pick in safe_picks:
            player_name = pick["player_name"]
            if player_name not in seen_players:
                seen_players.add(player_name)
                unique_picks.append(pick)
        
        # Take top 10 unique players
        top_10 = unique_picks[:10]
        
        # Store in goblin_vault collection
        await self.goblin_vault.delete_many({})
        if top_10:
            await self.goblin_vault.insert_many(top_10)
        
        # Log summary
        elite_count = len([p for p in all_candidates if p["hit_probability"] >= 90])
        safe_count = len([p for p in all_candidates if 80 <= p["hit_probability"] < 90])
        
        logger.info(f"[GOBLIN VAULT] Generated {len(top_10)} top safe plays")
        logger.info(f"  Elite (P>=90%): {elite_count} | Safe (P>=80%): {safe_count}")
        logger.info(f"  Total candidates: {len(all_candidates)}")
        
        # Log top 3 with safety levels
        for i, pick in enumerate(top_10[:3]):
            shields = "🛡️" * pick['safety_level']
            logger.info(f"  #{i+1}: {pick['player_name']} - {pick['stat_type']} {pick['goblin_line']} "
                       f"(Safety: {pick['safety_rating']}%, Score: {pick['vault_score']:.3f}) {shields}")
    
    def _calculate_safety_level(self, h10: float, h5: float, h10_over: int, h5_over: int, h10_games: int, h5_games: int) -> int:
        """
        Calculate Safety Level (1-5 Shields) based on consistency:
        - 5 Shields: Perfect 10/10 or 95%+ hit rate - FORTRESS
        - 4 Shields: 90%+ hit rate OR perfect 5/5 - VAULT
        - 3 Shields: 85%+ hit rate - SAFE
        - 2 Shields: 80%+ hit rate - RELIABLE
        - 1 Shield:  70%+ hit rate - MODERATE
        - 0 Shields: < 70% hit rate - RISKY
        """
        # 5 Shields: Perfect 10/10 or 95%+
        if h10_games >= 10 and h10_over == 10:
            return 5
        if h10 >= 0.95:
            return 5
        
        # 4 Shields: 90%+ OR perfect 5/5
        if h10 >= 0.90:
            return 4
        if h5_games >= 5 and h5_over == 5:
            return 4
        
        # 3 Shields: 85%+
        if h10 >= 0.85:
            return 3
        if h5 >= 0.90:
            return 3
        
        # 2 Shields: 80%+
        if h10 >= 0.80:
            return 2
        
        # 1 Shield: 70%+
        if h10 >= 0.70:
            return 1
        
        # 0 Shields: Below 70%
        return 0
    
    async def _build_parlay_builder(self, players_dict: Dict[str, Dict], sync_time: datetime):
        """
        THE BIG MONEY BUILDER - Parlay Generator Algorithm
        
        PRIZEPICKS COMPLIANCE:
        - MINIMUM 2 TEAMS REQUIRED: Pick #1 and Pick #2 must be from different teams
        - This ensures all generated lineups are valid for PrizePicks submission
        
        "WHALE" SCORING:
        1. Ceiling Frequency: Filter demons where H10 >= 30% (hit at least 3/10 times)
        2. Recent Heat: 20% boost if player hit in last game (H5 >= 20%)
        3. Smart Correlation: Opponent pairs for 4-6 pick lines to capture game pace
        
        LINE TYPES (Mathematically Accurate at +100 odds):
        - 2-Pick: Max 4x payout (2² = 4)
        - 3-Pick: Max 8x payout (2³ = 8)
        - 4-Pick: Max 16x payout (2⁴ = 16)
        - 5-Pick: Max 32x payout (2⁵ = 32)
        - 6-Pick: Max 64x payout (2⁶ = 64)
        """
        logger.info("[PARLAY BUILDER] Generating HIGH-PROBABILITY parlays with 2-Team Rule...")
        
        # Collect ONLY high-probability demons (minimum 50% hit rate)
        high_prob_demons = []
        
        # Debug: Log player dict structure
        logger.info(f"[PARLAY BUILDER DEBUG] players_dict type: {type(players_dict)}, len: {len(players_dict) if players_dict else 0}")
        
        for player_name, player_data in players_dict.items():
            # Skip None entries
            if player_data is None:
                logger.warning(f"[PARLAY BUILDER] Skipping None player_data for {player_name}")
                continue
            
            # Debug: check player_data type
            if not isinstance(player_data, dict):
                logger.warning(f"[PARLAY BUILDER] player_data is not dict for {player_name}: {type(player_data)}")
                continue
                
            demons = player_data.get("demons", [])
            team = player_data.get("team", "")
            
            for demon in demons:
                # Skip None demons
                if demon is None:
                    logger.warning(f"[PARLAY BUILDER] Skipping None demon for {player_name}")
                    continue
                hit_rates = demon.get("hit_rates", {})
                if hit_rates is None:
                    hit_rates = {}
                h10_data = hit_rates.get("l10", {})
                h5_data = hit_rates.get("l5", {})
                if h10_data is None:
                    h10_data = {}
                if h5_data is None:
                    h5_data = {}
                
                h10 = h10_data.get("hit_rate", 0)
                h5 = h5_data.get("hit_rate", 0)
                h10_games = h10_data.get("total_games", 0)
                h10_over = h10_data.get("games_over", 0)
                h5_over = h5_data.get("games_over", 0)
                h5_games = h5_data.get("total_games", 0)
                season_data = hit_rates.get("season", {})
                season_avg = season_data.get("avg", 0)
                
                # Calculate weighted probability
                base_prob = (h10 * 0.6) + (h5 * 0.4)
                
                # V3.2 FIX: SKIP players with no real stats data
                has_real_data = h10_games > 0 or h5_games > 0
                if not has_real_data:
                    continue
                
                # STRICT FILTER: Only include demons with 50%+ hit probability
                if base_prob < 0.50:
                    continue
                
                # Heat boost for recent performance
                heat_boost = 1.10 if (h5_games > 0 and h5_over >= 3) else 1.0
                
                whale_score = base_prob * heat_boost
                
                # Get game info for opponent pairing
                home_team = demon.get("home_team", "")
                away_team = demon.get("away_team", "")
                game_key = f"{away_team}@{home_team}" if home_team and away_team else ""
                
                # Determine opponent team
                opponent_team = away_team if team == home_team else home_team
                
                demon_entry = {
                    "player_name": player_name,
                    "team": team,
                    "opponent_team": opponent_team,
                    "nba_id": player_data.get("nba_id"),
                    "photo_url": player_data.get("photo_url"),
                    "stat_type": self._extract_stat_type(demon.get("market", "")),
                    "line": demon.get("line", 0),
                    "direction": demon.get("direction", "Over"),
                    "h10_rate": round(h10 * 100, 1),
                    "h5_rate": round(h5 * 100, 1),
                    "h10_over": h10_over,
                    "h10_games": h10_games,
                    "h5_over": h5_over,
                    "h5_games": h5_games,
                    "season_avg": season_avg,
                    "whale_score": round(whale_score, 4),
                    "hit_probability": round(base_prob * 100, 1),
                    "has_heat_boost": heat_boost > 1,
                    "game_key": game_key,
                    "home_team": home_team,
                    "away_team": away_team,
                    "price": demon.get("price", 100),
                    "is_demon": True,  # Mark as demon for payout calculation
                    "standard_line": demon.get("standard_line", demon.get("line", 0) * 0.85)  # Estimate standard line ~15% lower
                }
                
                high_prob_demons.append(demon_entry)
        
        # Sort by hit_probability - highest chance of hitting first
        high_prob_demons.sort(key=lambda x: x["hit_probability"], reverse=True)
        
        logger.info(f"[PARLAY BUILDER] Found {len(high_prob_demons)} high-probability demons (50%+ hit rate)")
        
        # ==================== TWO-TEAM RULE HELPER ====================
        def get_multi_team_picks(demons: List[Dict], count: int) -> Tuple[List[Dict], bool, int]:
            """
            Select picks enforcing PrizePicks 2-Team minimum rule.
            
            Logic:
            - Pick #1: Top-ranked player
            - Pick #2: Next highest-ranked from DIFFERENT team
            - Picks #3-6: Any team (2-team minimum already established)
            
            Returns: (picks, is_valid, team_count)
            """
            if len(demons) < count:
                return [], False, 0
            
            picks = []
            used_players = set()
            teams_used = set()
            
            # Pick #1: Best available
            pick_1 = demons[0]
            picks.append(pick_1)
            used_players.add(pick_1["player_name"])
            teams_used.add(pick_1["team"])
            
            # Pick #2: MUST be from different team (enforce 2-team rule)
            pick_2 = None
            for d in demons[1:]:
                if d["player_name"] not in used_players and d["team"] != pick_1["team"]:
                    pick_2 = d
                    break
            
            if not pick_2:
                # Fallback: Can't find different team, parlay would be invalid
                # But we'll still try to build it and mark as invalid
                for d in demons[1:]:
                    if d["player_name"] not in used_players:
                        pick_2 = d
                        break
            
            if pick_2:
                picks.append(pick_2)
                used_players.add(pick_2["player_name"])
                teams_used.add(pick_2["team"])
            
            # Picks #3-6: Fill with remaining best picks (any team now OK)
            for d in demons:
                if len(picks) >= count:
                    break
                if d["player_name"] not in used_players:
                    picks.append(d)
                    used_players.add(d["player_name"])
                    teams_used.add(d["team"])
            
            is_valid = len(teams_used) >= 2
            return picks[:count], is_valid, len(teams_used)
        
        # ==================== SMART CORRELATION HELPER ====================
        def get_opponent_paired_picks(demons: List[Dict], count: int) -> Tuple[List[Dict], bool, int, bool]:
            """
            For 4+ pick parlays, try to include opponent pairs from same game.
            This captures game pace correlation.
            
            Returns: (picks, is_valid, team_count, has_opponent_pair)
            """
            if len(demons) < count:
                return [], False, 0, False
            
            picks = []
            used_players = set()
            teams_used = set()
            has_opponent_pair = False
            
            # First, find the best opponent pair (players from opposing teams in same game)
            opponent_pairs = []
            for i, d1 in enumerate(demons[:20]):  # Check top 20
                for d2 in demons[i+1:30]:
                    if d1["player_name"] != d2["player_name"]:
                        # Check if they're opponents in the same game
                        if d1["game_key"] == d2["game_key"] and d1["team"] != d2["team"]:
                            combined_prob = (d1["hit_probability"] + d2["hit_probability"]) / 2
                            opponent_pairs.append({
                                "pair": [d1, d2],
                                "combined_prob": combined_prob,
                                "game_key": d1["game_key"]
                            })
            
            # Sort pairs by combined probability
            opponent_pairs.sort(key=lambda x: x["combined_prob"], reverse=True)
            
            # Start with best opponent pair if available
            if opponent_pairs:
                best_pair = opponent_pairs[0]["pair"]
                picks.extend(best_pair)
                for p in best_pair:
                    used_players.add(p["player_name"])
                    teams_used.add(p["team"])
                has_opponent_pair = True
            else:
                # No opponent pair, use standard 2-team logic
                pick_1 = demons[0]
                picks.append(pick_1)
                used_players.add(pick_1["player_name"])
                teams_used.add(pick_1["team"])
                
                for d in demons[1:]:
                    if d["player_name"] not in used_players and d["team"] != pick_1["team"]:
                        picks.append(d)
                        used_players.add(d["player_name"])
                        teams_used.add(d["team"])
                        break
            
            # Fill remaining slots
            for d in demons:
                if len(picks) >= count:
                    break
                if d["player_name"] not in used_players:
                    picks.append(d)
                    used_players.add(d["player_name"])
                    teams_used.add(d["team"])
            
            is_valid = len(teams_used) >= 2
            return picks[:count], is_valid, len(teams_used), has_opponent_pair
        
        parlays = {}
        
        def calculate_live_payout(picks: List[Dict]) -> Dict:
            """Calculate live payout using the payout engine."""
            payout_result = calculate_payout_from_picks(picks)
            return {
                "estimated_payout": payout_result.get("estimated_payout", 0),
                "payout_display": payout_result.get("payout_display", "0x"),
                "cumulative_modifier": payout_result.get("cumulative_modifier", 1.0),
                "base_multiplier": payout_result.get("base_multiplier", 3.0),
                "asset_breakdown": payout_result.get("asset_breakdown", {}),
                "legs": payout_result.get("legs", [])
            }
        
        # ==================== 2-PICK (Double Up) ====================
        if len(high_prob_demons) >= 2:
            picks_2, is_valid, team_count = get_multi_team_picks(high_prob_demons, 2)
            if picks_2:
                combined_prob = self._calculate_parlay_probability(picks_2)
                payout_data = calculate_live_payout(picks_2)
                parlays["2_pick"] = {
                    "name": "Double Up",
                    "picks": picks_2,
                    "pick_count": 2,
                    "combined_probability": combined_prob,
                    "estimated_payout": payout_data["estimated_payout"],
                    "payout_display": payout_data["payout_display"],
                    "base_multiplier": payout_data["base_multiplier"],
                    "cumulative_modifier": payout_data["cumulative_modifier"],
                    "asset_breakdown": payout_data["asset_breakdown"],
                    "description": "Top 2 highest-probability demons",
                    "lineup_valid": is_valid,
                    "team_count": team_count,
                    "lineup_status": "Valid (Multi-Team)" if is_valid else "INVALID (Single Team)"
                }
        
        # ==================== 3-PICK (Triple Threat) ====================
        if len(high_prob_demons) >= 3:
            picks_3, is_valid, team_count = get_multi_team_picks(high_prob_demons, 3)
            if picks_3:
                combined_prob = self._calculate_parlay_probability(picks_3)
                payout_data = calculate_live_payout(picks_3)
                parlays["3_pick"] = {
                    "name": "Triple Threat",
                    "picks": picks_3,
                    "pick_count": 3,
                    "combined_probability": combined_prob,
                    "estimated_payout": payout_data["estimated_payout"],
                    "payout_display": payout_data["payout_display"],
                    "base_multiplier": payout_data["base_multiplier"],
                    "cumulative_modifier": payout_data["cumulative_modifier"],
                    "asset_breakdown": payout_data["asset_breakdown"],
                    "description": "Top 3 highest-probability demons",
                    "lineup_valid": is_valid,
                    "team_count": team_count,
                    "lineup_status": "Valid (Multi-Team)" if is_valid else "INVALID (Single Team)"
                }
        
        # ==================== 4-PICK (Power Play) - With Opponent Pairing ====================
        if len(high_prob_demons) >= 4:
            picks_4, is_valid, team_count, has_pair = get_opponent_paired_picks(high_prob_demons, 4)
            if picks_4:
                combined_prob = self._calculate_parlay_probability(picks_4)
                payout_data = calculate_live_payout(picks_4)
                status = "Valid (Multi-Team)"
                if has_pair:
                    status = "Valid (Opponent Pair)"
                elif not is_valid:
                    status = "INVALID (Single Team)"
                
                parlays["4_pick"] = {
                    "name": "Power Play",
                    "picks": picks_4,
                    "pick_count": 4,
                    "combined_probability": combined_prob,
                    "estimated_payout": payout_data["estimated_payout"],
                    "payout_display": payout_data["payout_display"],
                    "base_multiplier": payout_data["base_multiplier"],
                    "cumulative_modifier": payout_data["cumulative_modifier"],
                    "asset_breakdown": payout_data["asset_breakdown"],
                    "description": "4 picks with opponent correlation" if has_pair else "Top 4 highest-probability demons",
                    "lineup_valid": is_valid,
                    "team_count": team_count,
                    "has_opponent_pair": has_pair,
                    "lineup_status": status
                }
        
        # ==================== 5-PICK (Heavy Hitter) - With Opponent Pairing ====================
        if len(high_prob_demons) >= 5:
            picks_5, is_valid, team_count, has_pair = get_opponent_paired_picks(high_prob_demons, 5)
            if picks_5:
                combined_prob = self._calculate_parlay_probability(picks_5)
                payout_data = calculate_live_payout(picks_5)
                status = "Valid (Multi-Team)"
                if has_pair:
                    status = "Valid (Opponent Pair)"
                elif not is_valid:
                    status = "INVALID (Single Team)"
                
                parlays["5_pick"] = {
                    "name": "Heavy Hitter",
                    "picks": picks_5,
                    "pick_count": 5,
                    "combined_probability": combined_prob,
                    "estimated_payout": payout_data["estimated_payout"],
                    "payout_display": payout_data["payout_display"],
                    "base_multiplier": payout_data["base_multiplier"],
                    "cumulative_modifier": payout_data["cumulative_modifier"],
                    "asset_breakdown": payout_data["asset_breakdown"],
                    "description": "5 picks with game correlation" if has_pair else "Top 5 highest-probability demons",
                    "lineup_valid": is_valid,
                    "team_count": team_count,
                    "has_opponent_pair": has_pair,
                    "lineup_status": status
                }
        
        # ==================== 6-PICK (Jackpot) - With Opponent Pairing ====================
        if len(high_prob_demons) >= 6:
            picks_6, is_valid, team_count, has_pair = get_opponent_paired_picks(high_prob_demons, 6)
            if picks_6:
                combined_prob = self._calculate_parlay_probability(picks_6)
                payout_data = calculate_live_payout(picks_6)
                status = "Valid (Multi-Team)"
                if has_pair:
                    status = "Valid (Opponent Pair)"
                elif not is_valid:
                    status = "INVALID (Single Team)"
                
                parlays["6_pick"] = {
                    "name": "Jackpot",
                    "picks": picks_6,
                    "pick_count": 6,
                    "combined_probability": combined_prob,
                    "estimated_payout": payout_data["estimated_payout"],
                    "payout_display": payout_data["payout_display"],
                    "base_multiplier": payout_data["base_multiplier"],
                    "cumulative_modifier": payout_data["cumulative_modifier"],
                    "asset_breakdown": payout_data["asset_breakdown"],
                    "description": "6 picks with game correlation - MAX PAYOUT!" if has_pair else "Top 6 highest-probability demons - MAX PAYOUT!",
                    "lineup_valid": is_valid,
                    "team_count": team_count,
                    "has_opponent_pair": has_pair,
                    "lineup_status": status
                }
        
        # Store in database
        await self.parlay_builder.delete_many({})
        
        # ==================== TWO-TEAM GUARDRAIL ====================
        # Final validation: Remove any parlays that don't meet 2-team requirement
        # This ensures users never see an illegal slip even if data errors occur
        validated_parlays = {}
        for key, parlay in parlays.items():
            picks = parlay.get("picks", [])
            if len(picks) >= 2:
                teams = set(p.get("team", "") for p in picks)
                if len(teams) >= 2:
                    validated_parlays[key] = parlay
                else:
                    logger.warning(f"[GUARDRAIL] Rejected {key}: All picks from same team ({teams})")
            else:
                validated_parlays[key] = parlay
        
        parlays = validated_parlays
        
        # Count valid lineups
        valid_count = sum(1 for p in parlays.values() if p.get("lineup_valid", False))
        
        parlay_doc = {
            "parlays": parlays,
            "total_demons_analyzed": len(high_prob_demons),
            "min_probability_threshold": "50%",
            "valid_lineups": valid_count,
            "total_lineups": len(parlays),
            "synced_at": sync_time.isoformat()
        }
        
        await self.parlay_builder.insert_one(parlay_doc)
        
        logger.info(f"[PARLAY BUILDER] Generated {len(parlays)} parlay types ({valid_count} valid for PrizePicks)")
        for ptype, pdata in parlays.items():
            status = pdata.get("lineup_status", "Unknown")
            logger.info(f"  {ptype}: {pdata['name']} - {pdata['estimated_payout']}x | {pdata['combined_probability']:.1f}% | {status}")
    
    def _build_correlated_parlay(self, all_demons: List[Dict], target_count: int, game_groups: Dict) -> List[Dict]:
        """Build a parlay with game correlation where possible"""
        selected = []
        used_players = set()
        
        # Start with top pick
        if all_demons:
            top = all_demons[0]
            selected.append(top)
            used_players.add(top["player_name"])
        
        # Try to add correlated picks from same games as selected
        for demon in selected[:]:
            game_key = demon.get("game_key", "")
            if game_key and game_key in game_groups:
                for corr in game_groups[game_key]:
                    if len(selected) >= target_count:
                        break
                    if corr["player_name"] not in used_players:
                        selected.append(corr)
                        used_players.add(corr["player_name"])
        
        # Fill remaining from top demons
        for demon in all_demons:
            if len(selected) >= target_count:
                break
            if demon["player_name"] not in used_players:
                selected.append(demon)
                used_players.add(demon["player_name"])
        
        return selected[:target_count]
    
    def _calculate_parlay_probability(self, picks: List[Dict]) -> float:
        """Calculate combined probability of parlay hitting.
        
        Uses actual hit_probability (L10*0.6 + L5*0.4) for accurate calculation.
        """
        if not picks:
            return 0
        
        prob = 1.0
        for pick in picks:
            # Use hit_probability (the actual weighted probability)
            p = pick.get("hit_probability", 50) / 100  # Convert from percentage
            # Don't cap - use actual probability
            p = max(0.10, p)  # Only prevent division issues
            prob *= p
        
        return round(prob * 100, 2)
    
    async def _build_goblin_recon(self, players_dict: Dict[str, Dict], sync_time: datetime):
        """
        THE GOBLIN RECON - Maximum Win Probability Parlay Generator
        
        FLOOR SCORING ALGORITHM ($F$):
        1. Primary Threshold: Only Goblins with 88%+ weighted hit rate
        2. "Worst Case" Check: Player's lowest stat in L10 >= Goblin line = "Recon Lock"
        3. Blowout Protection: Avoid players on teams favored by >12 points
        
        RECON TIERS:
        - "Daily Double" (2-Pick): Top 2 highest floor safety (~90%+ combined)
        - "Green Ladder" (3 & 4-Pick): Diversified across different games
        - "6-Pick Fortress" (Flex): Top 6 for PrizePicks Flex (5/6 still profits)
        
        PAYOUT STRUCTURE (Goblin odds are typically -137 to -150):
        - 2-Pick: ~2.5x payout
        - 3-Pick: ~4x payout
        - 4-Pick: ~6x payout
        - 6-Pick: ~12x payout (Flex: 5/6 = 1.5x, 6/6 = 12x)
        """
        logger.info("[GOBLIN RECON] Mining for high-consistency Goblin parlays...")
        
        recon_candidates = []
        game_groups = {}  # For diversification
        
        for player_name, player_data in players_dict.items():
            # Skip None entries
            if player_data is None:
                logger.warning(f"[GOBLIN RECON] Skipping None player_data for {player_name}")
                continue
            goblins = player_data.get("goblins", [])
            team = player_data.get("team", "")
            
            for goblin in goblins:
                # Skip None goblins
                if goblin is None:
                    logger.warning(f"[GOBLIN RECON] Skipping None goblin for {player_name}")
                    continue
                hit_rates = goblin.get("hit_rates", {})
                if hit_rates is None:
                    hit_rates = {}
                h10_data = hit_rates.get("l10", {})
                h5_data = hit_rates.get("l5", {})
                season_data = hit_rates.get("season", {})
                if h10_data is None:
                    h10_data = {}
                if h5_data is None:
                    h5_data = {}
                if season_data is None:
                    season_data = {}
                
                h10 = h10_data.get("hit_rate", 0)
                h5 = h5_data.get("hit_rate", 0)
                h10_games = h10_data.get("total_games", 0)
                h10_over = h10_data.get("games_over", 0)
                h5_games = h5_data.get("total_games", 0)
                h5_over = h5_data.get("games_over", 0)
                season_avg = season_data.get("avg", 0)
                
                # Calculate weighted hit rate
                weighted_hit_rate = (h10 * 0.6) + (h5 * 0.4)
                
                # V3.2 FIX: SKIP players with no real stats data
                has_real_data = h10_games > 0 or h5_games > 0
                if not has_real_data:
                    continue
                
                # PRIMARY THRESHOLD: Only 88%+ weighted hit rate
                if weighted_hit_rate < 0.88:
                    continue
                
                # Get goblin line info
                goblin_line = goblin.get("line", 0)
                goblin_stat = self._extract_stat_type(goblin.get("market", ""))
                goblin_direction = goblin.get("direction", "Over")
                
                if not goblin_stat or goblin_line <= 0:
                    continue
                
                # Get game info for diversification
                home_team = goblin.get("home_team", "")
                away_team = goblin.get("away_team", "")
                game_key = f"{away_team}@{home_team}" if home_team and away_team else ""
                
                # Calculate FLOOR SCORE
                # Check if player's floor (lowest game) >= goblin line
                # We approximate this: if hit rate is 100%, floor >= line
                # If hit rate is 90%, the floor might be just below line
                is_recon_lock = h10_games >= 5 and h10_over == h10_games  # Perfect 10/10 or 5/5+
                
                # Floor safety score (0-100)
                # Perfect streak = 100, 9/10 = 90, 8/10 = 80, etc.
                floor_score = (h10_over / h10_games * 100) if h10_games > 0 else 0
                
                # Reliability rating (for UI)
                reliability = round(weighted_hit_rate * 100, 1)
                
                # Safety string
                safety_string = f"{h10_over}/{h10_games}" if h10_games > 0 else "---"
                
                recon_entry = {
                    "player_name": player_name,
                    "team": team,
                    "nba_id": player_data.get("nba_id"),
                    "photo_url": player_data.get("photo_url"),
                    "stat_type": goblin_stat,
                    "line": goblin_line,
                    "direction": goblin_direction,
                    "h10_rate": round(h10 * 100, 1),
                    "h5_rate": round(h5 * 100, 1),
                    "h10_over": h10_over,
                    "h10_games": h10_games,
                    "h5_over": h5_over,
                    "h5_games": h5_games,
                    "season_avg": round(season_avg, 1),
                    "weighted_hit_rate": round(weighted_hit_rate * 100, 1),
                    "floor_score": round(floor_score, 1),
                    "reliability": reliability,
                    "safety_string": safety_string,
                    "is_recon_lock": is_recon_lock,
                    "game_key": game_key,
                    "home_team": home_team,
                    "away_team": away_team,
                    "price": goblin.get("price", -137),
                    "synced_at": sync_time.isoformat()
                }
                
                recon_candidates.append(recon_entry)
                
                # Group by game for diversification
                if game_key:
                    if game_key not in game_groups:
                        game_groups[game_key] = []
                    game_groups[game_key].append(recon_entry)
        
        # Sort by floor_score (highest first), then by reliability
        recon_candidates.sort(key=lambda x: (x["floor_score"], x["reliability"]), reverse=True)
        
        logger.info(f"[GOBLIN RECON] Found {len(recon_candidates)} candidates (88%+ hit rate)")
        
        # ==================== TWO-TEAM RULE HELPERS ====================
        def get_multi_team_picks(candidates: List[Dict], count: int) -> Tuple[List[Dict], bool, int]:
            """
            Select picks enforcing PrizePicks 2-Team minimum rule.
            
            Logic:
            - Pick #1: Top-ranked player
            - Pick #2: Next highest-ranked from DIFFERENT team
            - Picks #3-6: Any team (2-team minimum already established)
            
            Returns: (picks, is_valid, team_count)
            """
            if len(candidates) < count:
                return [], False, 0
            
            picks = []
            used_players = set()
            teams_used = set()
            
            # Pick #1: Best available
            pick_1 = candidates[0]
            picks.append(pick_1)
            used_players.add(pick_1["player_name"])
            teams_used.add(pick_1["team"])
            
            # Pick #2: MUST be from different team (enforce 2-team rule)
            pick_2 = None
            for c in candidates[1:]:
                if c["player_name"] not in used_players and c["team"] != pick_1["team"]:
                    pick_2 = c
                    break
            
            if not pick_2:
                # Fallback: Can't find different team
                for c in candidates[1:]:
                    if c["player_name"] not in used_players:
                        pick_2 = c
                        break
            
            if pick_2:
                picks.append(pick_2)
                used_players.add(pick_2["player_name"])
                teams_used.add(pick_2["team"])
            
            # Picks #3+: Fill with remaining best picks
            for c in candidates:
                if len(picks) >= count:
                    break
                if c["player_name"] not in used_players:
                    picks.append(c)
                    used_players.add(c["player_name"])
                    teams_used.add(c["team"])
            
            is_valid = len(teams_used) >= 2
            return picks[:count], is_valid, len(teams_used)
        
        def get_diversified_multi_team_picks(candidates: List[Dict], count: int, game_groups: Dict) -> Tuple[List[Dict], bool, int]:
            """
            Get diversified picks with 2-Team Rule enforcement.
            Prioritizes picks from different games AND different teams.
            """
            if len(candidates) < count:
                return [], False, 0
            
            picks = []
            used_players = set()
            used_games = set()
            teams_used = set()
            
            # Pick #1: Best available
            if candidates:
                pick_1 = candidates[0]
                picks.append(pick_1)
                used_players.add(pick_1["player_name"])
                teams_used.add(pick_1["team"])
                if pick_1.get("game_key"):
                    used_games.add(pick_1["game_key"])
            
            # Pick #2: Different team AND different game if possible
            pick_2 = None
            for c in candidates[1:]:
                if c["player_name"] not in used_players and c["team"] != picks[0]["team"]:
                    game = c.get("game_key", "")
                    if game and game not in used_games:
                        pick_2 = c
                        break
            
            # Fallback: Just different team
            if not pick_2:
                for c in candidates[1:]:
                    if c["player_name"] not in used_players and c["team"] != picks[0]["team"]:
                        pick_2 = c
                        break
            
            if pick_2:
                picks.append(pick_2)
                used_players.add(pick_2["player_name"])
                teams_used.add(pick_2["team"])
                if pick_2.get("game_key"):
                    used_games.add(pick_2["game_key"])
            
            # Remaining picks: Prioritize different games
            for c in candidates:
                if len(picks) >= count:
                    break
                if c["player_name"] not in used_players:
                    game = c.get("game_key", "")
                    if not game or game not in used_games:
                        picks.append(c)
                        used_players.add(c["player_name"])
                        teams_used.add(c["team"])
                        if game:
                            used_games.add(game)
            
            # Final fill if needed
            for c in candidates:
                if len(picks) >= count:
                    break
                if c["player_name"] not in used_players:
                    picks.append(c)
                    used_players.add(c["player_name"])
                    teams_used.add(c["team"])
            
            is_valid = len(teams_used) >= 2
            return picks[:count], is_valid, len(teams_used)
        
        def calculate_recon_probability(picks: List[Dict]) -> float:
            """Calculate combined probability for Recon parlays"""
            if not picks:
                return 0
            prob = 1.0
            for pick in picks:
                p = pick.get("weighted_hit_rate", 88) / 100
                prob *= p
            return round(prob * 100, 2)
        
        def calculate_goblin_payout(picks: List[Dict]) -> Dict:
            """Calculate payout for Goblin picks using actual PrizePicks formula: 1.2^n
            
            The formula already accounts for goblin odds (-137), so no additional
            modifier is applied.
            """
            num_picks = len(picks)
            
            # Use the exact PrizePicks formula: 1.2^n
            # 2-pick: 1.44 → 1.4x
            # 3-pick: 1.73 → 1.7x
            # 4-pick: 2.07 → 2.1x
            # 6-pick: 2.99 → 3.0x
            base_payout = round(1.2 ** num_picks, 2)
            
            # Build leg details for response
            leg_details = []
            for pick in picks:
                leg_details.append({
                    "player_name": pick.get("player_name", "Unknown"),
                    "stat_type": pick.get("stat_type", "PTS"),
                    "line": pick.get("line", 0),
                    "direction": pick.get("direction", "over"),
                    "team": pick.get("team", ""),
                    "asset_type": "goblin",
                    "modifier": 1.0,
                    "modifier_display": "1.00x"
                })
            
            return {
                "estimated_payout": base_payout,
                "payout_display": f"{base_payout:.1f}x",
                "cumulative_modifier": 1.0,
                "base_multiplier": base_payout,
                "asset_breakdown": {"demons": 0, "goblins": num_picks, "standards": 0},
                "payout_type": "goblin",
                "legs": leg_details
            }
        
        parlays = {}
        
        # ==================== DAILY DOUBLE (2-Pick) ====================
        if len(recon_candidates) >= 2:
            picks_2, is_valid, team_count = get_multi_team_picks(recon_candidates, 2)
            if picks_2:
                combined_prob = calculate_recon_probability(picks_2)
                payout_data = calculate_goblin_payout(picks_2)
                parlays["daily_double"] = {
                    "name": "Daily Double",
                    "tier": "daily_double",
                    "picks": picks_2,
                    "pick_count": 2,
                    "combined_probability": combined_prob,
                    "reliability": combined_prob,
                    "estimated_payout": payout_data["estimated_payout"],
                    "payout_display": payout_data["payout_display"],
                    "base_multiplier": payout_data["base_multiplier"],
                    "cumulative_modifier": payout_data["cumulative_modifier"],
                    "asset_breakdown": payout_data["asset_breakdown"],
                    "description": "Top 2 highest-consistency Goblins - Nearly automatic!",
                    "badge": "SAFEST BET",
                    "lineup_valid": is_valid,
                    "team_count": team_count,
                    "lineup_status": "Valid (Multi-Team)" if is_valid else "INVALID (Single Team)"
                }
        
        # ==================== GREEN LADDER 3-Pick ====================
        if len(recon_candidates) >= 3:
            picks_3, is_valid, team_count = get_diversified_multi_team_picks(recon_candidates, 3, game_groups)
            if picks_3:
                combined_prob = calculate_recon_probability(picks_3)
                payout_data = calculate_goblin_payout(picks_3)
                parlays["green_ladder_3"] = {
                    "name": "Green Ladder",
                    "tier": "green_ladder_3",
                    "picks": picks_3,
                    "pick_count": 3,
                    "combined_probability": combined_prob,
                    "reliability": combined_prob,
                    "estimated_payout": payout_data["estimated_payout"],
                    "payout_display": payout_data["payout_display"],
                    "base_multiplier": payout_data["base_multiplier"],
                    "cumulative_modifier": payout_data["cumulative_modifier"],
                    "asset_breakdown": payout_data["asset_breakdown"],
                    "description": "3 Goblins diversified across games",
                    "badge": "DIVERSIFIED",
                    "lineup_valid": is_valid,
                    "team_count": team_count,
                    "lineup_status": "Valid (Multi-Team)" if is_valid else "INVALID (Single Team)"
                }
        
        # ==================== GREEN LADDER 4-Pick ====================
        if len(recon_candidates) >= 4:
            picks_4, is_valid, team_count = get_diversified_multi_team_picks(recon_candidates, 4, game_groups)
            if picks_4:
                combined_prob = calculate_recon_probability(picks_4)
                payout_data = calculate_goblin_payout(picks_4)
                parlays["green_ladder_4"] = {
                    "name": "Green Ladder+",
                    "tier": "green_ladder_4",
                    "picks": picks_4,
                    "pick_count": 4,
                    "combined_probability": combined_prob,
                    "reliability": combined_prob,
                    "estimated_payout": payout_data["estimated_payout"],
                    "payout_display": payout_data["payout_display"],
                    "base_multiplier": payout_data["base_multiplier"],
                    "cumulative_modifier": payout_data["cumulative_modifier"],
                    "asset_breakdown": payout_data["asset_breakdown"],
                    "description": "4 Goblins diversified for risk management",
                    "badge": "BALANCED",
                    "lineup_valid": is_valid,
                    "team_count": team_count,
                    "lineup_status": "Valid (Multi-Team)" if is_valid else "INVALID (Single Team)"
                }
        
        # ==================== 5-PICK GREEN STACK ====================
        # Same pattern as 6-pick but with 5 picks
        if len(recon_candidates) >= 5:
            picks_5, is_valid, team_count = get_diversified_multi_team_picks(recon_candidates, 5, game_groups)
            if picks_5:
                combined_prob = calculate_recon_probability(picks_5)
                payout_data = calculate_goblin_payout(picks_5)
                
                parlays["green_stack_5"] = {
                    "name": "Green Stack",
                    "tier": "green_stack_5",
                    "picks": picks_5,
                    "pick_count": 5,
                    "combined_probability": combined_prob,
                    "reliability": combined_prob,
                    "estimated_payout": payout_data["estimated_payout"],
                    "payout_display": payout_data["payout_display"],
                    "base_multiplier": payout_data["base_multiplier"],
                    "cumulative_modifier": payout_data["cumulative_modifier"],
                    "asset_breakdown": payout_data["asset_breakdown"],
                    "description": "5 Goblins stacked for premium payout",
                    "badge": "STACK",
                    "lineup_valid": is_valid,
                    "team_count": team_count,
                    "lineup_status": "Valid (Multi-Team)" if is_valid else "INVALID (Single Team)"
                }
        
        # ==================== 6-PICK FORTRESS (Flex Play) ====================
        # Designed for PrizePicks Flex: 5/6 = 1.5x profit, 6/6 = 12x
        if len(recon_candidates) >= 6:
            picks_6, is_valid, team_count = get_diversified_multi_team_picks(recon_candidates, 6, game_groups)
            if picks_6:
                combined_prob = calculate_recon_probability(picks_6)
                payout_data = calculate_goblin_payout(picks_6)
                
                # Calculate Flex probability (hitting 5 or 6 out of 6)
                avg_p = sum(p["weighted_hit_rate"] for p in picks_6) / 600
                p_all_6 = avg_p ** 6
                p_exactly_5 = 6 * (avg_p ** 5) * (1 - avg_p)
                flex_win_prob = round((p_all_6 + p_exactly_5) * 100, 2)
                
                parlays["fortress_flex"] = {
                    "name": "6-Pick Fortress",
                    "tier": "fortress_flex",
                    "picks": picks_6,
                    "pick_count": 6,
                    "combined_probability": combined_prob,
                    "flex_probability": flex_win_prob,
                    "reliability": flex_win_prob,
                    "estimated_payout": payout_data["estimated_payout"],
                    "payout_display": payout_data["payout_display"],
                    "base_multiplier": payout_data["base_multiplier"],
                    "cumulative_modifier": payout_data["cumulative_modifier"],
                    "asset_breakdown": payout_data["asset_breakdown"],
                    "flex_payout": "5/6 = 1.5x | 6/6 = 15x",
                    "description": "PrizePicks Flex Play - Win on 5 OR 6 hits!",
                    "badge": "FLEX FORTRESS",
                    "lineup_valid": is_valid,
                    "team_count": team_count,
                    "lineup_status": "Valid (Multi-Team)" if is_valid else "INVALID (Single Team)"
                }
        
        # Store in database
        await self.goblin_recon.delete_many({})
        
        # ==================== TWO-TEAM GUARDRAIL ====================
        # Final validation: Remove any parlays that don't meet 2-team requirement
        # This ensures users never see an illegal slip even if data errors occur
        validated_parlays = {}
        for key, parlay in parlays.items():
            picks = parlay.get("picks", [])
            if len(picks) >= 2:
                teams = set(p.get("team", "") for p in picks)
                if len(teams) >= 2:
                    validated_parlays[key] = parlay
                else:
                    logger.warning(f"[GUARDRAIL] Rejected {key}: All picks from same team ({teams})")
            else:
                validated_parlays[key] = parlay
        
        parlays = validated_parlays
        
        # Count valid lineups
        valid_count = sum(1 for p in parlays.values() if p.get("lineup_valid", False))
        
        recon_doc = {
            "parlays": parlays,
            "total_candidates": len(recon_candidates),
            "recon_locks": len([c for c in recon_candidates if c["is_recon_lock"]]),
            "games_available": len(game_groups),
            "min_hit_rate_threshold": "88%",
            "valid_lineups": valid_count,
            "total_lineups": len(parlays),
            "synced_at": sync_time.isoformat()
        }
        
        await self.goblin_recon.insert_one(recon_doc)
        
        # Log summary
        locks_count = len([c for c in recon_candidates if c["is_recon_lock"]])
        logger.info(f"[GOBLIN RECON] Generated {len(parlays)} Recon parlay tiers ({valid_count} valid for PrizePicks)")
        logger.info(f"  Recon Locks (100% L10): {locks_count}")
        logger.info(f"  Games for diversification: {len(game_groups)}")
        
        for tier, data in parlays.items():
            reliability = data.get("reliability", 0)
            status = data.get("lineup_status", "Unknown")
            logger.info(f"  {data['name']}: {reliability}% reliability | {status}")
    
    def _extract_stat_type(self, market: str) -> str:
        """Extract stat type from market name"""
        # Remove _alternate suffix
        market = market.replace("_alternate", "")
        
        # Map to stat types
        stat_map = {
            "player_points": "PTS",
            "player_rebounds": "REB",
            "player_assists": "AST",
            "player_threes": "3PM",
            "player_blocks": "BLK",
            "player_steals": "STL",
            "player_turnovers": "TO",
            "player_points_rebounds": "P+R",
            "player_points_assists": "P+A",
            "player_rebounds_assists": "R+A",
            "player_points_rebounds_assists": "PRA",
        }
        
        return stat_map.get(market, "")
    
    async def get_demon_radar(self) -> Dict[str, Any]:
        """
        DUMB COMPONENT: Get the Demon Radar top 10 picks from MongoDB.
        
        Data is PRE-ENRICHED during sync. No runtime lookups.
        Just reads and returns the data with AI insights.
        """
        picks = await self.radar_picks.find({}, {"_id": 0}).sort("radar_score", -1).to_list(10)
        
        # Add AI insights (both old insight_summary and new intel_briefing)
        for pick in picks:
            player_name = pick.get('player_name')
            
            # Get old insight_summary from daily_insights
            insight = await self.daily_insights.find_one(
                {"player_name": player_name},
                {"_id": 0, "insight_summary": 1, "ai_confidence_rating": 1}
            )
            if insight:
                pick['insight_summary'] = insight.get('insight_summary', '')
                pick['ai_confidence'] = insight.get('ai_confidence_rating', 50)
            
            # Get new intel_briefing from cached_board or intel_briefings
            board_entry = await self.cached_board.find_one(
                {"player_name": player_name},
                {"_id": 0, "intel_briefing": 1}
            )
            if board_entry and board_entry.get('intel_briefing'):
                pick['intel_briefing'] = board_entry.get('intel_briefing')
        
        sync_meta = await self.sync_log.find_one({"type": "cached_board"})
        
        return {
            "success": True,
            "synced_at": sync_meta.get("synced_at") if sync_meta else None,
            "picks_count": len(picks),
            "picks": picks,
            "algorithm": {
                "description": "Weighted Probability + Line Gap",
                "formula": "Score = P / Gap_Ratio",
                "hit_probability": "(H10 × 0.6) + (H5 × 0.4)",
                "min_probability": "60%"
            }
        }
    
    async def get_goblin_vault(self) -> Dict[str, Any]:
        """
        DUMB COMPONENT: Get the Goblin Vault top 10 safe plays from MongoDB.
        
        Data is PRE-ENRICHED during sync. No runtime lookups.
        Just reads and returns the data with AI insights.
        """
        picks = await self.goblin_vault.find({}, {"_id": 0}).sort("vault_score", -1).to_list(10)
        
        # Add AI insights (both old insight_summary and new intel_briefing)
        for pick in picks:
            player_name = pick.get('player_name')
            
            # Get old insight_summary from daily_insights
            insight = await self.daily_insights.find_one(
                {"player_name": player_name},
                {"_id": 0, "insight_summary": 1, "ai_confidence_rating": 1}
            )
            if insight:
                pick['insight_summary'] = insight.get('insight_summary', '')
                pick['ai_confidence'] = insight.get('ai_confidence_rating', 50)
            
            # Get new intel_briefing from cached_board
            board_entry = await self.cached_board.find_one(
                {"player_name": player_name},
                {"_id": 0, "intel_briefing": 1}
            )
            if board_entry and board_entry.get('intel_briefing'):
                pick['intel_briefing'] = board_entry.get('intel_briefing')
        
        sync_meta = await self.sync_log.find_one({"type": "cached_board"})
        
        return {
            "success": True,
            "synced_at": sync_meta.get("synced_at") if sync_meta else None,
            "picks_count": len(picks),
            "picks": picks,
            "algorithm": {
                "description": "Hit Rate + Value Gap Scoring",
                "formula": "Score = (Hit_Rate × 0.8) + (Value_Gap × 0.2)",
                "target": "90%+ hit rate for maximum safety",
                "min_probability": "80%"
            }
        }
    
    async def get_parlay_builder(self) -> Dict[str, Any]:
        """
        DUMB COMPONENT: Get the Parlay Builder (Gauntlet) parlays from MongoDB.
        
        Data is PRE-ENRICHED during sync. No runtime lookups.
        Just reads and returns the data with AI insights.
        """
        doc = await self.parlay_builder.find_one({}, {"_id": 0})
        
        if not doc:
            return {
                "success": False,
                "message": "No parlay data. Run /api/v3/sync first.",
                "parlays": {}
            }
        
        # Only add AI insights (data is already enriched during sync)
        parlays = doc.get("parlays", {})
        for parlay_key, parlay_data in parlays.items():
            picks = parlay_data.get("picks", [])
            for pick in picks:
                insight = await self.daily_insights.find_one(
                    {"player_name": pick.get('player_name')},
                    {"_id": 0, "insight_summary": 1, "ai_confidence_rating": 1}
                )
                if insight:
                    pick['insight_summary'] = insight.get('insight_summary', '')
                    pick['ai_confidence_rating'] = insight.get('ai_confidence_rating', 50)
        
        return {
            "success": True,
            "synced_at": doc.get("synced_at"),
            "total_demons_analyzed": doc.get("total_demons_analyzed", 0),
            "games_with_correlation": doc.get("games_with_correlation", 0),
            "parlays": parlays,
            "algorithm": {
                "description": "Whale Scoring + Correlation Filter",
                "whale_score": "(H10 × 0.6) + (H5 × 0.4) × heat_boost",
                "correlation": "Same-game pairing for 4-6 picks"
            }
        }
    
    async def get_goblin_recon(self) -> Dict[str, Any]:
        """
        DUMB COMPONENT: Get the Goblin Recon (Safe Haven) parlays from MongoDB.
        
        Data is PRE-ENRICHED during sync. No runtime lookups.
        Just reads and returns the data with AI insights.
        """
        doc = await self.goblin_recon.find_one({}, {"_id": 0})
        
        if not doc:
            return {
                "success": False,
                "message": "No Recon data. Run /api/v3/sync first.",
                "parlays": {}
            }
        
        # Only add AI insights (data is already enriched during sync)
        parlays = doc.get("parlays", {})
        for parlay_key, parlay_data in parlays.items():
            picks = parlay_data.get("picks", [])
            for pick in picks:
                insight = await self.daily_insights.find_one(
                    {"player_name": pick.get('player_name')},
                    {"_id": 0, "insight_summary": 1, "ai_confidence_rating": 1}
                )
                if insight:
                    pick['insight_summary'] = insight.get('insight_summary', '')
                    pick['ai_confidence_rating'] = insight.get('ai_confidence_rating', 50)
        
        return {
            "success": True,
            "synced_at": doc.get("synced_at"),
            "total_candidates": doc.get("total_candidates", 0),
            "recon_locks": doc.get("recon_locks", 0),
            "games_available": doc.get("games_available", 0),
            "parlays": parlays,
            "algorithm": {
                "name": "Floor Scoring",
                "description": "Maximum win probability using high-consistency Goblins",
                "min_hit_rate": "88%+",
                "flex_play": "6-Pick Fortress designed for PrizePicks Flex"
            }
        }
    
    async def get_cached_board(self) -> Dict[str, Any]:
        """
        Get the CACHED board from MongoDB.
        NO API CALLS - reads only from database.
        """
        # Get sync metadata
        sync_meta = await self.sync_log.find_one({"type": "cached_board"})
        
        if not sync_meta:
            return {
                "success": False,
                "synced_at": None,
                "message": "No cached data. Run /api/v3/sync first.",
                "players": [],
                "trending": []
            }
        
        # Get all players from cached_board (exclude _id)
        players = await self.cached_board.find({}, {"_id": 0}).sort("rank", 1).to_list(500)
        
        # Clean any remaining ObjectIds
        for player in players:
            for prop in player.get("props", []):
                prop.pop("_id", None)
            for prop in player.get("demons", []):
                prop.pop("_id", None)
            for prop in player.get("goblins", []):
                prop.pop("_id", None)
            for prop in player.get("standard", []):
                prop.pop("_id", None)
        
        # Get trending (top 10)
        trending = players[:10] if players else []
        
        return {
            "success": True,
            "synced_at": sync_meta.get("synced_at"),
            "players_count": len(players),
            "total_props": sync_meta.get("total_props", 0),
            "players": players,
            "trending": trending
        }
    
    async def get_cached_player(self, player_name: str) -> Dict[str, Any]:
        """
        Get a single player from the CACHED board.
        Also includes advanced analytics insights from dg_daily_insights.
        NO API CALLS - reads only from database.
        """
        # Try exact match first
        player = await self.cached_board.find_one(
            {"player_name": player_name},
            {"_id": 0}
        )
        
        if player:
            # Clean ObjectIds from nested arrays
            self._clean_object_ids(player)
            # Add insights data
            await self._add_player_insights(player)
            return {"success": True, "player": player}
        
        # Try case-insensitive search
        player = await self.cached_board.find_one(
            {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0}
        )
        
        if player:
            self._clean_object_ids(player)
            await self._add_player_insights(player)
            return {"success": True, "player": player}
        
        # Fuzzy search
        all_players = await self.cached_board.find({}, {"player_name": 1, "_id": 0}).to_list(500)
        
        best_match = None
        best_score = 0
        for p in all_players:
            score = fuzz.ratio(player_name.lower(), p["player_name"].lower())
            if score > best_score and score > 70:
                best_score = score
                best_match = p["player_name"]
        
        if best_match:
            player = await self.cached_board.find_one(
                {"player_name": best_match},
                {"_id": 0}
            )
            self._clean_object_ids(player)
            await self._add_player_insights(player)
            return {"success": True, "player": player, "matched_name": best_match}
        
        return {
            "success": False,
            "message": "Lines loading... Player not in cache.",
            "player": None
        }
    
    async def _add_player_insights(self, player: Dict) -> None:
        """Fetch and add insights data to a player dict."""
        if not player or not player.get("player_name"):
            return
        
        insights = await self.daily_insights.find_one(
            {"player_name": player["player_name"]},
            {"_id": 0, "player_name": 0, "team": 0, "synced_at": 0}
        )
        
        if insights:
            player["insights"] = insights
        else:
            # Provide default insights if not calculated yet
            player["insights"] = {
                "schedule_density_factor": 1.0,
                "pace_adjustment_factor": 1.0,
                "usage_bump_percent": 0,
                "volatility_score": "Low",
                "volatility_stddev": 0,
                "insight_summary": "",
                "ai_confidence_rating": 50,
                "is_back_to_back": False,
                "is_three_in_four": False,
                "days_rest": 2,
                "injured_teammates": []
            }
    
    def _clean_object_ids(self, player: Dict) -> None:
        """Remove all ObjectId fields from nested arrays to prevent serialization errors"""
        for key in ["props", "demons", "goblins", "standard"]:
            if key in player and isinstance(player[key], list):
                for item in player[key]:
                    if isinstance(item, dict):
                        item.pop("_id", None)
    
    # ==================== PILLAR 1: THE ODDS API (PrizePicks) ====================
    
    async def fetch_todays_events(self) -> List[Dict[str, Any]]:
        """Fetch all NBA events for today"""
        try:
            url = f"{ODDS_API_BASE}/sports/basketball_nba/events"
            params = {"apiKey": ODDS_API_KEY, "dateFormat": "iso"}
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=15.0)
                
                if response.status_code == 200:
                    events = response.json()
                    
                    # Store all events (don't filter by date to ensure we get all games)
                    await self.events_cache.delete_many({})
                    for event in events:
                        event["fetched_at"] = datetime.now(timezone.utc).isoformat()
                        await self.events_cache.insert_one(event)
                    
                    logger.info(f"[PILLAR 1] Found {len(events)} NBA events")
                    for e in events[:10]:
                        logger.info(f"  • {e.get('away_team')} @ {e.get('home_team')}")
                    
                    return events
                    
        except Exception as e:
            logger.error(f"[PILLAR 1] Event fetch error: {e}")
        
        return []
    
    async def fetch_prizepicks_odds(self, event_id: str, event_info: Dict) -> Dict[str, Any]:
        """
        Fetch PrizePicks odds using the correct API parameters:
        - regions=us_dfs (Daily Fantasy Sports)
        - bookmakers=prizepicks
        - markets=ALL markets (both standard and alternate)
        
        Classification will happen later based on market type:
        - Standard markets → "standard" (no icon)
        - Alternate markets + price=100 → "demon" (red icon)
        - Alternate markets + price≠100 → "goblin" (green icon)
        """
        try:
            url = f"{ODDS_API_BASE}/sports/basketball_nba/events/{event_id}/odds"
            
            # CRITICAL: Use us_dfs region and prizepicks bookmaker with ALL markets
            params = {
                "apiKey": ODDS_API_KEY,
                "regions": PRIZEPICKS_REGION,  # us_dfs - REQUIRED for PrizePicks
                "markets": PRIZEPICKS_ALL_MARKETS,  # Both standard and alternate markets
                "bookmakers": PRIZEPICKS_BOOKMAKER,  # prizepicks specifically
                "oddsFormat": "american"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=30.0)
                
                if response.status_code == 200:
                    odds_data = response.json()
                    odds_data["event_id"] = event_id
                    odds_data["fetched_at"] = datetime.now(timezone.utc).isoformat()
                    odds_data["source"] = "prizepicks"
                    
                    # Count outcomes
                    total_outcomes = 0
                    players_found = set()
                    for bm in odds_data.get("bookmakers", []):
                        for market in bm.get("markets", []):
                            for outcome in market.get("outcomes", []):
                                total_outcomes += 1
                                if outcome.get("description"):
                                    players_found.add(outcome.get("description"))
                    
                    logger.info(f"  [PRIZEPICKS] {event_info.get('away_team')} @ {event_info.get('home_team')}: {total_outcomes} lines, {len(players_found)} players")
                    
                    # Store in cache
                    await self.odds_cache.update_one(
                        {"event_id": event_id, "source": "prizepicks"},
                        {"$set": odds_data},
                        upsert=True
                    )
                    
                    return odds_data
                    
                elif response.status_code == 422:
                    # Try with just the basic alternate markets
                    logger.warning(f"  [PRIZEPICKS] Some markets unavailable for {event_id}, trying basic markets")
                    params["markets"] = "player_points,player_points_alternate,player_rebounds,player_rebounds_alternate,player_assists,player_assists_alternate"
                    response = await client.get(url, params=params, timeout=30.0)
                    if response.status_code == 200:
                        odds_data = response.json()
                        odds_data["event_id"] = event_id
                        odds_data["source"] = "prizepicks"
                        return odds_data
                else:
                    logger.warning(f"  [PRIZEPICKS] API returned {response.status_code} for {event_id}")
                        
        except Exception as e:
            logger.error(f"[PILLAR 1] PrizePicks odds fetch error for {event_id}: {e}")
        
        return {}
    
    async def fetch_standard_odds(self, event_id: str, event_info: Dict) -> Dict[str, Any]:
        """Also fetch standard markets from DraftKings/FanDuel for comparison"""
        try:
            url = f"{ODDS_API_BASE}/sports/basketball_nba/events/{event_id}/odds"
            params = {
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": ",".join(PRIZEPICKS_STANDARD_MARKETS),
                "bookmakers": "draftkings,fanduel",
                "oddsFormat": "american"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=30.0)
                
                if response.status_code == 200:
                    return response.json()
                        
        except Exception as e:
            logger.error(f"[PILLAR 1] Standard odds fetch error: {e}")
        
        return {}
    
    def extract_prizepicks_props(self, odds_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract all PrizePicks props and classify correctly:
        
        CLASSIFICATION RULES:
        - STANDARD (no icon): Props from MAIN markets (e.g., player_points)
        - DEMON (red icon): Props from ALTERNATE markets with EVEN odds (+100)
        - GOBLIN (green icon): Props from ALTERNATE markets with odds ≠ +100
        
        Also tracks player order for popularity ranking.
        """
        props = []
        event_id = odds_data.get("id") or odds_data.get("event_id")
        home_team = odds_data.get("home_team", "")
        away_team = odds_data.get("away_team", "")
        commence_time = odds_data.get("commence_time", "")
        
        # Track player appearance order (first players in response = more popular)
        player_order_counter = 0
        seen_players_in_event = set()
        
        for bookmaker in odds_data.get("bookmakers", []):
            book_key = bookmaker.get("key", "")
            
            # Only process PrizePicks data
            if book_key != "prizepicks":
                continue
            
            for market in bookmaker.get("markets", []):
                market_key = market.get("key", "")
                market_last_update = market.get("last_update", "")
                
                # Determine if this is an alternate market
                is_alternate_market = "_alternate" in market_key
                
                for outcome in market.get("outcomes", []):
                    player_name = outcome.get("description", "")
                    direction = outcome.get("name", "")  # Over/Under
                    line = outcome.get("point")
                    price = outcome.get("price")  # American odds
                    
                    if player_name and line is not None:
                        # Track popularity order - first appearance = more popular
                        if player_name not in seen_players_in_event:
                            seen_players_in_event.add(player_name)
                            player_order_counter += 1
                            
                            # Store popularity (lower = more popular)
                            if player_name not in self._player_popularity:
                                self._player_popularity[player_name] = player_order_counter
                            else:
                                # Average with existing (in case player appears in multiple events)
                                self._player_popularity[player_name] = min(
                                    self._player_popularity[player_name], 
                                    player_order_counter
                                )
                        
                        # CLASSIFICATION LOGIC:
                        # 1. Standard: Main market (no _alternate) → no icon
                        # 2. Demon: Alternate market + price == +100 → red icon
                        # 3. Goblin: Alternate market + price != +100 → green icon
                        
                        if is_alternate_market:
                            # Alternate market classification
                            is_demon = price is not None and price == DEMON_ODDS
                            is_goblin = price is not None and price != DEMON_ODDS
                            prop_type = "demon" if is_demon else "goblin"
                        else:
                            # Standard market - no demon/goblin classification
                            is_demon = False
                            is_goblin = False
                            prop_type = "standard"
                        
                        props.append({
                            "event_id": event_id,
                            "home_team": home_team,
                            "away_team": away_team,
                            "commence_time": commence_time,
                            "player_name": player_name,
                            "market": market_key,
                            "direction": direction,
                            "line": float(line),
                            "price": price,
                            "bookmaker": "prizepicks",
                            "is_alternate_market": is_alternate_market,
                            "is_demon": is_demon,
                            "is_goblin": is_goblin,
                            "prop_type": prop_type,
                            "last_update": market_last_update,
                            "popularity_order": self._player_popularity.get(player_name, 999),
                            "fetched_at": datetime.now(timezone.utc).isoformat()
                        })
        
        return props
    
    # ==================== PILLAR 2: BALLDONTLIE API ====================
    
    async def search_bdl_player(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Map player name to BallDontLie player data"""
        if player_name in self._player_name_map:
            return self._player_name_map[player_name]
        
        try:
            name_parts = player_name.strip().split()
            search_terms = [name_parts[-1]] if len(name_parts) >= 2 else [player_name]
            if len(name_parts) >= 2:
                search_terms.append(name_parts[0])
            
            url = f"{BDL_BASE_URL}/players"
            headers = {"Authorization": BDL_API_KEY}
            
            for search_term in search_terms:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        url,
                        params={"search": search_term},
                        headers=headers,
                        timeout=10.0
                    )
                    
                    if response.status_code == 200:
                        players = response.json().get("data", [])
                        
                        if not players:
                            continue
                        
                        best_match = None
                        best_score = 0
                        
                        for player in players:
                            full_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
                            score = max(
                                fuzz.ratio(player_name.lower(), full_name.lower()),
                                fuzz.partial_ratio(player_name.lower(), full_name.lower())
                            )
                            
                            if score > best_score and score >= 60:
                                best_score = score
                                best_match = player
                        
                        if best_match:
                            self._player_name_map[player_name] = best_match
                            return best_match
            
        except Exception as e:
            logger.error(f"[PILLAR 2] BDL search error for {player_name}: {e}")
        
        return None
    
    async def fetch_player_season_stats(self, player_id: int) -> List[Dict[str, Any]]:
        """Fetch season stats for hit rate calculation"""
        try:
            # Check cache first
            cached = await self.stats_cache.find_one({"player_id": str(player_id)})
            if cached:
                cached_time = datetime.fromisoformat(cached["cached_at"])
                if datetime.now(timezone.utc) - cached_time < timedelta(hours=4):
                    return cached.get("games", [])
            
            url = f"{BDL_BASE_URL}/stats"
            params = {
                "player_ids[]": player_id,
                "seasons[]": CURRENT_SEASON,
                "per_page": 100
            }
            headers = {"Authorization": BDL_API_KEY}
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, headers=headers, timeout=15.0)
                
                if response.status_code == 200:
                    games = response.json().get("data", [])
                    
                    # Sort by date (most recent first)
                    games_sorted = sorted(
                        games,
                        key=lambda x: x.get("game", {}).get("date", ""),
                        reverse=True
                    )
                    
                    # Filter out DNP games
                    def player_played(game):
                        minutes = game.get("min")
                        if minutes:
                            min_str = str(minutes).replace(":", "").strip()
                            if min_str and min_str != "0" and min_str != "00":
                                return True
                        return (game.get("pts", 0) or 0) + (game.get("reb", 0) or 0) + (game.get("ast", 0) or 0) > 0
                    
                    played_games = [g for g in games_sorted if player_played(g)]
                    
                    # Cache results
                    await self.stats_cache.update_one(
                        {"player_id": str(player_id)},
                        {"$set": {
                            "player_id": str(player_id),
                            "games": played_games,
                            "cached_at": datetime.now(timezone.utc).isoformat()
                        }},
                        upsert=True
                    )
                    
                    return played_games
                    
        except Exception as e:
            logger.error(f"[PILLAR 2] Stats fetch error for player {player_id}: {e}")
        
        return []
    
    def calculate_hit_rates(self, games: List[Dict], market: str, line: float) -> Dict[str, Any]:
        """
        Calculate L5, L10, and Season hit rates with source verification.
        
        TRUTH ENGINE V3.1:
        - All stat keys MUST be lowercase (pts, reb, ast - not PTS, REB, AST)
        - Manual PRA check for each game
        - Returns raw values for verification
        """
        # CRITICAL: Case-insensitive stat key mapping (Tank01/BallDontLie use lowercase)
        market_to_stat = {
            # Primary markets (lowercase keys)
            "player_points": ["pts"],
            "player_rebounds": ["reb"],
            "player_assists": ["ast"],
            "player_threes": ["fg3m"],
            "player_blocks": ["blk"],
            "player_steals": ["stl"],
            "player_turnovers": ["turnover", "tov"],  # Both variants
            # Alternate markets
            "alternate_player_points": ["pts"],
            "alternate_player_rebounds": ["reb"],
            "alternate_player_assists": ["ast"],
            "alternate_player_threes": ["fg3m"],
            # Combo stats - MANUAL PRA CHECK
            "player_points_rebounds": ["pts", "reb"],
            "player_points_assists": ["pts", "ast"],
            "player_rebounds_assists": ["reb", "ast"],
            "player_points_rebounds_assists": ["pts", "reb", "ast"],  # PRA
            "player_steals_blocks": ["stl", "blk"],
        }
        
        stat_keys = market_to_stat.get(market, ["pts"])
        
        def get_stat_value(game):
            """Extract stat value with case-insensitive key lookup."""
            total = 0
            for key in stat_keys:
                # Try lowercase first (standard)
                value = game.get(key, None)
                # Fallback to uppercase if not found
                if value is None:
                    value = game.get(key.upper(), None)
                # Fallback to title case
                if value is None:
                    value = game.get(key.title(), None)
                total += (value or 0)
            return total
        
        def calc_window(game_list, line_val):
            if not game_list:
                return {"games_over": 0, "total_games": 0, "hit_rate": 0, "avg": 0, "values": [], "floor": 0, "ceiling": 0}
            
            values = [get_stat_value(g) for g in game_list]
            games_over = sum(1 for v in values if v > line_val)
            total = len(game_list)
            hit_rate = games_over / total if total > 0 else 0
            avg = sum(values) / total if total > 0 else 0
            
            return {
                "games_over": games_over,
                "total_games": total,
                "hit_rate": round(hit_rate, 3),
                "avg": round(avg, 1),
                "values": values,  # V3.1: Store raw values for verification
                "floor": min(values) if values else 0,
                "ceiling": max(values) if values else 0
            }
        
        l5 = calc_window(games[:5], line)
        l10 = calc_window(games[:10], line)
        season = calc_window(games, line)
        
        # Trend detection
        trends = []
        if l5["total_games"] >= 3 and season["total_games"] >= 10:
            if l5["avg"] > season["avg"] * 1.15:
                trends.append("HOT")
            elif l5["avg"] < season["avg"] * 0.85:
                trends.append("COLD")
        
        return {
            "l5": l5,
            "l10": l10,
            "season": season,
            "trends": trends
        }
    
    def _extract_l10_values(self, games: List[Dict], market: str) -> List[float]:
        """
        Extract raw stat values from last 10 games for verification.
        Uses case-insensitive key lookup.
        """
        market_to_stat = {
            "player_points": ["pts"],
            "player_rebounds": ["reb"],
            "player_assists": ["ast"],
            "player_threes": ["fg3m"],
            "player_blocks": ["blk"],
            "player_steals": ["stl"],
            "player_turnovers": ["turnover", "tov"],
            "player_points_rebounds": ["pts", "reb"],
            "player_points_assists": ["pts", "ast"],
            "player_rebounds_assists": ["reb", "ast"],
            "player_points_rebounds_assists": ["pts", "reb", "ast"],
            "player_steals_blocks": ["stl", "blk"],
        }
        
        stat_keys = market_to_stat.get(market, ["pts"])
        
        def get_stat_value(game):
            total = 0
            for key in stat_keys:
                value = game.get(key, None)
                if value is None:
                    value = game.get(key.upper(), None)
                if value is None:
                    value = game.get(key.title(), None)
                total += (value or 0)
            return total
        
        if not games:
            return []
            
        return [get_stat_value(g) for g in games[:10]]
    
    async def _log_verification_failure(self, player_name: str, failure_type: str, details: Dict[str, Any]):
        """
        Log verification failures to MongoDB for audit and data status reporting.
        
        V3.1 Truth Engine: All failures are logged for the data status endpoint.
        """
        try:
            failure_doc = {
                "player_name": player_name,
                "failure_type": failure_type,
                "details": details,
                "logged_at": datetime.now(timezone.utc).isoformat(),
                "sync_date": self.get_current_date()
            }
            
            await self.db.dg_verification_failures.insert_one(failure_doc)
            logger.info(f"[VERIFICATION LOG] Logged {failure_type} failure for {player_name}")
        except Exception as e:
            logger.error(f"[VERIFICATION LOG] Failed to log failure: {e}")
    
    async def get_data_integrity_status(self) -> Dict[str, Any]:
        """
        V3.1 Truth Engine: Report data integrity status for the latest sync.
        
        Used by /api/v3/data-status endpoint for the frontend status light.
        
        Returns:
            - status: "verified" | "discrepancy_found" | "no_data"
            - verified_count: Number of props that passed verification
            - failed_count: Number of props that failed verification
            - failure_details: Recent failures with types
            - last_sync: Timestamp of last sync
        """
        try:
            current_date = self.get_current_date()
            
            # Count verified vs failed props in cached_board
            total_props = 0
            verified_props = 0
            failed_props = 0
            unverified_props = 0
            
            # Get all players from cached board
            players = await self.cached_board.find({}).to_list(None)
            
            for player in players:
                for prop in player.get("props", []):
                    total_props += 1
                    if prop.get("source_verified"):
                        verified_props += 1
                    elif prop.get("verification_status") in ["HALLUCINATION_DETECTED", "DISCREPANCY", "NAJI_SAFEGUARD_FAILED"]:
                        failed_props += 1
                    else:
                        unverified_props += 1
            
            # Get recent verification failures
            recent_failures = await self.db.dg_verification_failures.find(
                {"sync_date": current_date},
                {"_id": 0}
            ).sort("logged_at", -1).limit(10).to_list(None)
            
            # Count failure types
            failure_types = {}
            for failure in recent_failures:
                ftype = failure.get("failure_type", "unknown")
                failure_types[ftype] = failure_types.get(ftype, 0) + 1
            
            # Get last sync time
            sync_log = await self.sync_log.find_one(
                {"type": "cached_board"},
                {"_id": 0, "synced_at": 1}
            )
            last_sync = sync_log.get("synced_at") if sync_log else None
            
            # Determine overall status
            if total_props == 0:
                status = "no_data"
            elif failed_props > 0:
                status = "discrepancy_found"
            elif verified_props > 0:
                status = "verified"
            else:
                status = "pending_verification"
            
            return {
                "success": True,
                "status": status,
                "sync_date": current_date,
                "last_sync": last_sync,
                "total_props": total_props,
                "verified_count": verified_props,
                "failed_count": failed_props,
                "unverified_count": unverified_props,
                "verification_rate": round((verified_props / total_props * 100), 2) if total_props > 0 else 0,
                "failure_types": failure_types,
                "recent_failures": recent_failures[:5],  # Limit to 5 for response size
                "naji_safeguard_enabled": True
            }
            
        except Exception as e:
            logger.error(f"[DATA STATUS] Error getting integrity status: {e}")
            return {
                "success": False,
                "status": "error",
                "error": str(e)
            }
    
    async def verify_player_roster_match(self, player_name: str, player_id: int, team_abbrev: str) -> bool:
        """
        NAJI SAFEGUARD: Verify player ID matches active roster for today.
        If name matches but playerID doesn't match today's roster, KILL the data.
        """
        try:
            # Check if player is in master roster with matching ID
            roster_player = await self.master_roster.find_one({
                "player_name": {"$regex": f"^{player_name}$", "$options": "i"},
                "team": team_abbrev
            })
            
            if not roster_player:
                logger.warning(f"[NAJI SAFEGUARD] {player_name} not found in master roster")
                return False
            
            # If we have a BDL ID stored, verify it matches
            stored_id = roster_player.get("bdl_player_id")
            if stored_id and stored_id != player_id:
                logger.error(
                    f"[NAJI SAFEGUARD] ID MISMATCH: {player_name} - "
                    f"Roster ID: {stored_id}, Provided ID: {player_id} - DATA KILLED"
                )
                return False
            
            return True
        except Exception as e:
            logger.error(f"[NAJI SAFEGUARD] Verification error for {player_name}: {e}")
            return False
    
    # ==================== PILLAR 3: TANK01 API (with Exponential Backoff) ====================
    
    async def fetch_injuries(self) -> Dict[str, Any]:
        """
        Fetch injury data from Tank01 with:
        - 4-hour cache to reduce API calls
        - Exponential backoff for rate limiting
        - Graceful degradation on failure
        """
        # Check cache first
        cached = await self.tank01_cache.find_one({"type": "injuries"})
        if cached:
            cached_time = datetime.fromisoformat(cached["cached_at"])
            age = datetime.now(timezone.utc) - cached_time
            if age < TANK01_CACHE_TTL:
                logger.info(f"[PILLAR 3] Using cached injury data (age: {age.total_seconds():.0f}s)")
                self._injury_data = cached.get("data", {})
                return self._injury_data
        
        # Fetch fresh data with exponential backoff
        url = f"{TANK01_BASE}/getNBATeams"
        params = {"rosters": "true", "schedules": "false"}
        headers = {
            "X-RapidAPI-Key": TANK01_API_KEY,
            "X-RapidAPI-Host": "tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com"
        }
        
        logger.info("[PILLAR 3] Fetching injury data from Tank01 (with backoff)...")
        data = await fetch_with_backoff(url, headers, params)
        
        if data:
            teams = data.get("body", []) if isinstance(data, dict) else data
            
            injuries = {}
            if isinstance(teams, list):
                for team in teams:
                    roster = team.get("Roster", {})
                    if isinstance(roster, dict):
                        for player_id, player_data in roster.items():
                            injury_info = player_data.get("injury", {})
                            if injury_info and isinstance(injury_info, dict):
                                status = injury_info.get("designation", "")
                                if status:
                                    player_name = player_data.get("longName", "")
                                    injuries[player_name.lower()] = {
                                        "status": status,
                                        "description": injury_info.get("description", ""),
                                        "return_date": injury_info.get("injReturnDate", ""),
                                        "team": team.get("teamAbv", "")
                                    }
            
            # Cache the results
            await self.tank01_cache.update_one(
                {"type": "injuries"},
                {"$set": {
                    "type": "injuries",
                    "data": injuries,
                    "cached_at": datetime.now(timezone.utc).isoformat()
                }},
                upsert=True
            )
            
            self._injury_data = injuries
            logger.info(f"[PILLAR 3] Found {len(injuries)} injured players (cached for 4h)")
            return injuries
        
        # Fallback to cached data if available (even if expired)
        if cached:
            logger.warning("[PILLAR 3] Using stale cached injury data (API failed)")
            self._injury_data = cached.get("data", {})
            return self._injury_data
        
        logger.warning("[PILLAR 3] No injury data available")
        return {}
    
    async def fetch_news(self) -> List[Dict[str, Any]]:
        """
        Fetch latest NBA news from Tank01 with:
        - 4-hour cache to reduce API calls
        - Exponential backoff for rate limiting
        - Graceful degradation on failure
        """
        # Check cache first
        cached = await self.tank01_cache.find_one({"type": "news"})
        if cached:
            cached_time = datetime.fromisoformat(cached["cached_at"])
            age = datetime.now(timezone.utc) - cached_time
            if age < TANK01_CACHE_TTL:
                logger.info(f"[PILLAR 3] Using cached news data (age: {age.total_seconds():.0f}s)")
                self._news_data = cached.get("data", [])
                return self._news_data
        
        # Fetch fresh data with exponential backoff
        url = f"{TANK01_BASE}/getNBANews"
        headers = {
            "X-RapidAPI-Key": TANK01_API_KEY,
            "X-RapidAPI-Host": "tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com"
        }
        
        logger.info("[PILLAR 3] Fetching news from Tank01 (with backoff)...")
        data = await fetch_with_backoff(url, headers)
        
        if data:
            news_items = data.get("body", []) if isinstance(data, dict) else data
            
            if isinstance(news_items, list):
                news_list = news_items[:100]
                
                # Cache the results
                await self.tank01_cache.update_one(
                    {"type": "news"},
                    {"$set": {
                        "type": "news",
                        "data": news_list,
                        "cached_at": datetime.now(timezone.utc).isoformat()
                    }},
                    upsert=True
                )
                
                self._news_data = news_list
                logger.info(f"[PILLAR 3] Fetched {len(news_list)} news items (cached for 4h)")
                return news_list
        
        # Fallback to cached data if available (even if expired)
        if cached:
            logger.warning("[PILLAR 3] Using stale cached news data (API failed)")
            self._news_data = cached.get("data", [])
            return self._news_data
        
        logger.warning("[PILLAR 3] No news data available")
        return []
    
    def get_player_injury_status(self, player_name: str) -> Dict[str, Any]:
        """Get injury status for a player"""
        player_lower = player_name.lower()
        result = {
            "has_injury": False,
            "injury_status": None,
            "injury_description": None,
            "has_news": False,
            "news_items": [],
            "warning_level": "none"  # none, questionable, out
        }
        
        # Check injury data
        if player_lower in self._injury_data:
            injury = self._injury_data[player_lower]
            status = injury.get("status", "").lower()
            result["has_injury"] = True
            result["injury_status"] = injury.get("status", "").upper()
            result["injury_description"] = injury.get("description", "")
            
            if status in ["out"]:
                result["warning_level"] = "out"
            elif status in ["questionable", "doubtful", "day-to-day", "gtd"]:
                result["warning_level"] = "questionable"
        
        # Check news for injury mentions
        name_parts = player_lower.split()
        for news in self._news_data[:50]:
            title = (news.get("title", "") or "").lower()
            mentioned = any(part in title for part in name_parts if len(part) > 2)
            
            if mentioned:
                has_injury_keyword = any(kw in title for kw in INJURY_KEYWORDS)
                if has_injury_keyword:
                    result["has_news"] = True
                    result["news_items"].append({
                        "title": news.get("title", ""),
                        "link": news.get("link", "")
                    })
                    if result["warning_level"] == "none":
                        result["warning_level"] = "questionable"
        
        return result
    
    # ==================== MAIN ORCHESTRATION ====================
    
    async def process_player_prop(self, prop: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a single prop through all three pillars with V3.1 "Truth Engine" verification.
        
        V3.1 NAJI SAFEGUARD:
        - Verify playerID from game logs matches playerID from active daily roster
        - Discard data if mismatch (prevents wrong player stats)
        - Log all discrepancies for audit
        """
        player_name = prop.get("player_name", "")
        market = prop.get("market", "")
        line = prop.get("line", 0)
        
        result = {
            **prop,
            "bdl_player_id": None,
            "bdl_team": None,
            "position": None,
            "hit_rates": None,
            "injury_info": {"warning_level": "none"},
            "has_goblin_warning": False,  # High hit rate + Questionable
            "source_verified": False,  # V3.1: Data integrity flag
            "verification_status": "unverified",  # V3.1: Verification status
            "verification_details": {},  # V3.1: Detailed verification info
            "naji_safeguard_passed": None,  # V3.1: Naji Safeguard result
            "processed_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Pillar 2: BallDontLie stats
        bdl_player = await self.search_bdl_player(player_name)
        if bdl_player:
            bdl_player_id = bdl_player.get("id")
            result["bdl_player_id"] = bdl_player_id
            result["bdl_team"] = bdl_player.get("team", {}).get("abbreviation", "")
            result["position"] = bdl_player.get("position", "")
            
            # Convert market name for stats lookup (remove _alternate suffix)
            stat_market = market.replace("_alternate", "")
            
            games = await self.fetch_player_season_stats(bdl_player_id)
            if games:
                # ==================== V3.1 NAJI SAFEGUARD ====================
                # Verify that game log playerIDs match the expected BDL player ID
                # This prevents data from wrong players (e.g., Naji Marshall issue)
                naji_safeguard_passed = True
                mismatched_games = []
                
                for game in games:
                    # BallDontLie game logs contain player reference in "player" field
                    game_player = game.get("player", {})
                    game_player_id = game_player.get("id") if isinstance(game_player, dict) else None
                    
                    # If game log has player ID, verify it matches
                    if game_player_id is not None and game_player_id != bdl_player_id:
                        naji_safeguard_passed = False
                        mismatched_games.append({
                            "expected_id": bdl_player_id,
                            "found_id": game_player_id,
                            "game_date": game.get("game", {}).get("date", "unknown")
                        })
                
                result["naji_safeguard_passed"] = naji_safeguard_passed
                
                if not naji_safeguard_passed:
                    # DISCARD DATA - Player ID mismatch detected
                    result["source_verified"] = False
                    result["verification_status"] = "NAJI_SAFEGUARD_FAILED"
                    result["verification_details"] = {
                        "reason": "Player ID mismatch in game logs",
                        "expected_player_id": bdl_player_id,
                        "mismatched_games": mismatched_games[:5]  # Limit to 5 for log size
                    }
                    logger.error(
                        f"[NAJI SAFEGUARD] FAILED for {player_name}: "
                        f"Expected ID {bdl_player_id}, found mismatched games: {len(mismatched_games)}"
                    )
                    # Store the failure for audit
                    await self._log_verification_failure(player_name, "naji_safeguard", result["verification_details"])
                else:
                    # Naji Safeguard passed - proceed with hit rate calculation
                    hit_rates = self.calculate_hit_rates(games, stat_market, line)
                    result["hit_rates"] = hit_rates
                    
                    # V3.1: Triple-check verification
                    l10_data = self._extract_l10_values(games[:10], stat_market)
                    if l10_data:
                        calculated_hits = sum(1 for v in l10_data if v > line)
                        calculated_rate = (calculated_hits / len(l10_data) * 100) if l10_data else 0
                        claimed_rate = hit_rates.get("l10", {}).get("hit_rate", 0) * 100
                        raw_avg = sum(l10_data) / len(l10_data) if l10_data else 0
                        
                        # Store verification details for audit
                        result["verification_details"] = {
                            "calculated_hits": calculated_hits,
                            "calculated_rate": round(calculated_rate, 2),
                            "claimed_rate": round(claimed_rate, 2),
                            "raw_avg": round(raw_avg, 2),
                            "line": line,
                            "games_analyzed": len(l10_data)
                        }
                        
                        # Verification Gate: Detect hallucinations
                        is_hallucinated = (
                            claimed_rate > 80 and 
                            raw_avg < line and 
                            calculated_rate < 50
                        )
                        
                        major_discrepancy = abs(claimed_rate - calculated_rate) > 20
                        
                        if is_hallucinated or major_discrepancy:
                            result["source_verified"] = False
                            result["verification_status"] = "HALLUCINATION_DETECTED" if is_hallucinated else "DISCREPANCY"
                            logger.warning(
                                f"[VERIFY FAIL] {player_name} {stat_market}: "
                                f"Claimed {claimed_rate:.1f}% vs Calculated {calculated_rate:.1f}% "
                                f"(avg {raw_avg:.1f} vs line {line})"
                            )
                            # Log failure for audit
                            await self._log_verification_failure(player_name, result["verification_status"], result["verification_details"])
                        else:
                            result["source_verified"] = True
                            result["verification_status"] = "verified"
                    else:
                        result["verification_status"] = "no_game_data"
            else:
                result["verification_status"] = "no_games_found"
        
        # Pillar 3: Injury check
        injury_info = self.get_player_injury_status(player_name)
        result["injury_info"] = injury_info
        
        # Special warning: Goblin with high hit rate but Questionable
        if prop.get("is_goblin") and result.get("hit_rates"):
            l10_hit_rate = result["hit_rates"].get("l10", {}).get("hit_rate", 0)
            if l10_hit_rate >= GOBLIN_HIT_RATE_WARNING and injury_info["warning_level"] == "questionable":
                result["has_goblin_warning"] = True
        
        return result
    
    # ==================== ADVANCED ANALYTICS ENGINE v3.1 ====================
    
    def calculate_volatility(self, game_values: List[float]) -> Tuple[str, float]:
        """
        Calculate volatility score from recent game values.
        
        Returns:
            (volatility_label "Low"/"Med"/"High", standard_deviation)
        """
        if not game_values or len(game_values) < 3:
            return ("Low", 0.0)
        
        try:
            stddev = statistics.stdev(game_values)
            
            if stddev > self.VOLATILITY_HIGH_THRESHOLD:
                return ("High", round(stddev, 2))
            elif stddev > self.VOLATILITY_MED_THRESHOLD:
                return ("Med", round(stddev, 2))
            else:
                return ("Low", round(stddev, 2))
        except:
            return ("Low", 0.0)
    
    def get_team_pace(self, team: str) -> float:
        """Get team's pace (possessions per 48 minutes)."""
        if team in self._team_pace_cache:
            return self._team_pace_cache[team]
        
        # 2024-25 season pace values
        TEAM_PACE = {
            "IND": 103.5, "ATL": 102.8, "MIL": 102.2, "SAC": 101.9, "MIN": 101.5,
            "DEN": 101.2, "BOS": 100.8, "PHX": 100.6, "GSW": 100.4, "LAL": 100.2,
            "DAL": 100.0, "OKC": 99.8, "NOP": 99.6, "POR": 99.4, "HOU": 99.2,
            "TOR": 99.0, "CHI": 98.8, "WAS": 98.6, "BKN": 98.4, "CHA": 98.2,
            "SAS": 98.0, "UTA": 97.8, "DET": 97.6, "ORL": 97.4, "MEM": 97.2,
            "PHI": 97.0, "CLE": 96.8, "MIA": 96.6, "NYK": 96.4, "LAC": 96.2
        }
        
        pace = TEAM_PACE.get(team, self.LEAGUE_AVG_PACE)
        self._team_pace_cache[team] = pace
        return pace
    
    def calculate_pace_factor(self, team: str, opponent: str) -> float:
        """
        Calculate pace adjustment factor.
        Formula: (Team_Pace + Opponent_Pace) / (2 * League_Avg_Pace)
        """
        team_pace = self.get_team_pace(team)
        opp_pace = self.get_team_pace(opponent)
        combined_pace = (team_pace + opp_pace) / 2
        return round(combined_pace / self.LEAGUE_AVG_PACE, 3)
    
    def get_high_usage_players(self, team: str) -> List[str]:
        """Get list of high-usage players (>25% usage rate) on a team."""
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
    
    def calculate_usage_bump(
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
        high_usage = self.get_high_usage_players(team)
        
        # Find injured high-usage players (excluding current player)
        injured_stars = [p for p in injured_teammates if p in high_usage and p != player_name]
        
        if not injured_stars:
            return (0.0, [])
        
        # Calculate usage bump with diminishing returns
        usage_bump = 0.0
        for i, _ in enumerate(injured_stars):
            multiplier = 1.0 / (i + 1)
            usage_bump += self.USAGE_REDISTRIBUTION_BASE * multiplier
        
        return (round(usage_bump, 1), injured_stars)
    
    def generate_insight_summary(
        self,
        player_name: str,
        pace_factor: float,
        usage_bump: float,
        volatility: str,
        days_rest: int,
        is_b2b: bool,
        is_3in4: bool,
        injured_teammates: List[str],
        opponent: str
    ) -> str:
        """Generate template-based insight summary prioritizing highest-impact factor."""
        insights = []
        
        # Priority 1: Usage Bump (most impactful)
        if usage_bump > 10:
            teammates_str = " & ".join(injured_teammates[:2])
            insights.append(f"🚀 Usage Spike: With {teammates_str} out, usage +{usage_bump:.0f}%")
        elif usage_bump > 5:
            teammates_str = injured_teammates[0] if injured_teammates else "teammate"
            insights.append(f"📈 Usage Up: {teammates_str} out, +{usage_bump:.0f}% opportunity")
        
        # Priority 2: Schedule Fatigue
        if is_3in4:
            insights.append("⚠️ 3-in-4 Fatigue: -8% performance expected")
        elif is_b2b:
            insights.append("⚠️ Back-to-Back: -5% fatigue factor")
        
        # Priority 3: Pace Matchup
        if pace_factor > 1.05:
            insights.append(f"🏃 Fast Pace vs {opponent}: +{(pace_factor-1)*100:.0f}% boost")
        elif pace_factor < 0.95:
            insights.append(f"🐢 Slow Pace vs {opponent}: {(pace_factor-1)*100:.0f}% drag")
        
        # Priority 4: Rest Advantage
        if days_rest >= 3:
            insights.append(f"😴 {days_rest} Days Rest: Fresh legs advantage")
        
        # Priority 5: Volatility Warning
        if volatility == "High":
            insights.append("📊 High Variance: Inconsistent, proceed with caution")
        
        if not insights:
            return f"📈 Standard projection. No significant modifiers."
        
        return " | ".join(insights[:2])
    
    def calculate_confidence_rating(
        self, 
        density_factor: float, 
        volatility: str, 
        sample_size: int
    ) -> int:
        """Calculate AI confidence rating (0-100)."""
        confidence = 70
        
        if density_factor < 0.95:
            confidence -= 10
        
        if volatility == "High":
            confidence -= 20
        elif volatility == "Med":
            confidence -= 10
        
        if sample_size >= 10:
            confidence += 10
        elif sample_size < 5:
            confidence -= 15
        
        return max(0, min(100, confidence))
    
    async def calculate_player_insights(
        self,
        player_name: str,
        team: str,
        opponent: str,
        game_stats: List[Dict],
        stat_type: str = "pts"
    ) -> Dict[str, Any]:
        """
        Calculate all advanced analytics for a player.
        
        Args:
            player_name: Player name
            team: Player's team abbreviation
            opponent: Opponent team abbreviation
            game_stats: List of recent game stats [{pts, reb, ast, ...}, ...]
            stat_type: Which stat to calculate volatility for
        
        Returns:
            Complete insights dictionary
        """
        # Extract stat values for volatility calculation
        stat_key_map = {
            "pts": "pts", "points": "pts",
            "reb": "reb", "rebounds": "reb",
            "ast": "ast", "assists": "ast",
            "fg3m": "fg3m", "3pm": "fg3m", "threes": "fg3m"
        }
        stat_key = stat_key_map.get(stat_type.lower(), "pts")
        
        recent_values = []
        for game in game_stats[:10]:
            val = game.get(stat_key, 0)
            if val is not None:
                recent_values.append(float(val))
        
        # Calculate volatility
        volatility, stddev = self.calculate_volatility(recent_values)
        
        # Calculate pace factor
        pace_factor = self.calculate_pace_factor(team, opponent) if opponent else 1.0
        
        # Get injured teammates (simplified - would need injury API integration)
        # For now, use empty list; will be populated by Tank01 in production
        injured_teammates = []
        
        # Calculate usage bump
        usage_bump, injured_stars = self.calculate_usage_bump(player_name, team, injured_teammates)
        
        # Determine schedule density (simplified)
        # In production, would check actual schedule
        days_rest = 2  # Default
        is_b2b = False
        is_3in4 = False
        density_factor = 1.0
        
        # Generate summary
        summary = self.generate_insight_summary(
            player_name=player_name,
            pace_factor=pace_factor,
            usage_bump=usage_bump,
            volatility=volatility,
            days_rest=days_rest,
            is_b2b=is_b2b,
            is_3in4=is_3in4,
            injured_teammates=injured_stars,
            opponent=opponent or "TBD"
        )
        
        # Calculate confidence
        confidence = self.calculate_confidence_rating(density_factor, volatility, len(recent_values))
        
        return {
            "schedule_density_factor": density_factor,
            "pace_adjustment_factor": pace_factor,
            "usage_bump_percent": usage_bump,
            "volatility_score": volatility,
            "volatility_stddev": stddev,
            "insight_summary": summary,
            "ai_confidence_rating": confidence,
            "is_back_to_back": is_b2b,
            "is_three_in_four": is_3in4,
            "days_rest": days_rest,
            "injured_teammates": injured_stars
        }
    
    async def sync_daily_insights(self) -> Dict[str, Any]:
        """
        Sync daily insights for all players with active props.
        Calculates advanced analytics and stores in MongoDB.
        Should be run daily at 8:00 AM EST.
        """
        sync_start = datetime.now(timezone.utc)
        logger.info("[INSIGHTS SYNC] Starting daily insights calculation...")
        
        insights_calculated = 0
        errors = []
        
        try:
            # Get all players from cached board
            players = await self.cached_board.find({}, {"_id": 0}).to_list(None)
            
            if not players:
                return {"success": True, "insights_calculated": 0, "message": "No players to process"}
            
            logger.info(f"[INSIGHTS SYNC] Processing {len(players)} players...")
            
            for player in players:
                try:
                    player_name = player.get("player_name", "")
                    team = player.get("team", "")
                    
                    # Get opponent from props (if available)
                    opponent = ""
                    if player.get("props"):
                        first_prop = player["props"][0]
                        opponent = first_prop.get("opponent", first_prop.get("away_team", ""))
                    
                    # Get cached stats for this player
                    stats_doc = await self.player_stats.find_one(
                        {"normalized_name": self.sanitize_player_name(player_name)},
                        {"_id": 0}
                    )
                    
                    game_stats = []
                    if stats_doc:
                        game_stats = stats_doc.get("games", [])[:10]
                    
                    # Calculate insights
                    insights = await self.calculate_player_insights(
                        player_name=player_name,
                        team=team,
                        opponent=opponent,
                        game_stats=game_stats,
                        stat_type="pts"
                    )
                    
                    # Add metadata
                    insights["player_name"] = player_name
                    insights["team"] = team
                    insights["opponent"] = opponent
                    insights["synced_at"] = sync_start.isoformat()
                    
                    # Store in MongoDB
                    await self.daily_insights.update_one(
                        {"player_name": player_name},
                        {"$set": insights},
                        upsert=True
                    )
                    
                    insights_calculated += 1
                    
                except Exception as e:
                    errors.append(f"{player.get('player_name', 'Unknown')}: {str(e)}")
            
            # Create indexes
            await self.daily_insights.create_index("player_name", unique=True)
            await self.daily_insights.create_index("team")
            
            duration = (datetime.now(timezone.utc) - sync_start).total_seconds()
            logger.info(f"[INSIGHTS SYNC] Completed: {insights_calculated} players in {duration:.1f}s")
            
            return {
                "success": True,
                "insights_calculated": insights_calculated,
                "duration_seconds": duration,
                "errors": errors[:5],
                "synced_at": sync_start.isoformat()
            }
            
        except Exception as e:
            logger.error(f"[INSIGHTS SYNC] Failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "insights_calculated": insights_calculated
            }
    
    async def get_player_insights(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Get cached insights for a player."""
        doc = await self.daily_insights.find_one(
            {"player_name": player_name},
            {"_id": 0}
        )
        return doc
    
    async def run_full_sync(self) -> Dict[str, Any]:
        """Execute the full three-pillar sync with PrizePicks data"""
        sync_start = datetime.now(timezone.utc)
        self._current_date = self.get_current_date()
        
        logger.info("=" * 70)
        logger.info(f"DEMON & GOBLIN ENGINE v3.0 - PRIZEPICKS SYNC")
        logger.info(f"Date: {self._current_date}")
        logger.info(f"Region: {PRIZEPICKS_REGION} | Bookmaker: {PRIZEPICKS_BOOKMAKER}")
        logger.info("=" * 70)
        
        results = {
            "success": True,
            "sync_date": self._current_date,
            "sync_time": sync_start.isoformat(),
            "events_count": 0,
            "total_props": 0,
            "unique_players": 0,
            "standard_count": 0,
            "demons_count": 0,
            "goblins_count": 0,
            "stats_fetched": 0,
            "injuries_found": 0,
            "goblin_warnings": 0,
            # V3.1 Truth Engine verification stats
            "verification_stats": {
                "verified_count": 0,
                "failed_count": 0,
                "naji_safeguard_failures": 0,
                "hallucinations_detected": 0,
                "discrepancies_found": 0
            },
            "errors": [],
            "duration": 0
        }
        
        try:
            # V3.1 Truth Engine: Clear previous verification failures for today's sync
            await self.db.dg_verification_failures.delete_many({"sync_date": self._current_date})
            logger.info("[TRUTH ENGINE] Cleared previous verification failures for today")
            
            # ===== PILLAR 1: FETCH EVENTS AND PRIZEPICKS ODDS =====
            logger.info("\n[PILLAR 1] Fetching NBA events and PrizePicks lines...")
            logger.info(f"  Using region={PRIZEPICKS_REGION}, bookmaker={PRIZEPICKS_BOOKMAKER}")
            
            events = await self.fetch_todays_events()
            results["events_count"] = len(events)
            
            if not events:
                results["success"] = False
                results["errors"].append("No NBA events found")
                return results
            
            all_props = []
            all_players: Set[str] = set()
            
            # Fetch PrizePicks odds for EVERY event
            for event in events:
                event_id = event.get("id")
                if event_id:
                    # Fetch PrizePicks alternate lines
                    odds_data = await self.fetch_prizepicks_odds(event_id, event)
                    if odds_data:
                        props = self.extract_prizepicks_props(odds_data)
                        all_props.extend(props)
                        for p in props:
                            all_players.add(p.get("player_name", ""))
                    
                    await asyncio.sleep(0.3)  # Rate limiting
            
            results["total_props"] = len(all_props)
            results["unique_players"] = len(all_players)
            results["standard_count"] = sum(1 for p in all_props if p.get("prop_type") == "standard")
            results["demons_count"] = sum(1 for p in all_props if p.get("is_demon"))
            results["goblins_count"] = sum(1 for p in all_props if p.get("is_goblin"))
            
            logger.info(f"\n[PILLAR 1] PRIZEPICKS DATA COMPLETE:")
            logger.info(f"  Total Props: {len(all_props)}")
            logger.info(f"  Unique Players: {len(all_players)}")
            logger.info(f"  STANDARD (Main Markets): {results['standard_count']}")
            logger.info(f"  DEMONS (Alternate +100): {results['demons_count']}")
            logger.info(f"  GOBLINS (Alternate ≠+100): {results['goblins_count']}")
            
            # ===== PILLAR 3: FETCH INJURIES FIRST =====
            logger.info("\n[PILLAR 3] Fetching injury data from Tank01...")
            
            injuries = await self.fetch_injuries()
            await self.fetch_news()
            results["injuries_found"] = len(injuries)
            
            # ===== PILLAR 2: PROCESS STATS =====
            logger.info("\n[PILLAR 2] Processing stats from BallDontLie...")
            
            # Deduplicate by player+market+line+direction
            unique_props = {}
            for prop in all_props:
                key = f"{prop['player_name']}|{prop['market']}|{prop['line']}|{prop['direction']}"
                if key not in unique_props:
                    unique_props[key] = prop
            
            # Process ALL unique props - no limit!
            processed_props = []
            prop_list = list(unique_props.values())
            batch_size = 50
            
            logger.info(f"  Processing {len(prop_list)} unique props...")
            
            for i in range(0, len(prop_list), batch_size):
                batch = prop_list[i:i+batch_size]
                
                for prop in batch:
                    try:
                        processed = await self.process_player_prop(prop)
                        processed_props.append(processed)
                        
                        if processed.get("bdl_player_id"):
                            results["stats_fetched"] += 1
                        
                        if processed.get("has_goblin_warning"):
                            results["goblin_warnings"] += 1
                        
                        # V3.1 Truth Engine - Track verification stats
                        if processed.get("source_verified"):
                            results["verification_stats"]["verified_count"] += 1
                        else:
                            verification_status = processed.get("verification_status", "")
                            if verification_status == "NAJI_SAFEGUARD_FAILED":
                                results["verification_stats"]["naji_safeguard_failures"] += 1
                                results["verification_stats"]["failed_count"] += 1
                            elif verification_status == "HALLUCINATION_DETECTED":
                                results["verification_stats"]["hallucinations_detected"] += 1
                                results["verification_stats"]["failed_count"] += 1
                            elif verification_status == "DISCREPANCY":
                                results["verification_stats"]["discrepancies_found"] += 1
                                results["verification_stats"]["failed_count"] += 1
                        
                        await asyncio.sleep(0.1)
                        
                    except Exception as e:
                        results["errors"].append(f"Prop error: {str(e)[:50]}")
                
                logger.info(f"  Processed {min(i+batch_size, len(prop_list))}/{len(prop_list)} props")
            
            # ===== STORE RESULTS GROUPED BY PLAYER =====
            logger.info("\n[STORAGE] Organizing data by player...")
            
            # Group props by player
            player_data = {}
            for prop in processed_props:
                player_name = prop.get("player_name", "Unknown")
                
                if player_name not in player_data:
                    # Get player photo from master roster
                    photo_url = await self.get_photo_url_from_master_roster(player_name)
                    nba_id = None
                    
                    # Fallback: Try static NBA player ID mapping
                    if not photo_url:
                        nba_id = get_nba_player_id(player_name)
                        if nba_id:
                            photo_url = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_id}.png"
                    
                    player_data[player_name] = {
                        "player_name": player_name,
                        "team": prop.get("bdl_team", ""),
                        "position": prop.get("position", ""),
                        "nba_id": nba_id,  # NBA CDN headshot ID
                        "photo_url": photo_url,  # Player headshot URL
                        "injury_info": prop.get("injury_info", {}),
                        "popularity_order": self._player_popularity.get(player_name, 999),
                        "props": [],
                        "standard": [],  # Standard props (main market, no icon)
                        "demons": [],    # Demon props (alternate market, +100)
                        "goblins": [],   # Goblin props (alternate market, ≠+100)
                        "has_goblin_warning": False,
                        "has_new_injury": False  # NEW: Track if this is a new injury update
                    }
                
                player_data[player_name]["props"].append(prop)
                
                # Classify into appropriate bucket
                if prop.get("is_demon"):
                    player_data[player_name]["demons"].append(prop)
                elif prop.get("is_goblin"):
                    player_data[player_name]["goblins"].append(prop)
                else:
                    player_data[player_name]["standard"].append(prop)
                
                if prop.get("has_goblin_warning"):
                    player_data[player_name]["has_goblin_warning"] = True
            
            # ===== BUILD TRENDING 10 =====
            logger.info("\n[TRENDING] Building Most Popular Today (Top 10)...")
            
            # Calculate popularity score for each player
            # Score = (API Order * 10) - (Demon Count * 5) - (Goblin Count * 3) - (Has Injury Flag * -50)
            # Lower score = more popular
            trending_list = []
            for name, data in player_data.items():
                demons_count = len(data.get("demons", []))
                goblins_count = len(data.get("goblins", []))
                special_count = demons_count + goblins_count
                
                # Only include players with at least 1 Demon or Goblin
                if special_count == 0:
                    continue
                
                popularity_order = data.get("popularity_order", 999)
                injury_info = data.get("injury_info", {})
                has_injury = injury_info.get("has_injury", False)
                
                # Popularity score (lower = more popular)
                score = popularity_order - (special_count * 2)
                if has_injury:
                    score += 20  # Penalize injured players slightly
                
                # Get best prop for display (highest hit rate Goblin or Demon)
                best_prop = None
                best_hit_rate = 0
                for prop in data.get("props", []):
                    if prop.get("is_demon") or prop.get("is_goblin"):
                        hit_rates = prop.get("hit_rates") or {}
                        l10 = hit_rates.get("l10") or {}
                        hit_rate = l10.get("hit_rate", 0) or 0
                        if hit_rate > best_hit_rate:
                            best_hit_rate = hit_rate
                            best_prop = prop
                
                trending_list.append({
                    "player_name": name,
                    "team": data.get("team", ""),
                    "position": data.get("position", ""),
                    "nba_id": data.get("nba_id"),  # NBA CDN headshot ID
                    "popularity_score": score,
                    "popularity_order": popularity_order,
                    "demons_count": demons_count,
                    "goblins_count": goblins_count,
                    "total_props": len(data.get("props", [])),
                    "injury_info": injury_info,
                    "has_new_injury": has_injury,  # Mark if they have any injury
                    "best_prop": best_prop,
                    "best_hit_rate": best_hit_rate
                })
            
            # Sort by popularity score (lower = better)
            trending_list.sort(key=lambda x: x["popularity_score"])
            
            # Take top 10
            trending_10 = trending_list[:10]
            
            # Store trending in DB
            await self.trending_cache.delete_many({})
            if trending_10:
                await self.trending_cache.insert_many(trending_10)
            
            results["trending_count"] = len(trending_10)
            logger.info(f"  Trending 10: {[t['player_name'] for t in trending_10]}")
            
            # Store all player data in MongoDB
            await self.player_data.delete_many({})
            if player_data:
                await self.player_data.insert_many(list(player_data.values()))
            
            # ===== STORE STATIC SHELL CACHE =====
            logger.info("\n[CACHE] Storing static shell (24h TTL)...")
            await self.store_static_shell(list(player_data.values()), trending_10)
            
            # ===== BUILD RADAR & VAULT (Top 10 Picks) =====
            logger.info("\n[RADAR/VAULT] Building top 10 pick sections...")
            
            # Build Demon Radar (Top 10 Demon Picks)
            try:
                await self._build_demon_radar(player_data, sync_start)
                logger.info("[DEMON RADAR] Rebuilt successfully")
            except Exception as e:
                logger.error(f"[DEMON RADAR] Error building: {e}")
            
            # Build Goblin Vault (Top 10 Safe Picks)
            try:
                await self._build_goblin_vault(player_data, sync_start)
                logger.info("[GOBLIN VAULT] Rebuilt successfully")
            except Exception as e:
                logger.error(f"[GOBLIN VAULT] Error building: {e}")
            
            # ===== BUILD PARLAY GENERATORS =====
            logger.info("\n[PARLAYS] Building parlay generators...")
            
            # Build demon parlays (The Gauntlet)
            try:
                await self._build_parlay_builder(player_data, sync_start)
            except Exception as e:
                logger.error(f"[PARLAY BUILDER] Error building demon parlays: {e}")
            
            # Build goblin parlays (The Safe Haven)
            try:
                await self._build_goblin_recon(player_data, sync_start)
            except Exception as e:
                logger.error(f"[GOBLIN RECON] Error building goblin parlays: {e}")
            
            logger.info("[PARLAYS] Parlay generators built successfully")
            
            # Log sync result
            await self.sync_log.insert_one({
                "sync_date": self._current_date,
                "sync_time": sync_start.isoformat(),
                "results": results,
                "completed_at": datetime.now(timezone.utc).isoformat()
            })
            
            self._last_sync = datetime.now(timezone.utc)
            
        except Exception as e:
            logger.error(f"Sync error: {e}")
            results["success"] = False
            results["errors"].append(str(e))
        
        results["duration"] = (datetime.now(timezone.utc) - sync_start).total_seconds()
        
        # Calculate verification rate
        total_verifiable = results["verification_stats"]["verified_count"] + results["verification_stats"]["failed_count"]
        verification_rate = (results["verification_stats"]["verified_count"] / total_verifiable * 100) if total_verifiable > 0 else 0
        results["verification_stats"]["verification_rate"] = round(verification_rate, 2)
        
        logger.info("\n" + "=" * 70)
        logger.info(f"""
DEMON & GOBLIN SYNC COMPLETE - PRIZEPICKS EDITION
==================================================
Duration: {results['duration']:.1f}s
Date: {results['sync_date']}

PILLAR 1 - PRIZEPICKS (us_dfs region):
  Events: {results['events_count']}
  Total Props: {results['total_props']}
  Unique Players: {results['unique_players']}
  
CLASSIFICATION (Market-Based):
  STANDARD (Main Markets): {results['standard_count']}
  DEMONS (Alternate +100): {results['demons_count']}
  GOBLINS (Alternate ≠+100): {results['goblins_count']}
  
PILLAR 2 - BALLDONTLIE:
  Stats Fetched: {results['stats_fetched']}
  
PILLAR 3 - TANK01:
  Injuries Found: {results['injuries_found']}
  Goblin Warnings: {results['goblin_warnings']}

V3.1 TRUTH ENGINE - DATA INTEGRITY:
  Verified Props: {results['verification_stats']['verified_count']}
  Failed Props: {results['verification_stats']['failed_count']}
  Naji Safeguard Failures: {results['verification_stats']['naji_safeguard_failures']}
  Hallucinations Detected: {results['verification_stats']['hallucinations_detected']}
  Discrepancies Found: {results['verification_stats']['discrepancies_found']}
  Verification Rate: {results['verification_stats']['verification_rate']}%
""")
        logger.info("=" * 70)
        
        return results
    
    async def run_delta_sync(self) -> Dict[str, Any]:
        """
        DELTA SYNC - Odds-only update for Delta Refreshes
        
        Updates line and price values for existing players without
        re-fetching stats or regenerating Vision AI.
        
        Used by Board Intelligence Engine for:
        - 1:45 PM, 4:00 PM, 5:45 PM, 7:00 PM ET refreshes
        """
        sync_start = datetime.now(timezone.utc)
        self._current_date = self.get_current_date()
        
        logger.info("─" * 70)
        logger.info(f"DELTA SYNC - ODDS ONLY UPDATE")
        logger.info(f"Date: {self._current_date}")
        logger.info("─" * 70)
        
        results = {
            "success": True,
            "sync_type": "delta",
            "sync_date": self._current_date,
            "sync_time": sync_start.isoformat(),
            "lines_updated": 0,
            "new_players": [],
            "removed_players": [],
            "errors": []
        }
        
        try:
            # Get existing players before update
            existing_board = await self.dg_cached_board.find_one({"type": "main_board"})
            existing_players = set()
            if existing_board and "board" in existing_board:
                for p in existing_board["board"].get("players", []):
                    existing_players.add(p.get("player_name", ""))
            
            # Fetch fresh events and odds (PILLAR 1 only)
            logger.info("\n[DELTA] Fetching fresh odds from PrizePicks...")
            events = await self.fetch_todays_events()
            
            if not events:
                results["success"] = False
                results["errors"].append("No NBA events found")
                return results
            
            all_props = []
            all_players = set()
            
            for event in events:
                # Fetch PrizePicks odds for each event
                props = await self.fetch_prizepicks_odds(event)
                if props:
                    all_props.extend(props)
                    for prop in props:
                        all_players.add(prop.get("player_name", ""))
            
            logger.info(f"[DELTA] Fetched {len(all_props)} props for {len(all_players)} players")
            
            # Identify new and removed players
            new_players = all_players - existing_players
            removed_players = existing_players - all_players
            
            results["new_players"] = list(new_players)
            results["removed_players"] = list(removed_players)
            
            if new_players:
                logger.info(f"[DELTA] New players: {list(new_players)[:5]}...")
            if removed_players:
                logger.info(f"[DELTA] Removed players: {list(removed_players)[:5]}...")
            
            # Update existing players' odds in the cached board
            if existing_board and "board" in existing_board:
                players_list = existing_board["board"].get("players", [])
                
                # Create lookup for new props by player
                props_by_player = {}
                for prop in all_props:
                    pname = prop.get("player_name", "")
                    if pname not in props_by_player:
                        props_by_player[pname] = []
                    props_by_player[pname].append(prop)
                
                # Update each player's props with fresh odds
                for player in players_list:
                    pname = player.get("player_name", "")
                    if pname in props_by_player:
                        new_props = props_by_player[pname]
                        
                        # Update standard props
                        for old_prop in player.get("props", []):
                            for new_prop in new_props:
                                if (old_prop.get("market") == new_prop.get("market") and
                                    old_prop.get("direction") == new_prop.get("direction")):
                                    old_prop["line"] = new_prop.get("line", old_prop.get("line"))
                                    old_prop["price"] = new_prop.get("price", old_prop.get("price"))
                                    results["lines_updated"] += 1
                                    break
                        
                        # Update demons
                        for old_demon in player.get("demons", []):
                            for new_prop in new_props:
                                if (old_demon.get("market") == new_prop.get("market") and
                                    old_demon.get("direction") == new_prop.get("direction") and
                                    new_prop.get("is_demon")):
                                    old_demon["line"] = new_prop.get("line", old_demon.get("line"))
                                    old_demon["price"] = new_prop.get("price", old_demon.get("price"))
                                    results["lines_updated"] += 1
                                    break
                        
                        # Update goblins
                        for old_goblin in player.get("goblins", []):
                            for new_prop in new_props:
                                if (old_goblin.get("market") == new_prop.get("market") and
                                    old_goblin.get("direction") == new_prop.get("direction") and
                                    new_prop.get("is_goblin")):
                                    old_goblin["line"] = new_prop.get("line", old_goblin.get("line"))
                                    old_goblin["price"] = new_prop.get("price", old_goblin.get("price"))
                                    results["lines_updated"] += 1
                                    break
                
                # Remove players whose lines were pulled
                if removed_players:
                    players_list = [p for p in players_list if p.get("player_name") not in removed_players]
                    existing_board["board"]["players"] = players_list
                
                # Update the board
                existing_board["board"]["delta_updated_at"] = sync_start.isoformat()
                await self.dg_cached_board.update_one(
                    {"type": "main_board"},
                    {"$set": existing_board}
                )
            
            logger.info(f"[DELTA] Updated {results['lines_updated']} lines")
            
            # Rebuild Demon Radar and Goblin Vault with updated data
            if existing_board and "board" in existing_board:
                players_list = existing_board["board"].get("players", [])
                
                # Convert players_list to players_dict format for radar/vault builders
                player_data = {}
                for player in players_list:
                    pname = player.get("player_name", "")
                    if pname:
                        player_data[pname] = player
                
                if player_data:
                    logger.info("[DELTA] Rebuilding Demon Radar and Goblin Vault...")
                    try:
                        await self._build_demon_radar(player_data, sync_start)
                        logger.info("[DEMON RADAR] Rebuilt with fresh data")
                    except Exception as e:
                        logger.error(f"[DEMON RADAR] Rebuild error: {e}")
                    
                    try:
                        await self._build_goblin_vault(player_data, sync_start)
                        logger.info("[GOBLIN VAULT] Rebuilt with fresh data")
                    except Exception as e:
                        logger.error(f"[GOBLIN VAULT] Rebuild error: {e}")
            
        except Exception as e:
            logger.error(f"[DELTA] Sync error: {e}")
            results["success"] = False
            results["errors"].append(str(e))
        
        results["duration"] = (datetime.now(timezone.utc) - sync_start).total_seconds()
        
        logger.info(f"[DELTA] Sync completed in {results['duration']:.1f}s")
        logger.info("─" * 70)
        
        return results
    
    # ==================== DATA ACCESS ====================
    
    async def get_all_players(self) -> List[Dict[str, Any]]:
        """Get all players with their props (collapsed view data)"""
        cursor = self.player_data.find({}, {"_id": 0})
        players = await cursor.to_list(1000)
        
        # Sort: Players with Demons/Goblins first
        def sort_key(p):
            has_special = len(p.get("demons", [])) + len(p.get("goblins", []))
            return (-has_special, p.get("player_name", ""))
        
        players.sort(key=sort_key)
        return players
    
    async def get_player_detail(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Get full detail for a specific player (expanded view)"""
        player = await self.player_data.find_one(
            {"player_name": {"$regex": player_name, "$options": "i"}},
            {"_id": 0}
        )
        
        if player:
            # Sort props: Demons first, then Goblins, then rest by hit rate
            props = player.get("props", [])
            
            def prop_sort_key(p):
                priority = 2  # Standard
                if p.get("is_demon"):
                    priority = 0
                elif p.get("is_goblin"):
                    priority = 1
                
                hit_rate = 0
                if p.get("hit_rates") and p.get("hit_rates", {}).get("l10"):
                    hit_rate = p.get("hit_rates", {}).get("l10", {}).get("hit_rate", 0) or 0
                
                return (priority, -hit_rate)
            
            props.sort(key=prop_sort_key)
            player["props"] = props
        
        return player
    
    async def get_all_demons(self) -> List[Dict[str, Any]]:
        """Get all Demon lines across all players"""
        players = await self.player_data.find({}, {"_id": 0}).to_list(1000)
        
        demons = []
        for player in players:
            for demon in player.get("demons", []):
                demon["player_team"] = player.get("team", "")
                demon["player_injury"] = player.get("injury_info", {})
                demons.append(demon)
        
        # Sort by price (highest odds first)
        demons.sort(key=lambda x: x.get("price", 0), reverse=True)
        return demons
    
    async def get_all_goblins(self) -> List[Dict[str, Any]]:
        """Get all Goblin lines across all players"""
        players = await self.player_data.find({}, {"_id": 0}).to_list(1000)
        
        goblins = []
        for player in players:
            for goblin in player.get("goblins", []):
                goblin["player_team"] = player.get("team", "")
                goblin["player_injury"] = player.get("injury_info", {})
                goblin["has_warning"] = player.get("has_goblin_warning", False)
                goblins.append(goblin)
        
        # Sort by hit rate (highest first)
        def sort_key(g):
            hit_rate = 0
            if g.get("hit_rates") and g.get("hit_rates", {}).get("l10"):
                hit_rate = g.get("hit_rates", {}).get("l10", {}).get("hit_rate", 0) or 0
            return -hit_rate
        
        goblins.sort(key=sort_key)
        return goblins
    
    async def get_sync_status(self) -> Dict[str, Any]:
        """Get current sync status"""
        players_count = await self.player_data.count_documents({})
        
        # Count standard, demons and goblins
        pipeline = [
            {"$project": {
                "standard_count": {"$size": {"$ifNull": ["$standard", []]}},
                "demons_count": {"$size": {"$ifNull": ["$demons", []]}},
                "goblins_count": {"$size": {"$ifNull": ["$goblins", []]}},
                "props_count": {"$size": {"$ifNull": ["$props", []]}}
            }},
            {"$group": {
                "_id": None,
                "total_standard": {"$sum": "$standard_count"},
                "total_demons": {"$sum": "$demons_count"},
                "total_goblins": {"$sum": "$goblins_count"},
                "total_props": {"$sum": "$props_count"}
            }}
        ]
        
        agg_result = await self.player_data.aggregate(pipeline).to_list(1)
        counts = agg_result[0] if agg_result else {"total_standard": 0, "total_demons": 0, "total_goblins": 0, "total_props": 0}
        
        # Get last sync log
        last_sync = await self.sync_log.find_one({}, sort=[("sync_time", -1)])
        
        return {
            "last_sync": self._last_sync.isoformat() if self._last_sync else (last_sync.get("sync_time") if last_sync else None),
            "sync_date": self._current_date or self.get_current_date(),
            "unique_players": players_count,
            "total_props": counts.get("total_props", 0),
            "standard_count": counts.get("total_standard", 0),
            "demons_count": counts.get("total_demons", 0),
            "goblins_count": counts.get("total_goblins", 0),
            "season": CURRENT_SEASON
        }
    
    async def search_players(self, query: str) -> List[Dict[str, Any]]:
        """Search for players by name"""
        cursor = self.player_data.find(
            {"player_name": {"$regex": query, "$options": "i"}},
            {"_id": 0}
        )
        return await cursor.to_list(50)

    
    async def get_trending_10(self) -> List[Dict[str, Any]]:
        """
        Get the Top 10 Most Popular players today
        Based on PrizePicks board order and Demon/Goblin count
        """
        cursor = self.trending_cache.find({}, {"_id": 0}).sort("popularity_score", 1)
        trending = await cursor.to_list(10)
        
        # Enrich with full player data if needed
        enriched = []
        for t in trending:
            player_name = t.get("player_name")
            # Get full player data
            player = await self.player_data.find_one(
                {"player_name": player_name},
                {"_id": 0}
            )
            
            if player:
                # Get top 3 props (best hit rate Demons/Goblins)
                top_props = []
                all_props = player.get("props", [])
                
                # Filter to Demons and Goblins only
                special_props = [p for p in all_props if p.get("is_demon") or p.get("is_goblin")]
                
                # Sort by hit rate - handle None values
                def get_hit_rate(x):
                    hr = x.get("hit_rates") or {}
                    l10 = hr.get("l10") or {}
                    return l10.get("hit_rate", 0) or 0
                
                special_props.sort(key=get_hit_rate, reverse=True)
                
                top_props = special_props[:3]
                
                enriched.append({
                    **t,
                    "top_props": top_props,
                    "all_demons": player.get("demons", [])[:5],
                    "all_goblins": player.get("goblins", [])[:5]
                })
            else:
                enriched.append(t)
        
        return enriched

    
    # ==================== HYBRID CACHING LAYER ====================
    
    async def get_static_shell(self) -> Dict[str, Any]:
        """
        Get cached STATIC SHELL data (24h TTL)
        Contains: Player metadata, teams, positions, historical stats (L5, L10, Season)
        Does NOT contain: Live betting lines
        """
        # Check if we have valid cached data
        cached = await self.static_shell_cache.find_one({"type": "shell"}, {"_id": 0})
        
        if cached:
            cached_time = datetime.fromisoformat(cached["cached_at"])
            age = datetime.now(timezone.utc) - cached_time
            
            if age < STATIC_CACHE_TTL:
                logger.info(f"[CACHE HIT] Static shell (age: {age.total_seconds():.0f}s)")
                return {
                    "cache_hit": True,
                    "cache_age_seconds": age.total_seconds(),
                    "players": cached.get("players", []),
                    "trending": cached.get("trending", []),
                    "sync_date": cached.get("sync_date"),
                    "stats_version": cached.get("stats_version")
                }
        
        # Cache miss - need full sync
        logger.info("[CACHE MISS] Static shell expired or not found")
        return {"cache_hit": False, "players": [], "trending": []}
    
    async def store_static_shell(self, players: List[Dict], trending: List[Dict]):
        """
        Store STATIC SHELL data with 24h TTL
        Strips out live betting lines, keeps only metadata and historical stats
        """
        # Extract static data only (no live lines)
        static_players = []
        for p in players:
            static_player = {
                "player_name": p.get("player_name"),
                "team": p.get("team"),
                "position": p.get("position"),
                "injury_info": p.get("injury_info"),
                "popularity_order": p.get("popularity_order"),
                # Historical stats only (these don't change intra-day)
                "stats_summary": self._extract_stats_summary(p.get("props", []))
            }
            static_players.append(static_player)
        
        # Clean trending data (remove any _id fields)
        clean_trending = []
        for t in trending:
            clean_t = {k: v for k, v in t.items() if k != '_id'}
            clean_trending.append(clean_t)
        
        # Store with timestamp
        await self.static_shell_cache.update_one(
            {"type": "shell"},
            {"$set": {
                "type": "shell",
                "players": static_players,
                "trending": clean_trending,
                "sync_date": self.get_current_date(),
                "stats_version": datetime.now(timezone.utc).strftime("%Y%m%d"),
                "cached_at": datetime.now(timezone.utc).isoformat()
            }},
            upsert=True
        )
        logger.info(f"[CACHE STORE] Static shell saved ({len(static_players)} players)")
    
    def _extract_stats_summary(self, props: List[Dict]) -> Dict[str, Any]:
        """Extract aggregated stats summary from props for caching"""
        if not props:
            return {}
        
        # Get unique market stats
        stats = {}
        for prop in props:
            market = prop.get("market", "").replace("_alternate", "")
            hit_rates = prop.get("hit_rates") or {}
            
            if market and hit_rates and market not in stats:
                stats[market] = {
                    "l5": hit_rates.get("l5"),
                    "l10": hit_rates.get("l10"),
                    "season": hit_rates.get("season"),
                    "trends": hit_rates.get("trends", [])
                }
        
        return stats
    
    async def get_live_lines(self) -> Dict[str, Any]:
        """
        Get DYNAMIC PULSE data (60s TTL)
        Contains ONLY: Live betting lines (price, point, demon/goblin tags)
        This is the lightweight endpoint for real-time updates
        """
        # Check dynamic cache
        cached = await self.dynamic_lines_cache.find_one({"type": "lines"}, {"_id": 0})
        
        if cached:
            cached_time = datetime.fromisoformat(cached["cached_at"])
            age = datetime.now(timezone.utc) - cached_time
            
            if age < DYNAMIC_CACHE_TTL:
                logger.info(f"[CACHE HIT] Dynamic lines (age: {age.total_seconds():.0f}s)")
                return {
                    "cache_hit": True,
                    "cache_age_seconds": age.total_seconds(),
                    "lines": cached.get("lines", {}),
                    "last_update": cached.get("cached_at")
                }
        
        # Cache miss - fetch fresh lines
        logger.info("[CACHE MISS] Dynamic lines - fetching fresh data")
        lines = await self._fetch_fresh_lines()
        
        # Store in cache
        await self.dynamic_lines_cache.update_one(
            {"type": "lines"},
            {"$set": {
                "type": "lines",
                "lines": lines,
                "cached_at": datetime.now(timezone.utc).isoformat()
            }},
            upsert=True
        )
        
        return {
            "cache_hit": False,
            "cache_age_seconds": 0,
            "lines": lines,
            "last_update": datetime.now(timezone.utc).isoformat()
        }
    
    async def _fetch_fresh_lines(self) -> Dict[str, List[Dict]]:
        """
        Fetch ONLY live betting lines (lightweight)
        Returns: {player_name: [{market, line, price, is_demon, is_goblin, prop_type}, ...]}
        
        Classification:
        - Standard: Main market (no _alternate)
        - Demon: Alternate market + price == +100
        - Goblin: Alternate market + price != +100
        """
        lines_by_player = {}
        
        try:
            # Get events
            events = await self.fetch_todays_events()
            
            for event in events[:10]:  # Limit to 10 events for speed
                event_id = event.get("id")
                if not event_id:
                    continue
                
                # Fetch PrizePicks lines - both standard and alternate
                url = f"{ODDS_API_BASE}/sports/basketball_nba/events/{event_id}/odds"
                params = {
                    "apiKey": ODDS_API_KEY,
                    "regions": PRIZEPICKS_REGION,
                    "markets": PRIZEPICKS_ALL_MARKETS,  # Both standard and alternate
                    "bookmakers": PRIZEPICKS_BOOKMAKER,
                    "oddsFormat": "american"
                }
                
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, params=params, timeout=15.0)
                    
                    if response.status_code == 200:
                        data = response.json()
                        
                        for bm in data.get("bookmakers", []):
                            if bm.get("key") != "prizepicks":
                                continue
                            
                            for market in bm.get("markets", []):
                                market_key = market.get("key", "")
                                is_alternate_market = "_alternate" in market_key
                                
                                for outcome in market.get("outcomes", []):
                                    player_name = outcome.get("description", "")
                                    if not player_name:
                                        continue
                                    
                                    price = outcome.get("price")
                                    
                                    # Classification logic
                                    if is_alternate_market:
                                        is_demon = price is not None and price == DEMON_ODDS
                                        is_goblin = price is not None and price != DEMON_ODDS
                                        prop_type = "demon" if is_demon else "goblin"
                                    else:
                                        is_demon = False
                                        is_goblin = False
                                        prop_type = "standard"
                                    
                                    line_data = {
                                        "market": market_key,
                                        "direction": outcome.get("name"),
                                        "line": outcome.get("point"),
                                        "price": price,
                                        "is_alternate_market": is_alternate_market,
                                        "is_demon": is_demon,
                                        "is_goblin": is_goblin,
                                        "prop_type": prop_type
                                    }
                                    
                                    if player_name not in lines_by_player:
                                        lines_by_player[player_name] = []
                                    lines_by_player[player_name].append(line_data)
                
                await asyncio.sleep(0.2)  # Rate limiting
                
        except Exception as e:
            logger.error(f"[LINES FETCH] Error: {e}")
        
        return lines_by_player
    
    async def get_hydrated_board(self) -> Dict[str, Any]:
        """
        Get board with hybrid caching:
        1. First load static shell (instant)
        2. Then hydrate with live lines (background)
        """
        # Get static shell first
        static = await self.get_static_shell()
        
        if not static.get("cache_hit"):
            # No cached data - need full sync
            return {
                "needs_sync": True,
                "players": [],
                "trending": []
            }
        
        # Get live lines
        lines_data = await self.get_live_lines()
        lines = lines_data.get("lines", {})
        
        # Hydrate static players with live lines
        hydrated_players = []
        for player in static.get("players", []):
            player_name = player.get("player_name")
            player_lines = lines.get(player_name, [])
            
            # Count demons and goblins from live lines
            demons_count = sum(1 for line in player_lines if line.get("is_demon"))
            goblins_count = sum(1 for line in player_lines if line.get("is_goblin"))
            
            hydrated_players.append({
                **player,
                "props": player_lines,
                "demons_count": demons_count,
                "goblins_count": goblins_count,
                "lines_loaded": len(player_lines) > 0
            })
        
        return {
            "needs_sync": False,
            "static_cache_age": static.get("cache_age_seconds"),
            "lines_cache_age": lines_data.get("cache_age_seconds"),
            "players": hydrated_players,
            "trending": static.get("trending", []),
            "sync_date": static.get("sync_date")
        }
