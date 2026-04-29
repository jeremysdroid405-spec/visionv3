"""Targeted MLB advanced-stats sync for the current slate.
Calls the existing MLBAdvancedStatsSync.fetch_player_splits() but
only for players appearing in the live slate. Writes vs_left,
vs_right, home_splits, away_splits onto each master_hub_2026 doc.
Skips season-stats / days-of-rest steps (those are unrelated to
the platoon/home-away wire fix).
"""
import asyncio
import logging
import os
import sys
import time

from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("slate_splits_sync")


async def main():
    from services.mlb_advanced_stats_sync import MLBAdvancedStatsSync

    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    master_hub = db["mlb_master_hub_2026"]

    # Pick the season that has full splits (2025 confirmed)
    season = int(os.environ.get("SPLITS_SEASON", "2025"))

    # Players in current slate
    players_in_slate = await db["mlb_live_props"].distinct("player_name")
    logger.info(f"slate players: {len(players_in_slate)}")

    # Load only those master_hub docs with bdl_id
    cursor = master_hub.find(
        {"$or": [{"display_name": {"$in": players_in_slate}},
                  {"mlb_full_name": {"$in": players_in_slate}}],
         "bdl_id": {"$exists": True, "$ne": None}},
        {"_id": 0, "bdl_id": 1, "display_name": 1, "mlb_full_name": 1,
         "vs_left": 1, "vs_right": 1, "home_splits": 1, "away_splits": 1},
    )
    docs = await cursor.to_list(length=2000)
    logger.info(f"master_hub docs to sync: {len(docs)}")

    sync = MLBAdvancedStatsSync(db)
    t0 = time.time()
    n_ok = 0
    n_no_data = 0
    n_err = 0

    for i, doc in enumerate(docs):
        try:
            splits = await sync.fetch_player_splits(doc["bdl_id"], season)
            if not splits:
                n_no_data += 1
                continue
            update = {}
            if splits.get("vs_left"):
                update["vs_left"] = splits["vs_left"]
            if splits.get("vs_right"):
                update["vs_right"] = splits["vs_right"]
            if splits.get("home"):
                update["home_splits"] = splits["home"]
            if splits.get("away"):
                update["away_splits"] = splits["away"]
            if not update:
                n_no_data += 1
                continue
            await master_hub.update_one(
                {"bdl_id": doc["bdl_id"]},
                {"$set": update},
            )
            n_ok += 1
        except Exception as e:  # pragma: no cover
            n_err += 1
            logger.warning(f"err for bdl_id={doc.get('bdl_id')}: {e}")

        if (i + 1) % 25 == 0:
            elapsed = time.time() - t0
            logger.info(f"  progress {i+1}/{len(docs)}  ok={n_ok} no_data={n_no_data} err={n_err}  elapsed={elapsed:.1f}s")

    elapsed = time.time() - t0
    logger.info(f"DONE in {elapsed:.1f}s  ok={n_ok} no_data={n_no_data} err={n_err}")

    await sync._client.aclose() if sync._client else None


if __name__ == "__main__":
    asyncio.run(main())
