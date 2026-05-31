"""
Phase 1.A.4.acquire — Historical odds + matchup ingest worker.

Walks a UTC date window via SGO `/v2/events?startsAfter=…&startsBefore=…`
and writes TWO collections per event in a single pass:

  • matchup row → team_matchups (mlb) | nfl_matchups (nfl)
  • per-(market, line, side, book, snapshot) row(s) →
      team_historical_props (mlb) | nfl_historical_props (nfl)

Goal: maximise retained historical data before SGO trial expires. Pure
acquisition. NO grading, NO modeling, NO filtering by target-market list.

Hard scope:
  - One sport per invocation
  - One date window per invocation (single SGO `startsAfter`/`startsBefore`
    pair, but the provider walks cursor pages internally)
  - DRY-RUN by default. `--yes` (or `dry_run=False`) required for writes.
  - Dispatch guard (`SGO_API_KEY` + `TEAM_INGEST_ENABLED=1`) required.
  - Lenient unresolved-teams policy: matchup row written with
    `home_team_id=None`/`away_team_id=None` + `unresolved_teams: […]`.
  - For odds normalization we use the SAME team-prop normalizer that
    powers the live ingest (`workers/team/_normalize.py`), but with
    `market_keys=("__none__",)` set so the in-payload filter is
    DISABLED — we keep every market_key SGO surfaces, then upsert.
  - Each run writes ONE row to `historical_acquire_runs` for audit.
"""
from __future__ import annotations

import logging
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from pymongo import UpdateOne

from services.team_master_hub.ingest_policy import (
    dispatch_guard_ok,
    is_book_blocked,
    is_book_reference_only,
)
from services.team_master_hub.team_events import (
    build_team_id_lookup,
    normalize_event_to_matchup,
)

from ._normalize import normalize_sgo_payload
from ._sgo_provider import SGOFetchError, SGOPayloadProvider

logger = logging.getLogger("workers.team.historical_ingest")

# Sport → (matchup_collection, historical_props_collection)
SPORT_COLLECTIONS: Dict[str, Tuple[str, str]] = {
    "mlb":   ("team_matchups",  "team_historical_props"),
    "nfl":   ("nfl_matchups",   "nfl_historical_props"),
    # NBA piggy-backs on team_matchups / team_historical_props for now
    "nba":   ("team_matchups",  "team_historical_props"),
    # NCAAF uses its own dedicated collections — mirrors NFL.
    "ncaaf": ("ncaaf_matchups", "ncaaf_historical_props"),
}

AUDIT_COLL = "historical_acquire_runs"

# A market_key that will never match any SGO market_key — used to
# disable the in-payload "target filter" in the team normalizer so we
# keep every market the API hands us. (Acquisition-first stance.)
_ACQUIRE_ALL_SENTINEL: Tuple[str, ...] = ("__acquire_all_sentinel__",)


def _daterange_inclusive(start: str, end: str) -> List[str]:
    """Inclusive list of UTC dates between [start, end]. ValueError on
    malformed input or end < start.
    """
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
    """Drop blocked books, tag reference-only books. Same policy as
    the live ingest (single source of truth — `team_policy.py`).
    """
    blocked = 0
    refs = 0
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


async def _resolve_team_ids_in_rows(
    db,
    rows: List[Dict[str, Any]],
    *,
    sport: str,
    lookup_override: Optional[Dict[str, str]] = None,
) -> Dict[str, int]:
    """Inline team-id resolution mirroring `team_odds_ingest._resolve…`.
    Lookup pulled once per run (or injected) and reused per row.
    """
    if lookup_override is not None:
        name_to_tid = lookup_override
    else:
        name_to_tid = await build_team_id_lookup(db, sport=sport)

    unresolved = 0
    kept: List[Dict[str, Any]] = []
    for r in rows:
        tid = r.get("team_id")
        name = r.pop("_team_name", None)
        if tid:
            kept.append(r)
            continue
        if not name:
            unresolved += 1
            continue
        looked = name_to_tid.get(name)
        if not looked:
            unresolved += 1
            continue
        r["team_id"] = looked
        kept.append(r)
    rows[:] = kept
    return {"n_unresolved": unresolved}


