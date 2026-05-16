"""
Manual Statcast backfill — 2026-04-27 .. today.
Run with: nohup python3 audits/backfill_statcast_now.py > /tmp/statcast_backfill.log 2>&1 &
"""
import asyncio, os, sys
from datetime import datetime, timezone
sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from scripts.mlb_statcast_ingest import ingest_range


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Cover the gap: newest Statcast in DB is 2026-04-26, fetch from
    # 2026-04-27 through today.
    print(f"=== Statcast backfill: 2026-04-27 → {today} ===", flush=True)
    res = await ingest_range(
        db, start="2026-04-27", end=today,
        dry_run=False, chunk_days=7, pause_seconds=1.0,
    )
    print(f"INGEST RESULT: {res}", flush=True)
    cli.close()


if __name__ == "__main__":
    asyncio.run(main())
