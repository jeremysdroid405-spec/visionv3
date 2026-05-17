"""Run the freshly re-ingested 2026-05-06 model outputs through all three
tier gates. Reports qualification counts and dumps qualifiers per tier."""
from __future__ import annotations
import asyncio
import csv
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient
from services.replay.mlb_replay_multi_tier_eval import (
    eval_safe_haven, eval_front_lines, eval_war_zone,
)

GAME_DATE = "2026-05-06"
SNAPSHOT  = f"{GAME_DATE}T11:00:00Z"
OUT_DIR   = Path("/app/backend/backtest5625")

EVALS = {
    "safe_haven":  eval_safe_haven,
    "front_lines": eval_front_lines,
    "war_zone":    eval_war_zone,
}

QUAL_COLS = [
    "player_name","stat_family","market","is_alternate","line","side",
    "book","odds","projection_mu","sigma",
    "model_probability","fair_probability","implied_probability","edge",
    "hit_rate_l5","hit_rate_l10","hit_rate_l20","cv",
    "home_team","away_team","commence_time","event_id",
]


async def amain():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    print(f"Loading fresh model outputs for {GAME_DATE} @ {SNAPSHOT}...", flush=True)
    cursor = db.mlb_replay_model_outputs.find(
        {"game_date": GAME_DATE, "snapshot_iso": SNAPSHOT}, {"_id": 0})
    rows = []
    async for r in cursor: rows.append(r)
    total = len(rows)
    print(f"  total props loaded: {total:,}")

    # Per-tier counters
    tier_pass = {t: 0 for t in EVALS}
    tier_fail_reasons = {t: Counter() for t in EVALS}
    qualified_rows = {t: [] for t in EVALS}

    for r in rows:
        for tier, fn in EVALS.items():
            passed, failed = fn(r)
            if passed:
                tier_pass[tier] += 1
                qualified_rows[tier].append(r)
            else:
                for fg in failed: tier_fail_reasons[tier][fg] += 1

    # Save per-tier qualifier CSVs
    for tier, picks in qualified_rows.items():
        fpath = OUT_DIR / f"qualified_{tier}.csv"
        with fpath.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=QUAL_COLS, extrasaction="ignore")
            w.writeheader()
            for p in picks: w.writerow(p)
        print(f"  wrote {len(picks):>5,}  →  {fpath}")

    print()
    print("=" * 78)
    print(f"GATE QUALIFICATION ON FRESH 2026-05-06 MODEL OUTPUT ({total:,} props)")
    print("=" * 78)
    print()
    print(f"  {'Tier':<14} {'Qualified':>10}  {'% of total':>10}")
    for tier in ("safe_haven", "front_lines", "war_zone"):
        n = tier_pass[tier]
        pct = (n / total * 100) if total else 0
        print(f"  {tier:<14} {n:>10,}  {pct:>9.2f}%")
    print()
    print("Failed-gate breakdown:")
    for tier in ("safe_haven", "front_lines", "war_zone"):
        print(f"\n  {tier}:")
        for fg, c in tier_fail_reasons[tier].most_common():
            print(f"    {fg:<24} {c:>6,}")

    cli.close()


if __name__ == "__main__":
    asyncio.run(amain())
