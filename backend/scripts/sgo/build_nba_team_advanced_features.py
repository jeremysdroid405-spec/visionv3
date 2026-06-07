"""
build_nba_team_advanced_features.py — Phase 2A enrichment: NBA advanced stats.

Aggregates player-level `bdl_advanced_stats` to team-game level, computes
rolling windows strictly before each game date (no lookahead), and $set-upserts
the new fields onto existing `team_model_features` docs. Existing fields are
never overwritten.

DATE ALIGNMENT
    BDL uses Eastern Time dates; The Odds API uses UTC dates. A game at
    8 PM EST on 2024-10-22 is game_date='2024-10-22' in BDL but
    as_of_date='2024-10-23' in team_model_features (UTC midnight).
    This script resolves the correct as_of_date by pre-loading the known
    (team_id, as_of_date) pairs from team_model_features and trying D
    then D+1 for each BDL game.

FIELDS ADDED TO team_model_features
    pace_l5/l10/l20, pace_stddev_l10
    off_rating_l5/l10/l20, off_rating_stddev_l10
    def_rating_l5/l10/l20
    ts_pct_l10, def_reb_pct_l10
    pct_pts_paint_l10, pct_pts_fast_break_l10
    assist_ratio_l10, turnover_ratio_l10
    is_back_to_back, games_in_last_7
    scoring_trend_l5_l20  (derived via pipeline update from avg_scored_l5/l20)

LEAKAGE GUARANTEE
    Rolling windows for a game use only prior games (index < i in the
    date-sorted team history). As_of_date is resolved to match the
    existing doc so Phase 2A and 2A-adv priors exclude the same game.

USAGE
    python -m scripts.sgo.build_nba_team_advanced_features --dry-run
    python -m scripts.sgo.build_nba_team_advanced_features
"""
from __future__ import annotations
import argparse
import asyncio
import math
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for _env in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(_env):
        load_dotenv(_env)
        break

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import UpdateOne

VERSION = "team_adv_v1"
SRC_COLL = "bdl_advanced_stats"
DST_COLL = "team_model_features"
SPORT = "nba"

# BDL field → our stored name (first-value per team-game)
_FIRST_FIELDS: Dict[str, str] = {
    "pace":               "pace",
    "offensive_rating":   "off_rating",
    "defensive_rating":   "def_rating",
    "possessions":        "possessions",
}

# BDL field → our stored name (averaged across players per team-game)
_AVG_FIELDS: Dict[str, str] = {
    "true_shooting_percentage":     "ts_pct",
    "defensive_rebound_percentage": "def_reb_pct",
    "pct_pts_paint":                "pct_pts_paint",
    "pct_pts_fast_break":           "pct_pts_fast_break",
    "assist_ratio":                 "assist_ratio",
    "turnover_ratio":               "turnover_ratio",
}

# Fields that get L5/L10/L20 rolling means
_WINDOW_FIELDS = (
    "pace", "off_rating", "def_rating",
    "ts_pct", "def_reb_pct", "pct_pts_paint", "pct_pts_fast_break",
)
# Fields that additionally get L10 stddev
_STDDEV_FIELDS = ("pace", "off_rating")
# Fields with only L10 rolling mean (no L5/L20)
_L10_ONLY_FIELDS = ("assist_ratio", "turnover_ratio")


# ───── pure numeric helpers ─────
def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
        return None if (math.isnan(x) or math.isinf(x)) else x
    except (TypeError, ValueError):
        return None


def _mean(vals: List[Optional[float]]) -> Optional[float]:
    good = [x for x in vals if x is not None]
    return sum(good) / len(good) if good else None


def _std(vals: List[Optional[float]]) -> Optional[float]:
    good = [x for x in vals if x is not None]
    if len(good) < 2:
        return None
    m = sum(good) / len(good)
    return math.sqrt(sum((x - m) ** 2 for x in good) / (len(good) - 1))


def _parse_date(s: str) -> date:
    y, mo, d = s.split("-")
    return date(int(y), int(mo), int(d))


def _date_plus_one(s: str) -> str:
    return (_parse_date(s) + timedelta(days=1)).isoformat()


