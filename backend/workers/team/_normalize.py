"""
Phase 1.A.3.5 — SGO v2 team-prop normalizer.

Walks the REAL SGO event shape:

    event.odds[<market_key>] = {
        "marketName":   "Moneyline" | "Spread" | "Total",
        "statID":       "points",
        "statEntityID": "away" | "home" | "all",
        "periodID":     "game",
        "betTypeID":    "ml" | "sp" | "ou",
        "sideID":       "away" | "home" | "over" | "under",
        "byBookmaker":  {
            "<bookmaker_key>": {
                "odds":      -110,
                "spread":    -1.5,        # SP markets
                "overUnder": 8.5,         # OU markets
                ...
            },
            ...
        },
    }

Only the 6 production-target market_keys (Phase 1.A.3.5) are emitted:
    points-away-game-ml-away
    points-home-game-ml-home
    points-away-game-sp-away
    points-home-game-sp-home
    points-all-game-ou-over
    points-all-game-ou-under

Player-level props under `event.props[]` are IGNORED (Phase 1.A scope).

Team-id resolution:
    statEntityID == "away" → event's away team name (resolver lookup)
    statEntityID == "home" → event's home team name (resolver lookup)
    statEntityID == "all"  → sentinel team_id="game" (no lookup)

`home_away` is set to "home" / "away" / None where derivable —
this populates §1.2's `home_away` column that Phase 1.A.4 schedule
backfill would otherwise own. We keep it pure: only set when SGO
gives us a definitive statEntityID.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

# Production-target market_keys. Order is stable for snapshot tests.
PRODUCTION_MARKET_KEYS: Tuple[str, ...] = (
    "points-away-game-ml-away",
    "points-home-game-ml-home",
    "points-away-game-sp-away",
    "points-home-game-sp-home",
    "points-all-game-ou-over",
    "points-all-game-ou-under",
)

# Sentinel team_id for game-level markets (statEntityID="all").
GAME_TEAM_ID = "game"

# Bookmaker payload field name to pull the line value out of.
# `betTypeID → field name`.
_LINE_FIELD_BY_BETTYPE: Dict[str, str] = {
    "ml": "",            # moneyline carries no line
    "sp": "spread",
    "ou": "overUnder",
}


def _norm_side(side_id: str) -> str | None:
    s = (side_id or "").strip().lower()
    if s in ("away", "home", "over", "under"):
        return s.upper()
    return None


def _extract_event_team_names(ev: Dict[str, Any]) -> Tuple[str | None,
                                                              str | None]:
    """Pull (home_name, away_name) from any of the common SGO shapes.

    Tolerates:
        event.teams.{home,away}.names.{long,short}
        event.teams.{home,away}.name
        event.home_team / event.away_team   (synthetic shape)
    Returns (None, None) on miss — caller will mark rows unresolved.
    """
    home: str | None = None
    away: str | None = None

    teams = ev.get("teams")
    if isinstance(teams, dict):
        for role, target in (("home", "home"), ("away", "away")):
            block = teams.get(role) or {}
            if isinstance(block, dict):
                names = block.get("names")
                if isinstance(names, dict):
                    value = (names.get("long") or names.get("short")
                              or names.get("display") or names.get("abbrev"))
                    if value:
                        if target == "home":
                            home = value
                        else:
                            away = value
                        continue
                # Fallback: bare `name` field
                if isinstance(block.get("name"), str):
                    if target == "home":
                        home = block["name"]
                    else:
                        away = block["name"]

    # Synthetic-payload fallback (kept so older tests still pass)
    if home is None and isinstance(ev.get("home_team"), str):
        home = ev["home_team"]
    if away is None and isinstance(ev.get("away_team"), str):
        away = ev["away_team"]
    return home, away


def _derive_game_date(commence_iso: str) -> str | None:
    if not commence_iso:
        return None
    try:
        return datetime.fromisoformat(
            commence_iso.replace("Z", "+00:00")
        ).astimezone(timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return None


def _extract_line(
    bm_payload: Dict[str, Any],
    bet_type_id: str,
) -> float | None:
    """Extract the line value from a per-bookmaker payload."""
    field = _LINE_FIELD_BY_BETTYPE.get((bet_type_id or "").lower(), "")
    if not field:
        return None
    raw = bm_payload.get(field)
    if raw is None:
        # Tolerate alternative field names some books use
        for alt in ("line", "points", "total"):
            if alt in bm_payload and bm_payload[alt] is not None:
                raw = bm_payload[alt]
                break
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _extract_odds(bm_payload: Dict[str, Any]) -> int | None:
    """Extract American odds. Tolerates `odds` / `americanOdds` /
    `price` field names.
    """
    for k in ("odds", "americanOdds", "price"):
        if k in bm_payload and bm_payload[k] is not None:
            try:
                return int(bm_payload[k])
            except (TypeError, ValueError):
                continue
    return None


def normalize_sgo_payload(
    payload: Dict[str, Any],
    *,
    sport: str,
    snapshot_iso: str,
    ingested_at: datetime,
    market_keys: Tuple[str, ...] | None = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Pure transform. Returns (rows, counters).

    Counters:
        sgo_events         events received
        sgo_markets_seen   distinct market_keys encountered (any type)
        sgo_outcomes       per-bookmaker quotes scanned
        rows_emitted       rows passing all in-payload guards
        dropped_bad_side   sideID not in {away,home,over,under}
        dropped_no_odds    bookmaker quote without `odds`
        dropped_unmapped   market_key not in `market_keys` filter
        dropped_no_team_name  statEntityID needs team name but event lacks it
    """
    targets = set(market_keys or PRODUCTION_MARKET_KEYS)
    counters = {
        "sgo_events":           0,
        "sgo_markets_seen":     0,
        "sgo_outcomes":         0,
        "rows_emitted":         0,
        "dropped_bad_side":     0,
        "dropped_no_odds":      0,
        "dropped_unmapped":     0,
        "dropped_no_team_name": 0,
    }
    rows: List[Dict[str, Any]] = []
    seen_market_keys: set[str] = set()

    for ev in payload.get("events", []) or []:
        counters["sgo_events"] += 1

        event_id     = ev.get("eventID") or ev.get("event_id")
        commence_iso = ev.get("startsAt") or ev.get("commence_time", "")
        game_date    = _derive_game_date(commence_iso)
        if not event_id:
            continue

        home_name, away_name = _extract_event_team_names(ev)
        odds_block = ev.get("odds") or {}
        if not isinstance(odds_block, dict):
            continue

        for market_key, market in odds_block.items():
            seen_market_keys.add(market_key)
            if not isinstance(market, dict):
                continue
            if market_key not in targets:
                # Still count the outcomes we skipped for visibility
                by_bm = market.get("byBookmaker") or {}
                if isinstance(by_bm, dict):
                    counters["dropped_unmapped"] += len(by_bm)
                    counters["sgo_outcomes"]    += len(by_bm)
                continue

            stat_id        = market.get("statID")
            stat_entity_id = market.get("statEntityID")
            period_id      = market.get("periodID")
            bet_type_id    = market.get("betTypeID")
            side_id        = market.get("sideID")
            market_name    = market.get("marketName")

            side = _norm_side(side_id)
            if side is None:
                bm_n = len((market.get("byBookmaker") or {}))
                counters["dropped_bad_side"] += bm_n
                counters["sgo_outcomes"]    += bm_n
                continue

            # ── Resolve team role + team-id input ──
            role = (stat_entity_id or "").lower()
            team_name_for_resolve: str | None = None
            team_id_direct:        str | None = None
            home_away:             str | None = None
            if role == "away":
                team_name_for_resolve = away_name
                home_away = "away"
            elif role == "home":
                team_name_for_resolve = home_name
                home_away = "home"
            elif role == "all":
                team_id_direct = GAME_TEAM_ID
                home_away = None
            else:
                # Unknown role — skip the whole market
                bm_n = len((market.get("byBookmaker") or {}))
                counters["dropped_no_team_name"] += bm_n
                counters["sgo_outcomes"]        += bm_n
                continue

            if team_id_direct is None and not team_name_for_resolve:
                bm_n = len((market.get("byBookmaker") or {}))
                counters["dropped_no_team_name"] += bm_n
                counters["sgo_outcomes"]        += bm_n
                continue

            by_bm = market.get("byBookmaker") or {}
            if not isinstance(by_bm, dict):
                continue

            for book_key, bm_payload in by_bm.items():
                counters["sgo_outcomes"] += 1
                if not isinstance(bm_payload, dict):
                    counters["dropped_no_odds"] += 1
                    continue
                odds = _extract_odds(bm_payload)
                if odds is None:
                    counters["dropped_no_odds"] += 1
                    continue
                line = _extract_line(bm_payload, bet_type_id or "")

                row: Dict[str, Any] = {
                    "event_id":      event_id,
                    "market":        market_key,   # unique-index col
                    "market_key":    market_key,
                    "market_name":   market_name,
                    "statID":        stat_id,
                    "statEntityID":  stat_entity_id,
                    "periodID":      period_id,
                    "betTypeID":     bet_type_id,
                    "sideID":        side_id,
                    "side":          side,
                    "line":          line,
                    "book":          (book_key or "").lower(),
                    "odds":          odds,
                    "is_alternate":  False,
                    "snapshot_iso":  snapshot_iso,
                    "commence_time": commence_iso,
                    "game_date":     game_date,
                    "home_away":     home_away,
                    "sport":         sport,
                    "ingested_at":   ingested_at,
                }
                if team_id_direct is not None:
                    row["team_id"] = team_id_direct
                else:
                    row["_team_name"] = team_name_for_resolve
                rows.append(row)
                counters["rows_emitted"] += 1

    counters["sgo_markets_seen"] = len(seen_market_keys)
    return rows, counters
