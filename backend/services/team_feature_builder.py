"""
Compute rolling team features from completed team_matchups and upsert
into team_model_features (one doc per team_id + sport + as_of_date).

Works identically for any sport — sport is always a parameter.
Safe to run multiple times on the same day (idempotent via upsert).
Missing values are stored as None/null, never as 0.
"""
from __future__ import annotations

import logging
import statistics
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from pymongo import UpdateOne

logger = logging.getLogger(__name__)

MATCHUPS_COLL = "team_matchups"
FEATURES_COLL = "team_model_features"


def _slope(values: List[float]) -> Optional[float]:
    """Least-squares slope of values ordered oldest→newest."""
    n = len(values)
    if n < 2:
        return None
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    if den == 0:
        return None
    return num / den


def _win_rate(games: List[Dict]) -> Optional[float]:
    margins = [g["margin"] for g in games if g["margin"] is not None]
    if not margins:
        return None
    return sum(1 for m in margins if m > 0) / len(margins)


def _avg(values: List[Optional[float]]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _build_features(games: List[Dict], as_of_date: date) -> Dict[str, Any]:
    """Compute all rolling features for a sorted (newest-first) game list."""
    sample_size = len(games)

    def tail(n: int) -> List[Dict]:
        return games[:n]

    l5 = tail(5)
    l10 = tail(10)

    scored_season = [g["scored"] for g in games]
    scored_l5 = [g["scored"] for g in l5]
    scored_l10 = [g["scored"] for g in l10]
    allowed_l10 = [g["allowed"] for g in l10]
    allowed_l5 = [g["allowed"] for g in l5]
    allowed_season = [g["allowed"] for g in games]
    margins_l10 = [g["margin"] for g in l10 if g["margin"] is not None]

    scored_l10_clean = [v for v in scored_l10 if v is not None]
    season_avg_total = _avg([g.get("total") for g in games])

    # spread cover proxy: margin > 0
    spread_cover_l10 = (
        sum(1 for m in margins_l10 if m > 0) / len(margins_l10)
        if margins_l10 else None
    )

    # OU hit rate proxy: actual total vs season avg total
    if season_avg_total is not None:
        ou_games = [
            g for g in l10
            if g.get("total") is not None
        ]
        ou_hit_rate = (
            sum(1 for g in ou_games if g["total"] > season_avg_total) / len(ou_games)
            if ou_games else None
        )
    else:
        ou_hit_rate = None

    # tempo proxy: avg total points per game last 10
    tempo_l10 = _avg([g.get("total") for g in l10])

    # run trend: slope of scored over last 10 (oldest→newest)
    scored_l10_ordered = [g["scored"] for g in reversed(l10) if g["scored"] is not None]
    run_trend_l10 = _slope(scored_l10_ordered) if len(scored_l10_ordered) >= 2 else None

    # rest days from last game
    if games:
        last_date = games[0].get("game_date")
        if last_date:
            if isinstance(last_date, datetime):
                last_date = last_date.date()
            elif isinstance(last_date, str):
                try:
                    last_date = datetime.fromisoformat(last_date).date()
                except ValueError:
                    last_date = None
            if last_date:
                rest_days = (as_of_date - last_date).days
            else:
                rest_days = None
        else:
            rest_days = None
    else:
        rest_days = None

    # home/away splits
    home_games = [g for g in games if g.get("is_home")]
    away_games = [g for g in games if not g.get("is_home")]

    # distribution stats on scored (all season)
    scored_clean = [v for v in scored_season if v is not None]
    if len(scored_clean) >= 2:
        mu = sum(scored_clean) / len(scored_clean)
        sigma = statistics.stdev(scored_clean)
        cv = sigma / mu if mu else None
    elif len(scored_clean) == 1:
        mu = scored_clean[0]
        sigma = None
        cv = None
    else:
        mu = sigma = cv = None

    return {
        "win_rate_l5":           _win_rate(l5),
        "win_rate_l10":          _win_rate(l10),
        "win_rate_season":       _win_rate(games),
        "avg_scored_l5":         _avg(scored_l5),
        "avg_scored_l10":        _avg(scored_l10),
        "avg_scored_season":     _avg(scored_season),
        "avg_allowed_l5":        _avg(allowed_l5),
        "avg_allowed_l10":       _avg(allowed_l10),
        "avg_allowed_season":    _avg(allowed_season),
        "home_win_rate":         _win_rate(home_games),
        "away_win_rate":         _win_rate(away_games),
        "spread_cover_rate_l10": spread_cover_l10,
        "ou_hit_rate_l10":       ou_hit_rate,
        "tempo_l10":             tempo_l10,
        "run_trend_l10":         run_trend_l10,
        "rest_days":             rest_days,
        "mu_points_scored":      mu,
        "sigma_points_scored":   sigma,
        "cv_points_scored":      cv,
        "sample_size":           sample_size,
    }


async def build_team_features_for_sport(db, sport: str) -> Dict[str, Any]:
    """Compute rolling features for all teams in `sport` and upsert to
    team_model_features.  Returns an audit dict."""
    sport = sport.lower()
    as_of = date.today()
    as_of_str = as_of.isoformat()

    logger.info("[TeamFeatureBuilder] %s — loading completed matchups", sport.upper())

    matchups: List[Dict] = await db[MATCHUPS_COLL].find(
        {
            "sport": sport,
            "home_score": {"$exists": True, "$ne": None},
            "away_score": {"$exists": True, "$ne": None},
        },
        {
            "event_id": 1,
            "game_date": 1,
            "home_team_id": 1,
            "away_team_id": 1,
            "home_score": 1,
            "away_score": 1,
            "home_spread": 1,
        },
    ).to_list(None)

    if not matchups:
        logger.warning("[TeamFeatureBuilder] %s — no completed matchups found", sport.upper())
        return {
            "sport": sport,
            "n_teams_processed": 0,
            "n_features_written": 0,
            "n_errors": 0,
            "as_of_date": as_of_str,
        }

    # Build per-team game lists
    team_games: Dict[str, List[Dict]] = {}

    for m in matchups:
        h_id = m.get("home_team_id")
        a_id = m.get("away_team_id")
        h_score = m.get("home_score")
        a_score = m.get("away_score")
        game_date = m.get("game_date")

        if h_id is None or a_id is None:
            continue

        total = (
            h_score + a_score
            if h_score is not None and a_score is not None
            else None
        )

        home_entry = {
            "game_date": game_date,
            "scored":    h_score,
            "allowed":   a_score,
            "margin":    (h_score - a_score) if h_score is not None and a_score is not None else None,
            "total":     total,
            "is_home":   True,
        }
        away_entry = {
            "game_date": game_date,
            "scored":    a_score,
            "allowed":   h_score,
            "margin":    (a_score - h_score) if h_score is not None and a_score is not None else None,
            "total":     total,
            "is_home":   False,
        }

        team_games.setdefault(h_id, []).append(home_entry)
        team_games.setdefault(a_id, []).append(away_entry)

    def _sort_key(g: Dict) -> Any:
        gd = g.get("game_date")
        if gd is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        if isinstance(gd, str):
            try:
                return datetime.fromisoformat(gd)
            except ValueError:
                return datetime.min.replace(tzinfo=timezone.utc)
        return gd

    ops: List[UpdateOne] = []
    n_errors = 0

    for team_id, games in team_games.items():
        try:
            games_sorted = sorted(games, key=_sort_key, reverse=True)
            features = _build_features(games_sorted, as_of)
            doc = {
                "team_id":    team_id,
                "sport":      sport,
                "as_of_date": as_of_str,
                "updated_at": datetime.now(timezone.utc),
                **features,
            }
            ops.append(UpdateOne(
                {"team_id": team_id, "sport": sport, "as_of_date": as_of_str},
                {"$set": doc},
                upsert=True,
            ))
        except Exception as exc:
            logger.exception(
                "[TeamFeatureBuilder] %s team_id=%s error: %s",
                sport.upper(), team_id, exc
            )
            n_errors += 1

    n_written = 0
    if ops:
        result = await db[FEATURES_COLL].bulk_write(ops, ordered=False)
        n_written = result.upserted_count + result.modified_count

    audit = {
        "sport":               sport,
        "n_teams_processed":   len(team_games),
        "n_features_written":  n_written,
        "n_errors":            n_errors,
        "as_of_date":          as_of_str,
    }
    logger.info(
        "[TeamFeatureBuilder] %s — teams=%s written=%s errors=%s as_of=%s",
        sport.upper(),
        audit["n_teams_processed"],
        audit["n_features_written"],
        audit["n_errors"],
        audit["as_of_date"],
    )
    return audit
