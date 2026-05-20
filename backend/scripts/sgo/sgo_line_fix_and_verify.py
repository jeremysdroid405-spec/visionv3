"""
sgo_line_fix_and_verify.py — one-shot post-deploy verification + backfill.

Runs end-to-end with ONE command:

    python -m scripts.sgo.sgo_line_fix_and_verify

Steps performed:
    1. Pre-flight: confirm normalize.py contains the _resolve_line helper.
       Refuses to proceed if the patch isn't deployed.
    2. Backfill sgo_props_raw.line by re-extracting from sgo_events.raw.
       Read-only against sgo_events; bulk_write({$set: line}) only on
       rows where line IS NULL. Idempotent. No SGO API calls.
    3. Run verification queries:
         (a) count of rows with non-null line
         (b) distinct lines per top stat_id
         (c) alt-line group rate (groups w/ >1 distinct line)
         (d) duplicate count by props_raw PK
         (e) spot-check of well-known alt-line stats
    4. Run the 4 analysis scripts in safe MONTHLY chunks
       (skip if no data in that month — never load all-time at once).
    5. Print a plain-English summary.

Flags:
    --dry-run               Skip the bulk_write update; everything else runs.
    --months 2025-06,...    Comma-separated YYYY-MM list. Default: every
                            month with sgo_events present.
    --max-months 6          Hard cap to avoid runaway scans.
    --skip-analysis         Run only steps 1–3 (backfill + verify).
    --skip-backfill         Run only steps 3–4 (verify + analyse).
"""
from __future__ import annotations
import argparse
import asyncio
import importlib.util
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")  # preview fallback

# ─── Env loader (production + preview) ─────────────────────────────────────
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne


# ─── Pre-flight: confirm normalize.py has the patch ────────────────────────
def preflight_check_patch() -> bool:
    """Return True iff scripts.sgo.normalize exposes _resolve_line."""
    try:
        from scripts.sgo import normalize as _norm
    except Exception as e:
        print(f"  [ERR] cannot import scripts.sgo.normalize: {e}")
        return False
    has_resolver = hasattr(_norm, "_resolve_line")
    if not has_resolver:
        print("  [ERR] normalize.py is missing _resolve_line(). "
              "Patch is not deployed. Refusing to proceed.")
        return False
    # Smoke-call to make sure it works
    try:
        v = _norm._resolve_line({"bookOverUnder": "1.5"})
        if v != 1.5:
            print(f"  [ERR] _resolve_line smoke call returned {v!r}, expected 1.5")
            return False
    except Exception as e:
        print(f"  [ERR] _resolve_line crashed during smoke call: {e}")
        return False
    print("  [ok] normalize.py contains a working _resolve_line() helper")
    return True


# ─── Step 2: backfill ──────────────────────────────────────────────────────
async def backfill_line(db, *, dry_run: bool) -> Dict[str, Any]:
    """Re-extract sgo_props_raw.line from sgo_events.raw. Idempotent."""
    from scripts.sgo.normalize import extract_props_and_outcomes
    line_idx: Dict[Tuple[str, str, str, str, str], float] = {}
    n_events = 0
    n_lines_extracted = 0
    async for ev in db.sgo_events.find(
        {"raw": {"$ne": None}},
        projection={"_id": 0, "event_id": 1, "snapshot_time": 1, "raw": 1},
    ):
        n_events += 1
        snap = ev.get("snapshot_time") or ""
        out = extract_props_and_outcomes(ev["raw"], snapshot_time=snap)
        for r in out.get("props_raw", []):
            if r.get("line") is None:
                continue
            k = (r["event_id"], r["odd_id"], r["book_id"], r["side"],
                 r.get("snapshot_time") or snap)
            line_idx[k] = r["line"]
            n_lines_extracted += 1

    # Bulk-update rows whose line is currently null
    ops: List[UpdateOne] = []
    matched = 0
    sampled_updates: List[Dict[str, Any]] = []
    async for p in db.sgo_props_raw.find(
        {"line": None},
        projection={"_id": 1, "event_id": 1, "odd_id": 1, "book_id": 1,
                    "side": 1, "snapshot_time": 1, "stat_id": 1, "price": 1},
    ):
        k = (p["event_id"], p["odd_id"], p["book_id"], p["side"],
             p.get("snapshot_time"))
        if k in line_idx:
            matched += 1
            ln = line_idx[k]
            if len(sampled_updates) < 8:
                sampled_updates.append({
                    "stat_id": p.get("stat_id"),
                    "book_id": p.get("book_id"),
                    "side":    p.get("side"),
                    "line":    ln,
                    "price":   p.get("price"),
                })
            if not dry_run:
                ops.append(UpdateOne({"_id": p["_id"]},
                                      {"$set": {"line": ln}}))
                if len(ops) >= 1000:
                    await db.sgo_props_raw.bulk_write(ops, ordered=False)
                    ops = []
    if ops and not dry_run:
        await db.sgo_props_raw.bulk_write(ops, ordered=False)

    total = await db.sgo_props_raw.count_documents({})
    after_null = await db.sgo_props_raw.count_documents({"line": None})
    return {
        "events_scanned": n_events,
        "line_extractions_from_raw": n_lines_extracted,
        "distinct_line_keys": len(line_idx),
        "matched_null_rows": matched,
        "applied_updates": 0 if dry_run else matched,
        "props_total": total,
        "props_still_null": after_null,
        "populated_pct": (round(100 * (total - after_null) / total, 2)
                          if total else 0.0),
        "sample_updates": sampled_updates,
        "dry_run": dry_run,
    }


