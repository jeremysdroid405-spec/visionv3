"""
MLB Oracle Apex Service - Safe Haven Tier Logic (2026 Season)
==============================================================
The "Vegas Killer" mathematically-proven Safe Haven tier for MLB.

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
    
    Uses Vegas Killer ML predictions combined with MLB-specific statistical filters
    to identify mathematically-proven safe plays for baseball.
    """
    
    def __init__(self, db, vegas_killer_model=None):
        self.db = db
        self.vegas_killer_model = vegas_killer_model
        self.cached_board = db.mlb_cached_board
        self.live_props = db.mlb_live_props
        self.master_hub = db.mlb_master_hub_2026
        self.oracle_apex_collection = db.mlb_oracle_apex_analyzed
        
    def set_vegas_killer_model(self, model):
        """Set the Vegas Killer model reference."""
        self.vegas_killer_model = model
    
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
            # PHASE 3: THE PREDICTIVE ACTUARY GATE
            # ================================================================
            # Blend Vegas market probability with our Season-to-Date True Hit Rate
            # to create a predictive PropVision True Probability.
            
            # 3a. Calculate Market Implied Probability from DK Odds
            # Convert American odds to implied probability
            if dk_odds and dk_odds < 0:
                market_prob = (abs(dk_odds) / (abs(dk_odds) + 100)) * 100
            else:
                market_prob = 50.0  # Fallback for positive or missing odds
            
            # 3b. Calculate PropVision True Probability (50/50 blend)
            # Blend Vegas market with our internal Season-to-Date True Hit Rate
            propvision_true_prob = (market_prob * 0.50) + (true_hit_rate * 0.50)
            
            # 3c. Get the casino's required win rate based on Goblin Tax curve
            casino_req_rate = get_pp_required_win_rate(dk_odds, prop_type)
            
            # 3d. Calculate True Edge: Our blended model vs Casino's required rate
            true_edge = propvision_true_prob - casino_req_rate
            
            # THE KILL SWITCH: If our blended model cannot beat the casino tax, kill it.
            if true_edge <= 0.0:
                gate_stats['fail_actuary_gate'] += 1
                logger.debug(f"[ACTUARY_GATE] KILLED: {player_name} {stat_type} | "
                            f"Market: {market_prob:.1f}% + HR: {true_hit_rate:.1f}% = "
                            f"PropVision: {propvision_true_prob:.1f}% vs Casino Req: {casino_req_rate:.1f}% | "
                            f"Edge: {true_edge:.1f}%")
                continue
            
            # ================================================================
            # PHASE 4: QUALIFIED - Build Output
            # ================================================================
            gate_stats['qualified'] += 1
            
            # Get additional prop data
            raw_vk_pred = prop.get("vk_predicted") or prop.get("raw_vk_pred") or prop.get("season_average") or 0
            matchup_modifier = prop.get("matchup_modifier", 1.0)
            tempo_modifier = prop.get("tempo_modifier", 1.0)
            
            # Raw edge (prediction vs line)
            raw_edge = (raw_vk_pred - line) if line > 0 else 0
            
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
                
                # Consistency
                'cv': round(cv, 3) if cv else None,
                
                # PropVision internal math
                'raw_vk_pred': round(raw_vk_pred, 2) if raw_vk_pred else None,
                'vk_predicted': round(raw_vk_pred * matchup_modifier * tempo_modifier, 2) if raw_vk_pred else None,
                'vk_edge': round(raw_edge, 2),
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
        Build the MLB Front Lines tier using 2026 logic with L10 Recency Override.
        
        FRONT LINES LOGIC (2026 Season):
        ================================
        
        1. PRIMARY MARKET QUALIFICATIONS (The Filter):
           - DK Odds: Must be strictly between -145 and -239 (inclusive)
           - Prop Type: Must be STANDARD or GOBLIN (Reject DEMON)
           - Lineup Status: is_lineup_confirmed MUST be True
           - Pinnacle TP: De-vigged True Probability must be >= 58.0%
        
        2. PRE-COMPUTATION:
           - Apply Matchup Modifier for Adjusted_VK_Projection
        
        3. 3-GATE CHECK with RECENCY OVERRIDE:
           - Gate 1: Hit Rate (L20) with L10 >= 80% override
           - Gate 2: CV <= max_cv
           - Gate 3: Adjusted Edge >= min_edge
        
        4. FINAL SORT:
           - Board_Score = TP_Prob + (Raw_Edge * 10) + (Hit_Rate_Pct * 0.1)
           - Return Top 10
        
        Args:
            all_picks: List of all props from the pipeline
            
        Returns:
            List of Top 10 qualified Front Lines picks
        """
        logger.info("[MLB_FRONT_LINES] Building Front Lines tier with 2026 logic (L10 Recency Override)...")
        logger.info(f"[MLB_FRONT_LINES] Input: {len(all_picks)} props to evaluate")
        
        # ====================================================================
        # DIAGNOSTIC LOGGING - Identify where props are being filtered out
        # ====================================================================
        total_props = len(all_picks)
        fail_lineup = 0
        fail_odds = 0
        fail_type = 0
        passed_filters = 0
        
        for prop in all_picks:
            # 1. Check Lineup
            if not prop.get('is_lineup_confirmed'):
                fail_lineup += 1
                continue
            
            # 2. Check Odds (Handle potential string/int conversion safely)
            # Front Lines: -145 to -239 (inclusive)
            try:
                odds = prop.get('dk_odds')
                if odds is None:
                    odds = prop.get('all_odds', {}).get('draftkings')
                if odds is None:
                    fail_odds += 1
                    continue
                odds = int(odds)
                if not (-239 <= odds <= -145):
                    fail_odds += 1
                    continue
            except (ValueError, TypeError):
                fail_odds += 1
                continue
            
            # 3. Check Prop Type (Handle case sensitivity)
            is_goblin = prop.get('is_goblin', False)
            is_demon = prop.get('is_demon', False)
            # Front Lines accepts STANDARD or GOBLIN (reject DEMON)
            if is_demon:
                fail_type += 1
                continue
            
            passed_filters += 1
        
        logger.info(f"[DIAGNOSTICS] Total: {total_props} | Failed Lineup: {fail_lineup} | Failed Odds: {fail_odds} | Failed Type: {fail_type} | Passed to Math: {passed_filters}")
        
        # ====================================================================
        # BDL SPLITS DATA ALREADY PREFETCHED BY mlb_tier_service.py
        # ====================================================================
        
        # ====================================================================
        # FRONT LINES THRESHOLD DICTIONARY (Raw Decimal Cushion Edges)
        # More relaxed than Safe Haven - allows mid-juice plays
        # ====================================================================
        thresholds = {
            "HITS": {"max_cv": 0.85, "min_l20": 13, "min_edge": 0.20},
            "TB": {"max_cv": 0.95, "min_l20": 12, "min_edge": 0.30},
            "K": {"max_cv": 0.60, "min_l20": 13, "min_edge": 0.80},
            "OUTS": {"max_cv": 0.50, "min_l20": 14, "min_edge": 1.00},
            "HRR": {"max_cv": 0.75, "min_l20": 13, "min_edge": 0.30},
            # Additional stats
            "RBIS": {"max_cv": 0.95, "min_l20": 11, "min_edge": 0.20},
            "RUNS": {"max_cv": 1.00, "min_l20": 10, "min_edge": 0.20},
            "SINGLES": {"max_cv": 0.80, "min_l20": 12, "min_edge": 0.20},
            "DOUBLES": {"max_cv": 1.10, "min_l20": 9, "min_edge": 0.15},
            "HR": {"max_cv": 1.40, "min_l20": 7, "min_edge": 0.10},
            "SB": {"max_cv": 1.20, "min_l20": 8, "min_edge": 0.15},
            "BB": {"max_cv": 1.00, "min_l20": 10, "min_edge": 0.20},
            "BATTER_K": {"max_cv": 0.85, "min_l20": 11, "min_edge": 0.25},
            "HITS_ALLOWED": {"max_cv": 0.80, "min_l20": 11, "min_edge": 0.40},
            "ER": {"max_cv": 0.95, "min_l20": 10, "min_edge": 0.30},
            "WALKS": {"max_cv": 1.00, "min_l20": 9, "min_edge": 0.25},
        }
        
        # Stat type aliases mapping to threshold keys
        stat_aliases = {
            "HITS": "HITS",
            "TOTAL BASES": "TB", "TB": "TB", "TOTAL_BASES": "TB",
            "PITCHER STRIKEOUTS": "K", "K": "K", "PITCHER_STRIKEOUTS": "K", "STRIKEOUTS": "K",
            "PITCHING OUTS": "OUTS", "OUTS": "OUTS", "PITCHING_OUTS": "OUTS", "OUTS RECORDED": "OUTS",
            "PITCHER OUTS": "OUTS", "PITCHER_OUTS": "OUTS",
            "HRR": "HRR", "HITS+RUNS+RBIS": "HRR", "HITS_RUNS_RBIS": "HRR",
            "RBIS": "RBIS", "RBI": "RBIS",
            "RUNS": "RUNS", "RUNS SCORED": "RUNS",
            "SINGLES": "SINGLES",
            "DOUBLES": "DOUBLES",
            "HOME RUNS": "HR", "HR": "HR", "HOME_RUNS": "HR",
            "STOLEN BASES": "SB", "SB": "SB", "STOLEN_BASES": "SB",
            "BATTER WALKS": "BB", "BB": "BB", "BATTER_WALKS": "BB", "WALKS": "BB",
            "BATTER STRIKEOUTS": "BATTER_K", "BATTER_STRIKEOUTS": "BATTER_K",
            "HITS ALLOWED": "HITS_ALLOWED", "HITS_ALLOWED": "HITS_ALLOWED",
            "EARNED RUNS": "ER", "ER": "ER", "EARNED_RUNS": "ER",
            "WALKS ALLOWED": "WALKS", "WALKS_ALLOWED": "WALKS",
        }
        
        # DK Odds range for Front Lines
        DK_MIN = -239  # More negative = more juiced (inclusive)
        DK_MAX = -145  # Less negative = less juiced (inclusive)
        
        # Minimum Pinnacle True Probability
        MIN_PINNACLE_TP = 58.0
        
        # Track gate statistics
        gate_stats = {
            'total_input': len(all_picks),
            'dk_odds_fail': 0,
            'demon_rejected': 0,
            'lineup_not_confirmed': 0,
            'pinnacle_tp_fail': 0,
            'unsupported_stat': 0,
            'gate1_fail': 0,
            'gate1_recency_override': 0,  # Track L10 overrides
            'gate2_fail': 0,
            'gate3_fail': 0,
            'qualified': 0,
        }
        
        qualified_picks = []
        
        for prop in all_picks:
            # ================================================================
            # 1. PRIMARY MARKET QUALIFICATIONS (The Filter)
            # ================================================================
            
            # Get stat type and normalize
            raw_stat = (prop.get("stat_type") or "").upper().replace(" ", "_")
            stat_key = stat_aliases.get(raw_stat.replace("_", " "), stat_aliases.get(raw_stat))
            
            # Skip unsupported stats
            if not stat_key or stat_key not in thresholds:
                gate_stats['unsupported_stat'] += 1
                continue
            
            cfg = thresholds[stat_key]
            
            # 1a. DK Odds: Must be strictly between -145 and -239 (inclusive)
            dk_odds = prop.get("dk_odds")
            if dk_odds is None:
                dk_odds = prop.get("all_odds", {}).get("draftkings")
            
            if dk_odds is None:
                gate_stats['dk_odds_fail'] += 1
                continue
            
            # Front Lines range: -239 to -145 (inclusive on both ends)
            if dk_odds < DK_MIN or dk_odds > DK_MAX:
                gate_stats['dk_odds_fail'] += 1
                continue
            
            # 1b. Prop Type: Must be STANDARD or GOBLIN (Reject DEMON)
            is_demon = prop.get("is_demon", False)
            if is_demon:
                gate_stats['demon_rejected'] += 1
                continue
            
            # 1c. Lineup Status: is_lineup_confirmed MUST be True
            is_lineup_confirmed = prop.get("is_lineup_confirmed")
            if is_lineup_confirmed is False:
                gate_stats['lineup_not_confirmed'] += 1
                continue
            
            # 1d. Pinnacle TP: De-vigged True Probability must be >= 58.0%
            pinnacle_tp = prop.get("pinnacle_tp") or prop.get("vk_prob_over") or prop.get("vk_probability")
            if pinnacle_tp is None or pinnacle_tp < MIN_PINNACLE_TP:
                gate_stats['pinnacle_tp_fail'] += 1
                continue
            
            # ================================================================
            # 2. PRE-COMPUTATION (Matchup Modifier + Tempo Modifier)
            # ================================================================
            # USES BDL MLB API (BallDontLie) for precise modifiers:
            # - L/R Splits: OPS > .850 → +5% matchup boost
            # - Batter vs Pitcher: OPS > .900 (10+ ABs) → +5% matchup boost  
            # - Batting Order: Position 1-9 → tempo adjustment (0.90x to 1.10x)
            # ================================================================
            
            # Get raw VK projection
            raw_vk_pred = prop.get("vk_predicted") or prop.get("raw_vk_pred") or prop.get("season_average")
            if not raw_vk_pred or raw_vk_pred <= 0:
                continue
            
            # Get opponent (needed for both pitcher and hitter paths)
            opponent = prop.get("opponent") or prop.get("opponent_abbr")
            
            # Determine if this is a pitcher stat (uses different tempo logic)
            is_pitcher_stat = stat_key in ["K", "OUTS", "ER", "PITCHER STRIKEOUTS", "PITCHING OUTS"]
            
            if is_pitcher_stat:
                # Pitcher stats: use legacy matchup + pitcher tempo
                matchup_modifier = self._get_matchup_modifier(raw_stat, opponent) if opponent else 1.0
                pitcher_ppa = prop.get("pitcher_ppa") or prop.get("pitches_per_pa")
                bullpen_rest = prop.get("bullpen_rest_days")
                tempo_modifier = calculate_pitcher_tempo(pitcher_ppa, bullpen_rest)
                bdl_details = None
            else:
                # Hitter stats: use cached BDL data (NO API CALLS)
                matchup_modifier, tempo_modifier, bdl_details = self._get_bdl_modifiers_from_cache(
                    prop=prop,
                    stat_key=stat_key
                )
                
                # Store BDL details in prop for Vision Intel Suite
                if bdl_details:
                    prop['bdl_modifiers'] = bdl_details
            
            # Chain the modifiers: Raw * Matchup * Tempo = Final Adjusted
            adjusted_vk_pred = raw_vk_pred * matchup_modifier * tempo_modifier
            
            # Get prop line
            line = prop.get("line", 0)
            if line <= 0:
                continue
            
            # ================================================================
            # 3. THE 3-GATE CHECK with RECENCY OVERRIDE
            # ================================================================
            
            # Get L20 and L10 hit data
            l20_hits = prop.get("l20_hits")
            l10_hits = prop.get("l10_hits")
            cv = prop.get("cv") or prop.get("vk_cv")
            games_played = prop.get("games_played", 10)
            
            # NORMALIZE CV: Convert percentage (69.92) to decimal (0.6992)
            # Thresholds are in decimal form (0.70 = 70%)
            if cv is not None and cv > 1:
                cv = cv / 100.0
            
            # If L20 hits not directly available, calculate from hit rate
            # MLB boards primarily use hit_rate_l10, so fall back to that
            if l20_hits is None:
                h20_rate = prop.get("h20_rate") or prop.get("hit_rate_l20") or prop.get("hit_rate_l10")
                if h20_rate is not None:
                    l20_hits = int((h20_rate / 100) * min(games_played, 20))
                else:
                    gate_stats['gate1_fail'] += 1
                    continue
            
            # If L10 hits not directly available, calculate from hit rate
            if l10_hits is None:
                h10_rate = prop.get("h10_rate") or prop.get("hit_rate_l10")
                if h10_rate is not None:
                    l10_hits = int((h10_rate / 100) * min(games_played, 10))
                else:
                    l10_hits = 0  # Default to 0 if no L10 data
            
            # ----------------------------------------------------------------
            # GATE 1: Hit Rate (L20) with L10 RECENCY OVERRIDE
            # ----------------------------------------------------------------
            # Primary check: L20 >= min_l20 (scaled for games played)
            # CRITICAL RECENCY EXCEPTION: If L20 fails, check L10.
            # If L10 hit rate >= 8/10 (80%), override the failure and PASS Gate 1.
            # ----------------------------------------------------------------
            required_l20 = int(cfg["min_l20"] * min(games_played, 20) / 20)
            passes_gate1 = l20_hits >= required_l20
            used_recency_override = False
            
            if not passes_gate1:
                # Check L10 Recency Override: >= 8/10 (80%)
                if l10_hits >= 8:
                    passes_gate1 = True
                    used_recency_override = True
                    gate_stats['gate1_recency_override'] += 1
                    logger.debug(f"[MLB_FRONT_LINES] RECENCY_OVERRIDE: {prop.get('player_name')} - "
                                f"{stat_key} | L20: {l20_hits}/20 FAILED but L10: {l10_hits}/10 PASSED")
            
            if not passes_gate1:
                gate_stats['gate1_fail'] += 1
                continue
            
            # ----------------------------------------------------------------
            # GATE 2: Consistency (CV)
            # CV must be <= max_cv
            # ----------------------------------------------------------------
            if cv is None or cv > cfg["max_cv"]:
                gate_stats['gate2_fail'] += 1
                continue
            
            # ----------------------------------------------------------------
            # GATE 3: Adjusted Edge
            # (Adjusted_VK_Projection - Prop_Line) >= min_edge
            # ----------------------------------------------------------------
            raw_edge = adjusted_vk_pred - line
            if raw_edge < cfg["min_edge"]:
                gate_stats['gate3_fail'] += 1
                continue
            
            # ================================================================
            # QUALIFIED - Build output pick
            # ================================================================
            gate_stats['qualified'] += 1
            
            # Calculate hit rate percentages for board score
            h20_rate_pct = (l20_hits / 20) * 100
            h10_rate_pct = (l10_hits / 10) * 100 if l10_hits else 0
            
            # Use the better of L20 or L10 for board score if recency override was used
            effective_hit_rate = max(h20_rate_pct, h10_rate_pct) if used_recency_override else h20_rate_pct
            
            # Calculate Board_Score
            # Formula: TP_Prob + (Raw_Edge * 10) + (Hit_Rate_Pct * 0.1)
            board_score = pinnacle_tp + (raw_edge * 10) + (effective_hit_rate * 0.1)
            
            # Build qualified pick with all required fields
            player_name = prop.get('player_name')
            is_goblin = prop.get("is_goblin", False)
            is_standard = not is_goblin and not is_demon
            
            qualified_pick = {
                # Player info
                'player_name': player_name,
                'team': prop.get('team'),
                'opponent': opponent,
                'photo_url': prop.get('photo_url') or prop.get('headshot_url'),
                'headshot_url': prop.get('headshot_url'),
                'game_time': prop.get('game_time') or prop.get('commence_time'),
                
                # Prop details
                'stat_type': raw_stat.replace("_", " ").title(),
                'stat_key': stat_key,  # Normalized key (HITS, TB, K, OUTS, HRR)
                'line': line,
                'dk_odds': dk_odds,
                
                # Classification
                'is_goblin': is_goblin,
                'is_demon': False,  # Demons are rejected
                'is_standard': is_standard,
                'is_lineup_confirmed': True,
                
                # Averages (carry forward from input)
                'l5_avg': prop.get('l5_avg'),
                'l10_avg': prop.get('l10_avg'),
                'l20_avg': prop.get('l20_avg'),
                'season_avg': prop.get('season_avg') or prop.get('l10_avg'),
                
                # Hit rates
                'h5_rate': prop.get('h5_rate') or prop.get('hit_rate_l5'),
                'h10_rate': round(h10_rate_pct, 1),
                'h20_rate': round(h20_rate_pct, 1),
                'hit_rate_l5': prop.get('h5_rate') or prop.get('hit_rate_l5'),
                'hit_rate_l10': round(h10_rate_pct, 1),
                'hit_rate_l20': round(h20_rate_pct, 1),
                'l10_hits': l10_hits,
                'l20_hits': l20_hits,
                
                # Recency Override flag
                'used_recency_override': used_recency_override,
                'recency_override_reason': f"L10 {l10_hits}/10 >= 80%" if used_recency_override else None,
                
                # Consistency
                'cv': round(cv, 3) if cv else None,
                
                # VK predictions with tempo
                'raw_vk_pred': round(raw_vk_pred, 2),
                'matchup_modifier': round(matchup_modifier, 3),
                'tempo_modifier': round(tempo_modifier, 3),
                'vk_predicted': round(adjusted_vk_pred, 2),  # Adjusted projection (Raw * Matchup * Tempo)
                'vk_edge': round(raw_edge, 2),  # Raw cushion (adjusted_pred - line)
                'vk_prob_over': round(pinnacle_tp, 1),
                'vk_probability': round(pinnacle_tp, 1),
                'pinnacle_tp': round(pinnacle_tp, 1),
                
                # Board score
                'board_score': round(board_score, 1),
                
                # Gate thresholds met (for debugging)
                'gate_thresholds': {
                    'min_l20_required': cfg["min_l20"],
                    'max_cv_allowed': cfg["max_cv"],
                    'min_edge_required': cfg["min_edge"],
                    'dk_range': f"{DK_MIN} to {DK_MAX}",
                    'min_pinnacle_tp': MIN_PINNACLE_TP,
                },
                
                # Tier classification
                'tier': 'front_lines',
                'tier_label': 'MLB Front Lines',
                'front_lines_qualified': True,
                
                # INTEL SUITE: Tempo breakdown for Vision Intel Suite
                'intel_suite': {
                    'tempo': self._build_tempo_intel_suite(prop, stat_key, tempo_modifier),
                    'pace_delta': self._build_tempo_intel_suite(prop, stat_key, tempo_modifier),  # Legacy alias
                    'stability_index': self._build_stability_index(cv),
                },
                
                # Active badges for UI display
                'active_badges': self._build_front_lines_badges(prop, stat_key, h20_rate, cv, used_recency_override),
                
                # Carry forward any vision intel
                'vision_intel': prop.get('vision_intel'),
                'intel_score': prop.get('intel_score'),
                'intel_verdict': prop.get('intel_verdict'),
                
                # BDL API Modifier Details (L/R splits, BVP, batting order)
                'bdl_modifiers': prop.get('bdl_modifiers'),
                
                # Timestamp
                'synced_at': datetime.now(timezone.utc).isoformat(),
            }
            
            qualified_picks.append(qualified_pick)
        
        # ====================================================================
        # 4. FINAL SORT & SLICE
        # ====================================================================
        
        # Log gate statistics
        logger.info("[MLB_FRONT_LINES] Gate Statistics:")
        logger.info(f"  Total Input: {gate_stats['total_input']}")
        logger.info(f"  DK Odds Fail (outside -239 to -145): {gate_stats['dk_odds_fail']}")
        logger.info(f"  Demon Rejected: {gate_stats['demon_rejected']}")
        logger.info(f"  Lineup Not Confirmed: {gate_stats['lineup_not_confirmed']}")
        logger.info(f"  Pinnacle TP Fail (< 58%): {gate_stats['pinnacle_tp_fail']}")
        logger.info(f"  Unsupported Stat: {gate_stats['unsupported_stat']}")
        logger.info(f"  Gate 1 Fail (Hit Rate): {gate_stats['gate1_fail']}")
        logger.info(f"  Gate 1 Recency Overrides (L10 >= 80%): {gate_stats['gate1_recency_override']}")
        logger.info(f"  Gate 2 Fail (CV): {gate_stats['gate2_fail']}")
        logger.info(f"  Gate 3 Fail (Edge): {gate_stats['gate3_fail']}")
        logger.info(f"  QUALIFIED: {gate_stats['qualified']}")
        
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
        
        logger.info(f"[MLB_FRONT_LINES] Final Result: {len(top_10)} picks (from {len(final_picks)} qualified)")
        
        for i, pick in enumerate(top_10[:5], 1):
            override_flag = " [L10 OVERRIDE]" if pick.get('used_recency_override') else ""
            logger.info(f"[MLB_FRONT_LINES]   {i}. {pick['player_name']} - {pick['stat_key']} @ {pick['line']} | "
                       f"Board: {pick['board_score']} | Edge: +{pick['vk_edge']:.2f} | "
                       f"L20: {pick['l20_hits']}/20 | L10: {pick['l10_hits']}/10 | CV: {pick['cv']:.2f}{override_flag}")
        
        return top_10
    
    async def build_war_zone_tier(self, all_picks: List[Dict]) -> List[Dict]:
        """
        Build the MLB War Zone tier using 2026 logic with L15 Ceiling Check and CV Fast-Track.
        
        WAR ZONE LOGIC (2026 Season):
        ==============================
        
        The War Zone hunts for MASSIVE PAYOUTS. We throw out "safety" entirely.
        We want boom-or-bust players - guys who might strike out 3 times, but
        when they connect, it's a 450-foot home run.
        
        KEY DIFFERENCE: We REWARD high CV (volatility) here - opposite of Safe Haven.
        
        1. PRIMARY MARKET QUALIFICATIONS (The Filter):
           - DK Odds: >= +150 OR is_demon == True
           - Prop Type: DEMON or High-Yield STANDARD (reject GOBLIN)
           - Lineup Status: is_lineup_confirmed MUST be True
           - Pinnacle TP: < 45.0% (underdog plays are acceptable)
        
        2. PRE-COMPUTATION:
           - Apply Matchup Modifier for Adjusted_VK_Projection
        
        3. 3-GATE CHECK with CEILING EXCEPTION & VOLATILITY FAST-TRACK:
           - Gate 1: Hit Rate (L20) + L15 CEILING CHECK (must have cleared line 2x in L15)
           - Gate 2: CV check with VOLATILITY FAST-TRACK (CV > 1.0 = auto-pass)
           - Gate 3: Adjusted Edge >= min_edge (must project to SMASH the line)
        
        4. FINAL SORT:
           - Board_Score weights raw edge heavier than TP
           - Return Top 10
        
        Args:
            all_picks: List of all props from the pipeline
            
        Returns:
            List of Top 10 qualified War Zone picks
        """
        logger.info("[MLB_WAR_ZONE] Building War Zone tier with 2026 logic (L15 Ceiling + CV Fast-Track)...")
        logger.info(f"[MLB_WAR_ZONE] Input: {len(all_picks)} props to evaluate")
        
        # ====================================================================
        # BDL SPLITS DATA ALREADY PREFETCHED BY mlb_tier_service.py
        # ====================================================================
        
        # ====================================================================
        # WAR ZONE THRESHOLD DICTIONARY (Raw Decimal Cushion Edges)
        # HIGH CV is allowed/rewarded here - we want VOLATILE players
        # ====================================================================
        thresholds = {
            "HITS": {"max_cv": 1.10, "min_l20": 8, "min_edge": 0.40},
            "TB": {"max_cv": 1.25, "min_l20": 7, "min_edge": 0.60},
            "K": {"max_cv": 0.85, "min_l20": 10, "min_edge": 1.50},
            "OUTS": {"max_cv": 0.70, "min_l20": 12, "min_edge": 2.00},
            "HRR": {"max_cv": 1.00, "min_l20": 9, "min_edge": 0.60},
            # Additional stats (War Zone = high ceiling plays)
            "RBIS": {"max_cv": 1.20, "min_l20": 6, "min_edge": 0.35},
            "RUNS": {"max_cv": 1.30, "min_l20": 5, "min_edge": 0.35},
            "SINGLES": {"max_cv": 1.00, "min_l20": 7, "min_edge": 0.35},
            "DOUBLES": {"max_cv": 1.40, "min_l20": 5, "min_edge": 0.25},
            "HR": {"max_cv": 1.80, "min_l20": 4, "min_edge": 0.20},
            "SB": {"max_cv": 1.50, "min_l20": 5, "min_edge": 0.25},
            "BB": {"max_cv": 1.25, "min_l20": 6, "min_edge": 0.30},
            "BATTER_K": {"max_cv": 1.00, "min_l20": 7, "min_edge": 0.40},
            "HITS_ALLOWED": {"max_cv": 1.00, "min_l20": 7, "min_edge": 0.60},
            "ER": {"max_cv": 1.20, "min_l20": 6, "min_edge": 0.50},
            "WALKS": {"max_cv": 1.30, "min_l20": 5, "min_edge": 0.40},
        }
        
        # Stat type aliases mapping to threshold keys
        stat_aliases = {
            "HITS": "HITS",
            "TOTAL BASES": "TB", "TB": "TB", "TOTAL_BASES": "TB",
            "PITCHER STRIKEOUTS": "K", "K": "K", "PITCHER_STRIKEOUTS": "K", "STRIKEOUTS": "K",
            "PITCHING OUTS": "OUTS", "OUTS": "OUTS", "PITCHING_OUTS": "OUTS", "OUTS RECORDED": "OUTS",
            "PITCHER OUTS": "OUTS", "PITCHER_OUTS": "OUTS",
            "HRR": "HRR", "HITS+RUNS+RBIS": "HRR", "HITS_RUNS_RBIS": "HRR",
            "RBIS": "RBIS", "RBI": "RBIS",
            "RUNS": "RUNS", "RUNS SCORED": "RUNS",
            "SINGLES": "SINGLES",
            "DOUBLES": "DOUBLES",
            "HOME RUNS": "HR", "HR": "HR", "HOME_RUNS": "HR",
            "STOLEN BASES": "SB", "SB": "SB", "STOLEN_BASES": "SB",
            "BATTER WALKS": "BB", "BB": "BB", "BATTER_WALKS": "BB", "WALKS": "BB",
            "BATTER STRIKEOUTS": "BATTER_K", "BATTER_STRIKEOUTS": "BATTER_K",
            "HITS ALLOWED": "HITS_ALLOWED", "HITS_ALLOWED": "HITS_ALLOWED",
            "EARNED RUNS": "ER", "ER": "ER", "EARNED_RUNS": "ER",
            "WALKS ALLOWED": "WALKS", "WALKS_ALLOWED": "WALKS",
        }
        
        # DK Odds minimum for War Zone (or is_demon)
        DK_MIN_WAR_ZONE = 150  # +150 or higher
        
        # Maximum Pinnacle True Probability (underdog plays)
        MAX_PINNACLE_TP = 45.0
        
        # Volatility threshold for CV Fast-Track
        VOLATILITY_THRESHOLD = 1.0
        
        # L15 Ceiling requirement (must clear line at least 2x in L15)
        L15_CEILING_MIN = 2
        
        # Track gate statistics
        gate_stats = {
            'total_input': len(all_picks),
            'dk_odds_fail': 0,
            'goblin_rejected': 0,  # War Zone rejects GOBLINs
            'lineup_not_confirmed': 0,
            'pinnacle_tp_fail': 0,
            'unsupported_stat': 0,
            'gate1_fail': 0,
            'gate1_ceiling_fail': 0,  # L15 ceiling check failures
            'gate1_hr_power_bypass': 0,  # HR props bypassed via L10 HRs or ISO
            'gate2_fail': 0,
            'gate2_volatility_fasttrack': 0,  # CV > 1.0 fast-tracks
            'gate3_fail': 0,
            'qualified': 0,
        }
        
        qualified_picks = []
        
        for prop in all_picks:
            # ================================================================
            # 1. PRIMARY MARKET QUALIFICATIONS (The Filter)
            # ================================================================
            
            # Get stat type and normalize
            raw_stat = (prop.get("stat_type") or "").upper().replace(" ", "_")
            stat_key = stat_aliases.get(raw_stat.replace("_", " "), stat_aliases.get(raw_stat))
            
            # Skip unsupported stats
            if not stat_key or stat_key not in thresholds:
                gate_stats['unsupported_stat'] += 1
                continue
            
            cfg = thresholds[stat_key]
            
            # 1a. DK Odds: >= +150 OR is_demon == True
            dk_odds = prop.get("dk_odds")
            if dk_odds is None:
                dk_odds = prop.get("all_odds", {}).get("draftkings")
            
            is_demon = prop.get("is_demon", False)
            is_goblin = prop.get("is_goblin", False)
            
            # Must be DEMON or have high DK odds (+150 or higher)
            if not is_demon and (dk_odds is None or dk_odds < DK_MIN_WAR_ZONE):
                gate_stats['dk_odds_fail'] += 1
                continue
            
            # 1b. Prop Type: DEMON or High-Yield STANDARD (Strictly reject GOBLIN)
            if is_goblin:
                gate_stats['goblin_rejected'] += 1
                continue
            
            # 1c. Lineup Status: is_lineup_confirmed MUST be True
            is_lineup_confirmed = prop.get("is_lineup_confirmed")
            if is_lineup_confirmed is False:
                gate_stats['lineup_not_confirmed'] += 1
                continue
            
            # 1d. Pinnacle TP: < 45.0% (underdog plays are acceptable)
            pinnacle_tp = prop.get("pinnacle_tp") or prop.get("vk_prob_over") or prop.get("vk_probability")
            # For War Zone, we accept lower TP - these are high-risk plays
            # If TP is missing or high (> 45%), we still allow demons through
            if pinnacle_tp is not None and pinnacle_tp > MAX_PINNACLE_TP and not is_demon:
                gate_stats['pinnacle_tp_fail'] += 1
                continue
            
            # Default to 35% if no TP available (typical for demon lines)
            if pinnacle_tp is None:
                pinnacle_tp = 35.0
            
            # ================================================================
            # 2. PRE-COMPUTATION (Matchup Modifier + Tempo Modifier)
            # ================================================================
            # USES BDL MLB API (BallDontLie) for precise modifiers:
            # - L/R Splits: OPS > .850 → +5% matchup boost
            # - Batter vs Pitcher: OPS > .900 (10+ ABs) → +5% matchup boost  
            # - Batting Order: Position 1-9 → tempo adjustment (0.90x to 1.10x)
            # ================================================================
            
            # Get raw VK projection
            raw_vk_pred = prop.get("vk_predicted") or prop.get("raw_vk_pred") or prop.get("season_average")
            if not raw_vk_pred or raw_vk_pred <= 0:
                continue
            
            # Get opponent (needed for both pitcher and hitter paths)
            opponent = prop.get("opponent") or prop.get("opponent_abbr")
            
            # Determine if this is a pitcher stat (uses different tempo logic)
            is_pitcher_stat = stat_key in ["K", "OUTS", "ER", "PITCHER STRIKEOUTS", "PITCHING OUTS"]
            
            if is_pitcher_stat:
                # Pitcher stats: use legacy matchup + pitcher tempo
                matchup_modifier = self._get_matchup_modifier(raw_stat, opponent) if opponent else 1.0
                pitcher_ppa = prop.get("pitcher_ppa") or prop.get("pitches_per_pa")
                bullpen_rest = prop.get("bullpen_rest_days")
                tempo_modifier = calculate_pitcher_tempo(pitcher_ppa, bullpen_rest)
                bdl_details = None
            else:
                # Hitter stats: use cached BDL data (NO API CALLS)
                matchup_modifier, tempo_modifier, bdl_details = self._get_bdl_modifiers_from_cache(
                    prop=prop,
                    stat_key=stat_key
                )
                
                # Store BDL details in prop for Vision Intel Suite
                if bdl_details:
                    prop['bdl_modifiers'] = bdl_details
            
            # Chain the modifiers: Raw * Matchup * Tempo = Final Adjusted
            adjusted_vk_pred = raw_vk_pred * matchup_modifier * tempo_modifier
            
            # Get prop line
            line = prop.get("line", 0)
            if line <= 0:
                continue
            
            # ================================================================
            # 3. THE 3-GATE CHECK with CEILING EXCEPTION & VOLATILITY FAST-TRACK
            # ================================================================
            
            # Get L20, L15, and CV data
            l20_hits = prop.get("l20_hits")
            l15_ceiling_hits = prop.get("l15_ceiling_hits")  # Times cleared THIS specific line in L15
            cv = prop.get("cv") or prop.get("vk_cv")
            games_played = prop.get("games_played", 10)
            
            # NORMALIZE CV: Convert percentage (69.92) to decimal (0.6992)
            # Thresholds are in decimal form (0.70 = 70%)
            if cv is not None and cv > 1:
                cv = cv / 100.0
            
            # If L20 hits not directly available, calculate from hit rate
            # MLB boards primarily use hit_rate_l10, so fall back to that
            if l20_hits is None:
                h20_rate = prop.get("h20_rate") or prop.get("hit_rate_l20") or prop.get("hit_rate_l10")
                if h20_rate is not None:
                    l20_hits = int((h20_rate / 100) * min(games_played, 20))
                else:
                    gate_stats['gate1_fail'] += 1
                    continue
            
            # If L15 ceiling hits not available, estimate from L15 values or L10 hit rate
            if l15_ceiling_hits is None:
                l15_values = prop.get("l15_values") or prop.get("last_15_values") or []
                if l15_values:
                    # Count how many times they cleared the line in L15
                    l15_ceiling_hits = sum(1 for v in l15_values if v >= line)
                else:
                    # Fallback: estimate from L10 hit rate if available
                    h10_rate = prop.get("h10_rate") or prop.get("hit_rate_l10")
                    if h10_rate is not None:
                        # Estimate ceiling hits proportionally
                        l15_ceiling_hits = int((h10_rate / 100) * min(games_played, 15) * 0.6)  # Conservative
                    else:
                        l15_ceiling_hits = 0
            
            # ----------------------------------------------------------------
            # GATE 1: Hit Rate (L20) + L15 CEILING CHECK
            # ----------------------------------------------------------------
            # Primary: L20 >= min_l20 (scaled for games played)
            # CRITICAL CEILING EXCEPTION: Player MUST have cleared this specific
            # Demon/High line at least TWICE in their last 15 games.
            # We want DEMONSTRATED SPIKES, not just average hits.
            #
            # HR POWER BYPASS (2026): If stat_type == 'HR' and fails Gate 1,
            # run secondary power check:
            #   - If L10 HRs >= 2 OR ISO > .200 (vs pitcher handedness)
            #   - Force PASS Gate 1 (power hitters with low hit rates still qualify)
            # ----------------------------------------------------------------
            required_l20 = int(cfg["min_l20"] * min(games_played, 20) / 20)
            passes_gate1_base = l20_hits >= required_l20
            passes_gate1_ceiling = l15_ceiling_hits >= L15_CEILING_MIN
            used_hr_power_bypass = False
            hr_bypass_reason = None
            
            # Check if this is an HR prop for the bypass rule
            is_hr_prop = stat_key == "HR"
            
            if not passes_gate1_base or not passes_gate1_ceiling:
                # ============================================================
                # HR POWER BYPASS - Check for power hitters who fail hit rate
                # ============================================================
                if is_hr_prop:
                    # Extract L10 HRs from game_logs (count HRs in last 10 games)
                    game_logs = prop.get("game_logs", []) or prop.get("bdl_game_logs", []) or []
                    l10_game_logs = game_logs[:10] if game_logs else []
                    l10_hrs = sum(g.get("home_runs", 0) or 0 for g in l10_game_logs)
                    
                    # Extract ISO (Isolated Power) vs pitcher handedness
                    # ISO = SLG - AVG (measures raw power)
                    # Check splits data if available
                    splits = prop.get("splits", {}) or {}
                    pitcher_hand = prop.get("pitcher_hand", "").upper() or prop.get("opposing_pitcher_hand", "").upper()
                    
                    # Default ISO from overall stats if splits not available
                    iso = 0.0
                    if pitcher_hand == "R":
                        vs_splits = splits.get("vs_rhp", {}) or splits.get("vs_right", {}) or {}
                        iso = vs_splits.get("iso", 0.0) or vs_splits.get("ISO", 0.0)
                    elif pitcher_hand == "L":
                        vs_splits = splits.get("vs_lhp", {}) or splits.get("vs_left", {}) or {}
                        iso = vs_splits.get("iso", 0.0) or vs_splits.get("ISO", 0.0)
                    
                    # Fallback: calculate ISO from prop averages if available
                    if iso == 0.0:
                        slg = prop.get("slg", 0.0) or prop.get("slugging", 0.0) or 0.0
                        avg = prop.get("avg", 0.0) or prop.get("batting_avg", 0.0) or 0.0
                        if slg > 0 and avg > 0:
                            iso = slg - avg
                    
                    # HR POWER BYPASS CONDITIONS:
                    # 1. L10 HRs >= 2 (hit 2+ dingers in last 10 games)
                    # 2. ISO > .200 (strong power profile vs pitcher handedness)
                    if l10_hrs >= 2:
                        passes_gate1_base = True
                        passes_gate1_ceiling = True
                        used_hr_power_bypass = True
                        hr_bypass_reason = f"L10_HRS={l10_hrs}>=2"
                        gate_stats['gate1_hr_power_bypass'] += 1
                        logger.info(f"[MLB_WAR_ZONE] HR_POWER_BYPASS: {prop.get('player_name')} - "
                                   f"HR @ {line} | L10 HRs: {l10_hrs} >= 2 | FORCING GATE1 PASS")
                    elif iso > 0.200:
                        passes_gate1_base = True
                        passes_gate1_ceiling = True
                        used_hr_power_bypass = True
                        hr_bypass_reason = f"ISO={iso:.3f}>.200"
                        gate_stats['gate1_hr_power_bypass'] += 1
                        logger.info(f"[MLB_WAR_ZONE] HR_POWER_BYPASS: {prop.get('player_name')} - "
                                   f"HR @ {line} | ISO: {iso:.3f} > .200 vs {pitcher_hand or 'N/A'} | FORCING GATE1 PASS")
            
            if not passes_gate1_base:
                gate_stats['gate1_fail'] += 1
                continue
            
            if not passes_gate1_ceiling:
                gate_stats['gate1_ceiling_fail'] += 1
                continue
            
            # ----------------------------------------------------------------
            # GATE 2: Consistency/Volatility Check with CV FAST-TRACK
            # ----------------------------------------------------------------
            # Normally: CV must be <= max_cv
            # VOLATILITY FAST-TRACK: If CV > 1.0, do NOT reject.
            # Fast-track to Gate 3 - we ACTIVELY WANT boom/bust profiles!
            # ----------------------------------------------------------------
            used_volatility_fasttrack = False
            
            if cv is None:
                gate_stats['gate2_fail'] += 1
                continue
            
            if cv > VOLATILITY_THRESHOLD:
                # CV > 1.0 = FAST-TRACK! High volatility is DESIRED in War Zone
                used_volatility_fasttrack = True
                gate_stats['gate2_volatility_fasttrack'] += 1
                logger.debug(f"[MLB_WAR_ZONE] VOLATILITY_FASTTRACK: {prop.get('player_name')} - "
                            f"{stat_key} | CV: {cv:.2f} > 1.0 = Boom/Bust Profile!")
            elif cv > cfg["max_cv"]:
                # CV is between max_cv and 1.0 - reject
                gate_stats['gate2_fail'] += 1
                continue
            
            # ----------------------------------------------------------------
            # GATE 3: Adjusted Edge
            # (Adjusted_VK_Projection - Prop_Line) >= min_edge
            # They must project to SMASH the line - big cushion required
            # ----------------------------------------------------------------
            raw_edge = adjusted_vk_pred - line
            if raw_edge < cfg["min_edge"]:
                gate_stats['gate3_fail'] += 1
                continue
            
            # ================================================================
            # QUALIFIED - Build output pick
            # ================================================================
            gate_stats['qualified'] += 1
            
            # Calculate hit rate percentages
            h20_rate_pct = (l20_hits / 20) * 100
            h15_ceiling_pct = (l15_ceiling_hits / 15) * 100 if l15_ceiling_hits else 0
            
            # Calculate Board_Score (WEIGHT EDGE HEAVIER than TP for War Zone)
            # Formula: (Raw_Edge * 15) + TP + (CV_Bonus)
            # The raw edge matters MORE here - we want players who project to crush the line
            cv_bonus = 5.0 if used_volatility_fasttrack else 0.0  # Bonus for high volatility
            board_score = (raw_edge * 15) + pinnacle_tp + cv_bonus
            
            # Build qualified pick with all required fields
            player_name = prop.get('player_name')
            
            qualified_pick = {
                # Player info
                'player_name': player_name,
                'team': prop.get('team'),
                'opponent': opponent,
                'photo_url': prop.get('photo_url') or prop.get('headshot_url'),
                'headshot_url': prop.get('headshot_url'),
                'game_time': prop.get('game_time') or prop.get('commence_time'),
                
                # Prop details
                'stat_type': raw_stat.replace("_", " ").title(),
                'stat_key': stat_key,  # Normalized key (HITS, TB, K, OUTS, HRR)
                'line': line,
                'dk_odds': dk_odds,
                
                # Classification
                'is_goblin': False,  # Goblins are rejected
                'is_demon': is_demon,
                'is_standard': not is_demon,
                'is_lineup_confirmed': True,
                
                # Averages (carry forward from input)
                'l5_avg': prop.get('l5_avg'),
                'l10_avg': prop.get('l10_avg'),
                'l20_avg': prop.get('l20_avg'),
                'season_avg': prop.get('season_avg') or prop.get('l10_avg'),
                
                # Hit rates
                'h5_rate': prop.get('h5_rate') or prop.get('hit_rate_l5'),
                'h10_rate': prop.get('h10_rate') or prop.get('hit_rate_l10'),
                'h20_rate': round(h20_rate_pct, 1),
                'hit_rate_l5': prop.get('h5_rate') or prop.get('hit_rate_l5'),
                'hit_rate_l10': prop.get('h10_rate') or prop.get('hit_rate_l10'),
                'hit_rate_l20': round(h20_rate_pct, 1),
                'l20_hits': l20_hits,
                
                # L15 Ceiling data
                'l15_ceiling_hits': l15_ceiling_hits,
                'l15_ceiling_pct': round(h15_ceiling_pct, 1),
                'ceiling_check_reason': f"Cleared line {l15_ceiling_hits}x in L15",
                
                # HR Power Bypass data (for HR props that failed standard Gate 1)
                'used_hr_power_bypass': used_hr_power_bypass,
                'hr_power_bypass_reason': hr_bypass_reason,
                
                # Volatility data
                'cv': round(cv, 3) if cv else None,
                'used_volatility_fasttrack': used_volatility_fasttrack,
                'volatility_fasttrack_reason': f"CV {cv:.2f} > 1.0 = Boom/Bust" if used_volatility_fasttrack else None,
                
                # VK predictions with tempo
                'raw_vk_pred': round(raw_vk_pred, 2),
                'matchup_modifier': round(matchup_modifier, 3),
                'tempo_modifier': round(tempo_modifier, 3),
                'vk_predicted': round(adjusted_vk_pred, 2),  # Adjusted projection (Raw * Matchup * Tempo)
                'vk_edge': round(raw_edge, 2),  # Raw cushion (adjusted_pred - line)
                'vk_prob_over': round(pinnacle_tp, 1),
                'vk_probability': round(pinnacle_tp, 1),
                'pinnacle_tp': round(pinnacle_tp, 1),
                
                # Board score
                'board_score': round(board_score, 1),
                
                # Gate thresholds met (for debugging)
                'gate_thresholds': {
                    'min_l20_required': cfg["min_l20"],
                    'max_cv_allowed': cfg["max_cv"],
                    'min_edge_required': cfg["min_edge"],
                    'dk_min': f"+{DK_MIN_WAR_ZONE} or is_demon",
                    'max_pinnacle_tp': MAX_PINNACLE_TP,
                    'l15_ceiling_min': L15_CEILING_MIN,
                    'volatility_threshold': VOLATILITY_THRESHOLD,
                },
                
                # Tier classification
                'tier': 'war_zone',
                'tier_label': 'MLB War Zone',
                'war_zone_qualified': True,
                
                # INTEL SUITE: Tempo breakdown for Vision Intel Suite
                'intel_suite': {
                    'tempo': self._build_tempo_intel_suite(prop, stat_key, tempo_modifier),
                    'pace_delta': self._build_tempo_intel_suite(prop, stat_key, tempo_modifier),  # Legacy alias
                    'stability_index': self._build_stability_index(cv),
                },
                
                # Active badges for UI display
                'active_badges': self._build_war_zone_badges(prop, stat_key, cv, used_volatility_fasttrack, used_hr_power_bypass),
                
                # Carry forward any vision intel
                'vision_intel': prop.get('vision_intel'),
                'intel_score': prop.get('intel_score'),
                'intel_verdict': prop.get('intel_verdict'),
                
                # BDL API Modifier Details (L/R splits, BVP, batting order)
                'bdl_modifiers': prop.get('bdl_modifiers'),
                
                # Timestamp
                'synced_at': datetime.now(timezone.utc).isoformat(),
            }
            
            qualified_picks.append(qualified_pick)
        
        # ====================================================================
        # 4. FINAL SORT & SLICE
        # ====================================================================
        
        # Log gate statistics
        logger.info("[MLB_WAR_ZONE] Gate Statistics:")
        logger.info(f"  Total Input: {gate_stats['total_input']}")
        logger.info(f"  DK Odds Fail (< +150 and not demon): {gate_stats['dk_odds_fail']}")
        logger.info(f"  Goblin Rejected (War Zone = Demons only): {gate_stats['goblin_rejected']}")
        logger.info(f"  Lineup Not Confirmed: {gate_stats['lineup_not_confirmed']}")
        logger.info(f"  Pinnacle TP Fail (> 45% and not demon): {gate_stats['pinnacle_tp_fail']}")
        logger.info(f"  Unsupported Stat: {gate_stats['unsupported_stat']}")
        logger.info(f"  Gate 1 Fail (L20 Hit Rate): {gate_stats['gate1_fail']}")
        logger.info(f"  Gate 1 Ceiling Fail (L15 < 2x): {gate_stats['gate1_ceiling_fail']}")
        logger.info(f"  Gate 1 HR Power Bypass (L10 HRs >= 2 or ISO > .200): {gate_stats['gate1_hr_power_bypass']}")
        logger.info(f"  Gate 2 Fail (CV): {gate_stats['gate2_fail']}")
        logger.info(f"  Gate 2 Volatility Fast-Tracks (CV > 1.0): {gate_stats['gate2_volatility_fasttrack']}")
        logger.info(f"  Gate 3 Fail (Edge): {gate_stats['gate3_fail']}")
        logger.info(f"  QUALIFIED: {gate_stats['qualified']}")
        
        # Sort descending by Board_Score (edge-weighted)
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
        
        logger.info(f"[MLB_WAR_ZONE] Final Result: {len(top_10)} picks (from {len(final_picks)} qualified)")
        
        for i, pick in enumerate(top_10[:5], 1):
            fasttrack_flag = " [CV FAST-TRACK]" if pick.get('used_volatility_fasttrack') else ""
            logger.info(f"[MLB_WAR_ZONE]   {i}. {pick['player_name']} - {pick['stat_key']} @ {pick['line']} | "
                       f"Board: {pick['board_score']:.1f} | Edge: +{pick['vk_edge']:.2f} | "
                       f"L20: {pick['l20_hits']}/20 | L15 Ceiling: {pick['l15_ceiling_hits']}x | "
                       f"CV: {pick['cv']:.2f}{fasttrack_flag}")
        
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
