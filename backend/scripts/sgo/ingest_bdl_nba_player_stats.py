"""
ingest_bdl_nba_player_stats.py — Ingest NBA player-level box scores from BDL
into MongoDB collection `bdl_historical_game_logs`.

One document per (player_id, game_id), upserted. Paginated via next_cursor.

Usage:
    python -m scripts.sgo.ingest_bdl_nba_player_stats --start 2025-10-01 --end 2026-06-10
    python -m scripts.sgo.ingest_bdl_nba_player_stats --start 2024-10-01 --end 2025-06-30 --season 2024

Rate limit: Semaphore of 5 + 0.1s per-request sleep. 429s retry up to 5x
with exponential backoff (2s, 4s, 8s, 16s, 32s).

Environment:
    BDL_API_KEY   required  (also checked as BALLDONTLIE_API_KEY)
    MONGO_URL     default: mongodb://localhost:27017
    DB_NAME       default: pick_vision
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")

from dotenv import load_dotenv
for _env in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(_env):
        load_dotenv(_env)
        break

log = logging.getLogger("bdl.nba.player_stats")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)

BDL_BASE = "https://api.balldontlie.io/v1"
COLLECTION = "bdl_historical_game_logs"
PER_PAGE = 100
SEMAPHORE_SIZE = 5
RATE_LIMIT_SLEEP = 0.1
MAX_RETRIES = 5
UPSERT_CHUNK = 500
LOG_EVERY = 1_000


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

async def _get(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    url: str,
    params: Any,
    api_key: str,
) -> dict:
    """Authenticated GET with semaphore gating, rate-limit sleep, and 429 retry."""
    headers = {"Authorization": api_key}
    last_resp = None
    for attempt in range(MAX_RETRIES):
        async with sem:
            resp = await client.get(url, params=params, headers=headers, timeout=30)
            await asyncio.sleep(RATE_LIMIT_SLEEP)
        last_resp = resp
        if resp.status_code == 429:
            wait = 2 ** (attempt + 1)
            log.warning("429 rate limited — retry %d/%d in %ds", attempt + 1, MAX_RETRIES, wait)
            await asyncio.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise httpx.HTTPStatusError(
        f"429 after {MAX_RETRIES} retries",
        request=last_resp.request,
        response=last_resp,
    )


# ---------------------------------------------------------------------------
# Stats fetching
# ---------------------------------------------------------------------------

async def fetch_all_stats(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    api_key: str,
    season: int,
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Fetch all player stats for the given season + date range, fully paginated."""
    records: list[dict] = []
    cursor: str | None = None
    page = 0

    while True:
        params: list[tuple] = [
            ("seasons[]", season),
            ("start_date", start_date),
            ("end_date", end_date),
            ("per_page", PER_PAGE),
        ]
        if cursor:
            params.append(("cursor", cursor))

        data = await _get(client, sem, f"{BDL_BASE}/stats", params, api_key)
        batch = data.get("data", [])
        records.extend(batch)
        page += 1

        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break

        if len(records) % LOG_EVERY < PER_PAGE:
            log.info("  page %d — %d records fetched so far …", page, len(records))

    log.info("season %d — %d player-game records fetched (%d pages)", season, len(records), page)
    return records


# ---------------------------------------------------------------------------
# Document extraction
# ---------------------------------------------------------------------------

