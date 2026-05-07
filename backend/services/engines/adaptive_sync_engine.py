"""
ADAPTIVE SYNC ENGINE - PropVision Multi-Sport Polling System
==============================================================
Implements "Market Stability" polling optimized for both NBA and MLB.

POLLING SCHEDULE (2026 Season):
- STANDBY (> 8 hours):     Every 4 hours   - Early board scouting, line openers
- ACTIVE (2-8 hours):      Every 60 min    - Catching initial line moves
- LOCK_IN (30m-2 hours):   Every 15 min    - Lineup Gate: MLB lineups confirmed, NBA rotations
- FINAL_CALL (< 30 min):   Every 10 min    - Last-minute Sharp moves, Demon verification
- LIVE (Game started):     STOP            - No betting on active games (stale/trap lines)

SPORT KEYS:
- NBA: basketball_nba
- MLB: baseball_mlb

MLB-SPECIFIC FEATURES:
- Lineup Gate: Props barred from Safe Haven until lineup_confirmed == True
- ABS System awareness: Umpire Challenge Success rate check in Final Call phase

TIER CLASSIFICATION (v3 - ANCHOR-BASED):
For each player+stat combination:
- MAIN LINE (Standard): The non-alternate market line (the anchor)
- DEMON (Red): Any alternate line ABOVE the main line (harder to hit)
- GOBLIN (Green): Any alternate line BELOW the main line (easier to hit)
"""

import asyncio
import os
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Set
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from enum import Enum
import httpx

from services.config.collection_names import COLL
from services.observability import log_silent_failure

logger = logging.getLogger(__name__)

# =============================================================================
# ADAPTIVE SYNC INTERVALS (Seconds) - Market Stability Logic
# =============================================================================
# 2026-04-30 product invariant: the watcher MUST run 24/7. Sportsbooks
# typically release the next slate's props OVERNIGHT (in the
# game_registry-empty "Standby" window). Our edge depends on detecting
# those releases before the lines move, so STANDBY cannot be a low-
# priority interval. It's the WHOLE GAME for early-line discovery.
#
# Tier rationale:
#   STANDBY (no games within 8h)   = 5 min  — catch new prop releases ASAP
#   ACTIVE  (2-8h to tip)          = 60 min — markets stable, line moves slow
#   LOCK_IN (30m-2h to tip)        = 15 min — Lineup Gate phase, sharper moves
#   FINAL_CALL (<30m to tip)       = 10 min — Vegas sharp action
# Note STANDBY is now TIGHTER than ACTIVE on purpose: empty registry is
# the precise window where new lines appear; we want to detect them
# fast, then fall back to the calmer ACTIVE cadence once games are
# discovered and the market stabilizes.
#
# Earlier history: STANDBY was 14400 (4h). On 2026-04-30 a single
# silent task death during one of those 4h sleeps caused a 17h dead
# pipeline. Heartbeat doc + 5min STANDBY makes that failure mode
# impossible to hide for more than ~15 min (3× heartbeat interval).
class PollInterval(Enum):
    STANDBY = 300        # 5 minutes - 24/7 watcher for early prop releases
    ACTIVE = 3600        # 60 minutes (2-8h to tip) - Catching line moves
    LOCK_IN = 900        # 15 minutes (30m-2h to tip) - Lineup Gate phase
    FINAL_CALL = 600     # 10 minutes (< 30m to tip) - Sharp moves verification
    LIVE = None          # Stop polling - Game started

# Thresholds in hours
STANDBY_THRESHOLD = 8        # > 8 hours = Standby
ACTIVE_THRESHOLD = 2         # 2-8 hours = Active
LOCK_IN_THRESHOLD = 0.5      # 30m-2 hours = Lock-In (Lineup Gate)
FINAL_CALL_THRESHOLD = 0.5   # < 30 minutes = Final Call

# Stale data threshold
STALE_DATA_THRESHOLD_SECONDS = 600  # 10 minutes (aligned with Final Call)

# Sport keys for The Odds API
SPORT_KEYS = {
    "nba": "basketball_nba",
    "mlb": "baseball_mlb"
}


class GameStatus(Enum):
    STANDBY = "standby"
    ACTIVE = "active"
    LOCK_IN = "lock_in"
    FINAL_CALL = "final_call"
    LIVE = "live"


def _normalize_name(name: str) -> str:
    """
    Normalize player names for consistent MongoDB lookups.
    Strips periods, commas, suffixes (Jr, Sr, II, III, IV, V).
    """
    if not name:
        return ""
    normalized = name.lower().strip()
    normalized = normalized.replace(".", "").replace(",", "")
    suffix_pattern = r'\b(jr|sr|ii|iii|iv|v)\b'
    normalized = re.sub(suffix_pattern, '', normalized, flags=re.IGNORECASE)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized


