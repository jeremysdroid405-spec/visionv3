#!/usr/bin/env python3
"""Backfill `bdl_advanced_stats` for the NBA 2023-24 regular season +
postseason (BallDontLie season=2023). Drives the production fetcher
(`backend/services/bdl_advanced_stats_fetcher.py`) — same code path
production uses for all other season ingests, no logic fork.

Scope per spec: 2023-10 → 2024-04. We pass that as `start_date` /
`end_date` to skip preseason / July-Sep gap.

Idempotent — `_store_stats` upserts on `(player_id, game_id)`. Writes
ONLY to `bdl_advanced_stats`. Reports progress + final coverage.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from dotenv import load_dotenv
load_dotenv(str(REPO_ROOT / "backend/.env"))

from pymongo import MongoClient                          # noqa: E402
from services.bdl_advanced_stats_fetcher import (        # noqa: E402
    BDLAdvancedStatsFetcher,
)

logging.basicConfig(level=logging.INFO,
                     format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("backfill_adv")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--season", type=int, default=2023,
                    help="BDL season year (start year of the regular "
                          "season). 2023 = 2023-24 NBA season.")
    p.add_argument("--start-date", default="2023-10-01")
    p.add_argument("--end-date",   default="2024-05-01")
    p.add_argument("--summary-out", default=None)
    args = p.parse_args()

    if not os.environ.get("BDL_API_KEY"):
        log.error("BDL_API_KEY not set in env")
        return 2

    client = MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    pre_count = db["bdl_advanced_stats"].count_documents(
        {"game_date": {"$gte": args.start_date, "$lt": args.end_date}}
    )
    log.info(f"pre-backfill rows in window: {pre_count}")

    fetcher = BDLAdvancedStatsFetcher(db)
    started = datetime.now(timezone.utc)
    res = fetcher.fetch_advanced_stats_for_season(
        season=args.season,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    finished = datetime.now(timezone.utc)

    post_count = db["bdl_advanced_stats"].count_documents(
        {"game_date": {"$gte": args.start_date, "$lt": args.end_date}}
    )
    feb24 = db["bdl_advanced_stats"].count_documents(
        {"game_date": {"$gte": "2024-02-01", "$lt": "2024-03-01"}}
    )

    summary = {
        "season":         args.season,
        "start_date":     args.start_date,
        "end_date":       args.end_date,
        "fetcher_result": res,
        "pre_window_count":  pre_count,
        "post_window_count": post_count,
        "feb_2024_count":    feb24,
        "started_utc":    started.isoformat(),
        "finished_utc":   finished.isoformat(),
        "wallclock_sec":  (finished - started).total_seconds(),
    }
    log.info(f"summary: {json.dumps(summary, indent=2)}")

    if args.summary_out:
        Path(args.summary_out).write_text(json.dumps(summary, indent=2))
        log.info(f"wrote {args.summary_out}")

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
