"""
Oracle Apex Service - Safe Haven Tier Logic
============================================
The "Vegas Killer" mathematically-proven Safe Haven tier.

STAT-SPECIFIC CALIBRATION:
| Stat | Max CV | Hit Rate | Min Edge | Notes |
|------|--------|----------|----------|-------|
| PTS  | 0.22   | 18/20    | 2.0      | Points are stable |
| REB  | 0.35   | 16/20*   | 1.5      | *14/20 OK if L20 Mean >= Line + 2.5 |
| AST  | 0.35   | 15/20    | 2.0      | Higher variance accepted |
| PRA  | 0.20   | 18/20    | 2.0      | Combos self-correct |

GATE LOGIC:
- Gate 1: Hit Rate (stat-specific, with REB buffer rule)
- Gate 2: CV (Coefficient of Variation) <= stat-specific limit
- Gate 3: Edge >= stat-specific AND VK Prob >= 75%

POST-FILTERS:
- Minutes >= 22 (volume check)
- Dedupe: Keep lowest line per player+stat (best goblin)
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
import logging
import numpy as np
from services.vk_model_enforcement import (
    calculate_vk_model,
    enforce_vk_fields,
    validate_market_first,
    VKResult,
    MARKET_FIRST_REQUIRED
)

logger = logging.getLogger(__name__)

# =============================================================================
# ORACLE APEX CONFIGURATION - SAFE HAVEN (Strictest)
# =============================================================================

ORACLE_APEX_CONFIG = {
    'PTS': {
        'max_cv': 0.22,
        'min_hit_rate': 18,
        'sample_size': 20,
        'min_edge': 2.0,
        'min_prob': 75.0,
    },
    'REB': {
        'max_cv': 0.35,
        'min_hit_rate': 16,
        'sample_size': 20,
        'min_edge': 1.5,
        'min_prob': 75.0,
        # Buffer rule: 14/20 OK if L20 Mean >= Line + 2.5
        'relaxed_hit_rate': 14,
        'relaxed_mean_buffer': 2.5,
    },
    'AST': {
        'max_cv': 0.35,
        'min_hit_rate': 15,
        'sample_size': 20,
        'min_edge': 2.0,
        'min_prob': 75.0,
    },
    'PRA': {
        'max_cv': 0.20,
        'min_hit_rate': 18,
        'sample_size': 20,
        'min_edge': 2.0,
        'min_prob': 75.0,
    },
}

# =============================================================================
# FRONT LINES CONFIGURATION (Moderate)
# =============================================================================

FRONT_LINES_CONFIG = {
    'PTS': {
        'max_cv': 0.28,           # Accommodates standard nightly variance
        'min_hit_rate': 14,
        'sample_size': 20,
        'min_edge': 1.5,
        'min_prob': 55.0,
    },
    'REB': {
        'max_cv': 0.40,           # Normal for players getting 25-30 mins
        'min_hit_rate': 12,
        'sample_size': 20,
        'min_edge': 1.5,
        'min_prob': 55.0,
        # Buffer rule: 10/20 OK if L5 Mean >= Line + 1.5
        'relaxed_hit_rate': 10,
        'relaxed_mean_buffer': 1.5,
        'relaxed_sample_size': 5,  # Uses L5 mean for buffer
    },
    'AST': {
        'max_cv': 0.40,           # Normal for secondary ball handlers
        'min_hit_rate': 12,
        'sample_size': 20,
        'min_edge': 1.5,
        'min_prob': 55.0,
    },
    'PRA': {
        'max_cv': 0.25,           # Baseline combo variance
        'min_hit_rate': 14,
        'sample_size': 20,
        'min_edge': 1.5,
        'min_prob': 55.0,
    },
}

# =============================================================================
# WAR ZONE CONFIGURATION (Riskiest - Demon Ceiling Plays)
# =============================================================================
# 2026 UPDATE: Increased CV thresholds to allow boom/bust profiles.
# Volatility Fast-Track: Props with CV > max_cv bypass Gate 2 entirely.
# We WANT high variance in War Zone - it's the ceiling play tier.

WAR_ZONE_CONFIG = {
    'PTS': {
        'max_cv': 0.85,           # High variance - boom/bust profiles welcome
        'min_hit_rate': 7,        # 7/20 hit rate minimum
        'sample_size': 20,
        'min_edge': -999,         # No edge requirement for demons
        'min_prob': 40.0,         # VK probability threshold
        # Buffer rule: 5/20 OK if L5 Mean > Line + 3.0
        'relaxed_hit_rate': 5,
        'relaxed_mean_buffer': 3.0,
        'relaxed_sample_size': 5,
    },
    'REB': {
        'max_cv': 1.00,           # Massive spikes allowed - rebounding is volatile
        'min_hit_rate': 7,        # 7/20 hit rate minimum
        'sample_size': 20,
        'min_edge': -999,         # No edge requirement for demons
        'min_prob': 40.0,         # VK probability threshold
    },
    'AST': {
        'max_cv': 1.00,           # Massive spikes allowed - assists are volatile
        'min_hit_rate': 7,        # 7/20 hit rate minimum
        'sample_size': 20,
        'min_edge': -999,         # No edge requirement for demons
        'min_prob': 40.0,         # VK probability threshold
    },
    'PRA': {
        'max_cv': 0.75,           # Combined stat - slightly tighter
        'min_hit_rate': 7,        # 7/20 hit rate minimum
        'sample_size': 20,
        'min_edge': -999,         # No edge requirement for demons
        'min_prob': 40.0,         # VK probability threshold
    },
}

# War Zone DK Odds Floor (lowered from +200 to +140)
WAR_ZONE_DK_ODDS_FLOOR = 140

# Minimum minutes for volume check
MIN_MINUTES = 22

# =============================================================================
# MLB ORACLE APEX CONFIGURATION - SAFE HAVEN (Strictest)
# =============================================================================
# MLB props have higher CV than NBA due to baseball's binary nature.
# Gates tuned for "Contact" and "Volume" metrics.
# DK Odds <= -240 for MLB Safe Haven (sweet spot for 0.5 Hits, 4.5 K props)

MLB_ORACLE_APEX_CONFIG = {
    'Hits': {
        'max_cv': 0.60,           # Higher CV due to 0-for-4 nights
        'min_hit_rate': 16,       # 80% of L20
        'sample_size': 20,
        'min_edge': 15.0,         # VK projection must be 15% above line
        'min_prob': 70.0,         # Sharp book de-vigged probability
    },
    'Total Bases': {
        'max_cv': 0.75,           # Highest CV - XBH are volatile
        'min_hit_rate': 15,       # 75% of L20
        'sample_size': 20,
        'min_edge': 20.0,         # Higher edge required due to variance
        'min_prob': 70.0,
    },
    'Pitcher Strikeouts': {
        'max_cv': 0.45,           # Moderately stable for aces
        'min_hit_rate': 15,       # 75% of L20
        'sample_size': 20,
        'min_edge': 12.0,
        'min_prob': 75.0,
    },
    'Pitching Outs': {
        'max_cv': 0.30,           # Most stable - manager pulls early = fail
        'min_hit_rate': 17,       # 85% of L20
        'sample_size': 20,
        'min_edge': 8.0,
        'min_prob': 80.0,
    },
    'Hits+Runs+RBIs': {
        'max_cv': 0.55,           # Combo smooths variance
        'min_hit_rate': 16,       # 80% of L20
        'sample_size': 20,
        'min_edge': 18.0,
        'min_prob': 70.0,
    },
    # Fallback for unmapped stats
    'DEFAULT': {
        'max_cv': 0.65,
        'min_hit_rate': 14,
        'sample_size': 20,
        'min_edge': 15.0,
        'min_prob': 70.0,
    },
}

# =============================================================================
# MLB FRONT LINES CONFIGURATION (Moderate)
# =============================================================================

MLB_FRONT_LINES_CONFIG = {
    'Hits': {
        'max_cv': 0.75,
        'min_hit_rate': 12,
        'sample_size': 20,
        'min_edge': 10.0,
        'min_prob': 55.0,
    },
    'Total Bases': {
        'max_cv': 0.85,
        'min_hit_rate': 11,
        'sample_size': 20,
        'min_edge': 12.0,
        'min_prob': 55.0,
    },
    'Pitcher Strikeouts': {
        'max_cv': 0.55,
        'min_hit_rate': 12,
        'sample_size': 20,
        'min_edge': 8.0,
        'min_prob': 60.0,
    },
    'Pitching Outs': {
        'max_cv': 0.40,
        'min_hit_rate': 14,
        'sample_size': 20,
        'min_edge': 5.0,
        'min_prob': 65.0,
    },
    'Hits+Runs+RBIs': {
        'max_cv': 0.70,
        'min_hit_rate': 12,
        'sample_size': 20,
        'min_edge': 12.0,
        'min_prob': 55.0,
    },
    'DEFAULT': {
        'max_cv': 0.80,
        'min_hit_rate': 10,
        'sample_size': 20,
        'min_edge': 8.0,
        'min_prob': 55.0,
    },
}

# =============================================================================
# MLB WAR ZONE CONFIGURATION (Demon Ceiling Plays)
# =============================================================================

MLB_WAR_ZONE_CONFIG = {
    'Hits': {
        'max_cv': 1.0,            # High variance allowed for ceiling plays
        'min_hit_rate': 6,
        'sample_size': 20,
        'min_edge': -999,         # No edge requirement for demons
        'min_prob': 35.0,
    },
    'Total Bases': {
        'max_cv': 1.2,            # Very high variance for XBH demons
        'min_hit_rate': 5,
        'sample_size': 20,
        'min_edge': -999,
        'min_prob': 30.0,
    },
    'Pitcher Strikeouts': {
        'max_cv': 0.70,
        'min_hit_rate': 6,
        'sample_size': 20,
        'min_edge': -999,
        'min_prob': 40.0,
    },
    'Hits+Runs+RBIs': {
        'max_cv': 0.90,
        'min_hit_rate': 6,
        'sample_size': 20,
        'min_edge': -999,
        'min_prob': 35.0,
    },
    'DEFAULT': {
        'max_cv': 1.0,
        'min_hit_rate': 5,
        'sample_size': 20,
        'min_edge': -999,
        'min_prob': 35.0,
    },
}

# MLB DK Odds threshold for Safe Haven
MLB_DK_TIER_SAFE_HAVEN_MAX = -240  # -240 is the sweet spot for MLB Goblins


# =============================================================================
# NBA ACTUARY GATE - PrizePicks Goblin Tax Curve
# =============================================================================
# Mirrors the MLB implementation for unified math across both sports.

def get_nba_pp_required_win_rate(dk_odds: int, prop_type: str) -> float:
    """
    Calculate the required win rate to beat PrizePicks' Goblin Tax curve for NBA.
    
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
        
        try:
            dk_odds = int(dk_odds)
        except (ValueError, TypeError):
            return 75.0
        
        # Goblin Tax Curve (mapped from PP multipliers)
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


