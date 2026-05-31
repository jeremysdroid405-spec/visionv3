"""
ingest_player_master.py — pull SGO `/v2/players` and persist as a true
identity master.

PURPOSE
    The NCAAF identity audit proved that `client.get_players()` exists
    but is never called. As a result, the only identity data we have
    is a side-effect-derived shadow inside `sgo_players` (built from
    `playerStats[]` walks). For NCAAF, ~1,067 prop player_ids never
    appear in stats and therefore never get an identity row, blocking
    every downstream join.

    This script fills that gap. One file, multi-league, idempotent,
    side-effect free for other leagues. NEVER touches outcomes /
    props / stats collections.

CONTRACT
    Target collection:  sgo_player_master (NEW)
    Unique key:         (player_id)
    Per row, preserve:
      • player_id            — SGO canonical playerID
      • player_name          — canonical display name
      • first_name           — split
      • last_name            — split
      • names[]              — all official names SGO ships
      • aliases[]            — alternate spellings/nicknames/IDs SGO ships
      • team_id              — current/most-recent SGO teamID
      • team_history[]       — list of {team_id, season, …} if shipped
      • position             — e.g. "QB", "RB1"
      • jersey_number        — string (some leagues ship "00")
      • height               — string as SGO ships it
      • weight               — string / int
      • status               — "active" / "inactive" / "retired" / etc.
      • birth_date           — if shipped
      • league_id            — duplicated for fast scoped queries
      • sport_id             — same
      • raw                  — full SGO payload for forensics
      • ingested_at          — datetime utc
      • ingest_version       — string ('v1' until schema changes)

USAGE
    # NCAAF only (the first run for the user)
    python -m scripts.sgo.ingest_player_master --league NCAAF

    # Multi-league pass (one league per invocation; explicit & auditable)
    python -m scripts.sgo.ingest_player_master --league NFL
    python -m scripts.sgo.ingest_player_master --league MLB
    python -m scripts.sgo.ingest_player_master --league NBA

    # Dry-run (no DB writes, prints counts + sample doc)
    python -m scripts.sgo.ingest_player_master --league NCAAF --dry-run

CONSTRAINTS
    • IDEMPOTENT upserts only. Re-runs produce zero net new rows.
    • Per-league scoped writes — invoking `--league NCAAF` cannot affect
      MLB/NBA/NFL rows. Match filter & writes are gated by league_id.
    • NEVER touches: ncaaf_player_historical_props, sgo_player_stats,
      sgo_ncaaf_research_outcomes, or any other production collection.
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import UpdateOne, ASCENDING

from scripts.sgo._index_utils import ensure_indexes as _shared_ensure_indexes
from scripts.sgo.client import SGOClient


DEST_COLL = "sgo_player_master"
INGEST_VERSION = "v1"

# Map our league name to SGO's expected (sportID, leagueID) tuple.
# SGO uses "FOOTBALL" for both NFL & NCAAF — only leagueID differs.
LEAGUE_TO_SGO: Dict[str, Tuple[str, str]] = {
    "MLB":   ("BASEBALL",   "MLB"),
    "NBA":   ("BASKETBALL", "NBA"),
    "NFL":   ("FOOTBALL",   "NFL"),
    "NCAAF": ("FOOTBALL",   "NCAAF"),
}


# ─────────────────────────── pure helpers ───────────────────────────
def _get(d: Dict[str, Any], *keys: str) -> Any:
    """First non-None value for any of the candidate keys."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _as_list(v: Any) -> List[Any]:
    """Coerce SGO's polymorphic name fields into a clean list."""
    if v is None:
        return []
    if isinstance(v, list):
        return [x for x in v if x not in (None, "")]
    if isinstance(v, str) and v:
        return [v]
    return []


