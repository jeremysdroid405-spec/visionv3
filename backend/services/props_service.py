"""
Props Service - Prop Building and Enrichment
=============================================
Extracted from services.engines.demon_goblin_engine.py for modularity.

Handles:
- Prop classification (Demon/Goblin/Standard)
- Hit rate calculations
- Pick scoring (4-pillar formula)
- EV calculations
"""
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
import logging

from services.stats_service import (
    calculate_hit_rates, calculate_heat_level, calculate_safety_level,
    calculate_bullet_level, STAT_FIELD_MAP
)
from services.dvp_service import calculate_dvp_modifier, get_dvp_label
from services.insights_service import calculate_confidence_rating
from services.utils_service import sanitize_player_name

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

# PrizePicks Classification Constants
DEMON_ODDS = 100  # Even odds = Demon
GOBLIN_HIT_RATE_WARNING = 0.90

# Stat type mapping for markets
STAT_TYPE_MAP = {
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

# Scoring weights
WAR_ZONE_WEIGHTS = {
    "ceiling_consistency": 0.45,
    "vegas_probability": 0.20,
    "dvp_matchup": 0.20,
    "context_shift": 0.15
}

GOBLIN_VAULT_WEIGHTS = {
    "floor_consistency": 0.50,
    "vegas_probability": 0.20,
    "dvp_matchup": 0.15,
    "context_shift": 0.15
}

FRONT_LINES_WEIGHTS = {
    "base_consistency": 0.50,
    "vegas_probability": 0.20,
    "dvp_matchup": 0.15,
    "context_shift": 0.15
}


class PropsService:
    """Service for building and scoring props - BDL is SSOT for all NBA data"""
    
    def __init__(self, db):
        self.db = db
        # BDL is the ONLY source for player stats
        self.master_hub = db[COLL("master_hub", "nba")]
        self.daily_insights = db.dg_daily_insights
    
    # ==================== STAT TYPE EXTRACTION ====================
    
    def extract_stat_type(self, market: str) -> str:
        """
        Extract stat type from market name.
        
        Example: "player_points_alternate" -> "PTS"
        """
        if not market:
            return "PTS"
        
        # Remove _alternate suffix
        base_market = market.replace("_alternate", "")
        
        return STAT_TYPE_MAP.get(base_market, "PTS")
    
    # ==================== PROP CLASSIFICATION ====================
    
    def classify_prop(self, prop: Dict) -> Tuple[bool, bool, str]:
        """
        Classify a prop as Demon, Goblin, or Standard.
        
        PrizePicks Classification Rules:
        1. STANDARD: Props from main markets (no alternate suffix)
        2. DEMON: Alternate market + Even odds (+100)
        3. GOBLIN: Alternate market + Any other odds
        
        Returns: (is_demon, is_goblin, prop_type)
        """
        market = prop.get("market", "")
        price = prop.get("price", 0)
        
        # Standard markets don't have _alternate suffix
        if "_alternate" not in market:
            return False, False, "standard"
        
        # Alternate markets - check odds
        if price == DEMON_ODDS or price == -DEMON_ODDS:
            return True, False, "demon"
        else:
            return False, True, "goblin"
    
    # ==================== HIT RATE CALCULATIONS (BDL SSOT) ====================
    
    async def calculate_prop_hit_rates(
        self, 
        player_name: str, 
        stat_type: str, 
        line: float
    ) -> Dict[str, Any]:
        """
        Calculate hit rates for a prop using BDL game logs from nba_master_hub_2026.
        BDL is the ONLY source of truth.
        
        Returns hit rates for L5, L10, and season.
        """
        normalized = sanitize_player_name(player_name)
        
        # Get player data from BDL master hub
        player_doc = await self.master_hub.find_one(
            {"$or": [
                {"display_name": player_name},
                {"display_name": {"$regex": f"^{normalized}$", "$options": "i"}}
            ]},
            {"_id": 0, "bdl_game_logs": 1, "baseline_stats": 1}
        )
        
        if not player_doc or not player_doc.get("bdl_game_logs"):
            return {}
        
        game_logs = player_doc.get("bdl_game_logs", [])
        return self._calculate_hit_rates_from_bdl(game_logs, stat_type, line)
    
    def _calculate_hit_rates_from_bdl(self, game_logs: list, stat_type: str, line: float) -> Dict[str, Any]:
        """Calculate hit rates from BDL game logs."""
        if not game_logs:
            return {}
        
        # Map stat types to BDL field names
        stat_field_map = {
            "PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl", 
            "BLK": "blk", "TO": "turnover", "3PM": "fg3m", "THREES": "fg3m",
            "PRA": ["pts", "reb", "ast"], "PR": ["pts", "reb"], 
            "PA": ["pts", "ast"], "RA": ["reb", "ast"]
        }
        
        field = stat_field_map.get(stat_type.upper())
        if not field:
            return {}
        
        # Extract values
        values = []
        for game in game_logs[:10]:
            if isinstance(field, list):
                total = sum(game.get(f, 0) or 0 for f in field)
                values.append(total)
            else:
                val = game.get(field, 0)
                if val is not None:
                    values.append(val)
        
        if not values:
            return {}
        
        l5_values = values[:5]
        l10_values = values[:10]
        
        l5_over = sum(1 for v in l5_values if v > line)
        l10_over = sum(1 for v in l10_values if v > line)
        
        return {
            "l5": {
                "hit_rate": l5_over / len(l5_values) if l5_values else 0,
                "games_over": l5_over,
                "total_games": len(l5_values)
            },
            "l10": {
                "hit_rate": l10_over / len(l10_values) if l10_values else 0,
                "games_over": l10_over,
                "total_games": len(l10_values)
            },
            "season": {
                "avg": sum(values) / len(values) if values else 0
            },
            "source": "bdl_game_logs"
        }
    
    # ==================== WAR ZONE PICK SCORING ====================
    
    def score_war_zone_pick(
        self,
        prop: Dict,
        hit_rates: Dict,
        player_data: Dict,
        player_insights: Dict = None
    ) -> Dict[str, Any]:
        """
        Score a War Zone (Demon) pick using 4-Pillar Formula.
        
        Tactical Formula v3.1:
        Pillar 1 (45%): Ceiling Consistency (L10 + L5 hit rates)
        Pillar 2 (20%): Vegas Implied Probability
        Pillar 3 (20%): DvP Matchup Modifier
        Pillar 4 (15%): Context Shift (injuries, pace, usage)
        """
        # Extract hit rate data
        l10_data = hit_rates.get("l10", {})
        l5_data = hit_rates.get("l5", {})
        season_data = hit_rates.get("season", {})
        
        h10 = l10_data.get("hit_rate", 0)
        h5 = l5_data.get("hit_rate", 0)
        h10_over = l10_data.get("games_over", 0)
        h10_games = l10_data.get("total_games", 0)
        h5_over = l5_data.get("games_over", 0)
        h5_games = l5_data.get("total_games", 0)
        season_avg = season_data.get("avg", 0)
        
        # Pillar 1: Ceiling Consistency (45%)
        # Demons reward ceiling performance - weight recent form heavily
        pillar_1_consistency = (h10 * 0.6) + (h5 * 0.4)
        
        # Pillar 2: Vegas Implied Probability (20%)
        # Even odds (+100) = 50% implied
        price = prop.get("price", 100)
        if price > 0:
            vegas_prob = 100 / (price + 100)
        else:
            vegas_prob = abs(price) / (abs(price) + 100)
        pillar_2_vegas = vegas_prob
        
        # Pillar 3: DvP Matchup (20%)
        opponent_team = player_data.get("opponent", "")
        stat_type = self.extract_stat_type(prop.get("market", ""))
        pillar_3_dvp = calculate_dvp_modifier(opponent_team, stat_type)
        
        # Pillar 4: Context Shift (15%)
        # Uses insights for pace, usage, injuries
        pillar_4_context = 0.5  # Neutral default
        if player_insights:
            pace_factor = player_insights.get("pace_factor", 1.0)
            usage_bump = player_insights.get("usage_bump", 0)
            
            # Convert to 0-1 scale
            pace_score = min(1.0, max(0.0, (pace_factor - 0.9) / 0.2))
            usage_score = min(1.0, max(0.0, usage_bump / 20))
            pillar_4_context = (pace_score + usage_score) / 2
        
        # Calculate final score
        weights = WAR_ZONE_WEIGHTS
        final_score = (
            pillar_1_consistency * weights["ceiling_consistency"] +
            pillar_2_vegas * weights["vegas_probability"] +
            pillar_3_dvp * weights["dvp_matchup"] +
            pillar_4_context * weights["context_shift"]
        )
        
        # Calculate EV
        line = prop.get("line", 0)
        standard_line = prop.get("standard_line", line * 0.85)
        gap_pct = ((line - standard_line) / standard_line * 100) if standard_line > 0 else 0
        value_gap_pct = abs(gap_pct) / 100
        
        ev_score = final_score * (1 + value_gap_pct * 0.5)
        
        return {
            "pillar_1_consistency": pillar_1_consistency,
            "pillar_2_vegas": pillar_2_vegas,
            "pillar_3_dvp": pillar_3_dvp,
            "pillar_4_context": pillar_4_context,
            "dvp_label": get_dvp_label(pillar_3_dvp),
            "final_score": final_score,
            "final_score_100": final_score * 100,
            "ev_score": ev_score,
            "ev_score_100": ev_score * 100,
            "gap_pct": gap_pct,
            "value_gap_pct": value_gap_pct,
            "heat_level": calculate_heat_level(h10, h5, h10_over, h5_over, h10_games, h5_games),
        }
    
    # ==================== GOBLIN VAULT PICK SCORING ====================
    
    def score_goblin_vault_pick(
        self,
        prop: Dict,
        hit_rates: Dict,
        player_data: Dict,
        player_insights: Dict = None
    ) -> Dict[str, Any]:
        """
        Score a Goblin Vault (safe) pick.
        
        Goblins prioritize floor consistency over ceiling.
        """
        l10_data = hit_rates.get("l10", {})
        l5_data = hit_rates.get("l5", {})
        season_data = hit_rates.get("season", {})
        
        h10 = l10_data.get("hit_rate", 0)
        h5 = l5_data.get("hit_rate", 0)
        h10_over = l10_data.get("games_over", 0)
        h10_games = l10_data.get("total_games", 0)
        h5_over = l5_data.get("games_over", 0)
        h5_games = l5_data.get("total_games", 0)
        season_avg = season_data.get("avg", 0)
        
        # Pillar 1: Floor Consistency (50%) - Goblins need reliability
        pillar_1_floor = (h10 * 0.7) + (h5 * 0.3)
        
        # Pillar 2: Vegas Probability (20%)
        price = prop.get("price", -110)
        if price > 0:
            vegas_prob = 100 / (price + 100)
        else:
            vegas_prob = abs(price) / (abs(price) + 100)
        pillar_2_vegas = vegas_prob
        
        # Pillar 3: DvP Matchup (15%)
        opponent_team = player_data.get("opponent", "")
        stat_type = self.extract_stat_type(prop.get("market", ""))
        pillar_3_dvp = calculate_dvp_modifier(opponent_team, stat_type)
        
        # Pillar 4: Context (15%)
        pillar_4_context = 0.5
        if player_insights:
            pace_factor = player_insights.get("pace_factor", 1.0)
            usage_bump = player_insights.get("usage_bump", 0)
            pace_score = min(1.0, max(0.0, (pace_factor - 0.9) / 0.2))
            usage_score = min(1.0, max(0.0, usage_bump / 20))
            pillar_4_context = (pace_score + usage_score) / 2
        
        weights = GOBLIN_VAULT_WEIGHTS
        final_score = (
            pillar_1_floor * weights["floor_consistency"] +
            pillar_2_vegas * weights["vegas_probability"] +
            pillar_3_dvp * weights["dvp_matchup"] +
            pillar_4_context * weights["context_shift"]
        )
        
        line = prop.get("line", 0)
        standard_line = prop.get("standard_line", line * 1.15)
        gap_pct = ((standard_line - line) / standard_line * 100) if standard_line > 0 else 0
        
        ev_score = final_score * (1 + abs(gap_pct) / 100 * 0.3)
        
        return {
            "pillar_1_floor": pillar_1_floor,
            "pillar_2_vegas": pillar_2_vegas,
            "pillar_3_dvp": pillar_3_dvp,
            "pillar_4_context": pillar_4_context,
            "dvp_label": get_dvp_label(pillar_3_dvp),
            "final_score": final_score,
            "final_score_100": final_score * 100,
            "ev_score": ev_score,
            "ev_score_100": ev_score * 100,
            "gap_pct": gap_pct,
            "safety_level": calculate_safety_level(h10, h5, h10_over, h5_over, h10_games, h5_games),
        }
    
    # ==================== FRONT LINES PICK SCORING ====================
    
    def score_front_lines_pick(
        self,
        prop: Dict,
        hit_rates: Dict,
        player_data: Dict,
        is_demon: bool,
        player_insights: Dict = None
    ) -> Dict[str, Any]:
        """
        Score a Front Lines (mixed tier) pick.
        
        Front Lines contains both mild demons and goblins.
        """
        l10_data = hit_rates.get("l10", {})
        l5_data = hit_rates.get("l5", {})
        season_data = hit_rates.get("season", {})
        
        h10 = l10_data.get("hit_rate", 0)
        h5 = l5_data.get("hit_rate", 0)
        h10_over = l10_data.get("games_over", 0)
        h10_games = l10_data.get("total_games", 0)
        h5_over = l5_data.get("games_over", 0)
        h5_games = l5_data.get("total_games", 0)
        season_avg = season_data.get("avg", 0)
        
        # Pillar 1: Base Consistency (50%)
        pillar_1_base = (h10 * 0.6) + (h5 * 0.4)
        
        # Pillar 2: Vegas (20%)
        price = prop.get("price", 100 if is_demon else -110)
        if price > 0:
            vegas_prob = 100 / (price + 100)
        else:
            vegas_prob = abs(price) / (abs(price) + 100)
        pillar_2_vegas = vegas_prob
        
        # Pillar 3: DvP (15%)
        opponent_team = player_data.get("opponent", "")
        stat_type = self.extract_stat_type(prop.get("market", ""))
        pillar_3_dvp = calculate_dvp_modifier(opponent_team, stat_type)
        
        # Pillar 4: Context (15%)
        pillar_4_context = 0.5
        if player_insights:
            pace_factor = player_insights.get("pace_factor", 1.0)
            usage_bump = player_insights.get("usage_bump", 0)
            pace_score = min(1.0, max(0.0, (pace_factor - 0.9) / 0.2))
            usage_score = min(1.0, max(0.0, usage_bump / 20))
            pillar_4_context = (pace_score + usage_score) / 2
        
        weights = FRONT_LINES_WEIGHTS
        final_score = (
            pillar_1_base * weights["base_consistency"] +
            pillar_2_vegas * weights["vegas_probability"] +
            pillar_3_dvp * weights["dvp_matchup"] +
            pillar_4_context * weights["context_shift"]
        )
        
        line = prop.get("line", 0)
        if is_demon:
            standard_line = prop.get("standard_line", line * 0.85)
            gap_pct = ((line - standard_line) / standard_line * 100) if standard_line > 0 else 0
        else:
            standard_line = prop.get("standard_line", line * 1.15)
            gap_pct = ((standard_line - line) / standard_line * 100) if standard_line > 0 else 0
        
        value_gap_pct = abs(gap_pct) / 100
        ev_score = final_score * (1 + value_gap_pct * 0.4)
        
        return {
            "pillar_1_consistency": pillar_1_base,
            "pillar_2_vegas": pillar_2_vegas,
            "pillar_3_dvp": pillar_3_dvp,
            "pillar_4_context": pillar_4_context,
            "dvp_label": get_dvp_label(pillar_3_dvp),
            "frontlines_score": final_score,
            "frontlines_score_100": final_score * 100,
            "final_ev_score": ev_score,
            "final_ev_score_100": ev_score * 100,
            "gap_pct": gap_pct,
            "value_gap_pct": value_gap_pct,
            "bullet_level": calculate_bullet_level(h10, h5, h10_over, h5_over, h10_games, h5_games),
        }
    
    # ==================== FULL PICK BUILDER ====================
    
    async def build_scored_pick(
        self,
        prop: Dict,
        player_data: Dict,
        tier: str = "war_zone",
        sync_time: datetime = None
    ) -> Dict[str, Any]:
        """
        Build a fully scored pick with all metadata.
        
        Args:
            prop: Raw prop data from Odds API
            player_data: Player data from cached board
            tier: "war_zone", "goblin_vault", or "front_lines"
            sync_time: Sync timestamp
        """
        if sync_time is None:
            sync_time = datetime.now(timezone.utc)
        
        player_name = prop.get("player_name") or player_data.get("player_name", "")
        stat_type = self.extract_stat_type(prop.get("market", ""))
        line = prop.get("line", 0)
        
        # Classify prop
        is_demon, is_goblin, prop_type = self.classify_prop(prop)
        
        # Get hit rates
        hit_rates = await self.calculate_prop_hit_rates(player_name, stat_type, line)
        
        # Get insights if available
        normalized = sanitize_player_name(player_name)
        insights_doc = await self.daily_insights.find_one(
            {"normalized_name": normalized},
            {"_id": 0}
        )
        
        # Score based on tier
        if tier == "war_zone":
            scores = self.score_war_zone_pick(prop, hit_rates, player_data, insights_doc)
        elif tier == "goblin_vault":
            scores = self.score_goblin_vault_pick(prop, hit_rates, player_data, insights_doc)
        else:
            scores = self.score_front_lines_pick(prop, hit_rates, player_data, is_demon, insights_doc)
        
        # Extract hit rate details
        l10_data = hit_rates.get("l10", {})
        l5_data = hit_rates.get("l5", {})
        season_data = hit_rates.get("season", {})
        
        h10 = l10_data.get("hit_rate", 0)
        h5 = l5_data.get("hit_rate", 0)
        
        # Build final pick document
        pick = {
            # Player info
            "player_name": player_name,
            "player_id": player_data.get("player_id"),
            "team": player_data.get("team"),
            "team_name": player_data.get("team_name"),
            "photo_url": player_data.get("photo_url") or player_data.get("headshot_url"),
            "headshot_url": player_data.get("headshot_url"),
            "nba_id": player_data.get("nba_id") or player_data.get("nba_com_id"),
            "opponent": player_data.get("opponent"),
            "opponent_abbr": player_data.get("opponent_abbr"),
            "position": player_data.get("position"),
            
            # Prop data
            "stat_type": stat_type,
            "line": line,
            "direction": prop.get("direction", "Over"),
            "price": prop.get("price", 100 if is_demon else -110),
            "market": prop.get("market", ""),
            
            # Classification
            "is_demon": is_demon,
            "is_goblin": is_goblin,
            "prop_type": prop_type,
            "demon_line": line if is_demon else None,
            "goblin_line": line if is_goblin else None,
            "standard_line": prop.get("standard_line", line),
            
            # Hit rates
            "h10_rate": round(h10 * 100, 1),
            "h5_rate": round(h5 * 100, 1),
            "h10_over": l10_data.get("games_over", 0),
            "h10_games": l10_data.get("total_games", 0),
            "h5_over": l5_data.get("games_over", 0),
            "h5_games": l5_data.get("total_games", 0),
            "season_avg": round(season_data.get("avg", 0), 1),
            "hit_probability": round(h10 * 100, 1),
            
            # Scores (from tier-specific scoring)
            **scores,
            
            # AI confidence
            "ai_confidence_rating": insights_doc.get("ai_confidence_rating", 50) if insights_doc else 50,
            "insight_summary": insights_doc.get("insight_summary", "") if insights_doc else "",
            
            # Flags
            "volatility_flag": player_data.get("volatility_flag", False),
            "revenge_game": player_data.get("revenge_game", False),
            "is_verified": player_data.get("is_verified", False),
            "has_real_data": l10_data.get("total_games", 0) > 0,
            
            # Metadata
            "tier": tier,
            "synced_at": sync_time.isoformat()
        }
        
        return pick