# ───── Step 1: aggregate BDL player rows to team-game level ─────
def aggregate_to_team_games(
    raw_docs: List[Dict[str, Any]],
) -> Dict[Tuple[str, int], Dict[str, Any]]:
    """Group player-level BDL docs by (team_abbr, game_id) and aggregate.

    Returns dict keyed by (team_abbr, game_id) → team-game record with:
        game_date, team_abbr, game_id
        pace, off_rating, def_rating, possessions  (first-value)
        ts_pct, def_reb_pct, pct_pts_paint, pct_pts_fast_break,
        assist_ratio, turnover_ratio               (player avg)
    """
    buckets: Dict[Tuple[str, int], Dict[str, Any]] = {}
    accum:   Dict[Tuple[str, int], Dict[str, List[float]]] = defaultdict(
        lambda: {dst: [] for dst in _AVG_FIELDS.values()})

    for doc in raw_docs:
        abbr = doc.get("team_abbr")
        gid  = doc.get("game_id")
        if not abbr or gid is None:
            continue
        key = (abbr, gid)
        if key not in buckets:
            rec: Dict[str, Any] = {
                "game_date": doc.get("game_date"),
                "team_abbr": abbr,
                "game_id":   gid,
            }
            for src, dst in _FIRST_FIELDS.items():
                rec[dst] = _num(doc.get(src))
            buckets[key] = rec
        for src, dst in _AVG_FIELDS.items():
            v = _num(doc.get(src))
            if v is not None:
                accum[key][dst].append(v)

    for key, rec in buckets.items():
        for dst, vals in accum[key].items():
            rec[dst] = (sum(vals) / len(vals)) if vals else None

    return buckets


