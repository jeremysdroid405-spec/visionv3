"""Production replay for a list of dates — uses existing SSOT path.

Steps per date:
  1. If Layer-3 has the legacy v1.0 source_version OR is missing, run
     `replay_date(force=True)` to rebuild against the hydrated v1.1 engine.
  2. Run `run_production_replay(...)` (Phase 2c). This persists:
       - `{sport}_production_replay_runs`
       - `{sport}_production_replay_outputs`
       - `{sport}_production_replay_cards`   (Phase 3 card builder)
  3. Report.

No card-builder mutations; runs the existing universal SSOT path.
Single date per subprocess invocation if called externally — but for
small contiguous ranges (≤6 dates) we run them in one process here
because:
  - pod cgroup limit is 8 GB
  - single-thread guard caps RSS at ~3 GB
  - between dates we free memory by deleting `MLBHighFrictionModel`
    refs and gc.collect().
"""
from __future__ import annotations
import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault("XGBOOST_N_THREADS", "1")

import argparse
import asyncio
import gc
import json
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

import psutil
from motor.motor_asyncio import AsyncIOMotorClient

from services.replay.mlb_replay_engine import (
    replay_date as mlb_layer3_replay_date,
    OUT_COLL as MLB_LAYER3_OUT_COLL,
    STATUS_COLL as MLB_LAYER3_STATUS_COLL,
    SOURCE_VERSION as MLB_LAYER3_SRC_V,
)
from services.replay.production_replay_runner import run_production_replay


def _rss_mb(): return psutil.Process().memory_info().rss / (1024 * 1024)
def _cgroup_gb():
    try:
        with open("/sys/fs/cgroup/memory.current") as f:
            return int(f.read().strip()) / 1024 / 1024 / 1024
    except Exception:
        return None


async def _ensure_layer3(db, date: str, snapshot_iso: str) -> Dict[str, Any]:
    sv_doc = await db[MLB_LAYER3_OUT_COLL].find_one(
        {"game_date": date}, {"_id": 0, "source_version": 1})
    current_sv = (sv_doc or {}).get("source_version")
    if current_sv == MLB_LAYER3_SRC_V:
        n = await db[MLB_LAYER3_OUT_COLL].count_documents({"game_date": date})
        return {"action": "kept", "rows": n, "source_version": current_sv}
    # Need rebuild. Purge prior partial rows + status.
    await db[MLB_LAYER3_OUT_COLL].delete_many({"game_date": date})
    await db[MLB_LAYER3_STATUS_COLL].delete_many({"game_date": date})
    summary = await mlb_layer3_replay_date(
        db, date, snapshot_iso=snapshot_iso, force=True,
        mem_limit_mb=5000,
    )
    return {"action": "rebuilt", **summary, "source_version": MLB_LAYER3_SRC_V}


