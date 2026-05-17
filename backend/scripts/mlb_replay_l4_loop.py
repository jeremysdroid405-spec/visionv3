"""Minimal L4 multi-tier loop for the 15-day sweep — quiet logging, no
per-date verbose printing. Just runs the gate eval, persists audit
rows, prints one line per (date × tier)."""
from __future__ import annotations
import asyncio
import logging
import os
import sys

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient
from services.replay.mlb_replay_engine import SCORING_CONFIG_VERSION
from services.replay.mlb_replay_multi_tier_eval import run_multi_tier_for_date

logging.basicConfig(level=logging.WARNING)

DATES = [f"2026-05-{d:02d}" for d in range(1, 16)]


async def amain():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    for d in DATES:
        # Skip if already done.
        existing = await db.mlb_replay_audit.count_documents(
            {"game_date": d, "snapshot_iso": f"{d}T11:00:00Z"})
        if existing >= 3:
            print(f"  {d}  skipped (audit already has {existing} rows)", flush=True)
            continue
        try:
            out = await run_multi_tier_for_date(
                db, d,
                snapshot_iso=f"{d}T11:00:00Z",
                scoring_config_version=SCORING_CONFIG_VERSION,
                mem_limit_mb=1500,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  {d}  ERROR: {exc!r}", flush=True)
            continue
        print(f"\n=== {d} ===", flush=True)
        for tier in ("safe_haven", "front_lines", "war_zone"):
            td = out["tiers"][tier]
            a = td["audit"]
            o = td["overall"]
            hr = f"{o['hit_rate_pct']:.1f}%" if o["hit_rate_pct"] is not None else "  --  "
            roi = f"{o['roi_pct']:+.1f}%" if o["roi_pct"] is not None else "  --  "
            print(f"  {tier:<12} {a['serial']:<40} "
                  f"picks={o['total']:>4}  HR={hr:>6}  ROI={roi:>7}  "
                  f"profit={o['profit_units']:+7.2f}u  "
                  f"checksum={a['pick_set_checksum'][:12]}", flush=True)
    cli.close()


if __name__ == "__main__":
    asyncio.run(amain())
