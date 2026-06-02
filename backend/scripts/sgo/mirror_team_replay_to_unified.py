"""scripts/sgo/mirror_team_replay_to_unified.py

Mirror `team_replay_model_outputs` into the unified optimizer source
`sgo_propvision_full_pipeline_replay` with `prop_type="team"`,
preserving every scored team row.

Counterpart to `mirror_player_replay_to_unified.py`. Per the locked
2-pipeline contract, both pipelines land scored rows in ONE shared
optimizer collection so the threshold search has a single dataset:

    sgo_propvision_full_pipeline_replay
        ├── prop_type=player  (player mirror)
        └── prop_type=team    (this script)

Safety:
  * Only touches rows with `prop_type="team"` in the unified
    collection — player rows untouched.
  * With `--commit`: deletes existing `{prop_type: "team"}` rows
    from the unified collection BEFORE inserting the fresh set.
  * Without `--commit`: dry-run; prints field-coverage report.

CLI
───
    python -m scripts.sgo.mirror_team_replay_to_unified           # dry-run
    python -m scripts.sgo.mirror_team_replay_to_unified --commit  # apply
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from pymongo import InsertOne  # noqa: E402

load_dotenv("/app/backend/.env")
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s",
                     level=logging.INFO)
logger = logging.getLogger("mirror_team_replay")

UNIFIED_COLL = "sgo_propvision_full_pipeline_replay"
SOURCE_COLL = "team_replay_model_outputs"
CHUNK = 10_000

TRACKED_FIELDS = [
    "event_id", "team_id", "opponent_team_id", "sport", "league_id",
    "market", "market_key", "market_category", "stat_family",
    "side", "line", "book", "odds", "is_alternate",
    "model_probability", "implied_probability", "edge", "edge_pct",
    "vision_score", "intel_score", "model_version",
    "hit", "outcome_resolved", "outcome_numeric", "actual_value",
    "game_date", "as_of_date", "scored_at",
    "pipeline_version", "ssot_source", "prop_type",
]


def _coverage_report(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    if not rows:
        return {}
    n = len(rows)
    return {
        f: round(sum(1 for r in rows if r.get(f) is not None) / n, 3)
        for f in TRACKED_FIELDS
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true",
                     help="Apply writes. Default: dry-run.")
    args = ap.parse_args()

    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    n_src = await db[SOURCE_COLL].count_documents({})
    logger.info(f"source `{SOURCE_COLL}`: {n_src:,} rows")
    if n_src == 0:
        logger.warning("No scored team rows. Run "
                        "`scripts.sgo.run_team_backtest --commit` first.")
        return

    sample = await db[SOURCE_COLL].find(
        {}, projection={"_id": 0}).limit(1000).to_list(1000)
    cov = _coverage_report(sample)
    logger.info("field coverage (first 1000):")
    for f, p in sorted(cov.items(), key=lambda kv: -kv[1]):
        logger.info(f"  {f:30s}  {p*100:5.1f}%")

    if not args.commit:
        logger.info("dry-run — no writes. Re-run with --commit.")
        return

    # Wipe existing team rows from the unified collection.
    del_res = await db[UNIFIED_COLL].delete_many({"prop_type": "team"})
    logger.info(f"unified `{UNIFIED_COLL}`: deleted "
                 f"{del_res.deleted_count:,} stale team rows")

    # Stream-insert in chunks.
    cursor = db[SOURCE_COLL].find({}, projection={"_id": 0})
    batch: List[Dict[str, Any]] = []
    total_written = 0
    async for r in cursor:
        r.setdefault("prop_type", "team")
        r.setdefault("mirrored_at", datetime.now(timezone.utc).isoformat())
        batch.append(r)
        if len(batch) >= CHUNK:
            ops = [InsertOne(d) for d in batch]
            await db[UNIFIED_COLL].bulk_write(ops, ordered=False)
            total_written += len(batch)
            batch = []
    if batch:
        ops = [InsertOne(d) for d in batch]
        await db[UNIFIED_COLL].bulk_write(ops, ordered=False)
        total_written += len(batch)
    logger.info(f"unified `{UNIFIED_COLL}`: inserted "
                 f"{total_written:,} team rows")

    # Final tallies.
    n_team = await db[UNIFIED_COLL].count_documents({"prop_type": "team"})
    n_player = await db[UNIFIED_COLL].count_documents(
        {"prop_type": "player"})
    logger.info(f"final unified row counts: "
                 f"team={n_team:,}  player={n_player:,}")


if __name__ == "__main__":
    asyncio.run(main())
