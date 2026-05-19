"""MLB BDL backfill for 2025-03-01 → 2025-05-31 (Phase-2 prep extension).

Adds 3 months of pre-June history so that L20 features have full depth
for pitchers (who start ~once per 5 days) and batters by 2025-06-01.

Idempotent: merges into existing `mlb_master_hub_2026.bdl_game_logs`
arrays, deduplicating by game_id.

Usage:
    sudo supervisorctl stop backend
    cd /app/backend && python3 audits/backfill_mlb_actuals_2025_03_to_05.py
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

# 2025-03-01 .. 2025-05-31 inclusive  (92 days)
START = datetime(2025, 3, 1)
DAYS = 92
TARGET_DATES: List[str] = [
    (START + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(DAYS)
]
SPORT = "mlb"
BDL_BASE = "https://api.balldontlie.io/mlb/v1"
PER_PAGE = 100


async def _fetch_stats_for_games(client, game_ids):
    out, cursor, pages = [], None, 0
    while True:
        q = [f"per_page={PER_PAGE}"]
        for gid in game_ids: q.append(f"game_ids[]={gid}")
        if cursor is not None: q.append(f"cursor={cursor}")
        url = f"{BDL_BASE}/stats?{'&'.join(q)}"
        resp = await client.get(url)
        if resp.status_code == 429:
            await asyncio.sleep(5.0); continue
        resp.raise_for_status()
        data = resp.json(); rows = data.get("data") or []
        out.extend(rows); pages += 1
        cursor = (data.get("meta") or {}).get("next_cursor")
        if not cursor or not rows: break
        if pages > 80: break
    return out


async def _build_game_cache_for_dates(client, dates):
    cache = {}
    last_print = 0
    for i, d in enumerate(dates):
        q = [f"dates[]={d}", "per_page=100"]
        url = f"{BDL_BASE}/games?{'&'.join(q)}"
        resp = await client.get(url); resp.raise_for_status()
        games = (resp.json() or {}).get("data") or []
        for g in games:
            gid = g.get("id")
            if gid:
                cache[gid] = {
                    "date": g.get("date"), "season": g.get("season"),
                    "home_team": g.get("home_team", {}),
                    "away_team": g.get("away_team", {}),
                    "home_team_name": g.get("home_team_name"),
                    "away_team_name": g.get("away_team_name"),
                    "venue": g.get("venue"),
                }
        if (i+1) - last_print >= 10 or i == len(dates) - 1:
            print(f"  [game-cache] {d}: cum_games={len(cache)}", flush=True)
            last_print = i + 1
    return cache


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    hub = db["mlb_master_hub_2026"]
    api_key = os.environ.get("BDL_API_KEY", "")
    if not api_key: raise RuntimeError("BDL_API_KEY missing")

    print("=" * 72)
    print(f"  MLB BDL BACKFILL — dates {TARGET_DATES[0]} → {TARGET_DATES[-1]}")
    print(f"  {len(TARGET_DATES)} dates to ingest")
    print("=" * 72, flush=True)

    headers = {"Authorization": api_key}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0), headers=headers,
        limits=httpx.Limits(max_connections=8),
    ) as client:
        print("\n[step 1] Building game cache…", flush=True)
        game_cache = await _build_game_cache_for_dates(client, TARGET_DATES)
        print(f"[game-cache] TOTAL: {len(game_cache)} games across "
              f"{len(TARGET_DATES)} dates", flush=True)

        print("\n[step 2] Fetching stat rows (25 games per request)…",
              flush=True)
        all_stats = []
        gids = sorted(game_cache.keys())
        BATCH = 25
        for i in range(0, len(gids), BATCH):
            sub = gids[i:i+BATCH]
            rows = await _fetch_stats_for_games(client, sub)
            all_stats.extend(rows)
            if (i // BATCH) % 4 == 0 or i + BATCH >= len(gids):
                print(f"  [games {i:>4d}-{i+len(sub)-1:<4d}] "
                      f"fetched={len(rows):>4d} cum_total={len(all_stats):>6d}",
                      flush=True)
            await asyncio.sleep(RATE_LIMIT_DELAY)

    print(f"\n[stats] total rows fetched: {len(all_stats)}", flush=True)

    service = BDLUniversalSyncService(db)
    service._mlb_game_cache = game_cache

    print("\n[step 3] Transforming + grouping by player_id…", flush=True)
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for s in all_stats:
        pid = (s.get("player") or {}).get("id")
        if not pid: continue
        log = service._transform_stat_to_game_log(s, "mlb")
        if not log.get("date"): continue
        grouped.setdefault(int(pid), []).append(log)
    print(f"[merge] players with new logs: {len(grouped)}", flush=True)

    print("\n[step 4] Merging into mlb_master_hub_2026.bdl_game_logs…",
          flush=True)
    now = datetime.now(timezone.utc).isoformat()
    updated = skipped = new_logs_total = 0
    for i, (pid, new_logs) in enumerate(grouped.items()):
        doc = await hub.find_one({"bdl_id": pid}, {"_id":0,"bdl_game_logs":1})
        if not doc: skipped += 1; continue
        existing = doc.get("bdl_game_logs") or []
        by_gid = {int(g["game_id"]): g for g in existing
                   if g.get("game_id") is not None}
        for log in new_logs:
            gid = log.get("game_id")
            if gid is None: continue
            if int(gid) not in by_gid: new_logs_total += 1
            by_gid[int(gid)] = log
        merged = sorted(by_gid.values(),
                         key=lambda x: x.get("date") or "", reverse=True)
        await hub.update_one(
            {"bdl_id": pid},
            {"$set": {"bdl_game_logs": merged,
                       "bdl_game_logs_count": len(merged),
                       "bdl_last_sync": now, "sport": "mlb"}})
        updated += 1
        if (i+1) % 200 == 0:
            print(f"  merged {i+1}/{len(grouped)} players "
                  f"(+{new_logs_total} new logs)", flush=True)
    print(f"[merge] updated {updated} player docs "
          f"(skipped {skipped})  TOTAL new logs: {new_logs_total}",
          flush=True)

    # Sanity
    for d in ("2025-03-15","2025-04-15","2025-05-15"):
        n = await hub.count_documents(
            {"bdl_game_logs.date":{"$regex":f"^{d}"}})
        print(f"  {d}: {n} players logged")

if __name__ == "__main__":
    asyncio.run(main())
