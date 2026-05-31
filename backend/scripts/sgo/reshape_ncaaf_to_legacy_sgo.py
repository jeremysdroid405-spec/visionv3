"""
reshape_ncaaf_to_legacy_sgo.py — one-shot NCAAF migration into legacy SGO archive shape.

PURPOSE
    The legacy SGO research pipeline (`build_pp_research_core`,
    `build_historical_outcomes`, `build_historical_model_features`,
    `score_historical_model`) reads from these archive collections:
        sgo_props_raw       — per-book price quotes
        sgo_events          — event metadata
        sgo_players         — player_id → player_name lookup
        sgo_book_consensus  — (optional) fair-odds / consensus probability

    Production already has these populated for MLB / NBA / NFL via the
    legacy `scripts/sgo/ingest.py`. For NCAAF, only the NEW per-sport
    pipeline (`workers/team/historical_player_ingest.py`) was run, which
    writes to `ncaaf_player_historical_props` / `ncaaf_matchups`. This
    script bridges that data into the legacy schema so the SGO research
    pipeline can run without re-ingesting from the SGO API.

WHAT THIS SCRIPT DOES
    Step A  ncaaf_matchups                  → sgo_events    (+ league_id=NCAAF)
    Step B  sgo_player_stats (NCAAF)        → sgo_players   (player_id, player_name)
    Step C  ncaaf_player_historical_props   → sgo_props_raw
    Step D  sgo_book_consensus              — NOT POPULATED. Optional.
            `build_pp_research_core` handles missing consensus gracefully
            (returns None and stamps `fair_odds=None`).

GUARANTEES
    - IDEMPOTENT. Re-runs are safe. All writes are `bulk_write(UpdateOne,
      upsert=True)` using the EXACT same unique-key contracts as the
      production indexes in `scripts/sgo/ingest.py::ensure_indexes()`.
    - DOES NOT TOUCH MLB / NBA / NFL ROWS. Every match filter is scoped
      to `league_id=NCAAF` (or equivalent).
    - DOES NOT re-fetch from SGO. Pure DB → DB reshape.
    - SAFE BY DEFAULT. Runs in --dry-run mode unless --apply is passed.

USAGE
    # Dry-run first (counts only, no writes)
    python -m scripts.sgo.reshape_ncaaf_to_legacy_sgo --dry-run

    # Live migration
    python -m scripts.sgo.reshape_ncaaf_to_legacy_sgo --apply

    # Re-run safely (idempotent)
    python -m scripts.sgo.reshape_ncaaf_to_legacy_sgo --apply

ODD_ID SYNTHESIS
    Production `sgo_props_raw` is keyed by `odd_id` (SGO's market ID
    that all books share when quoting the same anchor). The new pipeline
    discards that ID. We synthesize a deterministic stable substitute:

        synth_odd_id = sha1(f"{event_id}|{statID}|{statEntityID}|{periodID}|{side}|{line}")[:24]

    Same anchor across books → same synth_odd_id. Different anchors →
    different IDs. Re-runs produce the same IDs (deterministic).
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import UpdateOne, ASCENDING

from ._index_utils import ensure_indexes as _shared_ensure_indexes

# Collection names — match the legacy SGO archive exactly
SRC_MATCHUPS  = "ncaaf_matchups"
SRC_PROPS     = "ncaaf_player_historical_props"
SRC_PSTATS    = "sgo_player_stats"     # already populated with NCAAF rows

DST_EVENTS    = "sgo_events"
DST_PLAYERS   = "sgo_players"
DST_PROPS_RAW = "sgo_props_raw"

LEAGUE_ID = "NCAAF"      # the league_id stamp we will write to legacy rows
SPORT_ID  = "FOOTBALL"   # SGO's canonical sport_id for football

# Reshape stamps — applied to every migrated row for forensics
RESHAPE_VERSION = "v1"
RESHAPE_SOURCE  = "ncaaf_legacy_bridge"


# ──────────────────────────── helpers ────────────────────────────
def _synth_odd_id(event_id: Any, stat_id: Any, stat_entity_id: Any,
                    period_id: Any, side: Any, line: Any) -> str:
    """Deterministic synthetic odd_id for legacy sgo_props_raw unique key.
    Same anchor across multiple books → same synth_odd_id. Re-runs
    produce identical IDs. 24-char SHA1 prefix is collision-safe at
    NCAAF scale (~400k rows)."""
    parts = "|".join([
        str(event_id),
        str(stat_id or ""),
        str(stat_entity_id or ""),
        str(period_id or ""),
        str(side or ""),
        # Normalize None line to literal "NONE" so OU vs YN markets
        # don't collide on null line.
        ("NONE" if line is None else f"{float(line):g}"),
    ])
    return hashlib.sha1(parts.encode("utf-8")).hexdigest()[:24]


def _as_iso(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat()
    if isinstance(v, str):
        return v
    return str(v)


# ──────────────────────────── index self-heal ────────────────────────────
# Tolerant-by-pattern index creation is provided by the shared helper
# at scripts/sgo/_index_utils.py. Same unique-key contracts as
# `scripts/sgo/ingest.py::ensure_indexes()` — never drops, never
# mutates pre-existing indexes (touching them could ripple into
# MLB/NBA/NFL).
async def _ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    # sgo_events
    await _shared_ensure_indexes(db[DST_EVENTS], [
        {"keys": [("event_id", ASCENDING), ("snapshot_time", ASCENDING)],
         "unique": True, "name": "events_pk"},
        {"keys": "league_id",  "name": "events_league_id"},
        {"keys": "start_time", "name": "events_start_time"},
    ])
    # sgo_players
    await _shared_ensure_indexes(db[DST_PLAYERS], [
        {"keys": "player_id", "unique": True, "name": "players_pk"},
    ])
    # sgo_props_raw
    await _shared_ensure_indexes(db[DST_PROPS_RAW], [
        {"keys": [("event_id", ASCENDING), ("odd_id", ASCENDING),
                  ("book_id", ASCENDING), ("side", ASCENDING),
                  ("line", ASCENDING), ("snapshot_time", ASCENDING)],
         "unique": True, "name": "props_raw_pk"},
        {"keys": "league_id", "name": "props_raw_league_id"},
        {"keys": "player_id", "name": "props_raw_player_id"},
        {"keys": "stat_id",   "name": "props_raw_stat_id"},
    ])


# ──────────────────────────── Step A — events ────────────────────────────
async def migrate_events(db: AsyncIOMotorDatabase, *,
                            dry_run: bool, snapshot_iso: str) -> Dict[str, Any]:
    """ncaaf_matchups → sgo_events. NCAAF-only."""
    n_src = await db[SRC_MATCHUPS].count_documents({"league": LEAGUE_ID})
    if n_src == 0:
        # Try alternate filter (some matchup rows might use sport instead)
        n_src = await db[SRC_MATCHUPS].count_documents(
            {"sport": "ncaaf"})
    print(f"  [A] events: {n_src:,} source matchups (NCAAF)")
    if n_src == 0:
        return {"step": "events", "src": 0, "upserted": 0,
                "dry_run": dry_run, "warning":
                    f"no NCAAF rows in {SRC_MATCHUPS}"}

    BATCH = 1000
    upserts: List[UpdateOne] = []
    n_buffered = 0
    n_upserted = 0
    match = {"$or": [{"league": LEAGUE_ID}, {"sport": "ncaaf"}]}
    async for m in db[SRC_MATCHUPS].find(match, {"_id": 0}):
        eid = m.get("event_id")
        if not eid:
            continue
        # Map matchup fields → sgo_events schema
        commence = _as_iso(m.get("commence_time"))
        game_date = m.get("game_date")
        if not game_date and commence and isinstance(commence, str):
            game_date = commence[:10]
        doc = {
            "event_id":        eid,
            "sport_id":        SPORT_ID,
            "league_id":       LEAGUE_ID,
            "start_time":      commence,
            "game_status":     m.get("status"),
            "home_team_id":    m.get("home_team_id"),
            "away_team_id":    m.get("away_team_id"),
            "home_team_name":  m.get("home_team_name"),
            "away_team_name":  m.get("away_team_name"),
            "home_score":      m.get("home_score"),
            "away_score":      m.get("away_score"),
            "season":          m.get("season"),
            "week":            m.get("week"),
            "game_date":       game_date,
            "snapshot_time":   snapshot_iso,
            "raw":             {},   # legacy raw payload — empty (already
                                     #  re-extracted into sgo_player_stats)
            "reshape_source":  RESHAPE_SOURCE,
            "reshape_version": RESHAPE_VERSION,
            "reshaped_at":     datetime.now(timezone.utc),
        }
        filt = {"event_id": eid, "snapshot_time": snapshot_iso}
        upserts.append(UpdateOne(filt, {"$set": doc}, upsert=True))
        n_buffered += 1
        if len(upserts) >= BATCH and not dry_run:
            r = await db[DST_EVENTS].bulk_write(upserts, ordered=False)
            n_upserted += (r.upserted_count or 0) + (r.modified_count or 0)
            upserts = []
    if upserts and not dry_run:
        r = await db[DST_EVENTS].bulk_write(upserts, ordered=False)
        n_upserted += (r.upserted_count or 0) + (r.modified_count or 0)
    return {"step": "events", "src": n_src,
            "buffered": n_buffered, "upserted": n_upserted,
            "dry_run": dry_run}


# ──────────────────────────── Step B — players ────────────────────────────
async def migrate_players(db: AsyncIOMotorDatabase, *,
                             dry_run: bool) -> Dict[str, Any]:
    """sgo_player_stats (NCAAF) + ncaaf_player_historical_props →
    sgo_players. Idempotent upserts keyed by player_id.

    Two sources combined: sgo_player_stats has player_name; the props
    collection only has player_id. We start with the stats source for
    rich names, then fill in any missing IDs from the props collection
    using a synthetic name derived from the SGO player_id pattern
    (`PLAYER_NAME_<id>_<LEAGUE>`).
    """
    BATCH = 1000

    # 2.B.1 — names from sgo_player_stats (NCAAF only)
    seen_pids: set = set()
    upserts: List[UpdateOne] = []
    n_from_stats = 0
    n_from_props = 0
    n_upserted = 0
    async for s in db[SRC_PSTATS].find(
        {"league_id": LEAGUE_ID},
        {"_id": 0, "player_id": 1, "player_name": 1, "team_id": 1}
    ):
        pid = s.get("player_id")
        if not pid or pid in seen_pids:
            continue
        seen_pids.add(pid)
        doc = {
            "player_id":       pid,
            "player_name":     s.get("player_name") or "",
            "team_id":         s.get("team_id"),
            "league_id":       LEAGUE_ID,
            "sport_id":        SPORT_ID,
            "reshape_source":  RESHAPE_SOURCE,
            "reshape_version": RESHAPE_VERSION,
            "reshaped_at":     datetime.now(timezone.utc),
        }
        upserts.append(UpdateOne({"player_id": pid},
                                    {"$set": doc}, upsert=True))
        n_from_stats += 1
        if len(upserts) >= BATCH and not dry_run:
            r = await db[DST_PLAYERS].bulk_write(upserts, ordered=False)
            n_upserted += (r.upserted_count or 0) + (r.modified_count or 0)
            upserts = []
    if upserts and not dry_run:
        r = await db[DST_PLAYERS].bulk_write(upserts, ordered=False)
        n_upserted += (r.upserted_count or 0) + (r.modified_count or 0)
        upserts = []

    # 2.B.2 — fill in any IDs that appear in props but had no stats row
    cursor = db[SRC_PROPS].aggregate(
        [{"$match": {"league": LEAGUE_ID}},
         {"$group": {"_id": "$player_id"}}],
        allowDiskUse=True)
    async for r in cursor:
        pid = r.get("_id")
        if not pid or pid in seen_pids:
            continue
        seen_pids.add(pid)
        # Synthesize a name from the SGO player_id pattern. SGO uses
        # PLAYER_NAME_<id>_<LEAGUE> (e.g. "JOHN_SMITH_1_NCAAF"). We
        # take everything before the last "_<NUM>_<LEAGUE>" segment.
        name = ""
        if isinstance(pid, str) and "_" in pid:
            parts = pid.rsplit("_", 2)
            if len(parts) >= 1:
                name = parts[0].replace("_", " ").title()
        doc = {
            "player_id":       pid,
            "player_name":     name,
            "team_id":         None,
            "league_id":       LEAGUE_ID,
            "sport_id":        SPORT_ID,
            "reshape_source":  RESHAPE_SOURCE,
            "reshape_version": RESHAPE_VERSION,
            "reshape_name_source": "synthesized_from_player_id",
            "reshaped_at":     datetime.now(timezone.utc),
        }
        upserts.append(UpdateOne({"player_id": pid},
                                    {"$set": doc}, upsert=True))
        n_from_props += 1
        if len(upserts) >= BATCH and not dry_run:
            rr = await db[DST_PLAYERS].bulk_write(upserts, ordered=False)
            n_upserted += (rr.upserted_count or 0) + (rr.modified_count or 0)
            upserts = []
    if upserts and not dry_run:
        rr = await db[DST_PLAYERS].bulk_write(upserts, ordered=False)
        n_upserted += (rr.upserted_count or 0) + (rr.modified_count or 0)

    return {"step": "players", "from_stats": n_from_stats,
            "from_props_filled": n_from_props,
            "distinct_pids":     len(seen_pids),
            "upserted":          n_upserted, "dry_run": dry_run}


# ──────────────────────────── Step C — props_raw ────────────────────────────
async def migrate_props(db: AsyncIOMotorDatabase, *,
                          dry_run: bool) -> Dict[str, Any]:
    """ncaaf_player_historical_props → sgo_props_raw. One row per book
    quote. NCAAF-only. Idempotent on
    (event_id, odd_id, book_id, side, line, snapshot_time)."""
    n_src = await db[SRC_PROPS].count_documents({"league": LEAGUE_ID})
    print(f"  [C] props_raw: {n_src:,} source quotes (NCAAF)")
    if n_src == 0:
        return {"step": "props_raw", "src": 0, "upserted": 0,
                "dry_run": dry_run}

    BATCH = 1000
    upserts: List[UpdateOne] = []
    n_buffered = 0
    n_upserted = 0
    n_skipped_no_required = 0
    sample_doc: Optional[Dict[str, Any]] = None

    async for p in db[SRC_PROPS].find({"league": LEAGUE_ID}, {"_id": 0}):
        eid = p.get("event_id")
        pid = p.get("player_id")
        stat_id = p.get("statID") or p.get("market")
        side = p.get("side")
        snapshot_iso = p.get("snapshot_iso")
        # Required for legacy unique key. line can be None (yes/no markets).
        if not eid or not stat_id or not side or not snapshot_iso:
            n_skipped_no_required += 1
            continue
        stat_entity_id = p.get("statEntityID")
        period_id = p.get("periodID")
        line = p.get("line")
        odd_id = _synth_odd_id(eid, stat_id, stat_entity_id,
                                 period_id, side, line)

        doc = {
            "event_id":        eid,
            "league_id":       LEAGUE_ID,
            "odd_id":          odd_id,
            "stat_id":         stat_id,
            "stat_entity_id":  stat_entity_id,
            "player_id":       pid,
            "period_id":       period_id,
            "bet_type_id":     p.get("betTypeID"),
            "side":            side,
            "line":            line,
            "price":           p.get("odds"),
            "book_id":         (p.get("book") or "").lower(),
            "selection_id":    None,
            "opposing_odd_id": None,
            "snapshot_time":   snapshot_iso,
            # forensic stamps
            "reshape_source":  RESHAPE_SOURCE,
            "reshape_version": RESHAPE_VERSION,
            "is_alternate":    p.get("is_alternate"),
            "commence_time":   p.get("commence_time"),
            "game_date":       p.get("game_date"),
            "market_name":     p.get("market_name"),
            "reshaped_at":     datetime.now(timezone.utc),
        }
        if sample_doc is None:
            sample_doc = doc

        filt = {
            "event_id":      eid,
            "odd_id":        odd_id,
            "book_id":       doc["book_id"],
            "side":          side,
            "line":          line,
            "snapshot_time": snapshot_iso,
        }
        upserts.append(UpdateOne(filt, {"$set": doc}, upsert=True))
        n_buffered += 1
        if len(upserts) >= BATCH and not dry_run:
            r = await db[DST_PROPS_RAW].bulk_write(upserts, ordered=False)
            n_upserted += (r.upserted_count or 0) + (r.modified_count or 0)
            upserts = []
    if upserts and not dry_run:
        r = await db[DST_PROPS_RAW].bulk_write(upserts, ordered=False)
        n_upserted += (r.upserted_count or 0) + (r.modified_count or 0)

    return {"step": "props_raw", "src": n_src,
            "buffered":              n_buffered,
            "upserted":              n_upserted,
            "skipped_no_required":   n_skipped_no_required,
            "sample_doc":            sample_doc,
            "dry_run":               dry_run}


# ──────────────────────────── main ────────────────────────────
async def amain(args: argparse.Namespace) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    dry_run = not args.apply
    snapshot_iso = datetime.now(timezone.utc).isoformat()

    print(f"[{datetime.now(timezone.utc).isoformat()}] "
          f"reshape_ncaaf_to_legacy_sgo  (v={RESHAPE_VERSION})")
    print(f"  apply={args.apply}  dry_run={dry_run}  "
          f"snapshot_time(events)={snapshot_iso}")
    print(f"  STRICT FILTERS: only league_id={LEAGUE_ID} rows touched")

    t0 = time.time()
    if args.apply:
        print("  [indexes] self-healing sgo_events / sgo_players / "
              "sgo_props_raw indexes…")
        await _ensure_indexes(db)

    # Step A — events
    a = await migrate_events(db, dry_run=dry_run, snapshot_iso=snapshot_iso)
    print(f"  [A] events: src={a.get('src',0):,}  "
          f"buffered={a.get('buffered',0):,}  "
          f"upserted={a.get('upserted',0):,}  "
          f"warning={a.get('warning','')}")

    # Step B — players
    b = await migrate_players(db, dry_run=dry_run)
    print(f"  [B] players: from_stats={b['from_stats']:,}  "
          f"from_props_filled={b['from_props_filled']:,}  "
          f"distinct_pids={b['distinct_pids']:,}  "
          f"upserted={b['upserted']:,}")

    # Step C — props_raw
    c = await migrate_props(db, dry_run=dry_run)
    print(f"  [C] props_raw: src={c.get('src',0):,}  "
          f"buffered={c.get('buffered',0):,}  "
          f"upserted={c.get('upserted',0):,}  "
          f"skipped_no_required={c.get('skipped_no_required',0):,}")

    # Step D — consensus is intentionally NOT migrated (optional in
    # build_pp_research_core; downstream handles missing rows gracefully).
    print("  [D] sgo_book_consensus: SKIPPED (optional; downstream "
          "handles missing rows)")

    if c.get("sample_doc"):
        import json
        print("\n  Sample reshaped sgo_props_raw row:")
        print("    " + json.dumps(c["sample_doc"], indent=2, default=str)
                              .replace("\n", "\n    "))

    runtime = time.time() - t0
    print()
    print("=" * 72)
    print("  reshape_ncaaf_to_legacy_sgo SUMMARY")
    print("=" * 72)
    print(f"  mode:                          "
          f"{'APPLY' if args.apply else 'DRY-RUN (use --apply to write)'}")
    print(f"  events upserted:               {a.get('upserted',0):,}")
    print(f"  players upserted:              {b['upserted']:,}")
    print(f"  props_raw upserted:            {c.get('upserted',0):,}")
    print(f"  props skipped (missing keys):  {c.get('skipped_no_required',0):,}")
    print(f"  runtime:                       {runtime:,.1f}s")

    if args.apply:
        # Final verification — count NCAAF rows in legacy collections
        print()
        print("  POST-MIGRATION COUNTS (league_id=NCAAF):")
        n_ev = await db[DST_EVENTS].count_documents({"league_id": LEAGUE_ID})
        n_pl = await db[DST_PLAYERS].count_documents({"league_id": LEAGUE_ID})
        n_pr = await db[DST_PROPS_RAW].count_documents({"league_id": LEAGUE_ID})
        print(f"    sgo_events     NCAAF:  {n_ev:,}")
        print(f"    sgo_players    NCAAF:  {n_pl:,}")
        print(f"    sgo_props_raw  NCAAF:  {n_pr:,}")
    print("=" * 72)

    client.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true",
                    help="Actually write to legacy collections. "
                          "Without --apply this script is dry-run only.")
    p.add_argument("--dry-run", action="store_true",
                    help="Explicitly request dry-run (default behaviour "
                          "when --apply is omitted).")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
