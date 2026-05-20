"""
sgo_consensus_devig_analysis.py — read-only.

For markets where `sgo_book_consensus.fair_odds` and `book_odds` are populated,
analyzes the vig/no-vig gap.

Per market we compute (where data permits):
  • implied_prob(fair_odds)
  • implied_prob(book_odds)
  • spread = book_implied - fair_implied  (signed, in percentage points)
  • abs_spread (|spread|)
  • Two-way de-vigged probabilities for paired markets via sgo_props_raw.opposing_odd_id

Aggregated reports:
  A) Distribution by stat_id           (rows, median spread, p10/p50/p90)
  B) Distribution by book_id           (using props_raw.book_id; one consensus row
                                         can map to many book rows)
  C) Distribution by odds bucket       (using fair_odds bucket)
  D) Anomaly leaderboard: top 50 markets where |spread| > 5pp
  E) Two-way de-vig vs SGO fair_odds:  per pair, |sgo_fair_implied - devig|

Output:
  audits/sgo_analysis/devig_by_stat_<stamp>.csv
  audits/sgo_analysis/devig_by_book_<stamp>.csv
  audits/sgo_analysis/devig_by_bucket_<stamp>.csv
  audits/sgo_analysis/devig_anomalies_<stamp>.csv
  audits/sgo_analysis/devig_twoway_<stamp>.csv
  audits/sgo_analysis/devig_summary_<stamp>.json

Usage:
  python -m scripts.sgo.sgo_consensus_devig_analysis
  python -m scripts.sgo.sgo_consensus_devig_analysis --league MLB --start 2025-06-15 --end 2025-06-15
"""
from __future__ import annotations
import argparse
import asyncio
import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
sys.path.insert(0, "/app/backend")
from scripts.sgo._analysis_common import (
    AUDIT_DIR, ensure_audit_dir, bucket_for, get_db,
    write_csv, write_json, implied_prob, devig_two_way, fmt_pct,
)


def _percentile(xs: List[float], p: float) -> Optional[float]:
    if not xs: return None
    s = sorted(xs)
    idx = max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))
    return s[idx]


