"""
services/odds_event_props_cache.py — Per-event odds payload cache.

The scheduled_cron `sync_sport_props` previously re-fetched
`/v4/sports/{key}/events/{event_id}/odds` for EVERY upcoming event on
every cycle (every 10-15 min for MLB, every 5 min for NBA), even when
nothing had changed since the previous cycle. That's ~21 calls/sport
every cycle ⇒ hundreds of calls/hour with zero new information for
inactive games.

This cache gates the event_odds fetch behind a (a) TTL window and
(b) payload-hash check, so:

  * If `last_synced_at` is within `EVENT_ODDS_TTL_SECONDS` (default 600s),
    we REUSE the previously-fetched props with no API call. The sync
    can still stage the board because we return the cached `props`
    list verbatim.
  * Otherwise we fetch fresh, hash the props payload, and write back.
    On NEXT cycle the hash is compared to the freshly-fetched payload
    and unchanged events are treated as `mode="delta"`.

The cache stores ONLY the extracted props list and a content hash —
the raw Odds API response stays in-memory for the current sync cycle.
Per-event docs are tiny (~30-300 KB), TTL-indexed at 24h.

Collection: `odds_event_props_cache`
  {
    _id:                     "{sport}|{event_id}",
    sport:                   "mlb"|"nba"|"nfl",
    event_id:                str,
    last_synced_at:          datetime UTC,
    last_attempted_at:       datetime UTC,
    props_hash:              str,    # sha256 of canonical props list
    etag:                    str | None,
    props:                   List[Dict[str, Any]],
    sync_count:              int,    # bumped every fresh fetch
    fresh_count:             int,    # bumped every TTL skip
  }
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CACHE_COLL = "odds_event_props_cache"
DEFAULT_TTL_SECONDS = int(
    os.environ.get("EVENT_ODDS_TTL_SECONDS", "600") or 600)


def _doc_id(sport: str, event_id: str) -> str:
    return f"{sport.lower()}|{event_id}"


def hash_props(props: List[Dict[str, Any]]) -> str:
    """Stable SHA-256 of the props list. Order-independent within each
    prop dict — keys are sorted before serialization. The list ORDER
    matters (PyMongo writes preserve it). This hash is recomputed on
    every fresh fetch and compared to the stored value to detect
    no-op refreshes.
    """
    # Coerce datetimes to ISO strings so json.dumps doesn't choke.
    def _coerce(o):
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, set):
            return sorted(o)
        raise TypeError(repr(o))
    payload = json.dumps(props, sort_keys=True, default=_coerce)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def ensure_indexes(db) -> None:
    """Idempotent — call once at startup. TTL on `last_attempted_at`
    so dead events age out after 24h."""
    if db is None:
        return
    try:
        await db[CACHE_COLL].create_index(
            "last_attempted_at",
            expireAfterSeconds=86_400,
            name="ix_evcache_attempted_ttl",
        )
        await db[CACHE_COLL].create_index(
            [("sport", 1), ("event_id", 1)],
            name="ix_evcache_sport_event",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[EV_CACHE] index init non-fatal: {e}")


async def get(
    db, *, sport: str, event_id: str,
) -> Optional[Dict[str, Any]]:
    """Return the cache doc (without `_id`) or None."""
    if not (sport and event_id):
        return None
    return await db[CACHE_COLL].find_one(
        {"_id": _doc_id(sport, event_id)},
        projection={"_id": 0},
    )


def is_fresh(rec: Optional[Dict[str, Any]],
              *, ttl_seconds: int = DEFAULT_TTL_SECONDS,
              now: Optional[datetime] = None) -> bool:
    """Pure helper — does the cache record satisfy the TTL window?"""
    if rec is None:
        return False
    ts = rec.get("last_synced_at")
    if not isinstance(ts, datetime):
        return False
    # Mongo can return naive datetimes; treat them as UTC.
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() < ttl_seconds


async def put(
    db, *, sport: str, event_id: str,
    props: List[Dict[str, Any]],
    etag: Optional[str] = None,
    new_hash: Optional[str] = None,
    fresh: bool = True,
) -> Dict[str, Any]:
    """Upsert the cache row for one event.

    `fresh=True` means we just fetched from the API (bumps sync_count
    and last_synced_at). `fresh=False` is reserved for "we re-used the
    cache, just stamp last_attempted_at" (bumps fresh_count).
    """
    now = datetime.now(timezone.utc)
    update: Dict[str, Any] = {
        "$set": {
            "sport":             sport.lower(),
            "event_id":          event_id,
            "last_attempted_at": now,
        },
        "$setOnInsert": {"created_at": now},
        "$inc": {},
    }
    if fresh:
        update["$set"]["props"] = props
        update["$set"]["props_hash"] = new_hash or hash_props(props)
        update["$set"]["last_synced_at"] = now
        update["$set"]["etag"] = etag
        update["$inc"]["sync_count"] = 1
    else:
        update["$inc"]["fresh_count"] = 1
    if not update["$inc"]:
        update.pop("$inc")
    res = await db[CACHE_COLL].update_one(
        {"_id": _doc_id(sport, event_id)},
        update,
        upsert=True,
    )
    return {"upserted": bool(res.upserted_id), "modified": res.modified_count}


async def stats(db, *, sport: Optional[str] = None) -> Dict[str, Any]:
    """Quick diagnostic — total rows, oldest/newest sync, fresh-rate."""
    flt: Dict[str, Any] = {}
    if sport:
        flt["sport"] = sport.lower()
    pipe = [
        {"$match": flt},
        {"$group": {
            "_id":          "$sport",
            "n":            {"$sum": 1},
            "min_sync":     {"$min": "$last_synced_at"},
            "max_sync":     {"$max": "$last_synced_at"},
            "total_sync":   {"$sum": "$sync_count"},
            "total_fresh":  {"$sum": "$fresh_count"},
        }},
    ]
    out = []
    async for r in db[CACHE_COLL].aggregate(pipe):
        if isinstance(r.get("min_sync"), datetime):
            r["min_sync"] = r["min_sync"].isoformat()
        if isinstance(r.get("max_sync"), datetime):
            r["max_sync"] = r["max_sync"].isoformat()
        out.append(r)
    return {"by_sport": out, "ttl_seconds": DEFAULT_TTL_SECONDS}
