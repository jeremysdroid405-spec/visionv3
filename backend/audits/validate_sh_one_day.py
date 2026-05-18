"""Sequential per-day SH production-path validation.

Runs `run_pipeline(tier='safe_haven')` for ONE date passed via CLI.
Designed to be invoked 13 times in series (one date per invocation)
so each Python process is short-lived and memory pressure resets
between days. Backend stays up.

Writes the per-day summary to
`/tmp/sh_validate_<YYYYMMDD>.json` for the aggregator to read.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient
from services.pipeline import run_pipeline


async def _main(date_iso: str) -> None:
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    test_id = f"PROD-VFP-{date_iso.replace('-', '')}"
    snap_iso = f"{date_iso}T11:00:00Z"
    print(f"[{date_iso}] run_pipeline starting…", flush=True)
    summary = await run_pipeline(
        db, sport="mlb", mode="historical",
        snapshot_time=snap_iso, output_namespace="test",
        test_id=test_id, tier="safe_haven",
        notes="Volume-First production-path validation 2026-05-18",
    )
    serial = summary["serial"]
    print(f"[{date_iso}] serial={serial} qual={summary.get('rows_qualified')} "
          f"W={summary.get('wins')} L={summary.get('losses')} "
          f"P={summary.get('pushes')} ungr={summary.get('ungraded')}",
          flush=True)

    rows = await db["mlb_test_outputs"].find(
        {"replay_serial": serial, "gate_pass": True},
        projection={"_id": 0, "stat_family": 1, "side": 1, "line": 1,
                    "tier_reference_odds": 1, "grade_status": 1,
                    "profit_units": 1, "player_name": 1,
                    "projection_mu": 1, "edge_pct": 1, "tp": 1,
                    "cv": 1, "hit_rate_l20": 1, "tp_source": 1,
                    "game_date": 1, "actual_value": 1,
                    "canonical_book_count_either_side": 1},
    ).to_list(50000)

    out_path = Path(f"/tmp/sh_validate_{date_iso.replace('-', '')}.json")
    out_path.write_text(json.dumps({
        "date": date_iso, "serial": serial, "summary": summary,
        "gate_pass_rows": rows,
    }, default=str))
    print(f"[{date_iso}] wrote {out_path} ({len(rows)} gate_pass rows)",
          flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = p.parse_args()
    asyncio.run(_main(args.date))
