"""
build_team_prop_features.py — Phase 2B Per-Prop Feature Builder.

CONTRACT
    For every RESOLVED row in `team_historical_outcomes`, join the
    team's and the opponent's rolling priors (from `team_model_features`
    at `as_of_date = game_date`) and emit one denormalized
    `team_model_prop_features` document. Each row is one
    "bet decision" — direct input for Phase 3 backtesting.

LEAKAGE GUARANTEE
    `team_model_features` is computed in Phase 2A from games STRICTLY
    before `as_of_date`. By joining team_id + game_date → feature row,
    every prior embedded here is from BEFORE this game. No look-ahead.

OUTPUT DOC SHAPE (one row per resolved prop)
    {
      sport, event_id, team_id, opponent_team_id,
      game_date, commence_time, home_away,
      # bet
      market_key, market_category, market_name, side, line, book,
      odds, is_alternate, periodID, betTypeID,
      # graded outcome
      outcome, hit, push, outcome_resolved, outcome_numeric,
      margin_vs_line, home_score_used, away_score_used, actual_value,
      # joined priors (snapshotted as_of game_date)
      team_features:     {<Phase 2A snapshot> or null},
      opponent_features: {<Phase 2A snapshot> or null},
      team_features_completeness, opponent_features_completeness,
      computed_at, builder_version
    }

USAGE
    # MLB dry-run
    python -m scripts.sgo.build_team_prop_features --sport mlb --dry-run

    # MLB live (commits)
    python -m scripts.sgo.build_team_prop_features --sport mlb

    # All sports
    python -m scripts.sgo.build_team_prop_features --sport all
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
import pymongo
from pymongo import UpdateOne

BUILDER_VERSION = "team_prop_v1"
SRC_OUTCOMES = "team_historical_outcomes"
SRC_FEATURES = "team_model_features"
DST_COLL = "team_model_prop_features"

SUPPORTED_SPORTS = ("mlb", "nba", "nfl")

# Subset of fields hydrated from team_model_features into the embedded
# snapshot. We keep the entire prior bundle so Phase 3 can pick & choose.
_FEATURE_PROJECTION = {
    "_id": 0, "sample_size": 1, "feature_completeness": 1,
    "mu_points_scored": 1, "sigma_points_scored": 1,
    "cv_points_scored": 1,
    "win_rate_l5": 1, "win_rate_l10": 1, "win_rate_season": 1,
    "avg_scored_l5": 1, "avg_scored_l10": 1, "avg_scored_season": 1,
    "avg_allowed_l5": 1, "avg_allowed_l10": 1, "avg_allowed_season": 1,
    "spread_cover_rate_l10": 1, "ou_hit_rate_l10": 1,
    "home_win_rate": 1, "away_win_rate": 1,
    "rest_days": 1, "tempo_l10": 1, "run_trend_l10": 1,
    # MLB SP rotation quality (null for non-MLB sports)
    "sp_k_rate_avg": 1, "sp_woba_allowed_avg": 1,
    "sp_hard_hit_rate_avg": 1, "sp_bb_rate_avg": 1,
    "sp_xwoba_allowed_avg": 1,
}

# Fields lifted verbatim from the outcomes row into the prop_features doc.
_OUTCOME_FIELDS = (
    "event_id", "team_id", "opponent_team_id", "sport",
    "game_date", "commence_time", "home_away",
    "market_key", "market_category", "market_name",
    "side", "sideID", "line", "book", "odds", "is_alternate",
    "periodID", "betTypeID", "statEntityID", "statID",
    "outcome", "hit", "push", "outcome_resolved", "outcome_numeric",
    "margin_vs_line", "home_score_used", "away_score_used",
    "actual_value",
)


# ───── pure helpers (unit-tested) ─────
def stable_key(row: Dict[str, Any]) -> Dict[str, Any]:
    """The composite unique key for one prop_features doc. Stable across
    re-runs so upserts are idempotent. Pure function — nothing dynamic.

    book and line can both be None for some markets (e.g., h2h has
    line=None). We use empty-string sentinels for the unique index
    so MongoDB treats two h2h rows from the same book as a duplicate
    (which they are)."""
    return {
        "event_id":   row.get("event_id"),
        "team_id":    row.get("team_id"),
        "market_key": row.get("market_key"),
        "side":       row.get("side"),
        "line":       row.get("line"),
        "book":       row.get("book"),
    }


def assemble_prop_features_doc(
    outcome: Dict[str, Any], *,
    team_features: Optional[Dict[str, Any]],
    opponent_features: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build the denormalized prop-features document from one outcome
    row + the two (already-loaded) prior snapshots.

    Pure function. Caller is responsible for loading priors with the
    correct (sport, team_id, as_of_date=game_date) — leakage hygiene
    is delegated to Phase 2A which built those priors strictly from
    games before game_date.
    """
    doc: Dict[str, Any] = {}
    for f in _OUTCOME_FIELDS:
        if f in outcome:
            doc[f] = outcome[f]
    doc["team_features"]     = team_features or None
    doc["opponent_features"] = opponent_features or None
    doc["team_features_completeness"] = (
        team_features.get("feature_completeness") if team_features else None)
    doc["opponent_features_completeness"] = (
        opponent_features.get("feature_completeness")
        if opponent_features else None)
    doc["builder_version"] = BUILDER_VERSION
    doc["computed_at"]     = datetime.now(timezone.utc)
    return doc


