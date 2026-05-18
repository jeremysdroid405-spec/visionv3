"""Production-path SH Volume-First validation.

Runs `run_pipeline(tier='safe_haven')` on each of 13 days
(2026-05-03 → 2026-05-15) WITHOUT any audit override kwargs. The
production gate stack does ALL filtering. Then aggregates the
resulting `mlb_test_outputs.gate_pass=True` rows and validates them
against the audit target.

Verifies:
  1. Overall replay totals (graded, W/L, HR, ROI, P&L)
  2. Per-family breakdown (hits, pitcher_strikeouts)
  3. Blocked families have ZERO gate_pass=True rows in SH
  4. No gate_pass=True row has odds ≤ -500 (odds_floor_gate works)
  5. Front Lines / War Zone thresholds unchanged
"""
from __future__ import annotations
import asyncio
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient
from services.pipeline import run_pipeline


DATES = [(datetime(2026, 5, 3) + timedelta(days=i)).strftime("%Y-%m-%d")
         for i in range(13)]
TARGET = {"families": ("hits", "pitcher_strikeouts")}
BLOCKED = ("earned_runs", "batter_strikeouts", "total_bases", "runs",
           "rbis", "hits_runs_rbis", "pitching_outs")
AUDIT_TARGET = dict(graded=43, wins=40, losses=3, hr=93.0, roi=18.05,
                    profit=7.76)


