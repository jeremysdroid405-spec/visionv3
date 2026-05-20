"""
sgo_market_depth_analysis.py — read-only.

Quantifies SGO's player-prop market depth ("ladders") which the strict
same-stat alt-line metric undercounts. Real ladders are encoded across
DIFFERENT stat_ids per (player, event), e.g. hits 1.5, singles 0.5,
doubles 0.5, HR 0.5, total_bases 1.5, fantasy_score 7, etc.

Metrics:
  1. player_event_market_depth  — distinct stat_id count per (player, event, side)
  2. player_event_line_depth    — distinct line    count per (player, event, side)
  3. book_market_depth          — distinct stat_id count per (player, event, side, book)
  4. book_line_depth            — distinct line    count per (player, event, side, book)
  5. Top (player, event) pairs by row count
  6. Top stat clusters: most common SETS of stat_ids offered per (player, event)

Outputs:
  audits/sgo_analysis/market_depth_distributions_<stamp>.csv
  audits/sgo_analysis/market_depth_top_player_events_<stamp>.csv
  audits/sgo_analysis/market_depth_top_clusters_<stamp>.csv
  audits/sgo_analysis/market_depth_<stamp>.json

Usage:
  python -m scripts.sgo.sgo_market_depth_analysis
  python -m scripts.sgo.sgo_market_depth_analysis --league MLB --start 2025-06-15 --end 2025-06-15
"""
from __future__ import annotations
import argparse
import asyncio
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
sys.path.insert(0, "/app/backend")
from scripts.sgo._analysis_common import (
    AUDIT_DIR, ensure_audit_dir, get_db, write_csv, write_json,
)
from scripts.sgo.coverage import _is_real_player_id


def _pct(xs: List[int], p: float) -> Optional[float]:
    if not xs: return None
    s = sorted(xs)
    idx = max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))
    return s[idx]


def _summarize_distribution(xs: List[int], name: str) -> Dict[str, Any]:
    if not xs:
        return {"name": name, "n": 0, "mean": None, "median": None,
                "p10": None, "p25": None, "p75": None, "p90": None,
                "max": None, "ge_2_rate": None, "ge_3_rate": None,
                "ge_5_rate": None}
    n = len(xs)
    return {
        "name": name,
        "n": n,
        "mean":   round(statistics.fmean(xs), 3),
        "median": statistics.median(xs),
        "p10":    _pct(xs, 0.10),
        "p25":    _pct(xs, 0.25),
        "p75":    _pct(xs, 0.75),
        "p90":    _pct(xs, 0.90),
        "max":    max(xs),
        "ge_2_rate": round(sum(1 for x in xs if x >= 2) / n, 4),
        "ge_3_rate": round(sum(1 for x in xs if x >= 3) / n, 4),
        "ge_5_rate": round(sum(1 for x in xs if x >= 5) / n, 4),
    }


