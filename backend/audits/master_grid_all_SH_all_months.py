"""
MASTER MULTI-FAMILY / MULTI-MONTH GRID SWEEP — SAFE HAVEN only.

"All gates paused" = we sweep the raw `routed_tier=safe_haven` pool from
`mlb_test_outputs` with NO production gate filtering, applying only the
analytical 4800-cell filter grid on top. Production gates remain
untouched.

Scope:
  • Stat families: all SH families seen in mlb_test_outputs (7 total)
  • Sides:         OVER and UNDER
  • Bands:         all 7 odds bands (skip if empty)
  • Months:        2025-06, 2026-05

Output:
  • Per (family · side · band) baseline + qualified-config count
  • Cross-regime durable configs (positive ROI in BOTH months)
  • TOP-10 GLOBAL by min(ROI) and by sum(P&L)
  • Per-(family·side·band) TOP-10 month-by-month comparison tables
  • CSV exports
"""
import asyncio, csv, math, os, sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


def _band(o):
    """SH-specific 10-bucket band scheme (deep-chalk territory)."""
    if o is None: return "unknown"
    o = int(o)
    if o <= -5000: return "[-inf,-5000]"
    if o <= -3000: return "[-4999,-3000]"
    if o <= -2000: return "[-2999,-2000]"
    if o <= -1500: return "[-1999,-1500]"
    if o <= -1000: return "[-1499,-1000]"
    if o <=  -800: return "[-999,-800]"
    if o <=  -600: return "[-799,-600]"
    if o <=  -500: return "[-599,-500]"
    if o <=  -400: return "[-499,-400]"
    if o <=  -300: return "[-399,-300]"
    return "[other]"

BAND_ORDER = ["[-inf,-5000]","[-4999,-3000]","[-2999,-2000]","[-1999,-1500]",
              "[-1499,-1000]","[-999,-800]","[-799,-600]","[-599,-500]",
              "[-499,-400]","[-399,-300]","[other]"]


def _edge_pp(v):
    if v is None: return None
    try: x = float(v)
    except: return None
    return x * 100.0 if abs(x) < 1.5 else x


@dataclass
class Row:
    odds: Optional[float]
    cv: Optional[float]
    edge_pp: Optional[float]
    hr_l20: Optional[float]
    tp: Optional[float]
    mu_gap: Optional[float]
    grade: str
    pnl: float
    stake: float
    game_date: str


def _agg(rows: List[Row]) -> Dict[str, Any]:
    g = [r for r in rows if r.grade in ("win","loss","push")]
    w = sum(1 for r in g if r.grade=="win")
    l = sum(1 for r in g if r.grade=="loss")
    pnl = sum(r.pnl for r in g); stk = sum(r.stake for r in g)
    hr = (100*w/(w+l)) if (w+l) else None
    roi = (100*pnl/stk) if stk else None
    rets = [r.pnl/r.stake for r in g if r.stake]
    roi_lo = roi_hi = None
    if rets:
        n=len(rets); m=sum(rets)/n; v=sum((x-m)**2 for x in rets)/max(n-1,1)
        se=math.sqrt(v/n)
        roi_lo = round(100*(m-1.96*se),2); roi_hi=round(100*(m+1.96*se),2)
    by_d = defaultdict(list)
    for r in g: by_d[r.game_date].append(r)
    pos = grd_d = 0
    for rs in by_d.values():
        stk2 = sum(r.stake for r in rs); pnl2 = sum(r.pnl for r in rs)
        roi2 = (100*pnl2/stk2) if stk2 else None
        grd_d += 1
        if (roi2 or 0) >= 0: pos += 1
    consist = round(pos/grd_d,3) if grd_d else None
    return {"n":len(rows),"gr":len(g),"w":w,"l":l,
            "hr": round(hr,2) if hr is not None else None,
            "roi": round(roi,2) if roi is not None else None,
            "roi_lo": roi_lo, "roi_hi": roi_hi,
            "pnl": round(pnl,3), "consist": consist, "grd_days": grd_d}


def _passes(r: Row, *, hr, edg, cv, mu, tp) -> bool:
    if hr  is not None and (r.hr_l20  is None or r.hr_l20 < hr):  return False
    if edg is not None and (r.edge_pp is None or r.edge_pp < edg): return False
    if cv  is not None and (r.cv      is None or r.cv > cv):       return False
    if mu  is not None and (r.mu_gap  is None or r.mu_gap < mu):   return False
    if tp  is not None and (r.tp      is None or r.tp < tp):       return False
    return True


