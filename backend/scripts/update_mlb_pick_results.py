"""
MLB Pick History — Result Updater (Total Bases v1)
====================================================
Idempotent. Finds every row in `mlb_pick_history` where `hit is None`,
joins it to actual game logs, and updates {actual, result, hit}.

Game-log lookup order:
  1. mlb_master_hub_2026.bdl_game_logs   (current season — fastest hit)
  2. mlb_historical_logs                 (multi-season fallback)

Stat extraction:
  TOTAL_BASES → log.total_bases  (computed if absent:
                                  1·1B + 2·2B + 3·3B + 4·HR)

Run as often as you like (cron / CLI); only ungraded rows are touched.
Already-graded rows are left untouched.

CLI:
    python -m scripts.update_mlb_pick_results
    python -m scripts.update_mlb_pick_results --dry
    python -m scripts.update_mlb_pick_results --since 2026-04-25
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
logger = logging.getLogger("update_mlb_pick_results")

PICK_COLLECTION = "mlb_pick_history"
HUB_COLLECTION = "mlb_master_hub_2026"
HISTORICAL_LOGS = "mlb_historical_logs"


def _f(v: Any) -> Optional[float]:
    if v in (None, "", "None"): return None
    try: return float(v)
    except (TypeError, ValueError): return None


def _to_date(v: Any) -> Optional[str]:
    if v is None: return None
    if isinstance(v, datetime): return v.strftime("%Y-%m-%d")
    s = str(v)
    return s[:10] if len(s) >= 10 else None


def _total_bases(log: Dict[str, Any]) -> Optional[float]:
    """Read `total_bases` directly when stored; else derive it.
       TB = 1·1B + 2·2B + 3·3B + 4·HR  (singles = hits − doubles − triples − HR)."""
    tb = _f(log.get("total_bases"))
    if tb is not None: return tb
    hits    = _f(log.get("hits"))
    doubles = _f(log.get("doubles"))
    triples = _f(log.get("triples"))
    hr      = _f(log.get("home_runs"))
    if hits is None: return None
    d = doubles or 0.0; t = triples or 0.0; h_r = hr or 0.0
    singles = max(0.0, hits - d - t - h_r)
    return 1*singles + 2*d + 3*t + 4*h_r


async def _build_log_index(db) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """{name_lower → {date_str → log_dict}}, hub > historical priority."""
    by_name: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)

    # Historical (lower priority — written first)
    if HISTORICAL_LOGS in await db.list_collection_names():
        async for d in db[HISTORICAL_LOGS].find(
            {}, {"_id": 0, "player_name": 1, "date": 1, "game_date": 1,
                 "total_bases": 1, "hits": 1, "doubles": 1, "triples": 1,
                 "home_runs": 1}):
            nm = (d.get("player_name") or "").strip().lower()
            date = _to_date(d.get("date") or d.get("game_date"))
            if nm and date: by_name[nm][date] = d

    # Current-season hub (higher priority — overwrites historical)
    async for d in db[HUB_COLLECTION].find(
        {"is_batter": True, "bdl_game_logs_count": {"$gt": 0}},
        {"_id": 0, "player_name": 1, "display_name": 1,
         "bdl_game_logs": 1}):
        names = [n for n in (d.get("display_name"), d.get("player_name"))
                 if n]
        for lg in (d.get("bdl_game_logs") or []):
            date = _to_date(lg.get("date") or lg.get("game_date"))
            if not date: continue
            for n in names:
                by_name[n.strip().lower()][date] = lg
    return by_name


def _grade(line: float, side: str, actual: float) -> Dict[str, Any]:
    """OVER wins iff actual > line; UNDER wins iff actual < line.
    Push (actual == line) is treated as a miss for the OVER side and
    as a miss for the UNDER side (PrizePicks-faithful: pushes lose)."""
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
    logger.info(f"Indexed game logs for {len(log_index):,} batters")

    query: Dict[str, Any] = {"hit": None}
    if since: query["game_date"] = {"$gte": since}

    cursor = coll.find(query)
    n_total = updated = no_log = no_actual = errors = 0
    from pymongo import UpdateOne
    ops: List[Any] = []

    async for pick in cursor:
        n_total += 1
        nm = (pick.get("player") or "").strip().lower()
        date = pick.get("game_date")
        line = pick.get("line")
        side = pick.get("side")
        if not (nm and date and line is not None and side):
            errors += 1; continue
        log = log_index.get(nm, {}).get(date)
        if not log: no_log += 1; continue
        actual = _total_bases(log)
        if actual is None: no_actual += 1; continue
        graded = _grade(float(line), side, actual)
        ops.append(UpdateOne({"_id": pick["_id"]}, {"$set": graded}))
        updated += 1

    if dry_run:
        logger.info(
            f"[DRY] would-grade={updated} no_log={no_log} "
            f"no_actual={no_actual} errors={errors} scanned={n_total}")
        return {"updated": 0, "no_log": no_log,
                "no_actual": no_actual, "errors": errors,
                "scanned": n_total}

    updated_real = 0
    if ops:
        result = await coll.bulk_write(ops, ordered=False)
        updated_real = result.modified_count or 0

    logger.info(
        f"updated={updated_real} no_log={no_log} no_actual={no_actual} "
        f"errors={errors} scanned={n_total}")
    return {"updated": updated_real, "no_log": no_log,
            "no_actual": no_actual, "errors": errors,
            "scanned": n_total}


async def _amain() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true",
                    help="Report-only. Don't update anything.")
    p.add_argument("--since", default=None,
                    help="ISO date (YYYY-MM-DD). Only grade rows >= this.")
    args = p.parse_args()
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    await update_results(db, since=args.since, dry_run=args.dry)


if __name__ == "__main__":
    asyncio.run(_amain())
