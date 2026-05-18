"""
Micro-grid sweep — FL `hits` OVER side, across ALL FL odds bands.

Window: 2026-05-03 → 2026-05-15
Side: OVER (UNDER has effectively zero coverage on total_bases OVER props)
Bands (every FL-applicable):
    [-500,-300]  [-299,-200]  [-199,-150]  [-149,-110]
    [-109,+100]  [+101,+200]  [+201,+∞]

Filter grid (full cross):
    HR20  ∈ {None, 45, 50, 55, 60, 65, 70, 75}
    EDG   ∈ {None, -5, 0, 5, 10}                 (percentage points)
    CV    ∈ {None, 0.70, 0.60, 0.50, 0.45, 0.40}
    μ-line∈ {None, 0, 0.5, 1.0}                  (projection_mu − line)
    TP    ∈ {None, 50, 55, 60, 65}

Output (per band):
    A) Niche baseline (no filter)
    B) Top-15 by ROI (n_graded ≥ 30)
    C) Top-15 by P&L  (n_graded ≥ 30)
    D) Volume-First (ROI ≥ +10 %, ranked by n)
    E) HR20=unrestricted-only summary (best 5 configs)
"""
import asyncio, csv, math, os, sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
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
    # Daily consistency
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


def _sweep(rows: List[Row]) -> List[Dict[str,Any]]:
    out = []
    for h in HR_GRID:
      for e in EDG_GRID:
        for cv in CV_GRID:
          for mu in MU_GRID:
            for tp in TP_GRID:
              sub = [r for r in rows if _passes(r, hr=h, edg=e, cv=cv, mu=mu, tp=tp)]
              a = _agg(sub)
              if (a["gr"] or 0) < 30: continue
              out.append({
                  "label": (f"HR≥{int(h) if h else '-'}/"
                             f"EDG≥{e if e is not None else '-'}/"
                             f"CV≤{cv if cv else '-'}/"
                             f"μ≥{mu if mu is not None else '-'}/"
                             f"TP≥{int(tp) if tp else '-'}"),
                  "hr": h, "edg": e, "cv": cv, "mu": mu, "tp": tp,
                  **a,
              })
    return out


