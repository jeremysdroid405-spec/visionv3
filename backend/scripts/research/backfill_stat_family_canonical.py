"""
backfill_stat_family_canonical.py — one-shot normalizer for legacy
`stat_family` values stored in:

    mlb_replay_feature_cache.stat_family
    mlb_replay_model_outputs.stat_family

These were written BEFORE the canonical_stats SSOT cutover (2026-05-18).
Some legacy tokens (`strikeouts`, `pitcher_walks`, `hits+runs+rbis`, etc.)
need to be rewritten to their canonical equivalents
(`batter_strikeouts`, `walks_allowed`, `hits_runs_rbis`).

Pure, idempotent: rows that are already canonical are skipped.

Safe by default:
  - `--dry-run` (default ON)  →  reports counts only, NO writes.
  - `--commit`                →  performs the bulk_write.
  - `--collection`            →  restrict to one collection at a time.
  - `--league`                →  restrict by league_id (MLB only).

Usage examples
    # Preview only
    python -m scripts.research.backfill_stat_family_canonical \\
        --collection mlb_replay_feature_cache --dry-run

    # Commit on outputs
    python -m scripts.research.backfill_stat_family_canonical \\
        --collection mlb_replay_model_outputs --commit
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
from collections import Counter
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne

from services.scoring.canonical_stats import canonical_family

# Collections this script is allowed to touch. Anything else is rejected
# even if the user passes it — defensive guard.
ALLOWED_COLLECTIONS = {
    "mlb_replay_feature_cache",
    "mlb_replay_model_outputs",
}


async def _scan_and_plan(
    db,
    coll: str,
    sport: str,
    match: Dict[str, Any],
    sample_limit: int = 20,
) -> Dict[str, Any]:
    """Walk the collection, classify rows as (already-canonical | needs-update
    | unrecognised), build a bulk-update plan. Pure on the caller side: we
    never write here, only collect."""
    plan: List[UpdateOne] = []
    seen = 0
    already_canonical = 0
    needs_update = 0
    unrecognised = 0
    by_legacy = Counter()       # legacy -> canonical mapping breakdown
    samples_unrecognised: List[Dict[str, Any]] = []

    proj = {"_id": 1, "stat_family": 1, "player_name": 1, "market": 1}
    async for row in db[coll].find(match, proj):
        seen += 1
        legacy = row.get("stat_family")
        if not legacy:
            unrecognised += 1
            if len(samples_unrecognised) < sample_limit:
                samples_unrecognised.append({
                    "_id": str(row["_id"]),
                    "stat_family": legacy,
                    "player_name": row.get("player_name"),
                    "market": row.get("market"),
                })
            continue

        canon = canonical_family(sport, legacy)
        if canon == legacy:
            already_canonical += 1
            continue

        # If canonical_family returned the SAME (no alias resolved), but the
        # legacy string differs in case / underscore form, normalize to lower.
        if canon and canon != legacy:
            needs_update += 1
            by_legacy[(legacy, canon)] += 1
            plan.append(UpdateOne(
                {"_id": row["_id"]},
                {"$set": {"stat_family": canon,
                            "stat_family_legacy": legacy}},
            ))

    return {
        "coll": coll,
        "seen": seen,
        "already_canonical": already_canonical,
        "needs_update": needs_update,
        "unrecognised": unrecognised,
        "by_mapping": dict(by_legacy),
        "samples_unrecognised": samples_unrecognised,
        "_plan": plan,
    }


async def amain(args: argparse.Namespace) -> int:
    if args.collection not in ALLOWED_COLLECTIONS:
        print(f"[ERROR] collection '{args.collection}' not in "
                f"ALLOWED_COLLECTIONS={sorted(ALLOWED_COLLECTIONS)}",
                file=sys.stderr)
        return 2

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    match: Dict[str, Any] = {}
    if args.league:
        # mlb_replay_* uses sport=mlb implicitly via the collection name,
        # but feature_cache rows may carry league_id when shared collections.
        match["league_id"] = args.league

    print("=" * 72)
    print("  STAT_FAMILY CANONICALISATION BACKFILL")
    print(f"  collection : {args.collection}")
    print("  sport      : mlb")
    print(f"  filter     : {match or '<all rows>'}")
    print(f"  mode       : {'COMMIT' if args.commit else 'DRY-RUN'}")
    print("=" * 72)

    result = await _scan_and_plan(db, args.collection, sport="mlb",
                                     match=match,
                                     sample_limit=args.sample_limit)

    print(f"\n  rows scanned          : {result['seen']:,}")
    print(f"  already canonical     : {result['already_canonical']:,}")
    print(f"  needs update          : {result['needs_update']:,}")
    print(f"  unrecognised / blank  : {result['unrecognised']:,}")

    if result["by_mapping"]:
        print("\n  ── mapping breakdown ──")
        for (legacy, canon), n in sorted(
            result["by_mapping"].items(), key=lambda kv: -kv[1],
        ):
            print(f"    {legacy!r:<26} → {canon!r:<26} ({n:,})")

    if result["samples_unrecognised"]:
        print("\n  ── sample rows with blank/unmappable stat_family ──")
        for s in result["samples_unrecognised"][:10]:
            print(f"    {s}")

    if not args.commit:
        print("\n  [DRY-RUN] No writes performed. Re-run with --commit.")
        return 0

    plan = result["_plan"]
    if not plan:
        print("\n  [COMMIT] nothing to update.")
        return 0

    # Bulk write in chunks to avoid huge single ops on prod
    chunk_size = max(1, int(args.chunk_size))
    total_modified = 0
    total_matched = 0
    for i in range(0, len(plan), chunk_size):
        sub = plan[i : i + chunk_size]
        r = await db[args.collection].bulk_write(sub, ordered=False)
        total_modified += r.modified_count
        total_matched += r.matched_count
        print(f"  bulk_write chunk {i // chunk_size + 1}: "
                f"matched={r.matched_count} modified={r.modified_count}")

    print(f"\n  [COMMIT DONE] matched={total_matched} "
            f"modified={total_modified}")
    return 0


def _parse():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--collection", required=True,
                     choices=sorted(ALLOWED_COLLECTIONS),
                     help="Target collection")
    p.add_argument("--league", default=None,
                     help="Restrict by league_id (e.g. MLB)")
    p.add_argument("--commit", action="store_true",
                     help="Perform writes (default is dry-run)")
    p.add_argument("--dry-run", action="store_true",
                     help="Force dry-run (default if --commit absent)")
    p.add_argument("--chunk-size", type=int, default=500,
                     help="Bulk-write chunk size (default 500)")
    p.add_argument("--sample-limit", type=int, default=20,
                     help="Max sample rows to print for unrecognised values")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(amain(_parse())))
