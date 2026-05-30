"""
Phase 1.A.4.acquire / Phase 4 — NFL player-prop historical ingest.

Walks a UTC date window via SGO `/v2/events` and writes
`nfl_player_historical_props` (one row per per-book per-line outcome).

Acquire-all semantics: NO stat-family filter. NO market_key filter.
The only filter is the entity filter (player-level vs team-level)
implemented in the normalizer (`_normalize_player.py`).

Same proven pattern as `historical_ingest.py`:
  - Streaming flush every FLUSH_PROPS rows → bounded memory
  - run_id-scoped audit in `historical_acquire_runs`
  - Dispatch guard required (`SGO_API_KEY` + `TEAM_INGEST_ENABLED=1`)
  - DRY-RUN by default; live writes require `dry_run=False`
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pymongo import InsertOne, UpdateOne
from pymongo.errors import BulkWriteError

from services.team_master_hub.ingest_policy import (
    dispatch_guard_ok,
    is_book_blocked,
    is_book_reference_only,
)
from services.team_master_hub.team_events import (
    build_team_id_lookup,
    normalize_event_to_matchup,
)

from ._normalize_player import normalize_player_payload
from ._sgo_provider import SGOFetchError, SGOPayloadProvider

logger = logging.getLogger("workers.team.historical_player_ingest")

PLAYER_HIST_COLL = "nfl_player_historical_props"
AUDIT_COLL       = "historical_acquire_runs"


def _daterange_inclusive(start: str, end: str) -> List[str]:
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    if d1 < d0:
        raise ValueError(f"end_date {end!r} < start_date {start!r}")
    out: List[str] = []
    cur = d0
    while cur <= d1:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _apply_book_policy_in_place(
    rows: List[Dict[str, Any]],
) -> Dict[str, int]:
    blocked = 0
    refs    = 0
    kept: List[Dict[str, Any]] = []
    for r in rows:
        book = r.get("book", "")
        if is_book_blocked(book):
            blocked += 1
            continue
        r["reference_only"] = is_book_reference_only(book)
        if r["reference_only"]:
            refs += 1
        kept.append(r)
    rows[:] = kept
    return {"n_blocked": blocked, "n_refs": refs}


def _build_player_inserts(
    rows: List[Dict[str, Any]],
) -> List[InsertOne]:
    """Phase 4-fast: pure InsertOne ops (no upsert). Combined with the
    compound unique index + ordered=False, duplicates are silently
    skipped at the index layer. ~10× faster than UpdateOne(upsert=True)
    because there's no pre-write index lookup.
    """
    ops: List[InsertOne] = []
    for r in rows:
        # InsertOne expects the full doc — including `ingested_at`
        # (which UpdateOne kept under `$setOnInsert`).
        ops.append(InsertOne(dict(r)))
    return ops


def _build_player_upserts(
    rows: List[Dict[str, Any]],
) -> List[UpdateOne]:
    ops: List[UpdateOne] = []
    for r in rows:
        ingested_at = r.pop("ingested_at", None)
        filter_doc = {
            "event_id":     r["event_id"],
            "player_id":    r["player_id"],
            "market":       r["market"],
            "line":         r["line"],
            "side":         r["side"],
            "book":         r["book"],
            "snapshot_iso": r["snapshot_iso"],
        }
        ops.append(UpdateOne(
            filter_doc,
            {"$set": r,
             "$setOnInsert": {"ingested_at": ingested_at}},
            upsert=True,
        ))
    return ops


async def acquire_player_historical_window(
    db,
    *,
    sport: str,
    start_date: str,
    end_date: str,
    api_key: str,
    dry_run: bool = True,
    provider: Optional[SGOPayloadProvider] = None,
    write_mode: str = "insert",   # "insert" (fast) | "upsert" (idempotent)
) -> Dict[str, Any]:
    """Walk [start_date, end_date] UTC inclusive and upsert NFL
    player-prop rows. NFL-only in Phase 4 (other sports may be added
    once their player-master tables exist).
    """
    sport_l = sport.lower()
    if sport_l != "nfl":
        raise ValueError(
            f"Phase 4 supports nfl only (got {sport!r}). "
            "MLB/NBA player-prop ingest is a separate slice.")

    dates   = _daterange_inclusive(start_date, end_date)
    run_id  = str(uuid.uuid4())
    started = datetime.now(timezone.utc)
    snap_iso = started.isoformat()
    league  = "NFL"

    # ── Dispatch guard ──
    ok, reasons = dispatch_guard_ok()
    if not ok:
        audit = {
            "run_id":          run_id,
            "sport":           sport_l,
            "kind":            "player_historical",
            "hist_coll":       PLAYER_HIST_COLL,
            "start_date":      start_date,
            "end_date":        end_date,
            "n_dates":         len(dates),
            "dry_run":         dry_run,
            "status":          "guard_closed",
            "diagnosis":       "; ".join(reasons) or "dispatch guard closed",
            "started_at":      started,
            "finished_at":     datetime.now(timezone.utc),
            "n_sgo_pages":     0, "n_sgo_events": 0,
            "n_props_normalized": 0,
            "n_props_written":    0,
            "n_props_upserted":   0,
            "n_props_modified":   0,
            "n_blocked":          0, "n_refs": 0,
            "stat_families":      {},
            "per_date_counts":    {},
            "sample_endpoints":   [],
        }
        await db[AUDIT_COLL].insert_one(dict(audit))
        audit.pop("_id", None)
        return audit

    prov = provider or SGOPayloadProvider(api_key)

    # Streaming flush thresholds (proven OOM-safe for MLB-scale runs)
    FLUSH_PROPS = 50_000

    n_sgo_pages = 0
    n_sgo_events = 0
    n_props_norm = 0
    n_props_written  = 0
    n_props_upserted = 0
    n_props_modified = 0
    n_props_duplicates = 0
    n_blocked = 0
    n_refs    = 0
    stat_families: Dict[str, int] = {}
    per_date_counts: Dict[str, int] = {}
    sample_endpoints: List[str] = []

    props_ops: List[Any] = []

    async def _flush(force: bool = False) -> None:
        nonlocal n_props_upserted, n_props_modified, n_props_written
        nonlocal n_props_duplicates
        if not dry_run:
            if props_ops and (force or len(props_ops) >= FLUSH_PROPS):
                CHUNK = 1000
                slab_total = len(props_ops)
                for i in range(0, slab_total, CHUNK):
                    slab = props_ops[i:i + CHUNK]
                    try:
                        rr = await db[PLAYER_HIST_COLL].bulk_write(
                            slab, ordered=False)
                        if write_mode == "insert":
                            n_props_upserted += int(rr.inserted_count or 0)
                        else:
                            n_props_upserted += len(rr.upserted_ids or {})
                            n_props_modified += int(rr.modified_count or 0)
                    except BulkWriteError as bwe:
                        # Insert mode: duplicate-key errors are expected
                        # on re-runs. Count them and continue.
                        details = bwe.details or {}
                        n_dup = sum(1 for w in details.get(
                            "writeErrors", []) if w.get("code") == 11000)
                        n_other = len(details.get("writeErrors", [])) - n_dup
                        n_inserted = int(details.get("nInserted") or 0)
                        n_props_upserted   += n_inserted
                        n_props_duplicates += n_dup
                        if n_other > 0:
                            logger.warning(
                                "[player_hist] %d non-duplicate write "
                                "errors in this chunk",
                                n_other,
                            )
                n_props_written += slab_total
                props_ops.clear()
        else:
            if props_ops and (force or len(props_ops) >= FLUSH_PROPS):
                n_props_written += len(props_ops)
                props_ops.clear()

    try:
        for game_date in dates:
            try:
                fetched = prov.fetch_events_by_date(
                    sport=sport_l, game_date=game_date)
            except SGOFetchError as exc:
                logger.warning(
                    "[player_hist_acquire] %s %s SGO error: %s",
                    sport_l, game_date, exc,
                )
                per_date_counts[game_date] = -1
                continue
            events = fetched.get("events") or []
            n_sgo_events += len(events)
            n_sgo_pages  += int(fetched.get("n_pages") or 0)
            eps = fetched.get("sgo_endpoints") or []
            if eps and len(sample_endpoints) < 5:
                sample_endpoints.append(eps[0])
            per_date_counts[game_date] = len(events)

            now_ingested = datetime.now(timezone.utc)

            for ev in events:
                if not isinstance(ev, dict):
                    continue
                # Player-prop rows only
                rows, counters = normalize_player_payload(
                    {"events": [ev]},
                    sport=sport_l, league=league,
                    snapshot_iso=snap_iso,
                    ingested_at=now_ingested,
                )
                for fam, n in (counters.get("stat_families") or {}).items():
                    stat_families[fam] = stat_families.get(fam, 0) + n
                bc = _apply_book_policy_in_place(rows)
                n_blocked += bc["n_blocked"]
                n_refs    += bc["n_refs"]
                n_props_norm += counters.get("rows_emitted", 0)
                if write_mode == "insert":
                    props_ops.extend(_build_player_inserts(rows))
                else:
                    props_ops.extend(_build_player_upserts(rows))

            # Day complete — streaming flush
            await _flush(force=False)

        await _flush(force=True)

        status = "succeeded" if not dry_run else "dry_run"
        diagnosis = (
            f"upserted={n_props_upserted} modified={n_props_modified}"
            if not dry_run
            else f"would-write {n_props_written} player-prop rows"
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[player_hist_acquire] failed")
        status = "errored"
        diagnosis = f"failed: {exc}"

    finished = datetime.now(timezone.utc)
    audit = {
        "run_id":         run_id,
        "sport":          sport_l,
        "kind":           "player_historical",
        "hist_coll":      PLAYER_HIST_COLL,
        "start_date":     start_date,
        "end_date":       end_date,
        "n_dates":        len(dates),
        "dry_run":        dry_run,
        "write_mode":     write_mode,
        "status":         status,
        "diagnosis":      diagnosis,
        "started_at":     started,
        "finished_at":    finished,
        "duration_ms":    int((finished - started).total_seconds() * 1000),
        "n_sgo_pages":    n_sgo_pages,
        "n_sgo_events":   n_sgo_events,
        "n_props_normalized": n_props_norm,
        "n_props_written":    n_props_written,
        "n_props_upserted":   n_props_upserted,
        "n_props_modified":   n_props_modified,
        "n_props_duplicates": n_props_duplicates,
        "n_blocked":          n_blocked,
        "n_refs":             n_refs,
        "stat_families":      stat_families,
        "per_date_counts":    per_date_counts,
        "sample_endpoints":   sample_endpoints,
    }
    await db[AUDIT_COLL].insert_one(dict(audit))
    audit.pop("_id", None)
    return audit


__all__ = ["PLAYER_HIST_COLL", "acquire_player_historical_window"]