# ─── Step 3: verification queries ──────────────────────────────────────────
async def verify(db) -> Dict[str, Any]:
    rep: Dict[str, Any] = {}
    # (a) coverage
    total = await db.sgo_props_raw.count_documents({})
    has_line = await db.sgo_props_raw.count_documents({"line": {"$ne": None}})
    rep["line_coverage"] = {
        "total": total, "has_line": has_line,
        "rate": round(has_line / total, 4) if total else None,
    }
    # (b) distinct lines per top stat
    top_stats: List[Dict[str, Any]] = []
    pipe = [
        {"$match": {"line": {"$ne": None}}},
        {"$group": {"_id": "$stat_id",
                    "distinct_lines": {"$addToSet": "$line"},
                    "rows": {"$sum": 1}}},
        {"$project": {"_id": 1, "rows": 1,
                       "n_lines": {"$size": "$distinct_lines"},
                       "sample": {"$slice": [{"$sortArray": {
                           "input": "$distinct_lines", "sortBy": 1}}, 10]}}},
        {"$sort": {"rows": -1}},
        {"$limit": 15},
    ]
    async for d in db.sgo_props_raw.aggregate(pipe, allowDiskUse=True):
        top_stats.append({"stat_id": d["_id"], "rows": d["rows"],
                           "n_distinct_lines": d["n_lines"],
                           "sample_lines": d["sample"]})
    rep["top_stats_by_lines"] = top_stats
    # (c) strict same-stat alt-line group rate
    pipe = [
        {"$match": {"line": {"$ne": None}}},
        {"$group": {"_id": {
            "event_id": "$event_id", "player_id": "$player_id",
            "stat_id":  "$stat_id",  "side":     "$side",
            "book_id":  "$book_id",  "period_id": "$period_id"},
            "distinct_lines": {"$addToSet": "$line"}}},
        {"$project": {"line_count": {"$size": "$distinct_lines"}}},
        {"$group": {"_id": None,
                    "total_groups":      {"$sum": 1},
                    "multi_line_groups": {"$sum": {"$cond": [
                        {"$gt": ["$line_count", 1]}, 1, 0]}}}},
    ]
    alt = {"total_groups": 0, "multi_line_groups": 0, "rate": None}
    async for d in db.sgo_props_raw.aggregate(pipe, allowDiskUse=True):
        alt["total_groups"] = d["total_groups"]
        alt["multi_line_groups"] = d["multi_line_groups"]
        alt["rate"] = (round(d["multi_line_groups"] / d["total_groups"], 4)
                       if d["total_groups"] else None)
    rep["strict_same_stat_alt_line_groups"] = alt
    # (d) duplicates
    dup_pipe = [
        {"$group": {"_id": {"event_id": "$event_id", "odd_id": "$odd_id",
                             "book_id": "$book_id", "side": "$side",
                             "line": "$line", "snapshot_time": "$snapshot_time"},
                     "c": {"$sum": 1}}},
        {"$match": {"c": {"$gt": 1}}},
        {"$count": "n"},
    ]
    dup_n = 0
    async for d in db.sgo_props_raw.aggregate(dup_pipe, allowDiskUse=True):
        dup_n = d.get("n", 0)
    rep["duplicates"] = {"props_raw_dup_groups": dup_n}
    # (e) spot-check
    spot: Dict[str, List[float]] = {}
    for stat in ["batting_hits", "batting_homeRuns", "batting_totalBases",
                 "pitching_strikeouts", "batting_RBI", "batting_singles"]:
        lines = await db.sgo_props_raw.distinct(
            "line", {"stat_id": stat, "line": {"$ne": None}})
        spot[stat] = sorted(lines)[:15]
    rep["spot_check"] = spot
    return rep