async def build(
    *, league: str | None = None, sport: str | None = None,
    start_date: str | None = None, end_date: str | None = None,
) -> Dict[str, Any]:
    client, db = get_db()

    # 1) event filter
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
            "summary": {
                "markets_with_consensus": 0,
                "props_with_consensus":   0,
                "anomaly_count_ge_5pp":   0,
                "twoway_pairs":           0,
            },
            "by_stat": [], "by_book": [], "by_bucket": [],
            "anomalies": [], "twoway": [],
        }

    # 2) consensus index: (event, odd_id) -> {fair_odds, book_odds, cons_prob}
    cons_idx: Dict[Tuple[str, str], Dict[str, Any]] = {}
    async for c in db.sgo_book_consensus.find(
        {"event_id": {"$in": list(eligible)},
         "fair_odds": {"$ne": None}, "book_odds": {"$ne": None}},
        projection={"_id": 0, "event_id": 1, "odd_id": 1,
                    "fair_odds": 1, "book_odds": 1,
                    "consensus_probability": 1, "snapshot_time": 1}):
        k = (c.get("event_id"), c.get("odd_id"))
        prev = cons_idx.get(k)
        if (prev is None or
            (c.get("snapshot_time") or "") > (prev.get("snapshot_time") or "")):
            cons_idx[k] = c

    # 3) walk props_raw to attribute consensus to a stat / book / side / bucket
    by_stat:  Dict[str, List[float]] = defaultdict(list)
    by_book:  Dict[str, List[float]] = defaultdict(list)
    by_bkt:   Dict[str, List[float]] = defaultdict(list)
    anomalies: List[Dict[str, Any]] = []
    seen_markets: set = set()

    # Two-way de-vig — group props by (event, base_market_signature)
    # using opposing_odd_id pairing.
    pair_buf: Dict[Tuple[str, str], Dict[str, Any]] = {}

    proj = {"_id": 0, "event_id": 1, "stat_id": 1, "side": 1,
            "book_id": 1, "line": 1, "price": 1, "odd_id": 1,
            "opposing_odd_id": 1, "player_id": 1, "stat_entity_id": 1,
            "period_id": 1}
    async for p in db.sgo_props_raw.find(
        {"event_id": {"$in": list(eligible)}}, projection=proj):
        c = cons_idx.get((p.get("event_id"), p.get("odd_id")))
        if not c: continue
        fp = implied_prob(c["fair_odds"])
        bp = implied_prob(c["book_odds"])
        if fp is None or bp is None: continue
        spread = bp - fp                                 # signed (pp/100)
        abs_spread = abs(spread)
        stat = p.get("stat_id") or "_unknown"
        book = p.get("book_id") or "_unknown"
        bk   = bucket_for(c["fair_odds"])
        by_stat[stat].append(spread)
        by_book[book].append(spread)
        by_bkt[bk].append(spread)
        market_key = (p.get("event_id"), p.get("odd_id"))
        seen_markets.add(market_key)

        if abs_spread >= 0.05:   # ≥ 5pp absolute gap
            anomalies.append({
                "event_id": p.get("event_id"),
                "odd_id":   p.get("odd_id"),
                "stat_id":  stat,
                "book_id":  book,
                "side":     p.get("side"),
                "line":     p.get("line"),
                "price":    p.get("price"),
                "fair_odds":  c["fair_odds"],
                "book_odds":  c["book_odds"],
                "fair_implied": round(fp, 5),
                "book_implied": round(bp, 5),
                "spread_pp":    round(100*spread, 3),
                "abs_spread_pp": round(100*abs_spread, 3),
            })

        # two-way pair buffer
        opp = p.get("opposing_odd_id")
        if opp:
            base = tuple(sorted([p.get("odd_id"), opp]))
            slot = pair_buf.setdefault((p.get("event_id"), base), {
                "event_id": p.get("event_id"),
                "stat_id":  stat,
                "book_id":  book,
                "line":     p.get("line"),
                "yes_fair_prob": None, "no_fair_prob": None,
                "yes_book_prob": None, "no_book_prob": None,
                "yes_side": None, "no_side": None,
            })
            side = p.get("side")
            if side in ("over", "yes", "side1") and slot["yes_fair_prob"] is None:
                slot["yes_fair_prob"] = fp
                slot["yes_book_prob"] = bp
                slot["yes_side"] = side
            elif slot["no_fair_prob"] is None:
                slot["no_fair_prob"] = fp
                slot["no_book_prob"] = bp
                slot["no_side"] = side

    # Build per-stat / per-book / per-bucket summaries
    def summarize(buckets: Dict[str, List[float]]) -> List[Dict[str, Any]]:
        out = []
        for k, xs in buckets.items():
            if not xs: continue
            out.append({
                "key": k,
                "rows": len(xs),
                "mean_spread_pp": round(100*statistics.fmean(xs), 3),
                "median_spread_pp": round(100*statistics.median(xs), 3),
                "p10_pp": round(100*_percentile(xs, 0.10), 3),
                "p90_pp": round(100*_percentile(xs, 0.90), 3),
                "max_abs_pp": round(100*max(abs(x) for x in xs), 3),
                "min_pp":     round(100*min(xs), 3),
            })
        out.sort(key=lambda r: -r["rows"])
        return out

    by_stat_rows   = summarize(by_stat)
    by_book_rows   = summarize(by_book)
    by_bucket_rows = summarize(by_bkt)
    anomalies.sort(key=lambda r: -r["abs_spread_pp"])

    # Finalize two-way de-vig table
    twoway: List[Dict[str, Any]] = []
    for (eid, base), s in pair_buf.items():
        devig = devig_two_way(s["yes_book_prob"], s["no_book_prob"])
        if devig is None: continue
        yd, nd = devig
        twoway.append({
            "event_id": eid,
            "stat_id":  s["stat_id"],
            "book_id":  s["book_id"],
            "line":     s["line"],
            "yes_side": s["yes_side"],
            "no_side":  s["no_side"],
            "yes_book_prob":   round(s["yes_book_prob"], 5),
            "no_book_prob":    round(s["no_book_prob"], 5),
            "yes_devig_prob":  round(yd, 5),
            "no_devig_prob":   round(nd, 5),
            "yes_fair_prob":   round(s["yes_fair_prob"], 5)
                                 if s["yes_fair_prob"] is not None else None,
            "no_fair_prob":    round(s["no_fair_prob"], 5)
                                 if s["no_fair_prob"] is not None else None,
            "yes_fair_vs_devig_pp": (round(100*(s["yes_fair_prob"] - yd), 3)
                                      if s["yes_fair_prob"] is not None else None),
            "vig_pp": round(100*(s["yes_book_prob"]+s["no_book_prob"]-1), 3),
        })
    twoway.sort(key=lambda r: -abs(r.get("yes_fair_vs_devig_pp") or 0))

    client.close()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filter": {"league": league, "sport": sport,
                    "start_date": start_date, "end_date": end_date},
        "summary": {
            "markets_with_consensus": len(seen_markets),
            "props_with_consensus":   sum(len(xs) for xs in by_stat.values()),
            "anomaly_count_ge_5pp":   len(anomalies),
            "twoway_pairs":           len(twoway),
        },
        "by_stat":    by_stat_rows,
        "by_book":    by_book_rows,
        "by_bucket":  by_bucket_rows,
        "anomalies":  anomalies[:500],
        "twoway":     twoway[:2000],
    }


