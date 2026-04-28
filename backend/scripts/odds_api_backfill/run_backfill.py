"""
CLI entrypoint for the historical-odds backfill.

USAGE
-----
Single-slate validation (cheapest gate, ≤ 1.5k credits):
    ODDS_API_KEY=... python -m scripts.odds_api_backfill.run_backfill \
        --slate 2026-04-22 --snapshots open

Full 30-day backfill:
    ODDS_API_KEY=... python -m scripts.odds_api_backfill.run_backfill \
        --num-days 30 --snapshots open,pregame_-1h,pregame_-10m

Cost-only dry run (no API calls — prints projected credit usage):
    python -m scripts.odds_api_backfill.run_backfill \
        --num-days 30 --estimate-only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from scripts.odds_api_backfill.orchestrator import (
    SNAPSHOT_PLAN, run_backfill, run_slate,
)
from scripts.odds_api_backfill.schema import (
    COLLECTION_NAME, TARGET_MARKETS, ensure_indexes,
)
from scripts.odds_api_backfill.client import OddsAPIClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("odds_api_backfill")


def _parse_snapshots(s: str):
    if not s: return SNAPSHOT_PLAN
    sel = {x.strip() for x in s.split(",")}
    return [t for t in SNAPSHOT_PLAN if t[0] in sel]


def _estimate_only(num_days: int, n_snapshots: int, n_markets: int,
                    n_regions: int = 1, avg_events_per_slate: int = 10) -> dict:
    """Pure-python cost estimate. No API calls."""
    cost_per_event_call = n_markets * n_regions * 10  # docs: 10/m/r/e
    per_snapshot = avg_events_per_slate * cost_per_event_call
    per_slate = per_snapshot * n_snapshots
    total = per_slate * num_days
    # +1 events-list call per snapshot (1 credit per docs)
    total += num_days * n_snapshots * 1
    return {
        "num_days": num_days,
        "snapshots_per_slate": n_snapshots,
        "markets_per_request": n_markets,
        "regions": n_regions,
        "avg_events_per_slate": avg_events_per_slate,
        "credits_per_event_call": cost_per_event_call,
        "credits_per_snapshot": per_snapshot,
        "credits_per_slate": per_slate,
        "credits_total_est": total,
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--num-days", type=int, default=30)
    ap.add_argument("--slate", default=None,
                    help="Single-slate test, YYYY-MM-DD (UTC).")
    ap.add_argument("--snapshots", default=None,
                    help=f"Comma-sep subset of {[s[0] for s in SNAPSHOT_PLAN]}")
    ap.add_argument("--estimate-only", action="store_true",
                    help="Dry run; print projected cost only, no API calls.")
    args = ap.parse_args()

    snaps = _parse_snapshots(args.snapshots)

    if args.estimate_only:
        est = _estimate_only(num_days=args.num_days,
                              n_snapshots=len(snaps),
                              n_markets=len(TARGET_MARKETS))
        print(json.dumps(est, indent=2))
        return

    if not os.environ.get("ODDS_API_KEY"):
        print("ERROR: ODDS_API_KEY not set in environment.", file=sys.stderr)
        sys.exit(1)

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    await ensure_indexes(db)

    if args.slate:
        async with OddsAPIClient() as client:
            slate_dt = datetime.strptime(args.slate, "%Y-%m-%d") \
                          .replace(tzinfo=timezone.utc)
            res = await run_slate(db, client=client, slate_date=slate_dt,
                                    snapshot_plan=snaps)
            print(json.dumps(res, default=str, indent=2))
            return

    res = await run_backfill(db, num_days=args.num_days, snapshot_plan=snaps)
    print(json.dumps({
        "rows_inserted_total": res["rows_inserted_total"],
        "rows_modified_total": res["rows_modified_total"],
        "credits_used_local":  res["credits_used_local"],
        "api_stats":           res.get("api_stats"),
        "slates":              len(res["slates"]),
    }, default=str, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
