#!/usr/bin/env python3
"""Post-ingest validation + coverage report.

Reads `replay_*` collections, runs safety guards, runs leakage checks
against the actual stored snapshot timestamps, and produces the 13
deliverables requested by the user (2026-05-09).

NO API CALLS. NO MUTATIONS. Read-only.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from dotenv import load_dotenv
load_dotenv(str(REPO_ROOT / "backend/.env"))

from motor.motor_asyncio import AsyncIOMotorClient   # noqa: E402

from services.replay.ingest_telemetry import (        # noqa: E402
    IngestAborted,
    assert_book_whitelist_compliance, assert_chronology_intact,
    assert_malformed_below_threshold, assert_no_duplicate_anomaly,
)
from services.replay.markets import (                  # noqa: E402
    REPLAY_BOOK_WHITELIST_PHASE1, REPLAY_NBA_MARKETS,
)
from services.replay.leakage_checks import (           # noqa: E402
    ChronologyViolation, assert_pregame_only,
)


async def collect(db) -> Dict[str, Any]:
    snap_count = await db["replay_odds_snapshots"].count_documents({})
    norm_count = await db["replay_props_normalized"].count_documents({})
    prog_count = await db["replay_ingest_progress"].count_documents({})

    by_status = {}
    async for d in db["replay_ingest_progress"].aggregate([
        {"$group": {"_id": "$status", "n": {"$sum": 1}}},
    ]):
        by_status[d["_id"]] = d["n"]

    distinct_events = len(
        await db["replay_props_normalized"].distinct("event_id")
    )

    market_counts: Dict[str, int] = {}
    async for d in db["replay_props_normalized"].aggregate([
        {"$group": {"_id": "$market_key", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]):
        market_counts[d["_id"]] = d["n"]

    book_counts: Dict[str, int] = {}
    async for d in db["replay_props_normalized"].aggregate([
        {"$group": {"_id": "$bookmaker", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]):
        book_counts[d["_id"]] = d["n"]

    alt_count = await db["replay_props_normalized"].count_documents(
        {"is_alternate": True})
    combo_count = await db["replay_props_normalized"].count_documents(
        {"is_combo": True})

    by_window: Dict[str, int] = {}
    async for d in db["replay_props_normalized"].aggregate([
        {"$group": {"_id": "$snapshot_label", "n": {"$sum": 1}}},
    ]):
        by_window[d["_id"]] = d["n"]

    by_game_date_progress: Dict[str, Dict[str, int]] = {}
    async for d in db["replay_ingest_progress"].aggregate([
        {"$group": {"_id": {"date": "$game_date", "status": "$status"},
                     "n": {"$sum": 1}}},
        {"$sort": {"_id.date": 1}},
    ]):
        date = d["_id"]["date"]
        by_game_date_progress.setdefault(date, {})[d["_id"]["status"]] = d["n"]

    # Missing-market: per-market 'not_available' count
    not_avail = []
    async for d in db["replay_ingest_progress"].aggregate([
        {"$match": {"status": "not_available"}},
        {"$group": {"_id": "$window_label", "n": {"$sum": 1}}},
    ]):
        not_avail.append({"window": d["_id"], "n": d["n"]})

    # Storage estimate
    snap_stats = await db.command("collStats", "replay_odds_snapshots")
    norm_stats = await db.command("collStats", "replay_props_normalized")
    prog_stats = await db.command("collStats", "replay_ingest_progress")

    return {
        "counts": {
            "snapshot_docs": snap_count,
            "normalized_rows": norm_count,
            "alt_rows": alt_count,
            "combo_rows": combo_count,
            "progress_docs": prog_count,
            "distinct_events": distinct_events,
            "by_progress_status": by_status,
            "by_window_label": by_window,
            "not_available_by_window": not_avail,
        },
        "market_counts": market_counts,
        "book_counts": book_counts,
        "by_game_date_progress": by_game_date_progress,
        "storage_bytes": {
            "replay_odds_snapshots": snap_stats.get("size", 0),
            "replay_odds_snapshots_indexes":
                snap_stats.get("totalIndexSize", 0),
            "replay_props_normalized": norm_stats.get("size", 0),
            "replay_props_normalized_indexes":
                norm_stats.get("totalIndexSize", 0),
            "replay_ingest_progress": prog_stats.get("size", 0),
            "replay_ingest_progress_indexes":
                prog_stats.get("totalIndexSize", 0),
        },
    }


async def run_anomaly_checks(db) -> Dict[str, Any]:
    out: Dict[str, Any] = {"checks": []}
    for name, coro in (
        ("duplicate_anomaly",
         assert_no_duplicate_anomaly(db)),
        ("malformed_threshold",
         assert_malformed_below_threshold(db)),
        ("book_whitelist_compliance",
         assert_book_whitelist_compliance(
             db, allowed_books=set(REPLAY_BOOK_WHITELIST_PHASE1))),
        ("chronology_intact",
         assert_chronology_intact(db)),
    ):
        try:
            await coro
            out["checks"].append({"name": name, "status": "PASS"})
        except IngestAborted as exc:
            out["checks"].append(
                {"name": name, "status": "FAIL", "error": str(exc)})
    return out


async def run_per_event_pregame_audit(db, *, sample: int = 200) -> Dict[str, Any]:
    """Pull a random sample and assert every snapshot_ts < commence_time."""
    pipeline = [
        {"$sample": {"size": sample}},
        {"$project": {"_id": 0, "snapshot_ts": 1, "commence_time": 1,
                       "event_id": 1, "snapshot_label": 1}},
    ]
    bad: List[Dict[str, Any]] = []
    n = 0
    async for d in db["replay_props_normalized"].aggregate(pipeline):
        n += 1
        # Mongo returns naive datetimes (UTC). Re-attach UTC tz.
        snap_ts = d["snapshot_ts"]
        ct = d["commence_time"]
        if snap_ts.tzinfo is None:
            snap_ts = snap_ts.replace(tzinfo=timezone.utc)
        if ct.tzinfo is None:
            ct = ct.replace(tzinfo=timezone.utc)
        try:
            assert_pregame_only(snap_ts, ct)
        except ChronologyViolation as exc:
            bad.append({"row": d, "err": str(exc)})
    return {"sampled": n, "violations": len(bad), "violation_rows": bad[:5]}


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    started = datetime.now(timezone.utc)
    aggregates = await collect(db)
    anomalies = await run_anomaly_checks(db)
    pregame_audit = await run_per_event_pregame_audit(db, sample=500)

    coverage = {}
    for d, by_status in aggregates["by_game_date_progress"].items():
        total = sum(by_status.values())
        terminal = (by_status.get("done", 0) +
                    by_status.get("not_available", 0))
        coverage[d] = {
            "events_window_total": total,
            "terminal_pct": round(terminal / total * 100, 1) if total else 0,
            **by_status,
        }

    deliverables = {
        "1_total_credits_used":   None,  # see /tmp/full_ingest_30day_loop.log
        "2_total_events":         aggregates["counts"]["distinct_events"],
        "3_total_snapshots":      aggregates["counts"]["snapshot_docs"],
        "4_total_normalized_props":
            aggregates["counts"]["normalized_rows"],
        "5_total_alt_line_props": aggregates["counts"]["alt_rows"],
        "5b_total_combo_props":   aggregates["counts"]["combo_rows"],
        "6_market_distribution":  aggregates["market_counts"],
        "7_sportsbook_distribution": aggregates["book_counts"],
        "8_missing_market_stats": {
            "by_window_label": aggregates["counts"]["by_window_label"],
            "not_available_by_window":
                aggregates["counts"]["not_available_by_window"],
            "progress_status_counts":
                aggregates["counts"]["by_progress_status"],
        },
        "9_replay_coverage_report": coverage,
        "10_ingest_anomalies":     anomalies,
        "11_duplicate_counts":     {
            "duplicate_groups_in_normalized": (
                next((c for c in anomalies["checks"]
                      if c["name"] == "duplicate_anomaly"), {}).get("status")
            ),
        },
        "12_final_wallclock":     None,  # see loop log
        "13_storage_footprint":   {
            "bytes_per_collection": aggregates["storage_bytes"],
            "total_bytes_replay":  sum(
                aggregates["storage_bytes"].values()
            ),
            "human_readable_total":
                f"{sum(aggregates['storage_bytes'].values()) / 1024 / 1024:.1f} MB",
        },
        "extra_pregame_audit_500_sample": pregame_audit,
    }

    finished = datetime.now(timezone.utc)
    deliverables["report_generated_at_utc"] = finished.isoformat()
    deliverables["report_wallclock_seconds"] = (
        finished - started).total_seconds()

    print(json.dumps(deliverables, indent=2, default=str))
    cli.close()


if __name__ == "__main__":
    asyncio.run(main())
