"""
backfill_team_matchup_scores.py — populate final scores into team/nfl matchup docs.

PROBLEM
    `build_team_historical_outcomes` is wired and tested, but `nfl_matchups`
    (and `team_matchups`) carry NO `home_score` / `away_score` fields today.
    Without them, every team-prop outcome resolves as
    `unresolved_reason = "no_final_score"`.

THIS SCRIPT
    For each completed event in the matchup collection, fetch the same
    event from SGO `/v2/events?eventID=<id>&expandResults=true` and write
    the final scores back into the matchup doc in-place.

OUTPUT (per matchup row, when scores are found)
    home_score                         numeric
    away_score                         numeric
    final_score: {home, away}          dict mirror
    score_source:        "sgo_event_results"
    score_backfilled_at: utc datetime
    score_backfill_version: "v1"

SAFETY
    • Idempotent. If `home_score`/`away_score` already exist (any non-null
      numeric), the row is SKIPPED by default. Pass `--force` to override.
    • Per-sport scoped (`--sport nfl|mlb|nba`). Default is `nfl` only.
    • Per-row updates (NEVER bulk-replace). All other matchup fields are
      preserved untouched.
    • Never touches the production team outcome builder, live routing,
      player model, or NCAAF.
    • Defaults to dry-run unless `--yes` is passed.

USAGE
    # Probe what would change (no SGO calls if --dry-run-no-sgo is set)
    python -m scripts.sgo.backfill_team_matchup_scores --sport nfl --dry-run

    # Live backfill, NFL only, full window
    python -m scripts.sgo.backfill_team_matchup_scores --sport nfl --yes

    # Specific window
    python -m scripts.sgo.backfill_team_matchup_scores --sport nfl \\
        --start 2024-09-01 --end 2024-12-31 --yes
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
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

from scripts.sgo.client import SGOClient


BACKFILL_VERSION = "v1"
SCORE_SOURCE     = "sgo_event_results"

# sport → (matchups_collection, sgo_league_id)
SPORT_CONFIG: Dict[str, Tuple[str, str]] = {
    "nfl": ("nfl_matchups",  "NFL"),
    "mlb": ("team_matchups", "MLB"),
    "nba": ("team_matchups", "NBA"),
}

COMPLETED_STATUSES = ("completed", "final", "finalized",
                        "Final", "FINAL", "COMPLETED")


# ───── helpers ─────
def _g(d: Dict[str, Any], *ks: str) -> Any:
    for k in ks:
        v = d.get(k)
        if v is not None:
            return v
    return None


def _num(v: Any) -> Optional[float]:
    if v is None: return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def extract_scores_from_sgo_event(ev: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Pull (home_score, away_score) from a /v2/events?expandResults=true
    payload, tolerant to SGO's polymorphic shapes.

    Pure function. Easy to unit-test.
    """
    if not isinstance(ev, dict):
        return None, None
    # Direct top-level keys (camelCase or snake_case)
    hs = _num(_g(ev, "homeScore", "home_score"))
    as_ = _num(_g(ev, "awayScore", "away_score"))
    if hs is not None and as_ is not None:
        return hs, as_
    # Nested under `results`
    res = ev.get("results") or ev.get("result") or {}
    if isinstance(res, dict):
        hs = hs if hs is not None else _num(_g(res, "homeScore", "home_score",
                                                  "homePoints", "home_points"))
        as_ = as_ if as_ is not None else _num(_g(res, "awayScore", "away_score",
                                                    "awayPoints", "away_points"))
        # Nested under `results.scores` / `results.final`
        for k in ("scores", "final", "finalScore", "final_score"):
            sub = res.get(k)
            if isinstance(sub, dict):
                hs = hs if hs is not None else _num(_g(sub, "home", "homeScore"))
                as_ = as_ if as_ is not None else _num(_g(sub, "away", "awayScore"))
    # `final_score` / `scores` directly under event
    for k in ("final_score", "finalScore", "scores"):
        sub = ev.get(k)
        if isinstance(sub, dict):
            hs = hs if hs is not None else _num(_g(sub, "home", "homeScore"))
            as_ = as_ if as_ is not None else _num(_g(sub, "away", "awayScore"))
    # Nested under `homeTeam.score` / `awayTeam.score`
    ht = ev.get("homeTeam") or {}
    at = ev.get("awayTeam") or {}
    if isinstance(ht, dict):
        hs = hs if hs is not None else _num(ht.get("score"))
    if isinstance(at, dict):
        as_ = as_ if as_ is not None else _num(at.get("score"))
    return hs, as_


