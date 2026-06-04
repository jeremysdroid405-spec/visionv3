"""
One-shot cleanup — purge stale rows from blocked books in the four
live collections.

Background: the 2026-06-03 audit added blocked books to the policy
SSOT and gated NEW ingestion. But the four LIVE collections still
contained tens of thousands of rows from previously-ingested blocked
books, which kept surfacing as Best Bet / consensus_books on the
dashboard.

This script deletes every row whose book/best_book/bookmaker field
matches `BLOCKED_BOOKS`. It is idempotent — re-running is safe.

Counts BEFORE deletion (audit findings):
  dg_raw_odds_markets : 76,463 rows  (betonlineag 37,873 + betparx 10,548 +
                                       ballybet 7,492 + betrivers 6,238 +
                                       fliff 14,312 + others)
  team_live_props     : ~50 rows  (50+ international books, ~6 rows each)
  nba_prop_scores     : 900 rows  (betonline 392 + betrivers 309 +
                                    betparx 161 + fliff 38)
  mlb_prop_scores     : 146 rows  (fliff 139 + betrivers 5 + betparx 2)
  team_prop_scores    : ~50 rows  (same international rows as team_live_props)

Usage:
  cd /app/backend && python3 scripts/cleanup_blocked_books.py
"""
import asyncio
import os
import sys
from pathlib import Path

# Run from /app/backend so imports resolve
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()


# Collection → list of (field_name, comparison) tuples to check.
# Field values are compared case-insensitively against BLOCKED_BOOKS.
LIVE_COLLECTIONS = {
    "dg_raw_odds_markets": ["bookmaker"],
    "team_live_props":     ["book", "sportsbook"],
    "nba_prop_scores":     ["best_book"],
    "mlb_prop_scores":     ["best_book"],
    "team_prop_scores":    ["best_book", "book"],
}


async def purge_collection(db, coll: str, fields: list, blocked: set) -> int:
    """Delete every doc whose any-field matches a blocked book.

    Builds case-insensitive query. Mongo `$in` is case-sensitive, so
    expand the list with both the lower and original-case alternatives
    that exist in the collection (queried via `distinct`).
    """
    total_deleted = 0
    for f in fields:
        try:
            vals = await db[coll].distinct(f)
        except Exception as e:
            print(f"  [{coll}.{f}] distinct error: {e}")
            continue
        matches = [
            v for v in vals
            if isinstance(v, str) and v.lower() in blocked
        ]
        if not matches:
            continue
        res = await db[coll].delete_many({f: {"$in": matches}})
        n = res.deleted_count
        total_deleted += n
        print(f"  [{coll}.{f}] deleted {n:,} rows  "
              f"books={matches[:6]}{'…' if len(matches)>6 else ''}")
    return total_deleted


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    from services.team_policy import BLOCKED_BOOKS

    print(f"BLOCKED_BOOKS set size: {len(BLOCKED_BOOKS)}")
    print(f"Sample: {sorted(BLOCKED_BOOKS)[:8]}…\n")

    grand_total = 0
    for coll, fields in LIVE_COLLECTIONS.items():
        n_before = await db[coll].estimated_document_count()
        print(f"── {coll} (was {n_before:,} docs) ──")
        deleted = await purge_collection(db, coll, fields, BLOCKED_BOOKS)
        n_after = await db[coll].estimated_document_count()
        print(f"  total deleted: {deleted:,}   docs now: {n_after:,}\n")
        grand_total += deleted

    print(f"GRAND TOTAL deleted: {grand_total:,} rows from blocked books")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
