#!/usr/bin/env python3
"""Safe Haven near-miss debug.

For a given replay_run_id, finds the candidates that came CLOSEST to
qualifying for `safe_haven` and breaks down which gate blocked each.
Useful for understanding whether SH=0 is a thresholding problem or a
feature-coverage problem.

Outputs:
  - count of SH-attempt candidates by blocking gate
  - top 25 closest near-misses (by `vision_score_v2`)
  - feature-contribution breakdown (vk2 projection, edge, hr, cv,
    odds, vision_score_v2)
  - estimated SH count if injury / matchup wiring lifted
    `vision_score_v2` to its production typical band

Read-only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
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

EVALS = "replay_evaluations"
SH_PREFIX = "safe_haven_failed:"


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--top-n", type=int, default=25)
    args = p.parse_args()

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    # Distribution of SH-attempt blocking gates.
    by_gate: Dict[str, int] = {}
    async for d in db[EVALS].aggregate([
        {"$match": {"replay_run_id": args.run_id,
                     "tier_reason": {"$regex": "^safe_haven_failed"}}},
        {"$group": {"_id": "$tier_reason", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]):
        by_gate[d["_id"] or "<none>"] = d["n"]

    # vision_score_v2 distribution for SH-attempt rows.
    v2_buckets: List[Dict[str, Any]] = []
    async for d in db[EVALS].aggregate([
        {"$match": {"replay_run_id": args.run_id,
                     "tier_reason": {"$regex": "^safe_haven_failed"},
                     "vision_score_v2": {"$ne": None}}},
        {"$bucket": {
            "groupBy": "$vision_score_v2",
            "boundaries": [0, 30, 40, 50, 60, 70, 75, 80, 85, 90, 100],
            "default": "other",
            "output": {"n": {"$sum": 1}},
        }},
    ]):
        v2_buckets.append({"bucket": d["_id"], "n": d["n"]})

    # Top N near misses — strongest candidates that didn't make SH.
    near_miss: List[Dict[str, Any]] = []
    cursor = db[EVALS].find(
        {"replay_run_id": args.run_id,
         "tier_reason": {"$regex": "^safe_haven_failed"}},
        {"_id": 0,
         "player": 1, "stat_family": 1, "line": 1, "side": 1,
         "odds_american": 1, "ref_odds": 1,
         "vk2_projection": 1, "vk2_p_over": 1,
         "p_model": 1, "p_true_active": 1, "edge_vs_fair": 1,
         "feature_set.cv": 1, "feature_set.hit_rate_l20": 1,
         "feature_set.ceiling_rate": 1,
         "vision_score_v2": 1, "tier_reason": 1,
         "vk2_adv_coverage_l10": 1,
         "feature_completeness": 1},
    ).sort("vision_score_v2", -1).limit(args.top_n)
    async for d in cursor:
        near_miss.append(d)

    # Counter-factual: how many would have hit SH if v2 lifted to >= 80?
    # We approximate this by: count rows with edge_vs_fair >= SH min
    # and HR_l20 >= 70 and CV <= 35 — same shape as SH gates minus
    # the v2 (which can't be computed without injury/matchup).
    near_threshold = await db[EVALS].count_documents({
        "replay_run_id": args.run_id,
        "tier_reason": {"$regex": "^safe_haven_failed"},
        "edge_vs_fair": {"$gte": 1.5},
        "feature_set.hit_rate_l20": {"$gte": 0.70},
        "feature_set.cv": {"$lte": 0.40},
        "p_true_active": {"$gte": 65.0},
    })

    md: List[str] = [
        f"# Safe Haven Debug — `{args.run_id}`\n",
        f"_Generated_: {datetime.now(timezone.utc).isoformat()}\n\n",
        "## Blocking gate distribution\n",
        "| reason | n |\n|---|---|\n",
    ]
    for k, v in by_gate.items():
        md.append(f"| {k} | {v:,} |\n")

    md.append("\n## vision_score_v2 distribution (SH-attempt rows)\n")
    md.append("| bucket | n |\n|---|---|\n")
    for b in v2_buckets:
        md.append(f"| {b['bucket']} | {b['n']:,} |\n")

    md.append(f"\n## Top {args.top_n} closest near-misses\n")
    md.append("| player | family | line/side | odds | vk2_proj | edge | "
              "p_true | hr_l20 | cv | v2 | adv_l10 | reason |\n")
    md.append("|---|---|---|---|---|---|---|---|---|---|---|---|\n")
    for d in near_miss:
        fs = d.get("feature_set") or {}
        md.append(
            f"| {d.get('player','')[:18]} | {d.get('stat_family','')} | "
            f"{d.get('line')}/{d.get('side','')} | "
            f"{d.get('odds_american')} | "
            f"{d.get('vk2_projection')} | "
            f"{d.get('edge_vs_fair')} | "
            f"{d.get('p_true_active')} | "
            f"{fs.get('hit_rate_l20')} | "
            f"{fs.get('cv')} | "
            f"{d.get('vision_score_v2')} | "
            f"{d.get('vk2_adv_coverage_l10')} | "
            f"{d.get('tier_reason','')} |\n"
        )

    md.append("\n## Counter-factual: estimated SH count if v2 wasn't blocking\n")
    md.append(f"Candidates already passing SH-shaped predicates (edge≥1.5, "
              f"HR L20≥70%, CV≤40%, TP≥65%) **but blocked by some other "
              f"SH gate**: **{near_threshold:,}**.\n\n"
              "These are the rows most likely to qualify if injury / "
              "matchup wiring lifts vision_score_v2 to ≥80. This is a "
              "ROUGH estimate — actual SH gating also depends on book "
              "coverage, sigma, and the other v2 components.\n")

    Path(args.output).write_text("".join(md))
    Path(args.output).with_suffix(".json").write_text(
        json.dumps({
            "run_id": args.run_id,
            "by_gate": by_gate,
            "v2_buckets": v2_buckets,
            "near_miss_top_n": near_miss,
            "estimated_sh_unblocked_count": near_threshold,
        }, indent=2, default=str)
    )
    print(f"wrote {args.output}")
    cli.close()


if __name__ == "__main__":
    asyncio.run(main())
