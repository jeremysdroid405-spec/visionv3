"""
sgo_bucket_analysis.py — read-only.

Buckets every row in `sgo_props_raw` by American-odds band and reports per
(league, sport, stat_id, side, book_id, bucket):
  • total rows
  • fair_odds available rate         (from sgo_book_consensus.fair_odds)
  • book_odds available rate         (from sgo_book_consensus.book_odds)
  • consensus_probability available  (from sgo_book_consensus)
  • opposing-side present rate       (props_raw.opposing_odd_id ∈ same scope)
  • alt-line group present rate      (>1 distinct line under same group key)

Output:
  audits/sgo_analysis/bucket_analysis_<stamp>.csv
  audits/sgo_analysis/bucket_analysis_<stamp>.json

Usage:
  python -m scripts.sgo.sgo_bucket_analysis
  python -m scripts.sgo.sgo_bucket_analysis --league MLB --start 2025-06-15 --end 2025-06-15
"""
from __future__ import annotations
import argparse
import asyncio
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Set, Tuple
sys.path.insert(0, "/app/backend")
from scripts.sgo._analysis_common import (
    AUDIT_DIR, ensure_audit_dir, bucket_for, BUCKET_LABELS,
    get_db, write_csv, write_json, fmt_pct, fmt_num,
)