def normalize_player_doc(raw: Dict[str, Any], *,
                          league_id: str, sport_id: str,
                          now: Optional[datetime] = None
                          ) -> Optional[Dict[str, Any]]:
    """Turn a raw SGO player document into our `sgo_player_master`
    row shape. Returns None if the doc lacks a usable player_id.

    Pure function — no I/O. Easy to unit-test.
    """
    pid = _get(raw, "playerID", "player_id", "id", "_id")
    if not pid:
        return None
    pid = str(pid)
    names_raw   = _get(raw, "names", "name_aliases", "displayNames")
    aliases_raw = _get(raw, "aliases", "alternateNames",
                        "alternate_names", "alias")
    names   = _as_list(names_raw)
    aliases = _as_list(aliases_raw)
    # Coerce dict-shaped names {first, last} into the list too
    first = _get(raw, "firstName", "first_name")
    last  = _get(raw, "lastName",  "last_name")
    display = _get(raw, "playerName", "displayName", "name",
                    "full_name", "fullName")
    if display and display not in names:
        names = [display, *names]
    canonical_name = display or (
        f"{first} {last}".strip() if first or last else
        (names[0] if names else ""))

    team_id     = _get(raw, "teamID", "team_id")
    team_hist   = _get(raw, "teamHistory", "team_history") or []
    position    = _get(raw, "position", "primaryPosition",
                        "primary_position")
    jersey      = _get(raw, "jerseyNumber", "jersey_number", "jersey",
                        "number")
    height      = _get(raw, "height")
    weight      = _get(raw, "weight")
    status      = _get(raw, "status", "playerStatus", "rosterStatus")
    birth_date  = _get(raw, "birthDate", "birth_date", "dob")

    return {
        "player_id":      pid,
        "player_name":    canonical_name,
        "first_name":     first,
        "last_name":      last,
        "names":          names,
        "aliases":        aliases,
        "team_id":        team_id,
        "team_history":   team_hist if isinstance(team_hist, list) else [],
        "position":       position,
        "jersey_number":  str(jersey) if jersey is not None else None,
        "height":         height,
        "weight":         weight,
        "status":         status,
        "birth_date":     birth_date,
        "league_id":      league_id,
        "sport_id":       sport_id,
        "raw":            raw,
        "ingested_at":    (now or datetime.now(timezone.utc)),
        "ingest_version": INGEST_VERSION,
    }


# ─────────────────────────── indexes ───────────────────────────
async def _ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """Idempotent indexes via the shared tolerant helper."""
    await _shared_ensure_indexes(db[DEST_COLL], [
        {"keys": "player_id", "unique": True, "name": "player_master_pk"},
        {"keys": "league_id", "name": "league_id_1"},
        {"keys": "team_id",   "name": "team_id_1"},
        # Multi-key index — Mongo indexes each entry of the `aliases`
        # array, enabling fast `{aliases: <pid_or_name>}` lookups for
        # downstream reconciliation. Sparse: skip the many docs with
        # an empty aliases[].
        {"keys": "aliases",   "name": "aliases_1", "sparse": True},
        {"keys": "names",     "name": "names_1",   "sparse": True},
    ])


# ─────────────────────────── ingest ───────────────────────────
async def ingest_league(
    client: SGOClient, db: AsyncIOMotorDatabase, *,
    league_id: str, sport_id: str, dry_run: bool, page_size: int,
    max_pages: int,
) -> Dict[str, Any]:
    """Pull all pages of /v2/players for one league. Idempotent upserts."""
    print(f"\n  [{league_id}] starting /v2/players pull "
          f"(sportID={sport_id}, leagueID={league_id})")
    BATCH = 500
    buf: List[UpdateOne] = []
    n_pages = 0
    n_raw = 0
    n_normalized = 0
    n_skipped_no_pid = 0
    n_upserted = 0
    sample_doc: Optional[Dict[str, Any]] = None
    next_cursor: Optional[str] = None
    page = 1
    while page <= max_pages:
        params: Dict[str, Any] = {
            "sportID": sport_id, "leagueID": league_id,
            "limit":   page_size,
        }
        if next_cursor is not None:
            params["cursor"] = next_cursor
        else:
            params["page"] = page
        data = await client.get_players(**params)
        players = (data.get("players") or data.get("data") or
                     data.get("results") or [])
        if not players:
            break
        n_pages += 1
        n_raw += len(players)
        for p in players:
            doc = normalize_player_doc(
                p, league_id=league_id, sport_id=sport_id)
            if doc is None:
                n_skipped_no_pid += 1
                continue
            n_normalized += 1
            if sample_doc is None:
                # Strip raw blob for the screen sample
                sample_doc = {k: v for k, v in doc.items() if k != "raw"}
            if not dry_run:
                buf.append(UpdateOne(
                    {"player_id": doc["player_id"]},
                    {"$set": doc},
                    upsert=True))
                if len(buf) >= BATCH:
                    r = await db[DEST_COLL].bulk_write(buf, ordered=False)
                    n_upserted += (r.upserted_count or 0) + (r.modified_count or 0)
                    buf = []

        # End-of-data heuristic mirrors client.iter_events
        next_cursor = (data.get("nextPage") or data.get("nextCursor")
                       or data.get("next")
                       or (data.get("meta") or {}).get("nextCursor"))
        if next_cursor:
            page += 1
            continue
        if len(players) < page_size:
            break
        page += 1
    if buf and not dry_run:
        r = await db[DEST_COLL].bulk_write(buf, ordered=False)
        n_upserted += (r.upserted_count or 0) + (r.modified_count or 0)

    return {
        "league_id":         league_id,
        "sport_id":          sport_id,
        "pages":             n_pages,
        "raw":               n_raw,
        "normalized":        n_normalized,
        "skipped_no_pid":    n_skipped_no_pid,
        "upserted":          n_upserted,
        "dry_run":           dry_run,
        "sample_doc":        sample_doc,
    }


