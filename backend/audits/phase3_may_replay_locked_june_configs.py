"""PHASE-3 CROSS-REGIME VALIDATION — May 2026 replay of locked June 2025 niches.

NO optimization. NO threshold changes. NO grid search.
Apply EXACTLY these 3 locked configs to the May 2026 FL pool:

  1) ER_UNDER_DOG_MU       earned_runs UNDER [+101,+200]  mu_line >= 0.5
  2) RUNS_UNDER_HEAVY      runs        UNDER [-299,-200]  baseline (no filter)
  3) RUNS_UNDER_MID        runs        UNDER [-199,-150]  EDGE >= -5
"""
import asyncio, math, os, sys
from collections import defaultdict, Counter
from typing import Any, Dict, List, Optional
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

def _band(o):
    if o is None: return None
    o = int(o)
    if o <= -300: return "[-500,-300]"
    if o <= -200: return "[-299,-200]"
    if o <= -150: return "[-199,-150]"
    if o <= -110: return "[-149,-110]"
    if o <=  100: return "[-109,+100]"
    if o <=  200: return "[+101,+200]"
    return "[+201,+inf]"

def _edge_pp(v):
    if v is None: return None
    try: x = float(v)
    except: return None
    return x*100.0 if abs(x) < 1.5 else x

def _fold_may(d):
    if d <= "2026-05-06": return "A: 05-03..06"
    if d <= "2026-05-10": return "B: 05-07..10"
    return "C: 05-11..15"


def _agg(rows):
    g = [r for r in rows if r["grade"] in ("win","loss","push")]
    w = sum(1 for r in g if r["grade"]=="win")
    l = sum(1 for r in g if r["grade"]=="loss")
    pnl = sum(r["pnl"] for r in g); stk = sum(r["stake"] for r in g)
    hr = (100*w/(w+l)) if (w+l) else None
    roi = (100*pnl/stk) if stk else None
    rets = [r["pnl"]/r["stake"] for r in g if r["stake"]]
    roi_lo = roi_hi = None
    if rets:
        n=len(rets); m=sum(rets)/n
        v=sum((x-m)**2 for x in rets)/max(n-1,1)
        se=math.sqrt(v/n)
        roi_lo = round(100*(m-1.96*se),2); roi_hi=round(100*(m+1.96*se),2)
    by_d = defaultdict(list)
    for r in g: by_d[r["game_date"]].append(r)
    daily = []
    for d in sorted(by_d.keys()):
        dp = sum(r["pnl"] for r in by_d[d])
        ds = sum(r["stake"] for r in by_d[d])
        daily.append((d, len(by_d[d]), dp,
                      (100*dp/ds) if ds else None))
    pos = sum(1 for _,_,_,r in daily if (r or 0) >= 0)
    cons = round(pos/len(daily),3) if daily else None
    mwd = max(daily, key=lambda t: t[2]) if daily else None
    mld = min(daily, key=lambda t: t[2]) if daily else None
    return {"n":len(rows),"gr":len(g),"w":w,"l":l,
            "hr":round(hr,2) if hr is not None else None,
            "roi":round(roi,2) if roi is not None else None,
            "roi_lo":roi_lo,"roi_hi":roi_hi,
            "pnl":round(pnl,3),"stake":round(stk,3),
            "consist":cons,"grd_days":len(daily),
            "max_win_day": (f"{mwd[0]}/{mwd[1]}p/+{mwd[2]:.2f}u" if mwd else "-"),
            "max_loss_day": (f"{mld[0]}/{mld[1]}p/{mld[2]:+.2f}u" if mld else "-"),
            "daily_pnl": daily}


