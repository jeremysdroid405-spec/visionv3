"""
Phase 1.A.4a — Team event schedule sync worker.

Single-pass worker that fetches every SGO event for a given
`(sport, game_date)` and upserts a `team_matchups` row per event.

  fetch_and_sync(db, *, sport, game_date, api_key, dry_run=True,
                  provider=None) -> audit dict

Hard scope:
  - One date per call (no ranges)
  - No cadence loop, no retries (single SGO fetch + cursor pages)
  - Dispatch guard (`SGO_API_KEY` + `TEAM_INGEST_ENABLED=1`) required
  - Writes ONLY to `team_matchups`. Nothing else is touched.
  - Lenient unresolved-teams: row is still upserted with team_id=None.

Returns a flat summary the CLI and the admin endpoint render verbatim.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pymongo import UpdateOne

from services.team_master_hub.ingest_policy import dispatch_guard_ok
from services.team_master_hub.team_events import (
    build_team_id_lookup,
    normalize_event_to_matchup,
)

from ._sgo_provider import SGOFetchError, SGOPayloadProvider

logger = logging.getLogger("workers.team.team_events_sync")

MATCHUPS_COLL = "team_matchups"


def _build_matchup_upserts(rows: List[Dict[str, Any]]) -> List[UpdateOne]:
    """One upsert per (sport, event_id). `created_at` only on insert."""
    ops: List[UpdateOne] = []
    for r in rows:
        fetched_at = r["fetched_at"]
        filter_doc = {"sport": r["sport"], "event_id": r["event_id"]}
        ops.append(UpdateOne(
            filter_doc,
            {"$set": r,
             "$setOnInsert": {"created_at": fetched_at}},
            upsert=True,
        ))
    return ops


async def fetch_and_sync(
    db,
    *,
    sport: str,
    game_date: str,
    api_key: str,
    dry_run: bool = True,
    provider: Optional[SGOPayloadProvider] = None,
) -> Dict[str, Any]:
    """Fetch + normalize + (optionally) upsert events for one date.

    Returns an audit summary dict — also written to logs. The audit
    is returned even on dispatch-guard failure (just with status set).
    """
    run_id  = str(uuid.uuid4())
    started = datetime.now(timezone.utc)

    # ── Dispatch guard ──
    ok, reasons = dispatch_guard_ok()
    if not ok:
        return {
            "run_id":          run_id,
            "sport":           sport,
            "game_date":       game_date,
            "dry_run":         dry_run,
            "status":          "guard_closed",
            "diagnosis":       "; ".join(reasons) or "dispatch guard closed",
            "started_at":      started,
            "finished_at":     datetime.now(timezone.utc),
            "n_sgo_events":    0,
            "n_normalized":    0,
            "n_unresolved":    0,
            "n_writes":        0,
            "n_upserted":      0,
            "n_modified":      0,
            "n_matched":       0,
            "sgo_endpoints":   [],
            "sample_rows":     [],
        }

    prov = provider or SGOPayloadProvider(api_key)
    try:
        fetched = prov.fetch_events_by_date(
            sport=sport, game_date=game_date)
    except SGOFetchError as exc:
        return {
            "run_id":         run_id,
            "sport":          sport,
            "game_date":      game_date,
            "dry_run":        dry_run,
            "status":         "sgo_failure",
            "diagnosis":      str(exc),
            "started_at":     started,
            "finished_at":    datetime.now(timezone.utc),
            "n_sgo_events":   0,
            "n_normalized":   0,
            "n_unresolved":   0,
            "n_writes":       0,
            "n_upserted":     0,
            "n_modified":     0,
            "n_matched":      0,
            "sgo_endpoints":  [],
            "sample_rows":    [],
        }

    raw_events = fetched.get("events") or []
    sgo_endpoints: List[str] = list(fetched.get("sgo_endpoints") or [])
    primary_endpoint = sgo_endpoints[0] if sgo_endpoints else None

    # ── Resolve teams via master hub ──
    name_to_tid = await build_team_id_lookup(db, sport=sport)

    rows: List[Dict[str, Any]] = []
    n_unresolved = 0
    for ev in raw_events:
        if not isinstance(ev, dict):
            continue
        row = normalize_event_to_matchup(
            ev, sport=sport, team_id_lookup=name_to_tid,
            fetched_at=started, source_endpoint=primary_endpoint,
        )
        if row is None:
            continue
        if row.get("unresolved_teams"):
            n_unresolved += len(row["unresolved_teams"])
        rows.append(row)

    # ── Write (or skip) ──
    n_upserted = 0
    n_modified = 0
    n_matched  = 0
    status = "dry_run"
    diagnosis = "dry_run mode — no rows written"
    if not dry_run and rows:
        try:
            ops = _build_matchup_upserts(rows)
            result = await db[MATCHUPS_COLL].bulk_write(ops, ordered=False)
            n_upserted = len(result.upserted_ids or {})
            n_modified = int(result.modified_count or 0)
            n_matched  = int(result.matched_count or 0)
            status = "succeeded"
            diagnosis = (
                f"upserted={n_upserted}, modified={n_modified}, "
                f"matched={n_matched}"
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[team_events_sync] write failed")
            status = "errored"
            diagnosis = f"bulk_write failed: {exc}"
    elif not dry_run and not rows:
        status = "succeeded_empty"
        diagnosis = "no rows to write"

    finished = datetime.now(timezone.utc)
    sample = [
        {k: v for k, v in r.items() if k != "status_raw"}
        for r in rows[:3]
    ]
    return {
        "run_id":         run_id,
        "sport":          sport,
        "game_date":      game_date,
        "dry_run":        dry_run,
        "status":         status,
        "diagnosis":      diagnosis,
        "started_at":     started,
        "finished_at":    finished,
        "duration_ms":    int(
            (finished - started).total_seconds() * 1000),
        "n_sgo_events":   len(raw_events),
        "n_normalized":   len(rows),
        "n_unresolved":   n_unresolved,
        "n_writes":       len(rows) if not dry_run else 0,
        "n_upserted":     n_upserted,
        "n_modified":     n_modified,
        "n_matched":      n_matched,
        "sgo_endpoints":  sgo_endpoints,
        "sample_rows":    sample,
    }


__all__ = ["fetch_and_sync"]
