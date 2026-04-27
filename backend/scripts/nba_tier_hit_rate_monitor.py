"""
NBA Tier Hit-Rate Forward Monitor — 7-Day Window
=================================================
Tracks tier-level hit rates against the established post-cutover
baselines:

  Safe Haven   ≥ 88.5%   (no degradation expected after 100/0 cutover)
  Front Lines  ≥ 75.8%   (no degradation expected after 100/0 cutover)
  War Zone     ≥ 58.6%   (rescue from 22.2%)

Run daily. Persists results to `nba_tier_monitor` collection (one doc
per run, idempotent on `run_date`) so trend lines can be plotted later.

The script also splits each tier's outcomes into PRE-CUTOVER and
POST-CUTOVER buckets so we can see whether the live experience matches
the 272-pick simulation. Cutover date = `2026-04-29` (when env var
NBA_RATE_BLEND_MODE=100_0 went live).

Read-only on the production scoring path — only writes the monitor doc.
"""
import os
import asyncio
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")


CUTOVER_DATE_ISO = "2026-04-29"   # NBA_RATE_BLEND_MODE=100_0 promotion
TIER_BASELINES = {
    "safe_haven":  {"hit_pct": 88.5, "n_baseline": 78,  "label": "Safe Haven"},
    "front_lines": {"hit_pct": 75.8, "n_baseline": 95,  "label": "Front Lines"},
    "war_zone":    {"hit_pct": 58.6, "n_baseline": 99,  "label": "War Zone (post-cutover)"},
}
WINDOW_DAYS = 7


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db  = cli[os.environ["DB_NAME"]]

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=WINDOW_DAYS)
    print(f"[MONITOR] Window: last {WINDOW_DAYS} days  "
          f"(from {window_start.isoformat()} → {now.isoformat()})")

    cur = db.forward_test_outcomes.find(
        {"sport": "nba",
         "outcome": {"$in": ["hit", "miss"]},
         "resolved_at": {"$gte": window_start}},
    ).sort("resolved_at", -1)
    rows: List[Dict[str, Any]] = await cur.to_list(length=None)
    print(f"[MONITOR] Loaded {len(rows)} settled outcomes in window")

    # ----- Aggregate by tier (window-only) ----------------------------------
    by_tier: Dict[str, Dict[str, int]] = {
        t: {"n": 0, "hits": 0, "misses": 0,
            "n_pre": 0, "hits_pre": 0,
            "n_post": 0, "hits_post": 0}
        for t in TIER_BASELINES
    }
    for r in rows:
        tier = (r.get("tier") or "").lower()
        if tier not in by_tier: continue
        is_hit = r.get("outcome") == "hit"
        is_post = ((r.get("game_time") or "")[:10] >= CUTOVER_DATE_ISO)
        by_tier[tier]["n"] += 1
        if is_hit: by_tier[tier]["hits"] += 1
        else:      by_tier[tier]["misses"] += 1
        if is_post:
            by_tier[tier]["n_post"] += 1
            if is_hit: by_tier[tier]["hits_post"] += 1
        else:
            by_tier[tier]["n_pre"] += 1
            if is_hit: by_tier[tier]["hits_pre"] += 1

    # ----- Print report -----------------------------------------------------
    print()
    print("=" * 96)
    print(f"  NBA TIER HIT-RATE MONITOR  ({WINDOW_DAYS}-day window)")
    print("=" * 96)
    print(f"  Cutover (100/0 promotion): {CUTOVER_DATE_ISO}")
    print(f"  Baselines: SH ≥ 88.5%, FL ≥ 75.8%, WZ ≥ 58.6% (post-cutover)\n")
    print(f"  {'tier':14s} {'n':>4s} {'hits':>5s} {'hit%':>6s}  "
          f"{'pre':>10s}  {'post':>10s}  {'baseline':>9s}  {'status':10s}")
    print("  " + "-" * 90)

    summary = {"as_of": now.isoformat(),
               "cutover": CUTOVER_DATE_ISO,
               "window_days": WINDOW_DAYS,
               "tiers": {}}

    for tier, b in TIER_BASELINES.items():
        d = by_tier[tier]
        n  = d["n"]; h = d["hits"]
        hit_pct = (h / n * 100) if n > 0 else None
        pre_pct = (d["hits_pre"] / d["n_pre"] * 100) if d["n_pre"] > 0 else None
        post_pct = (d["hits_post"] / d["n_post"] * 100) if d["n_post"] > 0 else None

        if hit_pct is None:
            status = "no_data"
        elif n < 10:
            status = "low_n"
        elif hit_pct >= b["hit_pct"] - 2.0:
            status = "OK"
        elif hit_pct >= b["hit_pct"] - 5.0:
            status = "WARN"
        else:
            status = "ALERT"

        h_str = f"{hit_pct:>5.1f}%" if hit_pct is not None else "  —  "
        pre_str = (f"{pre_pct:>5.1f}% (n={d['n_pre']})"
                   if pre_pct is not None else f"  —    (n={d['n_pre']})")
        post_str = (f"{post_pct:>5.1f}% (n={d['n_post']})"
                    if post_pct is not None else f"  —    (n={d['n_post']})")
        print(f"  {b['label'][:14]:14s} {n:>4d} {h:>5d} {h_str:>6s}  "
              f"{pre_str:>10s}  {post_str:>10s}  "
              f"{b['hit_pct']:>8.1f}%  {status:10s}")

        summary["tiers"][tier] = {
            "n": n, "hits": h, "hit_pct": hit_pct,
            "n_pre": d["n_pre"], "hit_pct_pre": pre_pct,
            "n_post": d["n_post"], "hit_pct_post": post_pct,
            "baseline_hit_pct": b["hit_pct"],
            "status": status,
        }

    # ----- Persist (one doc per run_date for trend plotting) ----------------
    run_date = now.strftime("%Y-%m-%d")
    summary["run_date"] = run_date
    await db.nba_tier_monitor.update_one(
        {"run_date": run_date}, {"$set": summary}, upsert=True,
    )
    print(f"\n[MONITOR] Persisted to nba_tier_monitor (run_date={run_date})")
    print("[MONITOR] DONE")


if __name__ == "__main__":
    asyncio.run(main())
