"""
Intel Suite Calculator Service v2
===================================
Refactored to remove legacy baseline_stats dependency.
All metrics now pull from live bdl_game_logs / history.2025_season.
Lasso v2 projections wired into vision_insight generation.
"""
import logging
import numpy as np
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

TEAM_PACE_DEFAULTS = {
    "IND": 103.5, "ATL": 102.8, "MIL": 101.2, "SAC": 100.9, "MIN": 100.5,
    "DEN": 99.8, "LAL": 99.5, "BOS": 99.2, "PHX": 99.0, "DAL": 98.8,
    "NOP": 98.5, "GSW": 98.2, "CHI": 98.0, "CHA": 97.8, "POR": 97.5,
    "BKN": 97.2, "HOU": 97.0, "TOR": 96.8, "ORL": 96.5, "WAS": 96.2,
    "DET": 96.0, "PHI": 95.8, "OKC": 95.5, "SAS": 95.2, "CLE": 95.0,
    "MIA": 94.8, "NYK": 94.5, "LAC": 94.2, "MEM": 93.8, "UTA": 93.5,
}
DEFAULT_SEASON_PACE = 97.5

# Stat type -> game log field name
STAT_TO_FIELD = {
    "PTS": "pts", "Points": "pts", "points": "pts",
    "REB": "reb", "Rebounds": "reb", "rebounds": "reb",
    "AST": "ast", "Assists": "ast", "assists": "ast",
    "3PM": "fg3m", "Three Pointers Made": "fg3m",
    "STL": "stl", "BLK": "blk",
    "PRA": "pra", "Pts+Rebs+Asts": "pra",
}


def _extract_stat_values(game_logs: List[Dict], stat_type: str) -> List[float]:
    """Pull a stat column from game logs, computing PRA on the fly if needed."""
    field = STAT_TO_FIELD.get(stat_type, stat_type.lower())
    values = []
    for log in game_logs:
        if field == "pra":
            val = (log.get("pts") or 0) + (log.get("reb") or 0) + (log.get("ast") or 0)
        else:
            val = log.get(field)
        if val is not None:
            values.append(float(val))
    return values


