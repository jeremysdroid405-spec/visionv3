"""Phase 2B — Historical pitcher×date → opposing-lineup resolver.

Why this exists
───────────────
The Phase 2A `matchup_resolver` answered "for batter B on date D, which
pitcher did they face first?" — a batter-centric view. For pitcher
models we need the inverse: "on date D, which batters did pitcher P
actually face?" — the opposing-lineup view.

Pure-historical resolver — sources strictly from
`mlb_statcast_raw` (per-pitch records, post-hoc and fully observed).
Used ONLY for building Phase 2B training data. The live-prediction
path uses `services.mlb_live_lineup_feed`.

Output shape
────────────
    {
      "lineup": {
        (pitcher_id, "YYYY-MM-DD"): [
            {"batter_id": int, "stand": "L|R|S", "n_pitches": int,
             "first_appearance_order": int},
            ...
        ],
        ...
      },
      "n_pitchers": int,
      "n_pairs": int,
    }

`first_appearance_order` is the 1-based order in which a batter first
saw a pitch from this pitcher in the game — a reasonable proxy for
the top of the order facing the starter. Used in training to weight
top-of-order batters more heavily.

Persistence
───────────
The resolver is pickled to
`/app/backend/models/mlb_hf/_phase2b_workdir/lineup_resolver.pkl`
so subsequent retrain-worker invocations skip the ~30s build.
"""
from __future__ import annotations

import logging
import os
import pickle
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("mlb_lineup_resolver")

WORKDIR = "/var/www/app/backend/models/mlb_hf/_phase2b_workdir"
RESOLVER_PATH = os.path.join(WORKDIR, "lineup_resolver.pkl")


def build_lineup_resolver(db) -> Dict[str, Any]:
    """Aggregate `mlb_statcast_raw` per (pitcher, game_date) → lineup list.

    Streaming aggregate keeps peak memory bounded — we emit one row per
    (pitcher, game_date, batter) triple, then fold into the output dict.
    """
    logger.info("Building lineup resolver via Mongo aggregation…")
    os.makedirs(WORKDIR, exist_ok=True)
    t0 = time.time()

    pipe = [
        {"$match": {
            "pitcher": {"$ne": None},
            "batter": {"$ne": None},
            "game_date": {"$ne": None},
        }},
        # Sort so we can capture first-appearance-order per batter.
        {"$sort": {
            "pitcher": 1, "game_date": 1,
            "at_bat_number": 1, "pitch_number": 1,
        }},
        {"$group": {
            "_id": {
                "p": "$pitcher", "gd": "$game_date", "b": "$batter",
            },
            "stand": {"$first": "$stand"},
            "n_pitches": {"$sum": 1},
            "first_ab": {"$min": "$at_bat_number"},
        }},
    ]

    # (pitcher_id, gd) -> list of dicts (preserving first_ab order)
    by_game: Dict[Tuple[int, str], List[Dict[str, Any]]] = defaultdict(list)

    n_rows = 0
    for d in db.mlb_statcast_raw.aggregate(
            pipe, allowDiskUse=True, batchSize=2000):
        _id = d.get("_id") or {}
        pid_raw = _id.get("p")
        gd = _id.get("gd")
        bid_raw = _id.get("b")
        if pid_raw is None or bid_raw is None or not gd:
            continue
        try:
            pid = int(pid_raw)
            bid = int(bid_raw)
        except (TypeError, ValueError):
            continue
        stand = d.get("stand")
        first_ab = d.get("first_ab")
        by_game[(pid, gd)].append({
            "batter_id": bid,
            "stand": (str(stand).strip().upper()[:1]
                      if stand else None),
            "n_pitches": int(d.get("n_pitches") or 0),
            "first_ab": int(first_ab) if first_ab is not None else None,
        })
        n_rows += 1

    # Sort each game's lineup by first_ab so position 0 = leadoff-vs-pitcher.
    lineup: Dict[Tuple[int, str], List[Dict[str, Any]]] = {}
    for key, batters in by_game.items():
        batters.sort(key=lambda r: (r["first_ab"] is None, r["first_ab"]))
        for i, b in enumerate(batters, start=1):
            b["first_appearance_order"] = i
            b.pop("first_ab", None)
        lineup[key] = batters

    n_pitchers = len({k[0] for k in lineup.keys()})
    out = {
        "lineup": lineup,
        "n_pitchers": n_pitchers,
        "n_pairs": len(lineup),
        "n_rows_raw": n_rows,
    }
    logger.info(
        f"  resolver: pitchers={n_pitchers:,}, "
        f"(pitcher,date) pairs={len(lineup):,}, "
        f"raw rows={n_rows:,}, elapsed={time.time()-t0:.1f}s"
    )
    return out


def load_or_build_resolver(db) -> Dict[str, Any]:
    if os.path.exists(RESOLVER_PATH):
        with open(RESOLVER_PATH, "rb") as f:
            r = pickle.load(f)
        logger.info(
            f"Loaded lineup resolver from disk: "
            f"{r['n_pairs']:,} pitcher-game pairs"
        )
        return r
    r = build_lineup_resolver(db)
    os.makedirs(WORKDIR, exist_ok=True)
    with open(RESOLVER_PATH, "wb") as f:
        pickle.dump(r, f, protocol=pickle.HIGHEST_PROTOCOL)
    logger.info(f"Saved lineup resolver → {RESOLVER_PATH}")
    return r


def get_lineup(resolver: Dict[str, Any],
               pitcher_id: int,
               game_date: str) -> Optional[List[Dict[str, Any]]]:
    """Lookup helper. Returns None when no lineup is known.

    Empty list (vs None) means the resolver did see this pair but no
    batters were valid — different from "never saw this pitcher".
    """
    if pitcher_id is None or not game_date:
        return None
    try:
        key = (int(pitcher_id), str(game_date)[:10])
    except (TypeError, ValueError):
        return None
    return resolver["lineup"].get(key)
