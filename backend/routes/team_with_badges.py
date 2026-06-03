"""Team-with-badges endpoint — the team analog of
`/api/v3/player-with-badges/{name}`.

Returns a player-shaped payload for a team, so `TeamDetailPage` can
forward the result directly to `PlayerDetailPage` (1:1 clone, no
team-specific UI).

GET /api/v3/team-with-badges/{team_id}?sport=nba|mlb|nfl

Response contract mirrors `routes/player.py::get_player_with_badges`:
    {
      "success": True,
      "sport": "nba",
      "source": "team_prop_scores+team_historical_outcomes",
      "player": {
        "player_name": "<team display name>",
        "team":        "<team abbr>",
        "photo_url":   "<team logo url>",
        "opponent":    "<opp abbr|null>",
        "event_id":    "<next event id|null>",
        "sport":       "nba",
        "is_team_prop": True,
        "props":       [...],      # all live team props for the team
        "baseline_stats": {...},   # team-level rollup (TEAM_TOTAL,
                                     OPP_TOTAL, GAME_TOTAL averages)
        "game_logs":   [...],      # last-N graded games (team/opp
                                     scores, margin, opponent)
      }
    }

Each prop carries the SAME field shape as a player prop — `stat_type`,
`stat_type_extracted`, `market`, `line`, `direction`,
`hit_rate_l5/l10/l20`, `l5_avg/l10_avg/l20_avg/season_avg`,
`vk_predicted`, `edge_vs_fair`, `best_book`, `vision_intel`,
`scout_badges`, `intel_suite` — so the unmodified PlayerDetailPage
renders every surface (prop row, bar chart, vision intel suite).

Vision Intel + scout_badges are DERIVED deterministically from the
team's historical hit history (no model, no LLM). Examples:
   - "Hit OVER 110.5 in 7 of last 10 games"
   - "Recent form trending over (4/5 last games)"
   - "vs DAL: 3/4 hits on this market over last meetings"

If `team_prop_scores` has no live rows for the team (e.g. NBA today
before the live ingest catches up), the endpoint still returns the
identity + baseline_stats + game_logs so the page is not empty —
just `props=[]` (PlayerDetailPage will show its standard
"No available Bets today" empty state, with the team header,
season stats, and game history still visible).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query

# Reuse the rich, SSOT team badge + intel-suite builders from the
# board pipeline so the detail card stays in sync with the pick card.
# `_build_team_scout_badges` produces sport-specific labels (Brick
# Wall / Green Wave / Fortress / Wolf Pack / etc.) — wording is team-
# centric throughout, no "player" references. `_build_team_intel_suite`
# produces the rich usage_ripple / pace_delta / blowout_risk /
# momentum_data / matchup_analysis tiles the modal renders.
from services.team_prop_tier_service import (
    _build_team_scout_badges as _build_rich_team_scout_badges,
    _build_team_intel_suite as _build_rich_team_intel_suite,
    _compute_league_ranks as _compute_team_league_ranks,
    compute_team_edge_pct,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["team-with-badges"])

_SUPPORTED_SPORTS = ("mlb", "nba", "nfl")

# Stat-type tokens used by the frontend (statLabel.js + GameLogBarChart
# STAT_FIELD_MAP + PlayerDetailPage CATEGORY_ORDER). One token per
# market_category — keeps the section grouping clean.
_MARKET_CATEGORY_TO_STAT_TOKEN = {
    "team_total":  "TEAM_TOTAL",
    "game_total":  "GAME_TOTAL",
    "spread":      "SPREAD",
    "h2h":         "MONEYLINE",
}


def _classify_market_category_from_key(market_key: Optional[str]) -> str:
    """Classify a raw market_key → canonical market_category.

    Mirrors `services.team_prop_tier_service._classify_market_category_from_key`.
    Necessary because `team_prop_scores` rows from the live passthrough
    don't always stamp `market_category`. Without this, every prop
    falls to the `TEAM_PROP` fallback token and the chart / hit-rate
    / section grouping all break.
    """
    mk = (market_key or "").lower()
    if not mk:
        return ""
    # NBA-style markets (the_odds_api returns these tokens verbatim)
    if mk == "h2h":
        return "h2h"
    if mk in ("spreads", "spread"):
        return "spread"
    if mk in ("totals", "total"):
        return "game_total"
    if "team_total" in mk or "team-total" in mk:
        return "team_total"
    # Odds-API extended (MLB/NBA): "points-{home|away}-game-{ml|sp|tt}-{home|away}"
    if "-ml-" in mk:
        return "h2h"
    if "-sp-" in mk:
        return "spread"
    if "-tt-" in mk or "team-total" in mk:
        return "team_total"
    if "-game-total" in mk or "game-total" in mk:
        return "game_total"
    # "first/innings" subset markets — keep as-is, not exposed today.
    return ""


# ── DB handle ─────────────────────────────────────────────────────────
_db = None


def set_team_with_badges_db(db) -> None:
    """Wire the DB handle. Called from `routes/__init__.py`."""
    global _db
    _db = db


# ── Identity helpers ──────────────────────────────────────────────────
_NBA_ABBR_TO_NAME = {
    "atl": "Atlanta Hawks", "bos": "Boston Celtics", "bkn": "Brooklyn Nets",
    "cha": "Charlotte Hornets", "chi": "Chicago Bulls",
    "cle": "Cleveland Cavaliers", "dal": "Dallas Mavericks",
    "den": "Denver Nuggets", "det": "Detroit Pistons",
    "gsw": "Golden State Warriors", "hou": "Houston Rockets",
    "ind": "Indiana Pacers", "lac": "LA Clippers", "lal": "Los Angeles Lakers",
    "mem": "Memphis Grizzlies", "mia": "Miami Heat",
    "mil": "Milwaukee Bucks", "min": "Minnesota Timberwolves",
    "nop": "New Orleans Pelicans", "nyk": "New York Knicks",
    "okc": "Oklahoma City Thunder", "orl": "Orlando Magic",
    "phi": "Philadelphia 76ers", "phx": "Phoenix Suns",
    "por": "Portland Trail Blazers", "sac": "Sacramento Kings",
    "sas": "San Antonio Spurs", "tor": "Toronto Raptors",
    "uta": "Utah Jazz", "was": "Washington Wizards",
}
_MLB_ABBR_TO_NAME = {
    "ari": "Arizona Diamondbacks", "atl": "Atlanta Braves",
    "bal": "Baltimore Orioles", "bos": "Boston Red Sox",
    "chc": "Chicago Cubs", "cws": "Chicago White Sox",
    "cin": "Cincinnati Reds", "cle": "Cleveland Guardians",
    "col": "Colorado Rockies", "det": "Detroit Tigers",
    "hou": "Houston Astros", "kc": "Kansas City Royals",
    "laa": "Los Angeles Angels", "lad": "Los Angeles Dodgers",
    "mia": "Miami Marlins", "mil": "Milwaukee Brewers",
    "min": "Minnesota Twins", "nym": "New York Mets",
    "nyy": "New York Yankees", "oak": "Oakland Athletics",
    "phi": "Philadelphia Phillies", "pit": "Pittsburgh Pirates",
    "sd": "San Diego Padres", "sf": "San Francisco Giants",
    "sea": "Seattle Mariners", "stl": "St. Louis Cardinals",
    "tb": "Tampa Bay Rays", "tex": "Texas Rangers",
    "tor": "Toronto Blue Jays", "wsh": "Washington Nationals",
}


def _split_team_id(team_id: str, sport: str) -> Tuple[str, str]:
    """`nba_bos` → ('bos', 'BOS').  Returns (abbr_lower, abbr_upper)."""
    tid = (team_id or "").lower().strip()
    prefix = f"{sport}_"
    if tid.startswith(prefix):
        tid = tid[len(prefix):]
    return tid, tid.upper()


def _team_display_name(team_id: str, sport: str) -> str:
    abbr_l, abbr_u = _split_team_id(team_id, sport)
    if sport == "nba":
        return _NBA_ABBR_TO_NAME.get(abbr_l, abbr_u)
    if sport == "mlb":
        return _MLB_ABBR_TO_NAME.get(abbr_l, abbr_u)
    return abbr_u


def _team_logo_url(team_id: str, sport: str) -> Optional[str]:
    """Team logo URL — matches the constants/TEAM_LOGO maps the
    frontend already uses for player headshots' team fallbacks. The
    backend doesn't host a logo CDN; the frontend resolves logos
    client-side via `getTeamLogo(sport, abbr)`. Return None here and
    let the frontend resolver pick it up from the `team` abbr.
    """
    return None


# ── Hit-rate / averages helpers ──────────────────────────────────────
def _hit_rate(rows: List[Dict[str, Any]]) -> Optional[float]:
    graded = [r for r in rows if r.get("hit") in (True, False)]
    if not graded:
        return None
    wins = sum(1 for r in graded if r.get("hit") is True)
    return round(wins / len(graded) * 100.0, 1)


def _team_score_from_row(row: Dict[str, Any]) -> Optional[float]:
    """Return team's own points scored from a graded outcome row.
    `home_score_used` / `away_score_used` carry the final score; the
    row's `home_away` flag tells us which side is the team."""
    ha = (row.get("home_away") or "").lower()
    h = row.get("home_score_used")
    a = row.get("away_score_used")
    if h is None or a is None:
        return None
    if ha == "home":
        return float(h)
    if ha == "away":
        return float(a)
    return None


