"""
build_team_game_features.py — Build rolling team game features from BDL data.

Sport-agnostic: adding a new sport requires only a SportConfig entry in
SPORT_CONFIGS — no other code changes needed.

For each (team_id, game_date) pair found in team_matchups, computes:

Rolling boxscore features (strictly before game_date):
  - L5/L10/L20 means: scored, allowed, first_half_scored, total, first_period_scored
  - season averages: scored, allowed
  - first_period_score_rate — season % of games team scored in period 1

Season stats features: flat fields listed in SportConfig.season_stat_fields

Output: config.output_coll keyed by (config.team_id_field, game_date).

LEAKAGE GUARANTEE: all features use data strictly before game_date.

Usage:
    python -m scripts.sgo.build_team_game_features \\
        --sport mlb --start 2025-04-01 --end 2025-10-01
    python -m scripts.sgo.build_team_game_features \\
        --sport nba --start 2025-10-01 --end 2026-06-10
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
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

from scripts.sgo import get_bdl_team_mapping

log = logging.getLogger("build.team.game.features")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)

MATCHUPS_COLL = "team_matchups"
UPSERT_CHUNK = 500


# ---------------------------------------------------------------------------
# Sport configuration
# ---------------------------------------------------------------------------

@dataclass
class SportConfig:
    sport: str
    team_id_field: str           # e.g. 'mlb_team_id', 'nba_team_id'
    boxscores_coll: str          # e.g. 'bdl_mlb_game_boxscores'
    season_stats_coll: str       # e.g. 'bdl_mlb_team_season_stats'
    output_coll: str             # e.g. 'bdl_mlb_team_game_features'
    home_score_field: str        # field name for home score in boxscore
    away_score_field: str        # field name for away score in boxscore
    home_period_scores_field: str   # 'home_inning_scores' or 'home_quarter_scores'
    away_period_scores_field: str
    periods_for_first_half: int     # 5 for MLB (innings 1-5), 2 for NBA (q1+q2)
    season_stat_fields: List[str]   # flat field names to pull from season stats doc


SPORT_CONFIGS: Dict[str, SportConfig] = {
    'mlb': SportConfig(
        sport='mlb',
        team_id_field='mlb_team_id',
        boxscores_coll='bdl_mlb_game_boxscores',
        season_stats_coll='bdl_mlb_team_season_stats',
        output_coll='bdl_mlb_team_game_features',
        home_score_field='home_runs',
        away_score_field='away_runs',
        home_period_scores_field='home_inning_scores',
        away_period_scores_field='away_inning_scores',
        periods_for_first_half=5,
        season_stat_fields=[
            'batting_avg', 'batting_obp', 'batting_slg', 'batting_ops',
            'batting_ab', 'batting_so', 'batting_bb', 'batting_hr',
            'pitching_era', 'pitching_whip', 'pitching_ip', 'pitching_k',
            'pitching_bb', 'pitching_hr', 'pitching_qs', 'fielding_fp', 'gp',
        ],
    ),
    'nba': SportConfig(
        sport='nba',
        team_id_field='nba_team_id',
        boxscores_coll='bdl_nba_game_boxscores',
        season_stats_coll='bdl_nba_team_season_stats',
        output_coll='bdl_nba_team_game_features',
        home_score_field='home_score',
        away_score_field='away_score',
        home_period_scores_field='home_quarter_scores',
        away_period_scores_field='away_quarter_scores',
        periods_for_first_half=2,
        season_stat_fields=[
            'base_pts', 'base_w_pct', 'base_fg_pct', 'base_fg3_pct',
            'base_ft_pct', 'base_reb', 'base_ast', 'base_stl', 'base_blk',
            'base_tov', 'advanced_pace', 'advanced_off_rating', 'advanced_def_rating',
            'advanced_ts_pct', 'advanced_ast_pct', 'advanced_reb_pct',
            'advanced_net_rating', 'advanced_pie',
        ],
    ),
}


# ---------------------------------------------------------------------------
# Pure math helpers
# ---------------------------------------------------------------------------

def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _mean(vals: List[float]) -> Optional[float]:
    return round(sum(vals) / len(vals), 4) if vals else None


def _period_scores(doc: Dict[str, Any], field: str) -> List[int]:
    raw = doc.get(field) or []
    out: List[int] = []
    for v in raw:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            out.append(0)
    return out


# ---------------------------------------------------------------------------
# Boxscore expansion
# ---------------------------------------------------------------------------

def _expand_boxscore(
    doc: Dict[str, Any],
    bdl_team_id: int,
    config: SportConfig,
) -> Optional[Dict[str, Any]]:
    """Return a generic per-team game record from a boxscore doc, or None."""
    home_id = doc.get("home_team_id")
    away_id = doc.get("away_team_id")
    if bdl_team_id not in (home_id, away_id):
        return None

    is_home = bdl_team_id == home_id
    score_field   = config.home_score_field   if is_home else config.away_score_field
    opp_field     = config.away_score_field   if is_home else config.home_score_field
    periods_field = config.home_period_scores_field if is_home else config.away_period_scores_field

    scored  = _f(doc.get(score_field))
    allowed = _f(doc.get(opp_field))

    home_s = _f(doc.get(config.home_score_field))
    away_s = _f(doc.get(config.away_score_field))
    total  = (home_s + away_s) if (home_s is not None and away_s is not None) else None

    periods = _period_scores(doc, periods_field)
    first_period_scored = float(int(bool(periods and periods[0] > 0)))
    first_half_scored   = float(sum(periods[:config.periods_for_first_half])) if periods else 0.0

    return {
        "game_date":           (doc.get("game_date") or "")[:10],
        "season":              doc.get("season"),
        "scored":              scored,
        "allowed":             allowed,
        "total":               total,
        "first_period_scored": first_period_scored,
        "first_half_scored":   first_half_scored,
    }


# ---------------------------------------------------------------------------
# Rolling window feature computation (pure — no DB)
# ---------------------------------------------------------------------------

def compute_boxscore_features(
    prior_games: List[Dict[str, Any]],
    as_of_date: str,
    config: SportConfig,
) -> Dict[str, Any]:
    """
    Compute rolling boxscore features for one team as of as_of_date.
    `prior_games` must be sorted ascending by game_date.
    All features use data strictly before as_of_date (leakage-safe).
    """
    prior = [g for g in prior_games if g["game_date"] and g["game_date"] < as_of_date]
    season = int(as_of_date[:4])
    season_games = [g for g in prior if g.get("season") == season]

    feats: Dict[str, Any] = {}

    for field in ("scored", "allowed", "first_half_scored", "total", "first_period_scored"):
        for n, label in ((5, "l5"), (10, "l10"), (20, "l20")):
            window = prior[-n:] if len(prior) >= n else prior
            vals = [g[field] for g in window if g.get(field) is not None]
            feats[f"{field}_{label}"] = _mean(vals)

    for field in ("scored", "allowed"):
        vals = [g[field] for g in season_games if g.get(field) is not None]
        feats[f"{field}_season_avg"] = _mean(vals)

    if season_games:
        rate = sum(1 for g in season_games if g.get("first_period_scored", 0) > 0)
        feats["first_period_score_rate"] = round(rate / len(season_games), 4)
    else:
        feats["first_period_score_rate"] = None

    feats["games_prior_count"]  = len(prior)
    feats["season_games_count"] = len(season_games)

    return feats


# ---------------------------------------------------------------------------
# Season stats feature extraction (pure — no DB)
# ---------------------------------------------------------------------------

def compute_season_stat_features(
    season_doc: Optional[Dict[str, Any]],
    config: SportConfig,
) -> Dict[str, Any]:
    """Extract flat season stat fields defined in config.season_stat_fields."""
    if season_doc is None:
        return {}
    return {field: _f(season_doc.get(field)) for field in config.season_stat_fields}


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

async def _load_matchup_pairs(
    db: AsyncIOMotorDatabase,
    config: SportConfig,
    start: str,
    end: str,
) -> List[Tuple[str, str]]:
    """Return sorted unique (team_id, game_date) pairs from team_matchups."""
    pairs: set[Tuple[str, str]] = set()
    cursor = db[MATCHUPS_COLL].find(
        {
            "sport": config.sport,
            "game_date": {"$gte": start, "$lte": end},
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


async def _load_boxscores(
    db: AsyncIOMotorDatabase,
    config: SportConfig,
    end_date: str,
) -> Dict[int, List[Dict[str, Any]]]:
    """
    Load all boxscores up to end_date, grouped by BDL team_id (both sides).
    Returns {bdl_team_id: [game_records sorted asc by game_date]}.
    """
    projection = {
        "_id": 0,
        "game_date": 1, "season": 1,
        "home_team_id": 1, "away_team_id": 1,
        config.home_score_field: 1,
        config.away_score_field: 1,
        config.home_period_scores_field: 1,
        config.away_period_scores_field: 1,
    }

    by_team: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    cursor = db[config.boxscores_coll].find(
        {"game_date": {"$lte": end_date}},
        projection=projection,
    ).batch_size(5000)

    count = 0
    async for doc in cursor:
        count += 1
        for bdl_id in (doc.get("home_team_id"), doc.get("away_team_id")):
            if bdl_id is None:
                continue
            record = _expand_boxscore(doc, bdl_id, config)
            if record is None:
                continue
            by_team[bdl_id].append(record)

    for records in by_team.values():
        records.sort(key=lambda r: r["game_date"])

    log.info("boxscores loaded: %d docs → %d teams", count, len(by_team))
    return dict(by_team)


async def _load_season_stats(
    db: AsyncIOMotorDatabase,
    config: SportConfig,
) -> Dict[Tuple[int, int], Dict[str, Any]]:
    """
    Load regular-season team stats from config.season_stats_coll.
    Returns {(bdl_team_id, season): doc}.
    """
    index: Dict[Tuple[int, int], Dict[str, Any]] = {}
    cursor = db[config.season_stats_coll].find(
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

async def _ensure_indexes(db: AsyncIOMotorDatabase, config: SportConfig) -> None:
    try:
        await db[config.output_coll].create_index(
            [(config.team_id_field, 1), ("game_date", 1)],
            unique=True,
            name="uniq_team_date",
            background=True,
        )
        await db[config.output_coll].create_index(
            [("game_date", 1)],
            name="game_date_1",
            background=True,
        )
        await db[config.output_coll].create_index(
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
    config: SportConfig,
    start_date: str,
    end_date: str,
) -> Dict[str, Any]:
    log.info(
        "[%s] building team game features  %s → %s  → %s",
        config.sport.upper(), start_date, end_date, config.output_coll,
    )

    await _ensure_indexes(db, config)

    bdl_map = get_bdl_team_mapping(config.sport)   # {bdl_int: canonical_str}
    inv_map = {v: k for k, v in bdl_map.items()}   # {canonical_str: bdl_int}

    pairs = await _load_matchup_pairs(db, config, start_date, end_date)
    log.info("[%s] matchup pairs to compute: %d", config.sport.upper(), len(pairs))
    if not pairs:
        log.warning("[%s] no matchup pairs found — nothing to do", config.sport.upper())
        return {"pairs": 0, "written": 0, "skipped_no_bdl_id": 0}

    boxscores   = await _load_boxscores(db, config, end_date)
    stats_index = await _load_season_stats(db, config)

    counters = {
        "pairs":             len(pairs),
        "written":           0,
        "skipped_no_bdl_id": 0,
    }
    pending: List[UpdateOne] = []

    async def _flush() -> None:
        if not pending:
            return
        res = await db[config.output_coll].bulk_write(pending, ordered=False)
        counters["written"] += (res.upserted_count or 0) + (res.modified_count or 0)
        pending.clear()

    for idx, (team_id, game_date) in enumerate(pairs, 1):
        bdl_team_id = inv_map.get(team_id)
        if bdl_team_id is None:
            counters["skipped_no_bdl_id"] += 1
            log.debug("no BDL id for %s — skipping", team_id)
            continue

        season = int(game_date[:4])

        team_games = boxscores.get(bdl_team_id, [])
        box_feats  = compute_boxscore_features(team_games, game_date, config)

        season_doc = stats_index.get((bdl_team_id, season))
        stat_feats = compute_season_stat_features(season_doc, config)

        doc: Dict[str, Any] = {
            config.team_id_field: team_id,
            "bdl_team_id":        bdl_team_id,
            "game_date":          game_date,
            "season":             season,
            "sport":              config.sport,
            "computed_at":        datetime.now(timezone.utc),
            **box_feats,
            **stat_feats,
        }

        if config.sport == 'mlb':
            doc['runs_scored_l5']        = doc.get('scored_l5')
            doc['runs_scored_l10']       = doc.get('scored_l10')
            doc['runs_scored_l20']       = doc.get('scored_l20')
            doc['runs_allowed_l5']       = doc.get('allowed_l5')
            doc['runs_allowed_l10']      = doc.get('allowed_l10')
            doc['runs_allowed_l20']      = doc.get('allowed_l20')
            doc['first5_runs_l5']        = doc.get('first_half_scored_l5')
            doc['first5_runs_l10']       = doc.get('first_half_scored_l10')
            doc['first5_runs_l20']       = doc.get('first_half_scored_l20')
            doc['total_runs_l10_avg']    = doc.get('total_l10')
            doc['first_inning_score_rate'] = doc.get('first_period_score_rate')

        pending.append(
            UpdateOne(
                {config.team_id_field: team_id, "game_date": game_date},
                {"$set": doc},
                upsert=True,
            )
        )

        if len(pending) >= UPSERT_CHUNK:
            await _flush()

        if idx % 1000 == 0:
            log.info(
                "[%s] progress: %d/%d pairs processed, %d written",
                config.sport.upper(), idx, len(pairs), counters["written"],
            )

    await _flush()
    log.info(
        "[%s] done — pairs=%d  written=%d  skipped_no_bdl_id=%d",
        config.sport.upper(),
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
        description="Build rolling team game features from BDL data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--sport", required=True, choices=list(SPORT_CONFIGS),
        help="Sport to build features for.",
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
        await build_features(db, SPORT_CONFIGS[args.sport], args.start, args.end)
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    _args = parse_args()
    raise SystemExit(asyncio.run(amain(_args)))
