"""
Phase 4 — `nfl_player_historical_acquire` CLI.

Pull a UTC date window from SGO and persist NFL player-prop rows to
`nfl_player_historical_props`. DRY-RUN by default; `--yes` writes.

  python -m scripts.nfl_player_historical_acquire \\
      --start 2024-02-10 --end 2026-02-09 [--yes] [--json]
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

from workers.team.historical_player_ingest import (
    acquire_player_historical_window,
)


EXIT_OK            = 0
EXIT_BAD_ARGS      = 2
EXIT_GUARD_CLOSED  = 3
EXIT_SGO_FAILURE   = 5
EXIT_MONGO_ERROR   = 9

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nfl_player_historical_acquire",
        description=(
            "Phase 4: pull NFL player-prop historical odds for one "
            "UTC date window. Acquire-all (no stat filter)."
        ),
    )
    p.add_argument("--start", required=True,
                    help="inclusive UTC start date YYYY-MM-DD")
    p.add_argument("--end",   required=True,
                    help="inclusive UTC end date YYYY-MM-DD")
    p.add_argument("--yes",  action="store_true",
                    help="actually write rows (default: dry-run)")
    p.add_argument("--json", action="store_true",
                    help="emit the audit row as JSON only")
    return p


def _print_banner(args, dry_run: bool) -> None:
    print("─── nfl_player_historical_acquire ───")
    print(f"  sport     : nfl")
    print(f"  window    : {args.start} → {args.end}")
    print(f"  mode      : {'DRY-RUN' if dry_run else 'LIVE WRITE'}")
    print(f"  target    : nfl_player_historical_props")


def _print_summary(audit: Dict[str, Any]) -> None:
    print("\n─── audit ───")
    for k in ("run_id", "status", "diagnosis", "duration_ms",
              "n_dates", "n_sgo_pages", "n_sgo_events",
              "n_props_normalized", "n_props_written",
              "n_props_upserted", "n_props_modified",
              "n_blocked", "n_refs"):
        print(f"  {k:22s}: {audit.get(k)}")
    fams = audit.get("stat_families") or {}
    if fams:
        print("\n─── stat families (top 20 by frequency) ───")
        for k, v in sorted(fams.items(),
                             key=lambda kv: -kv[1])[:20]:
            print(f"  {k:40s}: {v}")


async def _run(args) -> int:
    if not _DATE_RE.match(args.start) or not _DATE_RE.match(args.end):
        print(f"ERROR: dates must be YYYY-MM-DD", file=sys.stderr)
        return EXIT_BAD_ARGS
    try:
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Mongo connect failed: {exc}", file=sys.stderr)
        return EXIT_MONGO_ERROR

    api_key = os.environ.get("SGO_API_KEY", "")
    dry_run = not args.yes
    try:
        audit = await acquire_player_historical_window(
            db, sport="nfl",
            start_date=args.start, end_date=args.end,
            api_key=api_key, dry_run=dry_run,
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
