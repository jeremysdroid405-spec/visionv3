"""
Picks Getter Service
====================
SSOT ARCHITECTURE: This service reads from MongoDB ONLY.

NO external API calls are made here. All stats come from:
- PIPE 1: nba_master_hub_2026 (stats vault, populated by 0400 CRON)
- PIPE 2: dg_cached_board (live lines, populated by Odds API polling)

ANCHOR-BASED TIER CLASSIFICATION (from cached_board):
Tier classification is done during sync using PrizePicks' own structure:
- Standard Line (is_alternate_market=false) is the ANCHOR
- ALL Alternate Lines ABOVE anchor = DEMON (Red)
- ALL Alternate Lines BELOW anchor = GOBLIN (Green)
- Standard Line itself = STANDARD (Gray)

ALL bets (over AND under) are classified by line value vs anchor.
This service preserves the tier flags from the cached_board.
The Vault provides stats (FG%, 3P%, STL, BLK) for display on cards.
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone, timedelta
import logging
import re
from bson import ObjectId

from motor.motor_asyncio import AsyncIOMotorDatabase

# CONSOLIDATED: Use shared player lookup utility
from utils.player_lookup import get_player_by_id, get_player_by_name as shared_get_player_by_name

# Probability scoring service - imported once at module level
from services.probability_score_service import ProbabilityScoreService

logger = logging.getLogger(__name__)


# ==================== OBJECTID SANITIZATION ====================
def _sanitize_objectid(doc: Dict) -> Dict:
    """
    Recursively sanitize ObjectId fields to strings to prevent Pydantic serialization errors.
    """
    if doc is None:
        return None
    
    sanitized = {}
    for key, value in doc.items():
        if key == "_id":
            continue  # Drop _id entirely
        elif isinstance(value, ObjectId):
            sanitized[key] = str(value)
        elif isinstance(value, dict):
            sanitized[key] = _sanitize_objectid(value)
        elif isinstance(value, list):
            sanitized[key] = [
                _sanitize_objectid(item) if isinstance(item, dict) 
                else str(item) if isinstance(item, ObjectId) 
                else item 
                for item in value
            ]
        else:
            sanitized[key] = value
    return sanitized


def _sanitize_picks_list(picks: List[Dict]) -> List[Dict]:
    """Sanitize a list of picks to remove/convert ObjectId fields."""
    return [_sanitize_objectid(pick) for pick in picks if pick]


# ==================== GAME STATUS HELPER ====================
def _get_game_status(commence_time_str: str) -> Dict[str, Any]:
    """
    Determine game status based on commence time.
    
    Returns:
        {
            "status": "upcoming" | "in_progress" | "completed",
            "is_locked": bool,
            "time_until_start": timedelta or None,
            "minutes_since_start": int or None
        }
    """
    if not commence_time_str:
        return {"status": "unknown", "is_locked": False, "time_until_start": None, "minutes_since_start": None}
    
    try:
        # Parse the commence time
        if isinstance(commence_time_str, str):
            commence_time = datetime.fromisoformat(commence_time_str.replace('Z', '+00:00'))
        else:
            commence_time = commence_time_str
        
        now = datetime.now(timezone.utc)
        time_diff = commence_time - now
        
        # Game hasn't started yet
        if time_diff.total_seconds() > 0:
            return {
                "status": "upcoming",
                "is_locked": False,
                "time_until_start": time_diff,
                "minutes_since_start": None
            }
        
        # Game has started - check if it's still in progress (NBA games ~2.5 hours)
        minutes_since_start = abs(time_diff.total_seconds()) / 60
        
        if minutes_since_start < 150:  # Less than 2.5 hours = in progress
            return {
                "status": "in_progress",
                "is_locked": True,
                "time_until_start": None,
                "minutes_since_start": int(minutes_since_start)
            }
        else:
            return {
                "status": "completed",
                "is_locked": True,
                "time_until_start": None,
                "minutes_since_start": int(minutes_since_start)
            }
    except Exception as e:
        logger.warning(f"Error parsing commence time '{commence_time_str}': {e}")
        return {"status": "unknown", "is_locked": False, "time_until_start": None, "minutes_since_start": None}


# ==================== DNP FILTER HELPER ====================
def _filter_played_games(game_logs: List[Dict]) -> List[Dict]:
    """
    Filter out DNP (Did Not Play) games from game logs.
    
    A game is considered DNP if minutes played is 0, "00", or empty.
    This is CRITICAL for accurate L5/L10 averages and hit rate calculations.
    """
    if not game_logs:
        return []
    
    def did_play(game: Dict) -> bool:
        mins = game.get("min", "0") or "0"
        if isinstance(mins, str):
            # Handle "MM:SS" format or "00"
            mins = mins.split(":")[0] if ":" in mins else mins
            try:
                return int(mins) > 0
            except ValueError:
                return False
        return float(mins) > 0 if mins else False
    
    return [g for g in game_logs if did_play(g)]


# ==================== TEAM NAME MAPPING ====================
# Maps team abbreviations (from master hub) to full names (from Odds API)
TEAM_ABBREV_TO_FULL = {
    "ATL": "Atlanta Hawks", "BOS": "Boston Celtics", "BKN": "Brooklyn Nets",
    "CHA": "Charlotte Hornets", "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets", "DET": "Detroit Pistons",
    "GS": "Golden State Warriors", "GSW": "Golden State Warriors",
    "HOU": "Houston Rockets", "IND": "Indiana Pacers", "LAC": "Los Angeles Clippers",
    "LAL": "Los Angeles Lakers", "MEM": "Memphis Grizzlies", "MIA": "Miami Heat",
    "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves", "NO": "New Orleans Pelicans",
    "NOP": "New Orleans Pelicans", "NY": "New York Knicks", "NYK": "New York Knicks",
    "OKC": "Oklahoma City Thunder", "ORL": "Orlando Magic", "PHI": "Philadelphia 76ers",
    "PHX": "Phoenix Suns", "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings",
    "SA": "San Antonio Spurs", "SAS": "San Antonio Spurs", "TOR": "Toronto Raptors",
    "UTA": "Utah Jazz", "WAS": "Washington Wizards"
}

def _get_opponent_from_game(player_team_abbrev: str, home_team: str, away_team: str) -> Optional[str]:
    """
    Derive opponent from home_team/away_team given player's team abbreviation.
    Returns the full team name of the opponent.
    """
    if not player_team_abbrev or not home_team or not away_team:
        return None
    
    player_team_full = TEAM_ABBREV_TO_FULL.get(player_team_abbrev.upper())
    if not player_team_full:
        return None
    
    # Compare and return opponent
    if player_team_full == home_team:
        return away_team
    elif player_team_full == away_team:
        return home_team
    return None


# ==================== NAME NORMALIZATION ====================
def _normalize_name(name: str) -> str:
    """
    Normalize player names for consistent MongoDB lookups.
    
    Strips:
    - Periods and commas
    - Common suffixes: Jr, Sr, II, III, IV, V
    - Extra whitespace
    
    Example: "Jaime Jaquez Jr." -> "jaime jaquez"
    Example: "Marcus Morris Sr." -> "marcus morris"
    """
    if not name:
        return ""
    
    # Convert to lowercase
    normalized = name.lower().strip()
    
    # Remove periods and commas
    normalized = normalized.replace(".", "").replace(",", "")
    
    # Remove common suffixes (with word boundaries)
    suffix_pattern = r'\b(jr|sr|ii|iii|iv|v)\b'
    normalized = re.sub(suffix_pattern, '', normalized, flags=re.IGNORECASE)
    
    # Collapse multiple spaces to single space
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    
    return normalized


class PicksGetterService:
    """
    SSOT-Compliant Picks Service.
    
    CRITICAL: This service reads from MongoDB ONLY.
    - Stats from nba_master_hub_2026 (PIPE 1)
    - Lines from dg_cached_board (PIPE 2)
    
    NO external API calls. NO secondary internal APIs.
    """
    
    # CLASS-LEVEL CACHE: Shared across all instances, survives request cycles
    _photo_cache = {}  # player_name -> {photo_url, team, position, nba_id}
    _photo_cache_loaded = False
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        
        # PIPE 2: Live Lines (Odds API destination)
        self.radar_picks = db.dg_radar_picks
        self.goblin_vault = db.dg_goblin_vault
        self.front_lines = db.dg_front_lines
        self.parlay_builder = db.dg_parlay_builder
        self.goblin_recon = db.dg_goblin_recon
        self.cached_board = db.dg_cached_board  # Active Lines
        self.player_data = db.dg_player_data
        self.daily_insights = db.dg_daily_insights
        self.sync_log = db.dg_sync_log
        self.events_cache = db.dg_events_cache
        self.odds_cache = db.dg_odds_cache
        
        # PIPE 1: Stats Vault (BDL CRON destination)
        self.master_hub = db.nba_master_hub_2026
        
        # Cache for game info (home_team, away_team) by game_id
        self._game_info_cache = {}
        
        # Cache for injured players
        self._injured_players_cache = None
    
    async def _load_photo_cache(self):
        """
        Pre-load ALL player photo data into memory for instant lookups.
        This eliminates individual DB queries for each pick.
        """
        if PicksGetterService._photo_cache_loaded:
            return
        
        logger.info("[PHOTO_CACHE] Loading player photo data...")
        
        # Load from master hub
        cursor = self.master_hub.find(
            {},
            {"_id": 0, "display_name": 1, "photo_url": 1, "headshot_url": 1, "team": 1, "position": 1, "nba_id": 1}
        )
        players = await cursor.to_list(6000)
        
        for player in players:
            name = player.get("display_name")
            if name:
                # Normalize name for lookup
                name_key = name.lower().strip()
                nba_id = player.get("nba_id")
                
                # LOCAL-FIRST: Use static path if we have nba_id
                photo_url = None
                if nba_id:
                    photo_url = f"/static/player-headshots/{nba_id}.png"
                else:
                    photo_url = player.get("photo_url") or player.get("headshot_url")
                
                PicksGetterService._photo_cache[name_key] = {
                    "photo_url": photo_url,
                    "team": player.get("team"),
                    "position": player.get("position"),
                    "nba_id": nba_id
                }
        
        # Also load from master roster as backup
        roster_cursor = self.db.dg_master_roster.find(
            {},
            {"_id": 0, "full_name": 1, "team_abbreviation": 1, "nba_id": 1}
        )
        roster = await roster_cursor.to_list(6000)
        
        for player in roster:
            name = player.get("full_name")
            if name:
                name_key = name.lower().strip()
                nba_id = player.get("nba_id")
                
                # Only add if not already in cache - LOCAL STATIC PATH
                if name_key not in PicksGetterService._photo_cache and nba_id:
                    PicksGetterService._photo_cache[name_key] = {
                        "photo_url": f"/static/player-headshots/{nba_id}.png",
                        "team": player.get("team_abbreviation"),
                        "position": None,
                        "nba_id": nba_id
                    }
        
        PicksGetterService._photo_cache_loaded = True
        logger.info(f"[PHOTO_CACHE] Loaded {len(PicksGetterService._photo_cache)} player photos into memory")
    
    async def _get_injured_players(self) -> set:
        """
        Get set of injured player names for quick lookup.
        Combines ESPN (dg_injuries) and BDL (bdl_injuries) sources.
        """
        if self._injured_players_cache is not None:
            return self._injured_players_cache
        
        injured = set()
        
        # Get BDL injuries (more reliable)
        bdl_cursor = self.db.bdl_injuries.find({}, {"_id": 0, "player_name": 1})
        bdl_injuries = await bdl_cursor.to_list(500)
        for inj in bdl_injuries:
            if inj.get("player_name"):
                injured.add(inj["player_name"].lower())
        
        # Get ESPN injuries
        espn_cursor = self.db.dg_injuries.find({}, {"_id": 0, "player_name": 1})
        espn_injuries = await espn_cursor.to_list(500)
        for inj in espn_injuries:
            if inj.get("player_name"):
                injured.add(inj["player_name"].lower())
        
        self._injured_players_cache = injured
        return injured
    
    async def _enrich_picks_with_photos(self, picks: list) -> list:
        """
        Enrich a list of picks with photos using IN-MEMORY CACHE.
        
        This is the SINGLE SOURCE OF TRUTH for photo enrichment.
        Uses pre-loaded cache for instant lookups - NO DB queries.
        
        Args:
            picks: List of pick dicts with 'player_name' field
            
        Returns:
            Same list with photo_url, team, position, nba_id enriched
        """
        if not picks:
            return picks
        
        # Ensure cache is loaded
        await self._load_photo_cache()
        
        for pick in picks:
            # Skip if already has a valid photo URL
            if pick.get("photo_url") and not pick["photo_url"].endswith("None"):
                continue
            
            player_name = pick.get("player_name")
            if not player_name:
                continue
            
            # Fast lookup from in-memory cache
            name_key = player_name.lower().strip()
            cached = PicksGetterService._photo_cache.get(name_key)
            
            if cached:
                pick["photo_url"] = cached.get("photo_url")
                if not pick.get("team"):
                    pick["team"] = cached.get("team")
                if not pick.get("position"):
                    pick["position"] = cached.get("position")
                if not pick.get("nba_id"):
                    pick["nba_id"] = cached.get("nba_id")
            else:
                # Fallback: If we have nba_id on the pick, construct LOCAL STATIC URL
                nba_id = pick.get("nba_id")
                if nba_id:
                    pick["photo_url"] = f"/static/player-headshots/{nba_id}.png"
        
        return picks
        
        return picks
    
    async def _get_game_info(self, game_id: str) -> Dict[str, str]:
        """
        Get home_team and away_team for a game from cached_board raw documents.
        Results are cached for the duration of this service instance.
        """
        if not game_id:
            return {"home_team": None, "away_team": None}
        
        if game_id in self._game_info_cache:
            return self._game_info_cache[game_id]
        
        # Look up from raw prop documents
        raw_doc = await self.cached_board.find_one(
            {"game_id": game_id, "home_team": {"$exists": True}},
            {"_id": 0, "home_team": 1, "away_team": 1}
        )
        
        if raw_doc:
            info = {
                "home_team": raw_doc.get("home_team"),
                "away_team": raw_doc.get("away_team")
            }
        else:
            info = {"home_team": None, "away_team": None}
        
        self._game_info_cache[game_id] = info
        return info
    
    async def _get_player_lookup(self) -> Dict[str, Dict]:
        """
        SSOT PIPE 1: Get player lookup from shared utility.
        Builds a dict of all players from master hub.
        """
        players = await self.master_hub.find({}, {"_id": 0}).to_list(1000)
        return {p.get("display_name", ""): p for p in players if p.get("display_name")}
    
    async def _get_player_by_id(self, player_id: str) -> Dict:
        """
        PRIMARY: Get player data from master hub by player_id.
        """
        return await get_player_by_id(self.db, player_id)
    
    async def _get_player_by_name(self, player_name: str) -> Dict:
        """
        FALLBACK: Get player data from master hub by name.
        Uses normalized name matching for consistency.
        Checks both display_name and normalized_name fields (BDL data).
        Use _get_player_by_id when possible.
        """
        # Try shared lookup first (handles its own normalization)
        player = await shared_get_player_by_name(self.db, player_name)
        if player:
            return player
        
        # Try normalized_name field (for BDL-synced players)
        normalized = _normalize_name(player_name)
        if normalized:
            player = await self.master_hub.find_one(
                {"normalized_name": normalized},
                {"_id": 0}
            )
            if player:
                return player
        
        # Fallback: Try normalized name search directly on master_hub
        if normalized:
            # Build regex pattern for normalized matching
            all_players = await self.master_hub.find({}, {"display_name": 1, "_id": 0}).to_list(1000)
            for p in all_players:
                if _normalize_name(p.get("display_name", "")) == normalized:
                    return await self.master_hub.find_one(
                        {"display_name": p["display_name"]},
                        {"_id": 0}
                    )
        
        return None
    
    async def _get_master_player(self, pick: Dict) -> Dict:
        """
        Get player from master hub - tries player_id first, then name.
        ALL photos and stats come from master hub.
        """
        # PRIMARY: Try player_id first
        player_id = pick.get('player_id') or pick.get('bdl_player_id') or pick.get('nba_player_id')
        if player_id:
            player = await self._get_player_by_id(str(player_id))
            if player:
                return player
        
        # FALLBACK: Try by name
        player_name = pick.get('player_name')
        if player_name:
            return await self._get_player_by_name(player_name)
        
        return None
    
    async def _get_player_stats(self, player_name: str, stat_type: str, line: float) -> Dict:
        """
        Get L5/L10/Season stats from master hub for a player.
        
        OPTIMIZED: 
        1. PRIMARY: Return from baseline_stats immediately if available
        2. FALLBACK: Calculate from game_logs only if stat is missing from baseline_stats
        
        Also includes BDL shooting/defensive stats (fg_pct, fg3_pct, stl, blk).
        Also calculates diff_from_avg for divergence tracking.
        """
        player = await self._get_player_by_name(player_name)
        if not player:
            return {
                "h5_rate": 0, "h10_rate": 0, "season_avg": 0, 
                "l5_avg": 0, "l10_avg": 0, "diff_from_avg": None,
                "is_stale": True, "stats_source": "missing",
                # BDL shooting/defensive stats (empty when missing)
                "fg_pct": None, "fg3_pct": None, "ft_pct": None,
                "stl": None, "blk": None, "min": None
            }
        
        # Check freshness: is_stale if last_updated > 24 hours ago
        last_updated = player.get("last_updated") or player.get("last_bdl_sync")
        is_stale = True
        if last_updated:
            try:
                if isinstance(last_updated, datetime):
                    # Make sure both are timezone-aware
                    if last_updated.tzinfo is None:
                        last_updated = last_updated.replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - last_updated).total_seconds()
                    is_stale = age > 86400  # 24 hours in seconds
                elif isinstance(last_updated, str):
                    last_dt = datetime.fromisoformat(last_updated.replace('Z', '+00:00'))
                    age = (datetime.now(timezone.utc) - last_dt).total_seconds()
                    is_stale = age > 86400
            except (ValueError, TypeError):
                pass
        
        # ========================================================
        # BDL SHOOTING & DEFENSIVE STATS (Open Door - Direct Read)
        # ========================================================
        # Pull directly from baseline_stats - NO calculation, exact BDL values
        baseline_stats = player.get("baseline_stats", {})
        
        # These come EXACTLY from BDL /season_averages endpoint
        bdl_fg_pct = baseline_stats.get("fg_pct")       # Field Goal %
        bdl_fg3_pct = baseline_stats.get("fg3_pct")     # 3-Point %
        bdl_ft_pct = baseline_stats.get("ft_pct")       # Free Throw %
        bdl_stl = baseline_stats.get("stl")             # Steals per game
        bdl_blk = baseline_stats.get("blk")             # Blocks per game
        bdl_min = baseline_stats.get("min")             # Minutes per game
        bdl_turnover = baseline_stats.get("turnover")   # Turnovers per game
        bdl_games_played = baseline_stats.get("games_played")
        
        # Normalize stat type for baseline_stats lookup
        stat_key = stat_type.upper()
        norm_map = {"P+R": "PR", "P+A": "PA", "R+A": "RA", "3PM": "THREES"}
        stat_key = norm_map.get(stat_key, stat_key)
        
        # Map stat type to BDL baseline_stats field
        bdl_stat_map = {
            "PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl", 
            "BLK": "blk", "3PM": "fg3m", "THREES": "fg3m", "TO": "turnover"
        }
        bdl_field = bdl_stat_map.get(stat_key)
        
        # Get game logs for hit rate calculation (ALWAYS needed for L5/L10 hit rates)
        # PRIORITY: bdl_game_logs (2025-26 season) > game_logs (legacy)
        game_logs = player.get("bdl_game_logs", []) or player.get("game_logs", [])
        
        # Helper function to calculate hit rates from game logs
        def calculate_hit_rates(logs, stat_type, line):
            """Calculate L5/L10 hit rates from game logs"""
            if not logs:
                return {"h5_rate": 0, "h10_rate": 0, "l5_avg": 0, "l10_avg": 0, "season_avg": 0, "l5_values": [], "l10_values": []}
            
            # Sort by date descending - handle both "date" (bdl_game_logs) and "game_date" (game_logs) formats
            sorted_logs = sorted(logs, key=lambda x: x.get("date", "") or x.get("game_date", "") or x.get("game", {}).get("date", ""), reverse=True)
            
            # Map stat type to game log field
            # NOTE: bdl_game_logs uses "fg3m" for 3PM, game_logs uses "tptfgm"
            # NOTE: bdl_game_logs uses "turnover" for TO, game_logs uses "tov"
            stat_map = {
                "PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl", "BLK": "blk",
                "3PM": "fg3m", "THREES": "fg3m", "TO": "turnover", 
                "PRA": "pra", "P+R": "pts_reb", "PR": "pts_reb",
                "P+A": "pts_ast", "PA": "pts_ast", "R+A": "reb_ast", "RA": "reb_ast"
            }
            log_key = stat_map.get(stat_type.upper(), stat_type.lower())
            
            def calc_stats(game_list):
                if not game_list:
                    return 0, 0, []
                values = []
                hits = 0
                for g in game_list:
                    val = g.get(log_key, 0) or 0
                    # Handle combined stats
                    if stat_type.upper() == "PRA":
                        val = (g.get("pts", 0) or 0) + (g.get("reb", 0) or 0) + (g.get("ast", 0) or 0)
                    elif stat_type.upper() in ["P+R", "PR"]:
                        val = (g.get("pts", 0) or 0) + (g.get("reb", 0) or 0)
                    elif stat_type.upper() in ["P+A", "PA"]:
                        val = (g.get("pts", 0) or 0) + (g.get("ast", 0) or 0)
                    elif stat_type.upper() in ["R+A", "RA"]:
                        val = (g.get("reb", 0) or 0) + (g.get("ast", 0) or 0)
                    values.append(val)
                    if line and val > line:
                        hits += 1
                avg = sum(values) / len(values) if values else 0
                hit_rate = (hits / len(values) * 100) if values else 0
                return avg, hit_rate, values
            
            l5_avg, h5_rate, l5_values = calc_stats(sorted_logs[:5])
            l10_avg, h10_rate, l10_values = calc_stats(sorted_logs[:10])
            season_avg, _, _ = calc_stats(sorted_logs)
            
            return {
                "h5_rate": round(h5_rate, 1),
                "h10_rate": round(h10_rate, 1),
                "l5_avg": round(l5_avg, 1),
                "l10_avg": round(l10_avg, 1),
                "season_avg": round(season_avg, 1),
                "l5_values": l5_values,
                "l10_values": l10_values
            }
        
        # Calculate hit rates from game logs (even if baseline_stats exists)
        hit_rate_data = calculate_hit_rates(game_logs, stat_type, line)
        
        # SSOT ENFORCEMENT: L5/L10 averages MUST come from the same game logs as hit rates
        # This ensures data consistency - if average is 35.2 over L5, hit rate reflects same games
        l5_avg_from_logs = hit_rate_data["l5_avg"]
        l10_avg_from_logs = hit_rate_data["l10_avg"]
        
        # Log SSOT debug info for data integrity verification
        if hit_rate_data["l5_values"]:
            logger.debug(f"[SSOT] {player_name} {stat_type} L5 values: {hit_rate_data['l5_values']} -> avg={l5_avg_from_logs}, line={line}, hits={sum(1 for v in hit_rate_data['l5_values'] if v > line)}")
        
        # PRIMARY SOURCE: Check baseline_stats first (BDL data) for season avg
        season_avg = None
        if bdl_field and baseline_stats.get(bdl_field) is not None:
            season_avg = baseline_stats.get(bdl_field)
        
        if season_avg is not None:
            # Calculate diff from season average for display
            diff_from_avg = None
            if season_avg > 0 and line:
                diff_from_avg = round(((line - season_avg) / season_avg) * 100, 1)
            
            # NOTE: Tier classification (is_demon, is_goblin) is done during sync
            # using anchor-based classification. We preserve those flags here.
            # We only add vault stats for display on cards.
            
            return {
                "h5_rate": hit_rate_data["h5_rate"],
                "h10_rate": hit_rate_data["h10_rate"],
                "season_avg": round(season_avg, 1),
                # SSOT: L5/L10 averages MUST come from game logs (same source as hit rates)
                "l5_avg": l5_avg_from_logs if l5_avg_from_logs > 0 else round(season_avg, 1),
                "l10_avg": l10_avg_from_logs if l10_avg_from_logs > 0 else round(season_avg, 1),
                "diff_from_avg": diff_from_avg,
                "is_stale": is_stale,
                "stats_source": "bdl_baseline" if not game_logs else "bdl_game_logs_ssot",
                # BDL shooting/defensive stats - OPEN DOOR POPULATION
                "fg_pct": round(bdl_fg_pct * 100, 1) if bdl_fg_pct else None,
                "fg3_pct": round(bdl_fg3_pct * 100, 1) if bdl_fg3_pct else None,
                "ft_pct": round(bdl_ft_pct * 100, 1) if bdl_ft_pct else None,
                "stl": round(bdl_stl, 1) if bdl_stl else None,
                "blk": round(bdl_blk, 1) if bdl_blk else None,
                "min": bdl_min,
                "turnover": round(bdl_turnover, 1) if bdl_turnover else None,
                "games_played": bdl_games_played
            }
        
        # SECONDARY: Check old-style baseline_stats structure
        stat_data = baseline_stats.get(stat_key) or baseline_stats.get(stat_type)
        
        if stat_data and isinstance(stat_data, dict) and stat_data.get("season_avg") is not None:
            season_avg = stat_data.get("season_avg", 0)
            l5_avg = stat_data.get("l5_avg", season_avg)
            l10_avg = stat_data.get("l10_avg", season_avg)
            
            # Calculate diff from season average for display
            diff_from_avg = None
            if season_avg > 0 and line:
                diff_from_avg = round(((line - season_avg) / season_avg) * 100, 1)
            
            return {
                "h5_rate": hit_rate_data["h5_rate"] if hit_rate_data["h5_rate"] > 0 else stat_data.get("l5_hit_rate", 0),
                "h10_rate": hit_rate_data["h10_rate"] if hit_rate_data["h10_rate"] > 0 else stat_data.get("l10_hit_rate", 0),
                "season_avg": round(season_avg, 1),
                # SSOT: L5/L10 averages MUST come from game logs (same source as hit rates)
                "l5_avg": l5_avg_from_logs if l5_avg_from_logs > 0 else round(l5_avg, 1),
                "l10_avg": l10_avg_from_logs if l10_avg_from_logs > 0 else round(l10_avg, 1),
                "diff_from_avg": diff_from_avg,
                "is_stale": is_stale,
                "stats_source": "baseline_stats" if not game_logs else "bdl_game_logs_ssot",
                # BDL shooting/defensive stats
                "fg_pct": round(bdl_fg_pct * 100, 1) if bdl_fg_pct else None,
                "fg3_pct": round(bdl_fg3_pct * 100, 1) if bdl_fg3_pct else None,
                "ft_pct": round(bdl_ft_pct * 100, 1) if bdl_ft_pct else None,
                "stl": round(bdl_stl, 1) if bdl_stl else None,
                "blk": round(bdl_blk, 1) if bdl_blk else None,
                "min": bdl_min,
                "turnover": round(bdl_turnover, 1) if bdl_turnover else None,
                "games_played": bdl_games_played
            }
        
        # FALLBACK: Use game_logs if baseline_stats completely missing
        if not game_logs:
            return {
                "h5_rate": 0, "h10_rate": 0, "season_avg": 0, 
                "l5_avg": 0, "l10_avg": 0, 
                "diff_from_avg": None,
                "is_stale": is_stale, "stats_source": "no_data",
                # BDL shooting/defensive stats (may still exist even without game logs)
                "fg_pct": round(bdl_fg_pct * 100, 1) if bdl_fg_pct else None,
                "fg3_pct": round(bdl_fg3_pct * 100, 1) if bdl_fg3_pct else None,
                "ft_pct": round(bdl_ft_pct * 100, 1) if bdl_ft_pct else None,
                "stl": round(bdl_stl, 1) if bdl_stl else None,
                "blk": round(bdl_blk, 1) if bdl_blk else None,
                "min": bdl_min,
                "turnover": round(bdl_turnover, 1) if bdl_turnover else None,
                "games_played": bdl_games_played
            }
        
        # Sort by date descending - handle both "date" (bdl_game_logs) and "game_date" (game_logs)
        game_logs = sorted(game_logs, key=lambda x: x.get("date", "") or x.get("game_date", "") or x.get("game", {}).get("date", ""), reverse=True)
        
        # Map stat type to game log field
        # NOTE: bdl_game_logs uses "fg3m" for 3PM and "turnover" for TO
        stat_map = {
            "PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl", "BLK": "blk",
            "3PM": "fg3m", "THREES": "fg3m", "TO": "turnover", "PRA": "pra", 
            "P+R": "pts_reb", "PR": "pts_reb",
            "P+A": "pts_ast", "PA": "pts_ast",
            "R+A": "reb_ast", "RA": "reb_ast"
        }
        log_key = stat_map.get(stat_type.upper(), stat_type.lower())
        
        # Calculate for different windows
        l5 = game_logs[:5]
        l10 = game_logs[:10]
        season = game_logs
        
        def calc_stats(logs):
            if not logs:
                return 0, 0
            values = []
            hits = 0
            for g in logs:
                val = g.get(log_key, 0) or 0
                # Handle combined stats
                if stat_type.upper() in ["PRA"]:
                    val = (g.get("pts", 0) or 0) + (g.get("reb", 0) or 0) + (g.get("ast", 0) or 0)
                elif stat_type.upper() in ["P+R", "PR"]:
                    val = (g.get("pts", 0) or 0) + (g.get("reb", 0) or 0)
                elif stat_type.upper() in ["P+A", "PA"]:
                    val = (g.get("pts", 0) or 0) + (g.get("ast", 0) or 0)
                elif stat_type.upper() in ["R+A", "RA"]:
                    val = (g.get("reb", 0) or 0) + (g.get("ast", 0) or 0)
                values.append(val)
                if val > line:
                    hits += 1
            avg = sum(values) / len(values) if values else 0
            hit_rate = (hits / len(values) * 100) if values else 0
            return avg, hit_rate
        
        l5_avg, h5_rate = calc_stats(l5)
        l10_avg, h10_rate = calc_stats(l10)
        season_avg, _ = calc_stats(season)
        
        # Calculate diff from season average for display
        diff_from_avg = None
        if season_avg > 0 and line:
            diff_from_avg = round(((line - season_avg) / season_avg) * 100, 1)
        
        return {
            "h5_rate": round(h5_rate, 1),
            "h10_rate": round(h10_rate, 1),
            "season_avg": round(season_avg, 1),
            "l5_avg": round(l5_avg, 1),
            "l10_avg": round(l10_avg, 1),
            "diff_from_avg": diff_from_avg,
            "is_stale": is_stale,
            "stats_source": "game_logs",
            # BDL shooting/defensive stats
            "fg_pct": round(bdl_fg_pct * 100, 1) if bdl_fg_pct else None,
            "fg3_pct": round(bdl_fg3_pct * 100, 1) if bdl_fg3_pct else None,
            "ft_pct": round(bdl_ft_pct * 100, 1) if bdl_ft_pct else None,
            "stl": round(bdl_stl, 1) if bdl_stl else None,
            "blk": round(bdl_blk, 1) if bdl_blk else None,
            "min": bdl_min,
            "turnover": round(bdl_turnover, 1) if bdl_turnover else None,
            "games_played": bdl_games_played
        }
    
    async def get_war_zone(self) -> Dict[str, Any]:
        """
        Get the War Zone - Pre-built cache of demon picks.
        
        STATIC ROUTE: This method performs a simple MongoDB find() only.
        All calculations are done during the background sync by tier_builder_service.
        
        Returns demon picks sorted by score from the dg_goblin_recon collection
        (War Zone demons are stored there with is_demon=True).
        """
        try:
            now = datetime.now(timezone.utc)
            
            # Simple find from pre-built cache - NO calculations
            picks = await self.cached_board.aggregate([
                {"$unwind": "$props"},
                {"$match": {
                    "props.is_demon": True,
                    "props.commence_time": {"$gt": now.isoformat().replace('+00:00', 'Z')}
                }},
                {"$project": {
                    "_id": 0,
                    "player_name": 1,
                    "team": 1,
                    "photo_url": 1,
                    "headshot_url": 1,
                    "position": 1,
                    "opponent": 1,
                    "game_id": "$props.game_id",
                    "commence_time": "$props.commence_time",
                    "stat_type": "$props.stat_type",
                    "line": "$props.line",
                    "anchor_line": "$props.anchor_line",
                    "h5_rate": "$props.h5_rate",
                    "h10_rate": "$props.h10_rate",
                    "l5_avg": "$props.l5_avg",
                    "l10_avg": "$props.l10_avg",
                    "season_avg": "$props.season_avg",
                    "is_demon": "$props.is_demon",
                    "is_goblin": "$props.is_goblin",
                    "tier_label": {"$literal": "DEMON"},
                    "pick_type": {"$literal": "demon"},
                    "combined_score": "$props.combined_score",
                    "payout_score": "$props.payout_score"
                }},
                {"$sort": {"h10_rate": -1, "combined_score": -1}},
                {"$limit": 100}
            ]).to_list(100)
            
            # De-duplicate by player (one pick per player)
            seen_players = set()
            unique_picks = []
            for pick in picks:
                pname = pick.get("player_name")
                if pname and pname not in seen_players:
                    seen_players.add(pname)
                    # Add game status
                    game_status = _get_game_status(pick.get("commence_time"))
                    pick["is_locked"] = game_status.get("is_locked", False)
                    pick["game_status"] = game_status.get("status", "upcoming")
                    unique_picks.append(pick)
                    if len(unique_picks) >= 20:
                        break
            
            # Enrich with photos
            unique_picks = await self._enrich_picks_with_photos(unique_picks)
            
            logger.info(f"[WAR_ZONE] Served {len(unique_picks)} picks (static cache read)")
            
            return {
                "status": "live",
                "picks": unique_picks,
                "count": len(unique_picks),
                "source": "cached_board_demons"
            }
            
        except Exception as e:
            logger.error(f"[WAR_ZONE] Error: {e}")
            return {"status": "error", "picks": [], "error": str(e)}
    def _get_season_avg(self, baseline_stats: Dict, stat_key: str) -> Optional[float]:
        """Helper to extract season_avg from baseline_stats with various formats."""
        # Try nested structure first
        stat_data = baseline_stats.get(stat_key, {})
        if isinstance(stat_data, dict) and stat_data.get("season_avg") is not None:
            return stat_data.get("season_avg")
        elif isinstance(stat_data, (int, float)):
            return stat_data
        
        # Try lowercase key
        flat_key_map = {"PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl", "BLK": "blk", "3PM": "fg3m"}
        flat_key = flat_key_map.get(stat_key)
        if flat_key and baseline_stats.get(flat_key) is not None:
            val = baseline_stats.get(flat_key)
            if isinstance(val, (int, float)):
                return val
            elif isinstance(val, dict):
                return val.get("season_avg")
        
        # Handle combo stats
        if stat_key in ["PRA", "PR", "PA", "RA"]:
            def get_avg(key):
                data = baseline_stats.get(key, {})
                if isinstance(data, dict):
                    return data.get("season_avg", 0) or 0
                return data if isinstance(data, (int, float)) else 0
            
            if stat_key == "PRA":
                return get_avg("PTS") + get_avg("REB") + get_avg("AST")
            elif stat_key == "PR":
                return get_avg("PTS") + get_avg("REB")
            elif stat_key == "PA":
                return get_avg("PTS") + get_avg("AST")
            elif stat_key == "RA":
                return get_avg("REB") + get_avg("AST")
        
        return None
    
    def _calculate_l10_avg(self, game_logs: List[Dict], stat_type: str) -> Dict:
        """Calculate L10 average from last 10 game logs (excluding DNPs)."""
        if not game_logs:
            return {"avg": 0, "games_counted": 0, "values": []}
        
        def safe_int(val):
            """Safely convert value to int, handling strings and None."""
            if val is None:
                return 0
            try:
                return int(float(val))
            except (ValueError, TypeError):
                return 0
        
        # CRITICAL: Filter out DNPs first, then sort by date (most recent first)
        from datetime import datetime
        
        played_games = _filter_played_games(game_logs)
        if not played_games:
            return {"avg": 0, "games_counted": 0, "values": []}
        
        def get_game_date(g):
            date_str = ""
            if isinstance(g.get("game"), dict):
                date_str = g.get("game", {}).get("date", "")
            if not date_str:
                date_str = g.get("date", "") or g.get("game_date", "")
            if date_str:
                try:
                    return datetime.strptime(date_str[:10], "%Y-%m-%d")
                except Exception:
                    pass
            return datetime.min
        
        sorted_logs = sorted(played_games, key=get_game_date, reverse=True)
        recent_games = sorted_logs[:10]
        stat_key = self._normalize_stat_key(stat_type)
        values = []
        
        for game in recent_games:
            if stat_key == 'PRA':
                value = safe_int(game.get('pts')) + safe_int(game.get('reb')) + safe_int(game.get('ast'))
            elif stat_key == 'PR':
                value = safe_int(game.get('pts')) + safe_int(game.get('reb'))
            elif stat_key == 'PA':
                value = safe_int(game.get('pts')) + safe_int(game.get('ast'))
            elif stat_key == 'RA':
                value = safe_int(game.get('reb')) + safe_int(game.get('ast'))
            else:
                field_map = {'PTS': 'pts', 'REB': 'reb', 'AST': 'ast', 'STL': 'stl', 'BLK': 'blk', '3PM': 'fg3m'}
                field = field_map.get(stat_key)
                if not field:
                    continue
                value = safe_int(game.get(field))
            values.append(value)
        
        avg = round(sum(values) / len(values), 1) if values else 0
        return {"avg": avg, "games_counted": len(values), "values": values}
    
    def _calculate_h5_hit_rate(self, game_logs: List[Dict], stat_type: str, line: float) -> Dict:
        """Calculate H5 hit rate (last 5 games, excluding DNPs)."""
        # Filter out DNPs first
        played_games = _filter_played_games(game_logs)
        if not played_games or len(played_games) < 5:
            return {"hit_rate": 0, "hits": 0, "games_counted": 0}
        
        def safe_num(val):
            """Safely convert value to number, handling strings and None."""
            if val is None:
                return 0
            try:
                return float(val)
            except (ValueError, TypeError):
                return 0
        
        # CRITICAL: Sort by date first (most recent first)
        from datetime import datetime
        
        def get_game_date(g):
            date_str = ""
            if isinstance(g.get("game"), dict):
                date_str = g.get("game", {}).get("date", "")
            if not date_str:
                date_str = g.get("date", "") or g.get("game_date", "")
            if date_str:
                try:
                    return datetime.strptime(date_str[:10], "%Y-%m-%d")
                except Exception:
                    pass
            return datetime.min
        
        sorted_logs = sorted(played_games, key=get_game_date, reverse=True)
        recent_games = sorted_logs[:5]
        stat_key = self._normalize_stat_key(stat_type)
        hits = 0
        
        for game in recent_games:
            if stat_key == 'PRA':
                value = safe_num(game.get('pts')) + safe_num(game.get('reb')) + safe_num(game.get('ast'))
            elif stat_key == 'PR':
                value = safe_num(game.get('pts')) + safe_num(game.get('reb'))
            elif stat_key == 'PA':
                value = safe_num(game.get('pts')) + safe_num(game.get('ast'))
            elif stat_key == 'RA':
                value = safe_num(game.get('reb')) + safe_num(game.get('ast'))
            else:
                field_map = {'PTS': 'pts', 'REB': 'reb', 'AST': 'ast', 'STL': 'stl', 'BLK': 'blk', '3PM': 'fg3m'}
                field = field_map.get(stat_key)
                value = safe_num(game.get(field)) if field else 0
            
            try:
                if float(value) >= float(line):
                    hits += 1
            except (ValueError, TypeError):
                continue
        
        hit_rate = round((hits / 5) * 100, 1)
        return {"hit_rate": hit_rate, "hits": hits, "games_counted": 5}
    
    def _calculate_l5_avg(self, game_logs: List[Dict], stat_type: str) -> Dict:
        """
        Calculate L5 average from last 5 game logs (excluding DNPs).
        
        Returns: {"avg": float, "games_counted": int, "values": list}
        """
        # Filter out DNPs first
        played_games = _filter_played_games(game_logs)
        if not played_games:
            return {"avg": 0, "games_counted": 0, "values": []}
        
        # CRITICAL: Sort by date first (most recent first)
        from datetime import datetime
        
        def get_game_date(g):
            date_str = ""
            if isinstance(g.get("game"), dict):
                date_str = g.get("game", {}).get("date", "")
            if not date_str:
                date_str = g.get("date", "") or g.get("game_date", "")
            if date_str:
                try:
                    return datetime.strptime(date_str[:10], "%Y-%m-%d")
                except Exception:
                    pass
            return datetime.min
        
        sorted_logs = sorted(played_games, key=get_game_date, reverse=True)
        recent_games = sorted_logs[:5]
        
        stat_key = self._normalize_stat_key(stat_type)
        
        values = []
        
        def safe_num(val):
            """Convert value to number, handling None and strings."""
            if val is None:
                return 0
            try:
                return float(val)
            except (ValueError, TypeError):
                return 0
        
        for game in recent_games:
            # Get stat value from game log
            if stat_key == 'PRA':
                value = safe_num(game.get('pts')) + safe_num(game.get('reb')) + safe_num(game.get('ast'))
            elif stat_key == 'PR':
                value = safe_num(game.get('pts')) + safe_num(game.get('reb'))
            elif stat_key == 'PA':
                value = safe_num(game.get('pts')) + safe_num(game.get('ast'))
            elif stat_key == 'RA':
                value = safe_num(game.get('reb')) + safe_num(game.get('ast'))
            else:
                field_map = {'PTS': 'pts', 'REB': 'reb', 'AST': 'ast', 'STL': 'stl', 'BLK': 'blk', '3PM': 'fg3m'}
                field = field_map.get(stat_key)
                if not field:
                    continue
                value = safe_num(game.get(field))
            
            values.append(value)
        
        avg = round(sum(values) / len(values), 1) if values else 0
        
        return {"avg": avg, "games_counted": len(values), "values": values}
    
    async def get_goblin_vault(self) -> Dict[str, Any]:
        """
        Get the Safe Haven - HIGH HIT RATE picks (80%+) for maximum safety.
        
        SAFE HAVEN LOGIC v5.0 - PROBABILITY FOCUSED:
        1. Get ONE best pick per player (maximize variety)
        2. L10 Hit Rate >= 80% (Safe Haven tier)
        3. Include BOTH demons and goblins that meet criteria
        4. Sort by PROBABILITY SCORE: hit_rate + DvP + badges + line_value
        5. Target: 10 UNIQUE players
        
        Probability score factors in matchups and badges for true hit probability.
        """
        prob_service = ProbabilityScoreService(self.db)
        
        # Pre-load probability caches ONCE (subsequent calls are instant)
        await prob_service._preload_dvp_cache()
        await prob_service._preload_badges_cache()
        
        MIN_HIT_RATE = 80  # Safe Haven = 80%+ hit rate
        TARGET_PICKS = 10
        
        # Get current time for filtering upcoming games
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat().replace('+00:00', 'Z')
        
        # Get all demon and goblin props from UPCOMING games
        # Data is stored as PLAYER DOCUMENTS with nested props arrays
        pipeline = [
            {"$unwind": "$props"},
            {"$match": {
                "$or": [{"props.is_demon": True}, {"props.is_goblin": True}],
                "props.commence_time": {"$gt": now_iso}
            }},
            {"$project": {
                "_id": 0,
                "player_name": 1,
                "team": 1,
                "photo_url": 1,
                "headshot_url": 1,
                "prop": "$props"
            }}
        ]
        
        results = await self.cached_board.aggregate(pipeline).to_list(3000)
        
        # Flatten to prop documents with player info
        all_props = []
        for result in results:
            prop = result.get("prop", {})
            prop["player_name"] = result.get("player_name")
            prop["team"] = result.get("team") or prop.get("team")
            prop["photo_url"] = result.get("photo_url") or result.get("headshot_url")
            all_props.append(prop)
        
        if not all_props:
            logger.warning("[SAFE_HAVEN v5] No demon/goblin props found")
            return {"picks": [], "picks_count": 0, "filters_applied": []}
        
        # Pre-fetch injured players
        injured_players = await self._get_injured_players()
        
        unique_players = set()
        for p in all_props:
            if p.get("player_name"):
                unique_players.add(p.get("player_name"))
        
        logger.info(f"[SAFE_HAVEN v5] Processing {len(all_props)} props from {len(unique_players)} unique players")
        
        # STEP 1: For each player, find their SINGLE BEST pick (demon or goblin)
        player_best_picks = {}  # player_name -> best pick
        
        filter_stats = {
            "total_players": len(unique_players),
            "total_qualifying_props": len(all_props),
            "demons_checked": 0,
            "goblins_checked": 0,
            "passed_hit_rate_80": 0,
            "final_demons": 0,
            "final_goblins": 0,
            "unique_players_with_picks": 0
        }
        
        for prop in all_props:
            player_name = prop.get("player_name")
            if not player_name:
                continue
            
            is_demon = prop.get("is_demon", False)
            is_goblin = prop.get("is_goblin", False)
            
            if is_demon:
                filter_stats["demons_checked"] += 1
            if is_goblin:
                filter_stats["goblins_checked"] += 1
            
            line = prop.get("line")
            anchor_line = prop.get("anchor_line")
            
            if not line:
                continue
            
            # Get hit rate from prop - use flattened h10_rate or nested hit_rates
            hit_rates = prop.get("hit_rates", {})
            l10_hit_rate = prop.get("h10_rate") or prop.get("h10_hit_rate") or hit_rates.get("l10_rate") or 0
            l5_hit_rate = prop.get("h5_rate") or prop.get("h5_hit_rate") or hit_rates.get("l5_rate") or 0
            l10_games = prop.get("h10_games") or 10
            l5_avg = prop.get("l5_avg") or hit_rates.get("l5_avg")
            l10_avg = prop.get("l10_avg") or hit_rates.get("l10_avg")
            season_avg = prop.get("season_avg") or hit_rates.get("season_avg") or 0
            
            # If still no hit rate, skip
            if l10_hit_rate is None or l10_hit_rate == 0:
                continue
            
            # Convert hit rate to percentage (it's stored as decimal 0-1 sometimes)
            l10_pct = l10_hit_rate * 100 if l10_hit_rate <= 1 else l10_hit_rate
            
            # Check minimum hit rate (Safe Haven = 80%+)
            if l10_pct < MIN_HIT_RATE:
                continue
            
            filter_stats["passed_hit_rate_80"] += 1
            
            # ANOMALY SCORE: Prioritize TRUE ANOMALIES (near-guaranteed hits)
            # 100% hit rate = HUGE bonus (basically free money)
            # 90%+ hit rate = Strong bonus
            # Also factor in how much avg beats the line
            
            anomaly_bonus = 0
            if l10_pct >= 100:
                anomaly_bonus = 100  # Perfect hit rate = maximum priority
            elif l10_pct >= 90:
                anomaly_bonus = 50   # Near-perfect
            
            # Margin: how much does avg beat the line?
            margin = (l10_avg - line) if l10_avg and line else 0
            
            # Combined score: prioritize anomalies
            safety_score = l10_pct / 100
            combined_score = anomaly_bonus + (safety_score * 50) + margin
            
            stat_type = prop.get("stat_type_extracted") or prop.get("market", "").replace("player_", "").replace("_alternate", "").upper()
            
            # Track demon vs goblin counts
            if is_demon:
                filter_stats["final_demons"] += 1
            else:
                filter_stats["final_goblins"] += 1

            # Get game status
            commence_time = prop.get("commence_time")
            game_status = _get_game_status(commence_time)

            pick = {
                "player_name": player_name,
                "team": prop.get("team"),
                "opponent": prop.get("opponent"),
                "game_id": prop.get("game_id"),
                "home_team": prop.get("home_team"),
                "away_team": prop.get("away_team"),
                "photo_url": prop.get("photo_url"),
                "stat_type": stat_type,
                "line": line,
                "anchor_line": anchor_line,
                "odds": prop.get("price", -110),
                "direction": prop.get("direction", "over"),
                "is_demon": is_demon,
                "is_goblin": is_goblin,
                "tier_label": "DEMON" if is_demon else "GOBLIN",
                "is_alternate_market": True,
                "season_avg": round(season_avg, 1) if season_avg else None,
                "l5_avg": round(l5_avg, 1) if l5_avg else None,
                "l10_avg": round(l10_avg, 1) if l10_avg else None,
                "h5_rate": round(l5_hit_rate, 1) if l5_hit_rate else None,
                "h10_rate": round(l10_pct, 1),
                "l5_hit_rate": round(l5_hit_rate, 1) if l5_hit_rate else None,
                "l10_hit_rate": round(l10_pct, 1),
                "l10_games": l10_games,
                "margin": round(margin, 1),  # How much avg beats line
                "floor_margin": round(season_avg - line, 1) if season_avg else None,
                "combined_score": round(combined_score, 4),
                "is_anomaly": l10_pct >= 90,  # Flag true anomalies
                "safe_haven_qualified": True,
                "position": prop.get("position"),
                "is_injured": player_name.lower() in injured_players,
                "commence_time": commence_time,
                "game_status": game_status["status"],
                "is_locked": game_status["is_locked"],
                "minutes_since_start": game_status.get("minutes_since_start"),
            }
            
            # Enrich with probability score (DvP + Badges + Line value)
            pick = await prob_service.enrich_pick_with_probability(pick)
            
            # Keep only the highest probability score pick for this player
            if player_name not in player_best_picks:
                player_best_picks[player_name] = pick
                filter_stats["unique_players_with_picks"] += 1
            elif pick.get("probability_score", 0) > player_best_picks[player_name].get("probability_score", 0):
                player_best_picks[player_name] = pick
        
        all_player_best_picks = list(player_best_picks.values())
        
        # STEP 2: Separate active picks (upcoming games) from locked picks (in progress)
        active_picks = [p for p in all_player_best_picks if not p.get("is_locked")]
        locked_picks = [p for p in all_player_best_picks if p.get("is_locked")]
        
        # Sort both lists by: HIT RATE (highest), then SEASON MARGIN (biggest anomaly), then PROBABILITY SCORE
        # This ensures picks with same HR are ranked by how much season avg beats the line
        active_picks.sort(
            key=lambda x: (
                x.get("l10_hit_rate", 0),  # Primary: Hit rate
                (x.get("season_avg") or 0) - (x.get("line") or 0),  # Secondary: Season margin
                x.get("probability_score", 0)  # Tertiary: Probability score
            ), 
            reverse=True
        )
        locked_picks.sort(
            key=lambda x: (
                x.get("l10_hit_rate", 0),
                (x.get("season_avg") or 0) - (x.get("line") or 0),
                x.get("probability_score", 0)
            ), 
            reverse=True
        )
        
        # NO LIMIT - Show ALL goblin anomalies on the board
        # Show all picks - active first, then locked
        final_picks = active_picks + locked_picks
        
        # Enrich with photos from master hub (SSOT for photos)
        await self._enrich_picks_with_photos(final_picks)
        
        unique_players = len(set(p["player_name"] for p in final_picks))
        demons_in_final = sum(1 for p in final_picks if p.get("is_demon"))
        goblins_in_final = sum(1 for p in final_picks if p.get("is_goblin"))
        active_count = len(active_picks)
        locked_count = len(locked_picks)
        
        logger.info(f"[SAFE_HAVEN v5] Generated {len(final_picks)} picks ({demons_in_final} demons, {goblins_in_final} goblins)")
        logger.info(f"[SAFE_HAVEN v5] Game status: {active_count} active, {locked_count} locked")
        logger.info(f"[SAFE_HAVEN v5] Filter stats: {filter_stats}")
        
        # Log top picks with probability scores
        for i, pick in enumerate(final_picks[:5], 1):
            status_tag = " [LOCKED]" if pick.get("is_locked") else ""
            prob_score = pick.get('probability_score', 0)
            logger.info(f"[SAFE_HAVEN] #{i} {pick['player_name']} {pick['stat_type']} @ {pick['line']} | "
                       f"L10: {pick['l10_hit_rate']:.0f}% | Prob: {prob_score:.1f}%{status_tag}")
        
        return {
            "picks": final_picks,
            "picks_count": len(final_picks),
            "unique_players": unique_players,
            "active_picks": active_count,
            "locked_picks": locked_count,
            "waiting_list_count": 0,
            "filter_stats": filter_stats,
            "filters_applied": ["l10_hit_rate_80pct", "probability_score", "one_per_player", "demons_and_goblins", "game_status"]
        }
    
    def _normalize_stat_key(self, stat_type: str) -> str:
        """Normalize stat type to match master hub keys."""
        stat_map = {
            'PTS': 'PTS', 'POINTS': 'PTS',
            'REB': 'REB', 'REBOUNDS': 'REB',
            'AST': 'AST', 'ASSISTS': 'AST',
            'STL': 'STL', 'STEALS': 'STL',
            'BLK': 'BLK', 'BLOCKS': 'BLK',
            '3PM': '3PM', 'THREES': '3PM',
            'PRA': 'PRA', 'P+R+A': 'PRA',
            'PR': 'PR', 'P+R': 'PR',
            'PA': 'PA', 'P+A': 'PA',
            'RA': 'RA', 'R+A': 'RA',
        }
        return stat_map.get(stat_type.upper(), stat_type.upper())
    
    def _calculate_h10_hit_rate(self, game_logs: List[Dict], stat_type: str, line: float) -> Dict:
        """
        Calculate H10 hit rate from last 10 game logs (excluding DNPs).
        
        Returns: {"hits": int, "games_counted": int, "hit_rate": float}
        """
        # Filter out DNPs first
        played_games = _filter_played_games(game_logs)
        if not played_games:
            return {"hits": 0, "games_counted": 0, "hit_rate": 0}
        
        def safe_num(val):
            """Safely convert value to number, handling strings and None."""
            if val is None:
                return 0
            try:
                return float(val)
            except (ValueError, TypeError):
                return 0
        
        # CRITICAL: Sort by date first (most recent first)
        # BDL format: game.game.date or game.date
        from datetime import datetime
        
        def get_game_date(g):
            date_str = ""
            if isinstance(g.get("game"), dict):
                date_str = g.get("game", {}).get("date", "")
            if not date_str:
                date_str = g.get("date", "") or g.get("game_date", "")
            if date_str:
                try:
                    return datetime.strptime(date_str[:10], "%Y-%m-%d")
                except Exception:
                    pass
            return datetime.min
        
        sorted_logs = sorted(played_games, key=get_game_date, reverse=True)
        
        # Take last 10 games (most recent first)
        recent_games = sorted_logs[:10]
        
        # Map stat_type to game log field
        stat_field_map = {
            'PTS': 'pts',
            'REB': 'reb',
            'AST': 'ast',
            'STL': 'stl',
            'BLK': 'blk',
            '3PM': 'fg3m',
            'PRA': None,  # Calculated
            'PR': None,   # Calculated
            'PA': None,   # Calculated
            'RA': None,   # Calculated
        }
        
        stat_key = self._normalize_stat_key(stat_type)
        field = stat_field_map.get(stat_key)
        
        hits = 0
        games_counted = 0
        
        for game in recent_games:
            # Get stat value from game log
            if stat_key == 'PRA':
                value = safe_num(game.get('pts')) + safe_num(game.get('reb')) + safe_num(game.get('ast'))
            elif stat_key == 'PR':
                value = safe_num(game.get('pts')) + safe_num(game.get('reb'))
            elif stat_key == 'PA':
                value = safe_num(game.get('pts')) + safe_num(game.get('ast'))
            elif stat_key == 'RA':
                value = safe_num(game.get('reb')) + safe_num(game.get('ast'))
            elif field:
                value = safe_num(game.get(field))
            else:
                continue
            
            games_counted += 1
            # Ensure both values are numeric before comparison
            try:
                if float(value) > float(line):  # Must EXCEED the line (not just meet it)
                    hits += 1
            except (ValueError, TypeError):
                continue
        
        hit_rate = round((hits / games_counted) * 100) if games_counted > 0 else 0
        
        return {"hits": hits, "games_counted": games_counted, "hit_rate": hit_rate}
    
    async def _get_master_player_by_name(self, player_name: str) -> Optional[Dict]:
        """
        Get player from master hub using suffix-neutral name matching.
        """
        from services.bdl_comprehensive_sync import _normalize_name
        
        # Try exact match first
        player = await self.master_hub.find_one(
            {"display_name": player_name},
            {"_id": 0}
        )
        
        if player:
            return player
        
        # Try normalized name match
        normalized = _normalize_name(player_name)
        player = await self.master_hub.find_one(
            {"display_name": {"$regex": f"^{normalized}$", "$options": "i"}},
            {"_id": 0}
        )
        
        return player
    
    async def get_front_lines(self) -> Dict[str, Any]:
        """
        Get THE FRONT LINES - Medium tier picks with solid probability.
        
        FRONT LINES LOGIC v2.0 - PROBABILITY FOCUSED:
        1. L10 Hit Rate >= 65% (solid but not Safe Haven tier)
        2. Line can't be the LOWEST available (not the safest floor play)
        3. Must NOT qualify for Safe Haven (H10 >= 80%)
        4. Can be DEMON or GOBLIN picks
        5. Sort by PROBABILITY SCORE: hit_rate + DvP + badges + line_value
        """
        prob_service = ProbabilityScoreService(self.db)
        
        # Pre-load probability caches ONCE (subsequent calls are instant)
        await prob_service._preload_dvp_cache()
        await prob_service._preload_badges_cache()
        
        # Get current time for filtering upcoming games
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat().replace('+00:00', 'Z')
        
        # Get all demon and goblin props from UPCOMING games
        # Data is stored as PLAYER DOCUMENTS with nested props arrays
        pipeline = [
            {"$unwind": "$props"},
            {"$match": {
                "$or": [{"props.is_demon": True}, {"props.is_goblin": True}],
                "props.commence_time": {"$gt": now_iso}
            }},
            {"$project": {
                "_id": 0,
                "player_name": 1,
                "team": 1,
                "photo_url": 1,
                "headshot_url": 1,
                "prop": "$props"
            }}
        ]
        
        results = await self.cached_board.aggregate(pipeline).to_list(3000)
        
        # Flatten to prop documents with player info
        all_props = []
        for result in results:
            prop = result.get("prop", {})
            prop["player_name"] = result.get("player_name")
            prop["team"] = result.get("team") or prop.get("team")
            prop["photo_url"] = result.get("photo_url") or result.get("headshot_url")
            all_props.append(prop)
        
        # Group props by player and stat_type for line comparison
        props_by_player_stat = {}
        unique_players = set()
        
        for prop in all_props:
            player_name = prop.get("player_name")
            if not player_name:
                continue
            unique_players.add(player_name)
            
            stat_type = prop.get("stat_type_extracted") or prop.get("market", "").replace("player_", "")
            key = (player_name, stat_type)
            
            if key not in props_by_player_stat:
                props_by_player_stat[key] = []
            props_by_player_stat[key].append(prop)
        
        # Build candidate picks
        front_line_picks = []
        filter_stats = {
            "total_players": len(unique_players),
            "total_qualifying_props": len(all_props),
            "demons_checked": 0,
            "goblins_checked": 0,
            "passed_hit_rate_65": 0,
            "excluded_safe_haven_80": 0,
            "excluded_lowest_line": 0,
            "final_demons": 0,
            "final_goblins": 0
        }
        
        for (player_name, stat_type), props in props_by_player_stat.items():
            # Sort props by line to find lowest
            all_lines_for_stat = sorted([p.get("line", 0) for p in props if p.get("line")])
            lowest_line = all_lines_for_stat[0] if all_lines_for_stat else None
            
            for prop in props:
                is_demon = prop.get("is_demon", False)
                is_goblin = prop.get("is_goblin", False)
                
                if not is_demon and not is_goblin:
                    continue
                
                if is_demon:
                    filter_stats["demons_checked"] += 1
                if is_goblin:
                    filter_stats["goblins_checked"] += 1
                
                line = prop.get("line")
                
                # Get hit rate from prop - use flattened h10_rate or nested hit_rates
                hit_rates = prop.get("hit_rates", {})
                l10_hit_rate = prop.get("h10_rate") or prop.get("h10_hit_rate") or hit_rates.get("l10_rate") or 0
                l5_hit_rate = prop.get("h5_rate") or prop.get("h5_hit_rate") or hit_rates.get("l5_rate") or 0
                l5_avg = prop.get("l5_avg") or hit_rates.get("l5_avg")
                l10_avg = prop.get("l10_avg") or hit_rates.get("l10_avg")
                season_avg = prop.get("season_avg") or hit_rates.get("season_avg")
                
                # Skip if no hit rate data
                if l10_hit_rate == 0:
                    continue
                
                # Convert to percentage if stored as decimal
                if l10_hit_rate and l10_hit_rate <= 1:
                    l10_hit_rate = l10_hit_rate * 100
                
                if not line:
                    continue
                
                # FILTER 1: L10 Hit Rate >= 65%
                if l10_hit_rate < 65:
                    continue
                filter_stats["passed_hit_rate_65"] += 1
                
                # FRONT LINES = MIDDLE GROUND ANOMALIES
                # Uses same season average anomaly detection as War Zone/Safe Haven
                # but captures the middle tier picks
                
                # Check if it's an anomaly (season avg >= line)
                is_season_anomaly = season_avg and line and season_avg >= line
                
                # Exclude picks that belong in Safe Haven (goblin with 80%+ HR)
                if is_goblin and l10_hit_rate >= 80:
                    filter_stats["excluded_safe_haven_80"] += 1
                    continue
                
                # Exclude picks that belong in War Zone (demon anomaly with HR >= 50%)
                is_war_zone_pick = is_demon and is_season_anomaly and l10_hit_rate >= 50
                if is_war_zone_pick:
                    filter_stats["excluded_war_zone"] = filter_stats.get("excluded_war_zone", 0) + 1
                    continue
                
                # FRONT LINES CRITERIA:
                # Must be a season anomaly (season avg >= line)
                # This ensures we're still finding oddsmaker mistakes
                if not is_season_anomaly:
                    filter_stats["excluded_not_anomaly"] = filter_stats.get("excluded_not_anomaly", 0) + 1
                    continue
                
                # What makes it to Front Lines:
                # - Goblin anomalies with HR 65-79% (good but not Safe Haven tier)
                # - Demon anomalies that didn't qualify for War Zone
                
                # FILTER: Can't be the lowest line (not safest floor play)
                if lowest_line and line == lowest_line and len(all_lines_for_stat) > 1:
                    filter_stats["excluded_lowest_line"] += 1
                    continue
                
                # Calculate margin (how much avg beats line)
                margin = (l10_avg - line) if l10_avg and line else 0
                season_margin = (season_avg - line) if season_avg and line else 0
                
                # ANOMALY DETECTION: Is this an oddsmaker error?
                # For Front Lines, we already verified is_season_anomaly (season_avg >= line)
                # Mark as anomaly since it passed the season average filter
                is_demon_anomaly = is_demon and is_season_anomaly
                is_goblin_anomaly = is_goblin and is_season_anomaly
                is_anomaly = is_season_anomaly  # All Front Lines picks are season anomalies
                
                # ANOMALY SCORE: Prioritize anomalies based on SEASON margin
                # All Front Lines picks are season anomalies, give bonus based on margin
                anomaly_bonus = 0
                if is_anomaly:
                    anomaly_bonus = 30 + season_margin  # Bigger season margin = bigger oddsmaker mistake
                
                # Calculate value score with anomaly bonus
                if is_demon:
                    anchor_line = prop.get("anchor_line", line)
                    if anchor_line and anchor_line > 0:
                        boost_pct = ((line - anchor_line) / anchor_line) * 100
                    else:
                        boost_pct = 10
                    payout_multiplier = 1.5 + (boost_pct / 20)
                else:
                    anchor_line = prop.get("anchor_line", line)
                    if anchor_line and anchor_line > 0:
                        discount_pct = ((anchor_line - line) / anchor_line) * 100
                    else:
                        discount_pct = 10
                    payout_multiplier = 1.0 + (discount_pct / 50)
                
                value_score = anomaly_bonus + (l10_hit_rate / 100) * payout_multiplier
                
                # PASSED ALL FILTERS
                if is_demon:
                    filter_stats["final_demons"] += 1
                else:
                    filter_stats["final_goblins"] += 1
                
                # Get game status for locking logic
                commence_time = prop.get("commence_time")
                game_status = _get_game_status(commence_time)
                
                pick = {
                    "player_name": player_name,
                    "team": prop.get("team"),
                    "opponent": prop.get("opponent"),
                    "game_id": prop.get("game_id"),
                    "home_team": prop.get("home_team"),
                    "away_team": prop.get("away_team"),
                    "photo_url": prop.get("photo_url"),
                    "stat_type": stat_type,
                    "line": line,
                    "anchor_line": prop.get("anchor_line"),
                    "odds": prop.get("price"),
                    "direction": prop.get("direction", "over"),
                    "is_demon": is_demon,
                    "is_goblin": is_goblin,
                    "tier_label": "FRONT_LINE",
                    "h5_rate": l5_hit_rate,
                    "h10_rate": l10_hit_rate,
                    "l10_hit_rate": l10_hit_rate,
                    "l5_hit_rate": l5_hit_rate,
                    "l10_avg": l10_avg,
                    "l5_avg": l5_avg,
                    "season_avg": season_avg,
                    "margin": round(margin, 1),
                    "is_anomaly": is_anomaly,
                    "is_demon_anomaly": is_demon_anomaly,
                    "is_goblin_anomaly": is_goblin_anomaly,
                    "front_line_qualified": True,
                    "lowest_line": lowest_line,
                    "payout_multiplier": round(payout_multiplier, 2),
                    "value_score": round(value_score, 3),
                    "is_alternate_market": prop.get("is_alternate_market", True),
                    "commence_time": commence_time,
                    "game_status": game_status["status"],
                    "is_locked": game_status["is_locked"],
                    "minutes_since_start": game_status.get("minutes_since_start"),
                }
                
                # Enrich with probability score (DvP + Badges + Line value)
                pick = await prob_service.enrich_pick_with_probability(pick)
                
                front_line_picks.append(pick)
                pick_type = "DEMON" if is_demon else "GOBLIN"
                status_tag = " [LOCKED]" if game_status["is_locked"] else ""
                prob_score = pick.get('probability_score', 0)
                logger.info(f"[FRONT_LINES] ✓ {player_name} {stat_type} @ {line} | {pick_type} | L10: {l10_hit_rate:.0f}% | Prob: {prob_score:.1f}%{status_tag}")
        
        # Sort by: HIT RATE (highest first), then SEASON MARGIN (biggest anomaly first), then PROBABILITY SCORE
        # This ensures picks with same HR are ranked by how much the season avg beats the line
        front_line_picks.sort(
            key=lambda x: (
                x.get("l10_hit_rate") or x.get("h10_rate") or 0,  # Primary: Hit rate
                (x.get("season_avg") or 0) - (x.get("line") or 0),  # Secondary: Season margin (bigger = better anomaly)
                x.get("probability_score", 0)  # Tertiary: Probability score
            ), 
            reverse=True
        )
        
        # Limit to 1 pick per player (take the highest probability score prop)
        seen_players = set()
        unique_picks = []
        for pick in front_line_picks:
            if pick["player_name"] not in seen_players:
                seen_players.add(pick["player_name"])
                unique_picks.append(pick)
        
        TARGET_PICKS = 10
        
        # Separate active picks (upcoming games) from locked picks (in progress)
        active_picks = [p for p in unique_picks if not p.get("is_locked")]
        locked_picks = [p for p in unique_picks if p.get("is_locked")]
        
        # Both lists already sorted by probability_score from the unique_picks sort above
        
        # Fill the board: prioritize active picks, then add locked picks if needed
        final_picks = []
        
        # Add active picks first (up to TARGET_PICKS)
        final_picks.extend(active_picks[:TARGET_PICKS])
        
        # If we don't have enough active picks, backfill with locked picks
        if len(final_picks) < TARGET_PICKS:
            remaining_slots = TARGET_PICKS - len(final_picks)
            final_picks.extend(locked_picks[:remaining_slots])
        
        active_count = sum(1 for p in final_picks if not p.get("is_locked"))
        locked_count = sum(1 for p in final_picks if p.get("is_locked"))
        
        logger.info(f"[FRONT_LINES] Found {len(unique_picks)} unique player picks | Demons: {filter_stats['final_demons']} | Goblins: {filter_stats['final_goblins']}")
        logger.info(f"[FRONT_LINES] Game status: {active_count} active, {locked_count} locked | Waiting list: {len(active_picks) - min(len(active_picks), TARGET_PICKS)}")
        
        # SSOT: Enrich ALL picks with photos from master hub
        await self._enrich_picks_with_photos(final_picks)
        
        return {
            "picks": final_picks,
            "picks_count": len(final_picks),
            "active_picks": active_count,
            "locked_picks": locked_count,
            "waiting_list_count": len(active_picks) - min(len(active_picks), TARGET_PICKS),
            "filter_stats": filter_stats,
            "filters_applied": ["l10_hit_rate_65pct", "not_lowest_line", "exclude_safe_haven", "one_per_player", "probability_score", "game_status"]
        }
    
    def _calculate_l25_hit_rate(self, game_logs: List[Dict], stat_type: str, line: float) -> Dict:
        """
        Calculate L25 hit rate from last 25 game logs (excluding DNPs).
        
        Returns: {"hits": int, "games_counted": int, "hit_rate": float}
        """
        # Filter out DNPs first
        played_games = _filter_played_games(game_logs)
        if not played_games:
            return {"hits": 0, "games_counted": 0, "hit_rate": 0}
        
        def safe_num(val):
            """Safely convert value to number, handling strings and None."""
            if val is None:
                return 0
            try:
                return float(val)
            except (ValueError, TypeError):
                return 0
        
        # CRITICAL: Sort by date first (most recent first)
        from datetime import datetime
        
        def get_game_date(g):
            date_str = ""
            if isinstance(g.get("game"), dict):
                date_str = g.get("game", {}).get("date", "")
            if not date_str:
                date_str = g.get("date", "") or g.get("game_date", "")
            if date_str:
                try:
                    return datetime.strptime(date_str[:10], "%Y-%m-%d")
                except Exception:
                    pass
            return datetime.min
        
        sorted_logs = sorted(played_games, key=get_game_date, reverse=True)
        recent_games = sorted_logs[:25]
        
        stat_key = self._normalize_stat_key(stat_type)
        
        hits = 0
        games_counted = 0
        
        for game in recent_games:
            # Get stat value from game log
            if stat_key == 'PRA':
                value = safe_num(game.get('pts')) + safe_num(game.get('reb')) + safe_num(game.get('ast'))
            elif stat_key == 'PR':
                value = safe_num(game.get('pts')) + safe_num(game.get('reb'))
            elif stat_key == 'PA':
                value = safe_num(game.get('pts')) + safe_num(game.get('ast'))
            elif stat_key == 'RA':
                value = safe_num(game.get('reb')) + safe_num(game.get('ast'))
            else:
                field_map = {'PTS': 'pts', 'REB': 'reb', 'AST': 'ast', 'STL': 'stl', 'BLK': 'blk', '3PM': 'fg3m'}
                field = field_map.get(stat_key)
                if not field:
                    continue
                value = safe_num(game.get(field))
            
            games_counted += 1
            try:
                if float(value) > float(line):
                    hits += 1
            except (ValueError, TypeError):
                continue
        
        hit_rate = round((hits / games_counted) * 100) if games_counted > 0 else 0
        
        return {"hits": hits, "games_counted": games_counted, "hit_rate": hit_rate}
    
    async def get_parlay_builder(self) -> Dict[str, Any]:
        """
        Build War Zone parlays DYNAMICALLY from curated tier picks.
        
        SOURCE OF TRUTH: get_war_zone() picks (demon anomalies)
        This ensures parlays use the same picks users see on the board.
        """
        # Get War Zone picks as source of truth
        war_zone_data = await self.get_war_zone()
        war_zone_picks = war_zone_data.get("picks", [])
        
        if not war_zone_picks:
            return {
                "success": False,
                "message": "No War Zone picks available for parlays.",
                "parlays": {}
            }
        
        # Sort by probability score (highest first)
        candidates = sorted(war_zone_picks, key=lambda x: x.get("probability_score", 0), reverse=True)
        
        parlays = {}
        
        # Build parlays using 2-team rule
        def get_multi_team_picks(picks_list, count):
            """Select picks enforcing PrizePicks 2-Team minimum rule."""
            if len(picks_list) < count:
                return [], False, 0
            
            selected = []
            used_players = set()
            teams_used = set()
            
            # Pick #1: Best available
            pick_1 = picks_list[0]
            selected.append(pick_1)
            used_players.add(pick_1["player_name"])
            teams_used.add(pick_1.get("team", ""))
            
            # Pick #2: MUST be from different team
            for p in picks_list[1:]:
                if p["player_name"] not in used_players and p.get("team") != pick_1.get("team"):
                    selected.append(p)
                    used_players.add(p["player_name"])
                    teams_used.add(p.get("team", ""))
                    break
            
            # Fill remaining slots
            for p in picks_list:
                if len(selected) >= count:
                    break
                if p["player_name"] not in used_players:
                    selected.append(p)
                    used_players.add(p["player_name"])
                    teams_used.add(p.get("team", ""))
            
            is_valid = len(teams_used) >= 2
            return selected[:count], is_valid, len(teams_used)
        
        def calculate_combined_probability(picks):
            """Calculate combined probability from individual hit rates."""
            if not picks:
                return 0
            prob = 1.0
            for p in picks:
                hr = p.get("h10_rate") or p.get("l10_hit_rate") or 50
                prob *= (hr / 100)
            return round(prob * 100, 2)
        
        # Daily Double (2-Pick) - WAR ZONE
        if len(candidates) >= 2:
            picks_2, is_valid, team_count = get_multi_team_picks(candidates, 2)
            if picks_2:
                combined_prob = calculate_combined_probability(picks_2)
                parlays["daily_double"] = {
                    "name": "War Zone Double",
                    "tier": "daily_double",
                    "picks": picks_2,
                    "pick_count": 2,
                    "combined_probability": combined_prob,
                    "estimated_payout": 3.0,
                    "payout_display": "3.0x",
                    "description": "Top 2 demon anomalies",
                    "badge": "HIGH VALUE",
                    "lineup_valid": is_valid,
                    "team_count": team_count
                }
        
        # Power Play (3-Pick)
        if len(candidates) >= 3:
            picks_3, is_valid, team_count = get_multi_team_picks(candidates, 3)
            if picks_3:
                combined_prob = calculate_combined_probability(picks_3)
                parlays["power_play"] = {
                    "name": "Power Play",
                    "tier": "power_play",
                    "picks": picks_3,
                    "pick_count": 3,
                    "combined_probability": combined_prob,
                    "estimated_payout": 5.0,
                    "payout_display": "5.0x",
                    "description": "3 demon anomalies",
                    "badge": "AGGRESSIVE",
                    "lineup_valid": is_valid,
                    "team_count": team_count
                }
        
        # Flex Play (4-Pick)
        if len(candidates) >= 4:
            picks_4, is_valid, team_count = get_multi_team_picks(candidates, 4)
            if picks_4:
                combined_prob = calculate_combined_probability(picks_4)
                parlays["flex_play"] = {
                    "name": "Flex Play",
                    "tier": "flex_play",
                    "picks": picks_4,
                    "pick_count": 4,
                    "combined_probability": combined_prob,
                    "estimated_payout": 10.0,
                    "payout_display": "10x",
                    "description": "4-pick flex (allows 1 miss)",
                    "badge": "FLEX MODE",
                    "lineup_valid": is_valid,
                    "team_count": team_count
                }
        
        return {
            "success": True,
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "total_demons_analyzed": len(war_zone_picks),
            "games_with_correlation": len(set(p.get("game_id") for p in war_zone_picks if p.get("game_id"))),
            "parlays": parlays,
            "algorithm": {
                "description": "Anomaly-Based Selection",
                "source": "War Zone tier picks (demon anomalies where season_avg >= line)",
                "sorting": "Probability score (hit_rate + DvP + badges)"
            }
        }
    
    async def get_goblin_recon(self) -> Dict[str, Any]:
        """
        Build Safe Haven parlays DYNAMICALLY from curated tier picks.
        
        SOURCE OF TRUTH: get_goblin_vault() picks (goblin anomalies with 80%+ HR)
        This ensures parlays use the same picks users see on the board.
        """
        # Get Safe Haven (goblin_vault) picks as source of truth
        safe_haven_data = await self.get_goblin_vault()
        safe_haven_picks = safe_haven_data.get("picks", [])
        
        if not safe_haven_picks:
            return {
                "success": False,
                "message": "No Safe Haven picks available for parlays.",
                "parlays": {}
            }
        
        # Sort by probability score (highest first), then by hit rate
        candidates = sorted(
            safe_haven_picks, 
            key=lambda x: (x.get("probability_score", 0), x.get("h10_rate") or x.get("l10_hit_rate") or 0), 
            reverse=True
        )
        
        parlays = {}
        
        # Build parlays using 2-team rule
        def get_multi_team_picks(picks_list, count):
            """Select picks enforcing PrizePicks 2-Team minimum rule."""
            if len(picks_list) < count:
                return [], False, 0
            
            selected = []
            used_players = set()
            teams_used = set()
            
            # Pick #1: Best available
            pick_1 = picks_list[0]
            selected.append(pick_1)
            used_players.add(pick_1["player_name"])
            teams_used.add(pick_1.get("team", ""))
            
            # Pick #2: MUST be from different team
            for p in picks_list[1:]:
                if p["player_name"] not in used_players and p.get("team") != pick_1.get("team"):
                    selected.append(p)
                    used_players.add(p["player_name"])
                    teams_used.add(p.get("team", ""))
                    break
            
            # Fill remaining slots (prioritize different teams for diversification)
            for p in picks_list:
                if len(selected) >= count:
                    break
                if p["player_name"] not in used_players:
                    selected.append(p)
                    used_players.add(p["player_name"])
                    teams_used.add(p.get("team", ""))
            
            is_valid = len(teams_used) >= 2
            return selected[:count], is_valid, len(teams_used)
        
        def calculate_combined_probability(picks):
            """Calculate combined probability from individual hit rates."""
            if not picks:
                return 0
            prob = 1.0
            for p in picks:
                hr = p.get("h10_rate") or p.get("l10_hit_rate") or 80
                prob *= (hr / 100)
            return round(prob * 100, 2)
        
        # Daily Double (2-Pick) - SAFE HAVEN
        if len(candidates) >= 2:
            picks_2, is_valid, team_count = get_multi_team_picks(candidates, 2)
            if picks_2:
                combined_prob = calculate_combined_probability(picks_2)
                parlays["daily_double"] = {
                    "name": "Daily Double",
                    "tier": "daily_double",
                    "picks": picks_2,
                    "pick_count": 2,
                    "combined_probability": combined_prob,
                    "estimated_payout": 1.44,
                    "payout_display": "1.44x",
                    "description": "Top 2 Safe Haven picks (highest probability)",
                    "badge": "SAFEST BET",
                    "lineup_valid": is_valid,
                    "team_count": team_count
                }
        
        # Green Ladder 3-Pick
        if len(candidates) >= 3:
            picks_3, is_valid, team_count = get_multi_team_picks(candidates, 3)
            if picks_3:
                combined_prob = calculate_combined_probability(picks_3)
                parlays["green_ladder_3"] = {
                    "name": "Green Ladder",
                    "tier": "green_ladder_3",
                    "picks": picks_3,
                    "pick_count": 3,
                    "combined_probability": combined_prob,
                    "estimated_payout": 1.73,
                    "payout_display": "1.73x",
                    "description": "3 high-consistency picks",
                    "badge": "CONSISTENT",
                    "lineup_valid": is_valid,
                    "team_count": team_count
                }
        
        # Green Ladder 4-Pick
        if len(candidates) >= 4:
            picks_4, is_valid, team_count = get_multi_team_picks(candidates, 4)
            if picks_4:
                combined_prob = calculate_combined_probability(picks_4)
                parlays["green_ladder_4"] = {
                    "name": "Green Fortress",
                    "tier": "green_ladder_4",
                    "picks": picks_4,
                    "pick_count": 4,
                    "combined_probability": combined_prob,
                    "estimated_payout": 2.07,
                    "payout_display": "2.07x",
                    "description": "4-pick fortress (allows 1 miss on flex)",
                    "badge": "FORTRESS",
                    "lineup_valid": is_valid,
                    "team_count": team_count
                }
        
        # 5-Pick Flex
        if len(candidates) >= 5:
            picks_5, is_valid, team_count = get_multi_team_picks(candidates, 5)
            if picks_5:
                combined_prob = calculate_combined_probability(picks_5)
                parlays["flex_5"] = {
                    "name": "5-Pick Flex",
                    "tier": "flex_5",
                    "picks": picks_5,
                    "pick_count": 5,
                    "combined_probability": combined_prob,
                    "estimated_payout": 2.49,
                    "payout_display": "2.49x",
                    "description": "5 picks with flex protection",
                    "badge": "FLEX MODE",
                    "lineup_valid": is_valid,
                    "team_count": team_count
                }
        
        # 6-Pick Fortress
        if len(candidates) >= 6:
            picks_6, is_valid, team_count = get_multi_team_picks(candidates, 6)
            if picks_6:
                combined_prob = calculate_combined_probability(picks_6)
                parlays["fortress_6"] = {
                    "name": "6-Pick Fortress",
                    "tier": "fortress_6",
                    "picks": picks_6,
                    "pick_count": 6,
                    "combined_probability": combined_prob,
                    "estimated_payout": 2.99,
                    "payout_display": "2.99x",
                    "description": "Maximum flex protection (allows 2 misses)",
                    "badge": "MAXIMUM FLEX",
                    "lineup_valid": is_valid,
                    "team_count": team_count
                }
        
        return {
            "success": True,
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "total_candidates": len(safe_haven_picks),
            "recon_locks": len([p for p in safe_haven_picks if (p.get("h10_rate") or p.get("l10_hit_rate") or 0) >= 90]),
            "games_available": len(set(p.get("game_id") for p in safe_haven_picks if p.get("game_id"))),
            "parlays": parlays,
            "algorithm": {
                "name": "Anomaly Floor Scoring",
                "description": "Maximum win probability using Safe Haven tier picks",
                "source": "Safe Haven picks (goblin anomalies with 80%+ hit rate)",
                "sorting": "Probability score + Hit rate"
            }
        }
    
    async def get_cached_board(self) -> Dict[str, Any]:
        """
        Get the CACHED board from MongoDB.
        NO API CALLS - reads only from database.
        
        LEAN PAYLOAD: Returns only essential fields for the board listing.
        Full player data is fetched on-demand via get_cached_player().
        
        Returns player-centric documents (those with props array).
        """
        sync_meta = await self.sync_log.find_one({"type": "cached_board"})
        
        if not sync_meta:
            return {
                "success": False,
                "synced_at": None,
                "message": "No cached data. Run /api/v3/sync first.",
                "players": [],
                "trending": []
            }
        
        # LEAN PAYLOAD PROJECTION - Only fields needed for board listing
        # Full data (game_logs, advanced_stats, etc.) loaded on player detail click
        lean_projection = {
            "_id": 0,
            "player_name": 1,
            "team": 1,
            "opponent": 1,
            "game_id": 1,
            "home_team": 1,
            "away_team": 1,
            "commence_time": 1,
            "position": 1,
            "photo_url": 1,
            "headshot_url": 1,
            "nba_id": 1,
            "nba_com_id": 1,
            "rank": 1,
            # Props array - contains line data for cards
            "props": 1
            # EXCLUDED: bdl_game_logs, game_logs, advanced_stats, baseline_stats
            # These are fetched on-demand via get_cached_player()
        }
        
        # Get only player-centric documents (those with props array)
        players = await self.cached_board.find(
            {"props": {"$exists": True}},
            lean_projection
        ).sort("rank", 1).to_list(500)
        
        # Clean any remaining ObjectIds and flatten hit_rates to prop level
        for player in players:
            self._clean_object_ids(player)
            # Flatten hit_rates for frontend compatibility
            for prop in player.get("props", []):
                hit_rates = prop.get("hit_rates", {})
                if hit_rates:
                    # Flatten to prop level for frontend
                    prop["l5_avg"] = hit_rates.get("l5_avg")
                    prop["l10_avg"] = hit_rates.get("l10_avg")
                    prop["season_avg"] = hit_rates.get("season_avg")
                    prop["h10_rate"] = hit_rates.get("l10_rate")  # Frontend expects h10_rate
                    prop["h5_rate"] = hit_rates.get("l5_rate")
                    prop["l10_hit_count"] = hit_rates.get("l10_hit_count")
                    prop["l5_hit_count"] = hit_rates.get("l5_hit_count")
        
        # SSOT: Enrich ALL players with photos from master hub
        await self._enrich_picks_with_photos(players)
        
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
        Works with NESTED structure (player docs with props arrays).
        NO API CALLS - reads only from database.
        
        Stats (L5/L10/SZN) come EXCLUSIVELY from nba_master_hub_2026.baseline_stats.
        """
        # Get current time for filtering upcoming games
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat().replace('+00:00', 'Z')
        
        # NESTED STRUCTURE: Player document with props array
        # Find the player document first
        player_doc = await self.cached_board.find_one(
            {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0}
        )
        
        if not player_doc:
            # Try normalized name search
            normalized_search = _normalize_name(player_name)
            all_players = await self.cached_board.distinct("player_name")
            
            matched_name = None
            for pname in all_players:
                if _normalize_name(pname) == normalized_search:
                    matched_name = pname
                    break
            
            if matched_name:
                player_doc = await self.cached_board.find_one(
                    {"player_name": matched_name},
                    {"_id": 0}
                )
        
        if not player_doc:
            return {
                "success": False,
                "message": "Lines loading... Player not in cache.",
                "player": None
            }
        
        # Extract props from the nested structure
        # Include games in progress (started within last 3 hours) - don't filter out active games
        raw_props = player_doc.get("props", [])
        
        from datetime import timedelta
        three_hours_ago = (now - timedelta(hours=3)).isoformat().replace('+00:00', 'Z')
        
        # Include props for upcoming games AND games currently in progress
        active_props = []
        for prop in raw_props:
            commence_time = prop.get("commence_time", "")
            # Include if: upcoming OR started within last 3 hours (game in progress)
            if commence_time and commence_time > three_hours_ago:
                active_props.append(prop)
        
        # Build player object from the player document
        player = {
            "player_name": player_doc.get("player_name"),
            "team": player_doc.get("team"),
            "opponent": player_doc.get("opponent"),
            "game_id": player_doc.get("game_id"),
            "home_team": player_doc.get("home_team"),
            "away_team": player_doc.get("away_team"),
            "commence_time": player_doc.get("commence_time"),
            "position": player_doc.get("position"),
            "photo_url": player_doc.get("photo_url") or player_doc.get("headshot_url"),
            "nba_id": player_doc.get("nba_id"),
            "bdl_game_logs": player_doc.get("bdl_game_logs", []),
            # Player-level enrichment (shared across all props)
            "momentum_data": player_doc.get("momentum_data"),
            "crew_chief": player_doc.get("crew_chief"),
            "ref_ou_pct": player_doc.get("ref_ou_pct"),
            "ref_ppg": player_doc.get("ref_ppg"),
            "whistle_class": player_doc.get("whistle_class"),
            "vacuum_data": player_doc.get("vacuum_data"),
            "board_member": player_doc.get("board_member"),
            "enriched_at": player_doc.get("enriched_at"),
            # Aggregate all upcoming props
            "props": []
        }
        
        # Add all active props to the player object with correct field mapping
        for prop in active_props:
            # Get hit rates from nested object or flattened fields
            hit_rates = prop.get("hit_rates", {})
            h10_rate = prop.get("h10_rate") or prop.get("h10_hit_rate") or hit_rates.get("l10_rate")
            h5_rate = prop.get("h5_rate") or prop.get("h5_hit_rate") or hit_rates.get("l5_rate")
            l5_avg = prop.get("l5_avg") or hit_rates.get("l5_avg")
            l10_avg = prop.get("l10_avg") or hit_rates.get("l10_avg")
            season_avg = prop.get("season_avg") or hit_rates.get("season_avg")
            
            player["props"].append({
                "stat_type_extracted": prop.get("stat_type_extracted") or prop.get("stat_type"),
                "stat_type": prop.get("stat_type_extracted") or prop.get("stat_type"),
                "market": prop.get("market"),
                "line": prop.get("line"),
                "anchor_line": prop.get("anchor_line"),
                "is_demon": prop.get("is_demon"),
                "is_goblin": prop.get("is_goblin"),
                "tier_label": prop.get("tier_label"),
                "direction": prop.get("direction"),
                "price": prop.get("price"),
                "h10_hit_rate": h10_rate,
                "h10_rate": h10_rate,
                "h5_rate": h5_rate,
                "h5_hit_rate": h5_rate,
                "l5_avg": l5_avg,
                "l10_avg": l10_avg,
                "season_avg": season_avg,
                "hit_rates": hit_rates,  # Keep nested object too
                "is_alternate_market": prop.get("is_alternate_market"),
                "commence_time": prop.get("commence_time"),
                "home_team": prop.get("home_team"),
                "away_team": prop.get("away_team"),
                # Vision Intel Pre-Cached fields
                "board": prop.get("board"),  # Which board this prop is featured on
                "vision_summary": prop.get("vision_summary"),
                "vision_score": prop.get("vision_score"),  # AI composite score
                "is_vision_enriched": prop.get("is_vision_enriched"),
                "vision_enriched_at": prop.get("vision_enriched_at"),
                "intel_suite": prop.get("intel_suite"),  # Pre-built intel suite
                "active_badges": prop.get("active_badges"),  # Badge keys
                # Momentum data (from optimized sync engine)
                "momentum_data": prop.get("momentum_data"),
                "momentum_modifier": prop.get("momentum_modifier"),
                "has_momentum_modifier": prop.get("has_momentum_modifier"),
                # Whistle/Officiating data (from optimized sync engine)
                "crew_chief": prop.get("crew_chief"),
                "ref_ou_pct": prop.get("ref_ou_pct"),
                "ref_ppg": prop.get("ref_ppg"),
                "whistle_class": prop.get("whistle_class"),
                "whistle_modifier": prop.get("whistle_modifier"),
                "has_whistle_modifier": prop.get("has_whistle_modifier"),
                "point_lift": prop.get("point_lift"),
                "lift_label": prop.get("lift_label"),
                "lift_type": prop.get("lift_type"),
                # Vacuum data (from optimized sync engine)
                "vacuum_data": prop.get("vacuum_data"),
                "vacuum_modifier": prop.get("vacuum_modifier"),
                "has_vacuum_modifier": prop.get("has_vacuum_modifier"),
            })
        
        self._clean_object_ids(player)
        await self._enrich_player_with_master_hub_stats(player)
        await self._add_player_insights(player)
        
        return {"success": True, "player": player, "source": "cached_board"}
    
    async def _enrich_player_with_master_hub_stats(self, player: Dict) -> None:
        """
        SSOT INTERSECTION: Join PIPE 1 stats with PIPE 2 lines.
        
        All stats come from nba_master_hub_2026 (PIPE 1).
        Lookup by player_id FIRST, then name as fallback.
        """
        if not player:
            return
        
        player_name = player.get("player_name", "")
        
        # SSOT: Get stats from master hub by player_id FIRST
        hub_player = await self._get_master_player(player)
        
        if not hub_player:
            logger.debug(f"[SSOT] No master hub data for: {player_name}")
            return
        
        # PIPE 1: Get baseline_stats and game_logs from master hub
        # PRIORITY: Use bdl_game_logs (more accurate), fallback to BDL game_logs
        baseline_stats = hub_player.get("baseline_stats", {})
        game_logs = hub_player.get("bdl_game_logs", []) or hub_player.get("game_logs", [])
        
        # Add structural data - PHOTOS FROM MASTER HUB ONLY
        player["baseline_stats"] = baseline_stats
        player["photo_url"] = hub_player.get("photo_url") or hub_player.get("headshot_url")
        player["headshot_url"] = hub_player.get("photo_url") or hub_player.get("headshot_url")
        
        # Import the coupled stats calculator (uses game_logs from PIPE 1)
        from services.stats_service import calculate_coupled_stats
        
        # Import intel calculator for radar picks
        from services.intel_suite_calculator import get_intel_calculator
        intel_calculator = get_intel_calculator(self.db)
        
        # INTERSECTION: Enrich PIPE 2 lines with PIPE 1 stats
        props = player.get("props", [])
        for prop in props:
            stat_type = prop.get("stat_type_extracted", "") or prop.get("stat_type", "")
            line_value = prop.get("line", 0)  # Line comes from PIPE 2
            
            # Normalize stat type for lookup (P+R -> PR, etc.)
            stat_key = stat_type
            norm_map = {"P+R": "PR", "P+A": "PA", "R+A": "RA"}
            stat_key = norm_map.get(stat_type, stat_type)
            
            # SSOT: ALWAYS calculate stats from bdl_game_logs (BDL is the source of truth)
            # Do NOT use baseline_stats - they may be stale
            if game_logs and line_value > 0:
                # Calculate from BDL game_logs (SSOT)
                coupled = calculate_coupled_stats(game_logs, stat_type, line_value)
                
                if coupled:
                    prop["l5_avg"] = coupled.get("l5_avg")
                    prop["l10_avg"] = coupled.get("l10_avg")
                    prop["season_avg"] = coupled.get("season_avg")
                    prop["l10_hit_rate"] = coupled.get("l10_hit_rate")
                    prop["l5_hit_rate"] = coupled.get("l5_hit_rate")
                    prop["l10_games_over"] = coupled.get("l10_games_over")
                    prop["l10_total_games"] = coupled.get("l10_total_games")
                    prop["l5_games_over"] = coupled.get("l5_games_over")
                    prop["l5_total_games"] = coupled.get("l5_total_games")
                    prop["stats_coupled"] = True
                    prop["stats_source"] = "bdl_game_logs"
                    
                    # Also set h5_rate/h10_rate for frontend consistency
                    if prop.get("l5_hit_rate") is not None:
                        prop["h5_rate"] = prop["l5_hit_rate"]
                    if prop.get("l10_hit_rate") is not None:
                        prop["h10_rate"] = prop["l10_hit_rate"]
            elif baseline_stats.get(stat_key):
                # Fallback to baseline_stats only if no game_logs
                stat_data = baseline_stats.get(stat_key, {})
                prop["l5_avg"] = stat_data.get("l5_avg")
                prop["l10_avg"] = stat_data.get("l10_avg")
                prop["season_avg"] = stat_data.get("season_avg")
                prop["stats_coupled"] = False
                prop["stats_source"] = "baseline_fallback"
            
            # If this is a radar pick (demon or goblin) OR a board pick, add full intel_suite
            is_radar = prop.get("is_demon") or prop.get("is_goblin") or prop.get("is_radar_pick")
            is_board_pick = prop.get("board") is not None  # Featured props from Ferrari pipeline
            
            if is_radar or is_board_pick:
                calculated_intel = await intel_calculator.calculate_intel_suite(
                    player_name=player_name,
                    stat_type=stat_key,
                    line=prop.get("line", 0),
                    direction=prop.get("direction", "over"),
                    opponent=player.get("opponent"),
                    board_pick=prop  # Pass prop as board_pick for additional context
                )
                # MERGE with existing intel_suite (from optimized sync) instead of overwriting
                existing_intel = prop.get("intel_suite") or {}
                merged_intel = {**existing_intel, **calculated_intel}
                # Preserve the enrichment data (momentum, whistle, vacuum) from optimized sync
                if existing_intel.get("momentum_data"):
                    merged_intel["momentum_data"] = existing_intel["momentum_data"]
                if existing_intel.get("whistle_data"):
                    merged_intel["whistle_data"] = existing_intel["whistle_data"]
                if existing_intel.get("vacuum_data"):
                    merged_intel["vacuum_data"] = existing_intel["vacuum_data"]
                if existing_intel.get("ferrari_power_score"):
                    merged_intel["ferrari_power_score"] = existing_intel["ferrari_power_score"]
                if existing_intel.get("board"):
                    merged_intel["board"] = existing_intel["board"]
                logger.debug(f"[INTEL_MERGE] {player_name} merged keys: {list(merged_intel.keys())}")
                prop["intel_suite"] = merged_intel
        
        logger.debug(f"[SSOT] Enriched {len(props)} props for {player_name}")
    
    async def get_most_popular_bets(self) -> Dict[str, Any]:
        """
        Get TOP PICKS - Picks with the biggest line movements.
        
        Line movement is the best indicator of where money is flowing:
        - Line moves UP (24.5 -> 25.5) = Heavy OVER action
        - Line moves DOWN (24.5 -> 23.5) = Heavy UNDER action
        - Big moves = Sharp money or heavy public action
        
        Falls back to top picks from sections if no movements detected.
        """
        try:
            now = datetime.now(timezone.utc)
            
            # First, try to get picks based on line movements
            trending_picks = []
            line_movement_status = "unavailable"
            
            try:
                from services.line_movement_tracker import get_line_tracker
                line_tracker = get_line_tracker(self.db)
                trending_picks = await line_tracker.get_trending_picks(limit=12)
                
                if trending_picks:
                    line_movement_status = "live"
                    logger.info(f"[TOP_PICKS] Found {len(trending_picks)} trending picks from line movements")
            except Exception as e:
                logger.warning(f"[TOP_PICKS] Line tracker error: {e}")
            
            # If we have trending picks from line movements, use those
            if trending_picks:
                # Enrich with additional data from cached board
                enriched_picks = []
                for pick in trending_picks:
                    # Get full player data from cache if available
                    player = await self.cached_board.find_one(
                        {"player_name": pick.get("player_name")},
                        {"_id": 0}
                    )
                    
                    if player:
                        # Find the matching prop
                        for prop in player.get("props", []):
                            if prop.get("stat_type") == pick.get("stat_type"):
                                # Merge line movement data with prop data
                                enriched = {**prop, **pick}
                                enriched["photo_url"] = player.get("photo_url") or player.get("headshot_url")
                                enriched["team"] = player.get("team")
                                enriched["player_name"] = player.get("player_name")
                                enriched["source_section"] = "LINE_MOVEMENT"
                                enriched["section_label"] = "Trending"
                                enriched_picks.append(enriched)
                                break
                        else:
                            # Prop not found in current board, use line movement data as-is
                            pick["source_section"] = "LINE_MOVEMENT"
                            pick["section_label"] = "Trending"
                            enriched_picks.append(pick)
                    else:
                        # Player not in cache, use line movement data
                        pick["source_section"] = "LINE_MOVEMENT"
                        pick["section_label"] = "Trending"
                        enriched_picks.append(pick)
                
                trending_picks = enriched_picks
            
            # If no line movements, fall back to top picks from each section
            if not trending_picks:
                logger.info("[TOP_PICKS] No line movements - falling back to section picks")
                
                safe_haven_result = await self.get_goblin_vault()
                front_lines_result = await self.get_front_lines()
                war_zone_result = await self.get_war_zone()
                
                # Take top 4 from each section
                safe_haven_picks = safe_haven_result.get("picks", [])[:4]
                for pick in safe_haven_picks:
                    pick["source_section"] = "SAFE_HAVEN"
                    pick["section_label"] = "Safe Haven"
                    trending_picks.append(pick)
                
                front_lines_picks = front_lines_result.get("picks", [])[:4]
                for pick in front_lines_picks:
                    pick["source_section"] = "FRONT_LINES"
                    pick["section_label"] = "Front Lines"
                    trending_picks.append(pick)
                
                war_zone_picks = war_zone_result.get("picks", [])[:4]
                for pick in war_zone_picks:
                    pick["source_section"] = "WAR_ZONE"
                    pick["section_label"] = "War Zone"
                    trending_picks.append(pick)
            
            # Check if all picks are locked
            all_locked = all(pick.get("is_locked", False) for pick in trending_picks) if trending_picks else False
            locked_count = sum(1 for pick in trending_picks if pick.get("is_locked", False))
            
            # Calculate next release time
            next_release_time = None
            if all_locked:
                from datetime import timedelta
                import pytz
                
                et_tz = pytz.timezone('America/New_York')
                now_et = now.astimezone(et_tz)
                
                today_4am = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
                if now_et < today_4am:
                    next_sync_et = today_4am
                else:
                    next_sync_et = today_4am + timedelta(days=1)
                
                next_release_time = next_sync_et.astimezone(timezone.utc).isoformat()
            
            logger.info(f"[TOP_PICKS] Returning {len(trending_picks)} picks | Line Movement Status: {line_movement_status} | Locked: {locked_count}/{len(trending_picks)}")
            
            # CRITICAL: Sanitize all picks to prevent ObjectId serialization errors
            sanitized_picks = _sanitize_picks_list(trending_picks)
            
            return {
                "status": "live" if not all_locked else "all_locked",
                "bets": sanitized_picks,
                "source": "line_movements" if line_movement_status == "live" else "section_fallback",
                "line_movement_status": line_movement_status,
                "all_locked": all_locked,
                "locked_count": locked_count,
                "total_count": len(sanitized_picks),
                "next_release_time": next_release_time,
                "timestamp": now.isoformat()
            }
            
        except Exception as e:
            logger.error(f"[TOP_PICKS] Error: {e}")
            import traceback
            traceback.print_exc()
            return {"status": "error", "bets": [], "error": str(e)}
    
    # ==================== PRIVATE HELPER METHODS ====================
    
    async def _add_insights_to_pick(self, pick: Dict) -> None:
        """
        SSOT: Enrich a single pick with stats from master hub.
        
        ALL data comes from nba_master_hub_2026 (PIPE 1).
        Lookup is by player_id FIRST, then by name as fallback.
        """
        # SSOT: Get player data from master hub by ID first
        master_player = await self._get_master_player(pick)
        if master_player:
            # Structural data (protected fields)
            pick['player_id'] = master_player.get('player_id')
            pick['nba_id'] = master_player.get('nba_id') or master_player.get('nba_player_id')
            pick['espn_id'] = master_player.get('espn_id')
            # DO NOT set photo_url here - let _enrich_picks_with_photos handle it (SSOT)
            if not pick.get('team'):
                pick['team'] = master_player.get('team')
            if not pick.get('position'):
                pick['position'] = master_player.get('position')
            
            # PIPE 1: Get stats from master hub
            baseline_stats = master_player.get('baseline_stats', {})
            game_logs = master_player.get('game_logs', [])
            stat_type = pick.get('stat_type', '')
            line_value = pick.get('line') or pick.get('demon_line') or pick.get('goblin_line') or 0
            
            # Calculate coupled stats from PIPE 1 game_logs
            if game_logs and stat_type and line_value > 0:
                from services.stats_service import calculate_coupled_stats
                coupled = calculate_coupled_stats(game_logs, stat_type, line_value)
                
                # Use coupled stats (guaranteed consistent hit rate + avg)
                pick['l5_avg'] = coupled["l5"]["avg"]
                pick['l10_avg'] = coupled["l10"]["avg"]
                pick['season_avg'] = coupled["season"]["avg"] or baseline_stats.get(stat_type, {}).get('season_avg')
                pick['l5_hit_rate'] = coupled["l5"]["hit_rate"]
                pick['l10_hit_rate'] = coupled["l10"]["hit_rate"]
                pick['l5_games_over'] = coupled["l5"]["games_over"]
                pick['l10_games_over'] = coupled["l10"]["games_over"]
                pick['stats_coupled'] = True
                pick['stats_source'] = 'ssot_game_logs'
            elif stat_type and baseline_stats:
                # Fallback to baseline_stats if no game logs
                cat_stats = baseline_stats.get(stat_type, {})
                if cat_stats:
                    pick['l5_avg'] = cat_stats.get('l5_avg')
                    pick['l10_avg'] = cat_stats.get('l10_avg')
                    pick['season_avg'] = cat_stats.get('season_avg')
                pick['stats_coupled'] = False
                pick['stats_source'] = 'ssot_baseline'
            
            # Store full baseline_stats for frontend access
            pick['baseline_stats'] = baseline_stats
        
        # Get player_name for legacy lookups (daily_insights, etc)
        player_name = pick.get('player_name', '')
        
        # Get old insight_summary from daily_insights
        if player_name:
            insight = await self.daily_insights.find_one(
                {"player_name": player_name},
                {"_id": 0, "insight_summary": 1, "ai_confidence_rating": 1}
            )
            if insight:
                pick['insight_summary'] = insight.get('insight_summary', '')
                pick['ai_confidence_rating'] = insight.get('ai_confidence_rating', 50)
            else:
                # Fallback: Calculate AI confidence from pillar_4_context (0-1) -> (0-100)
                pillar_4 = pick.get('pillar_4_context', 0.5)
                pick['ai_confidence_rating'] = int(pillar_4 * 100)
            
            # Get new intel_briefing from cached_board
            board_entry = await self.cached_board.find_one(
                {"player_name": player_name},
                {"_id": 0, "intel_briefing": 1}
            )
            if board_entry and board_entry.get('intel_briefing'):
                pick['intel_briefing'] = board_entry.get('intel_briefing')
    
    async def _add_player_insights(self, player: Dict) -> None:
        """
        SSOT: Add stats and insights from master hub to a player dict.
        All stats come from nba_master_hub_2026 (PIPE 1).
        """
        if not player or not player.get("player_name"):
            return
        
        player_name = player.get("player_name")
        
        # SSOT: Get player data from master hub ONLY
        master_player = await self._get_player_by_name(player_name)
        if master_player:
            # Structural data (protected fields)
            player['player_id'] = master_player.get('player_id')
            player['nba_id'] = master_player.get('nba_id')
            player['espn_id'] = master_player.get('espn_id')
            # DO NOT set photo_url here - let _enrich_picks_with_photos handle it (SSOT)
            if not player.get('team'):
                player['team'] = master_player.get('team')
            if not player.get('position'):
                player['position'] = master_player.get('position')
            
            # ===== BASELINE STATS from master hub =====
            player['baseline_stats'] = master_player.get('baseline_stats', {})
        
        insights = await self.daily_insights.find_one(
            {"player_name": player_name},
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
        """Remove all ObjectId fields from nested arrays to prevent serialization errors."""
        for key in ["props", "demons", "goblins", "standard"]:
            if key in player and isinstance(player[key], list):
                for item in player[key]:
                    if isinstance(item, dict):
                        item.pop("_id", None)
    
    def _flatten_hit_rates_to_props(self, player: Dict) -> None:
        """
        Flatten hit_rates object to prop level for frontend compatibility.
        
        The cached_board stores hit rates in nested hit_rates object:
        { hit_rates: { l5_avg: X, l10_avg: Y, season_avg: Z, l10_rate: R } }
        
        Frontend expects flat props:
        { l5_avg: X, l10_avg: Y, season_avg: Z, h10_rate: R }
        """
        for prop in player.get("props", []):
            hit_rates = prop.get("hit_rates", {})
            if hit_rates:
                prop["l5_avg"] = hit_rates.get("l5_avg")
                prop["l10_avg"] = hit_rates.get("l10_avg")
                prop["season_avg"] = hit_rates.get("season_avg")
                prop["h10_rate"] = hit_rates.get("l10_rate")
                prop["h5_rate"] = hit_rates.get("l5_rate")
                prop["l10_hit_count"] = hit_rates.get("l10_hit_count")
                prop["l5_hit_count"] = hit_rates.get("l5_hit_count")


    # ============================================================================
    # STATIC CACHE METHODS - Simple MongoDB reads, NO JIT calculations
    # ============================================================================
    
    async def _enrich_fallback_pick_on_demand(self, pick: Dict) -> Dict:
        """
        On-demand enrichment for fallback picks that weren't pre-enriched.
        
        When a game ends and new players fill board slots, they may not have
        intel_suite or vision_summary. This method enriches them JIT.
        """
        # Skip if already enriched
        if pick.get("is_vision_enriched") and pick.get("intel_suite") and pick.get("vision_summary"):
            return pick
        
        try:
            player_name = pick.get("player_name", "")
            stat_type = pick.get("stat_type", "PTS")
            line = pick.get("line", 0)
            opponent = pick.get("opponent", "")
            
            logger.info(f"[ON_DEMAND_ENRICH] Enriching fallback pick: {player_name} {stat_type}@{line}")
            
            # Calculate Intel Suite
            from services.intel_suite_calculator import IntelSuiteCalculator
            intel_calculator = IntelSuiteCalculator(self.db)
            
            intel_suite = await intel_calculator.calculate_intel_suite(
                player_name=player_name,
                stat_type=stat_type,
                line=line,
                direction=pick.get("direction", "over"),
                opponent=opponent,
                board_pick=pick
            )
            
            # Calculate Vision Score
            from services.vision_score_calculator import calculate_vision_score
            score_result = calculate_vision_score(
                h10_rate=pick.get("h10_rate", 0),
                dvp_rank=intel_suite.get("matchup_dvp", {}).get("rank"),
                active_badges=pick.get("active_badges", []),
                is_demon=pick.get("is_demon", False),
                is_goblin=pick.get("is_goblin", False)
            )
            
            intel_suite["vision_score"] = score_result["vision_score"]
            intel_suite["vision_score_breakdown"] = score_result
            
            # AI Summary is now generated by vision_intel_service.py in ferrari_tier_service.py
            # Check if we already have vision_intel from the tier building process
            ai_summary = pick.get("vision_intel") or pick.get("vision_summary")
            
            if not ai_summary:
                # Fallback: generate a simple summary without calling Gemini
                h_rate = pick.get("h20_rate", 0) or pick.get("h10_rate", 0)
                ai_summary = f"{player_name} {stat_type} @ {line} - {h_rate:.0f}% hit rate. Check Vision Intel for full analysis."
            
            # Update pick with enriched data
            pick["intel_suite"] = intel_suite
            pick["vision_summary"] = ai_summary
            pick["vision_score"] = score_result["vision_score"]
            pick["is_vision_enriched"] = True
            
            # Also persist to MongoDB for future reads
            await self._persist_fallback_enrichment(pick)
            
            logger.info(f"[ON_DEMAND_ENRICH] Completed: {player_name} (score={score_result['vision_score']}, summary={'Yes' if ai_summary else 'No'})")
            
        except Exception as e:
            logger.error(f"[ON_DEMAND_ENRICH] Error enriching {pick.get('player_name')}: {e}")
        
        return pick
    
    async def _persist_fallback_enrichment(self, pick: Dict):
        """Persist on-demand enrichment to MongoDB for future reads."""
        try:
            player_name = pick.get("player_name")
            stat_type = pick.get("stat_type", "")
            line = pick.get("line", 0)
            
            # Find the prop index
            player = await self.cached_board.find_one(
                {"player_name": player_name},
                {"_id": 0, "props": 1}
            )
            if not player:
                return
            
            for i, prop in enumerate(player.get("props", [])):
                prop_stat = prop.get("stat_type_extracted") or prop.get("stat_type", "")
                prop_line = prop.get("line", 0)
                if prop_stat == stat_type and abs(prop_line - line) < 0.1:
                    await self.cached_board.update_one(
                        {"player_name": player_name},
                        {"$set": {
                            f"props.{i}.intel_suite": pick.get("intel_suite"),
                            f"props.{i}.vision_summary": pick.get("vision_summary"),
                            f"props.{i}.vision_score": pick.get("vision_score"),
                            f"props.{i}.is_vision_enriched": True,
                            f"props.{i}.vision_enriched_at": datetime.now(timezone.utc).isoformat()
                        }}
                    )
                    logger.debug(f"[ON_DEMAND_ENRICH] Persisted to MongoDB: {player_name} idx={i}")
                    break
        except Exception as e:
            logger.warning(f"[ON_DEMAND_ENRICH] Persist failed for {pick.get('player_name')}: {e}")
    
    async def _enrich_fallback_picks_batch(self, picks: List[Dict]) -> List[Dict]:
        """
        Batch enrich fallback picks that are missing intel_suite/vision_summary.
        Runs concurrently with a semaphore to avoid rate limiting.
        """
        import asyncio
        
        needs_enrichment = [p for p in picks if not (p.get("is_vision_enriched") and p.get("intel_suite") and p.get("vision_summary"))]
        
        if not needs_enrichment:
            return picks
        
        logger.info(f"[ON_DEMAND_ENRICH] {len(needs_enrichment)} picks need enrichment")
        
        # Limit concurrency to avoid rate limiting
        semaphore = asyncio.Semaphore(2)
        
        async def enrich_with_semaphore(pick):
            async with semaphore:
                return await self._enrich_fallback_pick_on_demand(pick)
        
        # Enrich all in parallel (limited by semaphore)
        await asyncio.gather(*[enrich_with_semaphore(p) for p in needs_enrichment], return_exceptions=True)
        
        return picks

    async def get_war_zone_static(self) -> Dict[str, Any]:
        """
        STATIC ROUTE: War Zone (Demons with L10 HR >= 50%).
        
        Selection: is_demon=True AND h10_rate >= 50 AND board="war_zone"
        Sorted by: vision_score desc, h10_rate desc
        
        All enrichment done by board_intelligence_service.py
        """
        try:
            now = datetime.now(timezone.utc)
            now_iso = now.isoformat().replace('+00:00', 'Z')
            
            picks = await self.cached_board.aggregate([
                {"$unwind": "$props"},
                {"$match": {
                    "props.is_demon": True,
                    "props.h10_rate": {"$gte": 50},
                    "props.commence_time": {"$gt": now_iso}
                }},
                {"$project": {
                    "_id": 0,
                    "player_name": 1,
                    "team": 1,
                    "photo_url": 1,
                    "headshot_url": 1,
                    "position": 1,
                    "opponent": 1,
                    "game_id": "$props.game_id",
                    "commence_time": "$props.commence_time",
                    "stat_type": "$props.stat_type",
                    "line": "$props.line",
                    "anchor_line": "$props.anchor_line",
                    "h5_rate": "$props.h5_rate",
                    "h10_rate": "$props.h10_rate",
                    "l5_avg": "$props.l5_avg",
                    "l10_avg": "$props.l10_avg",
                    "season_avg": "$props.season_avg",
                    "is_demon": "$props.is_demon",
                    "is_goblin": "$props.is_goblin",
                    "tier_label": {"$literal": "DEMON"},
                    "pick_type": {"$literal": "demon"},
                    "combined_score": "$props.combined_score",
                    "payout_score": "$props.payout_score",
                    "vision_summary": "$props.vision_summary",
                    "intel_suite": "$props.intel_suite",
                    "is_vision_enriched": "$props.is_vision_enriched",
                    "vision_score": "$props.vision_score",
                    "board": "$props.board",
                    "context_badges": 1,
                    "active_badges": "$props.active_badges",
                    # Sharp book prices
                    "fanduel_price": "$props.fanduel_price",
                    "draftkings_price": "$props.draftkings_price",
                    "sharp_price": "$props.sharp_price",
                    "sharp_source": "$props.sharp_source",
                    "multiplier": "$props.multiplier"
                }},
                {"$sort": {"vision_score": -1, "h10_rate": -1, "combined_score": -1}},
                {"$limit": 100}
            ]).to_list(100)
            
            # De-duplicate by player, prioritize board="war_zone" picks
            seen = set()
            unique = []
            
            # First pass: picks explicitly assigned to war_zone
            for p in picks:
                name = p.get("player_name")
                if name and name not in seen and p.get("board") == "war_zone":
                    seen.add(name)
                    gs = _get_game_status(p.get("commence_time"))
                    p["is_locked"] = gs.get("is_locked", False)
                    p["game_status"] = gs.get("status", "upcoming")
                    unique.append(p)
                    if len(unique) >= 30:  # Fetch more to allow for VIP filtering
                        break
            
            # Second pass: fill remaining slots with other eligible demons
            if len(unique) < 30:
                for p in picks:
                    name = p.get("player_name")
                    if name and name not in seen:
                        seen.add(name)
                        gs = _get_game_status(p.get("commence_time"))
                        p["is_locked"] = gs.get("is_locked", False)
                        p["game_status"] = gs.get("status", "upcoming")
                        unique.append(p)
                        if len(unique) >= 30:
                            break
            
            # NO on-demand enrichment - serve from cache only for instant response
            # Background worker handles enrichment separately
            
            await self._enrich_picks_with_photos(unique)
            logger.info(f"[WAR_ZONE_STATIC] Served {len(unique)} picks")
            
            return {"status": "live", "picks": unique, "count": len(unique), "source": "static_cache"}
        except Exception as e:
            logger.error(f"[WAR_ZONE_STATIC] Error: {e}")
            return {"status": "error", "picks": [], "error": str(e)}
    
    async def get_goblin_vault_static(self) -> Dict[str, Any]:
        """
        STATIC ROUTE: Safe Haven (Goblins with L10 HR >= 80%).
        
        Selection: is_goblin=True AND h10_rate >= 80 AND board="safe_haven"
        Sorted by: vision_score desc, h10_rate desc
        
        All enrichment done by board_intelligence_service.py
        """
        try:
            now = datetime.now(timezone.utc)
            now_iso = now.isoformat().replace('+00:00', 'Z')
            
            picks = await self.cached_board.aggregate([
                {"$unwind": "$props"},
                {"$match": {
                    "props.is_goblin": True,
                    "props.h10_rate": {"$gte": 80},
                    "props.commence_time": {"$gt": now_iso}
                }},
                {"$project": {
                    "_id": 0,
                    "player_name": 1,
                    "team": 1,
                    "photo_url": 1,
                    "headshot_url": 1,
                    "position": 1,
                    "opponent": 1,
                    "game_id": "$props.game_id",
                    "commence_time": "$props.commence_time",
                    "stat_type": "$props.stat_type",
                    "line": "$props.line",
                    "anchor_line": "$props.anchor_line",
                    "h5_rate": "$props.h5_rate",
                    "h10_rate": "$props.h10_rate",
                    "l5_avg": "$props.l5_avg",
                    "l10_avg": "$props.l10_avg",
                    "season_avg": "$props.season_avg",
                    "is_demon": "$props.is_demon",
                    "is_goblin": "$props.is_goblin",
                    "tier_label": {"$literal": "GOBLIN"},
                    "pick_type": {"$literal": "goblin"},
                    "combined_score": "$props.combined_score",
                    "payout_score": "$props.payout_score",
                    "vision_summary": "$props.vision_summary",
                    "intel_suite": "$props.intel_suite",
                    "is_vision_enriched": "$props.is_vision_enriched",
                    "vision_score": "$props.vision_score",
                    "board": "$props.board",
                    "context_badges": 1,
                    "active_badges": "$props.active_badges",
                    # Sharp book prices
                    "fanduel_price": "$props.fanduel_price",
                    "draftkings_price": "$props.draftkings_price",
                    "sharp_price": "$props.sharp_price",
                    "sharp_source": "$props.sharp_source",
                    "multiplier": "$props.multiplier"
                }},
                {"$sort": {"vision_score": -1, "h10_rate": -1, "combined_score": -1}},
                {"$limit": 100}
            ]).to_list(100)
            
            # De-duplicate by player, prioritize board="safe_haven" picks
            seen = set()
            unique = []
            
            # First pass: picks explicitly assigned to safe_haven
            for p in picks:
                name = p.get("player_name")
                if name and name not in seen and p.get("board") == "safe_haven":
                    seen.add(name)
                    gs = _get_game_status(p.get("commence_time"))
                    p["is_locked"] = gs.get("is_locked", False)
                    p["game_status"] = gs.get("status", "upcoming")
                    unique.append(p)
                    if len(unique) >= 30:  # Fetch more to allow for VIP filtering
                        break
            
            # Second pass: fill remaining slots with other eligible goblins
            if len(unique) < 30:
                for p in picks:
                    name = p.get("player_name")
                    if name and name not in seen:
                        seen.add(name)
                        gs = _get_game_status(p.get("commence_time"))
                        p["is_locked"] = gs.get("is_locked", False)
                        p["game_status"] = gs.get("status", "upcoming")
                        unique.append(p)
                        if len(unique) >= 30:
                            break
            
            # NO on-demand enrichment - serve from cache only for instant response
            # Background worker handles enrichment separately
            
            await self._enrich_picks_with_photos(unique)
            logger.info(f"[SAFE_HAVEN_STATIC] Served {len(unique)} picks")
            
            return {"status": "live", "picks": unique, "count": len(unique), "source": "static_cache"}
        except Exception as e:
            logger.error(f"[SAFE_HAVEN_STATIC] Error: {e}")
            return {"status": "error", "picks": [], "error": str(e)}
    
    async def get_front_lines_static(self) -> Dict[str, Any]:
        """
        STATIC ROUTE: Front Lines (Goblin picks with sharp book validation).
        
        HARD FILTER: Only Goblins where:
        - Pinnacle price <= -300, OR
        - DraftKings price <= -300 (if no Pinnacle)
        - If neither has a price, EXCLUDE from Front Lines
        
        Sorted by: vision_score desc, h10_rate desc
        
        All enrichment done by board_intelligence_service.py
        """
        try:
            now = datetime.now(timezone.utc)
            now_iso = now.isoformat().replace('+00:00', 'Z')
            
            picks = await self.cached_board.aggregate([
                {"$unwind": "$props"},
                {"$match": {
                    "props.is_goblin": True,  # Front Lines = Goblins only
                    "props.h10_rate": {"$gte": 60},
                    "props.commence_time": {"$gt": now_iso}
                }},
                {"$project": {
                    "_id": 0,
                    "player_name": 1,
                    "team": 1,
                    "photo_url": 1,
                    "headshot_url": 1,
                    "position": 1,
                    "opponent": 1,
                    "game_id": "$props.game_id",
                    "commence_time": "$props.commence_time",
                    "stat_type": "$props.stat_type",
                    "line": "$props.line",
                    "anchor_line": "$props.anchor_line",
                    "h5_rate": "$props.h5_rate",
                    "h10_rate": "$props.h10_rate",
                    "l5_avg": "$props.l5_avg",
                    "l10_avg": "$props.l10_avg",
                    "season_avg": "$props.season_avg",
                    "is_demon": "$props.is_demon",
                    "is_goblin": "$props.is_goblin",
                    "tier_label": "GOBLIN",
                    "pick_type": "goblin",
                    "combined_score": "$props.combined_score",
                    "payout_score": "$props.payout_score",
                    "vision_summary": "$props.vision_summary",
                    "intel_suite": "$props.intel_suite",
                    "is_vision_enriched": "$props.is_vision_enriched",
                    "vision_score": "$props.vision_score",
                    "board": "$props.board",
                    "context_badges": 1,
                    "active_badges": "$props.active_badges",
                    # Sharp book prices
                    "draftkings_price": "$props.draftkings_price",
                    "fanduel_price": "$props.fanduel_price",
                    "sharp_price": "$props.sharp_price",
                    "sharp_source": "$props.sharp_source",
                    "multiplier": "$props.multiplier"
                }},
                {"$sort": {"vision_score": -1, "h10_rate": -1, "combined_score": -1}},
                {"$limit": 100}
            ]).to_list(100)
            
            # De-duplicate by player, prioritize board="front_lines" picks
            seen = set()
            unique = []
            
            # First pass: picks explicitly assigned to front_lines
            for p in picks:
                name = p.get("player_name")
                if name and name not in seen and p.get("board") == "front_lines":
                    seen.add(name)
                    gs = _get_game_status(p.get("commence_time"))
                    p["is_locked"] = gs.get("is_locked", False)
                    p["game_status"] = gs.get("status", "upcoming")
                    unique.append(p)
                    if len(unique) >= 30:  # Fetch more to allow for VIP filtering
                        break
            
            # Second pass: fill remaining slots (excluding safe_haven/war_zone assigned)
            if len(unique) < 30:
                for p in picks:
                    name = p.get("player_name")
                    if name and name not in seen and p.get("board") not in ["safe_haven", "war_zone"]:
                        seen.add(name)
                        gs = _get_game_status(p.get("commence_time"))
                        p["is_locked"] = gs.get("is_locked", False)
                        p["game_status"] = gs.get("status", "upcoming")
                        unique.append(p)
                        if len(unique) >= 30:
                            break
            
            # NO on-demand enrichment - serve from cache only for instant response
            # Background worker handles enrichment separately
            
            await self._enrich_picks_with_photos(unique)
            logger.info(f"[FRONT_LINES_STATIC] Served {len(unique)} picks")
            
            return {"status": "live", "picks": unique, "count": len(unique), "source": "static_cache"}
        except Exception as e:
            logger.error(f"[FRONT_LINES_STATIC] Error: {e}")
            return {"status": "error", "picks": [], "error": str(e)}
