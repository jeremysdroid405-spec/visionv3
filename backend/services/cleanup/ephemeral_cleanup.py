"""System-wide ephemeral data cleanup for PropVision.

Stops stale/orphan realtime scoring docs (and other ephemeral outputs)
from accumulating forever, while NEVER deleting the current active
slate.

LIFECYCLE
─────────
Each ephemeral doc transitions through two states:

  Active  (current live slate)
      active = True
      ttl_purge_at = None
      stale_reason = None
      stale_marked_at = None

  Inactive (orphan — canonical_key gone from live_props)
      active = False
      ttl_purge_at = <now + grace_hours>      ← TTL index removes it
      stale_reason = "orphan_missing_from_live_props"
      stale_marked_at = <utc now>

Mongo TTL index sits on ``ttl_purge_at`` (NOT ``updated_at``) so the
purge fires exactly at the recorded future timestamp.

If a previously-inactivated doc reappears on the slate (the
canonical_key shows up in live_props again), it is restored:
``active=True, ttl_purge_at=None, stale_reason=None``.

SAFETY
──────
* The utility refuses to touch any collection not explicitly listed in
  ``ephemeral_collections.EPHEMERAL_CLEANUP_CONFIG``.
* It refuses to touch any collection in ``PROTECTED_COLLECTIONS``.
* When ``live_props`` is empty (ingest outage), it aborts cleanup for
  that sport unless ``force=True`` — preventing a full slate wipe.
* Default to ``dry_run=True`` everywhere.
* No hard deletes from this utility — Mongo's TTL index does the
  eventual physical removal.

PUBLIC API
──────────
    ensure_ttl_indexes(db, sport=None)
    get_live_canonical_keys(db, sport)
    mark_orphan_docs(db, sport, dry_run=True, force=False)
    restore_active_docs(db, sport, dry_run=True)
    run_ephemeral_cleanup(db, sport=None, dry_run=True, force=False)
    status_report(db, sport=None)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from services.cleanup.ephemeral_collections import (
    EPHEMERAL_CLEANUP_CONFIG,
    PROTECTED_COLLECTIONS,
    get_sport_config,
    iter_collections,
    list_configured_sports,
)
# 2026-05-15 — Use the universal lifecycle helper instead of
# duplicating field assignments. Same contract as the cached_board
# publisher, so prop_scores + cached_board collections all carry
# byte-identical inactive markers.
from services.boards.board_lifecycle import (
    lifecycle_set_inactive,
    lifecycle_set_for_upsert,
)

logger = logging.getLogger(__name__)

TTL_INDEX_NAME = "ttl_purge_at_ephemeral_ix"
STALE_REASON_ORPHAN = "orphan_missing_from_live_props"


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enabled_sports(sport: Optional[str]) -> List[str]:
    if sport:
        if sport not in EPHEMERAL_CLEANUP_CONFIG:
            raise ValueError(f"unknown sport {sport!r}")
        return [sport]
    return [s for s in list_configured_sports()
            if EPHEMERAL_CLEANUP_CONFIG[s].get("enabled")]


async def _coll_has_data(db, coll_name: str) -> bool:
    return bool(await db[coll_name].estimated_document_count())


# ──────────────────────────────────────────────────────────────────────
# 1) ensure_ttl_indexes
# ──────────────────────────────────────────────────────────────────────
async def ensure_ttl_indexes(
    db, sport: Optional[str] = None,
) -> Dict[str, Any]:
    """Create (or validate) the TTL index on ``ttl_purge_at`` for every
    configured ephemeral collection.

    ``expireAfterSeconds=0`` means Mongo treats the value in
    ``ttl_purge_at`` as the literal expiry timestamp. Docs without the
    field are untouched (which is exactly our active-doc contract).
    """
    sports = _enabled_sports(sport) if sport else list_configured_sports()
    results: List[Dict[str, Any]] = []
    for s in sports:
        for entry in iter_collections(s):
            cname = entry["name"]
            try:
                await db[cname].create_index(
                    "ttl_purge_at",
                    expireAfterSeconds=0,
                    name=TTL_INDEX_NAME,
                )
                results.append({
                    "sport": s, "collection": cname,
                    "index": TTL_INDEX_NAME, "status": "ensured",
                })
                logger.info(
                    "[EPHEMERAL_CLEANUP:%s] TTL index ensured on %s",
                    s, cname,
                )
            except Exception as exc:  # pragma: no cover
                # If the index already exists with a different
                # expireAfterSeconds, Mongo raises. Surface it.
                results.append({
                    "sport": s, "collection": cname,
                    "index": TTL_INDEX_NAME, "status": "error",
                    "error": repr(exc),
                })
                logger.warning(
                    "[EPHEMERAL_CLEANUP:%s] TTL index create on %s "
                    "failed: %s", s, cname, exc,
                )
    return {"results": results}


# ──────────────────────────────────────────────────────────────────────
# 2) get_live_canonical_keys
# ──────────────────────────────────────────────────────────────────────
async def get_live_canonical_keys(db, sport: str) -> Set[str]:
    """Return the set of ``canonical_key`` values currently present
    in the configured ``live_collection`` for ``sport``."""
    cfg = get_sport_config(sport)
    live_coll = cfg["live_collection"]
    key_field = cfg.get("canonical_key_field") or "canonical_key"
    keys: Set[str] = set()
    cursor = db[live_coll].find({}, {key_field: 1, "_id": 0})
    async for d in cursor:
        v = d.get(key_field)
        if v:
            keys.add(v)
    return keys


# ──────────────────────────────────────────────────────────────────────
# 3) mark_orphan_docs
# ──────────────────────────────────────────────────────────────────────
async def mark_orphan_docs(
    db, sport: str,
    dry_run: bool = True,
    force: bool = False,
) -> Dict[str, Any]:
    """Mark every doc whose canonical_key is no longer in live_props as
    ``active=False`` and schedule it for TTL purge in
    ``grace_hours`` from now.

    ``force=True`` overrides the "live_props is empty" safety abort.
    """
    cfg = get_sport_config(sport)
    if not cfg.get("enabled") and not force:
        return {"sport": sport, "status": "skipped_disabled"}

    live_coll = cfg["live_collection"]
    grace_hours = int(cfg.get("grace_hours", 24))
    purge_at = _utcnow() + timedelta(hours=grace_hours)

    live_count = await db[live_coll].estimated_document_count()
    if live_count == 0 and not force:
        logger.warning(
            "[EPHEMERAL_CLEANUP:%s] ABORT — live_collection %s is "
            "EMPTY (likely ingest outage); refusing to mark all docs "
            "stale. Use force=True to override.",
            sport, live_coll,
        )
        return {
            "sport": sport, "status": "aborted_live_empty",
            "live_collection": live_coll, "live_count": 0,
        }

    live_keys = await get_live_canonical_keys(db, sport)
    per_collection: List[Dict[str, Any]] = []
    for entry in iter_collections(sport):
        cname = entry["name"]
        key_field = entry["key_field"]
        nested_path = entry.get("nested_key_path")

        # ── Build query that finds orphans ──────────────────────
        # Nested-path collections (e.g. cached_board with props array):
        # we keep player-doc if ANY nested prop's canonical_key is in
        # the live keep-set. Otherwise the player is no longer on the
        # slate and the whole doc is orphaned.
        total = await db[cname].estimated_document_count()
        already_inactive = await db[cname].count_documents({"active": False})

        # Find candidates currently considered active (or unset)
        active_filter = {"$or": [
            {"active": True}, {"active": {"$exists": False}}, {"active": None},
        ]}

        if nested_path:
            # nested: doc orphan iff none of its nested canonical_keys
            # are present in live_keys. Pull min projection.
            projection = {nested_path: 1, "_id": 1}
            orphan_ids: List[Any] = []
            scanned = 0
            missing_field = 0
            cursor = db[cname].find(active_filter, projection)
            async for d in cursor:
                scanned += 1
                items = d.get(nested_path) or []
                doc_keys = [
                    (it or {}).get(key_field) for it in items
                    if isinstance(it, dict)
                ]
                doc_keys = [k for k in doc_keys if k]
                if not doc_keys:
                    missing_field += 1
                    continue
                if not any(k in live_keys for k in doc_keys):
                    orphan_ids.append(d["_id"])
            would_mark = len(orphan_ids)
            sample_keys: List[str] = []
            if not dry_run and orphan_ids:
                await db[cname].update_many(
                    {"_id": {"$in": orphan_ids}},
                    {"$set": lifecycle_set_inactive(
                        reason=STALE_REASON_ORPHAN,
                        ttl_purge_at=purge_at,
                    )},
                )
            per_collection.append({
                "sport": sport, "collection": cname,
                "mode": "nested",
                "total_docs": total,
                "already_inactive": already_inactive,
                "scanned_active": scanned,
                "missing_canonical_key": missing_field,
                "would_mark_inactive": would_mark,
                "applied": (not dry_run) and bool(orphan_ids),
                "ttl_purge_at": purge_at.isoformat()
                if (not dry_run and orphan_ids) else None,
            })
        else:
            # flat: docs orphan iff key_field not in live_keys.
            # Use $nin against live_keys (chunked) for efficiency only
            # when keep-set is bounded; else stream.
            scanned = await db[cname].count_documents(active_filter)
            missing_field = await db[cname].count_documents({
                **active_filter,
                "$or": [{key_field: None}, {key_field: {"$exists": False}}],
            })
            # Use distinct() on the active set then diff in memory.
            active_keys_list = await db[cname].distinct(
                key_field, active_filter,
            )
            active_keys: Set[str] = {k for k in active_keys_list if k}
            orphan_keys = active_keys - live_keys
            would_mark_filter = {
                **active_filter,
                key_field: {"$in": list(orphan_keys)},
            } if orphan_keys else None
            would_mark = await db[cname].count_documents(
                would_mark_filter
            ) if would_mark_filter else 0
            sample_keys = list(sorted(orphan_keys))[:5]
            applied = False
            if not dry_run and would_mark_filter:
                res = await db[cname].update_many(
                    would_mark_filter,
                    {"$set": lifecycle_set_inactive(
                        reason=STALE_REASON_ORPHAN,
                        ttl_purge_at=purge_at,
                    )},
                )
                applied = True
                logger.info(
                    "[EPHEMERAL_CLEANUP:%s/%s] marked %d orphan docs "
                    "inactive (purge_at=%s, grace=%dh)",
                    sport, cname, res.modified_count,
                    purge_at.isoformat(), grace_hours,
                )
            per_collection.append({
                "sport": sport, "collection": cname,
                "mode": "flat",
                "total_docs": total,
                "already_inactive": already_inactive,
                "scanned_active": scanned,
                "missing_canonical_key": missing_field,
                "active_unique_keys": len(active_keys),
                "orphan_unique_keys": len(orphan_keys),
                "would_mark_inactive": would_mark,
                "applied": applied,
                "ttl_purge_at": purge_at.isoformat() if applied else None,
                "sample_orphan_keys": sample_keys,
            })
    # ── Legacy inactive backfill ────────────────────────────────
    # Many pre-existing inactive docs predate this utility and have
    # no ``ttl_purge_at`` — they would never get purged. Stamp the
    # field on every inactive doc that's also a confirmed orphan
    # (canonical_key not in live_keys) and lacks ``ttl_purge_at``.
    # This is additive and only schedules TTL — never reactivates.
    legacy_backfill: List[Dict[str, Any]] = []
    for entry in iter_collections(sport):
        cname = entry["name"]
        key_field = entry["key_field"]
        nested_path = entry.get("nested_key_path")
        if nested_path:
            continue  # skip nested for legacy backfill (player-doc)
        legacy_filter = {
            "active": False,
            "$or": [
                {"ttl_purge_at": None},
                {"ttl_purge_at": {"$exists": False}},
            ],
        }
        legacy_inactive_keys = await db[cname].distinct(
            key_field, legacy_filter,
        )
        legacy_keys = {k for k in legacy_inactive_keys if k}
        legacy_orphan = legacy_keys - live_keys
        n_legacy = 0
        applied_legacy = False
        if legacy_orphan:
            n_legacy = await db[cname].count_documents({
                **legacy_filter,
                key_field: {"$in": list(legacy_orphan)},
            })
            if not dry_run and n_legacy:
                await db[cname].update_many(
                    {**legacy_filter,
                     key_field: {"$in": list(legacy_orphan)}},
                    {"$set": {
                        "ttl_purge_at": purge_at,
                        "stale_reason": STALE_REASON_ORPHAN,
                    }},
                )
                applied_legacy = True
                logger.info(
                    "[EPHEMERAL_CLEANUP:%s/%s] legacy-inactive "
                    "backfill: stamped ttl_purge_at on %d orphan "
                    "docs", sport, cname, n_legacy,
                )
        legacy_backfill.append({
            "collection": cname,
            "legacy_inactive_orphans": n_legacy,
            "applied": applied_legacy,
        })
    return {
        "sport": sport,
        "live_collection": live_coll,
        "live_count": live_count,
        "live_keys_count": len(live_keys),
        "grace_hours": grace_hours,
        "dry_run": dry_run,
        "force": force,
        "collections": per_collection,
        "legacy_inactive_backfill": legacy_backfill,
    }


# ──────────────────────────────────────────────────────────────────────
# 4) restore_active_docs
# ──────────────────────────────────────────────────────────────────────
async def restore_active_docs(
    db, sport: str,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Restore docs that were previously marked inactive but whose
    canonical_key has reappeared on the slate.

    Sets ``active=True``, clears ``ttl_purge_at``, ``stale_reason``,
    ``stale_marked_at``.
    """
    cfg = get_sport_config(sport)
    if not cfg.get("enabled"):
        return {"sport": sport, "status": "skipped_disabled"}
    live_keys = await get_live_canonical_keys(db, sport)
    per_collection: List[Dict[str, Any]] = []
    for entry in iter_collections(sport):
        cname = entry["name"]
        key_field = entry["key_field"]
        nested_path = entry.get("nested_key_path")
        already_inactive_filter = {"active": False}

        if nested_path:
            # Restore if any nested key now appears in live.
            restore_ids = []
            scanned = 0
            cursor = db[cname].find(
                already_inactive_filter,
                {nested_path: 1, "_id": 1},
            )
            async for d in cursor:
                scanned += 1
                items = d.get(nested_path) or []
                doc_keys = [
                    (it or {}).get(key_field) for it in items
                    if isinstance(it, dict)
                ]
                doc_keys = [k for k in doc_keys if k]
                if any(k in live_keys for k in doc_keys):
                    restore_ids.append(d["_id"])
            would_restore = len(restore_ids)
            applied = False
            if not dry_run and restore_ids:
                await db[cname].update_many(
                    {"_id": {"$in": restore_ids}},
                    {"$set": lifecycle_set_for_upsert()},
                )
                applied = True
            per_collection.append({
                "sport": sport, "collection": cname, "mode": "nested",
                "scanned_inactive": scanned,
                "would_restore": would_restore, "applied": applied,
            })
        else:
            inactive_keys_list = await db[cname].distinct(
                key_field, already_inactive_filter,
            )
            inactive_keys = {k for k in inactive_keys_list if k}
            restore_keys = inactive_keys & live_keys
            would_restore_filter = {
                **already_inactive_filter,
                key_field: {"$in": list(restore_keys)},
            } if restore_keys else None
            would_restore = await db[cname].count_documents(
                would_restore_filter
            ) if would_restore_filter else 0
            applied = False
            if not dry_run and would_restore_filter:
                res = await db[cname].update_many(
                    would_restore_filter,
                    {"$set": lifecycle_set_for_upsert()},
                )
                applied = True
                logger.info(
                    "[EPHEMERAL_CLEANUP:%s/%s] restored %d docs "
                    "to active", sport, cname, res.modified_count,
                )
            per_collection.append({
                "sport": sport, "collection": cname, "mode": "flat",
                "inactive_unique_keys": len(inactive_keys),
                "restore_unique_keys": len(restore_keys),
                "would_restore": would_restore, "applied": applied,
            })
    return {
        "sport": sport,
        "live_keys_count": len(live_keys),
        "dry_run": dry_run,
        "collections": per_collection,
    }


