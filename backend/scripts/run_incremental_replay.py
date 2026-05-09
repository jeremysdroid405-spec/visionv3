#!/usr/bin/env python3
"""Fast-iteration replay — Stage C (gates / vision / TP) only.

Reads cached features + VK2 outputs from `replay_vk2_cache` and
re-scores under a fresh `replay_run_id`. Target: <5 min for 500k rows.

Examples:

  # Full incremental (score every cached row under a new run_id):
  python run_incremental_replay.py \\
      --run-id test_a \\
      --summary-out /tmp/test_a.json

  # Limited to rows seeded by one source run (typical iteration loop):
  python run_incremental_replay.py \\
      --run-id test_a \\
      --source-run-id vk2_full_adv_1778313861 \\
      --recompute-tp false

  # Sample for sub-minute loops:
  python run_incremental_replay.py \\
      --run-id smoke \\
      --limit 10000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from dotenv import load_dotenv
load_dotenv(str(REPO_ROOT / "backend/.env"))

from motor.motor_asyncio import AsyncIOMotorClient   # noqa: E402
from services.replay.scoring_only import run_scoring_only  # noqa: E402


def _parse_bool(v: str) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes")


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True,
                    help="Fresh replay_run_id to write under.")
    p.add_argument("--source-run-id", action="append",
                    help="Optional filter on cache rows. Repeatable.")
    p.add_argument("--sport", default="nba")
    p.add_argument("--recompute-tp", default="true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--summary-out", default=None)
    args = p.parse_args()

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    summary = await run_scoring_only(
        db,
        replay_run_id=args.run_id,
        source_run_ids=args.source_run_id,
        sport_short=args.sport,
        recompute_tp=_parse_bool(args.recompute_tp),
        limit=args.limit,
    )
    print(json.dumps(summary, indent=2, default=str))
    if args.summary_out:
        Path(args.summary_out).write_text(
            json.dumps(summary, indent=2, default=str)
        )
    cli.close()


if __name__ == "__main__":
    asyncio.run(main())
