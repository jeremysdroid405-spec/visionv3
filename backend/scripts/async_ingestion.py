#!/usr/bin/env python3
"""
MLB_ORACLE_APEX - Async 15x Parallel Deep Ingestion
====================================================
Uses asyncio.Semaphore(15) + aiohttp for maximum BDL GOAT Tier throughput.
Active-only filter. Bulk upserts every 100 players.
"""

import asyncio
import aiohttp
import time
import os
import sys
from datetime import datetime, timezone
from pymongo import MongoClient, UpdateOne

BDL_API_KEY = os.environ.get("BDL_API_KEY", "ad5544be-9969-434b-9389-2b7cf658c8e0")
BDL_BASE = "https://api.balldontlie.io/mlb/v1"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "pick_vision")

SEASONS = [2023, 2024, 2025]
SEM = asyncio.Semaphore(15)
BULK_SIZE = 100


async def api_get(session, endpoint, params=None, retries=4):
    """Single API call with retry + rate-limit backoff."""
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


async def fetch_all_active_players(session):
    """Paginate /players, return only active==True."""
    players = []
    cursor = None
    page = 0
    while True:
        params = {"per_page": 100}
        if cursor:
            params["cursor"] = cursor
        data = await api_get(session, "players", params)
        if not data:
            break
        batch = data.get("data", [])
        players.extend(batch)
        cursor = data.get("meta", {}).get("next_cursor")
        page += 1
        if page % 20 == 0:
            print(f"  [PLAYERS] Fetched {len(players)} so far...")
        if not cursor or not batch:
            break

    active = [p for p in players if p.get("active") is True]
    print(f"  [PLAYERS] Total fetched: {len(players)} | Active: {len(active)}")
    return active


async def fetch_player_season_stats(session, player_id, season):
    """Paginate all game logs for one player in one season."""
    logs = []
    cursor = None
    while True:
        params = {"player_ids[]": player_id, "seasons[]": season, "per_page": 100}
        if cursor:
            params["cursor"] = cursor
        data = await api_get(session, "stats", params)
        if not data:
            break
        batch = data.get("data", [])
        for entry in batch:
            row = {k: v for k, v in entry.items() if k != "player"}
            logs.append(row)
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor or not batch:
            break
    return logs


async def process_single_player(session, player):
    """Fetch 3-year stats for one player, return hub document."""
    pid = player["id"]
    name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
    team_obj = player.get("team") or {}

    history = {}
    total_games = 0

    for season in SEASONS:
        logs = await fetch_player_season_stats(session, pid, season)
        history[f"{season}_season"] = logs
        total_games += len(logs)

    return {
        "bdl_id": pid,
        "player_id": pid,
        "player_name": name,
        "display_name": name,
        "active": True,
        "team": team_obj.get("abbreviation", ""),
        "team_full": team_obj.get("full_name", ""),
        "position": player.get("position", ""),
        "debut_year": player.get("debut_year"),
        "bats_throws": player.get("bats_throws", ""),
        "age": player.get("age"),
        "height": player.get("height"),
        "weight": player.get("weight"),
        "draft": player.get("draft"),
        "history": history,
        "history_stats": {
            "2023_games": len(history.get("2023_season", [])),
            "2024_games": len(history.get("2024_season", [])),
            "2025_games": len(history.get("2025_season", [])),
            "total_games": total_games,
        },
        "games_played": total_games,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "data_source": "BDL_GOAT_ASYNC_15X",
        "schema_version": "2.0_SSOT",
    }


async def main():
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    hub = db.mlb_master_hub_2026

    # Clear old empty shells
    empty_count = hub.count_documents({"player_name": {"$exists": False}})
    if empty_count > 0:
        hub.delete_many({"player_name": {"$exists": False}})
        print(f"[CLEANUP] Purged {empty_count} empty shell documents")

    headers = {"Authorization": BDL_API_KEY}
    timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=90)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        # Step 1: Fetch active players
        print("=" * 60)
        print("[STEP 1] Fetching all active MLB players from BDL...")
        print("=" * 60)
        active_players = await fetch_all_active_players(session)
        total = len(active_players)
        if total == 0:
            print("[ERROR] No active players found. Check API key.")
            return

        # Step 2: Process in batches of BULK_SIZE, 15 concurrent per batch
        print("=" * 60)
        print(f"[STEP 2] Ingesting 3-year history for {total} active players")
        print(f"         Concurrency: Semaphore(15) | Bulk: {BULK_SIZE}")
        print("=" * 60)

        global_start = time.time()
        processed = 0
        errors = 0
        total_game_logs = 0

        for batch_idx in range(0, total, BULK_SIZE):
            batch = active_players[batch_idx : batch_idx + BULK_SIZE]
            batch_start = time.time()

            # Fire 15 concurrent player tasks within each batch
            tasks = [process_single_player(session, p) for p in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Build bulk write operations
            ops = []
            for res in results:
                if isinstance(res, Exception):
                    errors += 1
                    continue
                ops.append(
                    UpdateOne(
                        {"bdl_id": res["bdl_id"]},
                        {"$set": res},
                        upsert=True,
                    )
                )
                total_game_logs += res.get("games_played", 0)

            if ops:
                hub.bulk_write(ops, ordered=False)

            processed += len(batch)
            batch_elapsed = time.time() - batch_start
            avg_speed = batch_elapsed / len(batch) if batch else 0
            hub_count = hub.count_documents({})

            print(
                f"[BATCH COMPLETE] Hub Count: {hub_count} | "
                f"Processed: {processed}/{total} | "
                f"Speed: {avg_speed:.2f}s/player | "
                f"Game Logs: {total_game_logs:,} | "
                f"Errors: {errors}"
            )

        total_elapsed = time.time() - global_start
        final_count = hub.count_documents({})
        with_history = hub.count_documents({"history": {"$exists": True}})

        print("\n" + "=" * 60)
        print("[INGESTION COMPLETE]")
        print("=" * 60)
        print(f"  Active Players Processed: {processed}")
        print(f"  Hub Documents (total):    {final_count}")
        print(f"  Hub Docs with History:    {with_history}")
        print(f"  Total Game Logs Stored:   {total_game_logs:,}")
        print(f"  Errors:                   {errors}")
        print(f"  Total Time:               {total_elapsed:.1f}s")
        print(f"  Avg Speed:                {total_elapsed / max(processed, 1):.2f}s/player")
        print("=" * 60)

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
