"""Phase-2 ALL-STATS forensic — full FL micro-grid sweep, June 2025.

Window: 2025-06-01 → 2025-06-28
Routed tier: front_lines
Stats × sides:  ALL combinations of {pitcher_strikeouts, earned_runs,
  runs, batter_strikeouts, hits, total_bases} × {OVER, UNDER}

For each (family, side, band) cell with graded >= 30:
  baseline + filter sweep (HR20 / EDG / CV / mu / TP) → TOP-3 by ROI
"""
import asyncio, math, os, sys
from collections import defaultdict
from typing import Any, Dict, List, Optional
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

def _band(o):
    if o is None: return "unknown"
    o = int(o)
    if o <= -300: return "[-500,-300]"
    if o <= -200: return "[-299,-200]"
    if o <= -150: return "[-199,-150]"
    if o <= -110: return "[-149,-110]"
    if o <=  100: return "[-109,+100]"
    if o <=  200: return "[+101,+200]"
    return "[+201,+inf]"

BAND_ORDER = ["[-500,-300]","[-299,-200]","[-199,-150]","[-149,-110]",
              "[-109,+100]","[+101,+200]","[+201,+inf]"]

def _edge_pp(v):
    if v is None: return None
    try: x = float(v)
    except: return None
    return x*100.0 if abs(x) < 1.5 else x

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
    pos=grd=0
    for rs in by_d.values():
        stk2=sum(r["stake"] for r in rs); pnl2=sum(r["pnl"] for r in rs)
        roi2=(100*pnl2/stk2) if stk2 else None
        grd+=1
        if (roi2 or 0)>=0: pos+=1
    cons = round(pos/grd,3) if grd else None
    return {"n":len(rows),"gr":len(g),"w":w,"l":l,
            "hr":round(hr,2) if hr is not None else None,
            "roi":round(roi,2) if roi is not None else None,
            "roi_lo":roi_lo,"roi_hi":roi_hi,
            "pnl":round(pnl,3),"consist":cons,"grd_days":grd}


def _passes(r, *, hr, edg, cv, mu, tp):
    if hr  is not None and (r["hr_l20"]  is None or r["hr_l20"] < hr):  return False
    if edg is not None and (r["edge_pp"] is None or r["edge_pp"] < edg): return False
    if cv  is not None and (r["cv"]      is None or r["cv"] > cv):       return False
    if mu  is not None and (r["mu_gap"]  is None or r["mu_gap"] < mu):   return False
    if tp  is not None and (r["tp"]      is None or r["tp"] < tp):       return False
    return True


HR_GRID  = [None, 45.0, 50.0, 55.0, 60.0, 65.0, 70.0, 75.0]
EDG_GRID = [None, -5.0, 0.0, 5.0, 10.0]
CV_GRID  = [None, 0.70, 0.60, 0.50, 0.45, 0.40]
MU_GRID  = [None, 0.0, 0.5, 1.0]
TP_GRID  = [None, 50.0, 55.0, 60.0, 65.0]


def _sweep(rows):
    out = []
    for h in HR_GRID:
      for e in EDG_GRID:
        for cv in CV_GRID:
          for mu in MU_GRID:
            for tp in TP_GRID:
              sub = [r for r in rows
                      if _passes(r, hr=h, edg=e, cv=cv, mu=mu, tp=tp)]
              a = _agg(sub)
              if (a["gr"] or 0) < 20: continue
              out.append({
                  "label": (f"HR≥{int(h) if h else '-'}/"
                             f"EDG≥{e if e is not None else '-'}/"
                             f"CV≤{cv if cv else '-'}/"
                             f"μ≥{mu if mu is not None else '-'}/"
                             f"TP≥{int(tp) if tp else '-'}"),
                  **a})
    return out


