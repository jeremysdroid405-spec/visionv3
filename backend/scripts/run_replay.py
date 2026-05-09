#!/usr/bin/env python3
"""
PropVision Historical Replay — CLI driver (Phase 0 STUB).

In Phase 0 this script ONLY prints the planned execution. It does NOT:
  - call The Odds API
  - write to MongoDB
  - touch live collections / scoring / gates

Phases 1+ will progressively wire in `--ingest`, `--replay`, etc.

USAGE (Phase 0)
---------------
    python /app/backend/scripts/run_replay.py --plan-only \\
        --range 2024-01-01:2024-02-01 \\
        --sport nba

    python /app/backend/scripts/run_replay.py --plan-only \\
        --range 2024-01-01:2024-01-31 \\
        --sport nba \\
        --windows t-24h,t-6h,t-3h,t-60m,t-30m,close \\
        --markets all \\
        --books dk,fd,betonlineag,williamhill_us,betmgm \\
        --run-name "vk2_combo_fix_v1"
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.replay import (  # noqa: E402
    REPLAY_WINDOW_LABELS,
    PER_TIER_CANONICAL_SNAPSHOT,
    REPLAY_NBA_MARKETS,
    REPLAY_BOOK_WHITELIST_PHASE1,
    REPLAY_REGIONS_PHASE1,
    REPLAY_COLLECTIONS,
    DATASET_LINEAGE_VALUE,
    compute_run_fingerprint,
    new_run_id,
)


# Run-level credit kill switch (user directive 2026-05-09).
HARD_CREDIT_KILL_SWITCH = 1_000_000


def _parse_range(s: str) -> tuple[datetime, datetime]:
    a, b = s.split(":", 1)
    start = datetime.fromisoformat(a).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(b).replace(tzinfo=timezone.utc)
    if end < start:
        raise argparse.ArgumentTypeError("--range end must be >= start")
    return start, end


def _csv_list(s: str) -> List[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="PropVision historical replay driver (Phase 0 stub).",
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan-only", action="store_true",
                      help="Print planned actions; no DB / no API calls.")
    mode.add_argument("--ingest", action="store_true",
                      help="(Phase 1+, NOT IMPLEMENTED YET) — ingest snapshots.")
    mode.add_argument("--replay", action="store_true",
                      help="(Phase 2+, NOT IMPLEMENTED YET) — score snapshots.")

    p.add_argument("--range", required=True, type=_parse_range,
                   help="YYYY-MM-DD:YYYY-MM-DD UTC")
    p.add_argument("--sport", required=True, choices=["nba", "mlb", "nfl"])
    p.add_argument("--windows", default=",".join(REPLAY_WINDOW_LABELS),
                   help="Comma-separated subset of "
                        f"{REPLAY_WINDOW_LABELS}.")
    p.add_argument("--markets", default="all",
                   help="'all' or comma-separated list of market keys.")
    p.add_argument("--books",
                   default=",".join(REPLAY_BOOK_WHITELIST_PHASE1),
                   help="Comma-separated book keys.")
    p.add_argument("--run-name", default="(unnamed)",
                   help="Human-readable label for the planned run.")
    p.add_argument("--notes", default="")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.sport != "nba":
        print(f"ERROR: only --sport nba supported in Phase 0+1 "
              f"(requested: {args.sport})", file=sys.stderr)
        return 2

    windows = _csv_list(args.windows)
    bad = [w for w in windows if w not in REPLAY_WINDOW_LABELS]
    if bad:
        print(f"ERROR: unknown windows {bad}; valid: {REPLAY_WINDOW_LABELS}",
              file=sys.stderr)
        return 2

    if args.markets == "all":
        markets = list(REPLAY_NBA_MARKETS)
    else:
        markets = _csv_list(args.markets)
        bad = [m for m in markets if m not in REPLAY_NBA_MARKETS]
        if bad:
            print(f"ERROR: unknown markets {bad}", file=sys.stderr)
            return 2

    books = _csv_list(args.books)
    fingerprint = compute_run_fingerprint(repo_root=REPO_ROOT)
    run_id = new_run_id()

    plan = {
        "phase": "0_plan_only",
        "run_id_preview": run_id,
        "run_name": args.run_name,
        "notes": args.notes,
        "sport_key": "basketball_nba",
        "range_start_utc": args.range[0].isoformat(),
        "range_end_utc":   args.range[1].isoformat(),
        "windows": windows,
        "markets": markets,
        "books": books,
        "regions": REPLAY_REGIONS_PHASE1,
        "per_tier_canonical_snapshot": PER_TIER_CANONICAL_SNAPSHOT,
        "dataset_lineage": DATASET_LINEAGE_VALUE,
        "hard_credit_kill_switch": HARD_CREDIT_KILL_SWITCH,
        "target_collections_readonly_in_phase0": REPLAY_COLLECTIONS,
        "fingerprint": fingerprint,
        "warnings": [],
    }

    # Per-event credit estimate (informational).
    per_event = 10 * len(markets) * len(REPLAY_REGIONS_PHASE1)
    plan["estimated_credits_per_event_per_window"] = per_event
    plan["estimated_credits_per_event_full_ladder"] = per_event * len(windows)

    if args.ingest or args.replay:
        plan["warnings"].append(
            "Phase 0 only — --ingest / --replay are accepted but inert. "
            "Re-run with --plan-only to confirm behaviour."
        )

    print(json.dumps(plan, indent=2, sort_keys=False))
    print("\n[Phase 0] No DB writes. No API calls. Plan-only.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
