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
from services.board.set_active import set_active

logger = logging.getLogger(__name__)


# Module-level in-memory stats. Read by observability endpoints.
_STATS: Dict[str, Dict[str, Any]] = {}


async def scan_sport(db, sport: str) -> Dict[str, Any]:
    """Flip props whose games have tipped off to active=False. Returns
    a small stats dict per sport.

    SSOT enforcement (2026-05-04): routes through `services.board.set_active`
    so the `active` field on `{sport}_prop_scores` has exactly one
    canonical writer path. See FIELD_OWNERSHIP.md:active.
    """
    now_utc = datetime.now(timezone.utc)
    # Scope: any currently-active doc whose game_start_utc is in the
    # past. `canonical_keys=None` + `extra_filter` drives the scope so
    # we don't pre-enumerate keys (the scanner runs every 60s and
    # usually matches 0 rows).
    result = await set_active(
        db,
        sport=sport,
        canonical_keys=None,
        active=False,
        reason="game_started",
        extra_filter={"game_start_utc": {"$ne": None, "$lte": now_utc}},
        emit_audit=True,
    )
    stats = {
        "last_scan_at": now_utc.isoformat(),
        "last_flips":   int(result.get("modified", 0) or 0),
        "matched":      int(result.get("matched", 0) or 0),
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
