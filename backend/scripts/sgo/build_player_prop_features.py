"""
build_player_prop_features.py — Phase 2B Player Per-Prop Feature Builder.

CONTRACT
    For every RESOLVED row in `sgo_pp_research_outcomes`, join the
    player's rolling priors (from `player_model_features` at
    `as_of_date = game_date`) and emit one denormalized
    `player_model_prop_features` document. Each row is one "bet decision"
    — direct input for Phase 3 training.

LEAKAGE GUARANTEE
    `player_model_features` is computed in Phase 2A from outcomes STRICTLY
    before `as_of_date`. By joining player_id + game_date → feature row,
    every prior embedded here is from BEFORE this game. No look-ahead.

OUTPUT DOC SHAPE (one row per resolved prop)
    {
      sport, league_id, event_id, player_id, player_name,
      game_date, stat_family, period_id, side, line, prop_type="player"
      # priors (snapshotted as_of game_date)
      player_features:     {<Phase 2A stat-family snapshot> or null}
      # market signal
      implied_probability  (sharp_consensus_probability → consensus_probability
                            → pp_implied_probability cascade)
      clean_odds           (anchor_odds converted to int)
      # graded outcome
      outcome_numeric, hit, push, outcome_resolved
      # metadata
      computed_at, builder_version
    }

USAGE
    python -m scripts.sgo.build_player_prop_features --sport nba --dry-run
    python -m scripts.sgo.build_player_prop_features --sport nba
    python -m scripts.sgo.build_player_prop_features --sport all
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
for _env in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(_env):
        load_dotenv(_env)
        break

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import pymongo
from pymongo import UpdateOne

BUILDER_VERSION = "player_prop_v1"
SRC_COLL = "sgo_propvision_full_pipeline_replay"
SRC_FEATURES = "player_model_features"
DST_COLL = "player_model_prop_features"

SUPPORTED_SPORTS = ("nba", "mlb")
SPORT_TO_LEAGUE = {"nba": "NBA", "mlb": "MLB"}

# Fields lifted verbatim from each outcome row.
_OUTCOME_FIELDS = (
    "event_id", "player_id", "player_name", "league_id",
    "game_date", "stat_family", "stat_id", "period_id",
    "side", "line",
    "hit", "push", "outcome", "outcome_resolved", "outcome_numeric",
    "actual_value", "margin_vs_line",
)


# ───── pure helpers ─────
def _parse_american_odds(s: Any) -> Optional[int]:
    """Convert '+100', '-115', '100' → int. None on failure."""
    if s is None:
        return None
    try:
        return int(float(str(s).strip()))
    except (ValueError, TypeError, AttributeError):
        return None


def _best_implied(outcome: Dict[str, Any]) -> Optional[float]:
    """Cascade: sharp_consensus → consensus → pp_implied.
    Returns None if all are None."""
    v = outcome.get("sharp_consensus_probability")
    if v is not None:
        return float(v)
    v = outcome.get("consensus_probability")
    if v is not None:
        return float(v)
    v = outcome.get("pp_implied_probability")
    if v is not None:
        return float(v)
    return None


def stable_key(outcome: Dict[str, Any]) -> Dict[str, Any]:
    """Composite upsert key for one player prop feature doc.
    Dedup on (event_id, player_id, stat_family, side, line, period_id)."""
    return {
        "event_id":   outcome.get("event_id"),
        "player_id":  outcome.get("player_id"),
        "stat_family": outcome.get("stat_family"),
        "side":        outcome.get("side"),
        "line":        outcome.get("line"),
        "period_id":   outcome.get("period_id"),
    }


def assemble_prop_doc(
    outcome: Dict[str, Any],
    sport: str,
    player_features: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build the denormalized prop-features doc from one outcome row and
    the player's pre-loaded rolling-prior snapshot.

    player_features is the full Phase 2A doc for this player at game_date.
    We extract the stat_family-specific sub-dict from stat_families and
    embed it flat (hit_rate_l5, hit_rate_l10, hit_rate_l20, cv, avg_line,
    sample_size) alongside the global fields (rest_days, sample_size).
    """
    doc: Dict[str, Any] = {"sport": sport, "prop_type": "player"}
    for f in _OUTCOME_FIELDS:
        if f in outcome:
            doc[f] = outcome[f]

    # Global player-level priors
    doc["rest_days"]    = (player_features or {}).get("rest_days")
    doc["global_sample_size"] = (player_features or {}).get("sample_size")

    # Per-stat-family priors (flat into the doc)
    fam = outcome.get("stat_family") or "_unknown"
    sf_data: Dict[str, Any] = {}
    if player_features:
        sf_data = (player_features.get("stat_families") or {}).get(fam) or {}
    doc["hit_rate_l5"]   = sf_data.get("hit_rate_l5")
    doc["hit_rate_l10"]  = sf_data.get("hit_rate_l10")
    doc["hit_rate_l20"]  = sf_data.get("hit_rate_l20")
    doc["cv"]            = sf_data.get("cv")
    doc["avg_line"]      = sf_data.get("avg_line")
    doc["sample_size"]   = sf_data.get("sample_size", 0)

    # Store the full feature doc for downstream use
    doc["player_features"] = player_features or None

    # Market signal
    implied = _best_implied(outcome)
    doc["implied_probability"] = implied
    doc["clean_odds"] = (
        _parse_american_odds(outcome.get("anchor_odds"))
        or _parse_american_odds(outcome.get("book_odds"))
    )

    doc["builder_version"] = BUILDER_VERSION
    doc["computed_at"] = datetime.now(timezone.utc)
    return doc