async def run_for_date(db, *, date: str, sport: str) -> Dict[str, Any]:
    snapshot = f"{date}T11:00:00Z"
    t0 = time.time()
    rss0 = _rss_mb(); cg0 = _cgroup_gb()
    print(f"\n┌──── {date}  start  rss={rss0:.0f}MB  cgroup={cg0:.2f}GB ────")

    # Layer-3
    l3 = await _ensure_layer3(db, date, snapshot)
    print(f"│ Layer-3: action={l3['action']}  rows={l3.get('rows') or l3.get('rows_completed') or '?'}  sv={l3.get('source_version','?')}")

    # Phase 2c (writes outputs + cards)
    # Purge prior phase2c outputs/cards for clean idempotent rewrite
    pri = await db.mlb_production_replay_outputs.delete_many({"game_date": date})
    print(f"│ purged prior phase2c outputs: {pri.deleted_count}")
    summary = await run_production_replay(
        db, sport=sport, game_date=date, snapshot_iso=snapshot,
        tier="war_zone", dry_run=False, force_layer3=False,
        notes="ssot_replay_multidate_2026_05_17",
    )

    # Streaming aggregates per date for the report
    # μ stats (total_bases) — cheap sanity
    agg = db.mlb_production_replay_outputs.aggregate([
        {"$match": {"replay_serial": summary["serial"],
                     "stat_family": "total_bases"}},
        {"$group": {"_id": None, "n": {"$sum": 1},
                      "max_mu": {"$max": "$projection_mu"},
                      "n_gt_4p5": {"$sum": {"$cond": [
                          {"$gt": ["$projection_mu", 4.5]}, 1, 0]}}}},
    ])
    mu_stats = {}
    async for r in agg: mu_stats = {k: v for k, v in r.items() if k != "_id"}

    # Cards by tier / stat family / odds bucket
    cards = []
    async for c in db.mlb_production_replay_cards.find(
        {"replay_serial": summary["serial"]}, projection={"_id": 0}
    ).sort("rank", 1):
        cards.append(c)

    # By-tier (currently just war_zone — but generic)
    by_tier: Dict[str, Dict[str, Any]] = {}
    by_stat: Dict[str, Dict[str, Any]] = {}
    by_odds: Dict[str, Dict[str, Any]] = {}
    for c in cards:
        for d, key in (
            (by_tier, c.get("tier")),
            (by_stat, c.get("stat_family")),
            (by_odds, _odds_bucket(c.get("odds"))),
        ):
            entry = d.setdefault(key or "_unknown",
                                   {"n": 0, "wins": 0, "losses": 0,
                                    "pushes": 0, "ungraded": 0,
                                    "stake": 0.0, "profit": 0.0})
            entry["n"] += 1
            st = (c.get("grade_status") or "ungraded").lower()
            if st == "win":     entry["wins"] += 1
            elif st == "loss":  entry["losses"] += 1
            elif st == "push":  entry["pushes"] += 1
            else:               entry["ungraded"] += 1
            entry["stake"] += float(c.get("stake_units") or 0)
            entry["profit"] += float(c.get("profit_units") or 0)

    def _enrich(group):
        for k, e in group.items():
            decided = e["wins"] + e["losses"]
            e["hit_rate_pct"] = round(100.0 * e["wins"] / decided, 2) if decided else 0.0
            e["roi_pct"] = round(100.0 * e["profit"] / e["stake"], 2) if e["stake"] else 0.0
        return group

    by_tier = _enrich(by_tier); by_stat = _enrich(by_stat); by_odds = _enrich(by_odds)

    elapsed = time.time() - t0
    rss_end = _rss_mb()
    print(f"│ Phase 2c: serial={summary['serial']}  cards={summary['cards_displayed']}  "
          f"qualified={summary['rows_qualified']}  W/L/P={summary['wins']}/{summary['losses']}/{summary['pushes']}  "
          f"HR={summary['hit_rate_pct']}%  ROI={summary['roi_pct']}%")
    print(f"│ μ stats TB: {mu_stats}")
    print(f"└──── {date}  elapsed={elapsed:.1f}s  rss_end={rss_end:.0f}MB  "
          f"cgroup={_cgroup_gb():.2f}GB")
    gc.collect()
    return {
        "date": date,
        "phase2c": summary,
        "mu_stats_total_bases": mu_stats,
        "cards": cards,
        "by_tier": by_tier,
        "by_stat": by_stat,
        "by_odds": by_odds,
        "elapsed_s": round(elapsed, 1),
        "rss_end_mb": round(rss_end, 0),
    }


def _odds_bucket(o):
    if o is None: return "_unknown"
    o = int(o)
    if o >= 200: return "plus_high"
    if 100 <= o < 200: return "plus_med"
    if 0 < o < 100: return "plus_low"
    if o == 100: return "even"
    if -110 < o < 0: return "minus_low"
    if -150 < o <= -110: return "minus_med"
    if -250 < o <= -150: return "minus_heavy"
    return "minus_xx"


