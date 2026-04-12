"""
MLB Oracle Apex Service - Safe Haven Tier Logic (2026 Season)
==============================================================
The "Vegas Killer" mathematically-proven Safe Haven tier for MLB.

2026 MLB STAT-SPECIFIC CALIBRATION:
| Stat | Max CV | Min Hit Rate (L20) | Min Edge | Min VK Prob |
|------|--------|-------------------|----------|-------------|
| HITS | 0.60   | 16/20 (80%)       | 15%      | 70%         |
| TB   | 0.75   | 15/20 (75%)       | 20%      | 70%         |
| K    | 0.45   | 15/20 (75%)       | 12%      | 75%         |
| OUTS | 0.30   | 17/20 (85%)       | 8%       | 80%         |
| HRR  | 0.55   | 16/20 (80%)       | 18%      | 70%         |

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
- Gate 3: Adjusted_VK_Projection Edge >= min edge AND TP >= 70%

HARD-STOP FILTERS:
- Weather: If wind_direction == 'IN' AND wind_speed > 12mph, reject batter props

FINAL SORT:
- Sort by Board_Score (TP Prob + VK Edge + (Hit Rate * 10))
- Top 10 before Vision Intel Delta Check
"""
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
import logging
import numpy as np

from services.mlb_matchup_math import get_mlb_matchup_analysis

logger = logging.getLogger(__name__)

# =============================================================================
# 2026 MLB ORACLE APEX CONFIGURATION - SAFE HAVEN (Strictest)
# =============================================================================

