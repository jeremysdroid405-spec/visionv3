"""
backfill_team_features.py — Rebuild team_model_features with SP enrichment.

Runs build_features_for_sport for MLB and NBA, then verifies feature
completeness and prints an audit summary.

USAGE
    python -m scripts.sgo.backfill_team_features
    python -m scripts.sgo.backfill_team_features --sport mlb
    python -m scripts.sgo.backfill_team_features --dry-run
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient
from scripts.sgo.build_team_features import build_features_for_sport

SUPPORTED_SPORTS = ("mlb", "nba")
AUDIT_FIELDS = [
    "win_rate_l10", "avg_scored_l10", "avg_allowed_l10",
    "tempo_l10", "spread_cover_rate_l10",
]
MLB_AUDIT_FIELDS = AUDIT_FIELDS + [
    "sp_k_rate_avg", "sp_woba_allowed_avg", "sp_hard_hit_rate_avg",
]


async def _verify_completeness(db, sport: str) -> None:
    """Log how many teams have non-null values for key fields."""
    audit_fields = MLB_AUDIT_FIELDS if sport == "mlb" else AUDIT_FIELDS
    pipeline = [
        {"$match": {"sport": sport}},
        {"$group": {
            "_id": None,
            "total": {"$sum": 1},
            **{
                f"non_null_{f}": {
                    "$sum": {"$cond": [{"$ne": [f"${f}", None]}, 1, 0]}
                }
                for f in audit_fields
            },
        }},
    ]
    res = await db["team_model_features"].aggregate(pipeline).to_list(1)
    if not res:
        print(f"  [{sport.upper()}] no docs found in team_model_features")
        return
    r = res[0]
    total = r["total"]
    print(f"\n  [{sport.upper()}] team_model_features completeness (n={total:,}):")
    for f in audit_fields:
        nn = r.get(f"non_null_{f}", 0)
        pct = 100.0 * nn / total if total else 0
        status = "✓" if pct > 50 else "✗"
        print(f"    {status} {f:<30s}  {nn:>6,} / {total:,}  ({pct:.1f}%)")


async def amain(args: argparse.Namespace) -> int:
    sports = [args.sport] if args.sport != "all" else list(SUPPORTED_SPORTS)
    print(f"[{datetime.now(timezone.utc).isoformat()}] backfill_team_features")
    print(f"  sports={sports}  dry_run={args.dry_run}  max_teams={args.max_teams}")

    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]
    try:
        for sp in sports:
            r = await build_features_for_sport(
                db, sport=sp, dry_run=args.dry_run,
                force=args.force, max_teams=args.max_teams,
            )
            c = r["counters"]
            print(f"\n  ── {sp.upper()} BACKFILL SUMMARY ──")
            print(f"     teams processed:      {c['teams_processed']:,}")
            print(f"     team-dates emitted:   {c['team_dates_emitted']:,}")
            print(f"     feature rows written: {c['feature_rows_written']:,}  "
                  f"({'DRY-RUN' if c['dry_run'] else 'live'})")
            print(f"     leakage violations:   {c['leakage_violations']:,}")
            if r["sample_rows"]:
                print("     sample rows (first 5):")
                for s in r["sample_rows"]:
                    sp_info = (
                        f"  sp_k={s.get('sp_k_rate_avg')}  "
                        f"sp_woba={s.get('sp_woba_allowed_avg')}"
                    ) if sp == "mlb" else ""
                    print(f"       {s['team_id']:<10s} @ {s['as_of_date']}  "
                          f"n={s['sample_size']:>3}  "
                          f"win_l10={s.get('win_rate_l10')}  "
                          f"avg_scored_l10={s.get('avg_scored_l10')}"
                          f"{sp_info}")
            if not args.dry_run:
                await _verify_completeness(db, sp)

        if args.dry_run:
            print("\n  DRY-RUN — no writes. Re-run without --dry-run to persist.")
    finally:
        mongo.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sport", choices=list(SUPPORTED_SPORTS) + ["all"],
                   default="all")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--max-teams", type=int, default=200)
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
