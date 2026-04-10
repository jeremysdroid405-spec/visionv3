"""
MLB Vegas Killer Regression Model
==================================
Weighted Linear Regression model for MLB prop predictions.

Process:
1. For each player on today's slate, run weighted linear regression
2. Target (y): Specific prop stat (Total Bases, Strikeouts, etc.)
3. Inputs (x): Game Recency Weight, Opponent Rank, Park Factor
4. Outputs: projected_value, r_squared, std_error

Edge Calculation:
- Edge = (Projected - Line) / Line
- High-Value: Edge > 15% AND r_squared > 0.60

Ferrari Tier Distribution:
- Safe Haven: Edge > 20% + r_squared > 0.75 + L10 Hit Rate > 70%
- Front Lines: Edge > 15% + r_squared > 0.60
- War Zone: Edge > 25% + r_squared < 0.40 (High risk/reward)

Vision Intel:
- Safe Haven picks sent to Gemini 3.1 Pro for final context check
"""

import os
import logging
import math
import numpy as np
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.db_config import get_collection_name

logger = logging.getLogger(__name__)

# Season weights for regression (recency-weighted)
SEASON_WEIGHTS = {
    2026: 1.0,
    2025: 0.85,
    2024: 0.70,
    2023: 0.55,
    2022: 0.40,
    2021: 0.25,
}

# Stat type mapping from Odds API prop names to internal field names
STAT_FIELD_MAPPING = {
    "Total Bases": "total_bases",
    "Hits": "hits",
    "RBIs": "rbis",
    "Runs": "runs",
    "Stolen Bases": "stolen_bases",
    "Strikeouts": "pitcher_strikeouts",  # For pitchers
    "Batter Strikeouts": "strikeouts",   # For batters
    "Walks Allowed": "pitcher_walks",
    "Hits Allowed": "hits_allowed",
    "Home Runs": "home_runs",
}

# MLB Park Factors (2026 estimates - higher = more hitter-friendly)
# Source: Based on historical park factors
PARK_FACTORS = {
    "COL": 1.15,  # Coors Field - most hitter-friendly
    "CIN": 1.08,  # Great American Ball Park
    "TEX": 1.06,  # Globe Life Field
    "BOS": 1.05,  # Fenway Park
    "PHI": 1.04,  # Citizens Bank Park
    "BAL": 1.03,  # Camden Yards
    "CHC": 1.02,  # Wrigley Field
    "MIL": 1.02,  # American Family Field
    "NYY": 1.01,  # Yankee Stadium
    "MIN": 1.01,  # Target Field
    "ATL": 1.00,  # Truist Park (neutral)
    "LAA": 1.00,  # Angel Stadium
    "HOU": 0.99,  # Minute Maid Park
    "CLE": 0.99,  # Progressive Field
    "DET": 0.98,  # Comerica Park
    "WSH": 0.98,  # Nationals Park
    "ARI": 0.98,  # Chase Field
    "TOR": 0.97,  # Rogers Centre
    "NYM": 0.97,  # Citi Field
    "CHW": 0.97,  # Guaranteed Rate Field
    "KC": 0.96,   # Kauffman Stadium
    "STL": 0.96,  # Busch Stadium
    "LAD": 0.95,  # Dodger Stadium
    "SD": 0.94,   # Petco Park
    "PIT": 0.94,  # PNC Park
    "SF": 0.93,   # Oracle Park
    "TB": 0.92,   # Tropicana Field
    "SEA": 0.91,  # T-Mobile Park
    "MIA": 0.90,  # loanDepot park
    "OAK": 0.90,  # Oakland Coliseum
}

# Opponent strength rankings (1 = best defense, 30 = worst)
# Updated dynamically but defaults provided
DEFAULT_OPPONENT_RANKS = {team: 15 for team in PARK_FACTORS.keys()}

# Tier thresholds
SAFE_HAVEN_THRESHOLDS = {
    "edge_min": 0.20,        # 20% edge
    "r_squared_min": 0.75,   # High confidence
    "hit_rate_min": 0.70,    # 70% L10 hit rate
}

