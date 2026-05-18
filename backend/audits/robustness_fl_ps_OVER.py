"""
Same-window robustness validation for FL pitcher_strikeouts OVER.

Window: 2026-05-03 → 2026-05-15  (no future dates)
Bands:  [-149,-110]  /  [-109,+100]  /  [+101,+200]
Configs (each combined with EDG ≥ 5, HR20 unrestricted FIRST, HR20 ≥ 50 SECONDARY):
    cv_max ∈ {0.70, 0.50, 0.40}
    mu_min ∈ {None, 0, 0.5}
Folds:  A) 2026-05-03..06   B) 2026-05-07..10   C) 2026-05-11..15
Output: graded, W/L, HR, ROI, P&L, daily consistency, max win day, max loss day.
For [+101,+200]: also a sub-band breakdown:
    +101..+120 / +121..+140 / +141..+170 / +171..+200
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
    if o is None: return None
    o = int(o)
    if -149 <= o <= -110:  return "[-149,-110]"
    if -109 <= o <=  100:  return "[-109,+100]"
    if  101 <= o <=  200:  return "[+101,+200]"
    return None  # we only care about these 3 bands

def _subband_p101_200(o):
    o = int(o)
    if 101 <= o <= 120: return "+101..+120"
    if 121 <= o <= 140: return "+121..+140"
    if 141 <= o <= 170: return "+141..+170"
    if 171 <= o <= 200: return "+171..+200"
    return None


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
    mu_gap: Optional[float]
    grade: str
    pnl: float
    stake: float
    game_date: str
    band: Optional[str]
    sub_p101: Optional[str]


def _fold(d: str) -> str:
    if d <= "2026-05-06": return "A: 05-03..06"
    if d <= "2026-05-10": return "B: 05-07..10"
    return "C: 05-11..15"


def _agg(rows: List[Row]) -> Dict[str,Any]:
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
    # Daily max-win / max-loss day
    by_d: Dict[str,float] = defaultdict(float)
    by_d_grd: Dict[str,int] = defaultdict(int)
    for r in g:
        by_d[r.game_date] += r.pnl
        by_d_grd[r.game_date] += 1
    if by_d:
        max_d = max(by_d.items(), key=lambda kv: kv[1])
        min_d = min(by_d.items(), key=lambda kv: kv[1])
        max_win_day = f"{max_d[0]}/{by_d_grd[max_d[0]]}p/+{max_d[1]:.2f}u"
        max_los_day = f"{min_d[0]}/{by_d_grd[min_d[0]]}p/{min_d[1]:+.2f}u"
    else:
        max_win_day = max_los_day = "-"
    # Consistency: positive-ROI days / graded days
    pos=grd_d=0
    for d, rs in defaultdict(list, {d: [r for r in g if r.game_date==d] for d in by_d}).items():
        a2 = _agg_basic(rs)
        if a2["gr"]==0: continue
        grd_d += 1
        if (a2["roi"] or 0) >= 0: pos += 1
    consist = round(pos/grd_d,3) if grd_d else None
    return {
        "n": len(rows), "gr": len(g), "w": w, "l": l,
        "hr": round(hr,2) if hr is not None else None,
        "roi": round(roi,2) if roi is not None else None,
        "roi_lo": roi_lo, "roi_hi": roi_hi,
        "pnl": round(pnl,3),
        "max_win_day": max_win_day, "max_loss_day": max_los_day,
        "consist": consist, "grd_days": grd_d,
    }


def _agg_basic(rows: List[Row]) -> Dict[str,Any]:
    g = [r for r in rows if r.grade in ("win","loss","push")]
    pnl = sum(r.pnl for r in g); stk = sum(r.stake for r in g)
    roi = (100*pnl/stk) if stk else None
    return {"gr": len(g), "pnl": round(pnl,3),
            "roi": round(roi,2) if roi is not None else None}


def _passes(r: Row, *, edg, cv, mu, hr) -> bool:
    if edg is not None and (r.edge_pp is None or r.edge_pp < edg): return False
    if cv  is not None and (r.cv      is None or r.cv > cv):       return False
    if mu  is not None and (r.mu_gap  is None or r.mu_gap < mu):   return False
    if hr  is not None and (r.hr_l20  is None or r.hr_l20 < hr):   return False
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
        b = _band(odds)
        if b is None: continue
        line = d.get("line"); mu = d.get("projection_mu")
        mu_gap = (mu - line) if (mu is not None and line is not None) else None
        rows.append(Row(
            odds=odds, cv=d.get("cv"),
            edge_pp=_edge_pp(d.get("edge")),
            hr_l20=d.get("hit_rate_l20"),
            mu_gap=mu_gap,
            grade=d.get("grade_status") or "not_qualified",
            pnl=d.get("profit_units") or 0.0,
            stake=d.get("stake_units") or 0.0,
            game_date=d.get("game_date") or "",
            band=b,
            sub_p101=(_subband_p101_200(odds) if b=="[+101,+200]" else None),
        ))
    c.close()
    print(f"Loaded {len(rows)} FL ps OVER rows in tested bands  "
          f"(graded={sum(1 for r in rows if r.grade in ('win','loss','push'))})")
    by_band = defaultdict(list)
    for r in rows: by_band[r.band].append(r)
    for b in ("[-149,-110]","[-109,+100]","[+101,+200]"):
        sub = by_band[b]
        print(f"  {b}: n={len(sub)} graded={sum(1 for r in sub if r.grade in ('win','loss','push'))}")

    # Configurations
    CFG = []
    for hr_min in (None, 50.0):
        for cv in (0.70, 0.50, 0.40):
            for mu in (None, 0.0, 0.5):
                CFG.append({"hr_min": hr_min, "edg_min": 5.0, "cv_max": cv, "mu_min": mu})

    BANDS = ["[-149,-110]","[-109,+100]","[+101,+200]"]
    FOLDS = ["A: 05-03..06","B: 05-07..10","C: 05-11..15"]
    all_dates = sorted({r.game_date for r in rows})

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_csv = f"/app/backend/audits/robustness_fl_ps_OVER_{stamp}.csv"
    csv_rows: List[List[Any]] = []
    csv_rows.append(["band","fold","HR20","EDG_min","CV_max","mu_min",
                     "n_total","n_graded","wins","losses",
                     "HR_pct","ROI_pct","ROI_lo","ROI_hi","P_and_L",
                     "consist","grd_days","max_win_day","max_loss_day"])

    print("\n" + "="*128)
    print("CONFIG MATRIX  (EDG ≥ 5 always)   |   `HR20=-` = unrestricted")
    print("="*128)

    for band in BANDS:
        sub_band = by_band[band]
        print(f"\n┌──────────────────────────  BAND {band}  "
              f"(n_total={len(sub_band)} / graded={sum(1 for r in sub_band if r.grade in ('win','loss','push'))})  ──────────────────────────┐")
        print(f"  {'fold':<14s} {'HR20':<5s} {'CV≤':<5s} {'μ≥':<5s}  "
              f"{'n':>4s} {'gr':>4s} {'W':>3s} {'L':>3s} {'HR%':>6s} "
              f"{'ROI%':>7s} {'CI':>16s} {'P&L':>7s} {'cons':>5s} "
              f"{'maxWinDay':<22s} {'maxLossDay':<22s}")

        for fold in ["OVERALL"] + FOLDS:
            if fold == "OVERALL":
                fold_rows = sub_band
            else:
                fold_rows = [r for r in sub_band if _fold(r.game_date) == fold]
            for cfg in CFG:
                sub = [r for r in fold_rows if _passes(r, **{"edg":cfg["edg_min"],
                                                                "cv":cfg["cv_max"],
                                                                "mu":cfg["mu_min"],
                                                                "hr":cfg["hr_min"]})]
                a = _agg(sub)
                if a["gr"] == 0: continue
                hr_lbl = "-" if cfg["hr_min"] is None else f"≥{int(cfg['hr_min'])}"
                mu_lbl = "-" if cfg["mu_min"] is None else f"≥{cfg['mu_min']}"
                fold_lbl = fold[:14]
                roi_ci = (f"[{a['roi_lo']},{a['roi_hi']}]"
                           if a["roi_lo"] is not None else "-")
                hr_pct = str(a["hr"]) if a["hr"] is not None else "-"
                roi_pct = str(a["roi"]) if a["roi"] is not None else "-"
                print(f"  {fold_lbl:<14s} {hr_lbl:<5s} {cfg['cv_max']:<5} {mu_lbl:<5s}  "
                      f"{a['n']:>4d} {a['gr']:>4d} {a['w']:>3d} {a['l']:>3d} "
                      f"{hr_pct:>6s} {roi_pct:>7s} {roi_ci[:16]:>16s} "
                      f"{a['pnl']:>7.2f} {str(a['consist']):>5s} "
                      f"{a['max_win_day'][:22]:<22s} {a['max_loss_day'][:22]:<22s}")
                csv_rows.append([band, fold, hr_lbl, 5, cfg["cv_max"], mu_lbl,
                                 a["n"], a["gr"], a["w"], a["l"],
                                 a["hr"], a["roi"], a["roi_lo"], a["roi_hi"],
                                 a["pnl"], a["consist"], a["grd_days"],
                                 a["max_win_day"], a["max_loss_day"]])

    # ── Sub-band breakdown for +101..+200 ─────────────────────────
    print("\n" + "="*128)
    print("SUB-BAND BREAKDOWN inside [+101,+200]  — for SELECTED configs")
    print("="*128)
    sub_rows = by_band["[+101,+200]"]
    SUB_ORDER = ["+101..+120","+121..+140","+141..+170","+171..+200"]
    # We'll evaluate the 6 most informative configs (HR20=- + 3 CVs + with/without μ≥0)
    SELECT = [
        {"hr_min":None,"edg_min":5.0,"cv_max":0.70,"mu_min":None},
        {"hr_min":None,"edg_min":5.0,"cv_max":0.70,"mu_min":0.0},
        {"hr_min":None,"edg_min":5.0,"cv_max":0.50,"mu_min":None},
        {"hr_min":None,"edg_min":5.0,"cv_max":0.50,"mu_min":0.0},
        {"hr_min":None,"edg_min":5.0,"cv_max":0.40,"mu_min":None},
        {"hr_min":None,"edg_min":5.0,"cv_max":0.40,"mu_min":0.0},
    ]
    for cfg in SELECT:
        hr_lbl = "-" if cfg["hr_min"] is None else f"≥{int(cfg['hr_min'])}"
        mu_lbl = "-" if cfg["mu_min"] is None else f"≥{cfg['mu_min']}"
        sub_pool = [r for r in sub_rows if _passes(r, **{"edg":cfg["edg_min"],
                                                            "cv":cfg["cv_max"],
                                                            "mu":cfg["mu_min"],
                                                            "hr":cfg["hr_min"]})]
        a_all = _agg(sub_pool)
        print(f"\n── HR20{hr_lbl} ∧ EDG≥5 ∧ CV≤{cfg['cv_max']} ∧ μ{mu_lbl}  "
              f"(all sub-bands  n={a_all['n']}/{a_all['gr']}  ROI={a_all['roi']}%  P&L={a_all['pnl']})")
        print(f"    {'sub-band':<14s} {'n':>4s} {'gr':>4s} {'W':>3s} {'L':>3s} "
              f"{'HR%':>6s} {'ROI%':>7s} {'P&L':>7s} {'cons':>5s} {'maxWinDay':<22s}")
        by_sub: Dict[str,List[Row]] = defaultdict(list)
        for r in sub_pool:
            if r.sub_p101: by_sub[r.sub_p101].append(r)
        for sb in SUB_ORDER:
            rs = by_sub.get(sb, [])
            if not rs:
                print(f"    {sb:<14s}    (no picks)")
                continue
            a = _agg(rs)
            hr_pct = str(a["hr"]) if a["hr"] is not None else "-"
            roi_pct = str(a["roi"]) if a["roi"] is not None else "-"
            print(f"    {sb:<14s} {a['n']:>4d} {a['gr']:>4d} {a['w']:>3d} {a['l']:>3d} "
                  f"{hr_pct:>6s} {roi_pct:>7s} {a['pnl']:>7.2f} "
                  f"{str(a['consist']):>5s} {a['max_win_day'][:22]:<22s}")
            csv_rows.append([f"{cfg['cv_max']}/{mu_lbl}/sub:{sb}", "OVERALL",
                             hr_lbl, 5, cfg["cv_max"], mu_lbl,
                             a["n"], a["gr"], a["w"], a["l"], a["hr"],
                             a["roi"], a["roi_lo"], a["roi_hi"], a["pnl"],
                             a["consist"], a["grd_days"],
                             a["max_win_day"], a["max_loss_day"]])

    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f); w.writerows(csv_rows)
    print(f"\nSaved CSV → {out_csv}")

asyncio.run(main())