def _opp_score_from_row(row: Dict[str, Any]) -> Optional[float]:
    ha = (row.get("home_away") or "").lower()
    h = row.get("home_score_used")
    a = row.get("away_score_used")
    if h is None or a is None:
        return None
    if ha == "home":
        return float(a)
    if ha == "away":
        return float(h)
    return None


async def _fetch_team_game_history(
    team_id: str, sport: str, limit: int = 25,
) -> List[Dict[str, Any]]:
    """Return distinct (event_id, commence_time) games for the team —
    one row per game with team_score / opp_score / total_score /
    margin / opponent. Source: `team_historical_outcomes`.
    """
    if _db is None:
        return []
    pipeline = [
        {"$match": {
            "sport": sport,
            "team_id": team_id,
            "outcome_resolved": True,
            "home_score_used": {"$ne": None},
            "away_score_used": {"$ne": None},
        }},
        {"$sort": {"commence_time": -1}},
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
    async for r in _db["team_historical_outcomes"].aggregate(pipeline):
        ha = (r.get("home_away") or "").lower()
        home = r.get("home_score")
        away = r.get("away_score")
        if home is None or away is None:
            continue
        team_s = float(home) if ha == "home" else float(away)
        opp_s = float(away) if ha == "home" else float(home)
        opp_tid = r.get("opponent_team_id") or ""
        _, opp_abbr = _split_team_id(opp_tid, sport)
        out.append({
            "event_id":     r.get("_id"),
            "game_date":    r.get("game_date"),
            "commence_time": r.get("commence_time"),
            "home_game":    (ha == "home"),
            "opponent":     opp_abbr,
            "opponent_team_id": opp_tid,
            "team_score":   team_s,
            "opp_score":    opp_s,
            "total_score":  team_s + opp_s,
            "margin":       team_s - opp_s,
            # Mirror the player-side game_logs schema. GameLogBarChart's
            # STAT_FIELD_MAP looks up `pts` for PTS — we route the
            # team's own score there so the same code path works for
            # the TEAM_TOTAL bar chart (extended map in the frontend).
            "pts":          team_s,
        })
    return out


def _avg(values: List[float]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


def _windowed_avg(
    game_logs: List[Dict[str, Any]], field: str, n: int,
) -> Optional[float]:
    return _avg([g.get(field) for g in game_logs[:n]])


# ── Hit-rate over a market_category at the row's line/side ───────────
async def _fetch_team_outcomes_bulk(
    team_id: str, sport: str,
) -> List[Dict[str, Any]]:
    """One-shot fetch of all graded outcomes for the team across every
    market_category. Caller filters in Python to compute per-prop hit
    rates without N+1 queries.

    2026-06-03 — prod was timing out at 60s on team-with-badges because
    `_hit_rates_for_market` was issuing 2× DB queries per (market,
    line, side) tuple — 30+ tuples × ~500ms cold-collection latency.
    Bulk fetch keeps the route under 1s.
    """
    if _db is None:
        return []
    out: List[Dict[str, Any]] = []
    cur = _db["team_historical_outcomes"].find(
        {
            "sport": sport, "team_id": team_id,
            "outcome_resolved": True,
        },
        {
            "_id": 0, "hit": 1, "commence_time": 1,
            "market_category": 1, "side": 1, "line": 1,
            "opponent_team_id": 1,
        },
    ).sort("commence_time", -1).limit(2000)
    async for r in cur:
        out.append(r)
    return out


def _hit_rates_in_memory(
    outcomes: List[Dict[str, Any]],
    market_category: str, side: str, line: Optional[float],
    opp_team_id: Optional[str] = None,
) -> Dict[str, Any]:
    """In-memory equivalent of `_hit_rates_for_market`, fed by the
    bulk outcomes list. Same line-window (± 1.5) and same h2h ignore
    rules — preserves the previous numeric output exactly."""
    side_up = (side or "").upper()
    try:
        line_f = float(line) if line is not None else None
    except (TypeError, ValueError):
        line_f = None
    rows: List[Dict[str, Any]] = []
    for r in outcomes:
        if r.get("market_category") != market_category:
            continue
        if side_up and (r.get("side") or "").upper() != side_up:
            continue
        if (
            market_category != "h2h"
            and line_f is not None
        ):
            rl = r.get("line")
            if rl is None:
                continue
            try:
                rlf = float(rl)
            except (TypeError, ValueError):
                continue
            if not (line_f - 1.5 <= rlf <= line_f + 1.5):
                continue
        if opp_team_id and r.get("opponent_team_id") != opp_team_id:
            continue
        rows.append(r)
        if len(rows) >= 200:
            break
    return {
        "hit_rate_l5":  _hit_rate(rows[:5]),
        "hit_rate_l10": _hit_rate(rows[:10]),
        "hit_rate_l20": _hit_rate(rows[:20]),
        "season_hit_rate": _hit_rate(rows),
        "sample_size":  len(rows),
    }


# ── Hit-rate over a market_category at the row's line/side ───────────
async def _hit_rates_for_market(
    team_id: str, sport: str, market_category: str,
    side: str, line: Optional[float], opp_team_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute hit_rate_l5/l10/l20 + season for a (team, category,
    side, ~line) tuple.

    Lines drift across books and dates; for team_total / game_total /
    spread we widen the line match to `± 1.5` so a 110.5 query still
    pulls 110 and 111 rows. h2h ignores line entirely.
    """
    if _db is None:
        return {}
    base: Dict[str, Any] = {
        "sport": sport, "team_id": team_id,
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
            base["line"] = {"$gte": line_f - 1.5, "$lte": line_f + 1.5}
    if opp_team_id:
        base["opponent_team_id"] = opp_team_id

    rows: List[Dict[str, Any]] = []
    async for r in (
        _db["team_historical_outcomes"]
        .find(base, {"_id": 0, "hit": 1, "commence_time": 1, "line": 1})
        .sort("commence_time", -1)
        .limit(200)
    ):
        rows.append(r)

    return {
        "hit_rate_l5":  _hit_rate(rows[:5]),
        "hit_rate_l10": _hit_rate(rows[:10]),
        "hit_rate_l20": _hit_rate(rows[:20]),
        "season_hit_rate": _hit_rate(rows),
        "sample_size":  len(rows),
    }


# ── Deterministic Vision Intel + Scout badges (rules, not models) ────
def _build_vision_intel(
    stat_token: str, side: str, line: Optional[float],
    hit_l5: Optional[float], hit_l10: Optional[float],
    hit_l20: Optional[float], season_hr: Optional[float],
    opp_abbr: Optional[str], opp_hit_pct: Optional[float],
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
    }.get(stat_token, stat_token.lower())

    # Last 10 anchor
    if hit_l10 is not None:
        wins10 = int(round(hit_l10 / 10.0))
        parts.append(
            f"Hit {side_label}{line_label} in {wins10} of last 10 "
            f"{market_label} games."
        )

    # Last 5 momentum
    if hit_l5 is not None:
        if hit_l5 >= 60:
            parts.append("Recent form trending over.")
        elif hit_l5 <= 40:
            parts.append("Recent form trending under.")
        else:
            parts.append("Recent form mixed.")

    # Head-to-head vs opponent
    if opp_abbr and opp_hit_pct is not None and opp_sample >= 2:
        wins_h2h = int(round(opp_hit_pct / 100.0 * opp_sample))
        parts.append(
            f"vs {opp_abbr}: {wins_h2h}/{opp_sample} hits on this market."
        )

    if not parts:
        return None
    return " ".join(parts)


def _build_scout_badges(
    hit_l5: Optional[float], hit_l10: Optional[float],
    hit_l20: Optional[float],
) -> List[Dict[str, str]]:
    """Deterministic scout badges. Same keys the frontend
    BADGE_REGISTRY already supports (`hot_streak`, `floor_lock`)."""
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


# ── Live props for the team ──────────────────────────────────────────
async def _fetch_team_live_props(
    team_id: str, sport: str,
) -> List[Dict[str, Any]]:
    if _db is None:
        return []
    rows: List[Dict[str, Any]] = []
    cursor = (
        _db["team_prop_scores"]
        .find({"sport": sport, "team_id": team_id}, {"_id": 0})
        .sort([("commence_time", -1)])
        .limit(500)
    )
    async for r in cursor:
        rows.append(r)
    return rows


def _bucket_best_book(
    rows_same_market: List[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[int]]:
    """Find the highest payout book for this market across books."""
    best_book = None
    best_odds: Optional[int] = None
    for r in rows_same_market:
        odds = r.get("odds")
        if odds is None:
            continue
        try:
            o = int(odds)
        except (TypeError, ValueError):
            continue
        if best_odds is None or o > best_odds:
            best_odds = o
            best_book = r.get("book")
    return best_book, best_odds


# ── Endpoint ─────────────────────────────────────────────────────────
@router.get("/v3/team-with-badges/{team_id}")
async def get_team_with_badges(
    team_id: str,
    sport: str = Query(..., description="mlb | nba | nfl"),
):
    """Player-shaped payload for a team. See module docstring."""
    if _db is None:
        raise HTTPException(status_code=500, detail="DB not initialized")
    sport = (sport or "").lower()
    if sport not in _SUPPORTED_SPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"sport={sport!r} not supported (mlb|nba|nfl)",
        )
    tid = (team_id or "").lower().strip()
    if not tid:
        raise HTTPException(status_code=400, detail="team_id required")

    abbr_lower, abbr_upper = _split_team_id(tid, sport)
    display_name = _team_display_name(tid, sport)

    # 1) Live props (may be empty when ingest hasn't run for this sport)
    live_rows = await _fetch_team_live_props(tid, sport)

    # 2) Game history (always available if historical data exists)
    game_logs = await _fetch_team_game_history(tid, sport, limit=25)

    # 2.5) Bulk outcomes — one query for the entire team. Per-prop hit
    # rates are computed in-memory below to avoid the prod 504 timeout
    # we hit when each (market, line, side) row issued its own query.
    team_outcomes = await _fetch_team_outcomes_bulk(tid, sport)

    # 2.6) League-wide ranks — SAME pre-computed table the board pipeline
    # uses. Cached after first call, so this is free on subsequent
    # requests. Without this `_build_team_scout_badges` can't fire any
    # rank-based badges (Brick Wall / Fast Lane / Deadeye / Fortress /
    # Jet Fuel / Barrel Club / Icebox / Stout Defense).
    league_ranks = await _compute_team_league_ranks(_db, sport)

    # 3) Compute baseline_stats — team-level rollups for the header strip
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
    # `PTS` is the SSOT NBA stat token so the existing baseline_stats
    # header strip (player_detail) renders the team's PPG/L5/L10
    # without any conditional. We also expose TEAM_TOTAL / GAME_TOTAL
    # / OPP_TOTAL / SPREAD so future surfaces can read them.
    baseline_stats: Dict[str, Dict[str, Optional[float]]] = {
        "PTS":        _bs(team_pts),
        "TEAM_TOTAL": _bs(team_pts),
        "OPP_TOTAL":  _bs(opp_pts),
        "GAME_TOTAL": _bs(total_pts),
        "SPREAD":     _bs(margin),
    }

    # 4) Resolve event / opponent from the first live row (preferred) or
    #    fall back to the most recent game in `game_logs`.
    event_id = None
    commence_time = None
    opp_abbr: Optional[str] = None
    home_team = None
    away_team = None
    if live_rows:
        head = live_rows[0]
        event_id = head.get("event_id")
        commence_time = head.get("commence_time")
        home_team = head.get("home_team")
        away_team = head.get("away_team")
        # Opponent isn't always stamped on team_prop_scores; derive
        # from home_team/away_team if present.
        if home_team and away_team:
            t_up = abbr_upper
            if str(home_team).upper().endswith(t_up):
                opp_abbr = str(away_team)
            elif str(away_team).upper().endswith(t_up):
                opp_abbr = str(home_team)
    if opp_abbr is None and game_logs:
        opp_abbr = game_logs[0].get("opponent")

    # 5) Shape each live row into a player-shaped prop, enriching with
    #    historical hit rates + deterministic intel + scout badges +
    #    game_logs (so PropRow's right-side bar chart renders).
    # Group rows by (market_key, line, side) and pick the best-odds book
    # per group. Same dedupe behavior players use across books.
    grouped: Dict[Tuple[str, Optional[float], str], List[Dict[str, Any]]] = {}
    for r in live_rows:
        mkt = r.get("market_key") or r.get("market") or ""
        side = (r.get("side") or "").upper()
        try:
            line_v = float(r["line"]) if r.get("line") is not None else None
        except (TypeError, ValueError):
            line_v = None
        grouped.setdefault((mkt, line_v, side), []).append(r)

    props: List[Dict[str, Any]] = []
    # Look up H2H stats once
    opp_team_id = None
    if opp_abbr:
        opp_team_id = f"{sport}_{(opp_abbr or '').lower()}"

    # ── Pre-compute league-wide rates the rich badge builder needs ──
    # `cover_rate_l10`      = team's last-10 spread win rate (any side
    #                        — for nba_bos rows are HOME/AWAY, not
    #                        OVER/UNDER, so we filter by category only)
    # `total_over_rate_l10` = % of last-10 team_total OVER rows hit
    #                        (game_total rows aren't always graded
    #                        per-team; team_total is the safer proxy)
    # In-memory pass over `team_outcomes` keeps this O(N) once.
    def _rate_for(category: str, side_filter: Optional[str] = None) -> Optional[float]:
        rows: List[Dict[str, Any]] = []
        for r in team_outcomes:
            if r.get("market_category") != category:
                continue
            if side_filter and (r.get("side") or "").upper() != side_filter:
                continue
            rows.append(r)
            if len(rows) >= 10:
                break
        if not rows:
            return None
        graded = [x for x in rows if x.get("hit") in (True, False)]
        if not graded:
            return None
        wins = sum(1 for x in graded if x.get("hit") is True)
        return round(wins / len(graded), 3)

    cover_rate_l10      = _rate_for("spread")
    total_over_rate_l10 = (
        _rate_for("game_total", "OVER")
        or _rate_for("team_total", "OVER")
    )

    for (mkt, line_v, side), rows in grouped.items():
        head = rows[0]
        market_category = (
            head.get("market_category")
            or _classify_market_category_from_key(mkt)
            or _classify_market_category_from_key(head.get("market"))
            or ""
        )
        stat_token = _MARKET_CATEGORY_TO_STAT_TOKEN.get(
            market_category, market_category.upper() or "TEAM_PROP",
        )

        # Direction normalization — mirror team_prop_tier_service so the
        # SAME pick clicked from the board and the detail row share an
        # identical `(stat_type, line, direction)` triplet. Without this
        # the detail page's `isHighlightedProp` match fails and the
        # auto-scroll to the clicked "gold bet" silently no-ops.
        # Rules:
        #   • OVER  → "OVER"
        #   • UNDER → "UNDER"
        #   • HOME  → "OVER"   (favorite-side for spread / home team total)
        #   • AWAY  → "UNDER"  (underdog-side for spread / away team total)
        #   • ML    → "OVER" if the row's home_team matches our team,
        #             else "UNDER" — keeps moneyline picks routable.
        _side_up = (side or "").upper()
        if _side_up in ("OVER", "HOME"):
            direction_norm = "OVER"
        elif _side_up in ("UNDER", "AWAY"):
            direction_norm = "UNDER"
        elif _side_up == "ML":
            _ha = (head.get("home_away") or "").upper()
            if _ha == "HOME" or (
                home_team
                and str(home_team).upper().endswith(abbr_upper)
            ):
                direction_norm = "OVER"
            else:
                direction_norm = "UNDER"
        else:
            direction_norm = _side_up or "OVER"
        best_book, best_odds = _bucket_best_book(rows)
        # Historical hit rates @ this (category, side, ~line) — fed by
        # the bulk outcomes list (one query for the entire team).
        hr = _hit_rates_in_memory(
            team_outcomes, market_category, side, line_v,
        )
        hit_l5 = hr.get("hit_rate_l5")
        hit_l10 = hr.get("hit_rate_l10")
        hit_l20 = hr.get("hit_rate_l20")
        season_hr = hr.get("season_hit_rate")

        # H2H hit rate (same bulk list, filtered by opponent_team_id)
        opp_hr = None
        opp_sample = 0
        if opp_team_id:
            h2h = _hit_rates_in_memory(
                team_outcomes, market_category, side, line_v,
                opp_team_id=opp_team_id,
            )
            opp_hr = h2h.get("season_hit_rate")
            opp_sample = h2h.get("sample_size", 0)

        # Baseline avg for this stat token (so projection cell renders)
        bs = baseline_stats.get(stat_token, {})
        l5_avg = bs.get("l5_avg")
        l10_avg = bs.get("l10_avg")
        l20_avg = bs.get("l20_avg")
        season_avg = bs.get("season_avg")

        # Projection: use l10 avg as the deterministic projection (no
        # model). Edge via SSOT helper — handles signed spread lines
        # and flips sign for UNDER/AWAY so the per-side `edge_pct`
        # the modal renders ALWAYS aligns with projection direction.
        projection = l10_avg
        edge_pct_signed = compute_team_edge_pct(
            projection, line_v, _side_up, market_category,
        )
        edge_vs_fair: Optional[float] = (
            round(edge_pct_signed / 100.0, 4)
            if edge_pct_signed is not None else None
        )

        vision_intel = _build_vision_intel(
            stat_token, side, line_v,
            hit_l5, hit_l10, hit_l20, season_hr,
            opp_abbr, opp_hr, opp_sample,
        )

        # Determine is_home for THIS row so home/away-conditioned badges
        # (Fortress / Jet Fuel / Night Shift) fire correctly.
        _row_home_team = head.get("home_team")
        is_home_for_row: Optional[bool] = None
        if _row_home_team:
            is_home_for_row = (
                str(_row_home_team).upper().endswith(abbr_upper)
            )

        # Derive vision_score / tp_pct / edge_pct from local signals so
        # Crown Play / Trap Detector / Sharpshooter can fire.
        tp_pct = season_hr  # season hit-rate proxy for TP%
        edge_pct = edge_pct_signed
        # Composite vision_score: weighted blend of hit_l20 + signed
        # edge%. Negative edge → vision_score drops; agreeing side
        # gets the boost it deserves.
        vision_score: Optional[float] = None
        if hit_l20 is not None and edge_pct is not None:
            vision_score = round(
                max(0.0, min(100.0, hit_l20 * 0.7 + edge_pct * 1.5)),
                1,
            )
        elif hit_l20 is not None:
            vision_score = hit_l20

        # Rich, board-parity team scout badges (Brick Wall / Green Wave
        # / Fortress / Wolf Pack / etc.). All wording is team-centric.
        # `league_ranks` now comes from the SAME cached aggregation the
        # board uses — rank-based badges fire normally.
        scout_badges = _build_rich_team_scout_badges(
            sport=sport,
            team_abbr=abbr_upper,
            market_category=market_category,
            side=_side_up,
            is_home=is_home_for_row,
            l5_avg=l5_avg,
            season_avg=season_avg,
            hit_l5=hit_l5,
            hit_l10=hit_l10,
            hit_l20=hit_l20,
            cover_rate_l10=cover_rate_l10,
            total_over_rate_l10=total_over_rate_l10,
            league_ranks=league_ranks,
            tp_pct=tp_pct,
            edge_pct=edge_pct,
            vision_score=vision_score,
        )

        # Rich intel_suite — usage_ripple / pace_delta / blowout_risk /
        # momentum_data / matchup_analysis / variance / lasso tiles.
        # Player-style modal sections all light up.
        intel_suite = _build_rich_team_intel_suite(
            sport=sport,
            market_category=market_category,
            side=_side_up,
            line=line_v,
            l5_avg=l5_avg,
            l10_avg=l10_avg,
            l20_avg=l20_avg,
            season_avg=season_avg,
            league_ranks=league_ranks,
            team_abbr=abbr_upper,
            opp_abbr=opp_abbr,
            opp_def_rank=(
                league_ranks.get("opp_score_rank", {}).get(
                    (opp_abbr or "").upper())
                if opp_abbr else None
            ),
            projection=projection,
            hit_l10=hit_l10,
            game_logs=game_logs,
        )
        # Carry the rich scout_badges INTO the suite so the modal's
        # legacy `intel_suite.scout_badges` fallback ALSO finds them.
        intel_suite["scout_badges"]  = scout_badges
        intel_suite["context_badges"] = scout_badges

        props.append({
            # SSOT identity fields (mirror player props)
            "stat_type":           stat_token,
            "stat_type_extracted": stat_token,
            "market":              mkt,
            "market_key":          mkt,
            "market_category":     market_category,
            "line":                line_v,
            "direction":           direction_norm,
            "side":                _side_up,
            "recommendation":      direction_norm,
            # Books / odds
            "book":                best_book,
            "best_book":           best_book,
            "best_book_odds":      best_odds,
            "odds":                best_odds,
            # Reference odds — UniversalPlayerCard::resolveDisplayOdds
            # reads these to render the primary odds chip. For team
            # detail rows the chosen `best_book` IS the reference book
            # (no DK→FD→MGM chain; we already picked the best price).
            "tier_reference_odds":    best_odds,
            "tier_reference_book":    best_book,
            "display_reference_odds": best_odds,
            "display_reference_book": best_book,
            # Historical SSOT stats
            "hit_rate_l5":         hit_l5,
            "hit_rate_l10":        hit_l10,
            "hit_rate_l20":        hit_l20,
            "season_hit_rate":     season_hr,
            "hit_rate_sample_size": hr.get("sample_size"),
            "l5_avg":              l5_avg,
            "l10_avg":             l10_avg,
            "l20_avg":             l20_avg,
            "season_avg":          season_avg,
            # Projection + edge (signed — flipped for UNDER/AWAY,
            # threshold-aware for signed spread lines)
            "vk_predicted":        projection,
            "projection":          projection,
            "edge_vs_fair":        edge_vs_fair,
            "edge_pct":            edge_pct_signed,
            "tp":                  tp_pct,
            "vision_score":        vision_score,
            # Vision Intel (deterministic, no model)
            "vision_intel":        vision_intel,
            "vision_summary":      vision_intel,
            "scout_badges":        scout_badges,
            "context_badges":      [],
            "active_badges":       [b["badge_key"] for b in scout_badges],
            "intel_suite":         intel_suite,
            # Game history attached per-prop so PropRow's chart works
            "game_logs":           game_logs,
            # Identity carry
            "event_id":            head.get("event_id"),
            "commence_time":       head.get("commence_time"),
            "team_id":             tid,
            "opponent":            opp_abbr,
            "opponent_team_id":    opp_team_id,
            "is_team_prop":        True,
            "prop_type":           "team",
            "sport":               sport,
        })

    # 6) Sort props for stable section grouping (category order then
    #    line ascending).
    _CATEGORY_RANK = {
        "TEAM_TOTAL": 0, "GAME_TOTAL": 1, "SPREAD": 2, "MONEYLINE": 3,
    }
    props.sort(key=lambda p: (
        _CATEGORY_RANK.get(p["stat_type"], 99),
        p.get("line") or 0.0,
        p.get("direction") or "",
    ))

    player_payload = {
        "player_name":   display_name,
        "name":          display_name,
        "team":          abbr_upper,
        "team_abbr":     abbr_upper,
        "team_id":       tid,
        "photo_url":     _team_logo_url(tid, sport),
        "headshot_url":  _team_logo_url(tid, sport),
        "team_logo_url": _team_logo_url(tid, sport),
        "sport":         sport,
        "opponent":      opp_abbr,
        "event_id":      event_id,
        "commence_time": commence_time,
        "home_team":     home_team,
        "away_team":     away_team,
        "is_team_prop":  True,
        "prop_type":     "team",
        "props":         props,
        "baseline_stats": baseline_stats,
        "game_logs":     game_logs,
        # Demons / goblins not applicable to teams (model-less). Surface
        # empty lists so PlayerDetailPage's `demons.length` / `goblins.length`
        # reads return 0 instead of crashing.
        "demons":        [],
        "goblins":       [],
    }

    return {
        "success": True,
        "sport":   sport,
        "source":  "team_prop_scores+team_historical_outcomes",
        "player":  player_payload,
    }


__all__ = ["router", "set_team_with_badges_db"]
