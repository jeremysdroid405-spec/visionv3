"""Capture BEFORE/AFTER snapshot of MLB HF projections for the
Phase 2A audit players.

Reads `mlb_prop_scores` (active) for the four audit players + a
specific stat line each, dumps the relevant scoring fields to a
JSON file. Run once BEFORE the retrain to capture baseline, then
again AFTER recompute to produce the comparison report.

Usage::

    cd /app/backend && python scripts/phase2a_snapshot_audit.py \
        --tag before
    cd /app/backend && python scripts/phase2a_snapshot_audit.py \
        --tag after
    cd /app/backend && python scripts/phase2a_snapshot_audit.py \
        --diff
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


SNAPSHOT_DIR = "/app/backend/models/mlb_hf/_phase2a_audit"
TARGETS = [
    {"player_name": "Andy Pages", "stat_type": "Hits", "line": 0.5},
    {"player_name": "Kyle Tucker", "stat_type": "Hits", "line": 0.5},
    {"player_name": "Freddie Freeman", "stat_type": "Hits", "line": 0.5},
    {"player_name": "Ozzie Albies", "stat_type": "Hits+Runs+RBIs",
     "line": 1.5},
]
CAPTURE_FIELDS = (
    "player_name", "stat_type", "line", "recommendation",
    "model_projection", "predicted", "mu_final_projection",
    "tp", "tp_source", "market_probability",
    "edge_vs_fair", "total_edge", "edge_pct",
    "gate_results", "tier_gate_results", "routed_tier",
    "batter_hand", "batting_order", "venue",
    "opp_pitcher_name", "opp_pitcher_throws",
    "opp_pitcher_era", "opp_pitcher_whip", "opp_pitcher_k9",
    "same_hand_matchup", "opposite_hand_matchup",
    "active", "stale_reason",
    "projection_model_version",
)


async def snapshot(tag: str):
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    out: List[Dict[str, Any]] = []
    for t in TARGETS:
        # Match either OVER or UNDER recommendation rows for the line.
        docs = await db.mlb_prop_scores.find(
            {
                "player_name": t["player_name"],
                "stat_type": t["stat_type"],
                "line": t["line"],
                "active": True,
            },
            {"_id": 0, **{f: 1 for f in CAPTURE_FIELDS}},
        ).to_list(length=10)
        # If multiple book rows, dedupe by recommendation.
        seen = {}
        for d in docs:
            seen[(d.get("recommendation"),)] = d
        for d in seen.values():
            out.append(d)
    path = os.path.join(SNAPSHOT_DIR, f"audit_snapshot_{tag}.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"Snapshot ({tag}): {len(out)} rows → {path}")
    client.close()


def diff_snapshots():
    before_p = os.path.join(SNAPSHOT_DIR, "audit_snapshot_before.json")
    after_p = os.path.join(SNAPSHOT_DIR, "audit_snapshot_after.json")
    if not (os.path.exists(before_p) and os.path.exists(after_p)):
        print("Missing snapshot. Run with --tag before and --tag after first.")
        return
    with open(before_p) as f: before = json.load(f)
    with open(after_p) as f: after = json.load(f)
    by_key_before = {(d["player_name"], d["stat_type"],
                       d.get("recommendation")): d for d in before}
    by_key_after = {(d["player_name"], d["stat_type"],
                     d.get("recommendation")): d for d in after}
    keys = sorted(set(by_key_before) | set(by_key_after))
    print("\n" + "=" * 110)
    print(f"PHASE 2A RETRAIN — BEFORE/AFTER for {len(keys)} audit rows")
    print("=" * 110)
    fields_to_show = [
        "model_projection", "predicted", "mu_final_projection",
        "tp", "edge_vs_fair", "total_edge",
        "batter_hand", "opp_pitcher_throws",
        "same_hand_matchup", "opposite_hand_matchup",
        "projection_model_version",
    ]
    for k in keys:
        b = by_key_before.get(k, {})
        a = by_key_after.get(k, {})
        print(f"\n── {k[0]:<20s} {k[1]:<20s} {k[2] or '-':<6s}")
        for fld in fields_to_show:
            bv = b.get(fld); av = a.get(fld)
            mark = "✓" if bv != av else " "
            print(f"   [{mark}] {fld:<32s} {str(bv)[:30]:>30s}  →  {av}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", choices=["before", "after"], default=None)
    ap.add_argument("--diff", action="store_true")
    args = ap.parse_args()
    if args.tag:
        asyncio.run(snapshot(args.tag))
    elif args.diff:
        diff_snapshots()
    else:
        ap.print_help()
