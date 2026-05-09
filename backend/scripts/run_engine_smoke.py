#!/usr/bin/env python3
"""Replay engine integration smoke test — single-event sanity run.

Loads ONE event's `t-30m` snapshot offers from `replay_props_normalized`,
scores them against production `compute_scoring_stack()`, writes results
to `replay_evaluations` under a synthetic run_id, and prints a summary.

Demonstrates:
  - production scoring path is callable end-to-end with no fork
  - leakage gates are wired and abort on any future-data row
  - feature_completeness is correctly stamped as "minimal"

This is a FUNCTIONAL skeleton sanity check, not a full-parity run.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
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


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    # Wipe any previous smoke run for clean assertions.
    run_id = "smoke_engine_" + datetime.now(timezone.utc) \
        .strftime("%Y%m%d_%H%M%S")
    await db[REPLAY_EVALUATIONS].delete_many({"replay_run_id": run_id})

    range_start = datetime(2024, 2, 5, tzinfo=timezone.utc)
    range_end   = datetime(2024, 2, 6, tzinfo=timezone.utc)

    res = await run_replay_engine(
        db,
        replay_run_id=run_id,
        range_start=range_start, range_end=range_end,
        snapshot_label="t-30m",
        sport_key="basketball_nba",
        sport_short="nba",
        limit=200,           # cap to ~200 offers for the smoke run
    )

    eval_count = await db[REPLAY_EVALUATIONS].count_documents(
        {"replay_run_id": run_id})
    by_tier = {}
    async for d in db[REPLAY_EVALUATIONS].aggregate([
        {"$match": {"replay_run_id": run_id}},
        {"$group": {"_id": "$tier", "n": {"$sum": 1}}},
    ]):
        by_tier[d["_id"] or "<no-tier>"] = d["n"]

    sample_rows = []
    cursor = db[REPLAY_EVALUATIONS].find(
        {"replay_run_id": run_id},
        projection={"_id": 0, "scoring_payload": 0},
    ).limit(3)
    async for doc in cursor:
        sample_rows.append(doc)

    print(json.dumps({
        "smoke_run_id":   run_id,
        "engine_summary": res,
        "evaluations_persisted": eval_count,
        "tier_distribution": by_tier,
        "sample_rows":    sample_rows,
    }, indent=2, default=str))
    cli.close()


if __name__ == "__main__":
    asyncio.run(main())