HR_GRID  = [None, 45.0, 50.0, 55.0, 60.0, 65.0, 70.0, 75.0]
EDG_GRID = [None, -5.0, 0.0, 5.0, 10.0]
CV_GRID  = [None, 0.70, 0.60, 0.50, 0.45, 0.40]
MU_GRID  = [None, 0.0, 0.5, 1.0]
TP_GRID  = [None, 50.0, 55.0, 60.0, 65.0]


def _cfg_label(h, e, cv, mu, tp) -> str:
    return (f"HR≥{int(h) if h else '-'}/"
            f"EDG≥{e if e is not None else '-'}/"
            f"CV≤{cv if cv else '-'}/"
            f"μ≥{mu if mu is not None else '-'}/"
            f"TP≥{int(tp) if tp else '-'}")


def _sweep(rows: List[Row]) -> Dict[str, Dict[str,Any]]:
    out = {}
    for h in HR_GRID:
      for e in EDG_GRID:
        for cv in CV_GRID:
          for mu in MU_GRID:
            for tp in TP_GRID:
              sub = [r for r in rows if _passes(r, hr=h, edg=e, cv=cv, mu=mu, tp=tp)]
              a = _agg(sub)
              if (a["gr"] or 0) < 30: continue
              out[_cfg_label(h,e,cv,mu,tp)] = {
                  "hr": h, "edg": e, "cv": cv, "mu": mu, "tp": tp, **a,
              }
    return out


