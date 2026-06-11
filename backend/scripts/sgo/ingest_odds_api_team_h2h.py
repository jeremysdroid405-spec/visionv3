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
        "markets": "h2h,spreads,totals,alternate_spreads,alternate_totals,team_totals,alternate_team_totals",
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


def parse_spreads_totals(
    event_data: Dict[str, Any], home_team: str, away_team: str
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Extract per-book spreads and totals from event data.
    Returns (spread_books, totals_books) dicts keyed by book key.
    """
    spread_books: Dict[str, Any] = {}
    totals_books: Dict[str, Any] = {}
    bookmakers = event_data.get("bookmakers", []) or []

    for bm in bookmakers:
        key = bm.get("key", "")
        if key not in WHITELISTED_BOOKS:
            continue

        markets = bm.get("markets", []) or []

        spreads_market = next((m for m in markets if m.get("key") == "spreads"), None)
        if spreads_market:
            outcomes = {o["name"].lower(): o for o in spreads_market.get("outcomes", [])}
            home_out = outcomes.get(home_team)
            away_out = outcomes.get(away_team)
            if home_out and away_out:
                home_line = home_out.get("point")
                home_dec = home_out.get("price")
                away_dec = away_out.get("price")
                if home_line is not None and home_dec and away_dec:
                    spread_books[key] = {
                        "home_spread_line": home_line,
                        "home_spread_odds": decimal_to_american(home_dec),
                        "away_spread_odds": decimal_to_american(away_dec),
                        "home_spread_implied": round(decimal_to_implied(home_dec), 6),
                        "away_spread_implied": round(decimal_to_implied(away_dec), 6),
                    }

        totals_market = next((m for m in markets if m.get("key") == "totals"), None)
        if totals_market:
            outcomes = {o["name"].lower(): o for o in totals_market.get("outcomes", [])}
            over_out = outcomes.get("over")
            under_out = outcomes.get("under")
            if over_out and under_out:
                total_line = over_out.get("point")
                over_dec = over_out.get("price")
                under_dec = under_out.get("price")
                if total_line is not None and over_dec and under_dec:
                    totals_books[key] = {
                        "total_line": total_line,
                        "over_odds": decimal_to_american(over_dec),
                        "under_odds": decimal_to_american(under_dec),
                        "over_implied": round(decimal_to_implied(over_dec), 6),
                        "under_implied": round(decimal_to_implied(under_dec), 6),
                    }

    return spread_books, totals_books


def parse_team_totals(
    event_data: Dict[str, Any], home_team: str, away_team: str
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Extract per-book team totals and alternate team totals from event data.
    Returns (team_totals_books, alt_team_totals_books) dicts keyed by book key.

    team_totals_books entries: {home_line, home_over_odds, home_under_odds,
        home_over_implied, home_under_implied, away_line, away_over_odds,
        away_under_odds, away_over_implied, away_under_implied}
    alt_team_totals_books entries: list of {team, line, over_odds, under_odds,
        over_implied, under_implied} — description field identifies team.
    """
    team_totals_books: Dict[str, Any] = {}
    alt_team_totals_books: Dict[str, List[Dict[str, Any]]] = {}
    bookmakers = event_data.get("bookmakers", []) or []

    for bm in bookmakers:
        key = bm.get("key", "")
        if key not in WHITELISTED_BOOKS:
            continue

        markets = bm.get("markets", []) or []

        tt_market = next((m for m in markets if m.get("key") == "team_totals"), None)
        if tt_market:
            by_team: Dict[str, Dict[str, Any]] = {}
            for o in tt_market.get("outcomes", []):
                desc = (o.get("description") or "").lower()
                side_name = (o.get("name") or "").lower()
                point = o.get("point")
                price = o.get("price")
                if not desc or not side_name or point is None or price is None:
                    continue
                by_team.setdefault(desc, {})[side_name] = {"point": point, "price": price}

            entry: Dict[str, Any] = {}
            home_data = by_team.get(home_team)
            if home_data:
                home_over = home_data.get("over")
                home_under = home_data.get("under")
                if home_over and home_under:
                    entry["home_line"] = home_over["point"]
                    entry["home_over_odds"] = decimal_to_american(home_over["price"])
                    entry["home_under_odds"] = decimal_to_american(home_under["price"])
                    entry["home_over_implied"] = round(decimal_to_implied(home_over["price"]), 6)
                    entry["home_under_implied"] = round(decimal_to_implied(home_under["price"]), 6)
            away_data = by_team.get(away_team)
            if away_data:
                away_over = away_data.get("over")
                away_under = away_data.get("under")
                if away_over and away_under:
                    entry["away_line"] = away_over["point"]
                    entry["away_over_odds"] = decimal_to_american(away_over["price"])
                    entry["away_under_odds"] = decimal_to_american(away_under["price"])
                    entry["away_over_implied"] = round(decimal_to_implied(away_over["price"]), 6)
                    entry["away_under_implied"] = round(decimal_to_implied(away_under["price"]), 6)
            if entry:
                team_totals_books[key] = entry

        att_market = next((m for m in markets if m.get("key") == "alternate_team_totals"), None)
        if att_market:
            by_team_line: Dict[tuple, Dict[str, float]] = {}
            for o in att_market.get("outcomes", []):
                desc = (o.get("description") or "").lower()
                side_name = (o.get("name") or "").lower()
                point = o.get("point")
                price = o.get("price")
                if not desc or not side_name or point is None or price is None:
                    continue
                by_team_line.setdefault((desc, point), {})[side_name] = price

            book_lines = []
            for (team_desc, line), sides in sorted(by_team_line.items()):
                over_dec = sides.get("over")
                under_dec = sides.get("under")
                if over_dec is None or under_dec is None:
                    continue
                book_lines.append({
                    "team": team_desc,
                    "line": line,
                    "over_odds": decimal_to_american(over_dec),
                    "under_odds": decimal_to_american(under_dec),
                    "over_implied": round(decimal_to_implied(over_dec), 6),
                    "under_implied": round(decimal_to_implied(under_dec), 6),
                })
            if book_lines:
                alt_team_totals_books[key] = book_lines

    return team_totals_books, alt_team_totals_books


def parse_alternates(
    event_data: Dict[str, Any], home_team: str, away_team: str
) -> tuple[Dict[str, Any], Dict[str, Any], List[float], List[float]]:
    """
    Extract per-book alternate spreads and totals.
    Returns (alt_spread_books, alt_totals_books, alt_spread_lines, alt_total_lines).
    Each books dict is keyed by book key; values are lists of line entries sorted by line.
    """
    alt_spread_books: Dict[str, List[Dict[str, Any]]] = {}
    alt_totals_books: Dict[str, List[Dict[str, Any]]] = {}
    all_spread_lines: set = set()
    all_total_lines: set = set()

    bookmakers = event_data.get("bookmakers", []) or []

    for bm in bookmakers:
        key = bm.get("key", "")
        if key not in WHITELISTED_BOOKS:
            continue

        markets = bm.get("markets", []) or []

        alt_totals_market = next((m for m in markets if m.get("key") == "alternate_totals"), None)
        if alt_totals_market:
            by_line: Dict[float, Dict[str, float]] = {}
            for o in alt_totals_market.get("outcomes", []):
                point = o.get("point")
                name = (o.get("name") or "").lower()
                price = o.get("price")
                if point is None or price is None:
                    continue
                by_line.setdefault(point, {})[name] = price

            book_lines = []
            for line, sides in sorted(by_line.items()):
                over_dec = sides.get("over")
                under_dec = sides.get("under")
                if over_dec is None or under_dec is None:
                    continue
                book_lines.append({
                    "line": line,
                    "over_odds": decimal_to_american(over_dec),
                    "under_odds": decimal_to_american(under_dec),
                    "over_implied": round(decimal_to_implied(over_dec), 6),
                    "under_implied": round(decimal_to_implied(under_dec), 6),
                })
                all_total_lines.add(line)

            if book_lines:
                alt_totals_books[key] = book_lines

        alt_spreads_market = next((m for m in markets if m.get("key") == "alternate_spreads"), None)
        if alt_spreads_market:
            # Key by home spread line; away outcome's point is the away spread so home = -point
            by_home_line: Dict[float, Dict[str, Any]] = {}
            for o in alt_spreads_market.get("outcomes", []):
                point = o.get("point")
                name = (o.get("name") or "").lower()
                price = o.get("price")
                if point is None or price is None:
                    continue
                if name == home_team:
                    by_home_line.setdefault(point, {})["home_dec"] = price
                elif name == away_team:
                    by_home_line.setdefault(-point, {})["away_dec"] = price

            book_lines = []
            for home_line, sides in sorted(by_home_line.items()):
                home_dec = sides.get("home_dec")
                away_dec = sides.get("away_dec")
                if home_dec is None or away_dec is None:
                    continue
                book_lines.append({
                    "line": home_line,
                    "home_odds": decimal_to_american(home_dec),
                    "away_odds": decimal_to_american(away_dec),
                    "home_implied": round(decimal_to_implied(home_dec), 6),
                    "away_implied": round(decimal_to_implied(away_dec), 6),
                })
                all_spread_lines.add(home_line)

            if book_lines:
                alt_spread_books[key] = book_lines

    return (
        alt_spread_books,
        alt_totals_books,
        sorted(all_spread_lines),
        sorted(all_total_lines),
    )


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
    spread_books, totals_books = parse_spreads_totals(odds_data, home_team, away_team)
    alt_spread_books, alt_totals_books, alt_spread_lines, alt_total_lines = parse_alternates(
        odds_data, home_team, away_team
    )
    team_totals_books, alt_team_totals_books = parse_team_totals(odds_data, home_team, away_team)

    home_implieds = [v["home_implied"] for v in books.values()]
    away_implieds = [v["away_implied"] for v in books.values()]

    consensus_home = round(sum(home_implieds) / len(home_implieds), 6) if home_implieds else None
    consensus_away = round(sum(away_implieds) / len(away_implieds), 6) if away_implieds else None
    best_home = round(max(home_implieds), 6) if home_implieds else None
    best_away = round(max(away_implieds), 6) if away_implieds else None

    spread_lines = [v["home_spread_line"] for v in spread_books.values()]
    spread_home_imp = [v["home_spread_implied"] for v in spread_books.values()]
    spread_away_imp = [v["away_spread_implied"] for v in spread_books.values()]

    total_lines = [v["total_line"] for v in totals_books.values()]
    over_imp = [v["over_implied"] for v in totals_books.values()]
    under_imp = [v["under_implied"] for v in totals_books.values()]

    home_total_lines = [v["home_line"] for v in team_totals_books.values() if "home_line" in v]
    away_total_lines = [v["away_line"] for v in team_totals_books.values() if "away_line" in v]
    home_over_imps = [v["home_over_implied"] for v in team_totals_books.values() if "home_over_implied" in v]
    away_over_imps = [v["away_over_implied"] for v in team_totals_books.values() if "away_over_implied" in v]

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
        "spread_books": spread_books,
        "consensus_home_spread_line": round(sum(spread_lines) / len(spread_lines), 4) if spread_lines else None,
        "consensus_home_spread_implied": round(sum(spread_home_imp) / len(spread_home_imp), 6) if spread_home_imp else None,
        "consensus_away_spread_implied": round(sum(spread_away_imp) / len(spread_away_imp), 6) if spread_away_imp else None,
        "totals_books": totals_books,
        "consensus_total_line": round(sum(total_lines) / len(total_lines), 4) if total_lines else None,
        "consensus_over_implied": round(sum(over_imp) / len(over_imp), 6) if over_imp else None,
        "consensus_under_implied": round(sum(under_imp) / len(under_imp), 6) if under_imp else None,
        "alternate_spreads_books": alt_spread_books,
        "alternate_totals_books": alt_totals_books,
        "alternate_spread_lines": alt_spread_lines,
        "alternate_total_lines": alt_total_lines,
        "team_totals_books": team_totals_books,
        "alt_team_totals_books": alt_team_totals_books,
        "consensus_home_total_line": round(sum(home_total_lines) / len(home_total_lines), 4) if home_total_lines else None,
        "consensus_away_total_line": round(sum(away_total_lines) / len(away_total_lines), 4) if away_total_lines else None,
        "consensus_home_over_implied": round(sum(home_over_imps) / len(home_over_imps), 6) if home_over_imps else None,
        "consensus_away_over_implied": round(sum(away_over_imps) / len(away_over_imps), 6) if away_over_imps else None,
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
    _BACKFILL_RANGES = {
        "MLB": ("2024-07-01", "2026-06-10"),
        "NBA": ("2024-10-22", "2026-04-13"),
    }

    parser = argparse.ArgumentParser(description="Ingest historical h2h odds from The Odds API.")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--league", choices=["MLB", "NBA"], help="League: MLB or NBA")
    grp.add_argument("--sport", choices=["mlb", "nba"], help="Sport lowercase alias for --league")
    parser.add_argument("--start", help="Start date YYYY-MM-DD (required without --backfill)")
    parser.add_argument("--end", help="End date YYYY-MM-DD (required without --backfill)")
    parser.add_argument("--backfill", action="store_true", help="Ingest full historical season")
    args = parser.parse_args()

    league = args.league or args.sport.upper()
    if args.backfill:
        start, end = _BACKFILL_RANGES[league]
    else:
        if not args.start or not args.end:
            parser.error("--start and --end are required when --backfill is not set.")
        start, end = args.start, args.end

    asyncio.run(main(league, start, end))