MLB_SAFE_HAVEN_CONFIG = {
    'HITS': {
        'max_cv': 0.60,
        'min_hit_rate': 16,      # 16/20 = 80%
        'sample_size': 20,
        'min_edge_pct': 15.0,    # 15% edge minimum
        'min_prob': 70.0,
        'is_batter_stat': True,
    },
    'TB': {  # Total Bases
        'max_cv': 0.75,
        'min_hit_rate': 15,      # 15/20 = 75%
        'sample_size': 20,
        'min_edge_pct': 20.0,    # 20% edge minimum
        'min_prob': 70.0,
        'is_batter_stat': True,
    },
    'TOTAL BASES': {  # Alias for TB
        'max_cv': 0.75,
        'min_hit_rate': 15,
        'sample_size': 20,
        'min_edge_pct': 20.0,
        'min_prob': 70.0,
        'is_batter_stat': True,
    },
    'K': {  # Pitcher Strikeouts
        'max_cv': 0.45,
        'min_hit_rate': 15,      # 15/20 = 75%
        'sample_size': 20,
        'min_edge_pct': 12.0,    # 12% edge minimum
        'min_prob': 75.0,
        'is_batter_stat': False,
    },
    'PITCHER STRIKEOUTS': {  # Alias for K
        'max_cv': 0.45,
        'min_hit_rate': 15,
        'sample_size': 20,
        'min_edge_pct': 12.0,
        'min_prob': 75.0,
        'is_batter_stat': False,
    },
    'OUTS': {  # Pitching Outs Recorded
        'max_cv': 0.30,
        'min_hit_rate': 17,      # 17/20 = 85%
        'sample_size': 20,
        'min_edge_pct': 8.0,     # 8% edge minimum
        'min_prob': 80.0,
        'is_batter_stat': False,
    },
    'PITCHING OUTS': {  # Alias for OUTS
        'max_cv': 0.30,
        'min_hit_rate': 17,
        'sample_size': 20,
        'min_edge_pct': 8.0,
        'min_prob': 80.0,
        'is_batter_stat': False,
    },
    'HRR': {  # Hits + Runs + RBIs combo
        'max_cv': 0.55,
        'min_hit_rate': 16,      # 16/20 = 80%
        'sample_size': 20,
        'min_edge_pct': 18.0,    # 18% edge minimum
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
        
        # GATE 3: EDGE + PROBABILITY
        # Calculate edge as percentage: (adjusted_pred - line) / line * 100
        if line > 0:
            edge_pct = ((adjusted_vk_pred - line) / line) * 100
        else:
            edge_pct = 0
        
        if edge_pct < cfg['min_edge_pct']:
            return False, f"GATE3_EDGE: {edge_pct:.1f}% < {cfg['min_edge_pct']}%"
        
        if vk_prob < cfg['min_prob']:
            return False, f"GATE3_PROB: {vk_prob:.1f}% < {cfg['min_prob']}%"
        
        return True, "MLB_SAFE_HAVEN_QUALIFIED"
    
    def calculate_board_score(
        self,
        vk_prob: float,
        edge_pct: float,
        hit_rate_pct: float
    ) -> float:
        """
        Calculate Board Score for final sorting.
        
        Formula: TP Prob + VK Edge + (Hit Rate * 10)
        
        Example:
        - VK Prob: 75%
        - Edge: 18%
        - Hit Rate: 80%
        Board Score = 75 + 18 + (80 * 0.1) = 75 + 18 + 8 = 101
        """
        return vk_prob + edge_pct + (hit_rate_pct * 0.1)
    
    async def build_safe_haven_tier(self) -> List[Dict]:
        """
        Build the MLB Safe Haven tier using 2026 thresholds.
        
        PROCESS:
        1. Load all props from mlb_cached_board
        2. Apply Primary Qualifications (DK Odds, Goblin, Lineup)
        3. Get VK predictions from master_hub vk_baselines
        4. Apply Matchup Modifier for Adjusted_VK_Projection
        5. Apply 3-Gate Qualification (Hit Rate, CV, Edge+Prob)
        6. Apply Weather Hard-Stop for batter props
        7. Sort by Board_Score, take Top 10
        
        Returns:
            List of qualified Safe Haven picks
        """
        logger.info("[MLB_APEX] Building Safe Haven tier with 2026 thresholds...")
        
        # Load data sources
        all_players = await self.cached_board.find({}, {"_id": 0}).to_list(length=None)
        
        # Pre-load VK baselines from master hub
        vk_baselines_lookup = {}
        async for hub_doc in self.master_hub.find({}, {"_id": 0, "display_name": 1, "vk_baselines": 1}):
            display_name = hub_doc.get("display_name", "")
            if display_name and hub_doc.get("vk_baselines"):
                vk_baselines_lookup[display_name.lower()] = hub_doc.get("vk_baselines", {})
        
        logger.info(f"[MLB_APEX] Loaded {len(all_players)} players, {len(vk_baselines_lookup)} VK baselines")
        
        # Track gate statistics
        gate_stats = {
            'total_props': 0,
            'dk_odds_fail': 0,
            'not_goblin': 0,
            'lineup_not_confirmed': 0,
            'unsupported_stat': 0,
            'insufficient_data': 0,
            'no_vk_baseline': 0,
            'weather_hardstop': 0,
            'gate1_fail': 0,
            'gate2_fail': 0,
            'gate3_fail': 0,
            'qualified': 0,
        }
        
        qualified_picks = []
        seen_combos = set()
        
        for player_doc in all_players:
            player_name = player_doc.get("player_name", "")
            team = player_doc.get("team", "")
            
            # Get VK baselines for this player
            player_vk = vk_baselines_lookup.get(player_name.lower(), {})
            
            for prop in player_doc.get("props", []):
                gate_stats['total_props'] += 1
                
                raw_stat = prop.get("stat_type", "")
                stat_type = self._normalize_stat_type(raw_stat)
                line = prop.get("line", 0)
                
                # Skip unsupported stats
                if stat_type not in MLB_SAFE_HAVEN_CONFIG:
                    gate_stats['unsupported_stat'] += 1
                    continue
                
                # Dedupe by player + stat + line
                combo_key = f"{player_name}|{stat_type}|{line}"
                if combo_key in seen_combos:
                    continue
                seen_combos.add(combo_key)
                
                # ============================================================
                # PRIMARY QUALIFICATION CHECKS
                # ============================================================
                
                # 1. DK Odds check
                dk_odds = prop.get("dk_odds") or prop.get("all_odds", {}).get("draftkings")
                if dk_odds is None or dk_odds > DK_ODDS_THRESHOLD:
                    gate_stats['dk_odds_fail'] += 1
                    continue
                
                # 2. Goblin check
                is_goblin = prop.get("is_goblin", False)
                if not is_goblin:
                    gate_stats['not_goblin'] += 1
                    continue
                
                # 3. Lineup confirmed check
                is_lineup_confirmed = prop.get("is_lineup_confirmed", False)
                # Also accept if lineup data isn't available but player has recent games
                if not is_lineup_confirmed:
                    # Check if player has recent game data as proxy
                    last_games = prop.get("last_10_games", [])
                    if last_games and len(last_games) >= 5:
                        is_lineup_confirmed = True  # Assume active player
                
                if not is_lineup_confirmed:
                    gate_stats['lineup_not_confirmed'] += 1
                    continue
                
                # ============================================================
                # GET GAME LOG DATA
                # ============================================================
                
                last_games = prop.get("last_10_games", [])
                
                # Need at least 20 games for L20 calculation
                if len(last_games) < 20:
                    # Try to get from master hub
                    hub_doc = await self.master_hub.find_one(
                        {"display_name": player_name},
                        {"_id": 0, "bdl_game_logs": 1}
                    )
                    if hub_doc and hub_doc.get("bdl_game_logs"):
                        last_games = hub_doc["bdl_game_logs"][:20]
                
                if len(last_games) < 20:
                    gate_stats['insufficient_data'] += 1
                    continue
                
                # Calculate L20 values
                l20_values = self._get_mlb_stat_values(last_games[:20], stat_type)
                if len(l20_values) < 20:
                    gate_stats['insufficient_data'] += 1
                    continue
                
                # Calculate CV from L10
                l10_values = l20_values[:10]
                l10_mean = np.mean(l10_values) if l10_values else 0
                l10_std = np.std(l10_values) if l10_values else 0
                cv = l10_std / l10_mean if l10_mean > 0 else 999
                
                # ============================================================
                # GET VK PREDICTION & APPLY MATCHUP MODIFIER
                # ============================================================
                
                vk_key = VK_BASELINE_MAP.get(stat_type)
                if not vk_key or vk_key not in player_vk:
                    gate_stats['no_vk_baseline'] += 1
                    continue
                
                vk_stat = player_vk[vk_key]
                raw_vk_pred = vk_stat.get("weighted_baseline", 0)
                
                if not raw_vk_pred or raw_vk_pred <= 0:
                    gate_stats['no_vk_baseline'] += 1
                    continue
                
                # Get opponent for matchup modifier
                opponent = prop.get("opponent") or prop.get("opponent_abbr")
                if not opponent:
                    # Derive from away_team/home_team
                    away = prop.get("away_team", "")
                    home = prop.get("home_team", "")
                    if away and home:
                        # Simple derivation - if player team matches one, opponent is the other
                        if team in away:
                            opponent = home[:3].upper()
                        else:
                            opponent = away[:3].upper()
                
                # Apply matchup modifier
                matchup_modifier = self._get_matchup_modifier(stat_type, opponent)
                adjusted_vk_pred = raw_vk_pred * matchup_modifier
                
                # Calculate edge percentage
                edge_pct = ((adjusted_vk_pred - line) / line * 100) if line > 0 else 0
                
                # Calculate probability (simplified model based on edge)
                # Higher edge = higher probability of going over
                vk_prob = min(90, max(50, 50 + edge_pct * 1.5))
                
                # ============================================================
                # 3-GATE QUALIFICATION
                # ============================================================
                
                qualifies, reason = self.qualifies_for_mlb_safe_haven(
                    stat_type=stat_type,
                    line=line,
                    l20_values=l20_values,
                    cv=cv,
                    adjusted_vk_pred=adjusted_vk_pred,
                    vk_prob=vk_prob,
                    dk_odds=dk_odds,
                    is_goblin=is_goblin,
                    is_lineup_confirmed=is_lineup_confirmed,
                    prop=prop
                )
                
                if not qualifies:
                    if "GATE1" in reason:
                        gate_stats['gate1_fail'] += 1
                    elif "GATE2" in reason:
                        gate_stats['gate2_fail'] += 1
                    elif "GATE3" in reason:
                        gate_stats['gate3_fail'] += 1
                    elif "WEATHER" in reason:
                        gate_stats['weather_hardstop'] += 1
                    continue
                
                gate_stats['qualified'] += 1
                
                # ============================================================
                # BUILD QUALIFIED PICK
                # ============================================================
                
                l5_values = l20_values[:5]
                l5_avg = round(np.mean(l5_values), 1) if l5_values else None
                l10_avg = round(l10_mean, 1)
                l20_avg = round(np.mean(l20_values), 1)
                
                l20_hits = sum(1 for v in l20_values if v >= line)
                l10_hits = sum(1 for v in l10_values if v >= line)
                l5_hits = sum(1 for v in l5_values if v >= line)
                
                h5_rate = round((l5_hits / 5) * 100, 1) if len(l5_values) >= 5 else None
                h10_rate = round((l10_hits / 10) * 100, 1) if len(l10_values) >= 10 else None
                h20_rate = round((l20_hits / 20) * 100, 1)
                
                # Calculate Board Score for sorting
                board_score = self.calculate_board_score(vk_prob, edge_pct, h20_rate)
                
                qualified_picks.append({
                    'player_name': player_name,
                    'stat_type': stat_type,
                    'line': line,
                    # Averages
                    'l5_avg': l5_avg,
                    'l10_avg': l10_avg,
                    'l20_avg': l20_avg,
                    'season_avg': l10_avg,
                    # Hit rates (frontend field names)
                    'h5_rate': h5_rate,
                    'h10_rate': h10_rate,
                    'h20_rate': h20_rate,
                    'hit_rate_l5': h5_rate,
                    'hit_rate_l10': h10_rate,
                    'hit_rate_l20': h20_rate,
                    'l20_hits': l20_hits,
                    # CV
                    'cv': round(cv, 3),
                    # VK predictions
                    'vk_predicted': round(adjusted_vk_pred, 2),
                    'vk_edge': round(adjusted_vk_pred - line, 2),
                    'vk_edge_pct': round(edge_pct, 1),
                    'vk_prob_over': round(vk_prob, 1),
                    'vk_probability': round(vk_prob, 1),
                    'matchup_modifier': round(matchup_modifier, 3),
                    'raw_vk_pred': round(raw_vk_pred, 2),
                    # Board score for sorting
                    'board_score': round(board_score, 1),
                    # Prop metadata
                    'dk_odds': dk_odds,
                    'is_goblin': True,
                    'is_demon': False,
                    'team': team,
                    'opponent': opponent,
                    'game_time': prop.get('game_time') or prop.get('commence_time'),
                    'photo_url': player_doc.get('photo_url') or player_doc.get('headshot_url'),
                    'headshot_url': player_doc.get('headshot_url'),
                    # Tier
                    'tier': 'safe_haven',
                    'tier_label': 'MLB Safe Haven',
                    'oracle_apex_qualified': True,
                    'synced_at': datetime.now(timezone.utc).isoformat(),
                })
        
        logger.info(f"[MLB_APEX] Gate stats: {gate_stats}")
        
        # ============================================================
        # FINAL SORT BY BOARD SCORE, TAKE TOP 10
        # ============================================================
        
        # Sort by board_score descending
        qualified_picks.sort(key=lambda x: x.get('board_score', 0), reverse=True)
        
        # Dedupe: Keep highest board_score per player+stat
        dedupe_map = {}
        for pick in qualified_picks:
            key = f"{pick['player_name']}|{pick['stat_type']}"
            if key not in dedupe_map or pick['board_score'] > dedupe_map[key]['board_score']:
                dedupe_map[key] = pick
        
        final_picks = list(dedupe_map.values())
        final_picks.sort(key=lambda x: x.get('board_score', 0), reverse=True)
        
        # Take Top 10
        top_10 = final_picks[:10]
        
        logger.info(f"[MLB_APEX] Final Safe Haven: {len(top_10)} picks (from {len(final_picks)} qualified)")
        
        for i, pick in enumerate(top_10[:5], 1):
            logger.info(f"[MLB_APEX]   {i}. {pick['player_name']} - {pick['stat_type']} @ {pick['line']} | "
                       f"Score: {pick['board_score']} | Edge: {pick['vk_edge_pct']:.1f}% | "
                       f"HR: {pick['h20_rate']}%")
        
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
