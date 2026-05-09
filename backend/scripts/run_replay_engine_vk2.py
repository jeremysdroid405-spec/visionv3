#!/usr/bin/env python3
"""Run the replay engine WITH historical VK2 enabled.

Idempotent — re-running with the same `--run-id` upserts evaluations.
Writes only to `replay_evaluations` and `replay_engine_progress`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from dotenv import load_dotenv
load_dotenv(str(REPO_ROOT / "backend/.env"))

from motor.motor_asyncio import AsyncIOMotorClient   # noqa: E402
from services.replay.engine import run_replay_engine  # noqa: E402


def _parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", default=None)
    p.add_argument("--range-start", required=True,
                    help="ISO datetime, e.g. 2024-02-02T00:00:00Z")
    p.add_argument("--range-end", required=True)
    p.add_argument("--snapshot-label", default="t-30m")
    p.add_argument("--sport-key", default="basketball_nba")
    p.add_argument("--sport-short", default="nba")
    p.add_argument("--enable-vk2", default="true")
    p.add_argument("--cache-outputs", default="true",
                    help="Persist Stage-B cache for fast iteration.")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--sample-events", type=int, default=None,
                    help="Restrict to N random event_ids in the window.")
    p.add_argument("--summary-out", default=None,
                    help="Optional path to write run summary JSON")
    args = p.parse_args()

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    run_id = args.run_id or uuid.uuid4().hex
    enable_vk2 = (args.enable_vk2 or "").strip().lower() in ("1", "true", "yes")
    cache_outputs = (args.cache_outputs or "").strip().lower() in (
        "1", "true", "yes"
    )

    sample_event_ids = None
    if args.sample_events:
        # Pull a deterministic random sample of event_ids in the window.
        pipe = [
            {"$match": {
                "snapshot_label": args.snapshot_label,
                "sport_key":      args.sport_key,
                "commence_time": {
                    "$gte": _parse_iso(args.range_start),
                    "$lte": _parse_iso(args.range_end),
                },
            }},
            {"$group": {"_id": "$event_id"}},
            {"$sample": {"size": int(args.sample_events)}},
        ]
        sample_event_ids = [
            d["_id"] async for d in
            db["replay_props_normalized"].aggregate(pipe)
        ]
        print(f"[run_replay_engine_vk2] sampling "
              f"{len(sample_event_ids)} event_ids")

    print(f"[run_replay_engine_vk2] run_id={run_id} "
          f"enable_vk2={enable_vk2} cache_outputs={cache_outputs} "
          f"range={args.range_start} → {args.range_end}")

    summary = await run_replay_engine(
        db,
        replay_run_id=run_id,
        range_start=_parse_iso(args.range_start),
        range_end=_parse_iso(args.range_end),
        snapshot_label=args.snapshot_label,
        sport_key=args.sport_key,
        sport_short=args.sport_short,
        log_fn=print,
        limit=args.limit,
        enable_vk2=enable_vk2,
        cache_outputs=cache_outputs,
        sample_event_ids=sample_event_ids,
    )

    print(json.dumps(summary, indent=2, default=str))

    if args.summary_out:
        Path(args.summary_out).write_text(
            json.dumps(summary, indent=2, default=str)
        )
        print(f"wrote summary → {args.summary_out}")

    cli.close()


if __name__ == "__main__":
    asyncio.run(main())
