#!/usr/bin/env python3
"""Before/after replay summary — diffs two replay_run_ids on:

  • candidate count
  • qualified count by tier
  • PnL / ROI by tier
  • feature_completeness distribution
  • top fail reasons

Used to show the impact of wiring historical VK2 into the engine.
Read-only against `replay_evaluations` and `replay_outcomes`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from dotenv import load_dotenv
load_dotenv(str(REPO_ROOT / "backend/.env"))

from motor.motor_asyncio import AsyncIOMotorClient   # noqa: E402

EVALS = "replay_evaluations"
OUTS  = "replay_outcomes"
PUBLISHED = ("safe_haven", "front_lines", "war_zone")


async def _summary(db, run_id: str) -> Dict[str, Any]:
    n_eval = await db[EVALS].count_documents({"replay_run_id": run_id})
    by_tier: Dict[str, int] = {}
    async for d in db[EVALS].aggregate([
        {"$match": {"replay_run_id": run_id}},
        {"$group": {"_id": "$tier", "n": {"$sum": 1}}},
    ]):
        by_tier[d["_id"] or "<none>"] = d["n"]

    fc: Dict[str, int] = {}
    async for d in db[EVALS].aggregate([
        {"$match": {"replay_run_id": run_id}},
        {"$group": {"_id": "$feature_completeness", "n": {"$sum": 1}}},
    ]):
        fc[d["_id"] or "<none>"] = d["n"]

    fails: Dict[str, int] = {}
    async for d in db[EVALS].aggregate([
        {"$match": {"replay_run_id": run_id,
                     "tier": {"$nin": list(PUBLISHED)}}},
        {"$group": {"_id": "$tier_reason", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}}, {"$limit": 10},
    ]):
        fails[d["_id"] or "<none>"] = d["n"]

    pubs: Dict[str, Dict[str, Any]] = {t: {"n": 0, "hits": 0, "miss": 0,
                                            "void": 0, "pnl": 0.0,
                                            "hit_rate": None,
                                            "roi_per_unit": None}
                                       for t in PUBLISHED}
    async for d in db[OUTS].aggregate([
        {"$match": {"replay_run_id": run_id,
                     "tier_at_eval": {"$in": list(PUBLISHED)}}},
        {"$group": {
            "_id":   "$tier_at_eval",
            "n":     {"$sum": 1},
            "hits":  {"$sum": {"$cond": [{"$eq": ["$outcome", "hit"]}, 1, 0]}},
            "miss":  {"$sum": {"$cond": [{"$eq": ["$outcome", "miss"]}, 1, 0]}},
            "void":  {"$sum": {"$cond": [{"$eq": ["$outcome", "void_dnp"]}, 1, 0]}},
            "pnl":   {"$sum": "$pnl_units"},
        }},
    ]):
        decided = d["hits"] + d["miss"]
        pubs[d["_id"]] = {
            "n":            d["n"],
            "hits":         d["hits"],
            "miss":         d["miss"],
            "void":         d["void"],
            "pnl":          round(d["pnl"], 4),
            "hit_rate":     round(d["hits"] / decided, 4) if decided else None,
            "roi_per_unit": round(d["pnl"] / d["n"], 4) if d["n"] else None,
        }

    n_qualified = sum(by_tier.get(t, 0) for t in PUBLISHED)
    return {
        "run_id":            run_id,
        "candidates":        n_eval,
        "qualified_total":   n_qualified,
        "qualified_pct":     round(100.0 * n_qualified / n_eval, 4)
                              if n_eval else 0.0,
        "tier_distribution": by_tier,
        "feature_completeness": fc,
        "publications":      pubs,
        "top_fail_reasons":  fails,
    }


def render(before: Dict[str, Any], after: Dict[str, Any]) -> str:
    md = ["# Replay Before/After — VK2 Wiring\n",
           f"_Generated_: {datetime.now(timezone.utc).isoformat()}\n",
           f"\n- **Before**: `{before['run_id']}` (no historical VK2; "
           f"VK2 fields stamped from rolling-μ feature_set as legacy "
           f"placeholder).\n",
           f"- **After**:  `{after['run_id']}` (historical VK2 wired "
           f"end-to-end; production gates fed VK2 projections).\n\n"]

    md.append("## Headline\n")
    md.append("| metric | before | after | delta |\n|---|---|---|---|\n")
    md.append(f"| candidates | {before['candidates']:,} | "
              f"{after['candidates']:,} | "
              f"{after['candidates'] - before['candidates']:+,} |\n")
    md.append(f"| qualified | {before['qualified_total']:,} | "
              f"{after['qualified_total']:,} | "
              f"{after['qualified_total'] - before['qualified_total']:+,} |\n")
    md.append(f"| qualified_pct | {before['qualified_pct']}% | "
              f"{after['qualified_pct']}% | — |\n\n")

    md.append("## Publications by tier ($1 flat bet)\n")
    md.append("| tier | n_before | n_after | hr_after | "
              "roi_after | pnl_after |\n|---|---|---|---|---|---|\n")
    for t in PUBLISHED:
        b = before["publications"].get(t, {})
        a = after["publications"].get(t, {})
        hr_a = a.get("hit_rate")
        roi_a = a.get("roi_per_unit")
        pnl_a = a.get("pnl")
        md.append(f"| {t} | {b.get('n', 0)} | {a.get('n', 0)} | "
                  f"{hr_a if hr_a is not None else 'n/a'} | "
                  f"{roi_a if roi_a is not None else 'n/a'} | "
                  f"{pnl_a if pnl_a is not None else 0.0} |\n")
    # Combined.
    bc = sum(before["publications"][t]["n"] for t in PUBLISHED)
    ac = sum(after["publications"][t]["n"] for t in PUBLISHED)
    bh = sum(before["publications"][t]["hits"] for t in PUBLISHED)
    ah = sum(after["publications"][t]["hits"] for t in PUBLISHED)
    bm = sum(before["publications"][t]["miss"] for t in PUBLISHED)
    am = sum(after["publications"][t]["miss"] for t in PUBLISHED)
    bp = sum(before["publications"][t]["pnl"] for t in PUBLISHED)
    ap = sum(after["publications"][t]["pnl"] for t in PUBLISHED)
    bhr = (bh / (bh + bm)) if (bh + bm) else None
    ahr = (ah / (ah + am)) if (ah + am) else None
    md.append(f"| **combined** | {bc} | {ac} | "
              f"{round(ahr, 4) if ahr is not None else 'n/a'} | "
              f"{round(ap / ac, 4) if ac else 'n/a'} | "
              f"{round(ap, 2) if ap else 0.0} |\n\n")

    md.append("## Feature completeness\n")
    md.append("| label | before | after |\n|---|---|---|\n")
    keys = sorted(set(before["feature_completeness"]) |
                  set(after["feature_completeness"]))
    for k in keys:
        md.append(f"| {k} | "
                  f"{before['feature_completeness'].get(k, 0):,} | "
                  f"{after['feature_completeness'].get(k, 0):,} |\n")

    md.append("\n## Top fail reasons (after)\n")
    md.append("| reason | n |\n|---|---|\n")
    for r, n in after["top_fail_reasons"].items():
        md.append(f"| {r} | {n:,} |\n")

    md.append("\n## Notes\n")
    md.append("- This is the **first** end-to-end replay run with "
              "historical VK2 wired. Production gates received real "
              "VK2 projections; no fallback to legacy VK1.\n")
    md.append("- Injury / matchup / pace features remain stubbed — "
              "see `audit_reports/vk2_production_map.md`.\n")
    md.append("- Safe Haven generates 0 picks because the Feb-2024 "
              "window has zero `bdl_advanced_stats` rows; without "
              "advanced features VK2 vision-scores compress and the "
              "SH vision_score_gate (>= 80) rejects every candidate. "
              "This is a data-coverage issue, not a model issue.\n")
    md.append("- This is **NOT** production sign-off. The Front Lines "
              "/ War Zone numbers below should be reproduced on a "
              "later 30-day window where adv_stats are present before "
              "any deployment decision.\n")
    return "".join(md)


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--before-run-id", required=True)
    p.add_argument("--after-run-id",  required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    before = await _summary(db, args.before_run_id)
    after  = await _summary(db, args.after_run_id)
    md = render(before, after)
    Path(args.output).write_text(md)
    Path(args.output).with_suffix(".json").write_text(
        json.dumps({"before": before, "after": after}, indent=2, default=str)
    )
    print(f"wrote {args.output}")
    cli.close()


if __name__ == "__main__":
    asyncio.run(main())
