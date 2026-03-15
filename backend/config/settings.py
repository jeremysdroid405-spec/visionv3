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

# ==================== ANALYTICS CONSTANTS ====================
LEAGUE_AVG_PACE = 100.0
VOLATILITY_HIGH_THRESHOLD = 10.0
VOLATILITY_MED_THRESHOLD = 5.0
USAGE_REDISTRIBUTION_BASE = 12.0

# 2024-25 Team Pace Values (possessions per 48 min)
TEAM_PACE = {
    "IND": 103.5, "ATL": 102.8, "MIL": 102.2, "SAC": 101.9, "MIN": 101.5,
    "DEN": 101.2, "BOS": 100.8, "PHX": 100.6, "GSW": 100.4, "LAL": 100.2,
    "DAL": 100.0, "OKC": 99.8, "NOP": 99.6, "POR": 99.4, "HOU": 99.2,
    "TOR": 99.0, "CHI": 98.8, "WAS": 98.6, "BKN": 98.4, "CHA": 98.2,
    "SAS": 98.0, "UTA": 97.8, "DET": 97.6, "ORL": 97.4, "MEM": 97.2,
    "PHI": 97.0, "CLE": 96.8, "MIA": 96.6, "NYK": 96.4, "LAC": 96.2
}

# High-usage players by team (>25% usage rate)
HIGH_USAGE_PLAYERS = {
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


# ==================== TEAM LOGOS ====================
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


# ==================== NBA PLAYER ID MAPPING ====================
# NBA CDN headshot URL format: https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_id}.png
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
