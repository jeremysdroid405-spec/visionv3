"""
Utils Service - Common Utilities and Normalization
===================================================
Extracted from services.engines.demon_goblin_engine.py for modularity.
"""
from typing import Dict, Optional
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

# Name aliases for player normalization
NAME_ALIASES = {
    "nic": "Nicolas",
    "nick": "Nicolas",
    "mike": "Michael",
    "rob": "Robert",
    "bob": "Robert",
    "will": "William",
    "bill": "William",
    "jim": "James",
    "jimmy": "James",
    "tony": "Anthony",
    "joe": "Joseph",
    "joey": "Joseph",
    "chris": "Christopher",
    "dan": "Daniel",
    "danny": "Daniel",
    "dave": "David",
    "matt": "Matthew",
    "alex": "Alexander",
    "gg": "G.G.",
    "pj": "P.J.",
    "jj": "J.J.",
    "tj": "T.J.",
    "rj": "R.J.",
    "aj": "A.J.",
    "cj": "C.J.",
    "dj": "D.J.",
    "oj": "O.J.",
    "kj": "K.J.",
}

# NBA Team name mapping
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


def get_current_date() -> str:
    """Get current date in YYYY-MM-DD format"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def normalize_team_name(team_name: str) -> str:
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


def sanitize_player_name(name: str, cache: Dict[str, str] = None) -> str:
    """
    Sanitize and normalize player name for consistent storage.
    
    Handles:
    - Case normalization (Title Case)
    - Special character handling (G.G. → GG)
    - Common nickname variations (Nic → Nicolas)
    - Suffix standardization (Jr → Jr.)
    """
    if not name:
        return ""
    
    # Check cache first if provided
    if cache and name in cache:
        return cache[name]
    
    # Step 1: Basic cleanup
    cleaned = name.strip()
    
    # Step 2: Split into parts for processing
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
    
    # Step 3: Join and handle hyphenated names
    result = " ".join(normalized_parts)
    
    # Fix known hyphenation issues
    result = result.replace("Gilgeous Alexander", "Gilgeous-Alexander")
    result = result.replace("Porter Jr", "Porter Jr.")
    result = result.replace("Payton Ii", "Payton II")
    
    # Cache the result if cache provided
    if cache is not None:
        cache[name] = result
    
    return result


def create_composite_key(player_name: str, stat_type: str, game_date: str) -> str:
    """Create unique composite key for props"""
    sanitized = sanitize_player_name(player_name).lower().replace(" ", "_")
    return f"{sanitized}_{stat_type.lower()}_{game_date}"


def get_player_photo_url(player_name: str, team: str = None, nba_id: int = None) -> Dict[str, str]:
    """
    Generate player photo URLs from multiple sources.
    
    Returns dict with primary and fallback URLs.
    """
    urls = {}
    sanitized = sanitize_player_name(player_name)
    
    # NBA.com headshot (most reliable)
    if nba_id:
        urls["nba_headshot"] = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_id}.png"
        urls["nba_headshot_small"] = f"https://cdn.nba.com/headshots/nba/latest/260x190/{nba_id}.png"
    
    # ESPN photo (backup)
    name_parts = sanitized.lower().split()
    if len(name_parts) >= 2:
        espn_name = f"{name_parts[0]}-{name_parts[-1]}"
        urls["espn"] = f"https://a.espncdn.com/combiner/i?img=/i/headshots/nba/players/full/{espn_name}.png"
    
    # Default fallback
    urls["fallback"] = "https://cdn.nba.com/headshots/nba/latest/260x190/fallback.png"
    
    return urls


def format_stat_display(stat_type: str) -> str:
    """Format stat type for display"""
    display_map = {
        "PTS": "Points",
        "REB": "Rebounds",
        "AST": "Assists",
        "3PM": "3-Pointers",
        "BLK": "Blocks",
        "STL": "Steals",
        "TO": "Turnovers",
        "PRA": "Pts+Reb+Ast",
        "P+R": "Pts+Reb",
        "P+A": "Pts+Ast",
        "R+A": "Reb+Ast",
    }
    return display_map.get(stat_type.upper(), stat_type)


def format_odds_display(odds: int) -> str:
    """Format American odds for display"""
    if odds >= 0:
        return f"+{odds}"
    return str(odds)


def calculate_implied_probability(american_odds: int) -> float:
    """Convert American odds to implied probability"""
    if american_odds > 0:
        return 100 / (american_odds + 100)
    else:
        return abs(american_odds) / (abs(american_odds) + 100)


def probability_to_american_odds(probability: float) -> int:
    """Convert probability to American odds"""
    if probability <= 0 or probability >= 1:
        return 0
    
    if probability >= 0.5:
        return int(-100 * probability / (1 - probability))
    else:
        return int(100 * (1 - probability) / probability)


# Stat type mapping — known NBA markets get concise abbreviations used
# by the scoring pipeline (PTS/REB/AST/PRA/…). Unknown markets fall
# through the ``extract_stat_type`` helper and are returned as a
# readable slug derived from the raw market key so composite-keys stay
# unique (2026-04-21 "pull all markets" expansion).
STAT_TYPE_MAP = {
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
    # Extended NBA markets now unlocked by dynamic market discovery.
    "player_double_double": "DD",
    "player_triple_double": "TD",
    "player_blocks_steals": "BLK+STL",
    "player_first_basket": "FIRST BASKET",
    "player_first_team_basket": "FIRST TEAM BASKET",
    "player_field_goals": "FGM",
    "player_frees_made": "FTM",
    "player_frees_attempts": "FTA",
    "player_method_of_first_basket": "FIRST BASKET METHOD",
}


def extract_stat_type(market: str) -> str:
    """Extract stat type from market name.

    Known markets (see ``STAT_TYPE_MAP``) get concise abbreviations.
    Unknown markets return the raw market key (minus the
    ``_alternate`` suffix) so composite-keys stay unique when new
    markets surface via dynamic market discovery.
    """
    if not market:
        return ""
    base = market.replace("_alternate", "")
    mapped = STAT_TYPE_MAP.get(base)
    if mapped:
        return mapped
    # Fallback: return the raw base key uppercased for readability.
    # e.g. "player_first_basket" → "PLAYER_FIRST_BASKET"
    return base.upper()
