"""
Drive monthly player-prop acquisition for one sport over a window.

Splits [START, END] into calendar-month chunks and runs the existing
`acquire_player_historical_window` worker for each chunk. Idempotent
via the compound unique index — re-running a finished chunk is a
no-op (dups counted, not rewritten). Streams a one-line audit row
per chunk to a log file and a JSON ledger.

Usage:
  SGO_API_KEY=… TEAM_INGEST_ENABLED=1 \
    python -m scripts.drive_player_monthly \
        --sport mlb --start 2024-03-28 --end 2026-05-29 --yes

Without --yes, runs every chunk in DRY-RUN mode (counts only).
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List

from dateutil.relativedelta import relativedelta
from motor.motor_asyncio import AsyncIOMotorClient

from workers.team.historical_player_ingest import (
    PLAYER_HIST_COLL_BY_SPORT,
    acquire_player_historical_window,
)


def _monthly_chunks(start: date, end: date) -> List[tuple[date, date]]:
    """[(chunk_start, chunk_end), …] aligned to calendar months."""
    out: List[tuple[date, date]] = []
    cur = start.replace(day=1)
    while cur <= end:
        nxt = cur + relativedelta(months=1)
        chunk_start = max(cur, start)
        chunk_end   = min(nxt - timedelta(days=1), end)
        out.append((chunk_start, chunk_end))
        cur = nxt
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(prog="drive_player_monthly")
    ap.add_argument("--sport", required=True,
                    choices=sorted(PLAYER_HIST_COLL_BY_SPORT))
    ap.add_argument("--start", required=True,
                    help="inclusive UTC start YYYY-MM-DD")
    ap.add_argument("--end",   required=True,
                    help="inclusive UTC end YYYY-MM-DD")
    ap.add_argument("--yes", action="store_true",
                    help="actually write (default: dry-run)")
    ap.add_argument(
        "--write-mode", choices=("insert", "upsert"),
        default="insert")
    ap.add_argument(
        "--skip-months", default="",
        help=("comma-sep `YYYY-MM` months to skip "
              "(use when re-driving partial windows)"))
    ap.add_argument(
        "--no-book-filter", action="store_true",
        help="preserve every book (no BLOCKED_BOOKS drop)")
    args = ap.parse_args()

    sport = args.sport
    s     = date.fromisoformat(args.start)
    e     = date.fromisoformat(args.end)
    if e < s:
        print(f"ERROR: end < start", file=sys.stderr); return 2

    skip = {m.strip() for m in args.skip_months.split(",")
            if m.strip()}

    chunks = _monthly_chunks(s, e)
    print(f"\n─── drive_player_monthly ───")
    print(f"  sport      : {sport}")
    print(f"  window     : {s.isoformat()} → {e.isoformat()}")
    print(f"  mode       : {'LIVE' if args.yes else 'DRY-RUN'}")
    print(f"  write_mode : {args.write_mode}")
    print(f"  n_chunks   : {len(chunks)}")
    print(f"  target     : {PLAYER_HIST_COLL_BY_SPORT[sport]}")
    if skip:
        print(f"  skipping   : {sorted(skip)}")

    log_path = f"/app/memory/{sport}_player_drive_log.jsonl"
    ledger: List[Dict[str, Any]] = []
    total_written = 0
    total_blocked = 0
    total_refs    = 0
    total_dups    = 0
    t0 = time.time()

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    api_key = os.environ.get("SGO_API_KEY", "")
    target_coll = PLAYER_HIST_COLL_BY_SPORT[sport]
    try:
        with open(log_path, "w") as logf:
            for i, (cs, ce) in enumerate(chunks, 1):
                month_tag = cs.strftime("%Y-%m")
                if month_tag in skip:
                    print(f"  [{i:>2}/{len(chunks)}] {month_tag}  SKIPPED")
                    continue
                t_chunk = time.time()
                pre_count = await db[target_coll].count_documents(
                    {"game_date": {"$gte": cs.isoformat(),
                                    "$lte": ce.isoformat()}}) \
                    if args.yes else 0
                try:
                    audit = await acquire_player_historical_window(
                        db, sport=sport,
                        start_date=cs.isoformat(),
                        end_date=ce.isoformat(),
                        api_key=api_key, dry_run=not args.yes,
                        write_mode=args.write_mode,
                        block_books=not args.no_book_filter,
                    )
                except Exception as exc:  # noqa: BLE001
                    audit = {"status": "errored",
                              "diagnosis": str(exc)[:200]}
                post_count = await db[target_coll].count_documents(
                    {"game_date": {"$gte": cs.isoformat(),
                                    "$lte": ce.isoformat()}}) \
                    if args.yes else 0
                elapsed = time.time() - t_chunk
                row = {
                    "i":            i,
                    "month":        month_tag,
                    "chunk_start":  cs.isoformat(),
                    "chunk_end":    ce.isoformat(),
                    "status":       audit.get("status"),
                    "n_sgo_events": audit.get("n_sgo_events"),
                    "n_props_norm": audit.get("n_props_normalized"),
                    "n_written":    audit.get("n_props_written"),
                    "n_upserted":   audit.get("n_props_upserted"),
                    "n_dups":       audit.get("n_props_duplicates"),
                    "n_blocked":    audit.get("n_blocked"),
                    "n_refs":       audit.get("n_refs"),
                    "pre_count":    pre_count,
                    "post_count":   post_count,
                    "delta":        post_count - pre_count,
                    "elapsed_s":    round(elapsed, 1),
                    "run_id":       audit.get("run_id"),
                }
                ledger.append(row)
                logf.write(json.dumps(row) + "\n")
                logf.flush()
                total_written += int(audit.get("n_props_written") or 0)
                total_blocked += int(audit.get("n_blocked") or 0)
                total_refs    += int(audit.get("n_refs") or 0)
                total_dups    += int(audit.get("n_props_duplicates")
                                       or 0)
                tag = "OK " if audit.get("status") in (
                    "succeeded", "dry_run") else "ERR"
                print(f"  [{i:>2}/{len(chunks)}] {month_tag}  {tag}  "
                      f"events={row['n_sgo_events'] or 0}  "
                      f"written={row['n_written'] or 0:>7}  "
                      f"dups={row['n_dups'] or 0:>6}  "
                      f"Δrows={row['delta']:>7}  "
                      f"{elapsed:>5.1f}s  "
                      f"status={audit.get('status')}")
                # safety: stop if dispatch guard closed
                if audit.get("status") == "guard_closed":
                    print(f"  ABORT — guard closed: "
                          f"{audit.get('diagnosis')}")
                    break
    finally:
        client.close()

    elapsed = time.time() - t0
    print(f"\n─── DONE ─── elapsed: {elapsed:.1f}s")
    print(f"  chunks       : {len(ledger)}")
    print(f"  total written: {total_written:,}")
    print(f"  total dups   : {total_dups:,}")
    print(f"  total blocked: {total_blocked:,}")
    print(f"  total refs   : {total_refs:,}")
    print(f"  log          : {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
