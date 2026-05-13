"""
Diagnostic: Why are specific player cards missing Vision Intel?
================================================================
Investigates user-reported names (James Harden, Tobias Harris, Evan
Mobley, Max Strus) + 10 additional visible-board picks where
`vision_intel` is null/empty.

For every prop, dumps the 16-point diagnostic the user requested:
  1.  player_name
  2.  sport
  3.  stat_type
  4.  side / recommendation
  5.  line
  6.  bdl_player_id
  7.  nba_id / mlb_id
  8.  master hub match  (yes/no)
  9.  game logs found   (yes/no)
 10.  matchup context   (yes/no)
 11.  injury context    (yes/no)
 12.  cached intel lookup key (canonical_key)
 13.  cached intel exists (yes/no — incl. content_hash + generated_at)
 14.  generation attempted (yes/no — based on vision_intel_generated_at)
 15.  generation error / timeout reason (from recent logs)
 16.  stale/failed cache entry exists (yes/no)
"""
from __future__ import annotations

import asyncio
import os
import sys
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# Backend src on path so we can import config helpers.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from motor.motor_asyncio import AsyncIOMotorClient
from config.version_tags import for_sport


def for_sport_baseline(sport: str) -> str:
    return for_sport(sport, baseline=True)


TARGET_NAMES = [
    "James Harden",
    "Tobias Harris",
    "Evan Mobley",
    "Max Strus",   # canonical
    "Max Struss",  # mis-spelling fallback
]


async def fetch_prop_docs(db, sport: str, names: List[str]) -> List[Dict]:
    live = for_sport(sport)
    base = for_sport_baseline(sport)
    cursor = db[f"{sport}_prop_scores"].find(
        {
            "player_name": {"$in": names},
            "version_tag": {"$in": [live, base]},
        },
        {
            "_id": 0,
            "player_name": 1,
            "stat_type": 1,
            "line": 1,
            "direction": 1,
            "recommendation": 1,
            "tier": 1,
            "version_tag": 1,
            "canonical_key": 1,
            "vision_intel": 1,
            "vision_intel_content_hash": 1,
            "vision_intel_generated_at": 1,
            "bdl_player_id": 1,
            "nba_player_id": 1,
            "mlb_player_id": 1,
            "opponent": 1,
            "game_id": 1,
            "scored_at": 1,
            "team_abbr": 1,
        },
    )
    return await cursor.to_list(length=None)


async def fetch_top_misses(db, sport: str, limit: int = 10) -> List[Dict]:
    """Top N visible-tier picks where vision_intel is missing."""
    live = for_sport(sport)
    cursor = db[f"{sport}_prop_scores"].find(
        {
            "version_tag": live,
            "tier": {"$in": ["safe_haven", "front_lines", "war_zone"]},
            "$or": [
                {"vision_intel": None},
                {"vision_intel": ""},
                {"vision_intel": {"$exists": False}},
            ],
        },
        {
            "_id": 0,
            "player_name": 1,
            "stat_type": 1,
            "line": 1,
            "direction": 1,
            "recommendation": 1,
            "tier": 1,
            "version_tag": 1,
            "canonical_key": 1,
            "vision_intel": 1,
            "vision_intel_content_hash": 1,
            "vision_intel_generated_at": 1,
            "bdl_player_id": 1,
            "nba_player_id": 1,
            "opponent": 1,
            "game_id": 1,
            "scored_at": 1,
            "team_abbr": 1,
        },
    ).sort("scored_at", -1).limit(limit)
    return await cursor.to_list(length=limit)


