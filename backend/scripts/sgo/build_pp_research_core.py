"""
build_pp_research_core.py — derive sgo_pp_research_core from sgo_props_raw.

Anchors:
    book_id == 'prizepicks'

For every distinct PrizePicks line, attach every matching book quote where
(league_id, event_id, player_id, stat_id, side, line) align — same period_id
too. One output document per anchor key.

Read-only against sgo_* collections.
Writes only to sgo_pp_research_core (new, derived).
Idempotent — bulk_write upserts keyed by the anchor tuple.
OOM-safe: processes ONE month at a time using Mongo aggregation with
allowDiskUse=True. PrizePicks volume is small (~1–3k anchors/day for MLB),
so per-month memory is bounded.

Usage:
    python -m scripts.sgo.build_pp_research_core \\
        --league MLB --start 2025-06-01 --end 2025-06-30
    python -m scripts.sgo.build_pp_research_core --dry-run
    python -m scripts.sgo.build_pp_research_core --drop-existing
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")  # preview fallback
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import UpdateOne, ASCENDING

from ._index_utils import ensure_indexes as _shared_ensure_indexes

OUT_COLL = "sgo_pp_research_core"

# 2026-05-24 — Multi-book market universe (replaces single-PP anchor).
#
# Previously this script hard-filtered `{"books.book_id":"prizepicks"}` which
# silently destroyed entire prop families (HR, SB, doubles, triples) that
# PrizePicks doesn't carry historically. Per the architecture brief, we are
# evolving PropVision from "PP-only optimizer" to "universal sportsbook
# intelligence" — PP is one of N books, not the gatekeeper.
#
# Anchor priority: per-row, the FIRST book in `ANCHOR_PRIORITY` that offers
# a quote becomes the anchor. PP remains first so existing PP-anchored
# rows are byte-identical to before. The other books are added in
# rank-of-coverage order observed in prod (DK > FD > MGM > Caesars > BOL).
# Any other books we ingest are still kept in `books` and `available_books`
# but only become the anchor if NONE of the priority books offer the line.
ANCHOR_BOOK = "prizepicks"   # legacy alias — kept for backward compat
ANCHOR_PRIORITY: List[str] = [
    "prizepicks",
    "draftkings",
    "fanduel",
    "betmgm",
    "caesars",
    "betonlineag",
    "betonline",  # SGO sometimes uses this spelling
]
# Used to populate `playable_on_*` flags so the optimizer UI can filter
# without re-querying.
_PLAYABLE_FLAG_MAP: Dict[str, str] = {
    "prizepicks":   "playable_on_pp",
    "draftkings":   "playable_on_dk",
    "fanduel":      "playable_on_fd",
    "betmgm":       "playable_on_mgm",
    "caesars":      "playable_on_caesars",
    "betonlineag":  "playable_on_bol",
    "betonline":    "playable_on_bol",
}


def _pick_anchor(seen_book: Dict[str, Dict[str, Any]]
                       ) -> tuple[Optional[str], Dict[str, Any], str]:
    """Apply `ANCHOR_PRIORITY` to a per-row dict of books. Returns
    (book_id, anchor_quote, source_tag).

    `source_tag` is one of:
      - `"priority"` — chosen from `ANCHOR_PRIORITY`
      - `"fallback_first_available"` — no priority book had a quote;
        we used whichever book did (sorted alphabetically for
        determinism)
      - `"none"` — no quote at all (caller should skip)
    """
    for bid in ANCHOR_PRIORITY:
        if bid in seen_book:
            return bid, seen_book[bid], "priority"
    if not seen_book:
        return None, {}, "none"
    # Deterministic alphabetical fallback so the same input always
    # yields the same anchor — critical for reproducible backtests.
    fallback_bid = sorted(seen_book.keys())[0]
    return fallback_bid, seen_book[fallback_bid], "fallback_first_available"


def _resolve_out_coll(args: argparse.Namespace) -> str:
    """Allow per-league override of the destination collection. Hybrid
    layout: MLB keeps writing to `sgo_pp_research_core`; NFL writes to
    `sgo_nfl_research_core`; NCAAF writes to `sgo_ncaaf_research_core`.
    Set via `--out-coll` or auto-derived from `--league`."""
    if getattr(args, "out_coll", None):
        return str(args.out_coll)
    league = (getattr(args, "league", None) or "").upper()
    if league == "NFL":
        return "sgo_nfl_research_core"
    if league == "NCAAF":
        return "sgo_ncaaf_research_core"
    return OUT_COLL


# ─── indexes (idempotent; tolerant of pre-existing same-pattern indexes) ───
async def ensure_out_indexes(db: AsyncIOMotorDatabase, out_coll: str = OUT_COLL) -> None:
    await _shared_ensure_indexes(db[out_coll], [
        {"keys": [("event_id", ASCENDING), ("player_id", ASCENDING),
                  ("stat_id", ASCENDING), ("side", ASCENDING),
                  ("line", ASCENDING), ("period_id", ASCENDING)],
         "unique": True, "name": "pp_anchor_pk"},
        {"keys": "league_id", "name": "league_id_1"},
        {"keys": "game_date", "name": "game_date_1"},
        {"keys": "stat_id",   "name": "stat_id_1"},
        {"keys": "player_id", "name": "player_id_1"},
    ])


# ─── month iterator ───────────────────────────────────────────────────────
def _month_chunks(start: str, end: str):
    """Yield (yyyy-mm, first_day, last_day) tuples covering [start, end]."""
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    cur = s.replace(day=1)
    while cur <= e:
        # last day of cur's month
        if cur.month == 12:
            next_month = cur.replace(year=cur.year + 1, month=1, day=1)
        else:
            next_month = cur.replace(month=cur.month + 1, day=1)
        month_last = next_month - timedelta(days=1)
        chunk_start = max(cur, s).isoformat()
        chunk_end   = min(month_last, e).isoformat()
        yield (f"{cur.year:04d}-{cur.month:02d}", chunk_start, chunk_end)
        cur = next_month


# ─── one-month builder ────────────────────────────────────────────────────
async def build_month(
    db: AsyncIOMotorDatabase,
    *, league: Optional[str], month: str,
    start_iso: str, end_iso: str, dry_run: bool,
    player_name: Dict[str, str],
    out_coll: str = OUT_COLL,
) -> Dict[str, Any]:
    """Build/upsert anchors for one month. Returns telemetry."""
    # 1) eligible events for this month/league
    match_event: Dict[str, Any] = {"game_date": {"$gte": start_iso,
                                                   "$lte": end_iso}}
    if league:
        match_event["league_id"] = league
    event_meta: Dict[str, Dict[str, Any]] = {}
    async for ev in db.sgo_events.find(
        match_event,
        projection={"_id": 0, "event_id": 1, "league_id": 1,
                    "sport_id": 1, "game_date": 1}):
        if ev.get("event_id"):
            event_meta[ev["event_id"]] = ev
    eligible_events = list(event_meta)
    if not eligible_events:
        return {"month": month, "events": 0, "anchors": 0,
                "books_attached": 0, "with_consensus": 0,
                "upserts": 0, "skipped_dry_run": dry_run}

    # 2) Group props by (event, player, stat, side, line, period). Each
    # group must contain a PrizePicks row to qualify.
    pipeline = [
        {"$match": {"event_id": {"$in": eligible_events}}},
        # Keep only the latest snapshot per book/side/line for cleanliness
        {"$sort": {"snapshot_time": -1}},
        {"$group": {
            "_id": {
                "event_id": "$event_id",
                "player_id": "$player_id",
                "stat_id":   "$stat_id",
                "side":      "$side",
                "line":      "$line",
                "period_id": "$period_id",
            },
            "books": {"$push": {
                "book_id":       "$book_id",
                "price":         "$price",
                "snapshot_time": "$snapshot_time",
                "odd_id":        "$odd_id",
                "opposing_odd_id": "$opposing_odd_id",
                "selection_id":  "$selection_id",
            }},
            "league_id":   {"$first": "$league_id"},
            "stat_entity_id": {"$first": "$stat_entity_id"},
            "bet_type_id": {"$first": "$bet_type_id"},
        }},
        # 2026-05-24 — REMOVED prizepicks-only filter. Multi-book
        # universe — we keep EVERY row, then pick anchor by priority
        # below. HR/SB/doubles/etc. that PP doesn't carry will now
        # flow through downstream collections instead of being
        # silently dropped.
    ]

    upserts: List[UpdateOne] = []
    n_anchors = 0
    books_attached_total = 0
    cons_attached = 0
    sample_docs: List[Dict[str, Any]] = []

    cursor = db.sgo_props_raw.aggregate(pipeline, allowDiskUse=True)
    async for grp in cursor:
        n_anchors += 1
        eid = grp["_id"]["event_id"]
        pid = grp["_id"]["player_id"]
        ev_info = event_meta.get(eid, {})

        # De-duplicate book quotes (a single book can repeat across snapshots
        # if the latest-snapshot collapse didn't fully fold). Keep the
        # latest snapshot per book.
        seen_book: Dict[str, Dict[str, Any]] = {}
        for b in grp["books"]:
            bid = b.get("book_id")
            if bid is None:
                continue
            prev = seen_book.get(bid)
            if (prev is None or
                (b.get("snapshot_time") or "") > (prev.get("snapshot_time") or "")):
                seen_book[bid] = b
        anchor_book_id, anchor, anchor_source = _pick_anchor(seen_book)
        if anchor_book_id is None:
            continue  # no books at all — pre-existing aggregation skipped this
        # `other_books` = every book EXCEPT the chosen anchor. Same
        # shape as before so downstream consumers don't care which
        # book became the anchor.
        other_books = [v for k, v in seen_book.items() if k != anchor_book_id]
        books_attached_total += len(other_books)
        available_books = sorted(seen_book.keys())
        playable_flags = {flag: False for flag in _PLAYABLE_FLAG_MAP.values()}
        for bid in available_books:
            flag = _PLAYABLE_FLAG_MAP.get(bid)
            if flag:
                playable_flags[flag] = True

        # Consensus lookup (latest per ANY odd_id from the group; the
        # anchor's odd_id is preferred since it's market-identifying.
        # PrizePicks odd_ids can be opaque so prefer non-PP).
        consensus_doc: Optional[Dict[str, Any]] = None
        odd_ids_to_try: List[str] = []
        for b in other_books + [anchor]:
            oid = b.get("odd_id")
            if oid and oid not in odd_ids_to_try:
                odd_ids_to_try.append(oid)
        if odd_ids_to_try:
            cons_cursor = db.sgo_book_consensus.find(
                {"event_id": eid, "odd_id": {"$in": odd_ids_to_try}},
                projection={"_id": 0, "fair_odds": 1, "book_odds": 1,
                             "consensus_probability": 1, "snapshot_time": 1,
                             "odd_id": 1}
            ).sort([("snapshot_time", -1)]).limit(1)
            cons_rows = await cons_cursor.to_list(length=1)
            if cons_rows:
                consensus_doc = cons_rows[0]
                cons_attached += 1

        doc = {
            "event_id":   eid,
            "league_id":  ev_info.get("league_id") or grp.get("league_id"),
            "sport_id":   ev_info.get("sport_id"),
            "game_date":  ev_info.get("game_date"),
            "player_id":  pid,
            "player_name": player_name.get(pid, ""),
            "stat_id":    grp["_id"]["stat_id"],
            "side":       grp["_id"]["side"],
            "line":       grp["_id"]["line"],
            "period_id":  grp["_id"]["period_id"],
            "stat_entity_id": grp.get("stat_entity_id"),
            "bet_type_id":    grp.get("bet_type_id"),
            "anchor": {
                # 2026-05-24 — `book_id` is now whatever priority book
                # was found for this row. Existing PP-anchored rows
                # remain byte-identical because PP is first in
                # ANCHOR_PRIORITY. Downstream code that read
                # `anchor.book_id` continues to work — it just sees
                # DK/FD/MGM/etc. on rows PP didn't cover.
                "book_id":       anchor_book_id,
                "price":         anchor.get("price"),
                "snapshot_time": anchor.get("snapshot_time"),
                "odd_id":        anchor.get("odd_id"),
                "opposing_odd_id": anchor.get("opposing_odd_id"),
                "selection_id":  anchor.get("selection_id"),
                # New metadata fields the optimizer can read:
                "source":        anchor_source,
            },
            # New top-level fields for the multi-book universe. The
            # optimizer + UI use these for source-filtering without
            # reshaping the row.
            "anchor_book":     anchor_book_id,
            "anchor_line":     grp["_id"]["line"],
            "anchor_odds":     anchor.get("price"),
            "anchor_source":   anchor_source,
            "available_books": available_books,
            **playable_flags,
            "books":     other_books,
            "n_books":   len(other_books),
            "fair_odds": (consensus_doc or {}).get("fair_odds"),
            "book_odds": (consensus_doc or {}).get("book_odds"),
            "consensus_probability":
                          (consensus_doc or {}).get("consensus_probability"),
            "consensus_snapshot":
                          (consensus_doc or {}).get("snapshot_time"),
            "updated_at": datetime.now(timezone.utc),
        }
        if len(sample_docs) < 3:
            sample_docs.append(doc)

        filt = {
            "event_id":  eid,
            "player_id": pid,
            "stat_id":   doc["stat_id"],
            "side":      doc["side"],
            "line":      doc["line"],
            "period_id": doc["period_id"],
        }
        upserts.append(UpdateOne(filt, {"$set": doc}, upsert=True))
        if len(upserts) >= 500 and not dry_run:
            await db[out_coll].bulk_write(upserts, ordered=False)
            upserts = []

    if upserts and not dry_run:
        await db[out_coll].bulk_write(upserts, ordered=False)

    return {
        "month": month,
        "events": len(eligible_events),
        "anchors": n_anchors,
        "books_attached": books_attached_total,
        "with_consensus": cons_attached,
        "avg_books_per_anchor": (round(books_attached_total / n_anchors, 2)
                                   if n_anchors else 0.0),
        "consensus_rate": (round(cons_attached / n_anchors, 4)
                            if n_anchors else None),
        "upserts": 0 if dry_run else n_anchors,
        "skipped_dry_run": dry_run,
        "sample_docs": sample_docs,
    }


# ─── main ─────────────────────────────────────────────────────────────────
async def amain(args: argparse.Namespace) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Hybrid collection layout (2026-05-23 NFL split): MLB → sgo_pp_research_core,
    # NFL → sgo_nfl_research_core, all other leagues default to OUT_COLL.
    out_coll = _resolve_out_coll(args)

    print(f"[{datetime.now(timezone.utc).isoformat()}] "
          f"build_pp_research_core starting")
    print(f"  league={args.league or '(all)'}  "
          f"window=[{args.start or 'all'} .. {args.end or 'all'}]  "
          f"dry_run={args.dry_run}  drop_existing={args.drop_existing}  "
          f"out_coll={out_coll}")

    # Optional drop (gated by flag — needs explicit user opt-in)
    if args.drop_existing:
        if not dry_run_safe_drop(args, out_coll):
            return 2
        if not args.dry_run:
            existing = await db[out_coll].count_documents({})
            print(f"  [drop] {out_coll} currently has {existing} docs — dropping")
            await db[out_coll].drop()
        else:
            print(f"  [drop] dry-run: would have dropped {out_coll}")

    await ensure_out_indexes(db, out_coll)

    # Resolve the date window. If not supplied, auto-detect from sgo_events.
    if args.start and args.end:
        start, end = args.start, args.end
    else:
        bounds = await db.sgo_events.aggregate([
            {"$group": {"_id": None,
                         "min": {"$min": "$game_date"},
                         "max": {"$max": "$game_date"}}}]).to_list(None)
        if not bounds or not bounds[0].get("min"):
            print("  [err] no sgo_events found")
            client.close()
            return 1
        start = args.start or bounds[0]["min"]
        end   = args.end   or bounds[0]["max"]
        print(f"  [auto-window] using {start} .. {end} (from sgo_events range)")

    # Cache player_id → name once (small collection)
    player_name: Dict[str, str] = {}
    async for d in db.sgo_players.find(
        {}, projection={"_id": 0, "player_id": 1, "player_name": 1}):
        if d.get("player_id"):
            player_name[d["player_id"]] = d.get("player_name") or ""
    print(f"  [cache] sgo_players loaded: {len(player_name)} identities")

    # Run month by month
    total: Dict[str, Any] = {
        "months_processed": 0, "events": 0, "anchors": 0,
        "books_attached": 0, "with_consensus": 0, "upserts": 0,
        "sample_docs": [],
    }
    for month, m_start, m_end in _month_chunks(start, end):
        print(f"\n  [month] {month}  window=[{m_start} .. {m_end}]")
        try:
            r = await build_month(
                db, league=args.league, month=month,
                start_iso=m_start, end_iso=m_end, dry_run=args.dry_run,
                player_name=player_name, out_coll=out_coll)
        except Exception as e:
            print(f"    FAILED: {e!r}")
            continue
        print(f"    events={r['events']}  pp_anchors={r['anchors']}  "
              f"books_attached={r['books_attached']}  "
              f"avg_books/anchor={r['avg_books_per_anchor']}  "
              f"with_consensus={r['with_consensus']}  "
              f"upserts={r['upserts']}")
        total["months_processed"] += 1
        total["events"] += r["events"]
        total["anchors"] += r["anchors"]
        total["books_attached"] += r["books_attached"]
        total["with_consensus"] += r["with_consensus"]
        total["upserts"] += r["upserts"]
        if not total["sample_docs"]:
            total["sample_docs"] = r["sample_docs"][:3]

    # Final summary
    print()
    print("=" * 72)
    print(f"  build_pp_research_core SUMMARY")
    print("=" * 72)
    print(f"  months processed:       {total['months_processed']}")
    print(f"  events scanned:         {total['events']}")
    print(f"  PrizePicks anchors:     {total['anchors']}")
    print(f"  other-book attachments: {total['books_attached']}  "
          f"(avg "
          f"{round(total['books_attached']/max(total['anchors'],1),2)} per anchor)")
    print(f"  anchors w/ consensus:   {total['with_consensus']}  "
          f"({round(100*total['with_consensus']/max(total['anchors'],1),1)}%)")
    print(f"  upserts applied:        "
          f"{total['upserts'] if not args.dry_run else 0}  "
          f"(dry_run={args.dry_run})")
    if not args.dry_run:
        final_count = await db[out_coll].count_documents({})
        print(f"  {OUT_COLL} doc count: {final_count}")
    if total["sample_docs"]:
        import json
        print(f"\n  Sample documents (first {len(total['sample_docs'])}):")
        for d in total["sample_docs"]:
            print("    " + "─" * 60)
            # Trim books to first 3 for printability
            d2 = {**d, "books": d["books"][:3]}
            print("    " + json.dumps(d2, indent=2, default=str)
                              .replace("\n", "\n    "))
    print("=" * 72)
    client.close()
    return 0


def dry_run_safe_drop(args: argparse.Namespace, out_coll: str = OUT_COLL) -> bool:
    """Confirm --drop-existing is intentional (printed warning + checks --yes
    when not in dry-run). Returns True if drop should proceed."""
    if args.dry_run:
        return True
    if args.yes:
        print("  [drop] --drop-existing was acknowledged with --yes")
        return True
    print(f"  [err] --drop-existing requires --yes (or --dry-run) for "
          f"safety. Refusing to drop {out_coll}.")
    return False


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--league", default=None,
                    help="Filter to one league (e.g. MLB); default: all")
    p.add_argument("--start",  default=None, help="YYYY-MM-DD inclusive")
    p.add_argument("--end",    default=None, help="YYYY-MM-DD inclusive")
    p.add_argument("--dry-run", action="store_true",
                    help="Compute but don't write")
    p.add_argument("--drop-existing", action="store_true",
                    help=f"Drop {OUT_COLL} before rebuild "
                         f"(requires --yes when not --dry-run)")
    p.add_argument("--yes", action="store_true",
                    help="Acknowledge destructive --drop-existing")
    p.add_argument("--out-coll", default=None,
                    help="Override destination collection. Default: "
                          "sgo_pp_research_core (MLB) or sgo_nfl_research_core "
                          "when --league=NFL or sgo_ncaaf_research_core "
                          "when --league=NCAAF.")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
