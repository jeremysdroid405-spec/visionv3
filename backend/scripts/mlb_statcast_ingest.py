"""
MLB Statcast Ingest — daily pull from pybaseball
=================================================
Fetches Statcast pitch/batted-ball events for a date range, stamps each
row with normalized batter/pitcher names, and bulk-upserts into
`mlb_statcast_raw`. Idempotent on (game_pk, at_bat_number, pitch_number).

Primary source : pybaseball.statcast(start_dt, end_dt)
Fallback notes : Baseball Savant CSV / gamefeed are documented but not
                 wired here (pybaseball already proxies them under the
                 hood). If pybaseball goes down, the same column shape
                 can be loaded from
                 https://baseballsavant.mlb.com/statcast_search/csv?...
                 or from /gf?game_pk=… and inserted with the same key.

CLI:
    python -m scripts.mlb_statcast_ingest --start 2026-04-20 --end 2026-04-25
    python -m scripts.mlb_statcast_ingest --start 2026-04-25            # 1 day
    python -m scripts.mlb_statcast_ingest --start 2026-04-20 --end 2026-04-25 --dry
"""
from __future__ import annotations

import argparse, asyncio, logging, os, sys
from datetime import datetime, date, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
logging.basicConfig(level=logging.INFO,
                     format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mlb_statcast_ingest")

COLLECTION = "mlb_statcast_raw"

# Columns we persist (subset of pybaseball's 118-column statcast dump).
PERSIST_COLS = (
    "game_date", "game_pk", "at_bat_number", "pitch_number",
    "batter", "pitcher", "events", "description",
    "launch_speed", "launch_angle",
    "estimated_woba_using_speedangle", "woba_value",
    "bb_type", "pitch_type", "release_speed",
    "stand", "p_throws", "plate_x", "plate_z",
    "type", "balls", "strikes", "inning", "inning_topbot",
    "home_team", "away_team",
)


# ---------------------------------------------------------------------------
def _normalize_name(s: Optional[str]) -> Optional[str]:
    """Lower-case, "Last, First" → "first last".
    Statcast pitcher names come in "Last, First" — flip them so they
    join cleanly to mlb_master_hub_2026.player_name."""
    if not s or not isinstance(s, str): return None
    s = s.strip()
    if "," in s:
        last, first = (p.strip() for p in s.split(",", 1))
        return f"{first} {last}".lower()
    return s.lower()


def _safe_get(row, col):
    """numpy/pandas-aware None-safe column read."""
    if col not in row: return None
    v = row[col]
    # NaN / NaT handling — pandas.isna covers numpy NaN, NaT, None.
    try:
        import pandas as pd
        if pd.isna(v): return None
    except Exception:
        pass
    return v


def _to_py(v):
    """numpy → python primitives so BSON encodes correctly."""
    if v is None: return None
    try:
        import pandas as pd, numpy as np
        if isinstance(v, (pd.Timestamp,)): return v.strftime("%Y-%m-%d")
        if isinstance(v, np.integer):       return int(v)
        if isinstance(v, np.floating):      return float(v)
        if isinstance(v, np.bool_):         return bool(v)
    except Exception:
        pass
    return v


# ---------------------------------------------------------------------------
def _build_id_to_name(df) -> Dict[int, str]:
    """One-shot batter MLBAM-id → 'first last' lower-case map.
    pybaseball.statcast() puts pitcher's name in `player_name`; batter
    names are not in the row, so we round-trip via Chadwick registry."""
    import pybaseball as pb
    ids = sorted({int(b) for b in df["batter"].dropna().tolist()})
    if not ids: return {}
    logger.info(f"Resolving {len(ids):,} unique batter MLBAM ids …")
    lk = pb.playerid_reverse_lookup(ids, key_type="mlbam")
    out: Dict[int, str] = {}
    for _, r in lk.iterrows():
        first = (r.get("name_first") or "").strip()
        last  = (r.get("name_last")  or "").strip()
        if first and last:
            out[int(r["key_mlbam"])] = f"{first} {last}".lower()
    return out


# ---------------------------------------------------------------------------
async def ensure_indexes(db) -> None:
    coll = db[COLLECTION]
    await coll.create_index(
        [("game_pk", 1), ("at_bat_number", 1), ("pitch_number", 1)],
        name="uniq_game_ab_pitch", unique=True)
    await coll.create_index([("game_date", 1)], name="game_date")
    await coll.create_index([("batter", 1), ("game_date", 1)],
                              name="batter_date")
    await coll.create_index([("batter_name", 1), ("game_date", 1)],
                              name="batter_name_date")
    await coll.create_index([("pitcher", 1), ("game_date", 1)],
                              name="pitcher_date")


def _row_to_doc(row, batter_id_to_name: Dict[int, str]) -> Optional[Dict[str, Any]]:
    """Project a pybaseball Statcast row into the persisted schema."""
    game_pk = _safe_get(row, "game_pk")
    abn = _safe_get(row, "at_bat_number")
    pn = _safe_get(row, "pitch_number")
    if game_pk is None or abn is None or pn is None:
        return None

    batter_id = _safe_get(row, "batter")
    pitcher_id = _safe_get(row, "pitcher")
    pitcher_name_raw = _safe_get(row, "player_name")

    doc: Dict[str, Any] = {}
    for col in PERSIST_COLS:
        doc[col] = _to_py(_safe_get(row, col))

    # Normalize / map names.
    if batter_id is not None:
        bid = int(batter_id)
        doc["batter"] = bid
        doc["batter_name"] = batter_id_to_name.get(bid)
    if pitcher_id is not None:
        doc["pitcher"] = int(pitcher_id)
    doc["pitcher_name"] = _normalize_name(pitcher_name_raw)
    doc["ingested_at"] = datetime.now(timezone.utc)
    return doc


# ---------------------------------------------------------------------------
async def ingest_range(db, *, start: str, end: str,
                        dry_run: bool = False,
                        chunk_days: int = 7,
                        pause_seconds: float = 1.0) -> Dict[str, int]:
    """Pull pybaseball.statcast() in `chunk_days`-wide windows so the
    HTTP fetches stay small (the public endpoint limits at ~25k rows)
    and bulk-write into Mongo. Idempotent on the unique key.

    Pacing: between chunks we sleep `pause_seconds` (default 1s) so a
    long historical backfill doesn't pound Baseball Savant. Raise if
    you start seeing 429 / connection-reset errors. Set to 0 for full
    speed (only safe for small ranges or after-hours runs).
    """
    import pybaseball as pb
    from pymongo import UpdateOne

    pb.cache.disable()
    if not dry_run:
        await ensure_indexes(db)

    d_start = date.fromisoformat(start)
    d_end   = date.fromisoformat(end)
    if d_end < d_start:
        raise ValueError(f"end {end} < start {start}")

    total_rows = inserted = updated = errors = 0
    cur = d_start
    chunk_idx = 0
    while cur <= d_end:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), d_end)
        s, e = cur.isoformat(), chunk_end.isoformat()
        chunk_idx += 1

        # Inter-chunk pacing — 2026-04-30 user spec: respect upstream
        # by sleeping `pause_seconds` between chunks. Skipped on the
        # first chunk so a single-day fetch doesn't pay an unnecessary
        # cold-start tax.
        if chunk_idx > 1 and pause_seconds > 0:
            await asyncio.sleep(pause_seconds)

        logger.info(f"[fetch #{chunk_idx}] {s} → {e}")
        try:
            df = pb.statcast(start_dt=s, end_dt=e, verbose=False)
        except Exception as ex:
            logger.warning(f"[fetch] {s}..{e} failed: {ex!r}")
            errors += 1
            cur = chunk_end + timedelta(days=1); continue

        if df is None or len(df) == 0:
            logger.info(f"[fetch] {s}..{e} — 0 rows")
            cur = chunk_end + timedelta(days=1); continue

        id_map = _build_id_to_name(df)
        ops: List[Any] = []
        for _, row in df.iterrows():
            doc = _row_to_doc(row, id_map)
            if doc is None: errors += 1; continue
            ops.append(UpdateOne(
                filter={"game_pk": doc["game_pk"],
                         "at_bat_number": doc["at_bat_number"],
                         "pitch_number": doc["pitch_number"]},
                update={"$set": doc},
                upsert=True,
            ))
        total_rows += len(ops)
        logger.info(f"[fetch] {s}..{e} — {len(ops):,} rows projected")

        if dry_run:
            cur = chunk_end + timedelta(days=1); continue

        if ops:
            try:
                # Mongo bulk_write batching — split very large day-ranges
                # into 5k-op chunks to avoid the 16MB write limit.
                BATCH = 5000
                ins = upd = 0
                for i in range(0, len(ops), BATCH):
                    res = await db[COLLECTION].bulk_write(
                        ops[i:i + BATCH], ordered=False)
                    ins += (res.upserted_count or 0)
                    upd += (res.modified_count or 0)
                inserted += ins; updated += upd
                logger.info(f"[write] {s}..{e} — inserted={ins:,} "
                              f"updated={upd:,}")
            except Exception as ex:
                logger.warning(f"[write] {s}..{e} bulk failed: {ex!r}")
                errors += len(ops)

        cur = chunk_end + timedelta(days=1)

    return {"scanned": total_rows, "inserted": inserted,
            "updated": updated, "errors": errors}


# ---------------------------------------------------------------------------
async def _amain() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", default=None,
                    help="YYYY-MM-DD (defaults to --start, single day)")
    p.add_argument("--chunk-days", type=int, default=7,
                    help="HTTP request window size (default 7).")
    p.add_argument("--pause-seconds", type=float, default=1.0,
                    help="Sleep between chunks (default 1.0s).")
    p.add_argument("--dry", action="store_true",
                    help="Fetch + project but do not write.")
    args = p.parse_args()

    start = args.start
    end = args.end or args.start

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    res = await ingest_range(db, start=start, end=end,
                              dry_run=args.dry,
                              chunk_days=args.chunk_days,
                              pause_seconds=args.pause_seconds)
    logger.info(f"DONE  scanned={res['scanned']:,}  "
                  f"inserted={res['inserted']:,}  "
                  f"updated={res['updated']:,}  "
                  f"errors={res['errors']:,}")


if __name__ == "__main__":
    asyncio.run(_amain())
