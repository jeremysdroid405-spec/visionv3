#!/usr/bin/env python3
"""Diff two replay_run_ids — tier counts, ROI, hit-rate, gate-reason
shifts, top promoted/demoted picks. Read-only against
`replay_evaluations` and `replay_outcomes`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from dotenv import load_dotenv
load_dotenv(str(REPO_ROOT / "backend/.env"))

from motor.motor_asyncio import AsyncIOMotorClient   # noqa: E402

EVALS = "replay_evaluations"
OUTS  = "replay_outcomes"
PUBLISHED = ("safe_haven", "front_lines", "war_zone")


async def _summary(db, run_id: str) -> Dict[str, Any]:
    n = await db[EVALS].count_documents({"replay_run_id": run_id})
    by_tier: Dict[str, int] = {}
    async for d in db[EVALS].aggregate([
        {"$match": {"replay_run_id": run_id}},
        {"$group": {"_id": "$tier", "n": {"$sum": 1}}},
    ]):
        by_tier[d["_id"] or "<none>"] = d["n"]
    fails: Dict[str, int] = {}
    async for d in db[EVALS].aggregate([
        {"$match": {"replay_run_id": run_id,
                     "tier": {"$nin": list(PUBLISHED)}}},
        {"$group": {"_id": "$tier_reason", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}}, {"$limit": 25},
    ]):
        fails[d["_id"] or "<none>"] = d["n"]
    pubs: Dict[str, Dict[str, Any]] = {t: {"n": 0, "hits": 0, "miss": 0,
                                            "pnl": 0.0, "roi": None,
                                            "hit_rate": None}
                                       for t in PUBLISHED}
    async for d in db[OUTS].aggregate([
        {"$match": {"replay_run_id": run_id,
                     "tier_at_eval": {"$in": list(PUBLISHED)}}},
        {"$group": {
            "_id":  "$tier_at_eval",
            "n":    {"$sum": 1},
            "hits": {"$sum": {"$cond": [{"$eq": ["$outcome", "hit"]}, 1, 0]}},
            "miss": {"$sum": {"$cond": [{"$eq": ["$outcome", "miss"]}, 1, 0]}},
            "pnl":  {"$sum": "$pnl_units"},
        }},
    ]):
        decided = d["hits"] + d["miss"]
        pubs[d["_id"]] = {
            "n":        d["n"], "hits": d["hits"], "miss": d["miss"],
            "pnl":      round(d["pnl"], 4),
            "roi":      round(d["pnl"] / d["n"], 4) if d["n"] else None,
            "hit_rate": round(d["hits"] / decided, 4) if decided else None,
        }
    return {"run_id": run_id, "n": n, "by_tier": by_tier,
            "publications": pubs, "top_fail_reasons": fails}


async def _promotions_demotions(db, before: str, after: str
                                 ) -> Tuple[List[Dict], List[Dict]]:
    join_keys = ("event_id", "snapshot_label", "canonical_key",
                 "bookmaker", "side")
    promoted: List[Dict] = []
    demoted: List[Dict] = []
    cursor = db[EVALS].find(
        {"replay_run_id": after},
        {k: 1 for k in (
            "_id", "event_id", "snapshot_label", "canonical_key",
            "bookmaker", "side", "tier", "tier_reason",
            "player", "stat_family", "line", "odds_american",
            "vision_score_v2",
        )},
    ).limit(20000)
    async for a in cursor:
        b = await db[EVALS].find_one(
            {"replay_run_id": before,
             **{k: a.get(k) for k in join_keys}},
            {k: 1 for k in ("_id", "tier", "tier_reason",
                              "vision_score_v2")},
        )
        if not b or b.get("tier") == a.get("tier"):
            continue
        diff = {
            "player": a.get("player"), "stat_family": a.get("stat_family"),
            "line": a.get("line"), "side": a.get("side"),
            "odds": a.get("odds_american"),
            "tier_before": b.get("tier"), "tier_after": a.get("tier"),
            "v2_before":   b.get("vision_score_v2"),
            "v2_after":    a.get("vision_score_v2"),
            "delta_v2":    ((a.get("vision_score_v2") or 0)
                            - (b.get("vision_score_v2") or 0)),
        }
        if (b.get("tier") not in PUBLISHED
                and a.get("tier") in PUBLISHED):
            promoted.append(diff)
        elif (b.get("tier") in PUBLISHED
                and a.get("tier") not in PUBLISHED):
            demoted.append(diff)
    promoted.sort(key=lambda r: -(r["delta_v2"] or 0))
    demoted.sort(key=lambda r: (r["delta_v2"] or 0))
    return promoted[:25], demoted[:25]


def _fmt_table(rows: List[Dict[str, Any]], cols: List[str]) -> str:
    if not rows:
        return "_(none)_\n"
    out = ["| " + " | ".join(cols) + " |",
           "|" + "|".join(["---"] * len(cols)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return "\n".join(out) + "\n"


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
    promoted, demoted = await _promotions_demotions(
        db, args.before_run_id, args.after_run_id)

    md: List[str] = [
        f"# Replay Diff — {args.before_run_id} → {args.after_run_id}\n",
        f"_Generated_: {datetime.now(timezone.utc).isoformat()}\n\n",
        "## Tier counts\n",
    ]
    rows = []
    for t in PUBLISHED + ("unqualified",):
        rows.append({"tier": t,
                     "before": before["by_tier"].get(t, 0),
                     "after":  after["by_tier"].get(t, 0),
                     "delta":  (after["by_tier"].get(t, 0)
                                 - before["by_tier"].get(t, 0))})
    md.append(_fmt_table(rows, ["tier", "before", "after", "delta"]))

    md.append("\n## Per-tier ROI\n")
    rows = []
    for t in PUBLISHED:
        b = before["publications"][t]; a = after["publications"][t]
        rows.append({"tier": t,
                     "n_before": b["n"], "n_after": a["n"],
                     "hr_before": b["hit_rate"], "hr_after": a["hit_rate"],
                     "roi_before": b["roi"], "roi_after": a["roi"],
                     "pnl_before": b["pnl"], "pnl_after": a["pnl"]})
    md.append(_fmt_table(rows, [
        "tier", "n_before", "n_after",
        "hr_before", "hr_after",
        "roi_before", "roi_after",
        "pnl_before", "pnl_after",
    ]))

    md.append("\n## Gate-reason shifts (top 15 by |delta|)\n")
    keys = list({**before["top_fail_reasons"], **after["top_fail_reasons"]})
    rows = []
    for k in keys:
        rows.append({"reason": k,
                     "before": before["top_fail_reasons"].get(k, 0),
                     "after":  after["top_fail_reasons"].get(k, 0),
                     "delta":  (after["top_fail_reasons"].get(k, 0)
                                 - before["top_fail_reasons"].get(k, 0))})
    rows.sort(key=lambda r: abs(r["delta"]), reverse=True)
    md.append(_fmt_table(rows[:15], ["reason", "before", "after", "delta"]))

    md.append("\n## Top 25 PROMOTED (unqualified → published)\n")
    md.append(_fmt_table(promoted, [
        "player", "stat_family", "line", "side", "odds",
        "tier_before", "tier_after", "v2_before", "v2_after", "delta_v2",
    ]))

    md.append("\n## Top 25 DEMOTED (published → unqualified)\n")
    md.append(_fmt_table(demoted, [
        "player", "stat_family", "line", "side", "odds",
        "tier_before", "tier_after", "v2_before", "v2_after", "delta_v2",
    ]))

    Path(args.output).write_text("".join(md))
    Path(args.output).with_suffix(".json").write_text(
        json.dumps({"before": before, "after": after,
                    "promoted": promoted, "demoted": demoted},
                   indent=2, default=str)
    )
    print(f"wrote {args.output}")
    cli.close()


if __name__ == "__main__":
    asyncio.run(main())
