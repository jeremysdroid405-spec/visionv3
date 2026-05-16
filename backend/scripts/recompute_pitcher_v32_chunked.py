"""Pitcher-only forced chunked recompute → MLB_HF_v3.2_phase2b.

Resumable, memory-safe rescore of every active MLB pitcher-stat
canonical key in `mlb_live_props`. Uses the universal
`recompute_sport(only_canonical_keys=...)` path (upsert-mode, additive)
so we never destroy non-pitcher rows.

Chunks at 30 canonical keys per pass. Tracks completion in a JSON
progress file so the script is restart-safe if the pod OOMs mid-run.

Usage:
    python scripts/recompute_pitcher_v32_chunked.py --status
    python scripts/recompute_pitcher_v32_chunked.py --run
    python scripts/recompute_pitcher_v32_chunked.py --reset   # clear progress
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import List, Set

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("pitcher_v32_rescore")

PROGRESS_PATH = "/app/backend/audits/pitcher_v32_rescore_progress.json"
CHUNK_SIZE = 30  # canonical keys per recompute call
VERSION_TAG = "pitcher_v32_phase2b"
PITCHER_STATS = ["Pitcher Strikeouts", "Pitcher Outs", "Earned Runs",
                  "Hits Allowed", "Walks Allowed"]


def _load_progress():
    if os.path.exists(PROGRESS_PATH):
        with open(PROGRESS_PATH) as f:
            return json.load(f)
    return {"completed_keys": [], "started_at": datetime.now(timezone.utc).isoformat()}


def _save_progress(p):
    os.makedirs(os.path.dirname(PROGRESS_PATH), exist_ok=True)
    with open(PROGRESS_PATH, "w") as f:
        json.dump(p, f, indent=2, default=str)


def _pitcher_keys_sync() -> List[str]:
    import pymongo
    db = pymongo.MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    keys = db.mlb_live_props.distinct(
        "canonical_key", {"stat_type": {"$in": PITCHER_STATS}}
    )
    return sorted(k for k in keys if k)


async def _run_chunk(only_keys: Set[str], idx: int, total_chunks: int):
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.scoring.recompute import recompute_sport

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    t0 = time.time()
    logger.info(f"=== Chunk {idx}/{total_chunks} ({len(only_keys)} keys) ===")
    try:
        result = await recompute_sport(
            db, sport="mlb",
            version_tag=VERSION_TAG,
            dry_run=False,
            only_canonical_keys=only_keys,
        )
        scored = result.get("scored") or result.get("written") or 0
        logger.info(
            f"  chunk done: scored={scored} elapsed={time.time()-t0:.1f}s "
            f"summary={ {k:v for k,v in result.items() if k not in ('docs','props','rejected_props')} }"
        )
        return result
    finally:
        client.close()
        gc.collect()


def run():
    keys = _pitcher_keys_sync()
    progress = _load_progress()
    done = set(progress["completed_keys"])
    pending = [k for k in keys if k not in done]
    logger.info(f"Total pitcher keys: {len(keys)}, completed: {len(done)}, pending: {len(pending)}")
    if not pending:
        logger.info("Nothing to do — all pitcher keys already rescored.")
        return

    total_chunks = (len(pending) + CHUNK_SIZE - 1) // CHUNK_SIZE
    for i in range(0, len(pending), CHUNK_SIZE):
        chunk = pending[i:i + CHUNK_SIZE]
        idx = i // CHUNK_SIZE + 1
        asyncio.run(_run_chunk(set(chunk), idx, total_chunks))
        progress["completed_keys"] = sorted(done | set(chunk))
        done |= set(chunk)
        progress["last_updated"] = datetime.now(timezone.utc).isoformat()
        _save_progress(progress)
        gc.collect()
    logger.info(f"All {len(keys)} pitcher canonical keys rescored to {VERSION_TAG}.")


def status():
    keys = _pitcher_keys_sync()
    progress = _load_progress()
    done = set(progress["completed_keys"])
    print(f"Total pitcher canonical keys: {len(keys)}")
    print(f"Completed: {len(done)}")
    print(f"Pending: {len(keys) - len(done)}")
    if progress.get("last_updated"):
        print(f"Last updated: {progress['last_updated']}")


def reset():
    if os.path.exists(PROGRESS_PATH):
        os.remove(PROGRESS_PATH)
        print(f"Cleared {PROGRESS_PATH}")
    else:
        print("No progress file to clear.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    if args.status:
        status()
    elif args.reset:
        reset()
    elif args.run:
        run()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
