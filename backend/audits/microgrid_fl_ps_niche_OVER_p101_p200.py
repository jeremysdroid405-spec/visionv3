"""
Focused niche sweep: FL pitcher_strikeouts, OVER side, odds_band [+101, +200].
Grid:
    HR20  >= 45 / 50 / 55 / 60 / 65
    EDGE  >= -5 / 0 / 5 / 10                  (in percentage points)
    CV    <= 0.70 / 0.60 / 0.50 / 0.45 / 0.40
    μ-line>= 0   / 0.5 / 1.0 / 1.5             (projection_mu - line)
    TP    >= 50 / 55 / 60 / 65
Total combos: 5*4*5*4*4 = 1600.
For each combo with n_graded>=20 emit n/HR/ROI/P&L/consistency. Then leaderboards.
"""
import asyncio, csv, os, sys, math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


def _odds_band(o):
    if o is None: return "unknown"
    o = int(o)
    if o <= -300: return "[-500, -300]"
    if o <= -200: return "[-299, -200]"
    if o <= -150: return "[-199, -150]"
    if o <= -110: return "[-149, -110]"
    if o <=  100: return "[-109, +100]"
    if o <=  200: return "[+101, +200]"
    return "[+201, +∞]"


@dataclass
class Row:
    odds: Optional[float]
    cv: Optional[float]
    edge_pp: Optional[float]
    hr_l20: Optional[float]
    tp: Optional[float]
    mu_gap: Optional[float]   # projection_mu − line
    line: Optional[float]
    mu: Optional[float]
    grade: str
    pnl: float
    stake: float
    game_date: str


def _edge_pp(v):
    if v is None: return None
    try: x = float(v)
    except: return None
    return x*100.0 if abs(x) < 1.5 else x


def _agg(rows):
    g = [r for r in rows if r.grade in ("win","loss","push")]
    w = sum(1 for r in g if r.grade=="win")
    l = sum(1 for r in g if r.grade=="loss")
    pnl = sum(r.pnl for r in g)
    stk = sum(r.stake for r in g)
    hr = (100*w/(w+l)) if (w+l) else None
    roi = (100*pnl/stk) if stk else None
    # ROI CI via per-pick SE
    rets = [r.pnl/r.stake for r in g if r.stake]
    roi_lo = roi_hi = None
    if rets:
        n=len(rets); m=sum(rets)/n; v=sum((x-m)**2 for x in rets)/max(n-1,1)
        se=math.sqrt(v/n)
        roi_lo = round(100*(m-1.96*se),2); roi_hi=round(100*(m+1.96*se),2)
    return {"n":len(rows),"gr":len(g),"w":w,"l":l,
            "hr": round(hr,2) if hr is not None else None,
            "roi": round(roi,2) if roi is not None else None,
            "roi_lo": roi_lo, "roi_hi": roi_hi,
            "pnl": round(pnl,3)}


def _daily(rows):
    by_d = defaultdict(list)
    for r in rows: by_d[r.game_date].append(r)
    pos=grd=0
    for rs in by_d.values():
        a=_agg(rs)
        if (a["gr"] or 0)==0: continue
        grd+=1
        if (a["roi"] or 0)>=0: pos+=1
    return (round(pos/grd,3) if grd else None, grd)


