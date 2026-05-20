"""
build_historical_consensus_probabilities.py — derive sgo_pp_research_core_enriched.

Reads:   sgo_pp_research_core           (immutable, never mutated)
Writes:  sgo_pp_research_core_enriched  (idempotent upserts)

For each PP-anchored row:
  - per-book pair-then-devig fair probability (when opposite side exists for same book)
  - consensus / sharp-only consensus / best-book / market width / disagreement
  - PrizePicks implied probability and edge vs. consensus / best book
  - preserves every source field; appends derived fields + enrichment_version

Pair-then-devig methodology (production-aligned):
    For each book that quotes BOTH sides of the same anchor line:
        p_side  = implied(price_side)
        p_opp   = implied(price_opp)
        vig     = p_side + p_opp
        fair_p  = p_side / vig          ← per-book fair prob for the anchor side
    Then average across books.  We DO NOT average raw implied probs before
    devigging.

OOM-safe:
    Chunked by game_date. Each date loads its full anchor set (both sides)
    into a small dict, processes, bulk-writes. allowDiskUse on cursors.

Usage:
    python -m scripts.sgo.build_historical_consensus_probabilities \\
        --league MLB --start 2025-06-01 --end 2025-06-30
    python -m scripts.sgo.build_historical_consensus_probabilities --dry-run
    python -m scripts.sgo.build_historical_consensus_probabilities --drop-existing --yes
    python -m scripts.sgo.build_historical_consensus_probabilities --resume
"""
from __future__ import annotations
import argparse
import asyncio
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")  # preview fallback
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import UpdateOne, ASCENDING

SRC_COLL = "sgo_pp_research_core"
OUT_COLL = "sgo_pp_research_core_enriched"
ENRICHMENT_VERSION = "v1"
ANCHOR_BOOK = "prizepicks"
SHARP_BOOKS = {"draftkings", "fanduel", "pinnacle",
                "circa", "bet365", "betonline"}


# ─── odds helpers ──────────────────────────────────────────────────────────
def american_to_implied(price: Any) -> Optional[float]:
    if price is None or price == "":
        return None
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if p > 0:
        return 100.0 / (p + 100.0)
    if p < 0:
        return -p / (-p + 100.0)
    return None  # 0 odds is malformed