# ───── DB orchestration ─────
async def _ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """Idempotent — same tolerant pattern as the rest of the SGO scripts."""
    try:
        await db[DST_COLL].create_index(
            [("event_id",   pymongo.ASCENDING),
              ("team_id",    pymongo.ASCENDING),
              ("market_key", pymongo.ASCENDING),
              ("side",       pymongo.ASCENDING),
              ("line",       pymongo.ASCENDING),
              ("book",       pymongo.ASCENDING)],
            unique=True, name="uniq_prop_decision",
        )
        await db[DST_COLL].create_index(
            [("sport", pymongo.ASCENDING),
              ("game_date", pymongo.ASCENDING)],
            name="sport_game_date",
        )
        await db[DST_COLL].create_index(
            [("sport", pymongo.ASCENDING),
              ("market_category", pymongo.ASCENDING)],
            name="sport_market_category",
        )
    except Exception as e:
        print(f"  [indexes] non-fatal: {e}")


class _PriorsCache:
    """In-memory LRU-ish cache of (sport, team_id, as_of_date) → features.

    Outcomes are iterated in arbitrary order so we'd otherwise hammer
    Mongo with one feature read per outcome (~1.3M reads). Even a
    bounded LRU collapses repeats per game (each event has ~50 outcomes
    sharing the same (team, date) → feature key)."""
    def __init__(self, db: AsyncIOMotorDatabase, *, max_size: int = 8192):
        self._db = db
        self._cache: Dict[Tuple[str, str, str], Optional[Dict[str, Any]]] = {}
        self._max_size = max_size
        self.hits = 0
        self.misses = 0

    async def get(self, *, sport: str, team_id: str,
                    as_of_date: str) -> Optional[Dict[str, Any]]:
        key = (sport, team_id, as_of_date)
        if key in self._cache:
            self.hits += 1
            return self._cache[key]
        self.misses += 1
        doc = await self._db[SRC_FEATURES].find_one(
            {"sport": sport, "team_id": team_id, "as_of_date": as_of_date},
            projection=_FEATURE_PROJECTION,
        )
        if len(self._cache) >= self._max_size:
            # Crude eviction: drop the first 25% of insertion order.
            for k in list(self._cache.keys())[: self._max_size // 4]:
                self._cache.pop(k, None)
        self._cache[key] = doc
        return doc


async def build_prop_features_for_sport(
    db: AsyncIOMotorDatabase, *,
    sport: str, dry_run: bool, force: bool,
    max_props: int,
    bulk_chunk: int = 1000,
) -> Dict[str, Any]:
    print(f"\n  [{sport.upper()}] building per-prop features → {DST_COLL}")
    if not dry_run:
        await _ensure_indexes(db)

    n_outcomes = await db[SRC_OUTCOMES].count_documents(
        {"sport": sport, "outcome_resolved": True})
    print(f"  [{sport.upper()}] resolved outcomes to process: {n_outcomes:,}")

    counters = {
        "scanned":             0,
        "team_priors_missing": 0,
        "opponent_priors_missing": 0,
        "both_priors_missing": 0,
        "rows_emitted":        0,
        "rows_written":        0,
        "cache_hits":          0,
        "cache_misses":        0,
        "dry_run":             dry_run,
    }
    sample_rows: List[Dict[str, Any]] = []
    cache = _PriorsCache(db)
    pending: List[UpdateOne] = []

    cursor = db[SRC_OUTCOMES].find(
        {"sport": sport, "outcome_resolved": True},
    ).batch_size(2000)

    async def _flush() -> None:
        if not pending:
            return
        if dry_run:
            counters["rows_emitted"] += len(pending)
            pending.clear()
            return
        res = await db[DST_COLL].bulk_write(pending, ordered=False)
        # `upserted_count + modified_count` counts what we changed
        # (matched but unchanged = idempotent re-write).
        counters["rows_emitted"] += len(pending)
        counters["rows_written"] += (
            (res.upserted_count or 0) + (res.modified_count or 0))
        pending.clear()

    async for o in cursor:
        counters["scanned"] += 1
        if counters["scanned"] > max_props:
            print(f"  [{sport.upper()}] hit --max-props={max_props}; "
                  f"stopping early.")
            break
        gd = o.get("game_date")
        team_id = o.get("team_id")
        opp_id  = o.get("opponent_team_id")
        if not (gd and team_id):
            continue
        team_pri = await cache.get(sport=sport, team_id=team_id, as_of_date=gd)
        opp_pri  = (await cache.get(sport=sport, team_id=opp_id, as_of_date=gd)
                     if opp_id else None)
        if team_pri is None and opp_pri is None:
            counters["both_priors_missing"] += 1
        elif team_pri is None:
            counters["team_priors_missing"] += 1
        elif opp_pri is None:
            counters["opponent_priors_missing"] += 1

        doc = assemble_prop_features_doc(
            o, team_features=team_pri, opponent_features=opp_pri)
        key = stable_key(o)
        if len(sample_rows) < 5:
            sample_rows.append({
                "key": key,
                "outcome": doc.get("outcome"),
                "team_n":  (team_pri or {}).get("sample_size"),
                "opp_n":   (opp_pri or {}).get("sample_size"),
                "team_win_l10":     (team_pri or {}).get("win_rate_l10"),
                "opp_win_l10":      (opp_pri or {}).get("win_rate_l10"),
                "team_tempo_l10":   (team_pri or {}).get("tempo_l10"),
                "opp_tempo_l10":    (opp_pri or {}).get("tempo_l10"),
            })
        pending.append(UpdateOne(key, {"$set": doc}, upsert=True))
        if len(pending) >= bulk_chunk:
            await _flush()
            if counters["scanned"] % 20000 == 0:
                print(f"    [{sport.upper()}] scanned={counters['scanned']:,}  "
                      f"written={counters['rows_written']:,}  "
                      f"cache_hits={cache.hits:,} misses={cache.misses:,}")
    await _flush()

    counters["cache_hits"]   = cache.hits
    counters["cache_misses"] = cache.misses
    return {"sport": sport, "counters": counters, "sample_rows": sample_rows}


def _print_summary(r: Dict[str, Any]) -> None:
    c = r["counters"]
    print()
    print(f"  ── {r['sport'].upper()} PER-PROP FEATURE BUILD SUMMARY ──")
    print(f"     scanned outcomes:        {c['scanned']:,}")
    print(f"     team priors missing:     {c['team_priors_missing']:,}")
    print(f"     opponent priors missing: {c['opponent_priors_missing']:,}")
    print(f"     both priors missing:     {c['both_priors_missing']:,}")
    print(f"     rows emitted:            {c['rows_emitted']:,}")
    print(f"     rows written/changed:    {c['rows_written']:,}  "
          f"({'DRY-RUN' if c['dry_run'] else 'live'})")
    print(f"     prior-cache hits/misses: "
          f"{c['cache_hits']:,} / {c['cache_misses']:,}")
    if r["sample_rows"]:
        print("     sample rows (first 5):")
        for s in r["sample_rows"]:
            k = s["key"]
            print(f"        {k['team_id']:<10s} "
                  f"{k['market_key']:<35s} side={k['side']:<6s} "
                  f"line={k['line']!s:<6s} book={k['book']:<10s} "
                  f"out={s['outcome']:<6s} "
                  f"team_n={s['team_n']} (wL10={s['team_win_l10']})  "
                  f"opp_n={s['opp_n']} (wL10={s['opp_win_l10']})")


# ───── main ─────
async def amain(args: argparse.Namespace) -> int:
    sports = [args.sport] if args.sport != "all" else list(SUPPORTED_SPORTS)
    for s in sports:
        if s not in SUPPORTED_SPORTS:
            print(f"  ERROR: unsupported --sport {s!r}")
            return 2
    dry_run = bool(args.dry_run)
    print(f"[{datetime.now(timezone.utc).isoformat()}] "
          f"build_team_prop_features  version={BUILDER_VERSION}")
    print(f"  sports={sports}  dry_run={dry_run}  force={args.force}  "
          f"max_props={args.max_props}  bulk_chunk={args.bulk_chunk}")
    print(f"  CONTRACT: idempotent upserts to {DST_COLL} keyed by "
          "(event_id, team_id, market_key, side, line, book).")

    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]
    try:
        for sp in sports:
            r = await build_prop_features_for_sport(
                db, sport=sp, dry_run=dry_run, force=args.force,
                max_props=args.max_props, bulk_chunk=args.bulk_chunk)
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
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                    help="(Reserved for future incremental mode)")
    p.add_argument("--max-props", type=int, default=10_000_000)
    p.add_argument("--bulk-chunk", type=int, default=1000,
                    help="Bulk write batch size (default 1000).")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
