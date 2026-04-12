"""
MLB Oracle Apex Service - Safe Haven Tier Logic (2026 Season)
==============================================================
The "Vegas Killer" mathematically-proven Safe Haven tier for MLB.

2026 MLB STAT-SPECIFIC CALIBRATION (Raw Cushion Thresholds):
| Stat | Max CV | Min Hit Rate (L20) | Min Raw Edge | Min VK Prob |
|------|--------|-------------------|--------------|-------------|
| HITS | 0.60   | 16/20 (80%)       | +0.30        | 70%         |
| TB   | 0.75   | 15/20 (75%)       | +0.45        | 70%         |
| K    | 0.45   | 15/20 (75%)       | +1.00        | 75%         |
| OUTS | 0.30   | 17/20 (85%)       | +1.50        | 80%         |
| HRR  | 0.55   | 16/20 (80%)       | +0.45        | 70%         |

Raw Edge Logic (typical lines):
- HITS: +0.30 requires 0.80+ pred on 0.5 line
- TB: +0.45 requires 1.95+ pred on 1.5 line
- K: +1.00 requires 6.5+ pred on 5.5 line
- OUTS: +1.50 requires 19.0+ pred on 17.5 line
- HRR: +0.45 requires 1.95+ pred on 1.5 line

PRIMARY QUALIFICATIONS:
1. DK Odds: Must be <= -240
2. Prop Type: GOBLIN (Green) only - reject standard and demon props
3. Lineup Status: is_lineup_confirmed MUST be True

PRE-COMPUTATION:
- Import mlb_matchup_math.py for Matchup Modifier
- Multiply raw VK_Projection by Matchup Multiplier for Adjusted_VK_Projection

3-GATE QUALIFICATION:
- Gate 1: Hit Rate (strict L20 percentage, no weighted recency exceptions)
- Gate 2: CV <= stat-specific limit
- Gate 3: Adjusted_VK_Projection Raw Edge >= min threshold AND TP >= 70%

HARD-STOP FILTERS:
- Weather: If wind_direction == 'IN' AND wind_speed > 12mph, reject batter props

FINAL SORT:
- Sort by Board_Score (TP Prob + (Raw Edge * 10) + (Hit Rate * 10))
- Top 10 before Vision Intel Delta Check
"""
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
import logging
import numpy as np

from services.mlb_matchup_math import get_mlb_matchup_analysis
from services.mlb_tempo_math import (
    calculate_hitter_tempo,
    calculate_pitcher_tempo,
    get_hitter_tempo_breakdown,
    get_pitcher_tempo_breakdown
)

