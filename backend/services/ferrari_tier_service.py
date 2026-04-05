"""
PropVision v7 Pipeline - True Probability & Diversified Parlay Optimizer
=========================================================================
"Verified [X] active props to identify [30] Elite picks + [15] Optimized Parlays."

MISSION: Extract every possible 0.5% of edge through mathematical precision.

TRUE PROBABILITY FORMULA (0-100%):
  True_Prob = (Historical × 0.45) + (Sharp × 0.25) + (Floor × 0.15) + (Context × 0.15)
  
  HISTORICAL CONSISTENCY (45%):
    = (L3 × 0.40) + (L5 × 0.35) + (L10 × 0.25)
    
  SHARP MARKET SIGNAL (25%):
    = Sharp_Implied × Separation_Confidence_Multiplier
    
  STATISTICAL FLOOR (15%):
    = Cushion + Mode_Proximity - Variance_Penalty
    
  CONTEXTUAL MODIFIERS (15%):
    = DvP(+/-8) + Whistle(+/-5) + Vacuum(+/-5) + Blowout(-10)

HARD KILLS:
  - L3 < 33% (cold streak)
  - L5 < 40% (confirmed cold)
  - Sharp < 52% (no edge)
  - Line > Season Median
  - Blowout HIGH + PTS/PRA

TIER CLASSIFICATION (by True Probability):
  - Safe Haven: >= 72% (Elite locks)
  - Front Lines: 62-71% (Strong plays)
  - War Zone: 52-61% (Value bets)

PARLAY OPTIMIZER:
  - 5 parlays per tier (2-leg through 6-leg)
  - Max 2 appearances per player per tier
  - Max 2 picks per team per parlay
  - Max 3 picks per stat type per parlay
"""
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
from collections import Counter
import logging
import math

from services.standings_service import StandingsService
from services.propvision_v7_engine import (
    TrueProbabilityEngine,
    DiversifiedParlayOptimizer,
    calculate_granular_hit_rates,
    calculate_median,
    calculate_mode,
    calculate_std_dev
)

logger = logging.getLogger(__name__)

# ========== TEAM PACE DEFAULTS (possessions per game) ==========
TEAM_PACE_DEFAULTS = {
    "IND": 103.5, "ATL": 102.8, "MIL": 101.2, "SAC": 100.9, "MIN": 100.5,
    "DEN": 99.8, "LAL": 99.5, "BOS": 99.2, "PHX": 99.0, "DAL": 98.8,
    "NOP": 98.5, "GSW": 98.2, "CHI": 98.0, "CHA": 97.8, "POR": 97.5,
    "BKN": 97.2, "HOU": 97.0, "TOR": 96.8, "ORL": 96.5, "WAS": 96.2,
    "DET": 96.0, "PHI": 95.8, "OKC": 95.5, "SAS": 95.2, "CLE": 95.0,
    "MIA": 94.8, "NYK": 94.5, "LAC": 94.2, "MEM": 93.8, "UTA": 93.5
}
DEFAULT_SEASON_PACE = 97.5

# =============================================================================
# PROPVISION v7 CONSTANTS
# =============================================================================

# PRIZEPICKS IMPLIED PROBABILITY (-137)
PP_IMPLIED = 0.578  # 57.8%

# V7 TIER THRESHOLDS (True Probability %)
TIER_SAFE_HAVEN_MIN = 72.0
TIER_FRONT_LINES_MIN = 62.0
TIER_WAR_ZONE_MIN = 47.0  # Lowered for Demons only (must have multiplier)

# HARD KILL THRESHOLDS
HARD_KILL_L3_MIN = 33.0
HARD_KILL_L5_MIN = 40.0
HARD_KILL_SHARP_MIN = 52.0
HARD_KILL_SEPARATION_MIN = 3.0

# OUTPUT CAPS
MAX_PICKS_PER_TIER = 10
MAX_PARLAYS_PER_TIER = 5

# Legacy constants (for backwards compatibility in tier classification)
SAFE_HAVEN_MAX = -250
FRONT_LINES_MIN = -245
FRONT_LINES_MAX = -115
WAR_ZONE_MIN = -114
WAR_ZONE_MAX = 500


# =============================================================================
# MATHEMATICAL FUNCTIONS (V7 uses propvision_v7_engine, these kept for compatibility)
# =============================================================================

def american_to_implied(odds: int) -> float:
    """Convert American odds to implied probability (0-1)."""
    if odds is None:
        return 0.0
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    return 100 / (odds + 100)