def _opposite_side(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    u = s.upper()
    if u == "OVER":  return "UNDER"
    if u == "UNDER": return "OVER"
    if u == "YES":   return "NO"
    if u == "NO":    return "YES"
    return None


# ─── indexes ───────────────────────────────────────────────────────────────
async def ensure_out_indexes(db: AsyncIOMotorDatabase) -> None:
    c = db[OUT_COLL]
    await c.create_index(
        [("event_id", ASCENDING), ("player_id", ASCENDING),
         ("stat_id", ASCENDING), ("side", ASCENDING),
         ("line", ASCENDING), ("period_id", ASCENDING)],
        unique=True, name="enriched_anchor_pk")
    await c.create_index("league_id")
    await c.create_index("game_date")
    await c.create_index("player_id")
    await c.create_index("stat_id")
    await c.create_index("edge_vs_consensus")
    await c.create_index("consensus_probability")
    await c.create_index("has_valid_devig")
    await c.create_index("enrichment_version")


# ─── core enrichment per anchor ────────────────────────────────────────────
def _enrich_one(
    anchor_doc: Dict[str, Any],
    sibling_doc: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute the derived fields for a single anchor row.

    anchor_doc / sibling_doc shape comes from sgo_pp_research_core:
      { anchor:{book_id="prizepicks", price, ...},
        books:[{book_id, price, ...}, ...], ... }
    """
    # Build per-book price maps for both sides. Include the PP anchor row in
    # the side-price map because PP itself can quote a price worth surfacing
    # as PP implied prob.
    side_books: Dict[str, float] = {}      # book_id → price on anchor side
    for b in (anchor_doc.get("books") or []):
        bid = b.get("book_id")
        if bid and b.get("price") is not None:
            side_books[bid] = b["price"]
    # PP anchor price (anchor side)
    pp_price = (anchor_doc.get("anchor") or {}).get("price")
    if pp_price is not None:
        side_books.setdefault(ANCHOR_BOOK, pp_price)

    opp_books: Dict[str, float] = {}       # book_id → price on opposite side
    if sibling_doc:
        for b in (sibling_doc.get("books") or []):
            bid = b.get("book_id")
            if bid and b.get("price") is not None:
                opp_books[bid] = b["price"]
        opp_pp_price = (sibling_doc.get("anchor") or {}).get("price")
        if opp_pp_price is not None:
            opp_books.setdefault(ANCHOR_BOOK, opp_pp_price)

    # Per-book pair-then-devig (exclude PP from the *consensus* of fair books,
    # but PP CAN be devigged if both sides are PP — that's its own implied
    # rather than a fair estimate, so we exclude it explicitly here.)
    devig_probs_all: List[Tuple[str, float]] = []  # (book_id, fair_p)
    devig_probs_sharp: List[Tuple[str, float]] = []
    book_count_all = 0
    sharp_book_count = 0
    side_implieds: List[float] = []  # for market width fallback when devig sparse

    for book_id, price in side_books.items():
        if book_id != ANCHOR_BOOK:
            book_count_all += 1
            if book_id in SHARP_BOOKS:
                sharp_book_count += 1
        ip = american_to_implied(price)
        if ip is not None and book_id != ANCHOR_BOOK:
            side_implieds.append(ip)

        # Devig only when this same book quoted the opposite side
        opp_price = opp_books.get(book_id)
        if opp_price is None or book_id == ANCHOR_BOOK:
            continue
        p_s = american_to_implied(price)
        p_o = american_to_implied(opp_price)
        if p_s is None or p_o is None:
            continue
        vig = p_s + p_o
        if vig <= 0:
            continue
        fair = p_s / vig
        # Guard against pathological prices producing >1 fair probs
        if not (0.0 <= fair <= 1.0) or math.isnan(fair):
            continue
        devig_probs_all.append((book_id, fair))
        if book_id in SHARP_BOOKS:
            devig_probs_sharp.append((book_id, fair))

    has_valid_devig = len(devig_probs_all) > 0
    fair_list_all   = [f for _, f in devig_probs_all]
    fair_list_sharp = [f for _, f in devig_probs_sharp]

    consensus_probability = (
        sum(fair_list_all) / len(fair_list_all)
        if fair_list_all else None
    )
    sharp_consensus_probability = (
        sum(fair_list_sharp) / len(fair_list_sharp)
        if fair_list_sharp else None
    )
    # Best available = most bullish fair prob on the PP-anchored side
    best_book_probability = max(fair_list_all) if fair_list_all else None
    best_book_id = (
        max(devig_probs_all, key=lambda t: t[1])[0]
        if devig_probs_all else None
    )

    pp_implied_probability = american_to_implied(pp_price)
    edge_vs_consensus = (
        consensus_probability - pp_implied_probability
        if (consensus_probability is not None and pp_implied_probability is not None)
        else None
    )
    best_book_edge = (
        best_book_probability - pp_implied_probability
        if (best_book_probability is not None and pp_implied_probability is not None)
        else None
    )

    # Market width: spread of fair probs across books (preferred), otherwise
    # spread of raw implieds across same-side books.
    if len(fair_list_all) >= 2:
        market_width = max(fair_list_all) - min(fair_list_all)
        consensus_disagreement = statistics.pstdev(fair_list_all)
    elif len(side_implieds) >= 2:
        market_width = max(side_implieds) - min(side_implieds)
        consensus_disagreement = statistics.pstdev(side_implieds)
    else:
        market_width = None
        consensus_disagreement = None

    return {
        "consensus_probability":       consensus_probability,
        "sharp_consensus_probability": sharp_consensus_probability,
        "pp_implied_probability":      pp_implied_probability,
        "edge_vs_consensus":           edge_vs_consensus,
        "best_book_probability":       best_book_probability,
        "best_book_id":                best_book_id,
        "best_book_edge":              best_book_edge,
        "devig_book_count":            len(devig_probs_all),
        "sharp_book_count":            sharp_book_count,
        "book_count":                  book_count_all,
        "market_width":                market_width,
        "consensus_disagreement":      consensus_disagreement,
        "has_valid_devig":             has_valid_devig,
        "enrichment_version":          ENRICHMENT_VERSION,
        "enriched_at":                 datetime.now(timezone.utc),
    }


# ─── chunk processing ──────────────────────────────────────────────────────
async def _distinct_game_dates(
    db: AsyncIOMotorDatabase, *, league: Optional[str],
    start: Optional[str], end: Optional[str],
) -> List[str]:
    match: Dict[str, Any] = {}
    if league:
        match["league_id"] = league
    if start or end:
        gd: Dict[str, Any] = {}
        if start: gd["$gte"] = start
        if end:   gd["$lte"] = end
        match["game_date"] = gd
    pipeline: List[Dict[str, Any]] = []
    if match:
        pipeline.append({"$match": match})
    pipeline.append({"$group": {"_id": "$game_date"}})
    pipeline.append({"$sort": {"_id": 1}})
    dates: List[str] = []
    async for r in db[SRC_COLL].aggregate(pipeline, allowDiskUse=True):
        if r.get("_id"):
            dates.append(r["_id"])
    return dates


async def process_date(
    db: AsyncIOMotorDatabase, *, league: Optional[str], game_date: str,
    dry_run: bool, resume: bool,
) -> Dict[str, Any]:
    match: Dict[str, Any] = {"game_date": game_date}
    if league:
        match["league_id"] = league

    # Load all anchors for the date
    docs: List[Dict[str, Any]] = []
    async for d in db[SRC_COLL].find(match, projection={"_id": 0}):
        docs.append(d)
    if not docs:
        return {"processed": 0, "enriched": 0, "with_devig": 0,
                 "skipped_resume": 0}

    # Index by (event, player, stat, line, period) → side → doc
    pair: Dict[Tuple, Dict[str, Dict[str, Any]]] = {}
    for d in docs:
        k = (d.get("event_id"), d.get("player_id"), d.get("stat_id"),
             d.get("line"), d.get("period_id"))
        side = (d.get("side") or "").upper()
        pair.setdefault(k, {})[side] = d

    # Optional resume: collect existing enrichment_version flags for this date
    already_done: set = set()
    if resume and not dry_run:
        async for r in db[OUT_COLL].find(
            {"game_date": game_date, "enrichment_version": ENRICHMENT_VERSION},
            projection={"_id": 0, "event_id": 1, "player_id": 1,
                         "stat_id": 1, "side": 1, "line": 1, "period_id": 1}
        ):
            already_done.add((r.get("event_id"), r.get("player_id"),
                                r.get("stat_id"), (r.get("side") or "").upper(),
                                r.get("line"), r.get("period_id")))

    upserts: List[UpdateOne] = []
    enriched = 0
    with_devig = 0
    skipped_resume = 0
    sample_docs: List[Dict[str, Any]] = []

    for k, by_side in pair.items():
        for side_str, anchor_doc in by_side.items():
            opp_side = _opposite_side(side_str)
            sibling = by_side.get(opp_side) if opp_side else None
            uid = (k[0], k[1], k[2], side_str, k[3], k[4])
            if uid in already_done:
                skipped_resume += 1
                continue

            extra = _enrich_one(anchor_doc, sibling)
            if extra["has_valid_devig"]:
                with_devig += 1
            # Merge source fields with new derived fields. Drop motor _id
            # if it slipped in (we projected it out, but defensive).
            merged = {**anchor_doc, **extra}
            merged.pop("_id", None)
            enriched += 1

            if len(sample_docs) < 2 and extra["has_valid_devig"]:
                sample_docs.append(merged)

            filt = {
                "event_id":  merged["event_id"],
                "player_id": merged["player_id"],
                "stat_id":   merged["stat_id"],
                "side":      merged["side"],
                "line":      merged["line"],
                "period_id": merged["period_id"],
            }
            upserts.append(UpdateOne(filt, {"$set": merged}, upsert=True))
            if len(upserts) >= 1000 and not dry_run:
                await db[OUT_COLL].bulk_write(upserts, ordered=False)
                upserts = []

    if upserts and not dry_run:
        await db[OUT_COLL].bulk_write(upserts, ordered=False)

    return {
        "processed": len(docs), "enriched": enriched,
        "with_devig": with_devig, "skipped_resume": skipped_resume,
        "sample_docs": sample_docs,
    }


# ─── main ─────────────────────────────────────────────────────────────────
async def amain(args: argparse.Namespace) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    t0 = time.time()
    print(f"[{datetime.now(timezone.utc).isoformat()}] "
          f"build_historical_consensus_probabilities (enrichment={ENRICHMENT_VERSION})")
    print(f"  league={args.league or '(all)'}  "
          f"window=[{args.start or 'all'} .. {args.end or 'all'}]  "
          f"dry_run={args.dry_run}  drop={args.drop_existing}  resume={args.resume}")

    if args.drop_existing:
        if not args.dry_run and not args.yes:
            print(f"  [err] --drop-existing requires --yes (or --dry-run). Refusing to drop {OUT_COLL}.")
            client.close()
            return 2
        if not args.dry_run:
            existing = await db[OUT_COLL].count_documents({})
            print(f"  [drop] {OUT_COLL} has {existing} docs — dropping")
            await db[OUT_COLL].drop()
        else:
            print(f"  [drop] dry-run: would have dropped {OUT_COLL}")

    await ensure_out_indexes(db)

    dates = await _distinct_game_dates(
        db, league=args.league, start=args.start, end=args.end)
    if not dates:
        print(f"  [err] no anchor docs found in {SRC_COLL} for the given window")
        client.close()
        return 1
    print(f"  [plan] {len(dates)} game_dates to process "
          f"(from {dates[0]} to {dates[-1]})")

    tot = {"processed": 0, "enriched": 0, "with_devig": 0,
            "skipped_resume": 0, "dates": 0,
            "agg_books": 0, "agg_sharp": 0, "agg_edge": 0.0, "agg_edge_n": 0,
            "sample_docs": []}

    log_every = 10_000
    next_log = log_every

    for gd in dates:
        try:
            r = await process_date(db, league=args.league, game_date=gd,
                                     dry_run=args.dry_run, resume=args.resume)
        except Exception as e:
            print(f"    [{gd}] FAILED: {e!r}")
            continue
        tot["dates"] += 1
        tot["processed"] += r["processed"]
        tot["enriched"]  += r["enriched"]
        tot["with_devig"] += r["with_devig"]
        tot["skipped_resume"] += r["skipped_resume"]
        if r.get("sample_docs") and len(tot["sample_docs"]) < 2:
            tot["sample_docs"].extend(r["sample_docs"][:2 - len(tot["sample_docs"])])

        # Progress every ~log_every processed docs
        if tot["processed"] >= next_log:
            elapsed = time.time() - t0
            rate = tot["processed"] / elapsed if elapsed > 0 else 0
            print(f"  [{gd}] cumulative processed={tot['processed']:,}  "
                  f"enriched={tot['enriched']:,}  with_devig={tot['with_devig']:,}  "
                  f"skipped={tot['skipped_resume']:,}  "
                  f"rate={rate:,.0f} docs/s  elapsed={elapsed:,.0f}s")
            next_log += log_every

    # Aggregate post-run telemetry from the output collection
    if not args.dry_run:
        agg_pipeline = [
            {"$match": {"enrichment_version": ENRICHMENT_VERSION,
                         **({"league_id": args.league} if args.league else {})}},
            {"$group": {
                "_id": None,
                "n": {"$sum": 1},
                "valid": {"$sum": {"$cond": ["$has_valid_devig", 1, 0]}},
                "avg_books": {"$avg": "$book_count"},
                "avg_sharp": {"$avg": "$sharp_book_count"},
                "avg_edge": {"$avg": "$edge_vs_consensus"},
            }}
        ]
        agg_rows = await db[OUT_COLL].aggregate(agg_pipeline).to_list(length=1)
        agg = agg_rows[0] if agg_rows else {}
    else:
        agg = {}

    runtime = time.time() - t0
    print()
    print("=" * 72)
    print(f"  build_historical_consensus_probabilities SUMMARY  ({ENRICHMENT_VERSION})")
    print("=" * 72)
    print(f"  game_dates processed:    {tot['dates']:,}")
    print(f"  docs processed (input):  {tot['processed']:,}")
    print(f"  docs enriched (output):  {tot['enriched']:,}")
    print(f"  with_valid_devig:        {tot['with_devig']:,}  "
          f"({(100*tot['with_devig']/max(tot['enriched'],1)):.1f}%)")
    print(f"  skipped (resume):        {tot['skipped_resume']:,}")
    if agg:
        valid_pct = 100 * (agg.get('valid') or 0) / max(agg.get('n') or 1, 1)
        print(f"  ── post-run aggregate (collection-wide @ {ENRICHMENT_VERSION}) ──")
        print(f"  total in enriched:       {agg.get('n'):,}")
        print(f"  valid devig %:           {valid_pct:.2f}%")
        print(f"  avg books per prop:      {(agg.get('avg_books') or 0):.2f}")
        print(f"  avg sharp books / prop:  {(agg.get('avg_sharp') or 0):.2f}")
        ae = agg.get('avg_edge')
        print(f"  avg edge_vs_consensus:   {ae:+.4f}" if ae is not None
              else "  avg edge_vs_consensus:   n/a")
    print(f"  runtime:                 {runtime:,.1f}s")
    if tot["sample_docs"]:
        import json
        print(f"\n  Sample enriched docs (first {len(tot['sample_docs'])}):")
        for d in tot["sample_docs"]:
            print("    " + "─" * 60)
            d2 = {**d, "books": (d.get("books") or [])[:3]}
            print("    " + json.dumps(d2, indent=2, default=str)
                              .replace("\n", "\n    "))
    print("=" * 72)
    client.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--league", default=None,
                    help="Filter to one league (e.g. MLB); default: all")
    p.add_argument("--start",  default=None, help="YYYY-MM-DD inclusive")
    p.add_argument("--end",    default=None, help="YYYY-MM-DD inclusive")
    p.add_argument("--dry-run", action="store_true",
                    help="Compute but don't write")
    p.add_argument("--drop-existing", action="store_true",
                    help=f"Drop {OUT_COLL} before rebuild (requires --yes)")
    p.add_argument("--yes", action="store_true",
                    help="Acknowledge destructive --drop-existing")
    p.add_argument("--resume", action="store_true",
                    help=f"Skip docs already enriched at version "
                         f"{ENRICHMENT_VERSION}")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
