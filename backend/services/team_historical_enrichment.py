"""Shared team-historical enrichment helpers.

Single home for the deterministic historical-stat math both
`routes/team_with_badges.py` (team detail page) and
`services/team_prop_tier_service.py` (Ferrari team board cards)
depend on. Centralised here so the two read paths can never drift.

Pure historical SSOT — every value comes from
`team_historical_outcomes`. No models, no LLM. The vision-intel
sentences are rules-based strings derived from hit / sample counts.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

_HIST_COLL = "team_historical_outcomes"


def split_team_id(team_id: str, sport: str) -> Tuple[str, str]:
    """`nba_bos` → ('bos', 'BOS')."""
    tid = (team_id or "").lower().strip()
    prefix = f"{sport}_"
    if tid.startswith(prefix):
        tid = tid[len(prefix):]
    return tid, tid.upper()


def hit_rate(rows: List[Dict[str, Any]]) -> Optional[float]:
    """Percent of rows where `hit is True`, ignoring ungraded."""
    graded = [r for r in rows if r.get("hit") in (True, False)]
    if not graded:
        return None
    wins = sum(1 for r in graded if r.get("hit") is True)
    return round(wins / len(graded) * 100.0, 1)


async def compute_hit_rates(
    db,
    *,
    team_id: str,
    sport: str,
    market_category: str,
    side: str,
    line: Optional[float],
    opp_team_id: Optional[str] = None,
    line_tolerance: float = 1.5,
    sample_cap: int = 200,
) -> Dict[str, Any]:
    """Per-(team, market_category, side, ~line) historical hit rate."""
    base: Dict[str, Any] = {
        "sport": sport,
        "team_id": team_id,
        "market_category": market_category,
        "outcome_resolved": True,
    }
    if side:
        base["side"] = side.upper()
    if market_category != "h2h" and line is not None:
        try:
            line_f = float(line)
        except (TypeError, ValueError):
            line_f = None
        if line_f is not None:
            base["line"] = {
                "$gte": line_f - line_tolerance,
                "$lte": line_f + line_tolerance,
            }
    if opp_team_id:
        base["opponent_team_id"] = opp_team_id

    pipeline = [
        {"$match": base},
        {"$sort": {"commence_time": -1}},
        {"$group": {
            "_id": "$event_id",
            "hit":          {"$first": "$hit"},
            "commence_time": {"$first": "$commence_time"},
            "line":         {"$first": "$line"},
        }},
        {"$sort": {"commence_time": -1}},
        {"$limit": sample_cap},
    ]
    rows: List[Dict[str, Any]] = []
    async for r in db[_HIST_COLL].aggregate(pipeline):
        rows.append(r)

    return {
        "hit_rate_l5":     hit_rate(rows[:5]),
        "hit_rate_l10":    hit_rate(rows[:10]),
        "hit_rate_l20":    hit_rate(rows[:20]),
        "season_hit_rate": hit_rate(rows),
        "sample_size":     len(rows),
    }


async def fetch_team_game_history(
    db, *, team_id: str, sport: str, limit: int = 25,
) -> List[Dict[str, Any]]:
    """Last-N graded games for a team — one row per event."""
    # Early pre-limit: process only the most recent rows (indexed by
    # commence_time DESC via ix_team_hist_game_history). MLB teams have
    # ~130 docs per event — scanning limit*200 rows guarantees we cover
    # the last `limit` unique events without scanning the full ~22K docs.
    pre_limit = limit * 200
    pipeline = [
        {"$match": {
            "sport": sport,
            "team_id": team_id,
            "outcome_resolved": True,
            "home_score_used": {"$ne": None},
            "away_score_used": {"$ne": None},
        }},
        {"$sort": {"commence_time": -1}},
        {"$limit": pre_limit},
        {"$group": {
            "_id": "$event_id",
            "game_date":        {"$first": "$game_date"},
            "commence_time":    {"$first": "$commence_time"},
            "home_away":        {"$first": "$home_away"},
            "opponent_team_id": {"$first": "$opponent_team_id"},
            "home_score":       {"$first": "$home_score_used"},
            "away_score":       {"$first": "$away_score_used"},
        }},
        {"$sort": {"commence_time": -1}},
        {"$limit": limit},
    ]
    out: List[Dict[str, Any]] = []
    async for r in db[_HIST_COLL].aggregate(pipeline):
        ha = (r.get("home_away") or "").lower()
        home, away = r.get("home_score"), r.get("away_score")
        if home is None or away is None:
            continue
        team_s = float(home) if ha == "home" else float(away)
        opp_s = float(away) if ha == "home" else float(home)
        opp_tid = r.get("opponent_team_id") or ""
        _, opp_abbr = split_team_id(opp_tid, sport)
        out.append({
            "event_id":         r.get("_id"),
            "game_date":        r.get("game_date"),
            "commence_time":    r.get("commence_time"),
            "home_game":        (ha == "home"),
            "opponent":         opp_abbr,
            "opponent_team_id": opp_tid,
            "team_score":       team_s,
            "opp_score":        opp_s,
            "total_score":      team_s + opp_s,
            "margin":           team_s - opp_s,
            "pts":              team_s,
        })
    return out


def _avg(values: List[Optional[float]]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


def compute_baseline_stats(
    game_logs: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Optional[float]]]:
    team_pts = [g["team_score"] for g in game_logs if g.get("team_score") is not None]
    opp_pts = [g["opp_score"] for g in game_logs if g.get("opp_score") is not None]
    total_pts = [g["total_score"] for g in game_logs if g.get("total_score") is not None]
    margin = [g["margin"] for g in game_logs if g.get("margin") is not None]

    def _bs(values: List[float]) -> Dict[str, Optional[float]]:
        return {
            "season_avg": _avg(values),
            "l5_avg":     _avg(values[:5]),
            "l10_avg":    _avg(values[:10]),
            "l20_avg":    _avg(values[:20]),
        }
    return {
        "PTS":        _bs(team_pts),
        "TEAM_TOTAL": _bs(team_pts),
        "OPP_TOTAL":  _bs(opp_pts),
        "GAME_TOTAL": _bs(total_pts),
        "SPREAD":     _bs(margin),
    }


_MARKET_CATEGORY_TO_STAT_TOKEN = {
    "team_total":  "TEAM_TOTAL",
    "game_total":  "GAME_TOTAL",
    "spread":      "SPREAD",
    "h2h":         "MONEYLINE",
}


def market_category_to_stat_token(market_category: str) -> str:
    return _MARKET_CATEGORY_TO_STAT_TOKEN.get(
        market_category, (market_category or "").upper() or "TEAM_PROP",
    )


def build_vision_intel(
    *,
    stat_token: str,
    side: str,
    line: Optional[float],
    hit_l5: Optional[float],
    hit_l10: Optional[float],
    hit_l20: Optional[float],
    season_hr: Optional[float],
    opp_abbr: Optional[str],
    opp_hit_pct: Optional[float],
    opp_sample: int,
) -> Optional[str]:
    """One short, deterministic sentence per prop describing the
    historical record. Returns None when there's no signal."""
    parts: List[str] = []
    side_label = (side or "").upper()
    line_label = f" {line}" if line is not None else ""
    market_label = {
        "TEAM_TOTAL": "team total",
        "GAME_TOTAL": "game total",
        "SPREAD":     "spread",
        "MONEYLINE":  "moneyline",
    }.get(stat_token, (stat_token or "").lower() or "team")

    if hit_l10 is not None:
        wins10 = int(round(hit_l10 / 10.0))
        parts.append(
            f"Hit {side_label}{line_label} in {wins10} of last 10 "
            f"{market_label} games."
        )
    if hit_l5 is not None:
        if hit_l5 >= 60:
            parts.append("Recent form trending over.")
        elif hit_l5 <= 40:
            parts.append("Recent form trending under.")
        else:
            parts.append("Recent form mixed.")
    if opp_abbr and opp_hit_pct is not None and opp_sample >= 2:
        wins_h2h = int(round(opp_hit_pct / 100.0 * opp_sample))
        parts.append(
            f"vs {opp_abbr}: {wins_h2h}/{opp_sample} hits on this market."
        )

    if not parts:
        return None
    return " ".join(parts)


def build_scout_badges(
    *,
    hit_l5: Optional[float],
    hit_l10: Optional[float],
    hit_l20: Optional[float],
) -> List[Dict[str, str]]:
    """Deterministic scout badges keyed on hit-rate thresholds.
    Same keys the frontend BADGE_REGISTRY already supports."""
    out: List[Dict[str, str]] = []
    if hit_l5 is not None and hit_l5 >= 80:
        out.append({
            "badge_key": "hot_streak",
            "name":      "Hot Streak",
            "description": (
                f"{int(round(hit_l5 / 10.0))} of last 5 games hit this side."
            ),
        })
    if hit_l20 is not None and hit_l20 >= 75:
        out.append({
            "badge_key": "floor_lock",
            "name":      "Floor Lock",
            "description": (
                f"Hit in {int(round(hit_l20 / 5.0))} of last 20 games "
                f"({hit_l20:.0f}%)."
            ),
        })
    return out


__all__ = [
    "split_team_id",
    "hit_rate",
    "compute_hit_rates",
    "fetch_team_game_history",
    "compute_baseline_stats",
    "market_category_to_stat_token",
    "build_vision_intel",
    "build_scout_badges",
]