def calculate_separation_pct(sharp_implied: float, pp_implied: float = PP_IMPLIED) -> float:
    """Calculate separation as percentage points difference."""
    return abs(sharp_implied - pp_implied) * 100


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
        # BDL is the SSOT for all player stats and game logs
        self.master_hub = db.nba_master_hub_2026
        
        # Output collections
        self.ferrari_safe_haven = db.ferrari_safe_haven
        self.ferrari_front_lines = db.ferrari_front_lines
        self.ferrari_war_zone = db.ferrari_war_zone
        self.ferrari_discarded = db.ferrari_discarded
        self.ferrari_scored = db.ferrari_scored
        
        # Referee service for Whistle Matrix
        self._referee_service = None
        
        # BDL player context cache (loaded once per pipeline run)
        self._bdl_player_cache = {}
    
    def _calculate_hit_rates_from_bdl(self, game_logs: List[Dict], stat_type: str, line: float) -> Dict[str, Any]:
        """
        Calculate hit rates from BDL game logs - THE SINGLE SOURCE OF TRUTH.
        
        Args:
            game_logs: List of BDL game log entries (sorted newest first)
            stat_type: The stat type (PTS, REB, AST, PRA, etc.)
            line: The prop line to check against
            
        Returns:
            Dict with l3_rate, l5_rate, l10_rate, l3_hits, l5_hits, l10_hits, avg, values
        """
        if not game_logs:
            return None
        
        # Map stat types to BDL field names
        stat_field_map = {
            "PTS": "pts",
            "REB": "reb",
            "AST": "ast",
            "STL": "stl",
            "BLK": "blk",
            "TO": "turnover",
            "3PM": "fg3m",
            "PRA": ["pts", "reb", "ast"],  # Combined
            "PR": ["pts", "reb"],
            "PA": ["pts", "ast"],
            "RA": ["reb", "ast"],
            "BLST": ["blk", "stl"],
            "FG": "fgm",
            "FGA": "fga",
            "FTM": "ftm",
            "FTA": "fta",
            "MIN": "min"
        }
        
        field = stat_field_map.get(stat_type.upper())
        if not field:
            return None
        
        # Extract stat values from game logs
        values = []
        for game in game_logs[:10]:  # Only look at last 10 games
            if isinstance(field, list):
                # Combined stat (PRA, PR, PA, RA, BLST)
                total = sum(game.get(f, 0) or 0 for f in field)
                values.append(total)
            else:
                val = game.get(field, 0)
                if val is not None:
                    values.append(val)
        
        if not values:
            return None
        
        # Calculate hits for L3, L5, L10
        l3_values = values[:3]
        l5_values = values[:5]
        l10_values = values[:10]
        
        l3_hits = sum(1 for v in l3_values if v > line)
        l5_hits = sum(1 for v in l5_values if v > line)
        l10_hits = sum(1 for v in l10_values if v > line)
        
        l3_rate = (l3_hits / len(l3_values) * 100) if l3_values else 0
        l5_rate = (l5_hits / len(l5_values) * 100) if l5_values else 0
        l10_rate = (l10_hits / len(l10_values) * 100) if l10_values else 0
        
        avg = sum(values) / len(values) if values else 0
        
        return {
            "l3_rate": round(l3_rate, 1),
            "l5_rate": round(l5_rate, 1),
            "l10_rate": round(l10_rate, 1),
            "l3_hits": l3_hits,
            "l5_hits": l5_hits,
            "l10_hits": l10_hits,
            "l3_total": len(l3_values),
            "l5_total": len(l5_values),
            "l10_total": len(l10_values),
            "avg": round(avg, 1),
            "values": values,
            "source": "bdl_game_logs"
        }
    
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
        """Load season median for each player/stat from BDL game logs."""
        medians = {}
        
        try:
            # Use BDL master hub for season medians
            cursor = self.master_hub.find(
                {},
                {"_id": 0, "display_name": 1, "bdl_game_logs": 1}
            )
            async for doc in cursor:
                player_name = doc.get("display_name")
                games = doc.get("bdl_game_logs", [])
                
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
            logger.error(f"[BDL-SSOT] Season median load error: {e}")
        
        return medians
    
    async def _load_player_context_data(self) -> Dict[str, Dict[str, Any]]:
        """
        Load player context data from master_hub for badge resolution.
        Returns: {player_name: {"game_logs": [...], "baseline_stats": {...}}}
        """
        context_data = {}
        
        try:
            master_hub = self.db.nba_master_hub_2026
            cursor = master_hub.find(
                {},
                {"_id": 0, "display_name": 1, "bdl_game_logs": 1, "game_logs": 1, "baseline_stats": 1}
            )
            
            async for doc in cursor:
                player_name = doc.get("display_name")
                if not player_name:
                    continue
                
                # BDL game logs are the ONLY source
                game_logs = doc.get("bdl_game_logs") or []
                baseline_stats = doc.get("baseline_stats", {})
                
                context_data[player_name] = {
                    "game_logs": game_logs,
                    "baseline_stats": baseline_stats
                }
            
            logger.info(f"[BDL-SSOT] Loaded context data for {len(context_data)} players from nba_master_hub_2026")
            
        except Exception as e:
            logger.error(f"[v6] Player context data load error: {e}")
        
        return context_data
    
    async def build_ferrari_tiers(self, sync_time: datetime) -> Dict[str, Any]:
        """
        Execute PropVision v7 Pipeline - True Probability & Diversified Parlays
        
        1. Sync referee data (Whistle Matrix)
        2. Universal Scan (100% coverage)
        3. TRUE PROBABILITY calculation using V7 engine
        4. Hard/Soft Kill filtering
        5. Tier classification by True Probability
        6. Parlay generation with diversification constraints
        7. Top-K selection + Top-5 parlays per tier
        """
        logger.info("=" * 70)
        logger.info("[PROPVISION v7] TRUE PROBABILITY & DIVERSIFIED PARLAYS")
        logger.info("=" * 70)
        
        results = {
            "success": True,
            "synced_at": sync_time.isoformat(),
            "pipeline": "PropVision v7 - True Probability",
            "universal_scan": {
                "total_props_scanned": 0,
                "players_processed": 0
            },
            "v7_kills": {
                "hard_kills": 0,
                "soft_penalties_applied": 0,
                "l3_cold_streak": 0,
                "l5_confirmed_cold": 0,
                "no_sharp_edge": 0,
                "line_above_median": 0,
                "blowout_bench_risk": 0,
                "no_sharp_data": 0,
                "no_hit_rate_data": 0
            },
            "scored": {
                "total_survivors": 0,
                "safe_haven_pool": 0,
                "front_lines_pool": 0,
                "war_zone_pool": 0,
                "below_threshold": 0
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
                "total_picks": 0,
                "total_parlays": 0
            },
            "parlays": {
                "safe_haven": 0,
                "front_lines": 0,
                "war_zone": 0
            },
            "verification": {
                "active_props_verified": 0,
                "elite_opportunities": 0,
                "optimized_parlays": 0,
                "message": ""
            }
        }
        
        # Initialize V7 True Probability Engine
        v7_engine = TrueProbabilityEngine()
        
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
            # PHASE 1: BDL SSOT - Load ALL Data from BallDontLie
            # =================================================================
            logger.info("[PHASE 1] BDL SSOT - Loading complete dataset from BallDontLie...")
            
            season_medians = await self._load_season_medians()
            
            # Load BDL player context (game logs, stats) - THE SINGLE SOURCE OF TRUTH
            player_context_data = await self._load_player_context_data()
            self._bdl_player_cache = player_context_data  # Cache for hit rate calculations
            logger.info(f"[BDL-SSOT] Loaded {len(self._bdl_player_cache)} players from BDL")
            
            # Get ALL players - NO LIMIT
            cursor = self.cached_board.find({}, {"_id": 0})
            players = await cursor.to_list(length=None)
            
            results["universal_scan"]["players_processed"] = len(players)
            
            logger.info(f"  Players loaded: {len(players)}")
            logger.info(f"  Season medians: {len(season_medians)} players")
            logger.info(f"  BDL context data: {len(player_context_data)} players")
            
            # =================================================================
            # PHASE 2: V7 TRUE PROBABILITY CALCULATION
            # =================================================================
            logger.info("[PHASE 2] V7 TRUE PROBABILITY - Calculating for ALL props...")
            
            all_scored = []
            discarded = []
            
            for player in players:
                player_name = player.get("player_name", "")
                player_medians = season_medians.get(player_name, {})
                
                # Get BDL context data for badge resolution and hit rates
                ctx_data = player_context_data.get(player_name, {})
                player_game_logs = ctx_data.get("game_logs", [])
                player_baseline_stats = ctx_data.get("baseline_stats", {})
                
                # Resolve player-level context badges ONCE per player (not per prop)
                player_context_badges = await self._resolve_player_context_badges(
                    player_name, player_game_logs, player_baseline_stats
                )
                
                for prop in player.get("props", []):
                    results["universal_scan"]["total_props_scanned"] += 1
                    
                    # Get sharp market data
                    sharp_market = prop.get("sharp_market", {})
                    sharp_price = sharp_market.get("sharp_price")
                    bovada_price = sharp_market.get("bovada_price")
                    
                    # Use Bovada if available, otherwise sharp_price
                    effective_sharp = bovada_price if bovada_price else sharp_price
                    
                    # Get prop type first to check if we should skip
                    is_demon = prop.get("is_demon", False)
                    is_goblin = prop.get("is_goblin", False)
                    
                    # For non-demons without sharp data, skip
                    # Demons can proceed without sharp data (use hit rates instead)
                    if effective_sharp is None and not is_demon:
                        results["v7_kills"]["no_sharp_data"] += 1
                        continue
                    
                    # Calculate implied probabilities (0 if no sharp data for demons)
                    sharp_implied = american_to_implied(effective_sharp) if effective_sharp else 0
                    
                    # Get prop details
                    stat_type = prop.get("stat_type", "").upper()
                    pp_line = prop.get("line", 0)
                    season_median = player_medians.get(stat_type)
                    
                    # ---------------------------------------------------------
                    # BDL SSOT: Calculate hit rates from BDL game logs ONLY
                    # No fallbacks - BDL is the single source of truth
                    # ---------------------------------------------------------
                    player_name_lookup = player.get("player_name", "") or player.get("name", "")
                    bdl_context = self._bdl_player_cache.get(player_name_lookup, {})
                    bdl_game_logs = bdl_context.get("game_logs", [])
                    
                    # Calculate hit rates from BDL game logs
                    bdl_hr = self._calculate_hit_rates_from_bdl(bdl_game_logs, stat_type, pp_line)
                    
                    if bdl_hr:
                        l3_rate = bdl_hr.get("l3_rate")
                        l5_rate = bdl_hr.get("l5_rate", 0)
                        l10_rate = bdl_hr.get("l10_rate", 0)
                        l3_hits = bdl_hr.get("l3_hits", 0)
                        l5_hits = bdl_hr.get("l5_hits", 0)
                        l10_hits = bdl_hr.get("l10_hits", 0)
                        stat_values = bdl_hr.get("values", [])
                        season_avg = bdl_hr.get("avg", 0)  # Player's season average for this stat
                    else:
                        # No BDL data available - skip this prop
                        results["v7_kills"]["no_hit_rate_data"] += 1
                        continue
                    
                    # Calculate separation percentage
                    separation = calculate_separation_pct(sharp_implied)
                    
                    # ---------------------------------------------------------
                    # WHISTLE MATRIX INFO (needed for V7 context)
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
                        whistle_modifier = ref_service.calculate_whistle_modifier(stat_type, whistle_class)
                        
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
                        player_usage_rate=None,
                        team_usage_avg=None
                    )
                    
                    # ---------------------------------------------------------
                    # USAGE VACUUM MODIFIER
                    # ---------------------------------------------------------
                    from services.injury_vacuum_service import get_vacuum_service
                    vacuum_service = get_vacuum_service(self.db)
                    vacuum_modifier, vacuum_data = vacuum_service.calculate_vacuum_modifier(player_name)
                    
                    if vacuum_modifier > 0:
                        results["usage_vacuum"]["beneficiaries_boosted"] += 1
                    
                    # ---------------------------------------------------------
                    # BLOWOUT RISK CALCULATION
                    # ---------------------------------------------------------
                    opponent = player.get("opponent") or player.get("opponent_abbr")
                    player_team = player.get("team", "")
                    blowout_risk_data = None
                    
                    if opponent and player_team:
                        try:
                            blowout_risk_data = await StandingsService.calculate_blowout_risk(
                                player_team, opponent
                            )
                        except Exception as e:
                            logger.warning(f"[v7] Blowout risk failed for {player_team} vs {opponent}: {e}")
                            blowout_risk_data = {"risk_level": "UNKNOWN", "warning": None}
                    else:
                        blowout_risk_data = {"risk_level": "UNKNOWN", "warning": None}
                    
                    blowout_risk = blowout_risk_data.get("risk_level", "UNKNOWN") if blowout_risk_data else "UNKNOWN"
                    
                    # ---------------------------------------------------------
                    # DEFENSIVE MOMENTUM
                    # ---------------------------------------------------------
                    momentum_modifier = 0.0
                    momentum_data = None
                    dvp_rank = None
                    is_elite_defense = False
                    is_weak_defense = False
                    
                    if opponent:
                        momentum_modifier, momentum_data = momentum_service.calculate_momentum_modifier(
                            opponent, stat_type
                        )
                        
                        if momentum_data:
                            dvp_rank = momentum_data.get("composite_rank")
                            is_elite_defense = momentum_data.get("is_elite", False)
                            is_weak_defense = momentum_data.get("is_weak", False)
                            
                            if is_elite_defense:
                                results["defensive_momentum"]["elite_matchups"] += 1
                            elif is_weak_defense:
                                results["defensive_momentum"]["weak_matchups"] += 1
                            if momentum_data.get("trend_alert"):
                                results["defensive_momentum"]["trend_alerts"] += 1
                    
                    # ---------------------------------------------------------
                    # V7 TRUE PROBABILITY CALCULATION
                    # ---------------------------------------------------------
                    # Calculate L10 statistical metrics FIRST (needed for trap detection)
                    l10_median = calculate_median(stat_values) if stat_values else None
                    l10_mode = calculate_mode(stat_values) if stat_values else None
                    l10_std_dev = calculate_std_dev(stat_values) if stat_values else 0.0
                    
                    # Run refined Hook/Bait detection (Mode-based, not just .5 lines)
                    sidecar = prop.get("sidecar", {})
                    
                    # Check if we have refined sidecar data with Mode analysis
                    has_refined_sidecar = sidecar.get("mode") is not None or sidecar.get("mode_frequency_pct") is not None
                    
                    if has_refined_sidecar:
                        # Use existing refined analysis
                        hook_risk = sidecar.get("hook_risk", False)
                        suspect_bait = sidecar.get("suspect_line_bait", False)
                    else:
                        # Run refined detection inline using HookBaitDetector logic
                        # Calculate mode with frequency from L10 stats
                        if stat_values and len(stat_values) >= 5:
                            from collections import Counter
                            rounded_vals = [round(v * 2) / 2 for v in stat_values[:20]]
                            freq = Counter(rounded_vals)
                            if freq:
                                mode_val, mode_count = freq.most_common(1)[0]
                                sample_size = len(rounded_vals)
                                mode_freq_pct = mode_count / sample_size
                                
                                # Hook Risk: Mode >= 25% frequency AND line within ±0.5 of Mode
                                hook_risk = (mode_freq_pct >= 0.25 and abs(pp_line - mode_val) <= 0.5)
                                
                                # Suspect Bait: Line significantly below median
                                if l10_median and l10_median >= 10:
                                    # High volume: 1.5 SD below median
                                    suspect_bait = (l10_std_dev and pp_line <= (l10_median - 1.5 * l10_std_dev) and (l10_median - pp_line) >= 3)
                                elif l10_median and l10_median >= 4:
                                    # Mid volume: 1.5 pts below median
                                    suspect_bait = (l10_median - pp_line) >= 1.5
                                elif l10_median:
                                    # Micro volume: 1.0 pts below median
                                    suspect_bait = (l10_median - pp_line) >= 1.0
                                else:
                                    suspect_bait = False
                            else:
                                hook_risk = False
                                suspect_bait = False
                        else:
                            hook_risk = False
                            suspect_bait = False
                    
                    trap_risk = hook_risk or suspect_bait
                    
                    # Get PP price for War Zone criteria (is_demon already set above)
                    pp_price = prop.get("price")
                    
                    v7_result = v7_engine.calculate_true_probability(
                        # Historical
                        l3_rate=l3_rate,
                        l5_rate=l5_rate,
                        l10_rate=l10_rate,
                        # Sharp
                        sharp_implied=sharp_implied,
                        separation_pct=separation,
                        # Statistical
                        line=pp_line,
                        median=l10_median,
                        mode=l10_mode,
                        std_dev=l10_std_dev,
                        season_median=season_median,
                        season_avg=season_avg,  # For line below avg bonus
                        # Context
                        dvp_rank=dvp_rank,
                        is_elite_defense=is_elite_defense,
                        is_weak_defense=is_weak_defense,
                        whistle_class=whistle_class,
                        vacuum_modifier=vacuum_modifier,
                        blowout_risk=blowout_risk,
                        stat_type=stat_type,
                        trap_risk=trap_risk,
                        # War Zone criteria
                        is_demon=is_demon,
                        pp_price=pp_price
                    )
                    
                    # Check if killed by V7 hard kills
                    if v7_result["is_killed"]:
                        kill_reason = v7_result["kill_reason"] or "Unknown"
                        discarded.append({
                            "player_name": player_name,
                            "stat_type": stat_type,
                            "line": pp_line,
                            "reason": kill_reason
                        })
                        results["v7_kills"]["hard_kills"] += 1
                        
                        # Track specific kill reasons
                        if "L3" in kill_reason:
                            results["v7_kills"]["l3_cold_streak"] += 1
                        elif "L5" in kill_reason:
                            results["v7_kills"]["l5_confirmed_cold"] += 1
                        elif "Sharp" in kill_reason:
                            results["v7_kills"]["no_sharp_edge"] += 1
                        elif "Median" in kill_reason:
                            results["v7_kills"]["line_above_median"] += 1
                        elif "blowout" in kill_reason.lower():
                            results["v7_kills"]["blowout_bench_risk"] += 1
                        
                        continue
                    
                    # Track soft penalties
                    if v7_result["soft_penalties"]:
                        results["v7_kills"]["soft_penalties_applied"] += 1
                    
                    # Get V7 outputs
                    true_probability = v7_result["true_probability"]
                    board_score = v7_result.get("board_score", true_probability)
                    pp_edge = v7_result.get("pp_edge", 0)
                    v7_tier = v7_result["tier"]
                    v7_confidence = v7_result["confidence"]
                    v7_components = v7_result["components"]
                    
                    # ==========================================================
                    # TIER PROP TYPE ENFORCEMENT:
                    # - SAFE HAVEN = Goblins only (high probability, no multiplier)
                    # - FRONT LINES = Both demons + goblins
                    # - WAR ZONE = Demons only (must have multiplier for the risk)
                    # ==========================================================
                    is_demon = prop.get("is_demon", False)
                    is_goblin = prop.get("is_goblin", False)
                    
                    # Skip standard lines entirely - only demons and goblins qualify
                    if not is_demon and not is_goblin:
                        results["scored"]["below_threshold"] += 1
                        discarded.append({
                            "player_name": player_name,
                            "stat_type": stat_type,
                            "line": pp_line,
                            "reason": "STANDARD_LINE: Only demons/goblins qualify for tiers"
                        })
                        continue
                    
                    # Safe Haven = Goblins only
                    if v7_tier == "safe_haven" and not is_goblin:
                        # Demons don't belong in Safe Haven - demote to Front Lines or below
                        if is_demon:
                            v7_tier = "front_lines"  # Demons can go to Front Lines
                    
                    # War Zone = Demons only (at 47%+ threshold)
                    if v7_tier == "war_zone" and not is_demon:
                        results["scored"]["below_threshold"] += 1
                        discarded.append({
                            "player_name": player_name,
                            "stat_type": stat_type,
                            "line": pp_line,
                            "reason": "WAR_ZONE_NON_DEMON: Only demons qualify for War Zone"
                        })
                        continue
                    
                    # Track tier pools
                    if v7_tier == "safe_haven":
                        results["scored"]["safe_haven_pool"] += 1
                    elif v7_tier == "front_lines":
                        results["scored"]["front_lines_pool"] += 1
                    elif v7_tier == "war_zone":
                        results["scored"]["war_zone_pool"] += 1
                    else:
                        results["scored"]["below_threshold"] += 1
                        # Skip picks below War Zone threshold
                        continue
                    
                    # Additional stats
                    anchor_line = prop.get("anchor_line", pp_line)
                    line_delta = pp_line - anchor_line if anchor_line else 0
                    hit_rates = prop.get("hit_rates", {})
                    
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
                        "direction": "Over",  # PrizePicks always uses Over
                        "line": pp_line,
                        "anchor_line": anchor_line,
                        "price": prop.get("price"),
                        # Sharp Movement Classification
                        "sharp_movement": abs(line_delta) >= 1.5 if line_delta else False,
                        "movement_delta": round(line_delta, 2) if line_delta else 0,
                        "movement_direction": "over_value" if line_delta and line_delta > 0 else "under_value" if line_delta and line_delta < 0 else "neutral",
                        "movement_strength": "significant" if abs(line_delta or 0) >= 3.0 else "moderate" if abs(line_delta or 0) >= 1.5 else "minimal",
                        # REFINED TRAP RISK (Mode-based, not just .5 lines)
                        "trap_risk": trap_risk,
                        "hook_risk": hook_risk,
                        "suspect_line_bait": suspect_bait,
                        # LEGACY: Backward compatibility flags
                        "is_demon": prop.get("is_demon", False),
                        "is_goblin": prop.get("is_goblin", False),
                        "is_alternate": sharp_market.get("is_alternate", False),
                        # Sharp Market
                        "sharp_price": effective_sharp,
                        "bovada_price": bovada_price,
                        "sharp_implied": round(sharp_implied * 100, 1),
                        "draftkings_price": sharp_market.get("draftkings_price"),
                        "fanduel_price": sharp_market.get("fanduel_price"),
                        # =====================================================
                        # V7.1 BOARD SCORE (EDGE-FIRST FORMULA)
                        # Board_Score = Sharp_Implied + PP_Edge + Hit_Rate_Avg - Penalties
                        # =====================================================
                        "true_probability": true_probability,
                        "board_score": board_score,
                        "pp_edge": pp_edge,
                        "v7_confidence": v7_confidence,
                        "v7_components": v7_components,
                        "components": v7_components,  # Also store as 'components' for easier access
                        "v7_soft_penalties": v7_result.get("soft_penalties", []),
                        # Board score is the primary sort field
                        "ferrari_power_score": board_score,
                        # V7 HIT RATE BREAKDOWN (L3/L5/L10)
                        "l3_rate": round(l3_rate, 1) if l3_rate is not None else None,
                        "l3_hits": l3_hits,
                        "l5_rate": round(l5_rate, 1) if l5_rate is not None else 0,
                        "l5_hits": l5_hits,
                        "l10_rate": round(l10_rate, 1) if l10_rate is not None else 0,
                        "l10_hits": l10_hits,
                        "h10_rate": round(l10_rate, 1) if l10_rate is not None else 0,  # Legacy alias
                        "h5_rate": round(l5_rate, 1) if l5_rate is not None else 0,    # Legacy alias
                        # WHISTLE MATRIX INFO
                        "whistle_modifier": round(whistle_modifier, 1),
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
                        # Averages
                        "l5_avg": hit_rates.get("l5_avg"),
                        "l10_avg": hit_rates.get("l10_avg"),
                        "season_avg": season_avg,  # From BDL game logs
                        # L10 Stats
                        "l10_mode": round(l10_mode, 1) if l10_mode else None,
                        "l10_median": round(l10_median, 1) if l10_median else None,
                        "l10_mean": round(calculate_mean(stat_values), 1) if calculate_mean(stat_values) else None,
                        "l10_std_dev": round(l10_std_dev, 2) if l10_std_dev else None,
                        # Classification (V7 tier based on True Probability)
                        "tier": v7_tier,
                        "tier_label": v7_tier.upper().replace("_", " "),
                        "pipeline": "propvision_v7",
                        "synced_at": sync_time.isoformat(),
                        # ==============================================
                        # TOP-LEVEL ENRICHMENT (for frontend compatibility)
                        # ==============================================
                        # NOTE: momentum_data is already at prop level (line 775)
                        # Active badges (merged prop + player badges)
                        "active_badges": list(set(
                            self._build_prop_badges(
                                blowout_risk_data.get("risk_level") if blowout_risk_data else None,
                                prop.get("sidecar", {}).get("hook_risk", False),
                                prop.get("sidecar", {}).get("suspect_line_bait", False),
                                abs(line_delta) >= 1.5 if line_delta else False,
                                momentum_data
                            ) + player_context_badges
                        )),
                        # =====================================================
                        # INTEL SUITE: Complete Vision Intelligence Package
                        # =====================================================
                        "intel_suite": {
                            # Blowout Risk Analysis (calculated from StandingsService)
                            "blowout_risk": {
                                "risk_level": blowout_risk_data.get("risk_level", "UNKNOWN") if blowout_risk_data else "UNKNOWN",
                                "player_team_record": blowout_risk_data.get("player_team_record", "") if blowout_risk_data else "",
                                "opponent_team_record": blowout_risk_data.get("opponent_team_record", "") if blowout_risk_data else "",
                                "win_pct_diff": blowout_risk_data.get("win_pct_diff") if blowout_risk_data else None,
                                "risk_reason": blowout_risk_data.get("risk_reason") if blowout_risk_data else None,
                                "warning": blowout_risk_data.get("warning") if blowout_risk_data else None
                            },
                            # Matchup DvP Analysis (Defensive Momentum)
                            "matchup_dvp": {
                                "display": f"vs {opponent}" if opponent else "TBD",
                                "opponent": opponent,
                                "opponent_abbr": player.get("opponent_abbr"),
                                "friction_level": "Low" if momentum_data and momentum_data.get("is_weak") else "High" if momentum_data and momentum_data.get("is_elite") else "Medium",
                                "friction_label": f"Rank #{int(momentum_data.get('composite_rank', 15))}" if momentum_data and momentum_data.get("composite_rank") else "Unknown",
                                "color": "green" if momentum_data and momentum_data.get("is_weak") else "red" if momentum_data and momentum_data.get("is_elite") else "yellow",
                                "dvp_rank": momentum_data.get("composite_rank") if momentum_data else None,
                                "stat_type": stat_type
                            },
                            # ===== DEFENSIVE MOMENTUM (replaces old DVP) =====
                            "defensive_momentum": {
                                "composite_rank": momentum_data.get("composite_rank") if momentum_data else None,
                                "season_rank": momentum_data.get("season_rank") if momentum_data else None,
                                "l10_rank": momentum_data.get("l10_rank") if momentum_data else None,
                                "l5_rank": momentum_data.get("l5_rank") if momentum_data else None,
                                "momentum_trend": momentum_data.get("momentum", "stable") if momentum_data else "unknown",
                                "is_elite": momentum_data.get("is_elite", False) if momentum_data else False,
                                "is_weak": momentum_data.get("is_weak", False) if momentum_data else False,
                                "trend_alert": momentum_data.get("trend_alert") if momentum_data else None,
                                "tooltip": momentum_data.get("tooltip") if momentum_data else f"Defensive data pending for {opponent}",
                                "display": f"#{int(momentum_data.get('composite_rank', 15))} Defense" if momentum_data and momentum_data.get("composite_rank") else f"vs {opponent}" if opponent else "Unknown",
                                "data_available": momentum_data is not None
                            },
                            # ===== TEMPO (Pace Delta) =====
                            "tempo": self._calculate_tempo(player.get("team"), opponent),
                            # Pace Delta (legacy - keep for compatibility)
                            "pace_delta": self._calculate_tempo(player.get("team"), opponent),
                            # ===== VARIANCE (Stability Index) =====
                            "variance": self._calculate_variance(stat_values, l10_rate, l5_rate),
                            # Stability Index from hit rates
                            "stability_index": {
                                "display": f"{int((l10_rate + l5_rate) / 2)}%",
                                "score": int((l10_rate + l5_rate) / 2),
                                "consistency": "Consistent" if (l10_rate + l5_rate) / 2 >= 70 else "Variable" if (l10_rate + l5_rate) / 2 >= 50 else "Volatile",
                                "std_dev": self._calculate_std_dev(stat_values),
                                "variance_level": "Low" if (l10_rate + l5_rate) / 2 >= 70 else "Medium" if (l10_rate + l5_rate) / 2 >= 45 else "High"
                            },
                            # Usage Ripple from vacuum_data
                            "usage_ripple": {
                                "display": "Elevated Usage" if vacuum_data else "Standard Volume",
                                "reasoning": vacuum_data.get("reason", "Based on team role") if vacuum_data else "Based on team role and recent minutes",
                                "bump_percent": int(vacuum_data.get("usage_bump", 0)) if vacuum_data else 0,
                                "shift_label": f"+{int(vacuum_data.get('usage_bump', 0))}% Usage" if vacuum_data else "Normal",
                                "injuries_affecting": [vacuum_data.get("injured_player")] if vacuum_data and vacuum_data.get("injured_player") else []
                            },
                            # Context Badges - MERGED: Prop badges + Player badges
                            "context_badges": list(set(
                                self._build_prop_badges(
                                    blowout_risk_data.get("risk_level") if blowout_risk_data else None,
                                    prop.get("sidecar", {}).get("hook_risk", False),
                                    prop.get("sidecar", {}).get("suspect_line_bait", False),
                                    abs(line_delta) >= 1.5 if line_delta else False,
                                    momentum_data
                                ) + player_context_badges
                            )),
                            # ===== TARGET LOCK RATIONALE =====
                            "target_lock_rationale": self._build_target_lock_rationale(
                                player_name, stat_type, pp_line, prop.get("direction", "over"),
                                l10_rate, l5_rate, momentum_data, blowout_risk_data,
                                vacuum_data, line_delta, true_probability
                            ),
                            # Vision Insight placeholder (AI summary - populated later)
                            "vision_insight": {
                                "primary": f"{player_name} {stat_type} @ {pp_line}",
                                "reasons": self._build_vision_reasons(
                                    l10_rate, l5_rate, momentum_data, vacuum_data, line_delta
                                ),
                                "confidence": v7_confidence
                            },
                            # Vision Summary (AI-generated - populated by VisionSummaryService)
                            "vision_summary": prop.get("vision_summary"),
                            # Raw enrichment data for advanced displays (keep in intel_suite for backwards compatibility)
                            "whistle_data": {
                                "crew_chief": crew_chief,
                                "ref_ou_pct": round(ref_ou_pct, 1) if ref_ou_pct else None,
                                "ref_ppg": round(ref_ppg, 1) if ref_ppg else None,
                                "whistle_class": whistle_class,
                                "point_lift": point_lift_data.get("point_lift", 0),
                                "lift_label": point_lift_data.get("lift_label", ""),
                                "lift_type": point_lift_data.get("lift_type", "neutral")
                            } if crew_chief else None,
                            "vacuum_data": vacuum_data
                        },
                        "is_vision_enriched": True
                    }
                    
                    all_scored.append(scored_prop)
            
            # Track V7 kill stats
            total_hard_kills = results["v7_kills"]["hard_kills"]
            results["scored"]["total_survivors"] = len(all_scored)
            
            # Remove duplicates from all_scored (same player + stat + line)
            seen = set()
            unique_scored = []
            for prop in all_scored:
                key = (prop.get("player_name"), prop.get("stat_type"), prop.get("line"))
                if key not in seen:
                    seen.add(key)
                    unique_scored.append(prop)
            
            all_scored = unique_scored
            
            logger.info(f"  Total scanned: {results['universal_scan']['total_props_scanned']}")
            logger.info(f"  V7 Hard Kills: {total_hard_kills}")
            logger.info(f"  V7 Soft Penalties: {results['v7_kills']['soft_penalties_applied']}")
            logger.info(f"  Survivors scored: {len(all_scored)} (after dedupe)")
            
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
                # Save all discards (was limited to 500)
                await self.ferrari_discarded.insert_many(discarded, ordered=False)
            
            logger.info(f"  Total discarded: {len(discarded)}")
            
            # =================================================================
            # PHASE 4: GLOBAL SORT - Prefer Clean Picks Over Trap Picks
            # =================================================================
            logger.info("[PHASE 4] GLOBAL SORT - Ranking by true_probability (clean picks first)...")
            
            # Track used players separately for Goblins vs Demons
            # Players CAN appear in both Safe Haven (Goblin) AND War Zone (Demon)
            used_goblin_players = set()
            used_demon_players = set()
            
            # SAFE HAVEN: Global sort by true_probability (ferrari_power_score), then dedupe, then limit
            # Only Goblins allowed
            sh_cursor = self.ferrari_scored.find(
                {"tier": "safe_haven", "is_goblin": True},
                {"_id": 0}
            ).sort("ferrari_power_score", -1)
            sh_all = await sh_cursor.to_list(length=None)
            sh_clean = sum(1 for p in sh_all if not (p.get("trap_risk") or p.get("hook_risk")))
            logger.info(f"  Safe Haven pool: {len(sh_all)} total, {sh_clean} clean, {len(sh_all) - sh_clean} trap")
            top_safe_haven = self._dedupe_select(sh_all, used_goblin_players, MAX_PICKS_PER_TIER)
            
            # FRONT LINES: Middle tier - includes BOTH Goblins AND "Safe" Demons
            # Goblins: Use used_goblin_players to exclude Safe Haven players
            # Demons: Use used_demon_players to track (separate from War Zone)
            fl_cursor = self.ferrari_scored.find(
                {"tier": "front_lines"},
                {"_id": 0}
            ).sort("ferrari_power_score", -1)
            fl_all = await fl_cursor.to_list(length=None)
            
            # Separate goblins and demons for proper deduplication
            fl_goblins = [p for p in fl_all if p.get("is_goblin") and not p.get("is_demon")]
            fl_demons = [p for p in fl_all if p.get("is_demon")]
            
            fl_clean = sum(1 for p in fl_all if not (p.get("trap_risk") or p.get("hook_risk")))
            logger.info(f"  Front Lines pool: {len(fl_all)} total ({len(fl_goblins)} goblins, {len(fl_demons)} demons), {fl_clean} clean")
            
            # Dedupe goblins (exclude Safe Haven players)
            fl_goblin_picks = self._dedupe_select(fl_goblins, used_goblin_players, MAX_PICKS_PER_TIER)
            
            # Dedupe demons separately (Front Lines demons are separate from War Zone demons)
            used_fl_demon_players = set()
            fl_demon_picks = self._dedupe_select(fl_demons, used_fl_demon_players, MAX_PICKS_PER_TIER)
            
            # Track Front Lines demons so they don't appear in War Zone
            for p in fl_demon_picks:
                used_demon_players.add(p.get("player_name"))
            
            # Merge and sort by board score, take top 10
            fl_merged = fl_goblin_picks + fl_demon_picks
            fl_merged.sort(key=lambda x: x.get("ferrari_power_score", 0), reverse=True)
            top_front_lines = fl_merged[:MAX_PICKS_PER_TIER]
            
            # WAR ZONE: Highest risk Demons ONLY (extreme plays)
            # Exclude demons already used in Front Lines
            wz_cursor = self.ferrari_scored.find(
                {"tier": "war_zone", "is_demon": True},
                {"_id": 0}
            ).sort("ferrari_power_score", -1)
            wz_all = await wz_cursor.to_list(length=None)
            wz_clean = sum(1 for p in wz_all if not (p.get("trap_risk") or p.get("hook_risk")))
            logger.info(f"  War Zone pool: {len(wz_all)} total, {wz_clean} clean, {len(wz_all) - wz_clean} trap")
            top_war_zone = self._dedupe_select(wz_all, used_demon_players, MAX_PICKS_PER_TIER)
            
            # =================================================================
            # PHASE 5: DIVERSIFIED PARLAY GENERATION (V7 NEW)
            # =================================================================
            logger.info("[PHASE 5] PARLAY OPTIMIZER - Building diversified parlays...")
            
            # Combine all tier picks for parlay generation
            all_tier_picks = top_safe_haven + top_front_lines + top_war_zone
            logger.info(f"  Total picks for parlay generation: {len(all_tier_picks)}")
            for tier_name, tier_picks in [("SH", top_safe_haven), ("FL", top_front_lines), ("WZ", top_war_zone)]:
                logger.info(f"    {tier_name}: {len(tier_picks)} picks, tiers: {set(p.get('tier') for p in tier_picks)}")
            
            # Generate parlays per tier
            parlay_optimizer = DiversifiedParlayOptimizer(all_tier_picks)
            
            safe_haven_parlays = parlay_optimizer.build_optimized_parlays("safe_haven", MAX_PARLAYS_PER_TIER)
            front_lines_parlays = parlay_optimizer.build_optimized_parlays("front_lines", MAX_PARLAYS_PER_TIER)
            war_zone_parlays = parlay_optimizer.build_optimized_parlays("war_zone", MAX_PARLAYS_PER_TIER)
            
            all_parlays = safe_haven_parlays + front_lines_parlays + war_zone_parlays
            
            logger.info(f"  Parlays generated: SH={len(safe_haven_parlays)}, FL={len(front_lines_parlays)}, WZ={len(war_zone_parlays)}")
            logger.info(f"  Total parlays to store: {len(all_parlays)}")
            
            # =================================================================
            # PHASE 6: STORE FINAL SELECTIONS + PARLAYS
            # =================================================================
            logger.info("[PHASE 6] FINAL SELECTION - Storing picks and parlays...")
            
            await self.ferrari_safe_haven.delete_many({})
            if top_safe_haven:
                await self.ferrari_safe_haven.insert_many(top_safe_haven)
            
            await self.ferrari_front_lines.delete_many({})
            if top_front_lines:
                await self.ferrari_front_lines.insert_many(top_front_lines)
            
            await self.ferrari_war_zone.delete_many({})
            if top_war_zone:
                await self.ferrari_war_zone.insert_many(top_war_zone)
            
            # Store parlays in a new collection
            parlays_collection = self.db.ferrari_parlays
            await parlays_collection.delete_many({})
            if all_parlays:
                logger.info(f"  Storing {len(all_parlays)} parlays to ferrari_parlays collection...")
                try:
                    insert_result = await parlays_collection.insert_many(all_parlays)
                    logger.info(f"  Successfully inserted {len(insert_result.inserted_ids)} parlays")
                except Exception as e:
                    logger.error(f"  Parlay insert failed: {e}")
            
            # Update results
            results["output"]["safe_haven"] = len(top_safe_haven)
            results["output"]["front_lines"] = len(top_front_lines)
            results["output"]["war_zone"] = len(top_war_zone)
            results["output"]["total_picks"] = len(top_safe_haven) + len(top_front_lines) + len(top_war_zone)
            results["output"]["total_parlays"] = len(all_parlays)
            
            results["parlays"]["safe_haven"] = len(safe_haven_parlays)
            results["parlays"]["front_lines"] = len(front_lines_parlays)
            results["parlays"]["war_zone"] = len(war_zone_parlays)
            
            # VERIFICATION MESSAGE
            results["verification"]["active_props_verified"] = results["scored"]["total_survivors"]
            results["verification"]["elite_opportunities"] = results["output"]["total_picks"]
            results["verification"]["optimized_parlays"] = results["output"]["total_parlays"]
            results["verification"]["message"] = (
                f"Verified {results['scored']['total_survivors']} active props → "
                f"{results['output']['total_picks']} Elite picks + {results['output']['total_parlays']} Optimized parlays"
            )
            
            logger.info("=" * 70)
            logger.info("[PROPVISION v7] PIPELINE COMPLETE")
            logger.info(f"  {results['verification']['message']}")
            logger.info("=" * 70)
            
        except Exception as e:
            logger.error(f"[v7] Pipeline error: {e}")
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
        """
        Deduplicate and select top N by power score.
        
        STRICT CLEAN PICKS ONLY:
        - Only select picks without hook_risk or suspect_line_bait
        - If we don't have enough clean picks, show fewer (not traps)
        """
        selected = []
        selected_names = set()
        
        for pick in candidates:
            name = pick.get("player_name")
            if name and name not in used_players and name not in selected_names:
                # Skip trap picks entirely
                is_trap = pick.get("trap_risk") or pick.get("hook_risk") or pick.get("suspect_line_bait")
                if is_trap:
                    continue
                
                used_players.add(name)
                selected_names.add(name)
                selected.append(pick)
                if len(selected) >= limit:
                    break
        
        if len(selected) < limit:
            logger.info(f"  [CLEAN ONLY] Selected {len(selected)} clean picks (target was {limit})")
        
        return selected
    
    def _build_prop_badges(self, blowout_risk, hook_risk, suspect_bait, sharp_movement, momentum_data) -> List[str]:
        """Build prop-specific badges based on game/matchup analysis."""
        badges = []
        
        if blowout_risk == "HIGH":
            badges.append("blowout_risk")
        # Note: trap_risk picks are now filtered out entirely, so no need for badge
        if sharp_movement:
            badges.append("sharp_movement")
        if momentum_data:
            if momentum_data.get("is_weak"):
                badges.append("soft_matchup")
            elif momentum_data.get("is_elite"):
                badges.append("tough_matchup")
            if momentum_data.get("trend_alert"):
                badges.append("trend_alert")
        
        return badges
    
    def _calculate_tempo(self, team: Optional[str], opponent: Optional[str]) -> Dict[str, Any]:
        """Calculate tempo/pace delta for a matchup."""
        team_pace = TEAM_PACE_DEFAULTS.get(team, DEFAULT_SEASON_PACE) if team else DEFAULT_SEASON_PACE
        opp_pace = TEAM_PACE_DEFAULTS.get(opponent, DEFAULT_SEASON_PACE) if opponent else DEFAULT_SEASON_PACE
        
        expected_game_pace = (team_pace + opp_pace) / 2
        possession_delta = expected_game_pace - DEFAULT_SEASON_PACE
        
        if possession_delta >= 4:
            tempo_label = "High Tempo"
        elif possession_delta >= 2:
            tempo_label = "Above Average"
        elif possession_delta >= -2:
            tempo_label = "Standard"
        elif possession_delta >= -4:
            tempo_label = "Below Average"
        else:
            tempo_label = "Slow Tempo"
        
        return {
            "possessions": round(possession_delta, 1),
            "display": f"{'+' if possession_delta >= 0 else ''}{possession_delta:.1f}",
            "tempo_label": tempo_label,
            "team_pace": round(team_pace, 1),
            "opponent_pace": round(opp_pace, 1),
            "expected_game_pace": round(expected_game_pace, 1)
        }
    
    def _calculate_std_dev(self, values: List[float]) -> Optional[float]:
        """Calculate sample standard deviation of values (Bessel's correction: n-1)."""
        if not values or len(values) < 2:
            return None
        n = len(values)
        avg = sum(values) / n
        # Sample variance uses (n-1) denominator (Bessel's correction)
        variance = sum((v - avg) ** 2 for v in values) / (n - 1)
        return round(variance ** 0.5, 2)
    
    def _calculate_variance(self, stat_values: List[float], l10_rate: float, l5_rate: float) -> Dict[str, Any]:
        """Calculate variance/stability metrics."""
        std_dev = self._calculate_std_dev(stat_values) if stat_values else None
        stability_score = int((l10_rate + l5_rate) / 2)
        
        if std_dev is None:
            std_dev = 3.0  # Default
        
        if std_dev <= 1.5:
            variance_label = "Very Low"
            consistency = "Extremely Consistent"
        elif std_dev <= 2.5:
            variance_label = "Low"
            consistency = "Very Consistent"
        elif std_dev <= 3.5:
            variance_label = "Medium"
            consistency = "Average Consistency"
        elif std_dev <= 5.0:
            variance_label = "High"
            consistency = "Below Average"
        else:
            variance_label = "Very High"
            consistency = "Volatile"
        
        return {
            "std_dev": std_dev,
            "variance_label": variance_label,
            "consistency": consistency,
            "stability_score": stability_score,
            "variance_level": "Low" if stability_score >= 70 else "Medium" if stability_score >= 45 else "High"
        }
    
    def _build_vision_reasons(
        self, l10_rate: float, l5_rate: float, 
        momentum_data: Optional[Dict], vacuum_data: Optional[Dict],
        line_delta: Optional[float]
    ) -> List[str]:
        """Build vision insight reasons."""
        reasons = []
        
        avg_rate = (l10_rate + l5_rate) / 2
        if avg_rate >= 80:
            reasons.append(f"Exceptional hit rate ({avg_rate:.0f}% L5-L10 avg)")
        elif avg_rate >= 70:
            reasons.append(f"Strong consistency ({avg_rate:.0f}% hit rate)")
        
        if momentum_data:
            if momentum_data.get("is_weak"):
                reasons.append("Favorable defensive matchup")
            if momentum_data.get("trend_alert"):
                reasons.append(f"Momentum alert: {momentum_data.get('momentum', 'shifting')}")
        
        if vacuum_data:
            reasons.append(f"Usage boost: {vacuum_data.get('reason', 'teammate out')}")
        
        if line_delta and abs(line_delta) >= 1.5:
            direction = "Under" if line_delta > 0 else "Over"
            reasons.append(f"Sharp line movement ({direction} value)")
        
        return reasons
    
    def _build_target_lock_rationale(
        self, player_name: str, stat_type: str, line: float, direction: str,
        l10_rate: float, l5_rate: float, momentum_data: Optional[Dict],
        blowout_risk_data: Optional[Dict], vacuum_data: Optional[Dict],
        line_delta: Optional[float], power_score: float
    ) -> Dict[str, Any]:
        """Build comprehensive target lock rationale for the pick."""
        # Build primary reasoning
        reasons = []
        warnings = []
        confidence_factors = []
        
        # Hit rate analysis
        avg_rate = (l10_rate + l5_rate) / 2
        if avg_rate >= 80:
            reasons.append(f"Elite {avg_rate:.0f}% L5-L10 hit rate")
            confidence_factors.append("hit_rate_elite")
        elif avg_rate >= 70:
            reasons.append(f"Strong {avg_rate:.0f}% consistency")
            confidence_factors.append("hit_rate_strong")
        elif avg_rate >= 60:
            reasons.append(f"Solid {avg_rate:.0f}% baseline")
        
        # Momentum/Matchup analysis
        if momentum_data:
            if momentum_data.get("is_weak"):
                rank = momentum_data.get("composite_rank", 0)
                reasons.append(f"Soft matchup (#{int(rank)} defense)")
                confidence_factors.append("soft_matchup")
            elif momentum_data.get("is_elite"):
                rank = momentum_data.get("composite_rank", 0)
                warnings.append(f"Tough matchup (#{int(rank)} defense)")
            
            if momentum_data.get("trend_alert"):
                reasons.append("Defensive momentum shifting")
        
        # Usage boost
        if vacuum_data:
            bump = vacuum_data.get("usage_bump", 0)
            if bump >= 10:
                reasons.append(f"+{bump}% usage boost (injury)")
                confidence_factors.append("usage_boost")
        
        # Line movement
        if line_delta and abs(line_delta) >= 1.5:
            if line_delta > 0:
                reasons.append(f"Under value ({line_delta:+.1f} from sharp)")
            else:
                reasons.append(f"Over value ({line_delta:+.1f} from sharp)")
            confidence_factors.append("line_value")
        
        # Blowout risk warning
        if blowout_risk_data and blowout_risk_data.get("risk_level") == "HIGH":
            warnings.append(blowout_risk_data.get("warning", "Blowout risk"))
        
        # Determine confidence level
        if len(confidence_factors) >= 3:
            confidence = "HIGH"
        elif len(confidence_factors) >= 2:
            confidence = "MEDIUM"
        else:
            confidence = "STANDARD"
        
        # Build summary
        summary = f"{player_name} {direction.upper()} {line} {stat_type}"
        if reasons:
            summary += f" - {reasons[0]}"
        
        return {
            "summary": summary,
            "reasons": reasons,
            "warnings": warnings,
            "confidence": confidence,
            "confidence_factors": confidence_factors,
            "power_score": power_score
        }
    
    async def _resolve_player_context_badges(self, player_name: str, game_logs: List[Dict], baseline_stats: Dict) -> List[str]:
        """
        Resolve player-level context badges using BDL data as SSOT.
        
        Badges:
        1. locked_in: L5 PPG > Season PPG + 5
        2. gassed: Back-to-back game OR 38+ minutes last game
        3. home_cookin: Home PPG 15%+ higher than Away (from game logs)
        4. deep_water: BDL injuries only
        5. pay_day: Contract year (from Spotrac/static)
        6-10: Other context badges from context_engine/static data
        """
        badges = []
        
        try:
            # ===== 1. LOCKED_IN: L5 PPG > Season PPG + 5 =====
            pts_stats = baseline_stats.get("PTS", {})
            season_ppg = pts_stats.get("season_avg", 0) if isinstance(pts_stats, dict) else 0
            
            if season_ppg and game_logs and len(game_logs) >= 5:
                l5_pts = [g.get("pts", 0) or 0 for g in game_logs[:5]]
                l5_ppg = sum(l5_pts) / len(l5_pts) if l5_pts else 0
                
                if l5_ppg > season_ppg + 5:
                    badges.append("locked_in")
            
            # ===== 2. GASSED: Back-to-back OR heavy minutes =====
            if game_logs and len(game_logs) >= 2:
                from datetime import datetime as dt
                try:
                    log1 = game_logs[0]
                    log2 = game_logs[1]
                    
                    date1_str = log1.get("game", {}).get("date") or log1.get("game_date")
                    date2_str = log2.get("game", {}).get("date") or log2.get("game_date")
                    
                    if date1_str and date2_str:
                        for fmt in ["%Y-%m-%d", "%b %d, %Y"]:
                            try:
                                date1 = dt.strptime(str(date1_str)[:10], fmt)
                                date2 = dt.strptime(str(date2_str)[:10], fmt)
                                break
                            except (ValueError, TypeError):
                                continue
                        else:
                            date1, date2 = None, None
                        
                        if date1 and date2 and abs((date1 - date2).days) == 1:
                            badges.append("gassed")
                except Exception as e:
                    logger.debug(f"[BADGE] Gassed B2B check error: {e}")
            
            # Heavy minutes check (38+ in last game)
            if game_logs and "gassed" not in badges:
                try:
                    last_game = game_logs[0]
                    minutes = last_game.get("min") or last_game.get("minutes", "0")
                    if isinstance(minutes, str) and ":" in minutes:
                        minutes = int(minutes.split(":")[0])
                    else:
                        minutes = int(float(minutes)) if minutes else 0
                    
                    if minutes >= 38:
                        badges.append("gassed")
                except Exception as e:
                    logger.debug(f"[BADGE] Heavy minutes check error: {e}")
            
            # ===== 3. HOME_COOKIN: Home PPG 15%+ higher than Away =====
            if game_logs and len(game_logs) >= 10:
                try:
                    home_pts = []
                    away_pts = []
                    
                    for log in game_logs[:20]:
                        pts = log.get("pts", 0) or 0
                        game_data = log.get("game", {})
                        team_data = log.get("team", {})
                        team_id = team_data.get("id")
                        home_team_id = game_data.get("home_team_id")
                        
                        matchup = log.get("matchup", "")
                        is_home = None
                        
                        if team_id and home_team_id:
                            is_home = team_id == home_team_id
                        elif "vs." in matchup:
                            is_home = True
                        elif "@" in matchup:
                            is_home = False
                        
                        if is_home is True:
                            home_pts.append(pts)
                        elif is_home is False:
                            away_pts.append(pts)
                    
                    if home_pts and away_pts:
                        home_avg = sum(home_pts) / len(home_pts)
                        away_avg = sum(away_pts) / len(away_pts)
                        
                        if away_avg > 0 and home_avg > away_avg * 1.15:
                            badges.append("home_cookin")
                except Exception as e:
                    logger.debug(f"[BADGE] Home cookin check error: {e}")
            
            # ===== 4. DEEP_WATER: BDL Injuries ONLY =====
            try:
                bdl_injuries = self.db.bdl_injuries
                injury = await bdl_injuries.find_one(
                    {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
                    {"_id": 0}
                )
                
                if injury:
                    severity = injury.get("severity", "unknown")
                    if severity in ["out", "doubtful", "season_ending", "questionable", "probable"]:
                        badges.append("deep_water")
            except Exception as e:
                logger.debug(f"[BADGE] Deep water check error: {e}")
            
            # ===== 5. PAY_DAY: Contract year =====
            try:
                from services.spotrac_contract_service import get_contract_year_info
                pay_day = await get_contract_year_info(player_name, self.db)
                if pay_day:
                    badges.append("pay_day")
            except Exception as e:
                logger.debug(f"[BADGE] Pay day check error: {e}")
            
            # ===== 6-10: Context engine flags (jet_lag, legal_noise, distraction, revenge, milestone) =====
            try:
                context_engine = self.db['nba_context_engine']
                context_query = {"active": True, "player_name": {"$regex": f"^{player_name}$", "$options": "i"}}
                
                async for flag in context_engine.find(context_query, {"_id": 0, "flag_type": 1, "travel_miles": 1}):
                    flag_type = flag.get("flag_type", "")
                    
                    if flag_type == "travel" and flag.get("travel_miles", 0) >= 1000:
                        if "jet_lag" not in badges:
                            badges.append("jet_lag")
                    elif "legal" in flag_type.lower():
                        if "legal_noise" not in badges:
                            badges.append("legal_noise")
                    elif flag_type in ["distraction", "trade_rumors", "drama"]:
                        if "distraction" not in badges:
                            badges.append("distraction")
                    elif flag_type == "revenge":
                        if "revenge" not in badges:
                            badges.append("revenge")
            except Exception as e:
                logger.debug(f"[BADGE] Context engine check error: {e}")
            
            # Check static distraction data
            if "distraction" not in badges:
                try:
                    from data.context_data import get_distraction_info
                    distraction = get_distraction_info(player_name)
                    if distraction:
                        badges.append("distraction")
                except Exception:
                    pass
            
            # Check static milestone data
            try:
                from data.career_milestones import get_best_milestone
                milestone = get_best_milestone(player_name)
                if milestone:
                    badges.append("milestone")
            except Exception:
                pass
            
        except Exception as e:
            logger.error(f"[BADGE] Error resolving badges for {player_name}: {e}")
        
        return badges

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
