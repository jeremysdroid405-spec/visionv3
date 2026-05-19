"""
SGO ingest pipeline — orchestrates Stage 1 (metadata), Stage 2 (props),
Stage 3 (results & stats), and Stage 4 (coverage validation).

Idempotent. Resume checkpoints in `sgo_ingest_status`. All bulk writes use
`bulk_write([UpdateOne(..., upsert=True)])` so re-runs are safe.

The pipeline never touches production data — it only writes to the
`sgo_*` collections under DB_NAME (pick_vision by default).
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import UpdateOne, ASCENDING

from .client import SGOClient
from .normalize import (
    extract_event, extract_result, extract_props_and_outcomes,
    extract_player_stats, extract_team_stats,
    extract_player_registry_entries,
)

log = logging.getLogger("sgo.ingest")

COLLECTIONS = {
    "events":       "sgo_events",
    "players":      "sgo_players",
    "results":      "sgo_results",
    "player_stats": "sgo_player_stats",
    "team_stats":   "sgo_team_stats",
    "props_raw":    "sgo_props_raw",
    "outcomes":     "sgo_odds_outcomes",
    "consensus":    "sgo_book_consensus",
    "status":       "sgo_ingest_status",
    "raw_resp":     "sgo_raw_responses",   # debug; small
    "meta_sports": "sgo_meta_sports",
    "meta_leagues":"sgo_meta_leagues",
    "meta_teams":  "sgo_meta_teams",
    "meta_books":  "sgo_meta_bookmakers",
}


# ─────────────────────────────────────────────────────────────────── indexes
async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db[COLLECTIONS["events"]].create_index(
        [("event_id", ASCENDING), ("snapshot_time", ASCENDING)], unique=True)
    await db[COLLECTIONS["events"]].create_index("league_id")
    await db[COLLECTIONS["events"]].create_index("start_time")
    await db[COLLECTIONS["players"]].create_index("player_id", unique=True)
    await db[COLLECTIONS["results"]].create_index("event_id", unique=True)
    await db[COLLECTIONS["player_stats"]].create_index(
        [("event_id", ASCENDING), ("player_id", ASCENDING)], unique=True)
    await db[COLLECTIONS["team_stats"]].create_index(
        [("event_id", ASCENDING), ("team_id", ASCENDING)], unique=True)
    await db[COLLECTIONS["props_raw"]].create_index([
        ("event_id", ASCENDING), ("odd_id", ASCENDING),
        ("book_id", ASCENDING), ("side", ASCENDING),
        ("line", ASCENDING), ("snapshot_time", ASCENDING),
    ], unique=True, name="props_raw_pk")
    await db[COLLECTIONS["outcomes"]].create_index([
        ("event_id", ASCENDING), ("odd_id", ASCENDING),
        ("book_id", ASCENDING), ("selection_id", ASCENDING),
    ], unique=True, name="outcomes_pk")
    await db[COLLECTIONS["consensus"]].create_index([
        ("event_id", ASCENDING), ("odd_id", ASCENDING),
        ("snapshot_time", ASCENDING),
    ], unique=True, name="consensus_pk")
    await db[COLLECTIONS["status"]].create_index("job_id", unique=True)


# ─────────────────────────────────────────────────────────── bulk upsert helper
async def _upsert(coll, docs: List[Dict[str, Any]], keys: List[str]) -> int:
    if not docs:
        return 0
    ops = []
    for d in docs:
        filt = {k: d.get(k) for k in keys}
        ops.append(UpdateOne(filt, {"$set": d}, upsert=True))
    res = await coll.bulk_write(ops, ordered=False)
    return (res.upserted_count or 0) + (res.modified_count or 0)


# ──────────────────────────────────────────────────────────── ingest one window
async def ingest_events_window(
    client: SGOClient,
    db: AsyncIOMotorDatabase,
    *,
    league_id: str,
    start_date: str,    # YYYY-MM-DD
    end_date: str,      # YYYY-MM-DD (inclusive)
    markets: Optional[List[str]] = None,
    books: Optional[List[str]] = None,
    include_alt_lines: bool = True,
    include_opposing: bool = True,
    include_consensus: bool = True,
    include_outcomes: bool = True,
    include_player_stats: bool = True,
    dry_run: bool = False,
    persist_raw_page: bool = False,  # save first page raw resp for schema audit
    finalized_only: bool = True,
) -> Dict[str, Any]:
    """
    Ingest one league × window. `start_date` and `end_date` are inclusive.
    Returns a summary dict; also writes a `sgo_ingest_status` row.
    """
    job_id = f"{league_id}:{start_date}:{end_date}"
    started = datetime.now(timezone.utc)
    snap = started.isoformat()
    status_doc = {
        "job_id": job_id, "league_id": league_id,
        "start_date": start_date, "end_date": end_date,
        "started_at": started, "status": "running",
        "dry_run": dry_run, "config": {
            "markets": markets, "books": books,
            "include_alt_lines": include_alt_lines,
            "include_opposing": include_opposing,
            "include_consensus": include_consensus,
            "include_outcomes": include_outcomes,
            "include_player_stats": include_player_stats,
            "finalized_only": finalized_only,
        }, "snapshot_time": snap,
    }
    if not dry_run:
        await db[COLLECTIONS["status"]].update_one(
            {"job_id": job_id}, {"$set": status_doc}, upsert=True)

    # Common filter set — date param names are schema-discovered (we try both styles)
    base_filters: Dict[str, Any] = {
        "oddsAvailable": "true",
        # Try both common SGO date param spellings; harmless if one is ignored.
        "startsAfter":   f"{start_date}T00:00:00Z",
        "startsBefore":  f"{end_date}T23:59:59Z",
        "from":          start_date,
        "to":            end_date,
    }
    if finalized_only:
        base_filters["finalized"] = "true"
    if include_alt_lines:
        base_filters["includeAltLines"] = "true"
    if include_opposing:
        base_filters["includeOpposingOdds"] = "true"
    if markets:
        base_filters["oddIDs"] = ",".join(markets)
    if books:
        base_filters["bookmakerID"] = ",".join(books)

    n_events = 0
    n_props = 0
    n_out = 0
    n_cons = 0
    n_pstats = 0
    n_tstats = 0
    last_event_id: Optional[str] = None
    first_page_saved = False
    by_book: Counter = Counter()
    by_stat: Counter = Counter()

    events_buf: List[Dict[str, Any]] = []
    results_buf: List[Dict[str, Any]] = []
    props_buf: List[Dict[str, Any]] = []
    out_buf: List[Dict[str, Any]] = []
    cons_buf: List[Dict[str, Any]] = []
    pstats_buf: List[Dict[str, Any]] = []
    tstats_buf: List[Dict[str, Any]] = []
    players_buf: List[Dict[str, Any]] = []

    async def flush() -> None:
        nonlocal events_buf, results_buf, props_buf, out_buf
        nonlocal cons_buf, pstats_buf, tstats_buf, players_buf
        if dry_run:
            events_buf.clear(); results_buf.clear(); props_buf.clear()
            out_buf.clear(); cons_buf.clear(); pstats_buf.clear()
            tstats_buf.clear(); players_buf.clear()
            return
        await _upsert(db[COLLECTIONS["events"]],   events_buf,
                      ["event_id", "snapshot_time"])
        await _upsert(db[COLLECTIONS["results"]],  results_buf,  ["event_id"])
        await _upsert(db[COLLECTIONS["props_raw"]], props_buf,
                      ["event_id", "odd_id", "book_id", "side", "line",
                       "snapshot_time"])
        await _upsert(db[COLLECTIONS["outcomes"]], out_buf,
                      ["event_id", "odd_id", "book_id", "selection_id"])
        await _upsert(db[COLLECTIONS["consensus"]], cons_buf,
                      ["event_id", "odd_id", "snapshot_time"])
        await _upsert(db[COLLECTIONS["player_stats"]], pstats_buf,
                      ["event_id", "player_id"])
        await _upsert(db[COLLECTIONS["team_stats"]], tstats_buf,
                      ["event_id", "team_id"])
        await _upsert(db[COLLECTIONS["players"]], players_buf, ["player_id"])
        events_buf = []; results_buf = []; props_buf = []
        out_buf = []; cons_buf = []; pstats_buf = []
        tstats_buf = []; players_buf = []

    try:
        async for ev in client.iter_events(league_id=league_id, **base_filters):
            n_events += 1
            last_event_id = ev.get("eventID") or ev.get("event_id") or ev.get("id")
            if persist_raw_page and not first_page_saved and not dry_run:
                await db[COLLECTIONS["raw_resp"]].insert_one({
                    "scope": job_id, "captured_at": datetime.now(timezone.utc),
                    "event_sample": ev,
                })
                first_page_saved = True
            events_buf.append(extract_event(ev, snapshot_time=snap))
            results_buf.append(extract_result(ev))
            poo = extract_props_and_outcomes(ev, snapshot_time=snap)
            props_buf.extend(poo["props_raw"])
            out_buf.extend(poo["outcomes"])
            if include_consensus:
                cons_buf.extend(poo["consensus"])
            for r in poo["props_raw"]:
                by_book[r.get("book_id") or "_unknown"] += 1
                by_stat[r.get("stat_id") or "_unknown"] += 1
            n_props += len(poo["props_raw"])
            n_out  += len(poo["outcomes"])
            n_cons += len(poo["consensus"])
            if include_player_stats:
                ps = extract_player_stats(ev)
                ts = extract_team_stats(ev)
                pstats_buf.extend(ps); tstats_buf.extend(ts)
                n_pstats += len(ps); n_tstats += len(ts)
                players_buf.extend(extract_player_registry_entries(ev))
            if len(events_buf) >= 100:
                await flush()
        await flush()
    except Exception as e:  # pragma: no cover
        log.exception("ingest failed for %s", job_id)
        status_doc["status"] = "failed"
        status_doc["error"]  = repr(e)
        status_doc["events_processed"] = n_events
        if not dry_run:
            await db[COLLECTIONS["status"]].update_one(
                {"job_id": job_id}, {"$set": status_doc}, upsert=True)
        raise

    completed = datetime.now(timezone.utc)
    summary = {
        **status_doc,
        "status": "completed" if not dry_run else "dry_run_ok",
        "completed_at": completed,
        "duration_sec": round((completed - started).total_seconds(), 2),
        "events_processed": n_events,
        "props_rows":   n_props,
        "outcome_rows": n_out,
        "consensus_rows": n_cons,
        "player_stats_rows": n_pstats,
        "team_stats_rows": n_tstats,
        "last_event_id": last_event_id,
        "api_calls":    client.stats(),
        "by_book_top10": by_book.most_common(10),
        "by_stat_top20": by_stat.most_common(20),
    }
    if not dry_run:
        await db[COLLECTIONS["status"]].update_one(
            {"job_id": job_id}, {"$set": summary}, upsert=True)
    return summary


# ─────────────────────────────────────────────────────────── stage 1 metadata
async def ingest_metadata(
    client: SGOClient, db: AsyncIOMotorDatabase,
    *, league_id: str = "MLB", sport_id: str = "BASEBALL",
    dry_run: bool = False,
) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for name, fetcher, key in [
        ("sports", client.get_sports,    "sport_id"),
        ("leagues", client.get_leagues,  "league_id"),
        ("bookmakers", client.get_bookmakers, "book_id"),
    ]:
        try:
            data = await fetcher()
        except Exception as e:
            log.warning("metadata %s failed: %s", name, e)
            counts[name] = -1
            continue
        rows = data.get("data") or data.get("results") or data.get(name) or []
        if not dry_run and rows:
            # Normalize keys minimally so collections are queryable
            norm: List[Dict[str, Any]] = []
            for r in rows:
                if isinstance(r, dict):
                    rr = dict(r)
                    rr.setdefault("_provider", "sgo")
                    rr.setdefault("_fetched_at",
                                  datetime.now(timezone.utc).isoformat())
                    norm.append(rr)
            coll_name = COLLECTIONS[f"meta_{name}"]
            await db[coll_name].drop()
            if norm:
                await db[coll_name].insert_many(norm)
        counts[name] = len(rows)

    try:
        teams = await client.get_teams(leagueID=league_id, limit=500)
        rows = teams.get("data") or teams.get("teams") or teams.get("results") or []
        if not dry_run and rows:
            await db[COLLECTIONS["meta_teams"]].drop()
            await db[COLLECTIONS["meta_teams"]].insert_many(rows)
        counts["teams"] = len(rows)
    except Exception as e:
        log.warning("teams fetch failed: %s", e)
        counts["teams"] = -1
    return counts
