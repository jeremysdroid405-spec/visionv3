"""
Replay Result Resolver — pulls actual NBA game outcomes for events in
`replay_events` / `replay_props_normalized` and writes them to
`replay_results`.

Sources (cross-validated when both available):
  A. `bdl_historical_game_logs`          — BallDontLie historical bulk archive
  B. `nba_master_hub_2026.player_game_logs` — production hub (recent games)

If sources disagree on key stats (pts/reb/ast/3pm/min), the row is
flagged `validation_status="mismatch"` and BOTH source values are
preserved in `source_a` / `source_b`. We never silently overwrite.

Phase 2 SCOPE NOTE:
  This module is read-only against the source collections. It writes
  ONLY to `replay_results`. Production collections are NEVER mutated.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pymongo import UpdateOne

logger = logging.getLogger(__name__)

REPLAY_RESULTS = "replay_results"
SOURCE_BDL = "bdl_historical_game_logs"
SOURCE_HUB = "nba_master_hub_2026"
PROPS_NORMALIZED = "replay_props_normalized"


# Stats we cross-validate. Any others get reported only from source A
# (canonical source = BDL because it has full historical depth).
COMPARED_STATS = ("pts", "reb", "ast", "fg3m", "min")


def _norm_name(s: Optional[str]) -> str:
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _safe_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _bdl_min_to_int(m: Any) -> Optional[int]:
    """BDL stores 'min' as either int (newer) or 'MM:SS' (older)."""
    if m is None or m == "":
        return None
    if isinstance(m, (int, float)):
        return int(m)
    if isinstance(m, str):
        if ":" in m:
            try:
                mm, ss = m.split(":", 1)
                return int(mm) + (1 if int(ss) >= 30 else 0)
            except (ValueError, TypeError):
                return None
        try:
            return int(m)
        except ValueError:
            return None
    return None


def _stats_from_bdl(row: Dict[str, Any]) -> Dict[str, Any]:
    pts = _safe_int(row.get("pts"))
    reb = _safe_int(row.get("reb"))
    ast = _safe_int(row.get("ast"))
    fg3m = _safe_int(row.get("fg3m"))
    stl = _safe_int(row.get("stl"))
    blk = _safe_int(row.get("blk"))
    minutes = _bdl_min_to_int(row.get("min"))
    return {
        "pts": pts, "reb": reb, "ast": ast, "fg3m": fg3m,
        "stl": stl, "blk": blk, "min": minutes,
        "pra": (None if any(v is None for v in (pts, reb, ast))
                else pts + reb + ast),
        "pr":  (None if any(v is None for v in (pts, reb)) else pts + reb),
        "pa":  (None if any(v is None for v in (pts, ast)) else pts + ast),
        "ra":  (None if any(v is None for v in (reb, ast)) else reb + ast),
    }


def _stats_from_hub(log_row: Dict[str, Any]) -> Dict[str, Any]:
    """The hub stores per-game logs with field names that match BDL closely.
    We accept either fg3m or three_pointers_made."""
    pts = _safe_int(log_row.get("pts"))
    reb = _safe_int(log_row.get("reb"))
    ast = _safe_int(log_row.get("ast"))
    fg3m = _safe_int(
        log_row.get("fg3m") or log_row.get("three_pointers_made"))
    stl = _safe_int(log_row.get("stl"))
    blk = _safe_int(log_row.get("blk"))
    minutes = _bdl_min_to_int(log_row.get("min") or log_row.get("minutes"))
    return {
        "pts": pts, "reb": reb, "ast": ast, "fg3m": fg3m,
        "stl": stl, "blk": blk, "min": minutes,
        "pra": (None if any(v is None for v in (pts, reb, ast))
                else pts + reb + ast),
        "pr":  (None if any(v is None for v in (pts, reb)) else pts + reb),
        "pa":  (None if any(v is None for v in (pts, ast)) else pts + ast),
        "ra":  (None if any(v is None for v in (reb, ast)) else reb + ast),
    }


def cross_validate(a: Dict[str, Any], b: Dict[str, Any]
                    ) -> Tuple[str, Dict[str, Any]]:
    """Returns (validation_status, mismatch_meta_dict)."""
    if a is None and b is None:
        return "missing_both", {}
    if a is None:
        return "source_b_only", {}
    if b is None:
        return "source_a_only", {}
    diffs = {}
    for stat in COMPARED_STATS:
        va, vb = a.get(stat), b.get(stat)
        if va is None or vb is None:
            continue
        if va != vb:
            diffs[stat] = {"a": va, "b": vb, "delta": va - vb}
    if not diffs:
        return "agree", {}
    return "mismatch", {"diffs": diffs}


# ---------------------------------------------------------------- ingest
async def list_events_to_resolve(db) -> List[Dict[str, Any]]:
    """Find every distinct (event_id, commence_time, players_universe)
    represented in the normalized replay collection."""
    pipe = [
        {"$group": {
            "_id": "$event_id",
            "commence_time": {"$first": "$commence_time"},
            "home_team": {"$first": "$home_team"},
            "away_team": {"$first": "$away_team"},
            "players": {"$addToSet": "$player"},
        }},
        {"$sort": {"commence_time": 1}},
    ]
    return [d async for d in db[PROPS_NORMALIZED].aggregate(pipe)]


def _bdl_date_str(commence_time: datetime) -> List[str]:
    """BDL stores `date` as 'YYYY-MM-DD' in the GAME's local US date.
    Tip-offs at 00:10 UTC = 19:10 ET previous day. Try both candidates."""
    local_day = commence_time.date()
    prev_day = (commence_time - timedelta_days(1)).date()
    return [local_day.isoformat(), prev_day.isoformat()]


# (placeholder import kept inline so module is importable without pyutils)
from datetime import timedelta as timedelta_days_internal
def timedelta_days(n: int):
    return timedelta_days_internal(days=n)


async def fetch_bdl_for_event(db, *, commence_time: datetime,
                                player_keys: List[str]
                                ) -> Dict[str, Dict[str, Any]]:
    """Index BDL rows by normalized player name for fast lookup."""
    candidates = _bdl_date_str(commence_time)
    rows = []
    async for row in db[SOURCE_BDL].find({
        "date": {"$in": candidates},
    }, projection={"_id": 0}):
        rows.append(row)
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        key = _norm_name(r.get("player_name"))
        if key:
            out[key] = r
    return out


async def fetch_hub_for_event(db, *, commence_time: datetime,
                                player_keys: List[str]
                                ) -> Dict[str, Dict[str, Any]]:
    """Pull per-game logs from the master hub. The hub stores logs
    embedded as `player_game_logs[]` per player doc, keyed by game_date.
    Returns {normalized_player_name: log_row} for the event date."""
    candidates = _bdl_date_str(commence_time)
    out: Dict[str, Dict[str, Any]] = {}
    # Iterate just the player docs whose normalized name matches our universe
    # to keep the scan small.
    async for doc in db[SOURCE_HUB].find(
        {}, projection={"_id": 0, "display_name": 1, "player_game_logs": 1},
    ):
        pname = doc.get("display_name") or ""
        key = _norm_name(pname)
        if key not in player_keys:
            continue
        for log in (doc.get("player_game_logs") or []):
            game_date = log.get("game_date") or log.get("date")
            if not game_date:
                continue
            if isinstance(game_date, datetime):
                game_date = game_date.strftime("%Y-%m-%d")
            if game_date in candidates:
                out[key] = log
                break
    return out


async def resolve_event(db, *, event: Dict[str, Any],
                          run_id: str) -> Dict[str, Any]:
    event_id = event["_id"]
    commence_time = event["commence_time"]
    if commence_time.tzinfo is None:
        commence_time = commence_time.replace(tzinfo=timezone.utc)
    player_keys = {_norm_name(p) for p in (event.get("players") or [])}

    bdl_map = await fetch_bdl_for_event(
        db, commence_time=commence_time, player_keys=player_keys,
    )
    hub_map = await fetch_hub_for_event(
        db, commence_time=commence_time, player_keys=player_keys,
    )

    rows: List[Dict[str, Any]] = []
    seen_keys = set(bdl_map.keys()) | set(hub_map.keys())
    for key in seen_keys:
        if key not in player_keys:
            continue
        bdl_row = bdl_map.get(key)
        hub_row = hub_map.get(key)
        a_stats = _stats_from_bdl(bdl_row) if bdl_row else None
        b_stats = _stats_from_hub(hub_row) if hub_row else None

        status, meta = cross_validate(a_stats, b_stats)
        canonical = a_stats or b_stats or {}

        display_name = (bdl_row or {}).get("player_name") \
            or (hub_row or {}).get("display_name")
        team = (bdl_row or {}).get("team") \
            or (hub_row or {}).get("team")
        bdl_player_id = (bdl_row or {}).get("player_id") \
            or (hub_row or {}).get("bdl_id")

        rows.append({
            "event_id":          event_id,
            "commence_time":     commence_time,
            "home_team":         event.get("home_team"),
            "away_team":         event.get("away_team"),
            "player_norm":       key,
            "player_name":       display_name,
            "player_id":         bdl_player_id,
            "team":              team,
            "minutes":           canonical.get("min"),
            "pts":               canonical.get("pts"),
            "reb":               canonical.get("reb"),
            "ast":               canonical.get("ast"),
            "fg3m":              canonical.get("fg3m"),
            "stl":               canonical.get("stl"),
            "blk":               canonical.get("blk"),
            "pra":               canonical.get("pra"),
            "pr":                canonical.get("pr"),
            "pa":                canonical.get("pa"),
            "ra":                canonical.get("ra"),
            "did_play":          (canonical.get("min") or 0) > 0,
            "source_a":          a_stats,
            "source_b":          b_stats,
            "validation_status": status,
            "mismatch_meta":     meta or None,
            "resolved_at":       datetime.now(timezone.utc),
            "resolver_run_id":   run_id,
        })
    return {
        "event_id": event_id,
        "rows":     rows,
        "players_in_props": len(player_keys),
        "players_resolved": len(rows),
    }


async def upsert_results(db, rows: List[Dict[str, Any]]
                          ) -> Tuple[int, int]:
    if not rows:
        return 0, 0
    ops = [
        UpdateOne(
            {"event_id": r["event_id"], "player_norm": r["player_norm"]},
            {"$set": r,
             "$setOnInsert": {"_first_seen": r["resolved_at"]}},
            upsert=True,
        )
        for r in rows
    ]
    res = await db[REPLAY_RESULTS].bulk_write(ops, ordered=False)
    return res.upserted_count or 0, res.modified_count or 0


async def ensure_results_indexes(db) -> List[str]:
    coll = db[REPLAY_RESULTS]
    out = []
    out.append(await coll.create_index(
        [("event_id", 1), ("player_norm", 1)],
        name="uniq_event_player", unique=True))
    out.append(await coll.create_index(
        [("validation_status", 1)], name="validation_status"))
    out.append(await coll.create_index(
        [("commence_time", 1)], name="commence_time"))
    return out


__all__ = [
    "REPLAY_RESULTS",
    "cross_validate",
    "list_events_to_resolve",
    "resolve_event",
    "upsert_results",
    "ensure_results_indexes",
    "_norm_name",
    "_stats_from_bdl",
    "_stats_from_hub",
]
