"""
ADAPTIVE SYNC ENGINE - Mission-Critical Polling System
========================================================
Implements intelligent polling based on game proximity:
- Standby (>6hrs): Refresh every 60 minutes
- Active (1-6hrs): Refresh every 10 minutes  
- Mission Critical (<60mins): Refresh every 60 seconds
- Post-Tip: Cease polling for that game

Also handles:
- Stale Intel detection and alerts
- Client-side protection (all reads from MongoDB)
- Last updated timestamps on all cached data

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

logger = logging.getLogger(__name__)

# Polling intervals in seconds - INCREASED to conserve API quota
class PollInterval(Enum):
    STANDBY = 7200       # 2 hours (>6hrs to tip) - was 60 min
    ACTIVE = 1800        # 30 minutes (1-6hrs to tip) - was 10 min
    MISSION_CRITICAL = 300  # 5 minutes (<60mins to tip) - was 60 sec
    POST_TIP = None      # Stop polling

# Thresholds in hours
STANDBY_THRESHOLD = 6       # >6 hours = Standby
ACTIVE_THRESHOLD = 1        # 1-6 hours = Active
MISSION_CRITICAL_THRESHOLD = 1  # <1 hour = Mission Critical

# Stale data threshold
STALE_DATA_THRESHOLD_SECONDS = 300  # 5 minutes


class GameStatus(Enum):
    STANDBY = "standby"
    ACTIVE = "active"
    MISSION_CRITICAL = "mission_critical"
    POST_TIP = "post_tip"


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
        
        # Collections
        self.cached_board_collection = "dg_cached_board"
        self.sync_status_collection = "dg_sync_status"
        self.game_schedule_collection = "dg_game_schedule"
        self.master_hub_collection = "nba_master_hub_2026"
        
        # Player stats cache (refreshed each sync cycle)
        self._player_stats_cache: Dict[str, Dict] = {}
        
        logger.info("[ADAPTIVE_SYNC] Engine initialized")
    
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
            {"_id": 0, "baseline_stats": 1, "bdl_game_logs": 1}
        )
        
        if not player:
            normalized = _normalize_name(player_name)
            all_players = await self.db[self.master_hub_collection].find(
                {}, {"display_name": 1, "baseline_stats": 1, "bdl_game_logs": 1, "_id": 0}
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
        
        # Calculate from BDL game logs for accurate L5/L10/H10
        game_logs = player.get("bdl_game_logs", [])
        if game_logs:
            # Sort by date (most recent first)
            game_logs = sorted(
                game_logs,
                key=lambda x: x.get('game', {}).get('date', '') if isinstance(x.get('game'), dict) else x.get('date', ''),
                reverse=True
            )
            
            # Map stat_key to game log field
            log_key_map = {
                "PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl",
                "BLK": "blk", "THREES": "fg3m", "3PM": "fg3m", "TO": "turnover"
            }
            log_key = log_key_map.get(stat_key)
            
            # Extract values from game logs
            game_values = []
            for g in game_logs:
                try:
                    if stat_key in ["PRA"]:
                        val = float(g.get("pts", 0) or 0) + float(g.get("reb", 0) or 0) + float(g.get("ast", 0) or 0)
                    elif stat_key in ["PR", "P+R"]:
                        val = float(g.get("pts", 0) or 0) + float(g.get("reb", 0) or 0)
                    elif stat_key in ["PA", "P+A"]:
                        val = float(g.get("pts", 0) or 0) + float(g.get("ast", 0) or 0)
                    elif stat_key in ["RA", "R+A"]:
                        val = float(g.get("reb", 0) or 0) + float(g.get("ast", 0) or 0)
                    elif log_key:
                        val = float(g.get(log_key, 0) or 0)
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
                return GameStatus.POST_TIP
            elif time_to_tip < ACTIVE_THRESHOLD:
                return GameStatus.MISSION_CRITICAL
            elif time_to_tip < STANDBY_THRESHOLD:
                return GameStatus.ACTIVE
            else:
                return GameStatus.STANDBY
                
        except Exception as e:
            logger.error(f"[ADAPTIVE_SYNC] Error parsing commence_time: {e}")
            return GameStatus.STANDBY
    
    def _get_poll_interval(self, status: GameStatus) -> Optional[int]:
        """Get polling interval in seconds based on game status."""
        intervals = {
            GameStatus.STANDBY: PollInterval.STANDBY.value,
            GameStatus.ACTIVE: PollInterval.ACTIVE.value,
            GameStatus.MISSION_CRITICAL: PollInterval.MISSION_CRITICAL.value,
            GameStatus.POST_TIP: None
        }
        return intervals.get(status)
    
    async def _fetch_live_odds(self) -> List[Dict[str, Any]]:
        """
        Fetch PrizePicks odds from The Odds API.
        
        PRIZEPICKS-SPECIFIC FETCH:
        - Uses regions=us_dfs (Daily Fantasy Sports region for PrizePicks)
        - Uses bookmakers=prizepicks (specifically target PrizePicks)
        - Fetches both standard and alternate markets for proper tier classification
        
        PrizePicks Classification:
        - STANDARD (Gray): Main market lines (player_points, player_rebounds, player_assists)
        - GOBLIN (Green): Alternate market lines with odds != +100 (discount/promo lines)
        - DEMON (Red): Alternate market lines with +100 odds (boosted/hard lines)
        
        Returns raw odds data for processing.
        """
        if not self.odds_api_key:
            logger.warning("[ADAPTIVE_SYNC] No Odds API key configured")
            return []
        
        # Step 1: Fetch list of NBA events
        events_url = f"{self.base_url}/sports/basketball_nba/events"
        events_params = {
            "apiKey": self.odds_api_key,
            "dateFormat": "iso"
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                events_response = await client.get(events_url, params=events_params)
                events_response.raise_for_status()
                events = events_response.json()
                logger.info(f"[PRIZEPICKS_SYNC] Found {len(events)} NBA events")
                
                if not events:
                    return []
                
                # Step 2: Fetch PrizePicks odds for each event
                # PrizePicks-specific markets (standard + alternate for tier classification)
                prizepicks_markets = ",".join([
                    # Standard markets (STANDARD tier)
                    "player_points", "player_rebounds", "player_assists",
                    "player_threes", "player_blocks", "player_steals",
                    "player_points_rebounds_assists", "player_points_rebounds",
                    "player_points_assists", "player_rebounds_assists",
                    # Alternate markets (GOBLIN/DEMON tiers based on odds)
                    "player_points_alternate", "player_rebounds_alternate", "player_assists_alternate",
                    "player_threes_alternate", "player_blocks_alternate", "player_steals_alternate",
                    "player_points_rebounds_assists_alternate", "player_points_rebounds_alternate",
                    "player_points_assists_alternate", "player_rebounds_assists_alternate"
                ])
                
                enriched_events = []
                
                for event in events:
                    event_id = event.get("id")
                    if not event_id:
                        continue
                    
                    # Fetch PrizePicks odds for this event
                    odds_url = f"{self.base_url}/sports/basketball_nba/events/{event_id}/odds"
                    odds_params = {
                        "apiKey": self.odds_api_key,
                        "regions": "us_dfs",  # PRIZEPICKS: Daily Fantasy Sports region
                        "bookmakers": "prizepicks",  # PRIZEPICKS: Target only PrizePicks
                        "markets": prizepicks_markets,
                        "oddsFormat": "american"
                    }
                    
                    try:
                        odds_response = await client.get(odds_url, params=odds_params, timeout=15.0)
                        if odds_response.status_code == 200:
                            odds_data = odds_response.json()
                            # Merge event info with odds data
                            odds_data["event_id"] = event_id
                            enriched_events.append(odds_data)
                            
                            # Log PrizePicks data found
                            pp_markets = 0
                            for bm in odds_data.get("bookmakers", []):
                                if bm.get("key") == "prizepicks":
                                    pp_markets = len(bm.get("markets", []))
                            if pp_markets > 0:
                                logger.info(f"  [PRIZEPICKS] {event.get('away_team')} @ {event.get('home_team')}: {pp_markets} markets")
                                
                        elif odds_response.status_code == 422:
                            # Try with core markets only
                            odds_params["markets"] = "player_points,player_points_alternate,player_rebounds,player_rebounds_alternate,player_assists,player_assists_alternate"
                            odds_response = await client.get(odds_url, params=odds_params, timeout=15.0)
                            if odds_response.status_code == 200:
                                odds_data = odds_response.json()
                                odds_data["event_id"] = event_id
                                enriched_events.append(odds_data)
                    except Exception as e:
                        logger.warning(f"[PRIZEPICKS_SYNC] Failed to fetch odds for event {event_id}: {e}")
                        continue
                
                logger.info(f"[PRIZEPICKS_SYNC] Fetched PrizePicks odds for {len(enriched_events)} events")
                return enriched_events
                
        except httpx.HTTPStatusError as e:
            logger.error(f"[PRIZEPICKS_SYNC] Odds API HTTP error: {e.response.status_code}")
            return []
        except Exception as e:
            logger.error(f"[PRIZEPICKS_SYNC] Odds API error: {e}")
            return []
    
    async def _update_game_registry(self, events: List[Dict[str, Any]]) -> None:
        """
        Update the game registry with current events and their statuses.
        """
        current_game_ids = set()
        
        for event in events:
            game_id = event.get("id")
            if not game_id:
                continue
            
            current_game_ids.add(game_id)
            commence_time = event.get("commence_time", "")
            status = self._get_game_status(commence_time)
            
            self.game_registry[game_id] = {
                "id": game_id,
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
            if self.game_registry[game_id]["status"] == GameStatus.POST_TIP.value:
                del self.game_registry[game_id]
                logger.info(f"[ADAPTIVE_SYNC] Removed completed game: {game_id}")
    
    async def _update_cached_board(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Update the dg_cached_board collection with PrizePicks odds data.
        
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
        # ============================================================
        # Key: (player_name, stat_type) -> main_line value
        main_lines: Dict[tuple, float] = {}
        # Collect all props for Pass 2
        all_props: List[Dict] = []
        
        for event in events:
            game_id = event.get("id")
            commence_time = event.get("commence_time", "")
            home_team = event.get("home_team", "")
            away_team = event.get("away_team", "")
            
            bookmakers = event.get("bookmakers", [])
            
            for bookmaker in bookmakers:
                bookmaker_key = bookmaker.get("key", "")
                
                # Only process PrizePicks
                if bookmaker_key != "prizepicks":
                    continue
                    
                markets = bookmaker.get("markets", [])
                
                for market in markets:
                    market_key = market.get("key", "")
                    outcomes = market.get("outcomes", [])
                    
                    # Detect alternate market
                    is_alternate_market = "_alternate" in market_key
                    
                    # Extract base stat type
                    stat_type_extracted = market_key.replace("_alternate", "").replace("player_", "").upper()
                    stat_type_extracted = stat_type_extracted.replace("POINTS_REBOUNDS_ASSISTS", "PRA")
                    stat_type_extracted = stat_type_extracted.replace("POINTS_REBOUNDS", "P+R")
                    stat_type_extracted = stat_type_extracted.replace("POINTS_ASSISTS", "P+A")
                    stat_type_extracted = stat_type_extracted.replace("REBOUNDS_ASSISTS", "R+A")
                    stat_type_extracted = stat_type_extracted.replace("THREES", "3PM")
                    stat_type_extracted = stat_type_extracted.replace("BLOCKS", "BLK")
                    stat_type_extracted = stat_type_extracted.replace("STEALS", "STL")
                    stat_type_extracted = stat_type_extracted.replace("TURNOVERS", "TO")
                    stat_type_extracted = stat_type_extracted.replace("POINTS", "PTS")
                    stat_type_extracted = stat_type_extracted.replace("REBOUNDS", "REB")
                    stat_type_extracted = stat_type_extracted.replace("ASSISTS", "AST")
                    
                    for outcome in outcomes:
                        player_name = outcome.get("description", "")
                        if not player_name:
                            continue
                        
                        price = outcome.get("price", 0)
                        line = outcome.get("point", 0)
                        direction = (outcome.get("name", "") or "over").lower()
                        
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
                            "direction": direction,
                            "name": outcome.get("name", ""),
                            "is_alternate_market": is_alternate_market,
                            "stat_type_extracted": stat_type_extracted
                        }
                        all_props.append(prop_data)
                        
                        # If this is NOT an alternate market, it's the MAIN LINE (anchor)
                        if not is_alternate_market:
                            key = (player_name, stat_type_extracted)
                            main_lines[key] = line
                            logger.debug(f"[ANCHOR] {player_name} {stat_type_extracted}: main_line = {line}")
        
        logger.info(f"[PRIZEPICKS_SYNC_V3] Pass 1 complete: {len(main_lines)} main lines (anchors) identified")
        
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
                    "direction": prop["direction"],
                    "name": prop["name"],
                    "last_updated": now,
                    "last_updated_iso": now.isoformat(),
                    "sync_source": "prizepicks_sync_v3",
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
                await self.db[self.cached_board_collection].update_one(
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
                logger.error(f"[PRIZEPICKS_SYNC] Error updating {player_name}: {e}")
        
        # Log classification distribution
        if updated_count > 0:
            logger.info(f"[PRIZEPICKS_SYNC_V3] Anchor-Based Classification: "
                       f"{demon_count} Demon (above anchor), "
                       f"{goblin_count} Goblin (below anchor), "
                       f"{standard_count} Standard")
            if l5_fallback_count > 0:
                logger.info(f"[PRIZEPICKS_SYNC_V3] L5/Season fallback used for {l5_fallback_count} player/stat combos (no main line)")
            if no_anchor_count > 0:
                logger.warning(f"[PRIZEPICKS_SYNC_V3] {no_anchor_count} player/stat combos had NO anchor (no main line, no stats)")
        
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
            stale_count = await self.db[self.cached_board_collection].count_documents({
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
        """
        logger.info("[ADAPTIVE_SYNC] Starting adaptive poll loop")
        
        # Track last sync times (sync every 30 min at most)
        last_injury_sync = None
        last_ticker_sync = None
        INJURY_SYNC_INTERVAL = 1800  # 30 minutes
        TICKER_SYNC_INTERVAL = 1800  # 30 minutes
        
        while self.is_running:
            try:
                # Fetch current odds
                events = await self._fetch_live_odds()
                
                # Update game registry with current statuses
                await self._update_game_registry(events)
                
                # Update cached board
                await self._update_cached_board(events)
                
                now = datetime.now(timezone.utc)
                
                # Sync injuries periodically (every 30 min) alongside odds
                if last_injury_sync is None or (now - last_injury_sync).total_seconds() >= INJURY_SYNC_INTERVAL:
                    await self._sync_injuries()
                    last_injury_sync = now
                
                # Sync ticker (games/news) periodically (every 30 min)
                if last_ticker_sync is None or (now - last_ticker_sync).total_seconds() >= TICKER_SYNC_INTERVAL:
                    await self._sync_ticker()
                    last_ticker_sync = now
                
                # Determine next poll interval based on most urgent game
                min_interval = PollInterval.STANDBY.value  # Default to 60 minutes
                
                for game in self.game_registry.values():
                    if game["status"] == "mission_critical":
                        min_interval = min(min_interval, PollInterval.MISSION_CRITICAL.value)
                    elif game["status"] == "active":
                        min_interval = min(min_interval, PollInterval.ACTIVE.value)
                
                # Check for stale intel in mission-critical windows
                stale_check = await self.check_stale_intel()
                if stale_check["has_stale_intel"]:
                    logger.warning(f"[ADAPTIVE_SYNC] STALE INTEL DETECTED: {len(stale_check['stale_games'])} games")
                    # Don't wait, do immediate refresh
                    min_interval = 5  # 5 second emergency refresh
                
                logger.info(f"[ADAPTIVE_SYNC] Poll complete. Next refresh in {min_interval}s. "
                           f"Games: {len(self.game_registry)} | "
                           f"Critical: {len([g for g in self.game_registry.values() if g['status'] == 'mission_critical'])}")
                
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
    
    async def start(self) -> None:
        """Start the adaptive sync engine."""
        if self.is_running:
            logger.warning("[ADAPTIVE_SYNC] Engine already running")
            return
        
        self.is_running = True
        self.main_task = asyncio.create_task(self._adaptive_poll_loop())
        logger.info("[ADAPTIVE_SYNC] Engine started")
    
    async def stop(self) -> None:
        """Stop the adaptive sync engine."""
        self.is_running = False
        
        if self.main_task:
            self.main_task.cancel()
            try:
                await self.main_task
            except asyncio.CancelledError:
                pass
        
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
        cursor = self.db[self.cached_board_collection].find(
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
