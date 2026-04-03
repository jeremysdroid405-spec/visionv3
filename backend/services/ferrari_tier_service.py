"""
Ferrari v6 Pipeline - Global Power Ranking & Universal Scan
============================================================
"Verified [X] active props to identify these [30] Elite opportunities."

UNIVERSAL SCAN REQUIREMENT:
- 100% scan of ALL props from API
- No early breaks or lazy loading
- Complete field analysis before selection

POWER SCORE FORMULA (0-100 scale):
  ferrari_power_score = (Edge × 0.4) + (Cushion × 0.3) + (Consistency × 0.3) + Whistle_Modifier
  
  Edge Weight (40%):
    = (Bovada_Implied_Prob - PrizePicks_Implied_Prob) × 4
    
  Cushion Weight (30%):
    = ((Season_Median - PrizePicks_Line) / PrizePicks_Line) × 100
    
  Consistency Weight (30%):
    = L10_Hit_Rate (0-100 scale)
    
  WHISTLE MATRIX MODIFIER:
    Green Light (+15 PTS/FTM, +7.5 PRA): High whistle crew (PPG > 118 OR O/U > 60%)
    Red Light (-15 PTS/FTM, -7.5 PRA): Low whistle crew (PPG < 113 OR O/U < 45%)

GLOBAL SORT:
  - Sort ALL survivors by ferrari_power_score DESC
  - Deduplication Priority: Safe Haven > Front Lines > War Zone
  - Top 10 per tier

PERFORMANCE:
  - Bulk write operations
  - Index on ferrari_power_score
  - Optimized for 5,000+ props
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from collections import Counter
import logging

logger = logging.getLogger(__name__)

# =============================================================================
# FERRARI v6 CONSTANTS
# =============================================================================

# KILL SWITCH: 5% Implied Probability Separation
MIN_SEPARATION_PCT = 5.0

# PRIZEPICKS IMPLIED PROBABILITY (-137)
PP_IMPLIED = 0.578  # 57.8%

# TIER WINDOWS
SAFE_HAVEN_MAX = -250
FRONT_LINES_MIN = -245
FRONT_LINES_MAX = -115
WAR_ZONE_MIN = -114
WAR_ZONE_MAX = 500

# POWER SCORE WEIGHTS
WEIGHT_EDGE = 0.40
WEIGHT_CUSHION = 0.30
WEIGHT_CONSISTENCY = 0.30

# OUTPUT CAPS
MAX_PICKS_PER_TIER = 10


# =============================================================================
# MATHEMATICAL FUNCTIONS
# =============================================================================

def american_to_implied(odds: int) -> float:
    """
    Convert American odds to implied probability.
    -200 → 0.667, +200 → 0.333, -137 → 0.578
    """
    if odds is None:
        return 0.0
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    return 100 / (odds + 100)


def calculate_edge_component(bovada_implied: float, pp_implied: float = PP_IMPLIED) -> float:
    """
    EDGE WEIGHT (40%):
    = (Bovada_Implied_Prob - PrizePicks_Implied_Prob) × 4
    
    This produces values typically 0-50 for the edge component.
    Multiplying by 4 scales the difference appropriately.
    
    Example:
      Bovada: -400 → 80.0% implied
      PP: -137 → 57.8% implied
      Edge = (0.80 - 0.578) × 4 = 0.222 × 4 = 0.888 → 88.8 (capped at contribution)
    """
    if bovada_implied <= 0:
        return 0.0
    
    edge = (bovada_implied - pp_implied) * 4
    # Scale to 0-100 range (max edge is ~1.6 when Bovada is 99%, which × 4 = 6.4)
    # Normalize: edge * 100 / 4 = edge * 25 to get 0-100 range
    edge_normalized = edge * 25
    return max(0, min(edge_normalized, 100))


def calculate_cushion_component(season_median: float, pp_line: float) -> float:
    """
    CUSHION WEIGHT (30%):
    = ((Season_Median - PrizePicks_Line) / PrizePicks_Line) × 100
    
    Example:
      Season Median: 25.0
      PP Line: 19.5
      Cushion = ((25.0 - 19.5) / 19.5) × 100 = 28.2%
    """
    if pp_line is None or pp_line <= 0 or season_median is None:
        return 0.0
    
    if season_median <= pp_line:
        return 0.0  # Line is at or above median - no cushion
    
    cushion = ((season_median - pp_line) / pp_line) * 100
    return max(0, min(cushion, 100))  # Cap at 100


def calculate_consistency_component(l10_hit_rate: float) -> float:
    """
    CONSISTENCY WEIGHT (30%):
    = L10_Hit_Rate (0-100 scale)
    
    Input should already be 0-100 (e.g., 80 for 80%)
    """
    return max(0, min(l10_hit_rate, 100))


def calculate_power_score(edge: float, cushion: float, consistency: float) -> float:
    """
    FERRARI POWER SCORE (0-100):
    = (Edge × 0.4) + (Cushion × 0.3) + (Consistency × 0.3)
    """
    score = (edge * WEIGHT_EDGE) + (cushion * WEIGHT_CUSHION) + (consistency * WEIGHT_CONSISTENCY)
    return round(score, 2)


def calculate_separation_pct(sharp_implied: float, pp_implied: float = PP_IMPLIED) -> float:
    """
    Calculate separation as percentage points difference.
    Kill switch requires >= 5% separation.
    """
    return abs(sharp_implied - pp_implied) * 100


def calculate_median(values: List[float]) -> Optional[float]:
    """Calculate median from a list."""
    if not values:
        return None
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n % 2 == 0:
        return (sorted_vals[n//2 - 1] + sorted_vals[n//2]) / 2
    return sorted_vals[n//2]


def calculate_mode(values: List[float]) -> Optional[float]:
    """Calculate mode (rounded to 0.5)."""
    if not values:
        return None
    rounded = [round(v * 2) / 2 for v in values]
    counts = Counter(rounded)
    if not counts:
        return None
    mode_val, mode_count = counts.most_common(1)[0]
    return mode_val if mode_count >= 2 else None


def calculate_mean(values: List[float]) -> Optional[float]:
    """Calculate mean."""
    if not values:
        return None
    return sum(values) / len(values)


# =============================================================================
# FERRARI v6 PIPELINE SERVICE
# =============================================================================

class FerrariTierService:
    """
    Ferrari v6 Pipeline - Global Power Ranking + Whistle Matrix
    
    Features:
    - 100% Universal Scan (no early breaks)
    - Power Score ranking for ALL survivors
    - Whistle Matrix modifier based on crew chief stats
    - Bulk write operations
    - Indexed ferrari_power_score for fast sorting
    - Market Intel verification footer
    """
    
    def __init__(self, db):
        self.db = db
        self.cached_board = db.dg_cached_board
        self.player_stats = db.dg_player_stats
        
        # Output collections
        self.ferrari_safe_haven = db.ferrari_safe_haven
        self.ferrari_front_lines = db.ferrari_front_lines
        self.ferrari_war_zone = db.ferrari_war_zone
        self.ferrari_discarded = db.ferrari_discarded
        self.ferrari_scored = db.ferrari_scored
        
        # Referee service for Whistle Matrix
        self._referee_service = None
    
    def _get_referee_service(self):
        """Lazy-load the referee scraper service."""
        if self._referee_service is None:
            from services.referee_scraper_service import get_referee_service
            self._referee_service = get_referee_service(self.db)
        return self._referee_service
    
    async def _sync_referee_data(self) -> Dict[str, Any]:
        """Sync referee assignments and stats before pipeline run."""
        try:
            ref_service = self._get_referee_service()
            result = await ref_service.sync_all()
            logger.info(f"[v6] Referee sync: {result.get('stats_count', 0)} refs, {result.get('assignments_count', 0)} games")
            return result
        except Exception as e:
            logger.warning(f"[v6] Referee sync failed (non-fatal): {e}")
            return {"success": False, "error": str(e)}
    
    async def _ensure_indexes(self):
        """Create index on ferrari_power_score for fast sorting."""
        try:
            await self.ferrari_scored.create_index(
                [("ferrari_power_score", -1)],
                name="power_score_desc"
            )
            await self.ferrari_scored.create_index(
                [("tier", 1), ("ferrari_power_score", -1)],
                name="tier_power_score"
            )
            logger.info("[v6] Indexes created/verified")
        except Exception as e:
            logger.warning(f"[v6] Index creation warning: {e}")
    
    async def _load_season_medians(self) -> Dict[str, Dict[str, float]]:
        """Load season median for each player/stat."""
        medians = {}
        
        try:
            cursor = self.player_stats.find({}, {"_id": 0, "player_name": 1, "games": 1})
            async for doc in cursor:
                player_name = doc.get("player_name")
                games = doc.get("games", [])
                
                if not player_name or not games:
                    continue
                
                stat_values = {
                    "PTS": [g["pts"] for g in games if g.get("pts") is not None],
                    "AST": [g["ast"] for g in games if g.get("ast") is not None],
                    "REB": [g["reb"] for g in games if g.get("reb") is not None],
                    "3PM": [g["fg3m"] for g in games if g.get("fg3m") is not None],
                    "BLK": [g["blk"] for g in games if g.get("blk") is not None],
                    "STL": [g["stl"] for g in games if g.get("stl") is not None],
                }
                
                # PRA
                pra_values = []
                for g in games:
                    pts = g.get("pts", 0) or 0
                    reb = g.get("reb", 0) or 0
                    ast = g.get("ast", 0) or 0
                    if pts or reb or ast:
                        pra_values.append(pts + reb + ast)
                stat_values["PRA"] = pra_values
                
                player_medians = {}
                for stat, values in stat_values.items():
                    if values:
                        player_medians[stat] = calculate_median(values)
                
                medians[player_name] = player_medians
                
        except Exception as e:
            logger.error(f"[v6] Season median load error: {e}")
        
        return medians
    
    async def _load_l10_stats(self) -> Dict[str, Dict[str, List[float]]]:
        """Load L10 game stats."""
        stats = {}
        
        try:
            cursor = self.player_stats.find({}, {"_id": 0, "player_name": 1, "games": 1})
            async for doc in cursor:
                player_name = doc.get("player_name")
                games = doc.get("games", [])
                
                if not player_name or not games:
                    continue
                
                sorted_games = sorted(
                    games,
                    key=lambda g: g.get("game", {}).get("date", ""),
                    reverse=True
                )[:10]
                
                stats[player_name] = {
                    "PTS": [g["pts"] for g in sorted_games if g.get("pts") is not None],
                    "AST": [g["ast"] for g in sorted_games if g.get("ast") is not None],
                    "REB": [g["reb"] for g in sorted_games if g.get("reb") is not None],
                    "3PM": [g["fg3m"] for g in sorted_games if g.get("fg3m") is not None],
                    "BLK": [g["blk"] for g in sorted_games if g.get("blk") is not None],
                    "STL": [g["stl"] for g in sorted_games if g.get("stl") is not None],
                    "PRA": [
                        (g.get("pts", 0) or 0) + (g.get("reb", 0) or 0) + (g.get("ast", 0) or 0)
                        for g in sorted_games
                    ]
                }
        except Exception as e:
            logger.error(f"[v6] L10 stats load error: {e}")
        
        return stats
    
    async def build_ferrari_tiers(self, sync_time: datetime) -> Dict[str, Any]:
        """
        Execute Ferrari v6 Pipeline - Global Power Ranking + Whistle Matrix
        
        1. Sync referee data (Whistle Matrix)
        2. Universal Scan (100% coverage)
        3. Power Score calculation + Whistle Modifier for ALL survivors
        4. Bulk write to scored collection
        5. Global sort by power_score
        6. Deduplication with tier priority
        7. Top-K selection
        """
        logger.info("=" * 70)
        logger.info("[FERRARI v6] GLOBAL POWER RANKING + WHISTLE MATRIX")
        logger.info("=" * 70)
        
        results = {
            "success": True,
            "synced_at": sync_time.isoformat(),
            "pipeline": "Ferrari v6 + Whistle Matrix",
            "universal_scan": {
                "total_props_scanned": 0,
                "players_processed": 0
            },
            "kill_switch": {
                "separation_fail": 0,
                "median_fail": 0,
                "no_sharp_data": 0,
                "total_killed": 0
            },
            "scored": {
                "total_survivors": 0,
                "safe_haven_pool": 0,
                "front_lines_pool": 0,
                "war_zone_pool": 0
            },
            "whistle_matrix": {
                "refs_synced": 0,
                "games_with_refs": 0,
                "green_light_applied": 0,
                "red_light_applied": 0,
                "neutral": 0
            },
            "usage_vacuum": {
                "active_vacuums": 0,
                "beneficiaries_boosted": 0
            },
            "defensive_momentum": {
                "teams_profiled": 0,
                "elite_matchups": 0,
                "weak_matchups": 0,
                "trend_alerts": 0
            },
            "output": {
                "safe_haven": 0,
                "front_lines": 0,
                "war_zone": 0,
                "total": 0
            },
            "verification": {
                "active_props_verified": 0,
                "elite_opportunities": 0,
                "message": ""
            }
        }
        
        try:
            # Ensure indexes exist
            await self._ensure_indexes()
            
            # =================================================================
            # PHASE 0: WHISTLE MATRIX - Sync Referee Data
            # =================================================================
            logger.info("[PHASE 0] WHISTLE MATRIX - Syncing referee data...")
            ref_service = self._get_referee_service()
            ref_sync_result = await self._sync_referee_data()
            results["whistle_matrix"]["refs_synced"] = ref_sync_result.get("stats_count", 0)
            results["whistle_matrix"]["games_with_refs"] = ref_sync_result.get("assignments_count", 0)
            
            # =================================================================
            # PHASE 0.5: USAGE VACUUM - Check Injuries
            # =================================================================
            logger.info("[PHASE 0.5] USAGE VACUUM - Checking injuries...")
            from services.injury_vacuum_service import get_vacuum_service
            vacuum_service = get_vacuum_service(self.db)
            await vacuum_service.sync_star_profiles()
            vacuum_check = await vacuum_service.check_injuries()
            results["usage_vacuum"]["active_vacuums"] = len(vacuum_check.get("vacuums_triggered", []))
            
            # =================================================================
            # PHASE 0.6: DEFENSIVE MOMENTUM - Build Rankings
            # =================================================================
            logger.info("[PHASE 0.6] DEFENSIVE MOMENTUM - Building rankings...")
            from services.defensive_momentum_service import get_momentum_service
            momentum_service = get_momentum_service(self.db)
            await momentum_service.ensure_cache()
            momentum_status = await momentum_service.get_status()
            results["defensive_momentum"]["teams_profiled"] = momentum_status.get("teams_cached", 0)
            
            # =================================================================
            # PHASE 1: UNIVERSAL SCAN - Load ALL Data (No Limits)
            # =================================================================
            logger.info("[PHASE 1] UNIVERSAL SCAN - Loading complete dataset...")
            
            season_medians = await self._load_season_medians()
            l10_stats = await self._load_l10_stats()
            
            # Get ALL players - NO LIMIT
            cursor = self.cached_board.find({}, {"_id": 0})
            players = await cursor.to_list(length=None)
            
            results["universal_scan"]["players_processed"] = len(players)
            
            logger.info(f"  Players loaded: {len(players)}")
            logger.info(f"  Season medians: {len(season_medians)} players")
            logger.info(f"  L10 stats: {len(l10_stats)} players")
            
            # =================================================================
            # PHASE 2: POWER SCORE CALCULATION (Every Surviving Prop)
            # =================================================================
            logger.info("[PHASE 2] POWER SCORE - Scoring ALL surviving props...")
            
            all_scored = []
            discarded = []
            
            for player in players:
                player_name = player.get("player_name", "")
                player_medians = season_medians.get(player_name, {})
                player_l10 = l10_stats.get(player_name, {})
                
                for prop in player.get("props", []):
                    results["universal_scan"]["total_props_scanned"] += 1
                    
                    # Get sharp market data
                    sharp_market = prop.get("sharp_market", {})
                    sharp_price = sharp_market.get("sharp_price")
                    bovada_price = sharp_market.get("bovada_price")
                    
                    # Use Bovada if available, otherwise sharp_price
                    effective_sharp = bovada_price if bovada_price else sharp_price
                    
                    if effective_sharp is None:
                        results["kill_switch"]["no_sharp_data"] += 1
                        continue
                    
                    # Calculate implied probabilities
                    sharp_implied = american_to_implied(effective_sharp)
                    
                    # ---------------------------------------------------------
                    # KILL SWITCH: 5% Implied Probability Separation
                    # ---------------------------------------------------------
                    separation = calculate_separation_pct(sharp_implied)
                    
                    if separation < MIN_SEPARATION_PCT:
                        discarded.append({
                            "player_name": player_name,
                            "reason": f"SEPARATION: {separation:.1f}% < 5%",
                            "sharp_price": effective_sharp,
                            "separation": round(separation, 1)
                        })
                        results["kill_switch"]["separation_fail"] += 1
                        continue
                    
                    # ---------------------------------------------------------
                    # MEDIAN ANCHOR CHECK
                    # ---------------------------------------------------------
                    stat_type = prop.get("stat_type", "").upper()
                    pp_line = prop.get("line", 0)
                    season_median = player_medians.get(stat_type)
                    
                    if season_median is not None and pp_line > season_median:
                        discarded.append({
                            "player_name": player_name,
                            "reason": f"MEDIAN: Line {pp_line} > Median {season_median}",
                            "sharp_price": effective_sharp
                        })
                        results["kill_switch"]["median_fail"] += 1
                        continue
                    
                    # ---------------------------------------------------------
                    # CALCULATE POWER SCORE COMPONENTS
                    # ---------------------------------------------------------
                    
                    # EDGE: (Bovada_Implied - PP_Implied) × 4, normalized
                    edge_component = calculate_edge_component(sharp_implied)
                    
                    # CUSHION: ((Median - Line) / Line) × 100
                    cushion_component = calculate_cushion_component(season_median, pp_line) if season_median else 0
                    
                    # CONSISTENCY: L10 Hit Rate (0-100)
                    hit_rates = prop.get("hit_rates", {})
                    if "l10_rate" in hit_rates:
                        l10_rate = hit_rates.get("l10_rate") or 0
                        l5_rate = hit_rates.get("l5_rate") or 0
                        l10_hits = hit_rates.get("l10_hit_count") or 0
                    else:
                        l10_data = hit_rates.get("l10", {})
                        l10_rate = (l10_data.get("hit_rate", 0) * 100) if isinstance(l10_data, dict) else 0
                        l10_hits = l10_data.get("games_over", 0) if isinstance(l10_data, dict) else 0
                        l5_rate = 0
                    
                    consistency_component = calculate_consistency_component(l10_rate)
                    
                    # BASE POWER SCORE
                    base_power_score = calculate_power_score(
                        edge_component,
                        cushion_component,
                        consistency_component
                    )
                    
                    # ---------------------------------------------------------
                    # WHISTLE MATRIX MODIFIER
                    # ---------------------------------------------------------
                    team_abbrev = player.get("team", "")
                    ref_info = ref_service.get_ref_for_team(team_abbrev)
                    
                    whistle_modifier = 0.0
                    whistle_class = "neutral"
                    crew_chief = None
                    ref_ou_pct = None
                    ref_ppg = None
                    
                    if ref_info:
                        crew_chief = ref_info.get("crew_chief")
                        ref_ou_pct = ref_info.get("ou_pct")
                        ref_ppg = ref_info.get("ppg")
                        whistle_class = ref_info.get("whistle_class", "neutral")
                        
                        # Calculate modifier based on stat type
                        whistle_modifier = ref_service.calculate_whistle_modifier(stat_type, whistle_class)
                        
                        # Track modifier applications
                        if whistle_class == "high_whistle" and whistle_modifier > 0:
                            results["whistle_matrix"]["green_light_applied"] += 1
                        elif whistle_class == "low_whistle" and whistle_modifier < 0:
                            results["whistle_matrix"]["red_light_applied"] += 1
                        else:
                            results["whistle_matrix"]["neutral"] += 1
                    
                    # Calculate Point Lift translation
                    point_lift_data = ref_service.calculate_point_lift(
                        stat_type=stat_type,
                        ref_ppg=ref_ppg or 115.5,
                        whistle_class=whistle_class,
                        player_usage_rate=None,  # TODO: Add usage rate when available
                        team_usage_avg=None
                    )
                    
                    # ---------------------------------------------------------
                    # USAGE VACUUM MODIFIER
                    # ---------------------------------------------------------
                    from services.injury_vacuum_service import get_vacuum_service
                    vacuum_service = get_vacuum_service(self.db)
                    vacuum_modifier, vacuum_data = vacuum_service.calculate_vacuum_modifier(player_name)
                    
                    if vacuum_modifier > 0:
                        results["usage_vacuum"]["beneficiaries_boosted"] = results.get("usage_vacuum", {}).get("beneficiaries_boosted", 0) + 1
                    
                    # ---------------------------------------------------------
                    # DEFENSIVE MOMENTUM MODIFIER
                    # ---------------------------------------------------------
                    opponent = player.get("opponent") or player.get("opponent_abbr")
                    momentum_modifier = 0.0
                    momentum_data = None
                    
                    if opponent:
                        momentum_modifier, momentum_data = momentum_service.calculate_momentum_modifier(
                            opponent,
                            stat_type
                        )
                        
                        if momentum_data:
                            if momentum_data.get("is_elite"):
                                results["defensive_momentum"]["elite_matchups"] += 1
                            elif momentum_data.get("is_weak"):
                                results["defensive_momentum"]["weak_matchups"] += 1
                            if momentum_data.get("trend_alert"):
                                results["defensive_momentum"]["trend_alerts"] += 1
                    
                    # FINAL POWER SCORE with all modifiers
                    power_score = round(base_power_score + whistle_modifier + vacuum_modifier + momentum_modifier, 2)
                    # Cap at 0-145 range (base max 100 + whistle 15 + vacuum 15 + momentum 15)
                    power_score = max(0, min(power_score, 145))
                    
                    # Determine tier
                    if effective_sharp <= SAFE_HAVEN_MAX:
                        tier = "safe_haven"
                        results["scored"]["safe_haven_pool"] += 1
                    elif FRONT_LINES_MIN <= effective_sharp <= FRONT_LINES_MAX:
                        tier = "front_lines"
                        results["scored"]["front_lines_pool"] += 1
                    elif WAR_ZONE_MIN <= effective_sharp <= WAR_ZONE_MAX:
                        tier = "war_zone"
                        results["scored"]["war_zone_pool"] += 1
                    else:
                        tier = "unclassified"
                    
                    # Additional stats
                    anchor_line = prop.get("anchor_line", pp_line)
                    line_delta = pp_line - anchor_line if anchor_line else 0
                    stat_values = player_l10.get(stat_type, [])
                    
                    # Build scored prop
                    scored_prop = {
                        # Player
                        "player_name": player_name,
                        "player_id": player.get("player_id"),
                        "team": player.get("team"),
                        "team_name": player.get("team_name"),
                        "photo_url": player.get("photo_url") or player.get("headshot_url"),
                        "headshot_url": player.get("headshot_url"),
                        "nba_id": player.get("nba_id"),
                        "position": player.get("position"),
                        "opponent": player.get("opponent") or player.get("opponent_abbr"),
                        "opponent_abbr": player.get("opponent_abbr"),
                        "game_time": player.get("game_time"),
                        # Prop
                        "stat_type": prop.get("stat_type"),
                        "market": prop.get("market"),
                        "direction": prop.get("direction"),
                        "line": pp_line,
                        "anchor_line": anchor_line,
                        "price": prop.get("price"),
                        # NEW: Sharp Movement Classification
                        "sharp_movement": abs(line_delta) >= 1.5 if line_delta else False,
                        "movement_delta": round(line_delta, 2) if line_delta else 0,
                        "movement_direction": "over_value" if line_delta and line_delta > 0 else "under_value" if line_delta and line_delta < 0 else "neutral",
                        "movement_strength": "significant" if abs(line_delta or 0) >= 3.0 else "moderate" if abs(line_delta or 0) >= 1.5 else "minimal",
                        "trap_risk": prop.get("sidecar", {}).get("hook_risk", False) or prop.get("sidecar", {}).get("suspect_line_bait", False),
                        "hook_risk": prop.get("sidecar", {}).get("hook_risk", False),
                        "suspect_line_bait": prop.get("sidecar", {}).get("suspect_line_bait", False),
                        # LEGACY: Backward compatibility flags (will be deprecated)
                        "is_demon": prop.get("is_demon", False),
                        "is_goblin": prop.get("is_goblin", False),
                        "is_alternate": sharp_market.get("is_alternate", False),
                        # Sharp Market
                        "sharp_price": effective_sharp,
                        "bovada_price": bovada_price,
                        "sharp_implied": round(sharp_implied * 100, 1),
                        "draftkings_price": sharp_market.get("draftkings_price"),
                        "fanduel_price": sharp_market.get("fanduel_price"),
                        # POWER SCORE BREAKDOWN
                        "edge_component": round(edge_component, 2),
                        "cushion_component": round(cushion_component, 2),
                        "consistency_component": round(consistency_component, 2),
                        "whistle_modifier": round(whistle_modifier, 1),
                        "base_power_score": round(base_power_score, 2),
                        "ferrari_power_score": power_score,
                        # WHISTLE MATRIX INFO
                        "crew_chief": crew_chief,
                        "ref_ou_pct": round(ref_ou_pct, 1) if ref_ou_pct else None,
                        "ref_ppg": round(ref_ppg, 1) if ref_ppg else None,
                        "whistle_class": whistle_class,
                        "has_whistle_modifier": whistle_modifier != 0,
                        # POINT LIFT TRANSLATION (Vegas Intel)
                        "point_lift": point_lift_data.get("point_lift", 0),
                        "lift_label": point_lift_data.get("lift_label", ""),
                        "lift_type": point_lift_data.get("lift_type", "neutral"),
                        "foul_rate_diff": point_lift_data.get("foul_rate_diff", 0),
                        # USAGE VACUUM INFO
                        "vacuum_modifier": round(vacuum_modifier, 1),
                        "has_vacuum_modifier": vacuum_modifier > 0,
                        "vacuum_data": vacuum_data,
                        # DEFENSIVE MOMENTUM INFO
                        "momentum_modifier": round(momentum_modifier, 1),
                        "has_momentum_modifier": momentum_modifier != 0,
                        "momentum_data": momentum_data,
                        # Metrics
                        "separation_pct": round(separation, 1),
                        "line_delta": round(line_delta, 1),
                        "season_median": round(season_median, 1) if season_median else None,
                        # Hit Rates
                        "l10_rate": round(l10_rate, 1),
                        "l5_rate": round(l5_rate, 1),
                        "h10_rate": round(l10_rate, 1),
                        "h5_rate": round(l5_rate, 1),
                        "l10_hits": l10_hits,
                        # Averages
                        "l5_avg": hit_rates.get("l5_avg"),
                        "l10_avg": hit_rates.get("l10_avg"),
                        "season_avg": hit_rates.get("season_avg"),
                        # L10 Stats
                        "l10_mode": round(calculate_mode(stat_values), 1) if calculate_mode(stat_values) else None,
                        "l10_median": round(calculate_median(stat_values), 1) if calculate_median(stat_values) else None,
                        "l10_mean": round(calculate_mean(stat_values), 1) if calculate_mean(stat_values) else None,
                        # Classification
                        "tier": tier,
                        "tier_label": "MINEFIELD" if (prop.get("sidecar", {}).get("hook_risk", False) or prop.get("sidecar", {}).get("suspect_line_bait", False)) else tier.upper().replace("_", "_"),
                        "pipeline": "ferrari_v6",
                        "synced_at": sync_time.isoformat()
                    }
                    
                    all_scored.append(scored_prop)
            
            results["kill_switch"]["total_killed"] = (
                results["kill_switch"]["separation_fail"] +
                results["kill_switch"]["median_fail"] +
                results["kill_switch"]["no_sharp_data"]
            )
            results["scored"]["total_survivors"] = len(all_scored)
            
            logger.info(f"  Total scanned: {results['universal_scan']['total_props_scanned']}")
            logger.info(f"  Killed: {results['kill_switch']['total_killed']}")
            logger.info(f"  Survivors scored: {len(all_scored)}")
            
            # =================================================================
            # PHASE 3: BULK WRITE (Performance Optimized)
            # =================================================================
            logger.info("[PHASE 3] BULK WRITE - Storing all scored props...")
            
            await self.ferrari_scored.delete_many({})
            if all_scored:
                # Bulk insert for performance
                await self.ferrari_scored.insert_many(all_scored, ordered=False)
            
            await self.ferrari_discarded.delete_many({})
            if discarded:
                await self.ferrari_discarded.insert_many(discarded[:500], ordered=False)
            
            # =================================================================
            # PHASE 4: GLOBAL SORT & TOP-K SELECTION
            # =================================================================
            logger.info("[PHASE 4] GLOBAL SORT - Ranking by ferrari_power_score...")
            
            used_players = set()
            
            # SAFE HAVEN: Global sort, then dedupe, then limit
            sh_cursor = self.ferrari_scored.find(
                {"tier": "safe_haven"},
                {"_id": 0}
            ).sort("ferrari_power_score", -1)
            sh_all = await sh_cursor.to_list(length=None)
            top_safe_haven = self._dedupe_select(sh_all, used_players, MAX_PICKS_PER_TIER)
            
            # FRONT LINES: Exclude Safe Haven players
            fl_cursor = self.ferrari_scored.find(
                {"tier": "front_lines"},
                {"_id": 0}
            ).sort("ferrari_power_score", -1)
            fl_all = await fl_cursor.to_list(length=None)
            top_front_lines = self._dedupe_select(fl_all, used_players, MAX_PICKS_PER_TIER)
            
            # WAR ZONE: Exclude Safe Haven + Front Lines players
            wz_cursor = self.ferrari_scored.find(
                {"tier": "war_zone"},
                {"_id": 0}
            ).sort("ferrari_power_score", -1)
            wz_all = await wz_cursor.to_list(length=None)
            top_war_zone = self._dedupe_select(wz_all, used_players, MAX_PICKS_PER_TIER)
            
            # =================================================================
            # PHASE 5: STORE FINAL SELECTIONS
            # =================================================================
            logger.info("[PHASE 5] FINAL SELECTION - Storing Top-10 per tier...")
            
            await self.ferrari_safe_haven.delete_many({})
            if top_safe_haven:
                await self.ferrari_safe_haven.insert_many(top_safe_haven)
            
            await self.ferrari_front_lines.delete_many({})
            if top_front_lines:
                await self.ferrari_front_lines.insert_many(top_front_lines)
            
            await self.ferrari_war_zone.delete_many({})
            if top_war_zone:
                await self.ferrari_war_zone.insert_many(top_war_zone)
            
            # Update results
            results["output"]["safe_haven"] = len(top_safe_haven)
            results["output"]["front_lines"] = len(top_front_lines)
            results["output"]["war_zone"] = len(top_war_zone)
            results["output"]["total"] = len(top_safe_haven) + len(top_front_lines) + len(top_war_zone)
            
            # VERIFICATION MESSAGE
            results["verification"]["active_props_verified"] = results["scored"]["total_survivors"]
            results["verification"]["elite_opportunities"] = results["output"]["total"]
            results["verification"]["message"] = (
                f"Verified {results['scored']['total_survivors']} active props "
                f"to identify these {results['output']['total']} Elite opportunities."
            )
            
            logger.info("=" * 70)
            logger.info("[FERRARI v6] PIPELINE COMPLETE")
            logger.info(f"  {results['verification']['message']}")
            logger.info("=" * 70)
            
        except Exception as e:
            logger.error(f"[v6] Pipeline error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results["success"] = False
            results["error"] = str(e)
        
        return results
    
    def _dedupe_select(
        self,
        candidates: List[Dict],
        used_players: set,
        limit: int
    ) -> List[Dict]:
        """Deduplicate and select top N by power score."""
        selected = []
        for pick in candidates:
            name = pick.get("player_name")
            if name and name not in used_players:
                used_players.add(name)
                selected.append(pick)
                if len(selected) >= limit:
                    break
        return selected
    
    # =========================================================================
    # GETTER METHODS
    # =========================================================================
    
    async def _get_verification_stats(self) -> Dict[str, Any]:
        """Get verification stats for Market Intel footer."""
        total = await self.ferrari_scored.count_documents({})
        sh = await self.ferrari_scored.count_documents({"tier": "safe_haven"})
        fl = await self.ferrari_scored.count_documents({"tier": "front_lines"})
        wz = await self.ferrari_scored.count_documents({"tier": "war_zone"})
        
        return {
            "active_props_verified": total,
            "safe_haven_pool": sh,
            "front_lines_pool": fl,
            "war_zone_pool": wz
        }
    
    async def get_safe_haven(self, limit: int = 10) -> Dict[str, Any]:
        cursor = self.ferrari_safe_haven.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        stats = await self._get_verification_stats()
        
        return {
            "tier": "safe_haven",
            "picks": picks,
            "count": len(picks),
            "window": f"Sharp <= {SAFE_HAVEN_MAX}",
            "pool_size": stats["safe_haven_pool"],
            "verification": stats
        }
    
    async def get_front_lines(self, limit: int = 10) -> Dict[str, Any]:
        cursor = self.ferrari_front_lines.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        stats = await self._get_verification_stats()
        
        return {
            "tier": "front_lines",
            "picks": picks,
            "count": len(picks),
            "window": f"Sharp {FRONT_LINES_MIN} to {FRONT_LINES_MAX}",
            "pool_size": stats["front_lines_pool"],
            "verification": stats
        }
    
    async def get_war_zone(self, limit: int = 10) -> Dict[str, Any]:
        cursor = self.ferrari_war_zone.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        stats = await self._get_verification_stats()
        
        return {
            "tier": "war_zone",
            "picks": picks,
            "count": len(picks),
            "window": f"Sharp {WAR_ZONE_MIN} to +{WAR_ZONE_MAX}",
            "pool_size": stats["war_zone_pool"],
            "verification": stats
        }
    
    async def get_discarded(self, limit: int = 50) -> Dict[str, Any]:
        cursor = self.ferrari_discarded.find({}, {"_id": 0}).limit(limit)
        discarded = await cursor.to_list(length=limit)
        total = await self.ferrari_discarded.count_documents({})
        
        return {
            "discarded": discarded,
            "count": len(discarded),
            "total_discarded": total,
            "kill_switch": "5% Implied Probability Separation + Median Anchor"
        }


# Singleton
_ferrari_service = None

def get_ferrari_tier_service(db=None):
    global _ferrari_service
    if _ferrari_service is None and db is not None:
        _ferrari_service = FerrariTierService(db)
    return _ferrari_service
