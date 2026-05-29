"""
Phase 1.A.4a — `team_events_sync` CLI.

One-shot operator tool: fetch every SGO event for `(sport, date)` and
upsert a `team_matchups` row per event.

  python -m scripts.team_events_sync --sport mlb --date 2025-06-15
                                       [--yes]   [--json]

Defaults to `--dry-run` semantics: the `--yes` flag is REQUIRED to
actually write to `team_matchups`. Without `--yes` it only prints
what would be written.

Exit codes (stable):
    0 ok / dry-run preview
    2 bad args
    3 dispatch guard closed
    5 SGO fetch failure
    9 Mongo error
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict

from motor.motor_asyncio import AsyncIOMotorClient

from workers.team.team_events_sync import fetch_and_sync


EXIT_OK            = 0
EXIT_BAD_ARGS      = 2
EXIT_GUARD_CLOSED  = 3
EXIT_SGO_FAILURE   = 5
EXIT_MONGO_ERROR   = 9


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="team_events_sync",
        description=(
            "Phase 1.A.4a: pull every SGO event for one UTC date and "
            "upsert a `team_matchups` row per event. DRY-RUN by "
            "default — pass --yes to actually write."
        ),
    )
    p.add_argument("--sport", required=True,
                    choices=["mlb", "nba", "nfl"])
    p.add_argument("--date",  required=True,
                    help="UTC game date as YYYY-MM-DD")
    p.add_argument("--yes",  action="store_true",
                    help="actually write rows (required for live writes)")
    p.add_argument("--json", action="store_true",
                    help="emit the audit summary as JSON only")
    return p


def _print_banner(args: argparse.Namespace, dry_run: bool) -> None:
    print("─── team_events_sync ───")
    print(f"  sport     : {args.sport}")
    print(f"  date      : {args.date}")
    print(f"  mode      : {'DRY-RUN' if dry_run else 'LIVE WRITE'}")
    print( "  target    : team_matchups")


def _print_summary(audit: Dict[str, Any]) -> None:
    print("\n─── audit ───")
    print(f"  run_id          : {audit['run_id']}")
    print(f"  status          : {audit['status']}")
    print(f"  diagnosis       : {audit['diagnosis']}")
    print(f"  duration_ms     : {audit.get('duration_ms')}")

    print("\n─── SGO ───")
    print(f"  n_sgo_events    : {audit['n_sgo_events']}")
    print(f"  n_endpoints     : {len(audit.get('sgo_endpoints') or [])}")
    eps = audit.get("sgo_endpoints") or []
    if eps:
        for ep in eps[:3]:
            print(f"    {ep}")
        if len(eps) > 3:
            print(f"    ... +{len(eps) - 3} more")

    print("\n─── normalize ───")
    print(f"  n_normalized    : {audit['n_normalized']}")
    print(f"  n_unresolved    : {audit['n_unresolved']}")

    print("\n─── write ───")
    print(f"  n_writes        : {audit['n_writes']}")
    print(f"  n_upserted      : {audit['n_upserted']}")
    print(f"  n_modified      : {audit['n_modified']}")
    print(f"  n_matched       : {audit['n_matched']}")

    samples = audit.get("sample_rows") or []
    if samples:
        print("\n─── sample matchups (first 3) ───")
        for s in samples:
            print(f"  {s.get('event_id'):24s}  "
                   f"{s.get('away_team_name'):24s} @ "
                   f"{s.get('home_team_name'):24s}  "
                   f"{s.get('commence_time')}  "
                   f"({s.get('status')})")


async def _run(args: argparse.Namespace) -> int:
    sport = args.sport.lower()
    date  = args.date
    # ── Mongo ──
    try:
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Mongo connect failed: {exc}", file=sys.stderr)
        return EXIT_MONGO_ERROR

    api_key = os.environ.get("SGO_API_KEY", "")
    dry_run = not args.yes

    try:
        audit = await fetch_and_sync(
            db, sport=sport, game_date=date,
            api_key=api_key, dry_run=dry_run,
        )
    finally:
        client.close()

    if audit["status"] == "guard_closed":
        if not args.json:
            _print_summary(audit)
        else:
            print(json.dumps(audit, indent=2, default=str))
        return EXIT_GUARD_CLOSED
    if audit["status"] == "sgo_failure":
        if not args.json:
            _print_summary(audit)
        else:
            print(json.dumps(audit, indent=2, default=str))
        return EXIT_SGO_FAILURE
    if audit["status"] == "errored":
        if not args.json:
            _print_summary(audit)
        else:
            print(json.dumps(audit, indent=2, default=str))
        return EXIT_MONGO_ERROR

    if args.json:
        print(json.dumps(audit, indent=2, default=str))
    else:
        _print_summary(audit)
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
