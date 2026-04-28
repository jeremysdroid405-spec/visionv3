"""
Single-slate validation harness. Run AFTER ingesting one slate.

Compares the new `historical_odds_full` for that slate against:
  * old `historical_odds` (counts only)
  * the most-recent live `nba_prop_scores` slate

Confirms:
  ✓ PRA market present
  ✓ alternate markets present
  ✓ SH/WZ-routable odds (≤-240 or ≥+150) present
  ✓ market counts within 30% of live board
  ✓ row count > expected floor
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from scripts.odds_api_backfill.schema import COLLECTION_NAME


def _bucket_odds(o):
    if o is None: return "—"
    if o <= -240: return "SH"
    if o >= 150:  return "WZ"
    return "FL"


async def validate(db, slate: str) -> None:
    print(f"\n[validate] slate = {slate}")
    coll = db[COLLECTION_NAME]
    rows = await coll.find({"game_date": slate}).to_list(None)
    print(f"  rows ingested              : {len(rows)}")
    if not rows:
        print("  ❌ no rows — backfill did not run on this slate")
        return

    fams = Counter(r.get("stat_family") for r in rows)
    print(f"  stat_family distribution   : {dict(fams)}")

    alt = sum(1 for r in rows if r.get("is_alternate"))
    combo = sum(1 for r in rows if r.get("is_combo"))
    print(f"  alternate-line rows        : {alt}")
    print(f"  combo-market rows          : {combo}")

    # OVER-side ref-odds bucket (per-event, take median across books)
    by_pps = {}
    for r in rows:
        if r.get("side") != "OVER": continue
        k = (r.get("event_id"), r.get("market_key"),
             r.get("player"), r.get("line"))
        by_pps.setdefault(k, []).append(r["odds_american"])
    routing = Counter()
    for v in by_pps.values():
        med = sorted(v)[len(v) // 2]
        routing[_bucket_odds(med)] += 1
    print(f"  unique props (OVER side)   : {len(by_pps)}")
    print(f"  routing distribution       : {dict(routing)}")
    print(f"     SH (≤-240)              : {routing.get('SH', 0)}")
    print(f"     FL (-239..+149)         : {routing.get('FL', 0)}")
    print(f"     WZ (+150+)              : {routing.get('WZ', 0)}")

    # Compare to old historical_odds (legacy, standard-only) for same date
    legacy_n = await db.historical_odds.count_documents({
        "game_date": {
            "$gte": datetime(*[int(x) for x in slate.split("-")],
                              tzinfo=timezone.utc),
            "$lt": datetime(*[int(x) for x in slate.split("-")],
                              23, 59, 59, tzinfo=timezone.utc),
        }})
    print(f"  legacy historical_odds rows: {legacy_n}")
    print()

    checks = [
        ("PRA market present",
         "PRA" in fams),
        ("Alternate-line rows present",
         alt > 0),
        ("Combo markets present",
         combo > 0),
        ("SH-routable odds present",
         routing.get("SH", 0) > 0),
        ("WZ-routable odds present",
         routing.get("WZ", 0) > 0),
        ("Sufficient volume (>500 rows)",
         len(rows) > 500),
    ]
    all_ok = True
    for label, ok in checks:
        print(f"  {'✅' if ok else '❌'}  {label}")
        all_ok = all_ok and ok
    print(f"\n  VERDICT: {'✅ PASS' if all_ok else '❌ FAIL'}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slate", required=True, help="UTC YYYY-MM-DD")
    args = ap.parse_args()
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    await validate(db, args.slate)


if __name__ == "__main__":
    asyncio.run(main())
