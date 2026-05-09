"""
Full 30-day NBA historical ingest driver.

Iterates UTC slate dates → lists events at each slate's noon UTC →
for each (event, window) pair: checkpoint, fetch, persist.

Resumable: skips entries already terminal in `replay_ingest_progress`.
Idempotent: re-running yields zero new writes.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .canary_events import CanaryEvent
from .ingest_odds import (
    REPLAY_ODDS_SNAPSHOTS, REPLAY_PROPS_NORMALIZED,
    ingest_event_window,
)
from .ingest_progress import (
    MAX_RETRIES,
    STATUS_DONE, STATUS_ERROR, STATUS_NOT_AVAILABLE,
    ensure_progress_indexes,
    is_terminal, mark_done, mark_error, mark_in_flight, mark_not_available,
    progress_summary,
)
from .ingest_telemetry import (
    IngestAborted, IngestTelemetry, run_safety_checks,
)
from .markets import (
    REPLAY_BOOK_WHITELIST_PHASE1, REPLAY_NBA_MARKETS,
    REPLAY_REGIONS_PHASE1,
)
from .odds_fetch import SnapshotNotAvailable, fetch_historical_events
from .schema import INDEX_SPECS
from .snapshot_plan import REPLAY_WINDOW_LABELS

logger = logging.getLogger(__name__)


SLATE_LIST_SNAPSHOT_HOUR_UTC = 18  # 1pm ET — events list query time


async def _ensure_all_required_indexes(db) -> None:
    """Create indexes for the 3 collections this driver writes."""
    for coll_name in (REPLAY_ODDS_SNAPSHOTS, REPLAY_PROPS_NORMALIZED):
        coll = db[coll_name]
        for spec in INDEX_SPECS[coll_name]:
            kwargs = {"name": spec["name"]}
            if spec.get("unique"):
                kwargs["unique"] = True
            await coll.create_index(spec["keys"], **kwargs)
    await ensure_progress_indexes(db)


def _slate_iso(slate: datetime) -> str:
    return slate.replace(
        hour=SLATE_LIST_SNAPSHOT_HOUR_UTC, minute=0, second=0,
        microsecond=0,
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _list_slate_events(client, slate_dt: datetime) -> List[CanaryEvent]:
    """Return every NBA event whose commence_time is on `slate_dt` (UTC)."""
    snap_iso = _slate_iso(slate_dt)
    raw = await fetch_historical_events(
        client, sport="basketball_nba", snapshot_iso=snap_iso,
    )
    target_yyyymmdd = slate_dt.strftime("%Y-%m-%d")
    out: List[CanaryEvent] = []
    for e in raw:
        ct_str = e.get("commence_time") or ""
        if ct_str[:10] != target_yyyymmdd and \
                (datetime.fromisoformat(ct_str.replace("Z", "+00:00")) - slate_dt).days != 0:
            # Use the day the event tips off, not the day it's listed.
            # We allow events whose commence_time falls on slate_dt's UTC day.
            if ct_str[:10] != target_yyyymmdd:
                continue
        if not all([e.get("id"), e.get("commence_time"),
                     e.get("home_team"), e.get("away_team")]):
            continue
        out.append({
            "event_id":      e["id"],
            "commence_time": datetime.fromisoformat(
                e["commence_time"].replace("Z", "+00:00")
            ),
            "home_team":     e["home_team"],
            "away_team":     e["away_team"],
        })
    return out


async def _list_slate_events_robust(client, slate_dt: datetime) -> List[CanaryEvent]:
    """Robust version — collects events whose commence_time is on slate_dt OR
    the next UTC day (NBA games tip off late evening ET → next UTC day)."""
    snap_iso = _slate_iso(slate_dt)
    raw = await fetch_historical_events(
        client, sport="basketball_nba", snapshot_iso=snap_iso,
    )
    today_str = slate_dt.strftime("%Y-%m-%d")
    next_str = (slate_dt + timedelta(days=1)).strftime("%Y-%m-%d")
    out: List[CanaryEvent] = []
    for e in raw:
        ct_str = e.get("commence_time") or ""
        if ct_str[:10] not in (today_str, next_str):
            continue
        if not all([e.get("id"), e.get("commence_time"),
                     e.get("home_team"), e.get("away_team")]):
            continue
        out.append({
            "event_id":      e["id"],
            "commence_time": datetime.fromisoformat(
                e["commence_time"].replace("Z", "+00:00")
            ),
            "home_team":     e["home_team"],
            "away_team":     e["away_team"],
        })
    return out


async def run_full_ingest(
    db,
    *,
    client,
    range_start: datetime,
    range_end: datetime,        # inclusive
    sport_key: str = "basketball_nba",
    run_id: str,
    hard_credit_kill_switch: int = 1_000_000,
    safety_check_every_n_calls: int = 200,
    telemetry_every_n_calls: int = 25,
    log_fn=print,
) -> Dict[str, Any]:
    """Drive the full 30-day NBA ingest. Resumable, idempotent, guarded."""
    if range_start.tzinfo is None or range_end.tzinfo is None:
        raise ValueError("range_start and range_end must be tz-aware UTC")

    await _ensure_all_required_indexes(db)

    # Pre-counts for delta reporting.
    pre_snap = await db[REPLAY_ODDS_SNAPSHOTS].count_documents({})
    pre_norm = await db[REPLAY_PROPS_NORMALIZED].count_documents({})

    tel = IngestTelemetry(
        started_credits=client.stats.get("credits_used_session", 0),
    )

    # Walk the date range, list events, enqueue (event, window) work.
    slates: List[datetime] = []
    cur = range_start.replace(hour=0, minute=0, second=0, microsecond=0)
    end = range_end.replace(hour=0, minute=0, second=0, microsecond=0)
    while cur <= end:
        slates.append(cur)
        cur += timedelta(days=1)

    log_fn(f"[full_ingest] range: {slates[0].date()} .. {slates[-1].date()} "
           f"({len(slates)} slates)")

    aborted_reason: Optional[str] = None
    last_safety_at_call = 0
    all_events_seen: List[Dict[str, Any]] = []

    try:
        for slate in slates:
            events = await _list_slate_events_robust(client, slate)
            log_fn(f"[full_ingest] slate {slate.strftime('%Y-%m-%d')}: "
                   f"{len(events)} events")
            for ev in events:
                all_events_seen.append({
                    "event_id":      ev["event_id"],
                    "game_date":     ev["commence_time"].strftime("%Y-%m-%d"),
                    "matchup":       f"{ev['away_team']} @ {ev['home_team']}",
                })
                # Update expected total once we know the slate event count.
                tel.expected_total_calls += len(REPLAY_WINDOW_LABELS)

            for ev in events:
                game_date = ev["commence_time"].strftime("%Y-%m-%d")
                for label in REPLAY_WINDOW_LABELS:
                    # ---- credit kill switch ----
                    if (client.stats["credits_used_session"]
                            >= hard_credit_kill_switch):
                        aborted_reason = (
                            f"CREDIT KILL SWITCH: "
                            f"{client.stats['credits_used_session']} >= "
                            f"{hard_credit_kill_switch}"
                        )
                        raise IngestAborted(aborted_reason)

                    # ---- checkpoint short-circuit ----
                    terminal = await is_terminal(
                        db, sport_key=sport_key,
                        event_id=ev["event_id"],
                        window_label=label,
                    )
                    if terminal in (STATUS_DONE, STATUS_NOT_AVAILABLE):
                        tel.windows_completed += 1
                        continue
                    if terminal == STATUS_ERROR:
                        log_fn(f"[full_ingest] SKIP exhausted-retries "
                               f"event={ev['event_id'][:8]} "
                               f"label={label}")
                        tel.windows_completed += 1
                        continue

                    await mark_in_flight(
                        db, sport_key=sport_key,
                        event_id=ev["event_id"], window_label=label,
                        game_date=game_date, run_id=run_id,
                    )

                    try:
                        res = await ingest_event_window(
                            db, client=client,
                            sport_key=sport_key,
                            event=ev, window_label=label,
                        )
                    except SnapshotNotAvailable:
                        # Already raised inside fetcher? If propagated, mark.
                        await mark_not_available(
                            db, sport_key=sport_key,
                            event_id=ev["event_id"], window_label=label,
                        )
                        tel.calls_total += 1
                        tel.calls_404 += 1
                        tel.windows_completed += 1
                        continue
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("ingest_event_window failed")
                        await mark_error(
                            db, sport_key=sport_key,
                            event_id=ev["event_id"], window_label=label,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                        tel.calls_total += 1
                        tel.calls_error += 1
                        continue

                    tel.calls_total += 1
                    if res.get("snapshot_not_available"):
                        await mark_not_available(
                            db, sport_key=sport_key,
                            event_id=ev["event_id"], window_label=label,
                        )
                        tel.calls_404 += 1
                    else:
                        tel.calls_200 += 1
                        tel.snapshot_inserts += res["snapshot_inserted"]
                        tel.snapshot_modifications += res["snapshot_modified"]
                        tel.normalized_inserts += res["normalized_inserted"]
                        tel.normalized_modifications += res["normalized_modified"]
                        await mark_done(
                            db, sport_key=sport_key,
                            event_id=ev["event_id"], window_label=label,
                            summary={
                                "credits": res["api_credits"],
                                "ins":     res["normalized_inserted"],
                                "mod":     res["normalized_modified"],
                                "books_seen": list(res["book_counts"].keys()),
                                "markets_with_data":
                                    res["markets_with_data"],
                            },
                        )
                    tel.windows_completed += 1

                    # ---- periodic telemetry ----
                    if tel.calls_total % telemetry_every_n_calls == 0:
                        snap = tel.snapshot(
                            client.stats["credits_used_session"])
                        log_fn(f"[telemetry] {snap}")

                    # ---- periodic safety guards ----
                    if (tel.calls_total - last_safety_at_call
                            >= safety_check_every_n_calls):
                        last_safety_at_call = tel.calls_total
                        try:
                            await run_safety_checks(
                                db,
                                allowed_books=set(REPLAY_BOOK_WHITELIST_PHASE1),
                            )
                        except IngestAborted as exc:
                            aborted_reason = str(exc)
                            raise

                tel.events_completed += 1

    except IngestAborted as exc:
        log_fn(f"[full_ingest] ABORTED: {exc}")
    except Exception as exc:  # noqa: BLE001
        aborted_reason = f"UNCAUGHT: {type(exc).__name__}: {exc}"
        log_fn(f"[full_ingest] CRASH: {aborted_reason}")
        logger.exception("driver crashed")

    post_snap = await db[REPLAY_ODDS_SNAPSHOTS].count_documents({})
    post_norm = await db[REPLAY_PROPS_NORMALIZED].count_documents({})
    prog = await progress_summary(db, sport_key=sport_key)

    return {
        "run_id":               run_id,
        "range_start_utc":      range_start.isoformat(),
        "range_end_utc":        range_end.isoformat(),
        "slates_in_range":      len(slates),
        "events_seen_total":    len(all_events_seen),
        "events_seen_unique":   len({e["event_id"] for e in all_events_seen}),
        "telemetry_final":      tel.snapshot(
            client.stats["credits_used_session"]),
        "progress_summary":     prog,
        "aborted_reason":       aborted_reason,
        "totals": {
            "snapshot_docs_in_collection":   post_snap,
            "normalized_rows_in_collection": post_norm,
            "snapshot_docs_inserted":        post_snap - pre_snap,
            "normalized_rows_inserted":      post_norm - pre_norm,
        },
        "api_stats":            dict(client.stats),
    }


__all__ = ["run_full_ingest"]
