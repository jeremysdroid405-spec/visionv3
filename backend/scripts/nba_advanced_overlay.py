#!/usr/bin/env python3
"""
NBA Advanced Stats Overlay - Async 15x Parallel
================================================
Enriches each game log in nba_master_hub_2026.history arrays
with advanced metrics from BDL /v1/stats/advanced endpoint.

Metrics merged per game log:
  usage_pct, true_shooting_pct, off_rating, def_rating,
  pace, ast_pct, reb_pct, net_rating, pie, eFG_pct, turnover_ratio

No-Loss Merge: Only $set the history arrays. All other fields preserved.
"""

import asyncio
import aiohttp
import time
import os
from datetime import datetime, timezone
from pymongo import MongoClient, UpdateOne

BDL_API_KEY = os.environ.get("BDL_API_KEY", "ad5544be-9969-434b-9389-2b7cf658c8e0")
BDL_BASE = "https://api.balldontlie.io/v1"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "pick_vision")

SEASONS = [2023, 2024, 2025]
SEM = asyncio.Semaphore(15)
BULK_SIZE = 50  # Smaller batches — each doc update is larger now


async def api_get(session, endpoint, params=None, retries=4):
    url = f"{BDL_BASE}/{endpoint}"
    for attempt in range(retries):
        try:
            async with SEM:
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    if resp.status == 429:
                        wait = 3 * (attempt + 1)
                        await asyncio.sleep(wait)
                        continue
                    if resp.status == 404:
                        return None
                    await asyncio.sleep(1)
        except Exception:
            await asyncio.sleep(2)
    return None


async def fetch_advanced_stats(session, player_id, season):
    """Fetch all advanced game logs for one player in one season.
    Returns dict: game_id -> advanced metrics."""
    adv_map = {}
    cursor = None
    while True:
        params = {"player_ids[]": player_id, "seasons[]": season, "per_page": 100}
        if cursor:
            params["cursor"] = cursor
        data = await api_get(session, "stats/advanced", params)
        if not data:
            break
        batch = data.get("data", [])
        for entry in batch:
            game = entry.get("game", {})
            gid = game.get("id")
            if gid is None:
                continue
            adv_map[gid] = {
                "usage_pct": entry.get("usage_percentage"),
                "true_shooting_pct": entry.get("true_shooting_percentage"),
                "off_rating": entry.get("offensive_rating"),
                "def_rating": entry.get("defensive_rating"),
                "net_rating": entry.get("net_rating"),
                "pace": entry.get("pace"),
                "ast_pct": entry.get("assist_percentage"),
                "reb_pct": entry.get("rebound_percentage"),
                "pie": entry.get("pie"),
                "eFG_pct": entry.get("effective_field_goal_percentage"),
                "turnover_ratio": entry.get("turnover_ratio"),
                "ast_to_tov": entry.get("assist_to_turnover"),
                "ast_ratio": entry.get("assist_ratio"),
                "oreb_pct": entry.get("offensive_rebound_percentage"),
                "dreb_pct": entry.get("defensive_rebound_percentage"),
            }
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor or not batch:
            break
    return adv_map


async def process_player(session, bdl_id):
    """Fetch 3-year advanced stats and return merged game_id -> metrics map per season."""
    result = {}
    total_enriched = 0
    for season in SEASONS:
        adv_map = await fetch_advanced_stats(session, bdl_id, season)
        result[f"{season}_season"] = adv_map
        total_enriched += len(adv_map)
    return result, total_enriched


def merge_advanced_into_history(history, adv_by_season):
    """Merge advanced metrics into existing history game logs in-place.
    Returns count of enriched logs."""
    enriched = 0
    for season_key, adv_map in adv_by_season.items():
        logs = history.get(season_key, [])
        for log in logs:
            gid = log.get("game_id")
            if gid and gid in adv_map:
                log.update(adv_map[gid])
                enriched += 1
    return enriched


