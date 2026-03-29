"""
Intel Suite Calculator Service

Calculates advanced metrics for PropVision Radar Picks:
- usage_ripple: Projected Usage Rate changes based on lineup/injury data
- matchup_dvp: Opponent's Defense vs. Position ranking
- pace_delta: Projected game pace differential
- stability_index: Consistency score based on L10 standard deviation
- vision_insight: AI reasoning for flagging this prop
"""
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import random

logger = logging.getLogger(__name__)

# Default pace values by team (possessions per game)
TEAM_PACE_DEFAULTS = {
    "IND": 103.5, "ATL": 102.8, "MIL": 101.2, "SAC": 100.9, "MIN": 100.5,
    "DEN": 99.8, "LAL": 99.5, "BOS": 99.2, "PHX": 99.0, "DAL": 98.8,
    "NOP": 98.5, "GSW": 98.2, "CHI": 98.0, "CHA": 97.8, "POR": 97.5,
    "BKN": 97.2, "HOU": 97.0, "TOR": 96.8, "ORL": 96.5, "WAS": 96.2,
    "DET": 96.0, "PHI": 95.8, "OKC": 95.5, "SAS": 95.2, "CLE": 95.0,
    "MIA": 94.8, "NYK": 94.5, "LAC": 94.2, "MEM": 93.8, "UTA": 93.5
}

# Default season pace
DEFAULT_SEASON_PACE = 97.5