# ───── DB orchestration ─────
async def _ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    try:
        # Drop old partial indexes (MongoDB doesn't support $ne: null in partialFilterExpression).
        for old in ("uniq_player_prop_decision",
                    "uniq_player_prop_decision_with_event",
                    "uniq_player_prop_decision_null_event"):
            try:
                await db[DST_COLL].drop_index(old)
            except Exception:
                pass  # already gone

        # Single unique index covers both backfill rows (no event_id) and SGO rows.
        await db[DST_COLL].create_index(
            [("player_id",   pymongo.ASCENDING),
             ("game_date",   pymongo.ASCENDING),
             ("stat_family", pymongo.ASCENDING),
             ("side",        pymongo.ASCENDING),
             ("line",        pymongo.ASCENDING)],
            unique=True,
            name="uniq_player_prop_decision",
        )
        await db[DST_COLL].create_index(
            [("sport", pymongo.ASCENDING),
             ("game_date", pymongo.ASCENDING)],
            name="sport_game_date",
        )
        await db[DST_COLL].create_index(
            [("sport", pymongo.ASCENDING),
             ("stat_family", pymongo.ASCENDING)],
            name="sport_stat_family",
        )
    except Exception as e:
        print(f"  [indexes] non-fatal: {e}")


class _FeaturesCache:
    """Cache (sport, player_id, as_of_date) → player_model_features doc.
    Avoids repeated Mongo round-trips for the same player+date combo."""

    def __init__(self, db: AsyncIOMotorDatabase, *, max_size: int = 16384):
        self._db = db
        self._cache: Dict[Tuple[str, str, str], Optional[Dict]] = {}
        self._max_size = max_size
        self.hits = 0
        self.misses = 0

    async def get(
        self, *, sport: str, player_id: str, as_of_date: str,
    ) -> Optional[Dict[str, Any]]:
        key = (sport, player_id, as_of_date)
        if key in self._cache:
            self.hits += 1
            return self._cache[key]
        self.misses += 1
        doc = await self._db[SRC_FEATURES].find_one(
            {"sport": sport, "player_id": player_id,
             "as_of_date": as_of_date},
            projection={"_id": 0, "stat_families": 1, "rest_days": 1,
                        "sample_size": 1, "feature_completeness": 1},
        )
        if len(self._cache) >= self._max_size:
            for k in list(self._cache.keys())[: self._max_size // 4]:
                self._cache.pop(k, None)
        self._cache[key] = doc
        return doc


async def build_prop_features_for_sport(
    db: AsyncIOMotorDatabase, *,
    sport: str, dry_run: bool,
    max_props: int, bulk_chunk: int = 1000,
) -> Dict[str, Any]:
    league_id = SPORT_TO_LEAGUE[sport]
    print(f"\n  [{sport.upper()}] building player prop features → {DST_COLL}")

    if not dry_run:
        await _ensure_indexes(db)

    n_total = await db[SRC_COLL].count_documents(
        {"league_id": league_id, "outcome_resolved": True})
    print(f"  [{sport.upper()}] resolved outcomes: {n_total:,}")

    counters = {
        "scanned":             0,
        "features_missing":    0,
        "rows_emitted":        0,
        "rows_written":        0,
        "dry_run":             dry_run,
    }
    sample_rows: List[Dict] = []
    cache = _FeaturesCache(db)
    pending: List[UpdateOne] = []

    async def _flush() -> None:
        if not pending:
            return
        if dry_run:
            counters["rows_emitted"] += len(pending)
            pending.clear()
            return
        res = await db[DST_COLL].bulk_write(pending, ordered=False)
        counters["rows_emitted"] += len(pending)
        counters["rows_written"] += (
            (res.upserted_count or 0) + (res.modified_count or 0))
        pending.clear()

    cursor = db[SRC_COLL].find(
        {"league_id": league_id, "outcome_resolved": True},
    ).batch_size(2000)

    async for o in cursor:
        counters["scanned"] += 1
        if counters["scanned"] > max_props:
            print(f"  [{sport.upper()}] hit --max-props={max_props}; stopping.")
            break
        gd = o.get("game_date")
        pid = o.get("player_id")
        if not (gd and pid):
            continue

        feat = await cache.get(sport=sport, player_id=pid, as_of_date=gd)
        if feat is None:
            counters["features_missing"] += 1

        doc = assemble_prop_doc(o, sport, feat)
        key = stable_key(o)

        if len(sample_rows) < 5 and feat is not None:
            sample_rows.append({
                "player_id": pid, "game_date": gd,
                "stat_family": o.get("stat_family"),
                "side": o.get("side"), "line": o.get("line"),
                "hit_rate_l20": doc.get("hit_rate_l20"),
                "cv": doc.get("cv"),
                "implied_probability": doc.get("implied_probability"),
                "outcome_numeric": doc.get("outcome_numeric"),
            })

        pending.append(UpdateOne(key, {"$set": doc}, upsert=True))
        if len(pending) >= bulk_chunk:
            await _flush()
            if counters["scanned"] % 50000 == 0:
                print(f"    [{sport.upper()}] scanned={counters['scanned']:,}  "
                      f"written={counters['rows_written']:,}  "
                      f"cache={cache.hits:,}/{cache.misses:,}")

    await _flush()
    counters["cache_hits"]   = cache.hits
    counters["cache_misses"] = cache.misses
    return {"sport": sport, "counters": counters, "sample_rows": sample_rows}


def _print_summary(r: Dict[str, Any]) -> None:
    c = r["counters"]
    print()
    print(f"  ── {r['sport'].upper()} PLAYER PER-PROP FEATURE SUMMARY ──")
    print(f"     scanned:               {c['scanned']:,}")
    print(f"     features missing:      {c['features_missing']:,}")
    print(f"     rows emitted:          {c['rows_emitted']:,}")
    print(f"     rows written/changed:  {c['rows_written']:,}  "
          f"({'DRY-RUN' if c['dry_run'] else 'live'})")
    print(f"     cache hits/misses:     "
          f"{c.get('cache_hits',0):,} / {c.get('cache_misses',0):,}")
    if r["sample_rows"]:
        print("     sample rows (first 5):")
        for s in r["sample_rows"]:
            print(f"        {s['player_id']:<32s} {s['game_date']}  "
                  f"{s['stat_family']:<12s} side={s['side']:<6s} "
                  f"line={s['line']!s:<6s}  "
                  f"hr_l20={s.get('hit_rate_l20')}  "
                  f"cv={s['cv']}  imp={s['implied_probability']}  "
                  f"out={s['outcome_numeric']}")


async def amain(args: argparse.Namespace) -> int:
    sports = [args.sport] if args.sport != "all" else list(SUPPORTED_SPORTS)
    for s in sports:
        if s not in SUPPORTED_SPORTS:
            print(f"  ERROR: unsupported --sport {s!r}")
            return 2
    dry_run = bool(args.dry_run)
    print(f"[{datetime.now(timezone.utc).isoformat()}] "
          f"build_player_prop_features  version={BUILDER_VERSION}")
    print(f"  sports={sports}  dry_run={dry_run}  "
          f"max_props={args.max_props}  bulk_chunk={args.bulk_chunk}")
    print(f"  CONTRACT: idempotent upserts to {DST_COLL} keyed by "
          "(event_id, player_id, stat_family, side, line, period_id).")

    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]
    try:
        for sp in sports:
            r = await build_prop_features_for_sport(
                db, sport=sp, dry_run=dry_run,
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
    p.add_argument("--max-props", type=int, default=10_000_000)
    p.add_argument("--bulk-chunk", type=int, default=1000,
                   help="Bulk write batch size (default 1000).")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
