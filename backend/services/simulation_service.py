"""
Command Post Simulation Service
================================
Risk Assessment Hub for parlay simulation.

Terminology:
- "Certainty" -> "Convergence Rate" / "Tactical Probability"
- "Success Meter" -> "Infiltration Grade"
- High variance -> "Volatility Index"

Grades:
- S-Tier: High-Alpha / Optimal Alignment (Convergence 75%+)
- A-Tier: Strong Tactical Position (65-74%)
- B-Tier: Standard Tactical Exposure (55-64%)
- C-Tier: Elevated Friction (45-54%)
- D-Tier: High-Friction / Volatile Environment (<45%)
"""
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
from dataclasses import dataclass
import logging
import statistics

from services.dvp_service import (
    get_dvp_rank, 
    get_dvp_rank_color, 
    calculate_dvp_modifier
)

logger = logging.getLogger(__name__)


# ==================== CONSTANTS ====================

# Infiltration Grade Thresholds
GRADE_THRESHOLDS = {
    "A": 75,  # Optimal Alignment - Low Risk
    "B": 65,  # Strong Position - Moderate Risk  
    "C": 55,  # Standard Exposure - Average Risk
    "D": 45,  # Elevated Friction - Higher Risk
    "F": 0,   # High-Friction / Volatile - Maximum Risk
}

# Volatility Index Thresholds
VOLATILITY_HIGH = 0.25      # >25% std deviation = High Volatility
VOLATILITY_MEDIUM = 0.15    # 15-25% = Medium Volatility
VOLATILITY_LOW = 0.15       # <15% = Low Volatility

# Environmental Delta Factors
HOME_ADVANTAGE = 1.03       # +3% boost for home games
AWAY_PENALTY = 0.97         # -3% penalty for away games

# Correlation penalties
SAME_PLAYER_CORRELATION = 0.85  # 15% penalty for same player props
SAME_TEAM_CORRELATION = 0.95    # 5% penalty for same team props
SAME_GAME_CORRELATION = 0.90    # 10% penalty for same game props


# ==================== DATA STRUCTURES ====================

@dataclass
class SimulationLeg:
    """A single leg in a parlay configuration."""
    player_name: str
    player_id: Optional[str]
    stat_type: str
    line: float
    direction: str  # "over" or "under"
    team: str
    opponent: str
    game_id: Optional[str]
    is_home: bool
    
    # Calculated fields
    base_hit_rate: float = 0.0
    usage_ripple: float = 0.0
    environmental_delta: float = 1.0
    defensive_friction: float = 0.5
    dvp_rank: int = 15
    dvp_rank_color: str = "yellow"
    volatility_index: float = 0.0
    stability_index: int = 50  # NEW: 1-100 based on std deviation
    
    # Statistical fields for stability calculation
    season_avg: float = 0.0
    std_dev: float = 0.0
    l5_avg: float = 0.0
    l10_avg: float = 0.0
    
    # Final tactical probability
    tactical_probability: float = 0.0
    volatility_label: str = "Medium"
    friction_label: str = "Standard"
    stability_label: str = "Moderate"  # NEW


@dataclass
class SimulationResult:
    """Result of a parlay simulation."""
    legs: List[SimulationLeg]
    convergence_rate: float
    infiltration_grade: str
    grade_label: str
    volatility_index: float
    stability_index: int  # NEW: Combined stability score
    stability_label: str  # NEW
    correlation_penalty: float
    conflicts_detected: List[Dict]
    risk_flags: List[str]
    environmental_summary: str


# ==================== CORE SIMULATION ENGINE ====================

