#!/usr/bin/env python3
"""
Resolve all NBA events in `replay_props_normalized` against
`bdl_historical_game_logs` + `nba_master_hub_2026.player_game_logs`.

Writes `replay_results` only. Idempotent.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from dotenv import load_dotenv
load_dotenv(str(REPO_ROOT / "backend/.env"))

from motor.motor_asyncio import AsyncIOMotorClient   # noqa: E402

from services.replay.result_ingester import (        # noqa: E402
    REPLAY_RESULTS,
    ensure_results_indexes, list_events_to_resolve,
    resolve_event, upsert_results,
)


async def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    await ensure_results_indexes(db)

    events = await list_events_to_resolve(db)
    print(f"[resolve_results] {len(events)} events to resolve")

    started = time.monotonic()
    run_id = "results_resolver_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    totals = {"events": 0, "rows_inserted": 0, "rows_modified": 0,
               "agree": 0, "mismatch": 0,
               "source_a_only": 0, "source_b_only": 0, "missing_both": 0}

    for i, ev in enumerate(events, 1):
        out = await resolve_event(db, event=ev, run_id=run_id)
        ins, mod = await upsert_results(db, out["rows"])
        totals["events"] += 1
        totals["rows_inserted"] += ins
        totals["rows_modified"] += mod
        for r in out["rows"]:
            totals[r["validation_status"]] = (
                totals.get(r["validation_status"], 0) + 1)
        if i % 25 == 0 or i == len(events):
            elapsed = time.monotonic() - started
            print(f"[resolve_results] {i}/{len(events)} done "
                  f"ins={totals['rows_inserted']} mod={totals['rows_modified']} "
                  f"elapsed={elapsed:.1f}s")

    finished = time.monotonic()
    summary = {
        "run_id":            run_id,
        "events_resolved":   totals["events"],
        "rows_inserted":     totals["rows_inserted"],
        "rows_modified":     totals["rows_modified"],
        "validation_status_counts": {
            k: totals.get(k, 0) for k in
            ("agree", "mismatch", "source_a_only",
             "source_b_only", "missing_both")
        },
        "wallclock_seconds": round(finished - started, 1),
        "results_collection_total":
            await db[REPLAY_RESULTS].count_documents({}),
    }
    print(json.dumps(summary, indent=2, default=str))
    cli.close()


if __name__ == "__main__":
    asyncio.run(main())
