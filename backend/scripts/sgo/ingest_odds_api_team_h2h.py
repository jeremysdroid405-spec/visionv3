"""
ingest_odds_api_team_h2h.py — Pull historical h2h odds from The Odds API
for MLB and NBA games and store them in odds_api_team_h2h.

Usage:
    python scripts/sgo/ingest_odds_api_team_h2h.py --league MLB --start 2025-04-01 --end 2025-04-30
    python scripts/sgo/ingest_odds_api_team_h2h.py --league NBA --start 2025-10-22 --end 2026-04-13
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")

from dotenv import load_dotenv

for _env in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(_env):
        load_dotenv(_env)
        break

import httpx
from motor.motor_asyncio import AsyncIOMotorClient

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SPORT_KEYS = {
    "MLB": "baseball_mlb",
    "NBA": "basketball_nba",
}

WHITELISTED_BOOKS = {
    "draftkings",
    "fanduel",
    "betmgm",
    "williamhill_us",
    "betrivers",
    "espnbet",
    "fanatics",
    "betparx",
    "hardrockbet",
    "bovada",
    "betonlineag",
    "pinnacle",
    "lowvig",
}

COLLECTION = "odds_api_team_h2h"
BASE_URL = "https://api.the-odds-api.com/v4"
DELAY_BETWEEN_CALLS = 0.5  # seconds


# ---------------------------------------------------------------------------
# Odds conversion helpers
# ---------------------------------------------------------------------------

def decimal_to_american(decimal_odds: float) -> Optional[int]:
    """Convert decimal odds to American odds (rounded integer)."""
    if decimal_odds <= 1.0:
        return None
    if decimal_odds >= 2.0:
        return round((decimal_odds - 1) * 100)
    else:
        return round(-100 / (decimal_odds - 1))


def decimal_to_implied(decimal_odds: float) -> float:
    """Convert decimal odds to implied probability (0–1)."""
    if decimal_odds <= 0:
        return 0.0
    return 1.0 / decimal_odds


# ---------------------------------------------------------------------------
# Core fetch logic
# ---------------------------------------------------------------------------

async def fetch_events(
    client: httpx.AsyncClient,
    sport_key: str,
    date_str: str,
    api_key: str,
) -> List[Dict[str, Any]]:
    """Fetch all events for a given sport and date."""
    url = f"{BASE_URL}/historical/sports/{sport_key}/events"
    params = {
        "apiKey": api_key,
        "date": f"{date_str}T18:00:00Z",
    }
    resp = await client.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", [])


async def fetch_event_odds(
    client: httpx.AsyncClient,
    sport_key: str,
    event_id: str,
    date_str: str,
    api_key: str,
) -> Optional[Dict[str, Any]]:
    """Fetch h2h odds for a single event."""
    url = f"{BASE_URL}/historical/sports/{sport_key}/events/{event_id}/odds"
    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": "h2h",
        "date": f"{date_str}T18:00:00Z",
    }
    resp = await client.get(url, params=params, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    return data.get("data")


def parse_books(event_data: Dict[str, Any], home_team: str, away_team: str) -> Dict[str, Any]:
    """
    Extract per-book h2h odds from event data.
    Returns dict of {book_key: {home_odds_american, away_odds_american, home_implied, away_implied}}.
    """
    books: Dict[str, Any] = {}
    bookmakers = event_data.get("bookmakers", []) or []

    for bm in bookmakers:
        key = bm.get("key", "")
        if key not in WHITELISTED_BOOKS:
            continue

        markets = bm.get("markets", []) or []
        h2h_market = next((m for m in markets if m.get("key") == "h2h"), None)
        if not h2h_market:
            continue

        outcomes = {o["name"].lower(): o["price"] for o in h2h_market.get("outcomes", [])}

        home_dec = outcomes.get(home_team)
        away_dec = outcomes.get(away_team)
        if home_dec is None or away_dec is None:
            continue

        home_american = decimal_to_american(home_dec)
        away_american = decimal_to_american(away_dec)
        if home_american is None or away_american is None:
            continue

        books[key] = {
            "home_odds_american": home_american,
            "away_odds_american": away_american,
            "home_implied": round(decimal_to_implied(home_dec), 6),
            "away_implied": round(decimal_to_implied(away_dec), 6),
        }

    return books


def build_document(
    event: Dict[str, Any],
    odds_data: Dict[str, Any],
    sport: str,
    game_date: str,
) -> Dict[str, Any]:
    """Build the MongoDB document for one event."""
    home_team = (odds_data.get("home_team") or event.get("home_team", "")).lower()
    away_team = (odds_data.get("away_team") or event.get("away_team", "")).lower()

    books = parse_books(odds_data, home_team, away_team)

    home_implieds = [v["home_implied"] for v in books.values()]
    away_implieds = [v["away_implied"] for v in books.values()]

    consensus_home = round(sum(home_implieds) / len(home_implieds), 6) if home_implieds else None
    consensus_away = round(sum(away_implieds) / len(away_implieds), 6) if away_implieds else None
    best_home = round(max(home_implieds), 6) if home_implieds else None
    best_away = round(max(away_implieds), 6) if away_implieds else None

    return {
        "odds_api_event_id": event["id"],
        "sport": sport,
        "game_date": game_date,
        "home_team": home_team,
        "away_team": away_team,
        "commence_time": odds_data.get("commence_time") or event.get("commence_time"),
        "books": books,
        "consensus_home_implied": consensus_home,
        "consensus_away_implied": consensus_away,
        "best_home_implied": best_home,
        "best_away_implied": best_away,
        "ingested_at": datetime.utcnow(),
    }


# ---------------------------------------------------------------------------
# Date range helper
# ---------------------------------------------------------------------------

def date_range(start: str, end: str) -> List[str]:
    """Return list of YYYY-MM-DD strings from start to end inclusive."""
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    dates = []
    cur = start_dt
    while cur <= end_dt:
        dates.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return dates


# ---------------------------------------------------------------------------
# Concurrent per-event worker
# ---------------------------------------------------------------------------

async def _fetch_and_upsert(
    sem: asyncio.Semaphore,
    http: httpx.AsyncClient,
    coll,
    sport_key: str,
    event: Dict[str, Any],
    date_str: str,
    api_key: str,
    league: str,
) -> tuple[int, int]:
    """Fetch odds for one event and upsert; returns (upserted, skipped) counts."""
    event_id = event.get("id")
    if not event_id:
        return 0, 0
    async with sem:
        await asyncio.sleep(DELAY_BETWEEN_CALLS)
        try:
            odds_data = await fetch_event_odds(http, sport_key, event_id, date_str, api_key)
        except httpx.HTTPStatusError as exc:
            print(f"  ERROR {event_id}: {exc}")
            return 0, 0
    if odds_data is None:
        return 0, 1
    doc = build_document(event, odds_data, league, date_str)
    await coll.update_one(
        {"odds_api_event_id": doc["odds_api_event_id"]},
        {"$set": doc},
        upsert=True,
    )
    return 1, 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(league: str, start: str, end: str) -> None:
    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        print("ERROR: ODDS_API_KEY not set in environment.", file=sys.stderr)
        sys.exit(1)

    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    sport_key = SPORT_KEYS[league]

    client_motor = AsyncIOMotorClient(mongo_url)
    db = client_motor[db_name]
    coll = db[COLLECTION]

    await coll.create_index("odds_api_event_id", unique=True)

    dates = date_range(start, end)
    print(f"[{league}] Ingesting {len(dates)} dates: {start} → {end}")

    total_upserted = 0
    total_skipped = 0

    sem = asyncio.Semaphore(10)

    async with httpx.AsyncClient() as http:
        for date_str in dates:
            print(f"\n--- {date_str} ---")

            try:
                events = await fetch_events(http, sport_key, date_str, api_key)
            except httpx.HTTPStatusError as exc:
                print(f"  ERROR fetching events: {exc}")
                await asyncio.sleep(DELAY_BETWEEN_CALLS)
                continue

            if not events:
                print(f"  No events found.")
                await asyncio.sleep(DELAY_BETWEEN_CALLS)
                continue

            print(f"  {len(events)} events found. Fetching odds in parallel...")
            await asyncio.sleep(DELAY_BETWEEN_CALLS)

            tasks = [
                _fetch_and_upsert(sem, http, coll, sport_key, ev, date_str, api_key, league)
                for ev in events
            ]
            results = await asyncio.gather(*tasks)
            date_upserted = sum(r[0] for r in results)
            date_skipped = sum(r[1] for r in results)
            total_upserted += date_upserted
            total_skipped += date_skipped
            print(f"  {date_upserted} upserted, {date_skipped} skipped")

    client_motor.close()
    print(f"\nDone. {total_upserted} upserted, {total_skipped} skipped (no odds data).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest historical h2h odds from The Odds API.")
    parser.add_argument("--league", required=True, choices=["MLB", "NBA"], help="League: MLB or NBA")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    args = parser.parse_args()

    asyncio.run(main(args.league, args.start, args.end))
