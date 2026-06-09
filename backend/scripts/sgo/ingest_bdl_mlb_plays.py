"""
ingest_bdl_mlb_plays.py — Ingest MLB play-by-play data from BallDontLie into
MongoDB collection `bdl_mlb_plays`.

Each document represents one play, upserted on (game_id, order).
Games already fully ingested (completed=True sentinel doc) are skipped.

Usage:
    python -m scripts.sgo.ingest_bdl_mlb_plays --start 2024-03-20 --end 2024-10-01
    python -m scripts.sgo.ingest_bdl_mlb_plays --start 2022-04-07 --end 2024-10-01

Rate limit: GOAT tier = 600 req/min. Semaphore of 4 concurrent requests + 0.12s
per-request sleep keeps us safely under the ceiling. 429s retry up to 5x with
exponential backoff (2s, 4s, 8s, 16s, 32s) before a game is permanently skipped.

Environment:
    BDL_API_KEY   required
    MONGO_URL     default: mongodb://localhost:27017
    DB_NAME       default: pick_vision
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
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

log = logging.getLogger("bdl.mlb.plays")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)

BDL_BASE = "https://api.balldontlie.io/mlb/v1"
PLAYS_COLL = "bdl_mlb_plays"
# Sentinel collection tracking which game_ids are fully ingested.
STATUS_COLL = "bdl_mlb_plays_status"
PER_PAGE = 100
SEMAPHORE_SIZE = 2
RATE_LIMIT_SLEEP = 0.5  # 120 req/min, well under 600 but safe for burst
MAX_RETRIES = 5


# ---------------------------------------------------------------------------
# BDL HTTP helpers
# ---------------------------------------------------------------------------

async def _get(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    url: str,
    params: dict,
    api_key: str,
) -> dict:
    """Authenticated GET with semaphore, rate-limit sleep, and 429 retry."""
    headers = {"Authorization": api_key}
    for attempt in range(MAX_RETRIES):
        async with sem:
            resp = await client.get(url, params=params, headers=headers, timeout=30)
            await asyncio.sleep(RATE_LIMIT_SLEEP)
        if resp.status_code == 429:
            wait = 2 ** (attempt + 1)  # 2, 4, 8, 16, 32
            log.warning("429 rate limited — retry %d/%d in %ds", attempt + 1, MAX_RETRIES, wait)
            await asyncio.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise httpx.HTTPStatusError(
        f"429 after {MAX_RETRIES} retries", request=resp.request, response=resp
    )


async def fetch_games(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    api_key: str,
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Fetch all MLB games in [start_date, end_date], handling pagination."""
    games: list[dict] = []
    cursor: str | None = None

    while True:
        params: dict[str, Any] = {
            "start_date": start_date,
            "end_date": end_date,
            "per_page": 100,
        }
        if cursor:
            params["cursor"] = cursor

        data = await _get(client, sem, f"{BDL_BASE}/games", params, api_key)
        games.extend(data.get("data", []))

        meta = data.get("meta", {})
        cursor = meta.get("next_cursor")
        if not cursor:
            break

    return games


async def fetch_plays_for_game(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    api_key: str,
    game_id: int,
) -> list[dict]:
    """Fetch all plays for a single game, handling pagination."""
    plays: list[dict] = []
    cursor: str | None = None

    while True:
        params: dict[str, Any] = {"game_id": game_id, "per_page": PER_PAGE}
        if cursor:
            params["cursor"] = cursor

        data = await _get(client, sem, f"{BDL_BASE}/plays", params, api_key)
        plays.extend(data.get("data", []))

        meta = data.get("meta", {})
        cursor = meta.get("next_cursor")
        if not cursor:
            break

    return plays


# ---------------------------------------------------------------------------
# Per-game ingest task
# ---------------------------------------------------------------------------

