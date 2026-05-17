"""CLI: Layer-4 gate evaluation + backtest grading for one date."""
from __future__ import annotations
import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient
from services.replay.mlb_replay_engine import SCORING_CONFIG_VERSION
from services.replay.mlb_replay_gate_eval import (
    DEFAULT_MEM_LIMIT_MB, GATE_CONFIG_VERSION, run_layer4_for_date,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s %(message)s")


async def amain(args):
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    snapshot_iso = args.snapshot_iso or f"{args.date}T11:00:00Z"
    s = await run_layer4_for_date(
        db, args.date,
        snapshot_iso=snapshot_iso,
        scoring_config_version=args.scoring_version or SCORING_CONFIG_VERSION,
        gate_config_version=args.gate_version or GATE_CONFIG_VERSION,
        mem_limit_mb=args.mem_limit,
    )
    o = s["overall"]
    print(f"\n=== Layer 4 summary for {args.date} @ {snapshot_iso} ===")
    print(f"  gate_config_version        {s['gate_config_version']}")
    print(f"  scoring_config_version     {s['scoring_config_version']}")
    print(f"  rows_scanned               {s['rows_scanned']:,}")
    print(f"  gate_pass / gate_fail      {s['gate_pass']:,} / {s['gate_fail']:,}")
    print(f"  failed_gate_breakdown      {s['failed_gate_breakdown']}")
    print(f"  qualified picks            {o['total']:,}")
    print(f"  wins/losses/pushes/ungrad  {o['wins']}/{o['losses']}/"
          f"{o['pushes']}/{o['ungraded']}")
    print(f"  hit_rate                   {o['hit_rate_pct']}")
    print(f"  profit_units / stake       {o['profit_units']:.2f} / "
          f"{o['stake_units']:.2f}")
    print(f"  ROI                        {o['roi_pct']}")
    print(f"  avg_odds / median_odds     {o['avg_odds']} / {o['median_odds']}")
    print(f"  elapsed                    {s['elapsed_s']:.2f}s")
    print(f"  RSS start/peak/end         "
          f"{s['rss_mb_start']}/{s['rss_mb_peak']}/{s['rss_mb_end']} MB")
    cli.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True)
    p.add_argument("--snapshot-iso", default=None)
    p.add_argument("--scoring-version", default=None)
    p.add_argument("--gate-version", default=None)
    p.add_argument("--mem-limit", default=DEFAULT_MEM_LIMIT_MB, type=int)
    asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    main()
