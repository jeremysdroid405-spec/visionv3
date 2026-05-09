#!/usr/bin/env python3
"""Vision-score component walkthrough — Live SH vs Replay SH near-miss.

Read-only diagnostic. Dumps:
  • Top SH-routed replay near-misses (ref_odds ≤ -300) with EVERY gate
    actual / threshold / passed / reason
  • Sample live SH-tier prop with full gate_details
  • Vision-score field reconstruction (raw / v1 / v2)
  • Component breakdown of `vision_score_v2` for both populations

NEVER calls the Odds API. NEVER mutates production collections.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(str(REPO_ROOT / "backend/.env"))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from services.scoring.scoring_stack import compute_scoring_stack  # noqa: E402
from services.scoring.tp_engine import compute_tp  # noqa: E402
from services.scoring.coverage_filter import classify_coverage  # noqa: E402
from services.scoring.vision_v2 import (  # noqa: E402
    compute_vision_v2, _edge_component, _consistency_component,
    _context_component, _market_confidence_component,
)
from services.replay.scoring_only import _rebuild_prop  # noqa: E402


def _safe_dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)


async def _pull_replay_sh_near_misses(
    db, *, run_id: str, top_n: int,
) -> List[Dict[str, Any]]:
    """Pull from replay_evaluations the highest-vision_score_v2 rows
    that ROUTED to safe_haven (ref_odds ≤ -300) and FAILED."""
    cursor = db.replay_evaluations.find(
        {"replay_run_id": run_id,
         "ref_odds": {"$lte": -300},
         "tier_reason": {"$regex": "^safe_haven_failed"}},
        {"_id": 0,
         "event_id": 1, "snapshot_label": 1, "canonical_key": 1,
         "side": 1, "bookmaker": 1,
         "player": 1, "stat_family": 1, "line": 1, "ref_odds": 1,
         "vk2_projection": 1, "vk2_p_over": 1,
         "p_model": 1, "p_true_active": 1, "edge_vs_fair": 1,
         "feature_set": 1,
         "vision_score_v2": 1, "tier_reason": 1,
         "usage_vacuum_factor": 1, "usage_spike": 1,
         "matchup_strength": 1, "matchup_pace_factor": 1,
         "rotation_compression": 1, "key_player_out_flag": 1,
         "tp_books_used": 1, "tp_source": 1,
         "feature_completeness": 1,
         "matchup_feature_completeness": 1,
         "injury_feature_completeness": 1,
         "odds_american": 1},
    ).sort("vision_score_v2", -1).limit(top_n)
    return [d async for d in cursor]


async def _rerun_scoring_for_near_miss(
    db, *, near: Dict[str, Any],
) -> Dict[str, Any]:
    """Reload the cache row for a near-miss + run compute_scoring_stack
    so we can see EVERY gate's actual/threshold/passed and the full
    vision_v2 component breakdown."""
    cache_row = await db.replay_vk2_cache.find_one(
        {"event_id":      near["event_id"],
         "snapshot_label": near["snapshot_label"],
         "canonical_key":  near["canonical_key"],
         "side":           near["side"]},
    )
    if cache_row is None:
        return {"error": "cache_row_missing"}

    prop = _rebuild_prop(cache_row, near["side"])
    fs = cache_row.get("feature_set") or {}
    vk2 = cache_row.get("vk2_blob") or {}

    # Re-run the scoring stack to capture gate details.
    p_model = vk2.get("p_over")
    if p_model is not None and (near["side"] or "").upper() == "UNDER":
        p_model = max(0.0, min(1.0, 1.0 - float(p_model)))

    tp_blob = compute_tp(prop=prop, side=near["side"]) or {}
    p_true = tp_blob.get("tp")
    edge_pct = near.get("edge_vs_fair")

    hr_for_stack = (fs["hit_rate_l20"] * 100.0
                    if fs.get("hit_rate_l20") is not None else None)
    layers = cache_row.get("by_book_layers") or {}

    scored = compute_scoring_stack(
        prop=prop,
        p_model=p_model,
        cv=fs.get("cv"),
        hit_rate=hr_for_stack,
        edge_pct=edge_pct,
        tp=p_true,
        ceiling_rate=fs.get("ceiling_rate"),
        books_available_count=len(layers),
        sport="nba",
    )

    # vision_v2 component decomposition.
    side_norm = (near["side"] or "").upper()
    sigma = (vk2.get("sigma") if vk2.get("sigma") is not None
             else fs.get("sigma"))
    projection = (vk2.get("projection") if vk2.get("projection") is not None
                  else fs.get("mu"))
    line = near.get("line")

    components: Dict[str, Any] = {
        "edge_component":
            _edge_component(edge_pct),
        "consistency_component":
            _consistency_component(fs.get("cv"), hr_for_stack),
        "context_component":
            _context_component(
                injury_context={"usage_vacuum_factor":
                                 prop.get("usage_vacuum_factor")},
                usage_spike=prop.get("usage_spike"),
                matchup_strength=prop.get("matchup_strength"),
                pace_factor=prop.get("pace_factor"),
                side=side_norm),
        "market_confidence_component":
            _market_confidence_component(
                books_count=len(layers),
                tp_books_used=tp_blob.get("books_used"),
                tp_source=tp_blob.get("tp_source")),
    }

    return {
        "near_miss":      near,
        "tier":           scored.get("tier"),
        "tier_reason":    scored.get("tier_reason"),
        "vision_score_v2": scored.get("vision_score_v2"),
        "vision_score":    scored.get("vision_score"),
        "vision_score_raw": scored.get("vision_score_raw"),
        "v2_components":   components,
        "tier_gate_results": scored.get("tier_gate_results"),
        "gate_eval":       scored.get("gate_eval"),
        "tp_blob":         tp_blob,
        "prop_inputs": {
            "side":               near["side"],
            "line":               line,
            "ref_odds":           near.get("ref_odds"),
            "vk2_projection":     projection,
            "vk2_sigma":          sigma,
            "p_model":            p_model,
            "p_true":             p_true,
            "edge_pct":           edge_pct,
            "cv":                 fs.get("cv"),
            "hit_rate_l20":       hr_for_stack,
            "ceiling_rate":       fs.get("ceiling_rate"),
            "books_available":    len(layers),
            "tp_source":          tp_blob.get("tp_source"),
            "tp_books_used":      tp_blob.get("books_used"),
            "usage_vacuum_factor": prop.get("usage_vacuum_factor"),
            "usage_spike":         prop.get("usage_spike"),
            "matchup_strength":    prop.get("matchup_strength"),
            "pace_factor":         prop.get("pace_factor"),
        },
    }


async def _pull_live_sh_samples(db, *, n: int) -> List[Dict[str, Any]]:
    """Pull live production SH-tier samples covering all three vision
    states: (a) vision_score populated (v1 percentile), (b) vision_score
    None (deferred, insufficient_market), (c) any with non-zero v2."""
    out: List[Dict[str, Any]] = []
    cursor = db.nba_prop_scores.find(
        {"tier": "safe_haven", "vision_score": {"$ne": None, "$gte": 80}},
        sort=[("_id", -1)],
    ).limit(n // 2 if n > 1 else 1)
    async for d in cursor:
        d.pop("_id", None)
        out.append({"vision_class": "v1_percentile_>=80", "doc": d})
    cursor = db.nba_prop_scores.find(
        {"tier": "safe_haven", "vision_score": None},
        sort=[("_id", -1)],
    ).limit(max(1, n - len(out)))
    async for d in cursor:
        d.pop("_id", None)
        out.append({"vision_class": "vision_score_None_deferred", "doc": d})
    return out


def _summarize_live_sh(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    fields = ("player", "stat_type", "line", "recommendation",
              "reference_odds", "vision_score", "vision_score_raw",
              "vision_score_v2", "quality_source", "tier",
              "tier_reason", "hit_rate_l20", "cv", "edge_vs_fair",
              "p_true_active", "tp_source", "is_alternate",
              "usage_vacuum_factor", "usage_spike",
              "matchup_strength", "pace_factor")
    out: List[Dict[str, Any]] = []
    for s in samples:
        d = s["doc"]
        gate_eval = d.get("gate_eval") or {}
        gd = gate_eval.get("gate_details") or {}
        gates_summary = {
            k: {"passed": v.get("passed"),
                "actual": v.get("actual"),
                "threshold": v.get("threshold"),
                "note": v.get("note")}
            for k, v in gd.items()
        }
        out.append({
            "vision_class": s["vision_class"],
            "fields": {k: d.get(k) for k in fields},
            "gates": gates_summary,
        })
    return {"samples": out}


async def _v2_distribution(db, *, scope: str, query: Dict[str, Any]
                            ) -> Dict[str, Any]:
    """Distribution of vision_score_v2 + vision_score across a query."""
    coll = db.nba_prop_scores if scope == "live" else db.replay_evaluations
    n = await coll.count_documents(query)
    pipe = [
        {"$match": {**query, "vision_score_v2": {"$ne": None}}},
        {"$bucket": {"groupBy": "$vision_score_v2",
                     "boundaries": [0, 20, 30, 40, 50, 60, 70, 80, 90, 101],
                     "default": "other",
                     "output": {"n": {"$sum": 1}}}},
    ]
    v2_buckets = []
    async for r in coll.aggregate(pipe):
        v2_buckets.append({"bucket": r["_id"], "n": r["n"]})

    pipe2 = [
        {"$match": {**query, "vision_score": {"$ne": None}}},
        {"$bucket": {"groupBy": "$vision_score",
                     "boundaries": [0, 20, 40, 60, 70, 80, 90, 101],
                     "default": "other",
                     "output": {"n": {"$sum": 1}}}},
    ]
    vs_buckets = []
    async for r in coll.aggregate(pipe2):
        vs_buckets.append({"bucket": r["_id"], "n": r["n"]})

    return {"total": n, "v2_buckets": v2_buckets, "vs_buckets": vs_buckets}


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--canary-run-id", default="sh_canary_c_1778342681")
    p.add_argument("--top-n", type=int, default=5)
    p.add_argument("--out", default="/app/audit_reports/vision_walkthrough")
    args = p.parse_args()

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    # 1) Pull replay near-misses + rerun scoring with full gate detail.
    near_misses = await _pull_replay_sh_near_misses(
        db, run_id=args.canary_run_id, top_n=args.top_n)
    near_results: List[Dict[str, Any]] = []
    for nm in near_misses:
        res = await _rerun_scoring_for_near_miss(db, near=nm)
        near_results.append(res)

    # 2) Live SH samples.
    live_samples = await _pull_live_sh_samples(db, n=4)
    live_summary = _summarize_live_sh(live_samples)

    # 3) Distribution comparisons.
    live_dist = await _v2_distribution(
        db, scope="live", query={"tier": "safe_haven"})
    replay_dist_sh_attempts = await _v2_distribution(
        db, scope="replay",
        query={"replay_run_id": args.canary_run_id,
               "tier_reason": {"$regex": "^safe_haven_failed"}})

    # 4) Live SH `vision_score` source breakdown.
    live_sh_vs_states = {
        "vision_score_None":
            await db.nba_prop_scores.count_documents(
                {"tier": "safe_haven", "vision_score": None}),
        "vision_score_lt_80":
            await db.nba_prop_scores.count_documents(
                {"tier": "safe_haven",
                 "vision_score": {"$ne": None, "$lt": 80}}),
        "vision_score_gte_80":
            await db.nba_prop_scores.count_documents(
                {"tier": "safe_haven",
                 "vision_score": {"$ne": None, "$gte": 80}}),
        "v2_promoted_into_vs":
            await db.nba_prop_scores.count_documents(
                {"tier": "safe_haven",
                 "vision_score_raw": {"$lte": 0},
                 "vision_score": {"$ne": None}}),
        "vision_score_raw_positive":
            await db.nba_prop_scores.count_documents(
                {"tier": "safe_haven",
                 "vision_score_raw": {"$gt": 0}}),
        "quality_source_insufficient":
            await db.nba_prop_scores.count_documents(
                {"tier": "safe_haven",
                 "quality_source": "insufficient_market"}),
        "total":
            await db.nba_prop_scores.count_documents({"tier": "safe_haven"}),
    }

    out_obj = {
        "generated_utc":           datetime.now(timezone.utc).isoformat(),
        "canary_run_id":           args.canary_run_id,
        "live_sh_vision_states":   live_sh_vs_states,
        "live_sh_v2_distribution": live_dist,
        "replay_sh_attempt_distribution": replay_dist_sh_attempts,
        "live_sh_samples":         live_summary,
        "replay_near_misses":      near_results,
    }

    md_lines: List[str] = [
        "# Vision Walkthrough — Live SH vs Replay SH near-miss\n",
        f"_Generated_: {out_obj['generated_utc']}\n\n",
        f"_Canary_: `{args.canary_run_id}`\n\n",
        "## Live SH vision-state breakdown\n",
        "```json\n", _safe_dump(live_sh_vs_states), "\n```\n\n",
        "## Live SH `vision_score_v2` distribution\n",
        "```json\n", _safe_dump(live_dist), "\n```\n\n",
        "## Replay SH-attempt `vision_score_v2` distribution\n",
        "```json\n", _safe_dump(replay_dist_sh_attempts), "\n```\n\n",
        "## Live SH samples (full gate_details)\n",
        "```json\n", _safe_dump(live_summary), "\n```\n\n",
        f"## Replay SH-routed top-{args.top_n} near-misses (full re-eval)\n",
    ]
    for r in near_results:
        nm = r.get("near_miss") or {}
        md_lines.append(
            f"\n### `{nm.get('player','')}` "
            f"{nm.get('stat_family','')} {nm.get('line','')} "
            f"{nm.get('side','')} @ {nm.get('ref_odds')}\n",
        )
        md_lines.append("```json\n")
        md_lines.append(_safe_dump({
            "tier":             r.get("tier"),
            "tier_reason":      r.get("tier_reason"),
            "vision_score_v2":  r.get("vision_score_v2"),
            "vision_score":     r.get("vision_score"),
            "vision_score_raw": r.get("vision_score_raw"),
            "v2_components":    r.get("v2_components"),
            "prop_inputs":      r.get("prop_inputs"),
            "tier_gate_results": r.get("tier_gate_results"),
        }))
        md_lines.append("\n```\n")

    Path(args.out + ".md").write_text("".join(md_lines))
    Path(args.out + ".json").write_text(_safe_dump(out_obj))
    print(f"wrote {args.out}.md + {args.out}.json")
    cli.close()


if __name__ == "__main__":
    asyncio.run(main())
