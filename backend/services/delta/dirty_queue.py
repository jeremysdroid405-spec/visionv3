"""
Delta dirty-queue
=================

Replaces timestamp-based watermark detection with an explicit dirty
queue. Every place that mutates `{sport}_live_props` enqueues the
canonical_key here; the detector reads from this queue instead of
`live_props.find({updated_at > watermark})`.

Why
---
Watermark detection raced upstream commits — when ingestion stamped
rows with `updated_at = T0` but the bulk-write committed at `T0 + 90s`
(longer than any reasonable grace window), the watermark would
advance past T0 before those rows became visible to the detector. The
rows were then permanently masked from every subsequent detect query.
That bug repeatedly froze tier composition for hours at a time.

The dirty queue exploits Mongo's monotonic-on-insert `_id`: every
insert into a collection gets an ObjectId greater than every prior
insert in that collection. Late commits can't beat this — even if a
row's `updated_at` is in the past, its `_id` is assigned at commit
time, so a `_id > last_processed_id` query is guaranteed to surface
every committed row exactly once.

API
---
    enqueue_dirty(db, canonical_keys, *, sport, reason)
        Bulk-insert one row per key. Idempotent at the queue level —
        the same key can appear N times; the consumer dedupes when
        draining. Cheap to call from inside any ingestion hot path.

    drain_dirty(db, *, sport, batch_limit)
        Fetch (don't delete) up to `batch_limit` queued canonical_keys
        for the given sport, ordered by `_id`. Returns
        `(canonical_keys, queue_ids)`. The detector calls this; the
        rescorer deletes `queue_ids` after a successful score-doc
        write.

    confirm_processed(db, queue_ids)
        Delete the rows we successfully rescored. Crash-safe: if the
        rescorer dies between drain and confirm, the rows remain in
        the queue and the next tick picks them up again. Rescore is
        idempotent so a re-run is harmless.

    queue_depth(db, *, sport)
        Diagnostic count.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, Sequence

from bson import ObjectId

logger = logging.getLogger(__name__)

DIRTY_QUEUE_COLLECTION = "delta_dirty_queue"
SUPPORTED_REASONS = ("ingestion", "manual", "score_recompute", "watcher")


async def enqueue_dirty(
    db,
    canonical_keys: Iterable[str],
    *,
    sport: str,
    reason: str = "ingestion",
    ingestion_batch: str | None = None,
) -> int:
    """Insert one row per (sport, canonical_key, reason). Returns the
    number of rows inserted. Empty input is a no-op (returns 0).

    Hot-path safe: a single bulk insert per call. Does NOT dedupe at
    the writer — the queue may carry duplicates; the consumer dedupes
    when draining. Duplicate cost is one ObjectId + ~100 bytes per
    write; cheaper than a unique-index check on every insert.
    """
    keys = [k for k in canonical_keys if k]
    if not keys:
        return 0
    if reason not in SUPPORTED_REASONS:
        # Loud — unknown reasons indicate a typo at a writer site.
        raise ValueError(
            f"enqueue_dirty: unknown reason {reason!r} "
            f"(supported: {SUPPORTED_REASONS})"
        )
    now = datetime.now(timezone.utc)
    docs = [
        {
            "canonical_key":  k,
            "sport":          sport,
            "enqueued_at":    now,
            "reason":         reason,
            "ingestion_batch": ingestion_batch,
        }
        for k in keys
    ]
    res = await db[DIRTY_QUEUE_COLLECTION].insert_many(
        docs, ordered=False
    )
    return len(res.inserted_ids)


async def drain_dirty(
    db,
    *,
    sport: str,
    batch_limit: int = 5000,
) -> tuple[list[str], list[ObjectId]]:
    """Fetch the next `batch_limit` queued canonical_keys for `sport`,
    oldest-first. Returns `(canonical_keys, queue_ids)`. Caller MUST
    call `confirm_processed(queue_ids)` after the rescore succeeds.

    Dedupes canonical_keys in-Python: returns each key once even if
    the queue has multiple entries for it; ALL their queue_ids are
    returned so confirm_processed deletes every duplicate.

    No timestamp filtering. The queue is the single source of truth
    for "what has changed since last tick".
    """
    coll = db[DIRTY_QUEUE_COLLECTION]
    cursor = coll.find(
        {"sport": sport},
        projection={"_id": 1, "canonical_key": 1},
        sort=[("_id", 1)],
        limit=batch_limit,
    )
    keys_seen: dict[str, None] = {}
    queue_ids: list[ObjectId] = []
    async for doc in cursor:
        ck = doc.get("canonical_key")
        if ck:
            keys_seen.setdefault(ck, None)
        queue_ids.append(doc["_id"])
    return list(keys_seen.keys()), queue_ids


async def confirm_processed(db, queue_ids: Sequence[ObjectId]) -> int:
    """Delete the queue rows we successfully rescored. Returns deleted
    count. Idempotent — calling with already-deleted ids is a no-op."""
    if not queue_ids:
        return 0
    res = await db[DIRTY_QUEUE_COLLECTION].delete_many(
        {"_id": {"$in": list(queue_ids)}}
    )
    return res.deleted_count


async def queue_depth(db, *, sport: str | None = None) -> int:
    """Diagnostic: how many rows are currently queued.

    Pass `sport=None` for the global total."""
    q = {} if sport is None else {"sport": sport}
    return await db[DIRTY_QUEUE_COLLECTION].count_documents(q)


async def ensure_indexes(db) -> None:
    """Idempotent index creation. Call once at app startup.

    `(_sport, _id)` compound index — supports the drain query
    `find({sport}).sort(_id, 1).limit(N)` without needing a full
    collection scan as the queue grows."""
    coll = db[DIRTY_QUEUE_COLLECTION]
    await coll.create_index(
        [("sport", 1), ("_id", 1)],
        name="sport_id_drain_idx",
        background=True,
    )


__all__ = [
    "DIRTY_QUEUE_COLLECTION",
    "enqueue_dirty",
    "drain_dirty",
    "confirm_processed",
    "queue_depth",
    "ensure_indexes",
]