# ─────────────────────────── main ───────────────────────────
async def amain(args: argparse.Namespace) -> int:
    league = args.league.upper()
    if league not in LEAGUE_TO_SGO:
        print(f"  ERROR: --league must be one of "
              f"{sorted(LEAGUE_TO_SGO)} (got {league})")
        return 2
    sport_id, league_id = LEAGUE_TO_SGO[league]

    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]
    api_key = os.environ.get("SGO_API_KEY")
    if not api_key:
        print("  ERROR: SGO_API_KEY missing from environment.")
        return 2

    sgo = SGOClient(api_key=api_key)
    try:
        t0 = datetime.now(timezone.utc)
        print(f"[{t0.isoformat()}] ingest_player_master  league={league_id}  "
              f"sport={sport_id}  dry_run={args.dry_run}")
        print(f"  Dest: {DEST_COLL}  (idempotent upserts on player_id)")
        print("  CONSTRAINTS: per-league scoped; never touches outcomes / "
              "props / stats / other leagues.")

        if not args.dry_run:
            await _ensure_indexes(db)

        result = await ingest_league(
            sgo, db,
            league_id=league_id, sport_id=sport_id,
            dry_run=args.dry_run,
            page_size=args.page_size,
            max_pages=args.max_pages)

        # Sample
        if result["sample_doc"]:
            import json
            print("\n  Sample normalized doc:")
            print("    " + json.dumps(result["sample_doc"], indent=2,
                                        default=str).replace("\n", "\n    "))

        # Summary
        print()
        print("=" * 72)
        print(f"  ingest_player_master  SUMMARY  ({league_id})")
        print("=" * 72)
        print(f"  pages fetched:           {result['pages']:,}")
        print(f"  raw players returned:    {result['raw']:,}")
        print(f"  normalized rows:         {result['normalized']:,}")
        print(f"  skipped (no player_id):  {result['skipped_no_pid']:,}")
        print(f"  upserted into {DEST_COLL}:")
        print(f"                           {result['upserted']:,}  "
              f"({'DRY-RUN — no writes' if args.dry_run else 'live'})")
        print(f"  SGO API call stats:      {sgo.stats()}")

        if not args.dry_run:
            n_now = await db[DEST_COLL].count_documents(
                {"league_id": league_id})
            print(f"  total {league_id} rows in {DEST_COLL} now: "
                  f"{n_now:,}")

        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        print(f"  runtime: {elapsed:.1f}s")
        print("=" * 72)
    finally:
        await sgo.aclose() if hasattr(sgo, "aclose") else None
        mongo.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--league", required=True,
                    choices=sorted(LEAGUE_TO_SGO.keys()),
                    help="League to pull /v2/players for. One league per "
                          "invocation — explicit and auditable.")
    p.add_argument("--dry-run", action="store_true",
                    help="Fetch + normalize but skip DB writes.")
    p.add_argument("--page-size", type=int, default=500,
                    help="Page size for /v2/players (default 500).")
    p.add_argument("--max-pages", type=int, default=1000,
                    help="Safety cap on pagination (default 1000).")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
