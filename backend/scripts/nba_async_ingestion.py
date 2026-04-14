#!/usr/bin/env python3
"""
NBA_ORACLE_APEX - Async 15x Parallel Deep Ingestion
====================================================
Mirrors MLB async_ingestion.py for the NBA hub.
No-Loss Merge: Only $set new history arrays + schema tag.
Preserves all existing metadata (headshots, badges, advanced_stats).
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
BULK_SIZE = 100


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


async def fetch_player_season_stats(session, player_id, season):
    """Paginate all game logs for one NBA player in one season."""
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
            game = entry.get("game", {})
            team = entry.get("team", {})
            row = {
                "game_id": game.get("id"),
                "date": game.get("date"),
                "season": game.get("season") or season,
                "home_team_id": game.get("home_team_id"),
                "visitor_team_id": game.get("visitor_team_id"),
                "home_team_score": game.get("home_team_score"),
                "visitor_team_score": game.get("visitor_team_score"),
                "team_id": team.get("id"),
                "team_abbr": team.get("abbreviation", ""),
                "team_name": team.get("full_name", ""),
                # Core box score
                "pts": entry.get("pts"),
                "reb": entry.get("reb"),
                "ast": entry.get("ast"),
                "stl": entry.get("stl"),
                "blk": entry.get("blk"),
                "turnover": entry.get("turnover"),
                "pf": entry.get("pf"),
                "min": entry.get("min"),
                "plus_minus": entry.get("plus_minus"),
                # Shooting
                "fgm": entry.get("fgm"),
                "fga": entry.get("fga"),
                "fg_pct": entry.get("fg_pct"),
                "fg3m": entry.get("fg3m"),
                "fg3a": entry.get("fg3a"),
                "fg3_pct": entry.get("fg3_pct"),
                "ftm": entry.get("ftm"),
                "fta": entry.get("fta"),
                "ft_pct": entry.get("ft_pct"),
                # Rebounds breakdown
                "oreb": entry.get("oreb"),
                "dreb": entry.get("dreb"),
            }
            logs.append(row)
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor or not batch:
            break
    return logs


async def process_single_player(session, bdl_id):
    """Fetch 3-year stats for one NBA player, return $set payload."""
    history = {}
    total_games = 0

    for season in SEASONS:
        logs = await fetch_player_season_stats(session, bdl_id, season)
        # Sort by date descending
        logs.sort(key=lambda x: x.get("date") or "", reverse=True)
        history[f"{season}_season"] = logs
        total_games += len(logs)

    return {
        "history": history,
        "history_stats": {
            "2023_games": len(history.get("2023_season", [])),
            "2024_games": len(history.get("2024_season", [])),
            "2025_games": len(history.get("2025_season", [])),
            "total_games": total_games,
        },
        "games_played_3yr": total_games,
        "schema_version": "2.0_SSOT",
        "data_source_3yr": "BDL_GOAT_ASYNC_15X",
        "history_updated_at": datetime.now(timezone.utc).isoformat(),
    }


async def main():
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    hub = db.nba_master_hub_2026

    # Step 0: Get all existing player bdl_ids from the hub
    print("=" * 60)
    print("[STEP 0] Reading existing NBA hub players...")
    print("=" * 60)
    players = []
    for doc in hub.find({}, {"_id": 0, "bdl_id": 1, "bdl_player_id": 1, "display_name": 1}):
        bid = doc.get("bdl_id") or doc.get("bdl_player_id")
        name = doc.get("display_name", "?")
        if bid:
            players.append({"bdl_id": bid, "name": name})

    total = len(players)
    print(f"  Found {total} players with bdl_id in hub")
    if total == 0:
        print("[ERROR] No players with bdl_id found. Aborting.")
        return

    # Step 1: Process in batches
    headers = {"Authorization": BDL_API_KEY}
    timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=90)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        print("=" * 60)
        print(f"[STEP 1] Ingesting 3-year history for {total} NBA players")
        print(f"         Concurrency: Semaphore(15) | Bulk: {BULK_SIZE}")
        print(f"         Mode: NO-LOSS MERGE ($set only)")
        print("=" * 60)

        global_start = time.time()
        processed = 0
        errors = 0
        total_game_logs = 0

        for batch_idx in range(0, total, BULK_SIZE):
            batch = players[batch_idx : batch_idx + BULK_SIZE]
            batch_start = time.time()

            # Fire concurrent tasks
            tasks = [process_single_player(session, p["bdl_id"]) for p in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Build bulk ops — $set ONLY (no-loss merge)
            ops = []
            for i, res in enumerate(results):
                if isinstance(res, Exception):
                    errors += 1
                    continue
                bid = batch[i]["bdl_id"]
                ops.append(
                    UpdateOne(
                        {"$or": [{"bdl_id": bid}, {"bdl_player_id": bid}]},
                        {"$set": res},
                    )
                )
                total_game_logs += res.get("history_stats", {}).get("total_games", 0)

            if ops:
                hub.bulk_write(ops, ordered=False)

            processed += len(batch)
            batch_elapsed = time.time() - batch_start
            avg_speed = batch_elapsed / len(batch) if batch else 0
            with_history = hub.count_documents({"history": {"$exists": True}})

            print(
                f"[BATCH COMPLETE] Hub Docs with History: {with_history}/{total} | "
                f"Processed: {processed}/{total} | "
                f"Speed: {avg_speed:.2f}s/player | "
                f"Game Logs: {total_game_logs:,} | "
                f"Errors: {errors}"
            )

        total_elapsed = time.time() - global_start
        final_with_history = hub.count_documents({"history": {"$exists": True}})
        schema_count = hub.count_documents({"schema_version": "2.0_SSOT"})

        print("\n" + "=" * 60)
        print("[INGESTION COMPLETE]")
        print("=" * 60)
        print(f"  NBA Players Processed:    {processed}")
        print(f"  Hub Docs with History:    {final_with_history}")
        print(f"  Schema 2.0_SSOT:          {schema_count}")
        print(f"  Total Game Logs Stored:   {total_game_logs:,}")
        print(f"  Errors:                   {errors}")
        print(f"  Total Time:               {total_elapsed:.1f}s")
        print(f"  Avg Speed:                {total_elapsed / max(processed, 1):.2f}s/player")
        print("=" * 60)

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