async def build(
    *, league: str | None = None, sport: str | None = None,
    start_date: str | None = None, end_date: str | None = None,
) -> Dict[str, Any]:
    client, db = get_db()

    # ── event filter
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
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "filter": {"league": league, "sport": sport,
                        "start_date": start_date, "end_date": end_date},
            "total_props": 0, "distributions": [],
            "top_player_events": [], "top_stat_clusters": [],
        }

    # ── load player registry for nicer top-N output
    player_name: Dict[str, str] = {}
    async for d in db.sgo_players.find(
        {}, projection={"_id": 0, "player_id": 1, "player_name": 1}):
        if d.get("player_id"):
            player_name[d["player_id"]] = d.get("player_name") or ""

    # ── single pass over sgo_props_raw, materialize 4 distinct-set keys
    pe_stats:   Dict[Tuple[str, str, str], Set[str]]    = defaultdict(set)
    pe_lines:   Dict[Tuple[str, str, str], Set[float]]  = defaultdict(set)
    peb_stats:  Dict[Tuple[str, str, str, str], Set[str]]   = defaultdict(set)
    peb_lines:  Dict[Tuple[str, str, str, str], Set[float]] = defaultdict(set)
    pe_rows:    Counter = Counter()
    pe_stat_counter: Dict[Tuple[str, str], Counter] = defaultdict(Counter)

    proj = {"_id": 0, "event_id": 1, "stat_id": 1, "side": 1,
            "line": 1, "book_id": 1, "player_id": 1}
    n_props_with_player = 0
    async for p in db.sgo_props_raw.find(
        {"event_id": {"$in": list(eligible)}}, projection=proj):
        pid = p.get("player_id")
        if not _is_real_player_id(pid):
            continue
        n_props_with_player += 1
        eid   = p.get("event_id")
        side  = p.get("side") or "_unknown"
        stat  = p.get("stat_id") or "_unknown"
        book  = p.get("book_id") or "_unknown"
        line  = p.get("line")
        pe_stats[(pid, eid, side)].add(stat)
        if line is not None: pe_lines[(pid, eid, side)].add(line)
        peb_stats[(pid, eid, side, book)].add(stat)
        if line is not None: peb_lines[(pid, eid, side, book)].add(line)
        pe_rows[(pid, eid)] += 1
        pe_stat_counter[(pid, eid)][stat] += 1

    # ── compute distributions
    dists = [
        _summarize_distribution([len(v) for v in pe_stats.values()],
                                  "player_event_market_depth"),
        _summarize_distribution([len(v) for v in pe_lines.values()],
                                  "player_event_line_depth"),
        _summarize_distribution([len(v) for v in peb_stats.values()],
                                  "book_market_depth"),
        _summarize_distribution([len(v) for v in peb_lines.values()],
                                  "book_line_depth"),
    ]

    # ── top (player, event) pairs by row volume
    top_pe = []
    for (pid, eid), n in pe_rows.most_common(50):
        stats_counter = pe_stat_counter[(pid, eid)]
        gd = (event_meta.get(eid) or {}).get("game_date")
        top_pe.append({
            "player_id":  pid,
            "player_name": player_name.get(pid, ""),
            "event_id":   eid,
            "game_date":  gd,
            "rows":       n,
            "distinct_stats": len(stats_counter),
            "top_stats":  stats_counter.most_common(10),
        })

    # ── top stat clusters (frozensets of stat_ids per (player,event), any side)
    cluster_counter: Counter = Counter()
    cluster_example: Dict[frozenset, List[Dict[str, Any]]] = defaultdict(list)
    for (pid, eid), stats_counter in pe_stat_counter.items():
        cluster = frozenset(stats_counter.keys())
        if not cluster: continue
        cluster_counter[cluster] += 1
        if len(cluster_example[cluster]) < 3:
            cluster_example[cluster].append({
                "player_id": pid, "event_id": eid,
                "player_name": player_name.get(pid, ""),
            })
    top_clusters = []
    for cluster, count in cluster_counter.most_common(25):
        top_clusters.append({
            "stat_ids":  sorted(cluster),
            "size":      len(cluster),
            "occurrences": count,
            "examples":  cluster_example[cluster],
        })

    client.close()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filter": {"league": league, "sport": sport,
                    "start_date": start_date, "end_date": end_date},
        "total_player_prop_rows": n_props_with_player,
        "distinct_player_events":    sum(1 for _ in pe_rows.values()),
        "distinct_player_event_sides": len(pe_stats),
        "distributions":             dists,
        "top_player_events":         top_pe,
        "top_stat_clusters":         top_clusters,
    }


