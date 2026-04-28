"""
MLB Statcast Pitcher Feature Builder
=====================================
Reads `mlb_statcast_raw` and emits one document per (pitcher_id,
game_date) into `mlb_statcast_pitcher_features` with rolling 7 / 14 /
30 + season windows AND L/R-handed-batter splits. Idempotent on
(pitcher_id, game_date).

Each window includes:
  xwOBA_allowed         = mean(estimated_woba_using_speedangle) over
                           AB-final pitches the pitcher threw
  wOBA_allowed          = mean(woba_value) over AB-final pitches
  hard_hit_allowed_rate = pct of BBE-against with launch_speed >= 95
  barrel_allowed_rate   = pct of BBE-against tagged 'barrel'
  k_rate                = K / PA-against
  bb_rate               = BB / PA-against
  batted_ball_events    = BBE-against
  plate_appearances     = PA-against

Splits:
  *_vs_L  — only pitches where stand=='L' (LHB faced)
  *_vs_R  — only pitches where stand=='R' (RHB faced)

Confidence rule (per spec):
  rolling_30.confidence_flag = "high" if BBE-against >= 25 else "low"

Usage:
    python -m scripts.mlb_statcast_build_pitcher_features
    python -m scripts.mlb_statcast_build_pitcher_features --since 2026-04-20
"""
from __future__ import annotations

import argparse, asyncio, logging, os, sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