FAMILIES = ["pitcher_strikeouts","earned_runs","runs",
            "batter_strikeouts","hits","total_bases"]


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    serials = [f"GSS-MLB-202506{d:02d}-FRON-POOL" for d in range(1, 29)]
    # Build (fam,side,band) → rows
    by_fsb: Dict[tuple, List] = defaultdict(list)
    async for d in db.mlb_test_outputs.find(
        {"replay_serial":{"$in":serials},
         "stat_family":{"$in":FAMILIES},
         "routed_tier":"front_lines"},
        projection={"_id":0}):
        odds = d.get("odds")
        if odds is None: continue
        side = d.get("side") or "OVER"
        line = d.get("line"); mu = d.get("projection_mu")
        mu_gap = (mu - line) if (mu is not None and line is not None) else None
        rec = {
            "odds":odds, "cv":d.get("cv"),
            "edge_pp":_edge_pp(d.get("edge")),
            "hr_l20":d.get("hit_rate_l20"),
            "tp":d.get("tp"),
            "mu_gap":mu_gap,
            "grade":d.get("grade_status") or "not_qualified",
            "pnl":d.get("profit_units") or 0.0,
            "stake":d.get("stake_units") or 0.0,
            "game_date":d.get("game_date") or "",
        }
        by_fsb[(d.get("stat_family"), side, _band(odds))].append(rec)
    c.close()

    # Collect positive cells across the whole grid
    positive_cells = []
    print("\n══════ POOL OVERVIEW + BASELINE BY (family,side,band) ══════")
    print(f"  {'family':<22s} {'side':<6s} {'band':<14s} {'n':>4s} "
          f"{'gr':>4s} {'HR':>6s} {'ROI':>7s} {'P&L':>7s}")
    for fam in FAMILIES:
        for side in ("OVER","UNDER"):
            for band in BAND_ORDER:
                rows = by_fsb.get((fam, side, band), [])
                if not rows: continue
                a = _agg(rows)
                gr = a["gr"] or 0
                if gr < 20: continue
                hr_s = f"{a['hr']:.2f}" if a["hr"] is not None else "-"
                roi_s = f"{a['roi']:+.2f}" if a["roi"] is not None else "-"
                mark = "🟢" if (a["roi"] or 0) > 0 else "🔴"
                print(f"  {fam:<22s} {side:<6s} {band:<14s} {a['n']:>4d} "
                      f"{gr:>4d} {hr_s:>6s} {roi_s:>7s} "
                      f"{a['pnl']:>+7.2f} {mark}")

    # For each (fam,side,band) with gr>=30, run sweep and print top-5 by ROI
    print("\n\n══════ TOP-5 CONFIGS PER (family,side,band) — sweep ══════")
    print("  (Showing only configs with ROI >= 5% to suppress noise)\n")
    overall_positive_count = 0
    for fam in FAMILIES:
        for side in ("OVER","UNDER"):
            for band in BAND_ORDER:
                rows = by_fsb.get((fam, side, band), [])
                a = _agg(rows)
                if (a["gr"] or 0) < 30: continue
                results = _sweep(rows)
                # keep configs with ROI > 5% (stricter than 0 to denoise)
                good = [r for r in results if (r["roi"] or 0) > 5.0]
                if not good: continue
                # Top-5 by ROI
                top = sorted(good, key=lambda r: r["roi"], reverse=True)[:5]
                print(f"  ── {fam} {side} {band}  "
                      f"(baseline ROI={a['roi']}% P&L={a['pnl']:+.2f}u) ──")
                print(f"    {'config':<48s} {'n':>4s} {'gr':>4s} "
                      f"{'HR%':>6s} {'ROI%':>7s} {'CI':>15s} "
                      f"{'P&L':>7s} {'cons':>5s}")
                for r in top:
                    ci = (f"[{r['roi_lo']},{r['roi_hi']}]"
                          if r["roi_lo"] is not None else "-")
                    print(f"    {r['label'][:48]:<48s} {r['n']:>4d} "
                          f"{r['gr']:>4d} {str(r['hr']):>6s} "
                          f"{str(r['roi']):>7s} {ci[:15]:>15s} "
                          f"{r['pnl']:>+7.2f} {str(r['consist']):>5s}")
                    overall_positive_count += 1
                    positive_cells.append({
                        "fam":fam,"side":side,"band":band,
                        "cfg":r["label"], "n":r["n"], "gr":r["gr"],
                        "hr":r["hr"],"roi":r["roi"],
                        "roi_lo":r["roi_lo"],"roi_hi":r["roi_hi"],
                        "pnl":r["pnl"],"cons":r["consist"]})

    # Final statistical-significance leaderboard: only CI low > 0
    print(f"\n\n══════ STATISTICALLY SIGNIFICANT POSITIVE CELLS ══════")
    print(f"  (CI lower bound > 0, gr >= 30)")
    sig = [c for c in positive_cells
           if c["roi_lo"] is not None and c["roi_lo"] > 0
           and (c["gr"] or 0) >= 30]
    sig.sort(key=lambda c: c["roi"], reverse=True)
    if not sig:
        print("  NO statistically significant positive cells found.")
    else:
        print(f"  {'family':<22s} {'side':<6s} {'band':<14s} "
              f"{'config':<48s} {'n':>4s} {'gr':>4s} {'HR%':>6s} "
              f"{'ROI%':>7s} {'CI':>15s} {'P&L':>7s} {'cons':>5s}")
        for c in sig:
            ci = f"[{c['roi_lo']},{c['roi_hi']}]"
            print(f"  {c['fam']:<22s} {c['side']:<6s} {c['band']:<14s} "
                  f"{c['cfg'][:48]:<48s} {c['n']:>4d} {c['gr']:>4d} "
                  f"{str(c['hr']):>6s} {str(c['roi']):>7s} {ci[:15]:>15s} "
                  f"{c['pnl']:>+7.2f} {str(c['cons']):>5s}")
    print(f"\n  Total positive-ROI cells (>5%): {overall_positive_count}")
    print(f"  Statistically significant (CI low > 0): {len(sig)}")

asyncio.run(main())
