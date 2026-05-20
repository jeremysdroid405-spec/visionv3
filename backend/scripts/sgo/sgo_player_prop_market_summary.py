"""
sgo_player_prop_market_summary.py — read-only.

For player-prop markets only (rows where `player_id` resolves to a real
person), summarize per (league, sport, stat_id):
  • distinct players
  • distinct events
  • total prop rows
  • avg books per (event, player)            ← book breadth
  • avg lines per (event, player)            ← alt-line depth
  • avg distinct lines per (event, player, book, side)
  • both-sided pair rate (via opposing_odd_id)
  • consensus availability rate
  • top-5 books by row volume                ← who covers this market
  • top-5 players by row volume              ← who's heavily-covered

Output:
  audits/sgo_analysis/player_prop_summary_<stamp>.csv
  audits/sgo_analysis/player_prop_top_players_<stamp>.csv
  audits/sgo_analysis/player_prop_summary_<stamp>.json

Usage:
  python -m scripts.sgo.sgo_player_prop_market_summary
  python -m scripts.sgo.sgo_player_prop_market_summary --league MLB --start 2025-06-15 --end 2025-06-15
"""
from __future__ import annotations
import argparse
import asyncio
import sys
from collections import defaultdict, Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Set, Tuple
sys.path.insert(0, "/app/backend")
from scripts.sgo._analysis_common import (
    AUDIT_DIR, ensure_audit_dir, get_db, write_csv, write_json, fmt_pct,
)
# Re-use the predicate from coverage.py so excluded tokens stay consistent
from scripts.sgo.coverage import _is_real_player_id


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
    event_meta: Dict[str, Dict[str, Any]] = {}
    async for ev in db.sgo_events.find(
        match_event or {},
        projection={"_id": 0, "event_id": 1, "league_id": 1,
                    "sport_id": 1, "game_date": 1}):
        if ev.get("event_id"):
            event_meta[ev["event_id"]] = ev
    eligible = set(event_meta.keys())
    if not eligible:
        client.close()
        return {"rows": [], "top_players": [], "total_props": 0,
                "filter": {"league": league, "sport": sport,
                            "start_date": start_date, "end_date": end_date}}

    # Player registry for name lookup
    player_name: Dict[str, str] = {}
    async for d in db.sgo_players.find(
        {}, projection={"_id": 0, "player_id": 1, "player_name": 1}):
        if d.get("player_id"):
            player_name[d["player_id"]] = d.get("player_name") or ""

    # Consensus key set for availability rate
    cons_keys: Set[Tuple[str, str]] = set()
    async for c in db.sgo_book_consensus.find(
        {"event_id": {"$in": list(eligible)}},
        projection={"_id": 0, "event_id": 1, "odd_id": 1}):
        cons_keys.add((c.get("event_id"), c.get("odd_id")))

    # Per-market aggregator (key = (league, sport, stat_id))
    Mkt = Dict[str, Any]
    by_market: Dict[Tuple[str, str, str], Mkt] = defaultdict(lambda: {
        "rows": 0,
        "players": set(),
        "events":  set(),
        "books":   set(),
        "books_per_player_event": defaultdict(set),
        "lines_per_player_event": defaultdict(set),
        "lines_per_pebs": defaultdict(set),     # (player, event, book, side)
        "book_counter":   Counter(),
        "player_counter": Counter(),
        "cons_avail":     0,
        "pair_candidates": 0,
        "pair_present":   0,
    })
    # Opposing-side resolution scope
    odd_index: Dict[Tuple, Set[str]] = defaultdict(set)
    pair_cands: Dict[Tuple[str, str, str], list] = defaultdict(list)

    # Per-player aggregator
    by_player: Dict[Tuple[str, str], Dict[str, Any]] = defaultdict(lambda: {
        "rows": 0, "events": set(), "books": set(), "stats": set(),
        "lines": set(),
    })

    proj = {"_id": 0, "event_id": 1, "stat_id": 1, "side": 1, "line": 1,
            "price": 1, "book_id": 1, "odd_id": 1, "opposing_odd_id": 1,
            "player_id": 1, "snapshot_time": 1}
    total_props = 0
    async for p in db.sgo_props_raw.find(
        {"event_id": {"$in": list(eligible)}}, projection=proj):
        pid = p.get("player_id")
        if not _is_real_player_id(pid):
            continue
        total_props += 1
        eid = p.get("event_id")
        meta = event_meta.get(eid, {})
        stat = p.get("stat_id") or "_unknown"
        key = (meta.get("league_id"), meta.get("sport_id"), stat)
        m = by_market[key]
        m["rows"] += 1
        m["players"].add(pid)
        m["events"].add(eid)
        m["books"].add(p.get("book_id"))
        m["books_per_player_event"][(pid, eid)].add(p.get("book_id"))
        if p.get("line") is not None:
            m["lines_per_player_event"][(pid, eid)].add(p.get("line"))
            m["lines_per_pebs"][(pid, eid, p.get("book_id"), p.get("side"))].add(p.get("line"))
        m["book_counter"][p.get("book_id") or "_unknown"] += 1
        m["player_counter"][pid] += 1
        if (eid, p.get("odd_id")) in cons_keys: m["cons_avail"] += 1

        snap = p.get("snapshot_time") or ""
        idx_key = (eid, p.get("book_id"), snap)
        if p.get("odd_id"): odd_index[idx_key].add(p["odd_id"])
        if p.get("opposing_odd_id"):
            m["pair_candidates"] += 1
            pair_cands[key].append((idx_key, p["opposing_odd_id"]))

        # per-player rollup
        pkey = (pid, stat)
        bp = by_player[pkey]
        bp["rows"] += 1
        bp["events"].add(eid)
        bp["books"].add(p.get("book_id"))
        bp["stats"].add(stat)
        if p.get("line") is not None: bp["lines"].add(p.get("line"))

    # Resolve opposing-side counts
    for key, cands in pair_cands.items():
        for idx_key, opp in cands:
            if opp in odd_index.get(idx_key, ()):
                by_market[key]["pair_present"] += 1

    # Build per-market rows
    rows: List[Dict[str, Any]] = []
    for (lg, sp, stat), m in by_market.items():
        bpe = m["books_per_player_event"]
        lpe = m["lines_per_player_event"]
        lpebs = m["lines_per_pebs"]
        avg_books = (sum(len(v) for v in bpe.values()) / len(bpe)) if bpe else None
        avg_lines = (sum(len(v) for v in lpe.values()) / len(lpe)) if lpe else None
        avg_lines_pebs = (sum(len(v) for v in lpebs.values()) / len(lpebs)) if lpebs else None
        rows.append({
            "league_id": lg, "sport_id": sp, "stat_id": stat,
            "rows": m["rows"],
            "distinct_players": len(m["players"]),
            "distinct_events":  len(m["events"]),
            "distinct_books":   len(m["books"]),
            "avg_books_per_player_event": avg_books,
            "avg_lines_per_player_event": avg_lines,
            "avg_distinct_lines_per_pebs": avg_lines_pebs,
            "consensus_avail": m["cons_avail"],
            "consensus_rate":  (m["cons_avail"]/m["rows"]) if m["rows"] else None,
            "pair_candidates": m["pair_candidates"],
            "pair_present":    m["pair_present"],
            "pair_rate":       (m["pair_present"]/m["pair_candidates"]
                                  if m["pair_candidates"] else None),
            "top5_books":      m["book_counter"].most_common(5),
            "top5_player_ids": [p for p, _ in m["player_counter"].most_common(5)],
        })
    rows.sort(key=lambda r: -r["rows"])

    # Per-player top table (across all stats they appear in)
    player_summary: List[Dict[str, Any]] = []
    aggregated: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "rows": 0, "events": set(), "books": set(), "stats": set(),
        "lines": set(),
    })
    for (pid, _stat), bp in by_player.items():
        agg = aggregated[pid]
        agg["rows"]   += bp["rows"]
        agg["events"] |= bp["events"]
        agg["books"]  |= bp["books"]
        agg["stats"]  |= bp["stats"]
        agg["lines"]  |= bp["lines"]
    for pid, bp in aggregated.items():
        player_summary.append({
            "player_id":   pid,
            "player_name": player_name.get(pid, ""),
            "rows":        bp["rows"],
            "distinct_events": len(bp["events"]),
            "distinct_books":  len(bp["books"]),
            "distinct_stats":  len(bp["stats"]),
            "distinct_lines":  len(bp["lines"]),
        })
    player_summary.sort(key=lambda r: -r["rows"])

    client.close()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filter": {"league": league, "sport": sport,
                    "start_date": start_date, "end_date": end_date},
        "total_props": total_props,
        "rows":         rows,
        "top_players":  player_summary[:200],
    }


