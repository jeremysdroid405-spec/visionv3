"""
MLB PropVision Pipeline - 1:1 Clone of NBA Architecture
=========================================================================
Exact same JIT Delta Check, Atomic Swaps, and Vision Intel as NBA.

MLB STAT CATEGORIES:
  - HITS (Hits)
  - TB (Total Bases)
  - K (Strikeouts - Pitchers)
  - OUTS (Pitching Outs Recorded)
  - HRR (Hits + Runs + RBIs)
  - SINGLES (Calculated: Hits - 2B - 3B - HR)
  - WALKS (Walks)
  - DOUBLES (Doubles)
  - RUNS (Runs Scored)
  - RBI (Runs Batted In)
  - SB (Stolen Bases)
  - HR (Home Runs)
  - ER (Earned Runs - Pitchers)

TIER CLASSIFICATION:
  - Safe Haven: DK <= -250 (heavily juiced)
  - Front Lines: -249 to +199 (moderate odds)
  - War Zone: +200+ (longshots)
"""
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
from collections import Counter
import logging
import math

# Import MLB Matchup Math for split matchup analysis
from services.mlb_matchup_math import get_mlb_matchup_analysis

# Import MLB Oracle Apex Service for Safe Haven strict 2026 gate logic
from services.mlb_oracle_apex_service import get_mlb_oracle_apex_service

logger = logging.getLogger(__name__)

# ========== MLB STAT MAPPINGS ==========
MLB_STAT_CATEGORIES = {
    "HITS": ["hits", "h"],
    "TB": ["total_bases", "tb"],
    "K": ["strikeouts", "k", "so"],
    "OUTS": ["pitching_outs", "outs_recorded", "outs"],
    "HRR": ["hits_runs_rbis", "hrr"],
    "SINGLES": ["singles", "1b"],
    "WALKS": ["walks", "bb"],
    "DOUBLES": ["doubles", "2b"],
    "RUNS": ["runs", "r"],
    "RBI": ["rbi", "rbis"],
    "SB": ["stolen_bases", "sb"],
    "HR": ["home_runs", "hr"],
    "ER": ["earned_runs", "er"],
}

# MLB Team abbreviations
MLB_TEAMS = [
    "ARI", "ATL", "BAL", "BOS", "CHC", "CHW", "CIN", "CLE", "COL", "DET",
    "HOU", "KC", "LAA", "LAD", "MIA", "MIL", "MIN", "NYM", "NYY", "OAK",
    "PHI", "PIT", "SD", "SF", "SEA", "STL", "TB", "TEX", "TOR", "WSH"
]

# Import MLB-specific engine classes
from services.mlb_propvision_engine import (
    TrueProbabilityEngine,
    DiversifiedParlayOptimizer,
    calculate_granular_hit_rates,
    calculate_median,
    calculate_mode,
    calculate_std_dev
)

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

# =============================================================================
# DK ODDS-BASED TIER CLASSIFICATION (Primary - Simple & Clear)
# =============================================================================
# If DK_Odds <= -250        → Safe Haven (heavily juiced, very likely)
# If -249 <= DK_Odds <= +199 → Front Lines (moderate odds range)
# If DK_Odds >= +200         → War Zone (longshots, value plays)
# =============================================================================
DK_TIER_SAFE_HAVEN_MAX = -250      # DK odds <= -250 = Safe Haven
DK_TIER_FRONT_LINES_MIN = -249     # DK odds >= -249
DK_TIER_FRONT_LINES_MAX = 199      # DK odds <= +199 = Front Lines
DK_TIER_WAR_ZONE_MIN = 200         # DK odds >= +200 = War Zone

# Legacy constants (kept for backwards compatibility)
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


def classify_tier_by_dk_odds(dk_odds: Optional[int]) -> str:
    """
    Classify tier based on DraftKings odds.
    
    Rules:
    - DK_Odds <= -250        → Safe Haven (heavily juiced, very likely)
    - -249 <= DK_Odds <= +199 → Front Lines (moderate odds range)  
    - DK_Odds >= +200         → War Zone (longshots, value plays)
    - No DK odds available    → Use fallback (front_lines by default)
    
    Args:
        dk_odds: DraftKings American odds (e.g., -300, -150, +180, +250)
        
    Returns:
        Tier name: "safe_haven", "front_lines", or "war_zone"
    """
    if dk_odds is None:
        return "front_lines"  # Default to Front Lines if no DK data
    
    if dk_odds <= DK_TIER_SAFE_HAVEN_MAX:  # <= -250
        return "safe_haven"
    elif dk_odds >= DK_TIER_WAR_ZONE_MIN:  # >= +200
        return "war_zone"
    else:  # -249 to +199
        return "front_lines"


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

