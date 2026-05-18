"""Targeted MLB actuals backfill for 2026-05-08 → 2026-05-15.

Why this script exists.
  The universal `bdl_universal_sync.sync_stats_batched` REPLACES the
  whole `bdl_game_logs` array with the per-batch result (`$set`).
  Running it for a partial date range would wipe out the existing
  logs for 2026-05-03 → 2026-05-07 that the replay relies on.

  This script does the opposite: fetches stats for ONLY the target
  date window (`dates[]=YYYY-MM-DD` on the BDL endpoint, no
  player_ids filter) and MERGES each player's logs into the
  existing `bdl_game_logs` array, deduplicating by `game_id`.

Output: `mlb_master_hub_2026.bdl_game_logs` gets new dated entries
appended (per player) for every player who appeared in a game on
the target dates.

Idempotent: re-running the script overwrites entries for the same
`game_id` with the freshest BDL payload. No row count drift.

Usage (run with backend stopped to stay under the 8 GB cgroup):
    sudo supervisorctl stop backend
    cd /app/backend && python3 audits/backfill_mlb_actuals_2026_05_08_to_15.py
    sudo supervisorctl start backend
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

# Make the project's `services` package importable when invoked
# from `/app/backend/audits/`.
sys.path.insert(0, "/app/backend")
load_dotenv("/app/backend/.env")

from services.bdl_universal_sync import (  # noqa: E402
    BDLUniversalSyncService, BATCH_SIZE, RATE_LIMIT_DELAY,
)

# ── Tunables ────────────────────────────────────────────────────────
TARGET_DATES: List[str] = [
    (datetime(2026, 5, 8) + timedelta(days=i)).strftime("%Y-%m-%d")
    for i in range(0, 8)  # 05-08 .. 05-15 inclusive
]
SPORT = "mlb"
BDL_BASE = "https://api.balldontlie.io/mlb/v1"
PER_PAGE = 100


# ── BDL fetch helpers ──────────────────────────────────────────────
async def _fetch_stats_for_games(
    client: httpx.AsyncClient, game_ids: List[int],
) -> List[Dict[str, Any]]:
    """Fetch all MLB stat rows for a batch of game_ids.

    NOTE: the BDL `/stats?dates[]=...&seasons[]=...` combo did NOT
    actually filter by date — it returned the whole season slate
    on every page. `game_ids[]` is the only reliable date scope.
    """
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
        if pages > 50:
            print(f"  [batch] WARNING — stopping at page {pages}")
            break
    return out


async def _build_game_cache_for_dates(
    client: httpx.AsyncClient, dates: List[str],
) -> Dict[int, Dict[str, Any]]:
    """Pull `/games` for each target date to map game_id → date/teams."""
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
    return cache


# ── Main entry point ────────────────────────────────────────────────
async def main() -> None:
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    hub = db["mlb_master_hub_2026"]
    api_key = os.environ.get("BDL_API_KEY", "")
    if not api_key:
        raise RuntimeError("BDL_API_KEY missing from environment")

    print("=" * 72)
    print(f"  MLB ACTUALS BACKFILL — dates {TARGET_DATES[0]} → {TARGET_DATES[-1]}")
    print("=" * 72)

    # ── Step 1: pull a game cache so we can stamp dates on stat rows ─
    headers = {"Authorization": api_key}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(45.0), headers=headers,
        limits=httpx.Limits(max_connections=8),
    ) as client:
        game_cache = await _build_game_cache_for_dates(client, TARGET_DATES)
        print(f"[game-cache] {len(game_cache)} games cached "
              f"across {len(TARGET_DATES)} dates")

        # ── Step 2: pull all stat rows for the dated games ────────
        all_stats: List[Dict[str, Any]] = []
        game_ids = sorted(game_cache.keys())
        GAMES_PER_REQUEST = 25
        for i in range(0, len(game_ids), GAMES_PER_REQUEST):
            batch = game_ids[i:i + GAMES_PER_REQUEST]
            rows = await _fetch_stats_for_games(client, batch)
            all_stats.extend(rows)
            print(f"  [games {i}-{i+len(batch)-1}] fetched {len(rows)} stat rows")
            await asyncio.sleep(RATE_LIMIT_DELAY)

    print(f"\n[stats] total rows fetched: {len(all_stats)}")

    # ── Step 3: transform via the existing universal sync helper ──
    # so the on-disk schema stays byte-identical to the rest of
    # `bdl_game_logs`. We instantiate the service ONLY to reuse its
    # `_transform_stat_to_game_log` + `_get_mlb_game_date` paths
    # (no DB writes from the service itself).
    service = BDLUniversalSyncService(db)
    service._mlb_game_cache = game_cache  # type: ignore[attr-defined]

    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for s in all_stats:
        pid = (s.get("player") or {}).get("id")
        if not pid:
            continue
        log = service._transform_stat_to_game_log(s, "mlb")  # type: ignore[attr-defined]
        # Skip rows the game cache couldn't date — they're useless for
        # the replay's actuals lookup (which keys off `date`).
        if not log.get("date"):
            continue
        grouped.setdefault(int(pid), []).append(log)

    print(f"\n[merge] players with new logs: {len(grouped)}")

    # ── Step 4: merge per-player into existing bdl_game_logs ──────
    # Strategy: read existing logs, build {game_id: log} dict, upsert
    # the new logs (overwriting same-game_id entries), then write back.
    now = datetime.now(timezone.utc).isoformat()
    updated = 0
    skipped_no_doc = 0
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
        # Upsert new logs (overwrites stale duplicates)
        for log in new_logs:
            gid = log.get("game_id")
            if gid is not None:
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

    # ── Step 5: sanity-check actuals coverage ─────────────────────
    from services.replay.providers.mlb_adapter import MLBReplayAdapter
    adapter = MLBReplayAdapter(db)
    print("\n[verify] post-backfill actuals coverage:")
    for d in TARGET_DATES:
        actuals = await adapter.fetch_actuals(game_date=d)
        n_batter_k = sum(1 for v in actuals.values() if "batter_strikeouts" in v)
        print(f"  {d}: {len(actuals):4d} players, "
              f"{n_batter_k:4d} with batter_strikeouts")


if __name__ == "__main__":
    asyncio.run(main())