def _is_score_present(row: Dict[str, Any]) -> bool:
    """True if the matchup row already has BOTH home_score and away_score
    (per the existing builder's resolver semantics)."""
    return (row.get("home_score") is not None
              and row.get("away_score") is not None)


def _completed_filter() -> Dict[str, Any]:
    return {"status": {"$in": list(COMPLETED_STATUSES)}}


# ───── core ─────
async def backfill_sport(
    sgo: SGOClient, db: AsyncIOMotorDatabase, *,
    sport: str, start: Optional[str], end: Optional[str],
    dry_run: bool, force: bool, max_events: int,
) -> Dict[str, Any]:
    coll_name, league_id = SPORT_CONFIG[sport]
    print(f"\n  [{sport.upper()}] backfilling final scores into {coll_name}")
    print(f"  [{sport.upper()}] league_id={league_id}  "
          f"start={start}  end={end}  dry_run={dry_run}  force={force}")

    match: Dict[str, Any] = _completed_filter()
    if sport != "nfl":
        match["sport"] = sport
    else:
        match["$or"] = [{"sport": "nfl"}, {"league": "NFL"}]
    if start or end:
        gd: Dict[str, Any] = {}
        if start: gd["$gte"] = start
        if end:   gd["$lte"] = end
        match["game_date"] = gd

    n_total = await db[coll_name].count_documents(match)
    print(f"  [{sport.upper()}] candidate completed events: {n_total:,}")

    counters = {
        "scanned":             0,
        "already_scored_skip": 0,
        "fetched_from_sgo":    0,
        "scores_found":        0,
        "scores_missing":      0,
        "fetch_errors":        0,
        "updated":             0,
        "dry_run":             dry_run,
    }
    sample_updates: List[Dict[str, Any]] = []

    cursor = db[coll_name].find(match, {
        "_id": 0, "event_id": 1, "home_team_name": 1, "away_team_name": 1,
        "game_date": 1, "home_score": 1, "away_score": 1,
    }).batch_size(200)

    async for m in cursor:
        counters["scanned"] += 1
        if counters["scanned"] > max_events:
            print(f"  [{sport.upper()}] hit --max-events={max_events}; "
                  f"stopping early.")
            break
        eid = m.get("event_id")
        if not eid:
            continue
        # Idempotency gate
        if not force and _is_score_present(m):
            counters["already_scored_skip"] += 1
            continue
        # Fetch from SGO. Method name is `get_event_with_results` —
        # not `get_event`. Returns the event dict (or None if SGO has
        # no record). `expand_results=True` is the canonical flag that
        # makes SGO include the `results` block with final scores.
        try:
            ev = await sgo.get_event_with_results(
                eid, expand_results=True, include_alt_lines=False)
        except Exception as e:
            counters["fetch_errors"] += 1
            if counters["fetch_errors"] <= 3:
                print(f"  [{sport.upper()}] SGO fetch error eid={eid}: {e}")
            continue
        counters["fetched_from_sgo"] += 1
        if not ev:
            counters["scores_missing"] += 1
            continue
        hs, as_ = extract_scores_from_sgo_event(ev)
        if hs is None or as_ is None:
            counters["scores_missing"] += 1
            continue
        counters["scores_found"] += 1
        if len(sample_updates) < 5:
            sample_updates.append({
                "event_id":   eid,
                "home_team":  m.get("home_team_name"),
                "away_team":  m.get("away_team_name"),
                "game_date":  m.get("game_date"),
                "home_score": hs, "away_score": as_,
            })
        if dry_run:
            continue
        # Idempotent in-place update — preserve all other fields.
        update_doc = {
            "$set": {
                "home_score":             hs,
                "away_score":             as_,
                "final_score":            {"home": hs, "away": as_},
                "score_source":           SCORE_SOURCE,
                "score_backfilled_at":    datetime.now(timezone.utc),
                "score_backfill_version": BACKFILL_VERSION,
            },
        }
        r = await db[coll_name].update_one({"event_id": eid}, update_doc)
        counters["updated"] += (r.modified_count or 0)

        # Periodic log
        if counters["scanned"] % 200 == 0:
            print(f"    [{sport.upper()}] scanned={counters['scanned']:,}  "
                  f"updated={counters['updated']:,}  "
                  f"already={counters['already_scored_skip']:,}  "
                  f"missing={counters['scores_missing']:,}  "
                  f"errors={counters['fetch_errors']:,}")

    return {"sport": sport, "coll": coll_name, "counters": counters,
            "sample_updates": sample_updates}


