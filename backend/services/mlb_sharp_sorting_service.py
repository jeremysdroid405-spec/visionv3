"""
MLB Sharp Sorting & Tier Distribution Service
==============================================
Advanced prop sorting using sharp book analysis.

Layers:
1. Pinnacle De-Vig: Calculate fair value probability from sharp odds
2. DraftKings Market Depth: Compare DK alt-lines to PrizePicks
3. Ferrari Final Sort: Classify into Goblins, Demons, Standard

MLB Safe Haven Tier Logic:
- DK Odds <= -240 (sweet spot for "0.5 Hits" or "4.5 Strikeouts" Goblins)
- ONLY Goblin props qualify
- 3-Gate System: Hit Rate (L20) + CV (Stability) + VK Edge/TP

Collections:
- mlb_goblins: Sharp odds ≤ -240 AND VK Projection > Line
- mlb_safe_haven: Goblins that pass 3-Gate qualification
- mlb_demons: VK Slope massive over + DK alt-line mispricing
- mlb_standard: Sharp and public agree (-110 to -130)
"""

import os
import logging
import math
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.db_config import get_collection_name

logger = logging.getLogger(__name__)

# =============================================================================
# MLB BALLPARK FACTORS - For War Zone Post-Filters
# =============================================================================
# Park Factor > 1.05 = Hitter's Haven (boosts offense)
# Park Factor < 0.95 = Pitcher's Park (suppresses offense)

HITTERS_HAVEN_PARKS = {
    "Coors Field": {"team": "COL", "factor": 1.28, "priority": 1},
    "Great American Ball Park": {"team": "CIN", "factor": 1.12, "priority": 2},
    "Fenway Park": {"team": "BOS", "factor": 1.08, "priority": 3},
    "Globe Life Field": {"team": "TEX", "factor": 1.07, "priority": 4},
    "Citizens Bank Park": {"team": "PHI", "factor": 1.06, "priority": 5},
    "Yankee Stadium": {"team": "NYY", "factor": 1.05, "priority": 6},
}

PITCHERS_PARKS = {
    "Oracle Park": {"team": "SF", "factor": 0.88},
    "Petco Park": {"team": "SD", "factor": 0.90},
    "T-Mobile Park": {"team": "SEA", "factor": 0.91},
    "Dodger Stadium": {"team": "LAD", "factor": 0.93},
    "Oakland Coliseum": {"team": "OAK", "factor": 0.93},
    "Tropicana Field": {"team": "TB", "factor": 0.94},
}

# Team abbreviation to park name mapping
TEAM_TO_PARK = {
    "COL": "Coors Field",
    "CIN": "Great American Ball Park",
    "BOS": "Fenway Park",
    "TEX": "Globe Life Field",
    "PHI": "Citizens Bank Park",
    "NYY": "Yankee Stadium",
    "SF": "Oracle Park",
    "SD": "Petco Park",
    "SEA": "T-Mobile Park",
    "LAD": "Dodger Stadium",
    "OAK": "Oakland Coliseum",
    "TB": "Tropicana Field",
}

# =============================================================================
# MLB BADGE THRESHOLDS - For Vision Intel Evaluation
# =============================================================================

MLB_BADGE_THRESHOLDS = {
    "barrel_master": {
        "barrel_pct_min": 15.0,  # Barrel % > 15% over L25 PA
        "hard_hit_pct_min": 40.0,  # Hard hit rate > 40%
    },
    "whiff_wizard": {
        "k_pct_min": 28.0,  # K% > 28%
        "swstr_pct_min": 12.0,  # Swinging strike % > 12%
    },
    "pure_contact": {
        "whiff_rate_max": 15.0,  # Whiff Rate < 15%
        "xba_min": 0.290,  # xBA > .290
    },
    "workhorse": {
        "deep_game_pct": 80.0,  # 80% of L10 reaching 6th inning
        "min_innings_avg": 5.5,
    },
}

# =============================================================================
# VOLATILITY INDEX SCORING
# =============================================================================
# Scale 1-10 where 10 = Extreme Volatility
# War Zone requires score > 8 for qualification

def calculate_volatility_index(cv: float, hit_rate: float, ceiling_stats: Dict) -> int:
    """
    Calculate Volatility Index (1-10) for War Zone qualification.
    
    Factors:
    - CV (Coefficient of Variation) - higher = more volatile
    - Hit Rate Variance - lower/inconsistent = more volatile  
    - Ceiling vs Floor spread - wider = more volatile
    
    Returns:
        int: Volatility index 1-10 (>8 = extreme, qualifies for War Zone)
    """
    score = 0
    
    # CV Score (0-4 points)
    if cv is not None:
        if cv > 1.2:
            score += 4
        elif cv > 1.0:
            score += 3
        elif cv > 0.8:
            score += 2
        elif cv > 0.6:
            score += 1
    
    # Hit Rate Variance Score (0-3 points)
    # Lower hit rates = more volatile
    if hit_rate is not None:
        if hit_rate < 30:
            score += 3
        elif hit_rate < 50:
            score += 2
        elif hit_rate < 70:
            score += 1
    
    # Ceiling/Floor Spread Score (0-3 points)
    if ceiling_stats:
        max_val = ceiling_stats.get("max_value", 0) or 0
        values = ceiling_stats.get("values", [])
        if values:
            min_val = min(values) if values else 0
            spread = max_val - min_val
            if spread >= 5:
                score += 3
            elif spread >= 3:
                score += 2
            elif spread >= 2:
                score += 1
    
    return min(10, max(1, score))


# MLB DK Odds threshold for Safe Haven
MLB_DK_SAFE_HAVEN_MAX = -240

# MLB DK Odds range for Front Lines
MLB_DK_FRONT_LINES_MIN = -240  # Exclusive (must be > -240)
MLB_DK_FRONT_LINES_MAX = -145  # Inclusive (must be <= -145)

# MLB DK Odds threshold for War Zone (underdog plays)
MLB_DK_WAR_ZONE_MIN = 150  # Must be > +150 for alt lines

# MLB Oracle Apex Gate Configuration
MLB_SAFE_HAVEN_GATES = {
    'Hits': {
        'max_cv': 0.60,
        'min_hit_rate': 16,  # 80% of L20
        'sample_size': 20,
        'min_edge': 15.0,
        'min_prob': 70.0,
    },
    'Total Bases': {
        'max_cv': 0.75,
        'min_hit_rate': 15,  # 75% of L20
        'sample_size': 20,
        'min_edge': 20.0,
        'min_prob': 70.0,
    },
    'Pitcher Strikeouts': {
        'max_cv': 0.45,
        'min_hit_rate': 15,  # 75% of L20
        'sample_size': 20,
        'min_edge': 12.0,
        'min_prob': 75.0,
    },
    'Pitching Outs': {
        'max_cv': 0.30,
        'min_hit_rate': 17,  # 85% of L20
        'sample_size': 20,
        'min_edge': 8.0,
        'min_prob': 80.0,
    },
    'Hits+Runs+RBIs': {
        'max_cv': 0.55,
        'min_hit_rate': 16,  # 80% of L20
        'sample_size': 20,
        'min_edge': 18.0,
        'min_prob': 70.0,
    },
}

