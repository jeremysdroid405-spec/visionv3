"""
PickVision Backend Configuration
================================
Centralized configuration for database connections, API settings, and constants.
"""
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

# ==================== DATABASE ====================
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "pickvision")

# MongoDB Client (singleton)
_mongo_client = None
_database = None

async def get_database():
    """Get database instance (creates connection if needed)"""
    global _mongo_client, _database
    if _database is None:
        _mongo_client = AsyncIOMotorClient(MONGO_URL)
        _database = _mongo_client[DB_NAME]
    return _database

async def close_database():
    """Close database connection"""
    global _mongo_client, _database
    if _mongo_client:
        _mongo_client.close()
        _mongo_client = None
        _database = None

# ==================== API KEYS ====================
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
BDL_API_KEY = os.environ.get("BDL_API_KEY", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
JWT_SECRET = os.environ.get("JWT_SECRET", "your-secret-key-here")

# ==================== API ENDPOINTS ====================
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
BDL_API_BASE = "https://api.balldontlie.io/v1"
NBA_API_BASE = "https://stats.nba.com/stats"
NBA_HEADSHOT_URL = "https://cdn.nba.com/headshots/nba/latest/1040x760"

# ==================== RATE LIMITING ====================
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
API_RATE_LIMIT = 100  # requests per minute

# ==================== COLLECTION NAMES ====================
COLLECTIONS = {
    "players": "players",
    "odds_cache": "dg_odds_cache",
    "cached_board": "dg_cached_board",
    "war_zone": "dg_radar_picks",
    "goblin_vault": "dg_goblin_vault",
    "front_lines": "dg_front_lines",
    "parlay_builder": "dg_parlay_builder",
    "goblin_recon": "dg_goblin_recon",
    "player_insights": "dg_player_insights",
    "sync_status": "dg_sync_status",
    "master_roster": "master_nba_roster",
    "users": "users",
    "static_shell": "dg_static_shell"
}

# ==================== SCORING WEIGHTS ====================
SCORING_WEIGHTS = {
    "war_zone": {
        "ceiling_consistency": 0.45,
        "vegas_probability": 0.20,
        "dvp_matchup": 0.20,
        "context_shift": 0.15
    },
    "goblin_vault": {
        "floor_consistency": 0.50,
        "vegas_probability": 0.20,
        "dvp_matchup": 0.15,
        "context_shift": 0.15
    },
    "front_lines": {
        "base_consistency": 0.50,
        "vegas_probability": 0.20,
        "dvp_matchup": 0.15,
        "context_shift": 0.15
    }
}

# ==================== TEAM ABBREVIATIONS ====================
TEAM_ABBREV_MAP = {
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

# ==================== STAT MAPPINGS ====================
STAT_TYPE_MAP = {
    "player_points": "PTS",
    "player_assists": "AST",
    "player_rebounds": "REB",
    "player_threes": "3PM",
    "player_blocks": "BLK",
    "player_steals": "STL",
    "player_points_rebounds_assists": "PRA",
    "player_points_rebounds": "P+R",
    "player_points_assists": "P+A",
    "player_rebounds_assists": "R+A",
    "player_turnovers": "TO",
    "player_double_double": "DD",
    "player_triple_double": "TD"
}

# ==================== DVP RANKINGS 2024-25 ====================
DVP_RANKINGS = {
    "PTS": {
        "CLE": 1, "OKC": 2, "HOU": 3, "MEM": 4, "ORL": 5, "MIN": 6, "BOS": 7, "NYK": 8,
        "MIA": 9, "DEN": 10, "LAL": 11, "GSW": 12, "PHX": 13, "DAL": 14, "SAC": 15,
        "NOP": 16, "MIL": 17, "IND": 18, "ATL": 19, "CHI": 20, "DET": 21, "TOR": 22,
        "CHA": 23, "SAS": 24, "POR": 25, "BKN": 26, "LAC": 27, "PHI": 28, "UTA": 29, "WAS": 30
    },
    "AST": {
        "OKC": 1, "CLE": 2, "HOU": 3, "ORL": 4, "MEM": 5, "MIN": 6, "NYK": 7, "BOS": 8,
        "MIA": 9, "GSW": 10, "LAL": 11, "DEN": 12, "PHX": 13, "DAL": 14, "MIL": 15,
        "SAC": 16, "NOP": 17, "IND": 18, "ATL": 19, "CHI": 20, "TOR": 21, "CHA": 22,
        "DET": 23, "SAS": 24, "POR": 25, "LAC": 26, "PHI": 27, "BKN": 28, "UTA": 29, "WAS": 30
    },
    "REB": {
        "BOS": 1, "CLE": 2, "OKC": 3, "MEM": 4, "MIN": 5, "HOU": 6, "ORL": 7, "NYK": 8,
        "LAL": 9, "DEN": 10, "MIA": 11, "GSW": 12, "PHX": 13, "DAL": 14, "MIL": 15,
        "SAC": 16, "NOP": 17, "IND": 18, "ATL": 19, "TOR": 20, "CHI": 21, "CHA": 22,
        "DET": 23, "SAS": 24, "POR": 25, "LAC": 26, "PHI": 27, "BKN": 28, "UTA": 29, "WAS": 30
    },
    "3PM": {
        "CLE": 1, "OKC": 2, "BOS": 3, "HOU": 4, "MEM": 5, "ORL": 6, "MIN": 7, "NYK": 8,
        "MIA": 9, "DEN": 10, "LAL": 11, "GSW": 12, "DAL": 13, "PHX": 14, "MIL": 15,
        "SAC": 16, "NOP": 17, "IND": 18, "ATL": 19, "CHI": 20, "TOR": 21, "DET": 22,
        "CHA": 23, "SAS": 24, "POR": 25, "LAC": 26, "PHI": 27, "BKN": 28, "UTA": 29, "WAS": 30
    },
    "BLK": {
        "OKC": 1, "CLE": 2, "HOU": 3, "MIN": 4, "MEM": 5, "ORL": 6, "BOS": 7, "NYK": 8,
        "MIA": 9, "LAL": 10, "DEN": 11, "GSW": 12, "PHX": 13, "DAL": 14, "MIL": 15,
        "SAC": 16, "NOP": 17, "IND": 18, "ATL": 19, "CHI": 20, "TOR": 21, "CHA": 22,
        "DET": 23, "SAS": 24, "POR": 25, "LAC": 26, "PHI": 27, "BKN": 28, "UTA": 29, "WAS": 30
    },
    "STL": {
        "MIN": 1, "OKC": 2, "CLE": 3, "HOU": 4, "MEM": 5, "ORL": 6, "BOS": 7, "NYK": 8,
        "MIA": 9, "DEN": 10, "LAL": 11, "GSW": 12, "PHX": 13, "DAL": 14, "MIL": 15,
        "SAC": 16, "NOP": 17, "IND": 18, "ATL": 19, "CHI": 20, "TOR": 21, "CHA": 22,
        "DET": 23, "SAS": 24, "POR": 25, "LAC": 26, "PHI": 27, "BKN": 28, "UTA": 29, "WAS": 30
    }
}
