"""
Delta Engine — Per-Sport Watermark Store
========================================
Phase D1 (2026-04-21): Persistent per-sport "last tick timestamp" cursor
for the PropVision near-real-time Delta Engine.

DESIGN INVARIANTS (do not violate):
  - Read-only detection layer in D1. Watermarks are *read* by the inspect
    endpoint and will be *advanced* only after a successful rescore+rebalance
    pass (D3+). D1 NEVER advances the watermark — first-tick-after-fullsync
    semantics are preserved for the plan's set-diff contract.
  - Sport-agnostic. No sport-specific branching here. Every sport gets the
    same schema; adding a sport requires zero edits to this module.
  - This module does NOT import from universal_odds_sync, BDL fetchers, or
    any upstream network client. Enforced by the delta-path lint guard
    (see /app/backend/tests/test_delta_upstream_isolation.py).

Schema — MongoDB collection `delta_watermarks`:
    {
        "_id":             <sport>,                  # string primary key
        "sport":           <sport>,
        "last_tick_utc":   datetime (UTC) | None,    # advanced by D3+
        "created_at":      datetime (UTC),
        "updated_at":      datetime (UTC),
    }
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

WATERMARK_COLLECTION = "delta_watermarks"

# 5-second grace window on the watermark read (plan §11 mitigation for
# clock drift / idempotent double-processing).
WATERMARK_GRACE_SECONDS = 5


async def get_watermark(db, sport: str) -> Optional[datetime]:
    """Return the last-tick timestamp for `sport`, or None if never ticked."""
    doc = await db[WATERMARK_COLLECTION].find_one(
        {"_id": sport}, {"_id": 0, "last_tick_utc": 1}
    )
    if not doc:
        return None
    ts = doc.get("last_tick_utc")
    if isinstance(ts, datetime) and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


async def get_watermark_with_grace(db, sport: str) -> Optional[datetime]:
    """Watermark shifted back by `WATERMARK_GRACE_SECONDS` for diff queries."""
    ts = await get_watermark(db, sport)
    if ts is None:
        return None
    from datetime import timedelta
    return ts - timedelta(seconds=WATERMARK_GRACE_SECONDS)


async def advance_watermark(db, sport: str, ts: Optional[datetime] = None) -> datetime:
    """Advance the watermark for `sport` to `ts` (defaults to now).

    NOTE — D1 scope: this function is a no-op from the inspect-endpoint
    perspective. It only becomes live from D3 onwards after a successful
    rescore+rebalance pass. Exposed now so later phases don't have to add it.
    """
    if ts is None:
        ts = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    await db[WATERMARK_COLLECTION].update_one(
        {"_id": sport},
        {
            "$set": {
                "sport": sport,
                "last_tick_utc": ts,
                "updated_at": datetime.now(timezone.utc),
            },
            "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
        },
        upsert=True,
    )
    return ts


async def describe_watermarks(db) -> Dict[str, Any]:
    """Return a summary of all per-sport watermarks (diagnostic helper)."""
    out: Dict[str, Any] = {}
    async for doc in db[WATERMARK_COLLECTION].find({}, {"_id": 0}):
        out[doc.get("sport")] = {
            "last_tick_utc": doc.get("last_tick_utc"),
            "updated_at": doc.get("updated_at"),
        }
    return out