# ──────────────────────────────────────────────────────────────────────
# 5) run_ephemeral_cleanup
# ──────────────────────────────────────────────────────────────────────
async def run_ephemeral_cleanup(
    db, sport: Optional[str] = None,
    dry_run: bool = True,
    force: bool = False,
) -> Dict[str, Any]:
    """Full cleanup pass for one or all sports.

    Order:
      1. Restore: any inactive doc whose canonical_key is back on
         the slate is reactivated (clear ttl_purge_at).
      2. Mark orphans inactive (with ttl_purge_at = now + grace_hours).
    """
    sports = _enabled_sports(sport)
    per_sport: Dict[str, Dict[str, Any]] = {}
    for s in sports:
        try:
            restore = await restore_active_docs(db, s, dry_run=dry_run)
            mark = await mark_orphan_docs(
                db, s, dry_run=dry_run, force=force,
            )
            per_sport[s] = {"restore": restore, "mark": mark}
        except Exception as exc:  # pragma: no cover
            logger.exception(
                "[EPHEMERAL_CLEANUP:%s] failed: %s", s, exc,
            )
            per_sport[s] = {"error": repr(exc)}
    return {
        "sports": sports, "dry_run": dry_run, "force": force,
        "per_sport": per_sport,
        "completed_at": _utcnow().isoformat(),
    }