# MLB Front Lines Gate Configuration (The Consistency Pivot)
MLB_FRONT_LINES_GATES = {
    'Hits': {
        'max_cv': 0.85,           # Higher variance allowed for streak hitters
        'min_hit_rate': 13,       # 65% of L20
        'pivot_min_hit_rate': 11, # Pivot rule: 11/20 OK if 8/10 L10
        'pivot_l10_threshold': 8, # Must hit 8/10 L10 for pivot
        'sample_size': 20,
        'min_edge': 10.0,
        'min_prob': 62.0,
    },
    'Total Bases': {
        'max_cv': 0.95,           # Highest variance - XBH streaks
        'min_hit_rate': 12,       # 60% of L20
        'pivot_min_hit_rate': 10,
        'pivot_l10_threshold': 7,
        'sample_size': 20,
        'min_edge': 15.0,
        'min_prob': 62.0,
    },
    'Pitcher Strikeouts': {
        'max_cv': 0.60,
        'min_hit_rate': 13,       # 65% of L20
        'pivot_min_hit_rate': 11,
        'pivot_l10_threshold': 8,
        'sample_size': 20,
        'min_edge': 10.0,
        'min_prob': 65.0,
    },
    'Pitching Outs': {
        'max_cv': 0.50,           # Stricter for outs
        'min_hit_rate': 14,       # 70% of L20
        'pivot_min_hit_rate': 12,
        'pivot_l10_threshold': 8,
        'sample_size': 20,
        'min_edge': 6.0,
        'min_prob': 70.0,
    },
    'Hits+Runs+RBIs': {
        'max_cv': 0.75,
        'min_hit_rate': 13,       # 65% of L20
        'pivot_min_hit_rate': 11,
        'pivot_l10_threshold': 8,
        'sample_size': 20,
        'min_edge': 12.0,
        'min_prob': 62.0,
    },
    'DEFAULT': {
        'max_cv': 0.85,
        'min_hit_rate': 12,
        'pivot_min_hit_rate': 10,
        'pivot_l10_threshold': 7,
        'sample_size': 20,
        'min_edge': 10.0,
        'min_prob': 60.0,
    },
}

# MLB War Zone Gate Configuration (The "Ceiling" Protocol)
# High CV is ENCOURAGED - we want explosive, pendulum players
MLB_WAR_ZONE_GATES = {
    'Hits': {
        'max_cv': 1.10,           # High variance encouraged
        'min_cv': 0.0,            # No minimum - but CV > 1.0 fast-tracked
        'min_hit_rate': 8,        # 40% of L20 (we want ceiling games)
        'boom_threshold': 2,      # Must surpass line 2x in L15
        'sample_size': 20,
        'min_edge': 25.0,         # Moonshot edge
        'min_prob': 45.0,         # Lower prob = higher payout
    },
    'Total Bases': {
        'max_cv': 1.25,           # Very high variance for XBH demons
        'min_cv': 0.0,
        'min_hit_rate': 7,        # 35% of L20
        'boom_threshold': 2,
        'sample_size': 20,
        'min_edge': 35.0,
        'min_prob': 40.0,
    },
    'Pitcher Strikeouts': {
        'max_cv': 0.85,
        'min_cv': 0.0,
        'min_hit_rate': 10,       # 50% of L20
        'boom_threshold': 2,
        'sample_size': 20,
        'min_edge': 20.0,
        'min_prob': 50.0,
    },
    'Pitching Outs': {
        'max_cv': 0.70,
        'min_cv': 0.0,
        'min_hit_rate': 12,       # 60% of L20
        'boom_threshold': 2,
        'sample_size': 20,
        'min_edge': 15.0,
        'min_prob': 55.0,
    },
    'Hits+Runs+RBIs': {
        'max_cv': 1.00,
        'min_cv': 0.0,
        'min_hit_rate': 9,        # 45% of L20
        'boom_threshold': 2,
        'sample_size': 20,
        'min_edge': 30.0,
        'min_prob': 45.0,
    },
    'DEFAULT': {
        'max_cv': 1.10,
        'min_cv': 0.0,
        'min_hit_rate': 8,
        'boom_threshold': 2,
        'sample_size': 20,
        'min_edge': 25.0,
        'min_prob': 45.0,
    },
}


