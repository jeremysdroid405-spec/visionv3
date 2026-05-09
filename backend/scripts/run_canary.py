#!/usr/bin/env python3
"""
PropVision Historical Replay — Phase 1 Canary orchestrator.

Scope (per user directive 2026-05-09):
  - 5 mid-season 2024 NBA events
  - all 8 snapshot windows
  - all 18 configured NBA markets
  - US-region books only, filtered to the Phase-1 whitelist
  - WRITES ONLY to:
      replay_odds_snapshots
      replay_props_normalized
  - hard credit kill switch: 1,000,000

USAGE
-----
    # First run (will spend ~7-9k credits)
    ODDS_API_KEY=... python /app/backend/scripts/run_canary.py --execute

    # Idempotency rerun (should spend the same credits but write 0 inserts)
    ODDS_API_KEY=... python /app/backend/scripts/run_canary.py --execute --label rerun

    # Plan-only — no API, no DB
    python /app/backend/scripts/run_canary.py --plan-only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
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
from pymongo import ASCENDING                         # noqa: E402

from scripts.odds_api_backfill.client import (        # noqa: E402
    CreditBudgetExceeded, OddsAPIClient,
)

from services.replay import (                         # noqa: E402
    REPLAY_NBA_MARKETS,
    REPLAY_BOOK_WHITELIST_PHASE1,
    REPLAY_REGIONS_PHASE1,
    REPLAY_WINDOW_LABELS,
)
from services.replay.canary_events import PHASE1_CANARY_EVENTS  # noqa: E402
from services.replay.ingest_odds import (              # noqa: E402
    REPLAY_ODDS_SNAPSHOTS,
    REPLAY_PROPS_NORMALIZED,
    ingest_event_window,
)
from services.replay.schema import INDEX_SPECS         # noqa: E402


HARD_CREDIT_KILL_SWITCH = 1_000_000


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


async def _ensure_canary_indexes(db) -> Dict[str, List[str]]:
    """Create indexes ONLY for the two collections the canary writes."""
    out: Dict[str, List[str]] = {}
    for coll_name in (REPLAY_ODDS_SNAPSHOTS, REPLAY_PROPS_NORMALIZED):
        coll = db[coll_name]
        names: List[str] = []
        for spec in INDEX_SPECS[coll_name]:
            kwargs = {"name": spec["name"]}
            if spec.get("unique"):
                kwargs["unique"] = True
            await coll.create_index(spec["keys"], **kwargs)
            names.append(spec["name"])
        out[coll_name] = names
    return out


async def _fetch_sample_rows(db, n: int = 20) -> List[Dict[str, Any]]:
    cursor = db[REPLAY_PROPS_NORMALIZED].aggregate([
        {"$sample": {"size": n}},
        {"$project": {
            "_id": 0,
            "event_id": 1, "snapshot_label": 1, "minutes_before_start": 1,
            "bookmaker": 1, "market_key": 1, "stat_family": 1,
            "is_alternate": 1, "is_combo": 1,
            "player": 1, "line": 1, "side": 1,
            "odds_american": 1, "implied_probability": 1,
        }},
    ])
    return [doc async for doc in cursor]


async def _aggregate_counts(db) -> Dict[str, Any]:
    snap_count = await db[REPLAY_ODDS_SNAPSHOTS].count_documents({})
    norm_count = await db[REPLAY_PROPS_NORMALIZED].count_documents({})

    market_counts: Dict[str, int] = {}
    async for d in db[REPLAY_PROPS_NORMALIZED].aggregate([
        {"$group": {"_id": "$market_key", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]):
        market_counts[d["_id"]] = d["n"]

    book_counts: Dict[str, int] = {}
    async for d in db[REPLAY_PROPS_NORMALIZED].aggregate([
        {"$group": {"_id": "$bookmaker", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]):
        book_counts[d["_id"]] = d["n"]

    # Duplicate detection — must always be 0 if unique index is enforced.
    dup_check = await db[REPLAY_PROPS_NORMALIZED].aggregate([
        {"$group": {
            "_id": {
                "event_id": "$event_id", "snapshot_label": "$snapshot_label",
                "bookmaker": "$bookmaker", "market_key": "$market_key",
                "player": "$player", "line": "$line", "side": "$side",
            },
            "n": {"$sum": 1},
        }},
        {"$match": {"n": {"$gt": 1}}},
        {"$count": "dups"},
    ]).to_list(length=1)
    dup_count = dup_check[0]["dups"] if dup_check else 0

    return {
        "snapshot_docs":      snap_count,
        "normalized_rows":    norm_count,
        "market_counts":      market_counts,
        "book_counts":        book_counts,
        "duplicate_groups":   dup_count,
    }


async def run_canary(*, execute: bool, run_label: str) -> Dict[str, Any]:
    plan = {
        "phase":            "1_canary",
        "run_label":        run_label,
        "events":           [{"event_id": e["event_id"],
                              "tip": e["commence_time"].isoformat(),
                              "matchup": f"{e['away_team']} @ {e['home_team']}"}
                             for e in PHASE1_CANARY_EVENTS],
        "windows":          REPLAY_WINDOW_LABELS,
        "markets":          REPLAY_NBA_MARKETS,
        "books":            REPLAY_BOOK_WHITELIST_PHASE1,
        "regions":          REPLAY_REGIONS_PHASE1,
        "expected_calls":   len(PHASE1_CANARY_EVENTS) * len(REPLAY_WINDOW_LABELS),
        "estimated_credits_per_call": (
            10 * len(REPLAY_NBA_MARKETS) * len(REPLAY_REGIONS_PHASE1)
        ),
        "estimated_total_credits": (
            len(PHASE1_CANARY_EVENTS) * len(REPLAY_WINDOW_LABELS)
            * 10 * len(REPLAY_NBA_MARKETS) * len(REPLAY_REGIONS_PHASE1)
        ),
        "hard_credit_kill_switch": HARD_CREDIT_KILL_SWITCH,
    }

    if not execute:
        return {"plan": plan, "executed": False}

    if not os.environ.get("ODDS_API_KEY"):
        raise RuntimeError("ODDS_API_KEY missing from /app/backend/.env")
    if not os.environ.get("MONGO_URL") or not os.environ.get("DB_NAME"):
        raise RuntimeError("MONGO_URL / DB_NAME missing from env")

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    ensured = await _ensure_canary_indexes(db)

    # Pre-counts (matters for idempotency rerun verification).
    pre_snap = await db[REPLAY_ODDS_SNAPSHOTS].count_documents({})
    pre_norm = await db[REPLAY_PROPS_NORMALIZED].count_documents({})

    per_call_results: List[Dict[str, Any]] = []
    halted_reason = None
    started = datetime.now(timezone.utc)

    async with OddsAPIClient(api_key=os.environ["ODDS_API_KEY"]) as client:
        try:
            for ev in PHASE1_CANARY_EVENTS:
                for label in REPLAY_WINDOW_LABELS:
                    if (client.stats["credits_used_session"]
                            >= HARD_CREDIT_KILL_SWITCH):
                        halted_reason = (
                            f"credits_used_session "
                            f"{client.stats['credits_used_session']} "
                            f">= kill switch {HARD_CREDIT_KILL_SWITCH}"
                        )
                        break
                    try:
                        res = await ingest_event_window(
                            db, client=client,
                            sport_key="basketball_nba",
                            event=ev, window_label=label,
                        )
                        per_call_results.append(res)
                    except CreditBudgetExceeded as exc:
                        halted_reason = f"CreditBudgetExceeded: {exc}"
                        break
                if halted_reason:
                    break
        finally:
            api_stats = dict(client.stats)

    finished = datetime.now(timezone.utc)
    aggregates = await _aggregate_counts(db)
    sample = await _fetch_sample_rows(db, n=20)

    delta = {
        "snapshot_docs_inserted_this_run": (
            aggregates["snapshot_docs"] - pre_snap
        ),
        "normalized_rows_inserted_this_run": (
            aggregates["normalized_rows"] - pre_norm
        ),
    }

    cli.close()

    return {
        "executed":          True,
        "plan":              plan,
        "started_utc":       started.isoformat(),
        "finished_utc":      finished.isoformat(),
        "wallclock_seconds": (finished - started).total_seconds(),
        "ensured_indexes":   ensured,
        "per_call_results":  per_call_results,
        "halted_reason":     halted_reason,
        "api_stats":         api_stats,
        "delta_this_run":    delta,
        "aggregates":        aggregates,
        "sample_normalized_rows_20": sample,
    }


def _parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Phase-1 NBA replay canary (5 events × 8 windows).",
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan-only", action="store_true",
                      help="Print plan; no DB / no API.")
    mode.add_argument("--execute", action="store_true",
                      help="Execute ingest into replay_* collections.")
    p.add_argument("--label", default="initial",
                   help="Free-form run label (e.g. 'initial', 'rerun').")
    p.add_argument("--out", default=None,
                   help="Optional path to dump full JSON result.")
    return p.parse_args(argv)


async def _amain():
    _setup_logging()
    args = _parse_args()

    out = await run_canary(execute=args.execute, run_label=args.label)

    blob = json.dumps(out, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(blob)
        print(f"[canary] wrote {args.out}")
    print(blob)


def main():
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
