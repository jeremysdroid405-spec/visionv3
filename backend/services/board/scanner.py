"""
Universal Game-Start Scanner
=============================
Every 60 seconds, for every registered sport, flips props whose games
have tipped off to `active=False`. The universal board reader filters
out `active=False` rows, so as soon as a prop is flipped its slot on the
visible board is backfilled by the next-best active prop in the same
tier — no publish / rebuild required.

Sport-agnostic by design: iterates `registered_sports()`, uses each
adapter's `scores_collection`. Adding a new sport auto-joins the scan.

Single indexed update_many per sport per tick (`idx_game_start_active`
covers the filter). Runtime is sub-10ms per sport when no props need
flipping.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from services.board.adapters import get_adapter, registered_sports

logger = logging.getLogger(__name__)


# Module-level in-memory stats. Read by observability endpoints.
_STATS: Dict[str, Dict[str, Any]] = {}


async def scan_sport(db, sport: str) -> Dict[str, Any]:
    """Flip props whose games have tipped off to active=False. Returns
    a small stats dict per sport."""
    adapter = get_adapter(sport)
    now_utc = datetime.now(timezone.utc)
    result = await db[adapter.scores_collection].update_many(
        {
            "active": True,
            "game_start_utc": {"$ne": None, "$lte": now_utc},
        },
        {"$set": {
            "active": False,
            "inactive_reason": "game_started",
            "active_changed_at": now_utc,
        }},
    )
    stats = {
        "last_scan_at": now_utc.isoformat(),
        "last_flips": int(getattr(result, "modified_count", 0) or 0),
        "matched": int(getattr(result, "matched_count", 0) or 0),
    }
    _STATS[sport] = stats
    if stats["last_flips"]:
        logger.info(
            f"[GAME_START_SCANNER] {sport}: "
            f"{stats['last_flips']} props → inactive (game_started)"
        )
    return stats


async def scan_all(db) -> Dict[str, Dict[str, Any]]:
    """One pass across every registered sport. Invoked by the 60s
    interval job registered in server.py."""
    out: Dict[str, Dict[str, Any]] = {}
    for sport in registered_sports():
        try:
            out[sport] = await scan_sport(db, sport)
        except Exception as e:
            logger.exception(
                f"[GAME_START_SCANNER] {sport}: scan failed: {e}"
            )
            out[sport] = {"error": str(e)}
    return out


def stats_snapshot() -> Dict[str, Dict[str, Any]]:
    """Read-only copy of per-sport scanner stats (for /api/board-stats)."""
    return {k: dict(v) for k, v in _STATS.items()}