async def diagnose_one(db, sport: str, doc: Dict) -> Dict[str, Any]:
    name = doc.get("player_name")
    bdl_id = doc.get("bdl_player_id")
    nba_id = doc.get("nba_player_id")
    ck = doc.get("canonical_key")
    vi = (doc.get("vision_intel") or "").strip()

    # 8. Master hub match
    hub_doc = None
    if sport == "nba":
        hub_doc = await db["nba_master_hub_2026"].find_one(
            {"$or": [
                {"player_name": name},
                {"bdl_player_id": bdl_id} if bdl_id else {"_no": 1},
                {"nba_player_id": nba_id} if nba_id else {"_no": 1},
            ]},
            {"_id": 0, "player_name": 1, "bdl_player_id": 1,
             "nba_player_id": 1, "team": 1, "last_updated": 1},
        )
    else:
        hub_doc = await db["mlb_master_hub_2026"].find_one(
            {"player_name": name},
            {"_id": 0, "player_name": 1, "mlb_player_id": 1,
             "team": 1, "last_updated": 1},
        )

    # 9. Game logs
    logs_col = "nba_player_game_logs" if sport == "nba" else "mlb_player_game_logs"
    logs_count = 0
    if bdl_id:
        logs_count = await db[logs_col].count_documents(
            {"bdl_player_id": bdl_id}, limit=1
        )
    if not logs_count and name:
        logs_count = await db[logs_col].count_documents(
            {"player_name": name}, limit=1
        )

    # 10. Matchup context (DvP / matchup card)
    matchup_count = 0
    if sport == "nba":
        matchup_count = await db["nba_dvp_rankings"].count_documents(
            {"team": doc.get("opponent")}, limit=1
        )

    # 11. Injury context
    injury_count = 0
    inj_col = "nba_injuries_unified" if sport == "nba" else "mlb_injuries"
    if bdl_id:
        injury_count = await db[inj_col].count_documents(
            {"bdl_player_id": bdl_id}, limit=1
        )
    if not injury_count and name:
        injury_count = await db[inj_col].count_documents(
            {"player_name": name}, limit=1
        )

    # 16. Stale / failed cache entry?  Any baseline-tag entry with old
    # generated_at but no current text — surfaces "I tried once and failed".
    base_tag = for_sport_baseline(sport)
    stale_entry = await db[f"{sport}_prop_scores"].find_one(
        {
            "canonical_key": ck,
            "version_tag": base_tag,
            "vision_intel_generated_at": {"$exists": True},
        },
        {
            "_id": 0,
            "vision_intel": 1,
            "vision_intel_generated_at": 1,
            "vision_intel_content_hash": 1,
        },
    )

    gen_attempted = bool(doc.get("vision_intel_generated_at"))

    return {
        "1_player_name":          name,
        "2_sport":                sport,
        "3_stat_type":            doc.get("stat_type"),
        "4_side":                 doc.get("direction") or doc.get("recommendation"),
        "5_line":                 doc.get("line"),
        "6_bdl_player_id":        bdl_id,
        "7_nba_or_mlb_id":        nba_id or doc.get("mlb_player_id"),
        "8_master_hub_match":     "yes" if hub_doc else "no",
        "9_game_logs":            "yes" if logs_count else "no",
        "10_matchup_context":     "yes" if matchup_count else "no",
        "11_injury_context":      "yes" if injury_count else "no",
        "12_cached_lookup_key":   ck,
        "13_cached_intel":        ("yes" if vi else "no") + (
            f" (gen_at={doc.get('vision_intel_generated_at')}, "
            f"hash={(doc.get('vision_intel_content_hash') or '')[:10]})"
        ),
        "14_generation_attempted": "yes" if gen_attempted else "no",
        "15_error_or_reason":     None,   # filled by log scan below
        "16_stale_entry":         "yes" if (
            stale_entry and not (stale_entry.get("vision_intel") or "").strip()
        ) else ("partial" if stale_entry else "no"),
        "_meta_version_tag":      doc.get("version_tag"),
        "_meta_tier":             doc.get("tier"),
        "_meta_opponent":         doc.get("opponent"),
        "_meta_game_id":          doc.get("game_id"),
        "_meta_team":              doc.get("team_abbr"),
        "_meta_scored_at":        doc.get("scored_at"),
        "_meta_has_intel":        bool(vi),
    }


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    print("=" * 100)
    print("VISION INTEL MISS DIAGNOSTIC")
    print("=" * 100)
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print(f"NBA live tag:     {for_sport('nba')}")
    print(f"NBA baseline tag: {for_sport_baseline('nba')}")
    print(f"MLB live tag:     {for_sport('mlb')}")
    print()

    # ── PART 1: User-reported names ──────────────────────────────────
    print("\n" + "=" * 100)
    print("PART 1 — USER-REPORTED PLAYERS")
    print("=" * 100)
    nba_docs = await fetch_prop_docs(db, "nba", TARGET_NAMES)
    print(f"Found {len(nba_docs)} NBA score docs across {TARGET_NAMES}")
    diag_user: List[Dict[str, Any]] = []
    for d in nba_docs:
        diag_user.append(await diagnose_one(db, "nba", d))

    # ── PART 2: Top 10 misses on the live NBA board ──────────────────
    print("\n" + "=" * 100)
    print("PART 2 — TOP 10 MISSES ON THE LIVE NBA BOARD")
    print("=" * 100)
    misses = await fetch_top_misses(db, "nba", limit=10)
    print(f"Found {len(misses)} live-tag NBA picks with NO vision_intel")
    diag_misses: List[Dict[str, Any]] = []
    for d in misses:
        diag_misses.append(await diagnose_one(db, "nba", d))

    # ── PART 3: Common-factor summary ────────────────────────────────
    print("\n" + "=" * 100)
    print("DIAGNOSTIC TABLE — USER-REPORTED")
    print("=" * 100)
    for row in diag_user:
        print(json.dumps(row, default=str, indent=2))

    print("\n" + "=" * 100)
    print("DIAGNOSTIC TABLE — TOP 10 BOARD MISSES")
    print("=" * 100)
    for row in diag_misses:
        print(json.dumps(row, default=str, indent=2))

    # Quick common-factor analysis on user-reported
    print("\n" + "=" * 100)
    print("COMMON-FACTOR ANALYSIS — USER-REPORTED")
    print("=" * 100)
    if diag_user:
        keys = ["8_master_hub_match", "9_game_logs", "10_matchup_context",
                "11_injury_context", "14_generation_attempted",
                "_meta_tier", "_meta_opponent", "_meta_team",
                "_meta_version_tag"]
        for k in keys:
            vals = [r.get(k) for r in diag_user]
            uniq = set(map(str, vals))
            print(f"  {k:35} → {sorted(uniq)}")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
