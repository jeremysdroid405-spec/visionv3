"""Seed synthetic NBA Finals team props for visual UX validation.

This script writes plausible NBA Finals team props (BOS vs DAL) into
`team_live_props` and runs the passthrough → scorer chain so the
ferrari team endpoints surface the rows. Used to validate frontend
parity in the preview pod where the real Odds API live feed is
intentionally disabled (`ODDS_API_KEY` empty per handoff).

USAGE
-----
    cd /app/backend && python scripts/seed_nba_finals_team_props.py

Idempotent: re-running upserts rows on the natural compound key.

PRODUCTION
----------
Do NOT run this on prod. Prod uses the real
`team_live_sync_service.sync_team_live_for_sport` path with a valid
`ODDS_API_KEY`. This script exists purely to unblock visual QA in
the preview/dev pod.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

SPORT = "nba"
HOME_ABBR = "bos"
AWAY_ABBR = "dal"
HOME_NAME = "Boston Celtics"
AWAY_NAME = "Dallas Mavericks"


async def main() -> None:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    # Synthetic NBA Finals event (commence_time tomorrow 8pm ET).
    commence_time = (datetime.now(timezone.utc)
                     + timedelta(days=1)).replace(
                         hour=0, minute=0, second=0,
                         microsecond=0).isoformat()
    event_id = "NBA_FINALS_G1_SEED"
    snapshot_iso = datetime.now(timezone.utc).isoformat()
    game_date = commence_time[:10]

    # ─── Synthetic books (3 books × multiple markets) ────────────────
    books = ["draftkings", "fanduel", "betmgm"]
    rows = []

    # H2H (moneyline) — BOS favorites, DAL underdog
    for book in books:
        rows.append({
            "event_id":        event_id,
            "team_id":         f"nba_{HOME_ABBR}",
            "opponent_team_id": f"nba_{AWAY_ABBR}",
            "market":          "points-home-game-ml-home",
            "market_key":      "h2h",
            "market_label":    "Moneyline",
            "line":            None,
            "side":            "ML",
            "odds":            -180,
            "book":            book,
            "sport":           SPORT,
            "snapshot_iso":    snapshot_iso,
            "commence_time":   commence_time,
            "game_date":       game_date,
            "home_team":       HOME_NAME,
            "away_team":       AWAY_NAME,
            "ingested_at":     snapshot_iso,
        })
        rows.append({
            "event_id":        event_id,
            "team_id":         f"nba_{AWAY_ABBR}",
            "opponent_team_id": f"nba_{HOME_ABBR}",
            "market":          "points-away-game-ml-away",
            "market_key":      "h2h",
            "market_label":    "Moneyline",
            "line":            None,
            "side":            "ML",
            "odds":            +155,
            "book":            book,
            "sport":           SPORT,
            "snapshot_iso":    snapshot_iso,
            "commence_time":   commence_time,
            "game_date":       game_date,
            "home_team":       HOME_NAME,
            "away_team":       AWAY_NAME,
            "ingested_at":     snapshot_iso,
        })

    # Spreads — BOS -5.5, DAL +5.5
    for book in books:
        rows.append({
            "event_id":        event_id,
            "team_id":         f"nba_{HOME_ABBR}",
            "market":          "points-home-game-sp-home",
            "market_key":      "spreads",
            "market_label":    "Spread",
            "line":            -5.5,
            "side":            "HOME",
            "odds":            -110,
            "book":            book,
            "sport":           SPORT,
            "snapshot_iso":    snapshot_iso,
            "commence_time":   commence_time,
            "game_date":       game_date,
            "home_team":       HOME_NAME,
            "away_team":       AWAY_NAME,
            "ingested_at":     snapshot_iso,
        })
        rows.append({
            "event_id":        event_id,
            "team_id":         f"nba_{AWAY_ABBR}",
            "market":          "points-away-game-sp-away",
            "market_key":      "spreads",
            "market_label":    "Spread",
            "line":            5.5,
            "side":            "AWAY",
            "odds":            -110,
            "book":            book,
            "sport":           SPORT,
            "snapshot_iso":    snapshot_iso,
            "commence_time":   commence_time,
            "game_date":       game_date,
            "home_team":       HOME_NAME,
            "away_team":       AWAY_NAME,
            "ingested_at":     snapshot_iso,
        })

    # Game totals — Over/Under 215.5
    for book in books:
        rows.append({
            "event_id":        event_id,
            "team_id":         f"nba_{HOME_ABBR}",
            "market":          "points-all-game-ou-over",
            "market_key":      "totals",
            "market_label":    "Game Total",
            "line":            215.5,
            "side":            "OVER",
            "odds":            -110,
            "book":            book,
            "sport":           SPORT,
            "snapshot_iso":    snapshot_iso,
            "commence_time":   commence_time,
            "game_date":       game_date,
            "home_team":       HOME_NAME,
            "away_team":       AWAY_NAME,
            "ingested_at":     snapshot_iso,
        })
        rows.append({
            "event_id":        event_id,
            "team_id":         f"nba_{HOME_ABBR}",
            "market":          "points-all-game-ou-under",
            "market_key":      "totals",
            "market_label":    "Game Total",
            "line":            215.5,
            "side":            "UNDER",
            "odds":            -110,
            "book":            book,
            "sport":           SPORT,
            "snapshot_iso":    snapshot_iso,
            "commence_time":   commence_time,
            "game_date":       game_date,
            "home_team":       HOME_NAME,
            "away_team":       AWAY_NAME,
            "ingested_at":     snapshot_iso,
        })

    # Team totals — BOS 110.5, DAL 105.5
    for book in books:
        for tid, line_o, line_u in [
            (f"nba_{HOME_ABBR}", 110.5, 110.5),
            (f"nba_{AWAY_ABBR}", 105.5, 105.5),
        ]:
            for side, line, mk in [
                ("OVER", line_o, f"points-{'home' if HOME_ABBR in tid else 'away'}-game-ou-over"),
                ("UNDER", line_u, f"points-{'home' if HOME_ABBR in tid else 'away'}-game-ou-under"),
            ]:
                rows.append({
                    "event_id":        event_id,
                    "team_id":         tid,
                    "market":          mk,
                    "market_key":      "team_totals",
                    "market_label":    "Team Total",
                    "line":            line,
                    "side":            side,
                    "odds":            -110,
                    "book":            book,
                    "sport":           SPORT,
                    "snapshot_iso":    snapshot_iso,
                    "commence_time":   commence_time,
                    "game_date":       game_date,
                    "home_team":       HOME_NAME,
                    "away_team":       AWAY_NAME,
                    "ingested_at":     snapshot_iso,
                })

    # ─── Upsert into team_live_props ─────────────────────────────────
    from pymongo import UpdateOne
    ops = [
        UpdateOne(
            {k: r[k] for k in ("event_id", "team_id", "market", "line",
                                "side", "book")},
            {"$set": r, "$setOnInsert": {"first_seen_at": snapshot_iso}},
            upsert=True,
        )
        for r in rows
    ]
    if ops:
        result = await db["team_live_props"].bulk_write(ops, ordered=False)
        print(f"team_live_props upsert: matched={result.matched_count}, "
              f"modified={result.modified_count}, "
              f"upserted={result.upserted_count}")

    # ─── Passthrough → team_prop_scores (uses production code path) ──
    from services.team_prop_passthrough import (
        passthrough_team_live_to_scores,
    )
    pt_audit = await passthrough_team_live_to_scores(db, sport=SPORT)
    print(f"passthrough audit: {pt_audit}")

    # ─── Verify counts ───────────────────────────────────────────────
    n_live = await db["team_live_props"].count_documents(
        {"sport": SPORT, "event_id": event_id})
    n_scored = await db["team_prop_scores"].count_documents(
        {"sport": SPORT, "event_id": event_id})
    print(f"final counts — live={n_live}  scored={n_scored}")


if __name__ == "__main__":
    asyncio.run(main())
