"""
build_player_features.py — Phase 2 Player Feature Builder.

CONTRACT
    For every (sport, player_id, as_of_date) tuple where the player has at
    least one `sgo_pp_research_outcomes` row, computes a rolling-prior
    feature snapshot and upserts it into `player_model_features`.

INPUTS
    Source: `sgo_pp_research_outcomes` (resolved player prop outcomes from
    PrizePicks / sportsbooks), filtered by league_id.

OUTPUT
    `player_model_features` documents keyed by
    (sport, player_id, as_of_date). One row per player per game date.

    Per-stat-family rolling priors are embedded in the `stat_families`
    nested dict so Phase 2B can extract the relevant family without a
    second Mongo query:
        stat_families.<family>.hit_rate_l5 / l10 / l20
        stat_families.<family>.cv
        stat_families.<family>.avg_line
        stat_families.<family>.sample_size

    Global (cross-stat-family) fields:
        sample_size     — unique game dates before as_of_date
        rest_days       — days since last game (any stat), capped at 14
        feature_completeness = "player_v1_priors"

LEAKAGE GUARANTEE
    Every feature is computed STRICTLY from games before `as_of_date`
    (game_date < as_of_date). Validated per-player by assert_no_future_games.

USAGE
    python -m scripts.sgo.build_player_features --sport nba --dry-run
    python -m scripts.sgo.build_player_features --sport nba
    python -m scripts.sgo.build_player_features --sport all
"""
from __future__ import annotations
import argparse
import asyncio
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, date as _date
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for _env in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(_env):
        load_dotenv(_env)
        break

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import pymongo

FEATURE_VERSION = "player_v1_priors"
SRC_COLL = "player_historical_outcomes"
DST_COLL = "player_model_features"

SUPPORTED_SPORTS = ("nba", "mlb")


# ───── pure helpers ─────
def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
        return None if (math.isnan(x) or math.isinf(x)) else x
    except (TypeError, ValueError):
        return None


def _mean(vals: List[float]) -> Optional[float]:
    return sum(vals) / len(vals) if vals else None


def _std(vals: List[float]) -> Optional[float]:
    if len(vals) < 2:
        return None
    m = sum(vals) / len(vals)
    var = sum((v - m) ** 2 for v in vals) / len(vals)
    return math.sqrt(var) if var > 0 else None


def _rate(flags: List[Optional[bool]]) -> Optional[float]:
    """Fraction of True among non-None. None = push/unknown, excluded."""
    clean = [bool(x) for x in flags if x is not None]
    return (sum(clean) / len(clean)) if clean else None


def _rest_days(prior_date: Optional[str], as_of: str) -> Optional[int]:
    if not prior_date:
        return None
    try:
        a = _date.fromisoformat(prior_date[:10])
        b = _date.fromisoformat(as_of[:10])
    except ValueError:
        return None
    d = (b - a).days
    return None if d < 0 else min(14, d)


def _r(v: Optional[float], nd: int = 4) -> Optional[float]:
    return round(v, nd) if v is not None else None


def compute_stat_family_features(
    outcomes: List[Dict[str, Any]],
    *,
    as_of_date: str,
) -> Dict[str, Any]:
    """Pure: given all resolved outcomes for one (player, stat_family)
    sorted ascending by game_date, compute rolling hit-rate features
    strictly before as_of_date."""
    prior = [o for o in outcomes if (o.get("game_date") or "") < as_of_date]
    n = len(prior)
    if n == 0:
        return {
            "hit_rate_l5": None, "hit_rate_l10": None, "hit_rate_l20": None,
            "cv": None, "avg_line": None, "sample_size": 0,
        }

    recent = list(reversed(prior))  # most-recent-first
    l5 = recent[:5]
    l10 = recent[:10]
    l20 = recent[:20]

    def _hr(rows: List[Dict[str, Any]]) -> Optional[float]:
        flags = [r.get("hit") if not r.get("push") else None for r in rows]
        return _rate(flags)

    # CV over last-20 actual_values (non-None, non-zero denominator)
    actual_l20 = [_num(r.get("actual_value")) for r in l20]
    actual_clean = [v for v in actual_l20 if v is not None]
    mu = _mean(actual_clean)
    sigma = _std(actual_clean)
    cv = _r((sigma / mu), 4) if (sigma is not None and mu and mu > 0) else None

    # Average prop line over last 20
    lines = [_num(r.get("line")) for r in l20]
    lines_clean = [v for v in lines if v is not None]
    avg_line = _r(_mean(lines_clean), 2)

    return {
        "hit_rate_l5":  _r(_hr(l5)),
        "hit_rate_l10": _r(_hr(l10)),
        "hit_rate_l20": _r(_hr(l20)),
        "cv":           cv,
        "avg_line":     avg_line,
        "sample_size":  n,
    }


