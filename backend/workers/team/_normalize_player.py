"""
Phase 1.A.4.acquire / Phase 4 — Player-prop normalizer.

Mirror of `_normalize.py` but flips the entity filter:
  team-prop normalizer keeps statEntityID ∈ {home, away, all}
  player-prop normalizer keeps statEntityID ∉ {home, away, all, game}

Output rows differ from team props in three ways:
  1. `player_id` (string, SGO's canonical `PLAYER_NAME_<id>_<LEAGUE>`)
     replaces `team_id`. No master-hub lookup — we trust SGO's ID.
  2. `marketName` is preserved (varies per player — useful for UI).
  3. `home_away` is unset (we don't know which side the player belongs
     to without a roster lookup, which Phase 4 explicitly defers).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Tuple

from services.team_master_hub.sgo_event_helpers import (
    derive_game_date,
    extract_event_start_iso,
)


TEAM_ENTITIES: set[str] = {"home", "away", "all", "game"}


def _norm_side(side_id: str | None) -> str | None:
    """Map SGO `sideID` to the OVER/UNDER/YES/NO/HOME/AWAY space.

    Player props use over/under far more than home/away. yes/no
    covers the firstTouchdown / anytimeTD propositions.
    """
    if not side_id:
        return None
    s = str(side_id).lower()
    if s in ("over", "under", "yes", "no",
              "home", "away", "draw"):
        return s.upper()
    return None


def _extract_odds(bm: Dict[str, Any]) -> int | None:
    """Mirror of team-prop helper. Accepts string '+150' or int -110."""
    v = bm.get("odds")
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        s = v.strip().replace("+", "")
        try:
            return int(s)
        except ValueError:
            return None
    return None


def _extract_line(bm: Dict[str, Any], bet_type: str) -> float | None:
    """For ou/sp markets SGO stores the line under `overUnder`/`spread`.
    For yn markets there is no line (we return None — schema allows it).
    """
    if bet_type in ("ou", "ou3way"):
        v = bm.get("overUnder")
    elif bet_type in ("sp", "sp3way"):
        v = bm.get("spread")
    else:
        return None
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _extract_is_alternate(bm: Dict[str, Any]) -> bool:
    """`isMainLine=True` means standard; `isAlternate=True` is the
    explicit alternate flag. Default to False (standard) on ambiguity.
    """
    if bm.get("isAlternate") is True:
        return True
    main = bm.get("isMainLine")
    if main is False:
        return True
    return False


def normalize_player_payload(
    payload: Dict[str, Any],
    *,
    sport: str,
    league: str,
    snapshot_iso: str,
    ingested_at: datetime,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Pure transform from one SGO `/v2/events` payload into player-prop
    rows. ACQUIRE-ALL semantics: no market_key filter, no stat-family
    filter. The only entity filter is `statEntityID NOT IN team_set`.

    Returns: (rows, counters)
    """
    counters = {
        "sgo_events":           0,
        "sgo_outcomes":         0,
        "rows_emitted":         0,
        "dropped_bad_side":     0,
        "dropped_no_odds":      0,
        "dropped_team_entity":  0,  # filtered out (team-level markets)
        "dropped_no_player_id": 0,
    }
    rows: List[Dict[str, Any]] = []
    seen_market_keys: set[str] = set()
    stat_families: Dict[str, int] = {}

    for ev in payload.get("events", []) or []:
        counters["sgo_events"] += 1
        event_id     = ev.get("eventID") or ev.get("event_id")
        if not event_id:
            continue
        commence_iso = extract_event_start_iso(ev)
        game_date    = derive_game_date(commence_iso)

        odds_block = ev.get("odds") or {}
        if not isinstance(odds_block, dict):
            continue

        for market_key, market in odds_block.items():
            if not isinstance(market, dict):
                continue
            seen_market_keys.add(market_key)

            entity = (market.get("statEntityID") or "").lower()
            if entity in TEAM_ENTITIES:
                # Skip team-level markets — those go to team_historical_props
                by_bm = market.get("byBookmaker") or {}
                if isinstance(by_bm, dict):
                    counters["dropped_team_entity"] += len(by_bm)
                continue

            stat_id        = market.get("statID")
            stat_entity_id = market.get("statEntityID")
            player_id      = (market.get("playerID")
                                 or market.get("statEntityID"))
            period_id      = market.get("periodID")
            bet_type_id    = market.get("betTypeID")
            side_id        = market.get("sideID")
            market_name    = market.get("marketName")

            if not player_id:
                bm_n = len((market.get("byBookmaker") or {}))
                counters["dropped_no_player_id"] += bm_n
                continue

            side = _norm_side(side_id)
            if side is None:
                bm_n = len((market.get("byBookmaker") or {}))
                counters["dropped_bad_side"] += bm_n
                counters["sgo_outcomes"]    += bm_n
                continue

            # stat-family bookkeeping (for audit metrics)
            family = market_key.split("-", 1)[0]
            stat_families[family] = stat_families.get(family, 0) + 1

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
                is_alt = _extract_is_alternate(bm_payload)

                row: Dict[str, Any] = {
                    "event_id":      event_id,
                    "player_id":     player_id,
                    "market":        market_key,
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
                    "is_alternate":  is_alt,
                    "snapshot_iso":  snapshot_iso,
                    "commence_time": commence_iso,
                    "game_date":     game_date,
                    "sport":         sport,
                    "league":        league,
                    "ingested_at":   ingested_at,
                }
                rows.append(row)
                counters["rows_emitted"] += 1

    counters["sgo_markets_seen"] = len(seen_market_keys)
    counters["stat_families"]    = stat_families
    return rows, counters


__all__ = ["TEAM_ENTITIES", "normalize_player_payload"]
