"""
Team Prop Tier Service — Phase 1 (odds-routed only).

Reads from `team_live_props` (preferred) and falls back to the freshest
window of `team_historical_props`. Routes rows into the same three
tiers as the player-prop board:

  safe_haven  — heavy favorites    (odds <= -250)
  front_lines — competitive prices (-245 .. -149 OR +100..+250)
  war_zone    — high-risk longshots (odds <= +500 was an example;
                                      here we accept +250..+1500)

Returns each row in the SAME envelope `_serve_ferrari_tier` returns for
player props, with these markers so the UI knows it's a stub:
    prop_type            = "team"
    odds_routed          = True
    team_model_pending   = True

Model-dependent fields are explicitly None so the card renders the
"team_model_pending" affordance instead of zeros.

NO model. NO Gemini. NO Vision. NO hit-rate. NO projection.
Just odds-routed today; the model wiring is a follow-up.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import logging

logger = logging.getLogger(__name__)

LIVE_COLL = "team_live_props"
HIST_COLL_BY_SPORT: Dict[str, str] = {
    "mlb": "team_historical_props",
    "nba": "team_historical_props",
    "nfl": "nfl_historical_props",
}
MATCHUP_COLL_BY_SPORT: Dict[str, str] = {
    "mlb": "team_matchups",
    "nba": "team_matchups",
    "nfl": "nfl_matchups",
}
TEAM_MASTER_COLL = "team_master_hub"

# Tier-routing thresholds — odds-only. Mirrors the spirit of the player
# tier breakdown (safe = chalk, war = longshot, front = middle).
SAFE_HAVEN_MAX_ODDS = -250
FRONT_LINES_NEG_MIN = -245
FRONT_LINES_POS_MAX = +250
WAR_ZONE_MIN_ODDS   = +250
WAR_ZONE_MAX_ODDS   = +1500


def _odds_to_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return None
            n = int(float(v))
        else:
            n = int(v)
    except (TypeError, ValueError):
        return None
    # Sanity bounds — anything past ±10,000 is SGO scratch / lock /
    # corrupt odds and shouldn't reach the board. Keeps the tiers
    # clean without filtering legitimate longshots.
    if n < -10000 or n > 10000:
        return None
    return n


def _route_tier(odds_us: Optional[int]) -> Optional[str]:
    """Pure odds → tier mapping. None when out-of-band."""
    if odds_us is None:
        return None
    if odds_us <= SAFE_HAVEN_MAX_ODDS:
        return "safe_haven"
    if FRONT_LINES_NEG_MIN <= odds_us <= FRONT_LINES_POS_MAX:
        return "front_lines"
    if WAR_ZONE_MIN_ODDS <= odds_us <= WAR_ZONE_MAX_ODDS:
        return "war_zone"
    return None


async def _build_team_lookup(db, sport: str) -> Dict[str, Dict[str, Any]]:
    """team_id → {abbr, full, short, market, sport, logo_url, colors}."""
    out: Dict[str, Dict[str, Any]] = {}
    async for t in db[TEAM_MASTER_COLL].find(
        {"sport": sport}, projection={"_id": 0}):
        tid = t.get("team_id")
        if not tid:
            continue
        dn   = t.get("display_names") or {}
        ext  = t.get("external_ids") or {}
        cols = t.get("colors") or {}
        # Logo URL: we do not have a canonical logo asset pipeline
        # yet (FE will choose to fall back to text abbr). Surface
        # the abbr so the card can render a clean "LAL"/"ARI" badge.
        out[tid] = {
            "team_id":     tid,
            "team_abbr":   dn.get("abbrev"),
            "team_name":   dn.get("full") or dn.get("short"),
            "team_short":  dn.get("short"),
            "team_market": dn.get("market"),
            "sport":       t.get("sport"),
            "league_id":   t.get("league_id"),
            "team_logo_url": None,   # TODO logo pipeline
            "team_colors": cols,
            "external_ids": ext,
        }
    return out


async def _build_event_lookup(
    db, sport: str, event_ids: List[str],
) -> Dict[str, Dict[str, Any]]:
    """event_id → matchup row (home/away team ids + names + game_date)."""
    if not event_ids:
        return {}
    coll = MATCHUP_COLL_BY_SPORT.get(sport, "team_matchups")
    out: Dict[str, Dict[str, Any]] = {}
    flt = {"event_id": {"$in": event_ids}}
    if sport == "nfl":
        flt["sport"] = "nfl"
    async for m in db[coll].find(flt, projection={"_id": 0}):
        out[m["event_id"]] = m
    return out


def _market_label(market_name: Any, market_key: Any,
                    stat_id: Any, period_id: Any) -> str:
    """User-facing market label. Falls back through the SGO field
    chain so we never render a raw ID like
    'points-home-game-ml-home'."""
    if isinstance(market_name, str) and market_name:
        return market_name
    if isinstance(stat_id, str) and stat_id:
        return stat_id.replace("_", " ").title()
    if isinstance(market_key, str) and market_key:
        return market_key.replace("-", " ").title()
    return "Team Prop"


async def _fetch_team_prop_rows(
    db, *, sport: str, limit_per_event: int = 200,
) -> List[Dict[str, Any]]:
    """Pull the freshest team-prop rows from live, falling back to
    the most recent historical day if live is empty.

    Hard cap defensive — never return >5,000 raw rows."""
    sport_l = sport.lower()
    rows: List[Dict[str, Any]] = []
    # Prefer live (real-time)
    try:
        cur = db[LIVE_COLL].find(
            {"sport": sport_l}, projection={"_id": 0}).limit(5000)
        rows = [r async for r in cur]
    except Exception as exc:  # noqa: BLE001
        logger.warning("team_live_props read failed: %s", exc)

    if rows:
        return rows

    # Fallback: most recent date present in historical
    hist_coll = HIST_COLL_BY_SPORT.get(sport_l, "team_historical_props")
    try:
        flt: Dict[str, Any] = {}
        if sport_l in ("mlb", "nba"):
            flt["sport"] = sport_l
        # Find the freshest game_date
        last_doc = await db[hist_coll].find_one(
            flt, sort=[("game_date", -1)],
            projection={"_id": 0, "game_date": 1})
        if not last_doc:
            return []
        freshest = last_doc.get("game_date")
        if not freshest:
            return []
        flt["game_date"] = freshest
        cur = db[hist_coll].find(flt, projection={"_id": 0}).limit(5000)
        rows = [r async for r in cur]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "%s read failed: %s", hist_coll, exc)
    return rows


def _normalize_row_to_card(
    row: Dict[str, Any], *, sport: str,
    teams: Dict[str, Dict[str, Any]],
    events: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """One team-prop row → one UniversalPlayerCard-shaped pick dict.

    Stamped with `prop_type="team"` and the pending-model flags so the
    UI never tries to read non-existent model fields.
    """
    tid = row.get("team_id")
    if not tid:
        return None
    # Reject game-level entity rows ("game", "all") — those are
    # game-totals (Over/Under) not per-team props. Same goes for
    # rows whose team_id isn't a known canonical id.
    if tid in ("game", "all", "home", "away"):
        return None
    odds = _odds_to_int(row.get("odds"))
    if odds is None:
        return None
    tier = _route_tier(odds)
    if tier is None:
        return None

    line = row.get("line")
    try:
        line = float(line) if line not in (None, "None", "") else None
    except (TypeError, ValueError):
        line = None
    side  = (row.get("side") or "").upper()
    book  = row.get("book") or ""
    if row.get("reference_only") in (True, "True"):
        # PrizePicks/Underdog — keep visible but flag to the card.
        is_ref = True
    else:
        is_ref = False

    event_id = row.get("event_id")
    matchup  = events.get(event_id) or {}
    home_id  = matchup.get("home_team_id")
    away_id  = matchup.get("away_team_id")
    home_team = teams.get(home_id) if home_id else None
    away_team = teams.get(away_id) if away_id else None
    this_team = teams.get(tid) or {}
    is_home   = (tid == home_id)
    opp_team  = away_team if is_home else home_team
    opp_name  = (opp_team or {}).get("team_short") \
                or (opp_team or {}).get("team_name") \
                or (matchup.get("away_team_name") if is_home
                    else matchup.get("home_team_name"))
    team_name = this_team.get("team_short") \
                or this_team.get("team_name") \
                or (matchup.get("home_team_name") if is_home
                    else matchup.get("away_team_name"))

    market_label = _market_label(
        row.get("market_name"), row.get("market_key"),
        row.get("statID"),      row.get("periodID"))

    # ── Build the card payload — mirrors player-card field names ──
    return {
        # Identity (the UI swaps player_name → team_name for prop_type=team)
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

        # Player-card identity fields → None on team rows so the FE
        # can branch cleanly.
        "player_id":          None,
        "player_name":        None,
        "photo_url":          None,
        "headshot_url":       None,

        # Market / line / odds — the same shape as a player row.
        "event_id":            event_id,
        "game_date":           row.get("game_date"),
        "commence_time":       row.get("commence_time"),
        "sport":               sport,
        "market":              market_label,
        "market_key":          row.get("market_key"),
        "stat_type":           row.get("statID"),
        "stat_type_extracted": row.get("statID"),
        "period_id":           row.get("periodID"),
        "line":                line,
        "side":                side,
        "odds":                odds,
        "sharp_price":         odds,
        "book":                book,
        "bookmaker":           book,
        "is_alternate":        row.get("is_alternate") in (True, "True"),
        "reference_only":      is_ref,

        # Tier + display
        "tier":                tier,
        "tier_label":          {"safe_haven":  "Safe Haven",
                                  "front_lines": "Front Lines",
                                  "war_zone":    "War Zone"}[tier],

        # Model-dependent fields — explicitly None per the directive.
        # The card MUST render its `team_model_pending` affordance
        # rather than substitute zeros / placeholders.
        "vision_score":        None,
        "intel_score":         None,
        "intel_verdict":       None,
        "intel_suite":         None,
        "vision_intel":        None,
        "confidence":          None,
        "edge":                None,
        "edge_pct":            None,
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
        "playable_on_pp":      True,   # don't filter team rows out
                                          # of the board.

        # Diagnostic markers — read by the card and any future audits.
        "odds_routed":         True,
        "team_model_pending":  True,
        "snapshot_iso":        row.get("snapshot_iso"),
        "ingested_at":         row.get("ingested_at"),
    }


async def get_team_prop_picks(
    db, *, sport: str, tier_name: str, limit: int = 10,
    sort: Optional[str] = None,
) -> Dict[str, Any]:
    """Public entry point. Returns the same envelope shape as
    `_serve_ferrari_tier` for player props.
    """
    sport_l = (sport or "").lower()
    raw_rows = await _fetch_team_prop_rows(db, sport=sport_l)
    teams = await _build_team_lookup(db, sport_l)
    event_ids = list({r.get("event_id") for r in raw_rows
                      if r.get("event_id")})
    events = await _build_event_lookup(db, sport_l, event_ids)

    cards: List[Dict[str, Any]] = []
    for r in raw_rows:
        c = _normalize_row_to_card(
            r, sport=sport_l, teams=teams, events=events)
        if c is None or c["tier"] != tier_name:
            continue
        cards.append(c)

    # Dedupe by (event_id, team_id, market_key, side, line). Sort:
    # safe = ascending odds (heaviest fav first)
    # front = sharp_price magnitude ascending
    # war = descending odds (longest shot first)
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
    else:  # front_lines
        deduped.sort(key=lambda x: abs(x["odds"] or 0))

    cards = deduped[:limit]
    return {
        "tier":        tier_name,
        "tier_label":  {"safe_haven":  "Safe Haven",
                         "front_lines": "Front Lines",
                         "war_zone":    "War Zone"}[tier_name]
                         + f" ({sport_l.upper()})",
        "sport":       sport_l,
        "prop_type":   "team",
        "picks":       cards,
        "count":       len(cards),
        "status":      "odds_routed",
        "pipeline":    {
            "source":             "team_live_props (+historical fallback)",
            "odds_routed":        True,
            "team_model_pending": True,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
