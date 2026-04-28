"""
Single-slate validation harness (multi-sport).

Compares newly ingested `historical_odds_full` for one slate of one
sport against the sport's required validation families.

Confirms (sport-aware):
  ✓ All `validation_required_families` present
  ✓ Alternate markets present (sport-agnostic suffix check)
  ✓ Combo markets present  (only checked when sport defines combos)
  ✓ SH/WZ-routable odds (≤-240 or ≥+150) present
  ✓ Sufficient row volume

USAGE
-----
    python -m scripts.odds_api_backfill.validate_slate \
        --sport basketball_nba --slate 2026-04-22

    python -m scripts.odds_api_backfill.validate_slate \
        --sport baseball_mlb --slate 2026-04-22
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
from scripts.odds_api_backfill.sport_markets import (
    DEFAULT_SPORT, SPORT_CONFIG, SUPPORTED_SPORTS,
    required_validation_families,
)


def _bucket_odds(o):
    if o is None: return "—"
    if o <= -240: return "SH"
    if o >= 150:  return "WZ"
    return "FL"


async def validate(db, *, sport_key: str, slate: str) -> bool:
    print(f"\n[validate] sport={sport_key}  slate={slate}")
    coll = db[COLLECTION_NAME]
    rows = await coll.find(
        {"sport_key": sport_key, "game_date": slate}
    ).to_list(None)
    print(f"  rows ingested              : {len(rows)}")
    if not rows:
        print("  ❌ no rows — backfill did not run on this slate")
        return False

    fams = Counter(r.get("stat_family") for r in rows)
    print(f"  stat_family distribution   : {dict(fams)}")

    alt = sum(1 for r in rows if r.get("is_alternate"))
    combo = sum(1 for r in rows if r.get("is_combo"))
    print(f"  alternate-line rows        : {alt}")
    print(f"  combo-market rows          : {combo}")

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

    # Compare to legacy historical_odds (NBA-only collection) for context.
    if sport_key == "basketball_nba":
        try:
            legacy_n = await db.historical_odds.count_documents({
                "game_date": {
                    "$gte": datetime(*[int(x) for x in slate.split("-")],
                                       tzinfo=timezone.utc),
                    "$lt": datetime(*[int(x) for x in slate.split("-")],
                                       23, 59, 59, tzinfo=timezone.utc),
                }})
            print(f"  legacy historical_odds rows: {legacy_n}")
        except Exception:
            pass
    print()

    required = required_validation_families(sport_key)
    cfg = SPORT_CONFIG.get(sport_key) or {}
    has_combos_defined = bool(cfg.get("combos"))

    checks = []
    for fam in sorted(required):
        checks.append((f"Required family present: {fam}",
                        fams.get(fam, 0) > 0))
    checks.append(("Alternate-line rows present", alt > 0))
    if has_combos_defined:
        checks.append(("Combo markets present", combo > 0))
    checks.append(("SH-routable odds present", routing.get("SH", 0) > 0))
    checks.append(("WZ-routable odds present", routing.get("WZ", 0) > 0))
    checks.append(("Sufficient volume (>500 rows)", len(rows) > 500))

    all_ok = True
    for label, ok in checks:
        print(f"  {'✅' if ok else '❌'}  {label}")
        all_ok = all_ok and ok
    print(f"\n  VERDICT: {'✅ PASS' if all_ok else '❌ FAIL'}")
    return all_ok


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default=DEFAULT_SPORT,
                    choices=list(SUPPORTED_SPORTS))
    ap.add_argument("--slate", required=True, help="UTC YYYY-MM-DD")
    args = ap.parse_args()
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    ok = await validate(db, sport_key=args.sport, slate=args.slate)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
