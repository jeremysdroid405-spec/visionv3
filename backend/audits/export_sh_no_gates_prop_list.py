"""Dump the complete Safe Haven prop list (zero gate filtering) to
a single CSV for the 2026-05-03 → 2026-05-15 sweep.

Reads `mlb_test_outputs` for the GSS-MLB-{date}-SAFE-POOL serials
just produced and writes every row that routed to safe_haven.
"""
from __future__ import annotations
import asyncio
import csv
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


DATES = [
    (datetime(2026, 5, 3) + timedelta(days=i)).strftime("%Y%m%d")
    for i in range(0, 13)  # 05-03 .. 05-15 inclusive
]
SERIALS = [f"GSS-MLB-{d}-SAFE-POOL" for d in DATES]

FIELDS = [
    # routing
    "replay_serial", "game_date", "snapshot_iso",
    "event_id", "home_team", "away_team", "commence_time",
    "tier", "routed_tier", "tier_reference_odds", "tier_reference_book",
    # prop identity
    "player_name_normalized", "player_name",
    "stat_family", "market", "is_alternate", "is_alternate_market",
    "line", "side", "book", "odds",
    # model
    "projection_mu", "sigma",
    "model_probability", "fair_probability", "implied_probability",
    "edge", "edge_pct", "canonical_edge",
    "tp", "tp_source", "devig_method",
    # form
    "hit_rate_l5", "hit_rate_l10", "hit_rate_l20",
    "cv",
    # canonical aggregation
    "canonical_path", "canonical_market_key",
    "canonical_book_count_over", "canonical_book_count_under",
    "canonical_book_count_either_side",
    "canonical_best_over_price", "canonical_best_over_book",
    "canonical_best_under_price", "canonical_best_under_book",
    "canonical_devig_over_prob", "canonical_devig_under_prob",
    # gate audit
    "gate_pass", "failed_gates", "gate_failed_reasons",
    "accuracy_test_mode_active", "accuracy_test_bypass_applied",
    "accuracy_test_bypass_gates",
    # grading
    "grade_status", "actual_value", "profit_units", "stake_units",
]


async def main() -> None:
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_path = Path(
        f"/app/backend/audits/sh_no_gates_full_prop_list_"
        f"2026-05-03_2026-05-15_{stamp}.csv"
    )
    total = 0
    by_family: dict[str, int] = {}
    by_date: dict[str, int] = {}

    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        async for r in db["mlb_test_outputs"].find(
            {"replay_serial": {"$in": SERIALS},
             "routed_tier": "safe_haven"},
            projection={"_id": 0},
        ):
            row_out = []
            for k in FIELDS:
                v = r.get(k)
                if isinstance(v, (list, dict)):
                    v = str(v)
                row_out.append(v)
            w.writerow(row_out)
            total += 1
            fam = r.get("stat_family") or "?"
            by_family[fam] = by_family.get(fam, 0) + 1
            d = r.get("game_date") or "?"
            by_date[d] = by_date.get(d, 0) + 1

    print("=" * 72)
    print(f"  SAFE HAVEN — ZERO GATES PROP LIST")
    print(f"  dates: 2026-05-03 → 2026-05-15 (13 days)")
    print(f"  routed_tier filter: safe_haven (canonical best ≤ -300)")
    print("=" * 72)
    print(f"\n[output] {out_path}")
    print(f"[rows]   {total} total SH-routed props")
    print(f"\nPer stat_family:")
    for fam, n in sorted(by_family.items(), key=lambda kv: -kv[1]):
        print(f"  {fam:<22s} {n:>5d}")
    print(f"\nPer game_date:")
    for d, n in sorted(by_date.items()):
        print(f"  {d}  {n:>5d}")


if __name__ == "__main__":
    asyncio.run(main())
