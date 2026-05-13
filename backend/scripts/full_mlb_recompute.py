"""One-off: full MLB recompute with version_tag=final-mlb-rt to publish
the 11-book de-vig + vision scores into the live board."""
import asyncio, os, sys
from datetime import datetime, timezone

sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient

async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ.get("DB_NAME", "pick_vision")]

    from services.scoring.recompute import recompute_sport

    t0 = datetime.now(timezone.utc)
    print(f"[{t0.isoformat()}] starting full MLB recompute (version_tag=final-mlb-rt)…")
    result = await recompute_sport(
        db=db, sport="mlb", version_tag="final-mlb-rt", dry_run=False
    )
    t1 = datetime.now(timezone.utc)
    print(f"[{t1.isoformat()}] done in {(t1-t0).total_seconds():.1f}s")
    print(f"  processed:   {result.get('processed')}")
    print(f"  written:     {result.get('written')}")
    print(f"  replaced:    {result.get('replaced')}")
    print(f"  skipped:     {result.get('skipped')}")
    print(f"  tier_distribution: {result.get('tier_distribution')}")
    print(f"  quality_source:    {result.get('quality_source_distribution')}")
    c.close()

if __name__ == "__main__":
    asyncio.run(main())
