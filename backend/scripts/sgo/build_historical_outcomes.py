"""
build_historical_outcomes.py — derive sgo_pp_research_outcomes.

Reads:   sgo_pp_research_core_enriched   (immutable; never mutated)
         sgo_player_stats                (immutable reference; never mutated)
Writes:  sgo_pp_research_outcomes        (idempotent upserts)

Joins every enriched PP-anchored prop with the actual player stat for the
same game, resolves composite stats (PRA, fantasyScore, hits+runs+rbi, etc.)
via a pluggable stat-resolver registry, and stamps:
    actual_value, outcome (WIN|LOSS|PUSH|UNRESOLVED), outcome_numeric
    (1|0|0.5|None), hit, push, margin_vs_line, outcome_resolved, resolved_at,
    grading_version, stat_family

OOM-safe:
    Chunked by game_date. Each date loads sgo_player_stats for that date
    into a {(event_id, player_id): stats_dict} map once, then streams enriched
    docs for the same date, grades, bulk_write upserts in batches of 1000.

Idempotent / resumable:
    Unique key (event_id, player_id, stat_id, side, line, period_id).
    --resume skips docs already at the current GRADING_VERSION.

Usage:
    python -m scripts.sgo.build_historical_outcomes \\
        --league MLB --start 2025-06-01 --end 2025-06-30
    python -m scripts.sgo.build_historical_outcomes --dry-run
    python -m scripts.sgo.build_historical_outcomes --drop-existing --yes
    python -m scripts.sgo.build_historical_outcomes --resume
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")  # preview fallback
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import UpdateOne, ASCENDING

SRC_COLL     = "sgo_pp_research_core_enriched"
STATS_COLL   = "sgo_player_stats"
OUT_COLL     = "sgo_pp_research_outcomes"
GRADING_VERSION = "v1"


# ───────────────────────────── stat resolver registry ─────────────────────
def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _g(stats: Dict[str, Any], *keys: str) -> Any:
    """First-match across multiple key variants (case-insensitive fallback)."""
    if not stats:
        return None
    for k in keys:
        if k in stats and stats[k] is not None:
            return stats[k]
    lower_map = {k.lower(): v for k, v in stats.items()
                  if isinstance(k, str)}
    for k in keys:
        v = lower_map.get(k.lower())
        if v is not None:
            return v
    return None


def _sum_or_none(*vals: Optional[float]) -> Optional[float]:
    if any(v is None for v in vals):
        return None
    return float(sum(vals))


# Each resolver: stats_dict -> Optional[float]
STAT_RESOLVERS: Dict[str, Callable[[Dict[str, Any]], Optional[float]]] = {
    # ─── MLB batting (atomic) ───
    "batting_hits":          lambda s: _num(_g(s, "hits", "batting_hits", "H")),
    "batting_runs":          lambda s: _num(_g(s, "runs", "batting_runs", "R")),
    "batting_rbi":           lambda s: _num(_g(s, "rbi", "RBI", "batting_rbi")),
    "batting_homeRuns":      lambda s: _num(_g(s, "homeRuns", "home_runs",
                                                "HR", "hr", "batting_homeRuns")),
    "batting_totalBases":    lambda s: _num(_g(s, "totalBases", "total_bases",
                                                "TB", "batting_totalBases")),
    "batting_strikeouts":    lambda s: _num(_g(s, "batting_strikeouts",
                                                "strikeouts", "K", "SO")),
    "batting_walks":         lambda s: _num(_g(s, "walks", "batting_walks", "BB")),
    "batting_stolenBases":   lambda s: _num(_g(s, "stolenBases", "stolen_bases",
                                                "SB")),
    "batting_singles":       lambda s: _num(_g(s, "singles", "1B")),
    "batting_doubles":       lambda s: _num(_g(s, "doubles", "2B")),
    "batting_triples":       lambda s: _num(_g(s, "triples", "3B")),
    # ─── MLB pitching ───
    "pitcher_strikeouts":    lambda s: _num(_g(s, "pitcher_strikeouts",
                                                "pitching_strikeouts",
                                                "strikeoutsPitched", "SO")),
    "pitcher_hits_allowed":  lambda s: _num(_g(s, "pitcher_hits_allowed",
                                                "hitsAllowed", "hits_allowed")),
    "pitching_outs":         lambda s: _num(_g(s, "pitching_outs", "outs",
                                                "outsPitched")),
    "pitcher_earned_runs":   lambda s: _num(_g(s, "earnedRuns", "earned_runs",
                                                "ER", "pitcher_earned_runs")),
    "pitcher_walks":         lambda s: _num(_g(s, "pitcher_walks",
                                                "walksAllowed", "walks_allowed")),
    # ─── MLB composites ───
    "hits_runs_rbis":        lambda s: _sum_or_none(
        _num(_g(s, "hits", "H")),
        _num(_g(s, "runs", "R")),
        _num(_g(s, "rbi", "RBI"))),
    "fantasyScore":          lambda s: _num(_g(s, "fantasyScore",
                                                "fantasy_score",
                                                "fantasyPoints")),
    # ─── NBA atomic ───
    "points":                lambda s: _num(_g(s, "points", "PTS")),
    "rebounds":              lambda s: _num(_g(s, "rebounds", "REB",
                                                "totalRebounds")),
    "assists":               lambda s: _num(_g(s, "assists", "AST")),
    "steals":                lambda s: _num(_g(s, "steals", "STL")),
    "blocks":                lambda s: _num(_g(s, "blocks", "BLK")),
    "turnovers":             lambda s: _num(_g(s, "turnovers", "TO")),
    "threePointersMade":     lambda s: _num(_g(s, "threePointersMade",
                                                "three_pointers_made", "3PM")),
    # ─── NBA composites ───
    "pts_reb_ast":           lambda s: _sum_or_none(
        _num(_g(s, "points", "PTS")),
        _num(_g(s, "rebounds", "REB", "totalRebounds")),
        _num(_g(s, "assists", "AST"))),
    "pts_reb":               lambda s: _sum_or_none(
        _num(_g(s, "points", "PTS")),
        _num(_g(s, "rebounds", "REB", "totalRebounds"))),
    "pts_ast":               lambda s: _sum_or_none(
        _num(_g(s, "points", "PTS")),
        _num(_g(s, "assists", "AST"))),
    "reb_ast":               lambda s: _sum_or_none(
        _num(_g(s, "rebounds", "REB", "totalRebounds")),
        _num(_g(s, "assists", "AST"))),
}

# stat_family bucket (for telemetry / coverage reports)
STAT_FAMILY: Dict[str, str] = {
    "batting_hits": "hits", "batting_runs": "runs", "batting_rbi": "rbi",
    "batting_homeRuns": "home_runs", "batting_totalBases": "total_bases",
    "batting_strikeouts": "batting_strikeouts", "batting_walks": "batting_walks",
    "batting_stolenBases": "stolen_bases", "batting_singles": "singles",
    "batting_doubles": "doubles", "batting_triples": "triples",
    "pitcher_strikeouts": "pitcher_strikeouts",
    "pitcher_hits_allowed": "pitcher_hits_allowed",
    "pitching_outs": "pitching_outs",
    "pitcher_earned_runs": "pitcher_earned_runs",
    "pitcher_walks": "pitcher_walks",
    "hits_runs_rbis": "hits_runs_rbis",
    "fantasyScore": "fantasy_score",
    "points": "points", "rebounds": "rebounds", "assists": "assists",
    "steals": "steals", "blocks": "blocks", "turnovers": "turnovers",
    "threePointersMade": "threes_made",
    "pts_reb_ast": "pra", "pts_reb": "pts_reb", "pts_ast": "pts_ast",
    "reb_ast": "reb_ast",
}


def resolve_stat_value(
    stat_id: str, raw_stats: Optional[Dict[str, Any]]
) -> Tuple[Optional[float], str]:
    """Return (numeric_value | None, stat_family_canonical_name).

    Falls back to direct lookup by stat_id (and snake/camel variants) when
    the registry has no entry, so unknown stat_ids still grade when the
    stats dict happens to contain a matching key.
    """
    if raw_stats is None:
        return None, STAT_FAMILY.get(stat_id, stat_id or "unknown")
    fn = STAT_RESOLVERS.get(stat_id)
    if fn is not None:
        return fn(raw_stats), STAT_FAMILY.get(stat_id, stat_id)
    # Fallback: direct lookup
    candidates = [stat_id] if stat_id else []
    if stat_id:
        candidates.extend([
            stat_id.lower(),
            stat_id.replace("_", ""),
            stat_id.replace("-", "_"),
        ])
    val = _num(_g(raw_stats, *candidates))
    return val, STAT_FAMILY.get(stat_id, stat_id or "unknown")


# ───────────────────────────── grading ────────────────────────────────────
def grade_outcome(
    side: Optional[str], actual: Optional[float], line: Optional[float]
) -> Dict[str, Any]:
    """Return the outcome dict for a single anchor."""
    if actual is None or line is None or side is None:
        return {
            "actual_value":     actual,
            "outcome":          "UNRESOLVED",
            "outcome_numeric":  None,
            "hit":              None,
            "push":             None,
            "margin_vs_line":   None,
            "outcome_resolved": False,
        }
    side_u = side.upper()
    try:
        line_f = float(line)
        actual_f = float(actual)
    except (TypeError, ValueError):
        return {
            "actual_value":     actual,
            "outcome":          "UNRESOLVED",
            "outcome_numeric":  None,
            "hit":              None, "push": None,
            "margin_vs_line":   None,
            "outcome_resolved": False,
        }
    if actual_f == line_f:
        return {
            "actual_value":     actual_f,
            "outcome":          "PUSH",
            "outcome_numeric":  0.5,
            "hit":              False, "push": True,
            "margin_vs_line":   0.0,
            "outcome_resolved": True,
        }
    if side_u in ("OVER", "YES"):
        won = actual_f > line_f
        margin = actual_f - line_f
    elif side_u in ("UNDER", "NO"):
        won = actual_f < line_f
        margin = line_f - actual_f
    else:
        return {
            "actual_value":     actual_f,
            "outcome":          "UNRESOLVED",
            "outcome_numeric":  None,
            "hit":              None, "push": None,
            "margin_vs_line":   None,
            "outcome_resolved": False,
        }
    return {
        "actual_value":     actual_f,
        "outcome":          "WIN" if won else "LOSS",
        "outcome_numeric":  1 if won else 0,
        "hit":              bool(won),
        "push":             False,
        "margin_vs_line":   margin,
        "outcome_resolved": True,
    }


# ───────────────────────────── indexes ────────────────────────────────────
async def ensure_out_indexes(db: AsyncIOMotorDatabase) -> None:
    c = db[OUT_COLL]
    await c.create_index(
        [("event_id", ASCENDING), ("player_id", ASCENDING),
         ("stat_id", ASCENDING), ("side", ASCENDING),
         ("line", ASCENDING), ("period_id", ASCENDING)],
        unique=True, name="outcome_anchor_pk")
    await c.create_index("league_id")
    await c.create_index("game_date")
    await c.create_index("player_id")
    await c.create_index("stat_id")
    await c.create_index("stat_family")
    await c.create_index("outcome")
    await c.create_index("outcome_resolved")
    await c.create_index("hit")
    await c.create_index("edge_vs_consensus")
    await c.create_index("has_valid_devig")
    await c.create_index("grading_version")


# ───────────────────────────── per-date processing ────────────────────────
async def _distinct_game_dates(
    db: AsyncIOMotorDatabase, *, league: Optional[str],
    start: Optional[str], end: Optional[str],
) -> List[str]:
    match: Dict[str, Any] = {}
    if league: match["league_id"] = league
    if start or end:
        gd: Dict[str, Any] = {}
        if start: gd["$gte"] = start
        if end:   gd["$lte"] = end
        match["game_date"] = gd
    pipeline: List[Dict[str, Any]] = []
    if match: pipeline.append({"$match": match})
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
    # Load stats for this date into {(event_id, player_id): stats_dict}
    stat_match: Dict[str, Any] = {"game_date": game_date}
    if league:
        stat_match["league_id"] = league
    stats_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    async for s in db[STATS_COLL].find(stat_match, {"_id": 0}):
        k = (s.get("event_id"), s.get("player_id"))
        if k[0] and k[1]:
            stats_map[k] = s.get("stats") or {}

    # Optional --resume set
    already_done: set = set()
    if resume and not dry_run:
        async for r in db[OUT_COLL].find(
            {"game_date": game_date,
             "grading_version": GRADING_VERSION},
            projection={"_id": 0, "event_id": 1, "player_id": 1,
                         "stat_id": 1, "side": 1, "line": 1,
                         "period_id": 1, "outcome_resolved": 1}
        ):
            if r.get("outcome_resolved"):
                already_done.add((r.get("event_id"), r.get("player_id"),
                                    r.get("stat_id"),
                                    (r.get("side") or "").upper(),
                                    r.get("line"), r.get("period_id")))

    upserts: List[UpdateOne] = []
    processed = 0
    resolved = 0
    unresolved = 0
    wins = 0; losses = 0; pushes = 0
    skipped = 0
    fam_counts: Dict[str, int] = {}
    sample_docs: List[Dict[str, Any]] = []
    missing_stats = 0  # joined but stats dict didn't carry the stat
    no_player_stats = 0  # no row at all for (event, player)

    src_match: Dict[str, Any] = {"game_date": game_date}
    if league:
        src_match["league_id"] = league

    async for doc in db[SRC_COLL].find(src_match, {"_id": 0}):
        processed += 1
        uid = (doc.get("event_id"), doc.get("player_id"), doc.get("stat_id"),
               (doc.get("side") or "").upper(), doc.get("line"),
               doc.get("period_id"))
        if uid in already_done:
            skipped += 1
            continue

        stats_dict = stats_map.get((doc.get("event_id"), doc.get("player_id")))
        if stats_dict is None:
            no_player_stats += 1
            actual = None
            fam = STAT_FAMILY.get(doc.get("stat_id"), doc.get("stat_id") or "unknown")
        else:
            actual, fam = resolve_stat_value(doc.get("stat_id"), stats_dict)
            if actual is None:
                missing_stats += 1

        outcome = grade_outcome(doc.get("side"), actual, doc.get("line"))

        if outcome["outcome_resolved"]:
            resolved += 1
            if outcome["outcome"] == "WIN":   wins += 1
            elif outcome["outcome"] == "LOSS": losses += 1
            elif outcome["outcome"] == "PUSH": pushes += 1
        else:
            unresolved += 1
        fam_counts[fam] = fam_counts.get(fam, 0) + 1

        merged = {
            **doc,
            **outcome,
            "stat_family":     fam,
            "grading_version": GRADING_VERSION,
            "resolved_at":     datetime.now(timezone.utc),
        }
        merged.pop("_id", None)
        if (outcome["outcome_resolved"] and len(sample_docs) < 2):
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
        "processed":       processed,
        "resolved":        resolved,
        "unresolved":      unresolved,
        "wins":            wins,
        "losses":          losses,
        "pushes":          pushes,
        "skipped_resume":  skipped,
        "no_player_stats": no_player_stats,
        "missing_stats":   missing_stats,
        "fam_counts":      fam_counts,
        "sample_docs":     sample_docs,
    }


# ───────────────────────────── main ───────────────────────────────────────
async def amain(args: argparse.Namespace) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    t0 = time.time()
    print(f"[{datetime.now(timezone.utc).isoformat()}] "
          f"build_historical_outcomes (grading={GRADING_VERSION})")
    print(f"  league={args.league or '(all)'}  "
          f"window=[{args.start or 'all'} .. {args.end or 'all'}]  "
          f"dry_run={args.dry_run}  drop={args.drop_existing}  resume={args.resume}")

    if args.drop_existing:
        if not args.dry_run and not args.yes:
            print(f"  [err] --drop-existing requires --yes (or --dry-run). "
                  f"Refusing to drop {OUT_COLL}.")
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
    print(f"  [plan] {len(dates)} game_dates to process  "
          f"(from {dates[0]} to {dates[-1]})")

    tot: Dict[str, Any] = {
        "dates": 0, "processed": 0, "resolved": 0, "unresolved": 0,
        "wins": 0, "losses": 0, "pushes": 0,
        "skipped_resume": 0, "no_player_stats": 0, "missing_stats": 0,
        "fam_counts": {}, "sample_docs": [],
    }
    log_every = 10_000
    next_log = log_every

    for gd in dates:
        try:
            r = await process_date(
                db, league=args.league, game_date=gd,
                dry_run=args.dry_run, resume=args.resume)
        except Exception as e:
            print(f"    [{gd}] FAILED: {e!r}")
            continue
        tot["dates"] += 1
        for k in ("processed", "resolved", "unresolved", "wins", "losses",
                   "pushes", "skipped_resume", "no_player_stats", "missing_stats"):
            tot[k] += r[k]
        for fam, n in r["fam_counts"].items():
            tot["fam_counts"][fam] = tot["fam_counts"].get(fam, 0) + n
        if r.get("sample_docs") and len(tot["sample_docs"]) < 2:
            tot["sample_docs"].extend(
                r["sample_docs"][:2 - len(tot["sample_docs"])])
        if tot["processed"] >= next_log:
            el = time.time() - t0
            rate = tot["processed"] / el if el > 0 else 0
            print(f"  [{gd}] cumulative processed={tot['processed']:,}  "
                  f"resolved={tot['resolved']:,}  unresolved={tot['unresolved']:,}  "
                  f"W/L/P={tot['wins']:,}/{tot['losses']:,}/{tot['pushes']:,}  "
                  f"rate={rate:,.0f}/s  elapsed={el:,.0f}s")
            next_log += log_every

    runtime = time.time() - t0
    pct = lambda a, b: (100.0 * a / b) if b else 0.0
    print()
    print("=" * 72)
    print(f"  build_historical_outcomes SUMMARY  ({GRADING_VERSION})")
    print("=" * 72)
    print(f"  game_dates processed:    {tot['dates']:,}")
    print(f"  docs processed (input):  {tot['processed']:,}")
    print(f"  resolved:                {tot['resolved']:,}  "
          f"({pct(tot['resolved'], tot['processed']):.2f}%)")
    print(f"  unresolved:              {tot['unresolved']:,}  "
          f"({pct(tot['unresolved'], tot['processed']):.2f}%)")
    print(f"    of which missing player stats row: {tot['no_player_stats']:,}")
    print(f"    of which stat not in player_stats: {tot['missing_stats']:,}")
    print(f"  wins / losses / pushes:  "
          f"{tot['wins']:,} / {tot['losses']:,} / {tot['pushes']:,}")
    if tot["resolved"]:
        print(f"  hit-rate (W / (W+L)):    "
              f"{pct(tot['wins'], tot['wins']+tot['losses']):.2f}%")
    print(f"  skipped (resume):        {tot['skipped_resume']:,}")
    print(f"  runtime:                 {runtime:,.1f}s")

    # stat_family coverage breakdown (sorted descending)
    if tot["fam_counts"]:
        print(f"\n  stat_family coverage (input docs):")
        for fam, n in sorted(tot["fam_counts"].items(),
                              key=lambda kv: -kv[1])[:30]:
            print(f"    {fam:<30s}  {n:,}")

    if tot["sample_docs"]:
        import json
        print(f"\n  Sample graded docs (first {len(tot['sample_docs'])}):")
        for d in tot["sample_docs"]:
            print("    " + "─" * 60)
            d2 = {**d, "books": (d.get("books") or [])[:2]}
            print("    " + json.dumps(d2, indent=2, default=str)
                              .replace("\n", "\n    "))
    print("=" * 72)
    client.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--league", default=None)
    p.add_argument("--start",  default=None, help="YYYY-MM-DD inclusive")
    p.add_argument("--end",    default=None, help="YYYY-MM-DD inclusive")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--drop-existing", action="store_true",
                    help=f"Drop {OUT_COLL} before rebuild (requires --yes)")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--resume", action="store_true",
                    help=f"Skip docs already resolved at grading_version "
                         f"{GRADING_VERSION}")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
