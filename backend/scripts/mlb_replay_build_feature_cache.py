"""
CLI wrapper for Layer-2 historical feature-cache build.

Usage:
    python -m scripts.mlb_replay_build_feature_cache --date 2026-05-05
    python -m scripts.mlb_replay_build_feature_cache \
        --start 2026-05-01 --end 2026-05-05

Per-date OOM-safe. Resumes via `mlb_replay_feature_status`.
"""
from __future__ import annotations
import argparse
import asyncio
import logging
import os
import sys
sys.path.insert(0, "/app/backend")
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient
from services.replay.mlb_feature_cache import (
    DEFAULT_MEM_LIMIT_MB, cache_date,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s %(message)s")


def daterange(start: str, end: str):
    d0 = datetime.strptime(start, "%Y-%m-%d")
    d1 = datetime.strptime(end, "%Y-%m-%d")
    while d0 <= d1:
        yield d0.strftime("%Y-%m-%d")
        d0 += timedelta(days=1)


async def amain(args):
    dates = ([args.date] if args.date
             else list(daterange(args.start, args.end)))
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    for d in dates:
        print(f"\n=== {d} ===", flush=True)
        try:
            s = await cache_date(
                db, d, mem_limit_mb=args.mem_limit, force=args.force,
            )
        except MemoryError as me:
            print(f"  HALTED: {me}", flush=True)
            break
        if s.get("skipped"):
            print("  already completed — skipped (use --force to override)")
            continue
        print(f"  pairs_cached       = {s['pairs_cached']}")
        print(f"  players_cached     = {s['players_cached']}")
        print(f"  skipped_no_hub     = {s['pairs_skipped_no_hub']}")
        print(f"  skipped_few_logs   = {s['pairs_skipped_insufficient_logs']}")
        print(f"  rows_written       = {s['rows_written']}")
        print(f"  rss start/peak/end = {s['rss_mb_start']}/"
              f"{s['rss_mb_peak']}/{s['rss_mb_end']} MB")
        print(f"  elapsed            = {s['elapsed_s']:.1f}s")
    cli.close()


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--date")
    g.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--mem-limit", default=DEFAULT_MEM_LIMIT_MB, type=int)
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    if args.start and not args.end:
        p.error("--start requires --end")
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
