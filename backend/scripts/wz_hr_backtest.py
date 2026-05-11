#!/usr/bin/env python3
"""
WZ HR-floor A/B backtest harness — 2026-05-10.

Runs Stage-C scoring twice against the existing `replay_vk2_cache`,
varying ONLY `_NBA_WAR_ZONE_BASE["hit_rate_gate"]["min"]`:

    Run A (baseline): HR_min = 50.0  (current production)
    Run B (test):     HR_min = 35.0

Both runs are settled against `replay_results` and compared on:
    - WZ qualified count
    - WZ settled rows
    - WZ hit rate
    - WZ ROI per unit
    - WZ rejection-reason mix

Foreground-only — every chunk completes before the next starts, so a
pod recycle won't lose progress.

Usage:
    python wz_hr_backtest.py --hr-baseline 50 --hr-test 35
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from dotenv import load_dotenv
load_dotenv(str(REPO_ROOT / "backend/.env"))

from motor.motor_asyncio import AsyncIOMotorClient   # noqa: E402
from pymongo import UpdateOne                         # noqa: E402

# Import the threshold module so we can mutate it in-process.
from services.scoring.gates import thresholds as _thresholds  # noqa: E402
from services.replay.scoring_only import run_scoring_only     # noqa: E402
from services.replay.resolver import (                        # noqa: E402
    REPLAY_OUTCOMES, REPLAY_EVALUATIONS, REPLAY_RESULTS,
    build_outcome_row, ensure_outcome_indexes,
)


def _norm_name(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _apply_hr_override(hr_min: float) -> float:
    """Mutate `_NBA_WAR_ZONE_BASE` in place, return prior value."""
    prior = _thresholds._NBA_WAR_ZONE_BASE["hit_rate_gate"]["min"]
    _thresholds._NBA_WAR_ZONE_BASE["hit_rate_gate"]["min"] = float(hr_min)
    # The THRESHOLDS table holds a reference, not a copy — confirm it.
    assert (
        _thresholds.THRESHOLDS["nba"]["war_zone"]["_default"]
            ["hit_rate_gate"]["min"] == float(hr_min)
    ), "threshold mutation did NOT propagate to THRESHOLDS table"
    return prior


async def _settle_run(db, run_id: str) -> Dict[str, Any]:
    """Settle every evaluation under `run_id` against replay_results.
    Identical contract to scripts/run_outcome_resolver.py — inlined so
    the backtest stays single-process and we can keep progress active.
    """
    await ensure_outcome_indexes(db)
    started = datetime.now(timezone.utc)
    bulk: List[UpdateOne] = []
    counts = {"total": 0, "hit": 0, "miss": 0, "push": 0, "void_dnp": 0,
               "ins": 0, "mod": 0}

    cursor = db[REPLAY_EVALUATIONS].find({"replay_run_id": run_id})
    async for ev in cursor:
        counts["total"] += 1
        result = await db[REPLAY_RESULTS].find_one({
            "event_id":    ev["event_id"],
            "player_norm": _norm_name(ev.get("player")),
        })
        row = build_outcome_row(evaluation=ev, result=result)
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1
        flt = {
            "replay_run_id": row["replay_run_id"],
            "canonical_key": row["canonical_key"],
            "snapshot_label": row["snapshot_label"],
            "bookmaker":     row["bookmaker"],
            "side":          row["side"],
        }
        bulk.append(UpdateOne(
            flt,
            {"$set": row,
             "$setOnInsert": {"_first_seen": row["resolved_at"]}},
            upsert=True,
        ))
        if len(bulk) >= 500:
            res = await db[REPLAY_OUTCOMES].bulk_write(bulk, ordered=False)
            counts["ins"] += res.upserted_count or 0
            counts["mod"] += res.modified_count or 0
            bulk.clear()
        if counts["total"] % 50_000 == 0:
            print(f"  [settle] {counts['total']:,} evals "
                  f"hit={counts['hit']:,} miss={counts['miss']:,} "
                  f"void={counts['void_dnp']:,} push={counts['push']:,}")
    if bulk:
        res = await db[REPLAY_OUTCOMES].bulk_write(bulk, ordered=False)
        counts["ins"] += res.upserted_count or 0
        counts["mod"] += res.modified_count or 0
    finished = datetime.now(timezone.utc)
    counts["wallclock_seconds"] = round(
        (finished - started).total_seconds(), 1)
    return counts


async def _wz_summary(db, run_id: str) -> Dict[str, Any]:
    """Aggregate WZ-only metrics for one run."""
    out: Dict[str, Any] = {"run_id": run_id}

    # WZ tier breakdown from evaluations
    tier_pipeline = [
        {"$match": {"replay_run_id": run_id}},
        {"$group": {"_id": "$tier", "n": {"$sum": 1}}},
    ]
    tier_dist: Dict[str, int] = {}
    async for d in db[REPLAY_EVALUATIONS].aggregate(tier_pipeline):
        tier_dist[d["_id"] or "unknown"] = d["n"]
    out["tier_distribution"] = tier_dist
    out["wz_qualified_evals"] = tier_dist.get("war_zone", 0)

    # WZ rejection reasons (routed=WZ but tier=unqualified)
    rej_pipeline = [
        {"$match": {
            "replay_run_id": run_id, "tier": "unqualified",
            "tier_reason": {"$regex": "^war_zone_failed"},
        }},
        {"$group": {"_id": "$tier_reason", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
        {"$limit": 12},
    ]
    rej: List[Dict[str, Any]] = []
    async for d in db[REPLAY_EVALUATIONS].aggregate(rej_pipeline):
        rej.append({"reason": d["_id"], "n": d["n"]})
    out["wz_rejection_top"] = rej

    # WZ settled outcomes + ROI from replay_outcomes
    out_pipeline = [
        {"$match": {
            "replay_run_id": run_id,
            "tier_at_eval": "war_zone",
        }},
        {"$group": {
            "_id": None,
            "n":       {"$sum": 1},
            "hits":    {"$sum": {"$cond": [{"$eq": ["$outcome", "hit"]}, 1, 0]}},
            "miss":    {"$sum": {"$cond": [{"$eq": ["$outcome", "miss"]}, 1, 0]}},
            "void":    {"$sum": {"$cond": [{"$eq": ["$outcome", "void_dnp"]}, 1, 0]}},
            "push":    {"$sum": {"$cond": [{"$eq": ["$outcome", "push"]}, 1, 0]}},
            "pnl":     {"$sum": "$pnl_units"},
        }},
    ]
    settled = None
    async for d in db[REPLAY_OUTCOMES].aggregate(out_pipeline):
        settled = d
    if settled:
        decided = settled["hits"] + settled["miss"]
        out["wz_settled"] = {
            "n_eval_rows":   settled["n"],
            "hits":          settled["hits"],
            "miss":          settled["miss"],
            "void_dnp":      settled["void"],
            "push":          settled["push"],
            "hit_rate":      (round(settled["hits"] / decided * 100, 2)
                              if decided else None),
            "pnl_units":     round(settled["pnl"], 2),
            "roi_per_unit":  (round(settled["pnl"] / decided * 100, 2)
                              if decided else None),
        }
    else:
        out["wz_settled"] = None

    # Distinct WZ canonical picks (de-duped across books, snapshots)
    # so the "pick count" line matches a Ferrari-tier view rather than
    # the underlying eval-row count.
    pick_pipe = [
        {"$match": {
            "replay_run_id": run_id,
            "tier_at_eval": "war_zone",
            "outcome": {"$in": ["hit", "miss"]},
        }},
        {"$group": {
            "_id": {"event_id": "$event_id", "canonical_key": "$canonical_key"},
            "outcome_first": {"$first": "$outcome"},
            "pnl_first":     {"$first": "$pnl_units"},
        }},
        {"$group": {
            "_id": None,
            "n":     {"$sum": 1},
            "hits":  {"$sum": {"$cond": [{"$eq": ["$outcome_first", "hit"]}, 1, 0]}},
            "pnl":   {"$sum": "$pnl_first"},
        }},
    ]
    picks = None
    async for d in db[REPLAY_OUTCOMES].aggregate(pick_pipe):
        picks = d
    if picks and picks["n"]:
        decided = picks["n"]
        out["wz_distinct_picks"] = {
            "n":            picks["n"],
            "hits":         picks["hits"],
            "miss":         decided - picks["hits"],
            "hit_rate":     round(picks["hits"] / decided * 100, 2),
            "pnl_units":    round(picks["pnl"], 2),
            "roi_per_unit": round(picks["pnl"] / decided * 100, 2),
        }
    else:
        out["wz_distinct_picks"] = None
    return out


async def _run_variant(
    db, *, run_id: str, hr_min: float,
) -> Dict[str, Any]:
    print(f"\n{'=' * 78}")
    print(f"VARIANT  run_id={run_id}  hit_rate_gate.min={hr_min}")
    print('=' * 78)
    prior_hr = _apply_hr_override(hr_min)
    try:
        # Clean prior run if it exists (idempotent).
        await db[REPLAY_EVALUATIONS].delete_many({"replay_run_id": run_id})
        await db[REPLAY_OUTCOMES].delete_many({"replay_run_id": run_id})

        # Stage-C scoring.
        t0 = time.monotonic()
        score = await run_scoring_only(
            db, replay_run_id=run_id,
            sport_short="nba", recompute_tp=True,
            log_fn=print, chunk_size=500,
        )
        t_score = time.monotonic() - t0
        print(f"  [score] done in {t_score:.1f}s  counters={score['counters']}")

        # Outcome resolution.
        t1 = time.monotonic()
        settle = await _settle_run(db, run_id)
        t_settle = time.monotonic() - t1
        print(f"  [settle] done in {t_settle:.1f}s  counts={settle}")

        # WZ summary.
        summary = await _wz_summary(db, run_id)
        summary["hr_min"] = hr_min
        summary["score_counters"] = score["counters"]
        summary["score_seconds"] = round(t_score, 1)
        summary["settle_counts"] = settle
        return summary
    finally:
        # Restore prior threshold for safety.
        _thresholds._NBA_WAR_ZONE_BASE["hit_rate_gate"]["min"] = prior_hr


def _fmt(v):
    if v is None: return "—"
    return v


def _print_compare(a: Dict[str, Any], b: Dict[str, Any]) -> None:
    print(f"\n{'=' * 78}")
    print(f"COMPARISON  baseline HR={a['hr_min']}  vs  test HR={b['hr_min']}")
    print('=' * 78)
    a_td = a.get("tier_distribution") or {}
    b_td = b.get("tier_distribution") or {}
    all_tiers = sorted(set(a_td.keys()) | set(b_td.keys()))
    print(f"\n  Tier distribution (evaluation rows):")
    print(f"    {'tier':<14} {'baseline':>12} {'test':>12} {'Δ':>10}")
    for t in all_tiers:
        av = a_td.get(t, 0); bv = b_td.get(t, 0)
        print(f"    {t:<14} {av:>12,} {bv:>12,} {bv - av:>+10,}")

    print(f"\n  WZ qualified eval rows: "
          f"baseline={a['wz_qualified_evals']:,}  "
          f"test={b['wz_qualified_evals']:,}  "
          f"Δ={b['wz_qualified_evals'] - a['wz_qualified_evals']:+,}")

    print(f"\n  WZ DISTINCT PICKS (de-duped per event×canonical):")
    a_p = a.get("wz_distinct_picks") or {}
    b_p = b.get("wz_distinct_picks") or {}
    if a_p or b_p:
        labels = ["n", "hits", "miss", "hit_rate", "pnl_units", "roi_per_unit"]
        units  = {"hit_rate": "%", "roi_per_unit": "%", "pnl_units": "u"}
        print(f"    {'metric':<14} {'baseline':>14} {'test':>14} {'Δ':>12}")
        for k in labels:
            av = a_p.get(k); bv = b_p.get(k)
            u = units.get(k, "")
            d = (bv - av) if (av is not None and bv is not None) else None
            avd = f"{av}{u}" if av is not None else "—"
            bvd = f"{bv}{u}" if bv is not None else "—"
            dd = f"{d:+}{u}" if d is not None else "—"
            print(f"    {k:<14} {avd:>14} {bvd:>14} {dd:>12}")
    else:
        print("    (no settled WZ picks in either run)")

    print(f"\n  WZ TOP REJECTION REASONS:")
    print(f"    {'reason':<55} {'baseline':>10} {'test':>10}")
    a_r = {r["reason"]: r["n"] for r in (a.get("wz_rejection_top") or [])}
    b_r = {r["reason"]: r["n"] for r in (b.get("wz_rejection_top") or [])}
    for reason in sorted(set(a_r) | set(b_r), key=lambda x: -max(a_r.get(x,0), b_r.get(x,0))):
        print(f"    {reason:<55} {a_r.get(reason,0):>10,} {b_r.get(reason,0):>10,}")


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--hr-baseline", type=float, default=50.0)
    p.add_argument("--hr-test",     type=float, default=35.0)
    p.add_argument("--run-prefix",  default=None,
                    help="Prefix for replay_run_id; default uses timestamp.")
    p.add_argument("--summary-out", default=None)
    args = p.parse_args()

    prefix = args.run_prefix or (
        "wz_hr_ab_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    )

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    started = time.monotonic()
    baseline = await _run_variant(
        db, run_id=f"{prefix}_hr{int(args.hr_baseline)}",
        hr_min=args.hr_baseline,
    )
    test = await _run_variant(
        db, run_id=f"{prefix}_hr{int(args.hr_test)}",
        hr_min=args.hr_test,
    )
    elapsed = time.monotonic() - started

    _print_compare(baseline, test)
    print(f"\nTotal wallclock: {elapsed:.1f}s")

    if args.summary_out:
        Path(args.summary_out).write_text(json.dumps({
            "prefix": prefix,
            "baseline": baseline,
            "test":     test,
            "elapsed_seconds": round(elapsed, 1),
        }, indent=2, default=str))
        print(f"Summary written to {args.summary_out}")

    cli.close()


if __name__ == "__main__":
    asyncio.run(main())
