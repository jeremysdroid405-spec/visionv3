"""Memory-safe full MLB rescore — chunked BY EVENT_ID.

Why this is safe (vs the doomed full recompute):
  • Each call to `recompute_sport(only_canonical_keys=...)` processes
    ~500-800 props (one game's worth) and force-upgrades into
    `upsert` mode — NEVER wipes other props.
  • The score_docs list is bounded to ~800 entries per chunk → peak
    memory < 100MB instead of the 1-2GB that killed the pod.
  • Vision score percentile is computed PER EVENT. For gate logic
    (vision_score_gate min=80/60) this is the cleanest reading:
    "elite within this game's slate" rather than "elite across
    the whole 16k pool" — both are valid; per-event is faster + safer.
  • 3-second pause between events so the FastAPI event loop stays
    responsive (no ingress 502s).

Output: writes to `final-mlb-rt` (the production tag). Idempotent and
race-safe — the realtime delta engine writes to the same tag in
parallel and the upsert pattern handles concurrent writers cleanly.
"""
import asyncio
import gc
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "/app/backend")

from motor.motor_asyncio import AsyncIOMotorClient

CHUNK_PAUSE_SEC = 3.0
LOG_EVERY = 1


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "pick_vision")]

    from services.scoring.recompute import recompute_sport

    # Step 1: pull canonical_keys grouped by event_id (projection-only,
    # tiny memory footprint).
    print(f"[{datetime.now(timezone.utc).isoformat()}] reading live event groups...")
    live_coll = db["mlb_live_props"]
    pipe = [
        {"$match": {"canonical_key": {"$ne": None}}},
        {"$group": {
            "_id": "$event_id",
            "keys": {"$addToSet": "$canonical_key"},
            "n": {"$sum": 1},
        }},
        {"$sort": {"n": -1}},
    ]
    event_groups = []
    async for doc in live_coll.aggregate(pipe):
        if doc["_id"]:
            event_groups.append((doc["_id"], doc["keys"], doc["n"]))

    total_keys = sum(len(g[1]) for g in event_groups)
    print(f"[{datetime.now(timezone.utc).isoformat()}] "
          f"found {len(event_groups)} events / {total_keys} canonical keys")

    t0 = time.monotonic()
    total_written = 0
    total_processed = 0
    failures = 0

    for i, (event_id, keys, raw_n) in enumerate(event_groups, start=1):
        chunk_start = time.monotonic()
        try:
            result = await recompute_sport(
                db=db,
                sport="mlb",
                version_tag="final-mlb-rt",
                dry_run=False,
                only_canonical_keys=set(keys),
            )
            written = result.get("written", 0)
            processed = result.get("processed", 0)
            total_written += written
            total_processed += processed
            elapsed = time.monotonic() - chunk_start
            if i % LOG_EVERY == 0:
                print(
                    f"[{i}/{len(event_groups)}] event={event_id[:12]}... "
                    f"raw={raw_n} matched={processed} written={written} "
                    f"({elapsed:.1f}s)"
                )
        except Exception as exc:
            failures += 1
            print(f"[{i}/{len(event_groups)}] event={event_id[:12]} FAILED: {exc!r}")

        # Aggressive GC + cooldown so the backend stays responsive
        gc.collect()
        await asyncio.sleep(CHUNK_PAUSE_SEC)

    total_elapsed = time.monotonic() - t0
    print()
    print("=" * 64)
    print(f"DONE in {total_elapsed:.1f}s")
    print(f"  events processed: {len(event_groups) - failures}/{len(event_groups)}")
    print(f"  props processed:  {total_processed}")
    print(f"  props written:    {total_written}")
    print(f"  failures:         {failures}")

    # Final DB state
    sc = db["mlb_prop_scores"]
    active = await sc.count_documents({"version_tag": "final-mlb-rt", "active": True})
    devig = await sc.count_documents({"version_tag": "final-mlb-rt", "active": True, "tp_source": "devig"})
    vis_gt0 = await sc.count_documents({"version_tag": "final-mlb-rt", "active": True, "vision_score": {"$gt": 0}})
    multi = await sc.count_documents({"version_tag": "final-mlb-rt", "active": True, "book_count": {"$gte": 2}})
    print()
    print(f"final-mlb-rt active: {active}")
    print(f"  tp_devig:        {devig}")
    print(f"  vision_score>0:  {vis_gt0}")
    print(f"  multi_book(>=2): {multi}")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