# ─── Step 4: monthly analysis runs ─────────────────────────────────────────
async def discover_months(db) -> List[str]:
    months: Set[str] = set()
    async for d in db.sgo_events.aggregate(
        [{"$group": {"_id": {"$substr": ["$game_date", 0, 7]}}}]):
        if d["_id"]:
            months.add(d["_id"])
    return sorted(months)


async def run_analyses_for_month(month: str) -> Dict[str, Any]:
    """Invoke the 5 analysis modules for a single month."""
    from scripts.sgo.sgo_bucket_analysis import build as bld_bucket
    from scripts.sgo.sgo_book_coverage import build as bld_book
    from scripts.sgo.sgo_consensus_devig_analysis import build as bld_devig
    from scripts.sgo.sgo_player_prop_market_summary import build as bld_pp
    from scripts.sgo.sgo_market_depth_analysis import build as bld_depth
    start = f"{month}-01"
    end   = f"{month}-31"
    summary: Dict[str, Any] = {"month": month}
    bucket = await bld_bucket(start_date=start, end_date=end)
    book   = await bld_book(start_date=start, end_date=end)
    devig  = await bld_devig(start_date=start, end_date=end)
    pp     = await bld_pp(start_date=start, end_date=end)
    depth  = await bld_depth(start_date=start, end_date=end)
    summary["bucket_total_rows"] = sum(r["rows"] for r in bucket["rows"])
    summary["bucket_breakdowns"]   = len(bucket["rows"])
    summary["strict_alt_line_rate_avg"] = (
        round(sum((r.get("strict_same_stat_alt_line_rate") or 0)
                   for r in bucket["rows"])
              / max(len([r for r in bucket["rows"]
                          if r.get("strict_same_stat_alt_line_rate") is not None]),
                     1), 4))
    summary["books"]               = len(book["books"])
    summary["devig_markets"]       = devig["summary"]["markets_with_consensus"]
    summary["devig_twoway_pairs"]  = devig["summary"]["twoway_pairs"]
    summary["devig_anomalies"]     = devig["summary"]["anomaly_count_ge_5pp"]
    summary["player_prop_rows"]    = pp["total_props"]
    summary["distinct_player_prop_markets"] = len(pp["rows"])
    # Market-depth headline numbers (cross-stat ladder visibility)
    by_name = {d["name"]: d for d in depth["distributions"]}
    pe_market = by_name.get("player_event_market_depth", {})
    pe_line   = by_name.get("player_event_line_depth", {})
    summary["pe_market_depth_median"] = pe_market.get("median")
    summary["pe_market_depth_p90"]    = pe_market.get("p90")
    summary["pe_market_depth_ge3_rate"] = pe_market.get("ge_3_rate")
    summary["pe_line_depth_median"]   = pe_line.get("median")
    summary["pe_line_depth_ge3_rate"] = pe_line.get("ge_3_rate")
    return summary