# ───── Step 2: rolling features per team ─────
def compute_team_rolling_features(
    team_games: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Given one team's games sorted ascending by game_date, return one
    enriched dict per game. Rolling windows use only games strictly
    before each game (no lookahead)."""
    results: List[Dict[str, Any]] = []
    for i, game in enumerate(team_games):
        history = team_games[:i]  # strictly before current game

        feats: Dict[str, Any] = {
            "bdl_game_date": game["game_date"],  # original BDL date
            "team_abbr": game["team_abbr"],
        }

        for field in _WINDOW_FIELDS:
            vals = [g.get(field) for g in history]
            for w, sfx in ((5, "l5"), (10, "l10"), (20, "l20")):
                feats[f"{field}_{sfx}"] = _mean(vals[-w:])
            if field in _STDDEV_FIELDS:
                feats[f"{field}_stddev_l10"] = _std(vals[-10:])

        for field in _L10_ONLY_FIELDS:
            vals = [g.get(field) for g in history]
            feats[f"{field}_l10"] = _mean(vals[-10:])

        if history:
            curr_dt = _parse_date(game["game_date"])
            last_dt = _parse_date(history[-1]["game_date"])
            delta = (curr_dt - last_dt).days
            feats["is_back_to_back"] = 1 if delta == 1 else 0
            feats["games_in_last_7"] = sum(
                1 for g in history
                if 0 < (curr_dt - _parse_date(g["game_date"])).days <= 7
            )
        else:
            feats["is_back_to_back"] = 0
            feats["games_in_last_7"] = 0

        results.append(feats)
    return results


# ───── Step 3: resolve BDL dates to team_model_features as_of_dates ─────
def resolve_as_of_date(
    bdl_date: str,
    known_dates: Set[str],
) -> Optional[str]:
    """Try BDL date, then BDL date +1 day (UTC shift for evening games).
    Returns the matching as_of_date or None if neither exists."""
    if bdl_date in known_dates:
        return bdl_date
    plus_one = _date_plus_one(bdl_date)
    if plus_one in known_dates:
        return plus_one
    return None


# ───── Step 4: build upsert ops ─────
def build_upsert_ops(
    feature_rows: List[Dict[str, Any]],
    known_dates_by_team: Dict[str, Set[str]],
) -> Tuple[List[UpdateOne], int]:
    """Convert computed feature rows to MongoDB UpdateOne ops.
    Resolves the correct as_of_date for each row. upsert=False.
    Returns (ops, n_skipped)."""
    ops: List[UpdateOne] = []
    skipped = 0
    for row in feature_rows:
        team_id = "nba_" + row["team_abbr"].lower()
        bdl_date = row["bdl_game_date"]
        known = known_dates_by_team.get(team_id, set())
        as_of = resolve_as_of_date(bdl_date, known)
        if as_of is None:
            skipped += 1
            continue
        payload = {k: v for k, v in row.items()
                   if k not in ("bdl_game_date", "team_abbr")}
        payload["adv_version"] = VERSION
        ops.append(UpdateOne(
            {"sport": SPORT, "team_id": team_id, "as_of_date": as_of},
            {"$set": payload},
            upsert=False,
        ))
    return ops, skipped


# ───── main orchestration ─────
async def run(*, dry_run: bool, bulk_chunk: int) -> None:
    print(f"[build_nba_team_advanced_features]  version={VERSION}  "
          f"dry_run={dry_run}")

    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db: AsyncIOMotorDatabase = mongo[os.environ["DB_NAME"]]
    try:
        # 0. Pre-load known (team_id, as_of_date) pairs from team_model_features
        print(f"  Loading known as_of_dates from {DST_COLL}…")
        known_dates_by_team: Dict[str, Set[str]] = defaultdict(set)
        async for doc in db[DST_COLL].find(
            {"sport": SPORT},
            projection={"_id": 0, "team_id": 1, "as_of_date": 1},
        ):
            tid = doc.get("team_id")
            aod = doc.get("as_of_date")
            if tid and aod:
                known_dates_by_team[tid].add(aod)
        n_known = sum(len(v) for v in known_dates_by_team.values())
        print(f"  Known: {len(known_dates_by_team)} teams, "
              f"{n_known:,} (team, date) pairs.")

        # 1. Load all BDL player-level rows (~150K docs)
        print(f"  Loading {SRC_COLL}…")
        raw: List[Dict[str, Any]] = []
        async for doc in db[SRC_COLL].find(
            {},
            projection={"_id": 0, "game_id": 1, "team_abbr": 1, "game_date": 1,
                        "pace": 1, "offensive_rating": 1,
                        "defensive_rating": 1, "possessions": 1,
                        "true_shooting_percentage": 1,
                        "defensive_rebound_percentage": 1,
                        "pct_pts_paint": 1, "pct_pts_fast_break": 1,
                        "assist_ratio": 1, "turnover_ratio": 1},
        ):
            raw.append(doc)
        print(f"  Loaded {len(raw):,} player-game rows.")

        # 2. Aggregate to team-game level
        team_game_map = aggregate_to_team_games(raw)
        print(f"  Aggregated to {len(team_game_map):,} team-game rows.")

        # 3. Group by team_abbr, sort by date, compute rolling features
        by_team: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for rec in team_game_map.values():
            by_team[rec["team_abbr"]].append(rec)
        for abbr in by_team:
            by_team[abbr].sort(key=lambda r: r["game_date"])

        all_feature_rows: List[Dict[str, Any]] = []
        for abbr, games in sorted(by_team.items()):
            rows = compute_team_rolling_features(games)
            all_feature_rows.extend(rows)
        print(f"  Computed {len(all_feature_rows):,} feature rows "
              f"across {len(by_team)} teams.")

        # 4. Resolve as_of_dates and build upsert ops
        ops, n_skipped = build_upsert_ops(all_feature_rows, known_dates_by_team)
        n_no_match = n_skipped
        print(f"  Ops: {len(ops):,}  no-match skipped: {n_no_match:,}")

        if dry_run:
            print(f"  DRY-RUN — would attempt {len(ops):,} update ops "
                  f"(upsert=False).")
        else:
            print(f"  Writing {len(ops):,} ops to {DST_COLL} in chunks of "
                  f"{bulk_chunk}…")
            total_matched = 0
            total_modified = 0
            for start in range(0, len(ops), bulk_chunk):
                chunk = ops[start: start + bulk_chunk]
                res = await db[DST_COLL].bulk_write(chunk, ordered=False)
                total_matched  += res.matched_count
                total_modified += res.modified_count
            print(f"  matched={total_matched:,}  modified={total_modified:,}  "
                  f"still-missed={len(ops) - total_matched:,}")

        # 5. Pipeline update: scoring_trend_l5_l20 = avg_scored_l5 - avg_scored_season
        # Phase 2A computes L5/L10/season but not L20; season avg is the best
        # available long-window proxy for the trend signal.
        if not dry_run:
            print("  Computing scoring_trend_l5_l20 via pipeline update…")
            res2 = await db[DST_COLL].update_many(
                {"sport": SPORT,
                 "avg_scored_l5":     {"$ne": None},
                 "avg_scored_season": {"$ne": None}},
                [{"$set": {"scoring_trend_l5_l20": {
                    "$subtract": ["$avg_scored_l5", "$avg_scored_season"]}}}],
            )
            print(f"  scoring_trend_l5_l20  "
                  f"matched={res2.matched_count:,}  "
                  f"modified={res2.modified_count:,}")
        else:
            print("  DRY-RUN — skipping scoring_trend_l5_l20 pipeline update.")

        print("  Done.")
    finally:
        mongo.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true",
                   help="Compute features but do not write to MongoDB.")
    p.add_argument("--bulk-chunk", type=int, default=500,
                   help="Bulk write batch size (default 500).")
    args = p.parse_args()
    asyncio.run(run(dry_run=args.dry_run, bulk_chunk=args.bulk_chunk))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
