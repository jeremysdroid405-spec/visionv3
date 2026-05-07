"""
MLB Cached Board Builder (Enrichment Pipeline)
===============================================
Merges mlb_live_props with mlb_master_hub_2026 to create enriched player cards.

Enrichment Process:
1. Match props to players by player_name and team
2. Attach last 10 game logs for the specific stat
3. Calculate season average for that stat
4. Calculate CV (Coefficient of Variation) for consistency scoring
5. Calculate hit rate (% of games over the line)
6. Determine lineup_status: CONFIRMED, PROJECTED, BENCHED, or UNKNOWN

Lineup Status Mapping:
- CONFIRMED: Player is in today's confirmed BDL lineup
- PROJECTED: Player has recent game activity (last 5 days) but lineup not yet confirmed
- BENCHED: Player's team has a confirmed lineup but player is NOT in it
- UNKNOWN: No lineup data and no recent activity

Circuit Breaker:
- If Odds API returns 0 props, DO NOT wipe mlb_cached_board
- Keep previous day's lines active
- Log warning: 'MLB Odds Sync failed - Preserving Board'

Output: mlb_cached_board collection
"""

import logging
import math
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.db_config import get_collection_name, validate_sport
from services.observability import log_silent_failure

logger = logging.getLogger(__name__)

# Stat type mapping from Odds API to BDL game log fields
MLB_STAT_MAPPING = {
    # Batter stats
    "Hits": "hits",
    "Total Bases": "total_bases",
    "RBIs": "rbis",
    "Runs": "runs",
    "Stolen Bases": "stolen_bases",
    "Home Runs": "home_runs",
    "Batter Walks": "walks",
    "Batter Strikeouts": "strikeouts",
    "Singles": "singles",
    "Doubles": "doubles",
    # Pitcher stats
    "Pitcher Strikeouts": "pitcher_strikeouts",
    "Walks Allowed": "pitcher_walks",
    "Hits Allowed": "hits_allowed",
    "Earned Runs": "earned_runs",
    "Pitcher Outs": "pitcher_outs",
    # Combo stats (calculated from components)
    "Hits+Runs+RBIs": ["hits", "runs", "rbis"],  # HRR combo
    "Hits+Runs": ["hits", "runs"],
    "Total Bases+Runs+RBIs": ["total_bases", "runs", "rbis"],
}

# Stats that need to be calculated as sum of components
COMBO_STATS = {
    "Hits+Runs+RBIs": ["hits", "runs", "rbis"],
    "Hits+Runs": ["hits", "runs"],
    "Total Bases+Runs+RBIs": ["total_bases", "runs", "rbis"],
}

# Minimum games for reliable CV calculation
MIN_GAMES_FOR_CV = 5
MIN_GAMES_FOR_HIT_RATE = 3


def calculate_cv(values: List[float]) -> Optional[float]:
    """
    Calculate Coefficient of Variation (CV).
    
    CV = (Standard Deviation / Mean) * 100
    
    Lower CV = More consistent player
    
    Returns None if insufficient data or mean is 0.
    """
    if len(values) < MIN_GAMES_FOR_CV:
        return None
    
    mean = sum(values) / len(values)
    if mean == 0:
        return None
    
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    std_dev = math.sqrt(variance)
    cv = (std_dev / mean) * 100
    
    return round(cv, 2)


def calculate_hit_rate(values: List[float], line: float) -> Optional[float]:
    """
    Calculate hit rate (% of games over the line).
    
    SSOT: Uses >= for "over" evaluation (consistent with player detail endpoint).
    For a 1.5 line, getting 2 hits = HIT. Getting 1 hit = MISS.
    
    Returns percentage (0-100).
    """
    if len(values) < MIN_GAMES_FOR_HIT_RATE:
        return None
    
    # SSOT: >= for "over" evaluation (value meets or exceeds line = HIT)
    hits = sum(1 for v in values if v >= line)
    rate = (hits / len(values)) * 100
    
    return round(rate, 1)


def calculate_season_average(values: List[float]) -> Optional[float]:
    """Calculate season average from game logs."""
    if not values:
        return None
    return round(sum(values) / len(values), 2)


