"""
Team odds fixture recorder — Phase 1.A.3.2 CLI STUB.

This CLI is the OPERATOR entrypoint for capturing sanitized SGO
event-odds payloads. **It is not wired in Phase 1.A.3.2.** Any
execution that reaches the recording phase aborts with
NotImplementedError to keep the safety contract:

  - no real SGO call in 1.A.3.2
  - no fixture committed in 1.A.3.2
  - no prod-key usage in 1.A.3.2

What this file IS in 1.A.3.2:
  - the locked argparse + flag contract
  - the dispatch-guard check (mirrors workers/team/base.py)
  - the operator-facing confirmation/print path

What this file BECOMES in 1.A.3.3:
  - real `httpx.get(...)` call to SGO (ONE request, no retries)
  - response body through `sanitize_response_bytes()`
  - `assert_sanitized()` gate
  - sibling `.meta.json` write with sha256 + git sha
  - atomic rename of both files into
    `backend/tests/fixtures/team_odds/<sport>/`

Refer to FIXTURE_RECORDING_PLAYBOOK.md §5 for the full flow spec.
"""
from __future__ import annotations

import argparse
import sys


def _build_parser() -> argparse.ArgumentParser:
    """Argparse contract — pinned in 1.A.3.2 so tests can assert
    against it before any real network code lands.
    """
    p = argparse.ArgumentParser(
        prog="team_odds_fixture_record",
        description=(
            "Record ONE sanitized SGO event-odds payload to "
            "backend/tests/fixtures/team_odds/ for Tier 3 replay "
            "tests. Phase 1.A.3.2: STUB ONLY — refuses to run."
        ),
    )
    p.add_argument(
        "--sport", required=True, choices=["mlb", "nba", "nfl"],
        help="Sport league to record against.",
    )
    p.add_argument(
        "--event-id", required=True,
        help="SGO event_id to capture (single event per invocation).",
    )
    p.add_argument(
        "--recorded-by", required=True,
        help="Operator identifier persisted into the meta file.",
    )
    p.add_argument(
        "--output", default="backend/tests/fixtures/team_odds",
        help=(
            "Output directory root. The recorder writes under "
            "<output>/<sport>/. Default: backend/tests/fixtures/team_odds"
        ),
    )
    p.add_argument(
        "--yes", action="store_true",
        help=(
            "Skip interactive confirmation. Required for "
            "scripted/CI use. Even with --yes the dispatch guard "
            "still applies and 1.A.3.2 still refuses to run."
        ),
    )
    p.add_argument(
        "--print-plan", action="store_true",
        help=(
            "Print the recording plan + exit 0. Safe in Phase "
            "1.A.3.2 — does NOT call SGO or write any file."
        ),
    )
    return p


def _print_plan(args: argparse.Namespace) -> None:
    """Operator-facing dry-run of the planned recording. Pure text
    output. No HTTP, no file I/O.
    """
    print("─── Team odds fixture recorder — RECORDING PLAN ───")
    print(f"  sport         : {args.sport}")
    print(f"  event_id      : {args.event_id}")
    print(f"  recorded_by   : {args.recorded_by}")
    print(f"  output dir    : {args.output}/{args.sport}/")
    print(f"  output files  : "
          f"<UTC_DATE>_{args.event_id}.json + .meta.json")
    print("  http requests : 1 (GET, no retries, no fan-out)")
    print("  sanitize      : leaked-key strip + assert_sanitized")
    print("  guards        : SGO_API_KEY + TEAM_INGEST_ENABLED=1")
    print( "  status        : Phase 1.A.3.2 STUB — recording disabled.")
    print( "                  Land Phase 1.A.3.3 to wire the real flow.")


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    _print_plan(args)
    if args.print_plan:
        return 0
    # Phase 1.A.3.2 hard refusal — no env check, no SGO call, no write.
    # The dispatch-guard check + real recording arrive in 1.A.3.3.
    print(
        "\nERROR: real recording is not wired in Phase 1.A.3.2.\n"
        "       Run with --print-plan to inspect the planned flow,\n"
        "       or wait for Phase 1.A.3.3 to land the recorder.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
