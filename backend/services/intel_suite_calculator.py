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

# Feature name -> human readable for vision intel text
FEATURE_DISPLAY = {
    "prev_fga": "last-game shot attempts",
    "prev_fgm": "last-game field goals",
    "prev_fg3a": "last-game 3pt attempts",
    "prev_fg3m": "last-game threes",
    "prev_fta": "last-game free throw attempts",
    "prev_ftm": "last-game free throws",
    "prev_ast": "last-game assists",
    "prev_reb": "last-game rebounds",
    "prev_pts": "last-game points",
    "prev_turnover": "last-game turnovers",
    "prev_stl": "last-game steals",
    "prev_blk": "last-game blocks",
    "prev_min": "last-game minutes",
    "L10_avg_fgm": "L10 field goal average",
    "L10_avg_fga": "L10 shot volume",
    "L10_avg_fg3m": "L10 three-point average",
    "L10_avg_fg3a": "L10 three-point volume",
    "L10_avg_ftm": "L10 free throw average",
    "L10_avg_fta": "L10 free throw volume",
    "L10_avg_pts": "L10 scoring average",
    "L10_avg_reb": "L10 rebound average",
    "L10_avg_ast": "L10 assist average",
    "L10_avg_stl": "L10 steal average",
    "L10_avg_turnover": "L10 turnover rate",
    "L10_avg_oreb": "L10 offensive rebounding",
    "L10_avg_dreb": "L10 defensive rebounding",
    "L10_avg_min": "L10 minutes average",
    "L5_avg_ftm": "L5 free throw average",
    "L5_avg_fgm": "L5 field goal average",
    "L5_avg_reb": "L5 rebound average",
    "L5_avg_ast": "L5 assist average",
    "L3_avg_ast": "recent 3-game assist trend",
    "L3_avg_fga": "recent 3-game shot volume",
    "L3_avg_fg3a": "recent 3-game three-point volume",
    "target_L10_avg": "L10 stat average",
    "target_L5_avg": "L5 stat average",
    "target_L3_avg": "recent 3-game average",
    "target_cv_L10": "L10 consistency score",
    "momentum_hit_rate_L5": "5-game momentum",
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


def _humanize_driver(feature_name: str) -> str:
    """Convert a Lasso feature name to human-readable text."""
    if feature_name in FEATURE_DISPLAY:
        return FEATURE_DISPLAY[feature_name]
    # Fallback: clean up the raw name
    name = feature_name
    name = name.replace("ix_", "").replace("_x_", " x ").replace("delta3_", "3-game trend in ")
    name = name.replace("L10_avg_", "L10 ").replace("L5_avg_", "L5 ").replace("L3_avg_", "L3 ")
    name = name.replace("L10_std_", "L10 volatility in ").replace("L5_std_", "L5 volatility in ")
    name = name.replace("prev_", "last-game ")
    name = name.replace("_", " ")
    return name


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
        use_llm: bool = False,
    ) -> Dict[str, Any]:
        """Calculate full Intel Suite.
        use_llm=True: Call Gemini for scout text (batch enrichment only).
        use_llm=False: Fast baseline text (inline request-time calls).
        """

        # Fetch player from hub — only need game logs + team
        player = await self.master_hub.find_one(
            {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0, "display_name": 1, "team": 1, "position": 1,
             "history.2025_season": 1, "bdl_game_logs": 1}
        )
        if not player:
            player = {}

        team = player.get("team") or (board_pick.get("team") if board_pick else None)

        # Derive opponent from home_team/away_team if not explicitly provided
        if not opponent and board_pick:
            opponent = board_pick.get("opponent") or board_pick.get("opponent_abbr")
            if not opponent:
                player_team = team
                home = board_pick.get("home_team")
                away = board_pick.get("away_team")
                if player_team and home and away:
                    opponent = away if player_team == home else home

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

        vision_insight = await self._generate_vision_insight(
            player_name, stat_type, line, direction,
            usage_ripple, matchup_dvp, pace_delta, stability_index,
            board_pick, lasso_result, use_llm=use_llm,
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
            std_dev = float(np.std(recent, ddof=1))
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

    async def _generate_vision_insight(self, player_name, stat_type, line, direction, usage_ripple, matchup_dvp, pace_delta, stability_index, board_pick, lasso_result, use_llm=False):
        """Generate vision_insight — Gemini for batch enrichment, fast baseline for inline."""
        lasso_proj = lasso_result.get("projection") if lasso_result else None
        lasso_edge = (lasso_proj - line) if lasso_proj and line else None

        if use_llm:
            from services.gemini_scout_engine import generate_gemini_scout_intel, build_scout_payload
            intel_suite_data = {
                "matchup_dvp": matchup_dvp, "pace_delta": pace_delta,
                "stability_index": stability_index, "usage_ripple": usage_ripple,
            }
            payload = build_scout_payload(
                player_name=player_name, stat_type=stat_type, line=line,
                lasso_result=lasso_result, board_pick=board_pick,
                intel_suite=intel_suite_data, sport="nba",
            )
            scout_text = await generate_gemini_scout_intel(payload)
        else:
            # Fast baseline — no LLM call
            direction_word = "OVER" if (lasso_edge and lasso_edge > 0) else "UNDER"
            proj_str = f"{lasso_proj:.1f}" if lasso_proj else "N/A"
            edge_str = f"{lasso_edge:+.1f}" if lasso_edge else ""
            scout_text = f"{player_name} {stat_type} — Projection: {proj_str} vs Line: {line} ({direction_word} {edge_str} edge)."

        return {
            "primary": scout_text,
            "summary": scout_text,
            "lasso_projection": lasso_proj,
            "lasso_edge": round(lasso_edge, 2) if lasso_edge is not None else None,
            "confidence": lasso_result.get("confidence_tier", "STANDARD") if lasso_result else "STANDARD",
            "tactical_note": self._get_tactical_note_from_data(usage_ripple, matchup_dvp, pace_delta, lasso_result),
        }

    def _generate_scout_badges(self, active_logs, stat_type, line, board_pick, lasso_result):
        """Generate scout badges from live data + Lasso."""
        badges = []
        values = _extract_stat_values(active_logs, stat_type)
        if not values:
            return badges

        l10 = values[-10:] if len(values) >= 10 else values
        l10_avg = float(np.mean(l10))
        l10_std = float(np.std(l10, ddof=1))
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

    def _get_tactical_note_from_data(self, usage_ripple, matchup_dvp, pace_delta, lasso_result):
        bump = usage_ripple.get("bump_percent", 0)
        dvp = matchup_dvp.get("rank", 15)
        poss = pace_delta.get("possessions", 0)
        tier = lasso_result.get("confidence_tier", "") if lasso_result else ""
        edge_pct = 0
        if lasso_result and lasso_result.get("projection"):
            line = lasso_result.get("line") or 0
            if line:
                edge_pct = abs(lasso_result["projection"] - line) / line * 100

        if tier == "HIGH_FIDELITY" and edge_pct >= 15 and dvp >= 22:
            return "Model + DvP alignment — market fracture detected"
        if tier == "HIGH_FIDELITY" and edge_pct >= 15:
            return "High-fidelity model projects significant edge over posted line"
        if bump >= 3 and dvp >= 22:
            return "Volume shift + soft defense = opportunity window"
        if bump >= 3:
            return "Lineup change creating usage vacuum"
        if dvp >= 22:
            return "Defensive weakness in opponent's scheme"
        if poss >= 3:
            return "Tempo multiplier active — increased opportunity volume"
        return "Baseline projection favorable"


_intel_calculator = None

def get_intel_calculator(db):
    global _intel_calculator
    if _intel_calculator is None:
        _intel_calculator = IntelSuiteCalculator(db)
    return _intel_calculator
