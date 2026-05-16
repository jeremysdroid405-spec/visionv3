"""
CLI wrapper for the MLB historical alternate-odds ingest (Layer 1).

Usage:
    python -m scripts.mlb_historical_alt_odds_ingest \
        --start 2026-05-01 --end 2026-05-01

    python -m scripts.mlb_historical_alt_odds_ingest \
        --start 2026-04-15 --end 2026-05-04 \
        --chunk-size 20 \
        --mem-limit 1500

20-date chunking is for SCHEDULING / RESUME GROUPING only — it does not
mean we ever hold 20 days in memory. Per the spec, the inner loop is
date → market → bulk-flush → release.

Resumes automatically: any (chunk, date, market) already marked `completed`
in `mlb_historical_alt_odds_ingest_status` is skipped unless --force.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, "/app/backend")

from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient

from services.replay.historical_alt_odds_ingest import (
    DEFAULT_MEM_LIMIT_MB, DEFAULT_MLB_MARKETS, DEFAULT_REGIONS,
    daterange, ingest_chunk,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
)


async def amain(args: argparse.Namespace) -> int:
    if args.markets:
        markets = [m.strip() for m in args.markets.split(",") if m.strip()]
    else:
        markets = DEFAULT_MLB_MARKETS
    if args.regions:
        regions = [r.strip() for r in args.regions.split(",") if r.strip()]
    else:
        regions = DEFAULT_REGIONS

    all_dates = daterange(args.start, args.end)
    chunk_size = int(args.chunk_size)
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    total_rows = 0
    halted = False
    halt_reason = None
    for i in range(0, len(all_dates), chunk_size):
        sub = all_dates[i:i + chunk_size]
        print(f"\n=== CHUNK {i//chunk_size + 1}: {sub[0]} .. {sub[-1]} "
              f"({len(sub)} dates) ===", flush=True)
        summary = await ingest_chunk(
            db, dates=sub, markets=markets, regions=regions,
            snapshot_hour=args.snapshot_hour,
            mem_limit_mb=args.mem_limit, force=args.force,
            min_credit_floor=args.credit_floor,
        )
        total_rows += summary.get("rows_total", 0)
        print(f"  rows_total={summary['rows_total']}  "
              f"rss start/peak/end = "
              f"{summary['rss_mb_start']}/{summary['rss_mb_peak']}/"
              f"{summary['rss_mb_end']} MB  "
              f"elapsed={summary['elapsed_s']:.1f}s", flush=True)
        for d in summary.get("per_date", []):
            print(f"    {d['date']}  events={d.get('events_total')}  "
                  f"rows={d.get('rows_total')}", flush=True)
        if summary.get("halted"):
            halted = True
            halt_reason = summary.get("halt_reason")
            print(f"  ⚠ HALTED: {halt_reason}", flush=True)
            break

    print(f"\n=== FINAL: total rows {total_rows} halted={halted} "
          f"reason={halt_reason} ===", flush=True)
    cli.close()
    return 0 if not halted else 2


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="YYYY-MM-DD inclusive")
    p.add_argument("--end", required=True, help="YYYY-MM-DD inclusive")
    p.add_argument("--chunk-size", default=20, type=int)
    p.add_argument("--markets", default="",
                   help="comma-separated; default = all 11 + alternates")
    p.add_argument("--regions", default="", help="default = us,us2")
    p.add_argument("--snapshot-hour", default=11, type=int,
                   help="UTC hour for the historical snapshot (default 11)")
    p.add_argument("--mem-limit", default=DEFAULT_MEM_LIMIT_MB, type=int)
    p.add_argument("--credit-floor", default=1_000, type=int)
    p.add_argument("--force", action="store_true",
                   help="re-ingest dates already marked completed")
    args = p.parse_args()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
