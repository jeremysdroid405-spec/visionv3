"""
Delta Publisher — universal `new_props` event emission.

Used by sport odds-sync paths to emit `BoardEvent('new_props', ...)`
for JUST the canonical_keys that are net-new compared to a pre-sync
snapshot. Keeps the event-bus free of full-wipe floods while the
48h Step 6 A/B observation window runs.

Usage:

    from services.board.delta_publisher import (
        capture_live_props_keys, publish_new_props_delta,
    )

    pre = await capture_live_props_keys(db, 'nba')
    # … odds sync wipes + reinserts live_props …
    post = await capture_live_props_keys(db, 'nba')
    await publish_new_props_delta(
        sport='nba', pre_keys=pre, post_keys=post,
        source='odds_sync_service',
    )

`capture_live_props_keys` uses the board adapter's hot-path
`canonical_key(prop)` to stay consistent with the engine's own
filter logic. Pure read; no mutation.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from services.board.adapters import get_adapter, registered_sports
from services.event_bus import BoardEvent, get_event_bus

logger = logging.getLogger(__name__)


# 2026-04-29 — guardrail raised from 500 → 5000.
# The original 500 cap was put in place during the 48h Step 6 A/B
# observation window (now retired) to keep the realtime path off the
# event bus during full slate rollovers. With dual-write live
# (canonical + shadow) and master_sync's hourly rebuild as the
# safety net, the realtime path is the PRIMARY surface — slate
# rollovers (typically 800–1500 new keys for NBA, 1500–3000 for MLB)
# MUST score immediately so newly posted props reach the live tier
# system within seconds, not at the next hourly rebuild.
#
# Sanity check: 5,000 keys × ~30ms scoring = ~150s wall time, still
# inside the engine's per-event budget. Anything genuinely larger
# (e.g., > one full slate's worth) is almost certainly a corruption
# event and should fall through to the rebuild — hence the cap is
# raised, not removed.
_MAX_DELTA_FOR_REALTIME = 5000


async def capture_live_props_keys(
    db,
    sport: str,
    limit: Optional[int] = None,
) -> Set[str]:
    """Read the current set of canonical_keys from `{sport}_live_props`.

    Returns an empty set on error (never raises). Safe to call both
    BEFORE a wipe (pre-snapshot) and AFTER an insert (post-snapshot).
    """
    try:
        adapter = get_adapter(sport)
    except Exception as e:
        logger.warning(f"[DELTA_PUB] {sport}: unknown sport, skip capture: {e}")
        return set()

    coll = db[adapter.live_props_collection]
    keys: Set[str] = set()
    try:
        projection = {"_id": 0}
        cursor = coll.find({}, projection)
        if limit:
            cursor = cursor.limit(int(limit))
        async for prop in cursor:
            ck = adapter.canonical_key(prop)
            if ck:
                keys.add(ck)
    except Exception as e:
        logger.warning(
            f"[DELTA_PUB] {sport}: capture_live_props_keys failed: {e}"
        )
    return keys


async def publish_new_props_delta(
    sport: str,
    pre_keys: Set[str],
    post_keys: Set[str],
    source: str = "odds_sync",
    max_delta: int = _MAX_DELTA_FOR_REALTIME,
) -> Dict[str, Any]:
    """Compute pre→post delta and emit BoardEvent('new_props', ...)
    for the net-new keys. Returns a small summary dict.

    Guardrails:
      - Empty delta → no event emitted (no-op).
      - Delta above `max_delta` threshold → no event emitted. The
        legacy full-rebuild coordinator handles slate-wide refreshes.
        The `reason='delta_too_large'` summary makes this auditable.
    """
    sport_l = (sport or "").strip().lower()
    pre = set(pre_keys or [])
    post = set(post_keys or [])
    added = post - pre
    removed = pre - post
    summary: Dict[str, Any] = {
        "sport": sport_l,
        "pre_count": len(pre),
        "post_count": len(post),
        "added": len(added),
        "removed": len(removed),
        "emitted": False,
        "reason": None,
        "source": source,
    }

    if not added:
        summary["reason"] = "no_new_props"
        logger.info(
            f"[DELTA_PUB] {sport_l} src={source} "
            f"pre={len(pre)} post={len(post)} added=0 removed={len(removed)} "
            f"→ event skipped (no_new_props)"
        )
        return summary

    if len(added) > max_delta:
        summary["reason"] = "delta_too_large"
        logger.warning(
            f"[DELTA_PUB] {sport_l} src={source} "
            f"pre={len(pre)} post={len(post)} added={len(added)} "
            f"> max={max_delta} → event skipped (delta_too_large); "
            f"full-rebuild coordinator will handle"
        )
        return summary

    try:
        await get_event_bus().publish(BoardEvent(
            sport=sport_l,
            event_type="new_props",
            source=source,
            metadata={"canonical_keys": sorted(added)},
        ))
        summary["emitted"] = True
        summary["reason"] = "emitted"
        logger.info(
            f"[DELTA_PUB] {sport_l} src={source} "
            f"pre={len(pre)} post={len(post)} added={len(added)} "
            f"removed={len(removed)} → BoardEvent(new_props) published"
        )
    except Exception as e:
        summary["reason"] = f"publish_failed: {e}"
        logger.exception(
            f"[DELTA_PUB] {sport_l}: publish_failed: {e}"
        )
    return summary


__all__ = [
    "capture_live_props_keys",
    "publish_new_props_delta",
]