class IntelSuiteCalculator:
    """
    Calculates advanced Intel Suite metrics for Ferrari Tier picks.
    v2: All data from live game logs, Lasso projections injected.
    """

    def __init__(self, db):
        self.db = db
        self.master_hub = db.nba_master_hub_2026
        self.injury_data = db.dg_injury_data

    async def calculate_intel_suite(
        self,
        player_name: str,
        stat_type: str,
        line: float,
        direction: str,
        opponent: Optional[str] = None,
        board_pick: Optional[Dict] = None,
        lasso_result: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Calculate full Intel Suite with Lasso v2 integration."""

        # Fetch player from hub — only need game logs + team
        player = await self.master_hub.find_one(
            {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0, "display_name": 1, "team": 1, "position": 1,
             "history.2025_season": 1, "bdl_game_logs": 1}
        )
        if not player:
            player = {}

        team = player.get("team") or (board_pick.get("team") if board_pick else None)

        # Build active game logs (2025-26 season, DNP filtered)
        active_logs = self._get_active_logs(player)

        # Calculate each metric with graceful fallbacks
        try:
            usage_ripple = await self._calculate_usage_ripple(player_name, team, stat_type, board_pick)
        except Exception as e:
            logger.warning(f"[INTEL] usage_ripple failed: {e}")
            usage_ripple = {"bump_percent": 0, "display": "Standard Volume", "shift_label": "Standard Volume", "injuries_affecting": [], "reasoning": "Data unavailable"}

        try:
            matchup_dvp = await self._calculate_matchup_dvp(opponent, stat_type, board_pick)
        except Exception as e:
            logger.warning(f"[INTEL] matchup_dvp failed: {e}")
            matchup_dvp = {"rank": 15, "display": f"Rank #15 vs. {stat_type}", "color": "yellow", "friction_level": "Medium", "friction_label": "Average", "opponent": opponent or "UNK"}

        try:
            pace_delta = self._calculate_pace_delta(team, opponent, board_pick)
        except Exception as e:
            logger.warning(f"[INTEL] pace_delta failed: {e}")
            pace_delta = {"possessions": 0, "display": "+0.0 Possessions", "tempo_label": "Standard Tempo", "pace_factor": 1.0}

        try:
            stability_index = self._calculate_stability_index(active_logs, stat_type, board_pick)
        except Exception as e:
            logger.warning(f"[INTEL] stability_index failed: {e}")
            stability_index = {"score": 50, "label": "Medium", "display": "50/100 (Medium)", "consistency": "Average"}

        try:
            blowout_risk = await self._calculate_blowout_risk(team, opponent)
        except Exception as e:
            logger.warning(f"[INTEL] blowout_risk failed: {e}")
            blowout_risk = {"risk_level": "UNKNOWN", "risk_reason": "Unavailable"}

        vision_insight = self._generate_vision_insight(
            player_name, stat_type, line, direction,
            usage_ripple, matchup_dvp, pace_delta, stability_index,
            board_pick, lasso_result,
        )

        # Scout badges from live data
        scout_badges = self._generate_scout_badges(active_logs, stat_type, line, board_pick, lasso_result)

        return {
            "usage_ripple": usage_ripple,
            "matchup_dvp": matchup_dvp,
            "pace_delta": pace_delta,
            "stability_index": stability_index,
            "vision_insight": vision_insight,
            "blowout_risk": blowout_risk,
            "scout_badges": scout_badges,
            "lasso": {
                "projection": lasso_result.get("projection") if lasso_result else None,
                "confidence_tier": lasso_result.get("confidence_tier") if lasso_result else None,
                "r_squared": lasso_result.get("r_squared") if lasso_result else None,
                "top_drivers": [c["feature"] for c in lasso_result.get("top_contributors", [])[:2]] if lasso_result else [],
            } if lasso_result else None,
            "calculated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _get_active_logs(self, player: Dict) -> List[Dict]:
        """Get DNP-filtered active game logs from current season."""
        # Prefer 2025_season (already cleaned), fall back to bdl_game_logs
        logs = player.get("history", {}).get("2025_season", [])
        if not logs:
            logs = player.get("bdl_game_logs", [])
        # DNP filter
        active = [g for g in logs if g.get("min") not in ("00", "", "0", None) and (g.get("fga") or g.get("pts") or 0) > 0]
        return active

    async def _calculate_usage_ripple(self, player_name, team, stat_type, board_pick):
        existing_bump = board_pick.get("usage_bump_percent", 0) if board_pick else 0
        injuries_affecting = []
        bump_percent = existing_bump

        if team:
            injured_teammates = await self.injury_data.find(
                {"team": {"$regex": f"^{team}$", "$options": "i"}, "status": {"$in": ["Out", "Doubtful", "Questionable"]}},
                {"_id": 0, "player_name": 1, "status": 1, "injury": 1}
            ).to_list(10)
            for inj in injured_teammates:
                if inj.get("player_name", "").lower() != player_name.lower():
                    injuries_affecting.append({"player": inj.get("player_name"), "status": inj.get("status"), "injury": inj.get("injury", "Unknown")})
                    bump_percent += {"Out": 2.5, "Doubtful": 1.5, "Questionable": 0.8}.get(inj.get("status"), 0)
        bump_percent = min(bump_percent, 15.0)
        shift = "High Volume Shift" if bump_percent >= 5 else "Moderate Volume Shift" if bump_percent >= 2 else "Minor Volume Shift" if bump_percent > 0 else "Standard Volume"
        return {"bump_percent": round(bump_percent, 1), "display": f"+{bump_percent:.1f}% Vol. Shift" if bump_percent > 0 else "Standard Volume", "shift_label": shift, "injuries_affecting": injuries_affecting[:3], "reasoning": f"Lineup changes project {bump_percent:.1f}% usage increase" if bump_percent > 0 else "No significant lineup changes"}

    async def _calculate_matchup_dvp(self, opponent, stat_type, board_pick):
        dvp_rank = board_pick.get("dvp_rank") if board_pick else None
        dvp_color = board_pick.get("dvp_rank_color") if board_pick else None
        stat_to_pos = {"PTS": "Scorers", "REB": "Rebounders", "AST": "Playmakers", "3PM": "Shooters", "PRA": "All-Around"}
        pos_label = stat_to_pos.get(stat_type, "Players")
        if dvp_rank is None and opponent:
            try:
                from services.dvp_service import get_dvp_rank, get_dvp_rank_color
                dvp_rank = get_dvp_rank(opponent, stat_type)
                dvp_color = get_dvp_rank_color(dvp_rank)
            except Exception:
                dvp_rank = None
        if dvp_rank is None:
            weak = ["WAS", "POR", "DET", "SAS", "CHA"]
            strong = ["BOS", "MIN", "CLE", "OKC", "MIA"]
            dvp_rank = 25 if opponent in weak else 5 if opponent in strong else 15
        if dvp_color is None:
            dvp_color = "green" if dvp_rank >= 22 else "yellow" if dvp_rank >= 15 else "orange" if dvp_rank >= 8 else "red"
        fl = "Low" if dvp_rank >= 25 else "Low-Med" if dvp_rank >= 18 else "Medium" if dvp_rank >= 12 else "Med-High" if dvp_rank >= 6 else "High"
        flab = "Soft Defense" if dvp_rank >= 22 else "Below Average Defense" if dvp_rank >= 15 else "Average Defense" if dvp_rank >= 12 else "Above Average Defense" if dvp_rank >= 6 else "Elite Defense"
        return {"rank": dvp_rank, "display": f"Rank #{dvp_rank} vs. {pos_label}", "color": dvp_color, "friction_level": fl, "friction_label": flab, "opponent": opponent or "Unknown", "stat_category": stat_type}

    def _calculate_pace_delta(self, team, opponent, board_pick):
        pace_factor = board_pick.get("pace_factor", 1.0) if board_pick else 1.0
        team_pace = TEAM_PACE_DEFAULTS.get(team, DEFAULT_SEASON_PACE) if team else DEFAULT_SEASON_PACE
        opp_pace = TEAM_PACE_DEFAULTS.get(opponent, DEFAULT_SEASON_PACE) if opponent else DEFAULT_SEASON_PACE
        expected = (team_pace + opp_pace) / 2
        delta = (expected - DEFAULT_SEASON_PACE) * pace_factor
        label = "High Tempo" if delta >= 4 else "Above Avg Tempo" if delta >= 2 else "Standard Tempo" if delta >= -2 else "Below Avg Tempo" if delta >= -4 else "Slow Grind"
        return {"possessions": round(delta, 1), "display": f"{'+' if delta>=0 else ''}{delta:.1f} Possessions", "pace_factor": round(pace_factor, 2), "tempo_label": label, "team_pace": round(team_pace, 1), "opponent_pace": round(opp_pace, 1), "expected_game_pace": round(expected, 1)}

    def _calculate_stability_index(self, active_logs: List[Dict], stat_type: str, board_pick: Optional[Dict]) -> Dict:
        """Compute stability from LIVE game logs — no baseline_stats."""
        values = _extract_stat_values(active_logs, stat_type)

        # Use board_pick std_dev if available
        std_dev = board_pick.get("std_dev") if board_pick else None

        if std_dev is None and len(values) >= 5:
            recent = values[-10:] if len(values) >= 10 else values
            std_dev = float(np.std(recent))
        elif std_dev is None:
            std_dev = 3.0

        if std_dev <= 1.5:
            score, label, consistency = 95, "Elite", "Extremely Consistent"
        elif std_dev <= 2.5:
            score, label, consistency = 85, "High", "Very Consistent"
        elif std_dev <= 3.5:
            score, label, consistency = 70, "Medium-High", "Above Average"
        elif std_dev <= 5.0:
            score, label, consistency = 55, "Medium", "Average"
        elif std_dev <= 7.0:
            score, label, consistency = 40, "Medium-Low", "Below Average"
        else:
            score, label, consistency = 25, "Low", "High Variance"

        return {"score": score, "label": label, "display": f"{score}/100 ({label})", "std_dev": round(std_dev, 2), "consistency": consistency, "variance_level": "Low" if score >= 70 else "Medium" if score >= 45 else "High"}

    async def _calculate_blowout_risk(self, team, opponent):
        if not team or not opponent:
            return {"risk_level": "UNKNOWN", "risk_reason": "Team data unavailable", "warning": None}
        try:
            from services.standings_service import StandingsService
            return await StandingsService.calculate_blowout_risk(team, opponent)
        except Exception as e:
            return {"risk_level": "UNKNOWN", "risk_reason": str(e), "warning": None}

    def _generate_vision_insight(self, player_name, stat_type, line, direction, usage_ripple, matchup_dvp, pace_delta, stability_index, board_pick, lasso_result):
        """Generate vision_insight with Lasso v2 data injected."""
        reasons = []
        confidence_factors = []

        # Lasso-driven insight (primary signal)
        lasso_edge = None
        lasso_proj = None
        lasso_tier = None
        top_driver = None
        if lasso_result and lasso_result.get("projection"):
            lasso_proj = lasso_result["projection"]
            lasso_tier = lasso_result.get("confidence_tier", "HIGH_VARIANCE")
            top_contribs = lasso_result.get("top_contributors", [])
            if top_contribs:
                top_driver = top_contribs[0].get("feature", "")
            if line > 0:
                lasso_edge = lasso_proj - line
                edge_pct = abs(lasso_edge / line) * 100
                if edge_pct >= 15:
                    reasons.append(f"Lasso projects {lasso_proj:.1f} vs {line} line ({lasso_edge:+.1f} edge)")
                    confidence_factors.append("lasso_high_edge")
                elif edge_pct >= 5:
                    reasons.append(f"Model sees {lasso_proj:.1f} (thin {lasso_edge:+.1f} edge)")
                    confidence_factors.append("lasso_edge")

        # Injury / usage
        bump = usage_ripple.get("bump_percent", 0)
        if bump >= 3:
            reasons.append(f"Usage +{bump}% from lineup changes")
            confidence_factors.append("volume_increase")

        # Matchup
        dvp = matchup_dvp.get("rank", 15)
        if dvp >= 22:
            reasons.append(f"Soft defense (DvP #{dvp})")
            confidence_factors.append("favorable_matchup")
        elif dvp <= 8:
            reasons.append(f"Tough defense (DvP #{dvp})")

        # Pace
        poss = pace_delta.get("possessions", 0)
        if poss >= 3:
            reasons.append(f"High-tempo game (+{poss:.1f} possessions)")
            confidence_factors.append("pace_advantage")

        # Stability
        stab = stability_index.get("score", 50)
        if stab >= 75:
            reasons.append("Highly consistent performer")
            confidence_factors.append("consistency")

        # Board pick signals
        if board_pick:
            h10 = board_pick.get("h10_rate") or 0
            if h10 >= 80:
                reasons.append(f"L10 hit rate: {int(h10)}%")
                confidence_factors.append("hit_rate")

        # Top Lasso feature as a reason
        if top_driver:
            driver_clean = top_driver.replace("_", " ").replace("L10 avg ", "").replace("prev ", "last-game ").replace("ix ", "")
            reasons.append(f"Driven by {driver_clean}")

        confidence = "High" if len(reasons) >= 3 else "Medium-High" if len(reasons) >= 2 else "Medium" if reasons else "Standard"
        if lasso_tier == "HIGH_FIDELITY" and "lasso_high_edge" in confidence_factors:
            confidence = "High"

        primary = reasons[0] if reasons else "Baseline projection favorable"
        return {
            "primary": primary,
            "reasons": reasons,
            "confidence": confidence,
            "confidence_factors": confidence_factors,
            "summary": f"Target-Lock: {direction.upper()} {line} {stat_type} — {primary}",
            "lasso_projection": lasso_proj,
            "lasso_edge": round(lasso_edge, 2) if lasso_edge is not None else None,
            "tactical_note": self._get_tactical_note(confidence_factors),
        }

    def _generate_scout_badges(self, active_logs, stat_type, line, board_pick, lasso_result):
        """Generate scout badges from live data + Lasso."""
        badges = []
        values = _extract_stat_values(active_logs, stat_type)
        if not values:
            return badges

        l10 = values[-10:] if len(values) >= 10 else values
        l10_avg = float(np.mean(l10))
        l10_std = float(np.std(l10))
        hit_rate = sum(1 for v in l10 if v > line) / len(l10) * 100 if l10 else 0

        if hit_rate >= 80:
            badges.append("hot_streak")
        if l10_std <= 2.0 and l10_avg > line:
            badges.append("floor_lock")
        if l10_std >= 6.0:
            badges.append("volatility_extreme")
        if lasso_result:
            edge = lasso_result.get("projection", 0) - line if line else 0
            if edge > line * 0.15:
                badges.append("lasso_high_edge")
            tier = lasso_result.get("confidence_tier", "")
            if tier == "HIGH_FIDELITY":
                badges.append("high_fidelity_model")

        # Matchup from board
        if board_pick:
            dvp = board_pick.get("dvp_rank") or 15
            if dvp >= 25:
                badges.append("soft_matchup")
            if board_pick.get("usage_bump_percent", 0) >= 3:
                badges.append("usage_spike")

        return badges

    def _get_tactical_note(self, factors):
        if "lasso_high_edge" in factors and "favorable_matchup" in factors:
            return "Lasso + DvP alignment — market fracture detected"
        if "lasso_high_edge" in factors:
            return "Lasso model projects significant edge over the posted line"
        if "volume_increase" in factors and "favorable_matchup" in factors:
            return "Volume shift + soft defense = opportunity window"
        if "volume_increase" in factors:
            return "Lineup change creating usage vacuum"
        if "favorable_matchup" in factors:
            return "Defensive weakness in opponent's scheme"
        if "pace_advantage" in factors:
            return "Tempo multiplier active — increased opportunity volume"
        if "consistency" in factors:
            return "Low variance profile — reliable baseline performer"
        return "Baseline projection favorable"


_intel_calculator = None

def get_intel_calculator(db):
    global _intel_calculator
    if _intel_calculator is None:
        _intel_calculator = IntelSuiteCalculator(db)
    return _intel_calculator
