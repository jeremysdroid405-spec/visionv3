"""
build_team_historical_outcomes.py — Phase 1 Team Outcome Builder.

PURPOSE
    Grade historical team props from `team_historical_props` (MLB / NBA)
    and `nfl_historical_props` (NFL) into a unified
    `team_historical_outcomes` collection.

    Phase-1 scope (markets):
      • h2h / moneyline      (betTypeID = 'ml', 'ml3way')
      • spreads              (betTypeID = 'sp')
      • game totals          (betTypeID = 'ou', statEntityID = 'all')
      • team totals          (betTypeID = 'ou', statEntityID = 'home'|'away')

    Out of scope for Phase 1:
      • alternate-line markets (`is_alternate=true` rows are SKIPPED)
      • period markets (1q / 1h / 1i)  → SKIPPED (only periodID='game')
      • other stat_ids (firstToScore, firstTo10, etc.) → SKIPPED
      • models, projections, edge, CV, hit-rates, scoring (later phases)

SCORE SOURCE
    Reads `home_score` and `away_score` from `team_matchups` (MLB / NBA)
    or `nfl_matchups` (NFL), joined on event_id. If those fields are
    not present on the matchup doc, the outcome row is written as
    UNRESOLVED with `unresolved_reason = "no_final_score"`. This makes
    the builder forward-compatible: the moment the matchup ingest
    starts persisting final scores, the same code will resolve them.

IDEMPOTENCY
    Unique key on the output collection:
        (event_id, team_id, market_key, line, side, book)
    Same tuple → same row. Re-runs are safe.

CONSTRAINTS (per Phase-1 contract)
    • Reads from existing collections only. Never writes to props /
      matchups / scores / live routing.
    • Does NOT touch any player-model code path.
    • No NCAAF (filtered out by sport allowlist).
    • No SGO live routing changes.

USAGE
    # Dry-run (no writes; prints counts + per-sport / per-market resolution)
    python -m scripts.sgo.build_team_historical_outcomes --dry-run

    # Live for all three sports
    python -m scripts.sgo.build_team_historical_outcomes

    # One sport at a time
    python -m scripts.sgo.build_team_historical_outcomes --sport nfl
    python -m scripts.sgo.build_team_historical_outcomes --sport mlb
    python -m scripts.sgo.build_team_historical_outcomes --sport nba

    # Optional date window (inclusive on both ends, ISO 'YYYY-MM-DD')
    python -m scripts.sgo.build_team_historical_outcomes \
        --start 2024-08-01 --end 2024-12-31 --sport nfl
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, UpdateOne

from scripts.sgo._index_utils import ensure_indexes as _shared_ensure_indexes
from scripts.sgo._team_outcome_graders import grade_row


GRADING_VERSION = "team_v1"
DEST_COLL       = "team_historical_outcomes"

SUPPORTED_BET_TYPES = ("ml", "ml3way", "sp", "ou")
SUPPORTED_SPORTS    = ("mlb", "nba", "nfl")

# Per-sport source mapping.
SPORT_SOURCES: Dict[str, Tuple[str, str]] = {
    # sport → (props_coll, matchups_coll)
    "mlb": ("team_historical_props", "team_matchups"),
    "nba": ("team_historical_props", "team_matchups"),
    "nfl": ("nfl_historical_props",  "nfl_matchups"),
}


# ───── pure helpers ─────
def _phase1_props_filter(sport: str) -> Dict[str, Any]:
    """Mongo filter that selects ONLY Phase-1 markets for a sport."""
    return {
        "sport":        sport,
        "betTypeID":    {"$in": list(SUPPORTED_BET_TYPES)},
        "periodID":     "game",     # Phase 1: only full-game markets
        "is_alternate": {"$ne": True},  # Phase 1: no alt-lines
    }


def _market_category(bet_type_id: str, stat_entity_id: Optional[str]) -> str:
    """Bucket for telemetry: h2h / spread / game_total / team_total."""
    bt = (bet_type_id or "").lower()
    if bt in ("ml", "ml3way"):
        return "h2h"
    if bt == "sp":
        return "spread"
    if bt == "ou":
        ent = (stat_entity_id or "").lower()
        if ent == "all":
            return "game_total"
        if ent in ("home", "away"):
            return "team_total"
        return "unknown_ou"
    return "unknown"


# ───── score-source resolver ─────
async def _load_score_index(
    db: AsyncIOMotorDatabase, sport: str, matchups_coll: str,
) -> Dict[str, Dict[str, Any]]:
    """Build {event_id → {home_score, away_score, home_team_id,
                          away_team_id, game_date, …}} for a sport.

    Reads only the fields we need. Tolerant to multiple shapes — SGO
    may ship scores under `home_score`/`away_score` OR
    `homeTeam.score`/`awayTeam.score` OR `final_score.home`/`final_score.away`.
    Whichever it is, we surface a uniform {home_score, away_score}."""
    out: Dict[str, Dict[str, Any]] = {}
    sport_filter = ({"sport": sport}
                     if sport != "nfl" else {"$or": [
                        {"sport": "nfl"}, {"league": "NFL"}]})
    cursor = db[matchups_coll].find(sport_filter, {
        "_id": 0, "event_id": 1,
        "home_team_id": 1, "away_team_id": 1,
        "home_team_name": 1, "away_team_name": 1,
        "game_date": 1, "commence_time": 1, "status": 1,
        "home_score": 1, "away_score": 1,
        "homeTeam": 1, "awayTeam": 1,
        "final_score": 1, "scores": 1, "results": 1,
    })
    async for d in cursor:
        eid = d.get("event_id")
        if not eid:
            continue
        # Resolve scores from any of the known shapes
        hs = d.get("home_score")
        as_ = d.get("away_score")
        if hs is None:
            ht = d.get("homeTeam") or {}
            hs = ht.get("score") if isinstance(ht, dict) else None
        if as_ is None:
            at = d.get("awayTeam") or {}
            as_ = at.get("score") if isinstance(at, dict) else None
        if hs is None or as_ is None:
            fs = d.get("final_score") or d.get("scores") or {}
            if isinstance(fs, dict):
                hs = hs if hs is not None else fs.get("home")
                as_ = as_ if as_ is not None else fs.get("away")
        out[eid] = {
            "home_score":    hs,
            "away_score":    as_,
            "home_team_id":  d.get("home_team_id"),
            "away_team_id":  d.get("away_team_id"),
            "home_team_name": d.get("home_team_name"),
            "away_team_name": d.get("away_team_name"),
            "game_date":     d.get("game_date"),
            "status":        d.get("status"),
        }
    return out


# ───── indexes ─────
async def _ensure_dest_indexes(db: AsyncIOMotorDatabase) -> None:
    await _shared_ensure_indexes(db[DEST_COLL], [
        {"keys": [("event_id", ASCENDING), ("team_id", ASCENDING),
                    ("market_key", ASCENDING), ("line", ASCENDING),
                    ("side", ASCENDING), ("book", ASCENDING)],
            "unique": True, "name": "team_outcome_pk"},
        {"keys": "sport",               "name": "sport_1"},
        {"keys": "game_date",           "name": "game_date_1"},
        {"keys": "team_id",             "name": "team_id_1"},
        {"keys": "betTypeID",           "name": "betTypeID_1"},
        {"keys": "market_category",     "name": "market_category_1"},
        {"keys": "outcome",             "name": "outcome_1"},
        {"keys": "outcome_resolved",    "name": "outcome_resolved_1"},
        {"keys": "grading_version",     "name": "grading_version_1"},
    ])


# ───── core loop ─────
async def process_sport(
    db: AsyncIOMotorDatabase, *, sport: str,
    start: Optional[str], end: Optional[str], dry_run: bool,
) -> Dict[str, Any]:
    props_coll, matchups_coll = SPORT_SOURCES[sport]
    print(f"\n  [{sport.upper()}] grading from {props_coll}, "
          f"scores from {matchups_coll}")

    # Build the score index ONCE per sport
    print(f"  [{sport.upper()}] loading score index from "
          f"{matchups_coll}…")
    scores = await _load_score_index(db, sport, matchups_coll)
    print(f"  [{sport.upper()}] score index size: {len(scores):,} events")
    n_with_scores = sum(1 for v in scores.values()
                          if v["home_score"] is not None
                          and v["away_score"] is not None)
    print(f"  [{sport.upper()}] events with final scores: "
          f"{n_with_scores:,}  ({_pct(n_with_scores, len(scores)):.2f}%)")

    # Phase-1 prop filter (+ optional date window)
    match = _phase1_props_filter(sport)
    if start or end:
        gd: Dict[str, Any] = {}
        if start: gd["$gte"] = start
        if end:   gd["$lte"] = end
        match["game_date"] = gd

    counters = {
        "scanned":         0,
        "skipped_non_phase1_bettype": 0,
        "graded_resolved": 0,
        "graded_push":     0,
        "graded_win":      0,
        "graded_loss":     0,
        "unresolved":      0,
        "no_final_score":  0,
        "no_matchup":      0,
        "upserts_buffered": 0,
        "upserts_written": 0,
    }
    reason_counts: Counter = Counter()
    market_cat_counts: Counter = Counter()
    market_cat_resolved: Counter = Counter()

    BATCH = 1000
    buf: List[UpdateOne] = []
    last_log = 0

    cursor = db[props_coll].find(match, {
        "_id": 0, "event_id": 1, "team_id": 1, "sport": 1,
        "game_date": 1, "commence_time": 1,
        "market": 1, "market_key": 1, "market_name": 1,
        "betTypeID": 1, "statID": 1, "statEntityID": 1,
        "periodID": 1, "side": 1, "sideID": 1,
        "line": 1, "odds": 1, "book": 1, "home_away": 1,
        "is_alternate": 1,
    }).batch_size(2000)

    async for p in cursor:
        counters["scanned"] += 1
        bt = (p.get("betTypeID") or "").lower()
        if bt not in SUPPORTED_BET_TYPES:
            counters["skipped_non_phase1_bettype"] += 1
            continue
        eid = p.get("event_id")
        match_info = scores.get(eid)
        if match_info is None:
            counters["no_matchup"] += 1
            outcome = {
                "outcome": "UNRESOLVED", "outcome_resolved": False,
                "outcome_numeric": None, "hit": None, "push": None,
                "actual_value": None, "margin_vs_line": None,
                "unresolved_reason": "no_matchup",
            }
            opponent = None
        else:
            hs = match_info["home_score"]
            as_ = match_info["away_score"]
            outcome = grade_row(p, hs, as_)
            if (outcome.get("unresolved_reason") == "missing_score"
                    and (hs is None or as_ is None)):
                outcome["unresolved_reason"] = "no_final_score"
                counters["no_final_score"] += 1
            # opponent derivation
            tid = p.get("team_id")
            if tid == match_info.get("home_team_id"):
                opponent = match_info.get("away_team_id")
            elif tid == match_info.get("away_team_id"):
                opponent = match_info.get("home_team_id")
            else:
                opponent = None

        cat = _market_category(bt, p.get("statEntityID"))
        market_cat_counts[cat] += 1
        if outcome["outcome_resolved"]:
            counters["graded_resolved"] += 1
            market_cat_resolved[cat] += 1
            if   outcome["outcome"] == "WIN":  counters["graded_win"] += 1
            elif outcome["outcome"] == "LOSS": counters["graded_loss"] += 1
            elif outcome["outcome"] == "PUSH": counters["graded_push"] += 1
        else:
            counters["unresolved"] += 1
            reason_counts[outcome.get("unresolved_reason") or "unknown"] += 1

        # Build doc
        doc = {
            "sport":             p.get("sport"),
            "event_id":          eid,
            "game_date":         p.get("game_date"),
            "commence_time":     p.get("commence_time"),
            "team_id":           p.get("team_id"),
            "opponent_team_id":  opponent,
            "home_away":         p.get("home_away"),
            "market":            p.get("market"),
            "market_key":        p.get("market_key"),
            "market_name":       p.get("market_name"),
            "market_category":   cat,
            "betTypeID":         p.get("betTypeID"),
            "statID":            p.get("statID"),
            "statEntityID":      p.get("statEntityID"),
            "periodID":          p.get("periodID"),
            "side":              p.get("side"),
            "sideID":            p.get("sideID"),
            "line":              p.get("line"),
            "odds":              p.get("odds"),
            "book":              p.get("book"),
            "is_alternate":      p.get("is_alternate"),
            # outcome fields (flat — matches player outcomes shape)
            **outcome,
            # grading provenance
            "grading_version":   GRADING_VERSION,
            "graded_at":         datetime.now(timezone.utc),
            "home_score_used":   (match_info or {}).get("home_score"),
            "away_score_used":   (match_info or {}).get("away_score"),
        }
        filt = {
            "event_id":   eid,
            "team_id":    p.get("team_id"),
            "market_key": p.get("market_key"),
            "line":       p.get("line"),
            "side":       p.get("side"),
            "book":       p.get("book"),
        }
        buf.append(UpdateOne(filt, {"$set": doc}, upsert=True))
        counters["upserts_buffered"] += 1
        if not dry_run and len(buf) >= BATCH:
            r = await db[DEST_COLL].bulk_write(buf, ordered=False)
            counters["upserts_written"] += (
                (r.upserted_count or 0) + (r.modified_count or 0))
            buf = []

        # Periodic progress log
        if counters["scanned"] - last_log >= 100000:
            print(f"    [{sport.upper()}] scanned={counters['scanned']:,}  "
                  f"resolved={counters['graded_resolved']:,}  "
                  f"unresolved={counters['unresolved']:,}")
            last_log = counters["scanned"]

    if buf and not dry_run:
        r = await db[DEST_COLL].bulk_write(buf, ordered=False)
        counters["upserts_written"] += (
            (r.upserted_count or 0) + (r.modified_count or 0))

    return {
        "sport":                  sport,
        "counters":               counters,
        "reason_counts":          dict(reason_counts),
        "market_cat_counts":      dict(market_cat_counts),
        "market_cat_resolved":    dict(market_cat_resolved),
    }


def _pct(a: int, b: int) -> float:
    return (100.0 * a / b) if b else 0.0


def _print_sport_summary(r: Dict[str, Any]) -> None:
    sport = r["sport"]
    c = r["counters"]
    print()
    print(f"  ── {sport.upper()} SUMMARY ──")
    print(f"     scanned (phase-1 markets):  {c['scanned']:,}")
    print(f"     resolved:                   {c['graded_resolved']:,}  "
          f"({_pct(c['graded_resolved'], c['scanned']):.2f}%)")
    print(f"        win:                     {c['graded_win']:,}")
    print(f"        loss:                    {c['graded_loss']:,}")
    print(f"        push:                    {c['graded_push']:,}")
    print(f"     unresolved:                 {c['unresolved']:,}  "
          f"({_pct(c['unresolved'], c['scanned']):.2f}%)")
    print(f"        no_matchup:              {c['no_matchup']:,}")
    print(f"        no_final_score:          {c['no_final_score']:,}")
    print(f"     upserts written:            {c['upserts_written']:,}")
    if r["market_cat_counts"]:
        print("     per market_category resolution:")
        for cat, n in sorted(r["market_cat_counts"].items()):
            res = r["market_cat_resolved"].get(cat, 0)
            print(f"        {cat:<14s}  total={n:>9,}  "
                  f"resolved={res:>9,}  ({_pct(res, n):.2f}%)")
    if r["reason_counts"]:
        print("     unresolved reasons:")
        for reason, n in sorted(r["reason_counts"].items(),
                                  key=lambda kv: -kv[1]):
            print(f"        {reason:<26s} {n:>9,}")


# ───── main ─────
async def amain(args: argparse.Namespace) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        sports = (
            [args.sport] if args.sport != "all"
            else list(SUPPORTED_SPORTS)
        )
        t0 = datetime.now(timezone.utc)
        print(f"[{t0.isoformat()}] build_team_historical_outcomes  "
              f"version={GRADING_VERSION}")
        print(f"  sports={sports}  start={args.start}  end={args.end}  "
              f"dry_run={args.dry_run}")
        print(f"  destination: {DEST_COLL}")
        print("  CONTRACT: Phase 1 — h2h / spreads / game_totals / "
              "team_totals on periodID=game, no alt-lines.")
        if not args.dry_run:
            await _ensure_dest_indexes(db)

        all_results: List[Dict[str, Any]] = []
        for sp in sports:
            if sp not in SUPPORTED_SPORTS:
                print(f"  [WARN] unsupported sport {sp!r}; skipping.")
                continue
            r = await process_sport(
                db, sport=sp,
                start=args.start, end=args.end,
                dry_run=args.dry_run)
            _print_sport_summary(r)
            all_results.append(r)

        # Grand totals
        print()
        print("=" * 72)
        print("  build_team_historical_outcomes  GRAND TOTALS")
        print("=" * 72)
        tot = Counter()
        for r in all_results:
            for k, v in r["counters"].items():
                tot[k] += v
        for k in ("scanned", "graded_resolved", "graded_win",
                    "graded_loss", "graded_push", "unresolved",
                    "no_matchup", "no_final_score",
                    "upserts_written"):
            print(f"  {k:<22s} {tot.get(k, 0):,}")
        if tot["scanned"]:
            print(f"\n  overall resolution rate: "
                  f"{_pct(tot['graded_resolved'], tot['scanned']):.2f}%")
        if args.dry_run:
            print("\n  DRY-RUN — no writes performed.")
        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        print(f"\n  runtime: {elapsed:.1f}s")
    finally:
        client.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sport", choices=["mlb", "nba", "nfl", "all"],
                    default="all")
    p.add_argument("--start", default=None,
                    help="Optional inclusive start date 'YYYY-MM-DD'.")
    p.add_argument("--end",   default=None,
                    help="Optional inclusive end date 'YYYY-MM-DD'.")
    p.add_argument("--dry-run", action="store_true")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
