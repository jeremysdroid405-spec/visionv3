"""
MLB Oracle Apex Service - Safe Haven Tier Logic (2026 Season)
==============================================================
The "Vegas Killer" mathematically-proven Safe Haven tier for MLB.

v2.0 UPDATE: STRICT MLR ENFORCEMENT
===================================
- Uses dedicated MLB XGBoost model trained on 90,000+ game logs
- Park factors mathematically applied (Coors vs Seattle = different predictions)
- Opponent K-rate for pitcher strikeouts
- NO FALLBACKS to season_avg or EWMA
- Props without MLR prediction are DISQUALIFIED

PARK FACTOR EXAMPLES:
- Coors Field (COL): 1.18 hits factor (hitter paradise)
- Oracle Park (SF): 0.92 hits factor (pitcher friendly)
- T-Mobile Park (SEA): 0.94 hits factor (pitcher friendly)

SAFE HAVEN 2.0 - PREDICTIVE ACTUARY MODEL
=========================================

STRICT PROP TYPE GATE:
- Safe Haven ONLY accepts GOBLIN props
- Demons and Standard props are strictly rejected
- This is our premium stability board

DYNAMIC HIT RATE (Season-to-Date):
- Uses actual games played, not hardcoded 20
- Formula: (hits / actual_games_played) * 100
- Solves early-season "teams haven't played 20 games" problem

GATE THRESHOLDS:
- Hit Rate Floor: >= 60% (filters cold streaks)
- CV Max: <= 0.70 (consistency check)
- Lineup Status: CONFIRMED or PROJECTED only

PREDICTIVE ACTUARY GATE:
1. Market Implied Probability (from DK odds):
   - If dk_odds < 0: market_prob = (abs(dk_odds) / (abs(dk_odds) + 100)) * 100
   - Fallback: 50.0%

2. PropVision True Probability (50/50 blend):
   - propvision_true_prob = (market_prob * 0.50) + (true_hit_rate * 0.50)

3. True Edge Calculation:
   - casino_req_rate = get_pp_required_win_rate(dk_odds, prop_type)
   - true_edge = propvision_true_prob - casino_req_rate

4. KILL SWITCH: If true_edge <= 0, prop is dropped

BOARD SCORE FORMULA:
- (true_edge * 3.0) - (cv * 15)
- Heavily weights true edge, penalizes volatility

Lineup Status Values:
- CONFIRMED: Player is in today's confirmed BDL lineup
- PROJECTED: Player has recent game activity but lineup not yet confirmed
- BENCHED: Player's team has lineup but player is NOT in it
- UNKNOWN: No lineup data and no recent activity
"""
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
import logging
import os
from services.vk_model_enforcement import (
    calculate_vk_model, 
    enforce_vk_fields, 
    bulk_enforce_vk_fields,
    validate_vk_fields,
    VKResult
)
from services.mlb_vegas_killer_model import MLBVegasKillerModel
from services.mlb_physical_engine import MLBPhysicalEngine, get_mlb_physical_engine
import numpy as np
import asyncio

from services.mlb_matchup_math import get_mlb_matchup_analysis
from services.mlb_tempo_math import (
    calculate_hitter_tempo,
    calculate_pitcher_tempo,
    get_hitter_tempo_breakdown,
    get_pitcher_tempo_breakdown
)
from services.bdl_splits_cache import (
    prefetch_all_splits,
    get_cached_modifiers,
    clear_cache
)

logger = logging.getLogger(__name__)

# =============================================================================
# ACTUARY GATE - PrizePicks Goblin Tax Curve (Empirically Mapped)
# =============================================================================
# This function returns the REQUIRED win rate to beat PrizePicks' dynamic
# multiplier system. If our internal probability can't beat this, the prop
# is mathematically a losing bet regardless of how "good" it looks.

def get_pp_required_win_rate(dk_odds: int, prop_type: str) -> float:
    """
    Calculate the required win rate to beat PrizePicks' Goblin Tax curve.
    
    Based on empirical testing of PP multipliers vs DK sharp odds:
    - DEMON (+100): 50% baseline (break-even on 2x payout)
    - GOBLIN (-137 to -350+): Dynamic curve from 65% to 91.2%
    - STANDARD: 54.3% (5/6-Pick Flex baseline)
    
    Args:
        dk_odds: DraftKings odds (negative for favorites, positive for dogs)
        prop_type: 'GOBLIN', 'DEMON', or 'STANDARD'
        
    Returns:
        Required win rate percentage to be +EV against PrizePicks
    """
    p_type = str(prop_type).upper() if prop_type else "STANDARD"
    
    if p_type == 'DEMON':
        return 50.0  # +100 baseline (2x payout requires 50% to break even)
    
    elif p_type == 'GOBLIN':
        if dk_odds is None:
            return 75.0  # Conservative fallback
        
        # Convert to int if string
        try:
            dk_odds = int(dk_odds)
        except (ValueError, TypeError):
            return 75.0
        
        # Goblin Tax Curve (mapped from PP multipliers)
        # More negative DK odds = higher required win rate
        if dk_odds <= -350:
            return 91.2   # 1.2x slip equivalent (near lock)
        if dk_odds <= -290:
            return 79.0   # 1.6x slip equivalent
        if dk_odds <= -250:
            return 76.7   # 1.7x slip equivalent
        if dk_odds <= -230:
            return 72.5   # 1.9x slip equivalent
        if dk_odds <= -210:
            return 70.7   # 2.0x slip equivalent
        if dk_odds <= -190:
            return 67.4   # 2.2x slip equivalent
        if dk_odds <= -170:
            return 65.9   # 2.3x slip equivalent
        return 65.0  # Absolute floor for weak Goblins (-145 to -169)
    
    else:  # STANDARD
        return 54.3  # 5/6-Pick Flex baseline


def calculate_master_probability(dk_odds: int, true_hit_rate: float, prop_type: str) -> dict:
    """
    MASTER PROBABILITY FUNCTION - Used by ALL tiers for consistent edge calculation.
    
    This ensures the same player shows the EXACT same True Edge regardless of which tier
    they appear in. Differentiation happens through FILTERING, not math.
    
    Formula (50/50 Blend):
        market_prob = (abs(dk_odds) / (abs(dk_odds) + 100)) * 100  (if dk_odds < 0)
        propvision_true_prob = (market_prob * 0.50) + (true_hit_rate * 0.50)
        true_edge = propvision_true_prob - casino_req_rate
    
    Args:
        dk_odds: DraftKings odds (negative for favorites, positive for dogs)
        true_hit_rate: Dynamic hit rate based on actual games played (%)
        prop_type: 'GOBLIN', 'DEMON', or 'STANDARD'
        
    Returns:
        dict with market_prob, propvision_true_prob, casino_req_rate, true_edge
    """
    # Calculate Market Implied Probability from DK Odds
    if dk_odds and dk_odds < 0:
        market_prob = (abs(dk_odds) / (abs(dk_odds) + 100)) * 100
    else:
        market_prob = 50.0  # Fallback for positive or missing odds
    
    # Calculate PropVision True Probability (MASTER 50/50 BLEND)
    propvision_true_prob = (market_prob * 0.50) + (true_hit_rate * 0.50)
    
    # Get the casino's required win rate based on Goblin Tax curve
    casino_req_rate = get_pp_required_win_rate(dk_odds, prop_type)
    
    # Calculate True Edge
    true_edge = propvision_true_prob - casino_req_rate
    
    return {
        'market_prob': round(market_prob, 1),
        'propvision_true_prob': round(propvision_true_prob, 1),
        'casino_req_rate': round(casino_req_rate, 1),
        'true_edge': round(true_edge, 1),
    }


# =============================================================================
# 2026 MLB ORACLE APEX CONFIGURATION - SAFE HAVEN (Strictest)
# =============================================================================

MLB_SAFE_HAVEN_CONFIG = {
    'HITS': {
        'max_cv': 0.60,
        'min_hit_rate': 16,      # 16/20 = 80%
        'sample_size': 20,
        'min_edge_raw': 0.30,    # Raw cushion: requires 0.80+ on 0.5 line
        'min_prob': 70.0,
        'is_batter_stat': True,
    },
    'TB': {  # Total Bases
        'max_cv': 0.75,
        'min_hit_rate': 15,      # 15/20 = 75%
        'sample_size': 20,
        'min_edge_raw': 0.45,    # Raw cushion: requires 1.95+ on 1.5 line
        'min_prob': 70.0,
        'is_batter_stat': True,
    },
    'TOTAL BASES': {  # Alias for TB
        'max_cv': 0.75,
        'min_hit_rate': 15,
        'sample_size': 20,
        'min_edge_raw': 0.45,
        'min_prob': 70.0,
        'is_batter_stat': True,
    },
    'K': {  # Pitcher Strikeouts
        'max_cv': 0.45,
        'min_hit_rate': 15,      # 15/20 = 75%
        'sample_size': 20,
        'min_edge_raw': 1.00,    # Raw cushion: requires 6.5+ on 5.5 line
        'min_prob': 75.0,
        'is_batter_stat': False,
    },
    'PITCHER STRIKEOUTS': {  # Alias for K
        'max_cv': 0.45,
        'min_hit_rate': 15,
        'sample_size': 20,
        'min_edge_raw': 1.00,
        'min_prob': 75.0,
        'is_batter_stat': False,
    },
    'OUTS': {  # Pitching Outs Recorded
        'max_cv': 0.30,
        'min_hit_rate': 17,      # 17/20 = 85%
        'sample_size': 20,
        'min_edge_raw': 1.50,    # Raw cushion: requires 19.0+ on 17.5 line
        'min_prob': 80.0,
        'is_batter_stat': False,
    },
    'PITCHING OUTS': {  # Alias for OUTS
        'max_cv': 0.30,
        'min_hit_rate': 17,
        'sample_size': 20,
        'min_edge_raw': 1.50,
        'min_prob': 80.0,
        'is_batter_stat': False,
    },
    'HRR': {  # Hits + Runs + RBIs combo
        'max_cv': 0.55,
        'min_hit_rate': 16,      # 16/20 = 80%
        'sample_size': 20,
        'min_edge_raw': 0.45,    # Raw cushion: requires 1.95+ on 1.5 line
        'min_prob': 70.0,
        'is_batter_stat': True,
    },
}

# Stat type normalization map
MLB_STAT_MAP = {
    'hits': 'HITS',
    'total_bases': 'TB',
    'total bases': 'TB',
    'tb': 'TB',
    'pitcher_strikeouts': 'PITCHER STRIKEOUTS',
    'pitcher strikeouts': 'PITCHER STRIKEOUTS',
    'strikeouts': 'PITCHER STRIKEOUTS',
    'k': 'K',
    'pitching_outs': 'PITCHING OUTS',
    'pitching outs': 'PITCHING OUTS',
    'outs': 'OUTS',
    'outs_recorded': 'OUTS',
    'hrr': 'HRR',
    'hits_runs_rbis': 'HRR',
    'hits+runs+rbis': 'HRR',
    # Direct mappings (uppercase)
    'HITS': 'HITS',
    'TB': 'TB',
    'TOTAL BASES': 'TB',
    'K': 'K',
    'PITCHER STRIKEOUTS': 'PITCHER STRIKEOUTS',
    'OUTS': 'OUTS',
    'PITCHING OUTS': 'PITCHING OUTS',
    'HRR': 'HRR',
}

# DK Odds threshold for Safe Haven
DK_ODDS_THRESHOLD = -240

# VK baseline key mapping for MLB stats
VK_BASELINE_MAP = {
    'HITS': 'hits',
    'TB': 'total_bases',
    'TOTAL BASES': 'total_bases',
    'K': 'pitcher_strikeouts',
    'PITCHER STRIKEOUTS': 'pitcher_strikeouts',
    'OUTS': 'innings_pitched',  # Use IP as proxy for outs
    'PITCHING OUTS': 'innings_pitched',
    'HRR': 'hrr',  # Combo stat
}


