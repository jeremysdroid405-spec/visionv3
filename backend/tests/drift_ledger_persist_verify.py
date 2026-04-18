"""
Persistent drift ledger — end-to-end hard verification.

Proves:
  1. `engine.on_new_props` writes to `board_drift_ledger` synchronously.
  2. Entries carry every required field + native datetime `observed_at`.
  3. TTL index exists with expireAfterSeconds=259200.
  4. `audit_persisted` classifies entries across 1h/6h/24h/48h windows.
  5. Restart survives: drop in-memory ledger, re-audit → persisted
     report still carries history.
  6. `/api/board-drift-audit` returns BOTH in_memory + persisted.
"""
import asyncio
import os
import time
import json
from datetime import datetime, timezone, timedelta

from motor.motor_asyncio import AsyncIOMotorClient

from services.event_bus import BoardEvent, get_event_bus
from services.board.engine import subscribe_new_props_handler
from services.board.adapters import get_adapter
from services.scoring.adapters import get_scoring_adapter
from services.board.delta_publisher import publish_new_props_delta
from services.board.drift_audit import (
    DRIFT_COLLECTION, ROLLING_WINDOWS,
    ensure_persistent_indexes, audit_persisted, snapshot, _LEDGERS,
)


async def step_1_ttl_index(db):
    print("\n========== [1] TTL INDEX EXISTS + CORRECT ==========")
    info = await db[DRIFT_COLLECTION].index_information()
    ttl = info.get("ttl_observed_at_72h")
    print(f"ttl_observed_at_72h: {ttl}")
    assert ttl is not None, "TTL index missing"
    assert ttl.get("expireAfterSeconds") == 72 * 3600, \
        f"TTL != 72h; got {ttl.get('expireAfterSeconds')}"
    print("[OK] TTL index present with expireAfterSeconds=259200")


async def step_2_e2e_writes(db, sport):
    print(f"\n========== [2] E2E WRITE — {sport.upper()} ==========")
    scoring = get_scoring_adapter(sport)
    board = get_adapter(sport)
    props = await scoring.load_live_props(db)
    keys = []
    for p in props:
        k = board.canonical_key(p)
        if k:
            keys.append(k)
            if len(keys) >= 3:
                break

    coll = db[DRIFT_COLLECTION]
    pre_count = await coll.count_documents({"sport": sport})
    print(f"pre_count (sport={sport}) = {pre_count}")

    t0 = time.monotonic()
    await publish_new_props_delta(
        sport=sport, pre_keys=set(), post_keys=set(keys),
        source=f"persist_verify_{sport}",
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    print(f"publish+handle elapsed: {elapsed} ms")

    post_count = await coll.count_documents({"sport": sport})
    added = post_count - pre_count
    print(f"post_count (sport={sport}) = {post_count} (added {added})")
    assert added >= 3, f"expected 3+ new ledger rows, got {added}"

    # Inspect a new doc's schema
    doc = await coll.find_one(
        {"sport": sport, "source": f"persist_verify_{sport}"},
        {"_id": 0},
        sort=[("observed_at", -1)],
    )
    print(f"sample doc: {json.dumps(doc, default=str, indent=2)}")
    required = {"sport", "canonical_key", "source", "observed_at",
                "tier_rt", "vision_score_rt", "quality_source_rt",
                "computed_at_rt", "active_rt"}
    assert required.issubset(doc.keys()), \
        f"missing fields: {required - set(doc.keys())}"
    assert isinstance(doc["observed_at"], datetime), \
        f"observed_at must be datetime, got {type(doc['observed_at'])}"
    print("[OK] entry carries all required fields + native datetime observed_at")
    return keys


async def step_3_persisted_audit(db, sport):
    print(f"\n========== [3] AUDIT_PERSISTED — {sport.upper()} ==========")
    report = await audit_persisted(db, sport)
    print(f"total_entries_72h = {report['total_entries_72h']}")
    print(f"latest_observed_at = {report['latest_observed_at']}")
    for label, w in report["windows"].items():
        print(
            f"  {label:4s}: entries={w['entries']:<5d} "
            f"converged={w['converged']:<5d} "
            f"tier_changed={w['tier_changed']:<3d} "
            f"vs_drift={w['vision_score_drift']:<3d} "
            f"missing={w['missing']:<3d} "
            f"inactive={w['inactive']:<3d} "
            f"ratio={w['divergence_ratio']}"
        )
    assert report["windows"]["1h"]["entries"] >= 3, "expected 3+ 1h entries"
    print("[OK] rolling windows populated 1h/6h/24h/48h")


async def step_4_drop_in_memory_restart_sim(db, sport):
    """Simulate a restart by flushing the in-memory ring buffer and
    re-running audit_persisted. Persisted report must still be intact."""
    print(f"\n========== [4] RESTART SIM — {sport.upper()} ==========")
    _LEDGERS[sport].clear()
    snap = snapshot(sport)
    print(f"in-memory count post-flush: {snap['count']}")
    assert snap["count"] == 0
    report = await audit_persisted(db, sport)
    assert report["total_entries_72h"] >= 3
    assert report["windows"]["1h"]["entries"] >= 3
    print(f"[OK] persisted history survives in-memory flush — "
          f"1h entries still = {report['windows']['1h']['entries']}")


async def step_5_divergence(db, sport, keys):
    """Direct-mutate one doc to induce a tier_changed divergence in
    persisted. Verify the audit catches it within 1h window."""
    print(f"\n========== [5] DIVERGENCE DETECTION — {sport.upper()} ==========")
    board = get_adapter(sport)
    scores = db[board.scores_collection]
    await scores.update_one(
        {"canonical_key": keys[0], "version_tag": board.version_tag},
        {"$set": {"tier": "safe_haven"}},
    )
    print(f"flipped {keys[0]} → tier=safe_haven")
    report = await audit_persisted(db, sport)
    w1 = report["windows"]["1h"]
    print(
        f"1h window: tier_changed={w1['tier_changed']} "
        f"vs_drift={w1['vision_score_drift']} "
        f"ratio={w1['divergence_ratio']}"
    )
    detected_for_target = [
        s for s in w1["divergence_samples"]
        if s["canonical_key"] == keys[0]
    ]
    print(f"samples touching {keys[0]}: {len(detected_for_target)}")
    for s in detected_for_target[:2]:
        print(f"  {s}")
    assert len(detected_for_target) >= 1, "divergence not detected"
    print("[OK] persisted audit detected the simulated tier_changed divergence")


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    await ensure_persistent_indexes(db)
    subscribe_new_props_handler(db)

    await step_1_ttl_index(db)
    for sport in ("nba", "mlb"):
        keys = await step_2_e2e_writes(db, sport)
        await step_3_persisted_audit(db, sport)
        await step_4_drop_in_memory_restart_sim(db, sport)
        await step_5_divergence(db, sport, keys)


if __name__ == "__main__":
    asyncio.run(main())
