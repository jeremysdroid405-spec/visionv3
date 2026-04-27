"""
NBA Pick History — Result Updater
==================================
Idempotent. Finds every row in `nba_pick_history` where `hit is None`,
joins it to actual game logs, and updates {actual, result, hit}.

Game-log lookup order:
  1. nba_master_hub_2026.bdl_game_logs   (current season — fastest hit)
  2. bdl_historical_game_logs            (prior seasons)
  3. nba_player_game_logs                (legacy / fallback)

Stat extraction:
  PTS  → log.pts
  REB  → log.reb
  AST  → log.ast
  3PM  → log.fg3m
  PRA  → pts + reb + ast

Run safely as often as you like (cron / CLI); only ungraded rows are
touched. Already-graded rows are not re-evaluated.

CLI:
    python -m scripts.update_nba_pick_results        # update everything
    python -m scripts.update_nba_pick_results --dry  # report-only
    python -m scripts.update_nba_pick_results --since 2026-04-01
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("update_nba_pick_results")

PICK_COLLECTION = "nba_pick_history"
HUB_COLLECTION = "nba_master_hub_2026"
HISTORICAL_LOGS = "bdl_historical_game_logs"
LEGACY_LOGS = "nba_player_game_logs"

STAT_FIELDS = {
    "PTS": ("pts",),
    "REB": ("reb",),
    "AST": ("ast",),
    "3PM": ("fg3m",),
    "THREES": ("fg3m",),     # canonical family alias
    "PRA": ("pts", "reb", "ast"),
    "PTS_REB": ("pts", "reb"),
    "PTS_AST": ("pts", "ast"),
    "REB_AST": ("reb", "ast"),
    "STL": ("stl",),
    "BLK": ("blk",),
    "TURNOVERS": ("turnover",),
}


def _f(v: Any) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _to_date(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    s = str(v)
    return s[:10] if len(s) >= 10 else None


def _stat_value(stat: str, log: Dict[str, Any]) -> Optional[float]:
    fields = STAT_FIELDS.get(stat.upper())
    if not fields:
        return None
    out = 0.0
    for f in fields:
        v = _f(log.get(f))
        if v is None:
            return None
        out += v
    return out


async def _build_log_index(db) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """{name_lower → {date_str → log_dict}}.

    Builds once per run from the three log sources.
    Newer source wins on date collisions (hub > historical > legacy)."""
    by_name: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)

    # Legacy (lowest priority — write first so newer overwrites)
    if LEGACY_LOGS in await db.list_collection_names():
        async for d in db[LEGACY_LOGS].find(
            {}, {"_id": 0, "player_name": 1, "date": 1,
                 "pts": 1, "reb": 1, "ast": 1, "fg3m": 1}):
            nm = (d.get("player_name") or "").strip().lower()
            date = _to_date(d.get("date"))
            if nm and date:
                by_name[nm][date] = d

    # Historical
    async for d in db[HISTORICAL_LOGS].find(
        {}, {"_id": 0, "player_name": 1, "date": 1,
             "pts": 1, "reb": 1, "ast": 1, "fg3m": 1}):
        nm = (d.get("player_name") or "").strip().lower()
        date = _to_date(d.get("date"))
        if nm and date:
            by_name[nm][date] = d

    # Current-season hub (highest priority)
    async for d in db[HUB_COLLECTION].find(
        {"bdl_game_logs_count": {"$gt": 0}},
        {"_id": 0, "player_name": 1, "display_name": 1,
         "normalized_name": 1, "bdl_game_logs": 1}):
        names = [n for n in (d.get("display_name"),
                              d.get("player_name"),
                              d.get("normalized_name")) if n]
        for lg in (d.get("bdl_game_logs") or []):
            date = _to_date(lg.get("date") or lg.get("game_date"))
            if not date:
                continue
            for n in names:
                by_name[n.strip().lower()][date] = lg
    return by_name


def _grade(line: float, side: str, actual: float) -> Dict[str, Any]:
    """Compute hit/result for a settled prop. Push (==) loses by PrizePicks
    convention; sportsbook -110 splits on push but we treat OVER ==
    line as a miss to be consistent with the existing forward-test
    grading. Side "OVER" wins iff actual > line, "UNDER" wins iff
    actual < line."""
    side = (side or "OVER").upper()
    if side == "OVER":
        won = actual > line
        result = "OVER" if actual > line else "UNDER"
    else:
        won = actual < line
        result = "UNDER" if actual < line else "OVER"
    return {"actual": float(actual), "result": result, "hit": bool(won)}


async def update_results(db, *, since: Optional[str] = None,
                          dry_run: bool = False) -> Dict[str, int]:
    coll = db[PICK_COLLECTION]
    log_index = await _build_log_index(db)
    logger.info(f"Indexed game logs for {len(log_index):,} players")

    query: Dict[str, Any] = {"hit": None}
    if since:
        query["game_date"] = {"$gte": since}

    cursor = coll.find(query)
    n_total = updated = no_log = no_actual = errors = 0
    from pymongo import UpdateOne
    ops: List[Any] = []

    async for pick in cursor:
        n_total += 1
        nm = (pick.get("player") or "").strip().lower()
        date = pick.get("game_date")
        stat = pick.get("stat")
        line = pick.get("line")
        side = pick.get("side")

        if not (nm and date and stat and line is not None and side):
            errors += 1
            continue

        log = log_index.get(nm, {}).get(date)
        if not log:
            no_log += 1
            continue

        actual = _stat_value(stat, log)
        if actual is None:
            no_actual += 1
            continue

        graded = _grade(float(line), side, actual)
        ops.append(UpdateOne(
            {"_id": pick["_id"]},
            {"$set": graded},
        ))
        updated += 1

    if dry_run:
        logger.info(
            f"[DRY] would-grade={updated} no_log={no_log} "
            f"no_actual={no_actual} errors={errors} scanned={n_total}"
        )
        return {"updated": 0, "no_log": no_log,
                "no_actual": no_actual, "errors": errors,
                "scanned": n_total}

    if ops:
        result = await coll.bulk_write(ops, ordered=False)
        updated_real = result.modified_count or 0
    else:
        updated_real = 0

    logger.info(
        f"updated={updated_real} no_log={no_log} no_actual={no_actual} "
        f"errors={errors} scanned={n_total}"
    )
    return {"updated": updated_real, "no_log": no_log,
            "no_actual": no_actual, "errors": errors,
            "scanned": n_total}


async def _amain() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true",
                   help="Report-only. Don't update anything.")
    p.add_argument("--since", default=None,
                   help="ISO date (YYYY-MM-DD). Only grade rows on or after.")
    args = p.parse_args()

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    await update_results(db, since=args.since, dry_run=args.dry)


if __name__ == "__main__":
    asyncio.run(_amain())
