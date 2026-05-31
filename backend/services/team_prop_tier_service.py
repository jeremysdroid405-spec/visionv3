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
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import logging

from services.team_prop_passthrough import (
    SCORES_COLL,
    passthrough_team_live_to_scores,
)

logger = logging.getLogger(__name__)

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

        # Model fields — explicitly None per directive (no fakes)
        "vision_score":        None,
        "intel_score":         None,
        "intel_verdict":       None,
        "intel_suite":         None,
        "vision_intel":        None,
        "confidence":          None,
        "edge":                None,
        "edge_pct":            None,
        "true_probability":    None,
        "projection":          None,
        "cv":                  None,
        "vk_predicted":        None,
        "season_avg":          None,
        "hit_rate_l5":         None,
        "hit_rate_l10":        None,
        "hit_rate_l20":        None,
        "context_badges":      [],
        "is_vision_enriched":  False,
        "validation":          {"is_fully_validated": False,
                                 "has_mlr": False,
                                 "has_gemini": False},
        "playable_on_pp":      True,

        # Flags / lineage
        "team_model_pending":  True,
        "routing_source":      score.get("routing_source") or "odds",
        "odds_routed":         True,
        "snapshot_iso":        score.get("snapshot_iso"),
        "ingested_at":         score.get("ingested_at"),
        "passthrough_at":      score.get("passthrough_at"),
    }


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

    score_rows: List[Dict[str, Any]] = []
    cur = db[SCORES_COLL].find(
        {"sport": sport_l, "tier": tier_name},
        projection={"_id": 0}
    ).limit(5000)
    async for r in cur:
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

    if tier_name == "safe_haven":
        deduped.sort(key=lambda x: (x["odds"] or 0))
    elif tier_name == "war_zone":
        deduped.sort(key=lambda x: -(x["odds"] or 0))
    else:
        deduped.sort(key=lambda x: abs(x["odds"] or 0))

    cards = deduped[:limit]
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
            "team_model_pending": True,
            "routing_source":     "odds",
        },
        "lazy_passthrough":  passthrough_audit,
        "generated_at":      datetime.now(timezone.utc).isoformat(),
    }