def calculate_nba_master_probability(dk_odds: int, true_hit_rate: float, prop_type: str) -> dict:
    """
    NBA MASTER PROBABILITY FUNCTION - Used by ALL tiers for consistent edge calculation.
    
    This ensures the same player shows the EXACT same True Edge regardless of which tier
    they appear in. Differentiation happens through FILTERING, not math.
    
    Formula (50/50 Blend):
        market_prob = (abs(dk_odds) / (abs(dk_odds) + 100)) * 100  (if dk_odds < 0)
        propvision_true_prob = (market_prob * 0.50) + (true_hit_rate * 0.50)
        true_edge = propvision_true_prob - casino_req_rate
    
    Args:
        dk_odds: DraftKings odds (negative for favorites, positive for dogs)
        true_hit_rate: Hit rate based on L10 games (%)
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
    casino_req_rate = get_nba_pp_required_win_rate(dk_odds, prop_type)
    
    # Calculate True Edge
    true_edge = propvision_true_prob - casino_req_rate
    
    return {
        'market_prob': round(market_prob, 1),
        'propvision_true_prob': round(propvision_true_prob, 1),
        'casino_req_rate': round(casino_req_rate, 1),
        'true_edge': round(true_edge, 1),
    }


class OracleApexService:
    """
    Oracle Apex Service - The new Safe Haven tier logic.
    
    Uses Vegas Killer ML predictions combined with statistical filters
    to identify mathematically-proven safe plays.
    """
    
    def __init__(self, db, vegas_killer_model=None):
        self.db = db
        self.vegas_killer_model = vegas_killer_model
        self.cached_board = db.dg_cached_board
        self.live_props = db.dg_live_props
        self.master_hub = db.nba_master_hub_2026
        self.oracle_apex_collection = db.oracle_apex_picks
        
    def set_vegas_killer_model(self, model):
        """Set the Vegas Killer model reference."""
        self.vegas_killer_model = model
    
    def _did_play(self, game: Dict) -> bool:
        """Check if player actually played in a game (not DNP)."""
        mins = game.get("min", "0") or "0"
        if isinstance(mins, str):
            try:
                mins_val = int(mins.split(':')[0]) if ':' in mins else int(mins)
            except (ValueError, TypeError):
                mins_val = 0
        else:
            mins_val = mins
        return mins_val > 0
    
    def _get_stat_values(self, game_logs: List[Dict], stat_type: str) -> List[float]:
        """Extract stat values from game logs based on stat type."""
        stat_field_map = {
            "PTS": "pts",
            "REB": "reb", 
            "AST": "ast",
            "STL": "stl",
            "BLK": "blk",
            "3PM": "fg3m",
            "THREES": "fg3m",
            "TO": "turnover",
        }
        
        played_games = [g for g in game_logs if self._did_play(g)]
        
        if stat_type == 'PRA':
            return [g.get('pts', 0) + g.get('reb', 0) + g.get('ast', 0) for g in played_games]
        elif stat_type == 'PR':
            return [g.get('pts', 0) + g.get('reb', 0) for g in played_games]
        elif stat_type == 'PA':
            return [g.get('pts', 0) + g.get('ast', 0) for g in played_games]
        elif stat_type == 'RA':
            return [g.get('reb', 0) + g.get('ast', 0) for g in played_games]
        elif stat_type in stat_field_map:
            field = stat_field_map[stat_type]
            return [g.get(field, 0) for g in played_games]
        else:
            # Unknown stat type - return empty
            return []
    
    def _get_avg_minutes(self, game_logs: List[Dict], sample_size: int = 10) -> float:
        """Calculate average minutes from recent games."""
        mins_list = []
        for g in game_logs[:sample_size]:
            if not self._did_play(g):
                continue
            mins = g.get('min', '0') or '0'
            if isinstance(mins, str):
                try:
                    mins_val = int(mins.split(':')[0]) if ':' in mins else int(mins)
                except (ValueError, TypeError):
                    continue
            else:
                mins_val = mins
            if mins_val > 0:
                mins_list.append(mins_val)
        return np.mean(mins_list) if mins_list else 0
    
    def qualifies_for_oracle_apex(
        self,
        stat_type: str,
        line: float,
        l20_values: List[float],
        cv: float,
        oracle_pred: float,
        vk_prob: float
    ) -> tuple[bool, str]:
        """
        Check if a prop qualifies for Oracle Apex (Safe Haven).
        
        Returns:
            (qualifies: bool, reason: str)
        """
        if stat_type not in ORACLE_APEX_CONFIG:
            return False, f"UNSUPPORTED_STAT: {stat_type}"
        
        cfg = ORACLE_APEX_CONFIG[stat_type]
        
        # Calculate L20 stats
        l20_hits = sum(1 for v in l20_values if v >= line)
        l20_mean = np.mean(l20_values) if l20_values else 0
        
        # GATE 1: HIT RATE (stat-specific)
        passes_gate1 = l20_hits >= cfg['min_hit_rate']
        
        # REB buffer rule: 14/20 OK if L20 Mean >= Line + 2.5
        if not passes_gate1 and 'relaxed_hit_rate' in cfg:
            if l20_hits >= cfg['relaxed_hit_rate']:
                if l20_mean >= (line + cfg['relaxed_mean_buffer']):
                    passes_gate1 = True
        
        if not passes_gate1:
            return False, f"GATE1_HIT_RATE: {l20_hits}/20 < {cfg['min_hit_rate']}/20"
        
        # GATE 2: CV (Coefficient of Variation)
        if cv > cfg['max_cv']:
            return False, f"GATE2_CV: {cv:.3f} > {cfg['max_cv']}"
        
        # GATE 3: EDGE + PROB
        edge = oracle_pred - line
        if edge < cfg['min_edge']:
            return False, f"GATE3_EDGE: {edge:.1f} < {cfg['min_edge']}"
        
        if vk_prob < cfg['min_prob']:
            return False, f"GATE3_PROB: {vk_prob:.1f}% < {cfg['min_prob']}%"
        
        return True, "ORACLE_APEX_QUALIFIED"
    
    def qualifies_for_front_lines(
        self,
        stat_type: str,
        line: float,
        l20_values: List[float],
        l5_values: List[float],
        cv: float,
        oracle_pred: float,
        vk_prob: float
    ) -> tuple[bool, str]:
        """
        Check if a prop qualifies for Front Lines tier.
        
        Front Lines has relaxed gates compared to Safe Haven:
        - Lower hit rate requirements (14/20 PTS, 12/20 REB/AST)
        - Higher CV tolerance (0.28-0.40)
        - Lower edge/prob requirements (1.5 edge, 55% prob)
        
        Returns:
            (qualifies: bool, reason: str)
        """
        if stat_type not in FRONT_LINES_CONFIG:
            return False, f"UNSUPPORTED_STAT: {stat_type}"
        
        cfg = FRONT_LINES_CONFIG[stat_type]
        
        # Calculate L20 stats
        l20_hits = sum(1 for v in l20_values if v >= line)
        l20_mean = np.mean(l20_values) if l20_values else 0
        l5_mean = np.mean(l5_values) if l5_values else 0
        
        # GATE 1: HIT RATE (stat-specific)
        passes_gate1 = l20_hits >= cfg['min_hit_rate']
        
        # REB buffer rule: 10/20 OK if L5 Mean >= Line + 1.5
        if not passes_gate1 and 'relaxed_hit_rate' in cfg:
            if l20_hits >= cfg['relaxed_hit_rate']:
                # Use L5 mean for Front Lines buffer (more recent form)
                buffer_mean = l5_mean if cfg.get('relaxed_sample_size') == 5 else l20_mean
                if buffer_mean >= (line + cfg['relaxed_mean_buffer']):
                    passes_gate1 = True
        
        if not passes_gate1:
            return False, f"FL_GATE1_HIT_RATE: {l20_hits}/20 < {cfg['min_hit_rate']}/20"
        
        # GATE 2: CV (Coefficient of Variation)
        if cv > cfg['max_cv']:
            return False, f"FL_GATE2_CV: {cv:.3f} > {cfg['max_cv']}"
        
        # GATE 3: EDGE + PROB
        edge = oracle_pred - line
        if edge < cfg['min_edge']:
            return False, f"FL_GATE3_EDGE: {edge:.1f} < {cfg['min_edge']}"
        
        if vk_prob < cfg['min_prob']:
            return False, f"FL_GATE3_PROB: {vk_prob:.1f}% < {cfg['min_prob']}%"
        
        return True, "FRONT_LINES_QUALIFIED"
    
    async def scan_all_props(self) -> Dict[str, Any]:
        """
        Scan ALL props and identify Oracle Apex (Safe Haven) picks.
        
        Returns:
            Dict with apex_picks list and stats
        """
        if not self.vegas_killer_model:
            logger.error("[ORACLE_APEX] Vegas Killer model not set!")
            return {"success": False, "error": "Vegas Killer model not initialized"}
        
        logger.info("[ORACLE_APEX] Starting full prop scan...")
        
        # Load all data
        all_props = await self.live_props.find({}, {"_id": 0}).to_list(length=None)
        cached_players = {p['player_name']: p async for p in self.cached_board.find({}, {"_id": 0})}
        hub_players = {p['display_name']: p async for p in self.master_hub.find({}, {"_id": 0})}
        
        logger.info(f"[ORACLE_APEX] Loaded {len(all_props)} props, {len(cached_players)} cached, {len(hub_players)} hub")
        
        # Normalize stat types
        stat_map = {
            'player_points': 'PTS',
            'player_rebounds': 'REB',
            'player_assists': 'AST',
            'player_points_rebounds_assists': 'PRA',
            'PTS': 'PTS', 'REB': 'REB', 'AST': 'AST', 'PRA': 'PRA'
        }
        
        apex_picks = []
        gate_stats = {stat: {'total': 0, 'g1': 0, 'g2': 0, 'g3': 0, 'passed': 0} 
                      for stat in ORACLE_APEX_CONFIG.keys()}
        skipped = {'no_data': 0, 'insufficient_games': 0, 'no_vk': 0, 'low_minutes': 0}
        
        seen = set()
        
        for prop in all_props:
            player_name = prop.get('player_name', '')
            raw_stat = prop.get('stat_type_extracted', prop.get('market', ''))
            stat_type = stat_map.get(raw_stat, raw_stat)
            line = prop.get('line', 0)
            
            if stat_type not in ORACLE_APEX_CONFIG:
                continue
            
            # Dedupe
            key = f"{player_name}|{stat_type}|{line}"
            if key in seen:
                continue
            seen.add(key)
            
            gate_stats[stat_type]['total'] += 1
            
            # Get player data
            player_data = cached_players.get(player_name) or hub_players.get(player_name)
            if not player_data:
                skipped['no_data'] += 1
                continue
            
            game_logs = player_data.get('bdl_game_logs', [])
            
            # Need at least 20 games
            played_games = [g for g in game_logs if self._did_play(g)]
            if len(played_games) < 20:
                skipped['insufficient_games'] += 1
                continue
            
            # Calculate L20 values
            all_values = self._get_stat_values(game_logs, stat_type)
            if len(all_values) < 20:
                skipped['insufficient_games'] += 1
                continue
            
            l20_values = all_values[:20]
            l10_values = all_values[:10]
            
            # Calculate CV from L10
            l10_mean = np.mean(l10_values)
            l10_std = np.std(l10_values)
            cv = l10_std / l10_mean if l10_mean > 0 else 999
            
            # Get VK prediction
            try:
                opponent = prop.get('away_team') or prop.get('home_team', '')
                result = self.vegas_killer_model.predict(player_name, stat_type, line, opponent_team=opponent)
                
                if not result or result.get('error'):
                    skipped['no_vk'] += 1
                    continue
                
                oracle_pred = result.get('predicted', 0)
                vk_prob_over = result.get('prob_over', 0)
                vk_prob_under = result.get('prob_under', 0)
                
                # The VK model returns percentages (0-100), not decimals (0-1)
                # So we don't need to multiply by 100
                # Only convert if values are in decimal format (0-1 range)
                if vk_prob_over > 0 and vk_prob_over <= 1:
                    vk_prob_over = vk_prob_over * 100
                    vk_prob_under = 100 - vk_prob_over  # Recalculate to ensure they sum to 100
                
                vk_recommendation = result.get('recommendation', '')
            except Exception:
                skipped['no_vk'] += 1
                continue
            
            # Check Oracle Apex qualification
            qualifies, reason = self.qualifies_for_oracle_apex(
                stat_type, line, l20_values, cv, oracle_pred, vk_prob_over
            )
            
            if not qualifies:
                if "GATE1" in reason:
                    gate_stats[stat_type]['g1'] += 1
                elif "GATE2" in reason:
                    gate_stats[stat_type]['g2'] += 1
                elif "GATE3" in reason:
                    gate_stats[stat_type]['g3'] += 1
                continue
            
            # Check minutes
            avg_mins = self._get_avg_minutes(game_logs)
            if avg_mins < MIN_MINUTES:
                skipped['low_minutes'] += 1
                continue
            
            gate_stats[stat_type]['passed'] += 1
            
            # Build apex pick with all required fields
            l5_values = all_values[:5]
            l5_avg = round(np.mean(l5_values), 1) if len(l5_values) >= 5 else None
            l10_avg = round(np.mean(l10_values), 1) if len(l10_values) >= 10 else None
            l20_avg = round(np.mean(l20_values), 1)
            season_avg = round(np.mean(all_values), 1) if all_values else None
            
            l20_hits = sum(1 for v in l20_values if v >= line)
            l10_hits = sum(1 for v in l10_values if v >= line)
            l5_hits = sum(1 for v in l5_values if v >= line)
            
            # Calculate hit rates as percentages (frontend expects h5_rate, h10_rate)
            h5_rate = round((l5_hits / 5) * 100, 1) if len(l5_values) >= 5 else None
            h10_rate = round((l10_hits / 10) * 100, 1) if len(l10_values) >= 10 else None
            h20_rate = round((l20_hits / 20) * 100, 1)
            
            edge = oracle_pred - line
            diff_from_avg = round(season_avg - line, 1) if season_avg else None
            
            apex_picks.append({
                'player_name': player_name,
                'stat_type': stat_type,
                'line': line,
                # L5/L10/L20 averages for frontend
                'l5_avg': l5_avg,
                'l10_avg': l10_avg,
                'l20_avg': l20_avg,
                'season_avg': season_avg,
                'diff_from_avg': diff_from_avg,
                # Hit rates - frontend field names (h5_rate, h10_rate)
                'h5_rate': h5_rate,
                'h10_rate': h10_rate,
                'h20_rate': h20_rate,
                'l5_hits': l5_hits,
                'l10_hits': l10_hits,
                'l20_hits': l20_hits,
                'l5_hit_rate': h5_rate,
                'l10_hit_rate': h10_rate,
                'l20_hit_rate': h20_rate,
                # CV
                'cv': round(cv, 3),
                # Vegas Killer predictions - frontend field names
                'vk_predicted': round(oracle_pred, 1),
                'vk_edge': round(edge, 1),
                'vk_prob_over': round(vk_prob_over, 1),
                'vk_prob_under': round(vk_prob_under, 1),
                'vk_recommendation': vk_recommendation,
                # Legacy field names for backward compat
                'oracle_pred': round(oracle_pred, 1),
                'edge': round(edge, 1),
                'vk_prob': round(vk_prob_over, 1),
                # Minutes
                'avg_mins': round(avg_mins, 1),
                # Prop metadata
                'is_goblin': prop.get('is_goblin', False),
                'is_demon': prop.get('is_demon', False),
                'team': player_data.get('team') or prop.get('home_team') or prop.get('away_team'),
                'opponent': prop.get('away_team') or prop.get('home_team'),
                'game_time': prop.get('commence_time'),
                'headshot_url': player_data.get('headshot_url'),
                'photo_url': player_data.get('photo_url') or player_data.get('headshot_url'),
                # Tier
                'tier': 'safe_haven',
                'tier_label': 'Oracle Apex',
                'synced_at': datetime.now(timezone.utc).isoformat(),
            })
        
        logger.info(f"[ORACLE_APEX] Gate stats: {gate_stats}")
        logger.info(f"[ORACLE_APEX] Skipped: {skipped}")
        logger.info(f"[ORACLE_APEX] Raw apex picks: {len(apex_picks)}")
        
        # Dedupe: Keep lowest line per player+stat
        dedupe_map = {}
        for pick in apex_picks:
            key = f"{pick['player_name']}|{pick['stat_type']}"
            if key not in dedupe_map or pick['line'] < dedupe_map[key]['line']:
                dedupe_map[key] = pick
        
        final_picks = list(dedupe_map.values())
        
        # =================================================================
        # ENRICHMENT: Merge with dg_cached_board to get full context data
        # This ensures intel_suite, active_badges, context data are included
        # =================================================================
        enriched_picks = []
        ferrari_scored = self.db.ferrari_scored
        
        for pick in final_picks:
            player_name = pick['player_name']
            stat_type = pick['stat_type']
            line = pick['line']
            
            # Look up player in cached_board
            player_doc = await self.cached_board.find_one(
                {"player_name": player_name},
                {"_id": 0}
            )
            
            # Also look up ferrari_scored for officiating data
            # First try to match exact stat_type, then fallback to any entry for this player
            # (Referee data is per-game, not per-stat-type)
            ferrari_doc = await ferrari_scored.find_one(
                {"player_name": player_name, "stat_type": stat_type},
                {"_id": 0, "ref_ppg": 1, "ref_ou_pct": 1, "whistle_class": 1, 
                 "whistle_modifier": 1, "crew_chief": 1, "opponent": 1, "game_time": 1}
            )
            
            # Fallback: if no exact stat_type match, get referee data from any entry for this player
            if not ferrari_doc or not ferrari_doc.get('ref_ppg'):
                ferrari_doc_fallback = await ferrari_scored.find_one(
                    {"player_name": player_name, "ref_ppg": {"$exists": True, "$ne": None}},
                    {"_id": 0, "ref_ppg": 1, "ref_ou_pct": 1, "whistle_class": 1, 
                     "whistle_modifier": 1, "crew_chief": 1, "opponent": 1, "game_time": 1}
                )
                if ferrari_doc_fallback:
                    ferrari_doc = ferrari_doc_fallback
                    logger.debug(f"[ORACLE_APEX] Using fallback ref data for {player_name} {stat_type}")
            
            enriched_prop = None
            if player_doc and player_doc.get('props'):
                # Remove any _id fields that MongoDB adds
                if '_id' in player_doc:
                    del player_doc['_id']
                    
                # Find the matching prop by stat_type and line
                for prop in player_doc['props']:
                    # Remove _id from prop if present
                    if '_id' in prop:
                        del prop['_id']
                    if prop.get('stat_type') == stat_type and prop.get('line') == line:
                        enriched_prop = prop
                        break
                
                # If exact line not found, try to find closest line with same stat_type
                if not enriched_prop:
                    same_stat_props = [p for p in player_doc['props'] if p.get('stat_type') == stat_type]
                    if same_stat_props:
                        # Use the one with closest line
                        same_stat_props.sort(key=lambda x: abs(x.get('line', 0) - line))
                        enriched_prop = same_stat_props[0]
                        logger.info(f"[ORACLE_APEX] Using closest line {enriched_prop.get('line')} instead of {line} for {player_name} {stat_type}")
            
            if enriched_prop:
                # Merge Oracle Apex fields into enriched data
                merged = {**enriched_prop}
                merged.update({
                    'player_name': player_name,
                    'line': line,  # Keep the Oracle Apex line (goblin)
                    'tier': 'safe_haven',
                    'tier_label': 'Oracle Apex',
                    'oracle_apex_qualified': True,
                    # Oracle Apex specific metrics
                    'vk_predicted': pick['vk_predicted'],
                    'vk_edge': pick['vk_edge'],
                    'vk_prob_over': pick['vk_prob_over'],
                    'vk_prob_under': pick['vk_prob_under'],
                    'vk_recommendation': pick['vk_recommendation'],
                    'cv': pick['cv'],
                    'l5_avg': pick['l5_avg'],
                    'l10_avg': pick['l10_avg'],
                    'l20_avg': pick['l20_avg'],
                    'season_avg': pick['season_avg'],
                    'h5_rate': pick['h5_rate'],
                    'h10_rate': pick['h10_rate'],
                    'h20_rate': pick['h20_rate'],
                    'l20_hits': pick['l20_hits'],
                    'avg_mins': pick['avg_mins'],
                    # Use enriched data for intel_suite and badges
                    'intel_suite': enriched_prop.get('intel_suite', {}),
                    'active_badges': enriched_prop.get('active_badges', []),
                    'momentum_data': enriched_prop.get('momentum_data'),
                    'vacuum_data': enriched_prop.get('vacuum_data'),
                    'whistle_data': enriched_prop.get('whistle_data'),
                    # Officiating data from ferrari_scored (primary) or player doc/prop
                    'ref_ppg': (ferrari_doc or {}).get('ref_ppg') or player_doc.get('ref_ppg') or enriched_prop.get('ref_ppg'),
                    'ref_ou_pct': (ferrari_doc or {}).get('ref_ou_pct') or player_doc.get('ref_ou_pct') or enriched_prop.get('ref_ou_pct'),
                    'whistle_class': (ferrari_doc or {}).get('whistle_class') or player_doc.get('whistle_class') or enriched_prop.get('whistle_class'),
                    'whistle_modifier': (ferrari_doc or {}).get('whistle_modifier') or player_doc.get('whistle_modifier') or enriched_prop.get('whistle_modifier'),
                    'crew_chief': (ferrari_doc or {}).get('crew_chief') or player_doc.get('crew_chief') or enriched_prop.get('crew_chief'),
                    # Photo URLs from player doc
                    'photo_url': player_doc.get('photo_url') or player_doc.get('headshot_url'),
                    'headshot_url': player_doc.get('headshot_url'),
                    'team': player_doc.get('team'),
                    # Game context
                    'opponent': (ferrari_doc or {}).get('opponent') or player_doc.get('opponent') or enriched_prop.get('opponent'),
                    'game_time': (ferrari_doc or {}).get('game_time') or player_doc.get('game_time') or enriched_prop.get('game_time'),
                })
                enriched_picks.append(merged)
                logger.info(f"[ORACLE_APEX] Enriched: {player_name} {stat_type} {line} with intel_suite={bool(merged.get('intel_suite'))}")
            else:
                # Fallback: use the basic Oracle Apex data
                pick['oracle_apex_qualified'] = True
                pick['intel_suite'] = {}
                pick['active_badges'] = []
                enriched_picks.append(pick)
                logger.warning(f"[ORACLE_APEX] No enriched data for: {player_name} {stat_type} {line}")
        
        enriched_picks.sort(key=lambda x: x.get('vk_edge', x.get('edge', 0)), reverse=True)
        
        logger.info(f"[ORACLE_APEX] Final enriched picks: {len(enriched_picks)}")
        
        return {
            'success': True,
            'apex_picks': enriched_picks,
            'total_scanned': len(seen),
            'gate_stats': gate_stats,
            'skipped': skipped,
        }
    
    async def scan_all_props_for_distribution(self) -> Dict[str, Any]:
        """
        Scan ALL props and return them with Oracle Apex analysis data.
        
        Unlike scan_all_props() which filters for Safe Haven only, this method:
        1. Analyzes EVERY prop with VK model
        2. Calculates Oracle Apex metrics (CV, hit rates, edge)
        3. Returns ALL props with their qualification status
        
        The tier distribution logic then uses this to cascade:
        - Safe Haven: Oracle Apex qualified props
        - Front Lines: Remaining props meeting FL criteria
        - War Zone: Remaining props meeting WZ criteria
        
        Returns:
            Dict with all_props list and analysis stats
        """
        if not self.vegas_killer_model:
            logger.error("[ORACLE_APEX] Vegas Killer model not set!")
            return {"success": False, "error": "Vegas Killer model not initialized"}
        
        logger.info("[ORACLE_APEX] Starting FULL prop scan for tier distribution...")
        
        # Load all data
        all_props = await self.live_props.find({}, {"_id": 0}).to_list(length=None)
        cached_players = {p['player_name']: p async for p in self.cached_board.find({}, {"_id": 0})}
        hub_players = {p['display_name']: p async for p in self.master_hub.find({}, {"_id": 0})}
        
        logger.info(f"[ORACLE_APEX] Loaded {len(all_props)} props, {len(cached_players)} cached, {len(hub_players)} hub")
        
        # Normalize stat types
        stat_map = {
            'player_points': 'PTS',
            'player_rebounds': 'REB',
            'player_assists': 'AST',
            'player_points_rebounds_assists': 'PRA',
            'PTS': 'PTS', 'REB': 'REB', 'AST': 'AST', 'PRA': 'PRA'
        }
        
        analyzed_props = []
        stats = {
            'total': 0, 
            'safe_haven_qualified': 0,
            'has_vk_data': 0,
            'skipped_no_data': 0,
            'skipped_insufficient_games': 0,
            'skipped_no_vk': 0,
        }
        
        seen = set()
        
        for prop in all_props:
            player_name = prop.get('player_name', '')
            raw_stat = prop.get('stat_type_extracted', prop.get('market', ''))
            stat_type = stat_map.get(raw_stat, raw_stat)
            line = prop.get('line', 0)
            
            # Dedupe
            key = f"{player_name}|{stat_type}|{line}"
            if key in seen:
                continue
            seen.add(key)
            
            stats['total'] += 1
            
            # Get player data - prefer cached_board for bdl_game_logs (SSOT for all stats)
            cached_player = cached_players.get(player_name)
            hub_player = hub_players.get(player_name)
            player_data = cached_player or hub_player
            if not player_data:
                stats['skipped_no_data'] += 1
                continue
            
            # SSOT: All stats come from BDL game logs
            game_logs = player_data.get('bdl_game_logs', [])
            played_games = [g for g in game_logs if self._did_play(g)]
            
            # Calculate values from BDL game logs (SSOT)
            all_values = self._get_stat_values(game_logs, stat_type)
            
            if len(played_games) < 5:
                stats['skipped_insufficient_games'] += 1
                continue
            
            # Calculate L values from BDL game logs (SSOT for all averages)
            l20_values = all_values[:20] if len(all_values) >= 20 else all_values
            l10_values = all_values[:10] if len(all_values) >= 10 else all_values
            l5_values = all_values[:5] if len(all_values) >= 5 else all_values
            
            # SSOT: All averages calculated from BDL game logs
            l5_avg = round(np.mean(l5_values), 1) if l5_values else None
            l10_avg = round(np.mean(l10_values), 1) if l10_values else None
            l20_avg = round(np.mean(l20_values), 1) if l20_values else None
            season_avg = round(np.mean(all_values), 1) if all_values else None
            
            # Calculate CV from L10
            l10_mean = np.mean(l10_values) if l10_values else 0
            l10_std = np.std(l10_values) if l10_values else 0
            cv = l10_std / l10_mean if l10_mean > 0 else 999
            
            # Get VK prediction
            oracle_pred = None
            vk_prob_over = 0
            vk_prob_under = 0
            vk_recommendation = ''
            edge = 0
            
            try:
                opponent = prop.get('away_team') or prop.get('home_team', '')
                result = self.vegas_killer_model.predict(player_name, stat_type, line, opponent_team=opponent)
                
                if result and not result.get('error'):
                    oracle_pred = result.get('predicted', 0)
                    vk_prob_over = result.get('prob_over', 0)
                    vk_prob_under = result.get('prob_under', 0)
                    
                    # Handle decimal vs percentage format
                    if vk_prob_over > 0 and vk_prob_over <= 1:
                        vk_prob_over = vk_prob_over * 100
                        vk_prob_under = 100 - vk_prob_over
                    
                    vk_recommendation = result.get('recommendation', '')
                    edge = oracle_pred - line if oracle_pred else 0
                    stats['has_vk_data'] += 1
                else:
                    stats['skipped_no_vk'] += 1
            except Exception as vk_err:
                if stats['total'] < 5:  # Log first few errors
                    logger.warning(f"[ORACLE_APEX] VK predict error for {player_name} {stat_type}: {vk_err}")
                stats['skipped_no_vk'] += 1
            
            # Check Oracle Apex qualification
            oracle_apex_qualified = False
            apex_reason = "NOT_ANALYZED"
            
            if stat_type in ORACLE_APEX_CONFIG and len(l20_values) >= 20 and oracle_pred is not None:
                oracle_apex_qualified, apex_reason = self.qualifies_for_oracle_apex(
                    stat_type, line, l20_values, cv, oracle_pred, vk_prob_over
                )
                
                # Also check minutes
                avg_mins = self._get_avg_minutes(game_logs)
                if oracle_apex_qualified and avg_mins < MIN_MINUTES:
                    oracle_apex_qualified = False
                    apex_reason = "LOW_MINUTES"
            
            if oracle_apex_qualified:
                stats['safe_haven_qualified'] += 1
            
            # Calculate hit rates (always use game log values for hit rate accuracy)
            l20_hits = sum(1 for v in l20_values if v >= line) if l20_values else 0
            l10_hits = sum(1 for v in l10_values if v >= line) if l10_values else 0
            l5_hits = sum(1 for v in l5_values if v >= line) if l5_values else 0
            
            h5_rate = round((l5_hits / len(l5_values)) * 100, 1) if l5_values else None
            h10_rate = round((l10_hits / len(l10_values)) * 100, 1) if l10_values else None
            h20_rate = round((l20_hits / len(l20_values)) * 100, 1) if l20_values else None
            
            avg_mins = self._get_avg_minutes(game_logs)
            
            analyzed_props.append({
                'player_name': player_name,
                'stat_type': stat_type,
                'line': line,
                # Oracle Apex qualification
                'oracle_apex_qualified': oracle_apex_qualified,
                'apex_reason': apex_reason,
                # L averages
                'l5_avg': l5_avg,
                'l10_avg': l10_avg,
                'l20_avg': l20_avg,
                'season_avg': season_avg,
                # Hit rates
                'h5_rate': h5_rate,
                'h10_rate': h10_rate,
                'h20_rate': h20_rate,
                'l5_hits': l5_hits,
                'l10_hits': l10_hits,
                'l20_hits': l20_hits,
                # CV
                'cv': round(cv, 3),
                # VK predictions
                'vk_predicted': round(oracle_pred, 1) if oracle_pred else None,
                'vk_edge': round(edge, 1) if edge else None,
                'vk_prob_over': round(vk_prob_over, 1),
                'vk_prob_under': round(vk_prob_under, 1),
                'vk_recommendation': vk_recommendation,
                # Minutes
                'avg_mins': round(avg_mins, 1),
                # Prop metadata
                'is_goblin': prop.get('is_goblin', False),
                'is_demon': prop.get('is_demon', False),
                'team': player_data.get('team') or prop.get('home_team') or prop.get('away_team'),
                'opponent': prop.get('away_team') or prop.get('home_team'),
                'game_time': prop.get('commence_time'),
                'headshot_url': player_data.get('headshot_url'),
                'photo_url': player_data.get('photo_url') or player_data.get('headshot_url'),
                'synced_at': datetime.now(timezone.utc).isoformat(),
            })
        
        logger.info(f"[ORACLE_APEX] Distribution scan complete: {stats}")
        
        return {
            'success': True,
            'all_props': analyzed_props,
            'stats': stats,
        }
    
    async def build_safe_haven_tier(self) -> List[Dict]:
        """
        Build the Safe Haven tier using Oracle Apex logic.
        
        This replaces the legacy Safe Haven logic with mathematically-proven picks.
        """
        result = await self.scan_all_props()
        
        if not result.get('success'):
            logger.error(f"[ORACLE_APEX] Failed to scan props: {result.get('error')}")
            return []
        
        apex_picks = result.get('apex_picks', [])
        
        # Store to collection
        await self.oracle_apex_collection.delete_many({})
        if apex_picks:
            await self.oracle_apex_collection.insert_many(apex_picks)
        
        logger.info(f"[ORACLE_APEX] Stored {len(apex_picks)} Oracle Apex picks to collection")
        
        return apex_picks
    
    async def build_elite_top_10_tiers(self, all_picks: List[Dict]) -> Dict[str, List[Dict]]:
        """
        NBA ELITE TOP 10 SORTING ENGINE - Sequential Claim Logic
        =========================================================
        
        Implements exclusive tier assignment to ensure NO prop appears in multiple tiers.
        PRESERVES: Blowout Warnings, Injury/Usage, DvP Matchups from existing pipeline.
        
        PROCESS:
        1. Build QUALIFIED POOL: All props passing safety filters with positive true_edge
           - Includes existing NBA intel: blowout_risk, vacuum_data, momentum_data
        2. WAR ZONE claims first: Demons + Standards (DK > +100), sorted by true_edge
        3. SAFE HAVEN claims second: Goblins only, sorted by propvision_true_prob + true_edge
        4. FRONT LINES claims last: Everything remaining, sorted by board_score
        
        CRITICAL: All NBA safety filters (Blowout, Injury/Usage, DvP) feed into the
        Qualified Pool BEFORE the sorting engine assigns them to tiers.
        
        Returns:
            Dict with 'safe_haven', 'front_lines', 'war_zone' lists (each Top 10, exclusive)
        """
        logger.info("=" * 70)
        logger.info("[NBA_ELITE_TOP_10] Starting Sequential Claim Sorting Engine...")
        logger.info(f"[NBA_ELITE_TOP_10] Total Input: {len(all_picks)} props")
        logger.info("=" * 70)
        
        # Track gate statistics
        gate_stats = {
            'total_input': len(all_picks),
            'fail_blowout': 0,
            'fail_hit_rate': 0,
            'fail_cv': 0,
            'fail_actuary_gate': 0,
            'fail_minutes': 0,
            'qualified_pool': 0,
        }
        
        qualified_pool = []
        
        for prop in all_picks:
            # Get basic prop info
            player_name = prop.get("player_name", "Unknown")
            stat_type = (prop.get("stat_type") or prop.get("stat_type_extracted") or "").upper()
            if not stat_type:
                market = prop.get("market", "")
                market_to_stat = {
                    "player_points": "PTS", "player_rebounds": "REB", "player_assists": "AST",
                    "player_threes": "3PM", "player_steals": "STL", "player_blocks": "BLK",
                    "player_turnovers": "TO", "player_points_rebounds_assists": "PRA",
                    "player_points_rebounds": "PR", "player_points_assists": "PA",
                    "player_rebounds_assists": "RA"
                }
                stat_type = market_to_stat.get(market, "")
            
            line = prop.get("line", 0)
            
            # Get prop classification
            is_goblin = prop.get("is_goblin", False)
            is_demon = prop.get("is_demon", False)
            prop_type = "GOBLIN" if is_goblin else ("DEMON" if is_demon else "STANDARD")
            
            # Get DK odds
            dk_odds = prop.get("dk_odds")
            if dk_odds is None:
                sharp_market = prop.get("sharp_market", {})
                dk_odds = (
                    sharp_market.get("draftkings_price") or 
                    prop.get("draftkings_price") or
                    sharp_market.get("sort_price") or
                    prop.get("sort_price") or
                    prop.get("price")  # Fallback to generic price field
                )
            
            # ================================================================
            # PHASE 0: MARKET-FIRST FILTER (dk_odds REQUIRED)
            # ================================================================
            # A prop MUST have non-null, non-zero dk_odds to be eligible
            if MARKET_FIRST_REQUIRED and (dk_odds is None or dk_odds == 0):
                gate_stats['fail_market_first'] = gate_stats.get('fail_market_first', 0) + 1
                continue
            
            # ================================================================
            # SAFETY FILTER 1: Blowout Risk Gate (NBA-SPECIFIC - PRESERVED)
            # ================================================================
            # High blowout risk means star players get benched in 4th quarter
            blowout_risk = prop.get("blowout_risk") or prop.get("intel_suite", {}).get("blowout_risk", {}).get("risk_level", "UNKNOWN")
            if blowout_risk == "HIGH":
                gate_stats['fail_blowout'] += 1
                continue
            
            # ================================================================
            # SAFETY FILTER 2: Minutes Check (NBA-SPECIFIC)
            # ================================================================
            avg_mins = prop.get("avg_mins", 0) or 0
            if avg_mins > 0 and avg_mins < MIN_MINUTES:
                gate_stats['fail_minutes'] += 1
                continue
            
            # ================================================================
            # SAFETY FILTER 3: Hit Rate Extraction (FERRARI FLATTENED FORMAT)
            # ================================================================
            # Ferrari stores hit rates as flat fields: l10_rate, l5_rate, h10_rate, h5_rate
            # Priority: l10_rate > h10_rate > l5_rate > h5_rate
            
            # Direct extraction from Ferrari flattened fields
            l10_rate = prop.get("l10_rate") or prop.get("h10_rate") or 0
            l5_rate = prop.get("l5_rate") or prop.get("h5_rate") or 0
            
            # Fallback to nested hit_rates structure (dg_live_props format) if flat fields are 0
            if l10_rate == 0 and l5_rate == 0:
                hit_rates = prop.get("hit_rates", {})
                if hit_rates:
                    l10_data = hit_rates.get("l10", {})
                    l5_data = hit_rates.get("l5", {})
                    l10_rate_raw = l10_data.get("hit_rate", 0) if isinstance(l10_data, dict) else 0
                    l5_rate_raw = l5_data.get("hit_rate", 0) if isinstance(l5_data, dict) else 0
                    # Convert decimal to percentage if needed
                    l10_rate = l10_rate_raw * 100 if 0 < l10_rate_raw <= 1 else l10_rate_raw
                    l5_rate = l5_rate_raw * 100 if 0 < l5_rate_raw <= 1 else l5_rate_raw
            
            # Calculate true_hit_rate from available data
            if l10_rate > 0:
                true_hit_rate = l10_rate
            elif l5_rate > 0:
                true_hit_rate = l5_rate
            else:
                gate_stats['fail_hit_rate'] += 1
                continue
            
            # Baseline hit rate floor: 40% (relaxed - tiers will apply stricter filters)
            if true_hit_rate < 40.0:
                gate_stats['fail_hit_rate'] += 1
                continue
            
            # ================================================================
            # SAFETY FILTER 4: CV Check (relaxed - tiers apply stricter)
            # ================================================================
            # Ferrari stores CV as l10_std_dev. Calculate CV = std_dev / mean
            cv = prop.get("cv")
            if cv is None or cv == 0:
                # Calculate from std_dev and mean
                l10_std_dev = prop.get("l10_std_dev") or 0
                l10_avg = prop.get("l10_avg") or prop.get("l10_mean") or 1
                if l10_avg > 0 and l10_std_dev > 0:
                    cv = l10_std_dev / l10_avg
                else:
                    cv = 0.5  # Default moderate volatility
            
            # Normalize CV if stored as percentage
            if cv > 1:
                cv = cv / 100.0
            
            # Max CV 0.90 for NBA (higher variance than MLB)
            if cv > 0.90:
                gate_stats['fail_cv'] += 1
                continue
            
            # ================================================================
            # SAFETY FILTER 5: PROPVISION TRUE PROBABILITY & EDGE CALCULATION
            # ================================================================
            # Ferrari pre-calculates true_probability using the 50/50 blend.
            # Use it directly if available, otherwise calculate fresh.
            
            ferrari_true_prob = prop.get("true_probability") or 0
            
            if ferrari_true_prob > 0:
                # Use Ferrari's pre-calculated probability (already 50/50 blended)
                propvision_true_prob = ferrari_true_prob
                # Calculate market_prob for reference
                if dk_odds and dk_odds < 0:
                    market_prob = (abs(dk_odds) / (abs(dk_odds) + 100)) * 100
                else:
                    market_prob = 50.0
            else:
                # Calculate fresh using master probability function
                prob_data = calculate_nba_master_probability(dk_odds, true_hit_rate, prop_type)
                market_prob = prob_data['market_prob']
                propvision_true_prob = prob_data['propvision_true_prob']
            
            # Get casino required win rate based on Goblin Tax curve
            casino_req_rate = get_nba_pp_required_win_rate(dk_odds, prop_type)
            
            # RECALCULATE TRUE EDGE (ensures non-zero values)
            true_edge = propvision_true_prob - casino_req_rate
            
            # Also check Ferrari's pp_edge if true_edge is still low
            ferrari_pp_edge = prop.get("pp_edge") or 0
            if ferrari_pp_edge > true_edge:
                true_edge = ferrari_pp_edge
            
            # KILL SWITCH: Must have positive edge
            if true_edge <= 0.0:
                gate_stats['fail_actuary_gate'] += 1
                continue
            
            # ================================================================
            # PASSED ALL SAFETY FILTERS - Add to Qualified Pool
            # ================================================================
            gate_stats['qualified_pool'] += 1
            
            # Get board scores for sorting (keep original board_score if available)
            original_board_score = prop.get("board_score", 0)
            
            # Calculate tier-specific board scores
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
                'stat_type': stat_type,
                'line': line,
                'dk_odds': dk_odds,
                'price': prop.get('price'),
                'direction': prop.get('direction', 'Over'),
                'market': prop.get('market'),
                
                # Classification
                'prop_type': prop_type,
                'is_goblin': is_goblin,
                'is_demon': is_demon,
                'is_standard': not is_goblin and not is_demon,
                
                # Hit rate & consistency (PRESERVED FROM FERRARI)
                'l10_rate': round(l10_rate, 1),
                'l5_rate': round(l5_rate, 1),
                'h10_rate': prop.get('h10_rate') or round(l10_rate, 1),
                'h5_rate': prop.get('h5_rate') or round(l5_rate, 1),
                'true_hit_rate': round(true_hit_rate, 1),
                'cv': round(cv, 3),
                'l10_std_dev': prop.get('l10_std_dev'),
                'l10_avg': prop.get('l10_avg'),
                'avg_mins': round(avg_mins, 1) if avg_mins else None,
                
                # PropVision math (MASTER FUNCTION - NON-ZERO GUARANTEED)
                'market_prob': round(market_prob, 1),
                'propvision_true_prob': round(propvision_true_prob, 1),
                'true_probability': round(propvision_true_prob, 1),  # Alias for compatibility
                'casino_req_rate': round(casino_req_rate, 1),
                'true_edge': round(true_edge, 1),
                'pp_edge': prop.get('pp_edge') or round(true_edge, 1),
                
                # Board scores for each tier
                'sh_board_score': round(sh_board_score, 1),
                'fl_board_score': round(fl_board_score, 1),
                'wz_board_score': round(wz_board_score, 1),
                'board_score': round(original_board_score, 1) if original_board_score else round(fl_board_score, 1),
                'ferrari_power_score': prop.get('ferrari_power_score') or original_board_score,
                
                # ====== PRESERVED NBA INTEL (Blowout, Usage, DvP) - FULL COPY ======
                'blowout_risk': blowout_risk,
                'intel_suite': prop.get('intel_suite') or {},
                'active_badges': prop.get('active_badges') or [],
                'momentum_data': prop.get('momentum_data'),
                'vacuum_data': prop.get('vacuum_data'),
                'whistle_data': prop.get('whistle_data') or (prop.get('intel_suite') or {}).get('whistle_data'),
                'whistle_class': prop.get('whistle_class'),
                'whistle_modifier': prop.get('whistle_modifier'),
                'momentum_modifier': prop.get('momentum_modifier'),
                'vacuum_modifier': prop.get('vacuum_modifier'),
                
                # V7 components (PRESERVED)
                'v7_components': prop.get('v7_components') or prop.get('components'),
                'v7_confidence': prop.get('v7_confidence'),
                'v7_soft_penalties': prop.get('v7_soft_penalties'),
                
                # Carry forward additional Ferrari fields
                'season_avg': prop.get('season_avg'),
                'l5_avg': prop.get('l5_avg'),
                'l10_median': prop.get('l10_median'),
                'l10_mode': prop.get('l10_mode'),
                'vk_predicted': prop.get('vk_predicted'),
                'vk_edge': prop.get('vk_edge'),
                'vk_prob_over': prop.get('vk_prob_over'),
                'is_vision_enriched': prop.get('is_vision_enriched'),
                
                # Sharp market data
                'draftkings_price': prop.get('draftkings_price'),
                'fanduel_price': prop.get('fanduel_price'),
                'sort_price': prop.get('sort_price'),
                'sort_source': prop.get('sort_source'),
                
                # Risk flags
                'hook_risk': prop.get('hook_risk'),
                'trap_risk': prop.get('trap_risk'),
                'suspect_line_bait': prop.get('suspect_line_bait'),
                
                # Tier assignment (will be set per-tier)
                'dk_tier': prop.get('dk_tier'),
                'tier_label': prop.get('tier_label'),
                
                # Timestamp
                'synced_at': datetime.now(timezone.utc).isoformat(),
            }
            
            # ================================================================
            # VK MODEL ENFORCEMENT - MANDATORY HANDSHAKE
            # ================================================================
            # Ensure VK fields are populated (never None)
            if qualified_prop.get('vk_prob_over') is None or qualified_prop.get('vk_verdict') is None:
                # Calculate VK model
                season_avg = prop.get('season_avg') or prop.get('l10_avg') or line
                vk_result = calculate_vk_model(
                    predicted_value=prop.get('vk_predicted') or season_avg,
                    line=line,
                    dk_odds=dk_odds,
                    season_avg=season_avg,
                    require_market=True
                )
                
                if not vk_result.is_valid:
                    gate_stats['fail_vk_model'] = gate_stats.get('fail_vk_model', 0) + 1
                    continue
                
                qualified_prop['vk_prob_over'] = vk_result.vk_prob_over
                qualified_prop['vk_prob_under'] = vk_result.vk_prob_under
                qualified_prop['vk_verdict'] = vk_result.vk_verdict
                qualified_prop['vk_edge'] = vk_result.vk_edge
                qualified_prop['vk_recommendation'] = vk_result.vk_recommendation
                qualified_prop['vk_confidence'] = vk_result.confidence_score
            
            qualified_pool.append(qualified_prop)
        
        # Log pool statistics
        logger.info("[NBA_ELITE_TOP_10] Safety Filter Results:")
        logger.info(f"  Failed Blowout (HIGH risk): {gate_stats['fail_blowout']}")
        logger.info(f"  Failed Minutes (<{MIN_MINUTES}): {gate_stats['fail_minutes']}")
        logger.info(f"  Failed Hit Rate (<40%): {gate_stats['fail_hit_rate']}")
        logger.info(f"  Failed CV (>0.90): {gate_stats['fail_cv']}")
        logger.info(f"  *** KILLED BY ACTUARY GATE (<=0%): {gate_stats['fail_actuary_gate']} ***")
        logger.info(f"  QUALIFIED POOL SIZE: {gate_stats['qualified_pool']}")
        
        # ====================================================================
        # STEP 2A: WAR ZONE CLAIMS FIRST (High-Alpha Demons)
        # ====================================================================
        # Demons + Standards with DK > +100, sorted by true_edge DESC
        
        war_zone_candidates = [
            p for p in qualified_pool
            if p['prop_type'] == 'DEMON' or (p['prop_type'] == 'STANDARD' and (p['dk_odds'] or 0) > 100)
        ]
        
        # Additional War Zone filter: true_edge >= 8% for high-alpha (slightly relaxed vs MLB's 10%)
        war_zone_candidates = [p for p in war_zone_candidates if p['true_edge'] >= 8.0]
        
        # Sort by true_edge DESC
        war_zone_candidates.sort(key=lambda x: x['true_edge'], reverse=True)
        
        # Dedupe by player+stat
        wz_seen = set()
        war_zone_picks = []
        for p in war_zone_candidates:
            key = f"{p['player_name']}|{p['stat_type']}"
            if key not in wz_seen and len(war_zone_picks) < 10:
                wz_seen.add(key)
                p['tier'] = 'war_zone'
                p['tier_label'] = 'NBA War Zone (Elite 10)'
                p['board_score'] = p['wz_board_score']
                war_zone_picks.append(p)
        
        # REMOVE claimed props from pool
        claimed_keys = {f"{p['player_name']}|{p['stat_type']}" for p in war_zone_picks}
        remaining_pool = [p for p in qualified_pool if f"{p['player_name']}|{p['stat_type']}" not in claimed_keys]
        
        logger.info(f"[NBA_ELITE_TOP_10] WAR ZONE claimed: {len(war_zone_picks)} picks")
        logger.info(f"  Remaining pool: {len(remaining_pool)}")
        
        # ====================================================================
        # STEP 2B: SAFE HAVEN CLAIMS SECOND (Elite Stability)
        # ====================================================================
        # Goblins only, sorted by propvision_true_prob + true_edge DESC
        
        safe_haven_candidates = [
            p for p in remaining_pool
            if p['prop_type'] == 'GOBLIN'
        ]
        
        # Additional Safe Haven filters: 
        # - HR >= 60% (trust gate)
        # - CV <= 0.35 (NBA-specific tighter CV)
        # - vk_prob_over >= 70% (MLR SUPREMACY - predictive model MUST show strong confidence)
        safe_haven_candidates = [
            p for p in safe_haven_candidates 
            if p['true_hit_rate'] >= 60.0 
            and p['cv'] <= 0.35
            and p.get('vk_prob_over', 0) >= 70.0  # MLR SUPREMACY: Reject < 70%
        ]
        
        # PRIMARY SORT: vk_prob_over DESC (MLR predictive model is king)
        # Historical L10 hit rate is NO LONGER a sort key
        safe_haven_candidates.sort(
            key=lambda x: x.get('vk_prob_over', 0), 
            reverse=True
        )
        
        logger.info(f"[NBA_ELITE_TOP_10] Safe Haven after MLR filter: {len(safe_haven_candidates)} candidates (vk_prob_over >= 70%)")
        
        # Dedupe by player+stat
        sh_seen = set()
        safe_haven_picks = []
        for p in safe_haven_candidates:
            key = f"{p['player_name']}|{p['stat_type']}"
            if key not in sh_seen and len(safe_haven_picks) < 10:
                sh_seen.add(key)
                p['tier'] = 'safe_haven'
                p['tier_label'] = 'NBA Safe Haven (Elite 10 - MLR Sorted)'
                p['board_score'] = p.get('vk_prob_over', 0)  # Use vk_prob_over as board_score
                safe_haven_picks.append(p)
        
        # REMOVE claimed props from pool
        claimed_keys = {f"{p['player_name']}|{p['stat_type']}" for p in safe_haven_picks}
        remaining_pool = [p for p in remaining_pool if f"{p['player_name']}|{p['stat_type']}" not in claimed_keys]
        
        logger.info(f"[NBA_ELITE_TOP_10] SAFE HAVEN claimed: {len(safe_haven_picks)} picks")
        logger.info(f"  Remaining pool: {len(remaining_pool)}")
        
        # ====================================================================
        # STEP 3: FRONT LINES (Sort by vk_edge DESC - MLR Arbitrage)
        # ====================================================================
        # Everything remaining, sorted by vk_edge (MLR vs market disagreement)
        
        front_lines_candidates = remaining_pool.copy()
        
        # Additional Front Lines filters: HR >= 50%, CV <= 0.50
        front_lines_candidates = [
            p for p in front_lines_candidates 
            if p['true_hit_rate'] >= 50.0 and p['cv'] <= 0.50
        ]
        
        # PRIMARY SORT: vk_edge DESC (MLR arbitrage - biggest disagreements with market)
        front_lines_candidates.sort(key=lambda x: x.get('vk_edge', 0), reverse=True)
        
        # Dedupe by player+stat
        fl_seen = set()
        front_lines_picks = []
        for p in front_lines_candidates:
            key = f"{p['player_name']}|{p['stat_type']}"
            if key not in fl_seen and len(front_lines_picks) < 10:
                fl_seen.add(key)
                p['tier'] = 'front_lines'
                p['tier_label'] = 'NBA Front Lines (Elite 10 - vk_edge Sorted)'
                p['board_score'] = p.get('vk_edge', 0)  # Use vk_edge as board_score
                front_lines_picks.append(p)
        
        logger.info(f"[NBA_ELITE_TOP_10] FRONT LINES claimed: {len(front_lines_picks)} picks")
        
        # ====================================================================
        # LOG FINAL RESULTS
        # ====================================================================
        logger.info("=" * 70)
        logger.info("[NBA_ELITE_TOP_10] FINAL TIER ASSIGNMENTS (Exclusive - No Duplicates):")
        logger.info("=" * 70)
        
        logger.info(f"\n[WAR ZONE] Top {len(war_zone_picks)} High-Alpha Plays:")
        for i, p in enumerate(war_zone_picks, 1):
            logger.info(f"  {i}. {p['player_name']} - {p['stat_type']} [{p['prop_type']}] | "
                       f"TRUE EDGE: +{p['true_edge']:.1f}% | PropVision: {p['propvision_true_prob']}%")
        
        logger.info(f"\n[SAFE HAVEN] Top {len(safe_haven_picks)} Stability Plays:")
        for i, p in enumerate(safe_haven_picks, 1):
            logger.info(f"  {i}. {p['player_name']} - {p['stat_type']} [{p['prop_type']}] | "
                       f"PropVision: {p['propvision_true_prob']}% | TRUE EDGE: +{p['true_edge']:.1f}%")
        
        logger.info(f"\n[FRONT LINES] Top {len(front_lines_picks)} Universal Value Plays:")
        for i, p in enumerate(front_lines_picks, 1):
            logger.info(f"  {i}. {p['player_name']} - {p['stat_type']} [{p['prop_type']}] | "
                       f"Board: {p['board_score']} | TRUE EDGE: +{p['true_edge']:.1f}%")
        
        # Verify no duplicates
        all_keys = set()
        for tier_name, picks in [('WAR_ZONE', war_zone_picks), ('SAFE_HAVEN', safe_haven_picks), ('FRONT_LINES', front_lines_picks)]:
            for p in picks:
                key = f"{p['player_name']}|{p['stat_type']}"
                if key in all_keys:
                    logger.error(f"[NBA_ELITE_TOP_10] DUPLICATE FOUND: {key}")
                all_keys.add(key)
        
        logger.info(f"\n[NBA_ELITE_TOP_10] Total unique picks: {len(all_keys)} (verified no duplicates)")
        logger.info("=" * 70)
        
        return {
            'war_zone': war_zone_picks,
            'safe_haven': safe_haven_picks,
            'front_lines': front_lines_picks,
        }


# Singleton instance
_oracle_apex_service = None

def get_oracle_apex_service(db, vegas_killer_model=None):
    """Get or create the Oracle Apex service singleton."""
    global _oracle_apex_service
    if _oracle_apex_service is None:
        _oracle_apex_service = OracleApexService(db, vegas_killer_model)
    elif vegas_killer_model:
        _oracle_apex_service.set_vegas_killer_model(vegas_killer_model)
    return _oracle_apex_service