# ─── Pretty summary ────────────────────────────────────────────────────────
def print_summary(pre_ok: bool, bf: Optional[Dict[str, Any]],
                   ver: Dict[str, Any],
                   monthly: List[Dict[str, Any]]) -> None:
    print()
    print("=" * 78)
    print("  SGO LINE-FIX + VERIFICATION — PLAIN-ENGLISH SUMMARY")
    print("=" * 78)
    print()
    print(f"• Pre-flight: normalize.py patch deployed  → "
          f"{'YES ✓' if pre_ok else 'NO ✗ (aborted)'}")
    if bf is not None:
        bf_tag = "DRY-RUN" if bf["dry_run"] else "APPLIED"
        print(f"• Backfill ({bf_tag}):")
        print(f"    events scanned from sgo_events.raw:  {bf['events_scanned']}")
        print(f"    distinct (event,odd,book,side,snap) lines found in raw:  "
              f"{bf['distinct_line_keys']}")
        print(f"    sgo_props_raw rows that were null and now matched:   "
              f"{bf['matched_null_rows']}")
        if not bf["dry_run"]:
            print(f"    bulk_write $set updates applied:                     "
                  f"{bf['applied_updates']}")
        print(f"    line population after backfill: "
              f"{bf['props_total'] - bf['props_still_null']}/"
              f"{bf['props_total']}  ({bf['populated_pct']}%)")
        if bf["sample_updates"]:
            print(f"    sample of newly-filled rows:")
            for s in bf["sample_updates"]:
                print(f"      {s['stat_id']:<22s} {s['book_id']:<12s} "
                      f"{s['side']:<6s} line={s['line']!s:<5s} price={s['price']}")
    print()
    print("• Verification:")
    cov = ver["line_coverage"]
    print(f"    line populated:        {cov['has_line']}/{cov['total']}  "
          f"({(cov['rate'] or 0)*100:.1f}%)")
    alt = ver["strict_same_stat_alt_line_groups"]
    rate = (alt["rate"] or 0) * 100
    print(f"    strict same-stat alt-line groups: "
          f"{alt['multi_line_groups']} multi-line of {alt['total_groups']} "
          f"total  ({rate:.2f}%)")
    print(f"    [NB] This metric ONLY counts the same stat on the same book "
          f"having multiple lines (e.g. FanDuel batting_hits 1.5+2.5). "
          f"Cross-stat ladders (hits/HR/totalBases/fantasyScore per player) "
          f"are not counted here — see Monthly Analysis below for "
          f"market-depth metrics.")
    print(f"    duplicate rows (by PK): {ver['duplicates']['props_raw_dup_groups']}")
    print()
    print("    Distinct lines on well-known alt markets:")
    for stat, lines in ver["spot_check"].items():
        if lines:
            print(f"      {stat:<22s} {lines}")
        else:
            print(f"      {stat:<22s} (no rows)")
    print()
    if monthly:
        print("• Monthly analysis runs:")
        print(f"    {'month':<10s} {'props':>9s} {'markets':>8s} "
              f"{'strict_alt%':>11s} {'pe_market_med':>13s} "
              f"{'pe_market_p90':>13s} {'pe_market_ge3':>13s} "
              f"{'pe_line_med':>11s} {'books':>6s} "
              f"{'twoway':>7s} {'pp_rows':>9s}")
        for m in monthly:
            ge3 = (m.get('pe_market_depth_ge3_rate') or 0) * 100
            print(f"    {m['month']:<10s} "
                  f"{m['bucket_total_rows']:>9d} "
                  f"{m['bucket_breakdowns']:>8d} "
                  f"{m['strict_alt_line_rate_avg']*100:>10.2f}% "
                  f"{(m.get('pe_market_depth_median') or 0):>13.1f} "
                  f"{(m.get('pe_market_depth_p90') or 0):>13.1f} "
                  f"{ge3:>12.1f}% "
                  f"{(m.get('pe_line_depth_median') or 0):>11.1f} "
                  f"{m['books']:>6d} "
                  f"{m['devig_twoway_pairs']:>7d} "
                  f"{m['player_prop_rows']:>9d}")
        print()
        print("    Legend:")
        print("      strict_alt%   = strict same-stat alt-line rate (FanDuel batting_hits 1.5+2.5)")
        print("      pe_market_*   = distinct stat_ids per (player,event,side) — CROSS-STAT ladder depth")
        print("      pe_line_med   = distinct lines per (player,event,side) — combined ladder depth")
        print("      pe_market_ge3 = share of (player,event,side) with ≥3 distinct stat markets")
    print()
    # Verdict
    alt = ver["strict_same_stat_alt_line_groups"]
    healthy = (alt["rate"] is not None and alt["rate"] > 0.05 and
               cov["rate"] is not None and cov["rate"] > 0.5 and
               ver["duplicates"]["props_raw_dup_groups"] == 0)
    print(f"• Overall verdict: {'HEALTHY ✓' if healthy else 'CHECK NEEDED ⚠'}")
    if not healthy:
        msg = []
        if cov["rate"] is None or cov["rate"] <= 0.5:
            msg.append("line coverage still < 50% — backfill may have skipped "
                       "rows due to a (event,odd,book,side,snapshot_time) "
                       "mismatch between props_raw and sgo_events.raw")
        if alt["rate"] is None or alt["rate"] <= 0.05:
            msg.append("strict same-stat alt-line rate ≤ 5% — this is "
                       "EXPECTED if your books primarily encode ladders "
                       "across stat_ids (not as multiple lines per stat). "
                       "Check the Monthly Analysis market-depth columns "
                       "(pe_market_med, pe_market_p90) for the real "
                       "ladder visibility.")
        if ver["duplicates"]["props_raw_dup_groups"] > 0:
            msg.append(f"{ver['duplicates']['props_raw_dup_groups']} duplicate "
                       f"groups detected — index may be missing or unique key drifted")
        for m in msg:
            print(f"    - {m}")
    print("=" * 78)


