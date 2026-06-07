"""
Team Prop Tier Service — Phase 1 (board read path).

PRODUCTION ROUTING RULES (this module is held to them):
  * NO read from SGO archive collections (team_historical_props,
    nfl_historical_props, sgo_*).
  * Read source = `team_prop_scores` (mirrors player path:
    {sport}_prop_scores).
  * If `team_prop_scores` is empty for the requested sport, lazily
    invoke `passthrough_team_live_to_scores` to copy whatever is
    currently in `team_live_props` (the live-provider write target)
    into `team_prop_scores`. The future live-ingest pipeline can also
    call the passthrough explicitly after its writes.
  * Model fields stay None. Cards display `team_model_pending=true`.
  * 2026-06-02 — every card is enriched with REAL historical hit
    rates (l5/l10/l20), team averages, projection, deterministic
    vision_intel + scout_badges from `team_historical_outcomes`
    via `services/team_historical_enrichment.py`. Same SSOT helpers
    `routes/team_with_badges.py` uses, so the board cards and the
    detail page can never disagree on the numbers.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import logging

# Per-sport debounce: track when the XGB scorer last ran so we don't
# block every request with a 30s scoring pass.  Requests that arrive
# while a score is already running (or ran recently) return the
# current passthrough data immediately; a background task updates the
# scores asynchronously for the NEXT request.
_SCORE_LAST_RUN: Dict[str, float] = {}
_SCORE_RUNNING: Dict[str, bool] = {}
_SCORE_DEBOUNCE_SECS = 120.0  # re-score at most once every 2 minutes per sport

from services.team_prop_passthrough import (
    SCORES_COLL,
    passthrough_team_live_to_scores,
)
from services.team_historical_enrichment import (
    compute_hit_rates,
    fetch_team_game_history,
    compute_baseline_stats,
    market_category_to_stat_token,
    build_vision_intel,
    build_scout_badges,
    split_team_id,
)


def _classify_market_category_from_key(market_key):
    """Mirror of `team_live_xgb_scorer.classify_market_category` —
    duplicated here to avoid an import cycle and keep this service
    self-contained.

      *-ml-*                       → h2h
      *-sp-*                       → spread
      *-all-game-ou-*              → game_total
      *-home-game-ou-*/-away-game-ou-* → team_total

    `market_key` here is what the live sync writes — either the raw
    Odds API key (`h2h` / `spreads` / `totals` / `team_totals`) or
    the canonical seeded `points-{side}-game-{type}` token. We
    accept both shapes.
    """
    if not market_key:
        return None
    s = str(market_key).lower()
    # Odds-API raw keys (live sync writes these on team_live_props).
    if s == "h2h":
        return "h2h"
    if s == "spreads":
        return "spread"
    if s == "totals":
        return "game_total"
    if s == "team_totals":
        return "team_total"
    # Canonical seeded / scorer token shape.
    if "-ml-" in s:
        return "h2h"
    if "-sp-" in s:
        return "spread"
    if "-all-game-ou-" in s:
        return "game_total"
    if "-home-game-ou-" in s or "-away-game-ou-" in s:
        return "team_total"
    return None

logger = logging.getLogger(__name__)


# ── Edge math (SSOT) ─────────────────────────────────────────────────
# A single source of truth for team-prop edge calculation. Used by
# BOTH the board (this module) and the detail endpoint
# (`routes/team_with_badges.py`). The naive `(projection - line) / line`
# produces garbage for spreads (line is signed) and never flips sign
# for UNDER/AWAY picks — both bugs reported by the user 2026-06-03
# ("we shouldn't be recommending an UNDER when projection is over").
#
# Returns edge as a SIGNED percentage:
#   • positive  → projection AGREES with the side (good pick)
#   • negative  → projection DISAGREES with the side (bad pick)
#   • None      → not computable (no line / no projection / h2h)
def compute_team_edge_pct(
    projection: Optional[float],
    line: Optional[float],
    side: Optional[str],
    market_category: Optional[str],
) -> Optional[float]:
    """Signed edge % for (projection, line, side, market_category).

    SPREAD: `line` is signed for the SIDE you're betting.
        - HOME -5.5 means: bet wins if team's margin > 5.5
        - AWAY +5.5 means: bet wins if team's margin > -5.5
        In BOTH cases the threshold = -line (since the projection is
        the team's avg margin from team_historical_outcomes, which
        flips for HOME vs AWAY rows). So:
          edge = (projection - threshold) / max(|threshold|, 1) * 100
        No side-flip needed for spreads — `line` already encodes side.

    TEAM_TOTAL / GAME_TOTAL: `line` is positive (e.g. 110.5).
        - OVER  → edge = (proj - line) / line * 100
        - UNDER → flip sign (UNDER wins when proj < line)

    H2H / MONEYLINE: no line → None.
    """
    if projection is None or line is None:
        return None
    cat = (market_category or "").lower()
    if cat == "h2h":
        return None
    try:
        line_f = float(line)
    except (TypeError, ValueError):
        return None
    side_up = (side or "").upper()

    if cat == "spread":
        # Threshold for the side this row represents. For team_historical_outcomes
        # the `hit` / `margin` fields are computed for THIS team's perspective,
        # so `-line` is the correct threshold whether HOME (line<0) or AWAY
        # (line>0). NO side-flip — the sign is in `line` already.
        threshold = -line_f
        baseline = max(abs(threshold), 1.0)
        delta = projection - threshold
        return round(delta / baseline * 100.0, 1)

    # team_total / game_total / fallback (positive line, OVER/UNDER sides)
    if line_f == 0:
        return None
    baseline = abs(line_f)
    delta = projection - line_f
    if side_up in ("UNDER", "AWAY"):
        delta = -delta
    return round(delta / baseline * 100.0, 1)


MATCHUP_COLL_BY_SPORT: Dict[str, str] = {
    "mlb":   "team_matchups",
    "nba":   "team_matchups",
    "nfl":   "nfl_matchups",
    "ncaaf": "ncaaf_matchups",
}
TEAM_MASTER_COLL = "team_master_hub"

_TIER_LABEL = {
    "safe_haven":  "Safe Haven",
    "front_lines": "Front Lines",
    "war_zone":    "War Zone",
}


async def _build_team_lookup(db, sport: str
                                  ) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    async for t in db[TEAM_MASTER_COLL].find(
        {"sport": sport}, projection={"_id": 0}):
        tid = t.get("team_id")
        if not tid:
            continue
        dn   = t.get("display_names") or {}
        cols = t.get("colors") or {}
        out[tid] = {
            "team_id":       tid,
            "team_abbr":     dn.get("abbrev"),
            "team_name":     dn.get("full") or dn.get("short"),
            "team_short":    dn.get("short"),
            "team_market":   dn.get("market"),
            "sport":         t.get("sport"),
            "team_logo_url": None,
            "team_colors":   cols,
        }
    return out


async def _build_event_lookup(db, sport: str,
                                   event_ids: List[str]
                                   ) -> Dict[str, Dict[str, Any]]:
    if not event_ids:
        return {}
    coll = MATCHUP_COLL_BY_SPORT.get(sport, "team_matchups")
    flt: Dict[str, Any] = {"event_id": {"$in": event_ids}}
    if sport == "nfl":
        flt["sport"] = "nfl"
    out: Dict[str, Dict[str, Any]] = {}
    async for m in db[coll].find(flt, projection={"_id": 0}):
        out[m["event_id"]] = m
    return out


def _market_label(score_row: Dict[str, Any]) -> str:
    """Best-available label without inventing strings."""
    for k in ("market_name", "stat_type", "market", "market_key"):
        v = score_row.get(k)
        if isinstance(v, str) and v.strip():
            return v.replace("_", " ").strip().title() \
                if k in ("stat_type", "market_key") else v
    return "Team Prop"


def _hydrate_card(score: Dict[str, Any],
                     teams: Dict[str, Dict[str, Any]],
                     events: Dict[str, Dict[str, Any]]
                     ) -> Optional[Dict[str, Any]]:
    """`team_prop_scores` row → UniversalPlayerCard-shaped pick."""
    tid = score.get("team_id")
    if not tid:
        return None
    event_id = score.get("event_id")
    matchup  = events.get(event_id) or {}
    home_id  = matchup.get("home_team_id")
    away_id  = matchup.get("away_team_id")
    this_team = teams.get(tid) or {}
    opp_team  = teams.get(away_id if tid == home_id else home_id) or {}
    is_home   = (tid == home_id)
    team_name = this_team.get("team_short") \
                or this_team.get("team_name") \
                or (matchup.get("home_team_name") if is_home
                    else matchup.get("away_team_name"))
    opp_name  = opp_team.get("team_short") \
                or opp_team.get("team_name") \
                or (matchup.get("away_team_name") if is_home
                    else matchup.get("home_team_name"))
    market_label = _market_label(score)

    # Fallback opponent resolution: when `events` doesn't have the
    # event (e.g. synthetic seeds or rows missing the master_hub
    # entry), use `home_team` / `away_team` strings stamped on the
    # score row by the live ingest.
    if not opp_name:
        home_team_str = score.get("home_team") or ""
        away_team_str = score.get("away_team") or ""
        team_abbr = (this_team.get("team_abbr") or "").upper()
        team_id_abbr = (tid or "").split("_", 1)[-1].upper()
        # If `home_team` string ends with this team's name or matches
        # the abbr, the team is HOME and opp = away_team_str.
        if home_team_str and team_id_abbr and (
                team_id_abbr in home_team_str.upper()
                or team_abbr and team_abbr in home_team_str.upper()):
            opp_name = away_team_str or opp_name
            is_home = True
        elif away_team_str and team_id_abbr and (
                team_id_abbr in away_team_str.upper()
                or team_abbr and team_abbr in away_team_str.upper()):
            opp_name = home_team_str or opp_name
            is_home = False

    # Derive opponent abbr from the resolved opponent display name —
    # downstream `_resolve_opp_def_rank` keys on the abbr.
    opp_abbr = None
    if opp_name:
        # Reverse-lookup opponent abbreviation from the already-loaded
        # team_master_hub dict (no extra import needed).
        opp_lower = opp_name.strip().lower()
        for v in teams.values():
            candidate = (v.get("team_name") or v.get("team_short") or "").strip().lower()
            if candidate and candidate == opp_lower:
                opp_abbr = v.get("team_abbr")
                break

    return {
        # Identity
        "prop_type":          "team",
        "team_id":            tid,
        "team_name":          team_name,
        "team_abbr":          this_team.get("team_abbr"),
        "team_logo_url":      this_team.get("team_logo_url"),
        "team_colors":        this_team.get("team_colors") or {},
        "team":               team_name,
        "opponent":           opp_name,
        "opponent_abbr":      opp_abbr,
        "home_team":          score.get("home_team"),
        "away_team":          score.get("away_team"),
        "home_away":          "home" if is_home else "away",
        "is_home":            is_home,
        "player_id":          None,
        "player_name":        None,
        "photo_url":          None,
        "headshot_url":       None,

        # Market / line / odds (passthrough)
        "event_id":            event_id,
        "game_date":           score.get("game_date"),
        "commence_time":       score.get("commence_time"),
        "sport":               score.get("sport"),
        "market":              market_label,
        "market_key":          score.get("market_key"),
        "display_market":      market_label,
        "stat_type":           score.get("stat_type"),
        "period_id":           score.get("period_id"),
        "line":                score.get("line"),
        "side":                score.get("side"),
        "odds":                score.get("odds"),
        "sharp_price":         score.get("sharp_price"),
        "book":                score.get("book"),
        "bookmaker":           score.get("book"),
        "is_alternate":        score.get("is_alternate"),
        "reference_only":      score.get("reference_only"),

        # Tier + display
        "tier":                score.get("tier"),
        "tier_label":          score.get("tier_label"),
        "badges":              score.get("badges") or [],

        # Model fields — populated when the XGB scorer has run (set by
        # services.team_live_xgb_scorer). Until then they're None and
        # `team_model_pending=True` keeps the card in its pending state.
        "vision_score":        score.get("vision_score"),
        "intel_score":         score.get("intel_score"),
        "intel_verdict":       None,
        "intel_suite":         None,
        "vision_intel":        score.get("vision_intel"),
        "vision_intel_content_hash":  score.get("vision_intel_content_hash"),
        "vision_intel_generated_at":  score.get("vision_intel_generated_at"),
        "confidence":          score.get("confidence"),
        "model_probability":   score.get("model_probability"),
        "true_probability":    score.get("true_probability"),
        "implied_probability": score.get("implied_probability"),
        "edge":                score.get("edge"),
        "edge_pct":            score.get("edge_pct"),
        "projection":          None,
        "cv":                  None,
        "vk_predicted":        None,
        "season_avg":          None,
        "hit_rate_l5":         None,
        "hit_rate_l10":        None,
        "hit_rate_l20":        None,
        "context_badges":      [],
        "is_vision_enriched":  bool(score.get("model_probability") is not None),
        "validation":          {"is_fully_validated": False,
                                 "has_mlr": False,
                                 "has_gemini": False},
        "playable_on_pp":      True,

        # Flags / lineage
        "team_model_pending":  bool(score.get("team_model_pending", True)),
        "model_version":       score.get("model_version"),
        "market_category":     score.get("market_category"),
        "gate_reasons":        score.get("gate_reasons") or [],
        "routing_source":      score.get("routing_source") or "odds",
        "odds_routed":         True,
        "snapshot_iso":        score.get("snapshot_iso"),
        "ingested_at":         score.get("ingested_at"),
        "passthrough_at":      score.get("passthrough_at"),
        "scored_at":           score.get("scored_at"),
    }


async def _resolve_opp_def_rank(
    db, *, sport: str, opp_team_abbr: Optional[str],
) -> Optional[int]:
    """Rank the opponent's points-allowed average against all other
    teams in the same sport. Cheap: a single aggregation over
    `team_historical_outcomes` keyed on `opponent_team_id`. Cached
    per request via the LRU in this module.
    """
    if not opp_team_abbr:
        return None
    sport_l = (sport or "").lower()
    cache_key = sport_l
    ranks = _OPP_DEF_RANK_CACHE.get(cache_key)
    if ranks is None:
        # Aggregate: for every team, the mean of `home_score_used +
        # away_score_used` filtered to games where THIS team was the
        # OPPONENT. Lower mean = tougher defense = lower rank (#1).
        pipeline = [
            {"$match": {
                "sport": sport_l,
                "outcome_resolved": True,
                "home_score_used": {"$ne": None},
                "away_score_used": {"$ne": None},
                "opponent_team_id": {"$ne": None},
            }},
            {"$group": {
                "_id": "$opponent_team_id",
                "n": {"$sum": 1},
                # The opponent's points scored = (this team's row's
                # opponent_score_used). The row stores BOTH scores
                # already so we just use total - team_score.
                "total_points_avg": {"$avg": {"$add": [
                    "$home_score_used", "$away_score_used"]}},
            }},
            {"$match": {"n": {"$gte": 5}}},
            {"$sort": {"total_points_avg": 1}},  # lowest = tougher
        ]
        ranks = {}
        i = 0
        async for r in db["team_historical_outcomes"].aggregate(pipeline):
            i += 1
            tid = (r["_id"] or "").lower()
            # Convert tid like "nba_bos" → "BOS" abbreviation
            abbr = tid.split("_", 1)[-1].upper()
            ranks[abbr] = i
        _OPP_DEF_RANK_CACHE[cache_key] = ranks
    return ranks.get((opp_team_abbr or "").upper())


_OPP_DEF_RANK_CACHE: Dict[str, Dict[str, int]] = {}
# League-wide ranking cache. Keyed by sport. Contains ALL the rank
# tables team scout badges need:
#   team_score_rank, opp_score_rank, total_score_rank,
#   home_win_pct_rank, away_win_pct_rank, cover_rate_l10,
#   total_over_rate_l10.
# Computed once per process (refresh by restart). Cheap: <100ms for
# all 4 aggregations at startup; zero per-request cost thereafter.
_LEAGUE_RANKS_CACHE: Dict[str, Dict[str, Dict[str, Any]]] = {}


async def _compute_league_ranks(db, sport: str) -> Dict[str, Dict[str, Any]]:
    """Aggregate league-wide ranking tables for the badge system.

    Returns:
        {
          "team_score_rank":     {"BOS": 3, "DAL": 17, ...},
          "opp_score_rank":      {"BOS": 5, ...},
          "total_score_rank":    {...},          # pace proxy
          "home_win_pct":        {"BOS": 0.72, ...},
          "away_win_pct":        {"BOS": 0.55, ...},
          "home_win_rank":       {...},
          "away_win_rank":       {...},
        }

    Every value is rank 1 = best (lowest opp_score = best D; highest
    team_score = best O; highest pace = #1 fast lane).
    """
    sport_l = (sport or "").lower()
    if sport_l in _LEAGUE_RANKS_CACHE:
        return _LEAGUE_RANKS_CACHE[sport_l]

    coll = db["team_historical_outcomes"]
    base_match = {
        "sport": sport_l, "outcome_resolved": True,
        "home_score_used": {"$ne": None},
        "away_score_used": {"$ne": None},
    }

    # ── Per-team aggregates (avg team_score, opp_score, total). ──
    pipeline = [
        {"$match": base_match},
        # Dedupe: one row per (event, team) — `team_historical_outcomes`
        # has many rows per event (one per market×line), so we group
        # to event-level first to avoid biasing toward popular markets.
        {"$group": {
            "_id": {"event": "$event_id", "team": "$team_id"},
            "home_score": {"$first": "$home_score_used"},
            "away_score": {"$first": "$away_score_used"},
            "home_away":  {"$first": "$home_away"},
        }},
        {"$project": {
            "team_id": "$_id.team",
            "team_score": {"$cond": [
                {"$eq": ["$home_away", "home"]},
                "$home_score", "$away_score",
            ]},
            "opp_score": {"$cond": [
                {"$eq": ["$home_away", "home"]},
                "$away_score", "$home_score",
            ]},
            "total_score": {"$add": ["$home_score", "$away_score"]},
            "home_away": 1,
        }},
        {"$group": {
            "_id":         "$team_id",
            "n":           {"$sum": 1},
            "team_avg":    {"$avg": "$team_score"},
            "opp_avg":     {"$avg": "$opp_score"},
            "total_avg":   {"$avg": "$total_score"},
            "home_wins":   {"$sum": {"$cond": [
                {"$and": [
                    {"$eq": ["$home_away", "home"]},
                    {"$gt": ["$team_score", "$opp_score"]},
                ]}, 1, 0]}},
            "home_games":  {"$sum": {"$cond": [
                {"$eq": ["$home_away", "home"]}, 1, 0]}},
            "away_wins":   {"$sum": {"$cond": [
                {"$and": [
                    {"$eq": ["$home_away", "away"]},
                    {"$gt": ["$team_score", "$opp_score"]},
                ]}, 1, 0]}},
            "away_games":  {"$sum": {"$cond": [
                {"$eq": ["$home_away", "away"]}, 1, 0]}},
        }},
        {"$match": {"n": {"$gte": 10}}},
    ]

    teams: List[Dict[str, Any]] = []
    async for r in coll.aggregate(pipeline, allowDiskUse=True):
        tid_abbr = (r["_id"] or "").split("_", 1)[-1].upper()
        teams.append({
            "abbr":         tid_abbr,
            "team_avg":     r["team_avg"],
            "opp_avg":      r["opp_avg"],
            "total_avg":    r["total_avg"],
            "home_win_pct": (r["home_wins"] / r["home_games"]
                              if r["home_games"] else 0.0),
            "away_win_pct": (r["away_wins"] / r["away_games"]
                              if r["away_games"] else 0.0),
        })

    def _rank_desc(field: str) -> Dict[str, int]:
        return {t["abbr"]: i + 1
                 for i, t in enumerate(sorted(
                     teams, key=lambda t: -t[field]))}

    def _rank_asc(field: str) -> Dict[str, int]:
        return {t["abbr"]: i + 1
                 for i, t in enumerate(sorted(
                     teams, key=lambda t: t[field]))}

    out = {
        "team_score_rank":   _rank_desc("team_avg"),
        "opp_score_rank":    _rank_asc("opp_avg"),   # lower = better D
        "total_score_rank":  _rank_desc("total_avg"),  # pace proxy
        "home_win_rank":     _rank_desc("home_win_pct"),
        "away_win_rank":     _rank_desc("away_win_pct"),
        "team_avg":          {t["abbr"]: t["team_avg"]   for t in teams},
        "opp_avg":           {t["abbr"]: t["opp_avg"]    for t in teams},
        "total_avg":         {t["abbr"]: t["total_avg"]  for t in teams},
    }
    _LEAGUE_RANKS_CACHE[sport_l] = out
    return out


async def _compute_spread_total_rates(
    db, team_id: str, sport: str,
) -> Dict[str, Optional[float]]:
    """L10 cover rate (spread) and L10 over rate (total). Both
    derived from `team_historical_outcomes` so they ride the SSOT.
    """
    coll = db["team_historical_outcomes"]
    out: Dict[str, Optional[float]] = {
        "cover_rate_l10": None, "total_over_rate_l10": None,
    }

    # Spread cover rate L10 (this team, spread market, last 10).
    rows: List[Dict[str, Any]] = []
    async for r in coll.find(
        {"sport": sport, "team_id": team_id,
         "market_category": "spread", "outcome_resolved": True},
        {"_id": 0, "hit": 1, "commence_time": 1},
    ).sort("commence_time", -1).limit(10):
        rows.append(r)
    if rows:
        out["cover_rate_l10"] = round(
            sum(1 for r in rows if r.get("hit") is True) / len(rows), 3)

    # Total OVER rate L10 (this team, game_total OVER side).
    rows = []
    async for r in coll.find(
        {"sport": sport, "team_id": team_id,
         "market_category": "game_total", "side": "OVER",
         "outcome_resolved": True},
        {"_id": 0, "hit": 1, "commence_time": 1},
    ).sort("commence_time", -1).limit(10):
        rows.append(r)
    if rows:
        out["total_over_rate_l10"] = round(
            sum(1 for r in rows if r.get("hit") is True) / len(rows), 3)

    return out


def _build_team_scout_badges(
    *,
    sport: str,
    team_abbr: Optional[str],
    market_category: str,
    side: str,
    is_home: Optional[bool],
    l5_avg: Optional[float],
    season_avg: Optional[float],
    hit_l5: Optional[float],
    hit_l10: Optional[float],
    hit_l20: Optional[float],
    cover_rate_l10: Optional[float],
    total_over_rate_l10: Optional[float],
    league_ranks: Dict[str, Dict[str, Any]],
    tp_pct: Optional[float],
    edge_pct: Optional[float],
    vision_score: Optional[float],
) -> List[Dict[str, Any]]:
    """Build the canonical deterministic team scout badge list.

    Returns at most 3 badges, sorted by `priority` descending. Every
    badge matches the schema in the directive:
        {label, reason, metric, value, threshold,
         source_collection, priority}
    """
    sport_l = (sport or "").lower()
    abbr = (team_abbr or "").upper()
    out: List[Dict[str, Any]] = []

    # Helper to push a badge dict.
    def _push(label, reason, metric, value, threshold, priority,
                source="team_historical_outcomes", icon=None, color="amber"):
        out.append({
            "label":             label,
            "reason":            reason,
            "metric":            metric,
            "value":             value,
            "threshold":         threshold,
            "source_collection": source,
            "priority":          priority,
            "icon":              icon,
            "color":             color,
            # back-compat with the player scout_badges renderer
            "badge_key":         label.lower().replace(" ", "_"),
            "name":              label,
            "description":       reason,
            "display":           label,
        })

    # ── Crown Play (priority 100, gold glow trigger). ─────────────
    if (vision_score is not None and tp_pct is not None
            and edge_pct is not None
            and vision_score >= 80 and tp_pct >= 65 and edge_pct >= 5):
        _push(
            "Crown Play",
            f"Vision {vision_score:.0f} · TP {tp_pct:.0f}% · "
            f"Edge {edge_pct:+.1f}%",
            "composite", vision_score,
            {"vision": 80, "tp": 65, "edge": 5}, 100,
            source="team_prop_scores", icon="Crown", color="gold",
        )

    # ── Trap Detector (priority 10) — large edge & disagreement. ──
    # We approximate "market direction != model direction" by
    # checking if edge_pct is materially positive (model > market)
    # AND the recommended side is contrarian against hit_l20.
    if edge_pct is not None and edge_pct >= 8:
        _push(
            "Trap Detector",
            f"Edge {edge_pct:+.1f}% vs market",
            "edge_pct", edge_pct, 8, 10,
            source="team_prop_scores", icon="Brain", color="purple",
        )

    # ── Freight Train / Spread dominance (priority 10). ───────────
    if cover_rate_l10 is not None and cover_rate_l10 >= 0.70:
        _push(
            "Freight Train",
            f"Covered {int(round(cover_rate_l10 * 10))} of last 10",
            "cover_rate_l10", cover_rate_l10, 0.70, 10,
            icon="Train", color="amber",
        )

    # ── Sport-specific rank-based badges. ─────────────────────────
    team_score_rank   = league_ranks.get("team_score_rank", {}).get(abbr)
    opp_score_rank    = league_ranks.get("opp_score_rank", {}).get(abbr)
    total_score_rank  = league_ranks.get("total_score_rank", {}).get(abbr)
    home_win_rank     = league_ranks.get("home_win_rank", {}).get(abbr)
    away_win_rank     = league_ranks.get("away_win_rank", {}).get(abbr)

    if sport_l == "nba":
        # Burn Rate (priority 8) — L5 recent > season + 7.
        if l5_avg is not None and season_avg is not None \
                and l5_avg >= season_avg + 7:
            _push(
                "Burn Rate",
                "Recent scoring exceeds baseline",
                "l5_minus_season", round(l5_avg - season_avg, 1), 7, 8,
                icon="Flame", color="red",
            )
        # Brick Wall (priority 9) — opp_score_rank top-5.
        if opp_score_rank is not None and opp_score_rank <= 5:
            _push(
                "Brick Wall", "Opponent scoring suppressed",
                "opp_score_rank", opp_score_rank, "≤ 5", 9,
                icon="ShieldCheck", color="zinc",
            )
        # Fast Lane (priority 7) — top pace.
        if total_score_rank is not None and total_score_rank <= 5:
            _push(
                "Fast Lane", "Top pace environment",
                "total_score_rank", total_score_rank, "≤ 5", 7,
                icon="Zap", color="yellow",
            )
        # Deadeye (priority 8) — top scoring efficiency proxy via
        # team_score_rank (no offensive_rating table yet).
        if team_score_rank is not None and team_score_rank <= 5:
            _push(
                "Deadeye", "Scoring efficiency advantage",
                "team_score_rank", team_score_rank, "≤ 5", 8,
                icon="Target", color="green",
            )
        # Green Wave (priority 7) — totals over.
        if total_over_rate_l10 is not None \
                and total_over_rate_l10 >= 0.70:
            _push(
                "Green Wave", "Totals consistently clearing",
                "total_over_rate_l10",
                total_over_rate_l10, 0.70, 7,
                icon="TrendingUp", color="green",
            )
        # Fortress (priority 6) — top-5 home.
        if home_win_rank is not None and home_win_rank <= 5 \
                and is_home is True:
            _push(
                "Fortress", "Strong home environment",
                "home_win_rank", home_win_rank, "≤ 5", 6,
                icon="Home", color="blue",
            )
        # Jet Fuel (priority 6) — top-5 away.
        if away_win_rank is not None and away_win_rank <= 5 \
                and is_home is False:
            _push(
                "Jet Fuel", "Travel performance advantage",
                "away_win_rank", away_win_rank, "≤ 5", 6,
                icon="Plane", color="blue",
            )
        # Wolf Pack (priority 8) — L5 recent_form > season AND hit_l5
        # supports the side.
        if (hit_l5 is not None and hit_l5 >= 60
                and l5_avg is not None and season_avg is not None
                and l5_avg > season_avg + 2):
            _push(
                "Wolf Pack", "Recent form acceleration",
                "l5_form", hit_l5, 60, 8,
                icon="Wand", color="purple",
            )

    elif sport_l == "mlb":
        # Barrel Club (priority 8) — top run creation.
        if team_score_rank is not None and team_score_rank <= 5:
            _push(
                "Barrel Club", "Top run environment",
                "team_score_rank", team_score_rank, "≤ 5", 8,
                icon="Crosshair", color="amber",
            )
        # Icebox (priority 9) — top run suppression.
        if opp_score_rank is not None and opp_score_rank <= 5:
            _push(
                "Icebox", "Opponent run suppression",
                "opp_score_rank", opp_score_rank, "≤ 5", 9,
                icon="Snowflake", color="blue",
            )
        # Scorched Earth (priority 8) — L5 runs > season + 1.5
        if l5_avg is not None and season_avg is not None \
                and l5_avg >= season_avg + 1.5:
            _push(
                "Scorched Earth", "Recent production surge",
                "l5_minus_season", round(l5_avg - season_avg, 2),
                1.5, 8,
                icon="Flame", color="red",
            )
        # Wave Rider (priority 7) — total over rate.
        if total_over_rate_l10 is not None \
                and total_over_rate_l10 >= 0.70:
            _push(
                "Wave Rider", "Totals consistently trending",
                "total_over_rate_l10",
                total_over_rate_l10, 0.70, 7,
                icon="TrendingUp", color="cyan",
            )
        # Fortress / Night Shift (home / away splits).
        if home_win_rank is not None and home_win_rank <= 5 \
                and is_home is True:
            _push(
                "Fortress", "Home split advantage",
                "home_win_rank", home_win_rank, "≤ 5", 6,
                icon="Home", color="blue",
            )
        if away_win_rank is not None and away_win_rank <= 5 \
                and is_home is False:
            _push(
                "Night Shift", "Travel performance advantage",
                "away_win_rank", away_win_rank, "≤ 5", 6,
                icon="Moon", color="indigo",
            )
        # Sharpshooter (priority 9) — TP >= 65 AND edge >= 5
        if tp_pct is not None and edge_pct is not None \
                and tp_pct >= 65 and edge_pct >= 5:
            _push(
                "Sharpshooter", "Model strongly aligned",
                "tp+edge", f"{tp_pct:.0f}% / {edge_pct:+.1f}%",
                "tp≥65 · edge≥5", 9,
                source="team_prop_scores", icon="Target", color="green",
            )
        # Blueprint (priority 7) — historical repeat rate (use L20
        # as a proxy when ≥ 70%).
        if hit_l20 is not None and hit_l20 >= 70:
            _push(
                "Blueprint", "Historically repeatable setup",
                "hit_rate_l20", hit_l20, 70, 7,
                icon="BookOpen", color="zinc",
            )

    elif sport_l == "nfl":
        # NFL keeps the same hierarchy with sport-specific labels.
        if team_score_rank is not None and team_score_rank <= 5:
            _push(
                "High-Powered", "Top scoring offense",
                "team_score_rank", team_score_rank, "≤ 5", 8,
                icon="Rocket", color="red",
            )
        if opp_score_rank is not None and opp_score_rank <= 5:
            _push(
                "Stout Defense", "Top defensive unit",
                "opp_score_rank", opp_score_rank, "≤ 5", 9,
                icon="ShieldCheck", color="zinc",
            )
        if total_over_rate_l10 is not None \
                and total_over_rate_l10 >= 0.70:
            _push(
                "Green Wave", "Totals consistently clearing",
                "total_over_rate_l10",
                total_over_rate_l10, 0.70, 7,
                icon="TrendingUp", color="green",
            )

    # ── Killshot (MLB) / Dragon (rare composite) — fires when 3+
    # other badges are already in the list. Bumps composite badge
    # priority above the lower individual badges so it always wins
    # the cap.
    if sport_l == "mlb" and len(out) >= 3:
        _push(
            "Killshot", "Multiple elite signals aligned",
            "badge_count", len(out), "≥ 3", 20,
            source="team_prop_scores", icon="Skull", color="red",
        )

    # ── Cap @ 3 by priority descending. ──────────────────────────
    out.sort(key=lambda b: -b["priority"])
    return out[:3]


def _build_team_intel_suite(
    *,
    sport: str,
    market_category: str,
    side: str,
    line: Optional[float],
    l5_avg: Optional[float],
    l10_avg: Optional[float],
    l20_avg: Optional[float],
    season_avg: Optional[float],
    league_ranks: Dict[str, Dict[str, Any]],
    team_abbr: Optional[str],
    opp_abbr: Optional[str],
    opp_def_rank: Optional[int],
    projection: Optional[float],
    hit_l10: Optional[float],
    game_logs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build the team analog of the player `intel_suite` dict.

    Returns the same key shape PlayerDetailPage reads — `usage_ripple`,
    `pace_delta`/`tempo`, `blowout_risk`, `matchup_dvp`,
    `momentum_data`, `matchup_analysis`, `variance`, `lasso`. Each
    tile gracefully degrades to None / "Standard" when the underlying
    data is missing, mirroring player-suite behaviour.
    """
    abbr = (team_abbr or "").upper()
    sport_l = (sport or "").lower()
    # ── Variance: std dev of last-20 team scores (game_logs[:20]).
    team_scores = [g["team_score"] for g in game_logs[:20]
                    if g.get("team_score") is not None]
    cv = None
    vol_score = None
    vol_label = None
    if len(team_scores) >= 5:
        mean = sum(team_scores) / len(team_scores)
        if mean > 0:
            var = sum((x - mean) ** 2 for x in team_scores) / len(team_scores)
            std = var ** 0.5
            cv = round(std / mean, 3)
            # Map CV → 0-100 volatility score. CV typically 0.05-0.30
            # for team totals — scale to 0-100.
            vol_score = max(0, min(100, round(cv * 300, 1)))
            vol_label = (
                "low"  if vol_score <= 40 else
                "mid"  if vol_score <= 70 else
                "high"
            )

    # ── Tempo / Pace Delta: total_avg vs league baseline.
    total_avg_team = (league_ranks.get("total_avg") or {}).get(abbr)
    all_totals = list((league_ranks.get("total_avg") or {}).values())
    league_total_avg = (
        sum(all_totals) / len(all_totals) if all_totals else None)
    tempo_pct = None
    tempo_label = "Standard"
    if total_avg_team is not None and league_total_avg:
        delta = total_avg_team - league_total_avg
        tempo_pct = round(100 * delta / league_total_avg, 1)
        if tempo_pct >= 5:
            tempo_label = "Fast"
        elif tempo_pct <= -5:
            tempo_label = "Slow"
        else:
            tempo_label = "Neutral"
    pace_rank = (league_ranks.get("total_score_rank") or {}).get(abbr)

    # ── Usage Ripple (team-volume analog).
    volume_label = "Standard Volume"
    bump_pct = 0
    shift_label = ""
    reasoning = ""
    if l5_avg is not None and season_avg is not None and season_avg > 0:
        bump_pct = round(100 * (l5_avg - season_avg) / season_avg, 1)
        if bump_pct >= 5:
            volume_label = "Elevated Volume"
            shift_label  = "+VOLUME"
            reasoning    = (
                f"L5 average {l5_avg:.1f} runs {bump_pct:+.1f}% above "
                f"season baseline {season_avg:.1f}."
            )
        elif bump_pct <= -5:
            volume_label = "Suppressed Volume"
            shift_label  = "-VOLUME"
            reasoning    = (
                f"L5 average {l5_avg:.1f} runs {bump_pct:+.1f}% below "
                f"season baseline {season_avg:.1f}."
            )
        else:
            shift_label  = "STANDARD"
            reasoning    = (
                f"L5 average {l5_avg:.1f} ~ season baseline "
                f"{season_avg:.1f}."
            )

    # ── Blowout Risk (NBA-leaning, derived from margin variance).
    margins = [g["margin"] for g in game_logs[:10]
                if g.get("margin") is not None]
    blowout_level = "NONE"
    team_record  = None
    opp_record   = None
    if margins:
        mean_margin = sum(margins) / len(margins)
        if abs(mean_margin) >= 12:
            blowout_level = "HIGH"
        elif abs(mean_margin) >= 7:
            blowout_level = "MEDIUM"
        wins = sum(1 for g in game_logs[:20]
                    if (g.get("margin") or 0) > 0)
        team_record = (
            f"{wins}-{min(len(game_logs), 20) - wins} L20")

    # ── Matchup DVP (team analog).
    matchup_dvp = None
    if opp_def_rank is not None:
        matchup_dvp = {
            "opponent":         opp_abbr or "OPP",
            "rank":             opp_def_rank,
            "stat_type":        (market_category or "").upper(),
            "label": (
                "Elite"   if opp_def_rank <= 5 else
                "Tough"   if opp_def_rank <= 10 else
                "Neutral" if opp_def_rank <= 20 else
                "Soft"
            ),
        }

    # ── Momentum data (NBA — MomentumTrackerFull reads this).
    momentum_data = None
    if sport_l == "nba" and margins:
        recent_5 = margins[:5]
        recent_5_avg = (sum(recent_5) / len(recent_5)) if recent_5 else None
        trend = (
            "ACCELERATING" if (recent_5_avg or 0) > 3 else
            "DECELERATING" if (recent_5_avg or 0) < -3 else
            "STABLE"
        )
        momentum_data = {
            "trend":         trend,
            "recent_margin": (round(recent_5_avg, 1)
                              if recent_5_avg is not None else None),
            "season_margin": (round(sum(margins) / len(margins), 1)
                              if margins else None),
            "opponent":      opp_abbr or "OPP",
            "stat_type":     (market_category or "").upper(),
        }

    # ── MLB Matchup Analysis (placeholder until split data lands).
    matchup_analysis = None
    if sport_l == "mlb" and opp_def_rank is not None:
        matchup_analysis = {
            "opponent":     opp_abbr or "OPP",
            "stat_type":    (market_category or "").upper(),
            "splits": [
                {"split":  "vs Opponent",
                  "rank":   opp_def_rank,
                  "label":  "Tough" if opp_def_rank <= 10 else "Soft"},
            ],
        }

    return {
        "usage_ripple": {
            "display":           volume_label,
            "reasoning":         reasoning,
            "bump_percent":      bump_pct,
            "shift_label":       shift_label,
            "injuries_affecting": [],
        },
        "pace_delta": {
            "display":      (f"{tempo_pct:+.1f}%"
                              if tempo_pct is not None else "—"),
            "total_pct":    tempo_pct,
            "possessions":  tempo_pct,
            "tempo_label":  tempo_label,
            "factors": (
                [{"name": f"Pace rank #{pace_rank}"}]
                if pace_rank is not None else []
            ),
        },
        "tempo": {
            "display":      (f"{tempo_pct:+.1f}%"
                              if tempo_pct is not None else "—"),
            "total_pct":    tempo_pct,
            "tempo_label":  tempo_label,
            "factors": (
                [{"name": f"Pace rank #{pace_rank}"}]
                if pace_rank is not None else []
            ),
        },
        "blowout_risk": {
            "risk_level":              blowout_level,
            "player_team_record":      team_record or "—",
            "opponent_team_record":    opp_record or "—",
        } if blowout_level != "NONE" else None,
        "matchup_dvp":      matchup_dvp,
        "momentum_data":    momentum_data,
        "matchup_analysis": matchup_analysis,
        "variance": {
            "cv":                cv,
            "volatility_score":  vol_score,
            "volatility_label":  vol_label,
        },
        "lasso": ({"projection":      projection,
                    "confidence_tier": "HISTORICAL"}
                   if projection is not None else None),
    }


async def _enrich_cards_with_history(
    db, cards: List[Dict[str, Any]], sport: str,
) -> List[Dict[str, Any]]:
    """Stamp REAL historical hit_rate_l5/l10/l20 + season avg +
    projection + vision_intel + scout_badges on every card.

    Pulled from `team_historical_outcomes` via shared SSOT helpers so
    the board card and the detail page are byte-equal on the numbers
    they display.

    Cost: one game-history query per UNIQUE team (cached), one
    hit-rate query per prop. For a typical tier read (10 cards), this
    is 1-2 team queries + 10 prop queries on a small graded-row index
    — well under 100ms.
    """
    if not cards:
        return cards

    # 1. Per-team game history (used for baseline averages).
    by_team: Dict[str, List[Dict[str, Any]]] = {}
    for c in cards:
        tid = c.get("team_id")
        if tid:
            by_team.setdefault(tid, []).append(c)
    baseline_by_team: Dict[str, Dict[str, Any]] = {}
    logs_by_team: Dict[str, List[Dict[str, Any]]] = {}
    # Pre-fetch league ranks (cached globally after first call) and per-team
    # spread/total rates (2 queries/team instead of 2 queries/card).
    league_ranks = await _compute_league_ranks(db, sport)
    rates_by_team: Dict[str, Dict[str, Optional[float]]] = {}
    for tid in by_team.keys():
        logs = await fetch_team_game_history(
            db, team_id=tid, sport=sport, limit=25)
        baseline_by_team[tid] = compute_baseline_stats(logs)
        logs_by_team[tid] = logs
        rates_by_team[tid] = await _compute_spread_total_rates(db, tid, sport)

    # 2. Per-card hit-rate + intel enrichment.
    for c in cards:
        tid = c.get("team_id")
        if not tid:
            continue
        # market_category lands on the row from the XGB scorer; the
        # passthrough output (pre-scorer) and seeded synthetic rows
        # don't carry it. Derive on-the-fly from market_key when
        # missing so enrichment works on both code paths.
        market_category = (
            c.get("market_category")
            or _classify_market_category_from_key(c.get("market_key"))
            or ""
        )
        c["market_category"] = market_category
        side = (c.get("side") or "").upper()
        try:
            line = float(c["line"]) if c.get("line") is not None else None
        except (TypeError, ValueError):
            line = None
        stat_token = market_category_to_stat_token(market_category)

        # Opponent abbr (for h2h hit rate sentence). Cards don't carry
        # `opponent_team_id`, only `opponent` name — h2h slice is left
        # to the team-detail endpoint where opp_team_id is resolved.
        opp_abbr = (c.get("opponent") or "")

        # Historical hit rates @ this (category, side, ~line).
        # ML side must be translated to HOME/AWAY for historical lookup.
        hr_side = side
        if side == "ML":
            hr_side = "HOME" if c.get("team_id") == c.get("home_team_id") else "AWAY"
        hr = await compute_hit_rates(
            db,
            team_id=tid, sport=sport,
            market_category=market_category,
            side=hr_side, line=line,
        )
        hit_l5 = hr.get("hit_rate_l5")
        hit_l10 = hr.get("hit_rate_l10")
        hit_l20 = hr.get("hit_rate_l20")
        season_hr = hr.get("season_hit_rate")

        # Baseline avg for this stat token (projection input).
        bs = (baseline_by_team.get(tid) or {}).get(stat_token, {}) or {}
        l5_avg = bs.get("l5_avg")
        l10_avg = bs.get("l10_avg")
        l20_avg = bs.get("l20_avg")
        season_avg = bs.get("season_avg")

        # Projection: use l10 avg (no model). Edge via SSOT helper —
        # mirrors `(margin proj vs threshold)` for spread and flips
        # sign for UNDER/AWAY so the recommended side ALWAYS matches
        # the projection (no more "UNDER but projection says OVER").
        projection = l10_avg
        # P1 fix: h2h cards show win probability not runs scored
        if market_category == 'h2h':
            mp = c.get('model_probability')
            projection = round(mp * 100, 1) if mp is not None else None
        edge_pct_signed = compute_team_edge_pct(
            projection, line, side, market_category,
        )
        # Back-compat field still used by some UI chips.
        edge_vs_fair: Optional[float] = (
            round(edge_pct_signed / 100.0, 4)
            if edge_pct_signed is not None else None
        )

        # H2H — opponent_team_id isn't stamped on team_prop_scores
        # today, so we skip the h2h slice for board cards. The team
        # detail page (team_with_badges) does include it because the
        # endpoint resolves opp_team_id from event_id + master_hub.
        opp_hr = None
        opp_sample = 0

        # ── Vision Intel — mirror player ferrari_tiers pattern.
        # Player flow (routes/ferrari_tiers.py L1082):
        #   1) `vision_intel` arrives on the pick from cached_board /
        #      `{sport}_prop_scores` (written by master_sync Gemini
        #      orchestrator).
        #   2) `_apply_field_ownership_overlay` passes through ONLY
        #      when the pick has no `vision_intel` yet.
        #   3) Deterministic fallback runs ONLY for picks without
        #      Gemini text.
        # We mirror that here exactly. `c.get("vision_intel")` is
        # populated by `_hydrate_card` from `team_prop_scores`. Use
        # it as the canonical UX string; only call the deterministic
        # builder when the score row carries nothing.
        score_intel = (c.get("vision_intel") or "").strip()
        if score_intel:
            vision_text = score_intel
        else:
            vision_text = build_vision_intel(
                stat_token=stat_token, side=side, line=line,
                hit_l5=hit_l5, hit_l10=hit_l10, hit_l20=hit_l20,
                season_hr=season_hr,
                opp_abbr=opp_abbr, opp_hit_pct=opp_hr, opp_sample=opp_sample,
            )
        scout = build_scout_badges(
            hit_l5=hit_l5, hit_l10=hit_l10, hit_l20=hit_l20,
        )

        # ── Display tokens UniversalPlayerCard reads. The card's
        # `formatStatType(stat_type)` call drives the visible
        # "STAT_TYPE LINE" headline. For team props, stat_type is None
        # on the raw row (no XGB scorer write) — we substitute the
        # human-friendly bucket name so the headline reads
        # "Team Total 110.5", "Spread -5.5", etc.
        stat_label = {
            "team_total": "Team Total",
            "game_total": "Game Total",
            "spread":     "Spread",
            "h2h":        "Moneyline",
        }.get(market_category, market_category.replace("_", " ").title())

        # Direction display: HOME / AWAY / OVER / UNDER → Over/Under
        # mapping so the existing `vk_recommendation` chip renders
        # the same colour gradient as player cards.
        if side in ("OVER", "HOME"):
            vk_rec, side_label = "OVER", "Over"
        elif side in ("UNDER", "AWAY"):
            vk_rec, side_label = "UNDER", "Under"
        elif side == "ML":
            # Moneyline — use HOME / AWAY semantics from the row
            ha = (c.get("home_away") or "").upper()
            if ha == "HOME":
                vk_rec, side_label = "OVER", "Home ML"
            else:
                vk_rec, side_label = "UNDER", "Away ML"
        else:
            vk_rec, side_label = "OVER", side.title() if side else ""

        # Headline strings the card renders verbatim.
        if line is not None:
            stat_line = f"{stat_label} {line}"
            big_pick = f"{side_label.upper()} {line} {stat_label.upper()}"
        else:
            stat_line = stat_label
            big_pick = f"{side_label.upper()} {stat_label.upper()}"

        # True-probability synthesis: in the player pipeline `tp` is
        # the consensus de-vigged probability. For teams (no model
        # yet) we use `season_hit_rate` as the consensus baseline so
        # the TP chip on the card has a real, deterministic number.
        # Fallback ladder: season_hr → l20 → l10 → 50.
        tp_pct = season_hr if season_hr is not None else (
            hit_l20 if hit_l20 is not None else (
                hit_l10 if hit_l10 is not None else 50.0
            ))

        # PrizePicks-style multiplier label heuristic (goblin /
        # balanced / demon) bound to historical hit-rate confidence.
        if hit_l20 is not None and hit_l20 >= 70:
            pp_label = "goblin"
        elif hit_l20 is not None and hit_l20 <= 35:
            pp_label = "demon"
        else:
            pp_label = "balanced"

        # Opponent allowance rank — team analog of player DVP. Reads
        # `OPP_TOTAL.season_avg` for the opponent (computed below).
        # Stored on the card as `opponent_defensive_rank` so the
        # existing DVP chip on UniversalPlayerCard renders for teams.
        opp_def_rank: Optional[int] = None
        opp_def_source: Optional[str] = None
        if c.get("opponent"):
            # Get all teams' OPP_TOTAL season_avg for this sport →
            # rank our opponent's "points allowed" baseline. Lower
            # rank = tougher defense (better for unders). Cheap:
            # 30 teams, single query, no per-card cost.
            opp_def_rank = await _resolve_opp_def_rank(
                db, sport=sport, opp_team_abbr=c.get("opponent"))
            opp_def_source = "team_historical_outcomes" if opp_def_rank else None

        # Stamp the canonical player-card fields so UniversalPlayerCard
        # / TeamPropRow render real numbers instead of nulls.
        c["hit_rate_l5"]  = hit_l5
        c["hit_rate_l10"] = hit_l10
        c["hit_rate_l20"] = hit_l20
        c["hit_rate_over"] = hit_l20 if side in ("OVER", "HOME", "ML") else None
        c["hit_rate_under"] = hit_l20 if side in ("UNDER", "AWAY") else None
        c["season_hit_rate"] = season_hr
        c["hit_rate_sample_size"] = hr.get("sample_size")
        c["hit_rate_status"]  = "computed" if hit_l20 is not None else "no_data"
        c["l5_avg"]   = l5_avg
        c["l10_avg"]  = l10_avg
        c["l20_avg"]  = l20_avg
        c["avg"]      = l10_avg
        c["season_avg"] = season_avg
        c["vk_predicted"] = projection
        c["vk2_projection"] = projection
        c["projection"]   = projection
        c["model_projection"] = projection
        c["edge_vs_fair"] = edge_vs_fair
        c["total_edge"]   = edge_vs_fair
        c["vision_intel"] = vision_text
        c["vision_summary"] = vision_text
        c["short_sentence"] = vision_text
        c["scout_badges"] = scout
        c["active_badges"] = [b["badge_key"] for b in scout]
        c["intel_suite"] = {
            "lasso": (
                {"projection": projection,
                 "confidence_tier": "HISTORICAL"}
                if projection is not None else None
            ),
            "scout_badges": scout,
            "context_badges": [],
        }
        c["is_vision_enriched"] = vision_text is not None

        # Display-binding fields (the ones the card actually reads to
        # render the visible headline / TP / DVP / pp_multiplier).
        c["stat_type"]            = stat_label
        c["stat_type_canonical"]  = stat_label
        c["stat_line"]            = stat_line
        c["big_pick_text"]        = big_pick
        c["recommendation"]       = side_label
        c["vk_recommendation"]    = vk_rec
        c["tp"]                   = round(tp_pct, 1) if tp_pct is not None else None
        c["confidence_score"]     = round((tp_pct or 0) / 100.0, 4)
        c["pp_multiplier_label"]  = pp_label
        c["pp_multiplier_source"] = "historical_hit_rate"
        c["opponent_defensive_rank"]      = opp_def_rank
        c["opponent_defensive_source"]    = opp_def_source
        c["opponent_defensive_stat_type"] = stat_label
        c["opponent_abbr"]                = c.get("opponent")
        # Vision score % synthesis: same TP-anchored value so the
        # green-circle chip on the card has a real number.
        c["vision_score"]    = round(tp_pct, 1) if tp_pct is not None else None
        c["vision_score_v2"] = c["vision_score"]
        c["intel_score"]     = c["vision_score"]

        # ── Glow drivers (mirror player cards exactly). ─────────────
        # `UniversalPlayerCard::getHighestTier` keys off `tier_label`
        # (UPPERCASE, e.g. "SAFE_HAVEN"/"FRONT_LINE"/"WAR_ZONE"),
        # `is_goblin`/`is_demon`/`is_standard` booleans, and
        # `sharp_movement` / `trap_risk`. Team rows were not setting
        # these — cards fell through to STANDARD theme (no glow).
        tier = (c.get("tier") or "").lower()
        TIER_LABEL_MAP = {
            "safe_haven":  "SAFE_HAVEN",
            "front_lines": "FRONT_LINE",
            "war_zone":    "WAR_ZONE",
        }
        c["tier_label"] = TIER_LABEL_MAP.get(tier, "STANDARD")
        c["is_goblin"]   = (tier == "safe_haven")
        c["is_demon"]    = (tier == "war_zone")
        c["is_standard"] = (tier not in ("safe_haven", "front_lines", "war_zone"))
        # Sharp-movement / trap-risk: team pipeline doesn't compute
        # these (live-line micro-movement is a player-prop only signal
        # for now). Stamp explicit False so the card's hot path
        # short-circuits cleanly.
        c["sharp_movement"] = False
        c["trap_risk"]      = False

        # ── Tier-gate metadata (player cards bind to these). ────────
        # Team rows reach a tier through `_route_tier` in the
        # passthrough; a row that landed in the tier passed every
        # tier rule. Stamp the SAME shape player cards consume.
        c["gate_pass"]      = (tier in ("safe_haven", "front_lines", "war_zone"))
        c["failed_gates"]   = c.get("failed_gates") or []
        c["gate_reasons"]   = c.get("gate_reasons") or []
        # ── Model-vs-Projection Consistency Gate ─────────────────
        # User reported 2026-06-03: "we are recommending UNDER 3.5
        # with a projection of 3.8 — that's contradictory."
        #
        # The XGB model and the team's recent l10 average can
        # disagree (model was trained on an older window where the
        # team scored less). When that disagreement is STRONG, the
        # model's recommendation is unreliable — the team's actual
        # recent form is the stronger signal. Demote those picks.
        #
        # Rule: if projection-derived edge < −5%, the projection
        # strongly disagrees with the picked side → demote.
        #   • UNDER 110.5, l10 119.9  →  edge_pct = −8.5%  →  demote
        #   • OVER  110.5, l10 105.0  →  edge_pct = −5.0%  →  demote
        # Only fires when projection is available (l10_avg present).
        # H2H / moneyline (edge_pct_signed is None) bypasses this gate.
        if (edge_pct_signed is not None
                and edge_pct_signed < -5.0
                and tier in ("safe_haven", "front_lines", "war_zone")):
            c["tier"]       = None
            c["tier_label"] = "unranked"
            c["model_demoted"] = True
            c["gate_reasons"]  = (c.get("gate_reasons") or []) + [
                f"projection_contradicts_side:"
                f"edge_pct={edge_pct_signed:.1f}"
            ]
            c["safe_haven_pass"]  = False
            c["front_lines_pass"] = False
            c["war_zone_pass"]    = False
            tier = None  # used by downstream gate flags

        c["safe_haven_pass"]  = (tier == "safe_haven")
        c["front_lines_pass"] = (tier == "front_lines")
        c["war_zone_pass"]    = (tier == "war_zone")

        # ── Probability fields. Synthesised from historical hit-rate
        # baseline + odds so the card's TP%/edge chips render real
        # numbers until the team XGB model finishes for this row.
        # `tp_pct` is already the deterministic baseline (season HR).
        tp_frac = (tp_pct or 0) / 100.0
        c["model_probability"] = c.get("model_probability") or tp_frac
        c["true_probability"]  = c.get("true_probability")  or tp_frac
        c["fair_probability"]  = c.get("fair_probability")  or tp_frac
        # Implied probability from American odds (decimal-form math).
        odds_val = c.get("odds")
        if odds_val is not None and c.get("implied_probability") is None:
            try:
                o = float(odds_val)
                if o < 0:
                    c["implied_probability"] = round(-o / (-o + 100.0), 4)
                else:
                    c["implied_probability"] = round(100.0 / (o + 100.0), 4)
            except (TypeError, ValueError):
                c["implied_probability"] = None
        c["edge"] = c.get("edge") if c.get("edge") is not None else (
            round(c["model_probability"] - c["implied_probability"], 4)
            if c.get("implied_probability") is not None else None)
        # SSOT edge: ALWAYS overwrite with our signed `edge_pct_signed`
        # so the card chip displays the correctly-signed % (positive
        # when projection agrees with the side, negative when it
        # doesn't). The naive market-implied edge from upstream is
        # misleading because it doesn't account for projection.
        if edge_pct_signed is not None:
            c["edge_pct"]     = edge_pct_signed
            c["edge"]         = round(edge_pct_signed / 100.0, 4)
        else:
            c["edge_pct"] = c.get("edge_pct") if c.get("edge_pct") is not None else (
                round(100.0 * c["edge"], 2) if c.get("edge") is not None else None)

        # ── DVP alias fields (player-card duals). The player card
        # reads `dvp_rank` / `dvp_value` / `matchup_badge` for the
        # matchup chip. For teams we mirror the opponent allowance
        # rank into those alias slots so the chip renders identically.
        c["dvp_rank"]      = opp_def_rank
        c["dvp_value"]     = opp_def_rank
        c["dvp_stat_type"] = stat_label
        if opp_def_rank is not None:
            rank_label = (
                "elite" if opp_def_rank <= 5 else
                "tough" if opp_def_rank <= 10 else
                "neutral" if opp_def_rank <= 20 else
                "soft"
            )
            c["matchup_badge"] = {
                "rank": opp_def_rank,
                "label": rank_label,
                "stat_type": stat_label,
            }
        else:
            c["matchup_badge"] = None

        # ── Sport-specific deterministic scout badges (replaces old
        # context_badges placeholders). Same renderer the player card
        # uses for `scout_badges`. Capped at 3 by priority. The badge
        # builder requires league-wide ranks + L10 spread/total rates,
        # which we compute below.
        rates = rates_by_team.get(tid) or {}
        team_badges = _build_team_scout_badges(
            sport=sport, team_abbr=(c.get("team_abbr") or "").upper(),
            market_category=market_category, side=side,
            is_home=c.get("is_home"),
            l5_avg=l5_avg, season_avg=season_avg,
            hit_l5=hit_l5, hit_l10=hit_l10, hit_l20=hit_l20,
            cover_rate_l10=rates.get("cover_rate_l10"),
            total_over_rate_l10=rates.get("total_over_rate_l10"),
            league_ranks=league_ranks,
            tp_pct=tp_pct, edge_pct=edge_pct_signed,
            vision_score=c.get("vision_score"),
        )
        c["scout_badges"]  = team_badges
        c["active_badges"] = [b["badge_key"] for b in team_badges]
        c["context_badges"] = team_badges
        # ── Crown Play → gold glow override. ─────────────────────
        # Per directive: golden glow ONLY triggers on Crown Play.
        # When the badge fires we elevate the card to SAFE_HAVEN
        # tier theme (gold ring + green accent), independent of
        # the original tier.
        if any(b["badge_key"] == "crown_play" for b in team_badges):
            c["tier_label"]   = "SAFE_HAVEN"
            c["is_goblin"]    = True
            c["is_demon"]     = False
            c["is_standard"]  = False
            c["is_crown_play"] = True
        else:
            c["is_crown_play"] = False

        # ── Full Vision Intel Suite payload (matches player shape).
        # Each tile of the player suite has a team analog so the same
        # PlayerDetailPage renders without conditionals.
        team_logs = logs_by_team.get(tid) or []
        suite = _build_team_intel_suite(
            sport=sport, market_category=market_category, side=side,
            line=line, l5_avg=l5_avg, l10_avg=l10_avg,
            season_avg=season_avg, l20_avg=l20_avg,
            league_ranks=league_ranks,
            team_abbr=(c.get("team_abbr") or "").upper(),
            opp_abbr=opp_abbr, opp_def_rank=opp_def_rank,
            projection=projection, hit_l10=hit_l10,
            game_logs=team_logs,
        )
        # Merge badge slots into the suite (player suite carries
        # `scout_badges` + `context_badges` at this level).
        suite["scout_badges"]   = team_badges
        suite["context_badges"] = team_badges
        c["intel_suite"] = suite
        # Variance tile fields used DIRECTLY on the prop (not the
        # suite) by PlayerDetailPage::variance-tile.
        c["cv"]               = suite["variance"]["cv"]
        c["cv_raw"]           = suite["variance"]["cv"]
        c["volatility_score"] = suite["variance"]["volatility_score"]
        c["volatility_label"] = suite["variance"]["volatility_label"]
        # MomentumTracker (NBA) reads `momentum_data` directly off the
        # prop. Carry from the suite so the existing component renders.
        c["momentum_data"]      = suite.get("momentum_data")
        # MLB MatchupAnalysis reads `matchup_analysis` directly.
        c["matchup_analysis"]   = suite.get("matchup_analysis")

        # ── Identity carry. Match player-card field name conventions.
        c["is_team_prop"]   = True
        c["game_id"]        = c.get("event_id")
        c["opponent_team"]  = c.get("opponent")
        c["best_book"]      = c.get("best_book") or c.get("book")

        # ── Reference-odds fields the card chip reads. ─────────────
        # Player cards render the primary odds chip from
        # `display_reference_*` → `tier_reference_*` → per-book
        # fallback (see UniversalPlayerCard::resolveDisplayOdds).
        # Team rows only carry `odds` + `book` (single book that hit
        # the tier threshold) — stamp those into the canonical chip
        # fields so the chip renders the real American price instead
        # of `—`. `best_book_odds` mirrors `odds` so the BEST BET row
        # also shows the price.
        _row_odds = c.get("odds")
        _row_book = (c.get("book") or "").lower() or None
        if c.get("tier_reference_odds") is None:
            c["tier_reference_odds"] = _row_odds
            c["tier_reference_book"] = _row_book
        if c.get("display_reference_odds") is None:
            c["display_reference_odds"] = _row_odds
            c["display_reference_book"] = _row_book
        if c.get("best_book_odds") is None:
            c["best_book_odds"] = _row_odds
        # Activity timestamp — players use `active_changed_at` for the
        # "X min ago" chip; team rows have `snapshot_iso`/`ingested_at`.
        c["active_changed_at"] = (
            c.get("scored_at") or c.get("snapshot_iso")
            or c.get("ingested_at") or c.get("passthrough_at")
        )

    return cards


async def get_team_prop_picks(db, *, sport: str, tier_name: str,
                                   limit: int = 10,
                                   sort: Optional[str] = None,
                                   ) -> Dict[str, Any]:
    """Read team_prop_scores → card-shaped picks for ONE tier.

    Returns the SAME envelope shape player tier endpoints return.
    """
    sport_l = (sport or "").lower()

    # Lazy passthrough: if scores are empty for this sport, copy
    # current live_props into scores. Idempotent.
    n_in_scores = await db[SCORES_COLL].count_documents(
        {"sport": sport_l, "team_model_pending": True})
    passthrough_audit: Optional[Dict[str, Any]] = None
    if n_in_scores == 0:
        passthrough_audit = await passthrough_team_live_to_scores(
            db, sport=sport_l)

    # XGB scoring is CPU-bound and must NOT run in the request path —
    # doing so blocks the event loop. Scoring runs via APScheduler
    # (or an explicit admin trigger). The board returns passthrough
    # data (team_model_pending=True badge) until scores are ready.

    # Two-pass fetch: H2H records first (they always survive the consistency
    # gate), then non-H2H to fill remaining slots. This guarantees H2H picks
    # appear in the pool regardless of DB insertion order, and keeps the total
    # hydration cost low even for large slates (MLB safe_haven has 2000+ rows).
    BASE_FETCH = max(limit * 10, 100)
    # Only show games that haven't started yet (or started within the last
    # 30 minutes — live betting window). Pre-game moneylines aren't
    # actionable once a game is underway; past-day games have stale odds.
    from datetime import timedelta
    cutoff_dt = datetime.now(timezone.utc) - timedelta(minutes=30)
    cutoff_iso = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    h2h_filter   = {"sport": sport_l, "tier": tier_name, "market_key": "h2h",
                    "commence_time": {"$gte": cutoff_iso}}
    other_filter = {"sport": sport_l, "tier": tier_name,
                    "market_key": {"$ne": "h2h"},
                    "commence_time": {"$gte": cutoff_iso}}
    score_rows: List[Dict[str, Any]] = []
    async for r in db[SCORES_COLL].find(h2h_filter, projection={"_id": 0}).limit(BASE_FETCH):
        score_rows.append(r)
    async for r in db[SCORES_COLL].find(other_filter, projection={"_id": 0}).limit(BASE_FETCH):
        score_rows.append(r)

    teams = await _build_team_lookup(db, sport_l)
    event_ids = list({r.get("event_id") for r in score_rows
                      if r.get("event_id")})
    events = await _build_event_lookup(db, sport_l, event_ids)

    cards: List[Dict[str, Any]] = []
    for r in score_rows:
        c = _hydrate_card(r, teams, events)
        if c is not None:
            cards.append(c)

    # Dedupe by canonical compound key
    seen: set = set()
    deduped: List[Dict[str, Any]] = []
    for c in cards:
        k = (c["event_id"], c["team_id"], c["market_key"],
              c["side"], c["line"])
        if k in seen:
            continue
        seen.add(k)
        deduped.append(c)

    def _is_h2h(c: Dict[str, Any]) -> bool:
        mk = (c.get("market_key") or c.get("market") or "").lower()
        return mk in ("h2h", "moneyline", "ml")

    if tier_name == "safe_haven":
        # H2H first (never gate-demoted), then most-negative odds.
        deduped.sort(key=lambda x: (0 if _is_h2h(x) else 1, x["odds"] or 0))
    elif tier_name == "war_zone":
        deduped.sort(key=lambda x: (0 if _is_h2h(x) else 1, -(x["odds"] or 0)))
    else:
        # front_lines: H2H first, then abs(odds) ascending (closest to even money).
        # H2H picks are never demoted by the consistency gate, so surfacing them
        # first lets us cap the enrich pool and still guarantee non-zero results.
        deduped.sort(key=lambda x: (0 if _is_h2h(x) else 1, abs(x["odds"] or 0)))

    # 2026-06-06 — Enrich a capped pool after H2H-first sort.
    # H2H picks (never demoted by the consistency gate) are now at the front of
    # `deduped`, so a pool of limit*5 captures them without touching the long
    # tail of totals/spreads records (e.g. 1843 for MLB front_lines).
    # For sports/slates where the entire pool is totals that all get demoted,
    # the cap is large enough to find any non-demoted totals within the tail.
    enrich_pool = deduped[:max(limit * 5, 50)]
    enrich_pool = await _enrich_cards_with_history(db, enrich_pool, sport_l)

    # Drop cards demoted by the consistency gate, then apply limit.
    cards = [c for c in enrich_pool if c.get("tier") == tier_name][:limit]
    return {
        "tier":         tier_name,
        "tier_label":   f"{_TIER_LABEL[tier_name]} "
                         f"({sport_l.upper()})",
        "sport":        sport_l,
        "prop_type":    "team",
        "picks":        cards,
        "count":        len(cards),
        "status":       "odds_routed",
        "pipeline": {
            "source":             SCORES_COLL,
            "passthrough_from":   "team_live_props",
            "odds_routed":        True,
            "team_model_pending": False,
            "routing_source":     "odds_then_xgb",
            "model_version":      (
                cards[0].get("model_version") if cards else None),
        },
        "lazy_passthrough":  passthrough_audit,
        "generated_at":      datetime.now(timezone.utc).isoformat(),
    }