class MLBSharpSortingService:
    """
    MLB Sharp Sorting & Tier Distribution.
    
    Uses Pinnacle (sharp) odds to identify value and classify props.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self._player_logs_cache = {}  # Cache for player historical logs
    
    # =========================================================================
    # MLB HIT RATE CALCULATION FROM GAME LOGS
    # =========================================================================
    
    async def _load_player_logs_cache(self):
        """Load all player game logs from mlb_master_hub_2026 into cache for fast lookup.
        
        SSOT: Uses mlb_master_hub_2026.bdl_game_logs as the single source of truth.
        This ensures consistency between pick cards, player detail views, and hit rate calculations.
        IMPORTANT: Filters to CURRENT SEASON (2026) only.
        """
        if self._player_logs_cache:
            return  # Already loaded
        
        from datetime import datetime
        current_season = datetime.now().year
        
        try:
            # SSOT: mlb_master_hub_2026.bdl_game_logs
            master_hub = self.db["mlb_master_hub_2026"]
            all_players = await master_hub.find(
                {"bdl_game_logs": {"$exists": True, "$ne": []}},
                {"_id": 0, "display_name": 1, "bdl_game_logs": 1}
            ).to_list(length=None)
            
            for player_doc in all_players:
                player_name = player_doc.get("display_name", "").lower().strip()
                if player_name:
                    all_logs = player_doc.get("bdl_game_logs", [])
                    
                    # CRITICAL: Filter to current season only
                    current_season_logs = [
                        log for log in all_logs
                        if log.get("season") == current_season or 
                           (log.get("date", "")[:4] == str(current_season))
                    ]
                    
                    self._player_logs_cache[player_name] = current_season_logs
            logger.info(f"[SHARP_SORT] Loaded game logs from mlb_master_hub_2026 for {len(self._player_logs_cache)} MLB players (filtered to {current_season} season)")
        except Exception as e:
            logger.warning(f"[SHARP_SORT] Failed to load player logs cache: {e}")
    
    def calculate_mlb_hit_rates(self, player_name: str, stat_type: str, line: float) -> Dict[str, Any]:
        """
        Calculate L5/L10 hit rates from MLB historical game logs.
        
        Args:
            player_name: Player name to look up
            stat_type: MLB stat type (e.g., "Hits", "Total Bases", "RBIs", etc.)
            line: The prop line to compare against
            
        Returns:
            Dict with h5_rate, h10_rate, l5_avg, l10_avg, season_avg
        """
        default_result = {
            "h5_rate": None,
            "h10_rate": None,
            "l5_avg": None,
            "l10_avg": None,
            "season_avg": None
        }
        
        if not player_name or not line:
            return default_result
        
        # Look up player logs
        player_key = player_name.lower().strip()
        game_logs = self._player_logs_cache.get(player_key, [])
        
        if not game_logs:
            return default_result
        
        # Map stat type to game log field
        stat_map = {
            "hits": "hits",
            "total bases": "total_bases",
            "rbis": "rbis",
            "runs": "runs",
            "home runs": "home_runs",
            "stolen bases": "stolen_bases",
            "walks": "walks",
            "strikeouts": "strikeouts",
            "hits+runs+rbis": ["hits", "runs", "rbis"],  # Combo stat
            "pitcher strikeouts": "pitcher_strikeouts",
            "pitching outs": "innings_pitched",  # IP * 3
            "earned runs": "earned_runs",
            "hits allowed": "hits_allowed",
            "walks allowed": "pitcher_walks",
        }
        
        stat_key = stat_type.lower().strip()
        log_field = stat_map.get(stat_key, stat_key.replace(" ", "_"))
        
        # Sort logs by date descending
        try:
            sorted_logs = sorted(
                game_logs, 
                key=lambda x: x.get("date", "") or "", 
                reverse=True
            )
        except Exception:
            sorted_logs = game_logs
        
        def get_stat_value(game, field):
            """Extract stat value from game log, handling combo stats.
            
            Returns None if value is missing (to be skipped in calculation).
            """
            if isinstance(field, list):
                # Combo stat - all components must exist
                combo_val = 0
                for f in field:
                    v = game.get(f)
                    if v is None:
                        return None  # Skip games with missing combo components
                    combo_val += (v or 0)
                return combo_val
            else:
                val = game.get(field)
                if val is None:
                    return None  # Skip games with missing data
                # Special handling for pitching outs (IP * 3)
                if field == "innings_pitched" and val:
                    return round(val * 3)
                return val or 0
        
        def calc_stats(game_list):
            """Calculate avg and hit rate for a set of games.
            
            SSOT: Skips games with None/missing values (consistent with player detail endpoint).
            """
            if not game_list:
                return 0, 0
            
            values = []
            hits = 0
            for g in game_list:
                val = get_stat_value(g, log_field)
                if val is None:
                    continue  # Skip games with missing data
                values.append(val)
                if line and val >= line:  # >= for "over" comparison
                    hits += 1
            
            if not values:
                return 0, 0
            
            avg = sum(values) / len(values)
            hit_rate = (hits / len(values) * 100)
            return avg, hit_rate
        
        # Calculate L5, L10, and season stats
        l5_avg, h5_rate = calc_stats(sorted_logs[:5])
        l10_avg, h10_rate = calc_stats(sorted_logs[:10])
        season_avg, _ = calc_stats(sorted_logs)
        
        return {
            "h5_rate": round(h5_rate) if h5_rate else None,
            "h10_rate": round(h10_rate) if h10_rate else None,
            "l5_avg": round(l5_avg, 1) if l5_avg else None,
            "l10_avg": round(l10_avg, 1) if l10_avg else None,
            "season_avg": round(season_avg, 1) if season_avg else None
        }
    
    def calculate_cv(self, player_name: str, stat_type: str) -> Optional[float]:
        """Calculate Coefficient of Variation for a player's stat over L20."""
        player_key = player_name.lower().strip()
        game_logs = self._player_logs_cache.get(player_key, [])
        
        if len(game_logs) < 5:
            return None
        
        # Map stat type to log field
        stat_map = {
            "Hits": "hits",
            "Total Bases": "total_bases",
            "RBIs": "rbis",
            "Runs": "runs",
            "Stolen Bases": "stolen_bases",
            "Home Runs": "home_runs",
            "Pitcher Strikeouts": "pitcher_strikeouts",
            "Pitching Outs": "innings_pitched",
            "Hits+Runs+RBIs": ["hits", "runs", "rbis"],
        }
        
        log_field = stat_map.get(stat_type)
        if not log_field:
            return None
        
        # Sort by date and get L20
        sorted_logs = sorted(
            game_logs,
            key=lambda x: x.get("date", "") or "",
            reverse=True
        )[:20]
        
        values = []
        for g in sorted_logs:
            if isinstance(log_field, list):
                val = sum((g.get(f) or 0) for f in log_field)
            else:
                val = g.get(log_field)
                if val is None:
                    continue
                if log_field == "innings_pitched":
                    val = val * 3  # Convert to outs
            values.append(val)
        
        if len(values) < 5:
            return None
        
        mean = sum(values) / len(values)
        if mean == 0:
            return None
        
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        std_dev = math.sqrt(variance)
        cv = std_dev / mean
        
        return round(cv, 3)
    
    def check_safe_haven_gates(
        self, 
        prop: Dict, 
        hit_rates: Dict,
        cv: Optional[float]
    ) -> Tuple[bool, str, Dict]:
        """
        Check if a prop passes MLB Safe Haven 3-Gate qualification.
        
        Gates:
        1. Hit Rate (L20): Must hit prop X% of last 20 games (stat-specific)
        2. CV (Stability): Must be below stat-specific threshold
        3. VK Edge + TP: Projection edge and sharp probability thresholds
        
        Returns:
            Tuple of (passes, reason, gate_results)
        """
        stat_type = prop.get("stat_type", "")
        
        # Get gate config for this stat type
        gate_config = MLB_SAFE_HAVEN_GATES.get(stat_type, MLB_SAFE_HAVEN_GATES.get('Hits'))
        
        gate_results = {
            "gate1_hit_rate": {"passed": False, "value": None, "threshold": gate_config['min_hit_rate']},
            "gate2_cv": {"passed": False, "value": None, "threshold": gate_config['max_cv']},
            "gate3_edge": {"passed": False, "value": None, "threshold": gate_config['min_edge']},
        }
        
        # GATE 1: Hit Rate Check (L20 -> using L10 * 2 as proxy if L20 not available)
        h10_rate = hit_rates.get("h10_rate")
        if h10_rate is not None:
            # Scale L10 to approximate L20 (conservative)
            approx_l20_hits = (h10_rate / 100) * 20
            gate_results["gate1_hit_rate"]["value"] = round(approx_l20_hits, 1)
            gate_results["gate1_hit_rate"]["passed"] = approx_l20_hits >= gate_config['min_hit_rate']
        
        if not gate_results["gate1_hit_rate"]["passed"]:
            return False, f"GATE1_FAIL: Hit rate {gate_results['gate1_hit_rate']['value']}/20 < {gate_config['min_hit_rate']}/20 required", gate_results
        
        # GATE 2: CV (Stability) Check
        if cv is not None:
            gate_results["gate2_cv"]["value"] = cv
            gate_results["gate2_cv"]["passed"] = cv <= gate_config['max_cv']
        else:
            # No CV data - use fallback based on hit rate
            gate_results["gate2_cv"]["passed"] = h10_rate and h10_rate >= 70
            gate_results["gate2_cv"]["value"] = "N/A (HR fallback)"
        
        if not gate_results["gate2_cv"]["passed"]:
            return False, f"GATE2_FAIL: CV {cv} > {gate_config['max_cv']} max allowed", gate_results
        
        # GATE 3: VK Edge + True Probability
        edge_pct = prop.get("edge_pct") or 0
        
        # Calculate True Probability - prefer Pinnacle, fallback to DK odds
        sharp_fair_value = prop.get("sharp_fair_value")
        if sharp_fair_value is None or sharp_fair_value == 0.5:
            # No Pinnacle odds - use DK odds to estimate TP
            dk_odds = prop.get("all_odds", {}).get("draftkings")
            if dk_odds and dk_odds < 0:
                # Convert DK American odds to implied probability
                # For negative odds: prob = |odds| / (|odds| + 100)
                tp_prob = abs(dk_odds) / (abs(dk_odds) + 100) * 100
            else:
                tp_prob = 50.0  # Default fallback
        else:
            tp_prob = sharp_fair_value * 100
        
        gate_results["gate3_edge"]["value"] = {"edge": edge_pct, "tp_prob": round(tp_prob, 1)}
        
        passes_edge = edge_pct >= gate_config['min_edge']
        passes_prob = tp_prob >= gate_config['min_prob']
        
        gate_results["gate3_edge"]["passed"] = passes_edge and passes_prob
        
        if not passes_edge:
            return False, f"GATE3_FAIL: Edge {edge_pct}% < {gate_config['min_edge']}% required", gate_results
        if not passes_prob:
            return False, f"GATE3_FAIL: TP {tp_prob:.1f}% < {gate_config['min_prob']}% required", gate_results
        
        return True, "ALL_GATES_PASSED", gate_results
    
    def check_front_lines_gates(
        self, 
        prop: Dict, 
        hit_rates: Dict,
        cv: Optional[float]
    ) -> Tuple[bool, str, Dict]:
        """
        Check if a prop passes MLB Front Lines 3-Gate qualification.
        
        Front Lines captures high-value mid-juice props (-240 < odds <= -145).
        
        Gates (The Consistency Pivot):
        1. Hit Rate (L20) with Pivot Rule: 11/20 OK if 8/10 in L10
        2. CV (Stability): Higher variance allowed for streak hitters
        3. VK Edge + TP: Edge > 10%, TP >= 62%
        
        Returns:
            Tuple of (passes, reason, gate_results)
        """
        stat_type = prop.get("stat_type", "")
        
        # Get gate config for this stat type
        gate_config = MLB_FRONT_LINES_GATES.get(stat_type, MLB_FRONT_LINES_GATES.get('DEFAULT'))
        
        gate_results = {
            "gate1_hit_rate": {"passed": False, "value": None, "threshold": gate_config['min_hit_rate'], "pivot_used": False},
            "gate2_cv": {"passed": False, "value": None, "threshold": gate_config['max_cv']},
            "gate3_edge": {"passed": False, "value": None, "threshold": gate_config['min_edge']},
        }
        
        # GATE 1: Hit Rate Check with Pivot Rule
        h10_rate = hit_rates.get("h10_rate")
        
        if h10_rate is not None:
            # Scale L10 to approximate L20
            approx_l20_hits = (h10_rate / 100) * 20
            gate_results["gate1_hit_rate"]["value"] = round(approx_l20_hits, 1)
            
            # Check standard threshold
            if approx_l20_hits >= gate_config['min_hit_rate']:
                gate_results["gate1_hit_rate"]["passed"] = True
            else:
                # Check Pivot Rule: Lower L20 OK if high L10 recency
                # 11/20 acceptable IF 8/10 in L10
                l10_hits = round((h10_rate / 100) * 10)
                pivot_l10_threshold = gate_config.get('pivot_l10_threshold', 8)
                pivot_min_hr = gate_config.get('pivot_min_hit_rate', 11)
                
                if approx_l20_hits >= pivot_min_hr and l10_hits >= pivot_l10_threshold:
                    gate_results["gate1_hit_rate"]["passed"] = True
                    gate_results["gate1_hit_rate"]["pivot_used"] = True
                    gate_results["gate1_hit_rate"]["pivot_detail"] = f"Pivot: {approx_l20_hits}/20 with {l10_hits}/10 L10"
        
        if not gate_results["gate1_hit_rate"]["passed"]:
            return False, f"GATE1_FAIL: Hit rate {gate_results['gate1_hit_rate']['value']}/20 < {gate_config['min_hit_rate']}/20 (pivot requires {gate_config.get('pivot_l10_threshold', 8)}/10 L10)", gate_results
        
        # GATE 2: CV (Stability) Check - Higher variance allowed for streak hitters
        if cv is not None:
            gate_results["gate2_cv"]["value"] = cv
            gate_results["gate2_cv"]["passed"] = cv <= gate_config['max_cv']
        else:
            # No CV data - more lenient fallback for Front Lines
            gate_results["gate2_cv"]["passed"] = h10_rate and h10_rate >= 60
            gate_results["gate2_cv"]["value"] = "N/A (HR fallback)"
        
        if not gate_results["gate2_cv"]["passed"]:
            return False, f"GATE2_FAIL: CV {cv} > {gate_config['max_cv']} max allowed", gate_results
        
        # GATE 3: VK Edge + True Probability (TP Buffer)
        edge_pct = prop.get("edge_pct") or 0
        
        # Calculate True Probability - prefer Pinnacle, fallback to DK odds
        sharp_fair_value = prop.get("sharp_fair_value")
        if sharp_fair_value is None or sharp_fair_value == 0.5:
            dk_odds = prop.get("all_odds", {}).get("draftkings")
            if dk_odds and dk_odds < 0:
                tp_prob = abs(dk_odds) / (abs(dk_odds) + 100) * 100
            else:
                tp_prob = 50.0
        else:
            tp_prob = sharp_fair_value * 100
        
        gate_results["gate3_edge"]["value"] = {"edge": edge_pct, "tp_prob": round(tp_prob, 1)}
        
        passes_edge = edge_pct >= gate_config['min_edge']
        passes_prob = tp_prob >= gate_config['min_prob']
        
        gate_results["gate3_edge"]["passed"] = passes_edge and passes_prob
        
        if not passes_edge:
            return False, f"GATE3_FAIL: Edge {edge_pct}% < {gate_config['min_edge']}% required", gate_results
        if not passes_prob:
            return False, f"GATE3_FAIL: TP {tp_prob:.1f}% < {gate_config['min_prob']}% required", gate_results
        
        return True, "ALL_GATES_PASSED", gate_results
    
    def calculate_ceiling_stats(self, player_name: str, stat_type: str, line: float) -> Dict:
        """
        Calculate ceiling statistics for War Zone qualification.
        
        Returns:
            Dict with max_value, ceiling_90th, boom_count (times surpassed line in L15)
        """
        player_key = player_name.lower().strip()
        game_logs = self._player_logs_cache.get(player_key, [])
        
        if len(game_logs) < 5:
            return {"max_value": None, "ceiling_90th": None, "boom_count": 0, "values": []}
        
        # Map stat type to log field
        stat_map = {
            "Hits": "hits",
            "Total Bases": "total_bases",
            "RBIs": "rbis",
            "Runs": "runs",
            "Stolen Bases": "stolen_bases",
            "Home Runs": "home_runs",
            "Pitcher Strikeouts": "pitcher_strikeouts",
            "Pitching Outs": "innings_pitched",
            "Hits+Runs+RBIs": ["hits", "runs", "rbis"],
        }
        
        log_field = stat_map.get(stat_type)
        if not log_field:
            return {"max_value": None, "ceiling_90th": None, "boom_count": 0, "values": []}
        
        # Sort by date and get L20
        sorted_logs = sorted(
            game_logs,
            key=lambda x: x.get("date", "") or "",
            reverse=True
        )[:20]
        
        values = []
        for g in sorted_logs:
            if isinstance(log_field, list):
                # Combo stat - all components must exist
                combo_vals = [g.get(f) for f in log_field]
                if any(v is None for v in combo_vals):
                    continue
                val = sum(v or 0 for v in combo_vals)
            else:
                val = g.get(log_field)
                if val is None:
                    continue
                if log_field == "innings_pitched":
                    val = val * 3  # Convert to outs
            values.append(val)
        
        if len(values) < 5:
            return {"max_value": None, "ceiling_90th": None, "boom_count": 0, "values": []}
        
        # Calculate ceiling stats
        max_value = max(values)
        
        # 90th percentile ceiling
        sorted_values = sorted(values, reverse=True)
        ceiling_idx = max(0, int(len(sorted_values) * 0.1))  # Top 10%
        ceiling_90th = sorted_values[ceiling_idx] if ceiling_idx < len(sorted_values) else max_value
        
        # Boom count: times surpassed line in L15
        l15_values = values[:15]
        boom_count = sum(1 for v in l15_values if v >= line)
        
        return {
            "max_value": max_value,
            "ceiling_90th": ceiling_90th,
            "boom_count": boom_count,
            "values": values[:10],  # Return L10 for display
            "max_upside_pct": round(((ceiling_90th - line) / line) * 100, 1) if line > 0 else 0
        }
    
    def check_war_zone_gates(
        self, 
        prop: Dict, 
        hit_rates: Dict,
        cv: Optional[float],
        ceiling_stats: Dict
    ) -> Tuple[bool, str, Dict]:
        """
        Check if a prop passes MLB War Zone 3-Gate qualification (The "Ceiling" Protocol).
        
        War Zone is for "Lottery Tickets" - high-variance Demons with explosive potential.
        High CV is ENCOURAGED (CV > 1.0 = fast-tracked).
        
        Gates:
        1. Ceiling Hit Rate: Must surpass line at least 2x in L15 ("Boom Rule")
        2. CV (Volatility): High CV encouraged - "Swing" rule fast-tracks CV > 1.0
        3. Moonshot Edge: 90th percentile ceiling must be 35%+ above line
        
        Returns:
            Tuple of (passes, reason, gate_results)
        """
        stat_type = prop.get("stat_type", "")
        line = prop.get("line", 0)
        
        # Get gate config for this stat type
        gate_config = MLB_WAR_ZONE_GATES.get(stat_type, MLB_WAR_ZONE_GATES.get('DEFAULT'))
        
        gate_results = {
            "gate1_ceiling": {"passed": False, "value": None, "threshold": gate_config['boom_threshold']},
            "gate2_volatility": {"passed": False, "value": None, "fast_tracked": False},
            "gate3_moonshot": {"passed": False, "value": None, "threshold": gate_config['min_edge']},
        }
        
        # GATE 1: "Boom Rule" - Must surpass line at least 2x in L15
        boom_count = ceiling_stats.get("boom_count", 0)
        max_value = ceiling_stats.get("max_value")
        
        gate_results["gate1_ceiling"]["value"] = {
            "boom_count": boom_count,
            "max_value": max_value,
            "line": line
        }
        gate_results["gate1_ceiling"]["passed"] = boom_count >= gate_config['boom_threshold']
        
        if not gate_results["gate1_ceiling"]["passed"]:
            return False, f"GATE1_FAIL: Boom count {boom_count} < {gate_config['boom_threshold']} (surpassed line only {boom_count}x in L15)", gate_results
        
        # GATE 2: "Swing Rule" - High volatility encouraged
        # CV > 1.0 = fast-tracked as high-upside candidate
        if cv is not None:
            gate_results["gate2_volatility"]["value"] = cv
            # High CV is GOOD for War Zone - fast-track if CV > 1.0
            if cv > 1.0:
                gate_results["gate2_volatility"]["passed"] = True
                gate_results["gate2_volatility"]["fast_tracked"] = True
            else:
                # Still passes if CV is reasonable (below max threshold)
                gate_results["gate2_volatility"]["passed"] = cv <= gate_config['max_cv']
        else:
            # No CV data - use hit rate volatility as proxy
            h10_rate = hit_rates.get("h10_rate") or 50
            # Highly variable hit rates (not 80-90%) suggest volatile player
            gate_results["gate2_volatility"]["passed"] = h10_rate < 70
            gate_results["gate2_volatility"]["value"] = f"N/A (HR proxy: {h10_rate}%)"
        
        if not gate_results["gate2_volatility"]["passed"]:
            return False, f"GATE2_FAIL: CV {cv} > {gate_config['max_cv']} (too stable for War Zone)", gate_results
        
        # GATE 3: "Moonshot Edge" - 90th percentile ceiling must be significantly above line
        ceiling_90th = ceiling_stats.get("ceiling_90th", 0)
        max_upside_pct = ceiling_stats.get("max_upside_pct", 0)
        
        # Calculate moonshot edge: how far above line is the ceiling?
        if line > 0 and ceiling_90th:
            moonshot_edge = ((ceiling_90th - line) / line) * 100
        else:
            moonshot_edge = 0
        
        gate_results["gate3_moonshot"]["value"] = {
            "ceiling_90th": ceiling_90th,
            "line": line,
            "moonshot_edge": round(moonshot_edge, 1),
            "max_upside_pct": max_upside_pct
        }
        
        gate_results["gate3_moonshot"]["passed"] = moonshot_edge >= gate_config['min_edge']
        
        if not gate_results["gate3_moonshot"]["passed"]:
            return False, f"GATE3_FAIL: Moonshot edge {moonshot_edge:.1f}% < {gate_config['min_edge']}% required", gate_results
        
        return True, "ALL_GATES_PASSED", gate_results
    
    # =========================================================================
    # PINNACLE DE-VIG CALCULATIONS
    # =========================================================================
    
    def american_to_decimal(self, american_odds: int) -> float:
        """Convert American odds to decimal odds."""
        if american_odds is None:
            return 2.0  # Default -110 equivalent
        
        if american_odds > 0:
            return (american_odds / 100) + 1
        else:
            return (100 / abs(american_odds)) + 1
    
    def decimal_to_implied_prob(self, decimal_odds: float) -> float:
        """Convert decimal odds to implied probability."""
        if decimal_odds <= 0:
            return 0.5
        return 1 / decimal_odds
    
    def remove_vig(self, over_odds: int, under_odds: int) -> Tuple[float, float]:
        """
        Remove the vig from a two-way market to get fair probabilities.
        
        Uses the additive method: Fair Prob = Implied Prob / Sum of Implied Probs
        
        Args:
            over_odds: American odds for OVER
            under_odds: American odds for UNDER
            
        Returns:
            Tuple of (over_fair_prob, under_fair_prob)
        """
        over_decimal = self.american_to_decimal(over_odds)
        under_decimal = self.american_to_decimal(under_odds)
        
        over_implied = self.decimal_to_implied_prob(over_decimal)
        under_implied = self.decimal_to_implied_prob(under_decimal)
        
        total_implied = over_implied + under_implied
        
        if total_implied == 0:
            return 0.5, 0.5
        
        over_fair = over_implied / total_implied
        under_fair = under_implied / total_implied
        
        return round(over_fair, 4), round(under_fair, 4)
    
    def calculate_fair_value(self, odds: int) -> float:
        """
        Calculate fair value probability from single-side odds.
        
        Assumes standard -110/-110 vig (~4.5% total).
        Removes estimated vig to get fair probability.
        """
        decimal_odds = self.american_to_decimal(odds)
        implied_prob = self.decimal_to_implied_prob(decimal_odds)
        
        # Estimate vig removal (assuming ~4.5% total vig on two-way)
        # Fair prob ≈ implied_prob / 1.045
        fair_prob = implied_prob / 1.045
        
        return round(min(fair_prob, 1.0), 4)
    
    def is_sharp_goblin(self, sharp_odds: int, direction: str) -> bool:
        """
        Check if prop qualifies as Sharp Goblin.
        
        Criteria: Sharp odds ≤ -180 (implies >64% fair probability after de-vig)
        
        Note: -240 is too strict for typical Pinnacle data.
        Using -180 which implies ~62% fair value after vig removal.
        """
        if sharp_odds is None:
            return False
        
        # -180 American = ~64.3% implied
        # After de-vig, this is ~61.5% fair
        return sharp_odds <= -180
    
    # =========================================================================
    # DRAFTKINGS MARKET DEPTH ANALYSIS
    # =========================================================================
    
    def analyze_dk_vs_pp(
        self,
        dk_line: float,
        dk_odds: int,
        pp_line: float,
        pp_odds: int = -110
    ) -> Dict[str, Any]:
        """
        Compare DraftKings line to PrizePicks.
        
        Identifies mispricing where DK alt-line suggests PP is mispriced.
        
        Args:
            dk_line: DraftKings line
            dk_odds: DraftKings American odds
            pp_line: PrizePicks line
            pp_odds: PrizePicks implied odds (usually -110 equivalent)
            
        Returns:
            Analysis dict with mispricing detection
        """
        if dk_line is None or pp_line is None:
            return {"is_demon": False, "mispricing": None}
        
        line_diff = dk_line - pp_line
        
        # Convert to implied probabilities
        dk_implied = self.decimal_to_implied_prob(self.american_to_decimal(dk_odds or -110))
        pp_implied = self.decimal_to_implied_prob(self.american_to_decimal(pp_odds))
        
        # Calculate mispricing
        # If DK has +180 (35.7% implied) but PP is -110 (47.6% implied)
        # That's a 12% edge on PP
        mispricing = pp_implied - dk_implied
        
        # Demon criteria: PP is significantly overvalued compared to DK
        # DK at +180 (~36%) vs PP equivalent at +400 (~20%)
        # This means PP thinks it's MORE likely than DK
        is_demon = mispricing > 0.10 and dk_odds >= 150  # DK is plus money but PP is favored
        
        return {
            "is_demon": is_demon,
            "mispricing": round(mispricing * 100, 2),  # Percentage
            "dk_implied": round(dk_implied * 100, 2),
            "pp_implied": round(pp_implied * 100, 2),
            "line_diff": line_diff
        }
    
    # =========================================================================
    # TIER CLASSIFICATION
    # =========================================================================
    
    def classify_prop(
        self,
        prop: Dict,
        vk_projection: Dict = None
    ) -> str:
        """
        Classify a prop into Goblin, Demon, or Standard tier.
        
        UPDATED CRITERIA (based on real Pinnacle data ranges):
        - GOBLIN: Sharp odds ≤ -150 AND VK Projection aligns with direction
                  OR Sharp Fair Value > 58% AND VK confirms
        - DEMON: DK/PP line discrepancy > 0.5 AND high edge
        - STANDARD: Sharp and public agree in -130 to +110 range
        
        Args:
            prop: Prop data with all_odds, sharp_line, etc.
            vk_projection: VK regression projection data
            
        Returns:
            Tier name: "GOBLIN", "DEMON", or "STANDARD"
        """
        sharp_odds = None
        all_odds = prop.get("all_odds", {})
        
        # Get Pinnacle (sharp) odds
        if "pinnacle" in all_odds:
            sharp_odds = all_odds.get("pinnacle")
        
        direction = prop.get("recommendation", "OVER")
        line = prop.get("line", 0)
        projected_value = vk_projection.get("projected_value") if vk_projection else prop.get("projected_value")
        # Note: edge_pct and hit_rate available in vk_projection but not used in current classification logic
        
        # =================================================================
        # GOBLIN CHECK: PP odds-based (negative odds = favorable)
        # Since Pinnacle doesn't offer MLB props, use PP classification
        # =================================================================
        # Check the is_goblin flag (set during sync based on PP odds < 0)
        if prop.get("is_goblin"):
            return "GOBLIN"
        
        # Alternative: Check PP odds directly
        pp_odds = prop.get("pp_odds")
        if pp_odds is not None and pp_odds < 0:
            # Favorable PP odds = Goblin
            # Also require some VK confirmation if available
            vk_confirms = True
            if projected_value and line:
                if direction == "OVER" and projected_value <= line:
                    vk_confirms = False
                elif direction == "UNDER" and projected_value >= line:
                    vk_confirms = False
            
            if vk_confirms:
                return "GOBLIN"
        
        # Sharp odds classification (if Pinnacle data available - usually not for MLB)
        if sharp_odds is not None and sharp_odds <= -150:
            vk_confirms = False
            if direction == "OVER" and projected_value and projected_value > line:
                vk_confirms = True
            elif direction == "UNDER" and projected_value and projected_value < line:
                vk_confirms = True
            
            if vk_confirms:
                return "GOBLIN"
        
        # =================================================================
        # DEMON CHECK: PP odds >= +100 or significant line discrepancy
        # =================================================================
        # Check the is_demon flag (set during sync based on PP odds >= +100)
        if prop.get("is_demon"):
            return "DEMON"
        
        # Alternative: Check PP odds directly
        if pp_odds is not None and pp_odds >= 100:
            return "DEMON"
        
        # =================================================================
        # STANDARD CHECK: Books agree on the line
        # =================================================================
        if sharp_odds is not None:
            # Standard: Sharp odds in the -130 to +110 range (neutral pricing)
            if -130 <= sharp_odds <= 110:
                return "STANDARD"
        
        # Fallback: Check DK odds for standard classification
        dk_odds = all_odds.get("draftkings")
        if dk_odds is not None:
            if -130 <= dk_odds <= 110:
                return "STANDARD"
        
        return "UNCLASSIFIED"
    
    # =========================================================================
    # MAIN SORTING PROCESS
    # =========================================================================
    
    async def run_sharp_sorting(
        self,
        stat_types: List[str] = None,
        save_to_db: bool = True
    ) -> Dict[str, Any]:
        """
        Run Sharp Sorting on all MLB props.
        
        Process:
        1. Fetch all props from mlb_live_props
        2. For each prop, calculate Pinnacle fair value
        3. Analyze DK vs PP market depth
        4. Classify into Goblins, Demons, Standard
        5. Save to respective collections
        
        Args:
            stat_types: Filter to specific stat types (e.g., ["Hits+Runs+RBIs", "Total Bases"])
            save_to_db: Whether to save results to collections
            
        Returns:
            Sorting results summary
        """
        logger.info("=" * 70)
        logger.info("[SHARP_SORT] Starting MLB Sharp Sorting & Tier Distribution")
        logger.info("=" * 70)
        
        start_time = datetime.now(timezone.utc)
        
        results = {
            "success": True,
            "started_at": start_time.isoformat(),
            "props_processed": 0,
            "goblins": [],
            "demons": [],
            "standard": [],
            "unclassified": 0,
            "stats": {
                "total_with_sharp_odds": 0,
                "total_with_dk_line": 0,
                "sharp_fair_value_avg": 0,
            },
            "errors": []
        }
        
        try:
            # Fetch props
            live_props = self.db[get_collection_name("live_props", "mlb")]
            
            query = {}
            if stat_types:
                query["stat_type"] = {"$in": stat_types}
            
            props = await live_props.find(query, {"_id": 0}).to_list(length=None)
            results["props_processed"] = len(props)
            
            logger.info(f"[SHARP_SORT] Processing {len(props)} props")
            
            if not props:
                logger.warning("[SHARP_SORT] No props found")
                return results
            
            # Get VK projections from war_zone (where most props end up)
            war_zone = self.db[get_collection_name("war_zone", "mlb")]
            vk_props = await war_zone.find({}, {"_id": 0}).to_list(length=None)
            
            # Build lookup by player/stat/line - use direction (VK) or recommendation (live_props)
            vk_lookup = {}
            for vk in vk_props:
                # VK picks use 'direction' field, normalize to support both
                dir_field = vk.get('direction') or vk.get('recommendation', 'OVER')
                # Create multiple lookup keys for flexible matching
                key1 = f"{vk.get('player_name')}|{vk.get('stat_type')}|{vk.get('line')}|{dir_field}"
                key2 = f"{vk.get('player_name')}|{vk.get('stat_type')}|{vk.get('line')}"  # Without direction
                vk_lookup[key1] = vk
                vk_lookup[key2] = vk
            
            # Also check safe haven and front lines
            for tier_name in ["safe_haven", "front_lines"]:
                tier_coll = self.db[get_collection_name(tier_name, "mlb")]
                tier_props = await tier_coll.find({}, {"_id": 0}).to_list(length=None)
                for vk in tier_props:
                    dir_field = vk.get('direction') or vk.get('recommendation', 'OVER')
                    key1 = f"{vk.get('player_name')}|{vk.get('stat_type')}|{vk.get('line')}|{dir_field}"
                    key2 = f"{vk.get('player_name')}|{vk.get('stat_type')}|{vk.get('line')}"
                    vk_lookup[key1] = vk
                    vk_lookup[key2] = vk
            
            logger.info(f"[SHARP_SORT] Loaded {len(vk_lookup)} VK projections for matching")
            
            # Load player historical logs cache for hit rate calculation
            await self._load_player_logs_cache()
            
            # Process each prop
            fair_values = []
            hit_rates_calculated = 0
            
            for prop in props:
                all_odds = prop.get("all_odds", {})
                sharp_odds = all_odds.get("pinnacle")
                dk_odds = all_odds.get("draftkings")
                
                # Track stats
                if sharp_odds is not None:
                    results["stats"]["total_with_sharp_odds"] += 1
                    fair_value = self.calculate_fair_value(sharp_odds)
                    fair_values.append(fair_value)
                    prop["sharp_fair_value"] = fair_value
                
                if prop.get("dk_line") is not None:
                    results["stats"]["total_with_dk_line"] += 1
                
                # Get VK projection - try multiple key formats
                prop_dir = prop.get('recommendation') or prop.get('direction', 'OVER')
                key1 = f"{prop.get('player_name')}|{prop.get('stat_type')}|{prop.get('line')}|{prop_dir}"
                key2 = f"{prop.get('player_name')}|{prop.get('stat_type')}|{prop.get('line')}"
                vk_projection = vk_lookup.get(key1) or vk_lookup.get(key2, {})
                
                # Merge VK data into prop
                prop["projected_value"] = vk_projection.get("projected_value")
                prop["r_squared"] = vk_projection.get("r_squared")
                prop["slope"] = vk_projection.get("slope")
                prop["edge_pct"] = vk_projection.get("edge_pct")
                
                # Add VK predicted fields for frontend compatibility (matches NBA format)
                projected_val = vk_projection.get("projected_value")
                edge = vk_projection.get("edge_pct") or 0
                line = prop.get("line") or 0
                
                if projected_val is not None:
                    prop["vk_predicted"] = round(projected_val, 1)
                    prop["vk_edge"] = round(edge, 1) if edge else None
                    
                    # Calculate VK recommendation based on edge
                    if edge > 25:
                        prop["vk_recommendation"] = "STRONG_OVER"
                    elif edge > 10:
                        prop["vk_recommendation"] = "LEAN_OVER"
                    elif edge < -25:
                        prop["vk_recommendation"] = "STRONG_UNDER"
                    elif edge < -10:
                        prop["vk_recommendation"] = "LEAN_UNDER"
                    else:
                        prop["vk_recommendation"] = "NEUTRAL"
                    
                    # Calculate probability estimates from edge
                    # Using logistic conversion: prob_over ≈ 50% + (edge%/2) capped at 95%
                    prob_over = min(95, max(5, 50 + (edge / 2)))
                    prop["vk_prob_over"] = round(prob_over, 0)
                    prop["vk_prob_under"] = round(100 - prob_over, 0)
                
                # Calculate hit rates from historical game logs
                hit_rates = self.calculate_mlb_hit_rates(
                    prop.get("player_name"),
                    prop.get("stat_type"),
                    prop.get("line")
                )
                
                # Apply calculated hit rates (prioritize fresh calculation over VK data)
                if hit_rates.get("h5_rate") is not None:
                    prop["h5_rate"] = hit_rates["h5_rate"]
                    hit_rates_calculated += 1
                if hit_rates.get("h10_rate") is not None:
                    prop["h10_rate"] = hit_rates["h10_rate"]
                    prop["hit_rate_l10"] = hit_rates["h10_rate"] / 100  # Also keep decimal version
                else:
                    # Fallback to VK data if no logs
                    prop["hit_rate_l10"] = vk_projection.get("hit_rate_l10")
                    # Convert to percentage for h10_rate
                    if prop["hit_rate_l10"] is not None:
                        prop["h10_rate"] = round(prop["hit_rate_l10"] * 100) if prop["hit_rate_l10"] <= 1 else prop["hit_rate_l10"]
                
                # Apply averages from game logs
                if hit_rates.get("l5_avg") is not None:
                    prop["l5_avg"] = hit_rates["l5_avg"]
                
                if hit_rates.get("l10_avg") is not None:
                    prop["l10_avg"] = hit_rates["l10_avg"]
                else:
                    prop["l10_avg"] = vk_projection.get("l10_avg")
                
                if hit_rates.get("season_avg") is not None:
                    prop["season_avg"] = hit_rates["season_avg"]
                elif prop.get("l10_avg"):
                    prop["season_avg"] = prop["l10_avg"]  # Use L10 as fallback
                
                # Analyze DK vs PP
                dk_analysis = self.analyze_dk_vs_pp(
                    prop.get("dk_line"),
                    dk_odds,
                    prop.get("line"),
                    -110
                )
                prop["dk_analysis"] = dk_analysis
                
                # Classify
                tier = self.classify_prop(prop, vk_projection)
                prop["sharp_tier"] = tier
                prop["classified_at"] = datetime.now(timezone.utc).isoformat()
                
                # Add boolean flags for frontend
                prop["is_goblin"] = (tier == "GOBLIN")
                prop["is_demon"] = (tier == "DEMON")
                prop["tier_label"] = tier
                
                # Add to appropriate list
                if tier == "GOBLIN":
                    # Calculate CV for gate checks
                    cv = self.calculate_cv(prop.get("player_name"), prop.get("stat_type"))
                    prop["cv"] = cv
                    
                    dk_odds = all_odds.get("draftkings")
                    
                    # Check Safe Haven 3-Gate qualification (DK <= -240)
                    if dk_odds is not None and dk_odds <= MLB_DK_SAFE_HAVEN_MAX:
                        passes_gates, gate_reason, gate_results = self.check_safe_haven_gates(
                            prop, hit_rates, cv
                        )
                        prop["safe_haven_gate_results"] = gate_results
                        prop["safe_haven_qualified"] = passes_gates
                        prop["safe_haven_reason"] = gate_reason
                        
                        if passes_gates:
                            # Calculate Board Score for Safe Haven ranking
                            tp_prob = (prop.get("sharp_fair_value") or 0.5) * 100
                            if tp_prob == 50.0 and dk_odds and dk_odds < 0:
                                tp_prob = abs(dk_odds) / (abs(dk_odds) + 100) * 100
                            vk_edge = prop.get("edge_pct") or 0
                            hit_rate_score = (prop.get("h10_rate") or 0) * 10
                            weather_penalty = 0  # TODO: Add weather check
                            
                            board_score = tp_prob + vk_edge + hit_rate_score - weather_penalty
                            prop["board_score"] = round(board_score, 1)
                            
                            if "safe_haven" not in results:
                                results["safe_haven"] = []
                            results["safe_haven"].append(prop)
                    
                    # Check Front Lines 3-Gate qualification (-240 < DK <= -145)
                    elif dk_odds is not None and dk_odds > MLB_DK_FRONT_LINES_MIN and dk_odds <= MLB_DK_FRONT_LINES_MAX:
                        passes_gates, gate_reason, gate_results = self.check_front_lines_gates(
                            prop, hit_rates, cv
                        )
                        prop["front_lines_gate_results"] = gate_results
                        prop["front_lines_qualified"] = passes_gates
                        prop["front_lines_reason"] = gate_reason
                        
                        if passes_gates:
                            # Front Lines sorted by Edge % (descending)
                            if "front_lines" not in results:
                                results["front_lines"] = []
                            results["front_lines"].append(prop)
                    
                    results["goblins"].append(prop)
                    
                elif tier == "STANDARD":
                    # Standard props with elite hit rates can also qualify for Front Lines
                    cv = self.calculate_cv(prop.get("player_name"), prop.get("stat_type"))
                    prop["cv"] = cv
                    
                    dk_odds = all_odds.get("draftkings")
                    
                    # Check if Standard prop qualifies for Front Lines
                    if dk_odds is not None and dk_odds > MLB_DK_FRONT_LINES_MIN and dk_odds <= MLB_DK_FRONT_LINES_MAX:
                        passes_gates, gate_reason, gate_results = self.check_front_lines_gates(
                            prop, hit_rates, cv
                        )
                        prop["front_lines_gate_results"] = gate_results
                        prop["front_lines_qualified"] = passes_gates
                        prop["front_lines_reason"] = gate_reason
                        
                        if passes_gates:
                            if "front_lines" not in results:
                                results["front_lines"] = []
                            results["front_lines"].append(prop)
                    
                    results["standard"].append(prop)
                    
                elif tier == "DEMON":
                    # Demons are candidates for War Zone (high-variance plays)
                    cv = self.calculate_cv(prop.get("player_name"), prop.get("stat_type"))
                    prop["cv"] = cv
                    
                    # Calculate ceiling stats for War Zone qualification
                    ceiling_stats = self.calculate_ceiling_stats(
                        prop.get("player_name"),
                        prop.get("stat_type"),
                        prop.get("line", 0)
                    )
                    prop["ceiling_stats"] = ceiling_stats
                    
                    dk_odds = all_odds.get("draftkings")
                    
                    # Check War Zone 3-Gate qualification
                    # Qualify if: DK Odds > +150 (underdog) OR is a Demon (PP status)
                    # All Demons are potential War Zone candidates
                    passes_gates, gate_reason, gate_results = self.check_war_zone_gates(
                        prop, hit_rates, cv, ceiling_stats
                    )
                    prop["war_zone_gate_results"] = gate_results
                    prop["war_zone_qualified"] = passes_gates
                    prop["war_zone_reason"] = gate_reason
                    
                    if passes_gates:
                        # Calculate Max Upside % for ranking
                        max_upside = ceiling_stats.get("max_upside_pct", 0)
                        prop["max_upside_pct"] = max_upside
                        
                        # Track if fast-tracked due to high volatility
                        if gate_results.get("gate2_volatility", {}).get("fast_tracked"):
                            prop["volatility_fast_tracked"] = True
                        
                        # Calculate Volatility Index (1-10)
                        volatility_index = calculate_volatility_index(
                            cv, 
                            hit_rates.get("h10_rate"), 
                            ceiling_stats
                        )
                        prop["volatility_index"] = volatility_index
                        
                        # Add badges for War Zone
                        prop["badges"] = []
                        
                        # Volatility Extreme badge (index > 8)
                        if volatility_index >= 8:
                            prop["badges"].append({
                                "id": "volatility_extreme",
                                "name": "Extreme Volatility",
                                "earned": True,
                                "metrics": {
                                    "volatility_index": volatility_index,
                                    "cv": cv
                                }
                            })
                        
                        # Check for hitter's haven (park factor boost)
                        team = prop.get("team") or ""
                        if team in TEAM_TO_PARK:
                            park_name = TEAM_TO_PARK[team]
                            if park_name in HITTERS_HAVEN_PARKS:
                                park_info = HITTERS_HAVEN_PARKS[park_name]
                                prop["park_factor"] = park_info["factor"]
                                prop["hitters_haven"] = True
                                prop["badges"].append({
                                    "id": "hitters_haven",
                                    "name": "Hitter's Haven",
                                    "earned": True,
                                    "metrics": {
                                        "park_name": park_name,
                                        "park_factor": park_info["factor"],
                                        "priority": park_info["priority"]
                                    }
                                })
                                # Boost max upside for hitter's parks
                                prop["max_upside_pct"] = round(max_upside * park_info["factor"], 1)
                        
                        if "war_zone" not in results:
                            results["war_zone"] = []
                        results["war_zone"].append(prop)
                    
                    results["demons"].append(prop)
                else:
                    results["unclassified"] += 1
            
            # Calculate average fair value
            if fair_values:
                results["stats"]["sharp_fair_value_avg"] = round(sum(fair_values) / len(fair_values), 4)
            
            # Sort by edge/value
            results["goblins"].sort(key=lambda x: abs(x.get("edge_pct") or 0), reverse=True)
            results["demons"].sort(key=lambda x: abs(x.get("edge_pct") or 0), reverse=True)
            results["standard"].sort(key=lambda x: abs(x.get("edge_pct") or 0), reverse=True)
            
            # Sort Safe Haven by Board Score (top 10)
            if "safe_haven" in results and results["safe_haven"]:
                results["safe_haven"].sort(key=lambda x: x.get("board_score") or 0, reverse=True)
                # Dedupe: keep best prop per player
                seen_players = set()
                deduped_safe_haven = []
                for prop in results["safe_haven"]:
                    player = prop.get("player_name")
                    if player not in seen_players:
                        seen_players.add(player)
                        deduped_safe_haven.append(prop)
                results["safe_haven"] = deduped_safe_haven[:10]  # Top 10
            
            # Sort Front Lines by Edge % (descending) - Top 20
            if "front_lines" in results and results["front_lines"]:
                results["front_lines"].sort(key=lambda x: x.get("edge_pct") or 0, reverse=True)
                # Dedupe: keep best prop per player
                seen_players = set()
                deduped_front_lines = []
                for prop in results["front_lines"]:
                    player = prop.get("player_name")
                    if player not in seen_players:
                        seen_players.add(player)
                        deduped_front_lines.append(prop)
                results["front_lines"] = deduped_front_lines[:20]  # Top 20
            
            # Sort War Zone by Max Upside % (descending) - Top 15
            # Priority: Hitter's Haven picks first, then by max upside
            if "war_zone" in results and results["war_zone"]:
                results["war_zone"].sort(
                    key=lambda x: (
                        x.get("hitters_haven", False),  # Hitter's haven first
                        x.get("max_upside_pct") or 0    # Then by max upside
                    ), 
                    reverse=True
                )
                # Dedupe: keep best moonshot per player
                seen_players = set()
                deduped_war_zone = []
                for prop in results["war_zone"]:
                    player = prop.get("player_name")
                    if player not in seen_players:
                        seen_players.add(player)
                        deduped_war_zone.append(prop)
                results["war_zone"] = deduped_war_zone[:15]  # Top 15 Moonshots
            
            # Save to collections
            if save_to_db:
                await self._save_to_collections(results)
            
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            results["duration_seconds"] = round(duration, 2)
            results["completed_at"] = datetime.now(timezone.utc).isoformat()
            
            logger.info("[SHARP_SORT] Sharp Sorting Complete:")
            logger.info(f"  • Props Processed: {results['props_processed']}")
            logger.info(f"  • Hit Rates Calculated: {hit_rates_calculated}")
            logger.info(f"  • Sharp Goblins: {len(results['goblins'])}")
            logger.info(f"  • Safe Haven: {len(results.get('safe_haven', []))}")
            logger.info(f"  • Front Lines: {len(results.get('front_lines', []))}")
            logger.info(f"  • War Zone: {len(results.get('war_zone', []))}")
            logger.info(f"  • Demons: {len(results['demons'])}")
            logger.info(f"  • Standard: {len(results['standard'])}")
            logger.info(f"  • Unclassified: {results['unclassified']}")
            
        except Exception as e:
            logger.error(f"[SHARP_SORT] Error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results["success"] = False
            results["errors"].append(str(e))
        
        return results
    
    async def _save_to_collections(self, results: Dict) -> None:
        """Save classified props to their respective collections."""
        
        # Goblins
        if results["goblins"]:
            goblins_coll = self.db["mlb_goblins"]
            await goblins_coll.delete_many({})
            # Remove _id if present
            clean_goblins = [{k: v for k, v in p.items() if k != "_id"} for p in results["goblins"]]
            await goblins_coll.insert_many(clean_goblins)
            logger.info(f"[SHARP_SORT] Saved {len(clean_goblins)} Goblins")
        
        # Demons
        if results["demons"]:
            demons_coll = self.db["mlb_demons"]
            await demons_coll.delete_many({})
            clean_demons = [{k: v for k, v in p.items() if k != "_id"} for p in results["demons"]]
            await demons_coll.insert_many(clean_demons)
            logger.info(f"[SHARP_SORT] Saved {len(clean_demons)} Demons")
        
        # Standard
        if results["standard"]:
            standard_coll = self.db["mlb_standard"]
            await standard_coll.delete_many({})
            clean_standard = [{k: v for k, v in p.items() if k != "_id"} for p in results["standard"]]
            await standard_coll.insert_many(clean_standard)
            logger.info(f"[SHARP_SORT] Saved {len(clean_standard)} Standard")
        
        # Safe Haven (Top 10 Elite Goblins that pass 3-Gate)
        if results.get("safe_haven"):
            safe_haven_coll = self.db["mlb_ferrari_safe_haven"]
            await safe_haven_coll.delete_many({})
            clean_safe_haven = [{k: v for k, v in p.items() if k != "_id"} for p in results["safe_haven"]]
            await safe_haven_coll.insert_many(clean_safe_haven)
            logger.info(f"[SHARP_SORT] Saved {len(clean_safe_haven)} Safe Haven picks")
        
        # Front Lines (Top 20 Mid-Juice Goblins/Standards that pass 3-Gate)
        if results.get("front_lines"):
            front_lines_coll = self.db["mlb_ferrari_front_lines"]
            await front_lines_coll.delete_many({})
            clean_front_lines = [{k: v for k, v in p.items() if k != "_id"} for p in results["front_lines"]]
            await front_lines_coll.insert_many(clean_front_lines)
            logger.info(f"[SHARP_SORT] Saved {len(clean_front_lines)} Front Lines picks")
        
        # War Zone (Top 15 Moonshot Demons that pass Ceiling Protocol)
        if results.get("war_zone"):
            war_zone_coll = self.db["mlb_ferrari_war_zone"]
            await war_zone_coll.delete_many({})
            clean_war_zone = [{k: v for k, v in p.items() if k != "_id"} for p in results["war_zone"]]
            await war_zone_coll.insert_many(clean_war_zone)
            logger.info(f"[SHARP_SORT] Saved {len(clean_war_zone)} War Zone picks")


# Singleton
_sharp_sorting: Optional[MLBSharpSortingService] = None


def get_sharp_sorting_service(db: AsyncIOMotorDatabase) -> MLBSharpSortingService:
    """Get or create Sharp Sorting service."""
    global _sharp_sorting
    if _sharp_sorting is None:
        _sharp_sorting = MLBSharpSortingService(db)
    return _sharp_sorting


async def run_mlb_sharp_sorting(
    db: AsyncIOMotorDatabase,
    stat_types: List[str] = None,
    save_to_db: bool = True
) -> Dict[str, Any]:
    """
    Run MLB Sharp Sorting & Tier Distribution.
    
    Classifies props into:
    - Goblins: Sharp favored (odds ≤ -240) + VK confirms
    - Demons: DK mispricing + VK slope trend
    - Standard: Books agree (-110 to -130)
    """
    service = get_sharp_sorting_service(db)
    return await service.run_sharp_sorting(stat_types, save_to_db)