# ─── Main ──────────────────────────────────────────────────────────────────
async def amain(args: argparse.Namespace) -> int:
    print(f"[{datetime.now(timezone.utc).isoformat()}] SGO line-fix runner")

    # Step 1 — pre-flight
    print("\n[step 1] pre-flight")
    pre_ok = preflight_check_patch()
    if not pre_ok:
        return 2

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Step 2 — backfill
    bf: Optional[Dict[str, Any]] = None
    if not args.skip_backfill:
        print(f"\n[step 2] backfill (dry_run={args.dry_run})")
        bf = await backfill_line(db, dry_run=args.dry_run)
        print(f"  events scanned: {bf['events_scanned']}  "
              f"keys found: {bf['distinct_line_keys']}  "
              f"matched-null rows: {bf['matched_null_rows']}  "
              f"populated: {bf['populated_pct']}%")
    else:
        print("\n[step 2] backfill SKIPPED (--skip-backfill)")

    # Step 3 — verify
    print("\n[step 3] verification queries")
    ver = await verify(db)
    print(f"  line populated: "
          f"{ver['line_coverage']['has_line']}/{ver['line_coverage']['total']}")
    print(f"  strict same-stat alt-line group rate: "
          f"{(ver['strict_same_stat_alt_line_groups']['rate'] or 0)*100:.2f}%")

    # Step 4 — monthly analyses
    monthly: List[Dict[str, Any]] = []
    if not args.skip_analysis:
        print("\n[step 4] monthly analyses (one month at a time)")
        if args.months:
            months = [m.strip() for m in args.months.split(",") if m.strip()]
        else:
            months = await discover_months(db)
        if len(months) > args.max_months:
            print(f"  [warn] discovered {len(months)} months; capping at "
                  f"--max-months={args.max_months}")
            months = months[:args.max_months]
        for mo in months:
            try:
                row = await run_analyses_for_month(mo)
                monthly.append(row)
                print(f"  {mo}  props={row['bucket_total_rows']}  "
                      f"strict_alt%={row['strict_alt_line_rate_avg']*100:.2f}  "
                      f"pe_market_med={row.get('pe_market_depth_median') or '-'}  "
                      f"pe_market_p90={row.get('pe_market_depth_p90') or '-'}  "
                      f"books={row['books']}  twoway={row['devig_twoway_pairs']}")
            except Exception as e:
                print(f"  {mo}  ANALYSIS FAILED: {e!r}")
    else:
        print("\n[step 4] analyses SKIPPED (--skip-analysis)")

    print_summary(pre_ok, bf, ver, monthly)
    client.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true",
                    help="Don't apply $set updates; everything else runs")
    p.add_argument("--months", default=None,
                    help="Comma-separated YYYY-MM list; default = discover")
    p.add_argument("--max-months", type=int, default=6,
                    help="Hard cap on number of months analysed (OOM safety)")
    p.add_argument("--skip-analysis", action="store_true")
    p.add_argument("--skip-backfill", action="store_true")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
