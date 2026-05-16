"""Run batter + pitcher feature builders end-to-end after backfill."""
import asyncio, os, sys, time
sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    print("=== STEP 1/2: build batter features ===", flush=True)
    t0 = time.time()
    from scripts.mlb_statcast_build_features import build_features
    res = await build_features(db, since=None, player=None, dry_run=False)
    print(f"  batter_features: {res} ({time.time()-t0:.1f}s)", flush=True)

    print("=== STEP 2/2: build pitcher features ===", flush=True)
    t1 = time.time()
    from scripts.mlb_statcast_build_pitcher_features import build
    res2 = await build(db, since=None, dry_run=False)
    print(f"  pitcher_features: {res2} ({time.time()-t1:.1f}s)", flush=True)

    # Verify freshness
    print("\n=== POST-RUN FRESHNESS CHECK ===", flush=True)
    newest_pl = await db.mlb_statcast_player_features.find_one(
        {}, sort=[("game_date", -1)], projection={"_id": 0, "game_date": 1})
    newest_p = await db.mlb_statcast_pitcher_features.find_one(
        {}, sort=[("game_date", -1)], projection={"_id": 0, "game_date": 1})
    newest_raw = await db.mlb_statcast_raw.find_one(
        {}, sort=[("game_date", -1)], projection={"_id": 0, "game_date": 1})
    print(f"  newest mlb_statcast_raw           : {newest_raw}")
    print(f"  newest mlb_statcast_player_features: {newest_pl}")
    print(f"  newest mlb_statcast_pitcher_features: {newest_p}")
    cli.close()


if __name__ == "__main__":
    asyncio.run(main())
