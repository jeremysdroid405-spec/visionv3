"""
MLB Projected/Confirmed Lineup Loader  (read-only, no external API calls)
==========================================================================
Single canonical source for `batting_order` on `mlb_live_props`.

Background
----------
`services/feature_hydration.py` previously hard-coded
    p["batting_order"]   = None
    p["lineup_confirmed"] = False
with a comment "MOCKED until external feed".  No collection in the local
DB carried `batting_order` (audit 2026-01: 0 / 7,670 mlb_live_props rows
and 0 / 201,626 bdl_historical_game_logs rows).  Net effect: PA-v2
silently fell back to the 4.2 default for 100% of MLB Total Bases picks.

Contract (collection: `mlb_projected_lineups`)
----------------------------------------------
One document per (event_id, team_abbr).  Pre-game data only.

    {
      "event_id":   str,                 # joins mlb_live_props.event_id
      "team_abbr":  str,                 # 3-letter, e.g. "LAD"
      "as_of":      datetime,            # UTC when card was published
      "source":     str,                 # e.g. "mlb_stats_api"|"rotowire"|"manual"
      "confirmed":  bool,                # True = confirmed; False = projected
      "lineup": [
          {"slot": 1, "bdl_player_id": 12345, "player_name": "..."},
          ...                            # 9 entries, slots 1..9
      ],
    }

Indexes (created on first call):
    (event_id, team_abbr)   unique
    (as_of)                 for staleness queries

Strict no-leakage rule
----------------------
The loader will only surface a slot when `as_of <= commence_time` for the
prop being hydrated.  That guarantees no post-game / mid-game data ever
flows into the candidate builder.

Until an external lineup ingestor lands, this collection is EMPTY and
the loader returns an empty map.  Behaviour for PA-v2 is byte-identical
to today's "fallback to 4.2" path.

This module ONLY reads.  It never derives `batting_order` from game
logs and never uses post-game data.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

COLLECTION = "mlb_projected_lineups"

# (event_id, bdl_player_id) -> {"slot": int, "confirmed": bool,
#                                "as_of": datetime, "source": str}
SlotMap = Dict[Tuple[str, int], Dict[str, Any]]


async def ensure_indexes(db) -> None:
    """Create indexes if missing.  Safe to call repeatedly."""
    coll = db[COLLECTION]
    await coll.create_index(
        [("event_id", 1), ("team_abbr", 1)], unique=True, name="evt_team_uq"
    )
    await coll.create_index([("as_of", 1)], name="as_of_idx")


async def load_slot_map(
    db,
    event_ids: Iterable[str],
) -> SlotMap:
    """Return a `(event_id, bdl_player_id) -> slot_info` map.

    Caller is responsible for the no-leakage check (compare
    `slot_info["as_of"]` against the prop's `commence_time`).
    """
    eids = sorted({e for e in (event_ids or []) if e})
    if not eids:
        return {}
    out: SlotMap = {}
    cursor = db[COLLECTION].find(
        {"event_id": {"$in": eids}},
        {"_id": 0, "event_id": 1, "team_abbr": 1, "as_of": 1,
         "source": 1, "confirmed": 1, "lineup": 1},
    )
    async for d in cursor:
        eid = d.get("event_id")
        as_of = d.get("as_of")
        src = d.get("source") or "unknown"
        confirmed = bool(d.get("confirmed"))
        for entry in (d.get("lineup") or []):
            try:
                slot = int(entry.get("slot"))
                pid = int(entry.get("bdl_player_id"))
            except (TypeError, ValueError):
                continue
            if not (1 <= slot <= 9):
                continue
            out[(eid, pid)] = {
                "slot": slot,
                "confirmed": confirmed,
                "as_of": as_of,
                "source": src,
            }
    return out


def _to_dt(v: Any) -> Optional[datetime]:
    """Best-effort coerce datetime|ISO-str → tz-aware UTC datetime.
    Returns None on any failure.  Naive datetimes are assumed UTC
    (Mongo stores datetimes as naive UTC by convention)."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    return None


def lookup_slot(
    slot_map: SlotMap,
    event_id: Optional[str],
    bdl_player_id: Optional[int],
    commence_time: Any,
) -> Tuple[Optional[int], bool, Optional[str]]:
    """Resolve slot for a single prop with strict no-leakage guard.

    Returns (batting_order, lineup_confirmed, source).
    `(None, False, None)` when no row is available, the row leaks the
    future, or any input is missing.
    """
    if not event_id or bdl_player_id is None:
        return None, False, None
    try:
        pid = int(bdl_player_id)
    except (TypeError, ValueError):
        return None, False, None
    info = slot_map.get((event_id, pid))
    if not info:
        return None, False, None
    as_of = _to_dt(info.get("as_of"))
    ct = _to_dt(commence_time)
    # No-leakage: lineup must have been published BEFORE the prop's
    # game starts.  When either timestamp is unparseable we REJECT —
    # the only safe default is to fall through to PA = 4.2.
    if as_of is None or ct is None:
        return None, False, None
    if as_of > ct:
        return None, False, None
    return info["slot"], bool(info.get("confirmed")), info.get("source")
