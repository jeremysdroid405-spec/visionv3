"""
sgo_book_coverage.py — read-only.

For every bookmaker found in `sgo_props_raw`, reports:
  • total prop rows
  • distinct events covered
  • distinct stat_ids covered
  • distinct (player_id|stat_entity_id) covered
  • both-sided pair rate (via opposing_odd_id resolution)
  • consensus availability rate (rows whose oddID has a consensus row)
  • snapshot recency (max snapshot_time seen)
  • per-league row breakdown
  • cross-tab: book × stat_id (top 10 stat_ids per book)

Output:
  audits/sgo_analysis/book_coverage_<stamp>.csv
  audits/sgo_analysis/book_coverage_crosstab_<stamp>.csv
  audits/sgo_analysis/book_coverage_<stamp>.json

Usage:
  python -m scripts.sgo.sgo_book_coverage
  python -m scripts.sgo.sgo_book_coverage --league MLB --start 2025-06-15 --end 2025-06-15
"""
from __future__ import annotations
import argparse
import asyncio
import sys
from collections import defaultdict, Counter
from datetime import datetime, timezone
from typing import Any, Dict, Set, Tuple
sys.path.insert(0, "/app/backend")
from scripts.sgo._analysis_common import (
    AUDIT_DIR, ensure_audit_dir, get_db, write_csv, write_json, fmt_pct,
)


async def build(
    *, league: str | None = None, sport: str | None = None,
    start_date: str | None = None, end_date: str | None = None,
) -> Dict[str, Any]:
    client, db = get_db()
    match_event: Dict[str, Any] = {}
    if league: match_event["league_id"] = league
    if sport:  match_event["sport_id"]  = sport
    if start_date or end_date:
        gd: Dict[str, Any] = {}
        if start_date: gd["$gte"] = start_date
        if end_date:   gd["$lte"] = end_date
        match_event["game_date"] = gd
    event_meta: Dict[str, Dict[str, str]] = {}
    async for ev in db.sgo_events.find(
        match_event or {},
        projection={"_id": 0, "event_id": 1, "league_id": 1,
                    "sport_id": 1, "game_date": 1}):
        if ev.get("event_id"):
            event_meta[ev["event_id"]] = {
                "league_id": ev.get("league_id"),
                "sport_id":  ev.get("sport_id"),
                "game_date": ev.get("game_date"),
            }
    eligible = set(event_meta.keys())
    if not eligible:
        client.close()
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "filter": {"league": league, "sport": sport,
                        "start_date": start_date, "end_date": end_date},
            "total_props": 0, "books": [],
            "crosstab_stats": [], "crosstab": {},
        }

    # Pull consensus key set (event_id, odd_id)
    cons_keys: Set[Tuple[str, str]] = set()
    async for c in db.sgo_book_consensus.find(
        {"event_id": {"$in": list(eligible)}},
        projection={"_id": 0, "event_id": 1, "odd_id": 1}):
        cons_keys.add((c.get("event_id"), c.get("odd_id")))

    # Single scan of sgo_props_raw
    by_book: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "rows": 0,
        "events": set(),
        "stats":  set(),
        "players": set(),
        "leagues": Counter(),
        "stat_counter": Counter(),
        "cons_avail":  0,
        "pair_candidates": 0,
        "pair_present": 0,
        "max_snapshot": "",
    })
    # opposing-side resolution
    odd_index: Dict[Tuple, Set[str]] = defaultdict(set)
    pair_cands_per_book: Dict[str, list] = defaultdict(list)
    proj = {"_id": 0, "event_id": 1, "stat_id": 1, "book_id": 1,
            "side": 1, "player_id": 1, "stat_entity_id": 1,
            "odd_id": 1, "opposing_odd_id": 1, "snapshot_time": 1}
    total = 0
    async for p in db.sgo_props_raw.find(
        {"event_id": {"$in": list(eligible)}}, projection=proj):
        total += 1
        book = p.get("book_id") or "_unknown"
        meta = event_meta.get(p.get("event_id"), {})
        b = by_book[book]
        b["rows"] += 1
        b["events"].add(p.get("event_id"))
        b["stats"].add(p.get("stat_id"))
        ent = p.get("player_id") or p.get("stat_entity_id")
        if ent: b["players"].add(ent)
        if meta.get("league_id"):
            b["leagues"][meta["league_id"]] += 1
        b["stat_counter"][p.get("stat_id") or "_unknown"] += 1
        if (p.get("event_id"), p.get("odd_id")) in cons_keys:
            b["cons_avail"] += 1
        snap = p.get("snapshot_time") or ""
        if snap > b["max_snapshot"]: b["max_snapshot"] = snap
        idx_key = (p.get("event_id"), book, snap)
        if p.get("odd_id"):
            odd_index[idx_key].add(p["odd_id"])
        if p.get("opposing_odd_id"):
            b["pair_candidates"] += 1
            pair_cands_per_book[book].append((idx_key, p["opposing_odd_id"]))

    # resolve pairs
    for book, cands in pair_cands_per_book.items():
        for idx_key, opp in cands:
            if opp in odd_index.get(idx_key, ()):
                by_book[book]["pair_present"] += 1

    rows: list[dict] = []
    for book, b in by_book.items():
        rows.append({
            "book_id": book,
            "rows":    b["rows"],
            "distinct_events":  len(b["events"]),
            "distinct_stats":   len(b["stats"]),
            "distinct_players": len(b["players"]),
            "leagues":          dict(b["leagues"]),
            "consensus_avail":  b["cons_avail"],
            "consensus_rate":   (b["cons_avail"]/b["rows"]) if b["rows"] else None,
            "pair_candidates":  b["pair_candidates"],
            "pair_present":     b["pair_present"],
            "pair_rate":        (b["pair_present"]/b["pair_candidates"]
                                  if b["pair_candidates"] else None),
            "max_snapshot":     b["max_snapshot"],
            "top10_stats":      b["stat_counter"].most_common(10),
        })
    rows.sort(key=lambda r: -r["rows"])

    # cross-tab: book × stat (matrix of counts, top 20 stats × all books)
    stat_totals: Counter = Counter()
    for r in rows:
        for s, n in r["top10_stats"]:
            stat_totals[s] += n
    top_stats = [s for s, _ in stat_totals.most_common(25)]
    crosstab: Dict[str, Dict[str, int]] = {}
    for r in rows:
        m = dict(r["top10_stats"])  # may be incomplete for low-rank stats
        crosstab[r["book_id"]] = {s: m.get(s, 0) for s in top_stats}

    client.close()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filter": {"league": league, "sport": sport,
                    "start_date": start_date, "end_date": end_date},
        "total_props": total,
        "books": rows,
        "crosstab_stats": top_stats,
        "crosstab": crosstab,
    }