class MLBCachedBoardBuilder:
    """
    Builds the MLB cached board by enriching props with player stats.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.sport = "mlb"
        # Lineup cache: {player_name_lower: lineup_status}
        self._lineup_cache: Dict[str, str] = {}
        self._lineup_teams_loaded: set = set()
    
    async def _load_today_lineups(self):
        """
        Load today's lineups from bdl_lineups collection.
        Maps player names to lineup status: CONFIRMED, PROJECTED, BENCHED, UNKNOWN
        """
        from datetime import timedelta
        
        # Reset cache
        self._lineup_cache = {}
        self._lineup_teams_loaded = set()
        
        # Get lineups synced in the last 24 hours
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        
        lineups = await self.db.bdl_lineups.find({
            "synced_at": {"$gte": cutoff}
        }).to_list(None)
        
        logger.info(f"[MLB_BOARD] Loaded {len(lineups)} team lineups from bdl_lineups")
        
        # Build confirmed starters set
        for lineup in lineups:
            team_name = (lineup.get("team_name") or "").lower()
            players = lineup.get("players", [])
            
            self._lineup_teams_loaded.add(team_name)
            
            for player in players:
                player_name = (player.get("name") or "").strip().lower()
                if player_name:
                    # Player is in the confirmed lineup
                    self._lineup_cache[player_name] = "CONFIRMED"
        
        logger.info(f"[MLB_BOARD] {len(self._lineup_cache)} players marked CONFIRMED")
        logger.info(f"[MLB_BOARD] Teams with lineups: {len(self._lineup_teams_loaded)}")
    
    def _determine_lineup_status(self, player_name: str, team_name: str, game_logs: List[Dict]) -> str:
        """
        Determine lineup status for a player.
        
        Returns one of: "CONFIRMED", "PROJECTED", "BENCHED", "UNKNOWN"
        
        Logic:
        - CONFIRMED: Player is in today's confirmed BDL lineup
        - PROJECTED: Player has recent game activity (last 5 days) but lineup not yet confirmed
        - BENCHED: Player's team has a confirmed lineup but player is NOT in it
        - UNKNOWN: No lineup data and no recent activity
        """
        player_key = player_name.lower().strip()
        team_key = (team_name or "").lower().strip()
        
        # Check if player is in confirmed lineup
        if player_key in self._lineup_cache:
            return "CONFIRMED"
        
        # Check if team has lineup posted but player not in it
        if team_key in self._lineup_teams_loaded:
            # Team has lineup but player not listed = BENCHED
            return "BENCHED"
        
        # No lineup data for this team - check recent activity
        if game_logs:
            # Check for game in last 5 days = PROJECTED (expected starter)
            from datetime import timedelta
            cutoff_date = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
            
            for log in game_logs[:3]:  # Check last 3 logs
                log_date = log.get("date", "")
                if log_date >= cutoff_date:
                    return "PROJECTED"
        
        return "UNKNOWN"
    
    def _get_collection(self, base_name: str):
        """Get MLB-specific collection."""
        collection_name = get_collection_name(base_name, self.sport)
        return self.db[collection_name]
    
    async def get_player_stats(self, player_name: str, team_abbr: str = None) -> Optional[Dict]:
        """
        Get player from master_hub by name and optionally team.
        
        Args:
            player_name: Player's display name
            team_abbr: Optional team abbreviation for disambiguation
            
        Returns:
            Player document with game logs or None
        """
        master_hub = self._get_collection("master_hub")
        
        # Try exact match first
        query = {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}}
        if team_abbr:
            query["team_abbr"] = team_abbr.upper()
        
        player = await master_hub.find_one(query, {"_id": 0})
        
        if not player:
            # Try partial match (first/last name)
            query = {"display_name": {"$regex": player_name, "$options": "i"}}
            if team_abbr:
                query["team_abbr"] = team_abbr.upper()
            player = await master_hub.find_one(query, {"_id": 0})
        
        return player
    
    def extract_stat_values(
        self,
        game_logs: List[Dict],
        stat_type: str,
        limit: int = 10
    ) -> Tuple[List[float], str]:
        """
        Extract stat values from game logs.
        
        Handles both single stats and combo stats (Hits+Runs+RBIs, etc.)
        
        Args:
            game_logs: List of game log dictionaries
            stat_type: Prop stat type (e.g., "Hits", "Hits+Runs+RBIs")
            limit: Number of recent games to include
            
        Returns:
            Tuple of (values list, field name used)
        """
        # Check if this is a combo stat
        if stat_type in COMBO_STATS:
            component_fields = COMBO_STATS[stat_type]
            values = []
            
            for log in game_logs[:limit]:
                combo_value = 0
                valid = True
                
                for field in component_fields:
                    val = log.get(field)
                    if val is not None:
                        try:
                            combo_value += float(val)
                        except (ValueError, TypeError):
                            valid = False
                            break
                    else:
                        valid = False
                        break
                
                if valid:
                    values.append(combo_value)
            
            return values, "+".join(component_fields)
        
        # Single stat
        field_mapping = MLB_STAT_MAPPING.get(stat_type)
        if not field_mapping:
            logger.warning(f"[MLB_BOARD] Unknown stat type: {stat_type}")
            return [], stat_type.lower()
        
        # Handle if mapping is a list (shouldn't happen for non-combo, but be safe)
        if isinstance(field_mapping, list):
            field_name = field_mapping[0]
        else:
            field_name = field_mapping
        
        values = []
        for log in game_logs[:limit]:
            # Special handling for calculated fields
            if field_name == "singles":
                # Singles = hits - doubles - triples - home_runs
                hits = log.get("hits")
                if hits is not None:
                    doubles = log.get("doubles") or 0
                    triples = log.get("triples") or 0
                    home_runs = log.get("home_runs") or 0
                    value = max(0, hits - doubles - triples - home_runs)
                else:
                    value = None
            elif field_name == "pitcher_outs":
                # Pitcher Outs = innings_pitched * 3
                ip = log.get("innings_pitched")
                value = round(ip * 3) if ip is not None else None
            else:
                value = log.get(field_name)
            
            if value is not None:
                try:
                    values.append(float(value))
                except (ValueError, TypeError) as _swept_exc:
                    log_silent_failure("services.mlb_cached_board_builder.extract_stat_values", _swept_exc)  # sweep-auto-converted
        
        return values, field_name
    
    def enrich_prop(
        self,
        prop: Dict,
        player: Dict
    ) -> Dict:
        """
        Enrich a single prop with player stats.
        
        Adds:
        - last_10_games: Recent game logs for context
        - season_average: Average for this stat
        - cv: Coefficient of Variation
        - hit_rate_l10: % of L10 games over the line
        - hit_rate_l5: % of L5 games over the line
        - lineup_status: CONFIRMED, PROJECTED, BENCHED, or UNKNOWN
        
        SSOT: Uses mlb_master_hub_2026.bdl_game_logs as the single source of truth.
        IMPORTANT: Filters to CURRENT SEASON only.
        """
        from datetime import datetime
        current_season = datetime.now().year
        
        stat_type = prop.get("stat_type", "")
        line = prop.get("line", 0)
        player_name = prop.get("player_name", "") or player.get("display_name", "")
        team_name = player.get("team_name", "") or player.get("team_abbr", "")
        
        # SSOT: mlb_master_hub_2026.bdl_game_logs - FILTER TO CURRENT SEASON
        all_game_logs = player.get("bdl_game_logs", [])
        game_logs = [
            log for log in all_game_logs
            if log.get("season") == current_season or 
               (log.get("date", "")[:4] == str(current_season))
        ]
        
        # Determine lineup status
        lineup_status = self._determine_lineup_status(player_name, team_name, game_logs)
        
        # Extract stat values
        l10_values, field_name = self.extract_stat_values(game_logs, stat_type, limit=10)
        l5_values = l10_values[:5] if len(l10_values) >= 5 else l10_values
        
        # Calculate metrics
        season_avg = calculate_season_average(l10_values)
        cv = calculate_cv(l10_values)
        hit_rate_l10 = calculate_hit_rate(l10_values, line)
        hit_rate_l5 = calculate_hit_rate(l5_values, line)
        
        # Build enriched prop
        # 2026-04-27 routing fix:
        #   • Canonicalize stat_type via the shared SSOT normalizer so
        #     pitcher vs batter strikeouts, HRR vs Hits, and combo/alt
        #     spellings never collapse. Original market key preserved on
        #     `stat_type_raw` for traceability.
        #   • Set `direction` and `side` from `recommendation` so any
        #     consumer that joins on direction/side gets a value (the
        #     live_props writer leaves both as None today).
        from services.scoring.stat_family import (
            canonical_stat_family as _canon_stat,
            build_canonical_key as _canon_key,
        )
        raw_stat = prop.get("stat_type") or ""
        canon_stat = _canon_stat(raw_stat, sport="mlb")
        rec = (prop.get("recommendation") or prop.get("side")
               or prop.get("direction") or "OVER")
        rec_u = str(rec).strip().upper()
        if rec_u not in ("OVER", "UNDER"):
            rec_u = "OVER"
        rec_title = "Under" if rec_u == "UNDER" else "Over"

        enriched = {
            **prop,
            # Stat-type canonicalization
            "stat_type": canon_stat or raw_stat,
            "stat_type_canonical": canon_stat or raw_stat,
            **({"stat_type_raw": raw_stat}
               if raw_stat and raw_stat != (canon_stat or raw_stat) else {}),
            # Canonical join key (rebuilt from canonical stat — old key
            # may have used the raw label).
            "canonical_key": _canon_key(
                "mlb", prop.get("event_id"),
                prop.get("player_name") or player.get("display_name"),
                canon_stat or raw_stat, line, rec_u,
            ),
            # Side fields — canonical is `recommendation`+`side`; the
            # `direction` alias was dropped in SSOT Tier F #1
            # (2026-05-04). If upstream writes arrive with `direction`
            # but no `recommendation`, we rehydrate `recommendation`
            # from `direction` here (transitional) — but we no longer
            # stamp a new `direction` alias on the enriched row.
            "side":      prop.get("side") or rec_u,
            "recommendation": prop.get("recommendation") or rec_u,
            # Player info
            "player_id": player.get("bdl_id"),
            "team": player.get("team_abbr"),
            "team_name": player.get("team_name"),
            "position": player.get("position") or player.get("primary_position"),
            # Lineup status (NEW - replaces is_lineup_confirmed)
            "lineup_status": lineup_status,
            # Stats enrichment
            "stat_field": field_name,
            "season_average": season_avg,
            "cv": cv,
            "cv_grade": self._grade_cv(cv),
            "hit_rate_l10": hit_rate_l10,
            "hit_rate_l5": hit_rate_l5,
            # L10 game logs (trimmed for storage)
            "last_10_games": self._build_game_logs_for_prop(game_logs[:10], field_name),
            "games_played": len(l10_values),
            # Edge calculation
            "edge": self._calculate_edge(season_avg, line) if season_avg else None,
            # Metadata
            "enriched_at": datetime.now(timezone.utc).isoformat(),
            "is_enriched": True,
        }
        
        return enriched
    
    def _build_game_logs_for_prop(self, game_logs: List[Dict], field_name: str) -> List[Dict]:
        """Build game logs with proper value extraction for all stat types including calculated fields."""
        result = []
        for log in game_logs:
            # Calculate value based on field type
            if field_name == "singles":
                # Singles = hits - doubles - triples - home_runs
                hits = log.get("hits")
                if hits is not None:
                    doubles = log.get("doubles") or 0
                    triples = log.get("triples") or 0
                    home_runs = log.get("home_runs") or 0
                    value = max(0, hits - doubles - triples - home_runs)
                else:
                    value = None
            elif field_name == "pitcher_outs":
                # Pitcher Outs = innings_pitched * 3
                ip = log.get("innings_pitched")
                value = round(ip * 3) if ip is not None else None
            else:
                value = log.get(field_name)
            
            if value is not None:
                result.append({
                    "date": log.get("date"),
                    "value": value,
                    "opponent": log.get("opponent_abbr"),
                })
        return result
    
    def _grade_cv(self, cv: Optional[float]) -> str:
        """Grade CV for consistency."""
        if cv is None:
            return "UNKNOWN"
        if cv <= 20:
            return "ELITE"  # Very consistent
        if cv <= 35:
            return "GOOD"   # Consistent
        if cv <= 50:
            return "FAIR"   # Average
        return "VOLATILE"   # Inconsistent
    
    def _calculate_edge(self, avg: float, line: float) -> float:
        """Calculate edge percentage over the line."""
        if line == 0:
            return 0
        edge = ((avg - line) / line) * 100
        return round(edge, 2)
    
    async def build_cached_board(self) -> Dict[str, Any]:
        """
        Build the full MLB cached board.
        
        Process:
        1. Fetch all props from mlb_live_props
        2. Match each prop to a player in mlb_master_hub_2026
        3. Enrich with stats (L10 games, avg, CV, hit rates)
        4. Group by player and save to mlb_cached_board
        
        Circuit Breaker:
        - If 0 props found, preserve existing board
        
        Returns:
            Build summary with counts and errors
        """
        logger.info("=" * 70)
        logger.info("[MLB_BOARD] Building MLB Cached Board")
        logger.info("=" * 70)
        
        build_start = datetime.now(timezone.utc)
        
        results = {
            "success": True,
            "sport": self.sport,
            "built_at": build_start.isoformat(),
            "props_fetched": 0,
            "props_enriched": 0,
            "props_unmatched": 0,
            "players_in_board": 0,
            "errors": []
        }
        
        try:
            # Step 0: Load today's lineups for lineup_status mapping
            await self._load_today_lineups()
            
            # Step 1: Fetch all props from mlb_live_props (must have PrizePicks line)
            live_props = self._get_collection("live_props")
            props = await live_props.find(
                {
                    "$or": [
                        {"source": "prizepicks"},
                        {"pp_line": {"$ne": None}}  # Has a PrizePicks line
                    ]
                },
                {"_id": 0}
            ).to_list(length=None)
            results["props_fetched"] = len(props)
            
            logger.info(f"[MLB_BOARD] Fetched {len(props)} PrizePicks props from mlb_live_props")
            
            # ========================================================
            # CIRCUIT BREAKER: Do not wipe board if 0 props
            # ========================================================
            if len(props) == 0:
                logger.warning("=" * 60)
                logger.warning("[MLB_BOARD] ⚠️ MLB Odds Sync failed - Preserving Board")
                logger.warning("[MLB_BOARD] 0 props returned from Odds API")
                logger.warning("[MLB_BOARD] Previous day's lines remain active")
                logger.warning("=" * 60)
                
                results["success"] = False
                results["circuit_breaker_triggered"] = True
                results["errors"].append("MLB Odds Sync failed - Preserving Board")
                return results
            
            # Step 2: Group props by player and enrich
            player_cache: Dict[str, Dict] = {}  # Cache player lookups
            player_props: Dict[str, List[Dict]] = {}  # Group props by player
            unmatched = []
            
            for prop in props:
                player_name = prop.get("player_name", "")
                if not player_name:
                    continue
                
                # Get player from cache or fetch
                cache_key = player_name.lower()
                if cache_key not in player_cache:
                    # Try to find player in master_hub
                    player = await self.get_player_stats(player_name)
                    player_cache[cache_key] = player
                
                player = player_cache.get(cache_key)
                
                if player:
                    # Enrich the prop
                    enriched_prop = self.enrich_prop(prop, player)
                    
                    # Group by player name
                    if player_name not in player_props:
                        player_props[player_name] = {
                            "player_name": player_name,
                            "bdl_id": player.get("bdl_id"),
                            "team": player.get("team_abbr"),
                            "team_name": player.get("team_name"),
                            "position": player.get("position") or player.get("primary_position"),
                            "props": []
                        }
                    
                    player_props[player_name]["props"].append(enriched_prop)
                    results["props_enriched"] += 1
                else:
                    # Player not found in master_hub
                    unmatched.append(player_name)
                    results["props_unmatched"] += 1
            
            logger.info(f"[MLB_BOARD] Enriched {results['props_enriched']} props")
            logger.info(f"[MLB_BOARD] Unmatched {results['props_unmatched']} props")
            
            # Log some unmatched players for debugging
            unique_unmatched = list(set(unmatched))[:10]
            if unique_unmatched:
                logger.warning(f"[MLB_BOARD] Sample unmatched players: {unique_unmatched}")
            
            # Step 3: Save to mlb_cached_board
            cached_board = self._get_collection("cached_board")
            
            # Clear old data and insert new
            if player_props:
                # 2026-05-07 P0 §3 fix: precompute the canonical freshness
                # stamp ONCE so every player_doc inserted on this rebuild
                # carries identical `updated_at / last_publish_ts /
                # source_score_max_scored_at / sport / version_tag`. The
                # follow-up `master_sync` Step 7 will refresh these at
                # end-of-run, but writing them at insert-time guarantees
                # SLO §3 has a canonical signal even between master_sync
                # passes (e.g. a fresh rebuild that hasn't reached Step 7
                # yet).
                from services.board_freshness import (
                    build_freshness_stamp,
                    _max_scored_at,
                )
                _src_score_max = await _max_scored_at(self.db, self.sport)
                _freshness_stamp = build_freshness_stamp(
                    self.sport,
                    now=build_start,
                    source_score_max_scored_at=_src_score_max,
                )

                # Use bulk operations for efficiency
                await cached_board.delete_many({})  # Clear old
                
                player_docs = list(player_props.values())
                
                # Add metadata to each player doc
                for doc in player_docs:
                    # Phase 4 freshness contract (overrides legacy `sport`).
                    doc.update(_freshness_stamp)
                    doc["built_at"] = build_start.isoformat()  # legacy compat
                    doc["props_count"] = len(doc["props"])
                
                await cached_board.insert_many(player_docs)
                results["players_in_board"] = len(player_docs)
                
                logger.info(f"[MLB_BOARD] Saved {len(player_docs)} players to mlb_cached_board")
            
            # Summary
            duration = (datetime.now(timezone.utc) - build_start).total_seconds()
            results["duration_seconds"] = round(duration, 2)
            results["collection"] = get_collection_name("cached_board", self.sport)
            
            logger.info("[MLB_BOARD] Build Complete:")
            logger.info(f"  • Props: {results['props_enriched']}/{results['props_fetched']} enriched")
            logger.info(f"  • Players: {results['players_in_board']}")
            logger.info(f"  • Duration: {duration:.2f}s")
            
        except Exception as e:
            logger.error(f"[MLB_BOARD] Build error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results["success"] = False
            results["errors"].append(str(e))
        
        return results
    
    async def get_cached_board(self, limit: int = 100) -> Dict[str, Any]:
        """
        Get the current MLB cached board.
        
        Returns:
            Board data with players and their enriched props
        """
        cached_board = self._get_collection("cached_board")
        
        players = await cached_board.find({}, {"_id": 0}).limit(limit).to_list(length=limit)
        
        total_props = sum(len(p.get("props", [])) for p in players)
        
        return {
            "success": True,
            "sport": self.sport,
            "players": players,
            "players_count": len(players),
            "total_props": total_props,
            "collection": get_collection_name("cached_board", self.sport)
        }


# Singleton instance
_mlb_board_builder: Optional[MLBCachedBoardBuilder] = None


def get_mlb_board_builder(db: AsyncIOMotorDatabase) -> MLBCachedBoardBuilder:
    """Get or create the MLB cached board builder."""
    global _mlb_board_builder
    if _mlb_board_builder is None:
        _mlb_board_builder = MLBCachedBoardBuilder(db)
    return _mlb_board_builder


async def run_mlb_board_build(db: AsyncIOMotorDatabase) -> Dict[str, Any]:
    """
    Run the full MLB cached board build pipeline.
    
    1. Reads props from mlb_live_props
    2. Enriches with mlb_master_hub_2026 stats
    3. Saves to mlb_cached_board
    
    Returns:
        Build summary
    """
    builder = get_mlb_board_builder(db)
    return await builder.build_cached_board()
