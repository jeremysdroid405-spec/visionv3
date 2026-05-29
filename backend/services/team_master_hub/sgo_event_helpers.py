"""
Shared SGO event-field accessors — Phase 1.A.3.5b.

Single source of truth for "where does SGO put X on the event object?"
so the team-props pipeline and player-props pipeline never drift apart.

The first canonical helper here resolves the **event start timestamp**.
Order matches the proven lookup chain in
`/app/backend/scripts/sgo/ingest_historical_player_stats.py` (lines
1198-1199), which has been correctly reading SGO v2 since launch:

    1. event.status.startsAt    (primary — where SGO v2 actually puts it)
    2. event.startTime          (fallback — older camelCase shape)
    3. event.commenceTime       (fallback — sportsbook-vendor variant)
    4. event.commence_time      (fallback — snake_case synthetic shape
                                  used by some fixtures / older tests)

`startsAt` at the TOP level (no `status` parent) is also accepted as
a tertiary fallback — some synthetic test payloads put it there.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict


def extract_event_start_iso(ev: Dict[str, Any]) -> str:
    """Return an ISO timestamp string for the event's start, or '' if
    none of the canonical SGO locations carry it.

    Pure function — never raises, never reaches network or DB. Returns
    "" (not None) so downstream string operations remain safe.
    """
    if not isinstance(ev, dict):
        return ""

    status = ev.get("status")
    if isinstance(status, dict):
        v = status.get("startsAt")
        if isinstance(v, str) and v:
            return v

    for key in ("startTime", "commenceTime", "commence_time", "startsAt"):
        v = ev.get(key)
        if isinstance(v, str) and v:
            return v
    return ""


def derive_game_date(commence_iso: str) -> str | None:
    """Return UTC date as 'YYYY-MM-DD' from an ISO string, or None.

    Tolerates trailing `Z` and `+00:00` offsets. Returns None on any
    parse failure — caller decides whether to backfill from elsewhere.
    """
    if not commence_iso or not isinstance(commence_iso, str):
        return None
    try:
        return datetime.fromisoformat(
            commence_iso.replace("Z", "+00:00")
        ).astimezone(timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return None


__all__ = ["extract_event_start_iso", "derive_game_date"]
