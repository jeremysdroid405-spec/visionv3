"""PHASE-3 FORENSIC RE-AUDIT — pitcher_strikeouts OVER [+101,+200] EDG>=5 ∧ CV<=0.70
on the JUNE 2025 replay window.

LOCKED CONFIG. NO optimization. NO filter changes. Audit only.
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
    if   o <= -300: return "[-500,-300]"
    elif o <= -200: return "[-299,-200]"
    elif o <= -150: return "[-199,-150]"
    elif o <= -110: return "[-149,-110]"
    elif o <=  100: return "[-109,+100]"
    elif o <=  200: return "[+101,+200]"
    return "[+201,+inf]"

def _edge_pp(v):
    if v is None: return None
    try: x = float(v)
    except: return None
    return x*100.0 if abs(x) < 1.5 else x


def _fold(d):
    if d <= "2025-06-10": return "A: 06-01..10"
    if d <= "2025-06-20": return "B: 06-11..20"
    return "C: 06-21..30"


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
        rs = by_d[d]
        dp = sum(r["pnl"] for r in rs)
        ds = sum(r["stake"] for r in rs)
        droi = (100*dp/ds) if ds else None
        daily.append((d, len(rs), dp, droi,
                       sum(1 for r in rs if r["grade"]=="win"),
                       sum(1 for r in rs if r["grade"]=="loss")))
    pos = sum(1 for _,_,_,r,_,_ in daily if (r or 0) >= 0)
    cons = round(pos/len(daily),3) if daily else None
    return {"n":len(rows),"gr":len(g),"w":w,"l":l,
            "hr":round(hr,2) if hr is not None else None,
            "roi":round(roi,2) if roi is not None else None,
            "roi_lo":roi_lo,"roi_hi":roi_hi,
            "pnl":round(pnl,3),"stake":round(stk,3),
            "consist":cons,"grd_days":len(daily),
            "daily": daily}


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    serials = [f"GSS-MLB-202506{d:02d}-FRON-POOL" for d in range(1,31)]
    raw = []
    async for d in db.mlb_test_outputs.find(
        {"replay_serial":{"$in":serials},
         "stat_family":"pitcher_strikeouts",
         "routed_tier":"front_lines",
         "side":"OVER"},
        projection={"_id":0}):
        odds = d.get("odds")
        if odds is None: continue
        if _band(odds) != "[+101,+200]": continue
        line = d.get("line"); mu = d.get("projection_mu")
        mu_gap = (mu - line) if (mu is not None and line is not None) else None
        raw.append({
            "odds":odds, "cv":d.get("cv"),
            "edge_pp":_edge_pp(d.get("edge")),
            "hr_l20":d.get("hit_rate_l20"),
            "tp":d.get("tp"),
            "line":line, "mu":mu, "mu_gap":mu_gap,
            "grade":d.get("grade_status") or "not_qualified",
            "pnl":d.get("profit_units") or 0.0,
            "stake":d.get("stake_units") or 0.0,
            "game_date":d.get("game_date") or "",
            "player_name":d.get("player_name"),
            "player_norm":d.get("player_name_normalized"),
            "event_id":d.get("event_id"),
            "home_team":d.get("home_team"),
            "away_team":d.get("away_team"),
            "actual_value":d.get("actual_value"),
            "snapshot_iso":d.get("snapshot_iso"),
            "commence_time":d.get("commence_time"),
            "side":d.get("side"),
            "book":d.get("book"),
            "fair_p":d.get("fair_probability"),
        })
    c.close()

    # Apply locked filter
    rows = [r for r in raw if r["edge_pp"] is not None and r["edge_pp"] >= 5.0
            and r["cv"] is not None and r["cv"] <= 0.70]
    a = _agg(rows)
    print("═"*120)
    print("  PHASE-3 FORENSIC RE-AUDIT — PS_OVER [+101,+200] EDG≥5 ∧ CV≤0.70  (June 2025)")
    print("═"*120)

    # ── 1) Headline ────────────────────────────────────────────
    print("\n▶ 1) HEADLINE METRICS")
    print(f"  Band pool (unfiltered):  n={len(raw)}  graded={sum(1 for r in raw if r['grade'] in ('win','loss','push'))}")
    print(f"  After filter:            n={a['n']}  graded={a['gr']}")
    print(f"  W/L/P:                   {a['w']}/{a['l']}/{a['gr']-a['w']-a['l']}")
    print(f"  HR:                      {a['hr']}%")
    print(f"  ROI:                     {a['roi']}%   CI=[{a['roi_lo']},{a['roi_hi']}]")
    print(f"  P&L:                     {a['pnl']}u")
    print(f"  Stake:                   {a['stake']}u")
    avg_odds = sum(r["odds"] for r in rows)/len(rows) if rows else 0
    print(f"  Avg odds:                +{avg_odds:.1f}")
    print(f"  Consistency:             {a['consist']} ({a['grd_days']} graded days)")
    print(f"  Avg picks/day:           {a['gr']/max(a['grd_days'],1):.2f}")

    # ── 2) 3-fold ──────────────────────────────────────────────
    print("\n▶ 2) 3-FOLD ROBUSTNESS")
    for fold in ("A: 06-01..10","B: 06-11..20","C: 06-21..30"):
        sub = [r for r in rows if _fold(r["game_date"]) == fold]
        af = _agg(sub)
        if af["gr"] == 0:
            print(f"  {fold:<16s}  (no graded)"); continue
        ci = (f"[{af['roi_lo']},{af['roi_hi']}]"
              if af["roi_lo"] is not None else "-")
        print(f"  {fold:<16s}  n={af['n']:>3d} gr={af['gr']:>3d} "
              f"W={af['w']:>2d} L={af['l']:>2d}  HR={str(af['hr']):>5s}%  "
              f"ROI={str(af['roi']):>6s}%  CI={ci[:15]:<15s}  "
              f"P&L={af['pnl']:>+7.2f}u  cons={af['consist']}")

    # ── 3) Day-by-day ──────────────────────────────────────────
    print("\n▶ 3) DAY-BY-DAY")
    print(f"  {'date':<12s} {'picks':>5s} {'gr':>4s} {'W':>3s} {'L':>3s} "
          f"{'HR%':>6s} {'ROI%':>7s} {'P&L':>7s} {'cum':>8s}")
    cum = 0.0
    cum_arr = []
    for (d,n,pnl,roi,w,l) in a["daily"]:
        cum += pnl
        cum_arr.append((d, pnl, cum, roi or 0))
        hr_d = (100*w/(w+l)) if (w+l) else 0
        print(f"  {d:<12s} {n:>5d} {w+l:>4d} {w:>3d} {l:>3d} "
              f"{hr_d:>6.2f} {(roi or 0):>+7.2f} {pnl:>+7.2f} {cum:>+8.2f}")

    # day stats
    if cum_arr:
        best_d = max(cum_arr, key=lambda t: t[1])
        worst_d = min(cum_arr, key=lambda t: t[1])
        total = a["pnl"] or 0.0001
        sorted_d = sorted(cum_arr, key=lambda t: t[1], reverse=True)
        top1 = sorted_d[0][1]
        top3 = sum(t[1] for t in sorted_d[:3])
        print(f"\n  Best day:  {best_d[0]} {best_d[1]:+.2f}u")
        print(f"  Worst day: {worst_d[0]} {worst_d[1]:+.2f}u")
        if total != 0:
            print(f"  Top-1 day concentration: {100*top1/total:.1f}%")
            print(f"  Top-3 day concentration: {100*top3/total:.1f}%")
        # Max DD on cumulative
        peak = -1e18; max_dd = 0
        for (d,_,c,_) in cum_arr:
            if c > peak: peak = c
            if (peak - c) > max_dd: max_dd = (peak - c)
        print(f"  Max drawdown: -{max_dd:.2f}u")
        # Longest losing streak by daily ROI
        cur_l = max_l = 0
        for (_,_,_,r) in cum_arr:
            if r < 0: cur_l += 1; max_l = max(max_l, cur_l)
            else: cur_l = 0
        print(f"  Longest losing streak: {max_l} day(s)")
        # Rolling
        print(f"\n  Rolling 3-day P&L (last 5):")
        for i in range(max(0,len(cum_arr)-7), len(cum_arr)-2):
            sl = cum_arr[i:i+3]
            s = sum(t[1] for t in sl)
            print(f"    {sl[0][0]}→{sl[-1][0]}  P&L={s:>+6.2f}u")
        print(f"\n  Rolling 5-day P&L (last 5):")
        for i in range(max(0,len(cum_arr)-9), len(cum_arr)-4):
            sl = cum_arr[i:i+5]
            s = sum(t[1] for t in sl)
            print(f"    {sl[0][0]}→{sl[-1][0]}  P&L={s:>+6.2f}u")

    # ── 4) Odds sub-band ───────────────────────────────────────
    print("\n▶ 4) ODDS SUB-BAND BREAKDOWN")
    sub_buckets = [(101,120),(121,140),(141,160),(161,180),(181,200)]
    for lo,hi in sub_buckets:
        s = [r for r in rows if lo <= int(r["odds"]) <= hi]
        if not s:
            print(f"  [+{lo:>3d},+{hi:>3d}]  (no picks)"); continue
        a2 = _agg(s)
        ci = (f"[{a2['roi_lo']},{a2['roi_hi']}]"
              if a2["roi_lo"] is not None else "-")
        print(f"  [+{lo:>3d},+{hi:>3d}]  n={a2['n']:>3d}/{a2['gr']:>3d}  "
              f"W={a2['w']:>2d} L={a2['l']:>2d}  HR={str(a2['hr']):>5s}%  "
              f"ROI={str(a2['roi']):>6s}%  P&L={a2['pnl']:>+6.2f}u  "
              f"CI={ci[:15]:<15s}  cons={a2['consist']}")

    # ── 5) Structural decomposition ────────────────────────────
    print("\n▶ 5) STRUCTURAL DECOMPOSITION")
    print("  TP buckets:")
    tp_b = [(None,50),(50,55),(55,60),(60,65),(65,70),(70,75),(75,100)]
    for lo,hi in tp_b:
        s = [r for r in rows if r["tp"] is not None
             and (lo is None or r["tp"] >= lo) and r["tp"] < hi]
        if not s: continue
        a2 = _agg(s)
        lab = f"TP [{'-' if lo is None else lo},{hi})"
        print(f"    {lab:<14s}  n={a2['n']:>3d}/{a2['gr']:>3d}  "
              f"HR={str(a2['hr']):>5s}%  ROI={str(a2['roi']):>6s}%  "
              f"P&L={a2['pnl']:>+6.2f}u")
    print("\n  μ-line buckets:")
    mu_b = [(-99,-0.5),(-0.5,0.0),(0.0,0.5),(0.5,1.0),(1.0,1.5),(1.5,99)]
    for lo,hi in mu_b:
        s = [r for r in rows if r["mu_gap"] is not None
             and lo <= r["mu_gap"] < hi]
        if not s: continue
        a2 = _agg(s)
        print(f"    μ [{lo:>+5.2f},{hi:>+5.2f})  n={a2['n']:>3d}/{a2['gr']:>3d}  "
              f"HR={str(a2['hr']):>5s}%  ROI={str(a2['roi']):>6s}%  "
              f"P&L={a2['pnl']:>+6.2f}u")
    print("\n  EDGE buckets (pp):")
    e_b = [(5,7),(7,10),(10,15),(15,20),(20,99)]
    for lo,hi in e_b:
        s = [r for r in rows if r["edge_pp"] is not None
             and lo <= r["edge_pp"] < hi]
        if not s: continue
        a2 = _agg(s)
        print(f"    EDG [{lo:>+2d},{hi:>+3d})  n={a2['n']:>3d}/{a2['gr']:>3d}  "
              f"HR={str(a2['hr']):>5s}%  ROI={str(a2['roi']):>6s}%  "
              f"P&L={a2['pnl']:>+6.2f}u")
    print("\n  CV buckets:")
    cv_b = [(0,0.30),(0.30,0.40),(0.40,0.50),(0.50,0.60),(0.60,0.70)]
    for lo,hi in cv_b:
        s = [r for r in rows if r["cv"] is not None and lo <= r["cv"] < hi]
        if not s: continue
        a2 = _agg(s)
        print(f"    CV [{lo:.2f},{hi:.2f})  n={a2['n']:>3d}/{a2['gr']:>3d}  "
              f"HR={str(a2['hr']):>5s}%  ROI={str(a2['roi']):>6s}%  "
              f"P&L={a2['pnl']:>+6.2f}u")

    # ── 6) Forensic integrity ──────────────────────────────────
    print("\n▶ 6) FORENSIC INTEGRITY CHECKS")
    graded = [r for r in rows if r["grade"] in ("win","loss","push")]
    # mis-grades
    wg = []
    for r in graded:
        av = r["actual_value"]; line = r["line"]
        if av is None or line is None: continue
        exp = "push" if av == line else ("win" if av > line else "loss")
        if r["grade"] != exp: wg.append(r["player_name"])
    print(f"  Mis-graded:               {len(wg)}")
    # P&L formula
    pnl_wrong = []
    for r in graded:
        if r["stake"]==0: continue
        if r["grade"]=="push": exp = 0.0
        elif r["grade"]=="win":
            exp = r["stake"]*(r["odds"]/100.0 if r["odds"]>0 else 100.0/abs(r["odds"]))
        elif r["grade"]=="loss": exp = -r["stake"]
        else: continue
        if abs(r["pnl"]-exp) > 0.001: pnl_wrong.append(r["player_name"])
    print(f"  P&L formula mismatches:   {len(pnl_wrong)}")
    keyf = lambda r: (r["event_id"], r["player_norm"], r["side"], r["line"])
    dc = Counter(keyf(r) for r in rows)
    dups = [k for k,v in dc.items() if v>1]
    print(f"  Duplicate keys:           {len(dups)}")
    bs = [r for r in rows if r["snapshot_iso"] and r["commence_time"]
            and r["snapshot_iso"] >= r["commence_time"]]
    print(f"  Lookahead snapshots:      {len(bs)}")
    av_pop = sum(1 for r in graded if r["actual_value"] is not None)
    print(f"  actual_value populated:   {av_pop}/{len(graded)}")
    fp = [r["fair_p"] for r in rows if r["fair_p"] is not None]
    if fp:
        m = sum(fp)/len(fp); hi = sum(1 for p in fp if p>0.95)
        print(f"  Mean fair_p:              {m:.3f}   fair_p>0.95: {hi}/{len(fp)}")

    # ── 7) Diversification ─────────────────────────────────────
    print("\n▶ 7) DIVERSIFICATION / CONCENTRATION")
    pl = Counter(r["player_norm"] for r in rows)
    th = Counter(r["home_team"] for r in rows)
    ta = Counter(r["away_team"] for r in rows)
    ev = Counter(r["event_id"] for r in rows)
    print(f"  Distinct pitchers:        {len(pl)}")
    print(f"  Top pitchers by freq:")
    for p,c in pl.most_common(10):
        won = sum(1 for r in graded if r["player_norm"]==p and r["grade"]=="win")
        lost = sum(1 for r in graded if r["player_norm"]==p and r["grade"]=="loss")
        pn = sum(r["pnl"] for r in graded if r["player_norm"]==p)
        print(f"    {p or '?':<25s}  ×{c:>2d}  W={won}  L={lost}  P&L={pn:+.2f}u")
    print(f"  Distinct home teams:      {len(th)}")
    print(f"  Distinct away teams:      {len(ta)}")
    print(f"  Distinct events:          {len(ev)}")
    multi = sum(1 for v in ev.values() if v>1)
    print(f"  Events with >1 pick:      {multi}")
    # P&L by top-10 pitchers
    top10_pnl = sum(sum(r["pnl"] for r in graded if r["player_norm"]==p)
                     for p,_ in pl.most_common(10))
    total_pnl = sum(r["pnl"] for r in graded)
    print(f"  P&L from top-10 pitchers: {top10_pnl:+.2f}u of {total_pnl:+.2f}u total")
    if total_pnl != 0:
        print(f"    → {100*top10_pnl/total_pnl:.1f}% of total P&L")


asyncio.run(main())
