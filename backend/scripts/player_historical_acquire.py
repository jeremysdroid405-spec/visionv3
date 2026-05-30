"""
Phase 4 — Generic player-prop historical acquisition CLI.

Sport-aware version of `nfl_player_historical_acquire`. Pulls a UTC
date window from SGO and persists player-prop rows into the canonical
per-sport collection:

  mlb → mlb_player_historical_props
  nba → nba_player_historical_props
  nfl → nfl_player_historical_props   (also reachable via the legacy CLI)

DRY-RUN by default; `--yes` writes.

Acquire-all semantics: NO market filter. NO stat-family filter. NO
alternate-line filter. The only filter applied by the normalizer is
the entity filter (player-level vs team-level markets).

  python -m scripts.player_historical_acquire \
      --sport mlb --start 2026-04-01 --end 2026-04-07
  python -m scripts.player_historical_acquire \
      --sport nba --start 2024-10-01 --end 2024-10-31 --yes
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
    PLAYER_HIST_COLL_BY_SPORT,
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
        prog="player_historical_acquire",
        description=(
            "Phase 4: pull player-prop historical odds for one UTC date "
            "window into the canonical per-sport collection. "
            "Acquire-all (no market/stat-family filter)."
        ),
    )
    p.add_argument("--sport", required=True,
                    choices=sorted(PLAYER_HIST_COLL_BY_SPORT),
                    help="sport to pull (mlb|nba|nfl)")
    p.add_argument("--start", required=True,
                    help="inclusive UTC start date YYYY-MM-DD")
    p.add_argument("--end",   required=True,
                    help="inclusive UTC end date YYYY-MM-DD")
    p.add_argument("--yes",  action="store_true",
                    help="actually write rows (default: dry-run)")
    p.add_argument("--json", action="store_true",
                    help="emit the audit row as JSON only")
    p.add_argument(
        "--write-mode", choices=("insert", "upsert"),
        default="insert",
        help=("'insert' (default, ~10x faster, duplicates skipped via "
              "compound unique index) or 'upsert' (idempotent merge)"))
    p.add_argument(
        "--no-book-filter", action="store_true",
        help=("PRESERVE every book (incl. fliff/mybookie/unknown). "
              "Blocked books are tagged blocked=True instead of "
              "dropped. Use for full historical acquisition before "
              "an SGO trial expires."))
    return p


def _print_banner(args, dry_run: bool) -> None:
    target = PLAYER_HIST_COLL_BY_SPORT[args.sport]
    print(f"─── player_historical_acquire ───")
    print(f"  sport     : {args.sport}")
    print(f"  window    : {args.start} → {args.end}")
    print(f"  mode      : {'DRY-RUN' if dry_run else 'LIVE WRITE'}")
    print(f"  write_mode: {args.write_mode}")
    print(f"  target    : {target}")


def _print_summary(audit: Dict[str, Any]) -> None:
    print("\n─── audit ───")
    for k in ("run_id", "status", "diagnosis", "duration_ms",
              "n_dates", "n_sgo_pages", "n_sgo_events",
              "n_props_normalized", "n_props_written",
              "n_props_upserted", "n_props_modified",
              "n_props_duplicates", "n_blocked", "n_refs",
              "hist_coll"):
        print(f"  {k:22s}: {audit.get(k)}")
    fams = audit.get("stat_families") or {}
    if fams:
        print(f"\n─── stat families ({len(fams)} distinct, top 30) ───")
        for k, v in sorted(fams.items(),
                             key=lambda kv: -kv[1])[:30]:
            print(f"  {k:50s}: {v}")


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
            db, sport=args.sport,
            start_date=args.start, end_date=args.end,
            api_key=api_key, dry_run=dry_run,
            write_mode=args.write_mode,
            block_books=not args.no_book_filter,
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
