"""Universal cached board lifecycle stamping.

This module is the SINGLE authoritative implementation of the
"active doc" / "inactive doc" lifecycle contract for cached board
documents (e.g. ``{sport}_cached_board``). Every board publisher,
builder, and snapshot writer MUST stamp via this module so the
schema is identical across:

  • NBA cached_board
  • MLB cached_board
  • future-sport cached_board collections

Schema contract
───────────────
Active document (the doc is part of the current live slate):

    active           = True
    ttl_purge_at     = None       # excluded from the TTL purge
    stale_reason     = None
    stale_marked_at  = None
    updated_at       = <utc now>

Inactive document (orphan / no longer on current slate):

    active           = False
    ttl_purge_at     = <future ts>   # Mongo TTL physically removes
                                       # the doc when the clock passes
    stale_reason     = "<short string>"
    stale_marked_at  = <utc now>
    updated_at       = <utc now>

The TTL index on ``ttl_purge_at`` (``expireAfterSeconds=0``) is
ensured by ``services/cleanup/ephemeral_cleanup.ensure_ttl_indexes``
and matches the per-sport ``grace_hours`` window declared in
``services/cleanup/ephemeral_collections.EPHEMERAL_CLEANUP_CONFIG``.

PUBLIC API
──────────
``stamp_active_board_doc(doc)``    — in-place stamps an active doc.
``stamp_inactive_board_doc(doc, reason=None, ttl_purge_at=None)``
    — in-place stamps an inactive doc.
``normalize_board_doc(doc)``       — repair a doc missing lifecycle
    fields; defaults to active.
``lifecycle_set_for_upsert(now=None, source_version_tag=None)``
    — returns the $set fragment for active-doc upserts (suitable to
    spread into a ``{"$set": {...}}`` bulk op).
``lifecycle_set_inactive(reason, ttl_purge_at=None)``
    — returns the $set fragment for marking inactive in bulk ops.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

# Lifecycle fields the schema contract mandates.
LIFECYCLE_FIELDS = (
    "active",
    "ttl_purge_at",
    "stale_reason",
    "stale_marked_at",
    "updated_at",
)

DEFAULT_INACTIVE_GRACE_HOURS = 24
DEFAULT_INACTIVE_REASON_ORPHAN = "orphan_missing_from_live_props"
DEFAULT_INACTIVE_REASON_EMPTY = "empty_player_off_slate"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ──────────────────────────────────────────────────────────────────────
# In-place stamp helpers
# ──────────────────────────────────────────────────────────────────────
def stamp_active_board_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Stamp the active-doc lifecycle on ``doc`` in place. Returns
    the same dict so callers can chain ``await coll.insert_many(...)``
    after a list comprehension."""
    now = _utcnow()
    doc["active"] = True
    doc["ttl_purge_at"] = None
    doc["stale_reason"] = None
    doc["stale_marked_at"] = None
    doc["updated_at"] = now
    return doc


def stamp_inactive_board_doc(
    doc: Dict[str, Any],
    reason: Optional[str] = None,
    ttl_purge_at: Optional[datetime] = None,
    grace_hours: int = DEFAULT_INACTIVE_GRACE_HOURS,
) -> Dict[str, Any]:
    """Stamp the inactive-doc lifecycle on ``doc`` in place.

    ``ttl_purge_at`` is preserved if already set on the doc (never
    extend the grace window on re-mark). Falls back to ``now +
    grace_hours``.
    """
    now = _utcnow()
    existing_purge = doc.get("ttl_purge_at")
    if ttl_purge_at is not None:
        purge_at = ttl_purge_at
    elif isinstance(existing_purge, datetime):
        purge_at = existing_purge
    else:
        purge_at = now + timedelta(hours=grace_hours)
    doc["active"] = False
    doc["ttl_purge_at"] = purge_at
    doc["stale_reason"] = reason or DEFAULT_INACTIVE_REASON_ORPHAN
    doc["stale_marked_at"] = now
    doc["updated_at"] = now
    return doc


def normalize_board_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Repair a doc missing lifecycle fields.

    Strategy:
      • If ``active`` is explicitly False → preserve and fill in the
        missing inactive fields (default reason if absent).
      • Otherwise → treat as an active doc (preserve any
        already-populated ``updated_at``).
    """
    if doc.get("active") is False:
        # Preserve any explicit ttl_purge_at and stale_reason already
        # set; only fill in the missing ones.
        if doc.get("ttl_purge_at") is None:
            doc["ttl_purge_at"] = _utcnow() + timedelta(
                hours=DEFAULT_INACTIVE_GRACE_HOURS,
            )
        if not doc.get("stale_reason"):
            doc["stale_reason"] = (
                "normalize_backfill_active_false"
            )
        if doc.get("stale_marked_at") is None:
            doc["stale_marked_at"] = _utcnow()
        if doc.get("updated_at") is None:
            doc["updated_at"] = _utcnow()
        return doc
    # Default: active doc.
    if doc.get("active") is None:
        doc["active"] = True
    doc["ttl_purge_at"] = None
    doc["stale_reason"] = None
    doc["stale_marked_at"] = None
    if doc.get("updated_at") is None:
        doc["updated_at"] = _utcnow()
    return doc


def is_lifecycle_compliant(doc: Dict[str, Any]) -> bool:
    """Return True iff the doc has every lifecycle field present
    (value may be None for the inactive-fields-of-an-active-doc
    case, that's still compliant)."""
    for f in LIFECYCLE_FIELDS:
        if f not in doc:
            return False
    return True


def missing_lifecycle_fields(doc: Dict[str, Any]) -> list:
    """Return list of lifecycle fields absent from ``doc``."""
    return [f for f in LIFECYCLE_FIELDS if f not in doc]


# ──────────────────────────────────────────────────────────────────────
# $set fragments — convenience for UpdateOne / bulk_write callers
# ──────────────────────────────────────────────────────────────────────
def lifecycle_set_for_upsert(
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Return the ``$set`` lifecycle fragment for an active-doc
    upsert (caller spreads this into the existing $set)."""
    if now is None:
        now = _utcnow()
    return {
        "active": True,
        "ttl_purge_at": None,
        "stale_reason": None,
        "stale_marked_at": None,
        "updated_at": now,
    }


def lifecycle_set_inactive(
    reason: str = DEFAULT_INACTIVE_REASON_ORPHAN,
    ttl_purge_at: Optional[datetime] = None,
    grace_hours: int = DEFAULT_INACTIVE_GRACE_HOURS,
) -> Dict[str, Any]:
    """Return the ``$set`` lifecycle fragment for marking a doc
    inactive in a bulk op."""
    now = _utcnow()
    if ttl_purge_at is None:
        ttl_purge_at = now + timedelta(hours=grace_hours)
    return {
        "active": False,
        "ttl_purge_at": ttl_purge_at,
        "stale_reason": reason,
        "stale_marked_at": now,
        "updated_at": now,
    }


__all__ = [
    "LIFECYCLE_FIELDS",
    "DEFAULT_INACTIVE_GRACE_HOURS",
    "DEFAULT_INACTIVE_REASON_ORPHAN",
    "DEFAULT_INACTIVE_REASON_EMPTY",
    "stamp_active_board_doc",
    "stamp_inactive_board_doc",
    "normalize_board_doc",
    "is_lifecycle_compliant",
    "missing_lifecycle_fields",
    "lifecycle_set_for_upsert",
    "lifecycle_set_inactive",
]
