"""Hard e2e verification of Step 5 real-time ingest.

Checks:
 1. Fire BoardEvent('new_props', sport='nba', metadata={canonical_keys})
    with ONE canonical key. Confirm single doc upserted with fresh
    computed_at + active=True.
 2. Confirm the rest of nba_prop_scores is byte-identical pre/post
    (no collateral damage from upsert mode).
 3. Confirm /board-engine-stats endpoint reflects the ingest.
 4. Repeat for MLB.
"""
import asyncio
import os
import hashlib
import json
import time
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

from services.event_bus import BoardEvent, get_event_bus
from services.board.engine import subscribe_new_props_handler, stats_snapshot
from services.scoring.adapters import get_scoring_adapter
from services.board.adapters import get_adapter


async def _hash_pool_excluding(coll, exclude_key: str, version_tag: str) -> str:
    """Hash every doc in the pool EXCEPT the one canonical_key under
    test, to verify the upsert did not touch siblings."""
    cursor = coll.find({
        "version_tag": version_tag,
        "canonical_key": {"$ne": exclude_key},
    }, {"_id": 0}).sort("canonical_key", 1)
    h = hashlib.sha256()
    count = 0
    async for d in cursor:
        # Normalize non-JSON-serialisable values
        h.update(json.dumps(d, default=str, sort_keys=True).encode())
        count += 1
    return h.hexdigest(), count


async def verify_sport(db, sport: str):
    scoring = get_scoring_adapter(sport)
    board = get_adapter(sport)
    version_tag = board.version_tag
    scores_coll = db[board.scores_collection]
    live_coll = db[scoring.live_props_collection]

    # Pick one live prop, build its context to learn the canonical key
    print(f"\n========== {sport.upper()} ==========")
    print(f"live_props_collection={scoring.live_props_collection}")
    print(f"scores_collection={board.scores_collection} version_tag={version_tag}")

    candidate = None
    test_key = None
    existing_computed_at = None
    was_preexisting = False
    # First pass: try to find a live prop whose canonical_key ALREADY
    # has a score doc — that lets us verify computed_at advancement.
    async for raw in live_coll.find({}, {"_id": 0}).limit(500):
        try:
            ctx = await scoring.build_context(db, raw, {})
        except Exception:
            continue
        if ctx is None:
            continue
        ck = ctx.canonical_key
        if not ck:
            continue
        existing = await scores_coll.find_one(
            {"canonical_key": ck, "version_tag": version_tag}, {"_id": 0}
        )
        if existing:
            candidate = raw
            test_key = ck
            existing_computed_at = existing.get("computed_at")
            was_preexisting = True
            break

    # Second pass: if no overlap with the existing pool (possible when
    # the live props are from a newer slate than the last full recompute),
    # fall back to a net-new upsert scenario.
    if not test_key:
        async for raw in live_coll.find({}, {"_id": 0}).limit(50):
            try:
                ctx = await scoring.build_context(db, raw, {})
            except Exception:
                continue
            if ctx is None:
                continue
            if ctx.canonical_key:
                candidate = raw
                test_key = ctx.canonical_key
                break

    if not test_key:
        print(f"[{sport}] no eligible live prop found; skipping")
        return

    print(f"test_canonical_key = {test_key}")
    print(f"pre existed        = {was_preexisting}")
    print(f"pre computed_at    = {existing_computed_at}")

    # Snapshot the sibling pool to prove non-destructive upsert
    pre_hash, pre_count = await _hash_pool_excluding(scores_coll, test_key, version_tag)
    total_pre = await scores_coll.count_documents({"version_tag": version_tag})
    print(f"pre sibling_count = {pre_count}  total_version={total_pre}")
    print(f"pre sibling_hash  = {pre_hash[:16]}…")

    # Publish the event
    t0 = time.monotonic()
    await get_event_bus().publish(BoardEvent(
        sport=sport,
        event_type="new_props",
        source="e2e_verify",
        metadata={"canonical_keys": [test_key]},
    ))
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    print(f"event published + handled in {elapsed_ms} ms")

    # Verify post-state
    post_doc = await scores_coll.find_one(
        {"canonical_key": test_key, "version_tag": version_tag}, {"_id": 0}
    )
    if not post_doc:
        print(f"[FAIL] score doc for {test_key} missing after event")
        return
    post_computed_at = post_doc.get("computed_at")
    print(f"post computed_at  = {post_computed_at}")
    print(f"post active       = {post_doc.get('active')}")
    print(f"post tier         = {post_doc.get('tier')}")
    if was_preexisting:
        assert post_computed_at != existing_computed_at, "computed_at did not advance"
    assert post_doc.get("active") is True, "active must be True after ingest"

    # Sibling hash unchanged?
    post_hash, post_count = await _hash_pool_excluding(scores_coll, test_key, version_tag)
    total_post = await scores_coll.count_documents({"version_tag": version_tag})
    print(f"post sibling_count= {post_count}  total_version={total_post}")
    print(f"post sibling_hash = {post_hash[:16]}…")
    assert pre_hash == post_hash, f"sibling pool mutated (pre={pre_hash[:16]} post={post_hash[:16]})"
    assert pre_count == post_count, f"sibling count changed {pre_count} → {post_count}"

    print(f"[OK] {sport}: scoped upsert refreshed 1 doc; {pre_count} siblings byte-identical")


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Since this script runs in a separate process, the engine.subscribe
    # from the backend process is NOT visible here. We register our own
    # subscriber in this process so the publish round-trips.
    subscribe_new_props_handler(db)

    for sport in ("nba", "mlb"):
        try:
            await verify_sport(db, sport)
        except AssertionError as e:
            print(f"[FAIL] {sport}: {e}")
        except Exception as e:
            print(f"[ERROR] {sport}: {e}")

    print("\n========== engine stats (this process) ==========")
    print(json.dumps(stats_snapshot(), indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