class MLBOracleApexService:
    """
    MLB Oracle Apex Service for Safe Haven tier qualification.
    
    v2.0: STRICT MLR ENFORCEMENT
    - Uses dedicated MLB XGBoost model (trained on 90,000+ games)
    - Park factors mathematically applied
    - NO FALLBACKS to season_avg
    - Props without MLR prediction are DISQUALIFIED
    """
    
    def __init__(self, db, mlb_vegas_killer_model=None):
        self.db = db
        self.cached_board = db.mlb_cached_board
        self.live_props = db.mlb_live_props
        self.master_hub = db.mlb_master_hub_2026
        self.oracle_apex_collection = db.mlb_oracle_apex_analyzed
        
        # v2.0: Initialize MLB Physical Engine (64-feature XGBoost)
        # Create sync MongoDB client for MLBPhysicalEngine
        from pymongo import MongoClient
        mongo_url = os.environ.get('MONGO_URL')
        db_name = os.environ.get('DB_NAME', 'propvision')
        sync_client = MongoClient(mongo_url)
        sync_db = sync_client[db_name]
        
        # PRIMARY: Use new 64-feature Physical Engine
        self.mlb_physical_engine = MLBPhysicalEngine(sync_db)
        loaded_apex = self.mlb_physical_engine.load_models()
        logger.info(f"[MLB_ORACLE] MLBPhysicalEngine loaded with {loaded_apex} trained 64-feature XGBoost models")
        
        # FALLBACK: Keep legacy VK model for any missing stats
        if mlb_vegas_killer_model:
            self.mlb_vegas_killer_model = mlb_vegas_killer_model
        else:
            self.mlb_vegas_killer_model = MLBVegasKillerModel(sync_db)
            loaded_vk = self.mlb_vegas_killer_model.load_models()
            logger.info(f"[MLB_ORACLE] Legacy MLBVegasKillerModel loaded with {loaded_vk} models (fallback)")
        
    def set_vegas_killer_model(self, model):
        """Set the Vegas Killer model reference (legacy compatibility)."""
        self.mlb_vegas_killer_model = model
    
    def _normalize_stat_type(self, raw_stat: str) -> str:
        """Normalize stat type to our standard format."""
        if not raw_stat:
            return ''
        normalized = raw_stat.lower().replace('_', ' ').replace('-', ' ').strip()
        return MLB_STAT_MAP.get(normalized, MLB_STAT_MAP.get(raw_stat, raw_stat.upper()))
    
    def _get_mlb_stat_values(self, game_logs: List[Dict], stat_type: str) -> List[float]:
        """
        Extract stat values from MLB game logs based on stat type.
        
        MLB game log fields (from BallDontLie API):
        - hits, doubles, triples, home_runs, rbi, runs, stolen_bases
        - strikeouts (batter), walks
        - For pitchers: innings_pitched, earned_runs, strikeouts (pitcher)
        """
        stat_normalized = self._normalize_stat_type(stat_type)
        
        # Field mapping for MLB stats
        stat_field_map = {
            'HITS': 'hits',
            'TB': None,  # Calculated: singles + (2*doubles) + (3*triples) + (4*hr)
            'TOTAL BASES': None,
            'K': 'strikeouts',  # Pitcher strikeouts
            'PITCHER STRIKEOUTS': 'strikeouts',
            'OUTS': None,  # Calculated from innings_pitched
            'PITCHING OUTS': None,
            'HRR': None,  # Calculated: hits + runs + rbi
            'RBIS': 'rbi',
            'RUNS': 'runs',
            'STOLEN BASES': 'stolen_bases',
            'HOME RUNS': 'home_runs',
            'DOUBLES': 'doubles',
            'WALKS': 'walks',
            'BATTER STRIKEOUTS': 'strikeouts',  # Batter K (different context)
        }
        
        values = []
        for game in game_logs:
            if stat_normalized == 'TB' or stat_normalized == 'TOTAL BASES':
                # Total Bases = 1B + 2*2B + 3*3B + 4*HR
                hits = game.get('hits', 0) or 0
                doubles = game.get('doubles', 0) or 0
                triples = game.get('triples', 0) or 0
                home_runs = game.get('home_runs', 0) or 0
                singles = hits - doubles - triples - home_runs
                tb = singles + (2 * doubles) + (3 * triples) + (4 * home_runs)
                values.append(float(tb))
            elif stat_normalized == 'HRR':
                # Hits + Runs + RBIs
                hits = game.get('hits', 0) or 0
                runs = game.get('runs', 0) or 0
                rbi = game.get('rbi', 0) or 0
                values.append(float(hits + runs + rbi))
            elif stat_normalized in ('OUTS', 'PITCHING OUTS'):
                # Convert innings pitched to outs (IP * 3)
                ip = game.get('innings_pitched', 0) or 0
                if isinstance(ip, str):
                    try:
                        ip = float(ip)
                    except (ValueError, TypeError):
                        ip = 0
                outs = int(ip) * 3 + round((ip % 1) * 10)  # Handle .1, .2 innings
                values.append(float(outs))
            elif stat_normalized in stat_field_map and stat_field_map[stat_normalized]:
                field = stat_field_map[stat_normalized]
                val = game.get(field, 0) or 0
                values.append(float(val))
        
        return values
    
    def _get_matchup_modifier(self, stat_type: str, opponent_team: str) -> float:
        """
        Get matchup modifier from mlb_matchup_math service.
        
        Returns a multiplier to adjust the VK projection based on matchup quality.
        - Favorable matchup (Easy): 1.05 - 1.15
        - Neutral matchup (Medium): 1.0
        - Tough matchup (Brutal): 0.85 - 0.95
        """
        if not opponent_team:
            return 1.0
        
        try:
            matchup = get_mlb_matchup_analysis(
                stat_type=stat_type,
                opponent_team=opponent_team
            )
            
            if not matchup:
                return 1.0
            
            overall_edge = matchup.get('overall_edge', 0)
            
            # Convert edge (-30 to +30) to multiplier (0.85 to 1.15)
            # +30% edge = 1.15 multiplier, -30% edge = 0.85 multiplier
            modifier = 1.0 + (overall_edge / 200)  # Scale down for reasonable adjustment
            return max(0.85, min(1.15, modifier))
            
        except Exception as e:
            logger.warning(f"[MLB_APEX] Matchup modifier error: {e}")
            return 1.0
    
    def _get_bdl_modifiers_from_cache(
        self,
        prop: Dict,
        stat_key: str
    ) -> Tuple[float, float, Dict[str, Any]]:
        """
        Get BDL modifiers from pre-populated cache. NO API CALLS.
        Cache is populated once at rebuild start via prefetch_all_splits().
        """
        matchup_modifier = 1.0
        tempo_modifier = 1.0
        bdl_details = None
        
        # Only apply to hitter stats
        is_pitcher_stat = stat_key in ["K", "OUTS", "ER", "PITCHER STRIKEOUTS", "PITCHING OUTS"]
        if is_pitcher_stat:
            return matchup_modifier, tempo_modifier, bdl_details
        
        player_id = prop.get("player_id") or prop.get("bdl_id")
        if not player_id:
            return matchup_modifier, tempo_modifier, bdl_details
        
        try:
            pitcher_hand = (prop.get("pitcher_hand") or 
                           prop.get("opposing_pitcher_hand") or 
                           prop.get("opp_pitcher_hand") or "R").upper()
            
            # Get from cache - no API call
            cached = get_cached_modifiers(int(player_id), pitcher_hand)
            matchup_modifier = cached.get("matchup_modifier", 1.0)
            tempo_modifier = cached.get("tempo_modifier", 1.0)
            
            if cached.get("lr_split"):
                bdl_details = {
                    "source": "bdl_cache",
                    "lr_split": cached.get("lr_split")
                }
        except Exception as e:
            logger.debug(f"[BDL_CACHE] Error for player {player_id}: {e}")
        
        return matchup_modifier, tempo_modifier, bdl_details
    
    def _collect_unique_player_ids(self, props: List[Dict]) -> set:
        """Collect unique hitter player IDs from props."""
        player_ids = set()
        for prop in props:
            stat_key = (prop.get("stat_key") or prop.get("stat_type") or "").upper()
            is_pitcher_stat = stat_key in ["K", "OUTS", "ER", "PITCHER STRIKEOUTS", "PITCHING OUTS"]
            if is_pitcher_stat:
                continue
            player_id = prop.get("player_id") or prop.get("bdl_id")
            if player_id:
                try:
                    player_ids.add(int(player_id))
                except (ValueError, TypeError):
                    pass
        return player_ids
    
    def _build_tempo_intel_suite(
        self, 
        prop: Dict, 
        stat_key: str,
        tempo_modifier: float
    ) -> Dict[str, Any]:
        """
        Build the tempo section of intel_suite for Vision Intel Suite display.
        
        Returns a structured object with display, label, factors for UI rendering.
        """
        is_pitcher_stat = stat_key in ["K", "OUTS", "ER"]
        pct_change = (tempo_modifier - 1) * 100
        
        if is_pitcher_stat:
            # Pitcher tempo breakdown
            pitcher_ppa = prop.get("pitcher_ppa") or prop.get("pitches_per_pa")
            bullpen_rest = prop.get("bullpen_rest_days")
            breakdown = get_pitcher_tempo_breakdown(pitcher_ppa, bullpen_rest)
            
            if pct_change >= 8:
                tempo_label = "Pitcher Deep - High K Upside"
            elif pct_change >= 3:
                tempo_label = "Extended Outing Expected"
            elif pct_change <= -8:
                tempo_label = "Early Hook Risk"
            elif pct_change <= -3:
                tempo_label = "Short Start Likely"
            else:
                tempo_label = "Standard Workload"
            
            tooltip_parts = []
            if pitcher_ppa is not None:
                if pitcher_ppa < 3.8:
                    tooltip_parts.append(f"P/PA {pitcher_ppa:.2f} is efficient - goes deep")
                elif pitcher_ppa > 4.2:
                    tooltip_parts.append(f"P/PA {pitcher_ppa:.2f} is high - early hook risk")
            if bullpen_rest is not None and bullpen_rest == 0:
                tooltip_parts.append("Bullpen worked yesterday - longer leash")
            
            return {
                "multiplier": tempo_modifier,
                "display": f"{'+' if pct_change >= 0 else ''}{pct_change:.0f}%",
                "tempo_label": tempo_label,
                "factors": breakdown.get("factors", []),
                "total_pct": breakdown.get("total_pct", 0),
                "player_type": "pitcher",
                "ppa": pitcher_ppa,
                "bullpen_rest": bullpen_rest,
                "tooltip": " | ".join(tooltip_parts) if tooltip_parts else "Standard workload expected"
            }
        else:
            # Hitter tempo breakdown
            batting_order = prop.get("batting_order") or prop.get("lineup_position")
            is_away = prop.get("is_away_team") or prop.get("is_away")
            team_obp_rank = prop.get("team_obp_rank")
            breakdown = get_hitter_tempo_breakdown(batting_order, is_away, team_obp_rank)
            
            # Determine tempo label based on actual data availability
            has_tempo_data = any([batting_order is not None, is_away is not None, team_obp_rank is not None])
            
            if not has_tempo_data:
                # No lineup data available - indicate clearly
                tempo_label = "Lineup Pending"
            elif pct_change >= 10:
                tempo_label = "Max PA Opportunity"
            elif pct_change >= 5:
                tempo_label = "High PA Upside"
            elif pct_change <= -10:
                tempo_label = "Limited PA Risk"
            elif pct_change <= -5:
                tempo_label = "Reduced Opportunity"
            else:
                tempo_label = "Standard PA Volume"
            
            tooltip_parts = []
            if not has_tempo_data:
                tooltip_parts.append("Lineup data not yet available")
            else:
                if is_away is True:
                    tooltip_parts.append("Away team guarantees 9th inning AB")
                elif is_away is False:
                    tooltip_parts.append("Home team risks no 9th inning")
                if batting_order is not None:
                    if batting_order <= 3:
                        tooltip_parts.append(f"Batting {batting_order} maximizes PAs")
                    elif batting_order >= 6:
                        tooltip_parts.append(f"Batting {batting_order} risks only 3 PAs")
                if team_obp_rank is not None:
                    if team_obp_rank <= 10:
                        tooltip_parts.append(f"Top {team_obp_rank} OBP creates lineup turnover")
                    elif team_obp_rank >= 21:
                        tooltip_parts.append(f"#{team_obp_rank} OBP limits at-bats")
            
            return {
                "multiplier": tempo_modifier,
                "display": f"{'+' if pct_change >= 0 else ''}{pct_change:.0f}%",
                "tempo_label": tempo_label,
                "factors": breakdown.get("factors", []),
                "total_pct": breakdown.get("total_pct", 0),
                "player_type": "hitter",
                "batting_order": batting_order,
                "is_away": is_away,
                "team_obp_rank": team_obp_rank,
                "tooltip": " | ".join(tooltip_parts) if tooltip_parts else "Standard plate appearance volume expected"
            }
    
    def _build_war_zone_badges(
        self,
        prop: Dict[str, Any],
        stat_key: str,
        cv: float,
        used_volatility_fasttrack: bool,
        used_hr_power_bypass: bool
    ) -> List[str]:
        """
        Build active badges for War Zone picks.
        
        Badges indicate special conditions or notable characteristics:
        - volatility_king: CV > 1.0 (boom/bust profile)
        - power_bypass: HR prop qualified via power metrics
        - demon: PrizePicks demon prop
        - sharp_fade: Sharps are fading this line
        - ceiling_spike: Demonstrated ceiling hits
        """
        badges = []
        
        # Volatility badge
        if used_volatility_fasttrack or cv > 1.0:
            badges.append("volatility_king")
        
        # HR Power Bypass badge
        if used_hr_power_bypass:
            badges.append("power_bypass")
        
        # Demon badge (PrizePicks classification)
        if prop.get("is_demon", False):
            badges.append("demon")
        
        # Sharp fade badge (if Pinnacle is fading)
        pinnacle_tp = prop.get("pinnacle_tp") or prop.get("sharp_tp")
        if pinnacle_tp and pinnacle_tp < 40:
            badges.append("sharp_fade")
        
        # Ceiling spike badge (L15 hits >= 3)
        l15_ceiling_hits = prop.get("l15_ceiling_hits", 0)
        if l15_ceiling_hits >= 3:
            badges.append("ceiling_spike")
        
        # High hit rate badge (L10 > 60%)
        h10_rate = prop.get("h10_rate") or prop.get("hit_rate_l10", 0)
        if h10_rate and h10_rate >= 60:
            badges.append("hot_hand")
        
        return badges
    
    def _build_safe_haven_badges(
        self,
        prop: Dict[str, Any],
        stat_key: str,
        h20_rate: float,
        cv: float
    ) -> List[str]:
        """
        Build active badges for Safe Haven picks.
        
        Badges indicate safety and consistency:
        - goblin: PrizePicks goblin (safer) prop
        - ultra_consistent: CV < 0.30 (very predictable)
        - bank_it: Hit rate > 85%
        - sharp_agree: Pinnacle TP > 70%
        """
        badges = []
        
        # Goblin badge (PrizePicks classification)
        if prop.get("is_goblin", False):
            badges.append("goblin")
        
        # Ultra consistent badge
        if cv < 0.30:
            badges.append("ultra_consistent")
        
        # Bank it badge (high hit rate)
        if h20_rate >= 85:
            badges.append("bank_it")
        
        # Sharp agreement badge
        pinnacle_tp = prop.get("pinnacle_tp") or prop.get("sharp_tp")
        if pinnacle_tp and pinnacle_tp >= 70:
            badges.append("sharp_agree")
        
        return badges
    
    def _build_front_lines_badges(
        self,
        prop: Dict[str, Any],
        stat_key: str,
        h20_rate: float,
        cv: float,
        used_recency_override: bool
    ) -> List[str]:
        """
        Build active badges for Front Lines picks.
        
        Badges indicate value and trend:
        - value_play: Solid edge with moderate risk
        - hot_streak: Used recency override (L10 > 80%)
        - trending: Recent performance exceeds season average
        - sharp_lean: Pinnacle leaning towards over
        """
        badges = []
        
        # Value play (default for Front Lines)
        badges.append("value_play")
        
        # Hot streak badge (recency override triggered)
        if used_recency_override:
            badges.append("hot_streak")
        
        # Trending badge (L10 significantly better than L20)
        h10_rate = prop.get("h10_rate") or prop.get("hit_rate_l10", 0)
        if h10_rate and h20_rate and (h10_rate - h20_rate) >= 15:
            badges.append("trending")
        
        # Sharp lean badge
        pinnacle_tp = prop.get("pinnacle_tp") or prop.get("sharp_tp")
        if pinnacle_tp and pinnacle_tp >= 58:
            badges.append("sharp_lean")
        
        # Consistent performer badge
        if cv < 0.50:
            badges.append("consistent")
        
        return badges
    
    def _build_stability_index(self, cv: float) -> Dict[str, Any]:
        """
        Build stability index from CV for Vision Intel Suite.
        
        CV in MLB is stored as a percentage (e.g., 50 means 50% variation).
        
        Converts CV (Coefficient of Variation) to a 100-point stability score:
        - 100% = CV of 0 (perfectly consistent)
        - 0% = CV of 200+ (extremely volatile)
        
        Scale: stability_score = max(0, 100 - (cv / 2))
        
        Labels:
        - 75-100%: "Ultra Consistent" (CV < 50)
        - 50-74%: "Stable" (CV 50-100)  
        - 25-49%: "Volatile" (CV 100-150)
        - 0-24%: "Boom/Bust" (CV > 150)
        """
        if cv is None:
            return {
                "score": None,
                "display": "-",
                "consistency": "Data unavailable",
                "std_dev": None,
                "raw_cv": None
            }
        
        # Convert CV percentage to 100-point stability scale (lower CV = higher stability)
        # CV of 0% = 100% stability, CV of 200% = 0% stability
        stability_score = max(0, min(100, 100 - (cv / 2)))
        
        # Determine consistency label based on CV percentage
        if cv < 50:
            consistency = "Ultra Consistent"
        elif cv < 100:
            consistency = "Stable"
        elif cv < 150:
            consistency = "Volatile"
        else:
            consistency = "Boom/Bust"
        
        return {
            "score": round(stability_score, 1),
            "display": f"{round(stability_score)}%",
            "consistency": consistency,
            "std_dev": f"CV: {cv:.1f}%",
            "raw_cv": round(cv, 2)
        }
    
    def _check_weather_hardstop(
        self, 
        prop: Dict, 
        stat_type: str
    ) -> Tuple[bool, str]:
        """
        Check weather hard-stop filter for batter props.
        
        RULE: If wind_direction == 'IN' AND wind_speed > 12mph, 
              reject all Batter props (Hits, TB, HRR) for that game.
        
        Returns:
            (passes: bool, reason: str)
        """
        stat_config = MLB_SAFE_HAVEN_CONFIG.get(stat_type, {})
        is_batter_stat = stat_config.get('is_batter_stat', True)
        
        if not is_batter_stat:
            return True, "NOT_BATTER_STAT"
        
        # Get weather data from prop
        weather = prop.get('weather', {}) or {}
        wind_direction = weather.get('wind_direction', '').upper()
        wind_speed = weather.get('wind_speed', 0) or 0
        
        # Convert wind_speed to float if string
        if isinstance(wind_speed, str):
            try:
                wind_speed = float(wind_speed.replace('mph', '').strip())
            except (ValueError, TypeError):
                wind_speed = 0
        
        # Hard-stop: Wind blowing IN > 12mph kills batter props
        if wind_direction == 'IN' and wind_speed > 12:
            return False, f"WEATHER_HARDSTOP: Wind {wind_direction} @ {wind_speed}mph"
        
        return True, "WEATHER_OK"
    
    def qualifies_for_mlb_safe_haven(
        self,
        stat_type: str,
        line: float,
        l20_values: List[float],
        cv: float,
        adjusted_vk_pred: float,
        vk_prob: float,
        dk_odds: float,
        is_goblin: bool,
        is_lineup_confirmed: bool,
        prop: Dict = None
    ) -> Tuple[bool, str]:
        """
        Check if a prop qualifies for MLB Safe Haven tier.
        
        PRIMARY QUALIFICATIONS:
        1. DK Odds <= -240
        2. Must be GOBLIN (Green) prop
        3. is_lineup_confirmed must be True
        
        3-GATE QUALIFICATION:
        - Gate 1: Hit Rate (strict L20 %, no weighted recency exceptions)
        - Gate 2: CV <= stat-specific limit
        - Gate 3: Adjusted_VK Edge >= min edge AND TP >= 70%
        
        Returns:
            (qualifies: bool, reason: str)
        """
        # Normalize stat type
        stat_normalized = self._normalize_stat_type(stat_type)
        
        if stat_normalized not in MLB_SAFE_HAVEN_CONFIG:
            return False, f"UNSUPPORTED_STAT: {stat_type}"
        
        cfg = MLB_SAFE_HAVEN_CONFIG[stat_normalized]
        
        # ============================================================
        # PRIMARY QUALIFICATIONS (Pre-Gate Checks)
        # ============================================================
        
        # 1. DK Odds must be <= -240 (heavy favorites only)
        if dk_odds is not None and dk_odds > DK_ODDS_THRESHOLD:
            return False, f"DK_ODDS_FAIL: {dk_odds} > {DK_ODDS_THRESHOLD}"
        
        # 2. Must be a GOBLIN (Green) prop - reject standard and demon
        if not is_goblin:
            return False, "NOT_GOBLIN: Only green props allowed in Safe Haven"
        
        # 3. Lineup must be confirmed
        if is_lineup_confirmed is False:
            return False, "LINEUP_NOT_CONFIRMED: Player lineup status unverified"
        
        # 4. Weather hard-stop for batter props
        if prop:
            passes_weather, weather_reason = self._check_weather_hardstop(prop, stat_normalized)
            if not passes_weather:
                return False, weather_reason
        
        # ============================================================
        # 3-GATE QUALIFICATION
        # ============================================================
        
        # Ensure we have enough L20 data
        if len(l20_values) < cfg['sample_size']:
            return False, f"INSUFFICIENT_DATA: {len(l20_values)}/{cfg['sample_size']} games"
        
        # Calculate L20 hit rate
        l20_hits = sum(1 for v in l20_values if v >= line)
        l20_hit_rate_pct = (l20_hits / 20) * 100
        
        # GATE 1: HIT RATE (strict L20 percentage, NO weighted recency exceptions)
        if l20_hits < cfg['min_hit_rate']:
            return False, f"GATE1_HIT_RATE: {l20_hits}/20 ({l20_hit_rate_pct:.0f}%) < {cfg['min_hit_rate']}/20 ({cfg['min_hit_rate']/20*100:.0f}%)"
        
        # GATE 2: CV (Coefficient of Variation) - consistency check
        if cv > cfg['max_cv']:
            return False, f"GATE2_CV: {cv:.3f} > {cfg['max_cv']}"
        
        # GATE 3: RAW CUSHION EDGE + PROBABILITY
        # Calculate edge as raw cushion: adjusted_pred - line
        raw_edge = adjusted_vk_pred - line
        
        if raw_edge < cfg['min_edge_raw']:
            return False, f"GATE3_EDGE: {raw_edge:.2f} < {cfg['min_edge_raw']} (pred {adjusted_vk_pred:.2f} vs line {line})"
        
        if vk_prob < cfg['min_prob']:
            return False, f"GATE3_PROB: {vk_prob:.1f}% < {cfg['min_prob']}%"
        
        return True, "MLB_SAFE_HAVEN_QUALIFIED"
    
    def calculate_board_score(
        self,
        vk_prob: float,
        raw_edge: float,
        hit_rate_pct: float
    ) -> float:
        """
        Calculate Board Score for final sorting.
        
        Formula: TP Prob + (Raw Edge * 10) + (Hit Rate * 10)
        
        The raw edge is multiplied by 10 to scale it appropriately since
        raw cushion values are typically 0.3 - 2.0 range.
        
        Example:
        - VK Prob: 75%
        - Raw Edge: 1.5 (cushion above line)
        - Hit Rate: 80%
        Board Score = 75 + (1.5 * 10) + (80 * 0.1) = 75 + 15 + 8 = 98
        """
        return vk_prob + (raw_edge * 10) + (hit_rate_pct * 0.1)
    
    async def build_elite_top_10_tiers(self, all_picks: List[Dict]) -> Dict[str, List[Dict]]:
        """
        ELITE TOP 10 SORTING ENGINE - Sequential Claim Logic
        =====================================================
        
        Implements exclusive tier assignment to ensure NO prop appears in multiple tiers.
        
        PROCESS:
        1. Build QUALIFIED POOL: All props passing safety filters with positive true_edge
        2. WAR ZONE claims first: Demons + Standards (DK > +100), sorted by true_edge
        3. SAFE HAVEN claims second: Goblins only, sorted by propvision_true_prob + true_edge
        4. FRONT LINES claims last: Everything remaining, sorted by board_score
        
        CRITICAL: All safety filters (Lineup, Weather, CV, Hit Rate, Actuary Kill Switch)
        remain intact. This only changes how survivors are SORTED and ASSIGNED.
        
        Returns:
            Dict with 'safe_haven', 'front_lines', 'war_zone' lists (each Top 10, exclusive)
        """
        logger.info("=" * 70)
        logger.info("[ELITE_TOP_10] Starting Sequential Claim Sorting Engine...")
        logger.info(f"[ELITE_TOP_10] Total Input: {len(all_picks)} props")
        logger.info("=" * 70)
        
        BATTER_STATS = {"HITS", "TB", "HRR", "RBIS", "RUNS", "SINGLES", "DOUBLES", "HR", "SB", "BB", "TOTAL BASES"}
        
        # ====================================================================
        # STEP 1: BUILD THE QUALIFIED POOL
        # ====================================================================
        # Every prop must pass ALL safety filters to enter the pool
        
        gate_stats = {
            'total_input': len(all_picks),
            'fail_lineup': 0,
            'fail_weather': 0,
            'fail_hit_rate': 0,
            'fail_cv': 0,
            'fail_actuary_gate': 0,
            'qualified_pool': 0,
        }
        
        qualified_pool = []
        
        for prop in all_picks:
            # Get basic prop info
            player_name = prop.get("player_name", "Unknown")
            stat_type = (prop.get("stat_type") or "").upper()
            stat_key = stat_type.replace(" ", "_")
            line = prop.get("line", 0)
            
            # Get prop classification
            is_goblin = prop.get("is_goblin", False)
            is_demon = prop.get("is_demon", False)
            prop_type = "GOBLIN" if is_goblin else ("DEMON" if is_demon else "STANDARD")
            
            # Get DK odds
            dk_odds = prop.get("dk_odds")
            if dk_odds is None:
                dk_odds = prop.get("all_odds", {}).get("draftkings")
            
            # ================================================================
            # SAFETY FILTER 1: Lineup Gate
            # ================================================================
            lineup_status = prop.get('lineup_status')
            if lineup_status == "BENCHED":
                gate_stats['fail_lineup'] += 1
                continue
            
            # ================================================================
            # SAFETY FILTER 2: Weather Tunnel (Batter props only)
            # ================================================================
            if stat_key.replace("_", " ") in BATTER_STATS or stat_type in BATTER_STATS:
                weather = prop.get("weather", {}) or {}
                wind_direction = (weather.get("wind_direction") or "").upper()
                wind_speed = weather.get("wind_speed", 0) or 0
                
                if isinstance(wind_speed, str):
                    try:
                        wind_speed = float(wind_speed.replace("mph", "").strip())
                    except (ValueError, TypeError):
                        wind_speed = 0
                
                if wind_direction == "IN" and wind_speed > 12:
                    gate_stats['fail_weather'] += 1
                    continue
            
            # ================================================================
            # SAFETY FILTER 3: Dynamic Hit Rate Calculation
            # ================================================================
            games_played = prop.get("games_played") or prop.get("l10_games") or 0
            hit_count = prop.get("l20_hits") or prop.get("l10_hits") or 0
            existing_hit_rate = prop.get("hit_rate_l10") or prop.get("hit_rate_l20") or prop.get("h20_rate")
            
            if games_played > 0 and hit_count > 0:
                true_hit_rate = (hit_count / games_played) * 100
            elif existing_hit_rate is not None:
                true_hit_rate = existing_hit_rate
            else:
                gate_stats['fail_hit_rate'] += 1
                continue
            
            # Baseline hit rate floor: 50% (very relaxed - tiers will apply stricter filters)
            if true_hit_rate < 50.0:
                gate_stats['fail_hit_rate'] += 1
                continue
            
            # ================================================================
            # SAFETY FILTER 4: CV Check (relaxed - tiers apply stricter)
            # ================================================================
            cv = prop.get("cv") or prop.get("vk_cv") or 0.5
            if cv > 1:
                cv = cv / 100.0
            
            # Max CV 0.80 (tiers will apply stricter limits)
            if cv > 0.80:
                gate_stats['fail_cv'] += 1
                continue
            
            # ================================================================
            # SAFETY FILTER 5: THE ACTUARY KILL SWITCH (0.0 floor)
            # ================================================================
            prob_data = calculate_master_probability(dk_odds, true_hit_rate, prop_type)
            market_prob = prob_data['market_prob']
            propvision_true_prob = prob_data['propvision_true_prob']
            casino_req_rate = prob_data['casino_req_rate']
            true_edge = prob_data['true_edge']
            
            # KILL SWITCH: Must have positive edge
            if true_edge <= 0.0:
                gate_stats['fail_actuary_gate'] += 1
                continue
            
            # ================================================================
            # PASSED ALL SAFETY FILTERS - Add to Qualified Pool
            # ================================================================
            gate_stats['qualified_pool'] += 1
            
            # ================================================================
            # v2.0: CALL TRAINED MLB XGBoost MODEL - STRICT ENFORCEMENT
            # ================================================================
            # v2.0: MLB PHYSICAL ENGINE (64-feature XGBoost)
            # NO FALLBACKS - If model fails, prop is DISQUALIFIED
            # ================================================================
            mlr_prediction = None
            mlr_std_dev = None
            mlr_success = False
            mlr_matchup = {}
            vision_summary = None
            
            # Try Physical Engine first (64-feature model)
            if self.mlb_physical_engine:
                try:
                    opponent = prop.get('opponent') or prop.get('opponent_abbr')
                    park_team = prop.get('home_team') if prop.get('is_away_team') else prop.get('team')
                    dk_odds_int = int(dk_odds) if dk_odds else None
                    
                    # Get pitcher hand if available
                    pitcher_hand = prop.get('opposing_pitcher_hand') or prop.get('pitcher_hand')
                    
                    # Call Physical Engine
                    apex_result = self.mlb_physical_engine.predict(
                        player_name=player_name,
                        stat_type=stat_type,
                        line=line,
                        opponent_team=opponent,
                        park_team=park_team,
                        pitcher_hand=pitcher_hand,
                        dk_odds=dk_odds_int
                    )
                    
                    if apex_result.is_valid and apex_result.mlr_predicted is not None:
                        mlr_prediction = apex_result.mlr_predicted
                        mlr_std_dev = apex_result.sigma_used
                        mlr_matchup = apex_result.mlr_matchup
                        mlr_success = True
                        
                        # Build Vision Summary (Park + Splits explanation)
                        park_info = mlr_matchup.get('park', {})
                        splits_info = mlr_matchup.get('splits', {})
                        trends_info = mlr_matchup.get('trends', {})
                        
                        park_factor = park_info.get('factor', 1.0)
                        matchup_avg = splits_info.get('matchup_avg', 0)
                        platoon = splits_info.get('platoon_split', 0)
                        l10_avg = trends_info.get('l10_avg', 0)
                        
                        # Create vision text
                        park_desc = "neutral"
                        if park_factor > 1.05:
                            park_desc = "hitter-friendly"
                        elif park_factor < 0.95:
                            park_desc = "pitcher-friendly"
                        
                        vision_summary = (
                            f"Park: {park_info.get('venue', 'N/A')} ({park_desc}, {park_factor:.2f}x) | "
                            f"vs {pitcher_hand or 'TBD'}HP: .{int(matchup_avg*1000):03d} | "
                            f"L10: {l10_avg:.1f} | σ={mlr_std_dev:.2f}"
                        )
                        
                        logger.info(
                            f"[MLB_APEX] {player_name} {stat_type}: pred={mlr_prediction:.2f}, "
                            f"park={park_factor:.2f}, σ={mlr_std_dev:.3f}, "
                            f"P(over)={apex_result.vk_prob_over}%, edge={apex_result.vk_edge}"
                        )
                    else:
                        error_msg = apex_result.error or 'Unknown'
                        logger.warning(f"[MLB_APEX_FAIL] {player_name} {stat_type}: {error_msg}")
                        
                except Exception as e:
                    logger.warning(f"[MLB_APEX_FAIL] {player_name} {stat_type}: {e}")
            
            # Fallback to legacy VK model if Physical Engine failed
            if not mlr_success and self.mlb_vegas_killer_model:
                try:
                    opponent = prop.get('opponent') or prop.get('opponent_abbr')
                    park_team = prop.get('home_team') if prop.get('is_away_team') else prop.get('team')
                    
                    vk_result_legacy = self.mlb_vegas_killer_model.predict(
                        player_name,
                        stat_type,
                        line=line,
                        opponent_team=opponent,
                        park_team=park_team
                    )
                    
                    if vk_result_legacy and not vk_result_legacy.get('error'):
                        mlr_prediction = vk_result_legacy.get('predicted')
                        mlr_std_dev = vk_result_legacy.get('std_dev')
                        mlr_matchup = vk_result_legacy.get('full_features', {}).get('matchup', {})
                        
                        if mlr_prediction is not None and not (isinstance(mlr_prediction, float) and np.isnan(mlr_prediction)):
                            mlr_success = True
                            vision_summary = f"Legacy VK Model | pred={mlr_prediction:.2f}"
                            logger.info(f"[MLB_VK_FALLBACK] {player_name} {stat_type}: pred={mlr_prediction:.2f}")
                except Exception as e:
                    logger.warning(f"[MLB_VK_FALLBACK_FAIL] {player_name} {stat_type}: {e}")
            
            # STRICT DISQUALIFICATION: No MLR = No Elite Tier
            if not mlr_success:
                gate_stats['fail_mlr_model'] = gate_stats.get('fail_mlr_model', 0) + 1
                continue
            
            # ================================================================
            # VK MODEL ENFORCEMENT - Use MLR prediction for probability
            # ================================================================
            vk_result = calculate_vk_model(
                predicted_value=mlr_prediction,
                line=line,
                dk_odds=dk_odds,
                season_avg=prop.get('season_average'),
                require_market=True,
                std_dev=mlr_std_dev,
                player_name=player_name,
                stat_type=stat_type,
                sport="MLB"
            )
            
            if not vk_result.is_valid:
                gate_stats['fail_vk_model'] = gate_stats.get('fail_vk_model', 0) + 1
                continue
            
            # Get additional data for scoring
            raw_vk_pred = mlr_prediction  # Use MLR prediction (not season_avg fallback)
            matchup_modifier = prop.get("matchup_modifier", 1.0)
            tempo_modifier = prop.get("tempo_modifier", 1.0)
            raw_edge = (raw_vk_pred - line) if line > 0 else 0
            
            # Calculate board scores for sorting
            sh_board_score = (true_edge * 3.0) - (cv * 15)  # Safe Haven formula
            fl_board_score = (true_edge * 4.0) + (true_hit_rate * 0.5) - (cv * 10)  # Front Lines formula
            wz_board_score = (true_edge * 15.0) + (true_hit_rate * 2.0) - (cv * 5)  # War Zone formula
            
            qualified_prop = {
                # Player info
                'player_name': player_name,
                'team': prop.get('team'),
                'opponent': prop.get('opponent') or prop.get('opponent_abbr'),
                'photo_url': prop.get('photo_url') or prop.get('headshot_url'),
                'headshot_url': prop.get('headshot_url'),
                'game_time': prop.get('game_time') or prop.get('commence_time'),
                
                # Prop details
                'stat_type': stat_type.replace("_", " ").title(),
                'stat_key': stat_key,
                'line': line,
                'dk_odds': dk_odds,
                'pp_odds': prop.get('pp_odds') or prop.get('all_odds', {}).get('prizepicks'),
                
                # Classification
                'prop_type': prop_type,
                'is_goblin': is_goblin,
                'is_demon': is_demon,
                'is_standard': not is_goblin and not is_demon,
                'lineup_status': lineup_status,
                
                # Hit rate & consistency
                'games_played': games_played,
                'hit_count': hit_count,
                'true_hit_rate': round(true_hit_rate, 1),
                'cv': round(cv, 3),
                
                # PropVision math (MASTER FUNCTION)
                'market_prob': market_prob,
                'propvision_true_prob': propvision_true_prob,
                'casino_req_rate': casino_req_rate,
                'true_edge': true_edge,
                
                # Board scores for each tier
                'sh_board_score': round(sh_board_score, 1),
                'fl_board_score': round(fl_board_score, 1),
                'wz_board_score': round(wz_board_score, 1),
                
                # v2.0: MLR MODEL OUTPUT (HIGH PRECISION)
                'vk_predicted': round(mlr_prediction, 2),
                'mlr_raw_prediction': mlr_prediction,
                'vk_prob_over': vk_result.vk_prob_over,
                'vk_prob_under': vk_result.vk_prob_under,
                'vk_edge': vk_result.vk_edge,
                'vk_verdict': vk_result.vk_verdict,
                'vk_sigma_used': vk_result.standard_deviation_used,
                'vk_sigma_source': vk_result.sigma_source,
                'vk_z_score': vk_result.z_score,
                
                # v2.0: MLB MATCHUP FEATURES (Park Factors, etc.)
                'mlr_matchup': mlr_matchup,
                'mlr_features_used': True,
                'park_factor': mlr_matchup.get('park', {}).get('factor') or mlr_matchup.get('park_factor'),
                'opp_k_rate': mlr_matchup.get('opponent', {}).get('k_rate') or mlr_matchup.get('opp_k_rate'),
                
                # v2.0: VISION SUMMARY (Park + Splits human-readable)
                'vision_summary': vision_summary,
                
                'matchup_modifier': round(matchup_modifier, 3),
                'tempo_modifier': round(tempo_modifier, 3),
                
                # Carry forward intel
                'vision_intel': prop.get('vision_intel'),
                'intel_score': prop.get('intel_score'),
                'intel_verdict': prop.get('intel_verdict'),
                'bdl_modifiers': prop.get('bdl_modifiers'),
                
                # Timestamp
                'synced_at': datetime.now(timezone.utc).isoformat(),
            }
            
            qualified_pool.append(qualified_prop)
        
        # Log pool statistics
        logger.info("[ELITE_TOP_10] Safety Filter Results:")
        logger.info(f"  Failed Lineup (BENCHED): {gate_stats['fail_lineup']}")
        logger.info(f"  Failed Weather: {gate_stats['fail_weather']}")
        logger.info(f"  Failed Hit Rate (<50%): {gate_stats['fail_hit_rate']}")
        logger.info(f"  Failed CV (>0.80): {gate_stats['fail_cv']}")
        logger.info(f"  *** KILLED BY ACTUARY GATE (<=0%): {gate_stats['fail_actuary_gate']} ***")
        logger.info(f"  QUALIFIED POOL SIZE: {gate_stats['qualified_pool']}")
        
        # ====================================================================
        # STEP 2A: WAR ZONE CLAIMS FIRST (High-Alpha Payouts)
        # ====================================================================
        # ====================================================================
        # STEP 1: WAR ZONE (Sort by vk_edge DESC - MLR Supremacy)
        # ====================================================================
        # Demons + Standards with DK > +100, sorted by vk_edge DESC (MLR arbitrage)
        
        war_zone_candidates = [
            p for p in qualified_pool
            if p['prop_type'] == 'DEMON' or (p['prop_type'] == 'STANDARD' and (p['dk_odds'] or 0) > 100)
        ]
        
        # Additional War Zone filter: vk_edge >= 10% for high-alpha (using MLR model)
        war_zone_candidates = [p for p in war_zone_candidates if p.get('vk_edge', 0) >= 10.0]
        
        # PRIMARY SORT: vk_edge DESC (MLR arbitrage - biggest market disagreements)
        war_zone_candidates.sort(key=lambda x: x.get('vk_edge', 0), reverse=True)
        
        # Dedupe by player+stat
        wz_seen = set()
        war_zone_picks = []
        for p in war_zone_candidates:
            key = f"{p['player_name']}|{p['stat_key']}"
            if key not in wz_seen and len(war_zone_picks) < 10:
                wz_seen.add(key)
                p['tier'] = 'war_zone'
                p['tier_label'] = 'MLB War Zone (Elite 10 - vk_edge Sorted)'
                p['board_score'] = p.get('vk_edge', 0)  # Use vk_edge as board_score
                war_zone_picks.append(p)
        
        # REMOVE claimed props from pool
        claimed_keys = {f"{p['player_name']}|{p['stat_key']}" for p in war_zone_picks}
        remaining_pool = [p for p in qualified_pool if f"{p['player_name']}|{p['stat_key']}" not in claimed_keys]
        
        logger.info(f"[ELITE_TOP_10] WAR ZONE claimed: {len(war_zone_picks)} picks")
        logger.info(f"  Remaining pool: {len(remaining_pool)}")
        
        # ====================================================================
        # STEP 2: SAFE HAVEN (MLR Supremacy - Sort by vk_prob_over DESC)
        # ====================================================================
        # Goblins only, sorted PURELY by vk_prob_over (MLR predictive model)
        # Historical rates (L5/L10) are metadata only - NOT sort keys
        
        safe_haven_candidates = [
            p for p in remaining_pool
            if p['prop_type'] == 'GOBLIN'
        ]
        
        # Additional Safe Haven filters: 
        # - HR >= 60% (trust gate)
        # - CV <= 0.70 (consistency gate)
        # - vk_prob_over >= 70% (MLR SUPREMACY - predictive model MUST show strong confidence)
        safe_haven_candidates = [
            p for p in safe_haven_candidates 
            if p['true_hit_rate'] >= 60.0 
            and p['cv'] <= 0.70
            and p.get('vk_prob_over', 0) >= 70.0  # MLR SUPREMACY: Reject < 70%
        ]
        
        # PRIMARY SORT: vk_prob_over DESC (MLR predictive model is king)
        # Historical L10 hit rate is NO LONGER a sort key
        safe_haven_candidates.sort(
            key=lambda x: x.get('vk_prob_over', 0), 
            reverse=True
        )
        
        logger.info(f"[ELITE_TOP_10] Safe Haven after MLR filter: {len(safe_haven_candidates)} candidates (vk_prob_over >= 70%)")
        
        # Dedupe by player+stat
        sh_seen = set()
        safe_haven_picks = []
        for p in safe_haven_candidates:
            key = f"{p['player_name']}|{p['stat_key']}"
            if key not in sh_seen and len(safe_haven_picks) < 10:
                sh_seen.add(key)
                p['tier'] = 'safe_haven'
                p['tier_label'] = 'MLB Safe Haven (Elite 10 - MLR Sorted)'
                p['board_score'] = p.get('vk_prob_over', 0)  # Use vk_prob_over as board_score
                safe_haven_picks.append(p)
        
        # REMOVE claimed props from pool
        claimed_keys = {f"{p['player_name']}|{p['stat_key']}" for p in safe_haven_picks}
        remaining_pool = [p for p in remaining_pool if f"{p['player_name']}|{p['stat_key']}" not in claimed_keys]
        
        logger.info(f"[ELITE_TOP_10] SAFE HAVEN claimed: {len(safe_haven_picks)} picks")
        logger.info(f"  Remaining pool: {len(remaining_pool)}")
        
        # ====================================================================
        # STEP 3: FRONT LINES (Sort by vk_edge DESC - MLR Arbitrage)
        # ====================================================================
        # Everything remaining, sorted by vk_edge (MLR vs market disagreement)
        
        front_lines_candidates = remaining_pool.copy()
        
        # Additional Front Lines filters: HR >= 55%, CV <= 0.75
        front_lines_candidates = [
            p for p in front_lines_candidates 
            if p['true_hit_rate'] >= 55.0 and p['cv'] <= 0.75
        ]
        
        # PRIMARY SORT: vk_edge DESC (MLR arbitrage - biggest disagreements with market)
        front_lines_candidates.sort(key=lambda x: x.get('vk_edge', 0), reverse=True)
        
        # Dedupe by player+stat
        fl_seen = set()
        front_lines_picks = []
        for p in front_lines_candidates:
            key = f"{p['player_name']}|{p['stat_key']}"
            if key not in fl_seen and len(front_lines_picks) < 10:
                fl_seen.add(key)
                p['tier'] = 'front_lines'
                p['tier_label'] = 'MLB Front Lines (Elite 10 - vk_edge Sorted)'
                p['board_score'] = p.get('vk_edge', 0)  # Use vk_edge as board_score
                front_lines_picks.append(p)
        
        logger.info(f"[ELITE_TOP_10] FRONT LINES claimed: {len(front_lines_picks)} picks")
        
        # ====================================================================
        # LOG FINAL RESULTS
        # ====================================================================
        logger.info("=" * 70)
        logger.info("[ELITE_TOP_10] FINAL TIER ASSIGNMENTS (Exclusive - No Duplicates):")
        logger.info("=" * 70)
        
        logger.info(f"\n[WAR ZONE] Top {len(war_zone_picks)} High-Alpha Plays:")
        for i, p in enumerate(war_zone_picks, 1):
            logger.info(f"  {i}. {p['player_name']} - {p['stat_key']} [{p['prop_type']}] | "
                       f"TRUE EDGE: +{p['true_edge']:.1f}% | PropVision: {p['propvision_true_prob']}%")
        
        logger.info(f"\n[SAFE HAVEN] Top {len(safe_haven_picks)} Stability Plays:")
        for i, p in enumerate(safe_haven_picks, 1):
            logger.info(f"  {i}. {p['player_name']} - {p['stat_key']} [{p['prop_type']}] | "
                       f"PropVision: {p['propvision_true_prob']}% | TRUE EDGE: +{p['true_edge']:.1f}%")
        
        logger.info(f"\n[FRONT LINES] Top {len(front_lines_picks)} Universal Value Plays:")
        for i, p in enumerate(front_lines_picks, 1):
            logger.info(f"  {i}. {p['player_name']} - {p['stat_key']} [{p['prop_type']}] | "
                       f"Board: {p['board_score']} | TRUE EDGE: +{p['true_edge']:.1f}%")
        
        # Verify no duplicates
        all_keys = set()
        for tier_name, picks in [('WAR_ZONE', war_zone_picks), ('SAFE_HAVEN', safe_haven_picks), ('FRONT_LINES', front_lines_picks)]:
            for p in picks:
                key = f"{p['player_name']}|{p['stat_key']}"
                if key in all_keys:
                    logger.error(f"[ELITE_TOP_10] DUPLICATE FOUND: {key}")
                all_keys.add(key)
        
        logger.info(f"\n[ELITE_TOP_10] Total unique picks: {len(all_keys)} (verified no duplicates)")
        logger.info("=" * 70)
        
        return {
            'war_zone': war_zone_picks,
            'safe_haven': safe_haven_picks,
            'front_lines': front_lines_picks,
        }
    
    async def build_safe_haven_tier(self, all_picks: List[Dict]) -> List[Dict]:
        """
        Build the MLB Safe Haven 2.0 tier with the ACTUARY GATE.
        
        The Actuary Gate compares our internal True Probability against 
        PrizePicks' dynamic Goblin Tax curve. If we can't beat the casino's
        required win rate, the prop is mathematically a losing bet.
        
        PIPELINE:
        Phase 1: Strict Baseline Gates (Lineup, Weather, L20 >= 75%, CV <= 0.65)
        Phase 2: Internal Math (PropVision vk_prob_over)
        Phase 3: ACTUARY GATE (propvision_prob vs casino_req_rate)
        Phase 4: Output & Sorting (board_score weighted by propvision_edge)
        
        Args:
            all_picks: List of all props from the pipeline
            
        Returns:
            List of Top 10 qualified Safe Haven picks that BEAT the Goblin Tax
        """
        logger.info("[MLB_SAFE_HAVEN 2.0] Building Safe Haven with ACTUARY GATE (GOBLIN-ONLY)...")
        logger.info(f"[MLB_SAFE_HAVEN 2.0] Input: {len(all_picks)} props to evaluate")
        
        # Batter stats affected by wind
        BATTER_STATS = {"HITS", "TB", "HRR", "RBIS", "RUNS", "SINGLES", "DOUBLES", "HR", "SB", "BB", "TOTAL BASES"}
        
        # Track gate statistics
        gate_stats = {
            'total_input': len(all_picks),
            'fail_prop_type': 0,
            'fail_lineup': 0,
            'fail_weather': 0,
            'fail_hit_rate': 0,
            'fail_cv': 0,
            'fail_actuary_gate': 0,
            'qualified': 0,
        }
        
        qualified_picks = []
        
        for prop in all_picks:
            # Get basic prop info
            player_name = prop.get("player_name", "Unknown")
            stat_type = (prop.get("stat_type") or "").upper()
            stat_key = stat_type.replace(" ", "_")
            line = prop.get("line", 0)
            
            # Get prop classification
            is_goblin = prop.get("is_goblin", False)
            is_demon = prop.get("is_demon", False)
            prop_type = "GOBLIN" if is_goblin else ("DEMON" if is_demon else "STANDARD")
            
            # Get DK odds for Actuary Gate
            dk_odds = prop.get("dk_odds")
            if dk_odds is None:
                dk_odds = prop.get("all_odds", {}).get("draftkings")
            
            # ================================================================
            # PHASE 0: MARKET-FIRST FILTER (dk_odds REQUIRED)
            # ================================================================
            # A prop MUST have non-null, non-zero dk_odds to be eligible
            if dk_odds is None or dk_odds == 0:
                gate_stats['fail_market_first'] = gate_stats.get('fail_market_first', 0) + 1
                continue
            
            # ================================================================
            # PHASE 1: STRICT BASELINE GATES
            # ================================================================
            
            # 1a. STRICT PROP TYPE GATE - Safe Haven is GOBLIN-ONLY
            # Reject Demons and Standard props - this is our premium stability board
            if prop_type != 'GOBLIN':
                gate_stats['fail_prop_type'] += 1
                continue
            
            # 1b. Lineup Status Gate - Allow confirmed starters and projected starters
            # Kill benched players and unknowns (early-day protection without empty board)
            current_status = prop.get('lineup_status')
            if current_status not in ["CONFIRMED", "PROJECTED"]:
                gate_stats['fail_lineup'] += 1
                continue
            
            # 1c. Weather Hard-Stop (Batter props only)
            if stat_key.replace("_", " ") in BATTER_STATS or stat_type in BATTER_STATS:
                weather = prop.get("weather", {}) or {}
                wind_direction = (weather.get("wind_direction") or "").upper()
                wind_speed = weather.get("wind_speed", 0) or 0
                
                if isinstance(wind_speed, str):
                    try:
                        wind_speed = float(wind_speed.replace("mph", "").strip())
                    except (ValueError, TypeError):
                        wind_speed = 0
                
                if wind_direction == "IN" and wind_speed > 12:
                    gate_stats['fail_weather'] += 1
                    continue
            
            # ================================================================
            # PHASE 2: DYNAMIC HIT RATE (Season-to-Date)
            # ================================================================
            # Early season fix: Use actual games played, not hardcoded 20
            
            games_played = prop.get("games_played") or prop.get("l10_games") or 0
            
            # Get hit count from various possible fields
            hit_count = prop.get("l20_hits") or prop.get("l10_hits") or 0
            
            # Also check hit_rate fields that may be pre-calculated
            existing_hit_rate = prop.get("hit_rate_l10") or prop.get("hit_rate_l20") or prop.get("h20_rate")
            
            # Calculate TRUE hit rate based on ACTUAL games played
            if games_played > 0 and hit_count > 0:
                # Dynamic formula: (hits / actual_games_played) * 100
                true_hit_rate = (hit_count / games_played) * 100
            elif existing_hit_rate is not None:
                # Use pre-calculated rate if available
                true_hit_rate = existing_hit_rate
            else:
                # No hit rate data available
                gate_stats['fail_hit_rate'] += 1
                continue
            
            # 2a. Hit Rate Floor: Filter out cold streaks (60% minimum)
            if true_hit_rate < 60.0:
                gate_stats['fail_hit_rate'] += 1
                continue
            
            # 2b. CV (Coefficient of Variation) <= 0.70
            cv = prop.get("cv") or prop.get("vk_cv")
            
            # Normalize CV if stored as percentage
            if cv is not None and cv > 1:
                cv = cv / 100.0
            
            if cv is None or cv > 0.70:
                gate_stats['fail_cv'] += 1
                continue
            
            # ================================================================
            # PHASE 3: THE PREDICTIVE ACTUARY GATE (MASTER FUNCTION)
            # ================================================================
            # Uses the centralized calculate_master_probability for consistent
            # edge calculations across ALL tiers.
            
            prob_data = calculate_master_probability(dk_odds, true_hit_rate, prop_type)
            market_prob = prob_data['market_prob']
            propvision_true_prob = prob_data['propvision_true_prob']
            casino_req_rate = prob_data['casino_req_rate']
            true_edge = prob_data['true_edge']
            
            # THE KILL SWITCH: If our blended model cannot beat the casino tax, kill it.
            if true_edge <= 0.0:
                gate_stats['fail_actuary_gate'] += 1
                continue
            
            # ================================================================
            # PHASE 4: QUALIFIED - Build Output
            # ================================================================
            gate_stats['qualified'] += 1
            
            # Get additional prop data
            raw_vk_pred = prop.get("vk_predicted") or prop.get("raw_vk_pred") or prop.get("season_average") or 0
            matchup_modifier = prop.get("matchup_modifier", 1.0)
            tempo_modifier = prop.get("tempo_modifier", 1.0)
            season_avg = prop.get("season_average") or prop.get("season_avg") or 0
            
            # Raw edge (prediction vs line)
            raw_edge = (raw_vk_pred - line) if line > 0 else 0
            
            # ================================================================
            # VK MODEL ENFORCEMENT - MANDATORY HANDSHAKE
            # ================================================================
            # VK MODEL ENFORCEMENT - MANDATORY HANDSHAKE (with CV for proper stats)
            # v2.0: TRUE VARIANCE - pass player_name and stat_type for L10 DB lookup
            # ================================================================
            vk_result = calculate_vk_model(
                predicted_value=raw_vk_pred,
                line=line,
                dk_odds=dk_odds,
                season_avg=season_avg,
                cv=cv,  # Pass CV for dynamic standard deviation calculation
                player_name=player_name,
                stat_type=stat_type,
                sport="MLB"
            )
            
            # STRICT: If VK model failed, log critical error
            if not vk_result.is_valid:
                logger.critical(f"[MLB_SAFE_HAVEN] VK MODEL FAILED for {player_name} - RETRYING")
                # Retry with fallback values
                vk_result = calculate_vk_model(
                    predicted_value=season_avg or line,
                    line=line,
                    dk_odds=dk_odds,
                    season_avg=season_avg,
                    cv=cv,
                    player_name=player_name,
                    stat_type=stat_type,
                    sport="MLB"
                )
            
            # Calculate Board Score - Weights true edge heavily, penalizes volatility
            # Formula: (true_edge * 3.0) - (cv * 15)
            board_score = (true_edge * 3.0) - (cv * 15)
            
            qualified_pick = {
                # Player info
                'player_name': player_name,
                'team': prop.get('team'),
                'opponent': prop.get('opponent') or prop.get('opponent_abbr'),
                'photo_url': prop.get('photo_url') or prop.get('headshot_url'),
                'headshot_url': prop.get('headshot_url'),
                'game_time': prop.get('game_time') or prop.get('commence_time'),
                
                # Prop details
                'stat_type': stat_type.replace("_", " ").title(),
                'stat_key': stat_key,
                'line': line,
                'dk_odds': dk_odds,
                'pp_odds': prop.get('pp_odds') or prop.get('all_odds', {}).get('prizepicks'),
                
                # Classification
                'prop_type': prop_type,
                'is_goblin': is_goblin,
                'is_demon': is_demon,
                'lineup_status': current_status,
                
                # Dynamic Hit Rate (Season-to-Date)
                'games_played': games_played,
                'hit_count': hit_count,
                'true_hit_rate': round(true_hit_rate, 1),
                
                # *** HIT RATES FOR FRONTEND DISPLAY ***
                'h5_rate': prop.get('hit_rate_l5') or prop.get('h5_rate'),
                'h10_rate': prop.get('hit_rate_l10') or prop.get('h10_rate'),
                'season_avg': prop.get('season_average') or prop.get('season_avg'),
                'hit_rates': {
                    'l5': prop.get('hit_rate_l5') or prop.get('h5_rate'),
                    'l10': prop.get('hit_rate_l10') or prop.get('h10_rate'),
                },
                'last_10_games': prop.get('last_10_games'),
                
                # *** VK PROBABILITY FOR VISION MODEL DISPLAY ***
                'vk_prob_over': vk_result.vk_prob_over,
                'vk_prob_under': vk_result.vk_prob_under,
                'vk_verdict': vk_result.vk_verdict,
                'vk_edge': vk_result.vk_edge,
                'vk_recommendation': vk_result.vk_recommendation,
                'vk_confidence': vk_result.confidence_score,
                
                # Consistency
                'cv': round(cv, 3) if cv else None,
                
                # PropVision internal math
                'raw_vk_pred': round(raw_vk_pred, 2) if raw_vk_pred else None,
                'vk_predicted': round(raw_vk_pred * matchup_modifier * tempo_modifier, 2) if raw_vk_pred else None,
                'matchup_modifier': round(matchup_modifier, 3),
                'tempo_modifier': round(tempo_modifier, 3),
                
                # *** PREDICTIVE ACTUARY GATE FIELDS ***
                'market_prob': round(market_prob, 1),
                'propvision_true_prob': round(propvision_true_prob, 1),
                'casino_req_rate': round(casino_req_rate, 1),
                'true_edge': round(true_edge, 1),
                
                # Board score
                'board_score': round(board_score, 1),
                
                # Tier classification
                'tier': 'safe_haven',
                'tier_label': 'MLB Safe Haven 2.0 (Predictive)',
                'oracle_apex_qualified': True,
                'actuary_gate_passed': True,
                
                # Gate info for debugging
                'gate_info': {
                    'market_prob': round(market_prob, 1),
                    'true_hit_rate': round(true_hit_rate, 1),
                    'propvision_true_prob': round(propvision_true_prob, 1),
                    'casino_req_rate': round(casino_req_rate, 1),
                    'true_edge': round(true_edge, 1),
                    'hit_rate_floor': 60.0,
                    'cv_max': 0.70,
                },
                
                # Carry forward vision intel
                'vision_intel': prop.get('vision_intel'),
                'intel_score': prop.get('intel_score'),
                'intel_verdict': prop.get('intel_verdict'),
                'bdl_modifiers': prop.get('bdl_modifiers'),
                
                # Timestamp
                'synced_at': datetime.now(timezone.utc).isoformat(),
            }
            
            qualified_picks.append(qualified_pick)
        
        # ====================================================================
        # FINAL SORT & SLICE
        # ====================================================================
        
        # Log gate statistics
        logger.info("[MLB_SAFE_HAVEN 2.0] Gate Statistics (Predictive Model):")
        logger.info(f"  Total Input: {gate_stats['total_input']}")
        logger.info(f"  Failed Prop Type (Non-GOBLIN): {gate_stats['fail_prop_type']}")
        logger.info(f"  Failed Lineup: {gate_stats['fail_lineup']}")
        logger.info(f"  Failed Weather: {gate_stats['fail_weather']}")
        logger.info(f"  Failed Hit Rate (<60%): {gate_stats['fail_hit_rate']}")
        logger.info(f"  Failed CV (>0.70): {gate_stats['fail_cv']}")
        logger.info(f"  *** KILLED BY PREDICTIVE ACTUARY GATE: {gate_stats['fail_actuary_gate']} ***")
        logger.info(f"  QUALIFIED (Goblins that Beat the Tax): {gate_stats['qualified']}")
        
        # Sort descending by Board_Score
        qualified_picks.sort(key=lambda x: x.get('board_score', 0), reverse=True)
        
        # Dedupe: Keep highest board_score per player+stat
        dedupe_map = {}
        for pick in qualified_picks:
            key = f"{pick['player_name']}|{pick['stat_key']}"
            if key not in dedupe_map or pick['board_score'] > dedupe_map[key]['board_score']:
                dedupe_map[key] = pick
        
        final_picks = list(dedupe_map.values())
        final_picks.sort(key=lambda x: x.get('board_score', 0), reverse=True)
        
        # Slice to Top 10
        top_10 = final_picks[:10]
        
        logger.info(f"[MLB_SAFE_HAVEN 2.0] Final Result: {len(top_10)} picks (Predictive Model)")
        
        for i, pick in enumerate(top_10[:5], 1):
            logger.info(f"[MLB_SAFE_HAVEN 2.0]   {i}. {pick['player_name']} - {pick['stat_key']} | "
                       f"HR: {pick['true_hit_rate']}% + Market: {pick['market_prob']}% = "
                       f"PropVision: {pick['propvision_true_prob']}% vs Casino: {pick['casino_req_rate']}% | "
                       f"TRUE EDGE: +{pick['true_edge']:.1f}% | Board: {pick['board_score']}")
        
        return top_10
    
    async def build_front_lines_tier(self, all_picks: List[Dict]) -> List[Dict]:
        """
        Build the MLB Front Lines tier - Standard Props and 'Broken' Goblins.
        
        FRONT LINES 2.0 LOGIC (2026 Season):
        =====================================
        
        Front Lines targets Standard props and "broken" Goblins (Goblins that fail Safe Haven's
        strict 60% hit rate / 0.70 CV thresholds but still have positive edge).
        
        CONTENT FILTER (Tier Differentiation):
        - STANDARD props: ✅ ALLOWED
        - GOBLIN props: ✅ ALLOWED (catches "broken" Goblins that miss Safe Haven)
        - DEMON props: ❌ BLOCKED (reserved for War Zone)
        
        GATES:
        1. Lineup: Allow CONFIRMED, PROJECTED, UNKNOWN (reject only BENCHED)
        2. Hit Rate >= 55%
        3. CV <= 0.75
        4. True Edge > 0 (MASTER 50/50 BLEND)
        
        BOARD SCORE:
        - board_score = (true_edge * 4.0) + (true_hit_rate * 0.5) - (cv * 10)
        
        Args:
            all_picks: List of all props from the pipeline
            
        Returns:
            List of Top 10 qualified Front Lines picks (Standards + Broken Goblins)
        """
        logger.info("[MLB_FRONT_LINES 2.0] Building Front Lines (Standards + Broken Goblins)...")
        logger.info(f"[MLB_FRONT_LINES 2.0] Input: {len(all_picks)} props to evaluate")
        
        # Track gate statistics
        gate_stats = {
            'total_input': len(all_picks),
            'fail_demon_blocked': 0,
            'fail_lineup': 0,
            'fail_hit_rate': 0,
            'fail_cv': 0,
            'fail_actuary_gate': 0,
            'qualified': 0,
            'goblins_qualified': 0,
            'standards_qualified': 0,
        }
        
        qualified_picks = []
        
        for prop in all_picks:
            # Get basic prop info
            player_name = prop.get("player_name", "Unknown")
            stat_type = (prop.get("stat_type") or "").upper()
            stat_key = stat_type.replace(" ", "_")
            line = prop.get("line", 0)
            
            # Get prop classification
            is_goblin = prop.get("is_goblin", False)
            is_demon = prop.get("is_demon", False)
            prop_type = "GOBLIN" if is_goblin else ("DEMON" if is_demon else "STANDARD")
            
            # Get DK odds
            dk_odds = prop.get("dk_odds")
            if dk_odds is None:
                dk_odds = prop.get("all_odds", {}).get("draftkings")
            
            # ================================================================
            # PHASE 0: MARKET-FIRST FILTER (dk_odds REQUIRED)
            # ================================================================
            # A prop MUST have non-null, non-zero dk_odds to be eligible
            if dk_odds is None or dk_odds == 0:
                gate_stats['fail_market_first'] = gate_stats.get('fail_market_first', 0) + 1
                continue
            
            # ================================================================
            # PHASE 1: CONTENT FILTER - Block Demons (reserved for War Zone)
            # ================================================================
            if prop_type == 'DEMON':
                gate_stats['fail_demon_blocked'] += 1
                continue
            
            # ================================================================
            # PHASE 2: LINEUP GATE (Hybrid)
            # ================================================================
            # Allow CONFIRMED, PROJECTED, and UNKNOWN. Only reject explicitly BENCHED.
            lineup_status = prop.get('lineup_status')
            if lineup_status == "BENCHED":
                gate_stats['fail_lineup'] += 1
                continue
            
            # ================================================================
            # PHASE 3: BASELINE FILTERS
            # ================================================================
            
            # 3a. Dynamic Hit Rate (Season-to-Date)
            games_played = prop.get("games_played") or prop.get("l10_games") or 0
            hit_count = prop.get("l20_hits") or prop.get("l10_hits") or 0
            existing_hit_rate = prop.get("hit_rate_l10") or prop.get("hit_rate_l20") or prop.get("h20_rate")
            
            # Calculate TRUE hit rate based on ACTUAL games played
            if games_played > 0 and hit_count > 0:
                true_hit_rate = (hit_count / games_played) * 100
            elif existing_hit_rate is not None:
                true_hit_rate = existing_hit_rate
            else:
                gate_stats['fail_hit_rate'] += 1
                continue
            
            # Hit Rate Floor: 55% (lower than Safe Haven)
            if true_hit_rate < 55.0:
                gate_stats['fail_hit_rate'] += 1
                continue
            
            # 3b. CV (Coefficient of Variation) <= 0.75
            cv = prop.get("cv") or prop.get("vk_cv")
            
            # Normalize CV if stored as percentage
            if cv is not None and cv > 1:
                cv = cv / 100.0
            
            if cv is None or cv > 0.75:
                gate_stats['fail_cv'] += 1
                continue
            
            # ================================================================
            # PHASE 4: THE PREDICTIVE ACTUARY GATE (MASTER FUNCTION)
            # ================================================================
            # Uses the centralized calculate_master_probability for consistent
            # edge calculations across ALL tiers.
            
            prob_data = calculate_master_probability(dk_odds, true_hit_rate, prop_type)
            market_prob = prob_data['market_prob']
            propvision_true_prob = prob_data['propvision_true_prob']
            casino_req_rate = prob_data['casino_req_rate']
            true_edge = prob_data['true_edge']
            
            # THE KILL SWITCH
            if true_edge <= 0.0:
                gate_stats['fail_actuary_gate'] += 1
                continue
            
            # ================================================================
            # PHASE 5: QUALIFIED - Build Output
            # ================================================================
            gate_stats['qualified'] += 1
            
            # Track prop type distribution (Demons are blocked, so won't count)
            if is_goblin:
                gate_stats['goblins_qualified'] += 1
            else:
                gate_stats['standards_qualified'] += 1
            
            # Get additional prop data
            raw_vk_pred = prop.get("vk_predicted") or prop.get("raw_vk_pred") or prop.get("season_average") or 0
            matchup_modifier = prop.get("matchup_modifier", 1.0)
            tempo_modifier = prop.get("tempo_modifier", 1.0)
            
            # Raw edge (prediction vs line)
            raw_edge = (raw_vk_pred - line) if line > 0 else 0
            
            # ================================================================
            # VK MODEL ENFORCEMENT - MANDATORY HANDSHAKE
            # v2.0: TRUE VARIANCE - pass player_name and stat_type for L10 DB lookup
            # ================================================================
            season_avg = prop.get("season_average") or prop.get("season_avg") or 0
            vk_result = calculate_vk_model(
                predicted_value=raw_vk_pred,
                line=line,
                dk_odds=dk_odds,
                season_avg=season_avg,
                player_name=player_name,
                stat_type=stat_type,
                sport="MLB"
            )
            
            # Calculate Board Score - Arbitrage-weighted (heavily favor true_edge)
            # Formula: (true_edge * 4.0) + (true_hit_rate * 0.5) - (cv * 10)
            board_score = (true_edge * 4.0) + (true_hit_rate * 0.5) - (cv * 10)
            
            qualified_pick = {
                # Player info
                'player_name': player_name,
                'team': prop.get('team'),
                'opponent': prop.get('opponent') or prop.get('opponent_abbr'),
                'photo_url': prop.get('photo_url') or prop.get('headshot_url'),
                'headshot_url': prop.get('headshot_url'),
                'game_time': prop.get('game_time') or prop.get('commence_time'),
                
                # Prop details
                'stat_type': stat_type.replace("_", " ").title(),
                'stat_key': stat_key,
                'line': line,
                'dk_odds': dk_odds,
                'pp_odds': prop.get('pp_odds') or prop.get('all_odds', {}).get('prizepicks'),
                
                # Classification
                'prop_type': prop_type,
                'is_goblin': is_goblin,
                'is_demon': is_demon,
                'is_standard': not is_goblin and not is_demon,
                'lineup_status': lineup_status,
                
                # Dynamic Hit Rate (Season-to-Date)
                'games_played': games_played,
                'hit_count': hit_count,
                'true_hit_rate': round(true_hit_rate, 1),
                
                # *** HIT RATES FOR FRONTEND DISPLAY ***
                'h5_rate': prop.get('hit_rate_l5') or prop.get('h5_rate'),
                'h10_rate': prop.get('hit_rate_l10') or prop.get('h10_rate'),
                'season_avg': prop.get('season_average') or prop.get('season_avg'),
                'hit_rates': {
                    'l5': prop.get('hit_rate_l5') or prop.get('h5_rate'),
                    'l10': prop.get('hit_rate_l10') or prop.get('h10_rate'),
                },
                'last_10_games': prop.get('last_10_games'),
                
                # *** VK PROBABILITY FOR VISION MODEL DISPLAY ***
                'vk_prob_over': vk_result.vk_prob_over,
                'vk_prob_under': vk_result.vk_prob_under,
                'vk_verdict': vk_result.vk_verdict,
                'vk_edge': vk_result.vk_edge,
                'vk_recommendation': vk_result.vk_recommendation,
                'vk_confidence': vk_result.confidence_score,
                
                # Consistency
                'cv': round(cv, 3) if cv else None,
                
                # PropVision internal math
                'raw_vk_pred': round(raw_vk_pred, 2) if raw_vk_pred else None,
                'vk_predicted': round(raw_vk_pred * matchup_modifier * tempo_modifier, 2) if raw_vk_pred else None,
                'matchup_modifier': round(matchup_modifier, 3),
                'tempo_modifier': round(tempo_modifier, 3),
                
                # *** PREDICTIVE ACTUARY GATE FIELDS ***
                'market_prob': round(market_prob, 1),
                'propvision_true_prob': round(propvision_true_prob, 1),
                'casino_req_rate': round(casino_req_rate, 1),
                'true_edge': round(true_edge, 1),
                
                # Board score
                'board_score': round(board_score, 1),
                
                # Tier classification
                'tier': 'front_lines',
                'tier_label': 'MLB Front Lines 2.0 (Arbitrage)',
                'front_lines_qualified': True,
                'actuary_gate_passed': True,
                
                # Gate info for debugging
                'gate_info': {
                    'market_prob': round(market_prob, 1),
                    'true_hit_rate': round(true_hit_rate, 1),
                    'propvision_true_prob': round(propvision_true_prob, 1),
                    'casino_req_rate': round(casino_req_rate, 1),
                    'true_edge': round(true_edge, 1),
                    'hit_rate_floor': 55.0,
                    'cv_max': 0.75,
                },
                
                # Carry forward vision intel
                'vision_intel': prop.get('vision_intel'),
                'intel_score': prop.get('intel_score'),
                'intel_verdict': prop.get('intel_verdict'),
                'bdl_modifiers': prop.get('bdl_modifiers'),
                
                # Timestamp
                'synced_at': datetime.now(timezone.utc).isoformat(),
            }
            
            qualified_picks.append(qualified_pick)
        
        # ====================================================================
        # FINAL SORT & SLICE
        # ====================================================================
        
        # Log gate statistics
        logger.info("[MLB_FRONT_LINES 2.0] Gate Statistics (Standards + Broken Goblins):")
        logger.info(f"  Total Input: {gate_stats['total_input']}")
        logger.info(f"  Failed DEMON Blocked (reserved for War Zone): {gate_stats['fail_demon_blocked']}")
        logger.info(f"  Failed Lineup (BENCHED only): {gate_stats['fail_lineup']}")
        logger.info(f"  Failed Hit Rate (<55%): {gate_stats['fail_hit_rate']}")
        logger.info(f"  Failed CV (>0.75): {gate_stats['fail_cv']}")
        logger.info(f"  *** KILLED BY MASTER ACTUARY GATE: {gate_stats['fail_actuary_gate']} ***")
        logger.info(f"  QUALIFIED: {gate_stats['qualified']} (Goblins: {gate_stats['goblins_qualified']} | Standards: {gate_stats['standards_qualified']})")
        
        # Sort descending by Board_Score
        qualified_picks.sort(key=lambda x: x.get('board_score', 0), reverse=True)
        
        # Dedupe: Keep highest board_score per player+stat
        dedupe_map = {}
        for pick in qualified_picks:
            key = f"{pick['player_name']}|{pick['stat_key']}"
            if key not in dedupe_map or pick['board_score'] > dedupe_map[key]['board_score']:
                dedupe_map[key] = pick
        
        final_picks = list(dedupe_map.values())
        final_picks.sort(key=lambda x: x.get('board_score', 0), reverse=True)
        
        # Slice to Top 10
        top_10 = final_picks[:10]
        
        logger.info(f"[MLB_FRONT_LINES 2.0] Final Result: {len(top_10)} picks (Predictive Arbitrage)")
        
        for i, pick in enumerate(top_10[:5], 1):
            logger.info(f"[MLB_FRONT_LINES 2.0]   {i}. {pick['player_name']} - {pick['stat_key']} [{pick['prop_type']}] | "
                       f"HR: {pick['true_hit_rate']}% + Market: {pick['market_prob']}% = "
                       f"PropVision: {pick['propvision_true_prob']}% vs Casino: {pick['casino_req_rate']}% | "
                       f"TRUE EDGE: +{pick['true_edge']:.1f}% | Board: {pick['board_score']}")
        
        return top_10
    
    async def build_war_zone_tier(self, all_picks: List[Dict]) -> List[Dict]:
        """
        Build the MLB War Zone tier - ONLY Demons and High-Odds Standards.
        
        WAR ZONE 2.0 LOGIC (2026 Season):
        ==================================
        
        CONTENT FILTER (Tier Differentiation):
        - DEMON props: ✅ ALLOWED (high-risk, high-reward)
        - STANDARD props with DK odds >= +150: ✅ ALLOWED (high-odds specials)
        - GOBLIN props: ❌ BLOCKED (reserved for Safe Haven / Front Lines)
        - STANDARD props with DK odds < +150: ❌ BLOCKED
        
        GATES:
        1. Content filter (Demons + High-Odds Standards only)
        2. Lineup: Allow CONFIRMED, PROJECTED, UNKNOWN (reject only BENCHED)
        3. True Edge >= 10.0 (MASTER 50/50 BLEND - aggressive floor)
        
        BOARD SCORE:
        - board_score = (true_edge * 15.0) + (true_hit_rate * 2.0) - (cv * 5)
        - Jackpot Ranker: finds biggest Vegas/PropVision disagreements
        
        TOP 10 CAP:
        - Returns only the Elite 10 highest-scoring props
        
        Args:
            all_picks: List of all props from the pipeline
            
        Returns:
            List of Top 10 Elite War Zone picks (Demons + High-Odds Standards)
        """
        logger.info("[MLB_WAR_ZONE 2.0] Building Elite 10 (Demons + High-Odds Standards)...")
        logger.info(f"[MLB_WAR_ZONE 2.0] Input: {len(all_picks)} props to evaluate")
        
        # Track gate statistics
        gate_stats = {
            'total_input': len(all_picks),
            'fail_market_first': 0,
            'fail_goblin': 0,
            'fail_standard_low_odds': 0,
            'fail_lineup': 0,
            'fail_hit_rate': 0,
            'fail_true_edge': 0,
            'qualified': 0,
            'demons_qualified': 0,
            'standards_qualified': 0,
        }
        
        qualified_picks = []
        
        for prop in all_picks:
            # Get basic prop info
            player_name = prop.get("player_name", "Unknown")
            stat_type = (prop.get("stat_type") or "").upper()
            stat_key = stat_type.replace(" ", "_")
            line = prop.get("line", 0)
            
            # Get prop classification
            is_goblin = prop.get("is_goblin", False)
            is_demon = prop.get("is_demon", False)
            prop_type = "GOBLIN" if is_goblin else ("DEMON" if is_demon else "STANDARD")
            
            # Get DK odds
            dk_odds = prop.get("dk_odds")
            if dk_odds is None:
                dk_odds = prop.get("all_odds", {}).get("draftkings")
            
            # ================================================================
            # PHASE 1: MARKET-FIRST FILTER (dk_odds REQUIRED)
            # ================================================================
            # A prop MUST have non-null, non-zero dk_odds to be eligible
            if dk_odds is None or dk_odds == 0:
                gate_stats['fail_market_first'] += 1
                continue
            
            # ================================================================
            # PHASE 2: CONTENT FILTER (Demons + High-Odds Standards ONLY)
            # ================================================================
            
            # Block Goblins (reserved for Safe Haven / Front Lines)
            if prop_type == 'GOBLIN':
                gate_stats['fail_goblin'] += 1
                continue
            
            # For Standard props, require high odds (>= +150)
            if prop_type == 'STANDARD':
                if dk_odds < 150:
                    gate_stats['fail_standard_low_odds'] += 1
                    continue
            
            # ================================================================
            # PHASE 3: LINEUP GATE (Hybrid)
            # ================================================================
            # Allow CONFIRMED, PROJECTED, UNKNOWN. Only reject explicitly BENCHED.
            lineup_status = prop.get('lineup_status')
            if lineup_status == "BENCHED":
                gate_stats['fail_lineup'] += 1
                continue
            
            # ================================================================
            # PHASE 3: DYNAMIC HIT RATE CALCULATION
            # ================================================================
            games_played = prop.get("games_played") or prop.get("l10_games") or 0
            hit_count = prop.get("l20_hits") or prop.get("l10_hits") or 0
            existing_hit_rate = prop.get("hit_rate_l10") or prop.get("hit_rate_l20") or prop.get("h20_rate")
            
            # Calculate TRUE hit rate based on ACTUAL games played
            if games_played > 0 and hit_count > 0:
                true_hit_rate = (hit_count / games_played) * 100
            elif existing_hit_rate is not None:
                true_hit_rate = existing_hit_rate
            else:
                gate_stats['fail_hit_rate'] += 1
                continue
            
            # CV for board score (no strict filter, but used in scoring)
            cv = prop.get("cv") or prop.get("vk_cv") or 0.5
            if cv > 1:
                cv = cv / 100.0
            
            # ================================================================
            # PHASE 4: THE PREDICTIVE ACTUARY GATE (MASTER FUNCTION)
            # ================================================================
            # Uses the SAME 50/50 blend as other tiers for consistent edge calculation.
            # Differentiation happens through the 10.0% floor, not different math.
            
            prob_data = calculate_master_probability(dk_odds, true_hit_rate, prop_type)
            market_prob = prob_data['market_prob']
            propvision_true_prob = prob_data['propvision_true_prob']
            casino_req_rate = prob_data['casino_req_rate']
            true_edge = prob_data['true_edge']
            
            # AGGRESSIVE FLOOR: If we are taking a 'War Zone' risk, the edge MUST be massive.
            # Require true_edge >= 10.0 to ensure only mathematically superior plays qualify.
            if true_edge < 10.0:
                gate_stats['fail_true_edge'] += 1
                continue
            
            # ================================================================
            # PHASE 5: QUALIFIED - Build Output
            # ================================================================
            gate_stats['qualified'] += 1
            
            # Track prop type distribution
            if is_demon:
                gate_stats['demons_qualified'] += 1
            else:
                gate_stats['standards_qualified'] += 1
            
            # Get additional prop data
            raw_vk_pred = prop.get("vk_predicted") or prop.get("raw_vk_pred") or prop.get("season_average") or 0
            matchup_modifier = prop.get("matchup_modifier", 1.0)
            tempo_modifier = prop.get("tempo_modifier", 1.0)
            
            # Raw edge (prediction vs line)
            raw_edge = (raw_vk_pred - line) if line > 0 else 0
            
            # ================================================================
            # VK MODEL ENFORCEMENT - MANDATORY HANDSHAKE
            # v2.0: TRUE VARIANCE - pass player_name and stat_type for L10 DB lookup
            # ================================================================
            season_avg = prop.get("season_average") or prop.get("season_avg") or 0
            vk_result = calculate_vk_model(
                predicted_value=raw_vk_pred,
                line=line,
                dk_odds=dk_odds,
                season_avg=season_avg,
                player_name=player_name,
                stat_type=stat_type,
                sport="MLB"
            )
            
            # Calculate Board Score - JACKPOT RANKER
            # Weight True Edge HEAVILY to find the biggest Vegas/PropVision disagreements.
            # Formula: (true_edge * 15.0) + (true_hit_rate * 2.0) - (cv * 5)
            board_score = (true_edge * 15.0) + (true_hit_rate * 2.0) - (cv * 5)
            
            qualified_pick = {
                # Player info
                'player_name': player_name,
                'team': prop.get('team'),
                'opponent': prop.get('opponent') or prop.get('opponent_abbr'),
                'photo_url': prop.get('photo_url') or prop.get('headshot_url'),
                'headshot_url': prop.get('headshot_url'),
                'game_time': prop.get('game_time') or prop.get('commence_time'),
                
                # Prop details
                'stat_type': stat_type.replace("_", " ").title(),
                'stat_key': stat_key,
                'line': line,
                'dk_odds': dk_odds,
                'pp_odds': prop.get('pp_odds') or prop.get('all_odds', {}).get('prizepicks'),
                
                # Classification
                'prop_type': prop_type,
                'is_goblin': False,  # Goblins are blocked
                'is_demon': is_demon,
                'is_standard': not is_demon,
                'lineup_status': lineup_status,
                
                # Dynamic Hit Rate (Season-to-Date)
                'games_played': games_played,
                'hit_count': hit_count,
                'true_hit_rate': round(true_hit_rate, 1),
                
                # *** HIT RATES FOR FRONTEND DISPLAY ***
                'h5_rate': prop.get('hit_rate_l5') or prop.get('h5_rate'),
                'h10_rate': prop.get('hit_rate_l10') or prop.get('h10_rate'),
                'season_avg': prop.get('season_average') or prop.get('season_avg'),
                'hit_rates': {
                    'l5': prop.get('hit_rate_l5') or prop.get('h5_rate'),
                    'l10': prop.get('hit_rate_l10') or prop.get('h10_rate'),
                },
                'last_10_games': prop.get('last_10_games'),
                
                # *** VK PROBABILITY FOR VISION MODEL DISPLAY ***
                'vk_prob_over': vk_result.vk_prob_over,
                'vk_prob_under': vk_result.vk_prob_under,
                'vk_verdict': vk_result.vk_verdict,
                'vk_edge': vk_result.vk_edge,
                'vk_recommendation': vk_result.vk_recommendation,
                'vk_confidence': vk_result.confidence_score,
                
                # Volatility
                'cv': round(cv, 3) if cv else None,
                
                # PropVision internal math
                'raw_vk_pred': round(raw_vk_pred, 2) if raw_vk_pred else None,
                'vk_predicted': round(raw_vk_pred * matchup_modifier * tempo_modifier, 2) if raw_vk_pred else None,
                'matchup_modifier': round(matchup_modifier, 3),
                'tempo_modifier': round(tempo_modifier, 3),
                
                # *** JACKPOT ACTUARY GATE FIELDS ***
                'market_prob': round(market_prob, 1),
                'propvision_true_prob': round(propvision_true_prob, 1),
                'casino_req_rate': round(casino_req_rate, 1),
                'true_edge': round(true_edge, 1),
                
                # Board score
                'board_score': round(board_score, 1),
                
                # Tier classification
                'tier': 'war_zone',
                'tier_label': 'MLB War Zone 2.0 (Elite 10)',
                'war_zone_qualified': True,
                'actuary_gate_passed': True,
                
                # Gate info for debugging
                'gate_info': {
                    'market_prob': round(market_prob, 1),
                    'true_hit_rate': round(true_hit_rate, 1),
                    'propvision_true_prob': round(propvision_true_prob, 1),
                    'casino_req_rate': round(casino_req_rate, 1),
                    'true_edge': round(true_edge, 1),
                    'true_edge_floor': 10.0,
                    'blend_ratio': 'MASTER 50/50 (same as all tiers)',
                },
                
                # Carry forward vision intel
                'vision_intel': prop.get('vision_intel'),
                'intel_score': prop.get('intel_score'),
                'intel_verdict': prop.get('intel_verdict'),
                'bdl_modifiers': prop.get('bdl_modifiers'),
                
                # Timestamp
                'synced_at': datetime.now(timezone.utc).isoformat(),
            }
            
            qualified_picks.append(qualified_pick)
        
        # ====================================================================
        # FINAL SORT & SLICE - ELITE 10 CAP
        # ====================================================================
        
        # Log gate statistics
        logger.info("[MLB_WAR_ZONE 2.0] Gate Statistics (Demons + High-Odds Standards):")
        logger.info(f"  Total Input: {gate_stats['total_input']}")
        logger.info(f"  Failed GOBLIN blocked: {gate_stats['fail_goblin']}")
        logger.info(f"  Failed STANDARD low odds (<+150): {gate_stats['fail_standard_low_odds']}")
        logger.info(f"  Failed Lineup (BENCHED only): {gate_stats['fail_lineup']}")
        logger.info(f"  Failed Hit Rate (no data): {gate_stats['fail_hit_rate']}")
        logger.info(f"  *** FAILED TRUE EDGE FLOOR (<10%): {gate_stats['fail_true_edge']} ***")
        logger.info(f"  QUALIFIED: {gate_stats['qualified']} (Demons: {gate_stats['demons_qualified']} | Standards: {gate_stats['standards_qualified']})")
        
        # Sort descending by Board_Score (Jackpot Ranker)
        qualified_picks.sort(key=lambda x: x.get('board_score', 0), reverse=True)
        
        # Dedupe: Keep highest board_score per player+stat
        dedupe_map = {}
        for pick in qualified_picks:
            key = f"{pick['player_name']}|{pick['stat_key']}"
            if key not in dedupe_map or pick['board_score'] > dedupe_map[key]['board_score']:
                dedupe_map[key] = pick
        
        final_picks = list(dedupe_map.values())
        final_picks.sort(key=lambda x: x.get('board_score', 0), reverse=True)
        
        # ELITE 10 CAP - Return only the Top 10 highest-scoring Jackpot plays
        top_10 = final_picks[:10]
        
        logger.info(f"[MLB_WAR_ZONE 2.0] ELITE 10 Final Result: {len(top_10)} picks (Jackpot Ranker)")
        
        for i, pick in enumerate(top_10, 1):
            logger.info(f"[MLB_WAR_ZONE 2.0]   {i}. {pick['player_name']} - {pick['stat_key']} [{pick['prop_type']}] | "
                       f"HR: {pick['true_hit_rate']}% + Market: {pick['market_prob']}% = "
                       f"PropVision: {pick['propvision_true_prob']}% vs Casino: {pick['casino_req_rate']}% | "
                       f"TRUE EDGE: +{pick['true_edge']:.1f}% | Board: {pick['board_score']}")
        
        return top_10
    
    async def analyze_all_props(self) -> List[Dict]:
        """
        Analyze all MLB props and return Safe Haven qualified picks.
        
        This is the main entry point for the Safe Haven tier builder.
        """
        return await self.build_safe_haven_tier()


# =============================================================================
# SINGLETON INSTANCE
# =============================================================================

_mlb_oracle_apex_instance = None

def get_mlb_oracle_apex_service(db, vegas_killer_model=None):
    """Get singleton instance of MLB Oracle Apex service."""
    global _mlb_oracle_apex_instance
    if _mlb_oracle_apex_instance is None:
        _mlb_oracle_apex_instance = MLBOracleApexService(db, vegas_killer_model)
    return _mlb_oracle_apex_instance
