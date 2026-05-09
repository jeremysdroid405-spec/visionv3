#!/usr/bin/env python3
"""Event-level chunker. Processes ONE event at a time, persists its
event_id into `replay_engine_progress.completed_event_ids`. Each event
is ~1-2 min — survives any pod recycle pattern.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(str(REPO_ROOT / "backend/.env"))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from services.replay.engine import run_replay_engine  # noqa: E402

PROGRESS_COLL = "replay_engine_progress"


async def _all_events(db, *, sport_key: str, snapshot_label: str,
                      start_iso: str, end_iso: str) -> List[str]:
    s = datetime.fromisoformat(start_iso).replace(tzinfo=timezone.utc)
    e = datetime.fromisoformat(end_iso).replace(
        hour=23, minute=59, tzinfo=timezone.utc)
    pipe = [
        {"$match": {"sport_key": sport_key,
                     "snapshot_label": snapshot_label,
                     "commence_time": {"$gte": s, "$lte": e}}},
        {"$group": {"_id": "$event_id",
                     "ct": {"$min": "$commence_time"}}},
        {"$sort": {"ct": 1}},
    ]
    return [d["_id"] async for d in
            db.replay_props_normalized.aggregate(pipe)]


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2024-02-01")
    p.add_argument("--end",   default="2024-03-01")
    p.add_argument("--snapshot-label", default="t-30m")
    p.add_argument("--run-id", required=True)
    p.add_argument("--max-events-per-invocation", type=int, default=2)
    args = p.parse_args()

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    all_events = await _all_events(
        db, sport_key="basketball_nba", snapshot_label=args.snapshot_label,
        start_iso=args.start, end_iso=args.end)
    progress = await db[PROGRESS_COLL].find_one(
        {"replay_run_id": args.run_id})
    done_evts = set((progress or {}).get("completed_event_ids") or [])
    todo = [e for e in all_events if e not in done_evts]

    print(f"[evt] {len(done_evts)}/{len(all_events)} events done; "
          f"{len(todo)} remain.")
    if not todo:
        cli.close()
        sys.exit(2)

    target = todo[: args.max_events_per_invocation]
    range_start = datetime.fromisoformat(args.start).replace(
        tzinfo=timezone.utc)
    range_end = datetime.fromisoformat(args.end).replace(
        hour=23, minute=59, tzinfo=timezone.utc)

    for ev in target:
        started = datetime.now(timezone.utc)
        print(f"[evt] {ev} — starting")
        try:
            summary = await run_replay_engine(
                db,
                replay_run_id=args.run_id,
                range_start=range_start,
                range_end=range_end,
                snapshot_label=args.snapshot_label,
                sport_key="basketball_nba",
                sport_short="nba",
                enable_vk2=True,
                cache_outputs=True,
                sample_event_ids=[ev],
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[evt] {ev}: ERROR {exc}")
            cli.close()
            sys.exit(3)
        finished = datetime.now(timezone.utc)
        wall_min = round((finished - started).total_seconds() / 60.0, 2)
        print(f"[evt] {ev}: done in {wall_min} min  "
              f"counters={summary.get('counters', {})}")
        done_evts.add(ev)
        await db[PROGRESS_COLL].update_one(
            {"replay_run_id": args.run_id},
            {"$set": {"completed_event_ids": sorted(done_evts),
                      "last_event_done":     ev,
                      "last_event_wallclock_minutes": wall_min,
                      "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )

    remaining = [e for e in all_events if e not in done_evts]
    if remaining:
        print(f"[evt] EXIT 0 — {len(done_evts)}/{len(all_events)} done; "
              f"{len(remaining)} remaining")
        cli.close()
        sys.exit(0)
    else:
        print(f"[evt] EXIT 2 — all events done")
        cli.close()
        sys.exit(2)


if __name__ == "__main__":
    asyncio.run(main())
