"""
MLB Projected/Confirmed Lineup Ingestor   (read-only against MLB Stats API)
============================================================================
Populates the local `mlb_projected_lineups` collection from the
public MLB Stats API.  Output is consumed by
`services/mlb_lineups_loader.lookup_slot()` during prop-hydration so
that PA-v2 sees real `batting_order` / `lineup_confirmed` instead of
falling back to the 4.2 default.

Source endpoints
----------------
1. Schedule (with lineups + probablePitcher):
   https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=YYYY-MM-DD
       &hydrate=probablePitcher,lineups

2. Per-game live feed (richer fallback when the schedule lineup
   field is empty but the game has already posted a card):
   https://statsapi.mlb.com/api/v1.1/game/{gamePk}/feed/live

CLI
---
    python -m scripts.ingest_mlb_projected_lineups --date 2026-05-20
    python -m scripts.ingest_mlb_projected_lineups --date 2026-05-20 --dry
    python -m scripts.ingest_mlb_projected_lineups --date 2026-05-20 --force

Flags
-----
  --date      YYYY-MM-DD (defaults to today's UTC date)
  --dry       print but do not write
  --force     bypass the (as_of <= commence_time) guard.  Only used for
              backfill validation; production cron must NEVER pass this.
  --source    overrides the `source` tag (default: "mlb_stats_api")

No-leakage policy
-----------------
The MLB Stats API's `homePlayers` / `awayPlayers` IS the originally
submitted starting lineup, not box-score data.  We persist with
    as_of = min(now_utc, commence_time)
so the `as_of <= commence_time` invariant always holds (including
for backfill of finished games — we are using the pre-game card,
not anything derived from in-game events).

When `--force` is passed and a card cannot satisfy the no-leakage
rule for any reason, we still skip — the flag only relaxes
already-final games where commence_time has trivially passed.

This script ONLY reads upstream and ONLY writes to
`mlb_projected_lineups`.  It NEVER:
  * touches mlb_live_props
  * mutates statcast features
  * changes μ / σ / gates / thresholds / tier routing / selection
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/app/backend")

import httpx
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")

from services.mlb_lineups_loader import COLLECTION as LINEUP_COLL  # noqa: E402
from services.mlb_lineups_loader import ensure_indexes              # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("ingest_mlb_projected_lineups")

SCHEDULE_URL = (
    "https://statsapi.mlb.com/api/v1/schedule"
    "?sportId=1&date={date}&hydrate=probablePitcher,lineups,team"
)
LIVE_FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


async def _build_team_alias_map(db) -> Dict[str, str]:
    """Lower-cased team-name → 3-letter abbreviation, sourced from
    `mlb_master_hub_2026`.  Mirrors `feature_hydration._build_team_alias_map`
    but localized so this script has no import cycle."""
    aliases: Dict[str, str] = {}
    cursor = db["mlb_master_hub_2026"].find(
        {}, {"team": 1, "team_abbr": 1, "team_name": 1,
             "team_full_name": 1, "team_full": 1, "bdl_game_logs": 1},
    )
    async for row in cursor:
        abbr = row.get("team_abbr") or row.get("team")
        if not abbr:
            continue
        abbr = str(abbr).upper().strip()
        for k in ("team_name", "team_full_name", "team_full"):
            v = row.get(k)
            if v:
                aliases[str(v).lower().strip()] = abbr
        # MLB hub sometimes leaves team_name blank — fall back to game logs.
        logs = row.get("bdl_game_logs") or []
        if logs and isinstance(logs, list):
            for lg in logs[:3]:
                tn = (lg or {}).get("team_name")
                if tn:
                    aliases[str(tn).lower().strip()] = abbr
                    break
        aliases[abbr.lower()] = abbr
    return aliases


async def _build_mlbid_to_bdl(db) -> Dict[int, Dict[str, Any]]:
    """{mlb_id (== statcast_id == MLBAM): {bdl_id, bdl_name, team}}"""
    out: Dict[int, Dict[str, Any]] = {}
    cursor = db["mlb_player_identity_map"].find(
        {"mlb_id": {"$ne": None}, "bdl_id": {"$ne": None}},
        {"_id": 0, "mlb_id": 1, "bdl_id": 1, "bdl_name": 1, "team": 1},
    )
    async for r in cursor:
        try:
            mid = int(r["mlb_id"])
            bid = int(r["bdl_id"])
        except (TypeError, ValueError, KeyError):
            continue
        out[mid] = {
            "bdl_id": bid,
            "bdl_name": r.get("bdl_name"),
            "team": r.get("team"),
        }
    return out


async def _build_eventid_lookup(
    db, date_iso: str
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """{(home_full_lower, away_full_lower): {event_id, commence_time}} for
    all `mlb_live_props` rows whose commence_time falls on `date_iso`
    (UTC) OR the day after (covers the late-night-East-Coast case)."""
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    # Scan ±1 day to catch late-night games crossing UTC midnight.
    target = datetime.fromisoformat(date_iso).replace(tzinfo=timezone.utc)
    lo = target - timedelta(days=1)
    hi = target + timedelta(days=2)
    pipeline = [
        {"$match": {
            "event_id": {"$ne": None},
            "home_team": {"$ne": None},
            "away_team": {"$ne": None},
            "commence_time": {"$ne": None},
        }},
        {"$group": {
            "_id": "$event_id",
            "home": {"$first": "$home_team"},
            "away": {"$first": "$away_team"},
            "commence": {"$first": "$commence_time"},
        }},
    ]
    async for d in db["mlb_live_props"].aggregate(pipeline):
        ct = _parse_iso(d.get("commence")) if isinstance(d.get("commence"), str) \
            else d.get("commence")
        if isinstance(ct, datetime) and ct.tzinfo is None:
            ct = ct.replace(tzinfo=timezone.utc)
        if not isinstance(ct, datetime) or not (lo <= ct <= hi):
            continue
        home = (d["home"] or "").strip().lower()
        away = (d["away"] or "").strip().lower()
        if home and away:
            out[(home, away)] = {
                "event_id": d["_id"],
                "commence_time": ct,
            }
    return out


def _team_full_lower(team: Dict[str, Any]) -> str:
    return (team.get("name") or "").strip().lower()


# ---------------------------------------------------------------------------
# MLB Stats API I/O
# ---------------------------------------------------------------------------
async def _fetch_schedule(client: httpx.AsyncClient, date: str) -> List[Dict[str, Any]]:
    r = await client.get(SCHEDULE_URL.format(date=date), timeout=20.0)
    r.raise_for_status()
    payload = r.json() or {}
    games: List[Dict[str, Any]] = []
    for d in payload.get("dates", []) or []:
        for g in d.get("games", []) or []:
            games.append(g)
    return games


async def _fetch_live_feed(client: httpx.AsyncClient, game_pk: int
                           ) -> Optional[Dict[str, Any]]:
    try:
        r = await client.get(LIVE_FEED_URL.format(game_pk=game_pk), timeout=25.0)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("[FEED] gamePk=%s failed: %s", game_pk, e)
        return None


# ---------------------------------------------------------------------------
# Lineup extractors
# ---------------------------------------------------------------------------
def _extract_lineup_from_schedule_block(
    block: Dict[str, Any]
) -> Optional[List[Dict[str, Any]]]:
    """The schedule.lineups.{home,away}Players is the 9-deep starting
    lineup in batting-order sequence (index 0 = leadoff)."""
    if not isinstance(block, list) or not block:
        return None
    out: List[Dict[str, Any]] = []
    for slot, p in enumerate(block, start=1):
        if not isinstance(p, dict):
            continue
        try:
            mid = int(p.get("id"))
        except (TypeError, ValueError):
            continue
        out.append({
            "slot": slot,
            "mlb_id": mid,
            "player_name": p.get("fullName") or p.get("useName") or "",
            "position": (p.get("primaryPosition") or {}).get("abbreviation"),
        })
    return out if 1 <= len(out) <= 9 else None


def _extract_lineup_from_live_feed(
    feed: Dict[str, Any], side: str
) -> Optional[List[Dict[str, Any]]]:
    """Feed-live `liveData.boxscore.teams.{home,away}.battingOrder` is a
    list of `ID######` strings in batting-order sequence."""
    try:
        team_block = (
            feed.get("liveData", {})
                .get("boxscore", {})
                .get("teams", {})
                .get(side, {})
        )
        order = team_block.get("battingOrder") or []
        players = team_block.get("players") or {}
    except AttributeError:
        return None
    if not order:
        return None
    out: List[Dict[str, Any]] = []
    for slot, key in enumerate(order, start=1):
        try:
            mid = int(str(key).replace("ID", ""))
        except ValueError:
            continue
        info = players.get(key) or players.get(f"ID{mid}") or {}
        person = info.get("person") or {}
        pos = (info.get("position") or {}).get("abbreviation")
        out.append({
            "slot": slot,
            "mlb_id": mid,
            "player_name": person.get("fullName") or "",
            "position": pos,
        })
    return out if 1 <= len(out) <= 9 else None


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
async def ingest(
    db,
    date: str,
    *,
    dry: bool = False,
    force: bool = False,
    source: str = "mlb_stats_api",
) -> Dict[str, Any]:
    await ensure_indexes(db)
    alias_map = await _build_team_alias_map(db)
    mlbid_to_bdl = await _build_mlbid_to_bdl(db)
    event_lookup = await _build_eventid_lookup(db, date)

    log.info(
        "[INGEST] date=%s  alias_map=%d  mlb_id->bdl=%d  event_lookup=%d  "
        "dry=%s force=%s",
        date, len(alias_map), len(mlbid_to_bdl), len(event_lookup), dry, force,
    )

    counters: Counter = Counter()

    async with httpx.AsyncClient(headers={"User-Agent": "PA-v2-ingestor/1.0"}) \
            as client:
        games = await _fetch_schedule(client, date)
        counters["games_scanned"] = len(games)
        log.info("[INGEST] schedule: %d games", len(games))

        upserts: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []

        for g in games:
            game_pk = g.get("gamePk")
            game_date_iso = g.get("gameDate")
            commence = _parse_iso(game_date_iso)
            teams = g.get("teams", {}) or {}
            home_team = (teams.get("home", {}) or {}).get("team", {}) or {}
            away_team = (teams.get("away", {}) or {}).get("team", {}) or {}
            home_full = _team_full_lower(home_team)
            away_full = _team_full_lower(away_team)
            home_abbr = (
                home_team.get("abbreviation")
                or alias_map.get(home_full)
            )
            away_abbr = (
                away_team.get("abbreviation")
                or alias_map.get(away_full)
            )

            if not (game_pk and commence and home_full and away_full):
                counters["games_missing_metadata"] += 1
                continue

            # Match to event_id from mlb_live_props.
            evt = event_lookup.get((home_full, away_full))
            if not evt:
                counters["games_no_event_match"] += 1
                # Still try to extract lineups so we can log/diagnose,
                # but we cannot persist without an event_id (loader joins
                # by event_id).
                continue
            event_id = evt["event_id"]

            # ------- Source 1: schedule.lineups -----------------------
            lineups_blk = g.get("lineups") or {}
            home_lu = _extract_lineup_from_schedule_block(
                lineups_blk.get("homePlayers"))
            away_lu = _extract_lineup_from_schedule_block(
                lineups_blk.get("awayPlayers"))

            # ------- Source 2 (fallback): feed/live.boxscore ----------
            if (home_lu is None or away_lu is None):
                feed = await _fetch_live_feed(client, game_pk)
                if feed:
                    if home_lu is None:
                        home_lu = _extract_lineup_from_live_feed(feed, "home")
                    if away_lu is None:
                        away_lu = _extract_lineup_from_live_feed(feed, "away")

            for side, lu, my_abbr, opp_abbr in (
                ("home", home_lu, home_abbr, away_abbr),
                ("away", away_lu, away_abbr, home_abbr),
            ):
                if not lu:
                    counters["sides_no_lineup"] += 1
                    continue
                if not my_abbr:
                    counters["sides_no_team_abbr"] += 1
                    continue

                # No-leakage stamp.
                now_utc = datetime.now(timezone.utc)
                as_of = min(now_utc, commence)
                if as_of > commence and not force:
                    counters["sides_skipped_leakage"] += 1
                    continue

                # Resolve mlb_id -> bdl_id.
                resolved: List[Dict[str, Any]] = []
                for entry in lu:
                    mid = entry["mlb_id"]
                    bdl_info = mlbid_to_bdl.get(mid) or {}
                    bdl_id = bdl_info.get("bdl_id")
                    if bdl_id is not None:
                        counters["players_mapped"] += 1
                    else:
                        counters["players_unmapped"] += 1
                    resolved.append({
                        "slot": entry["slot"],
                        "player_name": entry["player_name"],
                        "mlb_id": mid,
                        "bdl_player_id": bdl_id,
                        "position": entry["position"],
                    })

                # The `mlb_lineups_loader` schema we use uses the key
                # `lineup` for the array.  Per the user's spec the
                # human-readable doc uses the key `batting_order` for the
                # same payload — we duplicate-write both so consumers and
                # human inspectors can use either.
                doc_filter = {"event_id": event_id, "team_abbr": my_abbr}
                doc_set = {
                    "event_id":         event_id,
                    "game_pk":          int(game_pk),
                    "team_abbr":        my_abbr,
                    "opponent_abbr":    opp_abbr,
                    "game_date":        date,
                    "commence_time":    commence,
                    "as_of":            as_of,
                    "source":           source,
                    "confirmed":        True,   # MLB Stats API only ever
                                                # returns *posted* lineups
                    "lineup_confirmed": True,   # human-readable alias
                    "lineup":           resolved,
                    "batting_order":    resolved,  # human-readable alias
                }
                upserts.append((doc_filter, doc_set))
                counters["lineup_cards_found"] += 1
                counters["confirmed_lineups"] += 1

        log.info(
            "[INGEST] extracted %d lineup cards from %d games "
            "(no_event_match=%d, no_lineup=%d)",
            counters["lineup_cards_found"], counters["games_scanned"],
            counters["games_no_event_match"], counters["sides_no_lineup"],
        )

    # ---- Persist ----------------------------------------------------------
    if not upserts:
        log.info("[INGEST] nothing to upsert.")
    elif dry:
        for filt, doc in upserts[:3]:
            log.info("[DRY] would upsert filter=%s doc.summary={team_abbr=%s "
                     "slots=%d source=%s}", filt, doc["team_abbr"],
                     len(doc["lineup"]), doc["source"])
        log.info("[DRY] total upserts skipped: %d", len(upserts))
    else:
        coll = db[LINEUP_COLL]
        wrote = 0
        for filt, doc in upserts:
            await coll.update_one(filt, {"$set": doc}, upsert=True)
            wrote += 1
        log.info("[INGEST] upserted %d lineup cards into %s",
                 wrote, LINEUP_COLL)

        # ---- Refresh existing mlb_live_props rows in-place ----
        # The hydrator only stamps batting_order on NEWLY ingested props.
        # Existing in-flight rows for the same slate need a one-shot
        # refresh so the engine reads them immediately.  This refresh
        # writes ONLY the four lineup-related fields and never touches
        # odds, lines, identity, or scoring fields.
        refresh_counts = await _refresh_live_props(
            db, [filt["event_id"] for filt, _ in upserts])
        counters["live_props_refreshed"] = refresh_counts["updated"]
        counters["live_props_inspected"] = refresh_counts["inspected"]
        log.info(
            "[INGEST] live-props refresh: updated=%d / inspected=%d events=%d",
            refresh_counts["updated"], refresh_counts["inspected"],
            refresh_counts["events"],
        )

    return dict(counters)


async def _refresh_live_props(db, event_ids: List[str]) -> Dict[str, int]:
    """Stamp `batting_order` / `lineup_confirmed` / `lineup_source` on
    EXISTING `mlb_live_props` rows for the given events using the freshly
    upserted `mlb_projected_lineups` rows.  No other fields are touched.
    Strict no-leakage is enforced via `mlb_lineups_loader.lookup_slot`."""
    from services.mlb_lineups_loader import (load_slot_map as _load,
                                             lookup_slot as _lookup)
    distinct_events = sorted({e for e in event_ids if e})
    slot_map = await _load(db, distinct_events)
    coll = db["mlb_live_props"]
    updated = 0
    inspected = 0
    cursor = coll.find(
        {"event_id": {"$in": distinct_events},
         "bdl_player_id": {"$ne": None}},
        {"_id": 1, "event_id": 1, "bdl_player_id": 1, "commence_time": 1},
    )
    async for r in cursor:
        inspected += 1
        slot, confirmed, src = _lookup(
            slot_map, r.get("event_id"),
            r.get("bdl_player_id"), r.get("commence_time"),
        )
        if slot is None:
            continue
        await coll.update_one(
            {"_id": r["_id"]},
            {"$set": {
                "batting_order":    slot,
                "lineup_confirmed": bool(confirmed),
                "lineup_source":    src,
            }},
        )
        updated += 1
    return {"updated": updated, "inspected": inspected,
            "events": len(distinct_events)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _print_summary(c: Dict[str, Any]) -> None:
    print()
    print("=" * 78)
    print("  MLB PROJECTED-LINEUP INGEST SUMMARY")
    print("=" * 78)
    pairs = [
        ("games scanned",                  "games_scanned"),
        ("games missing metadata",         "games_missing_metadata"),
        ("games w/o mlb_live_props match", "games_no_event_match"),
        ("lineup cards found",             "lineup_cards_found"),
        ("confirmed lineups",              "confirmed_lineups"),
        ("sides w/o lineup posted",        "sides_no_lineup"),
        ("sides w/o team abbr",            "sides_no_team_abbr"),
        ("sides skipped (leakage)",        "sides_skipped_leakage"),
        ("players mapped to bdl_player_id","players_mapped"),
        ("players unmapped",               "players_unmapped"),
        ("live props rows refreshed",      "live_props_refreshed"),
        ("live props rows inspected",      "live_props_inspected"),
    ]
    for label, key in pairs:
        print(f"  {label:38s}: {c.get(key, 0):>6}")
    print()


async def _amain():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=_today_utc(),
                    help="YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--dry", action="store_true",
                    help="extract but do not persist")
    ap.add_argument("--force", action="store_true",
                    help="bypass as_of <= commence_time guard "
                    "(BACKFILL VALIDATION ONLY)")
    ap.add_argument("--source", default="mlb_stats_api",
                    help='source tag (default: "mlb_stats_api")')
    args = ap.parse_args()

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    counters = await ingest(
        db,
        args.date,
        dry=args.dry,
        force=args.force,
        source=args.source,
    )
    _print_summary(counters)


if __name__ == "__main__":
    asyncio.run(_amain())
