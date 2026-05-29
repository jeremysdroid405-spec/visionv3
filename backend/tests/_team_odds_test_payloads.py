"""
Shared synthetic-payload builders for Phase 1.A.3.5 onwards.

Produces payloads in the REAL SGO v2 shape:

    {
      "data": [{
        "eventID":  "...",
        "startsAt": "...",
        "teams":    {"home": {"names": {...}}, "away": {"names": {...}}},
        "odds": {
          "<market_key>": {
            "marketName":   "...",
            "statID":       "...",
            "statEntityID": "away|home|all",
            "periodID":     "game",
            "betTypeID":    "ml|sp|ou",
            "sideID":       "away|home|over|under",
            "byBookmaker":  {
              "<book>": {"odds": -110, "spread": -1.5, "overUnder": 8.5}
            }
          }
        }
      }]
    }

The provider normalizes `data` → `events` so `normalize_sgo_payload`
sees a stable shape.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable

PROD_MARKET_KEYS = (
    "points-away-game-ml-away",
    "points-home-game-ml-home",
    "points-away-game-sp-away",
    "points-home-game-sp-home",
    "points-all-game-ou-over",
    "points-all-game-ou-under",
)


def _market_metadata(market_key: str) -> Dict[str, str]:
    """Decompose the market_key into SGO identifier columns."""
    parts = market_key.split("-")
    # parts = [statID, statEntityID, periodID, betTypeID, sideID]
    stat_id, stat_entity, period, bet_type, side = parts
    market_name = {
        "ml": "Moneyline",
        "sp": "Spread",
        "ou": "Total",
    }.get(bet_type, "Unknown")
    return {
        "marketName":   market_name,
        "statID":       stat_id,
        "statEntityID": stat_entity,
        "periodID":     period,
        "betTypeID":    bet_type,
        "sideID":       side,
    }


def _line_for(bet_type: str, side: str) -> float | None:
    if bet_type == "ml":
        return None
    if bet_type == "sp":
        return -1.5 if side == "away" else 1.5
    if bet_type == "ou":
        return 8.5
    return None


def make_book_quote(
    *, odds: int = -110,
    bet_type: str | None = None,
    side: str | None = None,
    line_override: float | None = None,
) -> Dict[str, Any]:
    """Per-bookmaker payload entry inside `byBookmaker`."""
    q: Dict[str, Any] = {"odds": odds}
    if bet_type == "sp":
        q["spread"] = (line_override
                         if line_override is not None
                         else _line_for("sp", side or "away"))
    elif bet_type == "ou":
        q["overUnder"] = (line_override
                            if line_override is not None
                            else _line_for("ou", side or "over"))
    return q


def make_event(
    *,
    event_id: str = "evt_test_001",
    home_team: str = "New York Yankees",
    away_team: str = "Boston Red Sox",
    commence_iso: str = "2026-06-02T22:00:00Z",
    market_keys: Iterable[str] = PROD_MARKET_KEYS,
    books: Iterable[str] = ("draftkings", "fanduel", "betmgm"),
    extra_markets: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Build one event with all six prod markets across N books.

    `extra_markets`: optional dict of `{market_key: market_obj}` to
    splice in (e.g. an unmapped market to exercise the diff path).
    """
    odds_block: Dict[str, Any] = {}
    for mk in market_keys:
        meta = _market_metadata(mk)
        by_bm: Dict[str, Any] = {}
        for b in books:
            by_bm[b] = make_book_quote(
                odds=-110, bet_type=meta["betTypeID"],
                side=meta["sideID"],
            )
        odds_block[mk] = {**meta, "byBookmaker": by_bm}
    if extra_markets:
        odds_block.update(extra_markets)
    return {
        "eventID":  event_id,
        "startsAt": commence_iso,
        "teams": {
            "home": {"names": {"long": home_team,
                                 "short": home_team.split()[-1],
                                 "abbrev": "HOM"}},
            "away": {"names": {"long": away_team,
                                 "short": away_team.split()[-1],
                                 "abbrev": "AWY"}},
        },
        "odds":  odds_block,
        "props": [],   # player props — IGNORED by normalizer
    }


def make_payload(**event_kwargs: Any) -> Dict[str, Any]:
    """Top-level SGO envelope with one event under `data`."""
    return {"data": [make_event(**event_kwargs)]}


def make_payload_bytes(**event_kwargs: Any) -> bytes:
    return json.dumps(make_payload(**event_kwargs),
                       ensure_ascii=False).encode("utf-8")


def make_events_envelope(**event_kwargs: Any) -> Dict[str, Any]:
    """`{"events": [...]}` shape — for callers that bypass the
    SGO provider (e.g. direct `run_pass(payload, ...)`) and need
    the post-provider-normalize shape.
    """
    return {"events": [make_event(**event_kwargs)]}