def _to_int(val: Any) -> int | None:
    try:
        return int(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _to_float(val: Any) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def build_doc(stat: dict) -> dict:
    player = stat.get("player") or {}
    game = stat.get("game") or {}
    team = stat.get("team") or {}

    first = player.get("first_name") or ""
    last = player.get("last_name") or ""
    player_name = f"{first} {last}".strip()

    return {
        "player_id": player.get("id"),
        "game_id": game.get("id"),
        "ast": _to_int(stat.get("ast")),
        "blk": _to_int(stat.get("blk")),
        "date": (game.get("date") or "")[:10],
        "dreb": _to_int(stat.get("dreb")),
        "fg3_pct": _to_float(stat.get("fg3_pct")),
        "fg3a": _to_int(stat.get("fg3a")),
        "fg3m": _to_int(stat.get("fg3m")),
        "fg_pct": _to_float(stat.get("fg_pct")),
        "fga": _to_int(stat.get("fga")),
        "fgm": _to_int(stat.get("fgm")),
        "ft_pct": _to_float(stat.get("ft_pct")),
        "fta": _to_int(stat.get("fta")),
        "ftm": _to_int(stat.get("ftm")),
        "home_team_id": _to_int(game.get("home_team_id")),
        "home_team_score": _to_int(game.get("home_team_score")),
        "min": stat.get("min"),
        "oreb": _to_int(stat.get("oreb")),
        "pf": _to_int(stat.get("pf")),
        "player_name": player_name,
        "pts": _to_int(stat.get("pts")),
        "reb": _to_int(stat.get("reb")),
        "season": game.get("season"),
        "stl": _to_int(stat.get("stl")),
        "team_id": team.get("id"),
        "turnover": _to_int(stat.get("turnover")),
        "visitor_team_id": _to_int(game.get("visitor_team_id")),
        "visitor_team_score": _to_int(game.get("visitor_team_score")),
        "ingested_at": datetime.now(timezone.utc),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(start_date: str, end_date: str, season: int) -> None:
    api_key = os.environ.get("BDL_API_KEY") or os.environ.get("BALLDONTLIE_API_KEY")
    if not api_key:
        log.error("BDL_API_KEY not set — aborting")
        sys.exit(1)

    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    client_mongo = AsyncIOMotorClient(mongo_url)
    coll = client_mongo[db_name][COLLECTION]

    await coll.create_index([("player_id", 1), ("game_id", 1)], unique=True, background=True)
    await coll.create_index([("date", 1)], background=True)
    await coll.create_index([("season", 1)], background=True)
    await coll.create_index([("player_id", 1)], background=True)

    log.info("date range %s → %s  season: %d", start_date, end_date, season)

    sem = asyncio.Semaphore(SEMAPHORE_SIZE)

    async with httpx.AsyncClient() as http:
        records = await fetch_all_stats(http, sem, api_key, season, start_date, end_date)

    if not records:
        log.info("no records returned — done")
        client_mongo.close()
        return

    log.info("building documents and upserting %d records …", len(records))

    total_upserted = 0
    total_modified = 0
    total_written = 0

    for i in range(0, len(records), UPSERT_CHUNK):
        chunk = records[i : i + UPSERT_CHUNK]
        ops = [
            UpdateOne(
                {"player_id": doc["player_id"], "game_id": doc["game_id"]},
                {"$set": doc},
                upsert=True,
            )
            for doc in (build_doc(r) for r in chunk)
        ]
        result = await coll.bulk_write(ops, ordered=False)
        total_upserted += result.upserted_count
        total_modified += result.modified_count
        total_written += len(chunk)

        if total_written % LOG_EVERY < UPSERT_CHUNK or total_written == len(records):
            log.info(
                "progress — %d/%d records written (inserted: %d  updated: %d)",
                total_written,
                len(records),
                total_upserted,
                total_modified,
            )

    log.info(
        "done — total inserted: %d  total updated: %d",
        total_upserted,
        total_modified,
    )
    client_mongo.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Ingest BDL NBA player stats into bdl_historical_game_logs"
    )
    p.add_argument("--start", required=True, metavar="YYYY-MM-DD", help="Start date (inclusive)")
    p.add_argument("--end", required=True, metavar="YYYY-MM-DD", help="End date (inclusive)")
    p.add_argument("--season", type=int, default=2025, metavar="INT", help="BDL season year (default: 2025)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    for label, val in [("--start", args.start), ("--end", args.end)]:
        try:
            datetime.strptime(val, "%Y-%m-%d")
        except ValueError:
            log.error("%s must be YYYY-MM-DD, got: %s", label, val)
            sys.exit(1)
    asyncio.run(main(args.start, args.end, args.season))
