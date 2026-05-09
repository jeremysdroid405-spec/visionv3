#!/usr/bin/env python3
"""Full-window replay driver — fires `run_replay_engine` over the
configured range, writes results to /app/audit_reports/, and exits.

Designed to be launched in the background (via `nohup ... &`) since
the engine pass over the 30-day NBA window is ~25-35 minutes.

Side-effects: writes ONLY to `replay_*` collections.
NEVER calls the Odds API. Never mutates production collections.

Outputs:
  /app/audit_reports/full_replay_<run_id>.summary.json
  /app/audit_reports/full_replay_<run_id>.sh_report.json
  /app/audit_reports/full_replay_<run_id>.sh_report.md
  /app/audit_reports/full_replay_<run_id>.fail_dist.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(str(REPO_ROOT / "backend/.env"))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from services.replay.engine import run_replay_engine  # noqa: E402


async def _post_run_aggregates(db, *, run_id: str) -> dict:
    EVALS = "replay_evaluations"

    # Tier dist.
    tier = {}
    async for r in db[EVALS].aggregate([
        {"$match": {"replay_run_id": run_id}},
        {"$group": {"_id": "$tier", "n": {"$sum": 1}}},
    ]):
        tier[r["_id"] or "<none>"] = r["n"]

    # Top fail reasons.
    fails = {}
    async for r in db[EVALS].aggregate([
        {"$match": {"replay_run_id": run_id,
                     "tier_reason": {"$regex": "_failed:"}}},
        {"$group": {"_id": "$tier_reason", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}}, {"$limit": 30},
    ]):
        fails[r["_id"]] = r["n"]

    # SH-by-family.
    sh_fam = {}
    async for r in db[EVALS].aggregate([
        {"$match": {"replay_run_id": run_id, "tier": "safe_haven"}},
        {"$group": {"_id": "$stat_family", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]):
        sh_fam[r["_id"]] = r["n"]

    # SH outcomes (join via canonical_key + side).
    sh_pairs = []
    async for x in db[EVALS].find(
        {"replay_run_id": run_id, "tier": "safe_haven"},
        {"_id": 0, "canonical_key": 1, "side": 1},
    ):
        sh_pairs.append((x.get("canonical_key"), x.get("side")))
    unique_pairs = list({p for p in sh_pairs if all(p)})

    hits = misses = voids = 0
    pnl = 0.0
    for k, side in unique_pairs:
        oc = await db.replay_outcomes.find_one(
            {"canonical_key": k, "side": side})
        if not oc:
            continue
        o = oc.get("outcome") or oc.get("result")
        if o == "hit":
            hits += 1
        elif o == "miss":
            misses += 1
        else:
            voids += 1
        pnl += float(oc.get("pnl_units") or 0)

    settled = hits + misses
    return {
        "tier":              tier,
        "top_fail_reasons":  fails,
        "sh_by_family":      sh_fam,
        "sh_unique_picks":   len(unique_pairs),
        "sh_settled":        settled,
        "sh_hits":           hits,
        "sh_misses":         misses,
        "sh_voids_pushes":   voids,
        "sh_hit_rate_pct":   (100.0 * hits / settled) if settled else None,
        "sh_pnl_units":      round(pnl, 2),
        "sh_roi_pct":        (100.0 * pnl / settled) if settled else None,
    }


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2024-02-01")
    p.add_argument("--end",   default="2024-03-01")
    p.add_argument("--snapshot-label", default="t-30m")
    p.add_argument("--run-id", default=None,
                    help="Optional run_id; default is full_replay_<ts>.")
    p.add_argument("--cache-outputs", default="false",
                    help="Write Stage-B cache rows (default false to "
                         "conserve disk for full-window runs).")
    p.add_argument("--out-dir", default="/app/audit_reports")
    args = p.parse_args()

    started = datetime.now(timezone.utc)
    run_id = args.run_id or f"full_replay_{int(started.timestamp())}"
    range_start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    range_end = datetime.fromisoformat(args.end).replace(
        hour=23, minute=59, tzinfo=timezone.utc)

    print(f"[full] starting {run_id}  range {args.start} → {args.end} "
          f"snapshot={args.snapshot_label}")
    print(f"[full] started_utc={started.isoformat()}")

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    cache_outputs = (args.cache_outputs or "").strip().lower() in (
        "1", "true", "yes")

    summary = await run_replay_engine(
        db,
        replay_run_id=run_id,
        range_start=range_start,
        range_end=range_end,
        snapshot_label=args.snapshot_label,
        sport_key="basketball_nba",
        sport_short="nba",
        enable_vk2=True,
        cache_outputs=cache_outputs,
    )

    finished = datetime.now(timezone.utc)
    summary["wallclock_minutes"] = round(
        (finished - started).total_seconds() / 60.0, 2)
    print(f"[full] engine done in "
          f"{summary['wallclock_minutes']} min — "
          f"counters={summary.get('counters', {})}")

    # Post-run aggregates.
    print(f"[full] computing post-run aggregates …")
    agg = await _post_run_aggregates(db, run_id=run_id)
    summary["post_run_aggregates"] = agg

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / f"full_replay_{run_id}.summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"[full] wrote {summary_path}")

    # SH report (markdown).
    md_lines = [
        f"# Full-window NBA replay — `{run_id}`\n",
        f"_Generated_: {finished.isoformat()}\n\n",
        f"- Window: {args.start} → {args.end}, snapshot={args.snapshot_label}\n",
        f"- Wallclock: **{summary['wallclock_minutes']} min**\n",
        f"- Engine counters: \n```json\n",
        json.dumps(summary.get("counters", {}), indent=2),
        "\n```\n\n",
        "## Tier distribution\n",
        "| tier | n |\n|---|---|\n",
    ]
    for t, n in agg["tier"].items():
        md_lines.append(f"| {t} | {n:,} |\n")
    md_lines.append("\n## Top fail reasons\n| reason | n |\n|---|---|\n")
    for r, n in agg["top_fail_reasons"].items():
        md_lines.append(f"| {r} | {n:,} |\n")
    md_lines.append("\n## SH by stat family\n| family | n |\n|---|---|\n")
    for f, n in agg["sh_by_family"].items():
        md_lines.append(f"| {f} | {n:,} |\n")
    md_lines.append(
        f"\n## SH performance\n"
        f"- Unique SH picks: **{agg['sh_unique_picks']:,}**\n"
        f"- Settled: **{agg['sh_settled']:,}** "
        f"(hits {agg['sh_hits']:,} / misses {agg['sh_misses']:,} / "
        f"voids/pushes {agg['sh_voids_pushes']:,})\n"
        f"- HR: **{agg['sh_hit_rate_pct']:.1f}%**" if agg["sh_settled"]
        else "- HR: n/a\n",
    )
    if agg["sh_settled"]:
        md_lines.append(
            f" · PnL: **{agg['sh_pnl_units']:+.2f}u** · "
            f"ROI: **{agg['sh_roi_pct']:+.2f}%/u**\n"
        )

    md_path = out_dir / f"full_replay_{run_id}.sh_report.md"
    md_path.write_text("".join(md_lines))
    print(f"[full] wrote {md_path}")

    # Compact SH report json too.
    (out_dir / f"full_replay_{run_id}.sh_report.json").write_text(
        json.dumps(agg, indent=2, default=str))

    print(f"[full] done.")
    cli.close()


if __name__ == "__main__":
    asyncio.run(main())
