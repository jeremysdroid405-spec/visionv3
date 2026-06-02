"""scripts/sgo/run_team_backtest.py

Team backtest orchestrator — the team analog of the player replay
pipeline. Per the locked 2-pipeline contract
(`/app/memory/ARCHITECTURE.md`), this MUST call the SAME team
predictor the live serving path uses (`team_xgb_loader
.score_team_props_batch`) — no separate replay scorer.

Flow:
    sgo_propvision_full_pipeline_replay (prop_type=team, scored=False)
            │
            ▼
    team_xgb_loader.score_team_props_batch    ← SAME LIVE TEAM MODEL
            │
            ▼
    team_replay_model_outputs                 ← scored backtest rows
            │
            ▼ (via mirror_team_replay_to_unified.py)
    sgo_propvision_full_pipeline_replay (prop_type=team, scored=True)
            │
            ▼
    optimizer input

Rules enforced:
    * Same scorer as live (no replay-only scoring path).
    * Vision pipeline (deterministic intel + scout badges via
      `services/team_historical_enrichment.py`) is the same module
      the live ferrari team endpoints + team-with-badges detail page
      already use.
    * NO eligibility filter applied; every reshaped row is scored.
      Gate metadata is stamped onto the row, never used to drop.

CLI
───
    python -m scripts.sgo.run_team_backtest --sport mlb --dry-run
    python -m scripts.sgo.run_team_backtest --sport all --commit
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from pymongo import UpdateOne  # noqa: E402

load_dotenv("/app/backend/.env")
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("run_team_backtest")

SOURCE_COLL = "sgo_propvision_full_pipeline_replay"
OUTPUT_COLL = "team_replay_model_outputs"
SPORTS = ("mlb", "nba", "nfl")
CHUNK = 1_000


async def _resolve_features(db, sport: str, team_id: str, as_of_date: str):
    """Last `team_model_features` row for (team, sport) with
    as_of_date <= target. Returns the inner feature dict or {}."""
    doc = await db["team_model_features"].find_one(
        {"sport": sport, "team_id": team_id,
         "as_of_date": {"$lte": as_of_date}},
        sort=[("as_of_date", -1)],
        projection={"_id": 0},
    )
    return doc or {}


async def run_sport(db, *, sport: str, commit: bool, limit: int = 0) -> Dict[str, Any]:
    from services.team_xgb_loader import score_team_props_batch, VERSION
    from services.team_live_xgb_scorer import (
        classify_market_category, _normalize_side,
    )

    flt = {"prop_type": "team", "league_id": sport.upper()}
    q = db[SOURCE_COLL].find(flt, projection={"_id": 0})
    if limit:
        q = q.limit(limit)

    enriched: List[Dict[str, Any]] = []
    raw_rows: List[Dict[str, Any]] = []
    skipped_no_mc = 0
    skipped_no_feat = 0
    scanned = 0
    async for r in q:
        scanned += 1
        mk = r.get("market_key") or r.get("market")
        mc = classify_market_category(mk)
        if mc is None:
            skipped_no_mc += 1
            continue
        tid = r.get("team_id")
        opp_tid = r.get("opponent_team_id")
        as_of = r.get("game_date") or r.get("as_of_date")
        if not (tid and as_of):
            skipped_no_feat += 1
            continue
        tf = await _resolve_features(db, sport, tid, as_of)
        of = await _resolve_features(db, sport, opp_tid, as_of) if opp_tid else {}
        if not tf and not of:
            skipped_no_feat += 1
            continue
        enriched.append({
            "sport":              sport,
            "market_category":    mc,
            "line":               r.get("line"),
            "side":               _normalize_side(mk or "", r.get("side")),
            "is_alternate":       r.get("is_alternate") or False,
            "home_away":          r.get("home_away"),
            "odds":               r.get("odds"),
            "team_features":      tf or {},
            "opponent_features":  of or {},
        })
        raw_rows.append(r)

    # ── Batched inference — SAME function the live scorer calls. ──
    scores = score_team_props_batch(enriched) if enriched else []

    now_iso = datetime.now(timezone.utc).isoformat()
    ops: List[UpdateOne] = []
    for r, payload, score in zip(raw_rows, enriched, scores):
        if score is None:
            continue
        doc = dict(r)
        doc.update({
            "market_category":     payload["market_category"],
            "stat_family":         payload["market_category"],
            "model_probability":   score["model_probability"],
            "implied_probability": score["implied_probability"],
            "edge":                score["edge"],
            "edge_pct":            (round(100.0 * score["edge"], 2)
                                      if score["edge"] is not None else None),
            "vision_score":        score["vision_score"],
            "intel_score":         score["vision_score"],
            "model_version":       score["model_version"],
            "scored_at":           now_iso,
            "prop_type":           "team",
            "pipeline_version":    "team_backtest_v1",
            "ssot_source":         "team_xgb_replay",
        })
        ops.append(UpdateOne(
            {
                "event_id": r.get("event_id"),
                "team_id":  r.get("team_id"),
                "market":   r.get("market"),
                "line":     r.get("line"),
                "side":     r.get("side"),
                "book":     r.get("book"),
            },
            {"$set": doc},
            upsert=True,
        ))

    if commit and ops:
        for i in range(0, len(ops), CHUNK):
            slab = ops[i:i + CHUNK]
            await db[OUTPUT_COLL].bulk_write(slab, ordered=False)

    return {
        "sport":           sport,
        "scanned":         scanned,
        "skipped_no_mc":   skipped_no_mc,
        "skipped_no_feat": skipped_no_feat,
        "scored":          len([s for s in scores if s is not None]),
        "written":         len(ops) if commit else 0,
        "model_version":   VERSION,
        "dry_run":         not commit,
        "ts":              now_iso,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="all",
                     help="mlb | nba | nfl | all (default: all)")
    ap.add_argument("--commit", action="store_true",
                     help="Write to team_replay_model_outputs. "
                          "Default: dry-run.")
    ap.add_argument("--limit", type=int, default=0,
                     help="Optional row cap per sport (0 = no cap).")
    args = ap.parse_args()

    sports = SPORTS if args.sport == "all" else (args.sport,)
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    for sp in sports:
        audit = await run_sport(
            db, sport=sp, commit=args.commit, limit=args.limit)
        logger.info(f"{sp.upper()} audit: {audit}")


if __name__ == "__main__":
    asyncio.run(main())