def _build_matchup_upsert(row: Dict[str, Any]) -> UpdateOne:
    fetched_at = row["fetched_at"]
    return UpdateOne(
        {"sport": row["sport"], "event_id": row["event_id"]},
        {"$set": row, "$setOnInsert": {"created_at": fetched_at}},
        upsert=True,
    )


def _build_props_upserts(
    rows: List[Dict[str, Any]],
) -> List[UpdateOne]:
    """Compound-unique-key upserts mirroring the live odds-ingest
    write path. `ingested_at` lives under `$setOnInsert` so re-runs
    of the same snapshot produce modified_count=0.
    """
    ops: List[UpdateOne] = []
    for r in rows:
        ingested_at = r.pop("ingested_at", None)
        filter_doc = {
            "event_id":     r["event_id"],
            "team_id":      r["team_id"],
            "market":       r["market"],
            "line":         r["line"],
            "side":         r["side"],
            "book":         r["book"],
        }
        ops.append(UpdateOne(
            filter_doc,
            {"$set": r,
             "$setOnInsert": {"ingested_at": ingested_at}},
            upsert=True,
        ))
    return ops


# ── Self-heal: index spec for team-historical / team-live writers ──
# Mirrors `services/team_master_hub/collections.py` exactly. Baked
# in here so the worker can self-ensure its own indexes on every
# invocation, without coupling to ensure_team_collections() being
# called separately. This is what makes the acquisition idempotent
# even after a manual `db.<coll>.drop()`. Same shape as the
# historical_player_ingest worker.
_TEAM_HIST_INDEX_SPECS_BY_COLL: Dict[str, List[Dict[str, Any]]] = {
    # MLB/NBA team historical
    "team_historical_props": [
        {"name": "ix_hist_prop_compound_unique",
         "keys": [("event_id", 1), ("team_id", 1),
                  ("market", 1), ("line", 1),
                  ("side", 1), ("book", 1)],
         "unique": True},
        {"name": "ix_hist_prop_team_market_date",
         "keys": [("team_id", 1), ("market", 1),
                  ("game_date", 1)]},
    ],
    # NFL team historical
    "nfl_historical_props": [
        {"name": "ix_nfl_hist_prop_compound_unique",
         "keys": [("event_id", 1), ("team_id", 1),
                  ("market", 1), ("line", 1),
                  ("side", 1), ("book", 1)],
         "unique": True},
        {"name": "ix_nfl_hist_prop_date",
         "keys": [("game_date", 1)]},
        {"name": "ix_nfl_hist_prop_market_date",
         "keys": [("market", 1), ("game_date", 1)]},
    ],
    # Matchup tables don't change here — kept for completeness.
    "team_matchups": [
        {"name": "ix_matchup_event_id_unique",
         "keys": [("event_id", 1)],
         "unique": True},
    ],
    "nfl_matchups": [
        {"name": "ix_nfl_matchup_sport_event_unique",
         "keys": [("sport", 1), ("event_id", 1)],
         "unique": True},
    ],
    "ncaaf_matchups": [
        {"name": "ix_ncaaf_matchup_event_id_unique",
         "keys": [("event_id", 1)],
         "unique": True},
    ],
    "ncaaf_historical_props": [
        {"name": "ix_ncaaf_hist_prop_compound_unique",
         "keys": [("event_id", 1), ("team_id", 1),
                  ("market", 1), ("line", 1),
                  ("side", 1), ("book", 1)],
         "unique": True},
        {"name": "ix_ncaaf_hist_prop_date",
         "keys": [("game_date", 1)]},
        {"name": "ix_ncaaf_hist_prop_market_date",
         "keys": [("market", 1), ("game_date", 1)]},
    ],
}


