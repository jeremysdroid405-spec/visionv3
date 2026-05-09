#!/usr/bin/env python3
"""Settle ONLY qualified picks (`tier ∈ safe_haven|front_lines|war_zone`)
for a given replay_run_id. Used when the full resolver gets killed by
container restarts — qualified picks are <2k rows and finish in seconds.

Idempotent. Writes only to replay_outcomes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from dotenv import load_dotenv
load_dotenv(str(REPO_ROOT / "backend/.env"))

from motor.motor_asyncio import AsyncIOMotorClient   # noqa: E402
from pymongo import UpdateOne                         # noqa: E402

from services.replay.resolver import (                # noqa: E402
    REPLAY_OUTCOMES, REPLAY_EVALUATIONS, REPLAY_RESULTS,
    build_outcome_row, ensure_outcome_indexes,
)

PUBLISHED = ("safe_haven", "front_lines", "war_zone")


def _norm_name(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True)
    args = p.parse_args()

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    await ensure_outcome_indexes(db)

    started = datetime.now(timezone.utc)
    counts = {"total": 0, "hit": 0, "miss": 0, "push": 0, "void_dnp": 0}
    bulk: List[UpdateOne] = []

    cursor = db[REPLAY_EVALUATIONS].find(
        {"replay_run_id": args.run_id, "tier": {"$in": list(PUBLISHED)}},
    )
    async for ev in cursor:
        counts["total"] += 1
        result = await db[REPLAY_RESULTS].find_one({
            "event_id":    ev["event_id"],
            "player_norm": _norm_name(ev.get("player")),
        })
        row = build_outcome_row(evaluation=ev, result=result)
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1
        flt = {
            "replay_run_id":  row["replay_run_id"],
            "canonical_key":  row["canonical_key"],
            "snapshot_label": row["snapshot_label"],
            "bookmaker":      row["bookmaker"],
            "side":           row["side"],
        }
        bulk.append(UpdateOne(
            flt,
            {"$set": row,
             "$setOnInsert": {"_first_seen": row["resolved_at"]}},
            upsert=True,
        ))

    if bulk:
        await db[REPLAY_OUTCOMES].bulk_write(bulk, ordered=False)

    finished = datetime.now(timezone.utc)
    print(json.dumps({
        "run_id":            args.run_id,
        "wallclock_seconds": (finished - started).total_seconds(),
        **counts,
    }, indent=2, default=str))
    cli.close()


if __name__ == "__main__":
    asyncio.run(main())