# ──────────────────────────────────────────────────────────────────────
# 6) status_report — for /status endpoint
# ──────────────────────────────────────────────────────────────────────
async def status_report(
    db, sport: Optional[str] = None,
) -> Dict[str, Any]:
    """Read-only inventory of every configured collection."""
    sports = _enabled_sports(sport) if sport else list_configured_sports()
    out: Dict[str, Any] = {"sports": {}, "protected_count": len(PROTECTED_COLLECTIONS)}
    for s in sports:
        cfg = EPHEMERAL_CLEANUP_CONFIG[s]
        live = cfg["live_collection"]
        per_coll: List[Dict[str, Any]] = []
        live_count = await db[live].estimated_document_count() \
            if cfg.get("enabled") else None
        live_keys: Set[str] = set()
        if cfg.get("enabled"):
            try:
                live_keys = await get_live_canonical_keys(db, s)
            except Exception:
                live_keys = set()
        for entry in iter_collections(s):
            cname = entry["name"]
            total = await db[cname].estimated_document_count()
            n_active = await db[cname].count_documents({"active": True})
            n_inactive = await db[cname].count_documents({"active": False})
            n_pending_purge = await db[cname].count_documents(
                {"ttl_purge_at": {"$ne": None, "$exists": True}}
            )
            n_no_active_field = await db[cname].count_documents(
                {"$or": [
                    {"active": {"$exists": False}}, {"active": None},
                ]}
            )
            # Orphan count vs live (flat collections only — nested
            # requires per-doc scan, skip for speed).
            n_orphan_vs_live: Optional[int] = None
            if entry.get("nested_key_path") is None and live_keys:
                active_distinct = await db[cname].distinct(
                    entry["key_field"], {"active": True},
                )
                active_set = {k for k in active_distinct if k}
                orphan_keys = active_set - live_keys
                if orphan_keys:
                    n_orphan_vs_live = await db[cname].count_documents({
                        "active": True,
                        entry["key_field"]: {"$in": list(orphan_keys)},
                    })
                else:
                    n_orphan_vs_live = 0
            per_coll.append({
                "name": cname,
                "mode": "nested" if entry.get("nested_key_path") else "flat",
                "total": total,
                "active": n_active,
                "inactive": n_inactive,
                "pending_purge": n_pending_purge,
                "no_active_field": n_no_active_field,
                "orphan_vs_live": n_orphan_vs_live,
            })
        out["sports"][s] = {
            "enabled": cfg.get("enabled"),
            "live_collection": live,
            "live_count": live_count,
            "live_canonical_keys": len(live_keys),
            "grace_hours": cfg.get("grace_hours"),
            "collections": per_coll,
        }
    return out