async def _ensure_team_hist_indexes(db,
                                       *colls: str) -> None:
    """Idempotently ensure each team-historical / matchup
    collection has the indexes it needs — including the compound
    unique key that makes acquisition idempotent. Self-heals
    legacy indexes that include `snapshot_iso` by dropping and
    rebuilding them with the correct shape.
    """
    for target_coll in colls:
        specs = _TEAM_HIST_INDEX_SPECS_BY_COLL.get(target_coll) or []
        if not specs:
            continue
        coll = db[target_coll]
        try:
            info = await coll.index_information()
        except Exception:
            info = {}
        for spec in specs:
            name = spec["name"]
            want_keys = spec["keys"]
            want_unique = bool(spec.get("unique"))
            existing = info.get(name)
            if existing is not None:
                existing_keys = [(k, int(v)) for k, v
                                  in (existing.get("key") or [])]
                existing_unique = bool(existing.get("unique"))
                if existing_keys == want_keys \
                        and existing_unique == want_unique:
                    continue
                try:
                    await coll.drop_index(name)
                    logger.info(
                        "[team_hist] dropped stale index %s on %s "
                        "(was keys=%s unique=%s, want keys=%s "
                        "unique=%s)",
                        name, target_coll, existing_keys,
                        existing_unique, want_keys, want_unique,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "[team_hist] failed to drop stale index %s "
                        "on %s — skipping recreate",
                        name, target_coll)
                    continue
            try:
                kwargs: Dict[str, Any] = {"name": name}
                if want_unique:
                    kwargs["unique"] = True
                await coll.create_index(want_keys, **kwargs)
                logger.info(
                    "[team_hist] ensured index %s on %s (unique=%s)",
                    name, target_coll, want_unique)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[team_hist] failed to create index %s on %s",
                    name, target_coll)


