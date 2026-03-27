"""
Parlay Builder Service - High-Level Parlay Generation
======================================================
Extracted from services.engines.demon_goblin_engine.py for modularity.

Handles:
- Big Money Builder parlay generation (demon-based)
- Goblin Recon parlay generation (goblin-based)
- PrizePicks 2-Team Rule compliance
- Game correlation and diversification
"""
from typing import Dict, List, Optional, Any, Tuple, Set
from datetime import datetime, timezone
import logging

from services.parlay_service import (
    calculate_live_payout,
    calculate_weighted_parlay_probability,
    build_correlated_parlay
)

logger = logging.getLogger(__name__)

# Stat type mapping
STAT_TYPE_MAP = {
    "player_points": "PTS",
    "player_rebounds": "REB",
    "player_assists": "AST",
    "player_threes": "3PM",
    "player_blocks": "BLK",
    "player_steals": "STL",
    "player_turnovers": "TO",
    "player_points_rebounds": "P+R",
    "player_points_assists": "P+A",
    "player_rebounds_assists": "R+A",
    "player_points_rebounds_assists": "PRA",
}


class ParlayBuilderService:
    """Service for generating parlay tickets"""
    
    def __init__(self, db):
        self.db = db
        self.parlay_builder = db.dg_parlay_builder
        self.goblin_recon = db.dg_goblin_recon
    
    def _extract_stat_type(self, market: str) -> str:
        """Extract stat type from market name"""
        market = market.replace("_alternate", "")
        return STAT_TYPE_MAP.get(market, "")
    
    # ==================== TWO-TEAM RULE HELPERS ====================
    
    def _get_multi_team_picks(
        self, 
        candidates: List[Dict], 
        count: int
    ) -> Tuple[List[Dict], bool, int]:
        """
        Select picks enforcing PrizePicks 2-Team minimum rule.
        
        Logic:
        - Pick #1: Top-ranked player
        - Pick #2: Next highest-ranked from DIFFERENT team
        - Picks #3+: Any team (2-team minimum established)
        
        Returns: (picks, is_valid, team_count)
        """
        if len(candidates) < count:
            return [], False, 0
        
        picks = []
        used_players: Set[str] = set()
        teams_used: Set[str] = set()
        
        # Pick #1: Best available
        pick_1 = candidates[0]
        picks.append(pick_1)
        used_players.add(pick_1["player_name"])
        teams_used.add(pick_1["team"])
        
        # Pick #2: MUST be from different team
        pick_2 = None
        for c in candidates[1:]:
            if c["player_name"] not in used_players and c["team"] != pick_1["team"]:
                pick_2 = c
                break
        
        if not pick_2:
            # Fallback: Any different player
            for c in candidates[1:]:
                if c["player_name"] not in used_players:
                    pick_2 = c
                    break
        
        if pick_2:
            picks.append(pick_2)
            used_players.add(pick_2["player_name"])
            teams_used.add(pick_2["team"])
        
        # Fill remaining slots
        for c in candidates:
            if len(picks) >= count:
                break
            if c["player_name"] not in used_players:
                picks.append(c)
                used_players.add(c["player_name"])
                teams_used.add(c["team"])
        
        is_valid = len(teams_used) >= 2
        return picks[:count], is_valid, len(teams_used)
    
    def _get_opponent_paired_picks(
        self, 
        candidates: List[Dict], 
        count: int
    ) -> Tuple[List[Dict], bool, int, bool]:
        """
        For 4+ pick parlays, try to include opponent pairs from same game.
        
        Returns: (picks, is_valid, team_count, has_opponent_pair)
        """
        if len(candidates) < count:
            return [], False, 0, False
        
        picks = []
        used_players: Set[str] = set()
        teams_used: Set[str] = set()
        has_opponent_pair = False
        
        # Find best opponent pair
        opponent_pairs = []
        for i, d1 in enumerate(candidates[:20]):
            for d2 in candidates[i+1:30]:
                if d1["player_name"] != d2["player_name"]:
                    if d1.get("game_key") == d2.get("game_key") and d1["team"] != d2["team"]:
                        combined_prob = (d1.get("hit_probability", 0) + d2.get("hit_probability", 0)) / 2
                        opponent_pairs.append({
                            "pair": [d1, d2],
                            "combined_prob": combined_prob,
                            "game_key": d1.get("game_key")
                        })
        
        opponent_pairs.sort(key=lambda x: x["combined_prob"], reverse=True)
        
        if opponent_pairs:
            best_pair = opponent_pairs[0]["pair"]
            picks.extend(best_pair)
            for p in best_pair:
                used_players.add(p["player_name"])
                teams_used.add(p["team"])
            has_opponent_pair = True
        else:
            # No opponent pair, use standard 2-team logic
            pick_1 = candidates[0]
            picks.append(pick_1)
            used_players.add(pick_1["player_name"])
            teams_used.add(pick_1["team"])
            
            for d in candidates[1:]:
                if d["player_name"] not in used_players and d["team"] != pick_1["team"]:
                    picks.append(d)
                    used_players.add(d["player_name"])
                    teams_used.add(d["team"])
                    break
        
        # Fill remaining slots
        for d in candidates:
            if len(picks) >= count:
                break
            if d["player_name"] not in used_players:
                picks.append(d)
                used_players.add(d["player_name"])
                teams_used.add(d["team"])
        
        is_valid = len(teams_used) >= 2
        return picks[:count], is_valid, len(teams_used), has_opponent_pair
    
    def _get_diversified_picks(
        self, 
        candidates: List[Dict], 
        count: int, 
        game_groups: Dict
    ) -> Tuple[List[Dict], bool, int]:
        """
        Get diversified picks with 2-Team Rule enforcement.
        Prioritizes picks from different games AND different teams.
        """
        if len(candidates) < count:
            return [], False, 0
        
        picks = []
        used_players: Set[str] = set()
        used_games: Set[str] = set()
        teams_used: Set[str] = set()
        
        # Pick #1
        if candidates:
            pick_1 = candidates[0]
            picks.append(pick_1)
            used_players.add(pick_1["player_name"])
            teams_used.add(pick_1["team"])
            if pick_1.get("game_key"):
                used_games.add(pick_1["game_key"])
        
        # Pick #2: Different team AND different game if possible
        pick_2 = None
        for c in candidates[1:]:
            if c["player_name"] not in used_players and c["team"] != picks[0]["team"]:
                game = c.get("game_key", "")
                if game and game not in used_games:
                    pick_2 = c
                    break
        
        if not pick_2:
            for c in candidates[1:]:
                if c["player_name"] not in used_players and c["team"] != picks[0]["team"]:
                    pick_2 = c
                    break
        
        if pick_2:
            picks.append(pick_2)
            used_players.add(pick_2["player_name"])
            teams_used.add(pick_2["team"])
            if pick_2.get("game_key"):
                used_games.add(pick_2["game_key"])
        
        # Remaining picks: Prioritize different games
        for c in candidates:
            if len(picks) >= count:
                break
            if c["player_name"] not in used_players:
                game = c.get("game_key", "")
                if not game or game not in used_games:
                    picks.append(c)
                    used_players.add(c["player_name"])
                    teams_used.add(c["team"])
                    if game:
                        used_games.add(game)
        
        # Final fill if needed
        for c in candidates:
            if len(picks) >= count:
                break
            if c["player_name"] not in used_players:
                picks.append(c)
                used_players.add(c["player_name"])
                teams_used.add(c["team"])
        
        is_valid = len(teams_used) >= 2
        return picks[:count], is_valid, len(teams_used)
    
    def _validate_parlays(self, parlays: Dict) -> Dict:
        """Remove any parlays that don't meet 2-team requirement"""
        validated = {}
        for key, parlay in parlays.items():
            picks = parlay.get("picks", [])
            if len(picks) >= 2:
                teams = set(p.get("team", "") for p in picks)
                if len(teams) >= 2:
                    validated[key] = parlay
                else:
                    logger.warning(f"[GUARDRAIL] Rejected {key}: All picks from same team ({teams})")
            else:
                validated[key] = parlay
        return validated
    
    # ==================== BIG MONEY BUILDER ====================
    
    async def build_parlay_builder(
        self, 
        players_dict: Dict[str, Dict], 
        sync_time: datetime
    ) -> Dict[str, Any]:
        """
        Build Big Money Builder parlays (demon-based).
        
        PRIZEPICKS COMPLIANCE: Minimum 2 teams required.
        """
        logger.info("[PARLAY BUILDER] Generating HIGH-PROBABILITY parlays...")
        
        high_prob_demons = []
        
        for player_name, player_data in players_dict.items():
            if player_data is None or not isinstance(player_data, dict):
                continue
            
            demons = player_data.get("demons", [])
            team = player_data.get("team", "")
            
            for demon in demons:
                if demon is None:
                    continue
                
                hit_rates = demon.get("hit_rates", {}) or {}
                h10_data = hit_rates.get("l10", {}) or {}
                h5_data = hit_rates.get("l5", {}) or {}
                season_data = hit_rates.get("season", {}) or {}
                
                h10 = h10_data.get("hit_rate", 0)
                h5 = h5_data.get("hit_rate", 0)
                h10_games = h10_data.get("total_games", 0)
                h10_over = h10_data.get("games_over", 0)
                h5_over = h5_data.get("games_over", 0)
                h5_games = h5_data.get("total_games", 0)
                season_avg = season_data.get("avg", 0)
                
                base_prob = (h10 * 0.6) + (h5 * 0.4)
                has_real_data = h10_games > 0 or h5_games > 0
                
                if not has_real_data or base_prob < 0.50:
                    continue
                
                heat_boost = 1.10 if (h5_games > 0 and h5_over >= 3) else 1.0
                whale_score = base_prob * heat_boost
                
                home_team = demon.get("home_team", "")
                away_team = demon.get("away_team", "")
                game_key = f"{away_team}@{home_team}" if home_team and away_team else ""
                opponent_team = away_team if team == home_team else home_team
                
                demon_entry = {
                    "player_name": player_name,
                    "team": team,
                    "opponent_team": opponent_team,
                    "nba_id": player_data.get("nba_id"),
                    "photo_url": player_data.get("photo_url"),
                    "stat_type": self._extract_stat_type(demon.get("market", "")),
                    "line": demon.get("line", 0),
                    "direction": demon.get("direction", "Over"),
                    "h10_rate": round(h10 * 100, 1),
                    "h5_rate": round(h5 * 100, 1),
                    "h10_over": h10_over,
                    "h10_games": h10_games,
                    "h5_over": h5_over,
                    "h5_games": h5_games,
                    "season_avg": season_avg,
                    "whale_score": round(whale_score, 4),
                    "hit_probability": round(base_prob * 100, 1),
                    "has_heat_boost": heat_boost > 1,
                    "game_key": game_key,
                    "home_team": home_team,
                    "away_team": away_team,
                    "price": demon.get("price", 100),
                    "is_demon": True,
                    "standard_line": demon.get("standard_line", demon.get("line", 0) * 0.85)
                }
                
                high_prob_demons.append(demon_entry)
        
        high_prob_demons.sort(key=lambda x: x["hit_probability"], reverse=True)
        logger.info(f"[PARLAY BUILDER] Found {len(high_prob_demons)} high-probability demons")
        
        parlays = {}
        
        def calculate_live_payout_internal(picks: List[Dict]) -> Dict:
            payout_result = calculate_live_payout(picks)
            return {
                "estimated_payout": payout_result.get("estimated_payout", 0),
                "payout_display": payout_result.get("payout_display", "0x"),
                "cumulative_modifier": payout_result.get("cumulative_modifier", 1.0),
                "base_multiplier": payout_result.get("base_multiplier", 3.0),
                "asset_breakdown": payout_result.get("asset_breakdown", {}),
                "legs": payout_result.get("legs", [])
            }
        
        # 2-PICK
        if len(high_prob_demons) >= 2:
            picks_2, is_valid, team_count = self._get_multi_team_picks(high_prob_demons, 2)
            if picks_2:
                combined_prob = calculate_weighted_parlay_probability(picks_2)
                payout_data = calculate_live_payout_internal(picks_2)
                parlays["2_pick"] = {
                    "name": "Double Up",
                    "picks": picks_2,
                    "pick_count": 2,
                    "combined_probability": combined_prob,
                    "estimated_payout": payout_data["estimated_payout"],
                    "payout_display": payout_data["payout_display"],
                    "base_multiplier": payout_data["base_multiplier"],
                    "cumulative_modifier": payout_data["cumulative_modifier"],
                    "asset_breakdown": payout_data["asset_breakdown"],
                    "description": "Top 2 highest-probability demons",
                    "lineup_valid": is_valid,
                    "team_count": team_count,
                    "lineup_status": "Valid (Multi-Team)" if is_valid else "INVALID (Single Team)"
                }
        
        # 3-PICK
        if len(high_prob_demons) >= 3:
            picks_3, is_valid, team_count = self._get_multi_team_picks(high_prob_demons, 3)
            if picks_3:
                combined_prob = calculate_weighted_parlay_probability(picks_3)
                payout_data = calculate_live_payout_internal(picks_3)
                parlays["3_pick"] = {
                    "name": "Triple Threat",
                    "picks": picks_3,
                    "pick_count": 3,
                    "combined_probability": combined_prob,
                    "estimated_payout": payout_data["estimated_payout"],
                    "payout_display": payout_data["payout_display"],
                    "base_multiplier": payout_data["base_multiplier"],
                    "cumulative_modifier": payout_data["cumulative_modifier"],
                    "asset_breakdown": payout_data["asset_breakdown"],
                    "description": "Top 3 highest-probability demons",
                    "lineup_valid": is_valid,
                    "team_count": team_count,
                    "lineup_status": "Valid (Multi-Team)" if is_valid else "INVALID (Single Team)"
                }
        
        # 4-PICK with opponent pairing
        if len(high_prob_demons) >= 4:
            picks_4, is_valid, team_count, has_pair = self._get_opponent_paired_picks(high_prob_demons, 4)
            if picks_4:
                combined_prob = calculate_weighted_parlay_probability(picks_4)
                payout_data = calculate_live_payout_internal(picks_4)
                status = "Valid (Opponent Pair)" if has_pair else ("Valid (Multi-Team)" if is_valid else "INVALID")
                
                parlays["4_pick"] = {
                    "name": "Power Play",
                    "picks": picks_4,
                    "pick_count": 4,
                    "combined_probability": combined_prob,
                    "estimated_payout": payout_data["estimated_payout"],
                    "payout_display": payout_data["payout_display"],
                    "base_multiplier": payout_data["base_multiplier"],
                    "cumulative_modifier": payout_data["cumulative_modifier"],
                    "asset_breakdown": payout_data["asset_breakdown"],
                    "description": "4 picks with opponent correlation" if has_pair else "Top 4 demons",
                    "lineup_valid": is_valid,
                    "team_count": team_count,
                    "has_opponent_pair": has_pair,
                    "lineup_status": status
                }
        
        # 5-PICK
        if len(high_prob_demons) >= 5:
            picks_5, is_valid, team_count, has_pair = self._get_opponent_paired_picks(high_prob_demons, 5)
            if picks_5:
                combined_prob = calculate_weighted_parlay_probability(picks_5)
                payout_data = calculate_live_payout_internal(picks_5)
                status = "Valid (Opponent Pair)" if has_pair else ("Valid (Multi-Team)" if is_valid else "INVALID")
                
                parlays["5_pick"] = {
                    "name": "Heavy Hitter",
                    "picks": picks_5,
                    "pick_count": 5,
                    "combined_probability": combined_prob,
                    "estimated_payout": payout_data["estimated_payout"],
                    "payout_display": payout_data["payout_display"],
                    "base_multiplier": payout_data["base_multiplier"],
                    "cumulative_modifier": payout_data["cumulative_modifier"],
                    "asset_breakdown": payout_data["asset_breakdown"],
                    "description": "5 picks with game correlation" if has_pair else "Top 5 demons",
                    "lineup_valid": is_valid,
                    "team_count": team_count,
                    "has_opponent_pair": has_pair,
                    "lineup_status": status
                }
        
        # 6-PICK (Jackpot)
        if len(high_prob_demons) >= 6:
            picks_6, is_valid, team_count, has_pair = self._get_opponent_paired_picks(high_prob_demons, 6)
            if picks_6:
                combined_prob = calculate_weighted_parlay_probability(picks_6)
                payout_data = calculate_live_payout_internal(picks_6)
                status = "Valid (Opponent Pair)" if has_pair else ("Valid (Multi-Team)" if is_valid else "INVALID")
                
                parlays["6_pick"] = {
                    "name": "Jackpot",
                    "picks": picks_6,
                    "pick_count": 6,
                    "combined_probability": combined_prob,
                    "estimated_payout": payout_data["estimated_payout"],
                    "payout_display": payout_data["payout_display"],
                    "base_multiplier": payout_data["base_multiplier"],
                    "cumulative_modifier": payout_data["cumulative_modifier"],
                    "asset_breakdown": payout_data["asset_breakdown"],
                    "description": "6 picks - MAX PAYOUT!" if has_pair else "Top 6 demons - MAX PAYOUT!",
                    "lineup_valid": is_valid,
                    "team_count": team_count,
                    "has_opponent_pair": has_pair,
                    "lineup_status": status
                }
        
        # Validate and store
        parlays = self._validate_parlays(parlays)
        valid_count = sum(1 for p in parlays.values() if p.get("lineup_valid", False))
        
        await self.parlay_builder.delete_many({})
        parlay_doc = {
            "parlays": parlays,
            "total_demons_analyzed": len(high_prob_demons),
            "min_probability_threshold": "50%",
            "valid_lineups": valid_count,
            "total_lineups": len(parlays),
            "synced_at": sync_time.isoformat()
        }
        await self.parlay_builder.insert_one(parlay_doc)
        
        logger.info(f"[PARLAY BUILDER] Generated {len(parlays)} parlay types ({valid_count} valid)")
        
        return {
            "success": True,
            "parlays_count": len(parlays),
            "valid_count": valid_count,
            "demons_analyzed": len(high_prob_demons)
        }
    
    # ==================== GOBLIN RECON ====================
    
    async def build_goblin_recon(
        self, 
        players_dict: Dict[str, Dict], 
        sync_time: datetime
    ) -> Dict[str, Any]:
        """
        Build Goblin Recon parlays (goblin-based, high consistency).
        
        Threshold: 88%+ weighted hit rate
        """
        logger.info("[GOBLIN RECON] Mining for high-consistency Goblin parlays...")
        
        recon_candidates = []
        game_groups = {}
        
        for player_name, player_data in players_dict.items():
            if player_data is None:
                continue
            
            goblins = player_data.get("goblins", [])
            team = player_data.get("team", "")
            
            for goblin in goblins:
                if goblin is None:
                    continue
                
                hit_rates = goblin.get("hit_rates", {}) or {}
                h10_data = hit_rates.get("l10", {}) or {}
                h5_data = hit_rates.get("l5", {}) or {}
                season_data = hit_rates.get("season", {}) or {}
                
                h10 = h10_data.get("hit_rate", 0)
                h5 = h5_data.get("hit_rate", 0)
                h10_games = h10_data.get("total_games", 0)
                h10_over = h10_data.get("games_over", 0)
                h5_games = h5_data.get("total_games", 0)
                h5_over = h5_data.get("games_over", 0)
                season_avg = season_data.get("avg", 0)
                
                weighted_hit_rate = (h10 * 0.6) + (h5 * 0.4)
                has_real_data = h10_games > 0 or h5_games > 0
                
                if not has_real_data or weighted_hit_rate < 0.88:
                    continue
                
                goblin_line = goblin.get("line", 0)
                goblin_stat = self._extract_stat_type(goblin.get("market", ""))
                goblin_direction = goblin.get("direction", "Over")
                
                if not goblin_stat or goblin_line <= 0:
                    continue
                
                home_team = goblin.get("home_team", "")
                away_team = goblin.get("away_team", "")
                game_key = f"{away_team}@{home_team}" if home_team and away_team else ""
                
                is_recon_lock = h10_games >= 5 and h10_over == h10_games
                floor_score = (h10_over / h10_games * 100) if h10_games > 0 else 0
                reliability = round(weighted_hit_rate * 100, 1)
                safety_string = f"{h10_over}/{h10_games}" if h10_games > 0 else "---"
                
                recon_entry = {
                    "player_name": player_name,
                    "team": team,
                    "nba_id": player_data.get("nba_id"),
                    "photo_url": player_data.get("photo_url"),
                    "stat_type": goblin_stat,
                    "line": goblin_line,
                    "direction": goblin_direction,
                    "h10_rate": round(h10 * 100, 1),
                    "h5_rate": round(h5 * 100, 1),
                    "h10_over": h10_over,
                    "h10_games": h10_games,
                    "h5_over": h5_over,
                    "h5_games": h5_games,
                    "season_avg": round(season_avg, 1),
                    "weighted_hit_rate": round(weighted_hit_rate * 100, 1),
                    "floor_score": round(floor_score, 1),
                    "reliability": reliability,
                    "safety_string": safety_string,
                    "is_recon_lock": is_recon_lock,
                    "game_key": game_key,
                    "home_team": home_team,
                    "away_team": away_team,
                    "price": goblin.get("price", -137),
                    "synced_at": sync_time.isoformat()
                }
                
                recon_candidates.append(recon_entry)
                
                if game_key:
                    if game_key not in game_groups:
                        game_groups[game_key] = []
                    game_groups[game_key].append(recon_entry)
        
        recon_candidates.sort(key=lambda x: (x["floor_score"], x["reliability"]), reverse=True)
        logger.info(f"[GOBLIN RECON] Found {len(recon_candidates)} candidates (88%+ hit rate)")
        
        def calculate_recon_probability(picks: List[Dict]) -> float:
            if not picks:
                return 0
            prob = 1.0
            for pick in picks:
                p = pick.get("weighted_hit_rate", 88) / 100
                prob *= p
            return round(prob * 100, 2)
        
        def calculate_goblin_payout(picks: List[Dict]) -> Dict:
            num_picks = len(picks)
            base_payout = round(1.2 ** num_picks, 2)
            
            leg_details = []
            for pick in picks:
                leg_details.append({
                    "player_name": pick.get("player_name", "Unknown"),
                    "stat_type": pick.get("stat_type", "PTS"),
                    "line": pick.get("line", 0),
                    "direction": pick.get("direction", "over"),
                    "team": pick.get("team", ""),
                    "asset_type": "goblin",
                    "modifier": 1.0,
                    "modifier_display": "1.00x"
                })
            
            return {
                "estimated_payout": base_payout,
                "payout_display": f"{base_payout:.1f}x",
                "cumulative_modifier": 1.0,
                "base_multiplier": base_payout,
                "asset_breakdown": {"demons": 0, "goblins": num_picks, "standards": 0},
                "payout_type": "goblin",
                "legs": leg_details
            }
        
        parlays = {}
        
        # Daily Double (2-Pick)
        if len(recon_candidates) >= 2:
            picks_2, is_valid, team_count = self._get_multi_team_picks(recon_candidates, 2)
            if picks_2:
                combined_prob = calculate_recon_probability(picks_2)
                payout_data = calculate_goblin_payout(picks_2)
                parlays["daily_double"] = {
                    "name": "Daily Double",
                    "tier": "daily_double",
                    "picks": picks_2,
                    "pick_count": 2,
                    "combined_probability": combined_prob,
                    "reliability": combined_prob,
                    "estimated_payout": payout_data["estimated_payout"],
                    "payout_display": payout_data["payout_display"],
                    "base_multiplier": payout_data["base_multiplier"],
                    "cumulative_modifier": payout_data["cumulative_modifier"],
                    "asset_breakdown": payout_data["asset_breakdown"],
                    "description": "Top 2 highest-consistency picks",
                    "badge": "SAFEST BET",
                    "lineup_valid": is_valid,
                    "team_count": team_count,
                    "lineup_status": "Valid (Multi-Team)" if is_valid else "INVALID"
                }
        
        # Green Ladder 3-Pick
        if len(recon_candidates) >= 3:
            picks_3, is_valid, team_count = self._get_diversified_picks(recon_candidates, 3, game_groups)
            if picks_3:
                combined_prob = calculate_recon_probability(picks_3)
                payout_data = calculate_goblin_payout(picks_3)
                parlays["green_ladder_3"] = {
                    "name": "Green Ladder",
                    "tier": "green_ladder_3",
                    "picks": picks_3,
                    "pick_count": 3,
                    "combined_probability": combined_prob,
                    "reliability": combined_prob,
                    "estimated_payout": payout_data["estimated_payout"],
                    "payout_display": payout_data["payout_display"],
                    "base_multiplier": payout_data["base_multiplier"],
                    "cumulative_modifier": payout_data["cumulative_modifier"],
                    "asset_breakdown": payout_data["asset_breakdown"],
                    "description": "3 picks diversified",
                    "badge": "DIVERSIFIED",
                    "lineup_valid": is_valid,
                    "team_count": team_count,
                    "lineup_status": "Valid (Multi-Team)" if is_valid else "INVALID"
                }
        
        # Green Ladder 4-Pick
        if len(recon_candidates) >= 4:
            picks_4, is_valid, team_count = self._get_diversified_picks(recon_candidates, 4, game_groups)
            if picks_4:
                combined_prob = calculate_recon_probability(picks_4)
                payout_data = calculate_goblin_payout(picks_4)
                parlays["green_ladder_4"] = {
                    "name": "Green Ladder+",
                    "tier": "green_ladder_4",
                    "picks": picks_4,
                    "pick_count": 4,
                    "combined_probability": combined_prob,
                    "reliability": combined_prob,
                    "estimated_payout": payout_data["estimated_payout"],
                    "payout_display": payout_data["payout_display"],
                    "base_multiplier": payout_data["base_multiplier"],
                    "cumulative_modifier": payout_data["cumulative_modifier"],
                    "asset_breakdown": payout_data["asset_breakdown"],
                    "description": "4 picks diversified",
                    "badge": "BALANCED",
                    "lineup_valid": is_valid,
                    "team_count": team_count,
                    "lineup_status": "Valid (Multi-Team)" if is_valid else "INVALID"
                }
        
        # Green Stack 5-Pick
        if len(recon_candidates) >= 5:
            picks_5, is_valid, team_count = self._get_diversified_picks(recon_candidates, 5, game_groups)
            if picks_5:
                combined_prob = calculate_recon_probability(picks_5)
                payout_data = calculate_goblin_payout(picks_5)
                parlays["green_stack_5"] = {
                    "name": "Green Stack",
                    "tier": "green_stack_5",
                    "picks": picks_5,
                    "pick_count": 5,
                    "combined_probability": combined_prob,
                    "reliability": combined_prob,
                    "estimated_payout": payout_data["estimated_payout"],
                    "payout_display": payout_data["payout_display"],
                    "base_multiplier": payout_data["base_multiplier"],
                    "cumulative_modifier": payout_data["cumulative_modifier"],
                    "asset_breakdown": payout_data["asset_breakdown"],
                    "description": "5 picks stacked",
                    "badge": "STACK",
                    "lineup_valid": is_valid,
                    "team_count": team_count,
                    "lineup_status": "Valid (Multi-Team)" if is_valid else "INVALID"
                }
        
        # 6-Pick Fortress (Flex)
        if len(recon_candidates) >= 6:
            picks_6, is_valid, team_count = self._get_diversified_picks(recon_candidates, 6, game_groups)
            if picks_6:
                combined_prob = calculate_recon_probability(picks_6)
                payout_data = calculate_goblin_payout(picks_6)
                
                avg_p = sum(p["weighted_hit_rate"] for p in picks_6) / 600
                p_all_6 = avg_p ** 6
                p_exactly_5 = 6 * (avg_p ** 5) * (1 - avg_p)
                flex_win_prob = round((p_all_6 + p_exactly_5) * 100, 2)
                
                parlays["fortress_flex"] = {
                    "name": "6-Pick Fortress",
                    "tier": "fortress_flex",
                    "picks": picks_6,
                    "pick_count": 6,
                    "combined_probability": combined_prob,
                    "flex_probability": flex_win_prob,
                    "reliability": flex_win_prob,
                    "estimated_payout": payout_data["estimated_payout"],
                    "payout_display": payout_data["payout_display"],
                    "base_multiplier": payout_data["base_multiplier"],
                    "cumulative_modifier": payout_data["cumulative_modifier"],
                    "asset_breakdown": payout_data["asset_breakdown"],
                    "flex_payout": "5/6 = 1.5x | 6/6 = 15x",
                    "description": "PrizePicks Flex - Win on 5 OR 6!",
                    "badge": "FLEX FORTRESS",
                    "lineup_valid": is_valid,
                    "team_count": team_count,
                    "lineup_status": "Valid (Multi-Team)" if is_valid else "INVALID"
                }
        
        # Validate and store
        parlays = self._validate_parlays(parlays)
        valid_count = sum(1 for p in parlays.values() if p.get("lineup_valid", False))
        locks_count = len([c for c in recon_candidates if c.get("is_recon_lock")])
        
        await self.goblin_recon.delete_many({})
        recon_doc = {
            "parlays": parlays,
            "total_candidates": len(recon_candidates),
            "recon_locks": locks_count,
            "games_available": len(game_groups),
            "min_hit_rate_threshold": "88%",
            "valid_lineups": valid_count,
            "total_lineups": len(parlays),
            "synced_at": sync_time.isoformat()
        }
        await self.goblin_recon.insert_one(recon_doc)
        
        logger.info(f"[GOBLIN RECON] Generated {len(parlays)} parlay tiers ({valid_count} valid)")
        
        return {
            "success": True,
            "parlays_count": len(parlays),
            "valid_count": valid_count,
            "candidates": len(recon_candidates),
            "recon_locks": locks_count
        }
