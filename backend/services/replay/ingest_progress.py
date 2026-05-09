"""
Resumable checkpoint store for the replay ingest.

One doc per (sport_key, event_id, window_label). Tracks lifecycle:
  pending → in_flight → done | not_available | error

`done`            : 200 OK, snapshot + normalized rows persisted
`not_available`   : 404 from the API at this snapshot ts (no retry needed)
`error`           : last attempt threw; will be retried up to MAX_RETRIES
`in_flight`       : currently being processed (for crash recovery diagnostics)

The unique compound (sport_key, event_id, window_label) makes
checkpoint upserts idempotent. On resume we skip anything terminal
(done / not_available) and retry errors below MAX_RETRIES.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PROGRESS_COLLECTION = "replay_ingest_progress"

STATUS_PENDING = "pending"
STATUS_IN_FLIGHT = "in_flight"
STATUS_DONE = "done"
STATUS_NOT_AVAILABLE = "not_available"
STATUS_ERROR = "error"

TERMINAL_STATUSES = (STATUS_DONE, STATUS_NOT_AVAILABLE)

MAX_RETRIES = 2


async def ensure_progress_indexes(db) -> List[str]:
    coll = db[PROGRESS_COLLECTION]
    names: List[str] = []
    names.append(await coll.create_index(
        [("sport_key", 1), ("event_id", 1), ("window_label", 1)],
        name="uniq_sport_event_window", unique=True,
    ))
    names.append(await coll.create_index(
        [("status", 1)], name="status",
    ))
    names.append(await coll.create_index(
        [("game_date", 1)], name="game_date",
    ))
    return names


async def is_terminal(db, *, sport_key: str, event_id: str,
                      window_label: str) -> Optional[str]:
    """Returns the existing terminal status if present, else None."""
    doc = await db[PROGRESS_COLLECTION].find_one(
        {"sport_key": sport_key, "event_id": event_id,
         "window_label": window_label},
        {"_id": 0, "status": 1, "retries": 1},
    )
    if not doc:
        return None
    if doc.get("status") in TERMINAL_STATUSES:
        return doc["status"]
    if doc.get("status") == STATUS_ERROR \
            and (doc.get("retries") or 0) >= MAX_RETRIES:
        return STATUS_ERROR
    return None


async def mark_in_flight(db, *, sport_key: str, event_id: str,
                          window_label: str, game_date: str,
                          run_id: str) -> None:
    await db[PROGRESS_COLLECTION].update_one(
        {"sport_key": sport_key, "event_id": event_id,
         "window_label": window_label},
        {"$set": {"sport_key": sport_key, "event_id": event_id,
                   "window_label": window_label, "game_date": game_date,
                   "status": STATUS_IN_FLIGHT,
                   "last_run_id": run_id,
                   "last_attempt_at": datetime.now(timezone.utc)},
         "$setOnInsert": {"first_seen_at": datetime.now(timezone.utc),
                           "retries": 0}},
        upsert=True,
    )


async def mark_done(db, *, sport_key: str, event_id: str,
                     window_label: str, summary: Dict[str, Any]) -> None:
    await db[PROGRESS_COLLECTION].update_one(
        {"sport_key": sport_key, "event_id": event_id,
         "window_label": window_label},
        {"$set": {"status": STATUS_DONE,
                   "completed_at": datetime.now(timezone.utc),
                   "last_summary": summary}},
    )


async def mark_not_available(db, *, sport_key: str, event_id: str,
                              window_label: str) -> None:
    await db[PROGRESS_COLLECTION].update_one(
        {"sport_key": sport_key, "event_id": event_id,
         "window_label": window_label},
        {"$set": {"status": STATUS_NOT_AVAILABLE,
                   "completed_at": datetime.now(timezone.utc)}},
    )


async def mark_error(db, *, sport_key: str, event_id: str,
                      window_label: str, error: str) -> None:
    await db[PROGRESS_COLLECTION].update_one(
        {"sport_key": sport_key, "event_id": event_id,
         "window_label": window_label},
        {"$set": {"status": STATUS_ERROR,
                   "last_error": error,
                   "last_error_at": datetime.now(timezone.utc)},
         "$inc": {"retries": 1}},
    )


async def progress_summary(db, *, sport_key: str) -> Dict[str, int]:
    out: Dict[str, int] = {s: 0 for s in (
        STATUS_PENDING, STATUS_IN_FLIGHT, STATUS_DONE,
        STATUS_NOT_AVAILABLE, STATUS_ERROR,
    )}
    async for d in db[PROGRESS_COLLECTION].aggregate([
        {"$match": {"sport_key": sport_key}},
        {"$group": {"_id": "$status", "n": {"$sum": 1}}},
    ]):
        out[d["_id"]] = d["n"]
    return out


__all__ = [
    "PROGRESS_COLLECTION",
    "STATUS_PENDING", "STATUS_IN_FLIGHT", "STATUS_DONE",
    "STATUS_NOT_AVAILABLE", "STATUS_ERROR",
    "TERMINAL_STATUSES", "MAX_RETRIES",
    "ensure_progress_indexes",
    "is_terminal", "mark_in_flight", "mark_done",
    "mark_not_available", "mark_error", "progress_summary",
]