def _safe(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _agg(rows):
    w = l = p = u = 0
    stake = profit = 0.0
    for r in rows:
        st = r.get("grade_status")
        if st == "win":
            w += 1
            stake += 1.0
            profit += float(r.get("profit_units") or 0)
        elif st == "loss":
            l += 1
            stake += 1.0
            profit -= 1.0
        elif st == "push":
            p += 1
            stake += 1.0
        else:
            u += 1
    dec = w + l
    return dict(
        n=len(rows), graded=dec + p, ungraded=u, wins=w, losses=l, pushes=p,
        hr=(100.0 * w / dec) if dec else None,
        roi=(100.0 * profit / stake) if stake else None,
        profit=round(profit, 4),
    )


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    print("=" * 100)
    print("  PRODUCTION-PATH SH VOLUME-FIRST VALIDATION")
    print("  run_pipeline(tier='safe_haven') with NO override kwargs")
    print(f"  Window: {DATES[0]} → {DATES[-1]}  ({len(DATES)} days)")
    print("=" * 100)

    serials = []
    per_day_log = []
    for d in DATES:
        print(f"\n[run_pipeline] {d}T11:00:00Z …", flush=True)
        try:
            summary = await run_pipeline(
                db, sport="mlb", mode="historical",
                snapshot_time=f"{d}T11:00:00Z",
                output_namespace="test",
                test_id=f"PROD-VFP-{d.replace('-','')}",
                tier="safe_haven",
                notes="Volume-First production-path validation 2026-05-18",
            )
            serial = summary["serial"]
            serials.append(serial)
            per_day_log.append((d, summary))
            print(f"  serial={serial}  qual={summary.get('rows_qualified')}  "
                  f"W={summary.get('wins')}  L={summary.get('losses')}  "
                  f"P={summary.get('pushes')}  ungr={summary.get('ungraded')}",
                  flush=True)
        except Exception as exc:
            print(f"  ERROR: {exc!r}", flush=True)

    if not serials:
        print("\n[FATAL] No serials. Aborting validation.")
        return

    # ── Section 1 — totals ──
    rows = await db["mlb_test_outputs"].find(
        {"replay_serial": {"$in": serials}, "gate_pass": True},
        projection={"_id": 0},
    ).to_list(100000)
    a = _agg(rows)
    print("\n" + "=" * 100)
    print("  [1] REPLAY TOTALS  (gate_pass=True, ALL families)")
    print("=" * 100)
    hr = f"{a['hr']:.2f}%" if a['hr'] is not None else "—"
    roi = f"{a['roi']:+.2f}%" if a['roi'] is not None else "—"
    print(f"  n={a['n']}  graded={a['graded']}  W={a['wins']}/L={a['losses']} P={a['pushes']} U={a['ungraded']}")
    print(f"  HR={hr}   ROI={roi}   P&L={a['profit']:+.3f}u")

    print("\n  Audit target:  graded=43  W=40/L=3  HR=93.00%  ROI=+18.05%  P&L=+7.76u")

    # Score parity
    def _fmt(v, kind):
        if kind == "int":
            return f"{int(round(v))}"
        if kind == "pct":
            return f"{v:+.2f}" if v >= 0 else f"{v:.2f}"
        if kind == "pct_abs":
            return f"{v:.2f}"
        return f"{v:+.3f}" if abs(v) >= 0 else f"{v:.3f}"
    parity = [
        ("graded", a["graded"], AUDIT_TARGET["graded"], "int"),
        ("wins",   a["wins"],   AUDIT_TARGET["wins"],   "int"),
        ("losses", a["losses"], AUDIT_TARGET["losses"], "int"),
        ("HR%",    a["hr"] or 0, AUDIT_TARGET["hr"],   "pct_abs"),
        ("ROI%",   a["roi"] or 0, AUDIT_TARGET["roi"], "pct"),
        ("P&L",    a["profit"], AUDIT_TARGET["profit"],"pnl"),
    ]
    print("\n  PARITY CHECK")
    for name, prod_val, target_val, kind in parity:
        diff = prod_val - target_val
        prod_s   = _fmt(prod_val, kind)
        target_s = _fmt(target_val, kind)
        diff_s   = _fmt(diff, kind if kind != "pct_abs" else "pct")
        print(f"  {name:<8s}  prod={prod_s:>10s}  target={target_s:>10s}  Δ={diff_s:>+10s}")

    # ── Section 2 — per-family ──
    print("\n" + "=" * 100)
    print("  [2] PER-FAMILY BREAKDOWN  (gate_pass=True)")
    print("=" * 100)
    by_fam = defaultdict(list)
    for r in rows:
        by_fam[r.get("stat_family")].append(r)
    for fam in sorted(by_fam):
        x = _agg(by_fam[fam])
        hr = f"{x['hr']:.2f}%" if x['hr'] is not None else "—"
        roi = f"{x['roi']:+.2f}%" if x['roi'] is not None else "—"
        print(f"  {fam:<24s} n={x['n']:>3d} grd={x['graded']:>3d} "
              f"W={x['wins']:>3d} L={x['losses']:>2d}  HR={hr:>8s}  "
              f"ROI={roi:>9s}  P&L={x['profit']:+.3f}u")

    # ── Section 3 — blocked-family verification ──
    print("\n" + "=" * 100)
    print("  [3] BLOCKED-FAMILY VERIFICATION  (must all = 0)")
    print("=" * 100)
    for fam in BLOCKED:
        cnt = await db["mlb_test_outputs"].count_documents({
            "replay_serial": {"$in": serials},
            "stat_family": fam,
            "gate_pass": True,
        })
        ok = "✓" if cnt == 0 else "✗"
        print(f"  {ok}  {fam:<24s}  gate_pass=True count = {cnt}")

    # ── Section 4 — odds-floor verification ──
    print("\n" + "=" * 100)
    print("  [4] ODDS-FLOOR GATE VERIFICATION")
    print("=" * 100)
    deep = await db["mlb_test_outputs"].count_documents({
        "replay_serial": {"$in": serials},
        "gate_pass": True,
        "tier_reference_odds": {"$lte": -500},
    })
    ok = "✓" if deep == 0 else "✗"
    print(f"  {ok}  gate_pass=True rows with odds ≤ -500: {deep}  (must be 0)")
    in_band = await db["mlb_test_outputs"].count_documents({
        "replay_serial": {"$in": serials},
        "gate_pass": True,
        "tier_reference_odds": {"$gt": -500, "$lte": -300},
    })
    print(f"     gate_pass=True rows in -300..-499 band:  {in_band}")
    over = await db["mlb_test_outputs"].count_documents({
        "replay_serial": {"$in": serials},
        "gate_pass": True,
        "tier_reference_odds": {"$gt": -300},
    })
    ok = "✓" if over == 0 else "✗"
    print(f"  {ok}  gate_pass=True rows with odds > -300: {over}  (bucket router should prevent)")

    # ── Section 5 — gate-failure waterfall on SH-routed rows ──
    print("\n" + "=" * 100)
    print("  [5] GATE-FAILURE WATERFALL  (SH-routed, gate_pass=False)")
    print("=" * 100)
    failed_ct = Counter()
    sh_routed = await db["mlb_test_outputs"].count_documents({
        "replay_serial": {"$in": serials}, "routed_tier": "safe_haven"})
    sh_pass = await db["mlb_test_outputs"].count_documents({
        "replay_serial": {"$in": serials}, "routed_tier": "safe_haven",
        "gate_pass": True})
    async for r in db["mlb_test_outputs"].find({
        "replay_serial": {"$in": serials},
        "routed_tier": "safe_haven",
        "gate_pass": False,
    }, projection={"_id": 0, "failed_gates": 1, "stat_family": 1}):
        for g in (r.get("failed_gates") or []):
            failed_ct[g] += 1
    print(f"  SH-routed rows: {sh_routed} · gate_pass=True: {sh_pass} "
          f"· gate_pass=False: {sh_routed - sh_pass}")
    print(f"\n  Top gate rejections:")
    for g, n in failed_ct.most_common(10):
        print(f"    {g:<30s} {n:>5d}  ({100*n/sh_routed:.1f}% of SH pool)")

    # ── Section 6 — per-day breakdown ──
    print("\n" + "=" * 100)
    print("  [6] PER-DAY BREAKDOWN  (gate_pass=True only)")
    print("=" * 100)
    by_day = defaultdict(list)
    for r in rows:
        by_day[r.get("game_date") or "?"].append(r)
    days_pos = 0
    days_total = 0
    cum_pnl = 0.0
    for d in sorted(by_day):
        x = _agg(by_day[d])
        if x["graded"] == 0:
            continue
        days_total += 1
        if x["profit"] > 0:
            days_pos += 1
        cum_pnl += x["profit"]
        hr = f"{x['hr']:.1f}%" if x['hr'] is not None else "—"
        roi = f"{x['roi']:+.2f}%" if x['roi'] is not None else "—"
        print(f"  {d:<12s}  n={x['n']:>3d} grd={x['graded']:>3d}  "
              f"W={x['wins']:>2d} L={x['losses']:>2d}  HR={hr:>6s}  "
              f"ROI={roi:>9s}  P&L={x['profit']:>+7.3f}u  cum={cum_pnl:>+7.3f}u")
    if days_total:
        print(f"\n  Days with picks: {days_total}   Positive: {days_pos}   "
              f"daily_consistency = {days_pos/days_total:.3f}")

    # ── Section 7 — FL/WZ untouched check (count post-promotion outputs) ──
    print("\n" + "=" * 100)
    print("  [7] FRONT LINES / WAR ZONE UNTOUCHED (threshold-resolve audit)")
    print("=" * 100)
    from services.scoring.gates.thresholds import resolve_thresholds
    for tier in ("front_lines", "war_zone"):
        for fam in ("hits", "pitcher_strikeouts", "earned_runs",
                    "batter_strikeouts", "total_bases"):
            cfg = resolve_thresholds("mlb", tier, fam, side="OVER")
            of_min = cfg.get("odds_floor_gate", {}).get("min")
            hr_min = cfg["hit_rate_gate"]["min"]
            cv_max = cfg["cv_gate"]["max"]
            ok = "✓" if of_min is None else "✗"
            print(f"  {ok}  {tier:<12s} {fam:<22s}  hr_min={hr_min:>6.1f} "
                  f"cv_max={cv_max:>6.2f}  odds_floor={of_min}")

    print("\n" + "=" * 100)
    print(f"  Replay serials: {serials}")
    print("=" * 100)


if __name__ == "__main__":
    asyncio.run(main())