async def fetch_band(db, *, family, side, band, serial_prefix, date_range):
    rows = []
    async for d in db.mlb_test_outputs.find(
        {"replay_serial":{"$regex":serial_prefix},
         "stat_family":family,"routed_tier":"front_lines","side":side},
        projection={"_id":0}):
        odds = d.get("odds")
        if odds is None: continue
        if _band(odds) != band: continue
        gd = d.get("game_date") or ""
        if not (date_range[0] <= gd <= date_range[1]): continue
        line = d.get("line"); mu = d.get("projection_mu")
        mu_gap = (mu - line) if (mu is not None and line is not None) else None
        rows.append({
            "odds":odds, "cv":d.get("cv"),
            "edge_pp":_edge_pp(d.get("edge")),
            "hr_l20":d.get("hit_rate_l20"),
            "tp":d.get("tp"),
            "mu_gap":mu_gap,
            "grade":d.get("grade_status") or "not_qualified",
            "pnl":d.get("profit_units") or 0.0,
            "stake":d.get("stake_units") or 0.0,
            "game_date":gd,
            "player_name":d.get("player_name"),
            "player_norm":d.get("player_name_normalized"),
            "event_id":d.get("event_id"),
            "home_team":d.get("home_team"),
            "away_team":d.get("away_team"),
            "actual_value":d.get("actual_value"),
            "line":line, "mu":mu,
            "snapshot_iso":d.get("snapshot_iso"),
            "commence_time":d.get("commence_time"),
            "fair_p":d.get("fair_probability"),
            "book":d.get("book"),
            "side":d.get("side"),
        })
    return rows


def _audit(rows, label, *, side):
    print(f"\n  ▶ AUDIT — {label}")
    graded = [r for r in rows if r["grade"] in ("win","loss","push")]
    # Grade verification
    wg = []
    for r in graded:
        av = r["actual_value"]; line = r["line"]
        if av is None or line is None: continue
        if side == "OVER":
            exp = "push" if av == line else ("win" if av > line else "loss")
        else:
            exp = "push" if av == line else ("win" if av < line else "loss")
        if r["grade"] != exp: wg.append(r["player_name"])
    print(f"    Mis-graded: {len(wg)}")
    # Duplicates
    keyf = lambda r: (r["event_id"], r["player_norm"], r["side"], r["line"])
    dc = Counter(keyf(r) for r in rows)
    dups = [k for k,v in dc.items() if v>1]
    print(f"    Duplicate (event,player,side,line) keys: {len(dups)}")
    # Lookahead
    bs = [r for r in rows if r["snapshot_iso"] and r["commence_time"]
            and r["snapshot_iso"] >= r["commence_time"]]
    print(f"    Lookahead snapshots: {len(bs)}")
    # fair_p
    fp = [r["fair_p"] for r in rows if r["fair_p"] is not None]
    if fp:
        m = sum(fp)/len(fp); hi = sum(1 for p in fp if p>0.95)
        print(f"    Mean fair_p: {m:.3f}  fair_p>0.95: {hi}/{len(fp)}")
    # Player / team concentration
    pl = Counter(r["player_norm"] for r in rows)
    th = Counter(r["home_team"] for r in rows)
    top_p = pl.most_common(3); top_t = th.most_common(3)
    print(f"    Distinct players: {len(pl)}   top: {top_p}")
    print(f"    Distinct home teams: {len(th)}   top: {top_t}")


