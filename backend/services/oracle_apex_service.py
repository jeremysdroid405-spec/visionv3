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

logger = logging.getLogger(__name__)

# =============================================================================
# ORACLE APEX CONFIGURATION
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
        'min_edge': 1.5,  # Lower edge for REB
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

# Minimum minutes for volume check
MIN_MINUTES = 22


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
                if vk_prob_over <= 1:
                    vk_prob_over = vk_prob_over * 100
                if vk_prob_under <= 1:
                    vk_prob_under = vk_prob_under * 100
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
            
            edge = oracle_pred - line
            
            apex_picks.append({
                'player_name': player_name,
                'stat_type': stat_type,
                'line': line,
                # L5/L10/L20 averages for frontend
                'l5_avg': l5_avg,
                'l10_avg': l10_avg,
                'l20_avg': l20_avg,
                'season_avg': season_avg,
                # Hit rates
                'l5_hits': l5_hits,
                'l10_hits': l10_hits,
                'l20_hits': l20_hits,
                'l5_hit_rate': round((l5_hits / 5) * 100, 1) if len(l5_values) >= 5 else None,
                'l10_hit_rate': round((l10_hits / 10) * 100, 1) if len(l10_values) >= 10 else None,
                'l20_hit_rate': round((l20_hits / 20) * 100, 1),
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
        final_picks.sort(key=lambda x: x['edge'], reverse=True)
        
        logger.info(f"[ORACLE_APEX] Final picks after dedupe: {len(final_picks)}")
        
        return {
            'success': True,
            'apex_picks': final_picks,
            'total_scanned': len(seen),
            'gate_stats': gate_stats,
            'skipped': skipped,
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