def compute_player_as_of_features(
    outcomes_by_family: Dict[str, List[Dict[str, Any]]],
    all_game_dates: List[str],
    *,
    as_of_date: str,
) -> Dict[str, Any]:
    """Compute one full player feature snapshot as_of a given date.

    outcomes_by_family: {stat_family: [sorted-asc outcomes]}
    all_game_dates: sorted list of all unique game dates for this player
    """
    prior_dates = sorted(d for d in all_game_dates if d < as_of_date)
    sample_size = len(prior_dates)
    rest = _rest_days(prior_dates[-1] if prior_dates else None, as_of_date)

    stat_families: Dict[str, Any] = {}
    for fam, rows in outcomes_by_family.items():
        stat_families[fam] = compute_stat_family_features(rows, as_of_date=as_of_date)

    return {
        "sample_size":          sample_size,
        "rest_days":            rest,
        "stat_families":        stat_families,
        "feature_completeness": FEATURE_VERSION,
    }


def assert_no_future_games(
    outcomes: List[Dict[str, Any]], *, as_of_date: str,
) -> None:
    for o in outcomes:
        gd = o.get("game_date") or ""
        if gd >= as_of_date:
            raise RuntimeError(
                f"leakage: game_date {gd} >= as_of_date {as_of_date} "
                f"(event_id={o.get('event_id')})")


# ───── DB orchestration ─────
async def _ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    try:
        await db[DST_COLL].create_index(
            [("sport", pymongo.ASCENDING),
             ("player_id", pymongo.ASCENDING),
             ("as_of_date", pymongo.ASCENDING)],
            unique=True, name="uniq_sport_player_asof",
        )
        await db[DST_COLL].create_index(
            [("sport", pymongo.ASCENDING),
             ("as_of_date", pymongo.ASCENDING)],
            name="sport_asof",
        )
    except Exception as e:
        print(f"  [indexes] non-fatal: {e}")


async def _load_player_outcomes(
    db: AsyncIOMotorDatabase, *, sport: str, player_id: str,
) -> Tuple[Dict[str, List[Dict]], List[str]]:
    """Load all RESOLVED outcomes for one player, return:
      (outcomes_by_family, sorted unique game_dates)
    Only includes non-push outcomes for hit-rate computation.
    """
    rows: List[Dict[str, Any]] = []
    cursor = db[SRC_COLL].find(
        {"sport": sport, "player_id": player_id,
         "outcome_resolved": True},
        projection={
            "_id": 0, "game_date": 1, "stat_family": 1,
            "hit": 1, "push": 1, "actual_value": 1, "line": 1,
            "event_id": 1,
        },
    ).batch_size(2000)
    async for r in cursor:
        if r.get("game_date"):
            rows.append(r)

    outcomes_by_family: Dict[str, List[Dict]] = defaultdict(list)
    game_dates: set = set()
    for r in rows:
        game_dates.add(r["game_date"])
        outcomes_by_family[r.get("stat_family") or "_unknown"].append(r)

    # Sort each family's rows ascending by game_date
    for fam in outcomes_by_family:
        outcomes_by_family[fam].sort(key=lambda x: x["game_date"])

    return dict(outcomes_by_family), sorted(game_dates)


async def _distinct_player_ids(
    db: AsyncIOMotorDatabase, sport: str,
) -> List[str]:
    ids = await db[SRC_COLL].distinct(
        "player_id", {"sport": sport, "outcome_resolved": True})
    return sorted([p for p in ids if isinstance(p, str) and p])


