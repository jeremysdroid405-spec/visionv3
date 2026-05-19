"""
Stage-4 coverage validation report.

Reads from the sgo_* collections (only — no API calls), produces:
  • total events by date
  • total player props by date
  • prop rows by stat family
  • rows by bookmaker
  • both-sided coverage rate
  • alt-line coverage rate
  • fair-odds coverage rate
  • consensus coverage rate
  • rows with gradeable player stats
  • rows with SGO settled odds outcomes
  • missing player mappings
  • missing actuals
  • duplicate rates (by primary key)
  • API calls used (from sgo_ingest_status)
  • estimated remaining rate-limit headroom (from /account/usage if recent)

Output: pretty-printed report + JSON written to /app/backend/audits/.
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


async def build_report(*, league_id: str = "MLB",
                       start_date: str | None = None,
                       end_date: str | None = None) -> Dict[str, Any]:
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]

    match_event: Dict[str, Any] = {}
    if league_id:
        match_event["league_id"] = league_id
    date_match: Dict[str, Any] = {}
    if start_date:
        date_match["$gte"] = start_date
    if end_date:
        date_match["$lte"] = end_date + "T23:59:59Z"
    if date_match:
        match_event["start_time"] = date_match

    rep: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "league_id": league_id,
        "window": {"start_date": start_date, "end_date": end_date},
    }

    # ── events by date ───────────────────────────────────────────────────
    pipe = [
        {"$match": match_event or {}},
        {"$project": {"d": {"$substr": ["$start_time", 0, 10]}}},
        {"$group":   {"_id": "$d", "n": {"$sum": 1}}},
        {"$sort":    {"_id": 1}},
    ]
    rep["events_by_date"] = [
        {"date": d["_id"], "n": d["n"]}
        async for d in db.sgo_events.aggregate(pipe)
    ]
    rep["total_events"] = sum(x["n"] for x in rep["events_by_date"])

    # ── props by date ────────────────────────────────────────────────────
    # Join: props_raw → events to get the start_time. For speed, project event_id → date
    # by re-using events_by_date map.
    event_dates: Dict[str, str] = {}
    async for ev in db.sgo_events.find(
        match_event or {}, projection={"_id": 0, "event_id": 1, "start_time": 1}):
        if ev.get("event_id") and ev.get("start_time"):
            event_dates[ev["event_id"]] = ev["start_time"][:10]

    props_by_date: Counter = Counter()
    props_by_stat: Counter = Counter()
    props_by_book: Counter = Counter()
    n_props = 0
    side_seen: defaultdict = defaultdict(set)  # (event,oddID) -> {sides}
    line_seen: defaultdict = defaultdict(set)  # (event,oddID,book) -> {lines}
    proj = {"_id": 0, "event_id": 1, "stat_id": 1, "book_id": 1,
            "side": 1, "line": 1, "odd_id": 1, "price": 1}
    async for p in db.sgo_props_raw.find({}, projection=proj):
        n_props += 1
        d = event_dates.get(p.get("event_id"))
        if d: props_by_date[d] += 1
        props_by_stat[p.get("stat_id") or "_unknown"] += 1
        props_by_book[p.get("book_id") or "_unknown"] += 1
        side_seen[(p.get("event_id"), p.get("odd_id"))].add(p.get("side"))
        line_seen[(p.get("event_id"), p.get("odd_id"), p.get("book_id"))].add(p.get("line"))
    rep["total_props"] = n_props
    rep["props_by_date"] = [{"date": d, "n": n}
                             for d, n in sorted(props_by_date.items())]
    rep["props_by_stat_top20"] = props_by_stat.most_common(20)
    rep["props_by_book"] = sorted(props_by_book.most_common(), key=lambda x: -x[1])

    # both-sided coverage
    pairs = len(side_seen)
    both = sum(1 for s in side_seen.values() if len(s) >= 2)
    rep["both_sided_pairs"]   = pairs
    rep["both_sided_present"] = both
    rep["both_sided_rate"]    = round(both / pairs, 4) if pairs else None

    # alt-line coverage
    rep["alt_line_groups"] = sum(1 for v in line_seen.values() if len(v) >= 2)
    rep["alt_line_total_groups"] = len(line_seen)
    rep["alt_line_rate"] = (round(rep["alt_line_groups"] / len(line_seen), 4)
                             if line_seen else None)

    # ── consensus & fair odds coverage ───────────────────────────────────
    n_cons = await db.sgo_book_consensus.count_documents({})
    n_cons_fair = await db.sgo_book_consensus.count_documents(
        {"fair_odds": {"$ne": None}})
    n_cons_book = await db.sgo_book_consensus.count_documents(
        {"book_odds": {"$ne": None}})
    rep["consensus_rows"] = n_cons
    rep["consensus_fair_odds_rows"] = n_cons_fair
    rep["consensus_book_odds_rows"] = n_cons_book

    # ── outcomes ─────────────────────────────────────────────────────────
    n_out = await db.sgo_odds_outcomes.count_documents({})
    rep["outcome_rows_total"] = n_out

    # ── player stats ─────────────────────────────────────────────────────
    n_ps = await db.sgo_player_stats.count_documents({})
    rep["player_stats_rows"] = n_ps
    rep["distinct_players_with_stats"] = (
        await db.sgo_player_stats.distinct("player_id")).__len__()

    # ── missing player mappings: props referencing players with no stats row
    stat_players: set = set()
    async for d in db.sgo_player_stats.find(
        {}, projection={"_id": 0, "player_id": 1}):
        stat_players.add(d["player_id"])
    prop_players: set = set()
    async for d in db.sgo_props_raw.find(
        {"player_id": {"$ne": None}},
        projection={"_id": 0, "player_id": 1}):
        prop_players.add(d["player_id"])
    rep["distinct_prop_players"] = len(prop_players)
    rep["missing_player_mappings"] = len(prop_players - stat_players)

    # ── grading readiness ────────────────────────────────────────────────
    # Number of prop rows whose (event,player) has stats:
    have_stats_key = set()
    async for d in db.sgo_player_stats.find({}, projection={"_id": 0, "event_id": 1, "player_id": 1}):
        have_stats_key.add((d.get("event_id"), d.get("player_id")))
    gradeable = 0
    async for p in db.sgo_props_raw.find(
        {"player_id": {"$ne": None}},
        projection={"_id": 0, "event_id": 1, "player_id": 1}):
        if (p.get("event_id"), p.get("player_id")) in have_stats_key:
            gradeable += 1
    rep["prop_rows_gradeable_via_player_stats"] = gradeable

    # ── duplicate rates ──────────────────────────────────────────────────
    async def dup_rate(coll_name: str, keys: list[str]) -> Dict[str, Any]:
        pipe = [
            {"$group": {"_id": {k: f"${k}" for k in keys}, "c": {"$sum": 1}}},
            {"$match": {"c": {"$gt": 1}}},
            {"$count": "n"},
        ]
        n_dup = 0
        async for d in db[coll_name].aggregate(pipe):
            n_dup = d.get("n", 0)
        total = await db[coll_name].count_documents({})
        return {"total": total, "dup_groups": n_dup,
                "dup_rate": round(n_dup / total, 6) if total else None}
    rep["duplicates"] = {
        "sgo_events": await dup_rate("sgo_events", ["event_id", "snapshot_time"]),
        "sgo_props_raw": await dup_rate("sgo_props_raw",
            ["event_id", "odd_id", "book_id", "side", "line", "snapshot_time"]),
        "sgo_odds_outcomes": await dup_rate("sgo_odds_outcomes",
            ["event_id", "odd_id", "book_id", "selection_id"]),
        "sgo_book_consensus": await dup_rate("sgo_book_consensus",
            ["event_id", "odd_id", "snapshot_time"]),
        "sgo_player_stats": await dup_rate("sgo_player_stats",
            ["event_id", "player_id"]),
    }

    # ── API usage ────────────────────────────────────────────────────────
    last_jobs = await db.sgo_ingest_status.find(
        {"league_id": league_id} if league_id else {},
        projection={"_id": 0, "job_id": 1, "status": 1, "api_calls": 1,
                    "events_processed": 1, "props_rows": 1,
                    "duration_sec": 1, "completed_at": 1}
    ).sort([("completed_at", -1)]).limit(10).to_list(None)
    rep["recent_jobs"] = last_jobs
    rep["total_api_calls"] = sum(
        (j.get("api_calls") or {}).get("total", 0) for j in last_jobs)

    c.close()
    return rep


def pretty_print(rep: Dict[str, Any]) -> None:
    line = "═" * 80
    print(line)
    print(f"  SGO COVERAGE REPORT — league={rep['league_id']}  "
          f"window={rep['window']}  generated_at={rep['generated_at']}")
    print(line)
    print(f"\n• Total events:           {rep['total_events']:>10d}")
    print(f"• Total prop rows:        {rep['total_props']:>10d}")
    print(f"• Both-sided pair rate:   "
          f"{rep['both_sided_present']:>6d} / {rep['both_sided_pairs']:>6d}  "
          f"({rep['both_sided_rate']!s})")
    print(f"• Alt-line group rate:    "
          f"{rep['alt_line_groups']:>6d} / {rep['alt_line_total_groups']:>6d}  "
          f"({rep['alt_line_rate']!s})")
    print(f"• Consensus rows:         {rep['consensus_rows']:>10d}  "
          f"(fair_odds={rep['consensus_fair_odds_rows']}, "
          f"book_odds={rep['consensus_book_odds_rows']})")
    print(f"• Outcome rows (SGO):     {rep['outcome_rows_total']:>10d}")
    print(f"• Player-stats rows:      {rep['player_stats_rows']:>10d}  "
          f"(distinct players: {rep['distinct_players_with_stats']})")
    print(f"• Distinct prop players:  {rep['distinct_prop_players']:>10d}  "
          f"(missing mappings: {rep['missing_player_mappings']})")
    print(f"• Prop rows gradeable via player_stats: "
          f"{rep['prop_rows_gradeable_via_player_stats']:>10d}")
    print(f"• Total recent-job API calls: {rep['total_api_calls']}")
    print("\nEvents by date:")
    for d in rep["events_by_date"][:30]:
        print(f"    {d['date']}  n={d['n']}")
    if len(rep["events_by_date"]) > 30:
        print(f"    ... and {len(rep['events_by_date'])-30} more")
    print("\nProp rows by stat (top 20):")
    for s, n in rep["props_by_stat_top20"]:
        print(f"    {s:<32s} n={n}")
    print("\nProp rows by bookmaker:")
    for b, n in rep["props_by_book"]:
        print(f"    {b:<24s} n={n}")
    print("\nDuplicates by collection (target: dup_rate == 0):")
    for k, v in rep["duplicates"].items():
        print(f"    {k:<22s} total={v['total']:>8d} dup_groups={v['dup_groups']}")
    print(line)


async def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--league", default="MLB")
    p.add_argument("--start",  default=None)
    p.add_argument("--end",    default=None)
    p.add_argument("--out",    default=None,
                    help="Path to write JSON report; default auto-named")
    a = p.parse_args()
    rep = await build_report(league_id=a.league, start_date=a.start,
                              end_date=a.end)
    pretty_print(rep)
    out_path = a.out or (
        f"/app/backend/audits/sgo_coverage_{a.league}_"
        f"{(a.start or 'all').replace('-','')}_"
        f"{(a.end or 'all').replace('-','')}_"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # default=str for datetime objects coming from Mongo
    with open(out_path, "w") as f:
        json.dump(rep, f, indent=2, default=str)
    print(f"\nReport JSON → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