async def acquire_historical_window(
    db,
    *,
    sport: str,
    start_date: str,
    end_date: str,
    api_key: str,
    dry_run: bool = True,
    provider: Optional[SGOPayloadProvider] = None,
    market_keys: Optional[Tuple[str, ...]] = None,
) -> Dict[str, Any]:
    """Walk [start_date, end_date] UTC inclusive, fetch + normalize +
    upsert into the per-sport matchup + historical-props collections.

    Args:
      sport:        'mlb' | 'nba' | 'nfl' (collection routing)
      start_date:   'YYYY-MM-DD' inclusive
      end_date:     'YYYY-MM-DD' inclusive
      api_key:      SGO API key (passed explicitly — worker never reads env)
      dry_run:      True → no writes. False → writes (requires guard open).
      provider:     optional injected SGOPayloadProvider (for tests)
      market_keys:  optional target market filter. If None → ACQUIRE ALL.

    Returns the audit row that was (also) written to
    `historical_acquire_runs`.
    """
    sport_l = sport.lower()
    if sport_l not in SPORT_COLLECTIONS:
        raise ValueError(f"unsupported sport: {sport!r}")
    matchup_coll, hist_coll = SPORT_COLLECTIONS[sport_l]

    dates = _daterange_inclusive(start_date, end_date)
    run_id  = str(uuid.uuid4())
    started = datetime.now(timezone.utc)

    # ── Dispatch guard ──
    ok, reasons = dispatch_guard_ok()
    if not ok:
        audit = {
            "run_id":      run_id,
            "sport":       sport_l,
            "start_date":  start_date,
            "end_date":    end_date,
            "n_dates":     len(dates),
            "dry_run":     dry_run,
            "status":      "guard_closed",
            "diagnosis":   "; ".join(reasons) or "dispatch guard closed",
            "started_at":  started,
            "finished_at": datetime.now(timezone.utc),
            "n_sgo_pages":     0,
            "n_sgo_events":    0,
            "n_matchups_written":    0,
            "n_props_normalized":    0,
            "n_props_written":       0,
            "n_props_upserted":      0,
            "n_props_modified":      0,
            "n_blocked":             0,
            "n_refs":                0,
            "n_unresolved":          0,
            "market_keys_seen":      [],
            "per_date_counts":       {},
            "sample_endpoints":      [],
        }
        await db[AUDIT_COLL].insert_one(dict(audit))
        audit.pop("_id", None)
        return audit

    prov = provider or SGOPayloadProvider(api_key)
    name_to_tid = await build_team_id_lookup(db, sport=sport_l)

    # Self-heal indexes BEFORE any writes. Idempotent — no-op when
    # they already exist. Critical: this is what makes acquisition
    # idempotent after `db.<coll>.drop()` because the compound
    # unique index is what blocks duplicate inserts/upserts.
    if not dry_run:
        await _ensure_team_hist_indexes(db, hist_coll, matchup_coll)

    effective_market_keys = (market_keys
                                if market_keys is not None
                                else _ACQUIRE_ALL_SENTINEL)
    acquire_all = market_keys is None

    n_sgo_pages   = 0
    n_sgo_events  = 0
    n_props_norm  = 0
    n_props_written = 0
    n_props_upserted = 0
    n_props_modified = 0
    n_matchups_written = 0
    n_blocked     = 0
    n_refs        = 0
    n_unresolved  = 0
    market_keys_seen: set[str] = set()
    per_date_counts: Dict[str, int] = {}
    sample_endpoints: List[str] = []

    matchup_ops: List[UpdateOne] = []
    props_ops: List[UpdateOne]   = []
    # Streaming flush thresholds — bounds peak memory so MLB-scale
    # multi-season pulls don't OOM the pod. Each UpdateOne carries
    # a ~1KB filter+update dict, so 50k ops ≈ ~50 MB peak (fine).
    FLUSH_MATCHUPS = 200
    FLUSH_PROPS    = 50_000

    async def _flush(force: bool = False) -> None:
        nonlocal n_props_upserted, n_props_modified, n_props_written
        if not dry_run:
            if matchup_ops and (force or
                                  len(matchup_ops) >= FLUSH_MATCHUPS):
                rr = await db[matchup_coll].bulk_write(
                    matchup_ops, ordered=False)
                logger.info(
                    "[hist_acquire] %s matchups flush upserted=%d "
                    "modified=%d",
                    matchup_coll, len(rr.upserted_ids or {}),
                    rr.modified_count or 0,
                )
                matchup_ops.clear()
            if props_ops and (force or len(props_ops) >= FLUSH_PROPS):
                # Sub-chunk in 1000-op slabs to keep server-side batch
                # commands sane.
                CHUNK = 1000
                slab_total = len(props_ops)
                for i in range(0, slab_total, CHUNK):
                    slab = props_ops[i:i + CHUNK]
                    rr = await db[hist_coll].bulk_write(
                        slab, ordered=False)
                    n_props_upserted += len(rr.upserted_ids or {})
                    n_props_modified += int(rr.modified_count or 0)
                n_props_written += slab_total
                props_ops.clear()
        else:
            # Dry-run: just track the WOULD-write count, then drop ops
            # to keep memory bounded across multi-month windows.
            if matchup_ops and (force or
                                  len(matchup_ops) >= FLUSH_MATCHUPS):
                matchup_ops.clear()
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
                    "[hist_acquire] %s %s SGO error: %s",
                    sport_l, game_date, exc,
                )
                per_date_counts[game_date] = -1   # marker
                continue
            events = fetched.get("events") or []
            n_sgo_events += len(events)
            n_sgo_pages  += int(fetched.get("n_pages") or 0)
            eps = fetched.get("sgo_endpoints") or []
            if eps and len(sample_endpoints) < 5:
                sample_endpoints.append(eps[0])
            per_date_counts[game_date] = len(events)

            primary_endpoint = eps[0] if eps else None
            snap_iso = (started + timedelta(microseconds=0)).isoformat()
            now_ingested = datetime.now(timezone.utc)

            for ev in events:
                if not isinstance(ev, dict):
                    continue
                # 1) matchup row
                matchup = normalize_event_to_matchup(
                    ev, sport=sport_l, team_id_lookup=name_to_tid,
                    fetched_at=now_ingested,
                    source_endpoint=primary_endpoint,
                )
                if matchup is not None:
                    matchup_ops.append(_build_matchup_upsert(matchup))
                    n_matchups_written += 1
                    if matchup.get("unresolved_teams"):
                        n_unresolved += len(matchup["unresolved_teams"])

                # 2) historical odds rows — re-use the live normalizer
                rows, counters = normalize_sgo_payload(
                    {"events": [ev]},
                    sport=sport_l,
                    snapshot_iso=snap_iso,
                    ingested_at=now_ingested,
                    market_keys=effective_market_keys,
                )
                # If acquire-all, override the filter behavior: the
                # normalizer dropped every market because none matched
                # the sentinel. Re-emit by passing the full set of keys
                # observed in this event.
                if acquire_all:
                    observed = list(
                        (counters.get("markets_observed_counts") or {}).keys())
                    if observed:
                        rows, counters = normalize_sgo_payload(
                            {"events": [ev]},
                            sport=sport_l,
                            snapshot_iso=snap_iso,
                            ingested_at=now_ingested,
                            market_keys=tuple(observed),
                        )

                # update observed counters
                for mk in (counters.get("markets_observed_counts")
                            or {}).keys():
                    market_keys_seen.add(mk)

                # Apply book policy + resolve team ids
                bc = _apply_book_policy_in_place(rows)
                n_blocked += bc["n_blocked"]
                n_refs    += bc["n_refs"]
                rc = await _resolve_team_ids_in_rows(
                    db, rows, sport=sport_l,
                    lookup_override=name_to_tid,
                )
                n_unresolved += rc["n_unresolved"]

                n_props_norm += counters.get("rows_emitted", 0)
                props_ops.extend(_build_props_upserts(rows))

            # Day complete — opportunistic streaming flush
            await _flush(force=False)

        # ── Final flush ──
        await _flush(force=True)

        status = "succeeded" if not dry_run else "dry_run"
        diagnosis = (
            f"upserted={n_props_upserted} modified={n_props_modified} "
            f"matchups={n_matchups_written}" if not dry_run
            else f"would-write {n_props_written} prop rows, "
                  f"{n_matchups_written} matchup rows"
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[hist_acquire] failed")
        status = "errored"
        diagnosis = f"failed: {exc}"

    finished = datetime.now(timezone.utc)
    audit = {
        "run_id":         run_id,
        "sport":          sport_l,
        "matchup_coll":   matchup_coll,
        "hist_coll":      hist_coll,
        "start_date":     start_date,
        "end_date":       end_date,
        "n_dates":        len(dates),
        "dry_run":        dry_run,
        "status":         status,
        "diagnosis":      diagnosis,
        "started_at":     started,
        "finished_at":    finished,
        "duration_ms":    int((finished - started).total_seconds() * 1000),
        "n_sgo_pages":    n_sgo_pages,
        "n_sgo_events":   n_sgo_events,
        "n_matchups_written":  n_matchups_written,
        "n_props_normalized":  n_props_norm,
        "n_props_written":     n_props_written,
        "n_props_upserted":    n_props_upserted,
        "n_props_modified":    n_props_modified,
        "n_blocked":           n_blocked,
        "n_refs":              n_refs,
        "n_unresolved":        n_unresolved,
        "market_keys_seen":    sorted(market_keys_seen),
        "per_date_counts":     per_date_counts,
        "sample_endpoints":    sample_endpoints,
        "acquire_all":         acquire_all,
    }
    await db[AUDIT_COLL].insert_one(dict(audit))
    audit.pop("_id", None)
    return audit


__all__ = ["SPORT_COLLECTIONS", "acquire_historical_window"]
