"""
run_sgo_production_replay.py — drive the EXISTING production replay
pipeline (`services.replay.production_replay_runner.run_production_replay`)
against the SGO-derived odds collection.

The only difference from a live replay is the odds source. Everything
else — feature reconstruction, MLB-HF scoring, tier gate evaluation,
card builder, outcome grading — uses the production code path unchanged.

Steps the runner already does (we don't touch any of it):
    1.  read odds from adapter.config.odds_collection
    2.  reconstruct features via mlb_feature_cache
    3.  score via mlb_replay_engine (the live scoring stack)
    4.  apply gate engine (SH/FL/WZ via mlb_replay_gate_eval +
        evaluate_tier_with_overrides on canonical mode)
    5.  build production cards
    6.  persist runs, outputs, cards into a configurable namespace
    7.  return summary

We do TWO things here:
    a) point the adapter's odds_collection at `sgo_replay_alt_odds_raw`
    b) set output_namespace="sgo_replay" so SGO outputs are written to
       `mlb_sgo_replay_runs / outputs / cards` (segregated from live runs)

Usage (Admin API job runner):
    --start=2025-06-01 --end=2025-06-30 --tier=war_zone
    [--tier safe_haven | front_lines | war_zone]
    [--gate-path legacy_wz | universal]
    [--canonical-path]
    [--limit-dates N]   (for smoke runs)
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta, date
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path); break

from motor.motor_asyncio import AsyncIOMotorClient


SGO_ODDS_COLL = "sgo_replay_alt_odds_raw"
SGO_OUTPUT_NS = "sgo_replay"     # → mlb_sgo_replay_{runs,outputs,cards}


def _date_iter(start: str, end: str):
    s = date.fromisoformat(start); e = date.fromisoformat(end)
    cur = s
    while cur <= e:
        yield cur.isoformat()
        cur += timedelta(days=1)


async def _run(args: argparse.Namespace) -> int:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    # Lazy import — pulls in psutil/etc. which prod has.
    from services.replay.production_replay_runner import run_production_replay
    from services.replay.providers import MLBReplayAdapter

    # ── Point the adapter at the SGO odds collection ───────────────
    # MLBReplayAdapter.config.odds_collection is set at __init__ time
    # to "mlb_historical_alt_odds_raw". We override here so the runner
    # (which calls adapter.config.odds_collection) reads SGO rows.
    _orig_init = MLBReplayAdapter.__init__

    def _patched_init(self):
        _orig_init(self)
        self.config.odds_collection = SGO_ODDS_COLL  # type: ignore[attr-defined]

    MLBReplayAdapter.__init__ = _patched_init  # type: ignore[method-assign]

    dates = list(_date_iter(args.start, args.end))
    if args.limit_dates:
        dates = dates[: int(args.limit_dates)]

    print("=" * 76)
    print(f"  SGO PRODUCTION REPLAY  (using live replay pipeline)")
    print(f"  window      : {args.start}..{args.end}  ({len(dates)} dates)")
    print(f"  tier        : {args.tier}")
    print(f"  gate_path   : {args.gate_path}")
    print(f"  canonical   : {args.canonical_path}")
    print(f"  odds source : {SGO_ODDS_COLL}")
    print(f"  output ns   : {SGO_OUTPUT_NS}  → mlb_{SGO_OUTPUT_NS}_runs/outputs/cards")
    print("=" * 76)

    per_date_summaries: List[Dict[str, Any]] = []
    grand = {"rows_scanned": 0, "rows_qualified": 0,
              "wins": 0, "losses": 0, "pushes": 0,
              "profit_units": 0.0, "dates_run": 0,
              "dates_with_qualified": 0}

    for gd in dates:
        snapshot_iso = f"{gd}T11:00:00Z"
        try:
            summary = await run_production_replay(
                db, sport="mlb",
                game_date=gd,
                snapshot_iso=snapshot_iso,
                tier=args.tier,
                gate_path=args.gate_path,
                canonical_path=args.canonical_path,
                output_namespace=SGO_OUTPUT_NS,
                dry_run=args.dry_run,
                notes=f"sgo-replay v1 {args.start}..{args.end}",
            )
        except Exception as e:
            print(f"  [{gd}] FAILED: {e!r}")
            per_date_summaries.append({"game_date": gd, "error": repr(e)})
            continue

        per_date_summaries.append({"game_date": gd, **summary})
        grand["dates_run"]      += 1
        grand["rows_scanned"]   += summary.get("rows_scanned", 0)
        grand["rows_qualified"] += summary.get("rows_qualified", 0)
        grand["wins"]           += summary.get("wins", 0)
        grand["losses"]         += summary.get("losses", 0)
        grand["pushes"]         += summary.get("pushes", 0)
        grand["profit_units"]   += float(summary.get("profit_units", 0.0))
        if summary.get("rows_qualified", 0) > 0:
            grand["dates_with_qualified"] += 1

        print(f"  [{gd}]  serial={summary.get('serial')}  "
                f"scanned={summary.get('rows_scanned')}  "
                f"qualified={summary.get('rows_qualified')}  "
                f"wins={summary.get('wins')}  losses={summary.get('losses')}  "
                f"roi={summary.get('roi_pct')}%")

    # ── Grand summary ────────────────────────────────────────────
    n_dec = max(grand["wins"] + grand["losses"], 1)
    hit_pct = 100.0 * grand["wins"] / n_dec if (grand["wins"] + grand["losses"]) else None
    roi_pct = (100.0 * grand["profit_units"] / grand["rows_qualified"]
                 if grand["rows_qualified"] else None)
    print()
    print("=" * 76)
    print(f"  GRAND TOTAL — {args.tier} — {grand['dates_run']} dates")
    print(f"    rows_scanned     {grand['rows_scanned']:>8}")
    print(f"    rows_qualified   {grand['rows_qualified']:>8}")
    print(f"    wins / losses    {grand['wins']:>5} / {grand['losses']:<5}"
            f"   pushes {grand['pushes']}")
    print(f"    hit_rate         {hit_pct if hit_pct is None else f'{hit_pct:>7.2f}%'}")
    print(f"    profit_units     {grand['profit_units']:>+8.2f}")
    print(f"    roi              {roi_pct if roi_pct is None else f'{roi_pct:>+7.2f}%'}")
    print(f"    dates_with_qual  {grand['dates_with_qualified']}/{grand['dates_run']}")
    print("=" * 76)
    return 0


def _parse():
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end",   required=True)
    p.add_argument("--tier",  default="war_zone",
                     choices=("safe_haven", "front_lines", "war_zone"))
    p.add_argument("--gate-path", default="legacy_wz",
                     choices=("legacy_wz", "universal"))
    p.add_argument("--canonical-path", action="store_true")
    p.add_argument("--limit-dates", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(_run(_parse())))