async def main(dates: List[str], sport: str) -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    results = []
    for d in dates:
        r = await run_for_date(db, date=d, sport=sport)
        results.append(r)

    # ── Consolidated report ─────────────────────────────────────────
    print("\n\n" + "=" * 110)
    print(f"  CONSOLIDATED REPLAY REPORT  ({sport.upper()})  dates: {', '.join(dates)}")
    print("=" * 110)
    print(f"{'date':>12}  {'serial':<46}  {'cards':>6}  {'W':>4} {'L':>4} {'P':>4} "
          f"{'U':>4}  {'HR':>7}  {'ROI':>7}  {'P&L_$1':>8}")
    grand = {"cards": 0, "w": 0, "l": 0, "p": 0, "u": 0,
              "stake": 0.0, "profit": 0.0}
    for r in results:
        s = r["phase2c"]; cards = r["cards"]
        w = sum(1 for c in cards if c.get("grade_status") == "win")
        l = sum(1 for c in cards if c.get("grade_status") == "loss")
        pu = sum(1 for c in cards if c.get("grade_status") == "push")
        un = sum(1 for c in cards if c.get("grade_status") not in ("win","loss","push"))
        stake = sum(float(c.get("stake_units") or 0) for c in cards)
        profit = sum(float(c.get("profit_units") or 0) for c in cards)
        dec = w + l
        hr = (100*w/dec) if dec else 0.0
        roi = (100*profit/stake) if stake else 0.0
        print(f"{r['date']:>12}  {s['serial']:<46}  {len(cards):>6}  "
              f"{w:>4} {l:>4} {pu:>4} {un:>4}  {hr:>6.2f}%  {roi:>+6.2f}%  "
              f"{profit:>+8.2f}")
        grand["cards"] += len(cards); grand["w"] += w; grand["l"] += l
        grand["p"] += pu; grand["u"] += un
        grand["stake"] += stake; grand["profit"] += profit
    dec_g = grand["w"] + grand["l"]
    hr_g = (100*grand["w"]/dec_g) if dec_g else 0.0
    roi_g = (100*grand["profit"]/grand["stake"]) if grand["stake"] else 0.0
    print("-"*110)
    print(f"{'TOTAL':>12}  {'':46}  {grand['cards']:>6}  "
          f"{grand['w']:>4} {grand['l']:>4} {grand['p']:>4} {grand['u']:>4}  "
          f"{hr_g:>6.2f}%  {roi_g:>+6.2f}%  {grand['profit']:>+8.2f}")

    # By-tier / by-stat / by-odds aggregated across all dates
    print("\n──── BY STAT FAMILY (aggregated)")
    print(f"  {'stat':>20}  {'n':>4}  {'W':>3} {'L':>3} {'U':>3}  {'HR':>7}  {'ROI':>8}  {'P&L':>7}")
    agg_stat: Dict[str, Dict[str, Any]] = {}
    for r in results:
        for k, v in r["by_stat"].items():
            e = agg_stat.setdefault(k, {"n":0,"wins":0,"losses":0,"pushes":0,
                                          "ungraded":0,"stake":0.0,"profit":0.0})
            for fld in ("n","wins","losses","pushes","ungraded","stake","profit"):
                e[fld] += v.get(fld, 0)
    for k, e in sorted(agg_stat.items(), key=lambda kv: -kv[1]["n"]):
        dec = e["wins"] + e["losses"]
        hr = (100*e["wins"]/dec) if dec else 0
        roi = (100*e["profit"]/e["stake"]) if e["stake"] else 0
        print(f"  {k:>20}  {e['n']:>4}  {e['wins']:>3} {e['losses']:>3} {e['ungraded']:>3}  "
              f"{hr:>6.2f}%  {roi:>+7.2f}%  {e['profit']:>+7.2f}")

    print("\n──── BY ODDS BUCKET (aggregated)")
    print(f"  {'bucket':>14}  {'n':>4}  {'W':>3} {'L':>3}  {'HR':>7}  {'ROI':>8}  {'P&L':>7}")
    agg_odds: Dict[str, Dict[str, Any]] = {}
    for r in results:
        for k, v in r["by_odds"].items():
            e = agg_odds.setdefault(k, {"n":0,"wins":0,"losses":0,"stake":0.0,"profit":0.0})
            for fld in ("n","wins","losses","stake","profit"):
                e[fld] += v.get(fld, 0)
    for k in ("plus_high","plus_med","plus_low","even","minus_low",
               "minus_med","minus_heavy","minus_xx","_unknown"):
        if k not in agg_odds: continue
        e = agg_odds[k]
        dec = e["wins"] + e["losses"]
        hr = (100*e["wins"]/dec) if dec else 0
        roi = (100*e["profit"]/e["stake"]) if e["stake"] else 0
        print(f"  {k:>14}  {e['n']:>4}  {e['wins']:>3} {e['losses']:>3}  "
              f"{hr:>6.2f}%  {roi:>+7.2f}%  {e['profit']:>+7.2f}")

    # Top 20 displayed cards across all dates (by edge)
    print(f"\n──── TOP 20 DISPLAYED CARDS (all dates, by edge)")
    all_cards = []
    for r in results:
        all_cards.extend(r["cards"])
    all_cards.sort(key=lambda c: -float(c.get("edge") or 0))
    print(f"  {'#':>3}  {'date':>10}  {'player':>22}  {'stat':<14}  {'L/side':<12}  "
          f"{'book':>14}{'@odds':>6}  {'edge':>6}  grade  {'P&L':>7}")
    for i, c in enumerate(all_cards[:20], 1):
        # Look up date from cards collection's parent — already attached via serial
        date = next((r["date"] for r in results
                       if any(cc["replay_serial"] == c["replay_serial"]
                                and cc["rank"] == c["rank"] for cc in r["cards"])),
                      "?")
        print(f"  {i:>3}  {date:>10}  "
              f"{(c.get('player_name') or '')[:22]:>22}  "
              f"{(c.get('stat_family') or '')[:14]:<14}  "
              f"{str(c.get('line'))+'/'+(c.get('side') or ''):<12}  "
              f"{(c.get('book') or '')[:14]:>14}@{c.get('odds'):>5}  "
              f"{float(c.get('edge') or 0):>6.3f}  "
              f"{(c.get('grade_status') or '')[:5]:>5}  "
              f"{float(c.get('profit_units') or 0):>+7.2f}")

    # JSON artifact
    art_path = f"/app/backend/audits/ssot_replay_{dates[0]}_to_{dates[-1]}.json"
    with open(art_path, "w") as fh:
        # Strip the heavy cards array in the per-date entry (already
        # persisted to Mongo); keep summary fields.
        light = []
        for r in results:
            light.append({k: v for k, v in r.items() if k != "cards"})
            light[-1]["cards_count"] = len(r["cards"])
        json.dump({"sport": sport, "dates": dates,
                    "grand": {"cards": grand["cards"], "wins": grand["w"],
                                 "losses": grand["l"], "pushes": grand["p"],
                                 "ungraded": grand["u"], "stake_units": round(grand["stake"], 4),
                                 "profit_units": round(grand["profit"], 4),
                                 "hit_rate_pct": round(hr_g, 2),
                                 "roi_pct": round(roi_g, 2)},
                    "per_date": light,
                    "by_stat": agg_stat, "by_odds": agg_odds},
                   fh, indent=2, default=str)
    print(f"\n[json] wrote {art_path}")

    client.close()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--sport", default="mlb")
    p.add_argument("--dates", nargs="+", required=True,
                    help="space-separated YYYY-MM-DD dates")
    args = p.parse_args()
    sys.exit(asyncio.run(main(args.dates, args.sport)))
