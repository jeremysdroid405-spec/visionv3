"""Team historical surfaces.

GET /api/v3/team/historical/{team_id}
    Returns per-team historical surfaces consumed by
    `frontend/src/hooks/useTeamMasterStats.js`:

      * `recent_outcomes`   — last-N graded outcomes per
                              (market_category, side, line). Powers
                              the over/under hit-rate bar chart on
                              `TeamDetailPage`.
      * `scoring_split`     — last-N rolling team-points-scored vs
                              team-points-conceded averages. Powers
                              the scoring/conceding split surface.
      * `h2h_outcomes`      — same as `recent_outcomes` but filtered
                              by `opponent_team_id`. Powers the
                              head-to-head history surface.

Source collection: `team_historical_outcomes` (graded by
`workers/team/team_outcomes_grader.py`, 391,851 NBA rows + 879k MLB
+ 124k NFL as of 2026-06-02).

This is the team analog of `useMasterStats` — same JSON contract
shape so the frontend wrapper-clone pattern can stay symmetric.
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter(tags=["team-historical"])


def _get_db():
    """Lazy DB handle resolver. Imported at call-time to avoid the
    circular `routes/__init__.py` → `routes.team_historical` →
    `server` import order at startup."""
    from server import db   # noqa: WPS433 (intentional late import)
    return db
_SUPPORTED_SPORTS = ("mlb", "nba", "nfl")

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50

# market_category whitelist for the chart surface. Composite team_total
# / spread / game_total are the lines bettors compare against; h2h
# (moneyline) is binary and shown as a separate "ATS / SU record".
_HIT_RATE_CATEGORIES = (
    "team_total", "spread", "game_total", "h2h",
)


def _norm_id(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    return str(s).strip().lower()


@router.get("/v3/team/historical/{team_id}")
async def get_team_historical(
    team_id: str,
    sport: str = Query(..., description="mlb | nba | nfl"),
    opponent_team_id: Optional[str] = Query(
        None,
        description="If supplied, narrows `h2h_outcomes` to "
                     "head-to-head meetings only."),
    market_category: Optional[str] = Query(
        None,
        description="Optional filter — team_total | spread | "
                     "game_total | h2h."),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
) -> Dict[str, Any]:
    """Per-team historical surfaces.

    Always returns three top-level lists (possibly empty); the client
    decides which surfaces to render. Empty `team_id` / unknown sport
    raises 400 — the route is sport-aware to match every other team
    endpoint.
    """
    sport_l = (sport or "").lower()
    if sport_l not in _SUPPORTED_SPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"sport={sport!r} not supported (mlb|nba|nfl)",
        )
    tid = _norm_id(team_id)
    if not tid:
        raise HTTPException(status_code=400, detail="team_id required")
    opp_tid = _norm_id(opponent_team_id)
    db = _get_db()

    base_filter: Dict[str, Any] = {
        "sport": sport_l,
        "team_id": tid,
        "outcome_resolved": True,
    }
    if market_category:
        base_filter["market_category"] = market_category

    # ── 1. Recent outcomes (last-N graded results per category) ─────
    recent_outcomes: List[Dict[str, Any]] = []
    proj = {
        "_id": 0, "game_date": 1, "commence_time": 1, "event_id": 1,
        "market_category": 1, "market_name": 1, "market_key": 1,
        "line": 1, "side": 1, "home_away": 1, "opponent_team_id": 1,
        "odds": 1, "hit": 1, "outcome": 1, "actual_value": 1,
        "home_score_used": 1, "away_score_used": 1,
        "margin_vs_line": 1, "is_alternate": 1, "book": 1,
    }
    cursor = (db["team_historical_outcomes"]
              .find(base_filter, proj)
              .sort("commence_time", -1)
              .limit(limit))
    async for r in cursor:
        recent_outcomes.append(r)

    # ── 2. Scoring / conceding split — rolling per-game averages ────
    # team_historical_outcomes carries `home_score_used` /
    # `away_score_used` on every graded row, so we just need the
    # team's own score + opponent score per unique game. Distinct on
    # `event_id` so multiple market rows per game don't double-count.
    scoring_pipeline = [
        {"$match": {**base_filter,
                     "home_score_used": {"$ne": None},
                     "away_score_used": {"$ne": None}}},
        {"$sort": {"commence_time": -1}},
        {"$group": {
            "_id": "$event_id",
            "game_date":      {"$first": "$game_date"},
            "commence_time":  {"$first": "$commence_time"},
            "home_away":      {"$first": "$home_away"},
            "opponent_team_id": {"$first": "$opponent_team_id"},
            "home_score":     {"$first": "$home_score_used"},
            "away_score":     {"$first": "$away_score_used"},
        }},
        {"$sort": {"commence_time": -1}},
        {"$limit": limit},
    ]
    scoring_split: List[Dict[str, Any]] = []
    async for r in db["team_historical_outcomes"].aggregate(scoring_pipeline):
        ha = (r.get("home_away") or "").lower()
        home = r.get("home_score")
        away = r.get("away_score")
        if home is None or away is None:
            continue
        team_score = home if ha == "home" else away
        opp_score = away if ha == "home" else home
        scoring_split.append({
            "game_date":        r.get("game_date"),
            "commence_time":    r.get("commence_time"),
            "event_id":         r.get("_id"),
            "home_away":        ha or None,
            "opponent_team_id": r.get("opponent_team_id"),
            "team_score":       team_score,
            "opp_score":        opp_score,
            "diff":             team_score - opp_score,
        })

    # ── 3. Head-to-head outcomes (same shape as recent, opp-filtered).
    h2h_outcomes: List[Dict[str, Any]] = []
    if opp_tid:
        cursor = (db["team_historical_outcomes"]
                  .find({**base_filter, "opponent_team_id": opp_tid}, proj)
                  .sort("commence_time", -1)
                  .limit(limit))
        async for r in cursor:
            h2h_outcomes.append(r)

    # ── 4. Quick aggregate summary the client can stamp on the
    #       header without computing again (hit rate over the loaded
    #       window).
    def _hit_rate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        n = sum(1 for r in rows if r.get("hit") is not None)
        wins = sum(1 for r in rows if r.get("hit") is True)
        return {
            "n": n,
            "wins": wins,
            "hit_pct": (wins / n * 100.0) if n else None,
        }

    # Limit window for the headline = first 10 of recent_outcomes
    # (most-recent-first by sort).
    headline = _hit_rate(recent_outcomes[:10]) if recent_outcomes else {
        "n": 0, "wins": 0, "hit_pct": None,
    }

    return {
        "team_id": tid,
        "sport": sport_l,
        "opponent_team_id": opp_tid,
        "market_category": market_category,
        "limit": limit,
        "recent_outcomes": recent_outcomes,
        "scoring_split": scoring_split,
        "h2h_outcomes": h2h_outcomes,
        "summary": {
            "last_10_hit_rate": headline,
        },
    }


__all__ = ["router"]
