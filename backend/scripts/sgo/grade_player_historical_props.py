"""
grade_player_historical_props.py — grade unresolved NBA player prop rows in
sgo_propvision_full_pipeline_replay by joining to actual BDL box score stats.

Sources:
  sgo_propvision_full_pipeline_replay  — unresolved player rows
  bdl_historical_game_logs             — per-player per-game actuals
  nba_master_hub_2026                  — player identity (normalized_name → bdl_player_id)

Usage:
    python -m scripts.sgo.grade_player_historical_props --sport nba
    python -m scripts.sgo.grade_player_historical_props --sport nba --dry-run
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for _env in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(_env):
        load_dotenv(_env)
        break

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne

REPLAY_COLL = "sgo_propvision_full_pipeline_replay"
BDL_COLL    = "bdl_historical_game_logs"
HUB_COLL    = "nba_master_hub_2026"

BATCH_SIZE  = 5_000
LOG_EVERY   = 100_000

# ── stat resolver: stat_family → (bdl_doc → float | None) ──────────────────
def _safe(r: Dict[str, Any], *keys: str) -> Optional[float]:
    for k in keys:
        v = r.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def _sum(*vals: Optional[float]) -> Optional[float]:
    if any(v is None for v in vals):
        return None
    return float(sum(vals))  # type: ignore[arg-type]


STAT_MAP: Dict[str, Callable[[Dict[str, Any]], Optional[float]]] = {
    "pts":           lambda r: _safe(r, "pts"),
    "points":        lambda r: _safe(r, "pts"),
    "reb":           lambda r: _safe(r, "reb"),
    "rebounds":      lambda r: _safe(r, "reb"),
    "ast":           lambda r: _safe(r, "ast"),
    "assists":       lambda r: _safe(r, "ast"),
    "threes":        lambda r: _safe(r, "fg3m"),
    "threes_made":   lambda r: _safe(r, "fg3m"),
    "blk":           lambda r: _safe(r, "blk"),
    "blocks":        lambda r: _safe(r, "blk"),
    "stl":           lambda r: _safe(r, "stl"),
    "steals":        lambda r: _safe(r, "stl"),
    "turnovers":     lambda r: _safe(r, "turnover") if r.get("turnover") is not None
                               else float(0),
    "pra":           lambda r: _sum(_safe(r, "pts"), _safe(r, "reb"), _safe(r, "ast")),
    "pts_reb":       lambda r: _sum(_safe(r, "pts"), _safe(r, "reb")),
    "pts_ast":       lambda r: _sum(_safe(r, "pts"), _safe(r, "ast")),
    "reb_ast":       lambda r: _sum(_safe(r, "reb"), _safe(r, "ast")),
    "blocks_steals": lambda r: _sum(_safe(r, "blk"), _safe(r, "stl")),
}


# ── index builders ──────────────────────────────────────────────────────────
async def build_hub_index(db) -> Dict[str, int]:
    """normalized_name → bdl_player_id"""
    idx: Dict[str, int] = {}
    async for doc in db[HUB_COLL].find(
        {"bdl_player_id": {"$exists": True, "$ne": None}},
        {"normalized_name": 1, "bdl_player_id": 1, "_id": 0},
    ):
        name = doc.get("normalized_name", "").strip().lower()
        bdl_id = doc.get("bdl_player_id")
        if name and bdl_id is not None:
            idx[name] = int(bdl_id)
    return idx


async def build_bdl_index(db) -> Dict[Tuple[int, str], Dict[str, Any]]:
    """(bdl_player_id, date) → stats doc.

    Primary source: bdl_game_logs embedded in nba_master_hub_2026 (covers
    the 2025-26 season, Oct 2025–present). Secondary/historical source:
    bdl_historical_game_logs collection (covers 2020-21 through 2024-25).
    """
    idx: Dict[Tuple[int, str], Dict[str, Any]] = {}

    # Historical standalone collection
    async for doc in db[BDL_COLL].find({}, {"_id": 0}):
        pid = doc.get("player_id")
        dt  = doc.get("date", "")
        if pid is not None and dt:
            idx[(int(pid), str(dt))] = doc

    # Current-season logs embedded in master hub (higher priority; written last)
    async for hub in db[HUB_COLL].find(
        {"bdl_game_logs": {"$exists": True, "$not": {"$size": 0}}},
        {"bdl_game_logs": 1, "_id": 0},
    ):
        for entry in hub.get("bdl_game_logs") or []:
            pid = entry.get("bdl_player_id") or entry.get("player_id")
            dt  = entry.get("date") or entry.get("game_date") or ""
            if pid is not None and dt:
                idx[(int(pid), str(dt))] = entry

    return idx


# ── grading ─────────────────────────────────────────────────────────────────
def _normalize_name(raw: Optional[str]) -> str:
    if not raw:
        return ""
    return raw.strip().lower()


def compute_outcome(
    side: str, actual: float, line: float
) -> Tuple[int, float]:
    """Return (outcome_numeric, actual_value). Caller skips push."""
    s = side.upper()
    if s in ("OVER", "YES"):
        won = actual > line
    elif s in ("UNDER", "NO"):
        won = actual < line
    else:
        raise ValueError(f"Unknown side: {side!r}")
    return (1 if won else 0), actual


# ── main processing loop ────────────────────────────────────────────────────
async def amain(args: argparse.Namespace) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    t0 = time.time()

    print(f"[{datetime.now(timezone.utc).isoformat()}] grade_player_historical_props")
    print(f"  sport={args.sport}  dry_run={args.dry_run}")

    print("  [1/3] building nba_master_hub_2026 index …", flush=True)
    hub_idx = await build_hub_index(db)
    print(f"        {len(hub_idx):,} players mapped to bdl_player_id")

    print("  [2/3] building bdl_historical_game_logs index …", flush=True)
    bdl_idx = await build_bdl_index(db)
    print(f"        {len(bdl_idx):,} (player_id, date) entries loaded")

    # Query: unresolved NBA player rows
    query: Dict[str, Any] = {
        "prop_type":  "player",
        "league_id":  args.sport.upper(),
        "outcome_resolved": {"$ne": True},
    }
    total_unresolved = await db[REPLAY_COLL].count_documents(query)
    print(f"  [3/3] streaming {total_unresolved:,} unresolved rows …\n")

    scanned = 0
    graded  = 0
    skipped_no_bdl   = 0   # no BDL stats found for (player, date)
    skipped_no_hub   = 0   # player not in hub index
    skipped_no_stat  = 0   # stat_family not in STAT_MAP or resolver returned None
    skipped_push     = 0   # actual == line

    ops: List[UpdateOne] = []
    now_utc = datetime.now(timezone.utc)

    async for doc in db[REPLAY_COLL].find(query):
        scanned += 1

        # ── 1. resolve player → bdl_player_id via hub ──────────────────
        norm_name = _normalize_name(doc.get("player_name_normalized") or
                                     doc.get("player_name"))
        bdl_pid = hub_idx.get(norm_name)
        if bdl_pid is None:
            skipped_no_hub += 1
            continue

        # ── 2. look up actual game stats ────────────────────────────────
        game_date = doc.get("game_date", "")
        stats_doc = bdl_idx.get((bdl_pid, game_date))
        if stats_doc is None:
            skipped_no_bdl += 1
            continue

        # ── 3. compute actual stat value ────────────────────────────────
        stat_family = (doc.get("stat_family") or "").lower()
        resolver = STAT_MAP.get(stat_family)
        if resolver is None:
            skipped_no_stat += 1
            continue
        try:
            actual_value = resolver(stats_doc)
        except Exception:
            skipped_no_stat += 1
            continue
        if actual_value is None:
            skipped_no_stat += 1
            continue

        # ── 4. grade ─────────────────────────────────────────────────────
        try:
            line = float(doc.get("line") or 0)
        except (TypeError, ValueError):
            skipped_no_stat += 1
            continue

        side = str(doc.get("side") or "").upper()
        if side not in ("OVER", "UNDER", "YES", "NO"):
            skipped_no_stat += 1
            continue

        # Push — skip, don't write
        if actual_value == line:
            skipped_push += 1
            continue

        outcome_numeric, _ = compute_outcome(side, actual_value, line)
        graded += 1

        ops.append(UpdateOne(
            {"_id": doc["_id"]},
            {"$set": {
                "outcome_numeric":  outcome_numeric,
                "actual_value":     actual_value,
                "outcome_resolved": True,
                "graded_at":        now_utc,
                "grading_source":   "bdl_historical_game_logs",
            }},
        ))

        if len(ops) >= BATCH_SIZE and not args.dry_run:
            await db[REPLAY_COLL].bulk_write(ops, ordered=False)
            ops = []

        if scanned % LOG_EVERY == 0:
            elapsed = time.time() - t0
            rate = scanned / elapsed if elapsed else 0
            print(
                f"  … {scanned:,} scanned  graded={graded:,}  "
                f"no_hub={skipped_no_hub:,}  no_bdl={skipped_no_bdl:,}  "
                f"no_stat={skipped_no_stat:,}  push={skipped_push:,}  "
                f"rate={rate:,.0f}/s",
                flush=True,
            )

    # flush remainder
    if ops and not args.dry_run:
        await db[REPLAY_COLL].bulk_write(ops, ordered=False)

    runtime = time.time() - t0
    print()
    print("=" * 68)
    print("  grade_player_historical_props SUMMARY")
    print("=" * 68)
    print(f"  total scanned:                {scanned:,}")
    print(f"  graded (written):             {graded:,}")
    print(f"  skipped — player not in hub:  {skipped_no_hub:,}")
    print(f"  skipped — no BDL game log:    {skipped_no_bdl:,}")
    print(f"  skipped — stat not resolved:  {skipped_no_stat:,}")
    print(f"  skipped — push (actual=line): {skipped_push:,}")
    print(f"  dry_run:                      {args.dry_run}")
    print(f"  runtime:                      {runtime:,.1f}s")
    if not args.dry_run and scanned:
        pct = 100.0 * graded / scanned
        print(f"  grade rate:                   {pct:.1f}%")
    print("=" * 68)

    client.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sport",   default="nba",
                   help="League ID to grade (default: nba)")
    p.add_argument("--dry-run", action="store_true",
                   help="Scan and compute outcomes but do not write to MongoDB")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
