"""
Delta Engine — Shared Tiering Helpers
======================================
Phase D3 (2026-04-21). Sport-agnostic tier-rebalance helpers shared by
the full-sync pipeline and the near-real-time delta engine.

DESIGN CONTRACT
---------------
Per-prop `tier` assignment is produced INSIDE the scoring stack via the
Universal Gate Engine (`services.scoring.gates.UniversalGateEngine`,
invoked from `compute_scoring_stack → compute_tier`). Both full-sync
and delta paths converge on that single code path.

What a delta engine's tier rebalance DOES need is a single place to
handle the RETIRED signal: when a prop's live row flips `active=False`
(game start, line pull, injury scratch), the already-scored RT doc for
that prop must be marked inactive so the Ferrari endpoints
(`{active: True, tier: ...}` query) promote the next qualified pick
into its slot.

This module is deliberately thin — it exists so both pipelines (and
future sports) import ONE reference for tier eviction semantics rather
than each reimplementing a mark-inactive loop.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable

from services.board.set_active import set_active
from services.config.collection_names import COLL

logger = logging.getLogger(__name__)


async def mark_retired_inactive(
    db,
    sport: str,
    version_tag: str,
    retired_keys: Iterable[str],
    reason: str = "retired_by_delta_engine",
) -> Dict[str, Any]:
    """Flip `active=False` on scored RT docs whose live prop retired.

    SSOT enforcement (2026-05-04): this function now routes the write
    through the single canonical helper `services.board.set_active` so
    the `active` field on `{sport}_prop_scores` has exactly one
    allowed writer path. See FIELD_OWNERSHIP.md:active.

    Parameters & return: unchanged (back-compatible) — still returns
    `{matched, modified, keys_processed}`.
    """
    keys = list(retired_keys)
    if not keys:
        return {"matched": 0, "modified": 0, "keys_processed": 0}

    result = await set_active(
        db,
        sport=sport,
        canonical_keys=keys,
        active=False,
        reason=reason,
        version_tag=version_tag,
    )
    logger.info(
        f"[DELTA_TIERING:{sport}] mark_retired_inactive version='{version_tag}' "
        f"keys={len(keys)} matched={result['matched']} modified={result['modified']}"
    )
    return {
        "matched":        result["matched"],
        "modified":       result["modified"],
        "keys_processed": result["keys_processed"],
    }


async def get_tier_distribution(db, sport: str, version_tag: str) -> Dict[str, int]:
    """Observability helper: return `{tier: active_count}` for a version tag."""
    coll = db[COLL("prop_scores", sport)]
    pipeline = [
        {"$match": {"version_tag": version_tag, "active": {"$ne": False}}},
        {"$group": {"_id": "$tier", "n": {"$sum": 1}}},
    ]
    out: Dict[str, int] = {}
    async for row in coll.aggregate(pipeline):
        out[row.get("_id") or "unknown"] = row.get("n", 0)
    return out


__all__ = ["mark_retired_inactive", "get_tier_distribution"]
