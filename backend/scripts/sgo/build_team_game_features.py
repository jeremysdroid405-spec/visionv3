"""
build_team_game_features.py — Build rolling team game features from BDL MLB data.

For each (mlb_team_id, game_date) pair found in team_matchups, computes:

Rolling boxscore features (bdl_mlb_game_boxscores, strictly before game_date):
  - L5/L10/L20 means: runs_scored, runs_allowed, hits, first_inning_scored,
    first5_runs
  - Season averages: runs_scored, runs_allowed, hits per game
  - first_inning_score_rate — % of season games team scored in inning 1
  - total_runs_l10_avg — average total runs per game over last 10 games

Season stats features (bdl_mlb_team_season_stats, same calendar season):
  - batting_avg, batting_obp, batting_slg, batting_ops
  - batting_k_rate, batting_bb_rate, batting_hr_rate
  - pitching_era, pitching_whip
  - pitching_k_per_9, pitching_bb_per_9, pitching_hr_per_9
  - pitching_qs_rate, fielding_fp

Output: bdl_mlb_team_game_features keyed by (mlb_team_id, game_date).

LEAKAGE GUARANTEE: all features use data strictly before game_date.

Usage:
    python -m scripts.sgo.build_team_game_features \\
        --sport mlb --start 2025-04-01 --end 2025-10-01
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections import defaultdict
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
from pymongo import UpdateOne

from scripts.sgo import BDL_TEAM_ID_TO_MLG_TEAM_ID

log = logging.getLogger("build.team.game.features")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)

BOXSCORES_COLL = "bdl_mlb_game_boxscores"
SEASON_STATS_COLL = "bdl_mlb_team_season_stats"
MATCHUPS_COLL = "team_matchups"
OUTPUT_COLL = "bdl_mlb_team_game_features"

# Inverse mapping: "mlb_ari" → 1 (BDL integer)
_MLG_TO_BDL: Dict[str, int] = {v: k for k, v in BDL_TEAM_ID_TO_MLG_TEAM_ID.items()}

UPSERT_CHUNK = 500


# ---------------------------------------------------------------------------
# Pure math helpers
# ---------------------------------------------------------------------------

def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


def _mean(vals: List[float]) -> Optional[float]:
    return round(sum(vals) / len(vals), 4) if vals else None


def _safe_div(num: Optional[float], denom: Optional[float]) -> Optional[float]:
    if num is None or denom is None or denom == 0.0:
        return None
    return round(num / denom, 4)


def _per_9(stat: Optional[float], ip: Optional[float]) -> Optional[float]:
    """Compute per-9-innings rate: stat / ip * 9."""
    if stat is None or not ip or ip <= 0.0:
        return None
    return round(stat / ip * 9.0, 4)


# ---------------------------------------------------------------------------
# Boxscore expansion — turn one bdl_mlb_game_boxscores doc into a team record
# ---------------------------------------------------------------------------

def _inning_scores(doc: Dict[str, Any], side: str) -> List[int]:
    raw = doc.get(f"{side}_inning_scores") or []
    out: List[int] = []
    for v in raw:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            out.append(0)
    return out


def _expand_game(doc: Dict[str, Any], bdl_team_id: int) -> Optional[Dict[str, Any]]:
    """
    Return a per-team game record for the given BDL team_id from a boxscore doc,
    or None if the team isn't in this game or required fields are missing.
    """
    home_id = doc.get("home_team_id")
    away_id = doc.get("away_team_id")
    if bdl_team_id not in (home_id, away_id):
        return None

    is_home = bdl_team_id == home_id
    side = "home" if is_home else "away"
    opp  = "away" if is_home else "home"

    runs_scored  = _f(doc.get(f"{side}_runs"))
    runs_allowed = _f(doc.get(f"{opp}_runs"))
    hits         = _f(doc.get(f"{side}_hits"))

    innings = _inning_scores(doc, side)
    first_inning_scored = 1 if (innings and innings[0] > 0) else 0
    first5_runs = float(sum(innings[:5])) if innings else 0.0

    home_r = _f(doc.get("home_runs"))
    away_r = _f(doc.get("away_runs"))
    total_runs = (home_r + away_r) if (home_r is not None and away_r is not None) else None

    return {
        "game_date":           (doc.get("game_date") or "")[:10],
        "season":              doc.get("season"),
        "runs_scored":         runs_scored,
        "runs_allowed":        runs_allowed,
        "hits":                hits,
        "first_inning_scored": float(first_inning_scored),
        "first5_runs":         first5_runs,
        "total_runs":          total_runs,
    }


# ---------------------------------------------------------------------------
# Rolling window feature computation (pure — no DB)
# ---------------------------------------------------------------------------

def compute_boxscore_features(
    games: List[Dict[str, Any]],
    *,
    as_of_date: str,
    season: int,
) -> Dict[str, Any]:
    """
    Compute all rolling boxscore features for one team as of as_of_date.
    `games` must be sorted ascending by game_date.
    All features use data strictly before as_of_date (leakage-safe).
    Season-level features are scoped to `season`.
    """
    prior = [g for g in games if g["game_date"] and g["game_date"] < as_of_date]
    season_games = [g for g in prior if g.get("season") == season]

    feats: Dict[str, Any] = {}

    # L5 / L10 / L20 rolling means (most-recent-first slice from tail)
    for field in ("runs_scored", "runs_allowed", "hits", "first_inning_scored", "first5_runs"):
        for n, label in ((5, "l5"), (10, "l10"), (20, "l20")):
            window = prior[-n:] if len(prior) >= n else prior
            vals = [g[field] for g in window if g.get(field) is not None]
            feats[f"{field}_{label}"] = _mean(vals)

    # Season averages
    for field in ("runs_scored", "runs_allowed", "hits"):
        vals = [g[field] for g in season_games if g.get(field) is not None]
        feats[f"{field}_season_avg"] = _mean(vals)

    # first_inning_score_rate — season scope
    if season_games:
        rate = sum(1 for g in season_games if g.get("first_inning_scored", 0) > 0)
        feats["first_inning_score_rate"] = round(rate / len(season_games), 4)
    else:
        feats["first_inning_score_rate"] = None

    # total_runs_l10_avg — last 10 games (any season)
    total_window = prior[-10:] if len(prior) >= 10 else prior
    total_vals = [g["total_runs"] for g in total_window if g.get("total_runs") is not None]
    feats["total_runs_l10_avg"] = _mean(total_vals)

    feats["games_prior_count"]  = len(prior)
    feats["season_games_count"] = len(season_games)

    return feats


# ---------------------------------------------------------------------------
# Season stats feature extraction (pure — no DB)
# ---------------------------------------------------------------------------

def compute_season_stat_features(season_doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Derive rate stats from a bdl_mlb_team_season_stats document."""
    feats: Dict[str, Any] = {}
    if season_doc is None:
        return feats

    gp = _f(season_doc.get("gp"))
    ab = _f(season_doc.get("batting_ab"))
    ip = _f(season_doc.get("pitching_ip"))

    feats["batting_avg"] = _f(season_doc.get("batting_avg"))
    feats["batting_obp"] = _f(season_doc.get("batting_obp"))
    feats["batting_slg"] = _f(season_doc.get("batting_slg"))
    feats["batting_ops"] = _f(season_doc.get("batting_ops"))
    feats["batting_k_rate"]  = _safe_div(_f(season_doc.get("batting_so")), ab)
    feats["batting_bb_rate"] = _safe_div(_f(season_doc.get("batting_bb")), ab)
    feats["batting_hr_rate"] = _safe_div(_f(season_doc.get("batting_hr")), ab)
    feats["pitching_era"]    = _f(season_doc.get("pitching_era"))
    feats["pitching_whip"]   = _f(season_doc.get("pitching_whip"))
    feats["pitching_k_per_9"]  = _per_9(_f(season_doc.get("pitching_k")), ip)
    feats["pitching_bb_per_9"] = _per_9(_f(season_doc.get("pitching_bb")), ip)
    feats["pitching_hr_per_9"] = _per_9(_f(season_doc.get("pitching_hr")), ip)
    feats["pitching_qs_rate"]  = _safe_div(_f(season_doc.get("pitching_qs")), gp)
    feats["fielding_fp"]       = _f(season_doc.get("fielding_fp"))

    return feats


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

