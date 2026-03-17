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

from motor.motor_asyncio import AsyncIOMotorDatabase

# CONSOLIDATED: Use shared player lookup utility
from utils.player_lookup import get_player_by_id, get_player_by_name as shared_get_player_by_name

logger = logging.getLogger(__name__)


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
        
        # PIPE 1: Stats Vault (Tank01 CRON destination)
        self.master_hub = db.nba_master_hub_2026
    
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
        player_id = pick.get('player_id') or pick.get('tank01_player_id') or pick.get('nba_player_id')
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
        game_logs = player.get("game_logs", []) or player.get("bdl_game_logs", [])
        
        # Helper function to calculate hit rates from game logs
        def calculate_hit_rates(logs, stat_type, line):
            """Calculate L5/L10 hit rates from game logs"""
            if not logs:
                return {"h5_rate": 0, "h10_rate": 0, "l5_avg": 0, "l10_avg": 0, "season_avg": 0}
            
            # Sort by date descending
            sorted_logs = sorted(logs, key=lambda x: x.get("game_date", "") or x.get("game", {}).get("date", ""), reverse=True)
            
            # Map stat type to game log field
            stat_map = {
                "PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl", "BLK": "blk",
                "3PM": "tptfgm", "THREES": "tptfgm", "TO": "tov", 
                "PRA": "pra", "P+R": "pts_reb", "PR": "pts_reb",
                "P+A": "pts_ast", "PA": "pts_ast", "R+A": "reb_ast", "RA": "reb_ast"
            }
            log_key = stat_map.get(stat_type.upper(), stat_type.lower())
            
            def calc_stats(game_list):
                if not game_list:
                    return 0, 0
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
                return avg, hit_rate
            
            l5_avg, h5_rate = calc_stats(sorted_logs[:5])
            l10_avg, h10_rate = calc_stats(sorted_logs[:10])
            season_avg, _ = calc_stats(sorted_logs)
            
            return {
                "h5_rate": round(h5_rate, 1),
                "h10_rate": round(h10_rate, 1),
                "l5_avg": round(l5_avg, 1),
                "l10_avg": round(l10_avg, 1),
                "season_avg": round(season_avg, 1)
            }
        
        # Calculate hit rates from game logs (even if baseline_stats exists)
        hit_rate_data = calculate_hit_rates(game_logs, stat_type, line)
        
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
                "l5_avg": hit_rate_data["l5_avg"] if hit_rate_data["l5_avg"] > 0 else round(season_avg, 1),
                "l10_avg": hit_rate_data["l10_avg"] if hit_rate_data["l10_avg"] > 0 else round(season_avg, 1),
                "diff_from_avg": diff_from_avg,
                "is_stale": is_stale,
                "stats_source": "bdl_baseline" if not game_logs else "bdl_baseline+game_logs",
                "photo_url": player.get("headshot_url") or player.get("photo_url"),
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
                "l5_avg": hit_rate_data["l5_avg"] if hit_rate_data["l5_avg"] > 0 else round(l5_avg, 1),
                "l10_avg": hit_rate_data["l10_avg"] if hit_rate_data["l10_avg"] > 0 else round(l10_avg, 1),
                "diff_from_avg": diff_from_avg,
                "is_stale": is_stale,
                "stats_source": "baseline_stats" if not game_logs else "baseline_stats+game_logs",
                "photo_url": player.get("headshot_url") or player.get("photo_url"),
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
                "photo_url": player.get("headshot_url") or player.get("photo_url"),
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
        
        # Sort by date descending
        game_logs = sorted(game_logs, key=lambda x: x.get("game_date", "") or x.get("game", {}).get("date", ""), reverse=True)
        
        # Map stat type to game log field
        stat_map = {
            "PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl", "BLK": "blk",
            "3PM": "fg3m", "THREES": "fg3m", "TO": "tov", "PRA": "pra", 
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
            "photo_url": player.get("headshot_url") or player.get("photo_url"),
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
        Get the War Zone - HIGH-TIER DEMON picks with statistical probability.
        
        WAR ZONE LOGIC (Statistical Probability for Heaters):
        1. ANCHOR COMPARISON (The Boost): Demon line must be 10-20% above Standard line
        2. HEATER VERIFICATION (L5 vs Season): L5 avg must be >= 15% higher than season_avg
        3. PROBABILITY THRESHOLD (H10 Hit Rate): Must hit demon line in 7+ of last 10 games (H10 >= 70%)
        4. SUFFIX-NEUTRAL IDENTITY: Uses _normalize_name for player matching
        
        Returns only statistically-backed demon plays where player is currently "heating up".
        """
        from datetime import datetime, timezone
        
        # Get all players that have demon props
        players = await self.cached_board.find(
            {"props.is_demon": True},
            {"_id": 0}
        ).to_list(200)
        
        # Build candidate picks - ALL demon props
        candidates = []
        for player_doc in players:
            player_name = player_doc.get("player_name")
            if not player_name:
                continue
            
            # Get ALL demon props for this player
            demon_props = [p for p in player_doc.get("props", []) if p.get("is_demon")]
            
            for prop in demon_props:
                stat_type = prop.get("stat_type_extracted") or prop.get("market", "").replace("player_", "")
                demon_line = prop.get("line")
                anchor_line = prop.get("anchor_line")  # Standard line
                
                if not demon_line or not anchor_line:
                    continue
                
                # FILTER 1: ANCHOR COMPARISON - Demon must be 10-20% ABOVE Standard
                boost_pct = ((demon_line - anchor_line) / anchor_line) * 100
                if boost_pct < 10 or boost_pct > 20:
                    continue
                
                candidates.append({
                    "player_name": player_name,
                    "team": player_doc.get("team"),
                    "opponent": player_doc.get("opponent"),
                    "game_id": player_doc.get("game_id"),
                    "stat_type": stat_type,
                    "line": demon_line,
                    "anchor_line": anchor_line,
                    "boost_pct": round(boost_pct, 1),
                    "odds": prop.get("price"),
                    "direction": prop.get("direction", "over"),
                    "is_demon": True,
                    "is_goblin": False,
                    "tier_label": "DEMON",
                    "tier_source": "war_zone_heater",
                    "is_alternate_market": prop.get("is_alternate_market", True)
                })
        
        if not candidates:
            logger.warning("[WAR_ZONE] No demon candidates with 10-20% boost found")
            return {"picks": [], "picks_count": 0, "filters_applied": ["anchor_10_20_pct"]}
        
        # Apply Heater and Hit Rate filters
        war_zone_picks = []
        filter_stats = {
            "total_candidates": len(candidates),
            "passed_boost": len(candidates),
            "passed_heater": 0,
            "passed_h10": 0
        }
        
        for pick in candidates:
            player_name = pick["player_name"]
            stat_type = pick["stat_type"]
            demon_line = pick["line"]
            
            # Get master hub data using suffix-neutral name matching
            hub_player = await self._get_master_player_by_name(player_name)
            
            if not hub_player:
                logger.debug(f"[WAR_ZONE] No master hub data for: {player_name}")
                continue
            
            # Get baseline stats and game logs from Vault
            baseline_stats = hub_player.get("baseline_stats", {})
            game_logs = hub_player.get("bdl_game_logs", []) or hub_player.get("game_logs", [])
            
            if not game_logs or len(game_logs) < 10:
                logger.debug(f"[WAR_ZONE] Insufficient game logs for {player_name}: {len(game_logs) if game_logs else 0}")
                continue
            
            # Get season_avg from BDL baseline_stats
            # Structure: {"PTS": {"season_avg": 19.3}, ...} or {"pts": 19.3, ...}
            stat_key = self._normalize_stat_key(stat_type)
            
            # Try nested structure first (e.g., baseline_stats["PTS"]["season_avg"])
            season_avg = None
            stat_data = baseline_stats.get(stat_key, {})
            if isinstance(stat_data, dict) and stat_data.get("season_avg") is not None:
                season_avg = stat_data.get("season_avg")
            elif isinstance(stat_data, (int, float)):
                # Flat structure (e.g., baseline_stats["pts"] = 19.3)
                season_avg = stat_data
            else:
                # Try lowercase key for flat BDL structure
                flat_key_map = {
                    "PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl", 
                    "BLK": "blk", "3PM": "fg3m"
                }
                flat_key = flat_key_map.get(stat_key)
                if flat_key and baseline_stats.get(flat_key) is not None:
                    val = baseline_stats.get(flat_key)
                    season_avg = val if isinstance(val, (int, float)) else val.get("season_avg") if isinstance(val, dict) else None
            
            # Handle combo stats (PRA, PR, PA, RA)
            if season_avg is None and stat_key in ["PRA", "PR", "PA", "RA"]:
                def get_stat_avg(key):
                    """Helper to get season_avg from nested or flat structure"""
                    data = baseline_stats.get(key, {})
                    if isinstance(data, dict):
                        return data.get("season_avg", 0) or 0
                    return data if isinstance(data, (int, float)) else 0
                
                if stat_key == "PRA":
                    season_avg = get_stat_avg("PTS") + get_stat_avg("REB") + get_stat_avg("AST")
                elif stat_key == "PR":
                    season_avg = get_stat_avg("PTS") + get_stat_avg("REB")
                elif stat_key == "PA":
                    season_avg = get_stat_avg("PTS") + get_stat_avg("AST")
                elif stat_key == "RA":
                    season_avg = get_stat_avg("REB") + get_stat_avg("AST")
            
            if not season_avg or season_avg <= 0:
                logger.debug(f"[WAR_ZONE] No season_avg for {player_name} {stat_type}")
                continue
            
            # Calculate L5 average from game logs
            l5_result = self._calculate_l5_avg(game_logs, stat_type)
            l5_avg = l5_result["avg"]
            
            if l5_avg <= 0:
                logger.debug(f"[WAR_ZONE] No L5 avg for {player_name} {stat_type}")
                continue
            
            # FILTER 2: HEATER VERIFICATION - L5 must be >= 15% higher than Season
            heater_threshold = season_avg * 1.15
            if l5_avg < heater_threshold:
                logger.debug(f"[WAR_ZONE] L5 {l5_avg:.1f} < heater threshold {heater_threshold:.1f} for {player_name} {stat_type}")
                continue
            filter_stats["passed_heater"] += 1
            
            # FILTER 3: H10 HIT RATE - Must exceed demon line in 7+ of last 10 games
            h10_result = self._calculate_h10_hit_rate(game_logs, stat_type, demon_line)
            
            if h10_result["games_counted"] < 10:
                logger.debug(f"[WAR_ZONE] Insufficient games for H10: {h10_result['games_counted']}")
                continue
            
            if h10_result["hit_rate"] < 70:
                logger.debug(f"[WAR_ZONE] H10 {h10_result['hit_rate']}% < 70% for {player_name} {stat_type} @ {demon_line}")
                continue
            filter_stats["passed_h10"] += 1
            
            # PASSED ALL FILTERS - Add to War Zone
            heater_pct = round(((l5_avg - season_avg) / season_avg) * 100, 1)
            
            pick["season_avg"] = round(season_avg, 1)
            pick["l5_avg"] = round(l5_avg, 1)
            pick["heater_pct"] = heater_pct
            pick["heater_qualified"] = True
            pick["h10_hits"] = h10_result["hits"]
            pick["h10_games"] = h10_result["games_counted"]
            pick["h10_rate"] = h10_result["hit_rate"]
            
            # Add photo and enrichment
            pick["photo_url"] = hub_player.get("headshot_url") or hub_player.get("photo_url")
            pick["position"] = hub_player.get("position")
            
            war_zone_picks.append(pick)
            logger.info(f"[WAR_ZONE] ✓ {player_name} {stat_type} @ {demon_line} | Boost: {pick['boost_pct']}% | Heater: +{heater_pct}% | H10: {h10_result['hit_rate']}%")
        
        # Sort by heater percentage (highest first), then by H10 hit rate
        war_zone_picks.sort(key=lambda x: (x.get("heater_pct", 0), x.get("h10_rate", 0)), reverse=True)
        
        logger.info(f"[WAR_ZONE] Filters: {filter_stats}")
        
        return {
            "picks": war_zone_picks[:20],
            "picks_count": len(war_zone_picks),
            "filter_stats": filter_stats,
            "filters_applied": ["anchor_10_20_pct_boost", "heater_l5_15pct_above_season", "h10_hit_rate_70pct"]
        }
    
    def _calculate_l5_avg(self, game_logs: List[Dict], stat_type: str) -> Dict:
        """
        Calculate L5 average from last 5 game logs.
        
        Returns: {"avg": float, "games_counted": int, "values": list}
        """
        if not game_logs:
            return {"avg": 0, "games_counted": 0, "values": []}
        
        # Take last 5 games (most recent first)
        recent_games = game_logs[:5]
        
        stat_key = self._normalize_stat_key(stat_type)
        
        values = []
        
        for game in recent_games:
            # Get stat value from game log
            if stat_key == 'PRA':
                value = (game.get('pts', 0) or 0) + (game.get('reb', 0) or 0) + (game.get('ast', 0) or 0)
            elif stat_key == 'PR':
                value = (game.get('pts', 0) or 0) + (game.get('reb', 0) or 0)
            elif stat_key == 'PA':
                value = (game.get('pts', 0) or 0) + (game.get('ast', 0) or 0)
            elif stat_key == 'RA':
                value = (game.get('reb', 0) or 0) + (game.get('ast', 0) or 0)
            else:
                field_map = {'PTS': 'pts', 'REB': 'reb', 'AST': 'ast', 'STL': 'stl', 'BLK': 'blk', '3PM': 'fg3m'}
                field = field_map.get(stat_key)
                if not field:
                    continue
                value = game.get(field, 0) or 0
            
            values.append(value)
        
        avg = round(sum(values) / len(values), 1) if values else 0
        
        return {"avg": avg, "games_counted": len(values), "values": values}
    
    async def get_goblin_vault(self) -> Dict[str, Any]:
        """
        Get the Safe Haven - GOBLIN picks that pass strict filters.
        
        SAFE HAVEN LOGIC:
        1. ANCHOR COMPARISON: Line must be GOBLIN (below Standard Line)
        2. STATISTICAL FLOOR: Goblin line must be LOWER than season_avg
        3. HIT RATE: Player must hit line in 8+ of last 10 games (H10 >= 80%)
        4. FRESHNESS: Player data must be updated within 24 hours
        """
        
        # Get all players that have goblin props
        players = await self.cached_board.find(
            {"props.is_goblin": True},
            {"_id": 0}
        ).to_list(200)
        
        # Build candidate picks from goblin props
        candidates = []
        for player_doc in players:
            player_name = player_doc.get("player_name")
            if not player_name:
                continue
            
            # Find goblin props for this player
            goblin_props = [p for p in player_doc.get("props", []) if p.get("is_goblin")]
            
            for prop in goblin_props:
                stat_type = prop.get("stat_type_extracted") or prop.get("market", "").replace("player_", "")
                line = prop.get("line")
                anchor_line = prop.get("anchor_line")
                
                # FILTER 1: Must be GOBLIN (line < anchor)
                if not line or not anchor_line or line >= anchor_line:
                    continue
                
                candidates.append({
                    "player_name": player_name,
                    "team": player_doc.get("team"),
                    "opponent": player_doc.get("opponent"),
                    "game_id": player_doc.get("game_id"),
                    "stat_type": stat_type,
                    "line": line,
                    "odds": prop.get("price"),
                    "direction": prop.get("direction", "over"),
                    "is_demon": False,
                    "is_goblin": True,
                    "tier_label": "GOBLIN",
                    "anchor_line": anchor_line,
                    "is_alternate_market": prop.get("is_alternate_market", True)
                })
        
        if not candidates:
            logger.warning("[SAFE_HAVEN] No goblin candidates found")
            return {"picks": [], "picks_count": 0, "filters_applied": ["anchor_comparison"]}
        
        # Apply strict Safe Haven filters
        safe_haven_picks = []
        filter_stats = {"total_candidates": len(candidates), "passed_anchor": 0, "passed_floor": 0, "passed_hit_rate": 0, "passed_freshness": 0}
        
        for pick in candidates:
            player_name = pick["player_name"]
            stat_type = pick["stat_type"]
            line = pick["line"]
            
            # Get master hub data for this player (with name normalization)
            hub_player = await self._get_master_player_by_name(player_name)
            
            if not hub_player:
                logger.debug(f"[SAFE_HAVEN] No master hub data for: {player_name}")
                continue
            
            # FILTER 2: FRESHNESS CHECK - Data must be within 24 hours
            last_updated = hub_player.get("last_bdl_sync") or hub_player.get("last_updated")
            if last_updated:
                if isinstance(last_updated, datetime):
                    age = datetime.now(timezone.utc) - last_updated.replace(tzinfo=timezone.utc) if last_updated.tzinfo is None else datetime.now(timezone.utc) - last_updated
                    if age > timedelta(hours=24):
                        logger.debug(f"[SAFE_HAVEN] Stale data for {player_name}: {age}")
                        continue
            filter_stats["passed_freshness"] += 1
            
            # FILTER 3: STATISTICAL FLOOR - Line must be LOWER than season_avg
            baseline_stats = hub_player.get("baseline_stats", {})
            stat_key = self._normalize_stat_key(stat_type)
            stat_data = baseline_stats.get(stat_key, {})
            season_avg = stat_data.get("season_avg") if isinstance(stat_data, dict) else stat_data
            
            if season_avg is None:
                logger.debug(f"[SAFE_HAVEN] No season_avg for {player_name} {stat_type}")
                continue
            
            if line >= season_avg:
                logger.debug(f"[SAFE_HAVEN] Line {line} >= season_avg {season_avg} for {player_name} {stat_type}")
                continue
            filter_stats["passed_floor"] += 1
            
            # FILTER 4: HIT RATE - Must hit line in 8+ of last 10 games (H10 >= 80%)
            game_logs = hub_player.get("bdl_game_logs", []) or hub_player.get("game_logs", [])
            h10_result = self._calculate_h10_hit_rate(game_logs, stat_type, line)
            
            if h10_result["games_counted"] < 10:
                logger.debug(f"[SAFE_HAVEN] Insufficient games for {player_name}: {h10_result['games_counted']}")
                continue
            
            if h10_result["hit_rate"] < 80:
                logger.debug(f"[SAFE_HAVEN] H10 {h10_result['hit_rate']}% < 80% for {player_name} {stat_type} @ {line}")
                continue
            filter_stats["passed_hit_rate"] += 1
            
            # PASSED ALL FILTERS - Add to Safe Haven
            pick["season_avg"] = season_avg
            pick["h10_hits"] = h10_result["hits"]
            pick["h10_games"] = h10_result["games_counted"]
            pick["h10_hit_rate"] = h10_result["hit_rate"]
            pick["floor_margin"] = round(season_avg - line, 1)
            pick["safe_haven_qualified"] = True
            
            # Add photo and other enrichment
            pick["photo_url"] = hub_player.get("photo_url")
            pick["position"] = hub_player.get("position")
            
            safe_haven_picks.append(pick)
            logger.info(f"[SAFE_HAVEN] ✓ {player_name} {stat_type} @ {line} | H10: {h10_result['hit_rate']}% | Floor margin: {pick['floor_margin']}")
        
        # Sort by hit rate (highest first), then by floor margin
        safe_haven_picks.sort(key=lambda x: (x.get("h10_hit_rate", 0), x.get("floor_margin", 0)), reverse=True)
        
        logger.info(f"[SAFE_HAVEN] Filters: {filter_stats}")
        
        return {
            "picks": safe_haven_picks[:20],
            "picks_count": len(safe_haven_picks),
            "filter_stats": filter_stats,
            "filters_applied": ["anchor_comparison", "statistical_floor", "h10_hit_rate", "freshness_24h"]
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
        Calculate H10 hit rate from last 10 game logs.
        
        Returns: {"hits": int, "games_counted": int, "hit_rate": float}
        """
        if not game_logs:
            return {"hits": 0, "games_counted": 0, "hit_rate": 0}
        
        # Take last 10 games (most recent first)
        recent_games = game_logs[:10]
        
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
                value = (game.get('pts', 0) or 0) + (game.get('reb', 0) or 0) + (game.get('ast', 0) or 0)
            elif stat_key == 'PR':
                value = (game.get('pts', 0) or 0) + (game.get('reb', 0) or 0)
            elif stat_key == 'PA':
                value = (game.get('pts', 0) or 0) + (game.get('ast', 0) or 0)
            elif stat_key == 'RA':
                value = (game.get('reb', 0) or 0) + (game.get('ast', 0) or 0)
            elif field:
                value = game.get(field, 0) or 0
            else:
                continue
            
            games_counted += 1
            if value > line:  # Must EXCEED the line (not just meet it)
                hits += 1
        
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
        Get THE FRONT LINES - Medium tier picks with statistical probability focus.
        
        FRONT LINES LOGIC:
        1. ANCHOR COMPARISON (Discount): Line must be GOBLIN, 7-12% below Standard
        2. PROBABILITY FLOOR: Line must be at least 5% LOWER than season_avg
        3. CONSISTENCY CHECK: L25 Hit Rate >= 72% (18+ of last 25 games)
        4. EXCLUSION: Must NOT qualify for Safe Haven (H10 >= 80%)
        """
        from services.bdl_comprehensive_sync import _normalize_name
        
        # Get all players that have goblin props
        players = await self.cached_board.find(
            {"props.is_goblin": True},
            {"_id": 0}
        ).to_list(200)
        
        # Build candidate picks from goblin props
        candidates = []
        for player_doc in players:
            player_name = player_doc.get("player_name")
            if not player_name:
                continue
            
            # Find goblin props for this player
            goblin_props = [p for p in player_doc.get("props", []) if p.get("is_goblin")]
            
            for prop in goblin_props:
                stat_type = prop.get("stat_type_extracted") or prop.get("market", "").replace("player_", "")
                line = prop.get("line")
                anchor_line = prop.get("anchor_line")
                
                if not line or not anchor_line or line >= anchor_line:
                    continue
                
                # FILTER 1: ANCHOR COMPARISON - Must be 7-12% below Standard
                discount_pct = ((anchor_line - line) / anchor_line) * 100
                if discount_pct < 7 or discount_pct > 12:
                    continue
                
                candidates.append({
                    "player_name": player_name,
                    "team": player_doc.get("team"),
                    "opponent": player_doc.get("opponent"),
                    "game_id": player_doc.get("game_id"),
                    "stat_type": stat_type,
                    "line": line,
                    "odds": prop.get("price"),
                    "direction": prop.get("direction", "over"),
                    "is_demon": False,
                    "is_goblin": True,
                    "tier_label": "FRONT_LINE",
                    "anchor_line": anchor_line,
                    "discount_pct": round(discount_pct, 1),
                    "is_alternate_market": prop.get("is_alternate_market", True)
                })
        
        if not candidates:
            logger.warning("[FRONT_LINES] No candidates found with 7-12% discount")
            return {"picks": [], "picks_count": 0, "filters_applied": ["anchor_7_12_pct"]}
        
        # Apply probability filters
        front_line_picks = []
        filter_stats = {
            "total_candidates": len(candidates), 
            "passed_discount": len(candidates),
            "passed_season_floor": 0, 
            "passed_l25": 0,
            "excluded_safe_haven": 0
        }
        
        for pick in candidates:
            player_name = pick["player_name"]
            stat_type = pick["stat_type"]
            line = pick["line"]
            
            # Get master hub data for this player
            hub_player = await self._get_master_player_by_name(player_name)
            
            if not hub_player:
                logger.debug(f"[FRONT_LINES] No master hub data for: {player_name}")
                continue
            
            # FILTER 2: PROBABILITY FLOOR - Line must be 5% LOWER than season_avg
            baseline_stats = hub_player.get("baseline_stats", {})
            stat_key = self._normalize_stat_key(stat_type)
            stat_data = baseline_stats.get(stat_key, {})
            season_avg = stat_data.get("season_avg") if isinstance(stat_data, dict) else stat_data
            
            if season_avg is None:
                logger.debug(f"[FRONT_LINES] No season_avg for {player_name} {stat_type}")
                continue
            
            # Line must be at least 5% lower than season_avg
            season_floor = season_avg * 0.95  # 5% buffer
            if line > season_floor:
                logger.debug(f"[FRONT_LINES] Line {line} > season_floor {season_floor:.1f} for {player_name}")
                continue
            filter_stats["passed_season_floor"] += 1
            
            # Get game logs for H10 and L25 calculation
            game_logs = hub_player.get("bdl_game_logs", []) or hub_player.get("game_logs", [])
            
            # Check H10 - EXCLUDE if >= 80% (those go to Safe Haven)
            h10_result = self._calculate_h10_hit_rate(game_logs, stat_type, line)
            if h10_result["games_counted"] >= 10 and h10_result["hit_rate"] >= 80:
                logger.debug(f"[FRONT_LINES] Excluded (Safe Haven): {player_name} {stat_type} H10={h10_result['hit_rate']}%")
                filter_stats["excluded_safe_haven"] += 1
                continue
            
            # FILTER 3: L25 HIT RATE - Must hit in 72% of available games (up to 25)
            l25_result = self._calculate_l25_hit_rate(game_logs, stat_type, line)
            
            if l25_result["games_counted"] < 10:  # Need at least 10 games for reliability
                logger.debug(f"[FRONT_LINES] Insufficient games for {player_name}: {l25_result['games_counted']}")
                continue
            
            if l25_result["hit_rate"] < 72:
                logger.debug(f"[FRONT_LINES] L25 {l25_result['hit_rate']}% < 72% for {player_name} {stat_type}")
                continue
            filter_stats["passed_l25"] += 1
            
            # PASSED ALL FILTERS - Add to Front Lines
            pick["season_avg"] = season_avg
            pick["season_floor"] = round(season_floor, 1)
            pick["floor_buffer_pct"] = round(((season_avg - line) / season_avg) * 100, 1)
            pick["h10_hits"] = h10_result["hits"]
            pick["h10_games"] = h10_result["games_counted"]
            pick["h10_hit_rate"] = h10_result["hit_rate"]
            pick["l25_hits"] = l25_result["hits"]
            pick["l25_games"] = l25_result["games_counted"]
            pick["l25_hit_rate"] = l25_result["hit_rate"]
            pick["front_line_qualified"] = True
            
            # Add photo and enrichment
            pick["photo_url"] = hub_player.get("photo_url")
            pick["position"] = hub_player.get("position")
            
            front_line_picks.append(pick)
            logger.info(f"[FRONT_LINES] ✓ {player_name} {stat_type} @ {line} | Discount: {pick['discount_pct']}% | L25: {l25_result['hit_rate']}%")
        
        # Sort by L25 hit rate (highest first), then by discount
        front_line_picks.sort(key=lambda x: (x.get("l25_hit_rate", 0), x.get("discount_pct", 0)), reverse=True)
        
        logger.info(f"[FRONT_LINES] Filters: {filter_stats}")
        
        return {
            "picks": front_line_picks[:20],
            "picks_count": len(front_line_picks),
            "filter_stats": filter_stats,
            "filters_applied": ["anchor_7_12_pct", "season_floor_5pct", "l25_hit_rate_72pct", "exclude_safe_haven"]
        }
    
    def _calculate_l25_hit_rate(self, game_logs: List[Dict], stat_type: str, line: float) -> Dict:
        """
        Calculate L25 hit rate from last 25 game logs.
        
        Returns: {"hits": int, "games_counted": int, "hit_rate": float}
        """
        if not game_logs:
            return {"hits": 0, "games_counted": 0, "hit_rate": 0}
        
        # Take last 25 games
        recent_games = game_logs[:25]
        
        stat_key = self._normalize_stat_key(stat_type)
        
        hits = 0
        games_counted = 0
        
        for game in recent_games:
            # Get stat value from game log
            if stat_key == 'PRA':
                value = (game.get('pts', 0) or 0) + (game.get('reb', 0) or 0) + (game.get('ast', 0) or 0)
            elif stat_key == 'PR':
                value = (game.get('pts', 0) or 0) + (game.get('reb', 0) or 0)
            elif stat_key == 'PA':
                value = (game.get('pts', 0) or 0) + (game.get('ast', 0) or 0)
            elif stat_key == 'RA':
                value = (game.get('reb', 0) or 0) + (game.get('ast', 0) or 0)
            else:
                field_map = {'PTS': 'pts', 'REB': 'reb', 'AST': 'ast', 'STL': 'stl', 'BLK': 'blk', '3PM': 'fg3m'}
                field = field_map.get(stat_key)
                if not field:
                    continue
                value = game.get(field, 0) or 0
            
            games_counted += 1
            if value > line:
                hits += 1
        
        hit_rate = round((hits / games_counted) * 100) if games_counted > 0 else 0
        
        return {"hits": hits, "games_counted": games_counted, "hit_rate": hit_rate}
    
    async def get_parlay_builder(self) -> Dict[str, Any]:
        """
        Get the Parlay Builder (Gauntlet) parlays from MongoDB.
        Data is PRE-ENRICHED during sync. Just reads and returns with AI insights.
        """
        doc = await self.parlay_builder.find_one({}, {"_id": 0})
        
        if not doc:
            return {
                "success": False,
                "message": "No parlay data. Run /api/v3/sync first.",
                "parlays": {}
            }
        
        # Add AI insights to parlay picks
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
        Get the Goblin Recon (Safe Haven) parlays from MongoDB.
        Data is PRE-ENRICHED during sync. Just reads and returns with AI insights.
        """
        doc = await self.goblin_recon.find_one({}, {"_id": 0})
        
        if not doc:
            return {
                "success": False,
                "message": "No Recon data. Run /api/v3/sync first.",
                "parlays": {}
            }
        
        # Add AI insights to parlay picks
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
                "description": "Maximum win probability using high-consistency picks",
                "min_hit_rate": "88%+",
                "flex_play": "6-Pick Fortress designed for PrizePicks Flex"
            }
        }
    
    async def get_cached_board(self) -> Dict[str, Any]:
        """
        Get the CACHED board from MongoDB.
        NO API CALLS - reads only from database.
        
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
        
        # Get only player-centric documents (those with props array)
        players = await self.cached_board.find(
            {"props": {"$exists": True}},
            {"_id": 0}
        ).sort("rank", 1).to_list(500)
        
        # Clean any remaining ObjectIds
        for player in players:
            self._clean_object_ids(player)
        
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
        Get a single player from the CACHED board or player_data.
        Also includes advanced analytics insights.
        NO API CALLS - reads only from database.
        
        Stats (L5/L10/SZN) come EXCLUSIVELY from nba_master_hub_2026.baseline_stats.
        """
        # Try dg_cached_board first (has opponent data)
        player = await self.cached_board.find_one(
            {"player_name": player_name},
            {"_id": 0}
        )
        
        if player:
            self._clean_object_ids(player)
            await self._enrich_player_with_master_hub_stats(player)
            await self._add_player_insights(player)
            return {"success": True, "player": player, "source": "cached_board"}
        
        # Try case-insensitive search in cached_board
        player = await self.cached_board.find_one(
            {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0}
        )
        
        if player:
            self._clean_object_ids(player)
            await self._enrich_player_with_master_hub_stats(player)
            await self._add_player_insights(player)
            return {"success": True, "player": player, "source": "cached_board"}
        
        # Fallback: Try player_data (exact match)
        player = await self.player_data.find_one(
            {"player_name": player_name},
            {"_id": 0}
        )
        
        if player:
            self._clean_object_ids(player)
            await self._enrich_player_with_master_hub_stats(player)
            await self._add_player_insights(player)
            return {"success": True, "player": player, "source": "player_data"}
        
        # Try case-insensitive in player_data
        player = await self.player_data.find_one(
            {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0}
        )
        
        if player:
            self._clean_object_ids(player)
            await self._enrich_player_with_master_hub_stats(player)
            await self._add_player_insights(player)
            return {"success": True, "player": player, "source": "player_data"}
        
        # Normalized name search in both collections (replaces fuzzy matching)
        all_players_pd = await self.player_data.find({}, {"player_name": 1, "_id": 0}).to_list(500)
        all_players_cb = await self.cached_board.find({}, {"player_name": 1, "_id": 0}).to_list(500)
        
        normalized_search = _normalize_name(player_name)
        best_match = None
        match_source = None
        
        # Check player_data first
        for p in all_players_pd:
            if _normalize_name(p.get("player_name", "")) == normalized_search:
                best_match = p["player_name"]
                match_source = "player_data"
                break
        
        # Check cached_board if not found
        if not best_match:
            for p in all_players_cb:
                if _normalize_name(p.get("player_name", "")) == normalized_search:
                    best_match = p["player_name"]
                    match_source = "cached_board"
                    break
        
        if best_match:
            collection = self.player_data if match_source == "player_data" else self.cached_board
            player = await collection.find_one(
                {"player_name": best_match},
                {"_id": 0}
            )
            self._clean_object_ids(player)
            await self._enrich_player_with_master_hub_stats(player)
            await self._add_player_insights(player)
            return {"success": True, "player": player, "matched_name": best_match, "source": match_source}
        
        return {
            "success": False,
            "message": "Lines loading... Player not in cache.",
            "player": None
        }
    
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
        baseline_stats = hub_player.get("baseline_stats", {})
        game_logs = hub_player.get("game_logs", [])
        
        # Add structural data - PHOTOS FROM MASTER HUB ONLY
        player["baseline_stats"] = baseline_stats
        player["photo_url"] = hub_player.get("headshot_url")
        player["headshot_url"] = hub_player.get("headshot_url")
        
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
            
            # Calculate COUPLED stats from PIPE 1 game_logs
            if game_logs and line_value > 0:
                coupled = calculate_coupled_stats(game_logs, stat_type, line_value)
                
                # Use coupled stats for L5 and L10 (guaranteed consistent)
                prop["l5_avg"] = coupled["l5"]["avg"]
                prop["l10_avg"] = coupled["l10"]["avg"]
                prop["l5_hit_rate"] = coupled["l5"]["hit_rate"]
                prop["l10_hit_rate"] = coupled["l10"]["hit_rate"]
                prop["l5_games_over"] = coupled["l5"]["games_over"]
                prop["l10_games_over"] = coupled["l10"]["games_over"]
                prop["l5_total_games"] = coupled["l5"]["total_games"]
                prop["l10_total_games"] = coupled["l10"]["total_games"]
                
                # Season avg from coupled calculation (or fallback to baseline)
                prop["season_avg"] = coupled["season"]["avg"] or baseline_stats.get(stat_key, {}).get("season_avg")
                prop["season_hit_rate"] = coupled["season"]["hit_rate"]
                
                # Mark stats source
                prop["stats_coupled"] = True
                prop["stats_source"] = "ssot_game_logs"
            else:
                # Fallback to baseline_stats if no game logs
                stat_data = baseline_stats.get(stat_key, {})
                prop["l5_avg"] = stat_data.get("l5_avg")
                prop["l10_avg"] = stat_data.get("l10_avg")
                prop["season_avg"] = stat_data.get("season_avg")
                prop["stats_coupled"] = False
                prop["stats_source"] = "ssot_baseline"
            
            # If this is a radar pick (demon or goblin), add full intel_suite
            is_radar = prop.get("is_demon") or prop.get("is_goblin") or prop.get("is_radar_pick")
            if is_radar:
                intel_suite = await intel_calculator.calculate_intel_suite(
                    player_name=player_name,
                    stat_type=stat_key,
                    line=prop.get("line", 0),
                    direction=prop.get("direction", "over"),
                    opponent=player.get("opponent"),
                    board_pick=prop  # Pass prop as board_pick for additional context
                )
                prop["intel_suite"] = intel_suite
        
        logger.debug(f"[SSOT] Enriched {len(props)} props for {player_name}")
    
    async def get_most_popular_bets(self) -> Dict[str, Any]:
        """
        Get Top 20 Most Popular BETS (specific props, not just players)
        Returns actual bet lines with ticket volume/popularity scoring
        Includes Standard, Demon, and Goblin lines
        Auto-purges games that have already started
        
        Pulls from dg_radar_picks, dg_goblin_vault, and dg_front_lines collections
        """
        try:
            now = datetime.now(timezone.utc)
            popular_bets = []
            
            # STRATEGY: Get bets from tiered picks collections
            # These have already been processed with hit rates and season_avg
            
            # Get from War Zone (Demons)
            radar_picks = await self.radar_picks.find({}, {"_id": 0}).to_list(20)
            for pick in radar_picks:
                popular_bets.append({
                    "player_name": pick.get("player_name", ""),
                    "player_id": pick.get("player_id") or pick.get("tank01_player_id"),
                    "team": pick.get("team", ""),
                    "stat_type": pick.get("stat_type", ""),
                    "line": pick.get("demon_line") or pick.get("line"),
                    "line_type": "demon",
                    "is_demon": True,
                    "is_goblin": False,
                    "direction": pick.get("direction", "over").lower(),
                    "h10_rate": pick.get("h10_rate", 0),
                    "h5_rate": pick.get("h5_rate", 0),
                    "h10_over": pick.get("h10_over", 0),
                    "h10_games": pick.get("h10_games", 10),
                    "h5_over": pick.get("h5_over", 0),
                    "h5_games": pick.get("h5_games", 5),
                    "season_avg": pick.get("season_avg"),
                    "gap_pct": pick.get("gap_pct", 0),
                    "popularity_score": pick.get("radar_score", 0) or pick.get("demon_score", 0),
                    "odds": pick.get("demon_odds") or pick.get("odds"),
                    "commence_time": pick.get("commence_time"),
                    "source": "war_zone"
                })
            
            # Get from Safe Haven (Goblins)
            vault_picks = await self.goblin_vault.find({}, {"_id": 0}).to_list(20)
            for pick in vault_picks:
                popular_bets.append({
                    "player_name": pick.get("player_name", ""),
                    "player_id": pick.get("player_id") or pick.get("tank01_player_id"),
                    "team": pick.get("team", ""),
                    "stat_type": pick.get("stat_type", ""),
                    "line": pick.get("goblin_line") or pick.get("line"),
                    "line_type": "goblin",
                    "is_demon": False,
                    "is_goblin": True,
                    "direction": pick.get("direction", "over").lower(),
                    "h10_rate": pick.get("h10_rate", 0),
                    "h5_rate": pick.get("h5_rate", 0),
                    "h10_over": pick.get("h10_over", 0),
                    "h10_games": pick.get("h10_games", 10),
                    "h5_over": pick.get("h5_over", 0),
                    "h5_games": pick.get("h5_games", 5),
                    "season_avg": pick.get("season_avg"),
                    "gap_pct": pick.get("gap_pct", 0),
                    "popularity_score": pick.get("vault_score", 0) or pick.get("goblin_score", 0),
                    "odds": pick.get("goblin_odds") or pick.get("odds"),
                    "commence_time": pick.get("commence_time"),
                    "source": "safe_haven"
                })
            
            # Get from Front Lines (Mixed)
            front_picks = await self.front_lines.find({}, {"_id": 0}).to_list(20)
            for pick in front_picks:
                is_demon = pick.get("is_demon", False)
                is_goblin = pick.get("is_goblin", False)
                popular_bets.append({
                    "player_name": pick.get("player_name", ""),
                    "team": pick.get("team", ""),
                    "photo_url": pick.get("photo_url", ""),
                    "stat_type": pick.get("stat_type", ""),
                    "line": pick.get("demon_line") if is_demon else pick.get("goblin_line") if is_goblin else pick.get("line"),
                    "line_type": "demon" if is_demon else "goblin" if is_goblin else "standard",
                    "is_demon": is_demon,
                    "is_goblin": is_goblin,
                    "direction": pick.get("direction", "over").lower(),
                    "h10_rate": pick.get("h10_rate", 0),
                    "h5_rate": pick.get("h5_rate", 0),
                    "h10_over": pick.get("h10_over", 0),
                    "h10_games": pick.get("h10_games", 10),
                    "h5_over": pick.get("h5_over", 0),
                    "h5_games": pick.get("h5_games", 5),
                    "season_avg": pick.get("season_avg"),
                    "gap_pct": pick.get("gap_pct", 0),
                    "popularity_score": pick.get("front_lines_score", 0),
                    "odds": pick.get("odds"),
                    "commence_time": pick.get("commence_time"),
                    "source": "front_lines"
                })
            
            # Sort by popularity/score and dedupe
            seen = set()
            unique_bets = []
            for bet in sorted(popular_bets, key=lambda x: x.get("popularity_score", 0), reverse=True):
                key = f"{bet['player_name']}_{bet['stat_type']}_{bet['line']}"
                if key not in seen:
                    seen.add(key)
                    unique_bets.append(bet)
            
            # ===== PLAYER DATA & STATS ENRICHMENT from nba_master_hub_2026 =====
            # ALL player data and stats come from master hub by player_id
            for bet in unique_bets[:20]:
                master_player = await self._get_master_player(bet)
                if master_player:
                    stat_type = bet.get('stat_type', '')
                    # Player identity & photo FROM MASTER HUB
                    bet['player_id'] = master_player.get('player_id')
                    bet['nba_id'] = master_player.get('nba_id') or master_player.get('nba_player_id')
                    bet['espn_id'] = master_player.get('espn_id')
                    bet['photo_url'] = master_player.get('headshot_url')
                    bet['headshot_url'] = master_player.get('headshot_url')
                    
                    # Baseline stats for this prop category
                    baseline_stats = master_player.get('baseline_stats', {})
                    if stat_type and baseline_stats:
                        cat_stats = baseline_stats.get(stat_type, {})
                        if cat_stats:
                            bet['l5_avg'] = cat_stats.get('l5_avg')
                            bet['l10_avg'] = cat_stats.get('l10_avg')
                            bet['season_avg'] = cat_stats.get('season_avg')
                    
                    bet['baseline_stats'] = baseline_stats
            
            return {
                "status": "live" if unique_bets else "awaiting_action",
                "bets": unique_bets[:20],
                "total_available": len(unique_bets),
                "timestamp": now.isoformat()
            }
            
        except Exception as e:
            logger.error(f"[MOST_POPULAR] Error: {e}")
            return {
                "status": "error",
                "bets": [],
                "error": str(e)
            }
    
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
            # PHOTOS FROM MASTER HUB ONLY
            pick['headshot_url'] = master_player.get('headshot_url')
            pick['photo_url'] = master_player.get('headshot_url')
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
            player['headshot_url'] = master_player.get('headshot_url')
            player['photo_url'] = master_player.get('headshot_url')
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
