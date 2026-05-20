"""
verify_sgo_player_stats_coverage.py — coverage / gap report for sgo_player_stats.

Read-only. Compares:
  driver:  distinct (event_id, player_id, stat_id, side, line, period_id)
           in sgo_pp_research_core_enriched
  stats :  sgo_player_stats (keyed by event_id + player_id)
  graded:  sgo_pp_research_outcomes (where outcome_resolved=True)

Prints:
  - event coverage           : % of driver event_ids with ≥1 player_stats row
  - player coverage          : % of driver (event_id, player_id) tuples present in stats
  - alt-mapping coverage     : how many gaps are resolvable via stat_entity_id / player_name
  - outcome resolution rate  : % of driver rows where outcomes pipeline resolved
  - stat_family x resolution : breakdown by stat_family
  - sample unresolved rows   : actionable list (event_id, player_id, stat_id, reasons)

Usage:
    python -m scripts.sgo.verify_sgo_player_stats_coverage \\
        --league MLB --start 2025-06-01 --end 2025-06-30
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path); break

from motor.motor_asyncio import AsyncIOMotorClient

DRIVER = "sgo_pp_research_core_enriched"
STATS  = "sgo_player_stats"
OUTS   = "sgo_pp_research_outcomes"


async def amain(args: argparse.Namespace) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    match: Dict[str, Any] = {}
    if args.league: match["league_id"] = args.league
    if args.start or args.end:
        gd: Dict[str, Any] = {}
        if args.start: gd["$gte"] = args.start
        if args.end:   gd["$lte"] = args.end
        match["game_date"] = gd

    print("=" * 72)
    print("  sgo_player_stats coverage report")
    print(f"  filter: league={args.league or '(all)'}  "
          f"window=[{args.start or 'all'} .. {args.end or 'all'}]")
    print("=" * 72)

    # 1. Driver universe
    driver_events: set = set()
    driver_pairs: set = set()  # (event_id, player_id)
    driver_player_names: dict = {}  # (event_id, player_name_lower) → player_id
    async for d in db[DRIVER].find(match,
        {"_id": 0, "event_id": 1, "player_id": 1, "player_name": 1}):
        e = d.get("event_id"); p = d.get("player_id")
        if e: driver_events.add(e)
        if e and p: driver_pairs.add((e, p))
        nm = (d.get("player_name") or "").strip().lower()
        if e and nm:
            driver_player_names[(e, nm)] = p
    print(f"  driver events:          {len(driver_events):,}")
    print(f"  driver (event,player):  {len(driver_pairs):,}")

    # 2. Stats universe (restricted to driver events to keep memory small)
    stats_match: Dict[str, Any] = {}
    if driver_events:
        stats_match["event_id"] = {"$in": list(driver_events)}
    stats_events: set = set()
    stats_pairs: set = set()
    stats_entity_pairs: set = set()  # (event_id, stat_entity_id)
    stats_name_pairs: set = set()    # (event_id, name_lower)
    sources_counter: Dict[str, int] = {}
    async for s in db[STATS].find(stats_match,
        {"_id": 0, "event_id": 1, "player_id": 1, "stat_entity_id": 1,
          "player_name": 1, "source": 1}):
        e = s.get("event_id"); p = s.get("player_id")
        if e: stats_events.add(e)
        if e and p: stats_pairs.add((e, p))
        ent = s.get("stat_entity_id")
        if e and ent: stats_entity_pairs.add((e, ent))
        nm = (s.get("player_name") or "").strip().lower()
        if e and nm: stats_name_pairs.add((e, nm))
        src = s.get("source") or "?"
        sources_counter[src] = sources_counter.get(src, 0) + 1

    pct = lambda a, b: (100.0 * a / b) if b else 0.0
    event_cov = len(driver_events & stats_events)
    player_cov = len(driver_pairs & stats_pairs)
    print()
    print(f"  COVERAGE")
    print(f"  ────────")
    print(f"  events with ≥1 stat row:  {event_cov:,} / {len(driver_events):,}  "
          f"({pct(event_cov, len(driver_events)):.2f}%)")
    print(f"  (event, player) direct:   {player_cov:,} / {len(driver_pairs):,}  "
          f"({pct(player_cov, len(driver_pairs)):.2f}%)")
    # Alt joins
    missing = driver_pairs - stats_pairs
    alt_entity = sum(1 for (e, p) in missing if (e, p) in stats_entity_pairs)
    alt_name = 0
    for (e, p) in missing:
        for (en, nm), pid_seed in driver_player_names.items():
            if en == e and pid_seed == p and (e, nm) in stats_name_pairs:
                alt_name += 1; break
    print(f"  recoverable via stat_entity_id:  {alt_entity:,}")
    print(f"  recoverable via player_name:     {alt_name:,}")
    print(f"  truly missing:                   "
          f"{len(missing) - alt_entity - alt_name:,}")

    # 3. Sources distribution
    if sources_counter:
        print()
        print(f"  SOURCES (sgo_player_stats rows by source)")
        print(f"  ─────────────────────────────────────────")
        for s, n in sorted(sources_counter.items(), key=lambda kv: -kv[1]):
            print(f"    {s:<20s} {n:,}")

    # 4. Outcome resolution rate (if outcomes collection populated)
    out_match = dict(match)
    n_outcomes_total = await db[OUTS].count_documents(out_match)
    n_resolved = await db[OUTS].count_documents(
        {**out_match, "outcome_resolved": True})
    n_unresolved = await db[OUTS].count_documents(
        {**out_match, "outcome_resolved": False})
    print()
    print(f"  OUTCOME RESOLUTION")
    print(f"  ──────────────────")
    print(f"  outcomes total:    {n_outcomes_total:,}")
    print(f"  resolved:          {n_resolved:,}  "
          f"({pct(n_resolved, n_outcomes_total):.2f}%)")
    print(f"  unresolved:        {n_unresolved:,}  "
          f"({pct(n_unresolved, n_outcomes_total):.2f}%)")

    if n_outcomes_total > 0:
        # By stat_family
        print(f"\n  By stat_family (resolved / total):")
        async for r in db[OUTS].aggregate([
            {"$match": out_match},
            {"$group": {"_id": "$stat_family",
                          "total": {"$sum": 1},
                          "resolved": {"$sum": {"$cond":
                              ["$outcome_resolved", 1, 0]}}}},
            {"$sort": {"total": -1}},
        ], allowDiskUse=True):
            fam = r.get("_id") or "(none)"
            t = r["total"]; rv = r["resolved"]
            print(f"    {fam:<30s} {rv:,} / {t:,}  "
                  f"({pct(rv, t):.1f}%)")

        # Sample unresolved rows
        print(f"\n  Sample unresolved rows (first 10):")
        async for d in db[OUTS].find(
            {**out_match, "outcome_resolved": False},
            {"_id": 0, "event_id": 1, "player_id": 1, "player_name": 1,
              "stat_id": 1, "side": 1, "line": 1}
        ).limit(10):
            print(f"    {d.get('event_id')}  player={d.get('player_id')}  "
                  f"name='{d.get('player_name')}'  stat={d.get('stat_id')} "
                  f"{d.get('side')} {d.get('line')}")

    print("=" * 72)
    client.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--league", default=None)
    p.add_argument("--start",  default=None)
    p.add_argument("--end",    default=None)
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