async def load_matchup_pairs(
    db: AsyncIOMotorDatabase,
    *,
    sport: str,
    start_date: str,
    end_date: str,
) -> List[Tuple[str, str]]:
    """
    Return sorted unique (mlb_team_id, game_date) pairs from team_matchups
    for the given sport and date window (inclusive).
    """
    pairs: set[Tuple[str, str]] = set()
    cursor = db[MATCHUPS_COLL].find(
        {
            "sport": sport,
            "game_date": {"$gte": start_date, "$lte": end_date},
        },
        projection={"_id": 0, "home_team_id": 1, "away_team_id": 1, "game_date": 1},
    ).batch_size(5000)

    async for m in cursor:
        gd = (m.get("game_date") or "")[:10]
        if not gd:
            continue
        for key in ("home_team_id", "away_team_id"):
            tid = m.get(key)
            if tid and isinstance(tid, str):
                pairs.add((tid, gd))

    return sorted(pairs)


async def load_all_boxscores(
    db: AsyncIOMotorDatabase,
    *,
    end_date: str,
) -> Dict[int, List[Dict[str, Any]]]:
    """
    Load all bdl_mlb_game_boxscores with game_date <= end_date,
    grouped by BDL team_id (both home and away).

    Returns {bdl_team_id: [game_records sorted asc by game_date]}.
    """
    by_team: Dict[int, List[Dict[str, Any]]] = defaultdict(list)

    cursor = db[BOXSCORES_COLL].find(
        {"game_date": {"$lte": end_date}},
        projection={
            "_id": 0,
            "game_id": 1, "game_date": 1, "season": 1,
            "home_team_id": 1, "away_team_id": 1,
            "home_runs": 1, "away_runs": 1,
            "home_hits": 1, "away_hits": 1,
            "home_inning_scores": 1, "away_inning_scores": 1,
        },
    ).batch_size(5000)

    count = 0
    async for doc in cursor:
        count += 1
        for side_id in (doc.get("home_team_id"), doc.get("away_team_id")):
            if side_id is None:
                continue
            record = _expand_game(doc, side_id)
            if record is None:
                continue
            by_team[side_id].append(record)

    # Sort each team's games ascending by game_date
    for records in by_team.values():
        records.sort(key=lambda r: r["game_date"])

    log.info("boxscores loaded: %d docs → %d teams", count, len(by_team))
    return dict(by_team)