MONTHS = ["2025-06", "2026-05"]


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    families = ['batter_strikeouts','earned_runs','hits','pitcher_strikeouts',
                'runs','strikeouts','total_bases']
    sides = ['OVER','UNDER']

    pool: Dict[Tuple[str,str,str,str], List[Row]] = defaultdict(list)
    print("Loading SAFE-HAVEN replay rows…")
    for mo in MONTHS:
        cnt = 0
        async for d in db.mlb_test_outputs.find(
            {"routed_tier":"safe_haven",
             "game_date":{"$regex":f"^{mo}"},
             "stat_family":{"$in":families},
             "side":{"$in":sides}},
            projection={"_id":0}):
            odds = d.get("odds")
            if odds is None: continue
            line = d.get("line"); mu = d.get("projection_mu")
            mu_gap = (mu - line) if (mu is not None and line is not None) else None
            r = Row(
                odds=odds, cv=d.get("cv"),
                edge_pp=_edge_pp(d.get("edge")),
                hr_l20=d.get("hit_rate_l20"),
                tp=d.get("tp"),
                mu_gap=mu_gap,
                grade=d.get("grade_status") or "not_qualified",
                pnl=d.get("profit_units") or 0.0,
                stake=d.get("stake_units") or 0.0,
                game_date=d.get("game_date") or "",
            )
            key = (d["stat_family"], d["side"], mo, _band(odds))
            pool[key].append(r)
            cnt += 1
        print(f"  {mo}: {cnt} rows")
    c.close()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_all = f"/app/backend/audits/master_grid_all_SH_all_months_{stamp}.csv"
    out_dur = f"/app/backend/audits/master_grid_SH_cross_regime_durable_{stamp}.csv"
    out_base = f"/app/backend/audits/master_grid_SH_baselines_{stamp}.csv"

    all_rows = [["month","family","side","band","cfg","n","gr","w","l",
                 "HR_pct","ROI_pct","ROI_lo","ROI_hi","P_and_L","consist","grd_days"]]
    base_rows = [["family","side","band","month","n","gr","HR_pct","ROI_pct",
                  "ROI_lo","ROI_hi","P_and_L","consist","grd_days"]]
    dur_rows = [["family","side","band","cfg",
                 "may_n","may_gr","may_ROI","may_CI_lo","may_CI_hi","may_PnL","may_cons",
                 "jun_n","jun_gr","jun_ROI","jun_CI_lo","jun_CI_hi","jun_PnL","jun_cons",
                 "min_ROI","sum_PnL"]]

    print()
    print("═"*120)
    print("  MASTER CROSS-MONTH GRID — SAFE-HAVEN · all gates paused · all bands · 2025-06 vs 2026-05")
    print("═"*120)

    for fam in families:
        for side in sides:
            present_bands = [b for b in BAND_ORDER
                              if (fam,side,'2025-06',b) in pool
                                  or (fam,side,'2026-05',b) in pool]
            if not present_bands: continue
            tot_jun = sum(len(pool.get((fam,side,'2025-06',b),[])) for b in present_bands)
            tot_may = sum(len(pool.get((fam,side,'2026-05',b),[])) for b in present_bands)
            if tot_jun == 0 and tot_may == 0: continue
            print(f"\n\n┌{'─'*118}┐")
            print(f"│  {fam} · {side:<6s} │ Jun-2025 n={tot_jun:>5d} │ May-2026 n={tot_may:>5d}")
            print(f"└{'─'*118}┘")

            for b in BAND_ORDER:
                may_rows = pool.get((fam,side,'2026-05',b), [])
                jun_rows = pool.get((fam,side,'2025-06',b), [])
                if not may_rows and not jun_rows: continue
                a_may = _agg(may_rows) if may_rows else None
                a_jun = _agg(jun_rows) if jun_rows else None
                if a_may:
                    base_rows.append([fam,side,b,'2026-05',a_may['n'],a_may['gr'],
                                       a_may['hr'],a_may['roi'],a_may['roi_lo'],
                                       a_may['roi_hi'],a_may['pnl'],a_may['consist'],
                                       a_may['grd_days']])
                if a_jun:
                    base_rows.append([fam,side,b,'2025-06',a_jun['n'],a_jun['gr'],
                                       a_jun['hr'],a_jun['roi'],a_jun['roi_lo'],
                                       a_jun['roi_hi'],a_jun['pnl'],a_jun['consist'],
                                       a_jun['grd_days']])
                def fmt(a):
                    if a is None: return "—"
                    return (f"n={a['n']:>4d}/{a['gr']:>4d}  HR={str(a['hr'] or '-'):>5s}%  "
                            f"ROI={str(a['roi'] or '-'):>6s}%  "
                            f"CI=[{str(a['roi_lo']):>6s},{str(a['roi_hi']):>6s}]  "
                            f"PnL={a['pnl']:>+7.2f}  cons={a['consist']}")
                print(f"  ├ {b:<14s}  MAY: {fmt(a_may)}")
                print(f"  │ {'':<14s}  JUN: {fmt(a_jun)}")

                may_grid = _sweep(may_rows) if (a_may and a_may['gr'] >= 30) else {}
                jun_grid = _sweep(jun_rows) if (a_jun and a_jun['gr'] >= 30) else {}

                for cfg, r in may_grid.items():
                    all_rows.append(['2026-05',fam,side,b,cfg,r['n'],r['gr'],r['w'],r['l'],
                                      r['hr'],r['roi'],r['roi_lo'],r['roi_hi'],
                                      r['pnl'],r['consist'],r['grd_days']])
                for cfg, r in jun_grid.items():
                    all_rows.append(['2025-06',fam,side,b,cfg,r['n'],r['gr'],r['w'],r['l'],
                                      r['hr'],r['roi'],r['roi_lo'],r['roi_hi'],
                                      r['pnl'],r['consist'],r['grd_days']])

                common = set(may_grid.keys()) & set(jun_grid.keys())
                durable = []
                for cfg in common:
                    mm, jj = may_grid[cfg], jun_grid[cfg]
                    if mm['roi'] is None or jj['roi'] is None: continue
                    if mm['roi'] < 0 or jj['roi'] < 0: continue
                    durable.append((cfg, mm, jj))
                durable.sort(key=lambda t: min(t[1]['roi'], t[2]['roi']), reverse=True)
                if durable:
                    print(f"  │   ⭐ {len(durable)} cross-regime durable configs (ROI ≥ 0 in both months) — top 3 by min-ROI:")
                    for cfg, mm, jj in durable[:3]:
                        min_roi = min(mm['roi'], jj['roi'])
                        print(f"  │     {cfg[:48]:<48s}  May:n={mm['gr']:>3d} ROI={mm['roi']:>+6.2f}% | "
                              f"Jun:n={jj['gr']:>3d} ROI={jj['roi']:>+6.2f}%  min={min_roi:>+6.2f}%")
                    for cfg, mm, jj in durable:
                        dur_rows.append([fam,side,b,cfg,
                                          mm['n'],mm['gr'],mm['roi'],mm['roi_lo'],mm['roi_hi'],mm['pnl'],mm['consist'],
                                          jj['n'],jj['gr'],jj['roi'],jj['roi_lo'],jj['roi_hi'],jj['pnl'],jj['consist'],
                                          min(mm['roi'],jj['roi']), round(mm['pnl']+jj['pnl'],3)])

    with open(out_all,"w",newline="") as f:
        csv.writer(f).writerows(all_rows)
    with open(out_dur,"w",newline="") as f:
        csv.writer(f).writerows(dur_rows)
    with open(out_base,"w",newline="") as f:
        csv.writer(f).writerows(base_rows)
    print()
    print("═"*120)
    print(f"  Saved {len(all_rows)-1:>6d} qualified-config rows → {out_all}")
    print(f"  Saved {len(dur_rows)-1:>6d} cross-regime durable rows → {out_dur}")
    print(f"  Saved {len(base_rows)-1:>6d} band baselines             → {out_base}")
    print("═"*120)

asyncio.run(main())
