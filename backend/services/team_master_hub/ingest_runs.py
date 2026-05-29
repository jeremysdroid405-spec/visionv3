"""
Phase 1.A.3.1 follow-up — read-only audit query for
`team_odds_ingest_runs`.

Returns the latest run rows (sorted by `started_at` desc) with
sensitive fields redacted. NO writes, NO mutations, NO SGO calls.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .collections import COMPOUND_UNIQUE_KEYS  # noqa: F401 — re-export hint

INGEST_RUNS_COLL = "team_odds_ingest_runs"

# Fields excluded from the read response (sensitive or noisy):
#   - `_id`            : Mongo ObjectId, never JSON-safe
#   - `guard_reasons`  : may contain env-var names; redacted to a
#                         single `guard_blocked` boolean instead.
_REDACTED_FIELDS = {"_id", "guard_reasons"}


async def list_ingest_runs(
    db,
    *,
    sport: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 25,
    offset: int = 0,
) -> Dict[str, Any]:
    """Read the latest audit rows. Args are caller-validated.

    Returns:
        {
          "ok": True,
          "n_total": <int — total matching docs in the window>,
          "n_returned": <int>,
          "limit": int, "offset": int,
          "filters": {"sport": str|None, "status": str|None},
          "runs": [<redacted_audit_row>, ...],
        }
    """
    flt: Dict[str, Any] = {}
    if sport:
        flt["sport"] = sport.lower()
    if status:
        flt["status"] = status

    total = await db[INGEST_RUNS_COLL].count_documents(flt)

    runs: List[Dict[str, Any]] = []
    cursor = (
        db[INGEST_RUNS_COLL]
        .find(flt)
        .sort("started_at", -1)
        .skip(int(offset))
        .limit(int(limit))
    )
    async for doc in cursor:
        # Redaction
        guard_blocked = bool(doc.get("guard_reasons"))
        red = {k: v for k, v in doc.items() if k not in _REDACTED_FIELDS}
        red["guard_blocked"] = guard_blocked
        runs.append(red)

    return {
        "ok":         True,
        "n_total":    int(total),
        "n_returned": len(runs),
        "limit":      int(limit),
        "offset":     int(offset),
        "filters":    {"sport": sport, "status": status},
        "runs":       runs,
    }