def _print_top(rows, title, key, reverse=True, lim=15):
    print(f"\n  ── {title} ──")
    print(f"    {'config':<48s} {'n':>4s} {'gr':>4s} {'HR%':>6s} "
          f"{'ROI%':>7s} {'CI':>16s} {'P&L':>7s} {'cons':>5s}")
    rs = sorted([r for r in rows if r[key] is not None],
                 key=lambda r: r[key], reverse=reverse)[:lim]
    for r in rs:
        ci = f"[{r['roi_lo']},{r['roi_hi']}]" if r["roi_lo"] is not None else "-"
        print(f"    {r['label'][:48]:<48s} {r['n']:>4d} {r['gr']:>4d} "
              f"{str(r['hr']):>6s} {str(r['roi']):>7s} {ci[:16]:>16s} "
              f"{r['pnl']:>7.2f} {str(r['consist']):>5s}")


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    serials = [f"GSS-MLB-2026{m:02d}{d:02d}-FRON-POOL"
               for m in [5] for d in range(3,16)]
    by_band: Dict[str, List[Row]] = defaultdict(list)
    async for d in db.mlb_test_outputs.find(
        {"replay_serial":{"$in":serials},
         "stat_family":"total_bases",
         "routed_tier":"front_lines",
         "side":"OVER"},
        projection={"_id":0}):
        odds = d.get("odds")
        if odds is None: continue
        b = _band(odds)
        line = d.get("line"); mu = d.get("projection_mu")
        mu_gap = (mu - line) if (mu is not None and line is not None) else None
        by_band[b].append(Row(
            odds=odds, cv=d.get("cv"),
            edge_pp=_edge_pp(d.get("edge")),
            hr_l20=d.get("hit_rate_l20"),
            tp=d.get("tp"),
            mu_gap=mu_gap,
            grade=d.get("grade_status") or "not_qualified",
            pnl=d.get("profit_units") or 0.0,
            stake=d.get("stake_units") or 0.0,
            game_date=d.get("game_date") or "",
        ))
    c.close()
    print("══════ FL total_bases OVER — pool overview ══════")
    total_n=total_g=0
    for b in BAND_ORDER:
        n = len(by_band.get(b, []))
        g = sum(1 for r in by_band.get(b, []) if r.grade in ("win","loss","push"))
        print(f"  {b:<14s} n={n:>5d}  gr={g:>5d}")
        total_n += n; total_g += g
    print(f"  {'TOTAL':<14s} n={total_n:>5d}  gr={total_g:>5d}")

    summary_rows = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_csv = f"/app/backend/audits/microgrid_fl_tb_OVER_all_bands_{stamp}.csv"
    csv_rows = [["band","cfg","hr_min","edg_min","cv_max","mu_min","tp_min",
                 "n_total","n_graded","wins","losses","HR_pct","ROI_pct",
                 "ROI_lo","ROI_hi","P_and_L","consist","grd_days"]]

    for b in BAND_ORDER:
        rows = by_band.get(b, [])
        if not rows: continue
        print(f"\n\n{'═'*120}")
        print(f"  BAND {b}   ({len(rows)} rows / "
              f"{sum(1 for r in rows if r.grade in ('win','loss','push'))} graded)")
        print(f"{'═'*120}")
        a = _agg(rows)
        print(f"  ── A) Raw band baseline (no filter): n={a['n']}/{a['gr']}  "
              f"HR={a['hr']}%  ROI={a['roi']}% [CI {a['roi_lo']},{a['roi_hi']}]  "
              f"P&L={a['pnl']}  consist={a['consist']} ({a['grd_days']}d)")
        if (a["gr"] or 0) < 30:
            print("    (skipping micro-grid: graded < 30)")
            continue
        results = _sweep(rows)
        print(f"  ── B) Generated {len(results)} qualified configs "
              f"(n_graded ≥ 30) from {len(HR_GRID)*len(EDG_GRID)*len(CV_GRID)*len(MU_GRID)*len(TP_GRID)}-cell grid")
        _print_top(results, "TOP-15 by ROI", "roi")
        _print_top(results, "TOP-15 by P&L", "pnl")
        # Volume-First (ROI ≥ +10 %)
        vf10 = [r for r in results if (r["roi"] or 0) >= 10.0]
        vf10.sort(key=lambda r: r["gr"], reverse=True)
        print(f"\n  ── D) VOLUME-FIRST (ROI ≥ +10 %, ranked by n_graded) — top-10")
        print(f"    {'config':<48s} {'n':>4s} {'gr':>4s} {'HR%':>6s} "
              f"{'ROI%':>7s} {'P&L':>7s} {'cons':>5s}")
        for r in vf10[:10]:
            print(f"    {r['label'][:48]:<48s} {r['n']:>4d} {r['gr']:>4d} "
                  f"{str(r['hr']):>6s} {str(r['roi']):>7s} {r['pnl']:>7.2f} "
                  f"{str(r['consist']):>5s}")
        # HR20 unrestricted only
        no_hr = [r for r in results if r["hr"] is None]
        no_hr_top = sorted(no_hr, key=lambda r: r["roi"] or -999, reverse=True)[:5]
        print(f"\n  ── E) HR20 unrestricted — Top-5 by ROI")
        for r in no_hr_top:
            print(f"    {r['label'][:48]:<48s} n={r['n']:>3d}/{r['gr']:>3d}  "
                  f"HR={r['hr']}%  ROI={r['roi']}%  P&L={r['pnl']:.2f}  "
                  f"cons={r['consist']}")
        # save band-best to summary
        best_roi = max([r for r in results if r["roi"] is not None],
                        key=lambda r: r["roi"], default=None)
        best_pnl = max(results, key=lambda r: r["pnl"], default=None)
        if best_roi:
            summary_rows.append({"band": b, "type": "best_roi",
                                  "cfg": best_roi["label"], **best_roi})
        if best_pnl:
            summary_rows.append({"band": b, "type": "best_pnl",
                                  "cfg": best_pnl["label"], **best_pnl})
        for r in results:
            csv_rows.append([b, r["label"], r["hr"], r["edg"], r["cv"],
                             r["mu"], r["tp"], r["n"], r["gr"], r["w"],
                             r["l"], r["hr"], r["roi"], r["roi_lo"],
                             r["roi_hi"], r["pnl"], r["consist"],
                             r["grd_days"]])

    # ── CROSS-BAND SUMMARY ─────────────────────────────────────────
    print(f"\n\n{'═'*120}")
    print("  CROSS-BAND BEST CONFIGS")
    print(f"{'═'*120}")
    print(f"  {'band':<14s} {'type':<8s} {'config':<48s} {'n':>4s} {'gr':>4s} "
          f"{'HR%':>6s} {'ROI%':>7s} {'P&L':>7s} {'cons':>5s}")
    for r in summary_rows:
        print(f"  {r['band']:<14s} {r['type']:<8s} {r['cfg'][:48]:<48s} "
              f"{r['n']:>4d} {r['gr']:>4d} {str(r['hr']):>6s} "
              f"{str(r['roi']):>7s} {r['pnl']:>7.2f} {str(r['consist']):>5s}")

    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f); w.writerows(csv_rows)
    print(f"\nSaved CSV → {out_csv}")

asyncio.run(main())
