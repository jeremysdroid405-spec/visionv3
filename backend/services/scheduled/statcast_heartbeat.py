"""
Statcast Ingest Heartbeat — 2026-05-16 (Operational PR C)
=========================================================
Surfaces silent-failure cases for the daily Statcast pipeline.

Why this exists
---------------
Before this module, the daily pipeline could complete "ok" while
`02_statcast_ingest` returned `{scanned: 0, inserted: 0}`. That state
is structurally indistinguishable from "MLB had no games yesterday" —
but it is also exactly what happened on every run between 4/29 – 5/4
(Baseball Savant had not yet posted at 04:00 UTC). The pipeline marched
on, the rolling features re-computed against a frozen window, and the
batter strikeouts model produced inverted projections for 10+ days.

Contract
--------
Every Statcast ingest call writes ONE row to `statcast_ingest_heartbeat`
and one row to `sync_history`. The status is one of:

  ok       — scanned > 0 OR the window is genuinely game-less (off day)
  warning  — scanned == 0 for a window that should have had games AND
              the previous run was ok. Single anomaly. Likely transient.
  error    — scanned == 0 for two consecutive runs. Probably structural
              (Savant outage, pybaseball break, cron-timing regression).

Off-day detection
-----------------
We do NOT call the MLB schedule API to decide "should this day have had
games." Instead we use a cheap heuristic:

  - The MLB regular season runs roughly mar 28 → sep 30 in any given
    year. The post-season runs oct 1 → early nov.
  - Within that range, every calendar day except the All-Star break
    has at least one game. Spring training is excluded.

We treat any day outside `[03-25, 11-05]` as "off day" → status=ok even
when scanned == 0. Conservative: false-positive warnings during the
shoulder weeks (late March / early November) are acceptable and self-
clear once games return.

Read path
---------
`get_recent_heartbeats(db, n)` returns the n most recent entries sorted
newest-first. Surfaced by `/api/admin/mlb/statcast-health`.

NO model / scoring / gate / threshold / tier / vision change.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

HEARTBEAT_COLL = "statcast_ingest_heartbeat"

# Regular-season + early postseason window (UTC dates expressed as
# month-day tuples). Anything outside this range is treated as an MLB
# off day for scanned==0 evaluation.
_SEASON_START = (3, 25)
_SEASON_END = (11, 5)


def _is_in_mlb_season(d: date) -> bool:
    """True if `d` falls inside the rough regular-season + early
    postseason window. Conservative — true positives only."""
    md = (d.month, d.day)
    return _SEASON_START <= md <= _SEASON_END


async def _previous_status(db, *, exclude_id: Optional[str] = None) -> Optional[str]:
    """Returns the status of the immediately-prior heartbeat, or None
    when this is the first row. Used to detect 2-in-a-row failures."""
    q: Dict[str, Any] = {}
    if exclude_id is not None:
        from bson import ObjectId
        try:
            q["_id"] = {"$ne": ObjectId(exclude_id)}
        except Exception:  # noqa: BLE001
            pass
    doc = await db[HEARTBEAT_COLL].find_one(
        q, sort=[("completed_at", -1)], projection={"_id": 0, "status": 1},
    )
    return (doc or {}).get("status")


def _classify(
    *, scanned: int, start: str, end: str, prev_status: Optional[str],
) -> Tuple[str, str]:
    """Return (status, reason). Pure function — no DB access."""
    if scanned > 0:
        return "ok", f"ingested {scanned} rows"

    # scanned == 0. Off-season window? Treat as benign.
    try:
        d_end = date.fromisoformat(end)
    except ValueError:
        return "warning", f"scanned==0 and end={end!r} is unparseable"

    if not _is_in_mlb_season(d_end):
        return "ok", f"scanned==0 but {end} is outside MLB season window"

    # In-season + scanned == 0. If the previous run was also a non-ok
    # scanned==0, escalate.
    if prev_status in ("warning", "error"):
        return "error", (
            f"scanned==0 for {start}..{end} AND previous run was "
            f"status={prev_status!r} — 2+ consecutive zero-row runs"
        )
    return "warning", (
        f"scanned==0 for {start}..{end} but expected games "
        f"(in-season window)"
    )


async def record_heartbeat(
    db,
    *,
    start: str,
    end: str,
    scanned: int,
    inserted: int,
    updated: int,
    errors: int,
    job_id: str = "mlb_daily_pipeline",
) -> Dict[str, Any]:
    """Persist a heartbeat row and, when status != ok, log a clear
    WARNING / ERROR line + emit a `sync_history` row.

    Returns the heartbeat document (with status / reason fields)."""
    prev = await _previous_status(db)
    status, reason = _classify(
        scanned=scanned, start=start, end=end, prev_status=prev,
    )
    completed_at = datetime.now(timezone.utc)
    doc: Dict[str, Any] = {
        "job_id": job_id,
        "step": "02_statcast_ingest",
        "start_date": start,
        "end_date": end,
        "scanned": int(scanned or 0),
        "inserted": int(inserted or 0),
        "updated": int(updated or 0),
        "errors": int(errors or 0),
        "completed_at": completed_at,
        "status": status,
        "reason": reason,
        "previous_status": prev,
    }

    try:
        await db[HEARTBEAT_COLL].insert_one(dict(doc))
        # Strip ObjectId so the response document is BSON-safe.
        doc.pop("_id", None)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[STATCAST_HEARTBEAT] failed to persist heartbeat: %s "
            "(payload=%s)", exc, doc,
        )

    if status == "ok":
        logger.info(
            "[STATCAST_HEARTBEAT] %s..%s scanned=%d inserted=%d "
            "updated=%d errors=%d status=ok",
            start, end, scanned, inserted, updated, errors,
        )
    elif status == "warning":
        logger.warning(
            "[STATCAST_HEARTBEAT] %s..%s scanned==0 in-season — %s "
            "(prev=%s). Pipeline will continue but the rolling-feature "
            "rebuild is now operating on stale raw data.",
            start, end, reason, prev,
        )
    else:  # error
        logger.error(
            "[STATCAST_HEARTBEAT] %s..%s scanned==0 for 2+ consecutive "
            "runs — %s. Statcast ingest is structurally broken. "
            "Investigate pybaseball / Baseball Savant / cron timing. "
            "Run `python -m scripts.mlb_statcast_ingest --start <date>` "
            "manually to confirm upstream availability.",
            start, end, reason,
        )

    # Also append to sync_history so the existing operator dashboards
    # (which already read sync_history) surface the alert without any
    # new wiring.
    try:
        await db.sync_history.insert_one({
            "job_id": "statcast_ingest_heartbeat",
            "completed_at": completed_at,
            "status": status,
            "reason": reason,
            "start_date": start,
            "end_date": end,
            "scanned": int(scanned or 0),
            "inserted": int(inserted or 0),
            "updated": int(updated or 0),
            "errors": int(errors or 0),
            "previous_status": prev,
        })
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[STATCAST_HEARTBEAT] failed to write sync_history entry: %s",
            exc,
        )

    return doc


async def get_recent_heartbeats(db, *, n: int = 20) -> List[Dict[str, Any]]:
    """Return the n most-recent heartbeat rows, newest first."""
    cursor = db[HEARTBEAT_COLL].find(
        {}, projection={"_id": 0}, sort=[("completed_at", -1)], limit=int(n),
    )
    out: List[Dict[str, Any]] = []
    async for d in cursor:
        out.append(d)
    return out


async def get_health_summary(db) -> Dict[str, Any]:
    """Aggregate status for the admin endpoint. Worst-case status of
    the last 3 heartbeats wins ('error' > 'warning' > 'ok'). Also
    surfaces the freshest game_date in `mlb_statcast_raw` as a sanity
    cross-check."""
    recent = await get_recent_heartbeats(db, n=10)
    last3 = recent[:3]
    rank = {"ok": 0, "warning": 1, "error": 2}
    overall = "ok"
    for h in last3:
        if rank.get(h.get("status", "ok"), 0) > rank.get(overall, 0):
            overall = h["status"]

    newest_raw = await db.mlb_statcast_raw.find_one(
        {}, sort=[("game_date", -1)],
        projection={"_id": 0, "game_date": 1},
    )
    newest_features = await db.mlb_statcast_player_features.find_one(
        {}, sort=[("game_date", -1)],
        projection={"_id": 0, "game_date": 1},
    )

    return {
        "overall_status": overall,
        "consecutive_failures": _count_consecutive_failures(recent),
        "newest_raw_game_date": (newest_raw or {}).get("game_date"),
        "newest_feature_game_date": (newest_features or {}).get("game_date"),
        "heartbeats_inspected": len(last3),
        "recent_heartbeats": recent,
    }


def _count_consecutive_failures(recent: List[Dict[str, Any]]) -> int:
    """Walk from newest backwards and count how many leading entries
    have status != 'ok'."""
    n = 0
    for h in recent:
        if h.get("status") == "ok":
            break
        n += 1
    return n
