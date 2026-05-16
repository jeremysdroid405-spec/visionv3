"""Phase 2B — Chunked one-shot hydration for existing live_props.

Why this exists
───────────────
Phase 2B hydration is wired into `services/feature_hydration.py` so
FRESH ingest cycles stamp `opposing_lineup` on each pitcher prop.
Props that were ingested BEFORE Phase 2B shipped don't carry the
field — and the recompute path doesn't run hydration, so they stay
imputed forever (or at least until the next slate flush).

This script back-fills `opposing_lineup` + `opposing_lineup_size` on
existing `mlb_live_props` so the next recompute sees real lineup
data and the gate-tuning audit reflects v3.2's full feature lift.

Architecture
────────────
Runs as a STANDALONE Python process (not inside the long-lived
backend). Loads the lineup resolver once into local memory, exits
when done. No singleton, no leaked state. Memory profile:

    ~10MB Python + ~80MB resolver pickle expanded + ~10MB batter
    rolling cache (lazy-loaded for the ~1,400 referenced batters)

Total peak RSS ~150MB — completely safe.

Usage
─────
    cd /app/backend && python scripts/phase2b_hot_hydrate_live_props.py
    cd /app/backend && python scripts/phase2b_hot_hydrate_live_props.py \\
        --batch-size 50 --dry-run

By default the script overwrites only props where `opposing_lineup`
is currently absent or None — idempotent, safe to re-run.
"""
from __future__ import annotations

import argparse
import logging
import os
import pickle
import sys
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

import pymongo  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("phase2b_hot_hydrate")

RESOLVER_PATH = (
    "/app/backend/models/mlb_hf/_phase2b_workdir/lineup_resolver.pkl"
)

# Stat types that should carry opposing_lineup.
PITCHER_STAT_TYPES = (
    "Pitcher Strikeouts",
    "Pitcher Outs",
    "Earned Runs",
    "Hits Allowed",
    "Walks Allowed",
    "Pitcher Walks",
)


def _db():
    client = pymongo.MongoClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


def _load_resolver() -> Dict[str, Any]:
    if not os.path.exists(RESOLVER_PATH):
        raise SystemExit(
            f"Lineup resolver not found: {RESOLVER_PATH}\n"
            f"Run Phase 2B `--build-resolver` first."
        )
    with open(RESOLVER_PATH, "rb") as f:
        r = pickle.load(f)
    logger.info(
        f"Loaded lineup resolver: {r['n_pairs']:,} pitcher-game pairs, "
        f"{r['n_pitchers']:,} pitchers."
    )
    return r


def _resolve_mlbam(prop: Dict[str, Any],
                   bdl_to_mlbam: Dict[int, int],
                   name_to_mlbam: Dict[str, int]) -> Optional[int]:
    for k in ("statcast_id", "mlb_id", "mlbam_id"):
        v = prop.get(k)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                pass
    bdl = prop.get("bdl_player_id")
    if bdl is not None:
        try:
            mid = bdl_to_mlbam.get(int(bdl))
            if mid is not None:
                return mid
        except (TypeError, ValueError):
            pass
    nm = (prop.get("player_name") or "").lower()
    nm = nm.replace(".", "").replace("'", "").strip()
    if nm and nm in name_to_mlbam:
        return name_to_mlbam[nm]
    return None


