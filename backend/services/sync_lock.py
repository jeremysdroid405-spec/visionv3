"""
Mongo-backed cross-process sync advisory lock.
==============================================
Purpose: serialize writers across (a) APScheduler in-process jobs,
(b) RebuildCoordinator dispatch, (c) host-cron scripts, and (d) ad-hoc
admin-trigger routes. The in-process `services.upstream_sync_lock` does
not protect against shell-invoked Python scripts.

Contract
--------
* Locks are *advisory*. Callers must voluntarily acquire before mutating
  the protected collections.
* Each lock document is keyed on `lock_key` (string). Recommended keys:
      sync:{sport}        — full master-sync pipeline
      odds:{sport}        — UniversalOddsSync drop-and-rebuild step
      context:{sport}     — feature_hydration mass updates
      recompute:{sport}   — replace-mode recompute
      lineup:{sport}      — lineup ingest writing live_props
      grade:{sport}       — pick-history result grader
* `acquire` is single-attempt, non-blocking. It returns True on success,
  False when the lock is held by another live holder.
* TTL-based auto-expiry: if `expires_at < now`, the next acquirer steals
  the lock atomically (single update with a filter on `expires_at < now`).
  This protects against crashed holders.
* `release` only removes the lock if the same `holder_id` still owns it
  (cas-style), preventing a stale holder from releasing somebody else's
  lock after a TTL steal.

This module ONLY writes to `sync_locks`. It never touches model
state, scoring, gates, thresholds, μ/σ, tier routing, or selection.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

COLLECTION = "sync_locks"
DEFAULT_TTL_SECONDS = 600   # 10 min default; callers should override


@dataclass
class LockHandle:
    lock_key: str
    holder_id: str
    holder: str
    acquired_at: datetime
    expires_at: datetime


async def ensure_indexes(db) -> None:
    """Create indexes if missing. Idempotent."""
    coll = db[COLLECTION]
    await coll.create_index([("lock_key", 1)], unique=True, name="lock_key_uq")
    await coll.create_index([("expires_at", 1)], name="expires_idx")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _holder_string(suffix: Optional[str]) -> str:
    base = f"{socket.gethostname()}/pid={os.getpid()}"
    return f"{base}/{suffix}" if suffix else base


async def acquire(
    db,
    lock_key: str,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    holder: Optional[str] = None,
) -> Optional[LockHandle]:
    """Single-attempt non-blocking acquire.

    Returns a `LockHandle` on success, or `None` if another live holder
    has the lock. Stale locks (`expires_at < now`) are automatically
    stolen.
    """
    coll = db[COLLECTION]
    now = _now_utc()
    expires = now + timedelta(seconds=max(1, ttl_seconds))
    holder_str = _holder_string(holder)
    holder_id = str(uuid.uuid4())

    # Pattern: upsert with filter that accepts (a) doc missing, or (b) doc
    # present but stale. If a non-stale holder owns the lock, this filter
    # matches nothing AND the upsert collides on the unique index → caller
    # treats DuplicateKeyError as "held".
    try:
        await coll.update_one(
            {
                "lock_key": lock_key,
                "$or": [
                    {"expires_at": {"$lt": now}},
                    {"expires_at": {"$exists": False}},
                ],
            },
            {"$set": {
                "lock_key":    lock_key,
                "holder_id":   holder_id,
                "holder":      holder_str,
                "acquired_at": now,
                "expires_at":  expires,
                "status":      "held",
            }},
            upsert=True,
        )
    except Exception as e:  # noqa: BLE001 — duplicate-key on collision
        # Live holder owns the lock; check ownership and bail.
        cls = type(e).__name__
        if "DuplicateKey" in cls or "E11000" in str(e):
            return None
        logger.warning(
            "[SYNC_LOCK] acquire(%s) errored unexpectedly: %s",
            lock_key, e,
        )
        return None

    # Verify we own it (TTL steal could have raced).
    doc = await coll.find_one({"lock_key": lock_key}, {"_id": 0})
    if not doc or doc.get("holder_id") != holder_id:
        return None
    return LockHandle(
        lock_key=lock_key, holder_id=holder_id, holder=holder_str,
        acquired_at=now, expires_at=expires,
    )


async def release(db, handle: LockHandle) -> bool:
    """Release iff `handle.holder_id` still owns the lock."""
    if handle is None:
        return False
    res = await db[COLLECTION].delete_one({
        "lock_key": handle.lock_key,
        "holder_id": handle.holder_id,
    })
    return res.deleted_count == 1


async def is_locked(db, lock_key: str) -> bool:
    doc = await db[COLLECTION].find_one(
        {"lock_key": lock_key, "expires_at": {"$gt": _now_utc()}},
        {"_id": 0, "lock_key": 1},
    )
    return doc is not None


async def describe(db, lock_key: Optional[str] = None) -> Dict[str, Any]:
    """Inspector for the health endpoint."""
    q = {"lock_key": lock_key} if lock_key else {}
    out: Dict[str, Any] = {"now": _now_utc().isoformat(), "locks": []}
    async for d in db[COLLECTION].find(q, {"_id": 0}):
        ea = d.get("expires_at")
        d["expired"] = isinstance(ea, datetime) and ea < _now_utc()
        out["locks"].append(d)
    return out


@asynccontextmanager
async def with_sync_lock(
    db,
    lock_key: str,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    holder: Optional[str] = None,
    raise_if_locked: bool = True,
):
    """Async context manager. Acquires on entry, releases on exit.

    `raise_if_locked=True` raises RuntimeError when the lock is busy;
    `False` yields `None` instead (caller can branch on it)."""
    handle = await acquire(db, lock_key, ttl_seconds=ttl_seconds, holder=holder)
    if handle is None:
        if raise_if_locked:
            raise RuntimeError(
                f"sync_lock busy: {lock_key} (held by another writer)"
            )
        yield None
        return
    try:
        logger.info(
            "[SYNC_LOCK] acquired key=%s holder=%s ttl=%ds",
            lock_key, handle.holder, ttl_seconds,
        )
        yield handle
    finally:
        try:
            ok = await release(db, handle)
            logger.info(
                "[SYNC_LOCK] released key=%s ok=%s", lock_key, ok,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SYNC_LOCK] release(%s) failed: %s", lock_key, exc)


# ---------------------------------------------------------------------------
# Background TTL janitor (optional). Cleans up *expired* lock rows so the
# `sync_locks` collection doesn't accumulate. Single Mongo TTL index
# would also work — included here so callers without index permissions
# can still rely on cleanup.
# ---------------------------------------------------------------------------
async def janitor_once(db) -> int:
    """Delete every expired lock doc. Returns count deleted."""
    res = await db[COLLECTION].delete_many({"expires_at": {"$lt": _now_utc()}})
    return res.deleted_count


__all__ = [
    "COLLECTION", "DEFAULT_TTL_SECONDS", "LockHandle",
    "acquire", "release", "is_locked", "describe",
    "with_sync_lock", "ensure_indexes", "janitor_once",
]