async def build(
    *, league: str | None = None, sport: str | None = None,
    start_date: str | None = None, end_date: str | None = None,
) -> Dict[str, Any]:
    client, db = get_db()
    # Restrict to events matching league/sport/date window
    match_event: Dict[str, Any] = {}
    if league:
        match_event["league_id"] = league
    if sport:
        match_event["sport_id"] = sport
    if start_date or end_date:
        gd: Dict[str, Any] = {}
        if start_date:
            gd["$gte"] = start_date
        if end_date:
            gd["$lte"] = end_date
        match_event["game_date"] = gd

    event_meta: Dict[str, Dict[str, str]] = {}
    async for ev in db.sgo_events.find(
        match_event or {},
        projection={"_id": 0, "event_id": 1, "league_id": 1, "sport_id": 1,
                    "game_date": 1}):
        eid = ev.get("event_id")
        if eid:
            event_meta[eid] = {
                "league_id": ev.get("league_id"),
                "sport_id":  ev.get("sport_id"),
                "game_date": ev.get("game_date"),
            }
    eligible_events: Set[str] = set(event_meta)
    if not eligible_events:
        client.close()
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "filter": {"league": league, "sport": sport,
                        "start_date": start_date, "end_date": end_date},
            "total_props": 0, "row_count": 0, "rows": [],
        }

    # Index consensus rows by (event_id, odd_id) — latest snapshot wins
    cons_idx: Dict[Tuple[str, str], Dict[str, Any]] = {}
    async for c in db.sgo_book_consensus.find(
        {"event_id": {"$in": list(eligible_events)}},
        projection={"_id": 0, "event_id": 1, "odd_id": 1,
                    "fair_odds": 1, "book_odds": 1,
                    "consensus_probability": 1, "snapshot_time": 1}):
        k = (c.get("event_id"), c.get("odd_id"))
        prev = cons_idx.get(k)
        if (prev is None or
            (c.get("snapshot_time") or "") > (prev.get("snapshot_time") or "")):
            cons_idx[k] = c

    # Single scan of sgo_props_raw — bucket aggregator
    Counter = Dict[Tuple, Dict[str, Any]]
    agg: Counter = defaultdict(lambda: {
        "rows": 0, "fair_odds": 0, "book_odds": 0, "cons_prob": 0,
        "opp_present": 0, "alt_groups_total": 0, "alt_groups_multi": 0,
    })
    # For alt-line group detection: track distinct lines per group key
    alt_buf: Dict[Tuple, Set] = defaultdict(set)
    # For opposing-side detection: index of seen odd_ids by (event, book, snapshot)
    odd_index: Dict[Tuple, Set[str]] = defaultdict(set)
    pair_candidates_per_group: Dict[Tuple, list] = defaultdict(list)

    proj = {"_id": 0, "event_id": 1, "stat_id": 1, "side": 1, "line": 1,
            "price": 1, "book_id": 1, "odd_id": 1, "opposing_odd_id": 1,
            "player_id": 1, "stat_entity_id": 1, "period_id": 1,
            "snapshot_time": 1}
    n_seen = 0
    async for p in db.sgo_props_raw.find(
        {"event_id": {"$in": list(eligible_events)}}, projection=proj):
        n_seen += 1
        eid = p.get("event_id")
        meta = event_meta.get(eid, {})
        bk = bucket_for(p.get("price"))
        key = (meta.get("league_id"), meta.get("sport_id"),
               p.get("stat_id"), p.get("side"), p.get("book_id"), bk)
        a = agg[key]
        a["rows"] += 1
        # consensus availability for this market
        c = cons_idx.get((eid, p.get("odd_id")))
        if c:
            if c.get("fair_odds") is not None: a["fair_odds"] += 1
            if c.get("book_odds") is not None: a["book_odds"] += 1
            if c.get("consensus_probability") is not None: a["cons_prob"] += 1
        # alt-line groups: (event, player/entity, stat, side, book, period)
        ent = p.get("player_id") or p.get("stat_entity_id") or "_no_ent"
        alt_key = (eid, ent, p.get("stat_id"), p.get("side"),
                   p.get("book_id"), p.get("period_id"))
        line = p.get("line")
        if line is not None:
            alt_buf[alt_key].add(line)
        # remember mapping to bucket for later finalization
        # also index odd_ids for opposing-side resolution
        snap = p.get("snapshot_time")
        idx_key = (eid, p.get("book_id"), snap)
        if p.get("odd_id"):
            odd_index[idx_key].add(p["odd_id"])
        if p.get("opposing_odd_id"):
            pair_candidates_per_group[key].append((
                idx_key, p["opposing_odd_id"]))

    # Finalize opposing-side counts per bucket-key
    for key, cands in pair_candidates_per_group.items():
        a = agg[key]
        for idx_key, opp in cands:
            if opp in odd_index.get(idx_key, ()):
                a["opp_present"] += 1
    # Finalize alt-line group counts: map each alt_key to the bucket_key it
    # contributes to. We use the FIRST props_raw row's bucket — group size
    # itself is bucket-agnostic (same player×stat×side×book), but we attribute
    # to the bucket with the largest membership to avoid double-counting.
    # Cheap approach: walk alt_buf and attribute each group once to its
    # most-common bucket via a second scan.
    # (For groups with single line, len(lines)==1 → not a multi-line group.)
    # We track which bucket_keys a group "belongs to":
    group_buckets: Dict[Tuple, Dict[Tuple, int]] = defaultdict(
        lambda: defaultdict(int))
    # second scan would be expensive; instead attribute by a lookup pass:
    # we already have alt_buf keyed by (eid, ent, stat, side, book, period).
    # For attribution we need the dominant bucket per group. Approximate:
    # use bucket from one sample row. Track during main scan:
    # (re-scan minimal projection grouped by alt_key)
    async for p in db.sgo_props_raw.find(
        {"event_id": {"$in": list(eligible_events)}},
        projection={"_id": 0, "event_id": 1, "player_id": 1,
                    "stat_entity_id": 1, "stat_id": 1, "side": 1,
                    "book_id": 1, "period_id": 1, "price": 1}):
        meta = event_meta.get(p.get("event_id"), {})
        ent = p.get("player_id") or p.get("stat_entity_id") or "_no_ent"
        alt_key = (p.get("event_id"), ent, p.get("stat_id"), p.get("side"),
                   p.get("book_id"), p.get("period_id"))
        if alt_key not in alt_buf:
            continue
        bk = bucket_for(p.get("price"))
        bk_key = (meta.get("league_id"), meta.get("sport_id"),
                  p.get("stat_id"), p.get("side"), p.get("book_id"), bk)
        group_buckets[alt_key][bk_key] += 1

    # Attribute each group to its dominant bucket
    for alt_key, bcounts in group_buckets.items():
        dominant_bk_key = max(bcounts.items(), key=lambda kv: kv[1])[0]
        agg[dominant_bk_key]["alt_groups_total"] += 1
        if len(alt_buf[alt_key]) > 1:
            agg[dominant_bk_key]["alt_groups_multi"] += 1

    # Materialize rows
    rows: list[dict] = []
    for (lg, sp, stat, side, book, bk), a in agg.items():
        rows.append({
            "league_id": lg, "sport_id": sp, "stat_id": stat,
            "side": side, "book_id": book, "odds_bucket": bk,
            "rows": a["rows"],
            "fair_odds_avail":   a["fair_odds"],
            "fair_odds_rate":    (a["fair_odds"]/a["rows"]) if a["rows"] else None,
            "book_odds_avail":   a["book_odds"],
            "book_odds_rate":    (a["book_odds"]/a["rows"]) if a["rows"] else None,
            "cons_prob_avail":   a["cons_prob"],
            "cons_prob_rate":    (a["cons_prob"]/a["rows"]) if a["rows"] else None,
            "opp_present":       a["opp_present"],
            "opp_present_rate":  (a["opp_present"]/a["rows"]) if a["rows"] else None,
            "alt_groups_total":  a["alt_groups_total"],
            "alt_groups_multi":  a["alt_groups_multi"],
            "alt_line_rate":     (a["alt_groups_multi"]/a["alt_groups_total"]
                                  if a["alt_groups_total"] else None),
        })
    rows.sort(key=lambda r: (-r["rows"], r["league_id"] or "",
                              r["stat_id"] or "", r["side"] or "",
                              r["book_id"] or "", r["odds_bucket"]))
    client.close()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filter": {"league": league, "sport": sport,
                    "start_date": start_date, "end_date": end_date},
        "total_props": n_seen,
        "row_count": len(rows),
        "rows": rows,
    }


