"""
Ferrari Tier Builder V2 - SSOT Architecture

This service builds pick tiers using the Single Source of Truth model:
- BDL = SSOT for hit rates (calculated fresh from game_logs)
- Odds API = SSOT for props/lines

Key principles:
1. Hit rates are ALWAYS calculated fresh from BDL game logs
2. Never use cached/stale hit rate data
3. Variance and DNP detection are mandatory
4. DvP penalties use fresh BDL team stats
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple, Tuple
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# =============================================================================
# TIER THRESHOLDS
# =============================================================================

SAFE_HAVEN_THRESHOLDS = {
    "l5_min": 80,      # Minimum L5 hit rate
    "l10_min": 80,     # Minimum L10 hit rate  
    "variance_max": 25, # Maximum point spread in L10
    "dnp_max": 0,      # Maximum DNP games in L10
}

FRONT_LINES_THRESHOLDS = {
    "l5_min": 70,
    "l10_min": 70,
    "variance_max": 35,
    "dnp_max": 1,
}

WAR_ZONE_THRESHOLDS = {
    "l5_min": 50,
    "l10_min": 50,
    "variance_max": 50,  # Demons can have higher variance
    "dnp_max": 2,
}

# DvP penalty thresholds (rank 1-30, lower = better defense)
DVP_STRONG_DEFENSE = 10   # Top 10 defense = strong penalty
DVP_WEAK_DEFENSE = 20     # Bottom 10 defense = no penalty


# =============================================================================
# FERRARI TIER BUILDER V2
# =============================================================================

class FerrariTierBuilderV2:
    """
    Builds pick tiers using SSOT architecture.
    
    Flow:
    1. Get all props from Odds API
    2. For each prop, calculate fresh hit rates from BDL
    3. Apply DvP penalties from BDL team stats
    4. Score and rank picks
    5. Populate tier collections
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
    
    async def calculate_hit_rates(self, player_name: str, stat_type: str, line: float) -> Dict[str, Any]:
        """
        Calculate hit rates FRESH from BDL game logs.
        
        Returns dict with l5_rate, l10_rate, variance, dnp_count, etc.
        """
        # Normalize stat type
        stat_key = stat_type.lower()
        stat_mapping = {
            "pts": "pts", "points": "pts",
            "reb": "reb", "rebounds": "reb",
            "ast": "ast", "assists": "ast",
            "pra": "pra", "points_rebounds_assists": "pra",
            "3pm": "fg3m", "threes": "fg3m",
            "blk": "blk", "blocks": "blk",
            "stl": "stl", "steals": "stl",
        }
        stat_key = stat_mapping.get(stat_key, stat_key)
        
        # Find player in master hub
        player = await self.db.nba_master_hub_2026.find_one({
            "$or": [
                {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
                {"display_name": {"$regex": player_name, "$options": "i"}},
            ]
        })
        
        if not player:
            return {"error": f"Player not found: {player_name}"}
        
        game_logs = player.get("bdl_game_logs", []) or []
        
        if not game_logs:
            return {"error": f"No game logs for: {player_name}"}
        
        # Calculate PRA if needed
        if stat_key == "pra":
            for log in game_logs:
                log["pra"] = (log.get("pts") or 0) + (log.get("reb") or 0) + (log.get("ast") or 0)
        
        # Get stat values
        def get_values(games, stat):
            return [g.get(stat, 0) or 0 for g in games]
        
        l5_values = get_values(game_logs[:5], stat_key)
        l10_values = get_values(game_logs[:10], stat_key)
        l20_values = get_values(game_logs[:20], stat_key)
        
        # Calculate hit rates
        def calc_hit_rate(values, line):
            if not values:
                return 0.0
            hits = sum(1 for v in values if v > line)
            return (hits / len(values)) * 100
        
        l5_rate = calc_hit_rate(l5_values, line)
        l10_rate = calc_hit_rate(l10_values, line)
        l20_rate = calc_hit_rate(l20_values, line)
        
        # Calculate averages
        l5_avg = sum(l5_values) / len(l5_values) if l5_values else 0
        l10_avg = sum(l10_values) / len(l10_values) if l10_values else 0
        
        # Calculate variance (max - min in L10)
        variance = max(l10_values) - min(l10_values) if l10_values else 0
        
        # Count DNP games (0 minutes)
        dnp_count = sum(1 for g in game_logs[:10] if str(g.get("min", "0")) in ["0", "00", "0:00", ""])
        
        return {
            "player_name": player.get("display_name"),
            "bdl_id": player.get("bdl_id"),
            "team": player.get("team"),
            "stat_type": stat_type,
            "line": line,
            "l5_rate": round(l5_rate, 1),
            "l10_rate": round(l10_rate, 1),
            "l20_rate": round(l20_rate, 1),
            "l5_avg": round(l5_avg, 1),
            "l10_avg": round(l10_avg, 1),
            "variance_l10": round(variance, 1),
            "dnp_count_l10": dnp_count,
            "games_analyzed": len(game_logs),
            "calculated_at": datetime.now(timezone.utc).isoformat()
        }
    
    async def get_dvp_penalty(self, opponent_team: str, stat_type: str) -> float:
        """
        Get DvP penalty based on opponent's defensive ranking.
        
        Returns negative penalty (reduces score) for strong defenses.
        """
        # Get DVP rankings
        dvp_doc = await self.db.dvp_rankings.find_one({"type": "dvp_rankings"})
        
        if not dvp_doc:
            return 0.0
        
        rankings = dvp_doc.get("rankings", {})
        stat_rankings = rankings.get(stat_type.upper(), {})
        
        rank = stat_rankings.get(opponent_team, 15)  # Default middle rank
        
        # Calculate penalty
        if rank <= DVP_STRONG_DEFENSE:
            # Top 10 defense = -3.5 to -5 penalty
            return -3.5 - (DVP_STRONG_DEFENSE - rank) * 0.15
        elif rank >= DVP_WEAK_DEFENSE:
            # Bottom 10 defense = small boost
            return 1.0
        else:
            return 0.0
    
    def calculate_board_score(self, hit_rates: Dict, prop: Dict, dvp_penalty: float, is_demon: bool = False) -> Tuple[float, Dict]:
        """
        Calculate board score for ranking using multi-book sharp data.
        
        Formula: 
        Score = Hit_Rate_Avg + Sharp_Edge + Book_Consensus_Bonus - Penalties
        
        Sharp Edge = How much the hit rate exceeds the implied probability from odds
        Book Consensus = Bonus if multiple books agree (lower spread)
        
        Returns: (score, edge_details)
        """
        l5 = hit_rates.get("l5_rate", 0)
        l10 = hit_rates.get("l10_rate", 0)
        l10_avg = hit_rates.get("l10_avg", 0)
        line = hit_rates.get("line", 0)
        variance = hit_rates.get("variance_l10", 0)
        
        # Get multi-book data
        sharp_line = prop.get("sharp_line", line)
        consensus_line = prop.get("consensus_line", line)
        avg_line = prop.get("avg_line", line)
        books_count = prop.get("books_count", 1)
        line_spread = prop.get("line_spread", 0)
        odds = prop.get("odds", -110)
        
        # Calculate implied probability from odds
        if odds < 0:
            implied_prob = abs(odds) / (abs(odds) + 100) * 100
        else:
            implied_prob = 100 / (odds + 100) * 100
        
        # Base score from hit rates
        hit_rate_avg = (l5 + l10) / 2
        
        # Sharp Edge: How much our hit rate beats the implied probability
        sharp_edge = hit_rate_avg - implied_prob
        
        # Line Value Edge: How much average exceeds the sharp line
        line_value = ((l10_avg - sharp_line) / sharp_line * 100) if sharp_line > 0 else 0
        
        # Book Consensus Bonus: More books = more reliable line
        # Low spread between books = sharper/more accurate line
        consensus_bonus = 0
        if books_count >= 3:
            consensus_bonus += 2
        if books_count >= 5:
            consensus_bonus += 3
        if line_spread <= 1:
            consensus_bonus += 3  # Books strongly agree
        elif line_spread <= 2:
            consensus_bonus += 1
        
        # Variance penalty (high variance = risky)
        variance_penalty = 0
        if variance > 30:
            variance_penalty = (variance - 30) * 0.5
        elif variance > 20:
            variance_penalty = (variance - 20) * 0.3
        
        # Calculate final score
        score = hit_rate_avg + sharp_edge + line_value + consensus_bonus + dvp_penalty - variance_penalty
        
        edge_details = {
            "hit_rate_avg": round(hit_rate_avg, 1),
            "implied_prob": round(implied_prob, 1),
            "sharp_edge": round(sharp_edge, 1),
            "line_value": round(line_value, 1),
            "consensus_bonus": consensus_bonus,
            "variance_penalty": round(variance_penalty, 1),
            "dvp_penalty": dvp_penalty,
            "books_count": books_count,
            "line_spread": line_spread
        }
        
        return round(score, 2), edge_details
    
    def classify_tier(self, hit_rates: Dict, is_demon: bool = False) -> Optional[str]:
        """
        Determine which tier a pick belongs to based on hit rates.
        
        Returns: 'safe_haven', 'front_lines', 'war_zone', or None (rejected)
        """
        l5 = hit_rates.get("l5_rate", 0)
        l10 = hit_rates.get("l10_rate", 0)
        variance = hit_rates.get("variance_l10", 0)
        dnp = hit_rates.get("dnp_count_l10", 0)
        
        # Check Safe Haven
        if (l5 >= SAFE_HAVEN_THRESHOLDS["l5_min"] and
            l10 >= SAFE_HAVEN_THRESHOLDS["l10_min"] and
            variance <= SAFE_HAVEN_THRESHOLDS["variance_max"] and
            dnp <= SAFE_HAVEN_THRESHOLDS["dnp_max"]):
            return "safe_haven"
        
        # Check Front Lines
        if (l5 >= FRONT_LINES_THRESHOLDS["l5_min"] and
            l10 >= FRONT_LINES_THRESHOLDS["l10_min"] and
            variance <= FRONT_LINES_THRESHOLDS["variance_max"] and
            dnp <= FRONT_LINES_THRESHOLDS["dnp_max"]):
            return "front_lines"
        
        # Check War Zone (more lenient for Demons)
        if is_demon or (l5 >= WAR_ZONE_THRESHOLDS["l5_min"] and
                       l10 >= WAR_ZONE_THRESHOLDS["l10_min"]):
            return "war_zone"
        
        return None  # Rejected
    
    async def build_tiers(self) -> Dict[str, Any]:
        """
        Build all tiers from fresh SSOT data.
        
        1. Get all props from Odds API
        2. Calculate fresh hit rates for each
        3. Apply DvP penalties
        4. Score and rank
        5. Populate tier collections
        """
        logger.info("=" * 60)
        logger.info("[FERRARI V2] Building tiers from SSOT")
        logger.info("=" * 60)
        
        # Get all props from Odds API
        props_cursor = self.db.odds_api_props.find({})
        all_props = await props_cursor.to_list(length=2000)
        
        logger.info(f"[FERRARI V2] Processing {len(all_props)} props from Odds API")
        
        # Process each prop
        safe_haven = []
        front_lines = []
        war_zone = []
        rejected = []
        
        # Group props by player to avoid duplicate API calls
        props_by_player = {}
        for prop in all_props:
            player_name = prop.get("player_name", "")
            if player_name not in props_by_player:
                props_by_player[player_name] = []
            props_by_player[player_name].append(prop)
        
        logger.info(f"[FERRARI V2] {len(props_by_player)} unique players")
        
        processed = 0
        for player_name, player_props in props_by_player.items():
            for prop in player_props:
                stat_type = prop.get("stat_type", "")
                line = prop.get("line", 0)
                
                if not stat_type or not line:
                    continue
                
                # Calculate fresh hit rates from BDL
                hit_rates = await self.calculate_hit_rates(player_name, stat_type, line)
                
                if "error" in hit_rates:
                    continue
                
                # Get opponent and DvP penalty
                opponent = self._extract_opponent(prop, hit_rates.get("team", ""))
                dvp_penalty = await self.get_dvp_penalty(opponent, stat_type)
                
                # Calculate board score with multi-book data
                board_score, edge_details = self.calculate_board_score(hit_rates, prop, dvp_penalty)
                
                # Classify tier
                tier = self.classify_tier(hit_rates)
                
                # Build pick document
                pick = {
                    "player_name": hit_rates.get("player_name"),
                    "bdl_id": hit_rates.get("bdl_id"),
                    "team": hit_rates.get("team"),
                    "stat_type": stat_type,
                    "line": line,
                    "sharp_line": prop.get("sharp_line", line),
                    "consensus_line": prop.get("consensus_line", line),
                    "line_spread": prop.get("line_spread", 0),
                    "l5_rate": hit_rates.get("l5_rate"),
                    "l10_rate": hit_rates.get("l10_rate"),
                    "l5_avg": hit_rates.get("l5_avg"),
                    "l10_avg": hit_rates.get("l10_avg"),
                    "variance_l10": hit_rates.get("variance_l10"),
                    "dnp_count_l10": hit_rates.get("dnp_count_l10"),
                    "dvp_penalty": dvp_penalty,
                    "opponent": opponent,
                    "board_score": board_score,
                    "edge_details": edge_details,
                    "implied_prob": edge_details.get("implied_prob"),
                    "sharp_edge": edge_details.get("sharp_edge"),
                    "books_count": prop.get("books_count", 1),
                    "odds": prop.get("odds"),
                    "book": prop.get("book"),
                    "game_id": prop.get("game_id"),
                    "commence_time": prop.get("commence_time"),
                    "synced_at": datetime.now(timezone.utc),
                    "source": "BDL_SSOT"
                }
                
                # Add to appropriate tier
                if tier == "safe_haven":
                    safe_haven.append(pick)
                elif tier == "front_lines":
                    front_lines.append(pick)
                elif tier == "war_zone":
                    war_zone.append(pick)
                else:
                    rejected.append(pick)
                
                processed += 1
        
        logger.info(f"[FERRARI V2] Processed {processed} props")
        logger.info(f"[FERRARI V2] Safe Haven: {len(safe_haven)}, Front Lines: {len(front_lines)}, War Zone: {len(war_zone)}, Rejected: {len(rejected)}")
        
        # Sort by board score and take top 10 for each tier
        safe_haven.sort(key=lambda x: x.get("board_score", 0), reverse=True)
        front_lines.sort(key=lambda x: x.get("board_score", 0), reverse=True)
        war_zone.sort(key=lambda x: x.get("board_score", 0), reverse=True)
        
        # Deduplicate by player (1 pick per player per tier)
        safe_haven = self._dedupe_by_player(safe_haven)[:10]
        front_lines = self._dedupe_by_player(front_lines)[:10]
        war_zone = self._dedupe_by_player(war_zone)[:10]
        
        # Save to collections
        await self._save_tier("ferrari_safe_haven", safe_haven)
        await self._save_tier("ferrari_front_lines", front_lines)
        await self._save_tier("ferrari_war_zone", war_zone)
        
        # Save rejected for analysis
        if rejected:
            await self.db.ferrari_discarded.delete_many({})
            await self.db.ferrari_discarded.insert_many(rejected[:100])  # Keep top 100 rejected
        
        logger.info("=" * 60)
        logger.info("[FERRARI V2] Tier build complete")
        logger.info("=" * 60)
        
        return {
            "success": True,
            "safe_haven": len(safe_haven),
            "front_lines": len(front_lines),
            "war_zone": len(war_zone),
            "rejected": len(rejected),
            "processed": processed,
            "built_at": datetime.now(timezone.utc).isoformat()
        }
    
    def _extract_opponent(self, prop: Dict, player_team: str) -> str:
        """Extract opponent team from prop."""
        home = prop.get("home_team", "")
        away = prop.get("away_team", "")
        
        if player_team == home:
            return away
        elif player_team == away:
            return home
        else:
            return away or home or "UNK"
    
    def _dedupe_by_player(self, picks: List[Dict]) -> List[Dict]:
        """Keep only highest-scoring pick per player."""
        seen = set()
        deduped = []
        for pick in picks:
            player = pick.get("player_name", "")
            if player not in seen:
                seen.add(player)
                deduped.append(pick)
        return deduped
    
    async def _save_tier(self, collection_name: str, picks: List[Dict]):
        """Save picks to tier collection."""
        await self.db[collection_name].delete_many({})
        if picks:
            await self.db[collection_name].insert_many(picks)
        logger.info(f"[FERRARI V2] Saved {len(picks)} picks to {collection_name}")


# =============================================================================
# SINGLETON
# =============================================================================

_tier_builder: Optional[FerrariTierBuilderV2] = None

def get_tier_builder(db: AsyncIOMotorDatabase = None) -> FerrariTierBuilderV2:
    """Get or create the FerrariTierBuilderV2 singleton."""
    global _tier_builder
    if _tier_builder is None and db is not None:
        _tier_builder = FerrariTierBuilderV2(db)
    return _tier_builder