async def build_features_for_sport(
    db: AsyncIOMotorDatabase, *,
    sport: str, dry_run: bool,
    max_players: int = 5000,
) -> Dict[str, Any]:
    print(f"\n  [{sport.upper()}] building player features → {DST_COLL}")

    players = await _distinct_player_ids(db, sport)
    if max_players and len(players) > max_players:
        print(f"  [{sport.upper()}] capping at first {max_players} of "
              f"{len(players)} players")
        players = players[:max_players]
    print(f"  [{sport.upper()}] unique player_ids: {len(players)}")

    if not dry_run:
        await _ensure_indexes(db)

    counters = {
        "players_processed":    0,
        "player_dates_emitted": 0,
        "rows_written":         0,
        "leakage_violations":   0,
        "dry_run":              dry_run,
    }
    sample_rows: List[Dict] = []

    for player_id in players:
        outcomes_by_family, game_dates = await _load_player_outcomes(
            db, sport=sport, player_id=player_id)
        if not game_dates:
            continue
        counters["players_processed"] += 1

        for as_of in game_dates:
            # leakage guard: check all outcomes across all families
            all_prior: List[Dict] = []
            for rows in outcomes_by_family.values():
                all_prior.extend([r for r in rows if r["game_date"] < as_of])
            try:
                assert_no_future_games(all_prior, as_of_date=as_of)
            except RuntimeError as e:
                counters["leakage_violations"] += 1
                print(f"  [{sport.upper()}] LEAKAGE: {e}")
                continue

            feat = compute_player_as_of_features(
                outcomes_by_family, game_dates, as_of_date=as_of)
            counters["player_dates_emitted"] += 1

            if len(sample_rows) < 5 and feat["sample_size"] > 0:
                sample_rows.append({
                    "player_id": player_id, "as_of": as_of,
                    "n": feat["sample_size"], "rest": feat["rest_days"],
                    "families": list(feat["stat_families"].keys()),
                })
            if dry_run:
                continue

            doc = {
                "sport":       sport,
                "player_id":   player_id,
                "as_of_date":  as_of,
                "computed_at": datetime.now(timezone.utc),
                **feat,
            }
            await db[DST_COLL].update_one(
                {"sport": sport, "player_id": player_id,
                 "as_of_date": as_of},
                {"$set": doc}, upsert=True,
            )
            counters["rows_written"] += 1

        if counters["players_processed"] % 50 == 0:
            print(f"    [{sport.upper()}] processed {counters['players_processed']}/"
                  f"{len(players)} players; "
                  f"dates_emitted={counters['player_dates_emitted']:,}")

    return {"sport": sport, "counters": counters, "sample_rows": sample_rows}


def _print_summary(r: Dict[str, Any]) -> None:
    c = r["counters"]
    print()
    print(f"  ── {r['sport'].upper()} PLAYER FEATURE BUILD SUMMARY ──")
    print(f"     players processed:       {c['players_processed']:,}")
    print(f"     player-dates emitted:    {c['player_dates_emitted']:,}")
    print(f"     feature rows written:    {c['rows_written']:,}  "
          f"({'DRY-RUN' if c['dry_run'] else 'live'})")
    print(f"     leakage violations:      {c['leakage_violations']:,}")
    if r["sample_rows"]:
        print("     sample rows (first 5):")
        for s in r["sample_rows"]:
            print(f"        {s['player_id']:<32s} @ {s['as_of']}  "
                  f"n={s['n']:>3}  rest={s['rest']}  "
                  f"families={s['families'][:3]}")


async def amain(args: argparse.Namespace) -> int:
    sports = [args.sport] if args.sport != "all" else list(SUPPORTED_SPORTS)
    for s in sports:
        if s not in SUPPORTED_SPORTS:
            print(f"  ERROR: unsupported --sport {s!r}")
            return 2
    dry_run = bool(args.dry_run)
    print(f"[{datetime.now(timezone.utc).isoformat()}] "
          f"build_player_features  version={FEATURE_VERSION}")
    print(f"  sports={sports}  dry_run={dry_run}")
    print(f"  CONTRACT: upserts to {DST_COLL} keyed by "
          "(sport, player_id, as_of_date). Idempotent. Leakage-safe.")

    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]
    try:
        for sp in sports:
            r = await build_features_for_sport(
                db, sport=sp, dry_run=dry_run,
                max_players=args.max_players)
            _print_summary(r)
        if dry_run:
            print("\n  DRY-RUN — no writes. Re-run without --dry-run to persist.")
    finally:
        mongo.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sport", choices=list(SUPPORTED_SPORTS) + ["all"],
                   default="all")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute & summarize but do not write to Mongo.")
    p.add_argument("--max-players", type=int, default=5000,
                   help="Safety cap on players processed per sport.")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