def emit(rep: Dict[str, Any]) -> tuple[str, str]:
    ensure_audit_dir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    csv_path  = f"{AUDIT_DIR}/bucket_analysis_{stamp}.csv"
    json_path = f"{AUDIT_DIR}/bucket_analysis_{stamp}.json"
    header = ["league_id", "sport_id", "stat_id", "side", "book_id",
              "odds_bucket", "rows", "fair_odds_avail", "fair_odds_rate",
              "book_odds_avail", "book_odds_rate", "cons_prob_avail",
              "cons_prob_rate", "opp_present", "opp_present_rate",
              "alt_groups_total", "alt_groups_multi", "alt_line_rate"]
    csv_rows = [[r.get(h) for h in header] for r in rep["rows"]]
    write_csv(csv_path, header, csv_rows)
    write_json(json_path, rep)
    return csv_path, json_path


def pretty(rep: Dict[str, Any], top_n: int = 30) -> None:
    print("=" * 100)
    print(f"  SGO BUCKET ANALYSIS — filter={rep['filter']}  "
          f"total_props={rep['total_props']}  buckets={rep['row_count']}")
    print("=" * 100)
    hdr = (f"  {'league':<6s} {'stat':<26s} {'side':<6s} {'book':<14s} "
           f"{'bucket':<14s} {'rows':>7s} {'fair%':>7s} {'cons%':>7s} "
           f"{'opp%':>7s} {'alt%':>7s}")
    print(hdr)
    for r in rep["rows"][:top_n]:
        print(f"  {r['league_id'] or '-':<6s} {str(r['stat_id'])[:26]:<26s} "
              f"{str(r['side'])[:6]:<6s} {str(r['book_id'])[:14]:<14s} "
              f"{r['odds_bucket']:<14s} {r['rows']:>7d} "
              f"{fmt_pct(r['fair_odds_rate']):>7s} "
              f"{fmt_pct(r['cons_prob_rate']):>7s} "
              f"{fmt_pct(r['opp_present_rate']):>7s} "
              f"{fmt_pct(r['alt_line_rate']):>7s}")
    if len(rep["rows"]) > top_n:
        print(f"  ... and {len(rep['rows']) - top_n} more rows in CSV")


async def amain(args: argparse.Namespace) -> int:
    rep = await build(league=args.league, sport=args.sport,
                       start_date=args.start, end_date=args.end)
    pretty(rep, top_n=args.top)
    csv_path, json_path = emit(rep)
    print(f"\nCSV  → {csv_path}")
    print(f"JSON → {json_path}")
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
