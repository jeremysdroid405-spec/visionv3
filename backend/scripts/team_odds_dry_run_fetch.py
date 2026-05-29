"""
Phase 1.A.3.4a — `team_odds_dry_run_fetch` CLI.

One-shot operator tool that walks the FULL path against a real SGO
event WITHOUT ever writing to `team_live_props`:

    HTTP → SGOPayloadProvider → sanitize → run_pass(mode="dry_run")
        → team_odds_ingest_runs audit row → stdout summary

Hard-locked invariants (Phase 1.A.3.4a contract):
  - ONE event per invocation
  - mode is ALWAYS forced to "dry_run" — even with TEAM_INGEST_LIVE=1
    set, this CLI will not call `run_pass(mode="live")`
  - No retries (single fetch)
  - No fixture written to disk (use `team_odds_fixture_record` for that)
  - No fan-out
  - Dispatch guard still required (`SGO_API_KEY` + `TEAM_INGEST_ENABLED=1`)

Usage:
    export SGO_API_KEY=...
    export TEAM_INGEST_ENABLED=1
    python -m scripts.team_odds_dry_run_fetch \
        --sport mlb --event-id evt_abc [--snapshot-iso 2026-06-02T22:00:00Z]
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

from workers.team._sgo_provider import SGOFetchError
from workers.team.base import dispatch_guard_ok
from workers.team.team_odds_ingest import TeamOddsIngestWorker


# ── exit codes (stable) ──────────────────────────────────────────────
EXIT_OK            = 0
EXIT_BAD_ARGS      = 2
EXIT_GUARD_CLOSED  = 3
EXIT_SGO_FAILURE   = 5
EXIT_MONGO_ERROR   = 9


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="team_odds_dry_run_fetch",
        description=(
            "One-shot DRY-RUN SGO smoke fetch. Runs the full ingest "
            "path (HTTP → sanitize → run_pass) but writes ZERO rows "
            "to team_live_props. Used to validate the path against "
            "real SGO data before flipping TEAM_INGEST_LIVE=1."
        ),
    )
    p.add_argument("--sport",  required=True,
                    choices=["mlb", "nba", "nfl"])
    p.add_argument("--event-id",     required=True)
    p.add_argument("--snapshot-iso", default=None,
                    help="UTC ISO; defaults to now")
    p.add_argument("--json", action="store_true",
                    help="emit the full audit row as JSON only")
    return p


def _print_banner(args: argparse.Namespace) -> None:
    print("─── team_odds_dry_run_fetch — DRY-RUN ONLY ───")
    print(f"  sport         : {args.sport}")
    print(f"  event_id      : {args.event_id}")
    print(f"  snapshot_iso  : {args.snapshot_iso or '<now>'}")
    print( "  mode          : dry_run (HARD-LOCKED)")
    print( "  writes        : 0 to team_live_props")
    print( "  audit row     : 1 to team_odds_ingest_runs")


def _would_write_count(audit: Dict[str, Any]) -> int:
    """How many rows would land in team_live_props if mode were live
    AND the market-explosion guard didn't fire. Mirrors
    `_build_upsert_ops` math: normalized minus blocked minus
    unresolved. Reference-only rows ARE included since the worker
    writes them (with `reference_only=True`).
    """
    if audit.get("explosion_abort"):
        return 0
    return max(0,
        int(audit.get("n_rows_normalized", 0))
        - int(audit.get("n_blocked",        0))
        - int(audit.get("n_unresolved",     0))
    )


def _print_summary(audit: Dict[str, Any]) -> None:
    print("\n─── audit row ───")
    print(f"  run_id          : {audit['run_id']}")
    print(f"  status          : {audit['status']}")
    print(f"  diagnosis       : {audit['diagnosis']}")
    print(f"  duration_ms     : {audit['duration_ms']}")
    print(f"  snapshot_iso    : {audit['snapshot_iso']}")

    print("\n─── HTTP / normalize ───")
    print(f"  sgo_events      : {audit['n_sgo_events']}")
    print(f"  sgo_outcomes    : {audit['n_sgo_outcomes']}")
    print(f"  rows_normalized : {audit['n_rows_normalized']}")

    print("\n─── book policy ───")
    print(f"  n_blocked       : {audit['n_blocked']}")
    print(f"  n_refs          : {audit['n_refs']}")

    print("\n─── master hub resolution ───")
    print(f"  n_unresolved    : {audit['n_unresolved']}")

    print("\n─── market explosion guard ───")
    print(f"  observed_markets : {audit['observed_markets']}")
    print(f"  expected_markets : {audit['expected_markets']}")
    print(f"  explosion_abort  : {audit['explosion_abort']}")

    per_market = audit.get("per_market_counts") or {}
    if per_market:
        print("\n─── per-market normalized counts ───")
        for k in sorted(per_market.keys()):
            print(f"  {k:32s} : {per_market[k]}")
    else:
        print("\n─── per-market normalized counts ───  (none)")

    print("\n─── projected impact ───")
    print(f"  WOULD have written : {_would_write_count(audit)} "
           "rows to team_live_props (mode='live')")
    print(f"  actually wrote     : {audit['n_writes']} rows "
           "(mode='dry_run')")


async def _run(args: argparse.Namespace) -> int:
    # ── Dispatch guard ──
    ok, reasons = dispatch_guard_ok()
    if not ok:
        print(
            "ERROR: dispatch guard closed — cannot fetch.\n"
            "       reasons: " + "; ".join(reasons),
            file=sys.stderr,
        )
        return EXIT_GUARD_CLOSED

    api_key = os.environ["SGO_API_KEY"]

    # ── Mongo connect ──
    try:
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Mongo connect failed: {exc}", file=sys.stderr)
        return EXIT_MONGO_ERROR

    # Snapshot ISO defaulting
    snap_iso = args.snapshot_iso or \
                 datetime.now(timezone.utc).isoformat()

    worker = TeamOddsIngestWorker(args.sport)
    try:
        # mode is HARD-CODED to "dry_run" — Phase 1.A.3.4a contract.
        audit = await worker.fetch_and_run_pass(
            db,
            event_id=args.event_id,
            api_key=api_key,
            snapshot_iso=snap_iso,
            mode="dry_run",
        )
    except SGOFetchError as exc:
        print(f"ERROR: SGO fetch failed: {exc}", file=sys.stderr)
        return EXIT_SGO_FAILURE
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: unexpected failure: {exc}", file=sys.stderr)
        return EXIT_MONGO_ERROR
    finally:
        client.close()

    if args.json:
        print(json.dumps(audit, indent=2, default=str))
    else:
        _print_summary(audit)
    return EXIT_OK


def main() -> int:
    args = _build_parser().parse_args()
    _print_banner(args)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