async def ingest_game(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    api_key: str,
    game: dict,
    db: Any,
    counters: dict,
) -> None:
    game_id: int = game["id"]

    # Check sentinel — skip if already completed.
    already = await db[STATUS_COLL].find_one({"game_id": game_id, "completed": True})
    if already:
        counters["skipped"] += 1
        return

    game_date: str = game.get("date", "")[:10]  # "YYYY-MM-DD"
    home = game.get("home_team", {})
    away = game.get("away_team", {})

    try:
        plays = await fetch_plays_for_game(client, sem, api_key, game_id)
    except httpx.HTTPStatusError as exc:
        log.error("game %s PERMANENT FAIL HTTP %s — all retries exhausted", game_id, exc.response.status_code)
        counters["errors"] += 1
        return
    except Exception as exc:
        log.error("game %s PERMANENT FAIL: %s", game_id, exc)
        counters["errors"] += 1
        return

    if not plays:
        # Game exists but no plays (postponed / no data) — mark completed to skip next time.
        await db[STATUS_COLL].update_one(
            {"game_id": game_id},
            {"$set": {"game_id": game_id, "completed": True, "play_count": 0,
                       "ingested_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        return

    ops = []
    for play in plays:
        order = play.get("order") if play.get("order") is not None else play.get("id")
        doc = {
            **play,
            "game_id": game_id,
            "game_date": game_date,
            "home_team_id": home.get("id"),
            "home_team_name": home.get("full_name") or home.get("name"),
            "away_team_id": away.get("id"),
            "away_team_name": away.get("full_name") or away.get("name"),
        }
        ops.append(
            UpdateOne(
                {"game_id": game_id, "order": order},
                {"$set": doc},
                upsert=True,
            )
        )

    if ops:
        result = await db[PLAYS_COLL].bulk_write(ops, ordered=False)
        upserted = result.upserted_count + result.modified_count
        counters["plays"] += upserted

    # Mark game completed.
    await db[STATUS_COLL].update_one(
        {"game_id": game_id},
        {"$set": {"game_id": game_id, "completed": True, "play_count": len(plays),
                   "ingested_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    counters["games"] += 1

    if counters["games"] % 50 == 0:
        log.info(
            "progress — games: %d  plays upserted: %d  skipped: %d  errors: %d",
            counters["games"], counters["plays"], counters["skipped"], counters["errors"],
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(start_date: str, end_date: str) -> None:
    api_key = os.environ.get("BDL_API_KEY") or os.environ.get("BALLDONTLIE_API_KEY")
    if not api_key:
        log.error("BDL_API_KEY not set — aborting")
        sys.exit(1)

    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    client_mongo = AsyncIOMotorClient(mongo_url)
    db = client_mongo[db_name]

    # Ensure indexes once.
    await db[PLAYS_COLL].create_index([("game_id", 1), ("order", 1)], unique=True, background=True)
    await db[PLAYS_COLL].create_index([("game_date", 1)], background=True)
    await db[STATUS_COLL].create_index([("game_id", 1)], unique=True, background=True)

    sem = asyncio.Semaphore(SEMAPHORE_SIZE)
    counters: dict[str, int] = {"games": 0, "plays": 0, "skipped": 0, "errors": 0}

    async with httpx.AsyncClient() as http:
        log.info("fetching game list %s → %s …", start_date, end_date)
        try:
            games = await fetch_games(http, sem, api_key, start_date, end_date)
        except Exception as exc:
            log.error("failed to fetch game list: %s", exc)
            sys.exit(1)

        log.info("found %d games — ingesting plays …", len(games))

        tasks = [
            ingest_game(http, sem, api_key, game, db, counters)
            for game in games
        ]
        await asyncio.gather(*tasks)

    log.info(
        "done — games ingested: %d  plays upserted: %d  skipped: %d  errors: %d",
        counters["games"], counters["plays"], counters["skipped"], counters["errors"],
    )
    client_mongo.close()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest BDL MLB play-by-play into bdl_mlb_plays")
    p.add_argument("--start", required=True, metavar="YYYY-MM-DD", help="Start date (inclusive)")
    p.add_argument("--end", required=True, metavar="YYYY-MM-DD", help="End date (inclusive)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    # Validate date format early.
    for label, val in [("--start", args.start), ("--end", args.end)]:
        try:
            datetime.strptime(val, "%Y-%m-%d")
        except ValueError:
            log.error("%s must be YYYY-MM-DD, got: %s", label, val)
            sys.exit(1)
    asyncio.run(main(args.start, args.end))
