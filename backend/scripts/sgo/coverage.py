"""
Stage-4 coverage validation report.

Reads from the sgo_* collections (only — no API calls) and produces:
  • Total events by date            (uses sgo_events.game_date)
  • Total player props by date      (joined via sgo_events.game_date)
  • Prop rows by stat family / bookmaker
  • Player-identity registry coverage
      = distinct sgo_props_raw.player_id (real player IDs only) vs sgo_players
      → reported INDEPENDENTLY of player_stats
  • Both-sided pair rate
      → uses sgo_props_raw.opposing_odd_id; pair is "present" when the
        opposing odd exists for the same (event_id, book_id, snapshot_time)
  • Alt-line group rate
      → groups by (event_id, player_id|stat_entity_id, stat_id, side,
                   book_id, period_id), counts groups with >1 distinct line
  • Fair-odds / book-odds / consensus coverage
  • Outcome-row counts (SGO-settled grading data)
  • Player-stats gradeability (kept separate; reported as 0/unavailable when
    no player-stats endpoint is wired)
  • Duplicate rates (by primary key)
  • Recent API calls used (from sgo_ingest_status)

Output: pretty-printed report + JSON written to /app/backend/audits/.
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


# Player-ID values that are not real persons; never count them as
# "missing mappings". Compared case-insensitively against the suffix.
_NON_PERSON_PLAYER_IDS: Set[str] = {
    "", "home", "away", "all", "side1", "side2", "team_home", "team_away",
    "both", "none", "any", "tie",
}


def _is_real_player_id(pid: Optional[str]) -> bool:
    if pid is None:
        return False
    s = str(pid).strip()
    if not s:
        return False
    if s.lower() in _NON_PERSON_PLAYER_IDS:
        return False
    return True


async def build_report(
    *,
    league_id: str = "MLB",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]

    # ── event filter (uses game_date now) ───────────────────────────────
    match_event: Dict[str, Any] = {}
    if league_id:
        match_event["league_id"] = league_id
    if start_date or end_date:
        gd: Dict[str, Any] = {}
        if start_date:
            gd["$gte"] = start_date
        if end_date:
            gd["$lte"] = end_date
        match_event["game_date"] = gd

    rep: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "league_id": league_id,
        "window": {"start_date": start_date, "end_date": end_date},
    }

    # ── events by date (group on game_date) ─────────────────────────────
    pipe_events = [
        {"$match": match_event or {}},
        {"$group": {"_id": "$game_date", "n": {"$sum": 1}}},
        {"$sort":  {"_id": 1}},
    ]
    rep["events_by_date"] = [
        {"date": d["_id"], "n": d["n"]}
        async for d in db.sgo_events.aggregate(pipe_events)
        if d["_id"]
    ]
    rep["total_events"] = sum(x["n"] for x in rep["events_by_date"])

    # Build event_id → game_date map (used to bucket props by date)
    event_dates: Dict[str, str] = {}
    async for ev in db.sgo_events.find(
        match_event or {},
        projection={"_id": 0, "event_id": 1, "game_date": 1},
    ):
        eid = ev.get("event_id")
        gd  = ev.get("game_date")
        if eid and gd:
            event_dates[eid] = gd

    # ── single full scan of sgo_props_raw — compute everything in memory
    # We project narrowly; sgo_props_raw can be very large.
    proj = {
        "_id": 0, "event_id": 1, "stat_id": 1, "book_id": 1,
        "side": 1, "line": 1, "odd_id": 1, "opposing_odd_id": 1,
        "player_id": 1, "stat_entity_id": 1, "period_id": 1,
        "snapshot_time": 1,
    }
    props_by_date: Counter = Counter()
    props_by_stat: Counter = Counter()
    props_by_book: Counter = Counter()
    distinct_prop_players: Set[str] = set()
    distinct_prop_players_raw: Set[str] = set()  # incl. non-person tokens
    n_props = 0

    # alt-line groups
    alt_group: defaultdict[Tuple, Set] = defaultdict(set)
    # both-sided pairs — key by (event_id, book_id, snapshot_time)
    odd_index: defaultdict[Tuple, Set[str]] = defaultdict(set)
    pair_candidates: List[Tuple] = []  # (event_id, book_id, snapshot_time, odd_id, opposing_odd_id)

    async for p in db.sgo_props_raw.find({}, projection=proj):
        n_props += 1
        eid = p.get("event_id")
        d = event_dates.get(eid)
        if d:
            props_by_date[d] += 1
        props_by_stat[p.get("stat_id") or "_unknown"] += 1
        props_by_book[p.get("book_id") or "_unknown"] += 1

        pid_raw = p.get("player_id")
        if pid_raw is not None:
            distinct_prop_players_raw.add(str(pid_raw))
        if _is_real_player_id(pid_raw):
            distinct_prop_players.add(str(pid_raw))

        # alt-line grouping: (event, player_or_entity, stat, side, book, period)
        ent = p.get("player_id") or p.get("stat_entity_id") or "_no_ent"
        alt_key = (eid, ent, p.get("stat_id"), p.get("side"),
                   p.get("book_id"), p.get("period_id"))
        line = p.get("line")
        if line is not None:
            alt_group[alt_key].add(line)

        # both-sided index: per (event, book, snapshot) → set of seen odd_ids
        snap = p.get("snapshot_time")
        bk = p.get("book_id")
        key = (eid, bk, snap)
        if p.get("odd_id"):
            odd_index[key].add(p["odd_id"])
        if p.get("opposing_odd_id"):
            pair_candidates.append((eid, bk, snap, p.get("odd_id"),
                                    p.get("opposing_odd_id")))

    rep["total_props"] = n_props
    rep["props_by_date"] = [
        {"date": d, "n": n} for d, n in sorted(props_by_date.items())
    ]
    rep["props_by_stat_top20"] = props_by_stat.most_common(20)
    rep["props_by_book"] = sorted(
        props_by_book.most_common(), key=lambda x: -x[1])

    # ── Alt-line group rate ─────────────────────────────────────────────
    total_alt_groups = len(alt_group)
    multi_line_groups = sum(1 for v in alt_group.values() if len(v) > 1)
    rep["alt_line_total_groups"] = total_alt_groups
    rep["alt_line_groups"] = multi_line_groups
    rep["alt_line_rate"] = (
        round(multi_line_groups / total_alt_groups, 4)
        if total_alt_groups else None
    )

    # ── Both-sided pair rate (uses opposing_odd_id) ─────────────────────
    n_candidates = len(pair_candidates)
    n_present = 0
    for eid, bk, snap, _odd, opp in pair_candidates:
        if opp and opp in odd_index.get((eid, bk, snap), ()):
            n_present += 1
    rep["both_sided_pair_candidates"] = n_candidates
    rep["both_sided_pairs_present"]   = n_present
    rep["both_sided_rate"] = (
        round(n_present / n_candidates, 4) if n_candidates else None
    )

    # ── Player-identity registry coverage ───────────────────────────────
    # Distinct REAL player_ids from sgo_props_raw vs sgo_players registry.
    registry_ids: Set[str] = set()
    async for d in db.sgo_players.find(
        {}, projection={"_id": 0, "player_id": 1}):
        if d.get("player_id"):
            registry_ids.add(str(d["player_id"]))

    missing_player_ids = sorted(distinct_prop_players - registry_ids)
    rep["distinct_prop_players_raw"]   = len(distinct_prop_players_raw)
    rep["distinct_prop_players"]       = len(distinct_prop_players)
    rep["registry_player_ids"]         = len(registry_ids)
    rep["player_identity_missing"]     = len(missing_player_ids)
    rep["player_identity_coverage_rate"] = (
        round(1 - len(missing_player_ids) / len(distinct_prop_players), 4)
        if distinct_prop_players else None
    )
    rep["player_identity_missing_sample"] = missing_player_ids[:25]

    # ── Consensus / fair-odds coverage ──────────────────────────────────
    n_cons = await db.sgo_book_consensus.count_documents({})
    n_cons_fair = await db.sgo_book_consensus.count_documents(
        {"fair_odds": {"$ne": None}})
    n_cons_book = await db.sgo_book_consensus.count_documents(
        {"book_odds": {"$ne": None}})
    rep["consensus_rows"] = n_cons
    rep["consensus_fair_odds_rows"] = n_cons_fair
    rep["consensus_book_odds_rows"] = n_cons_book

    # ── Settled outcomes ────────────────────────────────────────────────
    rep["outcome_rows_total"] = await db.sgo_odds_outcomes.count_documents({})

    # ── Player-stats gradeability (kept as a separate, optional metric) ─
    n_ps = await db.sgo_player_stats.count_documents({})
    rep["player_stats_rows"] = n_ps
    rep["distinct_players_with_stats"] = (
        len(await db.sgo_player_stats.distinct("player_id")) if n_ps else 0
    )

    if n_ps == 0:
        rep["player_stats_endpoint"] = "unavailable"
        rep["prop_rows_gradeable_via_player_stats"] = 0
        rep["prop_rows_gradeable_note"] = (
            "No player_stats endpoint wired yet — this metric is "
            "intentionally 0 until SGO player-stats ingestion is added. "
            "Identity mapping (above) is unaffected."
        )
    else:
        # Compute gradeability only when player_stats actually exist.
        have_stats_key: Set[Tuple[str, str]] = set()
        async for d in db.sgo_player_stats.find(
            {}, projection={"_id": 0, "event_id": 1, "player_id": 1}):
            have_stats_key.add((d.get("event_id"), d.get("player_id")))
        gradeable = 0
        async for p in db.sgo_props_raw.find(
            {"player_id": {"$ne": None}},
            projection={"_id": 0, "event_id": 1, "player_id": 1},
        ):
            if (p.get("event_id"), p.get("player_id")) in have_stats_key:
                gradeable += 1
        rep["player_stats_endpoint"] = "available"
        rep["prop_rows_gradeable_via_player_stats"] = gradeable

    # ── Duplicate rates ─────────────────────────────────────────────────
    async def dup_rate(coll_name: str, keys: List[str]) -> Dict[str, Any]:
        pipe = [
            {"$group": {"_id": {k: f"${k}" for k in keys}, "c": {"$sum": 1}}},
            {"$match": {"c": {"$gt": 1}}},
            {"$count": "n"},
        ]
        n_dup = 0
        async for d in db[coll_name].aggregate(pipe, allowDiskUse=True):
            n_dup = d.get("n", 0)
        total = await db[coll_name].count_documents({})
        return {"total": total, "dup_groups": n_dup,
                "dup_rate": round(n_dup / total, 6) if total else None}
    rep["duplicates"] = {
        "sgo_events":          await dup_rate("sgo_events",
                                  ["event_id", "snapshot_time"]),
        "sgo_props_raw":       await dup_rate("sgo_props_raw",
                                  ["event_id", "odd_id", "book_id", "side",
                                   "line", "snapshot_time"]),
        "sgo_odds_outcomes":   await dup_rate("sgo_odds_outcomes",
                                  ["event_id", "odd_id", "book_id",
                                   "selection_id"]),
        "sgo_book_consensus":  await dup_rate("sgo_book_consensus",
                                  ["event_id", "odd_id", "snapshot_time"]),
        "sgo_player_stats":    await dup_rate("sgo_player_stats",
                                  ["event_id", "player_id"]),
    }

    # ── Recent API usage ────────────────────────────────────────────────
    last_jobs = await db.sgo_ingest_status.find(
        {"league_id": league_id} if league_id else {},
        projection={
            "_id": 0, "job_id": 1, "status": 1, "api_calls": 1,
            "events_processed": 1, "props_rows": 1,
            "duration_sec": 1, "completed_at": 1,
        },
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
    print(f"\n• Player identity coverage:")
    print(f"    distinct prop player_ids (raw, incl. non-person tokens): "
          f"{rep['distinct_prop_players_raw']}")
    print(f"    distinct prop player_ids (real persons):                 "
          f"{rep['distinct_prop_players']}")
    print(f"    sgo_players registry:                                    "
          f"{rep['registry_player_ids']}")
    print(f"    missing mappings (real persons not in registry):         "
          f"{rep['player_identity_missing']}")
    print(f"    coverage rate:                                           "
          f"{rep['player_identity_coverage_rate']!s}")
    if rep["player_identity_missing_sample"]:
        print(f"    sample missing IDs (first 25): "
              f"{rep['player_identity_missing_sample']}")
    print(f"\n• Both-sided pair rate (opposing_odd_id resolves "
          f"same event/book/snapshot):")
    print(f"    candidates: {rep['both_sided_pair_candidates']}   "
          f"present: {rep['both_sided_pairs_present']}   "
          f"rate: {rep['both_sided_rate']!s}")
    print(f"\n• Alt-line group rate "
          f"(event × player/entity × stat × side × book × period):")
    print(f"    groups: {rep['alt_line_total_groups']}   "
          f"multi-line groups: {rep['alt_line_groups']}   "
          f"rate: {rep['alt_line_rate']!s}")
    print(f"\n• Consensus rows:         {rep['consensus_rows']:>10d}  "
          f"(fair_odds={rep['consensus_fair_odds_rows']}, "
          f"book_odds={rep['consensus_book_odds_rows']})")
    print(f"• Outcome rows (SGO):     {rep['outcome_rows_total']:>10d}")
    print(f"\n• Player-stats endpoint:  {rep.get('player_stats_endpoint')}")
    print(f"    player_stats rows:                  {rep['player_stats_rows']}")
    print(f"    distinct players with stats:        "
          f"{rep['distinct_players_with_stats']}")
    print(f"    prop rows gradeable via player_stats: "
          f"{rep['prop_rows_gradeable_via_player_stats']}")
    if rep.get("prop_rows_gradeable_note"):
        print(f"    note: {rep['prop_rows_gradeable_note']}")
    print(f"\n• Total recent-job API calls: {rep['total_api_calls']}")
    print("\nEvents by date:")
    for d in rep["events_by_date"][:30]:
        print(f"    {d['date']}  n={d['n']}")
    if len(rep["events_by_date"]) > 30:
        print(f"    ... and {len(rep['events_by_date']) - 30} more")
    print("\nProp rows by stat (top 20):")
    for s, n in rep["props_by_stat_top20"]:
        print(f"    {s:<32s} n={n}")
    print("\nProp rows by bookmaker:")
    for b, n in rep["props_by_book"]:
        print(f"    {b:<24s} n={n}")
    print("\nDuplicates by collection (target: dup_rate == 0):")
    for k, v in rep["duplicates"].items():
        print(f"    {k:<22s} total={v['total']:>8d} "
              f"dup_groups={v['dup_groups']}")
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
    rep = await build_report(
        league_id=a.league, start_date=a.start, end_date=a.end)
    pretty_print(rep)
    out_path = a.out or (
        f"/app/backend/audits/sgo_coverage_{a.league}_"
        f"{(a.start or 'all').replace('-','')}_"
        f"{(a.end or 'all').replace('-','')}_"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(rep, f, indent=2, default=str)
    print(f"\nReport JSON → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
