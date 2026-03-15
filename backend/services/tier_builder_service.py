"""
Tier Builder Service - War Zone, Safe Haven, Front Lines
=========================================================
Extracted from demon_goblin_engine.py for modularity.

Handles the 4-Pillar scoring formulas for each tier:
- War Zone: High-ceiling demon plays
- Safe Haven: High-consistency goblin plays  
- Front Lines: Balanced mix of demons and goblins
"""
from typing import Dict, List, Optional, Any, Set
from datetime import datetime, timezone
import logging

from services.stats_service import (
    calculate_heat_level as stats_calculate_heat_level,
    calculate_safety_level as stats_calculate_safety_level,
    calculate_bullet_level as stats_calculate_bullet_level
)
from services.dvp_service import (
    calculate_dvp_modifier, 
    get_dvp_label,
    get_dvp_rank,
    get_dvp_rank_color,
    calculate_dvp_certainty_multiplier
)
from services.parlay_service import build_parlay_tickets, interleave_pick_arrays

logger = logging.getLogger(__name__)

# Tier-specific scoring weights
WAR_ZONE_WEIGHTS = {
    "ceiling_consistency": 0.40,
    "vegas_probability": 0.20,
    "dvp_matchup": 0.20,
    "context_shift": 0.20
}

SAFE_HAVEN_WEIGHTS = {
    "floor_consistency": 0.50,
    "vegas_probability": 0.20,
    "dvp_matchup": 0.15,
    "context_shift": 0.15
}

FRONT_LINES_WEIGHTS = {
    "base_consistency": 0.40,
    "vegas_probability": 0.20,
    "dvp_matchup": 0.20,
    "context_shift": 0.20
}

# Hard filter thresholds
WAR_ZONE_MIN_HIT_RATE = 0.10  # 10% minimum for demons
SAFE_HAVEN_MIN_HIT_RATE = 0.70  # 70% minimum for goblins
FRONT_LINES_DEMON_RANGE = (0.40, 0.70)  # 40-70% for mild demons
FRONT_LINES_GOBLIN_RANGE = (0.60, 0.85)  # 60-85% for quality goblins