def emit(rep: Dict[str, Any]) -> Dict[str, str]:
    ensure_audit_dir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    paths = {
        "markets": f"{AUDIT_DIR}/player_prop_summary_{stamp}.csv",
        "players": f"{AUDIT_DIR}/player_prop_top_players_{stamp}.csv",
        "json":    f"{AUDIT_DIR}/player_prop_summary_{stamp}.json",
    }
    mh = ["league_id", "sport_id", "stat_id", "rows",
          "distinct_players", "distinct_events", "distinct_books",
          "avg_books_per_player_event", "avg_lines_per_player_event",
          "avg_distinct_lines_per_pebs", "consensus_avail", "consensus_rate",
          "pair_candidates", "pair_present", "pair_rate",
          "top5_books", "top5_player_ids"]
    write_csv(paths["markets"], mh,
              [[r.get(h) if not isinstance(r.get(h), (list, tuple))
                else str(r.get(h)) for h in mh] for r in rep["rows"]])
    ph = ["player_id", "player_name", "rows", "distinct_events",
          "distinct_books", "distinct_stats", "distinct_lines"]
    write_csv(paths["players"], ph,
              [[r.get(h) for h in ph] for r in rep["top_players"]])
    write_json(paths["json"], rep)
    return paths


def pretty(rep: Dict[str, Any], top_n: int = 30) -> None:
    print("=" * 100)
    print(f"  SGO PLAYER-PROP MARKET SUMMARY — filter={rep['filter']}  "
          f"total_player_prop_rows={rep['total_props']}")
    print("=" * 100)
    print(f"  {'league':<6s} {'stat':<26s} {'rows':>7s} {'players':>7s} "
          f"{'events':>6s} {'books':>5s} {'avg_books':>9s} "
          f"{'avg_lines':>9s} {'pair%':>6s} {'cons%':>6s}")
    for r in rep["rows"][:top_n]:
        print(f"  {r['league_id'] or '-':<6s} {str(r['stat_id'])[:26]:<26s} "
              f"{r['rows']:>7d} {r['distinct_players']:>7d} "
              f"{r['distinct_events']:>6d} {r['distinct_books']:>5d} "
              f"{(r['avg_books_per_player_event'] or 0):>9.2f} "
              f"{(r['avg_lines_per_player_event'] or 0):>9.2f} "
              f"{fmt_pct(r['pair_rate']):>6s} "
              f"{fmt_pct(r['consensus_rate']):>6s}")
    if rep["top_players"]:
        print(f"\n-- Top 15 players by row count --")
        print(f"  {'player_id':<24s} {'player_name':<26s} {'rows':>6s} "
              f"{'events':>6s} {'books':>5s} {'stats':>5s} {'lines':>5s}")
        for p in rep["top_players"][:15]:
            print(f"  {p['player_id'][:24]:<24s} "
                  f"{(p['player_name'] or '')[:26]:<26s} {p['rows']:>6d} "
                  f"{p['distinct_events']:>6d} {p['distinct_books']:>5d} "
                  f"{p['distinct_stats']:>5d} {p['distinct_lines']:>5d}")


async def amain(args: argparse.Namespace) -> int:
    rep = await build(league=args.league, sport=args.sport,
                       start_date=args.start, end_date=args.end)
    pretty(rep, top_n=args.top)
    paths = emit(rep)
    print("\nOutputs:")
    for k, v in paths.items():
        print(f"  {k:<8s} → {v}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--league", default=None)
    p.add_argument("--sport",  default=None)
    p.add_argument("--start",  default=None)
    p.add_argument("--end",    default=None)
    p.add_argument("--top",    type=int, default=30)
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