class AdaptiveSyncEngine:
    """
    Mission-Critical Adaptive Sync Engine
    
    Manages polling frequency based on game start times to:
    1. Conserve API credits during quiet periods
    2. Maximize freshness during critical betting windows
    3. Track and display last_updated timestamps
    4. Detect and alert on stale intel
    """

    # ── Watchdog tunables (class-level so tests can override) ────────
    _WATCHDOG_INTERVAL_SECONDS: int = 30
    # Hard ceiling on staleness — even if `next_poll_in_seconds` is
    # short (e.g. STANDBY=300s), don't restart more aggressively than
    # every 10 min. Avoids restart storms during oddsAPI outages
    # where polls legitimately take longer than a tick.
    _WATCHDOG_STALE_FLOOR_SECONDS: int = 600
    _WATCHDOG_MAX_RESTARTS_IN_WINDOW: int = 5
    _WATCHDOG_RESTART_WINDOW_SECONDS: int = 1800  # 30 min
    # Don't tear down a brand-new engine while the very first
    # heartbeat is still being written (BDL game-log refresh on
    # cold start can take several minutes).
    _WATCHDOG_WARMUP_SECONDS: int = 600
    
    def __init__(self, db: AsyncIOMotorDatabase, odds_api_key: str):
        self.db = db
        self.odds_api_key = odds_api_key
        self.base_url = "https://api.the-odds-api.com/v4"
        
        # Track active games and their status
        self.game_registry: Dict[str, Dict[str, Any]] = {}
        self.active_polling_tasks: Dict[str, asyncio.Task] = {}
        
        # Background worker state
        self.is_running = False
        self.main_task: Optional[asyncio.Task] = None
        # Watchdog (2026-04-30): a separate task that detects a frozen
        # poll loop and forcibly restarts it. Without this, a silent
        # `asyncio.sleep` task death produces unbounded dead pipeline.
        self._watchdog_task: Optional[asyncio.Task] = None
        self._watchdog_restart_count: int = 0
        self._watchdog_first_restart_at: Optional[datetime] = None
        # Promoted to an instance attribute (instead of a local in the
        # watchdog loop) so that tests can backdate it to simulate a
        # long-running engine. Set by `start()` to "now" in production.
        self._engine_started_at: Optional[datetime] = None
        
        # Collections
        self.cached_board_collection = COLL("board_cache", "nba")
        # Wave 1 (Batch 6) shadow-writes: parallel handle that routes
        # `board_cache` operations through ShadowWriter. The string cache
        # above is preserved (unused externally today but retained for
        # logs/introspection). Only the 3 DB call-sites below now go
        # through `cached_board_handle` — readers delegate to primary,
        # the single writer fans out to both primary and shadow.
        self.cached_board_handle = COLL.handle(db, "board_cache", "nba")
        self.sync_status_collection = "dg_sync_status"
        self.game_schedule_collection = "dg_game_schedule"
        self.master_hub_collection = COLL("master_hub", "nba")
        
        # Player stats cache (refreshed each sync cycle)
        self._player_stats_cache: Dict[str, Dict] = {}
        
        # Reference to main sync function (set by server.py)
        self._sync_odds_callback = None
        
        logger.info("[ADAPTIVE_SYNC] Engine initialized")
    
    def _extract_stat_type(self, market_key: str) -> str:
        """Extract and normalize stat type from market key."""
        stat_type = market_key.replace("_alternate", "").replace("player_", "").upper()
        stat_type = stat_type.replace("POINTS_REBOUNDS_ASSISTS", "PRA")
        stat_type = stat_type.replace("POINTS_REBOUNDS", "P+R")
        stat_type = stat_type.replace("POINTS_ASSISTS", "P+A")
        stat_type = stat_type.replace("REBOUNDS_ASSISTS", "R+A")
        stat_type = stat_type.replace("THREES", "3PM")
        stat_type = stat_type.replace("BLOCKS", "BLK")
        stat_type = stat_type.replace("STEALS", "STL")
        stat_type = stat_type.replace("TURNOVERS", "TO")
        stat_type = stat_type.replace("POINTS", "PTS")
        stat_type = stat_type.replace("REBOUNDS", "REB")
        stat_type = stat_type.replace("ASSISTS", "AST")
        return stat_type
    
    def set_sync_callback(self, callback):
        """Set the callback to the proper sync_odds_to_mongo function."""
        self._sync_odds_callback = callback
        logger.info("[ADAPTIVE_SYNC] Sync callback registered")
    
    async def _get_player_season_avg(self, player_name: str, stat_type: str) -> Optional[float]:
        """
        Get player's season average for a stat from master hub.
        Uses normalized name matching and caching.
        
        Returns None if player not found or no data.
        """
        # Check cache first
        cache_key = f"{_normalize_name(player_name)}_{stat_type}"
        if cache_key in self._player_stats_cache:
            return self._player_stats_cache[cache_key]
        
        # Normalize stat type
        stat_key = stat_type.upper()
        norm_map = {"P+R": "PR", "P+A": "PA", "R+A": "RA", "3PM": "THREES"}
        stat_key = norm_map.get(stat_key, stat_key)
        
        # Try exact name match first
        player = await self.db[self.master_hub_collection].find_one(
            {"display_name": player_name},
            {"_id": 0, "baseline_stats": 1, "bdl_game_logs": 1, "game_logs": 1}
        )
        
        # Fallback: Try normalized name search
        if not player:
            normalized = _normalize_name(player_name)
            all_players = await self.db[self.master_hub_collection].find(
                {}, {"display_name": 1, "baseline_stats": 1, "bdl_game_logs": 1, "game_logs": 1, "_id": 0}
            ).to_list(1000)
            
            for p in all_players:
                if _normalize_name(p.get("display_name", "")) == normalized:
                    player = p
                    break
        
        if not player:
            self._player_stats_cache[cache_key] = None
            return None
        
        # PRIMARY: Get from baseline_stats
        baseline_stats = player.get("baseline_stats", {})
        stat_data = baseline_stats.get(stat_key) or baseline_stats.get(stat_type)
        
        if stat_data and stat_data.get("season_avg"):
            season_avg = float(stat_data.get("season_avg", 0))
            self._player_stats_cache[cache_key] = season_avg
            return season_avg
        
        # FALLBACK: Calculate from game_logs
        game_logs = player.get("game_logs", [])
        if not game_logs:
            self._player_stats_cache[cache_key] = None
            return None
        
        # Map stat type to game log field
        stat_map = {
            "PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl", "BLK": "blk",
            "3PM": "fg3m", "THREES": "fg3m", "TO": "tov",
            "PRA": None, "PR": None, "PA": None, "RA": None
        }
        
        log_key = stat_map.get(stat_key)
        values = []
        
        for g in game_logs:
            if stat_key in ["PRA"]:
                val = (g.get("pts", 0) or 0) + (g.get("reb", 0) or 0) + (g.get("ast", 0) or 0)
            elif stat_key in ["PR", "P+R"]:
                val = (g.get("pts", 0) or 0) + (g.get("reb", 0) or 0)
            elif stat_key in ["PA", "P+A"]:
                val = (g.get("pts", 0) or 0) + (g.get("ast", 0) or 0)
            elif stat_key in ["RA", "R+A"]:
                val = (g.get("reb", 0) or 0) + (g.get("ast", 0) or 0)
            elif log_key:
                val = g.get(log_key, 0) or 0
            else:
                val = 0
            values.append(val)
        
        if values:
            season_avg = sum(values) / len(values)
            self._player_stats_cache[cache_key] = season_avg
            return season_avg
        
        self._player_stats_cache[cache_key] = None
        return None
    
    async def _get_player_full_stats(self, player_name: str, stat_type: str, line: float) -> Dict[str, Any]:
        """
        Get player's full stats for a prop including L5/L10 averages and H10 hit rate.
        Uses BDL game logs for accurate calculations.
        
        Returns: {
            "season_avg": float,
            "l5_avg": float,
            "l10_avg": float,
            "h10_hit_rate": float (percentage),
            "h10_hits": int,
            "h10_games": int
        }
        """
        cache_key = f"{_normalize_name(player_name)}_{stat_type}_full"
        if cache_key in self._player_stats_cache:
            cached = self._player_stats_cache[cache_key]
            # Calculate H10 for specific line from cached game values
            if cached and "game_values" in cached:
                game_values = cached["game_values"][:10]
                hits = sum(1 for v in game_values if float(v) > float(line))
                return {
                    **cached,
                    "h10_hit_rate": round((hits / len(game_values)) * 100, 1) if game_values else 0,
                    "h10_hits": hits,
                    "h10_games": len(game_values)
                }
        
        # Default result
        result = {
            "season_avg": None, "l5_avg": None, "l10_avg": None,
            "h10_hit_rate": None, "h10_hits": 0, "h10_games": 0
        }
        
        # Normalize stat type
        stat_key = stat_type.upper()
        norm_map = {"P+R": "PR", "P+A": "PA", "R+A": "RA", "3PM": "THREES", "THREES": "THREES"}
        stat_key = norm_map.get(stat_key, stat_key)
        
        # Find player
        player = await self.db[self.master_hub_collection].find_one(
            {"display_name": player_name},
            {"_id": 0, "baseline_stats": 1, "bdl_game_logs": 1, "game_logs": 1}
        )
        
        if not player:
            normalized = _normalize_name(player_name)
            all_players = await self.db[self.master_hub_collection].find(
                {}, {"display_name": 1, "baseline_stats": 1, "bdl_game_logs": 1, "game_logs": 1, "_id": 0}
            ).to_list(1000)
            
            for p in all_players:
                if _normalize_name(p.get("display_name", "")) == normalized:
                    player = p
                    break
        
        if not player:
            return result
        
        # Get season_avg from baseline_stats (official BDL data)
        baseline_stats = player.get("baseline_stats", {})
        stat_data = baseline_stats.get(stat_key, {})
        if isinstance(stat_data, dict):
            result["season_avg"] = stat_data.get("season_avg")
            result["l5_avg"] = stat_data.get("l5_avg")
            result["l10_avg"] = stat_data.get("l10_avg")
        elif stat_data:
            result["season_avg"] = stat_data
        
        # Calculate from game logs for accurate L5/L10/H10
        # Use bdl_game_logs as PRIMARY source (correct field names: fg3m, turnover)
        # Fall back to game_logs only if bdl_game_logs is empty
        bdl_logs = player.get("bdl_game_logs", [])
        game_logs = player.get("game_logs", [])
        
        # Prefer bdl_game_logs - it has consistent field names from BDL API
        if len(bdl_logs) >= 5:
            logs_to_use = bdl_logs
            log_source = "bdl_game_logs"
            log_key_map = {
                "PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl",
                "BLK": "blk", "THREES": "fg3m", "3PM": "fg3m", "TO": "turnover"
            }
        elif len(game_logs) >= 5:
            logs_to_use = game_logs
            log_source = "game_logs"
            log_key_map = {
                "PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl",
                "BLK": "blk", "THREES": "tptfgm", "3PM": "tptfgm", "TO": "TOV"
            }
        else:
            logs_to_use = bdl_logs if bdl_logs else game_logs
            log_source = "bdl_game_logs" if bdl_logs else "game_logs"
            log_key_map = {
                "PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl",
                "BLK": "blk", "THREES": "fg3m" if bdl_logs else "tptfgm",
                "3PM": "fg3m" if bdl_logs else "tptfgm",
                "TO": "turnover" if bdl_logs else "TOV"
            }
        
        if logs_to_use:
            # Sort by date (most recent first)
            if log_source == "bdl_game_logs":
                logs_to_use = sorted(logs_to_use, key=lambda x: x.get('date', ''), reverse=True)
            else:
                logs_to_use = sorted(
                    logs_to_use,
                    key=lambda x: x.get('gameID', '')[:8] if x.get('gameID') else '',
                    reverse=True
                )
            
            log_key = log_key_map.get(stat_key)
            
            # Extract values from game logs
            game_values = []
            for g in logs_to_use:
                try:
                    if stat_key in ["PRA"]:
                        pts_val = g.get("pts", 0)
                        reb_val = g.get("reb", 0)
                        ast_val = g.get("ast", 0)
                        # Handle string values from game_logs
                        pts_val = float(pts_val) if pts_val else 0
                        reb_val = float(reb_val) if reb_val else 0
                        ast_val = float(ast_val) if ast_val else 0
                        val = pts_val + reb_val + ast_val
                    elif stat_key in ["PR", "P+R"]:
                        pts_val = float(g.get("pts", 0) or 0)
                        reb_val = float(g.get("reb", 0) or 0)
                        val = pts_val + reb_val
                    elif stat_key in ["PA", "P+A"]:
                        pts_val = float(g.get("pts", 0) or 0)
                        ast_val = float(g.get("ast", 0) or 0)
                        val = pts_val + ast_val
                    elif stat_key in ["RA", "R+A"]:
                        reb_val = float(g.get("reb", 0) or 0)
                        ast_val = float(g.get("ast", 0) or 0)
                        val = reb_val + ast_val
                    elif log_key:
                        raw_val = g.get(log_key, 0)
                        # Handle string values (game_logs stores as strings)
                        val = float(raw_val) if raw_val else 0
                    else:
                        val = 0
                    game_values.append(val)
                except (ValueError, TypeError):
                    game_values.append(0)
            
            if game_values:
                # Calculate L5 and L10 averages ONLY if not already present from baseline_stats
                l5_values = game_values[:5]
                l10_values = game_values[:10]
                
                # Only calculate from game logs if baseline_stats doesn't have it
                if l5_values and not result.get("l5_avg"):
                    result["l5_avg"] = round(sum(l5_values) / len(l5_values), 1)
                if l10_values and not result.get("l10_avg"):
                    result["l10_avg"] = round(sum(l10_values) / len(l10_values), 1)
                
                # Calculate H10 hit rate (> line, not >=)
                # Use l10_values from game logs OR baseline_stats l10_values if available
                values_for_hit_rate = l10_values
                baseline_l10_values = baseline_stats.get(stat_key, {}).get("l10_values", []) if baseline_stats else []
                if baseline_l10_values and len(baseline_l10_values) > len(l10_values):
                    values_for_hit_rate = baseline_l10_values
                
                if values_for_hit_rate:
                    hits = sum(1 for v in values_for_hit_rate if v > float(line))
                    result["h10_hit_rate"] = round((hits / len(values_for_hit_rate)) * 100, 1)
                    result["h10_hits"] = hits
                    result["h10_games"] = len(values_for_hit_rate)
                
                # Cache for reuse
                self._player_stats_cache[cache_key] = {
                    **result,
                    "game_values": game_values
                }
        
        return result
    
    def _get_game_status(self, commence_time: str) -> GameStatus:
        """
        Determine game status based on time to tip-off.
        
        Market Stability Logic (2026):
        - STANDBY (> 8h):     Early board scouting
        - ACTIVE (2-8h):      Catching initial line moves
        - LOCK_IN (30m-2h):   Lineup Gate phase
        - FINAL_CALL (< 30m): Sharp moves verification
        - LIVE (started):     Stop polling
        
        Args:
            commence_time: ISO format game start time
            
        Returns:
            GameStatus enum value
        """
        if not commence_time:
            return GameStatus.STANDBY
        
        try:
            game_time = datetime.fromisoformat(commence_time.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            time_to_tip = (game_time - now).total_seconds() / 3600  # Hours
            
            if time_to_tip < 0:
                return GameStatus.LIVE
            elif time_to_tip < FINAL_CALL_THRESHOLD:  # < 30 minutes
                return GameStatus.FINAL_CALL
            elif time_to_tip < ACTIVE_THRESHOLD:  # 30m - 2 hours
                return GameStatus.LOCK_IN
            elif time_to_tip < STANDBY_THRESHOLD:  # 2-8 hours
                return GameStatus.ACTIVE
            else:  # > 8 hours
                return GameStatus.STANDBY
                
        except Exception as e:
            logger.error(f"[ADAPTIVE_SYNC] Error parsing commence_time: {e}")
            return GameStatus.STANDBY
    
    def _get_poll_interval(self, status: GameStatus) -> Optional[int]:
        """Get polling interval in seconds based on game status."""
        intervals = {
            GameStatus.STANDBY: PollInterval.STANDBY.value,      # 4 hours
            GameStatus.ACTIVE: PollInterval.ACTIVE.value,        # 60 minutes
            GameStatus.LOCK_IN: PollInterval.LOCK_IN.value,      # 15 minutes
            GameStatus.FINAL_CALL: PollInterval.FINAL_CALL.value,  # 10 minutes
            GameStatus.LIVE: None                                 # Stop
        }
        return intervals.get(status)
    
    async def _fetch_live_odds(self, sport: str = "nba") -> List[Dict[str, Any]]:
        """
        Fetch odds from The Odds API for NBA or MLB.
        
        Multi-Sport Support (2026):
        - NBA: basketball_nba
        - MLB: baseball_mlb
        
        PRIZEPICKS-SPECIFIC FETCH:
        - Uses regions=us_dfs (Daily Fantasy Sports region)
        - Uses bookmakers=prizepicks (primary), underdog (fallback)
        - Fetches both standard and alternate markets for proper tier classification
        
        Classification:
        - STANDARD (Gray): Main market lines
        - GOBLIN (Green): Alternate lines BELOW player's average
        - DEMON (Red): Alternate lines ABOVE player's average
        
        Args:
            sport: "nba" or "mlb"
            
        Returns raw odds data for processing.
        """
        if not self.odds_api_key:
            logger.warning("[ADAPTIVE_SYNC] No Odds API key configured")
            return []
        
        # Sport key mapping
        sport_key = SPORT_KEYS.get(sport.lower(), "basketball_nba")
        sport_upper = sport.upper()
        
        # Define markets based on sport
        if sport.lower() == "mlb":
            # MLB-specific markets
            standard_markets = [
                "batter_hits", "batter_total_bases", "batter_rbis", "batter_runs",
                "batter_home_runs", "batter_stolen_bases", "batter_walks",
                "batter_strikeouts", "batter_hits_runs_rbis",
                "pitcher_strikeouts", "pitcher_outs", "pitcher_hits_allowed",
                "pitcher_walks", "pitcher_earned_runs"
            ]
        else:
            # NBA markets
            standard_markets = [
                "player_points", "player_rebounds", "player_assists",
                "player_threes", "player_blocks", "player_steals",
                "player_points_rebounds_assists", "player_points_rebounds",
                "player_points_assists", "player_rebounds_assists"
            ]
        
        # Add alternate markets for tier classification
        all_markets = standard_markets + [f"{m}_alternate" for m in standard_markets]
        markets_str = ",".join(all_markets)
        
        # Step 1: Fetch list of events
        events_url = f"{self.base_url}/sports/{sport_key}/events"
        events_params = {
            "apiKey": self.odds_api_key,
            "dateFormat": "iso"
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                events_response = await client.get(events_url, params=events_params)
                events_response.raise_for_status()
                events = events_response.json()
                logger.info(f"[ADAPTIVE_SYNC] Found {len(events)} {sport_upper} events")
                
                if not events:
                    return []
                
                enriched_events = []
                
                for event in events:
                    event_id = event.get("id")
                    if not event_id:
                        continue
                    
                    odds_url = f"{self.base_url}/sports/{sport_key}/events/{event_id}/odds"
                    
                    # === FETCH 1: PrizePicks (DFS region) ===
                    prizepicks_data = None
                    prizepicks_params = {
                        "apiKey": self.odds_api_key,
                        "regions": "us_dfs",
                        "bookmakers": "prizepicks",
                        "markets": markets_str,
                        "oddsFormat": "american",
                        "includeMultipliers": "true"
                    }
                    
                    try:
                        pp_response = await client.get(odds_url, params=prizepicks_params, timeout=15.0)
                        if pp_response.status_code == 200:
                            prizepicks_data = pp_response.json()
                    except Exception as e:
                        logger.warning(f"[SYNC] PrizePicks fetch failed for {event_id}: {e}")
                    
                    # === FETCH 2: Sharp Books - DraftKings + FanDuel (us region) ===
                    sharp_data = None
                    sharp_params = {
                        "apiKey": self.odds_api_key,
                        "regions": "us",
                        "bookmakers": "draftkings,fanduel",
                        "markets": markets_str,
                        "oddsFormat": "american",
                        "includeMultipliers": "true"
                    }
                    
                    try:
                        sharp_response = await client.get(odds_url, params=sharp_params, timeout=15.0)
                        if sharp_response.status_code == 200:
                            sharp_data = sharp_response.json()
                            # Log sharp book data
                            for bm in sharp_data.get("bookmakers", []):
                                bm_key = bm.get("key", "")
                                bm_markets = len(bm.get("markets", []))
                                if bm_markets > 0:
                                    logger.debug(f"  [SHARP:{bm_key.upper()}] {event.get('away_team')} @ {event.get('home_team')}: {bm_markets} markets")
                    except Exception as e:
                        logger.warning(f"[SYNC] Sharp books fetch failed for {event_id}: {e}")
                    
                    # === MERGE: Combine PrizePicks with Sharp Books ===
                    if prizepicks_data or sharp_data:
                        merged_event = {
                            "id": event_id,
                            "event_id": event_id,
                            "sport": sport.lower(),
                            "home_team": event.get("home_team"),
                            "away_team": event.get("away_team"),
                            "commence_time": event.get("commence_time"),
                            "bookmakers": []
                        }
                        
                        # Add PrizePicks bookmakers
                        if prizepicks_data:
                            merged_event["bookmakers"].extend(prizepicks_data.get("bookmakers", []))
                        
                        # Add Sharp bookmakers (DraftKings, FanDuel)
                        if sharp_data:
                            merged_event["bookmakers"].extend(sharp_data.get("bookmakers", []))
                        
                        enriched_events.append(merged_event)
                        
                        # Log combined data
                        pp_count = len(prizepicks_data.get("bookmakers", [])) if prizepicks_data else 0
                        sharp_count = len(sharp_data.get("bookmakers", [])) if sharp_data else 0
                        logger.debug(f"  [MERGED] {event.get('away_team')} @ {event.get('home_team')}: PP={pp_count}, Sharp={sharp_count}")
                
                logger.info(f"[ADAPTIVE_SYNC] Fetched {sport_upper} odds: {len(enriched_events)} events (PrizePicks + DK + FD)")
                return enriched_events
                
        except httpx.HTTPStatusError as e:
            logger.error(f"[ADAPTIVE_SYNC] {sport_upper} Odds API HTTP error: {e.response.status_code}")
            return []
        except Exception as e:
            logger.error(f"[ADAPTIVE_SYNC] {sport_upper} Odds API error: {e}")
            return []
    
    async def _update_game_registry(self, events: List[Dict[str, Any]]) -> None:
        """
        Update the game registry with current events and their statuses.
        Supports both NBA and MLB events with sport tracking.
        """
        current_game_ids = set()
        
        for event in events:
            game_id = event.get("id")
            if not game_id:
                continue
            
            current_game_ids.add(game_id)
            commence_time = event.get("commence_time", "")
            status = self._get_game_status(commence_time)
            sport = event.get("sport", "nba")  # Default to NBA for backwards compatibility
            
            self.game_registry[game_id] = {
                "id": game_id,
                "sport": sport,
                "home_team": event.get("home_team", ""),
                "away_team": event.get("away_team", ""),
                "commence_time": commence_time,
                "status": status.value,
                "poll_interval": self._get_poll_interval(status),
                "last_updated": datetime.now(timezone.utc).isoformat()
            }
        
        # Remove games that are no longer in the feed
        stale_games = set(self.game_registry.keys()) - current_game_ids
        for game_id in stale_games:
            if self.game_registry[game_id]["status"] == GameStatus.LIVE.value:
                del self.game_registry[game_id]
                logger.info(f"[ADAPTIVE_SYNC] Removed completed game: {game_id}")
    
    async def _update_cached_board(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Update the nba_cached_board collection with PrizePicks odds data.
        (Legacy name `dg_cached_board` was dropped 2026-04-30; this
        writer targets the canonical `nba_cached_board` via
        `COLL("board_cache", "nba")`.)
        
        TIER CLASSIFICATION v3 - ANCHOR-BASED:
        For each player + stat_type combination:
        
        1. MAIN LINE (Standard): The non-alternate market line = the ANCHOR
        2. DEMON (Red): Any alternate line ABOVE the main line (harder to hit)
        3. GOBLIN (Green): Any alternate line BELOW the main line (easier to hit)
        
        TWO-PASS APPROACH:
        - Pass 1: Collect all props and identify main lines (anchors)
        - Pass 2: Classify each prop based on comparison to anchor
        """
        if not events:
            return {"updated": 0, "errors": 0}
        
        now = datetime.now(timezone.utc)
        updated_count = 0
        error_count = 0
        goblin_count = 0
        demon_count = 0
        standard_count = 0
        
        # Clear player stats cache for fresh lookups this cycle
        self._player_stats_cache = {}
        
        # ============================================================
        # PASS 1: Collect all props and identify MAIN LINES (anchors)
        #         Also build sharp price lookup from DraftKings/FanDuel
        # ============================================================
        # Key: (player_name, stat_type) -> main_line value
        main_lines: Dict[tuple, float] = {}
        # Key: (player_name, stat_type, line, direction) -> {draftkings_price, fanduel_price}
        sharp_prices: Dict[tuple, Dict] = {}
        # Collect all props for Pass 2
        all_props: List[Dict] = []
        
        for event in events:
            game_id = event.get("id")
            commence_time = event.get("commence_time", "")
            home_team = event.get("home_team", "")
            away_team = event.get("away_team", "")
            
            bookmakers = event.get("bookmakers", [])
            
            # === FIRST: Extract sharp prices from DraftKings and FanDuel ===
            for bookmaker in bookmakers:
                bookmaker_key = bookmaker.get("key", "")
                
                if bookmaker_key not in ["draftkings", "fanduel"]:
                    continue
                
                markets = bookmaker.get("markets", [])
                
                for market in markets:
                    market_key = market.get("key", "")
                    outcomes = market.get("outcomes", [])
                    is_alternate_market = "_alternate" in market_key
                    
                    # Extract base stat type
                    stat_type_extracted = self._extract_stat_type(market_key)
                    
                    for outcome in outcomes:
                        player_name = outcome.get("description", "")
                        if not player_name:
                            continue
                        
                        price = outcome.get("price", 0)
                        line = outcome.get("point", 0)
                        direction = (outcome.get("name", "") or "over").lower()
                        
                        # Build lookup key
                        lookup_key = (player_name, stat_type_extracted, line, direction)
                        
                        if lookup_key not in sharp_prices:
                            sharp_prices[lookup_key] = {"draftkings_price": None, "fanduel_price": None}
                        
                        if bookmaker_key == "draftkings":
                            sharp_prices[lookup_key]["draftkings_price"] = price
                        elif bookmaker_key == "fanduel":
                            sharp_prices[lookup_key]["fanduel_price"] = price
            
            # === SECOND: Process PrizePicks props ===
            for bookmaker in bookmakers:
                bookmaker_key = bookmaker.get("key", "")
                
                if bookmaker_key != "prizepicks":
                    continue
                    
                markets = bookmaker.get("markets", [])
                
                for market in markets:
                    market_key = market.get("key", "")
                    outcomes = market.get("outcomes", [])
                    
                    # Detect alternate market
                    is_alternate_market = "_alternate" in market_key
                    
                    # Extract base stat type
                    stat_type_extracted = self._extract_stat_type(market_key)
                    
                    for outcome in outcomes:
                        player_name = outcome.get("description", "")
                        if not player_name:
                            continue
                        
                        price = outcome.get("price", 0)
                        line = outcome.get("point", 0)
                        direction = (outcome.get("name", "") or "over").lower()
                        multiplier = outcome.get("multiplier")
                        
                        # Look up sharp prices for this prop
                        lookup_key = (player_name, stat_type_extracted, line, direction)
                        sharp_data = sharp_prices.get(lookup_key, {})
                        draftkings_price = sharp_data.get("draftkings_price")
                        fanduel_price = sharp_data.get("fanduel_price")
                        
                        # Determine the sharp_price (DraftKings first, then FanDuel fallback)
                        sharp_price = draftkings_price if draftkings_price is not None else fanduel_price
                        sharp_source = "draftkings" if draftkings_price is not None else ("fanduel" if fanduel_price is not None else None)
                        
                        # Store prop data for Pass 2
                        prop_data = {
                            "game_id": game_id,
                            "commence_time": commence_time,
                            "home_team": home_team,
                            "away_team": away_team,
                            "bookmaker": bookmaker_key,
                            "market": market_key,
                            "player_name": player_name,
                            "line": line,
                            "price": price,
                            "multiplier": multiplier,
                            "direction": direction,
                            "name": outcome.get("name", ""),
                            "is_alternate_market": is_alternate_market,
                            "stat_type_extracted": stat_type_extracted,
                            # Sharp book prices
                            "draftkings_price": draftkings_price,
                            "fanduel_price": fanduel_price,
                            "sharp_price": sharp_price,
                            "sharp_source": sharp_source
                        }
                        all_props.append(prop_data)
                        
                        # If this is NOT an alternate market, it's the MAIN LINE (anchor)
                        if not is_alternate_market:
                            key = (player_name, stat_type_extracted)
                            main_lines[key] = line
                            logger.debug(f"[ANCHOR] {player_name} {stat_type_extracted}: main_line = {line}")
        
        logger.info(f"[SYNC_V3] Pass 1 complete: {len(main_lines)} anchors, {len(sharp_prices)} sharp prices")
        
        # ============================================================
        # PASS 2: Classify each prop based on comparison to anchor
        # FALLBACK: If no main line, use L5 average as anchor
        # ============================================================
        l5_fallback_count = 0
        no_anchor_count = 0
        
        for prop in all_props:
            player_name = prop["player_name"]
            stat_type_extracted = prop["stat_type_extracted"]
            line = prop["line"]
            is_alternate_market = prop["is_alternate_market"]
            
            # Get the main line (anchor) for this player+stat
            key = (player_name, stat_type_extracted)
            main_line = main_lines.get(key)
            
            # Get player stats for enrichment (needed for L5 fallback)
            full_stats = await self._get_player_full_stats(player_name, stat_type_extracted, line)
            season_avg = full_stats.get("season_avg")
            l5_avg = full_stats.get("l5_avg")
            l10_avg = full_stats.get("l10_avg")
            h10_hit_rate = full_stats.get("h10_hit_rate")
            h10_hits = full_stats.get("h10_hits", 0)
            h10_games = full_stats.get("h10_games", 0)
            
            # Determine anchor and source
            anchor_line = None
            anchor_source = "none"
            
            if main_line is not None:
                anchor_line = main_line
                anchor_source = "main_line"
            elif l5_avg is not None and l5_avg > 0:
                # FALLBACK: Use L5 average as anchor
                anchor_line = l5_avg
                anchor_source = "l5_avg"
                l5_fallback_count += 1
            elif season_avg is not None and season_avg > 0:
                # Secondary fallback: Use season average
                anchor_line = season_avg
                anchor_source = "season_avg"
                l5_fallback_count += 1
            else:
                no_anchor_count += 1
            
            # Classification logic
            is_demon = False
            is_goblin = False
            tier_style = "standard"
            tier_label = "STANDARD"
            
            if anchor_line is not None:
                if not is_alternate_market and anchor_source == "main_line":
                    # This IS the main line = STANDARD
                    tier_style = "standard"
                    tier_label = "STANDARD"
                    standard_count += 1
                elif line > anchor_line:
                    # Line ABOVE anchor = DEMON (harder to hit)
                    is_demon = True
                    tier_style = "red"
                    tier_label = "DEMON"
                    demon_count += 1
                elif line < anchor_line:
                    # Line BELOW anchor = GOBLIN (easier to hit)
                    is_goblin = True
                    tier_style = "green"
                    tier_label = "GOBLIN"
                    goblin_count += 1
                else:
                    # Line EQUALS anchor = STANDARD
                    tier_style = "standard"
                    tier_label = "STANDARD"
                    standard_count += 1
            else:
                # No anchor available - default to STANDARD (unclassified)
                tier_style = "standard"
                tier_label = "STANDARD"
                standard_count += 1
            
            # Calculate diff from anchor
            diff_from_anchor = None
            if anchor_line and anchor_line > 0:
                diff_from_anchor = round(line - anchor_line, 1)
            
            try:
                # Build the update document
                update_doc = {
                    "game_id": prop["game_id"],
                    "commence_time": prop["commence_time"],
                    "home_team": prop["home_team"],
                    "away_team": prop["away_team"],
                    "bookmaker": prop["bookmaker"],
                    "market": prop["market"],
                    "player_name": player_name,
                    "line": line,
                    "price": prop["price"],
                    "multiplier": prop.get("multiplier"),
                    "direction": prop["direction"],
                    "name": prop["name"],
                    "last_updated": now,
                    "last_updated_iso": now.isoformat(),
                    "sync_source": "adaptive_sync_v4",
                    # Market metadata
                    "is_alternate_market": is_alternate_market,
                    # ANCHOR-BASED tier classification
                    "is_demon": is_demon,
                    "is_goblin": is_goblin,
                    "tier_style": tier_style,
                    "tier_label": tier_label,
                    "stat_type_extracted": stat_type_extracted,
                    # Anchor reference (main line or L5/season fallback)
                    "anchor_line": anchor_line,
                    "anchor_source": anchor_source,
                    "diff_from_anchor": diff_from_anchor,
                    # Sharp book prices (DraftKings primary, FanDuel fallback)
                    "draftkings_price": prop.get("draftkings_price"),
                    "fanduel_price": prop.get("fanduel_price"),
                    "sharp_price": prop.get("sharp_price"),
                    "sharp_source": prop.get("sharp_source"),
                    # Stats for display
                    "season_avg": round(season_avg, 1) if season_avg else None,
                    "l5_avg": l5_avg,
                    "l10_avg": l10_avg,
                    "h10_hit_rate": h10_hit_rate,
                    "h10_hits": h10_hits,
                    "h10_games": h10_games,
                    "has_stats": season_avg is not None and season_avg > 0
                }
                
                # Upsert to collection
                await self.cached_board_handle.update_one(
                    {
                        "game_id": prop["game_id"],
                        "player_name": player_name,
                        "market": prop["market"],
                        "line": line,
                        "direction": prop["direction"],
                        "bookmaker": prop["bookmaker"]
                    },
                    {"$set": update_doc},
                    upsert=True
                )
                updated_count += 1
                
            except Exception as e:
                error_count += 1
                logger.error(f"[UNDERDOG_SYNC] Error updating {player_name}: {e}")
        
        # Log classification distribution
        if updated_count > 0:
            logger.info(f"[UNDERDOG_SYNC_V3] Anchor-Based Classification: "
                       f"{demon_count} Demon (above anchor), "
                       f"{goblin_count} Goblin (below anchor), "
                       f"{standard_count} Standard")
            if l5_fallback_count > 0:
                logger.info(f"[UNDERDOG_SYNC_V3] L5/Season fallback used for {l5_fallback_count} player/stat combos (no main line)")
            if no_anchor_count > 0:
                logger.warning(f"[UNDERDOG_SYNC_V3] {no_anchor_count} player/stat combos had NO anchor (no main line, no stats)")
        
        # Update sync status
        await self._update_sync_status(now, updated_count, error_count)
        
        return {"updated": updated_count, "errors": error_count, "timestamp": now.isoformat()}
    
    async def _update_sync_status(self, timestamp: datetime, updated: int, errors: int) -> None:
        """
        Update the sync status document for frontend consumption.
        """
        await self.db[self.sync_status_collection].update_one(
            {"_id": "adaptive_sync"},
            {
                "$set": {
                    "last_sync": timestamp,
                    "last_sync_iso": timestamp.isoformat(),
                    "records_updated": updated,
                    "errors": errors,
                    "next_sync_estimate": (timestamp + timedelta(seconds=60)).isoformat(),
                    "engine_status": "running" if self.is_running else "stopped",
                    "active_games": len([g for g in self.game_registry.values() if g["status"] != "post_tip"]),
                    "mission_critical_games": len([g for g in self.game_registry.values() if g["status"] == "mission_critical"]),
                    "game_registry": list(self.game_registry.values())
                }
            },
            upsert=True
        )
    
    async def get_sync_status(self) -> Dict[str, Any]:
        """
        Get current sync status for API endpoint.
        """
        status = await self.db[self.sync_status_collection].find_one({"_id": "adaptive_sync"})
        if status:
            status.pop("_id", None)
            
            # Calculate time since last sync
            if status.get("last_sync"):
                last_sync = status["last_sync"]
                if isinstance(last_sync, datetime):
                    # Make sure both datetimes are timezone-aware
                    if last_sync.tzinfo is None:
                        last_sync = last_sync.replace(tzinfo=timezone.utc)
                    now = datetime.now(timezone.utc)
                    seconds_ago = (now - last_sync).total_seconds()
                    status["seconds_since_sync"] = int(seconds_ago)
                    status["sync_age_display"] = self._format_time_ago(seconds_ago)
        
        return status or {}
    
    def _format_time_ago(self, seconds: float) -> str:
        """Format seconds into human-readable time ago string."""
        if seconds < 60:
            return f"{int(seconds)}s ago"
        elif seconds < 3600:
            return f"{int(seconds / 60)}m ago"
        else:
            return f"{int(seconds / 3600)}h ago"
    
    async def check_stale_intel(self, game_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Check for stale intel during mission-critical windows.
        Returns stale entries that need immediate refresh.
        """
        stale_entries = []
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(seconds=STALE_DATA_THRESHOLD_SECONDS)
        
        # Find mission-critical games
        critical_games = [
            g for g in self.game_registry.values() 
            if g["status"] == "mission_critical"
        ]
        
        if game_id:
            critical_games = [g for g in critical_games if g["id"] == game_id]
        
        for game in critical_games:
            # Check if any data for this game is stale
            stale_count = await self.cached_board_handle.count_documents({
                "game_id": game["id"],
                "last_updated": {"$lt": threshold}
            })
            
            if stale_count > 0:
                stale_entries.append({
                    "game_id": game["id"],
                    "home_team": game["home_team"],
                    "away_team": game["away_team"],
                    "stale_records": stale_count,
                    "status": "STALE_INTEL",
                    "threshold_seconds": STALE_DATA_THRESHOLD_SECONDS
                })
        
        return {
            "has_stale_intel": len(stale_entries) > 0,
            "stale_games": stale_entries,
            "checked_at": now.isoformat()
        }
    
    async def trigger_priority_refresh(self, game_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Trigger an immediate high-priority refresh.
        Used when stale intel is detected during mission-critical windows.
        """
        logger.info(f"[ADAPTIVE_SYNC] PRIORITY REFRESH triggered for game: {game_id or 'ALL'}")
        
        events = await self._fetch_live_odds()
        
        if game_id:
            events = [e for e in events if e.get("id") == game_id]
        
        result = await self._update_cached_board(events)
        result["trigger"] = "priority_refresh"
        result["game_id"] = game_id
        
        return result
    
    async def _adaptive_poll_loop(self) -> None:
        """
        Main adaptive polling loop.
        Adjusts polling frequency based on game proximity.
        
        Vision Intel Enrichment is now triggered automatically by the Board Builder
        immediately after tier boards are created (Board-Driven approach).
        """
        logger.info("[ADAPTIVE_SYNC] Starting adaptive poll loop")
        
        # Track last sync times
        last_injury_sync = None
        last_ticker_sync = None
        last_mlb_lineup_check = None
        
        # Sync intervals
        INJURY_SYNC_INTERVAL = 1800      # 30 minutes
        TICKER_SYNC_INTERVAL = 1800      # 30 minutes
        MLB_LINEUP_CHECK_INTERVAL = 900  # 15 minutes - MLB Lineup Gate check

        # 2026-05-07 P0-A FIX — BDL game-logs refreshes (NBA + MLB) used
        # to be inlined here on a 4-hour cadence. Each one paginates
        # hundreds of api.balldontlie.io calls and routinely takes
        # >900s, which exceeds the watchdog stale_threshold (3 ×
        # next_poll_in_seconds, floor 600s = 900s). That blocked the
        # heartbeat write at the bottom of this loop, triggered watchdog
        # restarts, which canceled the in-flight BDL sync, which started
        # over from scratch on the next poll, which froze again — six
        # restarts in 30 min → RESTART_STORM_DETECTED → engine permanently
        # paused. The same refreshes are already scheduled as standalone
        # APScheduler daily cron jobs in server.py (`bdl_game_logs_sync`
        # at 04:15 EST, `mlb_bdl_game_logs_sync` at 04:18 EST), so the
        # inline copies were redundant duplicates whose only effect was
        # to break the engine. Keeping them here would also fight the
        # SSOT lock acquired around _sync_odds_callback below.

        while self.is_running:
            try:
                now = datetime.now(timezone.utc)

                # =================================================================
                # MAIN SYNC: NBA + MLB via callback or legacy
                # =================================================================
                if self._sync_odds_callback:
                    logger.info("[ADAPTIVE_SYNC] Triggering multi-sport sync...")
                    sync_result = await self._sync_odds_callback()
                    logger.info(f"[ADAPTIVE_SYNC] Sync complete: {sync_result.get('demons_count', 0)} demons, {sync_result.get('goblins_count', 0)} goblins")
                else:
                    # Legacy fallback - fetch both sports
                    logger.warning("[ADAPTIVE_SYNC] No sync callback - using legacy method")
                    nba_events = await self._fetch_live_odds(sport="nba")
                    mlb_events = await self._fetch_live_odds(sport="mlb")
                    all_events = nba_events + mlb_events
                    await self._update_game_registry(all_events)
                    await self._update_cached_board(all_events)
                
                # =================================================================
                # MLB LINEUP GATE CHECK (Every 15 min during Lock-In phase)
                # Props barred from Safe Haven until lineup_confirmed == True
                # =================================================================
                mlb_lock_in_games = [g for g in self.game_registry.values() 
                                     if g.get("sport") == "mlb" and g["status"] == "lock_in"]
                
                if mlb_lock_in_games and (last_mlb_lineup_check is None or 
                    (now - last_mlb_lineup_check).total_seconds() >= MLB_LINEUP_CHECK_INTERVAL):
                    try:
                        await self._check_mlb_lineups()
                        last_mlb_lineup_check = now
                    except Exception as e:
                        logger.error(f"[LINEUP_GATE] MLB lineup check failed: {e}")
                
                # =================================================================
                # INJURY SYNC (Every 30 min)
                # =================================================================
                if last_injury_sync is None or (now - last_injury_sync).total_seconds() >= INJURY_SYNC_INTERVAL:
                    await self._sync_injuries()
                    last_injury_sync = now
                
                # =================================================================
                # TICKER SYNC (Every 30 min)
                # =================================================================
                if last_ticker_sync is None or (now - last_ticker_sync).total_seconds() >= TICKER_SYNC_INTERVAL:
                    await self._sync_ticker()
                    last_ticker_sync = now
                
                # =================================================================
                # BOARD INTELLIGENCE ENRICHMENT — DELETED 2026-04-22
                # The legacy board_intelligence_service wrote vision/intel
                # into nba_cached_board (previously aliased
                # `dg_cached_board`; the alias collection was dropped
                # 2026-04-30). The canonical scoring path
                # (recompute_sport → {sport}_prop_scores) is the only
                # board source now.
                # =================================================================
                
                # =================================================================
                # DETERMINE NEXT POLL INTERVAL (Market Stability Logic)
                # =================================================================
                min_interval = PollInterval.STANDBY.value  # Default: 4 hours
                
                for game in self.game_registry.values():
                    status = game["status"]
                    if status == "final_call":
                        min_interval = min(min_interval, PollInterval.FINAL_CALL.value)  # 10 min
                    elif status == "lock_in":
                        min_interval = min(min_interval, PollInterval.LOCK_IN.value)      # 15 min
                    elif status == "active":
                        min_interval = min(min_interval, PollInterval.ACTIVE.value)       # 60 min
                
                # Check for stale intel
                stale_check = await self.check_stale_intel()
                if stale_check["has_stale_intel"]:
                    logger.warning(f"[ADAPTIVE_SYNC] STALE INTEL: {len(stale_check['stale_games'])} games")
                    min_interval = 60  # 1 minute emergency refresh
                
                # Log status breakdown
                status_counts = {}
                for g in self.game_registry.values():
                    s = g["status"]
                    status_counts[s] = status_counts.get(s, 0) + 1
                
                logger.info(f"[ADAPTIVE_SYNC] Poll complete. Next: {min_interval}s. "
                           f"Games: {len(self.game_registry)} | "
                           f"Status: {status_counts}")

                # ── Heartbeat (2026-04-30) ────────────────────────────
                # Persist a document on every poll so the freeze that
                # killed us overnight (4h sleep + silent task death =
                # 17h dead pipeline) is detectable from outside the
                # process. `/api/health/adaptive-sync` (or any external
                # monitor) can alert when `last_heartbeat_at` is older
                # than ~3× the configured `next_poll_in_seconds`.
                try:
                    await self.db["adaptive_sync_heartbeat"].update_one(
                        {"_id": "adaptive_sync"},
                        {"$set": {
                            "last_heartbeat_at": datetime.now(timezone.utc),
                            "next_poll_in_seconds": int(min_interval),
                            "games_in_registry": len(self.game_registry),
                            "status_breakdown": status_counts,
                        }},
                        upsert=True,
                    )
                except Exception as _hb_err:
                    logger.warning(
                        "[ADAPTIVE_SYNC] heartbeat write failed: %s", _hb_err
                    )

                # Wait for next poll
                await asyncio.sleep(min_interval)
                
            except asyncio.CancelledError:
                logger.info("[ADAPTIVE_SYNC] Poll loop cancelled")
                break
            except Exception as e:
                logger.error(f"[ADAPTIVE_SYNC] Poll loop error: {e}")
                await asyncio.sleep(60)  # Wait 1 minute on error
    
    async def _sync_injuries(self) -> None:
        """Sync injury reports from BDL API."""
        try:
            from services.bdl_enhanced_data import get_bdl_enhanced_service
            
            bdl_service = get_bdl_enhanced_service(self.db)
            result = await bdl_service.sync_injuries()
            
            if result.get('success'):
                logger.info(f"[ADAPTIVE_SYNC] Injury sync: {result.get('injuries_count', 0)} injuries updated")
            else:
                logger.warning(f"[ADAPTIVE_SYNC] Injury sync failed: {result.get('error', 'Unknown')}")
        except Exception as e:
            logger.error(f"[ADAPTIVE_SYNC] Injury sync error: {e}")
    
    async def _sync_ticker(self) -> None:
        """Sync ticker data (games and news)."""
        try:
            from routes.live import sync_todays_games, sync_news_headlines
            
            games_result = await sync_todays_games()
            news_result = await sync_news_headlines()
            
            logger.info(f"[ADAPTIVE_SYNC] Ticker sync: {games_result.get('games_count', 0)} games, "
                       f"{news_result.get('headlines_count', 0)} headlines")
        except Exception as e:
            logger.error(f"[ADAPTIVE_SYNC] Ticker sync error: {e}")
    
    async def _check_mlb_lineups(self) -> None:
        """MLB LINEUP GATE — DELETED 2026-04-22.

        The legacy implementation wrote `lineup_gate_passed` into
        `mlb_ferrari_safe_haven` (a deleted collection). The canonical
        path never consumed that field; the lineup-confirmation signal
        now lives in the MLB scoring adapter via `lineup_confirmed` on
        `mlb_master_hub_2026`. This stub is retained only so existing
        callers don't error; it is a no-op."""
        return None
    
    async def _watchdog_loop(self) -> None:
        """Watchdog: detects a frozen poll loop and restarts it.

        On 2026-04-30 a silent asyncio task death during
        `asyncio.sleep(14400)` produced 17h of dead pipeline. The
        heartbeat doc + 5-min STANDBY made future freezes detectable
        within ~15 min, but only an external monitor would notice.
        This watchdog closes the loop:

          * Every 30s, read `adaptive_sync_heartbeat.last_heartbeat_at`.
          * If it's older than `max(3 × expected_interval, 600s)`,
            consider the poll task DEAD.
          * Cancel `self.main_task`. Spawn a fresh `_adaptive_poll_loop`.
          * Track restart count; if we restart > 5 times within 30
            minutes, emit a HIGH-SEVERITY observability event
            (engine is restarting in a tight loop — code bug or
            persistent upstream failure, not a transient hiccup).

        Watchdog itself NEVER dies on exception — every iteration is
        wrapped in try/except. On failure it logs and continues; the
        only way to stop it is `stop()` calling `cancel()`.

        Constants are class attributes (`_WATCHDOG_*`) so tests can
        override them without monkey-patching the function locals.
        """
        WATCHDOG_INTERVAL_SECONDS = self._WATCHDOG_INTERVAL_SECONDS
        STALE_FLOOR_SECONDS = self._WATCHDOG_STALE_FLOOR_SECONDS
        MAX_RESTARTS_IN_WINDOW = self._WATCHDOG_MAX_RESTARTS_IN_WINDOW
        RESTART_WINDOW_SECONDS = self._WATCHDOG_RESTART_WINDOW_SECONDS
        WARMUP_SECONDS = self._WATCHDOG_WARMUP_SECONDS

        engine_started_at = (
            self._engine_started_at or datetime.now(timezone.utc)
        )
        logger.info(
            "[WATCHDOG] starting (interval=%ds, stale_floor=%ds, "
            "max_restarts=%d/%dmin, warmup=%ds)",
            WATCHDOG_INTERVAL_SECONDS, STALE_FLOOR_SECONDS,
            MAX_RESTARTS_IN_WINDOW, RESTART_WINDOW_SECONDS // 60,
            WARMUP_SECONDS,
        )

        while self.is_running:
            try:
                await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)
                if not self.is_running:
                    break

                now = datetime.now(timezone.utc)
                # Skip checks during warmup window.
                if (now - engine_started_at).total_seconds() < WARMUP_SECONDS:
                    continue

                hb = await self.db["adaptive_sync_heartbeat"].find_one(
                    {"_id": "adaptive_sync"}
                )
                if hb is None:
                    # No heartbeat ever written. After warmup, that
                    # itself is suspicious — the poll loop should
                    # have written one by now.
                    logger.warning(
                        "[WATCHDOG] no heartbeat doc found post-warmup. "
                        "Poll loop may be stuck before first heartbeat."
                    )
                    await self._restart_poll_loop("no_heartbeat_post_warmup")
                    continue

                last = hb.get("last_heartbeat_at")
                if not isinstance(last, datetime):
                    continue
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)

                # The "alive timestamp" is whichever is more recent:
                #   * the last heartbeat (if from this engine generation)
                #   * the engine start time (gives the poll loop the
                #     full stale_threshold to write its first heartbeat
                #     before being considered frozen)
                #
                # This handles two failure modes correctly:
                #   1. Healthy engine starting up with a previous-run
                #      heartbeat still in the DB → engine_started_at
                #      wins, no spurious restart.
                #   2. Engine ran fine, then poll loop died silently →
                #      `last` is recent but stops advancing; eventually
                #      `now - last > threshold` and a real restart
                #      fires.
                #
                # Bug discovered by integration test
                # `test_live_no_restart_when_pipeline_healthy`
                # on 2026-04-30.
                alive_ts = max(last, engine_started_at)
                age_s = (now - alive_ts).total_seconds()

                expected_interval = int(hb.get("next_poll_in_seconds") or 300)
                stale_threshold = max(3 * expected_interval, STALE_FLOOR_SECONDS)

                if age_s > stale_threshold:
                    # Restart-loop guard: count restarts in window.
                    if self._watchdog_first_restart_at is None or (
                        now - self._watchdog_first_restart_at
                    ).total_seconds() > RESTART_WINDOW_SECONDS:
                        # Reset the counter (window expired or first restart).
                        self._watchdog_first_restart_at = now
                        self._watchdog_restart_count = 0

                    self._watchdog_restart_count += 1

                    if self._watchdog_restart_count > MAX_RESTARTS_IN_WINDOW:
                        logger.critical(
                            "[WATCHDOG] RESTART_STORM_DETECTED — "
                            "%d restarts in %dmin, NOT restarting again. "
                            "Engine likely has a persistent code bug or "
                            "upstream outage; manual investigation required.",
                            self._watchdog_restart_count,
                            RESTART_WINDOW_SECONDS // 60,
                        )
                        # Record once via the structured logger so the
                        # admin /api/v3/admin/errors/summary endpoint
                        # surfaces it.
                        try:
                            log_silent_failure(
                                "adaptive_sync_engine.watchdog.restart_storm",
                                RuntimeError(
                                    f"watchdog restart storm: "
                                    f"{self._watchdog_restart_count} "
                                    f"restarts in "
                                    f"{RESTART_WINDOW_SECONDS // 60}min"
                                ),
                            )
                        except Exception:  # noqa: BLE001 — observability
                            pass
                        # Sleep through the rest of the window to avoid
                        # log-spamming this critical line.
                        await asyncio.sleep(RESTART_WINDOW_SECONDS)
                        # Reset for next window.
                        self._watchdog_restart_count = 0
                        self._watchdog_first_restart_at = None
                        continue

                    logger.critical(
                        "[WATCHDOG] POLL_LOOP_FROZEN — heartbeat is %.0fs "
                        "old (threshold=%ds, expected_interval=%ds). "
                        "Restart #%d in current window.",
                        age_s, stale_threshold, expected_interval,
                        self._watchdog_restart_count,
                    )
                    await self._restart_poll_loop(
                        f"frozen_heartbeat_{int(age_s)}s",
                    )
            except asyncio.CancelledError:
                logger.info("[WATCHDOG] cancelled")
                raise
            except Exception as e:  # noqa: BLE001 — watchdog must never die
                logger.error("[WATCHDOG] iteration error: %s", e)
                # Continue — watchdog must keep running even on transient
                # DB errors. Sleep an extra interval to avoid hot-looping.
                try:
                    await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)
                except asyncio.CancelledError:
                    raise

    async def _restart_poll_loop(self, reason: str) -> None:
        """Cancel the (presumed-dead) poll task and spawn a new one.

        Called by the watchdog. NEVER call directly elsewhere — would
        race with the watchdog's restart-loop guard.
        """
        try:
            log_silent_failure(
                "adaptive_sync_engine.watchdog.restart",
                RuntimeError(f"watchdog restart: {reason}"),
            )
        except Exception:  # noqa: BLE001
            pass

        old = self.main_task
        if old is not None and not old.done():
            old.cancel()
            try:
                await asyncio.wait_for(old, timeout=10)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[WATCHDOG] old task raised on cancel: %s", e
                )
        self.main_task = asyncio.create_task(self._adaptive_poll_loop())
        logger.warning(
            "[WATCHDOG] poll loop restarted (reason=%s, restart_count=%d)",
            reason, self._watchdog_restart_count,
        )

    async def start(self) -> None:
        """Start the adaptive sync engine."""
        if self.is_running:
            logger.warning("[ADAPTIVE_SYNC] Engine already running")
            return
        
        self.is_running = True
        # Stamp engine start so the watchdog can correctly distinguish
        # heartbeats from this engine generation vs prior ones. Tests
        # may pre-set this attribute to simulate a long-running engine
        # — we honor that and only default if unset.
        if self._engine_started_at is None:
            self._engine_started_at = datetime.now(timezone.utc)
        self.main_task = asyncio.create_task(self._adaptive_poll_loop())
        # Spawn the watchdog AFTER the poll task so the poll task is
        # the only one writing heartbeats.
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        logger.info("[ADAPTIVE_SYNC] Engine started (with watchdog)")
    
    async def stop(self) -> None:
        """Stop the adaptive sync engine."""
        self.is_running = False
        
        # Cancel watchdog first so it doesn't try to restart the
        # poll task while we're tearing it down.
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._watchdog_task = None

        if self.main_task:
            self.main_task.cancel()
            try:
                await self.main_task
            except asyncio.CancelledError as _swept_exc:
                log_silent_failure("services.engines.adaptive_sync_engine.stop", _swept_exc)  # sweep-auto-converted
        
        # Update status
        await self.db[self.sync_status_collection].update_one(
            {"_id": "adaptive_sync"},
            {"$set": {"engine_status": "stopped", "stopped_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True
        )
        
        logger.info("[ADAPTIVE_SYNC] Engine stopped")
    
    async def get_board_with_freshness(self, limit: int = 100) -> Dict[str, Any]:
        """
        Get cached board data with freshness indicators.
        Used by frontend to display last_updated timestamps.
        """
        now = datetime.now(timezone.utc)
        
        # Get board entries
        cursor = self.cached_board_handle.find(
            {},
            {"_id": 0}
        ).sort("last_updated", -1).limit(limit)
        
        entries = await cursor.to_list(length=limit)
        
        # Add freshness indicators
        for entry in entries:
            last_updated = entry.get("last_updated")
            if last_updated:
                if isinstance(last_updated, datetime):
                    seconds_ago = (now - last_updated).total_seconds()
                    entry["freshness"] = {
                        "seconds_ago": int(seconds_ago),
                        "display": self._format_time_ago(seconds_ago),
                        "is_stale": seconds_ago > STALE_DATA_THRESHOLD_SECONDS
                    }
        
        return {
            "entries": entries,
            "count": len(entries),
            "retrieved_at": now.isoformat()
        }


# Singleton instance
_adaptive_sync_engine: Optional[AdaptiveSyncEngine] = None


def get_adaptive_sync_engine() -> Optional[AdaptiveSyncEngine]:
    """Get the singleton adaptive sync engine instance."""
    return _adaptive_sync_engine


def init_adaptive_sync_engine(db: AsyncIOMotorDatabase, odds_api_key: str) -> AdaptiveSyncEngine:
    """Initialize the adaptive sync engine singleton."""
    global _adaptive_sync_engine
    _adaptive_sync_engine = AdaptiveSyncEngine(db, odds_api_key)
    return _adaptive_sync_engine
