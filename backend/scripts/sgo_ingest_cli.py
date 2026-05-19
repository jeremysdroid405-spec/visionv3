"""
sgo_ingest_cli — single entry point for the SGO ingester.

Examples:

  # 0) Probe the API (no DB writes) — verifies auth, prints account usage,
  #    fetches 3 sample events and the discovered schema.
  python -m scripts.sgo_ingest_cli probe

  # 1) Stage-1 metadata ingest
  python -m scripts.sgo_ingest_cli metadata --league MLB --sport BASEBALL

  # 2) 1-day MLB dry-run (no DB writes, prints summary)
  python -m scripts.sgo_ingest_cli ingest \\
      --start 2025-06-15 --end 2025-06-15 \\
      --league MLB \\
      --include-alt-lines --include-consensus \\
      --include-outcomes --include-player-stats \\
      --dry-run

  # 3) 1-day MLB real ingest
  python -m scripts.sgo_ingest_cli ingest \\
      --start 2025-06-15 --end 2025-06-15 \\
      --league MLB \\
      --include-alt-lines --include-consensus \\
      --include-outcomes --include-player-stats

  # 4) Coverage report
  python -m scripts.sgo_ingest_cli coverage --league MLB --start 2025-06-15 --end 2025-06-15

Flags:
  --start, --end                 YYYY-MM-DD (inclusive)
  --sport, --league              metadata filter
  --markets a,b,c                comma-separated oddIDs (optional)
  --books a,b,c                  comma-separated bookmakerIDs (optional)
  --include-alt-lines            include alternate-line markets
  --include-consensus            include fair_odds + book_odds
  --include-outcomes             include SGO-settled outcome rows
  --include-player-stats         include player_stats + team_stats
  --no-finalized                 don't restrict to finalized events
  --dry-run                      no DB writes
  --max-rpm 250                  rate-limit (default 250 — under trial 300)
  --chunk-days 1                 split window into N-day jobs
"""
from __future__ import annotations
import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, date, timedelta, timezone
from typing import List, Optional
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

from scripts.sgo.client import SGOClient
from scripts.sgo.ingest import (
    ingest_events_window, ingest_metadata, ensure_indexes, COLLECTIONS,
)