def _passes(r, *, hr, edg, cv, mu, tp):
    if hr is not None and (r.hr_l20 is None or r.hr_l20 < hr): return False
    if edg is not None and (r.edge_pp is None or r.edge_pp < edg): return False
    if cv  is not None and (r.cv      is None or r.cv > cv): return False
    if mu  is not None and (r.mu_gap  is None or r.mu_gap < mu): return False
    if tp  is not None and (r.tp      is None or r.tp < tp): return False
    return True


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    serials = [f"GSS-MLB-2026{m:02d}{d:02d}-FRON-POOL"
               for m in [5] for d in range(3,16)]
    rows: List[Row] = []
    async for d in db.mlb_test_outputs.find(
        {"replay_serial":{"$in":serials},
         "stat_family":"pitcher_strikeouts",
         "routed_tier":"front_lines",
         "side":"OVER"},
        projection={"_id":0}):
        odds = d.get("odds")
        if odds is None: continue
        if not (101 <= int(odds) <= 200): continue
        line = d.get("line")
        mu   = d.get("projection_mu")
        mu_gap = (mu - line) if (mu is not None and line is not None) else None
        rows.append(Row(
            odds=odds, cv=d.get("cv"),
            edge_pp=_edge_pp(d.get("edge")),
            hr_l20=d.get("hit_rate_l20"),
            tp=d.get("tp"),
            mu_gap=mu_gap, line=line, mu=mu,
            grade=d.get("grade_status") or "not_qualified",
            pnl=d.get("profit_units") or 0.0,
            stake=d.get("stake_units") or 0.0,
            game_date=d.get("game_date") or "",
        ))
    c.close()
    print(f"Loaded {len(rows)} FL ps rows  side=OVER  band=[+101,+200]")
    print(f"  graded={sum(1 for r in rows if r.grade in ('win','loss','push'))}")

    # Distribution check
    print(f"  rows with TP populated:    {sum(1 for r in rows if r.tp is not None)}/{len(rows)}")
    print(f"  rows with mu_gap populated:{sum(1 for r in rows if r.mu_gap is not None)}/{len(rows)}")
    print(f"  rows with edge_pp:         {sum(1 for r in rows if r.edge_pp is not None)}/{len(rows)}")
    print(f"  rows with hr_l20:          {sum(1 for r in rows if r.hr_l20 is not None)}/{len(rows)}")

    a0 = _agg(rows)
    print(f"\n══ Niche BASELINE (no filter): n={a0['n']}/{a0['gr']}  HR={a0['hr']}%  ROI={a0['roi']}%  [CI {a0['roi_lo']},{a0['roi_hi']}]  P&L={a0['pnl']}")

    # Sweep
    HR  = [None, 45, 50, 55, 60, 65]   # include 'None' baseline
    EDG = [None, -5, 0, 5, 10]
    CV  = [None, 0.70, 0.60, 0.50, 0.45, 0.40]
    MU  = [None, 0, 0.5, 1.0, 1.5]
    TP  = [None, 50, 55, 60, 65]

    results: List[Dict[str,Any]] = []
    for h in HR:
        for e in EDG:
            for cv in CV:
                for mu in MU:
                    for t in TP:
                        sub = [r for r in rows if _passes(r, hr=h, edg=e, cv=cv, mu=mu, tp=t)]
                        a = _agg(sub)
                        if (a["gr"] or 0) < 20: continue
                        c_, gd = _daily(sub)
                        results.append({
                            "label": f"HR≥{h if h is not None else '-'}/EDG≥{e if e is not None else '-'}/CV≤{cv if cv is not None else '-'}/μ≥{mu if mu is not None else '-'}/TP≥{t if t is not None else '-'}",
                            "hr_min":h,"edg_min":e,"cv_max":cv,"mu_min":mu,"tp_min":t,
                            "n_total":a["n"],"n_graded":a["gr"],
                            "wins":a["w"],"losses":a["l"],
                            "HR_pct":a["hr"], "ROI_pct":a["roi"],
                            "ROI_lo":a["roi_lo"],"ROI_hi":a["roi_hi"],
                            "P_and_L":a["pnl"],
                            "consist":c_,"graded_days":gd,
                        })

    print(f"\nGenerated {len(results)} qualified configs (graded ≥ 20) over {5*4*5*4*4}-cell grid")

    # ── Leaderboards ─────────────────────────────────────────────
    by_roi = sorted([r for r in results if r["ROI_pct"] is not None],
                     key=lambda r: r["ROI_pct"], reverse=True)
    by_pnl = sorted(results, key=lambda r: r["P_and_L"], reverse=True)

    def _print(rows, title, lim=25):
        print(f"\n══════ {title} ══════")
        print(f"  {'config':<70s} {'n':>5s} {'gr':>4s} {'HR':>5s} {'ROI':>6s} {'CI':>16s} {'P&L':>7s} {'cons':>5s}")
        for r in rows[:lim]:
            ci = f"[{r['ROI_lo']},{r['ROI_hi']}]"
            print(f"  {r['label'][:70]:<70s} {r['n_total']:>5d} {r['n_graded']:>4d} "
                  f"{str(r['HR_pct']):>5s} {str(r['ROI_pct']):>6s} {ci[:16]:>16s} "
                  f"{r['P_and_L']:>7.2f} {str(r['consist']):>5s}")

    _print(by_roi, "TOP-25 by ROI")
    _print(by_pnl, "TOP-25 by P&L")

    # Volume-first @ ROI ≥ 15%
    vf15 = sorted([r for r in results if (r["ROI_pct"] or 0) >= 15.0],
                  key=lambda r: r["n_graded"], reverse=True)
    _print(vf15, "VOLUME-FIRST  (ROI ≥ +15%, ranked by n)")

    # Volume-first @ ROI ≥ 25%
    vf25 = sorted([r for r in results if (r["ROI_pct"] or 0) >= 25.0],
                  key=lambda r: r["n_graded"], reverse=True)
    _print(vf25, "VOLUME-FIRST  (ROI ≥ +25%, ranked by n)")

    # Best ROI with statistical significance (lower CI > 0)
    sig = [r for r in results if r["ROI_lo"] is not None and r["ROI_lo"] > 0]
    sig.sort(key=lambda r: r["ROI_pct"] or 0, reverse=True)
    _print(sig, "STATISTICALLY SIGNIFICANT (ROI CI-low > 0)")

    # CSV
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out = f"/app/backend/audits/microgrid_fl_ps_OVER_p101_p200_{stamp}.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        cols = ["label","hr_min","edg_min","cv_max","mu_min","tp_min",
                "n_total","n_graded","wins","losses","HR_pct","ROI_pct",
                "ROI_lo","ROI_hi","P_and_L","consist","graded_days"]
        w.writerow(cols)
        for r in by_roi: w.writerow([r[c] for c in cols])
    print(f"\nSaved CSV → {out}")

asyncio.run(main())
