"""
Team odds fixture recorder — Phase 1.A.3.3.

Single-shot CLI that:
  1. Verifies the dispatch guard (SGO_API_KEY + TEAM_INGEST_ENABLED=1).
  2. Builds the planned SGO URL with the operator's key stripped from
     anything logged.
  3. Prompts for confirmation (skipped with --yes).
  4. Calls `SGOPayloadProvider.fetch_event_odds` (ONE GET, no retries).
  5. Sanitizes the response bytes (`<REDACTED>` for the literal key
     + api_key= / x-rapidapi-key / authorization / bearer patterns).
  6. Builds the sibling meta file with provenance + sha256 checksum.
  7. Runs `assert_sanitized()` — abort on any rule violation.
  8. Atomically writes both files into
     `<output>/<sport>/<UTC-date>_<event_id>.json` (and `.meta.json`).
  9. Prints absolute paths.

Phase 1.A.3.3 still writes ZERO rows to `team_live_props`. Real ingest
dispatch arrives later.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from services.team_master_hub.fixtures import (
    SANITIZATION_VERSION,
    FixtureSanitizationError,
    assert_sanitized,
    compute_checksum,
)
from workers.team._sgo_provider import SGOFetchError, SGOPayloadProvider
from workers.team.base import dispatch_guard_ok


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="team_odds_fixture_record",
        description=(
            "Record ONE sanitized SGO event-odds payload to "
            "backend/tests/fixtures/team_odds/. "
            "Requires SGO_API_KEY + TEAM_INGEST_ENABLED=1."
        ),
    )
    p.add_argument("--sport",  required=True,
                    choices=["mlb", "nba", "nfl"])
    p.add_argument("--event-id",    required=True)
    p.add_argument("--recorded-by", required=True)
    p.add_argument("--output",      default=
                    "backend/tests/fixtures/team_odds")
    p.add_argument("--yes",         action="store_true",
                    help="skip interactive confirmation")
    p.add_argument("--print-plan",  action="store_true",
                    help="print the recording plan and exit 0")
    return p


def _print_plan(args: argparse.Namespace,
                  sanitized_url: str | None = None) -> None:
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
    if sanitized_url:
        print(f"  url           : {sanitized_url}")


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            check=False,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _safe_event_id(event_id: str) -> str:
    """Restrict event_id to filename-safe chars."""
    cleaned = re.sub(r"[^A-Za-z0-9_\-]", "_", event_id)
    return cleaned or "event"


def _atomic_write(path: Path, data: bytes) -> None:
    """Write atomically: `<path>.tmp` then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _do_record(args: argparse.Namespace) -> int:
    # ── Dispatch guard ──
    ok, reasons = dispatch_guard_ok()
    if not ok:
        print(
            "ERROR: dispatch guard closed — cannot record.\n"
            "       reasons: " + "; ".join(reasons),
            file=sys.stderr,
        )
        return 3
    api_key = os.environ["SGO_API_KEY"]

    # ── Provider + URL preview ──
    provider = SGOPayloadProvider(api_key)
    _, sanitized_url = provider._build_url(
        sport=args.sport, event_id=args.event_id)
    _print_plan(args, sanitized_url=sanitized_url)

    # ── Confirmation ──
    if not args.yes:
        try:
            ans = input("\nProceed with this recording? [y/N]: ")
        except EOFError:
            ans = ""
        if ans.strip().lower() != "y":
            print("aborted by operator", file=sys.stderr)
            return 4

    # ── Fetch ──
    try:
        result = provider.fetch_event_odds(
            sport=args.sport, event_id=args.event_id)
    except SGOFetchError as exc:
        print(f"ERROR: SGO fetch failed: {exc}", file=sys.stderr)
        return 5

    payload:         Dict[str, Any] = result["payload"]
    sanitized_bytes: bytes          = result["sanitized_bytes"]

    # ── Derive filename ──
    commence_iso = ""
    for ev in payload.get("events", []) or []:
        # New SGO shape uses `startsAt`; synthetic tests use
        # `commence_time` — tolerate both.
        iso = ev.get("startsAt") or ev.get("commence_time")
        if iso:
            commence_iso = iso
            break
    try:
        utc_date = datetime.fromisoformat(
            (commence_iso or "").replace("Z", "+00:00")
        ).astimezone(timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        utc_date = datetime.now(timezone.utc).date().isoformat()

    out_dir   = Path(args.output) / args.sport
    fname     = f"{utc_date}_{_safe_event_id(args.event_id)}.json"
    json_path = out_dir / fname
    meta_path = json_path.with_suffix(".meta.json")

    # ── Re-serialize sanitized payload deterministically.
    # The checksum is computed against THIS exact byte stream so
    # `load_fixture` round-trips cleanly. We also store the original
    # SGO bytes' length for visibility, but the on-disk file is the
    # canonical re-serialized form.
    final_bytes = json.dumps(payload, ensure_ascii=False,
                              sort_keys=True).encode("utf-8")

    meta = {
        "fixture_version":      1,
        "sanitization_version": SANITIZATION_VERSION,
        "recorded_at":          datetime.now(timezone.utc).isoformat(),
        "sport":                args.sport,
        "sgo_endpoint":         result["sgo_endpoint"],
        "event_id":             args.event_id,
        "commence_time":        commence_iso,
        "books_seen":           result["books_seen"],
        "markets_seen":         result["markets_seen"],
        "outcomes_count":       result["outcomes_count"],
        "recorded_by":          args.recorded_by,
        "git_sha":              _git_sha(),
        "sanitized_bytes_len":  len(sanitized_bytes),
        "checksum_sha256":      compute_checksum(final_bytes),
    }

    # ── Gate via assert_sanitized BEFORE writing ──
    try:
        assert_sanitized(payload, meta)
    except FixtureSanitizationError as exc:
        print(f"ERROR: sanitization failed: {exc}", file=sys.stderr)
        return 6

    # ── Atomic writes ──
    try:
        _atomic_write(json_path, final_bytes)
        meta_bytes = json.dumps(meta, indent=2,
                                  ensure_ascii=False).encode("utf-8")
        _atomic_write(meta_path, meta_bytes)
    except OSError as exc:
        print(f"ERROR: write failed: {exc}", file=sys.stderr)
        return 7

    print("\nRECORDED:")
    print(f"  payload : {json_path.resolve()}")
    print(f"  meta    : {meta_path.resolve()}")
    print(f"  books   : {len(result['books_seen'])} unique")
    print(f"  markets : {len(result['markets_seen'])} unique")
    print(f"  outcomes: {result['outcomes_count']}")
    return 0


def main() -> int:
    args = _build_parser().parse_args()
    if args.print_plan:
        _print_plan(args)
        return 0
    return _do_record(args)


if __name__ == "__main__":
    sys.exit(main())