class TierBuilderService:
    """Service for building tier picks with 4-Pillar scoring"""
    
    def __init__(self, db):
        self.db = db
        self.radar_picks = db.dg_radar_picks
        self.goblin_vault = db.dg_goblin_vault
        self.front_lines = db.dg_front_lines
        self.parlay_builder = db.dg_parlay_builder
        self.goblin_recon = db.dg_goblin_recon
        self.sync_log = db.dg_sync_log
    
    # ==================== UTILITY METHODS ====================
    
    def _extract_stat_type(self, market: str) -> str:
        """Extract stat type from market name"""
        if not market:
            return ""
        
        base_market = market.replace("_alternate", "")
        
        stat_map = {
            "player_points": "PTS",
            "player_assists": "AST",
            "player_rebounds": "REB",
            "player_threes": "3PM",
            "player_blocks": "BLK",
            "player_steals": "STL",
            "player_points_rebounds_assists": "PRA",
            "player_points_rebounds": "P+R",
            "player_points_assists": "P+A",
            "player_rebounds_assists": "R+A",
            "player_turnovers": "TO",
        }
        
        return stat_map.get(base_market, "")
    
    async def _get_ai_context_cache(self) -> Dict[str, float]:
        """Pre-fetch AI context scores for all players"""
        ai_context_cache = {}
        try:
            cursor = self.db.nba_master_hub_2026.find(
                {"ai_context_score": {"$exists": True}},
                {"_id": 0, "display_name": 1, "player_name": 1, "ai_context_score": 1}
            )
            async for doc in cursor:
                name = doc.get("display_name") or doc.get("player_name")
                if name:
                    ai_context_cache[name] = doc.get("ai_context_score", 0.5)
        except Exception as e:
            logger.warning(f"[AI_CONTEXT] Could not fetch cache: {e}")
        return ai_context_cache
    
    def _build_standard_map(self, standard_props: List[Dict]) -> Dict[str, Dict]:
        """Build a map of standard lines by stat type"""
        standard_map = {}
        for std_prop in standard_props:
            market = std_prop.get("market", "")
            stat_type = self._extract_stat_type(market)
            if stat_type:
                key = f"{stat_type}_{std_prop.get('direction', '')}"
                if key not in standard_map:
                    standard_map[key] = std_prop
        return standard_map
    
    def _calculate_vegas_implied(self, price: int) -> float:
        """Calculate Vegas implied probability from price"""
        if price < 0:
            return abs(price) / (abs(price) + 100)
        else:
            return 100 / (price + 100)
    
    # ==================== WAR ZONE BUILDER ====================
    
    async def build_war_zone(
        self, 
        players_dict: Dict[str, Dict], 
        sync_time: datetime,
        limit: int = 10
    ) -> Dict[str, Any]:
        """
        Build War Zone - 4-Pillar Ceiling Formula for Demon plays.
        
        Hard Filter: L10 hit rate >= 10%
        Sorting: final_ev_score = base_score * (1 + value_gap_pct)
        """
        logger.info("[WAR ZONE v3.0] Building with 4-Pillar Ceiling Formula...")
        
        all_candidates = []
        ai_context_cache = await self._get_ai_context_cache()
        
        for player_name, player_data in players_dict.items():
            if player_data is None:
                continue
            
            demons = player_data.get("demons", [])
            standard = player_data.get("standard", [])
            
            if not demons:
                continue
            
            standard_map = self._build_standard_map(standard)
            
            for demon in demons:
                candidate = self._score_war_zone_demon(
                    demon, player_name, player_data, standard_map, 
                    ai_context_cache, sync_time
                )
                if candidate:
                    all_candidates.append(candidate)
        
        # Sort by EV score
        all_candidates.sort(key=lambda x: x["final_ev_score"], reverse=True)
        
        # De-duplicate: one pick per player
        seen_players: Set[str] = set()
        unique_picks = []
        for pick in all_candidates:
            pname = pick["player_name"]
            if pname not in seen_players:
                seen_players.add(pname)
                unique_picks.append(pick)
        
        top_picks = unique_picks[:limit]
        
        # Store in MongoDB
        await self.radar_picks.delete_many({})
        if top_picks:
            await self.radar_picks.insert_many(top_picks)
        
        logger.info(f"[WAR ZONE v3.1] Generated {len(top_picks)} picks from {len(all_candidates)} candidates")
        
        return {
            "success": True,
            "picks_count": len(top_picks),
            "total_candidates": len(all_candidates),
            "synced_at": sync_time.isoformat()
        }
    
    def _score_war_zone_demon(
        self, 
        demon: Dict, 
        player_name: str, 
        player_data: Dict,
        standard_map: Dict[str, Dict],
        ai_context_cache: Dict[str, float],
        sync_time: datetime
    ) -> Optional[Dict]:
        """Score a single demon prop for War Zone"""
        demon_market = demon.get("market", "")
        demon_stat = self._extract_stat_type(demon_market)
        demon_line = demon.get("line", 0)
        demon_direction = demon.get("direction", "")
        demon_price = demon.get("price", 100)
        
        if not demon_stat or demon_line <= 0:
            return None
        
        # Get standard line reference
        std_key = f"{demon_stat}_{demon_direction}"
        std_prop = standard_map.get(std_key)
        std_line = std_prop.get("line", 0) if std_prop else demon_line * 0.85
        
        if std_line <= 0:
            return None
        
        # Get hit rates
        hit_rates = demon.get("hit_rates", {}) or {}
        h10_data = hit_rates.get("l10", {}) or {}
        h5_data = hit_rates.get("l5", {}) or {}
        season_data = hit_rates.get("season", {}) or {}
        
        h10 = h10_data.get("hit_rate", 0)
        h5 = h5_data.get("hit_rate", 0)
        h10_games = h10_data.get("total_games", 0)
        h5_games = h5_data.get("total_games", 0)
        h10_over = h10_data.get("games_over", 0)
        h5_over = h5_data.get("games_over", 0)
        season_avg = season_data.get("avg", 0)
        
        # Must have real data
        if h10_games == 0 and h5_games == 0:
            return None
        
        # Hard filter
        if h10 < WAR_ZONE_MIN_HIT_RATE:
            return None
        
        # Pillar 1: Ceiling Consistency (40%)
        ceiling_consistency = (h10 * 0.6) + (h5 * 0.4)
        
        # Pillar 2: Vegas Implied (20%)
        implied_prob = self._calculate_vegas_implied(demon_price)
        
        # Pillar 3: DvP (20%)
        opponent_team = player_data.get("opponent_abbr") or player_data.get("opponent")
        dvp_modifier = calculate_dvp_modifier(opponent_team, demon_stat)
        dvp_label = get_dvp_label(dvp_modifier)
        dvp_rank = get_dvp_rank(opponent_team, demon_stat)
        dvp_rank_color = get_dvp_rank_color(dvp_rank)
        dvp_certainty_mult = calculate_dvp_certainty_multiplier(dvp_rank)
        
        # Pillar 4: AI Context (20%)
        context_shift = ai_context_cache.get(player_name, 0.5)
        
        # Base demon score
        demon_score = (
            (ceiling_consistency * 0.40) +
            (implied_prob * 0.20) +
            (dvp_modifier * 0.20) +
            (context_shift * 0.20)
        )
        
        # Apply DvP certainty multiplier to the score
        # Rank >= 25 (Bottom 5): +10% boost | Rank <= 5 (Top 5): -15% penalty
        demon_score_adjusted = demon_score * dvp_certainty_mult
        
        # Value gap calculation
        gap_ratio = demon_line / std_line if std_line > 0 else 1.0
        value_gap_pct = gap_ratio - 1
        gap_pct = value_gap_pct * 100
        
        # Final EV score (using DvP-adjusted score)
        final_ev_score = demon_score_adjusted * (1 + value_gap_pct)
        
        # Heat level
        heat_level = stats_calculate_heat_level(h10, h5, h10_over, h5_over, h10_games, h5_games)
        is_hot_streak = h5_over >= 3 if h5_games >= 3 else False
        
        return {
            "player_id": player_data.get("player_id"),
            "tank01_player_id": player_data.get("tank01_player_id") or player_data.get("tank01_id"),
            "player_name": player_name,
            "team": player_data.get("team", ""),
            "team_name": player_data.get("team_name"),
            "photo_url": player_data.get("photo_url") or player_data.get("headshot_url"),
            "headshot_url": player_data.get("headshot_url") or player_data.get("photo_url"),
            "nba_com_id": player_data.get("nba_com_id") or player_data.get("nba_id"),
            "nba_id": player_data.get("nba_id") or player_data.get("nba_com_id"),
            "espn_id": player_data.get("espn_id"),
            "position": player_data.get("position"),
            "volatility_flag": player_data.get("volatility_flag", False),
            "revenge_game": player_data.get("revenge_game", False),
            "is_verified": player_data.get("is_verified", False),
            "is_mapper_matched": player_data.get("is_mapper_matched", False),
            "stat_type": demon_stat,
            "direction": demon_direction,
            "demon_line": demon_line,
            "standard_line": round(std_line, 1),
            "gap_ratio": round(gap_ratio, 3),
            "gap_pct": round(gap_pct, 1),
            "value_gap_pct": round(value_gap_pct, 4),
            "price": demon_price,
            "h10_rate": round(h10 * 100, 1),
            "h5_rate": round(h5 * 100, 1),
            "h10_over": h10_over,
            "h10_games": h10_games,
            "h5_over": h5_over,
            "h5_games": h5_games,
            "season_avg": round(season_avg, 1),
            "pillar_1_ceiling": round(ceiling_consistency, 4),
            "pillar_2_vegas": round(implied_prob, 4),
            "pillar_3_dvp": round(dvp_modifier, 4),
            "pillar_4_context": round(context_shift, 4),
            "dvp_modifier": round(dvp_modifier, 3),
            "dvp_label": dvp_label,
            "dvp_rank": dvp_rank,
            "dvp_rank_color": dvp_rank_color,
            "dvp_certainty_mult": dvp_certainty_mult,
            "opponent_team": opponent_team,
            "demon_score": round(demon_score, 4),
            "demon_score_adjusted": round(demon_score_adjusted, 4),
            "radar_score": round(demon_score_adjusted, 4),
            "demon_score_100": round(demon_score_adjusted * 100, 1),
            "final_ev_score": round(final_ev_score, 4),
            "final_ev_score_100": round(final_ev_score * 100, 1),
            "heat_level": heat_level,
            "is_hot_streak": is_hot_streak,
            "hit_probability": round(ceiling_consistency * 100, 1),
            "radar_strength": round(demon_score * 100, 1),
            "is_radar_pick": True,
            "is_demon": True,
            "has_real_data": True,
            "synced_at": sync_time.isoformat()
        }
    
    # ==================== SAFE HAVEN (GOBLIN VAULT) BUILDER ====================
    
    async def build_goblin_vault(
        self, 
        players_dict: Dict[str, Dict], 
        sync_time: datetime,
        limit: int = 10
    ) -> Dict[str, Any]:
        """
        Build Safe Haven - 4-Pillar Floor Formula for Goblin plays.
        
        Hard Filter: L10 hit rate >= 70%
        Sorting: final_ev_score = base_score * (1 + value_gap_pct)
        """
        logger.info("[SAFE HAVEN v3.1] Building with EV Multiplier sorting...")
        
        all_candidates = []
        ai_context_cache = await self._get_ai_context_cache()
        
        for player_name, player_data in players_dict.items():
            if player_data is None:
                continue
            
            goblins = player_data.get("goblins", [])
            standard = player_data.get("standard", [])
            
            if not goblins:
                continue
            
            standard_map = self._build_standard_map(standard)
            
            for goblin in goblins:
                candidate = self._score_goblin_vault_pick(
                    goblin, player_name, player_data, standard_map,
                    ai_context_cache, sync_time
                )
                if candidate:
                    all_candidates.append(candidate)
        
        # Sort by EV score
        all_candidates.sort(key=lambda x: x["final_ev_score"], reverse=True)
        
        # De-duplicate
        seen_players: Set[str] = set()
        unique_picks = []
        for pick in all_candidates:
            pname = pick["player_name"]
            if pname not in seen_players:
                seen_players.add(pname)
                unique_picks.append(pick)
        
        top_picks = unique_picks[:limit]
        
        # Store in MongoDB
        await self.goblin_vault.delete_many({})
        if top_picks:
            await self.goblin_vault.insert_many(top_picks)
        
        logger.info(f"[SAFE HAVEN v3.1] Generated {len(top_picks)} picks from {len(all_candidates)} candidates")
        
        return {
            "success": True,
            "picks_count": len(top_picks),
            "total_candidates": len(all_candidates),
            "synced_at": sync_time.isoformat()
        }
    
    def _score_goblin_vault_pick(
        self,
        goblin: Dict,
        player_name: str,
        player_data: Dict,
        standard_map: Dict[str, Dict],
        ai_context_cache: Dict[str, float],
        sync_time: datetime
    ) -> Optional[Dict]:
        """Score a single goblin prop for Safe Haven"""
        goblin_market = goblin.get("market", "")
        goblin_stat = self._extract_stat_type(goblin_market)
        goblin_line = goblin.get("line", 0)
        goblin_direction = goblin.get("direction", "")
        goblin_price = goblin.get("price", -110)
        
        if not goblin_stat or goblin_line <= 0:
            return None
        
        # Get standard line
        std_key = f"{goblin_stat}_{goblin_direction}"
        std_prop = standard_map.get(std_key)
        std_line = std_prop.get("line", 0) if std_prop else goblin_line * 1.15
        
        if std_line <= 0:
            return None
        
        # Get hit rates
        hit_rates = goblin.get("hit_rates", {}) or {}
        h10_data = hit_rates.get("l10", {}) or {}
        h5_data = hit_rates.get("l5", {}) or {}
        season_data = hit_rates.get("season", {}) or {}
        
        h10 = h10_data.get("hit_rate", 0)
        h5 = h5_data.get("hit_rate", 0)
        h10_games = h10_data.get("total_games", 0)
        h5_games = h5_data.get("total_games", 0)
        h10_over = h10_data.get("games_over", 0)
        h5_over = h5_data.get("games_over", 0)
        season_avg = season_data.get("avg", 0)
        
        # Must have real data
        if h10_games == 0 and h5_games == 0:
            return None
        
        # Hard filter
        if h10 < SAFE_HAVEN_MIN_HIT_RATE:
            return None
        
        # Pillar 1: Floor Consistency (50%)
        pillar_1_consistency = (h10 * 0.6) + (h5 * 0.4)
        
        # Pillar 2: Vegas Implied (20%)
        pillar_2_vegas = self._calculate_vegas_implied(goblin_price)
        pillar_2_vegas = min(1.0, max(0.0, pillar_2_vegas))
        
        # Pillar 3: DvP (15%)
        opponent_team = player_data.get("opponent_abbr") or player_data.get("opponent")
        pillar_3_dvp = calculate_dvp_modifier(opponent_team, goblin_stat)
        dvp_label = get_dvp_label(pillar_3_dvp)
        dvp_rank = get_dvp_rank(opponent_team, goblin_stat)
        dvp_rank_color = get_dvp_rank_color(dvp_rank)
        dvp_certainty_mult = calculate_dvp_certainty_multiplier(dvp_rank)
        
        # Pillar 4: AI Context (15%)
        pillar_4_context = ai_context_cache.get(player_name, 0.5)
        
        # Base vault score
        vault_score = (
            (pillar_1_consistency * 0.50) +
            (pillar_2_vegas * 0.20) +
            (pillar_3_dvp * 0.15) +
            (pillar_4_context * 0.15)
        )
        
        # Apply DvP certainty multiplier
        vault_score_adjusted = vault_score * dvp_certainty_mult
        
        # Value gap calculation
        gap_below_std = std_line - goblin_line
        value_gap_pct = (gap_below_std / std_line) if std_line > 0 else 0
        
        # Final EV score (using DvP-adjusted score)
        final_ev_score = vault_score_adjusted * (1 + value_gap_pct)
        
        vault_score_100 = vault_score_adjusted * 100
        final_ev_score_100 = final_ev_score * 100
        
        # Safety metrics
        safety_level = stats_calculate_safety_level(h10, h5, h10_over, h5_over, h10_games, h5_games)
        is_perfect_streak = h10_games >= 5 and h10_over == h10_games
        safety_string = f"{h10_over}/{h10_games}" if h10_games > 0 else "---"
        
        return {
            "player_id": player_data.get("player_id"),
            "tank01_player_id": player_data.get("tank01_player_id") or player_data.get("tank01_id"),
            "player_name": player_name,
            "team": player_data.get("team", ""),
            "team_name": player_data.get("team_name"),
            "team_abbr": player_data.get("team_abbr"),
            "photo_url": player_data.get("photo_url") or player_data.get("headshot_url"),
            "headshot_url": player_data.get("headshot_url") or player_data.get("photo_url"),
            "nba_com_id": player_data.get("nba_com_id") or player_data.get("nba_id"),
            "nba_id": player_data.get("nba_id") or player_data.get("nba_com_id"),
            "espn_id": player_data.get("espn_id"),
            "position": player_data.get("position"),
            "opponent": player_data.get("opponent"),
            "opponent_abbr": player_data.get("opponent_abbr"),
            "game_id": player_data.get("game_id"),
            "game_time": player_data.get("game_time"),
            "volatility_flag": player_data.get("volatility_flag", False),
            "revenge_game": player_data.get("revenge_game", False),
            "is_verified": player_data.get("is_verified", False),
            "is_mapper_matched": player_data.get("is_mapper_matched", False),
            "is_goblin": True,
            "stat_type": goblin_stat,
            "direction": goblin_direction,
            "goblin_line": goblin_line,
            "line": goblin_line,
            "standard_line": round(std_line, 1),
            "gap_below_std": round(gap_below_std, 1),
            "gap_pct": round(value_gap_pct * 100, 1),
            "value_gap_pct": round(value_gap_pct, 4),
            "price": goblin_price,
            "h10_rate": round(h10 * 100, 1),
            "h5_rate": round(h5 * 100, 1),
            "h10_over": h10_over,
            "h10_games": h10_games,
            "h5_over": h5_over,
            "h5_games": h5_games,
            "season_avg": round(season_avg, 1),
            "pillar_1_consistency": round(pillar_1_consistency, 4),
            "pillar_2_vegas": round(pillar_2_vegas, 4),
            "pillar_3_dvp": round(pillar_3_dvp, 4),
            "pillar_4_context": round(pillar_4_context, 4),
            "dvp_modifier": round(pillar_3_dvp, 3),
            "dvp_label": dvp_label,
            "dvp_rank": dvp_rank,
            "dvp_rank_color": dvp_rank_color,
            "dvp_certainty_mult": dvp_certainty_mult,
            "opponent_team": opponent_team,
            "vault_score": round(vault_score, 4),
            "vault_score_adjusted": round(vault_score_adjusted, 4),
            "vault_score_100": round(vault_score_100, 1),
            "final_ev_score": round(final_ev_score, 4),
            "final_ev_score_100": round(final_ev_score_100, 1),
            "hit_probability": round(h10 * 100, 1),
            "safety_level": safety_level,
            "safety_rating": round(h10 * 100, 1),
            "safety_string": safety_string,
            "is_perfect_streak": is_perfect_streak,
            "is_vault_pick": True,
            "has_real_data": True,
            "synced_at": sync_time.isoformat()
        }
    
    # ==================== FRONT LINES BUILDER ====================
    
    async def build_front_lines(
        self,
        players_dict: Dict[str, Dict],
        sync_time: datetime,
        limit: int = 10
    ) -> Dict[str, Any]:
        """
        Build Front Lines - Balanced mix of mild demons and quality goblins.
        
        Mild Demons: 40-70% hit rate
        Quality Goblins: 60-85% hit rate
        """
        logger.info("[FRONT LINES v3.0] Building mixed tier...")
        
        mild_demons = []
        quality_goblins = []
        ai_context_cache = await self._get_ai_context_cache()
        
        for player_name, player_data in players_dict.items():
            if player_data is None:
                continue
            
            demons = player_data.get("demons", [])
            goblins = player_data.get("goblins", [])
            standard = player_data.get("standard", [])
            
            standard_map = self._build_standard_map(standard)
            
            # Process demons for "mild" category
            for demon in demons:
                candidate = self._score_front_lines_demon(
                    demon, player_name, player_data, standard_map,
                    ai_context_cache, sync_time
                )
                if candidate:
                    mild_demons.append(candidate)
            
            # Process goblins for "quality" category
            for goblin in goblins:
                candidate = self._score_front_lines_goblin(
                    goblin, player_name, player_data, standard_map,
                    ai_context_cache, sync_time
                )
                if candidate:
                    quality_goblins.append(candidate)
        
        # Sort each by score
        mild_demons.sort(key=lambda x: x.get("frontlines_score", 0), reverse=True)
        quality_goblins.sort(key=lambda x: x.get("frontlines_score", 0), reverse=True)
        
        # Interleave for variety
        interleaved = interleave_pick_arrays(
            quality_goblins[:limit//2 + 1],
            mild_demons[:limit//2 + 1]
        )
        
        # De-duplicate
        seen_players: Set[str] = set()
        unique_picks = []
        for pick in interleaved:
            pname = pick["player_name"]
            if pname not in seen_players:
                seen_players.add(pname)
                unique_picks.append(pick)
        
        top_picks = unique_picks[:limit]
        
        # Store in MongoDB
        await self.front_lines.delete_many({})
        if top_picks:
            await self.front_lines.insert_many(top_picks)
        
        demon_count = sum(1 for p in top_picks if p.get("is_demon"))
        goblin_count = sum(1 for p in top_picks if p.get("is_goblin"))
        
        logger.info(f"[FRONT LINES v3.0] Generated {len(top_picks)} picks (D:{demon_count}/G:{goblin_count})")
        
        return {
            "success": True,
            "picks_count": len(top_picks),
            "demon_count": demon_count,
            "goblin_count": goblin_count,
            "synced_at": sync_time.isoformat()
        }
    
    def _score_front_lines_demon(
        self,
        demon: Dict,
        player_name: str,
        player_data: Dict,
        standard_map: Dict[str, Dict],
        ai_context_cache: Dict[str, float],
        sync_time: datetime
    ) -> Optional[Dict]:
        """Score a demon for Front Lines (mild demons only)"""
        demon_market = demon.get("market", "")
        demon_stat = self._extract_stat_type(demon_market)
        demon_line = demon.get("line", 0)
        demon_direction = demon.get("direction", "")
        demon_price = demon.get("price", 100)
        
        if not demon_stat or demon_line <= 0:
            return None
        
        # Get standard line
        std_key = f"{demon_stat}_{demon_direction}"
        std_prop = standard_map.get(std_key)
        std_line = std_prop.get("line", 0) if std_prop else demon_line * 0.85
        
        if std_line <= 0:
            return None
        
        # Get hit rates
        hit_rates = demon.get("hit_rates", {}) or {}
        h10_data = hit_rates.get("l10", {}) or {}
        h5_data = hit_rates.get("l5", {}) or {}
        season_data = hit_rates.get("season", {}) or {}
        
        h10 = h10_data.get("hit_rate", 0)
        h5 = h5_data.get("hit_rate", 0)
        h10_games = h10_data.get("total_games", 0)
        h5_games = h5_data.get("total_games", 0)
        h10_over = h10_data.get("games_over", 0)
        h5_over = h5_data.get("games_over", 0)
        season_avg = season_data.get("avg", 0)
        
        if h10_games == 0 and h5_games == 0:
            return None
        
        # Filter for "mild" demons (40-70% hit rate)
        min_rate, max_rate = FRONT_LINES_DEMON_RANGE
        if h10 < min_rate or h10 > max_rate:
            return None
        
        # 4-Pillar scoring
        pillar_1 = (h10 * 0.6) + (h5 * 0.4)
        pillar_2 = self._calculate_vegas_implied(demon_price)
        opponent = player_data.get("opponent_abbr") or player_data.get("opponent")
        pillar_3 = calculate_dvp_modifier(opponent, demon_stat)
        pillar_4 = ai_context_cache.get(player_name, 0.5)
        
        # Get DvP rank and color for badge display
        dvp_rank = get_dvp_rank(opponent, demon_stat)
        dvp_rank_color = get_dvp_rank_color(dvp_rank)
        dvp_certainty_mult = calculate_dvp_certainty_multiplier(dvp_rank)
        
        frontlines_score = (
            (pillar_1 * 0.40) +
            (pillar_2 * 0.20) +
            (pillar_3 * 0.20) +
            (pillar_4 * 0.20)
        )
        
        # Apply DvP certainty multiplier
        frontlines_score_adjusted = frontlines_score * dvp_certainty_mult
        
        # Value gap
        gap_ratio = demon_line / std_line if std_line > 0 else 1.0
        value_gap_pct = gap_ratio - 1
        final_ev = frontlines_score_adjusted * (1 + value_gap_pct)
        
        bullet_level = stats_calculate_bullet_level(h10, h5, h10_over, h5_over, h10_games, h5_games)
        
        return {
            "player_name": player_name,
            "player_id": player_data.get("player_id"),
            "team": player_data.get("team", ""),
            "team_name": player_data.get("team_name"),
            "photo_url": player_data.get("photo_url") or player_data.get("headshot_url"),
            "headshot_url": player_data.get("headshot_url"),
            "nba_id": player_data.get("nba_id"),
            "position": player_data.get("position"),
            "stat_type": demon_stat,
            "direction": demon_direction,
            "demon_line": demon_line,
            "line": demon_line,
            "standard_line": round(std_line, 1),
            "gap_pct": round(value_gap_pct * 100, 1),
            "value_gap_pct": round(value_gap_pct, 4),
            "price": demon_price,
            "h10_rate": round(h10 * 100, 1),
            "h5_rate": round(h5 * 100, 1),
            "h10_over": h10_over,
            "h10_games": h10_games,
            "h5_over": h5_over,
            "h5_games": h5_games,
            "season_avg": round(season_avg, 1),
            "pillar_1_consistency": round(pillar_1, 4),
            "pillar_2_vegas": round(pillar_2, 4),
            "pillar_3_dvp": round(pillar_3, 4),
            "pillar_4_context": round(pillar_4, 4),
            "dvp_modifier": round(pillar_3, 3),
            "dvp_label": get_dvp_label(pillar_3),
            "dvp_rank": dvp_rank,
            "dvp_rank_color": dvp_rank_color,
            "dvp_certainty_mult": dvp_certainty_mult,
            "opponent_team": opponent,
            "frontlines_score": round(frontlines_score, 4),
            "frontlines_score_adjusted": round(frontlines_score_adjusted, 4),
            "frontlines_score_100": round(frontlines_score_adjusted * 100, 1),
            "final_ev_score": round(final_ev, 4),
            "final_ev_score_100": round(final_ev * 100, 1),
            "hit_probability": round(h10 * 100, 1),
            "bullet_level": bullet_level,
            "is_demon": True,
            "is_goblin": False,
            "is_frontlines_pick": True,
            "has_real_data": True,
            "synced_at": sync_time.isoformat()
        }
    
    def _score_front_lines_goblin(
        self,
        goblin: Dict,
        player_name: str,
        player_data: Dict,
        standard_map: Dict[str, Dict],
        ai_context_cache: Dict[str, float],
        sync_time: datetime
    ) -> Optional[Dict]:
        """Score a goblin for Front Lines (quality goblins only)"""
        goblin_market = goblin.get("market", "")
        goblin_stat = self._extract_stat_type(goblin_market)
        goblin_line = goblin.get("line", 0)
        goblin_direction = goblin.get("direction", "")
        goblin_price = goblin.get("price", -110)
        
        if not goblin_stat or goblin_line <= 0:
            return None
        
        # Get standard line
        std_key = f"{goblin_stat}_{goblin_direction}"
        std_prop = standard_map.get(std_key)
        std_line = std_prop.get("line", 0) if std_prop else goblin_line * 1.15
        
        if std_line <= 0:
            return None
        
        # Get hit rates
        hit_rates = goblin.get("hit_rates", {}) or {}
        h10_data = hit_rates.get("l10", {}) or {}
        h5_data = hit_rates.get("l5", {}) or {}
        season_data = hit_rates.get("season", {}) or {}
        
        h10 = h10_data.get("hit_rate", 0)
        h5 = h5_data.get("hit_rate", 0)
        h10_games = h10_data.get("total_games", 0)
        h5_games = h5_data.get("total_games", 0)
        h10_over = h10_data.get("games_over", 0)
        h5_over = h5_data.get("games_over", 0)
        season_avg = season_data.get("avg", 0)
        
        if h10_games == 0 and h5_games == 0:
            return None
        
        # Filter for "quality" goblins (60-85% hit rate)
        min_rate, max_rate = FRONT_LINES_GOBLIN_RANGE
        if h10 < min_rate or h10 > max_rate:
            return None
        
        # 4-Pillar scoring
        pillar_1 = (h10 * 0.6) + (h5 * 0.4)
        pillar_2 = self._calculate_vegas_implied(goblin_price)
        opponent = player_data.get("opponent_abbr") or player_data.get("opponent")
        pillar_3 = calculate_dvp_modifier(opponent, goblin_stat)
        pillar_4 = ai_context_cache.get(player_name, 0.5)
        
        # Get DvP rank and color for badge display
        dvp_rank = get_dvp_rank(opponent, goblin_stat)
        dvp_rank_color = get_dvp_rank_color(dvp_rank)
        dvp_certainty_mult = calculate_dvp_certainty_multiplier(dvp_rank)
        
        frontlines_score = (
            (pillar_1 * 0.40) +
            (pillar_2 * 0.20) +
            (pillar_3 * 0.20) +
            (pillar_4 * 0.20)
        )
        
        # Apply DvP certainty multiplier
        frontlines_score_adjusted = frontlines_score * dvp_certainty_mult
        
        # Value gap
        gap_below_std = std_line - goblin_line
        value_gap_pct = (gap_below_std / std_line) if std_line > 0 else 0
        final_ev = frontlines_score_adjusted * (1 + value_gap_pct)
        
        bullet_level = stats_calculate_bullet_level(h10, h5, h10_over, h5_over, h10_games, h5_games)
        
        return {
            "player_name": player_name,
            "player_id": player_data.get("player_id"),
            "team": player_data.get("team", ""),
            "team_name": player_data.get("team_name"),
            "photo_url": player_data.get("photo_url") or player_data.get("headshot_url"),
            "headshot_url": player_data.get("headshot_url"),
            "nba_id": player_data.get("nba_id"),
            "position": player_data.get("position"),
            "stat_type": goblin_stat,
            "direction": goblin_direction,
            "goblin_line": goblin_line,
            "line": goblin_line,
            "standard_line": round(std_line, 1),
            "gap_pct": round(value_gap_pct * 100, 1),
            "value_gap_pct": round(value_gap_pct, 4),
            "price": goblin_price,
            "h10_rate": round(h10 * 100, 1),
            "h5_rate": round(h5 * 100, 1),
            "h10_over": h10_over,
            "h10_games": h10_games,
            "h5_over": h5_over,
            "h5_games": h5_games,
            "season_avg": round(season_avg, 1),
            "pillar_1_consistency": round(pillar_1, 4),
            "pillar_2_vegas": round(pillar_2, 4),
            "pillar_3_dvp": round(pillar_3, 4),
            "pillar_4_context": round(pillar_4, 4),
            "dvp_modifier": round(pillar_3, 3),
            "dvp_label": get_dvp_label(pillar_3),
            "dvp_rank": dvp_rank,
            "dvp_rank_color": dvp_rank_color,
            "dvp_certainty_mult": dvp_certainty_mult,
            "opponent_team": opponent,
            "frontlines_score": round(frontlines_score, 4),
            "frontlines_score_adjusted": round(frontlines_score_adjusted, 4),
            "frontlines_score_100": round(frontlines_score_adjusted * 100, 1),
            "final_ev_score": round(final_ev, 4),
            "final_ev_score_100": round(final_ev * 100, 1),
            "hit_probability": round(h10 * 100, 1),
            "bullet_level": bullet_level,
            "is_demon": False,
            "is_goblin": True,
            "is_frontlines_pick": True,
            "has_real_data": True,
            "synced_at": sync_time.isoformat()
        }
