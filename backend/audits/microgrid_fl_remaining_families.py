"""
All-bands micro-grid sweep — ALL remaining FL stat families, both sides.

Window: 2026-05-03 → 2026-05-15
Routed_tier: front_lines

Targets (already-done excluded):
  runs                  OVER, UNDER
  rbis                  OVER, UNDER
  walks_allowed         OVER, UNDER
  earned_runs           OVER, UNDER
  pitcher_strikeouts    UNDER (OVER already micro-gridded)

Bands tested per (family, side) (skip if < 30 graded in band):
  [-500,-300] [-299,-200] [-199,-150] [-149,-110]
  [-109,+100] [+101,+200] [+201,+inf]

Filter grid (identical to hits/bs/tb sweep):
  HR20 ∈ {None, 45, 50, 55, 60, 65, 70, 75}
  EDG  ∈ {None, -5, 0, 5, 10}
  CV   ∈ {None, 0.70, 0.60, 0.50, 0.45, 0.40}
  μ    ∈ {None, 0, 0.5, 1.0}
  TP   ∈ {None, 50, 55, 60, 65}

Per (family, side, band): print baseline + top-5 ROI + top-5 P&L + volume-first ≥10%.
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
    return x*100.0 if abs(x) < 1.5 else x


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
        n=len(rets); m=sum(rets)/n
        v=sum((x-m)**2 for x in rets)/max(n-1,1)
        se=math.sqrt(v/n)
        roi_lo = round(100*(m-1.96*se),2); roi_hi=round(100*(m+1.96*se),2)
    by_d = defaultdict(list)
    for r in g: by_d[r.game_date].append(r)
    pos=grd_d=0
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
            "pnl": round(pnl,3),"consist":consist,"grd_days":grd_d}


def _passes(r, *, hr, edg, cv, mu, tp):
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


def _sweep(rows):
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
                  "hr":h,"edg":e,"cv":cv,"mu":mu,"tp":tp,
                  **a,
              })
    return out


def _print_top(rows, title, key, reverse=True, lim=5):
    print(f"\n    ── {title} ──")
    print(f"      {'config':<45s} {'n':>4s} {'gr':>4s} {'HR%':>6s} "
          f"{'ROI%':>7s} {'CI':>15s} {'P&L':>7s} {'cons':>5s}")
    rs = sorted([r for r in rows if r[key] is not None],
                 key=lambda r: r[key], reverse=reverse)[:lim]
    for r in rs:
        ci = f"[{r['roi_lo']},{r['roi_hi']}]" if r["roi_lo"] is not None else "-"
        print(f"      {r['label'][:45]:<45s} {r['n']:>4d} {r['gr']:>4d} "
              f"{str(r['hr']):>6s} {str(r['roi']):>7s} {ci[:15]:>15s} "
              f"{r['pnl']:>7.2f} {str(r['consist']):>5s}")


TARGETS = [
    ("runs",               ["OVER","UNDER"]),
    ("rbis",               ["OVER","UNDER"]),
    ("walks_allowed",      ["OVER","UNDER"]),
    ("earned_runs",        ["OVER","UNDER"]),
    ("pitcher_strikeouts", ["UNDER"]),  # OVER already done
]


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    serials = [f"GSS-MLB-2026{m:02d}{d:02d}-FRON-POOL"
               for m in [5] for d in range(3,16)]
    by_fbs: Dict[tuple, List[Row]] = defaultdict(list)
    fams = {f for f,_ in TARGETS}
    async for d in db.mlb_test_outputs.find(
        {"replay_serial":{"$in":serials},
         "stat_family":{"$in":list(fams)},
         "routed_tier":"front_lines"},
        projection={"_id":0}):
        odds = d.get("odds")
        if odds is None: continue
        line = d.get("line"); mu = d.get("projection_mu")
        mu_gap = (mu - line) if (mu is not None and line is not None) else None
        by_fbs[(d.get("stat_family"), d.get("side") or "OVER")].append(Row(
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

    print("\n══════ POOL OVERVIEW (FL routed) ══════")
    for f, sides in TARGETS:
        for s in sides:
            rows = by_fbs.get((f,s), [])
            gr = sum(1 for r in rows if r.grade in ("win","loss","push"))
            print(f"  {f:<22s} {s:<6s}  n={len(rows):>5d}  graded={gr:>5d}")

    cross_summary = []  # (family, side, band, type, cfg, n, gr, hr, roi, pnl, cons)

    for f, sides in TARGETS:
        for s in sides:
            rows = by_fbs.get((f,s), [])
            print(f"\n\n{'═'*120}")
            print(f"  {f.upper()} · side={s}  ({len(rows)} rows / "
                  f"{sum(1 for r in rows if r.grade in ('win','loss','push'))} graded)")
            print(f"{'═'*120}")
            by_band: Dict[str, List[Row]] = defaultdict(list)
            for r in rows: by_band[_band(r.odds)].append(r)
            for b in BAND_ORDER:
                rs = by_band.get(b, [])
                if not rs: continue
                ag = _agg(rs)
                if (ag["gr"] or 0) < 30:
                    print(f"\n  ── band {b}: n={ag['n']}/{ag['gr']} — "
                          f"skipped (graded < 30)")
                    continue
                print(f"\n  ── band {b}  (n={ag['n']}/{ag['gr']})")
                print(f"    A) baseline (no filter): HR={ag['hr']}% "
                      f"ROI={ag['roi']}% [CI {ag['roi_lo']},{ag['roi_hi']}] "
                      f"P&L={ag['pnl']} consist={ag['consist']}")
                results = _sweep(rs)
                print(f"    B) {len(results)} qualified configs (gr≥30)")
                _print_top(results, "TOP-5 by ROI",  "roi")
                _print_top(results, "TOP-5 by P&L",  "pnl")
                # volume-first
                vf = sorted([r for r in results if (r["roi"] or 0) >= 10.0],
                             key=lambda r: r["gr"], reverse=True)[:5]
                if vf:
                    print(f"\n    D) Volume-first (ROI ≥ +10 %, top-5 by n):")
                    print(f"      {'config':<45s} {'n':>4s} {'gr':>4s} "
                          f"{'HR%':>6s} {'ROI%':>7s} {'P&L':>7s} {'cons':>5s}")
                    for r in vf:
                        print(f"      {r['label'][:45]:<45s} {r['n']:>4d} "
                              f"{r['gr']:>4d} {str(r['hr']):>6s} "
                              f"{str(r['roi']):>7s} {r['pnl']:>7.2f} "
                              f"{str(r['consist']):>5s}")
                # cross summary best (only if positive)
                if results:
                    best_pnl = max(results, key=lambda r: r["pnl"])
                    best_roi = max((r for r in results if r["roi"] is not None),
                                    key=lambda r: r["roi"], default=None)
                    cross_summary.append((f, s, b, "best_pnl", best_pnl))
                    if best_roi:
                        cross_summary.append((f, s, b, "best_roi", best_roi))

    # Final cross summary (positive configs only)
    print(f"\n\n{'═'*120}")
    print("  CROSS-FAMILY POSITIVE CELLS  (only configs with ROI > 0)")
    print(f"{'═'*120}")
    print(f"  {'family':<22s} {'side':<6s} {'band':<14s} {'type':<8s} "
          f"{'config':<45s} {'n':>4s} {'gr':>4s} {'HR%':>6s} {'ROI%':>7s} "
          f"{'P&L':>7s} {'cons':>5s}")
    for fam, s, band, typ, r in cross_summary:
        if (r["roi"] or -999) <= 0: continue
        print(f"  {fam:<22s} {s:<6s} {band:<14s} {typ:<8s} {r['label'][:45]:<45s} "
              f"{r['n']:>4d} {r['gr']:>4d} {str(r['hr']):>6s} "
              f"{str(r['roi']):>7s} {r['pnl']:>7.2f} {str(r['consist']):>5s}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_csv = f"/app/backend/audits/microgrid_fl_remaining_families_{stamp}.csv"
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["family","side","band","type","cfg","hr_min","edg_min",
                     "cv_max","mu_min","tp_min","n","gr","wins","losses",
                     "HR_pct","ROI_pct","ROI_lo","ROI_hi","P_and_L",
                     "consist","grd_days"])
        for fam, s, band, typ, r in cross_summary:
            w.writerow([fam,s,band,typ,r["label"],r["hr"],r["edg"],r["cv"],
                         r["mu"],r["tp"],r["n"],r["gr"],r["w"],r["l"],
                         r["hr"],r["roi"],r["roi_lo"],r["roi_hi"],
                         r["pnl"],r["consist"],r["grd_days"]])
    print(f"\nSaved CSV → {out_csv}")

asyncio.run(main())