class SimulationEngine:
    """
    Command Post Simulation Engine.
    
    Calculates tactical probability and risk assessment for parlay configurations.
    """
    
    def __init__(self, db=None):
        self.db = db
    
    async def simulate_configuration(
        self, 
        legs: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Main simulation entry point.
        
        Args:
            legs: List of leg dictionaries with player_name, stat_type, line, direction, etc.
        
        Returns:
            Complete simulation result with grades, probabilities, and risk flags.
        """
        if not legs:
            return self._empty_result()
        
        # 1. Validate and detect conflicts
        conflicts = self._detect_conflicts(legs)
        
        # 2. Process each leg
        processed_legs = []
        for leg_data in legs:
            processed = await self._process_leg(leg_data)
            processed_legs.append(processed)
        
        # 3. Calculate correlation penalties
        correlation_penalty = self._calculate_correlation_penalty(processed_legs)
        
        # 4. Calculate combined convergence rate
        convergence_rate = self._calculate_convergence_rate(processed_legs, correlation_penalty)
        
        # 5. Determine infiltration grade
        grade, grade_label = self._determine_grade(convergence_rate)
        
        # 6. Calculate overall volatility index
        volatility_index = self._calculate_overall_volatility(processed_legs)
        
        # 7. Calculate combined stability index
        combined_stability = self._calculate_combined_stability(processed_legs)
        stability_label = "HIGH STABILITY" if combined_stability >= 80 else "MODERATE" if combined_stability >= 50 else "VOLATILE"
        
        # 8. Generate risk flags
        risk_flags = self._generate_risk_flags(processed_legs, conflicts, volatility_index)
        
        # 9. Generate environmental summary
        env_summary = self._generate_environmental_summary(processed_legs)
        
        return {
            "success": True,
            "simulation": {
                "legs": [self._leg_to_dict(leg) for leg in processed_legs],
                "leg_count": len(processed_legs),
                "convergence_rate": round(convergence_rate, 1),
                "infiltration_grade": grade,
                "grade_label": grade_label,
                "volatility_index": round(volatility_index, 2),
                "volatility_label": self._get_volatility_label(volatility_index),
                "stability_index": combined_stability,
                "stability_label": stability_label,
                "correlation_penalty": round((1 - correlation_penalty) * 100, 1),
                "conflicts_detected": conflicts,
                "risk_flags": risk_flags,
                "environmental_summary": env_summary,
                "simulated_at": datetime.now(timezone.utc).isoformat()
            }
        }
    
    async def _process_leg(self, leg_data: Dict) -> SimulationLeg:
        """Process a single leg and calculate all metrics."""
        player_name = leg_data.get("player_name", "")
        stat_type = leg_data.get("stat_type", "")
        line = leg_data.get("line", 0)
        direction = leg_data.get("direction", "over").lower()
        team = leg_data.get("team", "")
        opponent = leg_data.get("opponent", "")
        game_id = leg_data.get("game_id")
        is_home = leg_data.get("is_home", True)
        
        # Create leg object
        leg = SimulationLeg(
            player_name=player_name,
            player_id=leg_data.get("player_id"),
            stat_type=stat_type,
            line=line,
            direction=direction,
            team=team,
            opponent=opponent,
            game_id=game_id,
            is_home=is_home
        )
        
        # Get base hit rate from leg data or calculate
        leg.base_hit_rate = leg_data.get("h10_rate", leg_data.get("hit_probability", 50)) / 100
        
        # Store statistical data for stability calculation
        leg.season_avg = leg_data.get("season_avg", 0)
        leg.std_dev = leg_data.get("std_dev", 0)
        leg.l5_avg = leg_data.get("l5_avg", 0)
        leg.l10_avg = leg_data.get("l10_avg", 0)
        
        # Calculate Usage Ripple effect
        leg.usage_ripple = leg_data.get("usage_bump_percent", 0)
        usage_multiplier = 1.0 + (leg.usage_ripple * 0.005)  # +0.5% per 1% usage bump
        
        # Calculate Environmental Delta (Home/Away)
        leg.environmental_delta = HOME_ADVANTAGE if is_home else AWAY_PENALTY
        
        # Calculate Defensive Friction (DvP)
        leg.dvp_rank = get_dvp_rank(opponent, stat_type)
        leg.dvp_rank_color = get_dvp_rank_color(leg.dvp_rank)
        leg.defensive_friction = calculate_dvp_modifier(opponent, stat_type)
        
        # Get friction label
        if leg.dvp_rank >= 25:
            leg.friction_label = "Low Friction"
        elif leg.dvp_rank <= 5:
            leg.friction_label = "High Friction"
        else:
            leg.friction_label = "Standard Friction"
        
        # Calculate Volatility Index
        std_dev = leg_data.get("std_dev", 0)
        season_avg = leg_data.get("season_avg", 1)
        if season_avg > 0 and std_dev > 0:
            leg.volatility_index = std_dev / season_avg
        else:
            # Estimate from hit rate variance
            h10 = leg_data.get("h10_rate", 50) / 100
            h5 = leg_data.get("h5_rate", 50) / 100
            leg.volatility_index = abs(h10 - h5)
        
        # Volatility label
        if leg.volatility_index >= VOLATILITY_HIGH:
            leg.volatility_label = "High Volatility"
        elif leg.volatility_index >= VOLATILITY_MEDIUM:
            leg.volatility_label = "Medium Volatility"
        else:
            leg.volatility_label = "Low Volatility"
        
        # Calculate Stability Index (1-100, inverse of volatility)
        # High Stability (80-100): Low variance, consistent
        # Moderate (50-79): Average variance
        # Volatile (0-49): High variance, boom-or-bust
        if season_avg > 0 and std_dev > 0:
            cv = (std_dev / season_avg) * 100  # Coefficient of variation
            leg.stability_index = max(0, min(100, int(100 - (cv * 2))))
        else:
            # Estimate from hit rate consistency
            h10 = leg_data.get("h10_rate", 50)
            h5 = leg_data.get("h5_rate", 50)
            consistency = 100 - abs(h10 - h5) * 2
            leg.stability_index = max(0, min(100, int(consistency)))
        
        # Stability label
        if leg.stability_index >= 80:
            leg.stability_label = "HIGH STABILITY"
        elif leg.stability_index >= 50:
            leg.stability_label = "MODERATE"
        else:
            leg.stability_label = "VOLATILE"
        
        # Calculate final tactical probability
        # Base * Usage Ripple * Environmental * (1 + DvP Modifier * 0.2)
        tactical_prob = leg.base_hit_rate * usage_multiplier * leg.environmental_delta
        tactical_prob *= (1 + (leg.defensive_friction - 0.5) * 0.2)  # DvP adjustment
        
        leg.tactical_probability = min(0.99, max(0.01, tactical_prob))
        
        return leg
    
    def _detect_conflicts(self, legs: List[Dict]) -> List[Dict]:
        """Detect conflicting legs (e.g., same player Over AND Under)."""
        conflicts = []
        
        # Group by player
        player_legs = {}
        for i, leg in enumerate(legs):
            player = leg.get("player_name", "")
            stat = leg.get("stat_type", "")
            direction = leg.get("direction", "").lower()
            key = f"{player}_{stat}"
            
            if key not in player_legs:
                player_legs[key] = []
            player_legs[key].append({
                "index": i,
                "direction": direction,
                "line": leg.get("line", 0)
            })
        
        # Check for conflicts
        for key, entries in player_legs.items():
            if len(entries) > 1:
                directions = set(e["direction"] for e in entries)
                if len(directions) > 1:
                    # Conflicting directions (Over and Under)
                    player, stat = key.rsplit("_", 1)
                    conflicts.append({
                        "type": "DIRECTION_CONFLICT",
                        "severity": "CRITICAL",
                        "message": f"{player} has both OVER and UNDER on {stat}",
                        "legs": [e["index"] for e in entries]
                    })
                elif len(entries) > 1:
                    # Same direction, different lines
                    player, stat = key.rsplit("_", 1)
                    conflicts.append({
                        "type": "DUPLICATE_STAT",
                        "severity": "WARNING",
                        "message": f"{player} has multiple {stat} lines",
                        "legs": [e["index"] for e in entries]
                    })
        
        return conflicts
    
    def _calculate_correlation_penalty(self, legs: List[SimulationLeg]) -> float:
        """Calculate correlation penalty for correlated legs."""
        if len(legs) <= 1:
            return 1.0
        
        penalty = 1.0
        
        # Check for same-player correlation
        players = [leg.player_name for leg in legs]
        for player in set(players):
            count = players.count(player)
            if count > 1:
                penalty *= SAME_PLAYER_CORRELATION ** (count - 1)
        
        # Check for same-team correlation
        teams = [leg.team for leg in legs]
        for team in set(teams):
            count = teams.count(team)
            if count > 2:  # Allow up to 2 from same team
                penalty *= SAME_TEAM_CORRELATION ** (count - 2)
        
        # Check for same-game correlation
        games = [leg.game_id for leg in legs if leg.game_id]
        for game in set(games):
            count = games.count(game)
            if count > 2:  # Allow up to 2 from same game
                penalty *= SAME_GAME_CORRELATION ** (count - 2)
        
        return penalty
    
    def _calculate_convergence_rate(
        self, 
        legs: List[SimulationLeg], 
        correlation_penalty: float
    ) -> float:
        """
        Calculate combined convergence rate.
        
        Uses geometric mean of individual tactical probabilities,
        adjusted by correlation penalty.
        """
        if not legs:
            return 0.0
        
        # Geometric mean of tactical probabilities
        product = 1.0
        for leg in legs:
            product *= leg.tactical_probability
        
        geometric_mean = product ** (1 / len(legs))
        
        # Apply correlation penalty
        convergence = geometric_mean * correlation_penalty * 100
        
        return min(99.0, max(1.0, convergence))
    
    def _determine_grade(self, convergence_rate: float) -> Tuple[str, str]:
        """Determine infiltration grade from convergence rate (A-F scale)."""
        if convergence_rate >= GRADE_THRESHOLDS["A"]:
            return "A", "Optimal Alignment - Low Risk"
        elif convergence_rate >= GRADE_THRESHOLDS["B"]:
            return "B", "Strong Position - Moderate Risk"
        elif convergence_rate >= GRADE_THRESHOLDS["C"]:
            return "C", "Standard Exposure - Average Risk"
        elif convergence_rate >= GRADE_THRESHOLDS["D"]:
            return "D", "Elevated Friction - Higher Risk"
        else:
            return "F", "High-Friction / Volatile - Maximum Risk"
    
    def _calculate_overall_volatility(self, legs: List[SimulationLeg]) -> float:
        """Calculate overall volatility index for the configuration."""
        if not legs:
            return 0.0
        
        volatilities = [leg.volatility_index for leg in legs]
        
        # Use max volatility as the configuration's volatility
        # (weakest link principle)
        return max(volatilities) if volatilities else 0.0
    
    def _calculate_combined_stability(self, legs: List[SimulationLeg]) -> int:
        """Calculate combined stability index for the configuration.
        
        Uses the average stability, penalized by the lowest stability leg.
        High Stability (80-100): Low variance, consistent
        Moderate (50-79): Average variance
        Volatile (0-49): High variance, boom-or-bust
        """
        if not legs:
            return 50
        
        stabilities = [leg.stability_index for leg in legs]
        
        avg_stability = sum(stabilities) / len(stabilities)
        min_stability = min(stabilities)
        
        # Combined score weights average (60%) and minimum (40%)
        # This penalizes configurations with one volatile leg
        combined = (avg_stability * 0.6) + (min_stability * 0.4)
        
        return int(min(100, max(0, combined)))
    
    def _get_volatility_label(self, volatility: float) -> str:
        """Get volatility label from index."""
        if volatility >= VOLATILITY_HIGH:
            return "High Volatility"
        elif volatility >= VOLATILITY_MEDIUM:
            return "Medium Volatility"
        else:
            return "Low Volatility"
    
    def _generate_risk_flags(
        self, 
        legs: List[SimulationLeg], 
        conflicts: List[Dict],
        volatility: float
    ) -> List[str]:
        """Generate risk flags for the configuration."""
        flags = []
        
        # Conflict flags
        if any(c["severity"] == "CRITICAL" for c in conflicts):
            flags.append("CRITICAL: Conflicting legs detected - review configuration")
        
        # High volatility flags
        high_vol_legs = [leg for leg in legs if leg.volatility_label == "High Volatility"]
        if high_vol_legs:
            names = ", ".join(leg.player_name for leg in high_vol_legs[:2])
            flags.append(f"HIGH VARIANCE: {names} - boom or bust profiles")
        
        # High friction flags
        high_friction = [leg for leg in legs if leg.friction_label == "High Friction"]
        if high_friction:
            for leg in high_friction[:2]:
                flags.append(f"DEFENSIVE WALL: {leg.player_name} faces #{leg.dvp_rank} defense")
        
        # All away games
        away_count = sum(1 for leg in legs if not leg.is_home)
        if away_count == len(legs) and len(legs) >= 3:
            flags.append("ROAD WARRIORS: All legs in away environments")
        
        # Overall volatility
        if volatility >= VOLATILITY_HIGH:
            flags.append("ELEVATED EXPOSURE: Configuration has high overall variance")
        
        return flags
    
    def _generate_environmental_summary(self, legs: List[SimulationLeg]) -> str:
        """Generate environmental summary for the configuration."""
        if not legs:
            return "No legs configured"
        
        home_count = sum(1 for leg in legs if leg.is_home)
        away_count = len(legs) - home_count
        
        avg_friction = sum(leg.dvp_rank for leg in legs) / len(legs)
        
        if avg_friction >= 20:
            friction_desc = "soft defensive matchups"
        elif avg_friction <= 10:
            friction_desc = "tough defensive slate"
        else:
            friction_desc = "mixed defensive environment"
        
        return f"{home_count} home / {away_count} away with {friction_desc}"
    
    def _leg_to_dict(self, leg: SimulationLeg) -> Dict[str, Any]:
        """Convert SimulationLeg to dictionary."""
        return {
            "player_name": leg.player_name,
            "player_id": leg.player_id,
            "stat_type": leg.stat_type,
            "line": leg.line,
            "direction": leg.direction,
            "team": leg.team,
            "opponent": leg.opponent,
            "is_home": leg.is_home,
            "base_hit_rate": round(leg.base_hit_rate * 100, 1),
            "usage_ripple": round(leg.usage_ripple, 1),
            "environmental_delta": round(leg.environmental_delta, 3),
            "defensive_friction": round(leg.defensive_friction, 3),
            "dvp_rank": leg.dvp_rank,
            "dvp_rank_color": leg.dvp_rank_color,
            "volatility_index": round(leg.volatility_index, 3),
            "volatility_label": leg.volatility_label,
            "friction_label": leg.friction_label,
            "tactical_probability": round(leg.tactical_probability * 100, 1),
            # New stability fields
            "stability_index": leg.stability_index,
            "stability_label": leg.stability_label,
            "season_avg": round(leg.season_avg, 1) if leg.season_avg else None,
            "l5_avg": round(leg.l5_avg, 1) if leg.l5_avg else None,
            "l10_avg": round(leg.l10_avg, 1) if leg.l10_avg else None
        }
    
    def _empty_result(self) -> Dict[str, Any]:
        """Return empty simulation result."""
        return {
            "success": True,
            "simulation": {
                "legs": [],
                "leg_count": 0,
                "convergence_rate": 0,
                "infiltration_grade": "-",
                "grade_label": "No configuration",
                "volatility_index": 0,
                "volatility_label": "N/A",
                "correlation_penalty": 0,
                "conflicts_detected": [],
                "risk_flags": [],
                "environmental_summary": "Add legs to begin simulation",
                "simulated_at": datetime.now(timezone.utc).isoformat()
            }
        }


# ==================== SINGLETON INSTANCE ====================

_simulation_engine: Optional[SimulationEngine] = None


def get_simulation_engine(db=None) -> SimulationEngine:
    """Get or create simulation engine instance."""
    global _simulation_engine
    if _simulation_engine is None:
        _simulation_engine = SimulationEngine(db)
    return _simulation_engine


def set_simulation_db(db):
    """Set database reference for simulation engine."""
    global _simulation_engine
    if _simulation_engine is None:
        _simulation_engine = SimulationEngine(db)
    else:
        _simulation_engine.db = db
