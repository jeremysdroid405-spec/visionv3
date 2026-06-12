"""
add_sgo_player_id_to_hub.py — one-time migration that stamps every doc in
nba_master_hub_2026 with a `sgo_player_id` derived from `normalized_name`.

Derivation:
    normalized_name (lowercase) → uppercase + spaces→_ + "_1_NBA"
    e.g. "austin reaves" → "AUSTIN_REAVES_1_NBA"

Usage:
    python -m scripts.sgo.add_sgo_player_id_to_hub
"""
from __future__ import annotations
import asyncio
import os
import sys

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for _env in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(_env):
        load_dotenv(_env)
        break

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne

HUB_COLL = "nba_master_hub_2026"
BATCH_SIZE = 1_000


def _derive_sgo_player_id(normalized_name: str) -> str:
    return normalized_name.strip().upper().replace(" ", "_") + "_1_NBA"


async def amain() -> None:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    ops: list[UpdateOne] = []
    total = await db[HUB_COLL].count_documents({"normalized_name": {"$exists": True, "$ne": ""}})
    print(f"nba_master_hub_2026: {total:,} docs with normalized_name")

    updated = 0
    skipped = 0

    async for doc in db[HUB_COLL].find(
        {"normalized_name": {"$exists": True, "$ne": ""}},
        {"_id": 1, "normalized_name": 1},
    ):
        name = doc.get("normalized_name", "").strip()
        if not name:
            skipped += 1
            continue
        sgo_id = _derive_sgo_player_id(name)
        ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": {"sgo_player_id": sgo_id}}))
        updated += 1

        if len(ops) >= BATCH_SIZE:
            await db[HUB_COLL].bulk_write(ops, ordered=False)
            ops = []

    if ops:
        await db[HUB_COLL].bulk_write(ops, ordered=False)

    client.close()
    print(f"Done. docs updated: {updated:,}  skipped (blank name): {skipped:,}")


if __name__ == "__main__":
    asyncio.run(amain())
