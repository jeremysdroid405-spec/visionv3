#!/usr/bin/env python3
"""Safe Haven activation canary — small sample, no Odds API.

Reproduces the production scoring stack on 10 highest-FL/WZ-density
NBA events from the existing Feb-2024 replay window using:

  - existing replay_props_normalized (Stage A)
  - existing replay_results / bdl_historical_game_logs (read-only)
  - rebuilds Stage-B cache rows ONLY for the canary events
    (vk2_blob + matchup_blob + injury_blob)
  - Stage-C incremental scoring under a fresh run_id

NEVER calls the Odds API. NEVER mutates production collections.

Outputs `/app/audit_reports/sh_canary_<ts>.json` + `.md`.
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
from dotenv import load_dotenv  # noqa: E402
load_dotenv(str(REPO_ROOT / "backend/.env"))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from services.replay.engine import run_replay_engine  # noqa: E402
from services.replay.scoring_only import run_scoring_only  # noqa: E402


CANARY_EVENTS_DEFAULT = [
    "96b3392988cf7f67e4d4ade73adc390e",  # 2024-02-29
    "3895ca89124a7838e6e3e337d73a606a",  # 2024-02-09
    "086bdadf2294ca0d8eba86e1d8f951d8",  # 2024-02-15
    "709971c24dfc3bea07590c093fd5abc4",  # 2024-02-28
    "6405ed37b375739beca42069272d8e2a",  # 2024-02-04
    "806bf8db6e8cafa62ddb55e7df58b06e",  # 2024-02-23
    "fb3103a4e0cd4a43d71ea54171617bba",  # 2024-02-25
    "433ccafea8b23768fdc3ccbcf0f6c571",  # 2024-02-07
    "2b78d5e64c903115fe8e81dcc1a25b86",  # 2024-02-25
    "522175be153068393b064500385b6726",  # 2024-02-03
]


async def _stage_b_for_events(
    db, run_id: str, event_ids: List[str], log_fn=print,
) -> Dict[str, Any]:
    """Run the full engine but restrict to the canary events. Writes
    Stage-B cache rows + Stage-C-style eval rows under run_id."""
    return await run_replay_engine(
        db,
        replay_run_id=run_id,
        range_start=datetime(2024, 2, 1, tzinfo=timezone.utc),
        range_end=datetime(2024, 3, 5, 23, 59, tzinfo=timezone.utc),
        snapshot_label="t-30m",
        sport_key="basketball_nba",
        sport_short="nba",
        enable_vk2=True,
        cache_outputs=True,
        sample_event_ids=event_ids,
        log_fn=log_fn,
    )


async def _stage_c_for_run(
    db, *, source_run_id: str, run_id: str, log_fn=print,
) -> Dict[str, Any]:
    return await run_scoring_only(
        db,
        replay_run_id=run_id,
        source_run_ids=[source_run_id],
        sport_short="nba",
        log_fn=log_fn,
    )


async def _aggregate_canary_summary(
    db, *, stage_c_run_id: str, top_n_near_miss: int = 25,
) -> Dict[str, Any]:
    EVALS = "replay_evaluations"
    flt = {"replay_run_id": stage_c_run_id}

    # Tier distribution.
    tier_dist: Dict[str, int] = {}
    async for d in db[EVALS].aggregate([
        {"$match": flt},
        {"$group": {"_id": "$tier", "n": {"$sum": 1}}},
    ]):
        tier_dist[d["_id"] or "<none>"] = d["n"]

    # vision_score_v2 buckets.
    v2_buckets: List[Dict[str, Any]] = []
    async for d in db[EVALS].aggregate([
        {"$match": {**flt, "vision_score_v2": {"$ne": None}}},
        {"$bucket": {
            "groupBy": "$vision_score_v2",
            "boundaries": [0, 30, 40, 50, 60, 70, 75, 80, 85, 90, 100],
            "default": "other",
            "output": {"n": {"$sum": 1}},
        }},
    ]):
        v2_buckets.append({"bucket": d["_id"], "n": d["n"]})

    # Threshold counts.
    thresholds = {}
    for thr in (60, 70, 75, 80):
        thresholds[f"v2_ge_{thr}"] = await db[EVALS].count_documents({
            **flt, "vision_score_v2": {"$gte": thr},
        })

    # SH-attempt blocking gate dist.
    sh_blocked: Dict[str, int] = {}
    async for d in db[EVALS].aggregate([
        {"$match": {**flt, "tier_reason": {"$regex": "^safe_haven_failed"}}},
        {"$group": {"_id": "$tier_reason", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]):
        sh_blocked[d["_id"] or "<none>"] = d["n"]
    top_blocking_reason = max(sh_blocked.items(), key=lambda kv: kv[1])[0] \
        if sh_blocked else None

    # Top near-misses among SH-attempt rows.
    near_miss: List[Dict[str, Any]] = []
    cursor = db[EVALS].find(
        {**flt, "tier_reason": {"$regex": "^safe_haven_failed"}},
        {"_id": 0, "player": 1, "stat_family": 1, "line": 1, "side": 1,
         "odds_american": 1, "ref_odds": 1, "vk2_projection": 1,
         "p_model": 1, "p_true_active": 1, "edge_vs_fair": 1,
         "feature_set.cv": 1, "feature_set.hit_rate_l20": 1,
         "feature_set.ceiling_rate": 1, "vision_score_v2": 1,
         "tier_reason": 1, "vk2_adv_coverage_l10": 1,
         "feature_completeness": 1,
         "usage_vacuum_factor": 1, "usage_spike": 1,
         "matchup_strength": 1, "matchup_pace_factor": 1,
         "rotation_compression": 1, "key_player_out_flag": 1},
    ).sort("vision_score_v2", -1).limit(top_n_near_miss)
    async for d in cursor:
        near_miss.append(d)

    # Counter-factual: how many would have hit SH if v2 unblocked?
    near_threshold = await db[EVALS].count_documents({
        **flt,
        "tier_reason": {"$regex": "^safe_haven_failed"},
        "edge_vs_fair": {"$gte": 1.5},
        "feature_set.hit_rate_l20": {"$gte": 0.70},
        "feature_set.cv": {"$lte": 0.40},
        "p_true_active": {"$gte": 65.0},
    })

    # Injury / matchup coverage on the canary slice.
    injury_full = await db[EVALS].count_documents({
        **flt, "injury_feature_completeness": "injury_full",
    })
    matchup_full = await db[EVALS].count_documents({
        **flt, "matchup_feature_completeness": "matchup_full",
    })
    total_evals = await db[EVALS].count_documents(flt)

    # Did the new context layers MOVE any rows?  Count rows where the
    # injury blob shifted vacuum factor above 1.05 OR usage_spike==True
    # AND the row reached v2 >= 70.
    moved_rows = await db[EVALS].count_documents({
        **flt,
        "vision_score_v2": {"$gte": 70},
        "$or": [
            {"usage_vacuum_factor": {"$gte": 1.05}},
            {"usage_spike": True},
            {"matchup_strength": {"$gte": 0.65}},
        ],
    })

    return {
        "stage_c_run_id":       stage_c_run_id,
        "total_evals":          total_evals,
        "tier_distribution":    tier_dist,
        "v2_buckets":           v2_buckets,
        "v2_thresholds":        thresholds,
        "sh_blocking_dist":     sh_blocked,
        "top_blocking_reason":  top_blocking_reason,
        "near_miss_top_n":      near_miss,
        "estimated_sh_unblocked_count": near_threshold,
        "context_coverage": {
            "injury_full_count":  injury_full,
            "matchup_full_count": matchup_full,
            "total":              total_evals,
        },
        "rows_moved_by_context": moved_rows,
    }


def _render_md(events: List[str], stage_b_summary: Dict[str, Any],
               stage_c_summary: Dict[str, Any],
               canary: Dict[str, Any]) -> str:
    md: List[str] = [
        f"# Safe Haven Activation Canary — `{canary['stage_c_run_id']}`\n",
        f"_Generated_: {datetime.now(timezone.utc).isoformat()}\n\n",
        "## Scope\n",
        f"- **Sport**: NBA\n",
        f"- **Window**: 2024-02-01 → 2024-03-05 (existing Stage-A data)\n",
        f"- **Events**: {len(events)} (highest FL+WZ density from "
        f"`vk2_full_30d_1778310068`)\n",
        f"- **Odds API credits spent**: 0 (Stage-B/C reuse + bdl reads only)\n",
        f"- **Production collections mutated**: 0\n\n",
        "## Runtime\n",
        f"- Stage-B (engine) wallclock: "
        f"**{stage_b_summary.get('wallclock_seconds', 0):.1f}s**\n",
        f"- Stage-C (incremental) wallclock: "
        f"**{stage_c_summary.get('wallclock_seconds', 0):.1f}s**\n\n",
        "## Stage-B counters\n",
        "```json\n",
        json.dumps(stage_b_summary.get("counters", {}), indent=2),
        "\n```\n\n",
        "## Tier distribution\n",
        "| tier | n |\n|---|---|\n",
    ]
    for k, v in canary["tier_distribution"].items():
        md.append(f"| {k} | {v:,} |\n")

    md.append("\n## Context coverage on canary\n")
    cc = canary["context_coverage"]
    md.append(
        f"- injury_full: {cc['injury_full_count']:,} / {cc['total']:,}\n"
        f"- matchup_full: {cc['matchup_full_count']:,} / {cc['total']:,}\n"
        f"- rows reaching v2 ≥ 70 with material context boost: "
        f"**{canary['rows_moved_by_context']:,}**\n\n",
    )

    md.append("## vision_score_v2 distribution\n")
    md.append("| bucket | n |\n|---|---|\n")
    for b in canary["v2_buckets"]:
        md.append(f"| {b['bucket']} | {b['n']:,} |\n")

    md.append("\n## v2 threshold counts\n")
    md.append("| threshold | n |\n|---|---|\n")
    for k, v in canary["v2_thresholds"].items():
        md.append(f"| {k} | {v:,} |\n")

    md.append("\n## SH-attempt blocking gate distribution\n")
    md.append("| reason | n |\n|---|---|\n")
    for k, v in canary["sh_blocking_dist"].items():
        md.append(f"| {k} | {v:,} |\n")
    if canary["top_blocking_reason"]:
        md.append(f"\n**Top blocking reason**: "
                   f"`{canary['top_blocking_reason']}`\n")

    md.append("\n## Top 25 Safe Haven near-misses\n")
    md.append("| player | family | line/side | odds | vk2_proj | edge | "
              "p_true | hr_l20 | cv | v2 | uv | spike | match | reason |\n")
    md.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
    for d in canary["near_miss_top_n"]:
        fs = d.get("feature_set") or {}
        md.append(
            f"| {(d.get('player') or '')[:18]} | {d.get('stat_family','')} | "
            f"{d.get('line')}/{d.get('side','')} | "
            f"{d.get('odds_american')} | "
            f"{d.get('vk2_projection')} | "
            f"{d.get('edge_vs_fair')} | "
            f"{d.get('p_true_active')} | "
            f"{fs.get('hit_rate_l20')} | "
            f"{fs.get('cv')} | "
            f"{d.get('vision_score_v2')} | "
            f"{d.get('usage_vacuum_factor')} | "
            f"{d.get('usage_spike')} | "
            f"{d.get('matchup_strength')} | "
            f"{(d.get('tier_reason') or '')[:32]} |\n"
        )

    md.append(
        f"\n## Counter-factual: SH rows blocked only by v2\n"
        f"Candidates already passing edge≥1.5, HR_L20≥70%, CV≤40%, "
        f"TP≥65% but blocked: **{canary['estimated_sh_unblocked_count']:,}**.\n"
    )

    return "".join(md)


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--events", nargs="*", default=None,
                    help="Override default canary event_ids.")
    p.add_argument("--out", default=None,
                    help="Output report path (json + md). Default: "
                         "/app/audit_reports/sh_canary_<ts>.{md,json}")
    args = p.parse_args()

    events = args.events or CANARY_EVENTS_DEFAULT
    ts = int(datetime.now(timezone.utc).timestamp())
    stage_b_run = f"sh_canary_{ts}"
    stage_c_run = f"sh_canary_c_{ts}"

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    print(f"[canary] Stage-B engine on {len(events)} events → {stage_b_run}")
    sb = await _stage_b_for_events(db, stage_b_run, events)
    print(f"[canary] Stage-B done: {sb.get('wallclock_seconds', 0):.1f}s")

    print(f"[canary] Stage-C incremental → {stage_c_run}")
    sc = await _stage_c_for_run(
        db, source_run_id=stage_b_run, run_id=stage_c_run)
    print(f"[canary] Stage-C done: {sc.get('wallclock_seconds', 0):.1f}s")

    canary = await _aggregate_canary_summary(
        db, stage_c_run_id=stage_c_run)

    # Decision rule.
    sh_count = canary["tier_distribution"].get("safe_haven", 0)
    near_miss_max_v2 = max(
        (m.get("vision_score_v2") or 0)
        for m in canary["near_miss_top_n"]
    ) if canary["near_miss_top_n"] else 0.0
    if sh_count > 0 or near_miss_max_v2 >= 75:
        decision = ("PROCEED_TO_FULL_REPLAY", "SH activation feasible — "
                                              "full-window replay should fire SH.")
    elif near_miss_max_v2 >= 70:
        decision = ("MARGINAL", "Near-misses approaching 70 but not 75. "
                                "Consider one more iteration on context contributions.")
    else:
        decision = ("STOP_AND_AUDIT_SH_FORMULA",
                     "No SH and no near-miss above v2=70. Audit SH formula / "
                     "vision_score_v2 contribution path before full replay.")

    canary["decision"] = decision[0]
    canary["decision_reason"] = decision[1]
    canary["near_miss_max_v2"] = near_miss_max_v2

    out = args.out or f"/app/audit_reports/sh_canary_{ts}"
    Path(out + ".json").write_text(json.dumps({
        "events": events,
        "stage_b_run_id": stage_b_run,
        "stage_c_run_id": stage_c_run,
        "stage_b_summary": sb,
        "stage_c_summary": sc,
        "canary": canary,
    }, indent=2, default=str))
    Path(out + ".md").write_text(
        _render_md(events, sb, sc, canary)
        + f"\n## Decision\n**{decision[0]}** — {decision[1]}\n"
        + f"Max near-miss v2: **{near_miss_max_v2}**.\n"
    )
    print(f"[canary] wrote {out}.md and {out}.json")
    print(f"[canary] decision: {decision[0]}")
    cli.close()


if __name__ == "__main__":
    asyncio.run(main())
