"""
reshape_sgo_to_replay_odds.py — re-emit historical SGO props into the EXACT
schema the existing production replay pipeline already consumes.

Target collection: `sgo_replay_alt_odds_raw` — same shape as
`mlb_historical_alt_odds_raw` (the live odds-api backfill).  After this
step runs, the existing `run_production_replay()` can be invoked as-is by
pointing the adapter's `odds_collection` at this new collection.

Source:    sgo_pp_research_core_enriched  (one row per offer; books only)
Driver:    league=MLB, snapshot_iso = f"{game_date}T11:00:00Z" (11 UTC
                                    locks the day's openers same as
                                    historical_alt_odds_ingest).

Idempotent compound upsert key:
    (sport, game_date, event_id, player_name_normalized,
     market, line, side, book, snapshot_iso)

Usage (via Admin API job runner):
    --league=MLB --start=2025-06-01 --end=2025-06-30
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path); break

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne, ASCENDING
from pymongo.errors import OperationFailure, BulkWriteError

SRC  = "sgo_pp_research_core_enriched"
DEST = "sgo_replay_alt_odds_raw"
SNAPSHOT_HOUR_UTC = 11


def _normalize_player_name(name: Optional[str]) -> str:
    if not name: return ""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    for ch in (",", ".", "'", "`", '"'):
        s = s.replace(ch, "")
    s = " ".join(s.split())
    return s


_STAT_FAMILY_TO_MARKET = {
    "hits":                "batter_hits",
    "total_bases":         "batter_total_bases",
    "hits_runs_rbis":      "batter_hits_runs_rbis",
    "rbis":                "batter_rbis",
    "runs":                "batter_runs_scored",
    "home_runs":           "batter_home_runs",
    "singles":             "batter_singles",
    "doubles":             "batter_doubles",
    "batter_strikeouts":   "batter_strikeouts",
    "batting_strikeouts":  "batter_strikeouts",
    "batting_walks":       "batter_walks",
    "pitcher_strikeouts":  "pitcher_strikeouts",
    "earned_runs":         "pitcher_earned_runs",
    "hits_allowed":        "pitcher_hits_allowed",
    "pitcher_hits_allowed": "pitcher_hits_allowed",
    "walks_allowed":       "pitcher_walks",
    "pitching_basesOnBalls": "pitcher_walks",
    "pitching_outs":       "pitcher_outs",
    "stolen_bases":        "batter_stolen_bases",
}


def _safe_int_odds(raw: Any) -> Optional[int]:
    """Coerce odds to int; return None if not coercible (NaN, strings, etc).
    Caller decides whether to skip the row or use a default."""
    if raw is None: return None
    try:
        f = float(raw)
        if f != f:  # NaN check
            return None
        return int(f)
    except (TypeError, ValueError):
        return None


def _safe_float_line(raw: Any) -> Optional[float]:
    if raw is None: return None
    try:
        f = float(raw)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


async def _ensure_indexes(db) -> None:
    """Idempotent index creation. If a legacy unique index on the same keys
    already exists under a different name, log and continue — the existing
    one is good enough. If a NAMED index exists with conflicting options,
    drop and recreate."""
    desired_keys = [
        ("sport", ASCENDING),
        ("game_date", ASCENDING),
        ("event_id", ASCENDING),
        ("player_name_normalized", ASCENDING),
        ("market", ASCENDING), ("line", ASCENDING),
        ("side", ASCENDING), ("book", ASCENDING),
        ("snapshot_iso", ASCENDING),
    ]
    try:
        await db[DEST].create_index(
            desired_keys,
            name="alt_odds_compound_unique_v2", unique=True,
            background=True,
        )
    except OperationFailure as e:
        # codes: 85 IndexOptionsConflict, 86 IndexKeySpecsConflict, 11000 dup key
        print(f"  ! create_index failed (code={getattr(e, 'code', '?')}): "
                  f"{e!r}", flush=True)
        # Inspect existing indexes; if any covers our key set as unique,
        # we're done. Otherwise rethrow.
        existing = await db[DEST].index_information()
        for name, info in existing.items():
            keys = info.get("key") or []
            if list(keys) == desired_keys and info.get("unique"):
                print(f"  ✓ existing unique index '{name}' covers the same "
                          f"key set — continuing.", flush=True)
                return
        # Last-resort: try non-unique (still good enough for the replay
        # pipeline; upserts make duplicates impossible by construction).
        print("  ! falling back to non-unique compound index "
                  "(upsert key keeps rows deduped logically).", flush=True)
        try:
            await db[DEST].create_index(
                desired_keys,
                name="alt_odds_compound_v2_nonunique",
                background=True,
            )
        except OperationFailure as e2:
            print(f"  ! fallback index also failed: {e2!r}", flush=True)
    # Secondary indexes — these are best-effort; never fatal.
    for k in ("game_date", "event_id", "snapshot_iso"):
        try:
            await db[DEST].create_index(k, background=True)
        except OperationFailure as e:
            print(f"  ! secondary index '{k}' skipped: {e!r}", flush=True)


async def _run(args: argparse.Namespace) -> int:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    await _ensure_indexes(db)

    match: Dict[str, Any] = {
        "league_id": args.league,
        "game_date": {"$gte": args.start, "$lte": args.end},
    }

    print("=" * 72)
    print(f"  RESHAPE SGO → REPLAY ODDS  ({SRC} → {DEST})")
    print(f"  window: {args.start}..{args.end}   league: {args.league}")
    print(f"  snapshot_hour_utc = {SNAPSHOT_HOUR_UTC:02d}")
    print("=" * 72)

    n_seen = n_skip = n_upsert = 0
    n_no_market = n_bad_odds = n_bad_line = n_bulk_err = 0
    buf: List[UpdateOne] = []
    now = datetime.now(timezone.utc)

    async def _flush_buf():
        nonlocal n_upsert, n_bulk_err
        if not buf: return
        try:
            r = await db[DEST].bulk_write(buf, ordered=False)
            n_upsert += (r.upserted_count + r.modified_count)
        except BulkWriteError as bwe:
            # Some rows duplicated against the unique index — still credit
            # the ones that landed and surface a count.
            wr = bwe.details or {}
            n_upsert += int(wr.get("nUpserted", 0)) + int(wr.get("nModified", 0))
            n_bulk_err += len(wr.get("writeErrors") or [])
            # Surface the FIRST error so prod failures are diagnosable.
            errs = wr.get("writeErrors") or []
            if errs:
                first = errs[0]
                print(f"  ! bulk_write partial failure: code={first.get('code')} "
                          f"msg={first.get('errmsg','')[:200]}", flush=True)

    # SGO enriched usually has ONE row per (event, player, stat, side, line)
    # carrying the best-book-derived edge fields. We don't have per-book
    # quotes in this collection — so we materialize one row per OFFER with
    # `book = best_book_id` (or "sgo_consensus" if absent). Downstream
    # consumers compare books-only edge from enriched; the replay adapter
    # only needs ONE row per (sport, game_date, event, player, market,
    # line, side, book) to drive scoring.
    cur = db[SRC].find(match, projection={"_id": 0})
    if args.limit:
        cur = cur.limit(int(args.limit))

    async for d in cur:
        n_seen += 1
        try:
            sf = d.get("stat_family")
            market = _STAT_FAMILY_TO_MARKET.get(sf) or d.get("market") or None
            if not market:
                n_no_market += 1; continue
            line = _safe_float_line(d.get("line"))
            if line is None:
                n_bad_line += 1; continue
            side = (d.get("side") or "").upper()
            if side not in ("OVER", "UNDER"):
                n_skip += 1; continue
            odds = _safe_int_odds(d.get("best_book_odds"))
            if odds is None:
                odds = _safe_int_odds(d.get("odds"))
            if odds is None:
                odds = -110
                n_bad_odds += 1
            book = d.get("best_book_id") or d.get("book") or "sgo_consensus"
            snapshot_iso = f"{d.get('game_date')}T{SNAPSHOT_HOUR_UTC:02d}:00:00Z"
            commence_time = (d.get("commence_time")
                                or f"{d.get('game_date')}T22:00:00Z")
            pname = d.get("player_name") or ""
            if not pname:
                n_skip += 1; continue

            row = {
                "sport": "mlb",
                "sport_key": "baseball_mlb",
                "game_date": d.get("game_date"),
                "event_id": d.get("event_id"),
                "home_team": d.get("home_team"),
                "away_team": d.get("away_team"),
                "commence_time": commence_time,
                "market": market,
                "stat": market,
                "is_alternate": bool(d.get("is_alternate")),
                "player_name": pname,
                "player_name_normalized": _normalize_player_name(pname),
                "line": line,
                "side": side,
                "book": book,
                "odds": odds,
                "book_last_update": now.isoformat(),
                "snapshot_iso": snapshot_iso,
                "ingested_at": now,
                "_source": "sgo_pp_research_core_enriched",
                "_sgo_consensus_probability":
                    d.get("consensus_probability"),
                "_sgo_best_book_probability":
                    d.get("best_book_probability"),
            }

            flt = {k: row[k] for k in
                    ("sport", "game_date", "event_id", "player_name_normalized",
                     "market", "line", "side", "book", "snapshot_iso")}
            buf.append(UpdateOne(flt, {"$set": row}, upsert=True))
        except Exception as row_exc:
            n_skip += 1
            if n_skip <= 5:   # Don't flood logs — first 5 are enough to diagnose.
                print(f"  ! row #{n_seen} skipped: {row_exc!r}", flush=True)
            continue

        if len(buf) >= 1000:
            await _flush_buf()
            buf = []
            if n_upsert and n_upsert % 5000 == 0:
                print(f"  progress: scanned={n_seen} upserted~{n_upsert}", flush=True)

    if buf:
        await _flush_buf()

    print()
    print(f"  scanned        {n_seen}")
    print(f"  upserted ~     {n_upsert}")
    print(f"  no_market      {n_no_market}")
    print(f"  bad_line       {n_bad_line}")
    print(f"  bad_odds       {n_bad_odds}")
    print(f"  skipped        {n_skip}")
    print(f"  bulk_errs      {n_bulk_err}")
    print(f"  → {DEST}")
    return 0


def _parse():
    p = argparse.ArgumentParser()
    p.add_argument("--league", default="MLB")
    p.add_argument("--start",  required=True)
    p.add_argument("--end",    required=True)
    p.add_argument("--limit",  type=int, default=None)
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(_run(_parse())))
