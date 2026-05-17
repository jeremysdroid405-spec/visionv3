"""CLI: Layer-3 model replay for one date (warm). No external API calls."""
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
from services.replay.mlb_replay_engine import (
    DEFAULT_MEM_LIMIT_MB, replay_date,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
)


async def amain(args):
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    try:
        s = await replay_date(
            db, args.date,
            snapshot_iso=args.snapshot_iso,
            mem_limit_mb=args.mem_limit,
            force=args.force,
        )
    except MemoryError as me:
        print(f"HALT: {me}")
        return 2
    if s.get("skipped"):
        print("  already completed — skipped (--force to override)")
        return 0
    print(f"\n=== Replay summary for {args.date} @ {s['snapshot_iso']} ===")
    for k in ("alt_odds_rows_seen", "model_outputs_written",
              "unique_mu_predictions",
              "candidates_skipped_no_cache",
              "candidates_skipped_inference_failed",
              "candidates_skipped_under_alt",
              "elapsed_s",
              "rss_mb_start", "rss_mb_after_model_load",
              "rss_mb_peak", "rss_mb_end",
              "scoring_config_version", "source_version"):
        print(f"  {k:40s} {s.get(k)}")
    cli.close()
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True)
    p.add_argument("--snapshot-iso", default=None)
    p.add_argument("--mem-limit", default=DEFAULT_MEM_LIMIT_MB, type=int)
    p.add_argument("--force", action="store_true")
    asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    main()