FRONT_LINES_THRESHOLDS = {
    "edge_min": 0.15,        # 15% edge
    "r_squared_min": 0.60,   # Good confidence
}

WAR_ZONE_THRESHOLDS = {
    "edge_min": 0.25,        # 25% edge
    "r_squared_max": 0.40,   # Low confidence (high variance)
}


class MLBVKRegressionModel:
    """
    MLB Vegas Killer Regression Model.
    
    Runs weighted linear regression on historical game logs to project
    today's prop values and calculate edges vs sportsbook lines.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self._opponent_ranks: Dict[str, int] = DEFAULT_OPPONENT_RANKS.copy()
    
    def _get_collection(self, base_name: str):
        """Get MLB-specific collection."""
        return self.db[get_collection_name(base_name, "mlb")]
    
    # =========================================================================
    # WEIGHTED LINEAR REGRESSION
    # =========================================================================
    
    def weighted_linear_regression(
        self,
        x_values: List[float],
        y_values: List[float],
        weights: List[float]
    ) -> Dict[str, Any]:
        """
        Perform weighted linear regression (y = mx + b).
        
        Args:
            x_values: Independent variable values (e.g., game index)
            y_values: Dependent variable values (the stat)
            weights: Weight for each observation (recency weights)
            
        Returns:
            Dict with slope, intercept, r_squared, std_error, projected_value
        """
        if len(x_values) < 3 or len(y_values) < 3:
            return {
                "slope": None,
                "intercept": None,
                "r_squared": None,
                "std_error": None,
                "projected_value": None,
                "sample_size": len(x_values),
                "valid": False
            }
        
        # Convert to numpy arrays
        x = np.array(x_values, dtype=float)
        y = np.array(y_values, dtype=float)
        w = np.array(weights, dtype=float)
        
        # Normalize weights
        w = w / np.sum(w)
        
        # Weighted means
        x_mean = np.sum(w * x)
        y_mean = np.sum(w * y)
        
        # Weighted covariance and variance
        cov_xy = np.sum(w * (x - x_mean) * (y - y_mean))
        var_x = np.sum(w * (x - x_mean) ** 2)
        var_y = np.sum(w * (y - y_mean) ** 2)
        
        if var_x == 0:
            return {
                "slope": 0,
                "intercept": y_mean,
                "r_squared": 0,
                "std_error": None,
                "projected_value": y_mean,
                "sample_size": len(x_values),
                "valid": False
            }
        
        # Calculate slope and intercept
        slope = cov_xy / var_x
        intercept = y_mean - slope * x_mean
        
        # R-squared (coefficient of determination)
        y_pred = slope * x + intercept
        ss_res = np.sum(w * (y - y_pred) ** 2)
        ss_tot = var_y if var_y > 0 else 1
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
        r_squared = max(0, min(1, r_squared))  # Clamp to [0, 1]
        
        # Standard error of the estimate
        n = len(x_values)
        if n > 2:
            std_error = math.sqrt(ss_res / (n - 2))
        else:
            std_error = None
        
        # Project next value (x = n, the next game)
        projected_value = slope * (n + 1) + intercept
        projected_value = max(0, projected_value)  # Can't be negative
        
        return {
            "slope": round(slope, 4),
            "intercept": round(intercept, 4),
            "r_squared": round(r_squared, 4),
            "std_error": round(std_error, 4) if std_error else None,
            "projected_value": round(projected_value, 3),
            "sample_size": n,
            "valid": True
        }
    
    # =========================================================================
    # PROJECTION CALCULATION
    # =========================================================================
    
    async def calculate_player_projection(
        self,
        player_id: int,
        stat_type: str,
        opponent_abbr: str = None,
        venue_team: str = None
    ) -> Dict[str, Any]:
        """
        Calculate projection for a specific player and stat.
        
        Uses weighted linear regression on historical game logs with:
        - Game recency weights (more recent = higher weight)
        - Opponent strength adjustment
        - Park factor adjustment
        
        Args:
            player_id: BDL player ID
            stat_type: Prop stat type (e.g., "Total Bases", "Strikeouts")
            opponent_abbr: Opponent team abbreviation for adjustment
            venue_team: Home team abbreviation for park factor
            
        Returns:
            Projection data including projected_value, r_squared, adjustments
        """
        # Get player from master hub
        master_hub = self._get_collection("master_hub")
        player = await master_hub.find_one(
            {"bdl_id": player_id},
            {"_id": 0, "display_name": 1, "bdl_game_logs": 1, "vk_baselines": 1}
        )
        
        if not player:
            return {"valid": False, "error": "Player not found"}
        
        game_logs = player.get("bdl_game_logs", [])
        if not game_logs:
            return {"valid": False, "error": "No game logs"}
        
        # Get stat field name
        stat_field = STAT_FIELD_MAPPING.get(stat_type, stat_type.lower().replace(" ", "_"))
        
        # Extract values with recency weights
        x_values = []  # Game index (1, 2, 3, ...)
        y_values = []  # Stat values
        weights = []   # Recency weights
        
        # Sort logs by date (oldest first for regression)
        sorted_logs = sorted(
            [log for log in game_logs if log.get("date")],
            key=lambda x: x.get("date", ""),
            reverse=False  # Oldest first
        )
        
        for i, log in enumerate(sorted_logs):
            value = log.get(stat_field)
            if value is None:
                continue
            
            try:
                value = float(value)
            except (ValueError, TypeError):
                continue
            
            # Get season weight
            season = log.get("season", 2026)
            season_weight = SEASON_WEIGHTS.get(season, 0.5)
            
            # Recency weight within season (more recent games weighted higher)
            recency_weight = (i + 1) / len(sorted_logs)  # 0.1 to 1.0
            
            combined_weight = season_weight * (0.5 + 0.5 * recency_weight)
            
            x_values.append(i + 1)
            y_values.append(value)
            weights.append(combined_weight)
        
        if len(x_values) < 3:
            return {"valid": False, "error": "Insufficient data points"}
        
        # Run weighted linear regression
        regression = self.weighted_linear_regression(x_values, y_values, weights)
        
        if not regression.get("valid"):
            return {"valid": False, "error": "Regression failed"}
        
        projected_value = regression["projected_value"]
        
        # Apply park factor adjustment
        park_factor = 1.0
        if venue_team:
            park_factor = PARK_FACTORS.get(venue_team.upper(), 1.0)
            projected_value *= park_factor
        
        # Apply opponent strength adjustment
        opponent_adjustment = 1.0
        if opponent_abbr:
            opp_rank = self._opponent_ranks.get(opponent_abbr.upper(), 15)
            # Weaker opponents (higher rank) = slight boost
            opponent_adjustment = 1.0 + (opp_rank - 15) * 0.005
            projected_value *= opponent_adjustment
        
        # Calculate L10 hit rate (for tier qualification)
        l10_values = y_values[-10:] if len(y_values) >= 10 else y_values
        
        return {
            "valid": True,
            "player_id": player_id,
            "player_name": player.get("display_name"),
            "stat_type": stat_type,
            "stat_field": stat_field,
            "projected_value": round(projected_value, 3),
            "raw_projection": regression["projected_value"],
            "r_squared": regression["r_squared"],
            "std_error": regression["std_error"],
            "slope": regression["slope"],
            "intercept": regression["intercept"],
            "sample_size": regression["sample_size"],
            "l10_values": l10_values,
            "l10_avg": round(sum(l10_values) / len(l10_values), 3) if l10_values else None,
            "adjustments": {
                "park_factor": park_factor,
                "opponent_adjustment": opponent_adjustment,
                "venue_team": venue_team,
                "opponent_abbr": opponent_abbr
            }
        }
    
    # =========================================================================
    # EDGE CALCULATION
    # =========================================================================
    
    def calculate_edge(
        self,
        projected_value: float,
        line: float
    ) -> Dict[str, Any]:
        """
        Calculate the VK Edge.
        
        Edge = (Projected - Line) / Line
        
        Args:
            projected_value: Model's projected value
            line: Sportsbook line (e.g., PrizePicks line)
            
        Returns:
            Edge data including percentage and direction
        """
        if line <= 0:
            return {
                "edge": 0,
                "edge_pct": 0,
                "direction": "NEUTRAL",
                "is_over": False
            }
        
        edge = (projected_value - line) / line
        edge_pct = edge * 100
        
        if edge > 0.05:
            direction = "OVER"
            is_over = True
        elif edge < -0.05:
            direction = "UNDER"
            is_over = False
        else:
            direction = "NEUTRAL"
            is_over = None
        
        return {
            "edge": round(edge, 4),
            "edge_pct": round(edge_pct, 2),
            "direction": direction,
            "is_over": is_over,
            "projected": projected_value,
            "line": line
        }
    
    def calculate_hit_rate(
        self,
        values: List[float],
        line: float,
        direction: str = "OVER"
    ) -> Optional[float]:
        """
        Calculate historical hit rate for a line.
        
        Args:
            values: Historical stat values
            line: The line to check against
            direction: "OVER" or "UNDER"
            
        Returns:
            Hit rate as decimal (0.0 to 1.0)
        """
        if not values:
            return None
        
        if direction == "OVER":
            hits = sum(1 for v in values if v > line)
        else:
            hits = sum(1 for v in values if v < line)
        
        return round(hits / len(values), 4)
    
    # =========================================================================
    # TIER CLASSIFICATION
    # =========================================================================
    
    def classify_tier(
        self,
        edge: float,
        r_squared: float,
        hit_rate: float
    ) -> str:
        """
        Classify a pick into Ferrari tiers.
        
        Safe Haven: Edge > 20% + r_squared > 0.75 + L10 Hit Rate > 70%
        Front Lines: Edge > 15% + r_squared > 0.60
        War Zone: Edge > 25% + r_squared < 0.40
        
        Args:
            edge: VK Edge (decimal, e.g., 0.20 for 20%)
            r_squared: Model confidence (0.0 to 1.0)
            hit_rate: L10 hit rate (0.0 to 1.0)
            
        Returns:
            Tier name: "SAFE_HAVEN", "FRONT_LINES", "WAR_ZONE", or "DISCARDED"
        """
        abs_edge = abs(edge)
        
        # Safe Haven: High edge + High confidence + High hit rate
        if (abs_edge >= SAFE_HAVEN_THRESHOLDS["edge_min"] and
            r_squared >= SAFE_HAVEN_THRESHOLDS["r_squared_min"] and
            hit_rate is not None and hit_rate >= SAFE_HAVEN_THRESHOLDS["hit_rate_min"]):
            return "SAFE_HAVEN"
        
        # War Zone: Very high edge but low confidence (risky)
        if (abs_edge >= WAR_ZONE_THRESHOLDS["edge_min"] and
            r_squared < WAR_ZONE_THRESHOLDS["r_squared_max"]):
            return "WAR_ZONE"
        
        # Front Lines: Good edge + Good confidence
        if (abs_edge >= FRONT_LINES_THRESHOLDS["edge_min"] and
            r_squared >= FRONT_LINES_THRESHOLDS["r_squared_min"]):
            return "FRONT_LINES"
        
        return "DISCARDED"
    
    # =========================================================================
    # SLATE PROCESSING
    # =========================================================================
    
    async def process_slate(self) -> Dict[str, Any]:
        """
        Process all props on today's MLB slate.
        
        1. Fetch live props from mlb_live_props
        2. Run regression for each player/stat combo
        3. Calculate edges
        4. Classify into tiers
        
        Returns:
            Processing results with tiered picks
        """
        logger.info("=" * 70)
        logger.info("[MLB_VK_REGRESSION] Processing Today's MLB Slate")
        logger.info("=" * 70)
        
        start_time = datetime.now(timezone.utc)
        
        results = {
            "success": True,
            "processed_at": start_time.isoformat(),
            "props_processed": 0,
            "projections_valid": 0,
            "high_value_picks": 0,
            "tiers": {
                "safe_haven": [],
                "front_lines": [],
                "war_zone": [],
                "discarded": 0
            },
            "errors": []
        }
        
        try:
            # Fetch live props
            live_props = self._get_collection("live_props")
            props = await live_props.find({}, {"_id": 0}).to_list(length=None)
            
            results["props_processed"] = len(props)
            logger.info(f"[MLB_VK_REGRESSION] Found {len(props)} live props")
            
            if not props:
                logger.warning("[MLB_VK_REGRESSION] No live props found")
                return results
            
            # Get master hub for player lookup
            master_hub = self._get_collection("master_hub")
            
            # Group props by player for efficiency
            player_props: Dict[str, List[Dict]] = defaultdict(list)
            for prop in props:
                player_name = prop.get("player_name", "")
                if player_name:
                    player_props[player_name].append(prop)
            
            logger.info(f"[MLB_VK_REGRESSION] Processing {len(player_props)} unique players")
            
            # Process each player
            for player_name, player_prop_list in player_props.items():
                # Find player in master hub
                player = await master_hub.find_one(
                    {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
                    {"_id": 0, "bdl_id": 1, "display_name": 1, "bdl_game_logs": 1, "team_abbr": 1}
                )
                
                if not player:
                    # Try partial match
                    player = await master_hub.find_one(
                        {"display_name": {"$regex": player_name, "$options": "i"}},
                        {"_id": 0, "bdl_id": 1, "display_name": 1, "bdl_game_logs": 1, "team_abbr": 1}
                    )
                
                if not player or not player.get("bdl_id"):
                    continue
                
                player_id = player["bdl_id"]
                
                # Process each prop for this player
                for prop in player_prop_list:
                    stat_type = prop.get("stat_type", "")
                    line = prop.get("line", 0)
                    
                    if not stat_type or not line:
                        continue
                    
                    # Get opponent and venue from prop
                    opponent = prop.get("opponent_abbr") or prop.get("away_team")
                    venue = prop.get("home_team")
                    
                    # Calculate projection
                    projection = await self.calculate_player_projection(
                        player_id=player_id,
                        stat_type=stat_type,
                        opponent_abbr=opponent,
                        venue_team=venue
                    )
                    
                    if not projection.get("valid"):
                        continue
                    
                    results["projections_valid"] += 1
                    
                    # Calculate edge
                    edge_data = self.calculate_edge(
                        projection["projected_value"],
                        line
                    )
                    
                    # Calculate hit rate
                    l10_values = projection.get("l10_values", [])
                    hit_rate = self.calculate_hit_rate(
                        l10_values,
                        line,
                        edge_data["direction"]
                    )
                    
                    # Check if high-value pick
                    abs_edge = abs(edge_data["edge"])
                    r_squared = projection["r_squared"]
                    
                    if abs_edge > 0.15 and r_squared > 0.60:
                        results["high_value_picks"] += 1
                    
                    # Classify tier
                    tier = self.classify_tier(
                        edge_data["edge"],
                        r_squared,
                        hit_rate
                    )
                    
                    # Build pick object
                    pick = {
                        "player_name": player_name,
                        "player_id": player_id,
                        "stat_type": stat_type,
                        "line": line,
                        "projected_value": projection["projected_value"],
                        "edge": edge_data["edge"],
                        "edge_pct": edge_data["edge_pct"],
                        "direction": edge_data["direction"],
                        "r_squared": r_squared,
                        "std_error": projection["std_error"],
                        "hit_rate_l10": hit_rate,
                        "l10_avg": projection["l10_avg"],
                        "sample_size": projection["sample_size"],
                        "tier": tier,
                        "adjustments": projection["adjustments"],
                        "prop_data": {
                            "bookmaker": prop.get("bookmaker"),
                            "event_id": prop.get("event_id"),
                            "home_team": prop.get("home_team"),
                            "away_team": prop.get("away_team"),
                            "commence_time": prop.get("commence_time"),
                        },
                        "created_at": datetime.now(timezone.utc).isoformat()
                    }
                    
                    # Add to appropriate tier
                    if tier == "SAFE_HAVEN":
                        results["tiers"]["safe_haven"].append(pick)
                    elif tier == "FRONT_LINES":
                        results["tiers"]["front_lines"].append(pick)
                    elif tier == "WAR_ZONE":
                        results["tiers"]["war_zone"].append(pick)
                    else:
                        results["tiers"]["discarded"] += 1
            
            # Sort tiers by edge
            for tier_name in ["safe_haven", "front_lines", "war_zone"]:
                results["tiers"][tier_name].sort(
                    key=lambda x: abs(x["edge"]),
                    reverse=True
                )
            
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            results["duration_seconds"] = round(duration, 2)
            
            logger.info("[MLB_VK_REGRESSION] Slate Processing Complete:")
            logger.info(f"  • Props Processed: {results['props_processed']}")
            logger.info(f"  • Valid Projections: {results['projections_valid']}")
            logger.info(f"  • High-Value Picks: {results['high_value_picks']}")
            logger.info(f"  • Safe Haven: {len(results['tiers']['safe_haven'])}")
            logger.info(f"  • Front Lines: {len(results['tiers']['front_lines'])}")
            logger.info(f"  • War Zone: {len(results['tiers']['war_zone'])}")
            logger.info(f"  • Discarded: {results['tiers']['discarded']}")
            
        except Exception as e:
            logger.error(f"[MLB_VK_REGRESSION] Processing error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results["success"] = False
            results["errors"].append(str(e))
        
        return results
    
    # =========================================================================
    # SAVE TO FERRARI COLLECTIONS
    # =========================================================================
    
    async def save_to_ferrari_collections(
        self,
        tier_results: Dict[str, List[Dict]]
    ) -> Dict[str, Any]:
        """
        Save tiered picks to MLB Ferrari collections.
        
        Args:
            tier_results: Dict with safe_haven, front_lines, war_zone lists
            
        Returns:
            Save summary
        """
        logger.info("[MLB_VK_REGRESSION] Saving to Ferrari collections...")
        
        save_results = {
            "safe_haven": 0,
            "front_lines": 0,
            "war_zone": 0
        }
        
        # Collection mapping
        tier_collections = {
            "safe_haven": self.db[get_collection_name("safe_haven", "mlb")],
            "front_lines": self.db[get_collection_name("front_lines", "mlb")],
            "war_zone": self.db[get_collection_name("war_zone", "mlb")]
        }
        
        for tier_name, picks in tier_results.items():
            if tier_name not in tier_collections:
                continue
            
            collection = tier_collections[tier_name]
            
            if picks:
                # Clear old picks
                await collection.delete_many({})
                
                # Insert new picks
                await collection.insert_many(picks)
                save_results[tier_name] = len(picks)
                
                logger.info(f"[MLB_VK_REGRESSION] Saved {len(picks)} to mlb_{tier_name}")
        
        return save_results


# Singleton
_mlb_vk_regression: Optional[MLBVKRegressionModel] = None


def get_mlb_vk_regression(db: AsyncIOMotorDatabase) -> MLBVKRegressionModel:
    """Get or create MLB VK Regression Model."""
    global _mlb_vk_regression
    if _mlb_vk_regression is None:
        _mlb_vk_regression = MLBVKRegressionModel(db)
    return _mlb_vk_regression


async def run_mlb_vk_slate_analysis(
    db: AsyncIOMotorDatabase,
    save_to_db: bool = True
) -> Dict[str, Any]:
    """
    Run full MLB VK slate analysis.
    
    1. Process all live props
    2. Calculate projections and edges
    3. Classify into tiers
    4. Save to Ferrari collections
    
    Returns:
        Analysis results
    """
    model = get_mlb_vk_regression(db)
    
    # Process slate
    results = await model.process_slate()
    
    # Save to DB
    if save_to_db and results.get("success"):
        save_results = await model.save_to_ferrari_collections(results["tiers"])
        results["saved"] = save_results
    
    return results