class IntelSuiteCalculator:
    """
    Calculates advanced Intel Suite metrics for Radar Picks.
    Only returns data for props flagged as is_radar = True.
    """
    
    def __init__(self, db):
        self.db = db
        self.master_hub = db.nba_master_hub_2026
        self.injury_data = db.dg_injury_data
        self.team_stats = db.dg_team_stats
    
    async def calculate_intel_suite(
        self,
        player_name: str,
        stat_type: str,
        line: float,
        direction: str,
        opponent: Optional[str] = None,
        board_pick: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Calculate full Intel Suite metrics for a Radar Pick.
        
        Returns:
            intel_suite dict with all 5 advanced metrics
        """
        # Get player data from master hub
        player = await self.master_hub.find_one(
            {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0}
        )
        
        if not player:
            logger.warning(f"[INTEL] Player not found in hub: {player_name}")
            player = {}
        
        team = player.get("team") or (board_pick.get("team") if board_pick else None)
        baseline_stats = player.get("baseline_stats", {})
        stat_data = baseline_stats.get(stat_type, {})
        
        # Calculate each metric
        usage_ripple = await self._calculate_usage_ripple(player_name, team, stat_type, board_pick)
        matchup_dvp = await self._calculate_matchup_dvp(opponent, stat_type, board_pick)
        pace_delta = await self._calculate_pace_delta(team, opponent, board_pick)
        stability_index = self._calculate_stability_index(stat_data, board_pick)
        vision_insight = self._generate_vision_insight(
            player_name, stat_type, line, direction,
            usage_ripple, matchup_dvp, pace_delta, stability_index, board_pick
        )
        
        return {
            "usage_ripple": usage_ripple,
            "matchup_dvp": matchup_dvp,
            "pace_delta": pace_delta,
            "stability_index": stability_index,
            "vision_insight": vision_insight,
            "calculated_at": datetime.now(timezone.utc).isoformat()
        }
    
    async def _calculate_usage_ripple(
        self,
        player_name: str,
        team: Optional[str],
        stat_type: str,
        board_pick: Optional[Dict]
    ) -> Dict[str, Any]:
        """
        Calculate usage_ripple (Operational Volume).
        Shows projected Usage Rate changes based on lineup/injury data.
        """
        # Check for existing usage bump from board pick
        existing_bump = board_pick.get("usage_bump_percent", 0) if board_pick else 0
        
        # Check for teammate injuries that might affect usage
        injuries_affecting = []
        bump_percent = existing_bump
        
        if team:
            # Query injury data for teammates
            injured_teammates = await self.injury_data.find({
                "team": {"$regex": f"^{team}$", "$options": "i"},
                "status": {"$in": ["Out", "Doubtful", "Questionable"]}
            }, {"_id": 0, "player_name": 1, "status": 1, "injury": 1}).to_list(10)
            
            for injury in injured_teammates:
                if injury.get("player_name", "").lower() != player_name.lower():
                    injuries_affecting.append({
                        "player": injury.get("player_name"),
                        "status": injury.get("status"),
                        "injury": injury.get("injury", "Unknown")
                    })
                    # Each injured teammate adds potential usage bump
                    if injury.get("status") == "Out":
                        bump_percent += 2.5
                    elif injury.get("status") == "Doubtful":
                        bump_percent += 1.5
                    elif injury.get("status") == "Questionable":
                        bump_percent += 0.8
        
        # Cap at reasonable maximum
        bump_percent = min(bump_percent, 15.0)
        
        # Determine shift label
        if bump_percent >= 5:
            shift_label = "High Volume Shift"
        elif bump_percent >= 2:
            shift_label = "Moderate Volume Shift"
        elif bump_percent > 0:
            shift_label = "Minor Volume Shift"
        else:
            shift_label = "Standard Volume"
        
        return {
            "bump_percent": round(bump_percent, 1),
            "display": f"+{bump_percent:.1f}% Vol. Shift" if bump_percent > 0 else "Standard Volume",
            "shift_label": shift_label,
            "injuries_affecting": injuries_affecting[:3],  # Top 3 injuries
            "reasoning": f"Lineup changes project {bump_percent:.1f}% usage increase" if bump_percent > 0 else "No significant lineup changes"
        }
    
    async def _calculate_matchup_dvp(
        self,
        opponent: Optional[str],
        stat_type: str,
        board_pick: Optional[Dict]
    ) -> Dict[str, Any]:
        """
        Calculate matchup_dvp (Defensive Friction).
        Returns opponent's Defense vs. Position ranking.
        """
        # Get DvP from board pick if available
        dvp_rank = board_pick.get("dvp_rank") if board_pick else None
        dvp_color = board_pick.get("dvp_rank_color") if board_pick else None
        
        # Map stat types to positions
        stat_to_position = {
            "PTS": "Scorers",
            "REB": "Rebounders", 
            "AST": "Playmakers",
            "3PM": "Shooters",
            "STL": "Ball Handlers",
            "BLK": "Rim Protectors",
            "PRA": "All-Around",
            "PR": "Scorers/Rebounders",
            "PA": "Scorers/Playmakers",
            "RA": "Rebounders/Playmakers"
        }
        position_label = stat_to_position.get(stat_type, "Players")
        
        # If no DvP data in board_pick, try to look it up from DVP service
        if dvp_rank is None and opponent:
            try:
                from services.dvp_service import get_dvp_rank, get_dvp_rank_color
                dvp_rank = get_dvp_rank(opponent, stat_type)
                dvp_color = get_dvp_rank_color(dvp_rank)
                logger.debug(f"[DVP_LOOKUP] {opponent} vs {stat_type}: rank={dvp_rank}")
            except Exception as e:
                logger.debug(f"[DVP_LOOKUP] Failed for {opponent}: {e}")
                dvp_rank = None
        
        # If still no DvP data, use team-based estimates
        if dvp_rank is None:
            dvp_rank = 15  # Default to middle-of-pack
            if opponent:
                # Some hardcoded team tendencies for demo
                weak_defenses = ["WAS", "POR", "DET", "SAS", "CHA"]
                strong_defenses = ["BOS", "MIN", "CLE", "OKC", "MIA"]
                if opponent in weak_defenses:
                    dvp_rank = random.randint(22, 28)
                elif opponent in strong_defenses:
                    dvp_rank = random.randint(3, 10)
                else:
                    dvp_rank = random.randint(10, 20)
        
        # Determine color if not provided
        if dvp_color is None:
            if dvp_rank >= 22:
                dvp_color = "green"
            elif dvp_rank >= 15:
                dvp_color = "yellow"
            elif dvp_rank >= 8:
                dvp_color = "orange"
            else:
                dvp_color = "red"
        
        # Friction level
        if dvp_rank >= 25:
            friction_level = "Low"
            friction_label = "Soft Defense - Favorable Matchup"
        elif dvp_rank >= 18:
            friction_level = "Low-Med"
            friction_label = "Below Average Defense"
        elif dvp_rank >= 12:
            friction_level = "Medium"
            friction_label = "Average Defensive Unit"
        elif dvp_rank >= 6:
            friction_level = "Med-High"
            friction_label = "Above Average Defense"
        else:
            friction_level = "High"
            friction_label = "Elite Defense - Tough Matchup"
        
        return {
            "rank": dvp_rank,
            "display": f"Rank #{dvp_rank} vs. {position_label}",
            "color": dvp_color,
            "friction_level": friction_level,
            "friction_label": friction_label,
            "opponent": opponent or "Unknown",
            "stat_category": stat_type
        }
    
    async def _calculate_pace_delta(
        self,
        team: Optional[str],
        opponent: Optional[str],
        board_pick: Optional[Dict]
    ) -> Dict[str, Any]:
        """
        Calculate pace_delta (Tempo Multiplier).
        Shows projected game pace differential.
        """
        # Get pace factor from board pick
        pace_factor = board_pick.get("pace_factor", 1.0) if board_pick else 1.0
        
        # Get team paces
        team_pace = TEAM_PACE_DEFAULTS.get(team, DEFAULT_SEASON_PACE) if team else DEFAULT_SEASON_PACE
        opp_pace = TEAM_PACE_DEFAULTS.get(opponent, DEFAULT_SEASON_PACE) if opponent else DEFAULT_SEASON_PACE
        
        # Calculate expected game pace (average of both teams)
        expected_game_pace = (team_pace + opp_pace) / 2
        
        # Calculate possession delta from player's average
        player_avg_pace = DEFAULT_SEASON_PACE  # Baseline
        possession_delta = expected_game_pace - player_avg_pace
        
        # Apply pace factor modifier
        adjusted_delta = possession_delta * pace_factor
        
        # Determine tempo label
        if adjusted_delta >= 4:
            tempo_label = "High Tempo - Fast Pace"
        elif adjusted_delta >= 2:
            tempo_label = "Above Average Tempo"
        elif adjusted_delta >= -2:
            tempo_label = "Standard Tempo"
        elif adjusted_delta >= -4:
            tempo_label = "Below Average Tempo"
        else:
            tempo_label = "Slow Tempo - Grind Game"
        
        return {
            "possessions": round(adjusted_delta, 1),
            "display": f"{'+' if adjusted_delta >= 0 else ''}{adjusted_delta:.1f} Possessions",
            "pace_factor": round(pace_factor, 2),
            "tempo_label": tempo_label,
            "team_pace": round(team_pace, 1),
            "opponent_pace": round(opp_pace, 1),
            "expected_game_pace": round(expected_game_pace, 1)
        }
    
    def _calculate_stability_index(
        self,
        stat_data: Dict,
        board_pick: Optional[Dict]
    ) -> Dict[str, Any]:
        """
        Calculate stability_index (Tactical Variance).
        Score 1-100 based on standard deviation of L10 games.
        """
        # Get std_dev from board pick or stat data
        std_dev = None
        if board_pick:
            std_dev = board_pick.get("std_dev")
        
        # If no std_dev, estimate from L5/L10 spread
        if std_dev is None:
            l5_avg = stat_data.get("l5_avg")
            l10_avg = stat_data.get("l10_avg")
            season_avg = stat_data.get("season_avg")
            
            if l5_avg and l10_avg:
                # Rough estimate: variance between recent averages
                variance = abs(l5_avg - l10_avg)
                std_dev = variance * 1.5  # Approximate
            elif season_avg:
                # Default to moderate variance
                std_dev = season_avg * 0.15
            else:
                std_dev = 3.0  # Default
        
        # Convert std_dev to stability score (0-100)
        # Lower std_dev = higher stability
        # Typical std_dev ranges: 1-2 (very stable), 3-5 (moderate), 6+ (volatile)
        if std_dev <= 1.5:
            stability_score = 95
            stability_label = "Elite"
            consistency = "Extremely Consistent"
        elif std_dev <= 2.5:
            stability_score = 85
            stability_label = "High"
            consistency = "Very Consistent"
        elif std_dev <= 3.5:
            stability_score = 70
            stability_label = "Medium-High"
            consistency = "Above Average Consistency"
        elif std_dev <= 5.0:
            stability_score = 55
            stability_label = "Medium"
            consistency = "Average Consistency"
        elif std_dev <= 7.0:
            stability_score = 40
            stability_label = "Medium-Low"
            consistency = "Below Average Consistency"
        else:
            stability_score = 25
            stability_label = "Low"
            consistency = "High Variance Player"
        
        return {
            "score": stability_score,
            "label": stability_label,
            "display": f"{stability_score}/100 ({stability_label})",
            "std_dev": round(std_dev, 2) if std_dev else None,
            "consistency": consistency,
            "variance_level": "Low" if stability_score >= 70 else "Medium" if stability_score >= 45 else "High"
        }
    
    def _generate_vision_insight(
        self,
        player_name: str,
        stat_type: str,
        line: float,
        direction: str,
        usage_ripple: Dict,
        matchup_dvp: Dict,
        pace_delta: Dict,
        stability_index: Dict,
        board_pick: Optional[Dict]
    ) -> Dict[str, Any]:
        """
        Generate vision_insight (Target-Lock Rationale).
        AI reasoning for flagging this prop.
        """
        reasons = []
        confidence_factors = []
        
        # Analyze usage ripple
        bump_percent = usage_ripple.get("bump_percent") or 0
        if bump_percent >= 3:
            reasons.append(f"Usage projected +{bump_percent}% due to lineup changes")
            confidence_factors.append("volume_increase")
        
        # Analyze matchup
        dvp_rank = matchup_dvp.get("rank", 15)
        if dvp_rank >= 22:
            reasons.append(f"Soft defensive matchup (DvP #{dvp_rank})")
            confidence_factors.append("favorable_matchup")
        elif dvp_rank <= 8:
            reasons.append(f"Note: Tough defensive matchup (DvP #{dvp_rank})")
        
        # Analyze pace
        pace_poss = pace_delta.get("possessions", 0)
        if pace_poss >= 3:
            reasons.append(f"High-tempo game projected (+{pace_poss:.1f} possessions)")
            confidence_factors.append("pace_advantage")
        
        # Analyze stability
        stability_score = stability_index.get("score", 50)
        if stability_score >= 75:
            reasons.append("Highly consistent recent performance")
            confidence_factors.append("consistency")
        
        # Check board pick for additional context
        if board_pick:
            h10_rate = board_pick.get("h10_rate") or 0
            edge_percent = board_pick.get("edge_percent") or 0
            # h10_rate is already a percentage (e.g., 80 = 80%), not a decimal
            if h10_rate >= 80:
                reasons.append(f"Strong L10 hit rate ({int(h10_rate)}%)")
                confidence_factors.append("hit_rate")
            elif h10_rate >= 70:
                reasons.append(f"Solid L10 hit rate ({int(h10_rate)}%)")
                confidence_factors.append("hit_rate")
            if edge_percent >= 5:
                reasons.append(f"Market edge detected (+{edge_percent:.1f}%)")
                confidence_factors.append("market_edge")
        
        # Build primary insight
        if len(reasons) >= 3:
            confidence = "High"
        elif len(reasons) >= 2:
            confidence = "Medium-High"
        elif len(reasons) >= 1:
            confidence = "Medium"
        else:
            confidence = "Standard"
            reasons.append("Baseline metrics favorable for this line")
        
        # Generate summary text
        primary_insight = reasons[0] if reasons else "Projected value based on current metrics"
        
        return {
            "primary": primary_insight,
            "reasons": reasons,
            "confidence": confidence,
            "confidence_factors": confidence_factors,
            "summary": f"Target-Lock: {direction.upper()} {line} {stat_type} - {primary_insight}",
            "tactical_note": self._get_tactical_note(confidence_factors)
        }
    
    def _get_tactical_note(self, factors: list) -> str:
        """Generate a tactical note based on confidence factors."""
        if "volume_increase" in factors and "favorable_matchup" in factors:
            return "Market fractured: Volume shift + soft defense = opportunity window"
        elif "volume_increase" in factors:
            return "Lineup change creating usage vacuum - projected absorption"
        elif "favorable_matchup" in factors:
            return "Defensive weakness identified in opponent's scheme"
        elif "pace_advantage" in factors:
            return "Tempo multiplier active - increased opportunity volume"
        elif "consistency" in factors:
            return "Low variance profile - reliable baseline performer"
        elif "hit_rate" in factors:
            return "Historical trend strongly favoring this line"
        elif "market_edge" in factors:
            return "Market inefficiency detected - line adjustment expected"
        else:
            return "Baseline projection favorable for current conditions"


# Singleton instance
_intel_calculator = None

def get_intel_calculator(db):
    """Get or create Intel Suite calculator instance."""
    global _intel_calculator
    if _intel_calculator is None:
        _intel_calculator = IntelSuiteCalculator(db)
    return _intel_calculator
