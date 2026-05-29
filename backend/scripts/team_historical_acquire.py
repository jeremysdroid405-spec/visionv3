"""
Phase 1.A.4.acquire — `team_historical_acquire` CLI.

Pull a UTC date window from SGO and persist to:
  - {team_matchups | nfl_matchups}
  - {team_historical_props | nfl_historical_props}

DRY-RUN by default. `--yes` required for live writes.

  python -m scripts.team_historical_acquire \\
      --sport nfl --start 2024-09-05 --end 2024-09-09 [--yes] [--json]

Exit codes:
    0 ok / dry-run preview
    2 bad args
    3 dispatch guard closed
    5 SGO failure
    9 Mongo error
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from typing import Any, Dict

from motor.motor_asyncio import AsyncIOMotorClient

from workers.team.historical_ingest import (
    SPORT_COLLECTIONS,
    acquire_historical_window,
)


EXIT_OK            = 0
EXIT_BAD_ARGS      = 2
EXIT_GUARD_CLOSED  = 3
EXIT_SGO_FAILURE   = 5
EXIT_MONGO_ERROR   = 9

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="team_historical_acquire",
        description=(
            "Walk a UTC date window via SGO and write historical "
            "matchups + odds props. DRY-RUN by default."
        ),
    )
    p.add_argument("--sport", required=True,
                    choices=list(SPORT_COLLECTIONS.keys()))
    p.add_argument("--start", required=True,
                    help="inclusive UTC start date YYYY-MM-DD")
    p.add_argument("--end",   required=True,
                    help="inclusive UTC end date YYYY-MM-DD")
    p.add_argument("--yes",  action="store_true",
                    help="actually write rows (default: dry-run)")
    p.add_argument("--json", action="store_true",
                    help="emit the audit row as JSON")
    p.add_argument("--markets", default="all",
                    help="comma-separated market_key allow-list. "
                         "Use 'all' (default) to acquire EVERY market.")
    return p


def _print_banner(args, dry_run: bool) -> None:
    print("─── team_historical_acquire ───")
    print(f"  sport     : {args.sport}")
    print(f"  window    : {args.start} → {args.end}")
    print(f"  markets   : {args.markets}")
    print(f"  mode      : {'DRY-RUN' if dry_run else 'LIVE WRITE'}")
    mc, hc = SPORT_COLLECTIONS[args.sport]
    print(f"  matchups  : {mc}")
    print(f"  hist props: {hc}")


def _print_summary(audit: Dict[str, Any]) -> None:
    print("\n─── audit ───")
    print(f"  run_id              : {audit['run_id']}")
    print(f"  status              : {audit['status']}")
    print(f"  diagnosis           : {audit['diagnosis']}")
    print(f"  duration_ms         : {audit.get('duration_ms')}")
    print(f"  n_dates             : {audit['n_dates']}")
    print(f"  n_sgo_pages         : {audit['n_sgo_pages']}")
    print(f"  n_sgo_events        : {audit['n_sgo_events']}")
    print(f"  n_matchups_written  : {audit['n_matchups_written']}")
    print(f"  n_props_normalized  : {audit['n_props_normalized']}")
    print(f"  n_props_written     : {audit['n_props_written']}")
    print(f"  n_props_upserted    : {audit['n_props_upserted']}")
    print(f"  n_props_modified    : {audit['n_props_modified']}")
    print(f"  n_blocked           : {audit['n_blocked']}")
    print(f"  n_refs              : {audit['n_refs']}")
    print(f"  n_unresolved        : {audit['n_unresolved']}")
    print(f"  market_keys_seen    : {len(audit.get('market_keys_seen') or [])}")
    pdc = audit.get('per_date_counts') or {}
    if pdc:
        print("\n─── per-date events ───")
        for d in sorted(pdc.keys()):
            print(f"  {d}  : {pdc[d]}")


async def _run(args) -> int:
    if not _DATE_RE.match(args.start) or not _DATE_RE.match(args.end):
        print(
            f"ERROR: dates must be YYYY-MM-DD (got {args.start!r}, "
            f"{args.end!r})",
            file=sys.stderr,
        )
        return EXIT_BAD_ARGS

    try:
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Mongo connect failed: {exc}", file=sys.stderr)
        return EXIT_MONGO_ERROR

    api_key = os.environ.get("SGO_API_KEY", "")
    dry_run = not args.yes

    market_keys = None
    if args.markets and args.markets != "all":
        market_keys = tuple(
            mk.strip() for mk in args.markets.split(",") if mk.strip())

    try:
        audit = await acquire_historical_window(
            db, sport=args.sport,
            start_date=args.start, end_date=args.end,
            api_key=api_key, dry_run=dry_run,
            market_keys=market_keys,
        )
    finally:
        client.close()

    if args.json:
        print(json.dumps(audit, indent=2, default=str))
    else:
        _print_summary(audit)

    if audit["status"] == "guard_closed":
        return EXIT_GUARD_CLOSED
    if audit["status"] == "errored":
        return EXIT_MONGO_ERROR
    return EXIT_OK


def main() -> int:
    args = _build_parser().parse_args()
    _print_banner(args, dry_run=not args.yes)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