log = logging.getLogger("sgo.cli")
logging.basicConfig(
    level=os.environ.get("SGO_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(name)s %(levelname)s %(message)s")


# ─────────────────────────────────────────────────────────────── env helpers
def _api_key() -> str:
    """Resolve the SGO API key, preferring the production env-var name.

    Priority:
      1. SPORTSGAMEODDS_API_KEY (canonical / production)
      2. SGO_API_KEY            (legacy scaffold-time fallback)
    """
    for name in ("SPORTSGAMEODDS_API_KEY", "SGO_API_KEY"):
        v = os.environ.get(name, "").strip()
        if v:
            return v
    print("ERROR: neither SPORTSGAMEODDS_API_KEY nor SGO_API_KEY is set "
          "in /app/backend/.env", file=sys.stderr)
    sys.exit(2)


def _mongo_db():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return c, c[os.environ["DB_NAME"]]


def _split_list(s: Optional[str]) -> Optional[List[str]]:
    if not s:
        return None
    return [x.strip() for x in s.split(",") if x.strip()]


def _date_chunks(start: str, end: str, days: int):
    s = date.fromisoformat(start); e = date.fromisoformat(end)
    cur = s
    while cur <= e:
        chunk_end = min(cur + timedelta(days=days - 1), e)
        yield cur.isoformat(), chunk_end.isoformat()
        cur = chunk_end + timedelta(days=1)


# ─────────────────────────────────────────────────────────────── subcommands
async def cmd_probe(_: argparse.Namespace) -> int:
    """Pre-flight probe: account usage + 1 small events query, no DB writes."""
    api_key = _api_key()
    print(f"[probe] base URL = https://api.sportsgameodds.com/v2")
    print(f"[probe] api_key  = {api_key[:6]}…{api_key[-4:]}  "
          f"(len={len(api_key)})")
    async with SGOClient(api_key=api_key, max_rpm=250) as cl:
        # 1) account/usage
        try:
            usage = await cl.get_account_usage()
            print("\n[probe] /account/usage:")
            print(json.dumps(usage, indent=2)[:2000])
        except Exception as e:
            print(f"[probe] /account/usage ERROR: {e!r}")

        # 2) one tiny events call
        print("\n[probe] /events?leagueID=MLB&limit=3&oddsAvailable=true")
        try:
            data = await cl.get_events(
                leagueID="MLB", limit=3, oddsAvailable="true")
            print(f"[probe] top-level keys: {sorted(data.keys())[:25]}")
            evs = (data.get("events") or data.get("data") or
                   data.get("results") or [])
            print(f"[probe] event count returned: {len(evs)}")
            if evs:
                ev = evs[0]
                print(f"[probe] sample event top-level keys: "
                      f"{sorted(ev.keys())}")
                print(f"[probe] sample event (truncated):")
                print(json.dumps(ev, indent=2)[:3500])
        except Exception as e:
            print(f"[probe] /events ERROR: {e!r}")

        print(f"\n[probe] HTTP stats: {cl.stats()}")
    return 0


async def cmd_metadata(args: argparse.Namespace) -> int:
    api_key = _api_key()
    cli, db = _mongo_db()
    await ensure_indexes(db)
    async with SGOClient(api_key=api_key, max_rpm=args.max_rpm) as cl:
        counts = await ingest_metadata(
            cl, db, league_id=args.league, sport_id=args.sport,
            dry_run=args.dry_run)
    print("[metadata] rows ingested per type:")
    for k, v in counts.items():
        print(f"   {k:<12s} n={v}")
    cli.close()
    return 0


async def cmd_ingest(args: argparse.Namespace) -> int:
    api_key = _api_key()
    cli, db = _mongo_db()
    await ensure_indexes(db)

    overall_stats: List[dict] = []
    async with SGOClient(api_key=api_key, max_rpm=args.max_rpm) as cl:
        for s, e in _date_chunks(args.start, args.end, args.chunk_days):
            print(f"\n[ingest] chunk {s} → {e}  league={args.league}  "
                  f"dry_run={args.dry_run}")
            summary = await ingest_events_window(
                cl, db,
                league_id=args.league, start_date=s, end_date=e,
                markets=_split_list(args.markets),
                books=_split_list(args.books),
                include_alt_lines=args.include_alt_lines,
                include_consensus=args.include_consensus,
                include_outcomes=args.include_outcomes,
                include_player_stats=args.include_player_stats,
                dry_run=args.dry_run,
                persist_raw_page=args.persist_raw_page,
                finalized_only=not args.no_finalized,
            )
            overall_stats.append(summary)
            print(f"[ingest] {summary['status']}  events={summary['events_processed']}  "
                  f"props={summary['props_rows']}  outcomes={summary['outcome_rows']}  "
                  f"consensus={summary['consensus_rows']}  "
                  f"player_stats={summary['player_stats_rows']}  "
                  f"duration={summary['duration_sec']}s  "
                  f"api_calls={summary['api_calls']}")
    print(f"\n[ingest] DONE — {len(overall_stats)} chunk(s)")
    cli.close()
    return 0


async def cmd_coverage(args: argparse.Namespace) -> int:
    from scripts.sgo.coverage import build_report, pretty_print
    rep = await build_report(league_id=args.league,
                              start_date=args.start, end_date=args.end)
    pretty_print(rep)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out = (args.out or
           f"/app/backend/audits/sgo_coverage_{args.league}_"
           f"{(args.start or 'all').replace('-','')}_"
           f"{(args.end or 'all').replace('-','')}_{stamp}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(rep, f, indent=2, default=str)
    print(f"\nReport JSON → {out}")
    return 0


# ──────────────────────────────────────────────────────────────────── parser
def build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="sgo_ingest_cli")
    sub = root.add_subparsers(dest="cmd", required=True)

    sub.add_parser("probe", help="auth/usage probe — no DB writes")

    m = sub.add_parser("metadata", help="ingest sports/leagues/teams/bookmakers")
    m.add_argument("--league", default="MLB")
    m.add_argument("--sport",  default="BASEBALL")
    m.add_argument("--max-rpm", type=int, default=250)
    m.add_argument("--dry-run", action="store_true")

    i = sub.add_parser("ingest", help="ingest events + odds + outcomes + stats")
    i.add_argument("--start",   required=True, help="YYYY-MM-DD inclusive")
    i.add_argument("--end",     required=True, help="YYYY-MM-DD inclusive")
    i.add_argument("--sport",   default="BASEBALL")
    i.add_argument("--league",  default="MLB")
    i.add_argument("--markets", default=None, help="comma-separated oddIDs")
    i.add_argument("--books",   default=None, help="comma-separated bookmakerIDs")
    i.add_argument("--include-alt-lines",    action="store_true")
    i.add_argument("--include-consensus",    action="store_true")
    i.add_argument("--include-outcomes",     action="store_true")
    i.add_argument("--include-player-stats", action="store_true")
    i.add_argument("--no-finalized",         action="store_true",
                    help="don't restrict to finalized=true")
    i.add_argument("--dry-run", action="store_true")
    i.add_argument("--persist-raw-page", action="store_true",
                    help="store the 1st raw event of each window for schema audit")
    i.add_argument("--max-rpm", type=int, default=250)
    i.add_argument("--chunk-days", type=int, default=1)

    c = sub.add_parser("coverage", help="produce Stage-4 coverage report")
    c.add_argument("--league", default="MLB")
    c.add_argument("--start",  default=None)
    c.add_argument("--end",    default=None)
    c.add_argument("--out",    default=None)
    return root


CMD = {
    "probe":    cmd_probe,
    "metadata": cmd_metadata,
    "ingest":   cmd_ingest,
    "coverage": cmd_coverage,
}


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(CMD[args.cmd](args))


if __name__ == "__main__":
    raise SystemExit(main())
