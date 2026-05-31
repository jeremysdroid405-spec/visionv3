"""
One-shot dedupe + reindex for player-historical-prop collections.

Removes `snapshot_iso` from the compound unique key (it was a run-time
field, defeating the dedupe contract on re-runs).

For each target collection:
  1. Count existing duplicates by the stable key
     (event_id, player_id, market, line, side, book).
  2. Drop the OLD unique index (which included snapshot_iso).
  3. Per-day dedupe: keep the row with earliest `ingested_at`,
     delete the rest.
  4. Build the NEW unique index without snapshot_iso.
  5. Report row counts before/after + verify the new index is unique.

Per-day chunking keeps memory bounded (max ~50k props/day fits easily).
Read-only on collections we don't target.

Usage:
  python -m scripts.dedupe_player_historical_snapshots --coll mlb_player_historical_props
  python -m scripts.dedupe_player_historical_snapshots --all
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
import time
from typing import Any, Dict, List

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")

OLD_INDEX_KEY = [
    ("event_id", 1), ("player_id", 1), ("market", 1),
    ("line", 1), ("side", 1), ("book", 1),
    ("snapshot_iso", 1),
]
NEW_INDEX_KEY = [
    ("event_id", 1), ("player_id", 1), ("market", 1),
    ("line", 1), ("side", 1), ("book", 1),
]
DEDUPE_KEY_FIELDS = ("event_id", "player_id", "market",
                     "line", "side", "book")

# Per-collection dedupe spec. Player-historical and team-historical
# collections share the same shape — only the entity field differs.
COLL_SPECS: Dict[str, Dict[str, Any]] = {
    "mlb_player_historical_props": {
        "index_name": "ix_mlb_player_hist_compound_unique",
        "entity": "player_id",
        "new_keys": [("event_id", 1), ("player_id", 1),
                       ("market", 1), ("line", 1),
                       ("side", 1), ("book", 1)],
    },
    "nba_player_historical_props": {
        "index_name": "ix_nba_player_hist_compound_unique",
        "entity": "player_id",
        "new_keys": [("event_id", 1), ("player_id", 1),
                       ("market", 1), ("line", 1),
                       ("side", 1), ("book", 1)],
    },
    "nfl_player_historical_props": {
        "index_name": "ix_nfl_player_hist_compound_unique",
        "entity": "player_id",
        "new_keys": [("event_id", 1), ("player_id", 1),
                       ("market", 1), ("line", 1),
                       ("side", 1), ("book", 1)],
    },
    "ncaaf_player_historical_props": {
        "index_name": "ix_ncaaf_player_hist_compound_unique",
        "entity": "player_id",
        "new_keys": [("event_id", 1), ("player_id", 1),
                       ("market", 1), ("line", 1),
                       ("side", 1), ("book", 1)],
    },
    "team_historical_props": {
        "index_name": "ix_hist_prop_compound_unique",
        "entity": "team_id",
        "new_keys": [("event_id", 1), ("team_id", 1),
                       ("market", 1), ("line", 1),
                       ("side", 1), ("book", 1)],
    },
    "nfl_historical_props": {
        "index_name": "ix_nfl_hist_prop_compound_unique",
        "entity": "team_id",
        "new_keys": [("event_id", 1), ("team_id", 1),
                       ("market", 1), ("line", 1),
                       ("side", 1), ("book", 1)],
    },
    "ncaaf_historical_props": {
        "index_name": "ix_ncaaf_hist_prop_compound_unique",
        "entity": "team_id",
        "new_keys": [("event_id", 1), ("team_id", 1),
                       ("market", 1), ("line", 1),
                       ("side", 1), ("book", 1)],
    },
    "team_live_props": {
        "index_name": "ix_live_prop_compound_unique",
        "entity": "team_id",
        "new_keys": [("event_id", 1), ("team_id", 1),
                       ("market", 1), ("line", 1),
                       ("side", 1), ("book", 1)],
    },
}

INDEX_NAMES = {k: v["index_name"] for k, v in COLL_SPECS.items()}


async def _drop_old_index(coll, index_name: str) -> str:
    info = await coll.index_information()
    if index_name in info:
        await coll.drop_index(index_name)
        return f"dropped {index_name}"
    return f"{index_name} not present"


async def _create_new_index(coll, index_name: str,
                                new_keys: List) -> str:
    info = await coll.index_information()
    if index_name in info:
        existing = info[index_name].get("key") or []
        existing_pairs = [(k, int(v)) for k, v in existing]
        if existing_pairs == new_keys:
            return f"{index_name} already matches new key"
        await coll.drop_index(index_name)
    await coll.create_index(new_keys, unique=True, name=index_name)
    return (f"created {index_name} (unique=True, "
            f"keys={[k for k, _ in new_keys]})")


async def _list_days(coll) -> List[str]:
    days: set[str] = set()
    async for d in coll.aggregate(
        [{"$group": {"_id": "$game_date"}}],
        maxTimeMS=120_000, allowDiskUse=True,
    ):
        v = d.get("_id")
        if v:
            days.add(str(v)[:10])
    return sorted(days)


async def _dedupe_day(coll, day: str,
                          dedupe_fields: tuple) -> Dict[str, int]:
    """Per-day dedupe. Keeps the row with the smallest ingested_at;
    falls back to smallest _id when ingested_at ties."""
    pipeline = [
        {"$match": {"game_date": day}},
        {"$sort":  {"ingested_at": 1, "_id": 1}},
        {"$group": {
            "_id": {f: f"${f}" for f in dedupe_fields},
            "keep_id":   {"$first": "$_id"},
            "drop_ids":  {"$push":  "$_id"},
            "count":     {"$sum":   1},
        }},
        {"$match": {"count": {"$gt": 1}}},
    ]
    n_groups = 0
    n_deleted = 0
    batch_ids: List[Any] = []
    BATCH = 5000
    async for grp in coll.aggregate(
        pipeline, allowDiskUse=True, maxTimeMS=600_000,
    ):
        n_groups += 1
        keep = grp["keep_id"]
        for did in grp["drop_ids"]:
            if did != keep:
                batch_ids.append(did)
        if len(batch_ids) >= BATCH:
            rr = await coll.delete_many(
                {"_id": {"$in": batch_ids}})
            n_deleted += int(rr.deleted_count or 0)
            batch_ids.clear()
    if batch_ids:
        rr = await coll.delete_many({"_id": {"$in": batch_ids}})
        n_deleted += int(rr.deleted_count or 0)
    return {"day": day, "n_dup_groups": n_groups,
            "n_deleted": n_deleted}


async def dedupe_collection(db, coll_name: str) -> Dict[str, Any]:
    coll = db[coll_name]
    spec = COLL_SPECS[coll_name]
    index_name = spec["index_name"]
    entity     = spec["entity"]
    new_keys   = spec["new_keys"]
    dedupe_fields = tuple(k for k, _ in new_keys)
    out: Dict[str, Any] = {"coll": coll_name,
                            "entity_field": entity,
                            "new_unique_key":
                                [k for k, _ in new_keys]}

    out["count_before"] = await coll.estimated_document_count()
    out["index_before"] = list((await coll.index_information()).keys())

    # 1. Drop the old (snapshot_iso-including) unique index FIRST so
    # dedupe deletes aren't blocked and so we can later create the new
    # one without conflict.
    out["drop_old"] = await _drop_old_index(coll, index_name)

    # 2. Per-day dedupe loop
    days = await _list_days(coll)
    out["n_days"] = len(days)
    print(f"  → {coll_name}: dedupe across {len(days)} days...")
    total_groups = 0
    total_deleted = 0
    t0 = time.time()
    per_day_summary: List[Dict[str, int]] = []
    for i, day in enumerate(days, 1):
        r = await _dedupe_day(coll, day, dedupe_fields)
        per_day_summary.append(r)
        total_groups  += r["n_dup_groups"]
        total_deleted += r["n_deleted"]
        if i % 25 == 0 or i == len(days):
            elapsed = time.time() - t0
            print(f"    [{i:>4}/{len(days)}] "
                  f"day={day}  cumulative_dups={total_groups:,}  "
                  f"deleted={total_deleted:,}  "
                  f"elapsed={elapsed:.1f}s")
    out["n_dup_groups_total"] = total_groups
    out["n_deleted_total"]    = total_deleted

    # 3. Build new unique index
    out["create_new"] = await _create_new_index(
        coll, index_name, new_keys)

    out["count_after"] = await coll.estimated_document_count()
    out["index_after"] = list((await coll.index_information()).keys())
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(prog="dedupe_player_historical")
    ap.add_argument("--coll", default="",
                    help="collection name (omit with --all)")
    ap.add_argument("--all", action="store_true",
                    help="run on all dedupe-eligible collections")
    args = ap.parse_args()

    if args.all:
        targets = list(COLL_SPECS.keys())
    elif args.coll:
        if args.coll not in COLL_SPECS:
            print(f"ERROR: unknown collection {args.coll!r}",
                  file=sys.stderr); return 2
        targets = [args.coll]
    else:
        print(f"ERROR: pass --coll <name> or --all", file=sys.stderr)
        return 2

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        for coll_name in targets:
            print(f"\n─── dedupe {coll_name} ───")
            existing = await db.list_collection_names()
            if coll_name not in existing:
                print(f"  {coll_name} not present — skipping")
                continue
            r = await dedupe_collection(db, coll_name)
            print(f"\n  count_before  : {r['count_before']:,}")
            print(f"  count_after   : {r['count_after']:,}")
            print(f"  dup_groups    : {r['n_dup_groups_total']:,}")
            print(f"  deleted       : {r['n_deleted_total']:,}")
            print(f"  drop_old      : {r['drop_old']}")
            print(f"  create_new    : {r['create_new']}")
            print(f"  index_after   : {sorted(r['index_after'])}")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