def emit(rep: Dict[str, Any]) -> Dict[str, str]:
    ensure_audit_dir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    paths = {
        "by_stat":   f"{AUDIT_DIR}/devig_by_stat_{stamp}.csv",
        "by_book":   f"{AUDIT_DIR}/devig_by_book_{stamp}.csv",
        "by_bucket": f"{AUDIT_DIR}/devig_by_bucket_{stamp}.csv",
        "anomalies": f"{AUDIT_DIR}/devig_anomalies_{stamp}.csv",
        "twoway":    f"{AUDIT_DIR}/devig_twoway_{stamp}.csv",
        "json":      f"{AUDIT_DIR}/devig_summary_{stamp}.json",
    }
    header = ["key", "rows", "mean_spread_pp", "median_spread_pp",
              "p10_pp", "p90_pp", "max_abs_pp", "min_pp"]
    for k in ("by_stat", "by_book", "by_bucket"):
        write_csv(paths[k], header, [[r.get(h) for h in header]
                                      for r in rep[k]])
    write_csv(paths["anomalies"],
        ["event_id","odd_id","stat_id","book_id","side","line","price",
         "fair_odds","book_odds","fair_implied","book_implied",
         "spread_pp","abs_spread_pp"],
        [[a[k] for k in ["event_id","odd_id","stat_id","book_id","side",
                          "line","price","fair_odds","book_odds",
                          "fair_implied","book_implied","spread_pp",
                          "abs_spread_pp"]] for a in rep["anomalies"]])
    write_csv(paths["twoway"],
        ["event_id","stat_id","book_id","line","yes_side","no_side",
         "yes_book_prob","no_book_prob","yes_devig_prob","no_devig_prob",
         "yes_fair_prob","no_fair_prob","yes_fair_vs_devig_pp","vig_pp"],
        [[t[k] for k in ["event_id","stat_id","book_id","line","yes_side",
                          "no_side","yes_book_prob","no_book_prob",
                          "yes_devig_prob","no_devig_prob","yes_fair_prob",
                          "no_fair_prob","yes_fair_vs_devig_pp","vig_pp"]]
         for t in rep["twoway"]])
    write_json(paths["json"], rep)
    return paths


def pretty(rep: Dict[str, Any]) -> None:
    print("=" * 100)
    print(f"  SGO DEVIG ANALYSIS — filter={rep['filter']}")
    print(f"  markets_with_consensus={rep['summary']['markets_with_consensus']}  "
          f"props_with_consensus={rep['summary']['props_with_consensus']}  "
          f"anomalies≥5pp={rep['summary']['anomaly_count_ge_5pp']}  "
          f"twoway_pairs={rep['summary']['twoway_pairs']}")
    print("=" * 100)
    print("\n-- By stat_id (top 15) --")
    print(f"  {'stat':<28s} {'rows':>7s} {'mean_pp':>8s} {'med_pp':>8s} "
          f"{'p10':>7s} {'p90':>7s} {'max|':>7s}")
    for r in rep["by_stat"][:15]:
        print(f"  {str(r['key'])[:28]:<28s} {r['rows']:>7d} "
              f"{r['mean_spread_pp']:>8.3f} {r['median_spread_pp']:>8.3f} "
              f"{r['p10_pp']:>7.2f} {r['p90_pp']:>7.2f} {r['max_abs_pp']:>7.2f}")
    print("\n-- By book_id (top 15) --")
    for r in rep["by_book"][:15]:
        print(f"  {str(r['key'])[:28]:<28s} {r['rows']:>7d} "
              f"{r['mean_spread_pp']:>8.3f} {r['median_spread_pp']:>8.3f}")
    print("\n-- By odds bucket --")
    for r in rep["by_bucket"]:
        print(f"  {str(r['key'])[:28]:<28s} {r['rows']:>7d} "
              f"{r['mean_spread_pp']:>8.3f} {r['median_spread_pp']:>8.3f}")
    if rep["anomalies"]:
        print(f"\n-- Top 10 spread anomalies (|spread|≥5pp) --")
        for a in rep["anomalies"][:10]:
            print(f"  {a['stat_id']:<22s} {a['book_id']:<12s} "
                  f"side={a['side']:<5s} line={a['line']!s:<6s} "
                  f"fair={a['fair_odds']!s:<5s} book={a['book_odds']!s:<5s} "
                  f"spread_pp={a['spread_pp']}")


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