def emit(rep: Dict[str, Any]) -> tuple[str, str, str]:
    ensure_audit_dir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    csv_path  = f"{AUDIT_DIR}/book_coverage_{stamp}.csv"
    cross_csv = f"{AUDIT_DIR}/book_coverage_crosstab_{stamp}.csv"
    json_path = f"{AUDIT_DIR}/book_coverage_{stamp}.json"

    # main books CSV
    header = ["book_id", "rows", "distinct_events", "distinct_stats",
              "distinct_players", "consensus_avail", "consensus_rate",
              "pair_candidates", "pair_present", "pair_rate", "max_snapshot",
              "leagues"]
    rows = [[
        b["book_id"], b["rows"], b["distinct_events"], b["distinct_stats"],
        b["distinct_players"], b["consensus_avail"], b["consensus_rate"],
        b["pair_candidates"], b["pair_present"], b["pair_rate"],
        b["max_snapshot"], str(b["leagues"]),
    ] for b in rep["books"]]
    write_csv(csv_path, header, rows)

    # cross-tab CSV
    ct_header = ["book_id"] + rep["crosstab_stats"]
    ct_rows = [[bid] + [rep["crosstab"][bid].get(s, 0)
                         for s in rep["crosstab_stats"]]
                for bid in rep["crosstab"]]
    write_csv(cross_csv, ct_header, ct_rows)

    write_json(json_path, rep)
    return csv_path, cross_csv, json_path


def pretty(rep: Dict[str, Any], top_n: int = 20) -> None:
    print("=" * 100)
    print(f"  SGO BOOK COVERAGE — filter={rep['filter']}  "
          f"total_props={rep['total_props']}  books={len(rep['books'])}")
    print("=" * 100)
    print(f"  {'book':<22s} {'rows':>8s} {'events':>7s} {'stats':>6s} "
          f"{'players':>7s} {'cons%':>7s} {'pair%':>7s} {'max_snapshot'}")
    for b in rep["books"][:top_n]:
        print(f"  {b['book_id'][:22]:<22s} {b['rows']:>8d} "
              f"{b['distinct_events']:>7d} {b['distinct_stats']:>6d} "
              f"{b['distinct_players']:>7d} "
              f"{fmt_pct(b['consensus_rate']):>7s} "
              f"{fmt_pct(b['pair_rate']):>7s} {(b['max_snapshot'] or '-')[:25]}")
    if len(rep["books"]) > top_n:
        print(f"  ... and {len(rep['books'])-top_n} more in CSV")


async def amain(args: argparse.Namespace) -> int:
    rep = await build(league=args.league, sport=args.sport,
                       start_date=args.start, end_date=args.end)
    pretty(rep, top_n=args.top)
    csv_path, cross_csv, json_path = emit(rep)
    print(f"\nCSV       → {csv_path}")
    print(f"Cross-tab → {cross_csv}")
    print(f"JSON      → {json_path}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--league", default=None)
    p.add_argument("--sport",  default=None)
    p.add_argument("--start",  default=None)
    p.add_argument("--end",    default=None)
    p.add_argument("--top",    type=int, default=20)
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