def emit(rep: Dict[str, Any]) -> Dict[str, str]:
    ensure_audit_dir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    paths = {
        "dist":      f"{AUDIT_DIR}/market_depth_distributions_{stamp}.csv",
        "top_pe":    f"{AUDIT_DIR}/market_depth_top_player_events_{stamp}.csv",
        "clusters":  f"{AUDIT_DIR}/market_depth_top_clusters_{stamp}.csv",
        "json":      f"{AUDIT_DIR}/market_depth_{stamp}.json",
    }
    write_csv(paths["dist"],
        ["name", "n", "mean", "median", "p10", "p25", "p75", "p90", "max",
         "ge_2_rate", "ge_3_rate", "ge_5_rate"],
        [[d.get(k) for k in ["name", "n", "mean", "median", "p10", "p25",
                              "p75", "p90", "max", "ge_2_rate",
                              "ge_3_rate", "ge_5_rate"]]
         for d in rep["distributions"]])
    write_csv(paths["top_pe"],
        ["player_id", "player_name", "event_id", "game_date", "rows",
         "distinct_stats", "top_stats"],
        [[r.get(k) if not isinstance(r.get(k), (list, tuple))
          else str(r.get(k))
          for k in ["player_id", "player_name", "event_id", "game_date",
                    "rows", "distinct_stats", "top_stats"]]
         for r in rep["top_player_events"]])
    write_csv(paths["clusters"],
        ["size", "occurrences", "stat_ids", "examples"],
        [[r["size"], r["occurrences"], "|".join(r["stat_ids"]),
          str(r["examples"])] for r in rep["top_stat_clusters"]])
    write_json(paths["json"], rep)
    return paths


def pretty(rep: Dict[str, Any]) -> None:
    print("=" * 100)
    print(f"  SGO MARKET DEPTH — filter={rep['filter']}")
    print(f"  total_player_prop_rows={rep['total_player_prop_rows']}  "
          f"distinct_(player,event)_sides={rep['distinct_player_event_sides']}")
    print("=" * 100)
    hdr = ["name", "n", "mean", "median", "p10", "p75", "p90", "max",
           "ge2%", "ge3%", "ge5%"]
    print("  " + "  ".join(f"{h:>{12 if i==0 else 7}s}"
                            for i, h in enumerate(hdr)))
    for d in rep["distributions"]:
        def f(v): return "-" if v is None else f"{v}"
        ge2 = "-" if d["ge_2_rate"] is None else f"{100*d['ge_2_rate']:>5.1f}%"
        ge3 = "-" if d["ge_3_rate"] is None else f"{100*d['ge_3_rate']:>5.1f}%"
        ge5 = "-" if d["ge_5_rate"] is None else f"{100*d['ge_5_rate']:>5.1f}%"
        print(f"  {d['name']:<28s} {d['n']:>7d} {f(d['mean']):>7s} "
              f"{f(d['median']):>7s} {f(d['p10']):>7s} {f(d['p75']):>7s} "
              f"{f(d['p90']):>7s} {f(d['max']):>7s} "
              f"{ge2:>7s} {ge3:>7s} {ge5:>7s}")
    if rep["top_player_events"]:
        print(f"\n-- Top 15 (player, event) pairs by row volume --")
        for r in rep["top_player_events"][:15]:
            stats_str = ", ".join(f"{s}({c})" for s, c in r["top_stats"][:6])
            print(f"  {r['player_id'][:24]:<24s} "
                  f"{(r['player_name'] or '')[:18]:<18s} "
                  f"event={r['event_id'][:12]:<12s} "
                  f"rows={r['rows']:>4d} stats={r['distinct_stats']:>3d}  "
                  f"top: {stats_str}")
    if rep["top_stat_clusters"]:
        print(f"\n-- Top 10 stat clusters offered per (player, event) --")
        for c in rep["top_stat_clusters"][:10]:
            stats = ", ".join(c["stat_ids"])
            print(f"  size={c['size']:>2d}  seen_in={c['occurrences']:>4d} "
                  f"(player,event) pairs  → {stats}")


async def amain(args: argparse.Namespace) -> int:
    rep = await build(league=args.league, sport=args.sport,
                       start_date=args.start, end_date=args.end)
    pretty(rep)
    paths = emit(rep)
    print("\nOutputs:")
    for k, v in paths.items():
        print(f"  {k:<10s} → {v}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--league", default=None)
    p.add_argument("--sport",  default=None)
    p.add_argument("--start",  default=None)
    p.add_argument("--end",    default=None)
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