async def load_all_season_stats(
    db: AsyncIOMotorDatabase,
) -> Dict[Tuple[int, int], Dict[str, Any]]:
    """
    Load all bdl_mlb_team_season_stats (regular season).
    Returns {(bdl_team_id, season): doc}.
    """
    index: Dict[Tuple[int, int], Dict[str, Any]] = {}

    cursor = db[SEASON_STATS_COLL].find(
        {"season_type": "regular"},
    ).batch_size(2000)

    async for doc in cursor:
        team_id = doc.get("team_id")
        season  = doc.get("season")
        if team_id is not None and season is not None:
            index[(int(team_id), int(season))] = doc

    log.info("season stats loaded: %d records", len(index))
    return index


# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------

async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    try:
        await db[OUTPUT_COLL].create_index(
            [("mlb_team_id", 1), ("game_date", 1)],
            unique=True,
            name="uniq_team_date",
            background=True,
        )
        await db[OUTPUT_COLL].create_index(
            [("game_date", 1)],
            name="game_date_1",
            background=True,
        )
        await db[OUTPUT_COLL].create_index(
            [("sport", 1), ("game_date", 1)],
            name="sport_game_date",
            background=True,
        )
    except Exception as exc:
        log.warning("index creation non-fatal: %s", exc)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def build_features(
    db: AsyncIOMotorDatabase,
    *,
    sport: str,
    start_date: str,
    end_date: str,
) -> Dict[str, Any]:
    log.info(
        "[%s] building team game features  %s → %s  → %s",
        sport.upper(), start_date, end_date, OUTPUT_COLL,
    )

    await ensure_indexes(db)

    pairs = await load_matchup_pairs(
        db, sport=sport, start_date=start_date, end_date=end_date)
    log.info("[%s] matchup pairs to compute: %d", sport.upper(), len(pairs))
    if not pairs:
        log.warning("[%s] no matchup pairs found — nothing to do", sport.upper())
        return {"pairs": 0, "written": 0, "skipped_no_bdl_id": 0}

    boxscores  = await load_all_boxscores(db, end_date=end_date)
    stats_index = await load_all_season_stats(db)

    counters = {
        "pairs":              len(pairs),
        "written":            0,
        "skipped_no_bdl_id":  0,
    }

    pending: List[UpdateOne] = []

    async def _flush() -> None:
        if not pending:
            return
        res = await db[OUTPUT_COLL].bulk_write(pending, ordered=False)
        counters["written"] += (res.upserted_count or 0) + (res.modified_count or 0)
        pending.clear()

    for idx, (mlb_team_id, game_date) in enumerate(pairs, 1):
        bdl_team_id = _MLG_TO_BDL.get(mlb_team_id)
        if bdl_team_id is None:
            counters["skipped_no_bdl_id"] += 1
            log.debug("no BDL id for %s — skipping", mlb_team_id)
            continue

        season = int(game_date[:4])

        # --- boxscore rolling features ---
        team_games = boxscores.get(bdl_team_id, [])
        box_feats = compute_boxscore_features(
            team_games, as_of_date=game_date, season=season)

        # --- season stat features ---
        season_doc = stats_index.get((bdl_team_id, season))
        stat_feats = compute_season_stat_features(season_doc)

        doc: Dict[str, Any] = {
            "mlb_team_id":  mlb_team_id,
            "bdl_team_id":  bdl_team_id,
            "game_date":    game_date,
            "season":       season,
            "sport":        sport,
            "computed_at":  datetime.now(timezone.utc),
            **box_feats,
            **stat_feats,
        }

        pending.append(
            UpdateOne(
                {"mlb_team_id": mlb_team_id, "game_date": game_date},
                {"$set": doc},
                upsert=True,
            )
        )

        if len(pending) >= UPSERT_CHUNK:
            await _flush()

        if idx % 1000 == 0:
            log.info(
                "[%s] progress: %d/%d pairs processed, %d written",
                sport.upper(), idx, len(pairs), counters["written"],
            )

    await _flush()
    log.info(
        "[%s] done — pairs=%d  written=%d  skipped_no_bdl_id=%d",
        sport.upper(),
        counters["pairs"],
        counters["written"],
        counters["skipped_no_bdl_id"],
    )
    return counters


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build rolling team game features from BDL MLB data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--sport", required=True, choices=["mlb"],
        help="Sport to build features for (currently only mlb supported).",
    )
    p.add_argument(
        "--start", required=True, metavar="YYYY-MM-DD",
        help="Start date (inclusive) — filters team_matchups game_date.",
    )
    p.add_argument(
        "--end", required=True, metavar="YYYY-MM-DD",
        help="End date (inclusive) — filters team_matchups game_date.",
    )
    return p.parse_args()


async def amain(args: argparse.Namespace) -> int:
    for label, val in (("--start", args.start), ("--end", args.end)):
        try:
            datetime.strptime(val, "%Y-%m-%d")
        except ValueError:
            log.error("%s must be YYYY-MM-DD, got: %s", label, val)
            return 1
    if args.start > args.end:
        log.error("--start must be <= --end")
        return 1

    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name   = os.environ.get("DB_NAME", "pick_vision")
    client    = AsyncIOMotorClient(mongo_url)
    db        = client[db_name]

    try:
        await build_features(
            db,
            sport=args.sport,
            start_date=args.start,
            end_date=args.end,
        )
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    _args = parse_args()
    raise SystemExit(asyncio.run(amain(_args)))