def _commence_date(prop: Dict[str, Any]) -> Optional[str]:
    ct = prop.get("commence_time") or prop.get("game_date")
    if not ct:
        return None
    return str(ct)[:10]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=50)
    ap.add_argument("--dry-run", action="store_true",
                     help="Compute hits/misses but don't write back.")
    ap.add_argument("--reset-all", action="store_true",
                     help="Rehydrate every pitcher prop (overwrite "
                          "existing opposing_lineup fields).")
    args = ap.parse_args()

    t_start = time.time()
    db = _db()
    resolver = _load_resolver()
    lineup_map = resolver["lineup"]

    # ── Identity mapping (multi-source) ─────────────────────────
    # Pitcher props carry only `bdl_player_id` + `player_name`. We
    # need an MLBAM/statcast pitcher_id to look up the resolver.
    # Source priority:
    #   1. `mlb_player_identity_map` by bdl_id  (most accurate)
    #   2. `mlb_statcast_pitcher_features` by normalised name
    #   3. `mlb_statcast_raw.pitcher_name` by normalised name
    def _norm(n):
        return (n or "").lower().replace(".", "").replace("'", "").strip()

    bdl_to_mlbam: Dict[int, int] = {}
    name_to_mlbam: Dict[str, int] = {}

    for m in db.mlb_player_identity_map.find(
            {"bdl_id": {"$ne": None}},
            {"_id": 0, "bdl_id": 1, "statcast_id": 1, "mlb_id": 1,
             "bdl_name": 1, "statcast_name": 1, "normalized_name": 1}):
        try:
            bdl = int(m["bdl_id"])
            mid = m.get("statcast_id") or m.get("mlb_id")
            if mid is not None:
                bdl_to_mlbam[bdl] = int(mid)
                for nm in (m.get("bdl_name"), m.get("statcast_name"),
                           m.get("normalized_name")):
                    nn = _norm(nm)
                    if nn:
                        name_to_mlbam.setdefault(nn, int(mid))
        except (TypeError, ValueError):
            continue
    logger.info(f"bdl→mlbam map: {len(bdl_to_mlbam):,} entries.")

    for d in db.mlb_statcast_pitcher_features.find(
            {"pitcher_id": {"$ne": None}},
            {"_id": 0, "pitcher_id": 1, "pitcher_name": 1}):
        nn = _norm(d.get("pitcher_name"))
        if nn and nn not in name_to_mlbam:
            try:
                name_to_mlbam[nn] = int(d["pitcher_id"])
            except (TypeError, ValueError):
                continue
    logger.info(
        f"name→mlbam map: {len(name_to_mlbam):,} entries "
        f"(identity_map + statcast_pitcher_features)."
    )

    # Build batter rolling-14 cache from referenced batters.
    referenced_batters: set = set()
    for lu in lineup_map.values():
        for b in lu:
            bid = b.get("batter_id")
            if bid is not None:
                referenced_batters.add(int(bid))
    logger.info(
        f"Referenced batters (resolver): {len(referenced_batters):,}"
    )
    sc_rolling: Dict[int, Dict[str, Any]] = defaultdict(dict)
    if referenced_batters:
        t0 = time.time()
        cursor = db.mlb_statcast_player_features.find(
            {"player_id": {"$in": list(referenced_batters)}},
            {"_id": 0, "player_id": 1,
             "game_date": 1, "rolling_14": 1},
        )
        n = 0
        for d in cursor:
            pid = d.get("player_id")
            gd = d.get("game_date")
            if pid is None or not gd:
                continue
            sc_rolling[int(pid)][str(gd)[:10]] = (
                d.get("rolling_14") or {}
            )
            n += 1
        logger.info(
            f"sc_rolling cache: {len(sc_rolling):,} batters, "
            f"{n:,} dated rows, elapsed={time.time()-t0:.1f}s"
        )

    # Iterate pitcher props in batches.
    q = {"sport": "mlb", "stat_type": {"$in": list(PITCHER_STAT_TYPES)}}
    if not args.reset_all:
        q["$or"] = [
            {"opposing_lineup": {"$exists": False}},
            {"opposing_lineup": None},
        ]
    total = db.mlb_live_props.count_documents(q)
    logger.info(f"Pitcher props to hydrate: {total:,}")
    if total == 0:
        logger.info("Nothing to do.")
        return

    n_hit_resolver = n_hit_fallback = 0
    n_miss_id = n_miss_lineup = n_written = 0
    bulk: List[pymongo.UpdateOne] = []

    # Live-feed fallback (BDL → last-played).
    from services.mlb_live_lineup_feed import (
        fetch_opposing_lineup_sync as _fetch_live_lineup,
    )
    # Memoise per (opp_team, game_date) — many props share a game.
    live_lineup_memo: Dict[Tuple[str, str],
                            Optional[List[Dict[str, Any]]]] = {}

    cursor = db.mlb_live_props.find(
        q,
        {"_id": 1, "bdl_player_id": 1, "statcast_id": 1, "mlb_id": 1,
         "mlbam_id": 1, "commence_time": 1, "game_date": 1,
         "opponent_team": 1, "player_name": 1, "stat_type": 1},
    )

    for prop in cursor:
        mlbam = _resolve_mlbam(prop, bdl_to_mlbam, name_to_mlbam)
        if mlbam is None:
            n_miss_id += 1
            continue
        gd = _commence_date(prop)
        if not gd:
            n_miss_id += 1
            continue

        # Priority 1: historical resolver (training-corpus games).
        lineup_raw = lineup_map.get((mlbam, gd))
        source = "resolver"
        if not lineup_raw:
            # Priority 2: live-feed fallback (BDL or last-played).
            opp_team = prop.get("opponent_team")
            if not opp_team:
                n_miss_lineup += 1
                continue
            key = (opp_team, gd)
            if key not in live_lineup_memo:
                try:
                    live_lineup_memo[key] = _fetch_live_lineup(
                        db, opp_team, gd,
                    )
                except Exception:
                    live_lineup_memo[key] = None
            lineup_raw = live_lineup_memo[key]
            source = "live_feed"
            if not lineup_raw:
                n_miss_lineup += 1
                continue

        if source == "resolver":
            n_hit_resolver += 1
        else:
            n_hit_fallback += 1

        # Decorate with inline rolling_14 as-of game_date.
        decorated: List[Dict[str, Any]] = []
        for b in lineup_raw:
            b2 = dict(b)
            bid = b.get("batter_id")
            if bid is not None:
                by_date = sc_rolling.get(int(bid)) or {}
                earlier = [d for d in by_date if d and d <= gd]
                if earlier:
                    rolling = by_date[max(earlier)]
                    if rolling:
                        b2["rolling_14"] = rolling
            decorated.append(b2)

        bulk.append(pymongo.UpdateOne(
            {"_id": prop["_id"]},
            {"$set": {
                "opposing_lineup": decorated,
                "opposing_lineup_size": len(decorated),
            }},
        ))
        if len(bulk) >= args.batch_size:
            if not args.dry_run:
                r = db.mlb_live_props.bulk_write(bulk, ordered=False)
                n_written += r.modified_count
            bulk.clear()
            logger.info(
                f"  progress: hit_resolver={n_hit_resolver} "
                f"hit_fallback={n_hit_fallback} miss_id={n_miss_id} "
                f"miss_lineup={n_miss_lineup} written={n_written}"
            )

    if bulk:
        if not args.dry_run:
            r = db.mlb_live_props.bulk_write(bulk, ordered=False)
            n_written += r.modified_count
        bulk.clear()

    elapsed = time.time() - t_start
    logger.info(
        f"DONE — total={total} hit_resolver={n_hit_resolver} "
        f"hit_fallback={n_hit_fallback} miss_id={n_miss_id} "
        f"miss_lineup={n_miss_lineup} written={n_written} "
        f"live_feed_unique_pairs={len(live_lineup_memo)} "
        f"elapsed={elapsed:.1f}s"
    )


if __name__ == "__main__":
    main()
