"""
ingest_bdl_nba_game_boxscores.py — Ingest NBA game-level box scores from BDL
into MongoDB collection `bdl_nba_game_boxscores`.

One document per game, upserted on game_id. Only "Final" status games are stored.
Quarter scores (Q1–Q4) and OT periods are extracted. Team IDs are resolved to
canonical nba_xxx identifiers via get_bdl_team_mapping('nba').

Usage:
    python -m scripts.sgo.ingest_bdl_nba_game_boxscores --start 2025-10-01 --end 2026-06-10
    python -m scripts.sgo.ingest_bdl_nba_game_boxscores --start 2024-10-01 --end 2025-06-30

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

from scripts.sgo import get_bdl_team_mapping

log = logging.getLogger("bdl.nba.boxscores")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)

BDL_BASE = "https://api.balldontlie.io/v1"
COLLECTION = "bdl_nba_game_boxscores"
PER_PAGE = 100
SEMAPHORE_SIZE = 5
RATE_LIMIT_SLEEP = 0.1
MAX_RETRIES = 5
STATUS_FINAL = "Final"
UPSERT_CHUNK = 500


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
# Game fetching
# ---------------------------------------------------------------------------

async def fetch_season_games(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    api_key: str,
    season: int,
) -> list[dict]:
    """Fetch all games for one season, fully paginated."""
    games: list[dict] = []
    cursor: str | None = None
    page = 0

    while True:
        params: list[tuple] = [
            ("seasons[]", season),
            ("per_page", PER_PAGE),
        ]
        if cursor:
            params.append(("cursor", cursor))

        data = await _get(client, sem, f"{BDL_BASE}/games", params, api_key)
        batch = data.get("data", [])
        games.extend(batch)
        page += 1

        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break

        if page % 10 == 0:
            log.info("  season %d — page %d, %d games so far …", season, page, len(games))

    log.info("season %d — %d games fetched (%d pages)", season, len(games), page)
    return games


def seasons_from_dates(start: str, end: str) -> list[int]:
    """Return every calendar year that spans [start, end]."""
    return list(range(int(start[:4]), int(end[:4]) + 1))


# ---------------------------------------------------------------------------
# Document extraction
# ---------------------------------------------------------------------------

def _to_int(val: Any) -> int | None:
    try:
        return int(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _extract_quarter_scores(game: dict, side: str) -> list[int | None]:
    """Extract Q1–Q4 scores for 'home' or 'visitor'."""
    return [
        _to_int(game.get(f"{side}_q{q}"))
        for q in range(1, 5)
    ]


def _extract_ot_scores(game: dict, side: str) -> list[int | None]:
    """Extract OT period scores (ot1, ot2, ot3) — omit trailing Nones."""
    ots = [
        _to_int(game.get(f"{side}_ot{n}"))
        for n in range(1, 4)
    ]
    # Trim trailing None values so docs stay compact for non-OT games.
    while ots and ots[-1] is None:
        ots.pop()
    return ots


def build_doc(game: dict, team_map: dict[int, str]) -> dict:
    home = game.get("home_team") or {}
    visitor = game.get("visitor_team") or {}

    home_bdl_id = home.get("id")
    away_bdl_id = visitor.get("id")

    return {
        "game_id": game["id"],
        "game_date": (game.get("date") or "")[:10],
        "season": game.get("season"),
        "home_team_id": home_bdl_id,
        "away_team_id": away_bdl_id,
        "home_nba_id": team_map.get(home_bdl_id),
        "away_nba_id": team_map.get(away_bdl_id),
        "home_abbr": home.get("abbreviation"),
        "away_abbr": visitor.get("abbreviation"),
        "home_score": _to_int(game.get("home_team_score")),
        "away_score": _to_int(game.get("visitor_team_score")),
        "home_quarter_scores": _extract_quarter_scores(game, "home"),
        "away_quarter_scores": _extract_quarter_scores(game, "visitor"),
        "home_ot_scores": _extract_ot_scores(game, "home"),
        "away_ot_scores": _extract_ot_scores(game, "visitor"),
        "status": game.get("status"),
        "postseason": game.get("postseason", False),
        "ingested_at": datetime.now(timezone.utc),
    }


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
    coll = client_mongo[db_name][COLLECTION]

    await coll.create_index([("game_id", 1)], unique=True, background=True)
    await coll.create_index([("game_date", 1)], background=True)
    await coll.create_index([("season", 1)], background=True)

    team_map = get_bdl_team_mapping("nba")
    seasons = seasons_from_dates(start_date, end_date)
    log.info("date range %s → %s  seasons: %s", start_date, end_date, seasons)

    sem = asyncio.Semaphore(SEMAPHORE_SIZE)

    async with httpx.AsyncClient() as http:
        season_batches = await asyncio.gather(
            *[fetch_season_games(http, sem, api_key, s) for s in seasons]
        )

    all_games: list[dict] = [g for batch in season_batches for g in batch]
    log.info("fetched %d total games across %d season(s)", len(all_games), len(seasons))

    final_in_range = [
        g for g in all_games
        if g.get("status") == STATUS_FINAL
        and start_date <= (g.get("date") or "")[:10] <= end_date
    ]
    skipped = len(all_games) - len(final_in_range)
    log.info(
        "%d final games in range | %d skipped (non-final or out of range)",
        len(final_in_range), skipped,
    )

    if not final_in_range:
        log.info("nothing to upsert — done")
        client_mongo.close()
        return

    total_upserted = 0
    total_modified = 0

    for i in range(0, len(final_in_range), UPSERT_CHUNK):
        chunk = final_in_range[i : i + UPSERT_CHUNK]
        ops = [
            UpdateOne({"game_id": doc["game_id"]}, {"$set": doc}, upsert=True)
            for doc in (build_doc(g, team_map) for g in chunk)
        ]
        result = await coll.bulk_write(ops, ordered=False)
        total_upserted += result.upserted_count
        total_modified += result.modified_count
        log.info(
            "progress — %d/%d games written (inserted: %d  updated: %d)",
            min(i + UPSERT_CHUNK, len(final_in_range)),
            len(final_in_range),
            total_upserted,
            total_modified,
        )

    log.info(
        "done — total inserted: %d  total updated: %d  skipped: %d",
        total_upserted, total_modified, skipped,
    )
    client_mongo.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Ingest BDL NBA game box scores into bdl_nba_game_boxscores"
    )
    p.add_argument("--start", required=True, metavar="YYYY-MM-DD", help="Start date (inclusive)")
    p.add_argument("--end", required=True, metavar="YYYY-MM-DD", help="End date (inclusive)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    for label, val in [("--start", args.start), ("--end", args.end)]:
        try:
            datetime.strptime(val, "%Y-%m-%d")
        except ValueError:
            log.error("%s must be YYYY-MM-DD, got: %s", label, val)
            sys.exit(1)
    asyncio.run(main(args.start, args.end))
