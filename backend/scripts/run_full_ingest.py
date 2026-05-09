#!/usr/bin/env python3
"""
PropVision Replay — full 30-day NBA ingest CLI.

USAGE
-----
    # Plan-only (no API, no DB)
    python /app/backend/scripts/run_full_ingest.py --plan-only \\
        --start 2024-02-01 --end 2024-03-01

    # Execute (resumable, idempotent)
    ODDS_API_KEY=... python /app/backend/scripts/run_full_ingest.py --execute \\
        --start 2024-02-01 --end 2024-03-01 \\
        --label "phase1_30day_nba_v1" \\
        --out /app/audit_reports/replay_full_ingest_2024-02-01_to_2024-03-01.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from dotenv import load_dotenv
load_dotenv(str(REPO_ROOT / "backend/.env"))

from motor.motor_asyncio import AsyncIOMotorClient   # noqa: E402

from scripts.odds_api_backfill.client import OddsAPIClient  # noqa: E402
from services.replay import (                                # noqa: E402
    REPLAY_BOOK_WHITELIST_PHASE1, REPLAY_NBA_MARKETS,
    REPLAY_REGIONS_PHASE1, REPLAY_WINDOW_LABELS,
    new_run_id,
)
from services.replay.full_ingest import run_full_ingest      # noqa: E402

HARD_CREDIT_KILL_SWITCH = 1_000_000


def _parse_date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Full 30-day NBA replay ingest driver.",
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    p.add_argument("--start", required=True, type=_parse_date,
                   help="UTC date YYYY-MM-DD (inclusive)")
    p.add_argument("--end", required=True, type=_parse_date,
                   help="UTC date YYYY-MM-DD (inclusive)")
    p.add_argument("--label", default="full_ingest")
    p.add_argument("--out", default=None,
                   help="Path to dump full result JSON.")
    p.add_argument("--telemetry-every", type=int, default=25)
    p.add_argument("--safety-every", type=int, default=200)
    return p


async def _amain():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = _build_parser().parse_args()

    if args.plan_only:
        plan = {
            "phase":                "1_full_ingest_plan_only",
            "range_start_utc":      args.start.isoformat(),
            "range_end_utc":        args.end.isoformat(),
            "windows":              REPLAY_WINDOW_LABELS,
            "markets":              REPLAY_NBA_MARKETS,
            "books":                REPLAY_BOOK_WHITELIST_PHASE1,
            "regions":              REPLAY_REGIONS_PHASE1,
            "hard_credit_kill_switch": HARD_CREDIT_KILL_SWITCH,
            "estimated_credits_per_event_full_ladder": (
                10 * len(REPLAY_NBA_MARKETS) * len(REPLAY_REGIONS_PHASE1)
                * len(REPLAY_WINDOW_LABELS)
            ),
            "telemetry_every_n_calls": args.telemetry_every,
            "safety_check_every_n_calls": args.safety_every,
        }
        print(json.dumps(plan, indent=2))
        return

    if not os.environ.get("ODDS_API_KEY"):
        raise RuntimeError("ODDS_API_KEY missing")
    if not os.environ.get("MONGO_URL") or not os.environ.get("DB_NAME"):
        raise RuntimeError("MONGO_URL / DB_NAME missing")

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    run_id = new_run_id()
    started = datetime.now(timezone.utc)
    print(f"[full_ingest] run_id={run_id} label={args.label} "
          f"start={args.start.date()} end={args.end.date()}")

    async with OddsAPIClient(api_key=os.environ["ODDS_API_KEY"]) as client:
        out: Dict[str, Any] = await run_full_ingest(
            db,
            client=client,
            range_start=args.start,
            range_end=args.end,
            run_id=run_id,
            hard_credit_kill_switch=HARD_CREDIT_KILL_SWITCH,
            safety_check_every_n_calls=args.safety_every,
            telemetry_every_n_calls=args.telemetry_every,
            log_fn=print,
        )

    finished = datetime.now(timezone.utc)
    out["wallclock_seconds"] = (finished - started).total_seconds()
    out["started_utc"] = started.isoformat()
    out["finished_utc"] = finished.isoformat()
    out["label"] = args.label

    blob = json.dumps(out, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(blob)
        print(f"[full_ingest] wrote {args.out}")
    print("---FINAL---")
    print(blob)
    cli.close()


def main():
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