class MLBTierService:
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
        self.cached_board = db.mlb_cached_board
        # BDL is the SSOT for all player stats and game logs
        self.master_hub = db.mlb_master_hub_2026
        
        # Output collections
        self.mlb_safe_haven = db.mlb_safe_haven
        self.mlb_front_lines = db.mlb_front_lines
        self.mlb_war_zone = db.mlb_war_zone
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
    
    def _check_front_lines_gates(
        self,
        stat_type: str,
        line: float,
        l20_values: List[float],
        l5_values: List[float],
        cv: float,
        vk_predicted: float,
        vk_prob: float,
        l20_hits: int = None,
        l5_avg: float = None,
        l20_avg: float = None
    ) -> tuple:
        """
        Check if a prop passes Front Lines 3-gate qualification.
        
        Front Lines Gates (Relaxed compared to Safe Haven):
        - GATE 1: Hit Rate - PTS 14/20, REB 12/20, AST 12/20, PRA 14/20
        - GATE 2: CV - PTS 0.28, REB 0.40, AST 0.40, PRA 0.25
        - GATE 3: VK Edge >= 1.5, VK Prob >= 55%
        
        Returns:
            (qualifies: bool, reason: str)
        """
        from services.mlb_oracle_apex_service import FRONT_LINES_CONFIG
        import numpy as np
        
        if stat_type not in FRONT_LINES_CONFIG:
            return False, f"UNSUPPORTED_STAT: {stat_type}"
        
        cfg = FRONT_LINES_CONFIG[stat_type]
        
        # Calculate L20 hits - prefer passed value, then calculate from values
        if l20_hits is None:
            l20_hits = sum(1 for v in l20_values if v >= line) if l20_values else 0
        
        # Calculate means
        if l20_avg is None:
            l20_avg = np.mean(l20_values) if l20_values else 0
        if l5_avg is None:
            l5_avg = np.mean(l5_values) if l5_values else 0
        
        # GATE 1: HIT RATE
        passes_gate1 = l20_hits >= cfg['min_hit_rate']
        
        # REB buffer rule: 10/20 OK if L5 Mean >= Line + 1.5
        if not passes_gate1 and 'relaxed_hit_rate' in cfg:
            if l20_hits >= cfg['relaxed_hit_rate']:
                buffer_mean = l5_avg if cfg.get('relaxed_sample_size') == 5 else l20_avg
                if buffer_mean >= (line + cfg['relaxed_mean_buffer']):
                    passes_gate1 = True
        
        if not passes_gate1:
            return False, f"FL_GATE1: {l20_hits}/20 < {cfg['min_hit_rate']}/20"
        
        # GATE 2: CV
        if cv and cv > cfg['max_cv']:
            return False, f"FL_GATE2: CV {cv:.2f} > {cfg['max_cv']}"
        
        # GATE 3: EDGE + PROB
        edge = (vk_predicted - line) if vk_predicted else 0
        if edge < cfg['min_edge']:
            return False, f"FL_GATE3: Edge {edge:.1f} < {cfg['min_edge']}"
        
        if vk_prob and vk_prob < cfg['min_prob']:
            return False, f"FL_GATE3: Prob {vk_prob:.0f}% < {cfg['min_prob']}%"
        
        return True, "FRONT_LINES_QUALIFIED"
    
    def _check_war_zone_gates(
        self,
        stat_type: str,
        line: float,
        l20_values: List[float],
        l5_values: List[float],
        cv: float,
        vk_predicted: float,
        vk_prob: float,
        l20_hits: int = None,
        l5_avg: float = None,
        l20_avg: float = None
    ) -> tuple:
        """
        Check if a prop passes War Zone 3-gate qualification.
        
        War Zone Gates (Demon Ceiling Plays):
        - GATE 1: Hit Rate - 6/20 for all stats (PTS: 4/20 OK if L5 Mean > Line + 3.0)
        - GATE 2: CV - Higher variance REQUIRED (0.40-0.50)
        - GATE 3: VK Edge >= 3.5, VK Prob >= 35%
        
        Returns:
            (qualifies: bool, reason: str)
        """
        from services.mlb_oracle_apex_service import WAR_ZONE_CONFIG
        import numpy as np
        
        if stat_type not in WAR_ZONE_CONFIG:
            return False, f"UNSUPPORTED_STAT: {stat_type}"
        
        cfg = WAR_ZONE_CONFIG[stat_type]
        
        # Calculate L20 hits - prefer passed value, then calculate from values
        if l20_hits is None:
            l20_hits = sum(1 for v in l20_values if v >= line) if l20_values else 0
        
        # Calculate means
        if l20_avg is None:
            l20_avg = np.mean(l20_values) if l20_values else 0
        if l5_avg is None:
            l5_avg = np.mean(l5_values) if l5_values else 0
        
        # GATE 1: HIT RATE (lower threshold for demons)
        passes_gate1 = l20_hits >= cfg['min_hit_rate']
        
        # PTS buffer rule: 4/20 OK if L5 Mean > Line + 3.0
        if not passes_gate1 and 'relaxed_hit_rate' in cfg:
            if l20_hits >= cfg['relaxed_hit_rate']:
                buffer_mean = l5_avg if cfg.get('relaxed_sample_size') == 5 else l20_avg
                if buffer_mean > (line + cfg['relaxed_mean_buffer']):
                    passes_gate1 = True
        
        if not passes_gate1:
            return False, f"WZ_GATE1: {l20_hits}/20 < {cfg['min_hit_rate']}/20"
        
        # GATE 2: CV (higher variance REQUIRED for demon ceiling plays)
        # Note: For War Zone, we want HIGHER CV - but still cap it
        if cv and cv > cfg['max_cv']:
            return False, f"WZ_GATE2: CV {cv:.2f} > {cfg['max_cv']}"
        
        # GATE 3: EDGE + PROB (higher edge required for demon plays)
        edge = (vk_predicted - line) if vk_predicted else 0
        if edge < cfg['min_edge']:
            return False, f"WZ_GATE3: Edge {edge:.1f} < {cfg['min_edge']}"
        
        if vk_prob and vk_prob < cfg['min_prob']:
            return False, f"WZ_GATE3: Prob {vk_prob:.0f}% < {cfg['min_prob']}%"
        
        return True, "WAR_ZONE_QUALIFIED"
    
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
            master_hub = self.db.mlb_master_hub_2026
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
            
            logger.info(f"[BDL-SSOT] Loaded context data for {len(context_data)} players from mlb_master_hub_2026")
            
        except Exception as e:
            logger.error(f"[v6] Player context data load error: {e}")
        
        return context_data
    
    async def build_ferrari_tiers(self, sync_time: datetime, target_sport: str = "nba") -> Dict[str, Any]:
        """
        Execute MLB PropVision Pipeline - True Probability & Diversified Parlays
        
        **SPORT-EXCLUSIVE**: Operates only on collections for target_sport.
        
        1. Sync referee data (Whistle Matrix)
        2. Universal Scan (100% coverage)
        3. TRUE PROBABILITY calculation using V7 engine
        4. Hard/Soft Kill filtering
        5. Tier classification by True Probability
        6. Parlay generation with diversification constraints
        7. Top-K selection + Top-5 parlays per tier
        
        Args:
            sync_time: Timestamp of sync start
            target_sport: Sport to process ('nba' or 'mlb')
        """
        # Normalize sport
        target_sport = (target_sport or "nba").lower()
        
        logger.info("=" * 70)
        logger.info(f"[PROPVISION v7] TRUE PROBABILITY & DIVERSIFIED PARLAYS - {target_sport.upper()}")
        logger.info("=" * 70)
        
        results = {
            "success": True,
            "synced_at": sync_time.isoformat(),
            "sport": target_sport,
            "pipeline": f"PropVision v7 - True Probability ({target_sport.upper()})",
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
                    
                    # Get prop details - use stat_type_extracted or market as fallback
                    stat_type = (prop.get("stat_type") or prop.get("stat_type_extracted") or "").upper()
                    if not stat_type:
                        # Extract from market field (e.g., "player_assists" -> "AST")
                        market = prop.get("market", "")
                        market_to_stat = {
                            "player_points": "PTS",
                            "player_rebounds": "REB",
                            "player_assists": "AST",
                            "player_threes": "3PM",
                            "player_steals": "STL",
                            "player_blocks": "BLK",
                            "player_turnovers": "TO",
                            "player_points_rebounds_assists": "PRA",
                            "player_points_rebounds": "PR",
                            "player_points_assists": "PA",
                            "player_rebounds_assists": "RA"
                        }
                        stat_type = market_to_stat.get(market, "")
                    
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
                    v7_confidence = v7_result["confidence"]
                    v7_components = v7_result["components"]
                    
                    # ==========================================================
                    # DK ODDS-BASED TIER CLASSIFICATION (Primary)
                    # If DK_Odds <= -250        → Safe Haven
                    # If -249 <= DK_Odds <= +199 → Front Lines
                    # If DK_Odds >= +200         → War Zone
                    # ==========================================================
                    
                    # Get DK odds from sharp_market or flat field
                    sharp_market = prop.get("sharp_market", {})
                    dk_odds = sharp_market.get("draftkings_price") or prop.get("draftkings_price")
                    
                    # Also try sort_price if DK not available (fallback to FD or BOL)
                    if dk_odds is None:
                        dk_odds = sharp_market.get("sort_price") or prop.get("sort_price")
                    
                    # Classify tier by DK odds
                    dk_tier = classify_tier_by_dk_odds(dk_odds)
                    
                    # Use DK-based tier as primary classification
                    v7_tier = dk_tier
                    
                    is_demon = prop.get("is_demon", False)
                    is_goblin = prop.get("is_goblin", False)
                    is_standard = not is_demon and not is_goblin
                    
                    # ==========================================================
                    # DK ODDS-BASED TIER RULES:
                    # - Safe Haven (DK <= -250): Goblins only
                    # - Front Lines (DK -249 to +199): Demons, Goblins, OR Standards
                    # - War Zone (DK >= +200): Demons only
                    # ==========================================================
                    
                    # Safe Haven = Goblins only (heavily juiced lines)
                    if v7_tier == "safe_haven":
                        if not is_goblin:
                            results["scored"]["below_threshold"] += 1
                            discarded.append({
                                "player_name": player_name,
                                "stat_type": stat_type,
                                "line": pp_line,
                                "reason": "SAFE_HAVEN_NON_GOBLIN: Only goblins qualify for Safe Haven"
                            })
                            continue
                        results["scored"]["safe_haven_pool"] += 1
                    
                    # Front Lines = Demons, Goblins, OR Standards (all prop types allowed)
                    elif v7_tier == "front_lines":
                        results["scored"]["front_lines_pool"] += 1
                    
                    # War Zone = Demons only (longshot value plays)
                    elif v7_tier == "war_zone":
                        if not is_demon:
                            results["scored"]["below_threshold"] += 1
                            discarded.append({
                                "player_name": player_name,
                                "stat_type": stat_type,
                                "line": pp_line,
                                "reason": "WAR_ZONE_NON_DEMON: Only demons qualify for War Zone"
                            })
                            continue
                        results["scored"]["war_zone_pool"] += 1
                    
                    else:
                        results["scored"]["below_threshold"] += 1
                        continue
                    
                    # Additional stats
                    anchor_line = prop.get("anchor_line", pp_line)
                    line_delta = pp_line - anchor_line if anchor_line else 0
                    hit_rates = prop.get("hit_rates", {})
                    
                    # Derive opponent from prop data (away_team/home_team)
                    player_team = player.get("team", "")
                    prop_away = prop.get("away_team", "")
                    prop_home = prop.get("home_team", "")
                    
                    # Try to extract opponent from away/home teams
                    derived_opponent = None
                    if prop_away and prop_home and player_team:
                        # Map full team names to abbreviations for comparison
                        TEAM_ABBREV_MAP = {
                            "Pittsburgh Pirates": "PIT", "Chicago Cubs": "CHC", "Los Angeles Dodgers": "LAD",
                            "New York Yankees": "NYY", "Boston Red Sox": "BOS", "Atlanta Braves": "ATL",
                            "Philadelphia Phillies": "PHI", "Houston Astros": "HOU", "San Diego Padres": "SD",
                            "Cleveland Guardians": "CLE", "Tampa Bay Rays": "TB", "Baltimore Orioles": "BAL",
                            "Milwaukee Brewers": "MIL", "Seattle Mariners": "SEA", "Minnesota Twins": "MIN",
                            "Texas Rangers": "TEX", "Arizona Diamondbacks": "ARI", "Miami Marlins": "MIA",
                            "Detroit Tigers": "DET", "San Francisco Giants": "SF", "Cincinnati Reds": "CIN",
                            "Kansas City Royals": "KC", "St. Louis Cardinals": "STL", "Toronto Blue Jays": "TOR",
                            "New York Mets": "NYM", "Los Angeles Angels": "LAA", "Colorado Rockies": "COL",
                            "Oakland Athletics": "OAK", "Chicago White Sox": "CWS", "Washington Nationals": "WAS"
                        }
                        away_abbr = TEAM_ABBREV_MAP.get(prop_away, prop_away[:3].upper() if prop_away else "")
                        home_abbr = TEAM_ABBREV_MAP.get(prop_home, prop_home[:3].upper() if prop_home else "")
                        
                        # Determine opponent based on player's team
                        if player_team == away_abbr:
                            derived_opponent = home_abbr
                        elif player_team == home_abbr:
                            derived_opponent = away_abbr
                    
                    # Also try from last_10_games if available
                    if not derived_opponent:
                        last_games = prop.get("last_10_games", [])
                        if last_games and len(last_games) > 0:
                            derived_opponent = last_games[0].get("opponent")
                    
                    # Use derived opponent or fallback to player's opponent field
                    final_opponent = player.get("opponent") or player.get("opponent_abbr") or derived_opponent
                    
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
                        "opponent": final_opponent,
                        "opponent_abbr": final_opponent,
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
                        "betonline_price": sharp_market.get("betonline_price"),
                        # DK-BASED TIER CLASSIFICATION
                        "dk_odds": dk_odds,
                        "dk_tier": dk_tier,
                        "sort_price": sharp_market.get("sort_price") or dk_odds,
                        "sort_source": sharp_market.get("sort_source"),
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
                                "display": f"vs {final_opponent}" if final_opponent else "TBD",
                                "opponent": final_opponent,
                                "opponent_abbr": final_opponent,
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
                        "is_vision_enriched": True,
                        # ===== MLB SPLIT MATCHUP ANALYSIS =====
                        # For hitters: SP + Bullpen gauntlet
                        # For pitchers: Lineup K-Rate + wRC+ discipline check
                        "matchup_analysis": get_mlb_matchup_analysis(
                            stat_type=stat_type,
                            opponent_team=final_opponent,
                            starting_pitcher_name=player.get("opposing_pitcher") or player.get("sp_name")
                        ) if final_opponent else None
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
            
            # =================================================================
            # ALL TIERS USE mlb_cached_board AS SINGLE SOURCE (MLB-SPECIFIC)
            # =================================================================
            # Unlike NBA (which uses oracle_apex_analyzed), MLB reads directly 
            # from mlb_cached_board since all DK odds are already embedded in props.
            # We classify into tiers based on DK odds:
            #   - Safe Haven: DK <= -250 (Goblins only)
            #   - Front Lines: DK -249 to +199 (Demons, Goblins, Standards)
            #   - War Zone: DK >= +200 (Demons only)
            # =================================================================
            logger.info("[PHASE 4] DK ODDS-BASED TIER BUILDING FROM MLB CACHED BOARD...")
            
            # Pre-load VK baselines from mlb_master_hub_2026 for model projections
            vk_baselines_lookup = {}
            master_hub = self.db.mlb_master_hub_2026
            async for hub_doc in master_hub.find({}, {"_id": 0, "display_name": 1, "vk_baselines": 1}):
                display_name = hub_doc.get("display_name", "")
                if display_name and hub_doc.get("vk_baselines"):
                    vk_baselines_lookup[display_name.lower()] = hub_doc.get("vk_baselines", {})
            logger.info(f"  Loaded VK baselines for {len(vk_baselines_lookup)} players")
            
            # Flatten all props from mlb_cached_board directly
            all_mlb_props = []
            cached_board = self.db.mlb_cached_board
            
            # Team abbreviation map for opponent derivation
            TEAM_ABBREV_MAP = {
                "Pittsburgh Pirates": "PIT", "Chicago Cubs": "CHC", "Los Angeles Dodgers": "LAD",
                "New York Yankees": "NYY", "Boston Red Sox": "BOS", "Atlanta Braves": "ATL",
                "Philadelphia Phillies": "PHI", "Houston Astros": "HOU", "San Diego Padres": "SD",
                "Cleveland Guardians": "CLE", "Tampa Bay Rays": "TB", "Baltimore Orioles": "BAL",
                "Milwaukee Brewers": "MIL", "Seattle Mariners": "SEA", "Minnesota Twins": "MIN",
                "Texas Rangers": "TEX", "Arizona Diamondbacks": "ARI", "Miami Marlins": "MIA",
                "Detroit Tigers": "DET", "San Francisco Giants": "SF", "Cincinnati Reds": "CIN",
                "Kansas City Royals": "KC", "St. Louis Cardinals": "STL", "Toronto Blue Jays": "TOR",
                "New York Mets": "NYM", "Los Angeles Angels": "LAA", "Colorado Rockies": "COL",
                "Oakland Athletics": "OAK", "Chicago White Sox": "CWS", "Washington Nationals": "WAS"
            }
            
            # Stat type to VK baseline key mapping
            STAT_TO_VK_KEY = {
                "HITS": "hits", "TOTAL BASES": "total_bases", "TB": "total_bases",
                "RBIS": "rbis", "RBI": "rbis", "RUNS": "runs", "STOLEN BASES": "stolen_bases",
                "HOME RUNS": "home_runs", "HR": "home_runs", "WALKS": "walks",
                "STRIKEOUTS": "strikeouts", "BATTER STRIKEOUTS": "strikeouts",
                "DOUBLES": "doubles", "SINGLES": "singles", "TRIPLES": "triples",
                # Pitcher stats
                "PITCHER STRIKEOUTS": "pitcher_strikeouts", "EARNED RUNS": "earned_runs",
                "ER": "earned_runs", "PITCHING OUTS": "innings_pitched"
            }
            
            async for player_doc in cached_board.find({}, {"_id": 0}):
                player_name = player_doc.get("player_name", "")
                team = player_doc.get("team", "")
                
                # Get VK baselines for this player
                player_vk = vk_baselines_lookup.get(player_name.lower(), {})
                
                for prop in player_doc.get("props", []):
                    # Copy player-level fields to prop
                    prop["player_name"] = player_name
                    prop["team"] = team
                    
                    # ========== CALCULATE L5/L10/SEASON AVERAGES ==========
                    last_games = prop.get("last_10_games", [])
                    if last_games:
                        # Extract values from last_10_games
                        values = [g.get("value") for g in last_games if g.get("value") is not None]
                        if values:
                            l5_values = values[:5]
                            l10_values = values[:10]
                            prop["l5_avg"] = round(sum(l5_values) / len(l5_values), 1) if l5_values else None
                            prop["l10_avg"] = round(sum(l10_values) / len(l10_values), 1) if l10_values else None
                            prop["season_avg"] = prop["l10_avg"]  # Use L10 as proxy for season
                            
                            # Calculate hit rates (% over line)
                            pp_line = prop.get("line", 0)
                            if pp_line:
                                l5_hits = sum(1 for v in l5_values if v > pp_line)
                                l10_hits = sum(1 for v in l10_values if v > pp_line)
                                prop["hit_rate_l5"] = round((l5_hits / len(l5_values)) * 100, 1) if l5_values else None
                                prop["hit_rate_l10"] = round((l10_hits / len(l10_values)) * 100, 1) if l10_values else None
                                # Frontend expects h5_rate and h10_rate
                                prop["h5_rate"] = prop["hit_rate_l5"]
                                prop["h10_rate"] = prop["hit_rate_l10"]
                    
                    # ========== ADD VK MODEL PROJECTIONS ==========
                    stat_type_upper = (prop.get("stat_type") or "").upper()
                    vk_key = STAT_TO_VK_KEY.get(stat_type_upper)
                    if vk_key and vk_key in player_vk:
                        vk_stat = player_vk[vk_key]
                        vk_predicted = vk_stat.get("weighted_baseline")
                        prop["vk_predicted"] = round(vk_predicted, 2) if vk_predicted else None
                        
                        # Calculate VK edge (same as NBA: raw cushion = predicted - line)
                        pp_line = prop.get("line", 0)
                        if pp_line and vk_predicted:
                            # Raw edge = cushion (how much above/below line)
                            edge = vk_predicted - pp_line
                            prop["vk_edge"] = round(edge, 2)
                            
                            # Probability estimate based on cushion and CV
                            # More cushion = higher probability of going over
                            cv = vk_stat.get("weighted_cv") or 50  # Default to 50 if None
                            
                            # Simple probability model:
                            # - Cushion of 1.0 with low CV (30) = ~75% over
                            # - Cushion of 0.5 with high CV (100) = ~55% over
                            # Base formula: 50 + (edge / max(1, line)) * 30 - (cv / 10)
                            cushion_factor = (edge / max(1.0, pp_line)) * 30
                            cv_penalty = min(15, cv / 10) if cv else 5  # Cap CV penalty at 15%
                            prob = 50 + cushion_factor - cv_penalty
                            prop["vk_prob_over"] = round(max(20, min(90, prob)), 1)
                            prop["vk_probability"] = prop["vk_prob_over"]  # Alias
                        
                        prop["vk_cv"] = vk_stat.get("weighted_cv")
                    
                    # Derive opponent from away_team/home_team
                    prop_away = prop.get("away_team", "")
                    prop_home = prop.get("home_team", "")
                    derived_opponent = None
                    
                    if prop_away and prop_home and team:
                        away_abbr = TEAM_ABBREV_MAP.get(prop_away, prop_away[:3].upper() if prop_away else "")
                        home_abbr = TEAM_ABBREV_MAP.get(prop_home, prop_home[:3].upper() if prop_home else "")
                        if team == away_abbr:
                            derived_opponent = home_abbr
                        elif team == home_abbr:
                            derived_opponent = away_abbr
                    
                    # Fallback to last_10_games
                    if not derived_opponent:
                        if last_games:
                            derived_opponent = last_games[0].get("opponent")
                    
                    prop["opponent"] = derived_opponent
                    prop["opponent_abbr"] = derived_opponent
                    
                    # Add matchup_analysis for MLB split matchup
                    if derived_opponent:
                        prop["matchup_analysis"] = get_mlb_matchup_analysis(
                            stat_type=prop.get("stat_type", ""),
                            opponent_team=derived_opponent,
                            starting_pitcher_name=prop.get("opposing_pitcher")
                        )
                    else:
                        prop["matchup_analysis"] = None
                    
                    all_mlb_props.append(prop)
            
            logger.info(f"  Loaded {len(all_mlb_props)} props from mlb_cached_board")
            
            # ====================================================================
            # MLB SAFE HAVEN: Use Oracle Apex Service for STRICT 2026 Gate Logic
            # ====================================================================
            # The Oracle Apex Service enforces:
            # - PRIMARY: DK Odds <= -240, GOBLIN only, Lineup confirmed
            # - GATE 1: Strict L20 Hit Rate (16-17/20 depending on stat)
            # - GATE 2: Max CV per stat type
            # - GATE 3: Raw Cushion Edge >= thresholds + TP >= 70-80%
            # - HARD-STOP: Wind Environment filter (IN > 12mph kills batter props)
            # ====================================================================
            logger.info("[MLB_ORACLE_APEX] Delegating Safe Haven tier to Oracle Apex Service...")
            
            oracle_apex = get_mlb_oracle_apex_service(self.db)
            safe_haven_pool = await oracle_apex.build_safe_haven_tier(all_mlb_props)
            
            logger.info(f"[MLB_ORACLE_APEX] Oracle Apex returned {len(safe_haven_pool)} Safe Haven picks")
            
            # ====================================================================
            # FRONT LINES: Use Oracle Apex Service for 2026 Logic (L10 Recency Override)
            # ====================================================================
            logger.info("[MLB_ORACLE_APEX] Delegating Front Lines tier to Oracle Apex Service...")
            
            front_lines_pool = await oracle_apex.build_front_lines_tier(all_mlb_props)
            
            logger.info(f"[MLB_ORACLE_APEX] Oracle Apex returned {len(front_lines_pool)} Front Lines picks")
            
            # ====================================================================
            # WAR ZONE: Use Oracle Apex Service for 2026 Logic (L15 Ceiling + CV Fast-Track)
            # ====================================================================
            logger.info("[MLB_ORACLE_APEX] Delegating War Zone tier to Oracle Apex Service...")
            
            war_zone_pool = await oracle_apex.build_war_zone_tier(all_mlb_props)
            
            logger.info(f"[MLB_ORACLE_APEX] Oracle Apex returned {len(war_zone_pool)} War Zone picks")
            
            logger.info(f"  Oracle Apex classification complete:")
            logger.info(f"    Safe Haven pool: {len(safe_haven_pool)} (via Oracle Apex 2026 Gates)")
            logger.info(f"    Front Lines pool: {len(front_lines_pool)} (via Oracle Apex L10 Recency Override)")
            logger.info(f"    War Zone pool: {len(war_zone_pool)} (via Oracle Apex L15 Ceiling + CV Fast-Track)")
            
            # =================================================================
            # ALL POOLS PRE-SORTED BY ORACLE APEX - Just apply final sort key
            # =================================================================
            def sort_key(prop):
                # Use board_score from Oracle Apex if available, otherwise VK metrics
                board_score = prop.get("board_score") or 0
                vk_edge = prop.get("vk_edge") or 0
                h20_rate = prop.get("h20_rate") or 0
                return (board_score, vk_edge, h20_rate)
            
            # All three pools are now pre-sorted by Oracle Apex with board_score
            # No additional sorting needed - Oracle Apex handles it
            
            # =================================================================
            # =================================================================
            # STEP 6: DEDUPE FIRST - Select Final Top 10 per Tier
            # Sort by VK metrics, then slice to Top 10 BEFORE running Gemini
            # =================================================================
            def dedupe_pool(pool, max_picks, pool_name=""):
                """Dedupe and select top picks by VK metrics (pre-Gemini)."""
                # Sort by VK edge and hit rate (Gemini runs AFTER this)
                pool.sort(key=lambda x: (
                    x.get('vk_edge', 0),           # VK edge first
                    x.get('h20_rate', 0),          # Then hit rate
                    x.get('vk_prob_over', 0)       # Then probability
                ), reverse=True)
                
                seen = set()
                deduped = []
                duplicates = []
                for prop in pool:
                    key = (prop.get("player_name"), prop.get("stat_type"))
                    if key not in seen:
                        seen.add(key)
                        deduped.append(prop)
                        if len(deduped) >= max_picks:
                            break
                    else:
                        duplicates.append(key)
                
                if pool_name:
                    logger.info(f"    [{pool_name}] Pool size: {len(pool)}, Unique combos: {len(seen)}, Selected: {len(deduped)}")
                return deduped
            
            # Log War Zone pool before dedupe for debugging
            logger.info(f"  WAR ZONE DEBUG - Pool before dedupe:")
            for i, prop in enumerate(war_zone_pool[:15]):  # Log first 15
                logger.info(f"    {i+1}. {prop.get('player_name')} - {prop.get('stat_type')} | DK: {prop.get('dk_odds')} | Edge: {prop.get('vk_edge', 0):.1f} | Prob: {prop.get('vk_prob_over', 0):.0f}%")
            
            # SELECT FINAL TOP 10 FIRST (before Gemini)
            top_safe_haven = dedupe_pool(safe_haven_pool, MAX_PICKS_PER_TIER, "SAFE_HAVEN")
            top_front_lines = dedupe_pool(front_lines_pool, MAX_PICKS_PER_TIER, "FRONT_LINES")
            top_war_zone = dedupe_pool(war_zone_pool, MAX_PICKS_PER_TIER, "WAR_ZONE")
            
            logger.info(f"  After dedupe (Final Top 10 per tier):")
            logger.info(f"    Safe Haven: {len(top_safe_haven)} picks")
            logger.info(f"    Front Lines: {len(top_front_lines)} picks")
            logger.info(f"    War Zone: {len(top_war_zone)} picks")
            
            # =================================================================
            # STEP 7: JUST-IN-TIME VISION INTEL (Delta Batch Strategy)
            # Only call Gemini for NEW or MISSING intel picks to save tokens
            # =================================================================
            try:
                from services.mlb_vision_intel import get_mlb_vision_intel as get_vision_intel_service
                vision_intel = get_vision_intel_service()
                
                if vision_intel.enabled:
                    logger.info("[VISION INTEL] Starting Just-In-Time Diff Check...")
                    
                    async def diff_check_and_enrich(new_picks, collection, tier_name):
                        """
                        Just-In-Time Diff Check:
                        1. Query existing collection for cached vision_intel
                        2. Identify delta picks (new or missing intel)
                        3. Call Gemini ONLY for delta picks
                        4. Merge cached intel for returning players
                        """
                        if not new_picks:
                            return []
                        
                        # Step 1: Query existing board for cached intel
                        existing_docs = await collection.find(
                            {},
                            {"player_name": 1, "stat_type": 1, "line": 1, "vision_intel": 1, 
                             "intel_score": 1, "intel_verdict": 1, "intel_risk": 1, 
                             "adjusted_confidence": 1, "vision_summary": 1}
                        ).to_list(length=50)
                        
                        # Build cache map: key -> cached intel data
                        cache_map = {}
                        for doc in existing_docs:
                            key = f"{doc.get('player_name')}|{doc.get('stat_type')}|{doc.get('line')}"
                            if doc.get("vision_intel"):  # Only cache if has intel
                                cache_map[key] = {
                                    "vision_intel": doc.get("vision_intel"),
                                    "vision_summary": doc.get("vision_summary"),
                                    "intel_score": doc.get("intel_score"),
                                    "intel_verdict": doc.get("intel_verdict"),
                                    "intel_risk": doc.get("intel_risk"),
                                    "adjusted_confidence": doc.get("adjusted_confidence")
                                }
                        
                        logger.info(f"  [{tier_name}] Cached intel found: {len(cache_map)} picks")
                        
                        # Step 2: Identify delta picks (new or missing intel)
                        delta_picks = []
                        returning_picks = []
                        
                        for pick in new_picks:
                            key = f"{pick.get('player_name')}|{pick.get('stat_type')}|{pick.get('line')}"
                            if key in cache_map:
                                # Returning player with cached intel - merge it
                                pick.update(cache_map[key])
                                returning_picks.append(pick)
                            else:
                                # New player or missing intel - needs Gemini
                                delta_picks.append(pick)
                        
                        logger.info(f"  [{tier_name}] Diff result: {len(returning_picks)} cached, {len(delta_picks)} delta (need Gemini)")
                        
                        # Step 3: Call Gemini ONLY for delta picks
                        if delta_picks:
                            logger.info(f"  [{tier_name}] Firing Gemini batch for {len(delta_picks)} delta picks...")
                            enriched_delta = await vision_intel.analyze_tier_props(
                                delta_picks, tier_name, max_concurrent=3
                            )
                            # Merge enriched delta back
                            enriched_picks = returning_picks + enriched_delta
                        else:
                            logger.info(f"  [{tier_name}] No delta picks - skipping Gemini call (0 tokens used)")
                            enriched_picks = returning_picks
                        
                        # Step 4: Preserve original sort order
                        # Re-sort by original position in new_picks
                        pick_order = {f"{p.get('player_name')}|{p.get('stat_type')}|{p.get('line')}": i 
                                      for i, p in enumerate(new_picks)}
                        enriched_picks.sort(key=lambda p: pick_order.get(
                            f"{p.get('player_name')}|{p.get('stat_type')}|{p.get('line')}", 999
                        ))
                        
                        return enriched_picks
                    
                    # Apply diff check to all three tiers
                    top_safe_haven = await diff_check_and_enrich(
                        top_safe_haven, self.mlb_safe_haven, "Safe Haven"
                    )
                    top_front_lines = await diff_check_and_enrich(
                        top_front_lines, self.mlb_front_lines, "Front Lines"
                    )
                    top_war_zone = await diff_check_and_enrich(
                        top_war_zone, self.mlb_war_zone, "War Zone"
                    )
                    
                    # Summary stats
                    total_with_intel = sum(1 for p in top_safe_haven + top_front_lines + top_war_zone 
                                           if p.get("vision_intel"))
                    total_picks = len(top_safe_haven) + len(top_front_lines) + len(top_war_zone)
                    logger.info(f"[VISION INTEL] Diff Check complete: {total_with_intel}/{total_picks} picks have vision_intel")
                    
                else:
                    logger.info("[VISION INTEL] Service disabled - picks will use fallback summaries")
            except Exception as e:
                logger.warning(f"[VISION INTEL] Diff Check failed: {e} - continuing without vision_intel")
                import traceback
                logger.warning(traceback.format_exc())
            
            # Track used players
            for pick in top_safe_haven:
                used_goblin_players.add(pick.get('player_name'))
            for pick in top_war_zone:
                used_demon_players.add(pick.get('player_name'))
            
            # Build exclusion sets
            safe_haven_keys = set()
            for pick in top_safe_haven:
                key = (pick.get('player_name'), pick.get('stat_type'))
                safe_haven_keys.add(key)
            
            front_lines_keys = set()
            for pick in top_front_lines:
                key = (pick.get('player_name'), pick.get('stat_type'))
                front_lines_keys.add(key)
            
            all_used_keys = safe_haven_keys | front_lines_keys
            
            # =================================================================
            # PHASE 5: WRITE TIERS TO COLLECTIONS
            # =================================================================
            logger.info("[PHASE 5] WRITING TIERS TO COLLECTIONS...")
            
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
            
            # CIRCUIT BREAKER: Don't wipe collections if we have very few/no picks
            # This prevents empty DB scenarios from bad API responses
            total_new_picks = len(top_safe_haven) + len(top_front_lines) + len(top_war_zone)
            if total_new_picks == 0:
                logger.warning("[CIRCUIT BREAKER] No picks generated - preserving existing tier data!")
                results["circuit_breaker"] = {
                    "triggered": True,
                    "reason": "No picks generated from pipeline",
                    "action": "Preserved existing tier collections"
                }
                # Skip the delete/insert and just return current state
                existing_safe = await self.mlb_safe_haven.count_documents({})
                existing_front = await self.mlb_front_lines.count_documents({})
                existing_war = await self.mlb_war_zone.count_documents({})
                results["output"]["safe_haven"] = existing_safe
                results["output"]["front_lines"] = existing_front
                results["output"]["war_zone"] = existing_war
                results["output"]["total_picks"] = existing_safe + existing_front + existing_war
                results["output"]["preserved"] = True
            else:
                # Normal flow - ATOMIC SWAP using bulkWrite with upsert
                # This prevents race conditions where frontend sees empty collection
                
                async def atomic_upsert_tier(collection, picks, tier_name):
                    """Atomic upsert: Replace all docs without emptying collection."""
                    if not picks:
                        # No picks = clear collection (rare edge case)
                        await collection.delete_many({})
                        return 0
                    
                    from pymongo import UpdateOne, DeleteMany
                    
                    # Build unique keys for current picks
                    current_keys = set()
                    operations = []
                    
                    for pick in picks:
                        key = f"{pick.get('player_name')}|{pick.get('stat_type')}|{pick.get('line')}"
                        current_keys.add(key)
                        
                        # Upsert each pick (insert or replace)
                        operations.append(UpdateOne(
                            {
                                "player_name": pick.get("player_name"),
                                "stat_type": pick.get("stat_type"),
                                "line": pick.get("line")
                            },
                            {"$set": pick},
                            upsert=True
                        ))
                    
                    # Execute upserts first (collection always has data)
                    if operations:
                        await collection.bulk_write(operations, ordered=False)
                    
                    # Now delete stale picks that aren't in current batch
                    # Build filter for picks NOT in current set
                    all_docs = await collection.find({}, {"player_name": 1, "stat_type": 1, "line": 1}).to_list(length=100)
                    stale_ids = []
                    for doc in all_docs:
                        key = f"{doc.get('player_name')}|{doc.get('stat_type')}|{doc.get('line')}"
                        if key not in current_keys:
                            stale_ids.append(doc["_id"])
                    
                    if stale_ids:
                        await collection.delete_many({"_id": {"$in": stale_ids}})
                        logger.info(f"  [{tier_name}] Cleaned {len(stale_ids)} stale picks")
                    
                    return len(picks)
                
                # Atomic upsert all tiers (collection never empty)
                safe_count = await atomic_upsert_tier(self.mlb_safe_haven, top_safe_haven, "Safe Haven")
                front_count = await atomic_upsert_tier(self.mlb_front_lines, top_front_lines, "Front Lines")
                war_count = await atomic_upsert_tier(self.mlb_war_zone, top_war_zone, "War Zone")
                
                logger.info(f"  ATOMIC UPSERT complete: Safe={safe_count}, Front={front_count}, War={war_count}")
                
                # Store parlays (less critical, can use simple replace)
                parlays_collection = self.db.mlb_parlays
                await parlays_collection.delete_many({})
                if all_parlays:
                    logger.info(f"  Storing {len(all_parlays)} parlays to mlb_parlays collection...")
                    try:
                        insert_result = await parlays_collection.insert_many(all_parlays)
                        logger.info(f"  Successfully inserted {len(insert_result.inserted_ids)} parlays")
                    except Exception as e:
                        logger.error(f"  Parlay insert failed: {e}")
                
                # Update results
                results["output"]["safe_haven"] = len(top_safe_haven)
                results["output"]["front_lines"] = len(top_front_lines)
                results["output"]["war_zone"] = len(top_war_zone)
                results["output"]["total_picks"] = total_new_picks
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
    
    async def _enrich_picks_with_bdl_hit_rates(self, picks: list) -> list:
        """
        SSOT: Recalculate hit rates from BDL game logs for each pick.
        This ensures pick cards show SAME data as player detail pages.
        """
        if not picks:
            return picks
            
        # Load master hub data for all players in picks
        player_names = list(set(p.get('player_name') for p in picks if p.get('player_name')))
        
        hub_data = {}
        async for player in self.db.mlb_master_hub_2026.find(
            {"display_name": {"$in": player_names}},
            {"_id": 0, "display_name": 1, "bdl_game_logs": 1}
        ):
            hub_data[player.get('display_name')] = player.get('bdl_game_logs', [])
        
        # Stat type to log field mapping
        stat_map = {
            "PTS": "pts", "REB": "reb", "AST": "ast",
            "STL": "stl", "BLK": "blk", "3PM": "fg3m",
        }
        
        for pick in picks:
            player_name = pick.get('player_name')
            stat_type = pick.get('stat_type')
            line = pick.get('line', 0)
            
            game_logs = hub_data.get(player_name, [])
            if not game_logs or line <= 0:
                continue
            
            # Filter for games actually played
            played_games = [g for g in game_logs if g.get('min') and str(g.get('min', '0')).replace(':', '').isdigit()]
            
            # Get stat values
            if stat_type == 'PRA':
                values = [g.get('pts', 0) + g.get('reb', 0) + g.get('ast', 0) for g in played_games]
            elif stat_type == 'PR':
                values = [g.get('pts', 0) + g.get('reb', 0) for g in played_games]
            elif stat_type == 'PA':
                values = [g.get('pts', 0) + g.get('ast', 0) for g in played_games]
            elif stat_type == 'RA':
                values = [g.get('reb', 0) + g.get('ast', 0) for g in played_games]
            elif stat_type in stat_map:
                field = stat_map[stat_type]
                values = [g.get(field, 0) for g in played_games]
            else:
                continue
            
            if not values:
                continue
            
            # Calculate hit rates for THIS line (SSOT)
            l5_vals = values[:5]
            l10_vals = values[:10]
            l20_vals = values[:20]
            
            if l5_vals:
                l5_hits = sum(1 for v in l5_vals if v >= line)
                pick['h5_rate'] = round((l5_hits / len(l5_vals)) * 100, 1)
                pick['l5_avg'] = round(sum(l5_vals) / len(l5_vals), 1)
            
            if l10_vals:
                l10_hits = sum(1 for v in l10_vals if v >= line)
                pick['h10_rate'] = round((l10_hits / len(l10_vals)) * 100, 1)
                pick['l10_avg'] = round(sum(l10_vals) / len(l10_vals), 1)
            
            if l20_vals:
                l20_hits = sum(1 for v in l20_vals if v >= line)
                pick['h20_rate'] = round((l20_hits / len(l20_vals)) * 100, 1)
                pick['l20_avg'] = round(sum(l20_vals) / len(l20_vals), 1)
            
            if values:
                pick['season_avg'] = round(sum(values) / len(values), 1)
        
        return picks
    
    async def get_safe_haven(self, limit: int = 10) -> Dict[str, Any]:
        cursor = self.mlb_safe_haven.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        
        # SSOT: Recalculate hit rates from BDL (same as player detail page)
        picks = await self._enrich_picks_with_bdl_hit_rates(picks)
        
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
        cursor = self.mlb_front_lines.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        
        # SSOT: Recalculate hit rates from BDL (same as player detail page)
        picks = await self._enrich_picks_with_bdl_hit_rates(picks)
        
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
        cursor = self.mlb_war_zone.find({}, {"_id": 0}).limit(limit)
        picks = await cursor.to_list(length=limit)
        
        # SSOT: Recalculate hit rates from BDL (same as player detail page)
        picks = await self._enrich_picks_with_bdl_hit_rates(picks)
        
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
        
        # SSOT: Recalculate hit rates from BDL (same as player detail page)
        discarded = await self._enrich_picks_with_bdl_hit_rates(discarded)
        
        total = await self.ferrari_discarded.count_documents({})
        
        return {
            "discarded": discarded,
            "count": len(discarded),
            "total_discarded": total,
            "kill_switch": "5% Implied Probability Separation + Median Anchor"
        }


# Singleton
_ferrari_service = None

def get_mlb_tier_service(db=None):
    global _ferrari_service
    if _ferrari_service is None and db is not None:
        _ferrari_service = MLBTierService(db)
    return _ferrari_service