logging.basicConfig(level=logging.INFO,
                     format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mlb_statcast_build_pitcher_features")

RAW = "mlb_statcast_raw"
OUT = "mlb_statcast_pitcher_features"

MIN_BBE_HIGH_CONFIDENCE = 25


def _f(v):
    if v is None: return None
    try: return float(v)
    except (TypeError, ValueError): return None


async def ensure_indexes(db) -> None:
    coll = db[OUT]
    await coll.create_index([("pitcher_id", 1), ("game_date", 1)],
                              name="uniq_pitcher_date", unique=True)
    await coll.create_index([("pitcher_name", 1), ("game_date", 1)],
                              name="pitcher_name_date")
    await coll.create_index([("game_date", 1)], name="game_date")


def _accumulate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate raw rows for one pitcher window. Mirrors batter
    feature accumulator structurally, but inverts the relationship:
    we're measuring what the pitcher GAVE UP."""
    if not rows:
        return {"plate_appearances": 0, "batted_ball_events": 0,
                "xwOBA_allowed": None, "wOBA_allowed": None,
                "hard_hit_allowed_rate": None, "barrel_allowed_rate": None,
                "k_rate": None, "bb_rate": None}

    pas = {(r["game_pk"], r["at_bat_number"]) for r in rows
           if r.get("game_pk") is not None
           and r.get("at_bat_number") is not None}
    n_pa = len(pas)

    bbes = [r for r in rows if r.get("bb_type")]
    n_bbe = len(bbes)

    final_rows = [r for r in rows if r.get("events")]
    woba_values = [_f(r.get("woba_value")) for r in final_rows]
    woba_values = [v for v in woba_values if v is not None]
    xwobas = [_f(r.get("estimated_woba_using_speedangle"))
              for r in final_rows]
    xwobas = [v for v in xwobas if v is not None]

    n_hard = sum(1 for r in bbes
                  if (s := _f(r.get("launch_speed"))) is not None
                  and s >= 95.0)
    n_barrel = sum(1 for r in bbes
                    if (r.get("bb_type") or "") == "barrel")

    n_k = sum(1 for r in final_rows
               if (r.get("events") or "") == "strikeout")
    n_bb = sum(1 for r in final_rows
                if (r.get("events") or "") in ("walk", "intent_walk"))

    def _div(a, b): return (a / b) if b else None
    def _avg(vs):   return (sum(vs) / len(vs)) if vs else None

    return {
        "plate_appearances":     n_pa,
        "batted_ball_events":    n_bbe,
        "xwOBA_allowed":         _avg(xwobas) if xwobas else None,
        "wOBA_allowed":          _avg(woba_values) if woba_values else None,
        "hard_hit_allowed_rate": _div(n_hard, n_bbe),
        "barrel_allowed_rate":   _div(n_barrel, n_bbe),
        "k_rate":                _div(n_k, n_pa),
        "bb_rate":               _div(n_bb, n_pa),
    }


def _split_rows(rows: List[Dict[str, Any]], stand: str
                 ) -> List[Dict[str, Any]]:
    """Filter rows by batter handedness ('L' or 'R')."""
    return [r for r in rows if r.get("stand") == stand]


async def _load_raw(db, since: Optional[str]
                    ) -> Dict[tuple, List[Dict[str, Any]]]:
    """Load all raw rows keyed by (pitcher_id, game_date)."""
    q: Dict[str, Any] = {"pitcher": {"$ne": None}}
    if since: q["game_date"] = {"$gte": since}
    proj = {"_id": 0, "game_pk": 1, "at_bat_number": 1, "pitch_number": 1,
             "pitcher": 1, "pitcher_name": 1, "p_throws": 1, "stand": 1,
             "game_date": 1, "events": 1, "bb_type": 1, "launch_speed": 1,
             "estimated_woba_using_speedangle": 1, "woba_value": 1}
    by_pd: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    n = 0
    async for d in db[RAW].find(q, proj):
        pid = d.get("pitcher"); date = d.get("game_date")
        if pid is None or not date: continue
        by_pd[(int(pid), date)].append(d); n += 1
    logger.info(f"loaded {n:,} raw rows  ·  unique (pitcher,date) keys: "
                  f"{len(by_pd):,}")
    return by_pd


def _name_for_pitcher(rows_all: List[Dict[str, Any]]) -> Optional[str]:
    from collections import Counter
    names = [r.get("pitcher_name") for r in rows_all if r.get("pitcher_name")]
    if not names: return None
    return Counter(names).most_common(1)[0][0]


def _p_throws_for(rows_all: List[Dict[str, Any]]) -> Optional[str]:
    """Most-common p_throws across this pitcher's rows."""
    from collections import Counter
    vals = [r.get("p_throws") for r in rows_all if r.get("p_throws")]
    if not vals: return None
    return Counter(vals).most_common(1)[0][0]


async def build(db, *, since: Optional[str] = None,
                  dry_run: bool = False) -> Dict[str, int]:
    if not dry_run: await ensure_indexes(db)
    by_pd = await _load_raw(db, since=since)
    if not by_pd: return {"computed": 0, "inserted": 0, "updated": 0}

    by_pitcher: Dict[int, Dict[str, List[Dict[str, Any]]]] = defaultdict(dict)
    for (pid, dt), rows in by_pd.items():
        by_pitcher[pid][dt] = rows

    from pymongo import UpdateOne
    ops: List[Any] = []; n_computed = 0

    for pid, by_date in by_pitcher.items():
        sorted_dates = sorted(by_date)
        all_rows: List[Dict[str, Any]] = []
        for d in sorted_dates: all_rows.extend(by_date[d])
        pitcher_name = _name_for_pitcher(all_rows)
        p_throws = _p_throws_for(all_rows)
        season = sorted_dates[0][:4] if sorted_dates else None
        season_overall = _accumulate(all_rows)

        for tgt in sorted_dates:
            target = date.fromisoformat(tgt)

            def _window(days: int) -> List[Dict[str, Any]]:
                lo = (target - timedelta(days=days - 1)).isoformat()
                return [r for d, rs in by_date.items()
                          if lo <= d <= tgt for r in rs]

            # Rolling overall windows.
            rows_7  = _window(7); rows_14 = _window(14); rows_30 = _window(30)
            r_30 = _accumulate(rows_30)
            r_14 = _accumulate(rows_14)
            r_7  = _accumulate(rows_7)
            # L/R splits over the 30-day window (the span used by the
            # shadow matchup_factor lookup).
            r_30_L = _accumulate(_split_rows(rows_30, "L"))
            r_30_R = _accumulate(_split_rows(rows_30, "R"))
            r_30["confidence_flag"] = (
                "high" if (r_30.get("batted_ball_events") or 0)
                          >= MIN_BBE_HIGH_CONFIDENCE
                else "low")

            doc = {
                "pitcher_id":   pid,
                "pitcher_name": pitcher_name,
                "p_throws":     p_throws,
                "game_date":    tgt,
                "season":       season,
                "rolling_7":    r_7,
                "rolling_14":   r_14,
                "rolling_30":   r_30,
                "split_30_vs_L": r_30_L,
                "split_30_vs_R": r_30_R,
                "season_window": season_overall,
                "computed_at":  datetime.now(timezone.utc),
            }
            n_computed += 1
            if not dry_run:
                ops.append(UpdateOne(
                    {"pitcher_id": pid, "game_date": tgt},
                    {"$set": doc}, upsert=True))

    inserted = updated = errors = 0
    if not dry_run and ops:
        BATCH = 1000
        for i in range(0, len(ops), BATCH):
            try:
                res = await db[OUT].bulk_write(
                    ops[i:i + BATCH], ordered=False)
                inserted += (res.upserted_count or 0)
                updated  += (res.modified_count or 0)
            except Exception as ex:
                logger.warning(f"batch {i} failed: {ex!r}")
                errors += len(ops[i:i + BATCH])

    logger.info(f"computed={n_computed:,}  inserted={inserted:,}  "
                  f"updated={updated:,}  errors={errors}")
    return {"computed": n_computed, "inserted": inserted, "updated": updated}


async def _amain():
    p = argparse.ArgumentParser()
    p.add_argument("--since", default=None)
    p.add_argument("--dry", action="store_true")
    args = p.parse_args()
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    await build(db, since=args.since, dry_run=args.dry)


if __name__ == "__main__":
    asyncio.run(_amain())
