#!/usr/bin/env python3
"""Chunked full-window replay — survives container restarts.

Processes ONE date's worth of events per invocation, persists
checkpoint to `replay_engine_progress`, and exits. Re-invoke
until all dates are complete.

Workflow:

  while replay_full_replay_chunked.py exits with code 0 (more work):
      run again
  done

Each invocation:
  • picks the next un-completed date in [start, end]
  • runs the engine with sample_event_ids = events on that date
  • writes a `replay_engine_progress` doc on success
  • exits 0 if more dates remain, exits 2 if all done

Outputs (final invocation):
  /app/audit_reports/full_replay_<run_id>.summary.json
  /app/audit_reports/full_replay_<run_id>.sh_report.json
  /app/audit_reports/full_replay_<run_id>.sh_report.md

Side-effects: writes ONLY to `replay_*` collections. Never calls
the Odds API. Never mutates production collections.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(str(REPO_ROOT / "backend/.env"))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from services.replay.engine import run_replay_engine  # noqa: E402

PROGRESS_COLL = "replay_engine_progress"


async def _events_for_date(db, *, sport_key: str, snapshot_label: str,
                           date_iso: str) -> List[str]:
    """Distinct event_ids for one calendar UTC date."""
    start = datetime.fromisoformat(date_iso).replace(tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return await db.replay_props_normalized.distinct(
        "event_id",
        {"sport_key": sport_key,
         "snapshot_label": snapshot_label,
         "commence_time": {"$gte": start, "$lt": end}},
    )


async def _date_list(start: datetime, end: datetime) -> List[str]:
    out: List[str] = []
    d = start.date()
    while d <= end.date():
        out.append(d.isoformat())
        d = d + timedelta(days=1)
    return out


async def _post_run_aggregates(db, *, run_id: str) -> Dict[str, Any]:
    EVALS = "replay_evaluations"
    tier: Dict[str, int] = {}
    async for r in db[EVALS].aggregate([
        {"$match": {"replay_run_id": run_id}},
        {"$group": {"_id": "$tier", "n": {"$sum": 1}}},
    ]):
        tier[r["_id"] or "<none>"] = r["n"]

    fails: Dict[str, int] = {}
    async for r in db[EVALS].aggregate([
        {"$match": {"replay_run_id": run_id,
                     "tier_reason": {"$regex": "_failed:"}}},
        {"$group": {"_id": "$tier_reason", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}}, {"$limit": 30},
    ]):
        fails[r["_id"]] = r["n"]

    sh_fam: Dict[str, int] = {}
    async for r in db[EVALS].aggregate([
        {"$match": {"replay_run_id": run_id, "tier": "safe_haven"}},
        {"$group": {"_id": "$stat_family", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]):
        sh_fam[r["_id"]] = r["n"]

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


async def _write_final_outputs(db, *, run_id: str, out_dir: Path) -> None:
    print(f"[chunk] all dates done — writing outputs for {run_id}")
    agg = await _post_run_aggregates(db, run_id=run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"full_replay_{run_id}.sh_report.json").write_text(
        json.dumps(agg, indent=2, default=str))

    md_lines = [
        f"# Full-window NBA replay — `{run_id}`\n",
        f"_Generated_: {datetime.now(timezone.utc).isoformat()}\n\n",
        "## Tier distribution\n", "| tier | n |\n|---|---|\n",
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
        f"- Settled: {agg['sh_settled']:,}  "
        f"(hits {agg['sh_hits']:,} / misses {agg['sh_misses']:,})\n",
    )
    if agg["sh_settled"]:
        md_lines.append(
            f"- HR: **{agg['sh_hit_rate_pct']:.1f}%** · "
            f"PnL: **{agg['sh_pnl_units']:+.2f}u** · "
            f"ROI: **{agg['sh_roi_pct']:+.2f}%/u**\n"
        )
    (out_dir / f"full_replay_{run_id}.sh_report.md").write_text(
        "".join(md_lines))


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2024-02-01")
    p.add_argument("--end",   default="2024-03-01")
    p.add_argument("--snapshot-label", default="t-30m")
    p.add_argument("--run-id", required=True,
                    help="Stable run_id used across chunked invocations.")
    p.add_argument("--max-dates-per-invocation", type=int, default=2,
                    help="Process up to this many dates per script run; "
                         "exit early so caller can restart in a fresh "
                         "container if needed.")
    p.add_argument("--out-dir", default="/app/audit_reports")
    args = p.parse_args()

    range_start = datetime.fromisoformat(args.start).replace(
        tzinfo=timezone.utc)
    range_end = datetime.fromisoformat(args.end).replace(
        hour=23, minute=59, tzinfo=timezone.utc)

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    all_dates = await _date_list(range_start, range_end)
    completed: List[str] = []
    progress = await db[PROGRESS_COLL].find_one(
        {"replay_run_id": args.run_id})
    if progress and isinstance(progress.get("completed_dates"), list):
        completed = list(progress["completed_dates"])

    todo = [d for d in all_dates if d not in completed]
    if not todo:
        print(f"[chunk] all {len(all_dates)} dates already complete")
        await _write_final_outputs(
            db, run_id=args.run_id,
            out_dir=Path(args.out_dir))
        cli.close()
        sys.exit(2)

    target = todo[: args.max_dates_per_invocation]
    print(f"[chunk] {len(completed)}/{len(all_dates)} done. "
          f"Processing next dates: {target}")

    for date_iso in target:
        events = await _events_for_date(
            db,
            sport_key="basketball_nba",
            snapshot_label=args.snapshot_label,
            date_iso=date_iso,
        )
        if not events:
            print(f"[chunk] {date_iso}: no events; marking complete")
            completed.append(date_iso)
            await db[PROGRESS_COLL].update_one(
                {"replay_run_id": args.run_id},
                {"$set": {"completed_dates": completed,
                          "updated_at": datetime.now(timezone.utc)}},
                upsert=True,
            )
            continue
        started = datetime.now(timezone.utc)
        print(f"[chunk] {date_iso}: {len(events)} events — engine start")
        try:
            summary = await run_replay_engine(
                db,
                replay_run_id=args.run_id,
                range_start=range_start,
                range_end=range_end,
                snapshot_label=args.snapshot_label,
                sport_key="basketball_nba",
                sport_short="nba",
                enable_vk2=True,
                cache_outputs=True,
                sample_event_ids=events,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[chunk] {date_iso}: ERROR {exc}")
            cli.close()
            sys.exit(3)
        finished = datetime.now(timezone.utc)
        wall_min = round((finished - started).total_seconds() / 60.0, 2)
        print(f"[chunk] {date_iso}: done in {wall_min} min  "
              f"counters={summary.get('counters', {})}")
        completed.append(date_iso)
        await db[PROGRESS_COLL].update_one(
            {"replay_run_id": args.run_id},
            {"$set": {"completed_dates":  completed,
                      "last_date_done":   date_iso,
                      "last_wallclock_minutes": wall_min,
                      "updated_at":       datetime.now(timezone.utc)}},
            upsert=True,
        )

    remaining = [d for d in all_dates if d not in completed]
    if remaining:
        print(f"[chunk] EXIT 0 — {len(completed)}/{len(all_dates)} done; "
              f"{len(remaining)} remaining")
        cli.close()
        sys.exit(0)
    else:
        print(f"[chunk] EXIT 2 — all dates done")
        await _write_final_outputs(
            db, run_id=args.run_id, out_dir=Path(args.out_dir))
        cli.close()
        sys.exit(2)


if __name__ == "__main__":
    asyncio.run(main())