async def main():
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    hub = db.nba_master_hub_2026

    # Load all players with their bdl_id and existing history
    print("=" * 60)
    print("[STEP 0] Loading NBA hub players...")
    print("=" * 60)

    players = []
    for doc in hub.find(
        {"history": {"$exists": True}},
        {"_id": 1, "bdl_id": 1, "bdl_player_id": 1, "display_name": 1, "history": 1}
    ):
        bid = doc.get("bdl_id") or doc.get("bdl_player_id")
        if bid:
            players.append({
                "doc_id": doc["_id"],
                "bdl_id": bid,
                "name": doc.get("display_name", "?"),
                "history": doc.get("history", {}),
            })

    total = len(players)
    print(f"  {total} players loaded with history arrays")

    headers = {"Authorization": BDL_API_KEY}
    timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=90)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        print("=" * 60)
        print(f"[STEP 1] Fetching advanced stats for {total} players (3 seasons)")
        print(f"         Concurrency: Semaphore(15) | Bulk: {BULK_SIZE}")
        print("=" * 60)

        global_start = time.time()
        processed = 0
        errors = 0
        total_enriched_logs = 0

        for batch_idx in range(0, total, BULK_SIZE):
            batch = players[batch_idx : batch_idx + BULK_SIZE]
            batch_start = time.time()

            # Fetch advanced stats concurrently
            tasks = [process_player(session, p["bdl_id"]) for p in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Merge into history and build bulk ops
            ops = []
            for i, res in enumerate(results):
                if isinstance(res, Exception):
                    errors += 1
                    continue

                adv_by_season, adv_count = res
                player = batch[i]
                history = player["history"]

                # Merge advanced metrics into each game log
                enriched = merge_advanced_into_history(history, adv_by_season)
                total_enriched_logs += enriched

                # $set only the history arrays (no-loss)
                ops.append(
                    UpdateOne(
                        {"_id": player["doc_id"]},
                        {"$set": {
                            "history": history,
                            "advanced_overlay_at": datetime.now(timezone.utc).isoformat(),
                        }},
                    )
                )

            if ops:
                hub.bulk_write(ops, ordered=False)

            processed += len(batch)
            batch_elapsed = time.time() - batch_start
            avg_speed = batch_elapsed / len(batch) if batch else 0

            print(
                f"[BATCH COMPLETE] Processed: {processed}/{total} | "
                f"Speed: {avg_speed:.2f}s/player | "
                f"Enriched Logs: {total_enriched_logs:,} | "
                f"Errors: {errors}"
            )

        total_elapsed = time.time() - global_start

        print("\n" + "=" * 60)
        print("[ADVANCED OVERLAY COMPLETE]")
        print("=" * 60)
        print(f"  Players Processed:       {processed}")
        print(f"  Game Logs Enriched:      {total_enriched_logs:,}")
        print(f"  Errors:                  {errors}")
        print(f"  Total Time:              {total_elapsed:.1f}s")
        print(f"  Avg Speed:               {total_elapsed / max(processed, 1):.2f}s/player")
        print("=" * 60)

        # === QUALITY CONTROL: SGA 3-game stretch ===
        print("\n" + "=" * 60)
        print("[QC] Shai Gilgeous-Alexander — pts vs usage_pct (3-game stretch)")
        print("=" * 60)
        sga = hub.find_one(
            {"$or": [
                {"display_name": {"$regex": "Gilgeous", "$options": "i"}},
                {"player_name": {"$regex": "Gilgeous", "$options": "i"}},
            ]},
            {"_id": 0, "display_name": 1, "history.2024_season": 1}
        )
        if sga:
            logs_2024 = sga.get("history", {}).get("2024_season", [])
            print(f"  Player: {sga.get('display_name')}")
            print(f"  2024_season logs: {len(logs_2024)}")
            print(f"  {'Game':<12} {'Date':<12} {'PTS':>4} {'Usage%':>8} {'TS%':>6} {'OffRtg':>7} {'DefRtg':>7} {'Pace':>6}")
            print(f"  {'-'*70}")
            for log in logs_2024[10:13]:
                print(
                    f"  {str(log.get('game_id','?')):<12} "
                    f"{str(log.get('date','?')):<12} "
                    f"{log.get('pts','?'):>4} "
                    f"{log.get('usage_pct','?'):>8} "
                    f"{log.get('true_shooting_pct','?'):>6} "
                    f"{log.get('off_rating','?'):>7} "
                    f"{log.get('def_rating','?'):>7} "
                    f"{log.get('pace','?'):>6}"
                )

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