logger = logging.getLogger(__name__)

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
            
            if pct_change >= 10:
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
        if not is_lineup_confirmed:
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
        Build the MLB Safe Haven tier using 2026 strict logic.
        
        PROCESS:
        1. PRIMARY FILTER: DK Odds <= -240, GOBLIN only, Lineup confirmed
        2. PRE-COMPUTATION: Apply Matchup Modifier for Adjusted_VK_Projection
        3. 3-GATE CHECK: Hit Rate (L20), CV, Edge+TP
        4. WEATHER HARD-STOP: Reject batter props when wind IN > 12mph
        5. FINAL SORT: Board_Score descending, return Top 10
        
        Args:
            all_picks: List of all props from the pipeline
            
        Returns:
            List of Top 10 qualified Safe Haven picks
        """
        logger.info("[MLB_SAFE_HAVEN] Building Safe Haven tier with 2026 strict logic...")
        logger.info(f"[MLB_SAFE_HAVEN] Input: {len(all_picks)} props to evaluate")
        
        # ====================================================================
        # SAFE HAVEN THRESHOLD DICTIONARY (Raw Decimal Cushion Edges)
        # ====================================================================
        thresholds = {
            "HITS": {"max_cv": 0.60, "min_l20": 16, "min_edge": 0.30, "min_tp": 70.0},
            "TB": {"max_cv": 0.75, "min_l20": 15, "min_edge": 0.45, "min_tp": 70.0},
            "K": {"max_cv": 0.45, "min_l20": 15, "min_edge": 1.00, "min_tp": 75.0},
            "OUTS": {"max_cv": 0.30, "min_l20": 17, "min_edge": 1.50, "min_tp": 80.0},
            "HRR": {"max_cv": 0.55, "min_l20": 16, "min_edge": 0.45, "min_tp": 70.0},
        }
        
        # Stat type aliases mapping to threshold keys
        stat_aliases = {
            "HITS": "HITS",
            "TOTAL BASES": "TB", "TB": "TB", "TOTAL_BASES": "TB",
            "PITCHER STRIKEOUTS": "K", "K": "K", "PITCHER_STRIKEOUTS": "K", "STRIKEOUTS": "K",
            "PITCHING OUTS": "OUTS", "OUTS": "OUTS", "PITCHING_OUTS": "OUTS", "OUTS RECORDED": "OUTS",
            "HRR": "HRR", "HITS+RUNS+RBIS": "HRR", "HITS_RUNS_RBIS": "HRR",
        }
        
        # Batter stats affected by wind
        BATTER_STATS = {"HITS", "TB", "HRR"}
        
        # Track gate statistics
        gate_stats = {
            'total_input': len(all_picks),
            'dk_odds_fail': 0,
            'not_goblin': 0,
            'lineup_not_confirmed': 0,
            'unsupported_stat': 0,
            'weather_hardstop': 0,
            'gate1_fail': 0,
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
            
            # 1a. DK Odds: Must be <= -240
            dk_odds = prop.get("dk_odds")
            if dk_odds is None:
                dk_odds = prop.get("all_odds", {}).get("draftkings")
            
            if dk_odds is None or dk_odds > -240:
                gate_stats['dk_odds_fail'] += 1
                continue
            
            # 1b. Prop Type: Must be exactly GOBLIN (Reject standard and Demon)
            is_goblin = prop.get("is_goblin", False)
            if not is_goblin:
                gate_stats['not_goblin'] += 1
                continue
            
            # 1c. Lineup Status: is_lineup_confirmed MUST be True
            is_lineup_confirmed = prop.get("is_lineup_confirmed")
            if not is_lineup_confirmed:
                gate_stats['lineup_not_confirmed'] += 1
                continue
            
            # ================================================================
            # 2. PRE-COMPUTATION (Matchup Modifier + Tempo Modifier)
            # ================================================================
            
            # Get raw VK projection
            raw_vk_pred = prop.get("vk_predicted") or prop.get("raw_vk_pred")
            if not raw_vk_pred or raw_vk_pred <= 0:
                continue
            
            # Get opponent for matchup calculation
            opponent = prop.get("opponent") or prop.get("opponent_abbr")
            
            # Calculate matchup modifier
            matchup_modifier = self._get_matchup_modifier(raw_stat, opponent) if opponent else 1.0
            
            # Calculate tempo modifier based on player type (hitter vs pitcher)
            is_pitcher_stat = stat_key in ["K", "OUTS"]
            
            if is_pitcher_stat:
                # Pitcher tempo: P/PA efficiency + bullpen rest
                pitcher_ppa = prop.get("pitcher_ppa") or prop.get("pitches_per_pa")
                bullpen_rest = prop.get("bullpen_rest_days")
                tempo_modifier = calculate_pitcher_tempo(pitcher_ppa, bullpen_rest)
            else:
                # Hitter tempo: batting order + away/home + team OBP
                batting_order = prop.get("batting_order") or prop.get("lineup_position")
                is_away = prop.get("is_away_team") or prop.get("is_away")
                team_obp_rank = prop.get("team_obp_rank")
                tempo_modifier = calculate_hitter_tempo(batting_order, is_away, team_obp_rank)
            
            # Chain the modifiers: Raw * Matchup * Tempo = Final Adjusted
            adjusted_vk_pred = raw_vk_pred * matchup_modifier * tempo_modifier
            
            # Get prop line
            line = prop.get("line", 0)
            if line <= 0:
                continue
            
            # ================================================================
            # 3. THE 3-GATE CHECK
            # ================================================================
            
            # Get L20 data
            l20_hits = prop.get("l20_hits")
            cv = prop.get("cv") or prop.get("vk_cv")
            tp_prob = prop.get("vk_prob_over") or prop.get("vk_probability") or prop.get("pinnacle_tp")
            
            # If L20 hits not directly available, calculate from hit rate
            if l20_hits is None:
                h20_rate = prop.get("h20_rate") or prop.get("hit_rate_l20")
                if h20_rate is not None:
                    l20_hits = int((h20_rate / 100) * 20)
                else:
                    # Cannot evaluate without hit data
                    gate_stats['gate1_fail'] += 1
                    continue
            
            # GATE 1: Strict Hit Rate (L20)
            # Must hit in >= min_l20 out of last 20 games (NO recency exceptions)
            if l20_hits < cfg["min_l20"]:
                gate_stats['gate1_fail'] += 1
                continue
            
            # GATE 2: Consistency (CV)
            # CV must be <= max_cv
            if cv is None or cv > cfg["max_cv"]:
                gate_stats['gate2_fail'] += 1
                continue
            
            # GATE 3: Adjusted Edge & TP
            # (Adjusted_VK_Projection - Prop_Line) >= min_edge
            raw_edge = adjusted_vk_pred - line
            if raw_edge < cfg["min_edge"]:
                gate_stats['gate3_fail'] += 1
                continue
            
            # Pinnacle True Probability must be >= min_tp
            if tp_prob is None or tp_prob < cfg["min_tp"]:
                gate_stats['gate3_fail'] += 1
                continue
            
            # ================================================================
            # 5. HARD-STOP ENVIRONMENTAL FILTER (Weather)
            # ================================================================
            
            # Check if this is a batter stat affected by wind
            if stat_key in BATTER_STATS:
                weather = prop.get("weather", {}) or {}
                wind_direction = (weather.get("wind_direction") or "").upper()
                wind_speed = weather.get("wind_speed", 0) or 0
                
                # Convert wind_speed to float if string
                if isinstance(wind_speed, str):
                    try:
                        wind_speed = float(wind_speed.replace("mph", "").strip())
                    except (ValueError, TypeError):
                        wind_speed = 0
                
                # HARD-STOP: wind_direction == 'IN' AND wind_speed > 12
                if wind_direction == "IN" and wind_speed > 12:
                    gate_stats['weather_hardstop'] += 1
                    logger.debug(f"[MLB_SAFE_HAVEN] WEATHER_REJECT: {prop.get('player_name')} - "
                                f"{stat_key} | Wind {wind_direction} @ {wind_speed}mph")
                    continue
            
            # ================================================================
            # QUALIFIED - Build output pick
            # ================================================================
            gate_stats['qualified'] += 1
            
            # Calculate hit rate percentage for board score
            h20_rate_pct = (l20_hits / 20) * 100
            
            # 6. Calculate Board_Score
            # Formula: TP_Prob + (Raw_Edge * 10) + (Hit_Rate_Pct * 0.1)
            board_score = tp_prob + (raw_edge * 10) + (h20_rate_pct * 0.1)
            
            # Build qualified pick with all required fields
            qualified_pick = {
                # Player info
                'player_name': prop.get('player_name'),
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
                'is_goblin': True,
                'is_demon': False,
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
                
                # Consistency
                'cv': round(cv, 3) if cv else None,
                
                # VK predictions with tempo
                'raw_vk_pred': round(raw_vk_pred, 2),
                'matchup_modifier': round(matchup_modifier, 3),
                'tempo_modifier': round(tempo_modifier, 3),
                'vk_predicted': round(adjusted_vk_pred, 2),  # Adjusted projection (Raw * Matchup * Tempo)
                'vk_edge': round(raw_edge, 2),  # Raw cushion (adjusted_pred - line)
                'vk_prob_over': round(tp_prob, 1),
                'vk_probability': round(tp_prob, 1),
                
                # Board score
                'board_score': round(board_score, 1),
                
                # Gate thresholds met (for debugging)
                'gate_thresholds': {
                    'min_l20_required': cfg["min_l20"],
                    'max_cv_allowed': cfg["max_cv"],
                    'min_edge_required': cfg["min_edge"],
                    'min_tp_required': cfg["min_tp"],
                },
                
                # Tier classification
                'tier': 'safe_haven',
                'tier_label': 'MLB Safe Haven',
                'oracle_apex_qualified': True,
                
                # INTEL SUITE: Tempo breakdown for Vision Intel Suite
                'intel_suite': {
                    'tempo': self._build_tempo_intel_suite(prop, stat_key, tempo_modifier),
                    'pace_delta': self._build_tempo_intel_suite(prop, stat_key, tempo_modifier),  # Legacy alias
                },
                
                # Carry forward any vision intel
                'vision_intel': prop.get('vision_intel'),
                'intel_score': prop.get('intel_score'),
                'intel_verdict': prop.get('intel_verdict'),
                
                # Timestamp
                'synced_at': datetime.now(timezone.utc).isoformat(),
            }
            
            qualified_picks.append(qualified_pick)
        
        # ====================================================================
        # 6. FINAL SORT & SLICE
        # ====================================================================
        
        # Log gate statistics
        logger.info("[MLB_SAFE_HAVEN] Gate Statistics:")
        logger.info(f"  Total Input: {gate_stats['total_input']}")
        logger.info(f"  DK Odds Fail (> -240): {gate_stats['dk_odds_fail']}")
        logger.info(f"  Not Goblin: {gate_stats['not_goblin']}")
        logger.info(f"  Lineup Not Confirmed: {gate_stats['lineup_not_confirmed']}")
        logger.info(f"  Unsupported Stat: {gate_stats['unsupported_stat']}")
        logger.info(f"  Gate 1 Fail (Hit Rate): {gate_stats['gate1_fail']}")
        logger.info(f"  Gate 2 Fail (CV): {gate_stats['gate2_fail']}")
        logger.info(f"  Gate 3 Fail (Edge/TP): {gate_stats['gate3_fail']}")
        logger.info(f"  Weather Hard-Stop: {gate_stats['weather_hardstop']}")
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
        
        logger.info(f"[MLB_SAFE_HAVEN] Final Result: {len(top_10)} picks (from {len(final_picks)} qualified)")
        
        for i, pick in enumerate(top_10[:5], 1):
            logger.info(f"[MLB_SAFE_HAVEN]   {i}. {pick['player_name']} - {pick['stat_key']} @ {pick['line']} | "
                       f"Board: {pick['board_score']} | Edge: +{pick['vk_edge']:.2f} | "
                       f"L20: {pick['l20_hits']}/20 | CV: {pick['cv']:.2f}")
        
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
        # FRONT LINES THRESHOLD DICTIONARY (Raw Decimal Cushion Edges)
        # More relaxed than Safe Haven - allows mid-juice plays
        # ====================================================================
        thresholds = {
            "HITS": {"max_cv": 0.85, "min_l20": 13, "min_edge": 0.20},
            "TB": {"max_cv": 0.95, "min_l20": 12, "min_edge": 0.30},
            "K": {"max_cv": 0.60, "min_l20": 13, "min_edge": 0.80},
            "OUTS": {"max_cv": 0.50, "min_l20": 14, "min_edge": 1.00},
            "HRR": {"max_cv": 0.75, "min_l20": 13, "min_edge": 0.30},
        }
        
        # Stat type aliases mapping to threshold keys
        stat_aliases = {
            "HITS": "HITS",
            "TOTAL BASES": "TB", "TB": "TB", "TOTAL_BASES": "TB",
            "PITCHER STRIKEOUTS": "K", "K": "K", "PITCHER_STRIKEOUTS": "K", "STRIKEOUTS": "K",
            "PITCHING OUTS": "OUTS", "OUTS": "OUTS", "PITCHING_OUTS": "OUTS", "OUTS RECORDED": "OUTS",
            "HRR": "HRR", "HITS+RUNS+RBIS": "HRR", "HITS_RUNS_RBIS": "HRR",
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
            if not is_lineup_confirmed:
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
            
            # Get raw VK projection
            raw_vk_pred = prop.get("vk_predicted") or prop.get("raw_vk_pred")
            if not raw_vk_pred or raw_vk_pred <= 0:
                continue
            
            # Get opponent for matchup calculation
            opponent = prop.get("opponent") or prop.get("opponent_abbr")
            
            # Calculate matchup modifier
            matchup_modifier = self._get_matchup_modifier(raw_stat, opponent) if opponent else 1.0
            
            # Calculate tempo modifier based on player type (hitter vs pitcher)
            is_pitcher_stat = stat_key in ["K", "OUTS"]
            
            if is_pitcher_stat:
                # Pitcher tempo: P/PA efficiency + bullpen rest
                pitcher_ppa = prop.get("pitcher_ppa") or prop.get("pitches_per_pa")
                bullpen_rest = prop.get("bullpen_rest_days")
                tempo_modifier = calculate_pitcher_tempo(pitcher_ppa, bullpen_rest)
            else:
                # Hitter tempo: batting order + away/home + team OBP
                batting_order = prop.get("batting_order") or prop.get("lineup_position")
                is_away = prop.get("is_away_team") or prop.get("is_away")
                team_obp_rank = prop.get("team_obp_rank")
                tempo_modifier = calculate_hitter_tempo(batting_order, is_away, team_obp_rank)
            
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
            
            # If L20 hits not directly available, calculate from hit rate
            if l20_hits is None:
                h20_rate = prop.get("h20_rate") or prop.get("hit_rate_l20")
                if h20_rate is not None:
                    l20_hits = int((h20_rate / 100) * 20)
                else:
                    gate_stats['gate1_fail'] += 1
                    continue
            
            # If L10 hits not directly available, calculate from hit rate
            if l10_hits is None:
                h10_rate = prop.get("h10_rate") or prop.get("hit_rate_l10")
                if h10_rate is not None:
                    l10_hits = int((h10_rate / 100) * 10)
                else:
                    l10_hits = 0  # Default to 0 if no L10 data
            
            # ----------------------------------------------------------------
            # GATE 1: Hit Rate (L20) with L10 RECENCY OVERRIDE
            # ----------------------------------------------------------------
            # Primary check: L20 >= min_l20
            # CRITICAL RECENCY EXCEPTION: If L20 fails, check L10.
            # If L10 hit rate >= 8/10 (80%), override the failure and PASS Gate 1.
            # ----------------------------------------------------------------
            passes_gate1 = l20_hits >= cfg["min_l20"]
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
                },
                
                # Carry forward any vision intel
                'vision_intel': prop.get('vision_intel'),
                'intel_score': prop.get('intel_score'),
                'intel_verdict': prop.get('intel_verdict'),
                
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
        # WAR ZONE THRESHOLD DICTIONARY (Raw Decimal Cushion Edges)
        # HIGH CV is allowed/rewarded here - we want VOLATILE players
        # ====================================================================
        thresholds = {
            "HITS": {"max_cv": 1.10, "min_l20": 8, "min_edge": 0.40},
            "TB": {"max_cv": 1.25, "min_l20": 7, "min_edge": 0.60},
            "K": {"max_cv": 0.85, "min_l20": 10, "min_edge": 1.50},
            "OUTS": {"max_cv": 0.70, "min_l20": 12, "min_edge": 2.00},
            "HRR": {"max_cv": 1.00, "min_l20": 9, "min_edge": 0.60},
        }
        
        # Stat type aliases mapping to threshold keys
        stat_aliases = {
            "HITS": "HITS",
            "TOTAL BASES": "TB", "TB": "TB", "TOTAL_BASES": "TB",
            "PITCHER STRIKEOUTS": "K", "K": "K", "PITCHER_STRIKEOUTS": "K", "STRIKEOUTS": "K",
            "PITCHING OUTS": "OUTS", "OUTS": "OUTS", "PITCHING_OUTS": "OUTS", "OUTS RECORDED": "OUTS",
            "HRR": "HRR", "HITS+RUNS+RBIS": "HRR", "HITS_RUNS_RBIS": "HRR",
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
            if not is_lineup_confirmed:
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
            
            # Get raw VK projection
            raw_vk_pred = prop.get("vk_predicted") or prop.get("raw_vk_pred")
            if not raw_vk_pred or raw_vk_pred <= 0:
                continue
            
            # Get opponent for matchup calculation
            opponent = prop.get("opponent") or prop.get("opponent_abbr")
            
            # Calculate matchup modifier
            matchup_modifier = self._get_matchup_modifier(raw_stat, opponent) if opponent else 1.0
            
            # Calculate tempo modifier based on player type (hitter vs pitcher)
            is_pitcher_stat = stat_key in ["K", "OUTS"]
            
            if is_pitcher_stat:
                # Pitcher tempo: P/PA efficiency + bullpen rest
                pitcher_ppa = prop.get("pitcher_ppa") or prop.get("pitches_per_pa")
                bullpen_rest = prop.get("bullpen_rest_days")
                tempo_modifier = calculate_pitcher_tempo(pitcher_ppa, bullpen_rest)
            else:
                # Hitter tempo: batting order + away/home + team OBP
                batting_order = prop.get("batting_order") or prop.get("lineup_position")
                is_away = prop.get("is_away_team") or prop.get("is_away")
                team_obp_rank = prop.get("team_obp_rank")
                tempo_modifier = calculate_hitter_tempo(batting_order, is_away, team_obp_rank)
            
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
            
            # If L20 hits not directly available, calculate from hit rate
            if l20_hits is None:
                h20_rate = prop.get("h20_rate") or prop.get("hit_rate_l20")
                if h20_rate is not None:
                    l20_hits = int((h20_rate / 100) * 20)
                else:
                    gate_stats['gate1_fail'] += 1
                    continue
            
            # If L15 ceiling hits not available, estimate from L15 values
            if l15_ceiling_hits is None:
                l15_values = prop.get("l15_values") or prop.get("last_15_values") or []
                if l15_values:
                    # Count how many times they cleared the line in L15
                    l15_ceiling_hits = sum(1 for v in l15_values if v >= line)
                else:
                    # Fallback: estimate from L10 hit rate if available
                    h10_rate = prop.get("h10_rate") or prop.get("hit_rate_l10")
                    if h10_rate is not None:
                        # Estimate L15 ceiling hits proportionally
                        l15_ceiling_hits = int((h10_rate / 100) * 15 * 0.6)  # Conservative
                    else:
                        l15_ceiling_hits = 0
            
            # ----------------------------------------------------------------
            # GATE 1: Hit Rate (L20) + L15 CEILING CHECK
            # ----------------------------------------------------------------
            # Primary: L20 >= min_l20
            # CRITICAL CEILING EXCEPTION: Player MUST have cleared this specific
            # Demon/High line at least TWICE in their last 15 games.
            # We want DEMONSTRATED SPIKES, not just average hits.
            # ----------------------------------------------------------------
            passes_gate1_base = l20_hits >= cfg["min_l20"]
            passes_gate1_ceiling = l15_ceiling_hits >= L15_CEILING_MIN
            
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
                },
                
                # Carry forward any vision intel
                'vision_intel': prop.get('vision_intel'),
                'intel_score': prop.get('intel_score'),
                'intel_verdict': prop.get('intel_verdict'),
                
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
