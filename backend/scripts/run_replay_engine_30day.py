#!/usr/bin/env python3
"""Run the replay engine across the full 30-day NBA window.

Phase 2.5 partial-features run. Writes only to replay_evaluations.
"""
from __future__ import annotations

import argparse, asyncio, json, logging, os, sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from dotenv import load_dotenv
load_dotenv(str(REPO_ROOT / "backend/.env"))

from motor.motor_asyncio import AsyncIOMotorClient   # noqa: E402

from services.replay.engine import (                 # noqa: E402
    REPLAY_EVALUATIONS, run_replay_engine,
)
from services.replay.run_header import (             # noqa: E402
    compute_run_fingerprint, new_run_id,
)


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2024-02-01")
    p.add_argument("--end",   default="2024-03-01")
    p.add_argument("--snapshot", default="t-30m")
    p.add_argument("--name",  default="phase25_30day_partial_v1")
    p.add_argument("--out",   default="/app/audit_reports/replay_engine_30day.json")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                         format="%(asctime)s [%(levelname)s] %(message)s")
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    run_id = new_run_id()
    fingerprint = compute_run_fingerprint()
    started = datetime.now(timezone.utc)

    range_start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    range_end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)

    print(f"run_id={run_id} fingerprint={fingerprint['scoring_config_hash'][:12]}/{fingerprint['gate_config_hash'][:12]}")

    res = await run_replay_engine(
        db,
        replay_run_id=run_id,
        range_start=range_start, range_end=range_end,
        snapshot_label=args.snapshot,
        sport_key="basketball_nba", sport_short="nba",
        chunk_size=200,
    )

    res["run_name"]    = args.name
    res["fingerprint"] = fingerprint

    finished = datetime.now(timezone.utc)
    res["wallclock_seconds"] = (finished - started).total_seconds()

    Path(args.out).write_text(json.dumps(res, indent=2, default=str))
    print(f"wrote {args.out}")

    # Tier distribution
    tier_dist = {}
    async for d in db[REPLAY_EVALUATIONS].aggregate([
        {"$match": {"replay_run_id": run_id}},
        {"$group": {"_id": "$tier", "n": {"$sum": 1}}},
    ]):
        tier_dist[d["_id"] or "<none>"] = d["n"]
    print("tier_distribution:", json.dumps(tier_dist))

    reason_dist = {}
    async for d in db[REPLAY_EVALUATIONS].aggregate([
        {"$match": {"replay_run_id": run_id}},
        {"$group": {"_id": "$tier_reason", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
        {"$limit": 10},
    ]):
        reason_dist[d["_id"] or "<none>"] = d["n"]
    print("top_reasons:", json.dumps(reason_dist, indent=2))

    cli.close()


if __name__ == "__main__":
    asyncio.run(main())
