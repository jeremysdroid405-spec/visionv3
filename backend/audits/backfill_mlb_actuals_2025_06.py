"""MLB BDL backfill for 2025-06-01 → 2025-06-30 (one-month Phase-2 prep).

Adapted from `backfill_mlb_actuals_2026_05_08_to_15.py`. Pulls BDL stats
for the target dates and MERGES them into existing
`mlb_master_hub_2026.bdl_game_logs` arrays (deduplicating by game_id).

Limitations the caller should accept:
  • Pitchers only start ~once per 5 days → June-only ≈ 6 starts max,
    L20 will NOT fully populate
  • Batters who haven't played in 2026 won't be in the hub and will be
    silently skipped
  • Early June dates will have very thin L20 (only a few prior June logs)

Usage:
    sudo supervisorctl stop backend
    cd /app/backend && python3 audits/backfill_mlb_actuals_2025_06.py
    sudo supervisorctl start backend
"""
from __future__ import annotations
import asyncio, os, sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")
from services.bdl_universal_sync import (
    BDLUniversalSyncService, BATCH_SIZE, RATE_LIMIT_DELAY,
)

TARGET_DATES: List[str] = [
    (datetime(2025, 6, 1) + timedelta(days=i)).strftime("%Y-%m-%d")
    for i in range(0, 30)  # 2025-06-01 .. 2025-06-30 inclusive
]
SPORT = "mlb"
BDL_BASE = "https://api.balldontlie.io/mlb/v1"
PER_PAGE = 100


async def _fetch_stats_for_games(
    client: httpx.AsyncClient, game_ids: List[int],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    cursor: Optional[int] = None
    pages = 0
    while True:
        query = [f"per_page={PER_PAGE}"]
        for gid in game_ids:
            query.append(f"game_ids[]={gid}")
        if cursor is not None:
            query.append(f"cursor={cursor}")
        url = f"{BDL_BASE}/stats?{'&'.join(query)}"
        resp = await client.get(url)
        if resp.status_code == 429:
            print(f"  [batch] 429 — sleeping 5s")
            await asyncio.sleep(5.0)
            continue
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("data") or []
        out.extend(rows)
        pages += 1
        cursor = (data.get("meta") or {}).get("next_cursor")
        if not cursor or not rows:
            break
        if pages > 80:
            print(f"  [batch] WARNING — stopping at page {pages}")
            break
    return out


async def _build_game_cache_for_dates(
    client: httpx.AsyncClient, dates: List[str],
) -> Dict[int, Dict[str, Any]]:
    cache: Dict[int, Dict[str, Any]] = {}
    for d in dates:
        query = [f"dates[]={d}", "per_page=100"]
        url = f"{BDL_BASE}/games?{'&'.join(query)}"
        resp = await client.get(url)
        resp.raise_for_status()
        games = (resp.json() or {}).get("data") or []
        for g in games:
            gid = g.get("id")
            if gid:
                cache[gid] = {
                    "date": g.get("date"),
                    "season": g.get("season"),
                    "home_team": g.get("home_team", {}),
                    "away_team": g.get("away_team", {}),
                    "home_team_name": g.get("home_team_name"),
                    "away_team_name": g.get("away_team_name"),
                    "venue": g.get("venue"),
                }
        print(f"  [game-cache] {d}: cumulative games={len(cache)}")
    return cache


async def main() -> None:
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    hub = db["mlb_master_hub_2026"]
    api_key = os.environ.get("BDL_API_KEY", "")
    if not api_key:
        raise RuntimeError("BDL_API_KEY missing from environment")

    print("=" * 72)
    print(f"  MLB BDL BACKFILL — dates {TARGET_DATES[0]} → {TARGET_DATES[-1]}")
    print(f"  {len(TARGET_DATES)} dates to ingest")
    print("=" * 72)

    headers = {"Authorization": api_key}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0), headers=headers,
        limits=httpx.Limits(max_connections=8),
    ) as client:
        print("\n[step 1] Building game cache…")
        game_cache = await _build_game_cache_for_dates(client, TARGET_DATES)
        print(f"[game-cache] {len(game_cache)} games cached "
              f"across {len(TARGET_DATES)} dates")

        print("\n[step 2] Fetching stat rows in batches of 25 games…")
        all_stats: List[Dict[str, Any]] = []
        game_ids = sorted(game_cache.keys())
        GAMES_PER_REQUEST = 25
        for i in range(0, len(game_ids), GAMES_PER_REQUEST):
            batch = game_ids[i:i + GAMES_PER_REQUEST]
            rows = await _fetch_stats_for_games(client, batch)
            all_stats.extend(rows)
            print(f"  [games {i:>3d}-{i+len(batch)-1:<3d}] fetched "
                  f"{len(rows):>4d} stat rows  "
                  f"(cumulative {len(all_stats):>5d})")
            await asyncio.sleep(RATE_LIMIT_DELAY)

    print(f"\n[stats] total rows fetched: {len(all_stats)}")

    service = BDLUniversalSyncService(db)
    service._mlb_game_cache = game_cache

    print("\n[step 3] Transforming + grouping by player_id…")
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for s in all_stats:
        pid = (s.get("player") or {}).get("id")
        if not pid:
            continue
        log = service._transform_stat_to_game_log(s, "mlb")
        if not log.get("date"):
            continue
        grouped.setdefault(int(pid), []).append(log)
    print(f"[merge] players with new logs: {len(grouped)}")

    print("\n[step 4] Merging into mlb_master_hub_2026.bdl_game_logs…")
    now = datetime.now(timezone.utc).isoformat()
    updated = 0
    skipped_no_doc = 0
    total_new_logs = 0
    for pid, new_logs in grouped.items():
        doc = await hub.find_one(
            {"bdl_id": pid},
            {"_id": 0, "bdl_game_logs": 1},
        )
        if not doc:
            skipped_no_doc += 1
            continue
        existing: List[Dict[str, Any]] = doc.get("bdl_game_logs") or []
        by_gid: Dict[int, Dict[str, Any]] = {}
        for log in existing:
            gid = log.get("game_id")
            if gid is not None:
                by_gid[int(gid)] = log
        for log in new_logs:
            gid = log.get("game_id")
            if gid is not None:
                if int(gid) not in by_gid:
                    total_new_logs += 1
                by_gid[int(gid)] = log
        merged = sorted(
            by_gid.values(),
            key=lambda x: x.get("date") or "",
            reverse=True,
        )
        await hub.update_one(
            {"bdl_id": pid},
            {"$set": {
                "bdl_game_logs": merged,
                "bdl_game_logs_count": len(merged),
                "bdl_last_sync": now,
                "sport": "mlb",
            }},
        )
        updated += 1

    print(f"[merge] updated {updated} player docs "
          f"(skipped {skipped_no_doc} with no matching hub doc)")
    print(f"[merge] total NEW logs appended: {total_new_logs}")

    # Sanity check
    print("\n[verify] sample coverage by date:")
    for d in (TARGET_DATES[0], TARGET_DATES[7], TARGET_DATES[14],
              TARGET_DATES[21], TARGET_DATES[-1]):
        cnt = await hub.count_documents({"bdl_game_logs.date": d})
        print(f"  {d}: {cnt} players with a log on this date")


if __name__ == "__main__":
    asyncio.run(main())
