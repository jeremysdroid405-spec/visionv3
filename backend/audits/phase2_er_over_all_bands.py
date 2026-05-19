"""Phase-2 forensic — `earned_runs OVER` picks across ALL FL odds bands.

Window: 2025-06-01 → 2025-06-28
Routed tier: front_lines
Side: OVER

For each band: baseline + filter sweep
Filter grid:
  HR20  in {None, 45, 50, 55, 60, 65, 70, 75}
  EDG   in {None, -5, 0, 5, 10}
  CV    in {None, 0.70, 0.60, 0.50, 0.45, 0.40}
  mu-gap in {None, 0, 0.5, 1.0}
  TP    in {None, 50, 55, 60, 65}
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
    pos = grd = 0
    for rs in by_d.values():
        stk2 = sum(r["stake"] for r in rs); pnl2 = sum(r["pnl"] for r in rs)
        roi2 = (100*pnl2/stk2) if stk2 else None
        grd += 1
        if (roi2 or 0) >= 0: pos += 1
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


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    serials = [f"GSS-MLB-202506{d:02d}-FRON-POOL" for d in range(1, 29)]
    by_band: Dict[str, List[Dict[str,Any]]] = defaultdict(list)
    async for d in db.mlb_test_outputs.find(
        {"replay_serial":{"$in":serials},
         "stat_family":"earned_runs",
         "routed_tier":"front_lines",
         "side":"OVER"},
        projection={"_id":0}):
        odds = d.get("odds")
        if odds is None: continue
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
        by_band[_band(odds)].append(rec)
    c.close()

    print("══════ POOL OVERVIEW ══════")
    total_n = total_g = 0
    for b in BAND_ORDER:
        n = len(by_band.get(b,[]))
        g = sum(1 for r in by_band.get(b,[]) if r["grade"] in ("win","loss","push"))
        if n: print(f"  {b:<14s}  n={n:>4d}  graded={g:>4d}")
        total_n += n; total_g += g
    print(f"  {'TOTAL':<14s}  n={total_n:>4d}  graded={total_g:>4d}")

    for b in BAND_ORDER:
        rows = by_band.get(b, [])
        if not rows: continue
        a = _agg(rows)
        if (a["gr"] or 0) < 30:
            print(f"\n── band {b}  ({a['n']}/{a['gr']})  HR={a['hr']}% "
                  f"ROI={a['roi']}% P&L={a['pnl']:+.2f}u  cons={a['consist']} "
                  f"(skipping micro-grid: gr<30)")
            continue
        print(f"\n\n══════ BAND {b}  ({a['n']}/{a['gr']}) ══════")
        print(f"  Baseline: HR={a['hr']}%  ROI={a['roi']}%  "
              f"CI=[{a['roi_lo']},{a['roi_hi']}]  P&L={a['pnl']:+.2f}u  "
              f"consist={a['consist']} ({a['grd_days']}d)")
        results = _sweep(rows)
        # Top 10 by ROI
        top_roi = sorted([r for r in results if r["roi"] is not None],
                         key=lambda r: r["roi"], reverse=True)[:10]
        print(f"\n  TOP-10 by ROI (gr≥20):")
        print(f"    {'config':<50s} {'n':>4s} {'gr':>4s} {'HR%':>6s} "
              f"{'ROI%':>7s} {'CI':>15s} {'P&L':>7s} {'cons':>5s}")
        for r in top_roi:
            ci = f"[{r['roi_lo']},{r['roi_hi']}]" if r["roi_lo"] is not None else "-"
            print(f"    {r['label'][:50]:<50s} {r['n']:>4d} {r['gr']:>4d} "
                  f"{str(r['hr']):>6s} {str(r['roi']):>7s} {ci[:15]:>15s} "
                  f"{r['pnl']:>+7.2f} {str(r['consist']):>5s}")
        # Top 10 by P&L
        top_pnl = sorted(results, key=lambda r: r["pnl"], reverse=True)[:10]
        print(f"\n  TOP-10 by P&L:")
        print(f"    {'config':<50s} {'n':>4s} {'gr':>4s} {'HR%':>6s} "
              f"{'ROI%':>7s} {'P&L':>7s} {'cons':>5s}")
        for r in top_pnl:
            print(f"    {r['label'][:50]:<50s} {r['n']:>4d} {r['gr']:>4d} "
                  f"{str(r['hr']):>6s} {str(r['roi']):>7s} {r['pnl']:>+7.2f} "
                  f"{str(r['consist']):>5s}")

asyncio.run(main())
