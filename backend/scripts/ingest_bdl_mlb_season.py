"""
ingest_bdl_mlb_season.py — Download a full BDL MLB season into the hub
========================================================================

Backfill BallDontLie MLB game logs for an entire season (e.g. 2025) so
the PropVision historical replay feature cache can build features for
ANY 2025 replay date (today the hub only has 2026 → feature_cache sees
0 prior games for 2025-05-01 → Layer-3 candidates_skipped_no_cache spikes).

Writes to TWO collections:
  • `bdl_mlb_historical_game_logs` (raw archive, idempotent upserts
    keyed on (bdl_player_id, game_id)) — source of truth
  • `mlb_master_hub_2026.bdl_game_logs[]` per-season merge — the array
    `mlb_replay_feature_cache.cache_date()` already reads from

Player coverage
---------------
Default: fetch FULL roster from `/players?seasons[]=<season>` (cursor-
paginated). Captures retired/season-only players that aren't on the
2026 active roster.

`--use-existing-roster` uses `bdl_id`s already present in the hub
(faster, but may miss retired-2025 players).

Cache-first / idempotent
------------------------
• Raw archive uses upserts → re-running over an already-fetched player
  doesn't duplicate rows.
• `--force` re-fetches every player. Without it, players who already
  have ≥1 row in `bdl_mlb_historical_game_logs` for the requested season
  are skipped (`players_skipped_cached` count printed in the report).

Usage
-----
    python -m scripts.ingest_bdl_mlb_season --season 2025
    python -m scripts.ingest_bdl_mlb_season --season 2025 --force
    python -m scripts.ingest_bdl_mlb_season --season 2025 \\
        --use-existing-roster --limit-players 50
    python -m scripts.ingest_bdl_mlb_season --season 2025 \\
        --skip-stats         # just refresh /players roster

Required env: BDL_API_KEY (must exist in backend/.env).
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient

from services.bdl_universal_sync import (
    BDL_API_KEY, get_bdl_universal_service, BDL_BASE_URLS,
    RAW_MLB_HISTORICAL_COLL,
)
from config.db_config import get_collection_name


# ── Hardcoded spot-check players to verify in the post-run report ──
# These names are pulled from the user's bug example ("Alec Bohm hub
# logs are only 2026") + a pitcher and a star bat for variety. Any of
# them having ≥1 season-N log strongly suggests the backfill worked.
SPOT_CHECK_PLAYERS = ["Alec Bohm", "Aaron Judge", "Paul Skenes",
                       "Shohei Ohtani", "Mookie Betts"]


async def _discover_players_for_season(svc, sport: str,
                                              season: int) -> List[int]:
    """Use BDL `/players?seasons[]=N` to enumerate every player who
    appeared in the given season. Upserts each into the sport's
    master_hub collection (touching only metadata fields — never
    `bdl_game_logs`). Returns the list of `bdl_id`s."""
    params = {"per_page": 100, "seasons[]": season}
    players = await svc._fetch_with_cursor("/players", sport, params,
                                                max_pages=500)
    if not players:
        return []
    master_hub_collection = get_collection_name("master_hub", sport)
    coll = svc.db[master_hub_collection]
    bdl_ids: List[int] = []
    for player in players:
        pid = player.get("id")
        if not pid:
            continue
        bdl_ids.append(pid)
        first_name = player.get("first_name") or ""
        last_name  = player.get("last_name") or ""
        team       = player.get("team") or {}
        doc = {
            "bdl_id":               pid,
            "display_name":         f"{first_name} {last_name}".strip(),
            "first_name":           first_name,
            "last_name":            last_name,
            "team_id":              team.get("id"),
            "team_abbr":            team.get("abbreviation"),
            "team_name":            team.get("full_name"),
            "sport":                sport,
            "bdl_last_seen_season": season,
        }
        if sport == "mlb":
            doc.update({
                "bats":             player.get("bats"),
                "throws":           player.get("throws"),
                "primary_position": player.get("primary_position"),
                "jersey_number":    player.get("jersey_number"),
                "birth_date":       player.get("birth_date"),
            })
        await coll.update_one(
            {"bdl_id": pid},
            {"$set": doc,
              "$setOnInsert": {
                  "first_seen_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
    return bdl_ids


async def _verification_report(db, season: int, sport: str = "mlb") -> Dict[str, Any]:
    """Build the post-run verification block:
      • raw collection counts (rows / distinct players / date range)
      • hub array stats (players with season-N logs)
      • spot-check: each name in SPOT_CHECK_PLAYERS — how many season-N
        logs they ended up with, and the earliest log date.
    """
    raw = db[RAW_MLB_HISTORICAL_COLL]
    hub_coll = get_collection_name("master_hub", sport)
    hub = db[hub_coll]

    n_rows_raw = await raw.count_documents({"season": season})
    distinct_players_raw = len(await raw.distinct(
        "bdl_player_id", {"season": season}))

    minmax = await raw.aggregate([
        {"$match": {"season": season}},
        {"$group": {"_id": None,
                    "min": {"$min": "$date"}, "max": {"$max": "$date"}}},
    ]).to_list(length=1)
    min_date = minmax[0]["min"] if minmax else None
    max_date = minmax[0]["max"] if minmax else None

    hub_players_with_season = await hub.count_documents(
        {"bdl_game_logs.season": season})

    # Per-player spot check
    spot: List[Dict[str, Any]] = []
    for name in SPOT_CHECK_PLAYERS:
        # Match either exact display_name or normalized lower form on
        # either the hub player doc OR the raw archive player_name.
        hub_doc = await hub.find_one(
            {"$or": [
                {"display_name":  {"$regex": f"^{name}$", "$options": "i"}},
                {"player_name":   {"$regex": f"^{name}$", "$options": "i"}},
            ]},
            {"_id": 0, "bdl_id": 1, "display_name": 1,
              "bdl_game_logs_count": 1},
        )
        if not hub_doc:
            spot.append({"name": name, "found_in_hub": False})
            continue
        bdl_id = hub_doc.get("bdl_id")
        # Count season-N logs for this player in both the raw and the
        # hub-array view.
        n_raw = await raw.count_documents(
            {"bdl_player_id": bdl_id, "season": season})
        hub_subq = await hub.aggregate([
            {"$match": {"bdl_id": bdl_id}},
            {"$project": {"_id": 0, "count": {"$size": {"$filter": {
                "input": {"$ifNull": ["$bdl_game_logs", []]},
                "as": "g", "cond": {"$eq": ["$$g.season", season]},
            }}}}},
        ]).to_list(length=1)
        n_hub = hub_subq[0]["count"] if hub_subq else 0
        # Earliest date in raw
        earliest = None
        if n_raw:
            cur = raw.find({"bdl_player_id": bdl_id, "season": season},
                            {"_id": 0, "date": 1}).sort("date", 1).limit(1)
            async for d in cur:
                earliest = d.get("date")
                break
        spot.append({
            "name": name, "found_in_hub": True, "bdl_id": bdl_id,
            f"raw_logs_season_{season}": n_raw,
            f"hub_logs_season_{season}": n_hub,
            "earliest_log_date": earliest,
        })
    return {
        "raw_rows":             n_rows_raw,
        "raw_distinct_players": distinct_players_raw,
        "raw_min_date":         min_date,
        "raw_max_date":         max_date,
        "hub_players_with_season_logs": hub_players_with_season,
        "spot_check":           spot,
    }


async def amain(args):
    if not BDL_API_KEY:
        print("ERROR: BDL_API_KEY missing from backend/.env. Cannot proceed.")
        return 2

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    svc = get_bdl_universal_service(db)

    sport = "mlb"
    season = args.season

    print("=" * 72)
    print(f"  BDL MLB BACKFILL — season={season}")
    print(f"  endpoint: {BDL_BASE_URLS[sport]}")
    print(f"  api_key_set: {bool(BDL_API_KEY)}")
    print(f"  mode: {'use_existing_roster' if args.use_existing_roster else 'discover_2025_roster'}")
    print(f"  force_refetch: {args.force}")
    print("=" * 72)
    started = datetime.now(timezone.utc)

    # ── Step 1 — discover players ───────────────────────────────────
    if args.use_existing_roster:
        master_hub_collection = get_collection_name("master_hub", sport)
        discovered_ids: List[int] = []
        async for d in db[master_hub_collection].find(
            {"bdl_id": {"$exists": True}}, {"bdl_id": 1, "_id": 0}
        ):
            if d.get("bdl_id"):
                discovered_ids.append(d["bdl_id"])
        print(f"\n[discovery] using existing hub roster: "
                  f"{len(discovered_ids)} players")
    else:
        print(f"\n[discovery] fetching /players?seasons[]={season} via cursor…")
        discovered_ids = await _discover_players_for_season(
            svc, sport=sport, season=season,
        )
        print(f"[discovery] {len(discovered_ids)} players for season {season}")

    if not discovered_ids:
        print("\nERROR: zero players discovered. BDL /players may not "
                  "support the seasons[] filter for MLB — retry with "
                  "--use-existing-roster.")
        await svc.close_client()
        cli.close()
        return 1

    if args.limit_players:
        discovered_ids = discovered_ids[: int(args.limit_players)]
        print(f"[discovery] limited to first {len(discovered_ids)} players "
                  f"for testing.")

    # ── Step 2 — fetch + persist game logs ──────────────────────────
    stats_summary: Dict[str, Any] = {}
    if not args.skip_stats:
        print(f"\n[stats] sync_stats_batched("
                  f"sport={sport!r}, season={season}, "
                  f"player_ids=<{len(discovered_ids)}>, "
                  f"merge_by_season=True, also_save_raw=True, "
                  f"skip_already_stored={not args.force})")
        stats_summary = await svc.sync_stats_batched(
            sport=sport, player_ids=discovered_ids,
            season=season,
            merge_by_season=True,
            also_save_raw=True,
            skip_already_stored=(not args.force),
        )

    # ── Step 3 — verification report ────────────────────────────────
    report = await _verification_report(db, season, sport=sport)
    duration = (datetime.now(timezone.utc) - started).total_seconds()

    print()
    print("─" * 72)
    print(f"  COVERAGE SUMMARY  (season={season})")
    print("  ────────────────")
    print(f"  discovered_players                  {len(discovered_ids)}")
    print(f"  players_skipped_cached              "
              f"{stats_summary.get('players_skipped_cached', 0)}")
    print(f"  api stats requested for             "
              f"{stats_summary.get('players_requested', 0)}")
    print(f"  api stats returned (players_with)   "
              f"{stats_summary.get('players_with_stats', 0)}")
    print(f"  api total_game_logs                 "
              f"{stats_summary.get('total_game_logs', 0)}")
    print(f"  errors                              "
              f"{len(stats_summary.get('errors') or [])}")
    print()
    print(f"  raw archive collection              {RAW_MLB_HISTORICAL_COLL}")
    print(f"     rows                             {report['raw_rows']}")
    print(f"     distinct players                 "
              f"{report['raw_distinct_players']}")
    print(f"     min/max date                     "
              f"{report['raw_min_date']} .. {report['raw_max_date']}")
    print()
    print(f"  hub players w/ season-{season} logs       "
              f"{report['hub_players_with_season_logs']}")
    print()
    print(f"  spot-check (per-player season-{season} logs):")
    for sc in report["spot_check"]:
        if not sc.get("found_in_hub"):
            print(f"     {sc['name']:.<28} NOT FOUND in hub")
            continue
        print(f"     {sc['name']:.<28} bdl_id={sc.get('bdl_id')} "
                  f"raw={sc.get(f'raw_logs_season_{season}')}  "
                  f"hub={sc.get(f'hub_logs_season_{season}')}  "
                  f"earliest={sc.get('earliest_log_date')}")
    if stats_summary.get("errors"):
        print()
        print("  errors[:3]:")
        for e in stats_summary["errors"][:3]:
            print(f"    - {e}")
    print()
    print(f"  duration                            {duration:.1f}s")
    print("─" * 72)

    # Hard-fail when we asked for stats but landed 0 rows
    if (not args.skip_stats and report["raw_rows"] == 0
            and stats_summary.get("players_skipped_cached", 0) == 0):
        print(f"\nERROR: 0 rows in {RAW_MLB_HISTORICAL_COLL} for season "
                  f"{season} after the run. Check BDL API key/endpoint.")
        await svc.close_client()
        cli.close()
        return 1

    await svc.close_client()
    cli.close()
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--season", type=int, required=True,
                      help="Season year (e.g. 2025).")
    p.add_argument("--use-existing-roster", action="store_true",
                      help="Skip /players discovery; use bdl_ids already "
                              "in mlb_master_hub_2026.")
    p.add_argument("--skip-stats", action="store_true",
                      help="Only run player discovery; don't fetch game logs.")
    p.add_argument("--limit-players", type=int, default=None,
                      help="Limit the number of players synced (for testing).")
    p.add_argument("--force", action="store_true",
                      help="Re-fetch every player even if they already "
                              "have season-N rows in the raw collection. "
                              "Default = cache-first (skip already stored).")
    args = p.parse_args()
    sys.exit(asyncio.run(amain(args)))


if __name__ == "__main__":
    main()