def _decompose(rows, label):
    print(f"\n  ▶ STRUCTURAL DECOMPOSITION — {label}")
    # Odds micro-band (5-step buckets)
    print("    Odds micro-bucket breakdown:")
    odds_sorted = sorted(set(int(r["odds"]) for r in rows))
    if odds_sorted:
        lo_o, hi_o = odds_sorted[0], odds_sorted[-1]
        # Use 20-unit buckets
        step = 20
        buckets = defaultdict(list)
        for r in rows:
            o = int(r["odds"]); k = (o // step) * step
            buckets[k].append(r)
        for k in sorted(buckets.keys()):
            a = _agg(buckets[k])
            if (a["gr"] or 0) == 0: continue
            lab = f"[{k},{k+step-1}]"
            print(f"      {lab:<14s}  n={a['n']:>3d}/{a['gr']:>3d}  "
                  f"HR={str(a['hr']):>5s}%  ROI={str(a['roi']):>6s}%  "
                  f"P&L={a['pnl']:+.2f}u")
    # TP
    print("\n    TP bucket breakdown:")
    tp_b = [(None,50),(50,55),(55,60),(60,65),(65,70),(70,75),(75,100)]
    for lo,hi in tp_b:
        s = [r for r in rows if r["tp"] is not None
             and (lo is None or r["tp"] >= lo) and r["tp"] < hi]
        a = _agg(s)
        if (a["gr"] or 0) == 0: continue
        lab = f"TP [{'-' if lo is None else lo},{hi})"
        print(f"      {lab:<16s}  n={a['n']:>3d}/{a['gr']:>3d}  "
              f"HR={str(a['hr']):>5s}%  ROI={str(a['roi']):>6s}%  "
              f"P&L={a['pnl']:+.2f}u")
    # EDGE
    print("\n    EDGE bucket breakdown:")
    e_b = [(-99,-10),(-10,-5),(-5,0),(0,5),(5,10),(10,20),(20,99)]
    for lo,hi in e_b:
        s = [r for r in rows if r["edge_pp"] is not None
             and lo <= r["edge_pp"] < hi]
        a = _agg(s)
        if (a["gr"] or 0) == 0: continue
        print(f"      EDG [{lo:>+5.1f},{hi:>+5.1f})  n={a['n']:>3d}/{a['gr']:>3d}  "
              f"HR={str(a['hr']):>5s}%  ROI={str(a['roi']):>6s}%  "
              f"P&L={a['pnl']:+.2f}u")
    # mu
    print("\n    μ-line bucket breakdown:")
    mu_b = [(-99,-1.0),(-1.0,-0.5),(-0.5,0.0),(0.0,0.5),(0.5,1.0),(1.0,1.5),(1.5,99)]
    for lo,hi in mu_b:
        s = [r for r in rows if r["mu_gap"] is not None
             and lo <= r["mu_gap"] < hi]
        a = _agg(s)
        if (a["gr"] or 0) == 0: continue
        print(f"      μ [{lo:>+5.2f},{hi:>+5.2f})  n={a['n']:>3d}/{a['gr']:>3d}  "
              f"HR={str(a['hr']):>5s}%  ROI={str(a['roi']):>6s}%  "
              f"P&L={a['pnl']:+.2f}u")


async def replay_target(db, *, name, family, side, band, filt,
                          filter_label, june_roi=None):
    print("\n\n" + "═"*120)
    print(f"  TARGET — {name}")
    print(f"  family={family}  side={side}  band={band}  filter={filter_label}")
    print("═"*120)
    raw = await fetch_band(db, family=family, side=side, band=band,
                            serial_prefix="^GSS-MLB-202605..-FRON-POOL$",
                            date_range=("2026-05-03","2026-05-15"))
    rows = [r for r in raw if filt(r)]
    a = _agg(rows)
    n_days_g = a["grd_days"]
    print(f"\n  HEADLINE (May 2026):")
    print(f"    raw band pool: n={len(raw)}  after filter: n={a['n']}/{a['gr']}")
    print(f"    W/L: {a['w']}/{a['l']}")
    print(f"    HR: {a['hr']}%   ROI: {a['roi']}%  CI=[{a['roi_lo']},{a['roi_hi']}]")
    print(f"    P&L: {a['pnl']}u    Stake: {a['stake']}u")
    print(f"    Avg picks/day (graded): "
          f"{a['gr']/max(n_days_g,1):.2f}")
    print(f"    Daily consistency: {a['consist']} ({n_days_g}d)")
    print(f"    Max-win day:  {a['max_win_day']}")
    print(f"    Max-loss day: {a['max_loss_day']}")

    # Day concentration
    daily = a["daily_pnl"]
    tot = a["pnl"]
    daily_sorted = sorted(daily, key=lambda t: t[2], reverse=True)
    if daily_sorted and tot:
        t1 = daily_sorted[0]; t3 = sum(t[2] for t in daily_sorted[:3])
        print(f"\n  DAY CONCENTRATION:")
        print(f"    Top-1 day: {t1[0]}  +{t1[2]:.2f}u "
              f"({100*t1[2]/tot:.1f}% of P&L)")
        print(f"    Top-3 days: +{t3:.2f}u ({100*t3/tot:.1f}% of P&L)")
        print(f"    Best day:  {daily_sorted[0][0]} +{daily_sorted[0][2]:.2f}u")
        print(f"    Worst day: {daily_sorted[-1][0]} {daily_sorted[-1][2]:+.2f}u")

    # 3-fold
    print(f"\n  3-FOLD ROBUSTNESS (May 2026):")
    for fold in ("A: 05-03..06","B: 05-07..10","C: 05-11..15"):
        sub = [r for r in rows if _fold_may(r["game_date"]) == fold]
        af = _agg(sub)
        if af["gr"] == 0:
            print(f"    {fold:<16s}  (no graded)")
            continue
        ci = (f"[{af['roi_lo']},{af['roi_hi']}]"
              if af["roi_lo"] is not None else "-")
        print(f"    {fold:<16s}  n={af['n']:>4d} gr={af['gr']:>4d} "
              f"W={af['w']:>3d} L={af['l']:>3d}  "
              f"HR={str(af['hr']):>5s}%  ROI={str(af['roi']):>6s}%  "
              f"CI={ci[:14]:<14s}  P&L={af['pnl']:>+7.2f}u  "
              f"consist={af['consist']}")

    # Audit
    _audit(rows, name, side=side)
    # Structural
    _decompose(rows, name)

    return a["roi"], a["pnl"], a["gr"]


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    print("═"*120)
    print("  PHASE-3 CROSS-REGIME VALIDATION — May 2026 replay of June 2025 locked configs")
    print("═"*120)

    results = {}

    results["ER_UNDER_DOG_MU"] = await replay_target(db,
        name="ER_UNDER_DOG_MU",
        family="earned_runs", side="UNDER", band="[+101,+200]",
        filt=lambda r: (r["mu_gap"] is not None and r["mu_gap"] >= 0.5),
        filter_label="mu_line >= 0.5",
        june_roi=31.70)

    results["RUNS_UNDER_HEAVY"] = await replay_target(db,
        name="RUNS_UNDER_HEAVY",
        family="runs", side="UNDER", band="[-299,-200]",
        filt=lambda r: True,
        filter_label="baseline (no filter)",
        june_roi=13.99)

    results["RUNS_UNDER_MID"] = await replay_target(db,
        name="RUNS_UNDER_MID",
        family="runs", side="UNDER", band="[-199,-150]",
        filt=lambda r: (r["edge_pp"] is not None and r["edge_pp"] >= -5.0),
        filter_label="EDGE >= -5",
        june_roi=21.76)

    c.close()

    print("\n\n" + "═"*120)
    print("  FINAL CROSS-REGIME COMPARISON")
    print("═"*120)
    print(f"  {'Target':<22s} {'June 2025':>12s} {'May 2026':>12s} "
          f"{'May gr':>8s} {'Verdict':<22s}")
    JUNE = {"ER_UNDER_DOG_MU": 31.70,
             "RUNS_UNDER_HEAVY": 13.99,
             "RUNS_UNDER_MID": 21.76}
    for name, (may_roi, may_pnl, may_gr) in results.items():
        june = JUNE[name]
        if may_roi is None:
            verdict = "INSUFFICIENT DATA"
        elif may_gr < 20:
            verdict = "INSUFFICIENT SAMPLE"
        elif may_roi > 0 and june > 0:
            if abs(may_roi - june) < 15:
                verdict = "🟢 DURABLE"
            else:
                verdict = "🟡 PARTIAL"
        elif may_roi > 5 and june > 5:
            verdict = "🟢 DURABLE"
        elif may_roi > 0 and june > 0:
            verdict = "🟡 PARTIAL"
        elif may_roi < 0 and june < 0:
            verdict = "🔴 FAILED both"
        else:
            verdict = "🔴 REGIME-DEPENDENT"
        print(f"  {name:<22s} {june:>+11.2f}% {(may_roi or 0):>+11.2f}% "
              f"{may_gr:>8d}  {verdict:<22s}  P&L_may={may_pnl:+.2f}u")


asyncio.run(main())
