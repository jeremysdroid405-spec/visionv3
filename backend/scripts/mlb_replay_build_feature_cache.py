"""
CLI wrapper for Layer-2 historical feature-cache build.

Usage:
    python -m scripts.mlb_replay_build_feature_cache --date 2026-05-05
    python -m scripts.mlb_replay_build_feature_cache \
        --start 2026-05-01 --end 2026-05-05

Per-date OOM-safe. Resumes via `mlb_replay_feature_status`.
"""
from __future__ import annotations
import argparse
import asyncio
import logging
import os
import sys
sys.path.insert(0, "/app/backend")
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient
from services.replay.mlb_feature_cache import (
    DEFAULT_MEM_LIMIT_MB, cache_date,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s %(message)s")


def daterange(start: str, end: str):
    d0 = datetime.strptime(start, "%Y-%m-%d")
    d1 = datetime.strptime(end, "%Y-%m-%d")
    while d0 <= d1:
        yield d0.strftime("%Y-%m-%d")
        d0 += timedelta(days=1)


async def amain(args):
    dates = ([args.date] if args.date
             else list(daterange(args.start, args.end)))
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    try:
        db = cli[os.environ["DB_NAME"]]
        for d in dates:
            print(f"\n=== {d} ===", flush=True)
            print(f"  odds_collection    = {args.odds_collection}")
            print(f"  feature_source     = {args.feature_source}")
            try:
                s = await cache_date(
                    db, d, mem_limit_mb=args.mem_limit, force=args.force,
                    odds_collection=args.odds_collection,
                    feature_source=args.feature_source,
                    league=args.league,
                    sgo_lookback_days=args.sgo_lookback_days,
                    min_prior_games=args.min_prior_games,
                )
            except MemoryError as me:
                print(f"  HALTED: {me}", flush=True)
                break
            except RuntimeError as rt:
                # Hard-fail from cache_date when rows_written=0 while odds>0.
                print(f"  HARD-FAIL: {rt}", flush=True)
                sys.exit(2)
            if s.get("skipped"):
                print("  already completed — skipped (use --force to override)")
                continue
            # The "no-prior-logs" counter is labelled differently depending
            # on source so the report makes sense regardless.
            no_prior_label = ("skipped_no_prior_sgo_stats"
                                  if args.feature_source == "sgo_player_stats"
                                  else "skipped_no_hub")
            print( "  ── coverage report ──")
            print(f"  feature_source                 = {s.get('feature_source', args.feature_source)}")
            print(f"  odds_universe_count            = {s.get('odds_rows_in_window', 0)}")
            print(f"  distinct_player_market_pairs   = {s.get('distinct_player_market_pairs', 0)}")
            print(f"  universe_size (post-fam-map)   = {s.get('universe_size', 0)}")
            print(f"  feature_cache_rows_written     = {s.get('rows_written', 0)}")
            print(f"  pairs_cached (matched players) = {s.get('pairs_cached', 0)}")
            print(f"  players_cached                 = {s.get('players_cached', 0)}")
            print(f"  {no_prior_label:.<30} = {s.get(no_prior_label, 0)}")
            print(f"  skipped_few_logs               = {s.get('skipped_few_logs', 0)}")
            print(f"  skipped_stat_mapping           = {s.get('skipped_stat_mapping', 0)}")
            print(f"  skipped_stat_family_mismatch   = {s.get('skipped_stat_family_mismatch', 0)}")
            print(f"  skipped_player_name_mismatch   = {s.get('skipped_player_name_mismatch', 0)}")
            if s.get("unknown_markets_sample"):
                print(f"  unknown_markets_sample         = {s['unknown_markets_sample']}")
            if s.get("missed_player_sample"):
                print(f"  missed_player_sample           = {s['missed_player_sample']}")
            print(f"  rss start/peak/end             = {s['rss_mb_start']}/"
                  f"{s['rss_mb_peak']}/{s['rss_mb_end']} MB")
            print(f"  elapsed                        = {s['elapsed_s']:.1f}s")
    finally:
        cli.close()


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--date")
    g.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--mem-limit", default=DEFAULT_MEM_LIMIT_MB, type=int)
    p.add_argument("--force", action="store_true")
    p.add_argument("--odds-collection", default="mlb_historical_alt_odds_raw",
                    help="Source odds collection for the universe scan. "
                            "Use 'sgo_replay_alt_odds_raw' for SGO replay mode.")
    p.add_argument("--feature-source",
                      choices=["bdl_hub", "sgo_player_stats"],
                      default="bdl_hub",
                      help="Where prior game logs come from. "
                            "'bdl_hub' = mlb_master_hub_2026.bdl_game_logs[] "
                            "(legacy). 'sgo_player_stats' = SGO historical "
                            "stats (recommended for SGO replay; no BDL "
                            "backfill required).")
    p.add_argument("--league", default="MLB",
                      help="League id stored in sgo_player_stats. Only "
                            "used when --feature-source=sgo_player_stats.")
    p.add_argument("--sgo-lookback-days", type=int, default=60,
                      help="Window before replay_date scanned in "
                            "sgo_player_stats. Default 60 — well above "
                            "WINDOW_DEPTH=30. Only used when "
                            "--feature-source=sgo_player_stats.")
    p.add_argument("--min-prior-games", type=int, default=5,
                      help="Minimum prior-game count required for a "
                            "player×family pair to land in the cache. "
                            "Default 5. Lower this when running a thin "
                            "early-season window — every pair with fewer "
                            "prior logs is counted under skipped_few_logs.")
    args = p.parse_args()
    if args.start and not args.end:
        p.error("--start requires --end")
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