def _print_summary(r: Dict[str, Any]) -> None:
    c = r["counters"]
    print()
    print(f"  ── {r['sport'].upper()} BACKFILL SUMMARY ({r['coll']}) ──")
    print(f"     scanned:               {c['scanned']:,}")
    print(f"     already scored (skip): {c['already_scored_skip']:,}")
    print(f"     fetched from SGO:      {c['fetched_from_sgo']:,}")
    print(f"     scores found:          {c['scores_found']:,}")
    print(f"     scores missing on SGO: {c['scores_missing']:,}")
    print(f"     fetch errors:          {c['fetch_errors']:,}")
    print(f"     rows updated:          {c['updated']:,}  "
          f"({'DRY-RUN' if c['dry_run'] else 'live'})")
    if r["sample_updates"]:
        print("     sample updates (first 5):")
        for s in r["sample_updates"]:
            print(f"        eid={s['event_id']}  "
                  f"{s.get('away_team') or '?'} @ {s.get('home_team') or '?'}  "
                  f"{s.get('game_date') or '?'}  "
                  f"→ {s['away_score']}–{s['home_score']}")


# ───── main ─────
async def amain(args: argparse.Namespace) -> int:
    api_key = os.environ.get("SGO_API_KEY")
    if not api_key:
        print("  ERROR: SGO_API_KEY missing from environment.")
        return 2

    # Default to NFL per the operator brief — explicit list otherwise.
    sports: List[str] = [args.sport] if args.sport != "all" else ["nfl", "mlb", "nba"]
    for s in sports:
        if s not in SPORT_CONFIG:
            print(f"  ERROR: unsupported --sport {s!r} "
                  f"(supported: {sorted(SPORT_CONFIG)})")
            return 2

    dry_run = not args.yes
    print(f"[{datetime.now(timezone.utc).isoformat()}] "
          f"backfill_team_matchup_scores  version={BACKFILL_VERSION}")
    print(f"  sports={sports}  yes={args.yes}  dry_run={dry_run}  "
          f"force={args.force}  max_events_per_sport={args.max_events}")
    print("  CONTRACT: in-place $set updates to matchup docs; preserves "
          "all other fields; idempotent.")

    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]
    sgo = SGOClient(api_key=api_key)
    try:
        all_results: List[Dict[str, Any]] = []
        for sp in sports:
            r = await backfill_sport(
                sgo, db, sport=sp,
                start=args.start, end=args.end,
                dry_run=dry_run, force=args.force,
                max_events=args.max_events)
            _print_summary(r)
            all_results.append(r)

        print()
        print("=" * 72)
        print("  GRAND TOTALS")
        print("=" * 72)
        tot = {"scanned":0,"updated":0,"scores_found":0,
                "scores_missing":0,"already_scored_skip":0,
                "fetch_errors":0,"fetched_from_sgo":0}
        for r in all_results:
            for k in tot:
                tot[k] += r["counters"].get(k, 0)
        for k, v in tot.items():
            print(f"  {k:<22s} {v:,}")
        print(f"  SGO API call stats:    {sgo.stats()}")
        if dry_run:
            print("\n  DRY-RUN — no writes. Pass --yes to apply.")
    finally:
        await sgo.aclose()
        mongo.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sport", choices=["nfl", "mlb", "nba", "all"],
                    default="nfl",
                    help="Sport(s) to backfill. Default 'nfl' "
                          "(NFL-first per operator brief).")
    p.add_argument("--start", default=None,
                    help="Optional inclusive start game_date 'YYYY-MM-DD'.")
    p.add_argument("--end",   default=None,
                    help="Optional inclusive end game_date 'YYYY-MM-DD'.")
    p.add_argument("--yes", action="store_true",
                    help="Actually write to Mongo (default is dry-run).")
    p.add_argument("--dry-run", action="store_true",
                    help="Explicit dry-run (default behaviour when --yes "
                          "is omitted; provided for clarity in scripts).")
    p.add_argument("--force", action="store_true",
                    help="Re-fetch and overwrite even when home_score / "
                          "away_score are already set.")
    p.add_argument("--max-events", type=int, default=10_000,
                    help="Safety cap on matchups processed per sport.")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
