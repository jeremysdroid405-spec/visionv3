"""
Same-window robustness validation pipeline — generalized for multiple stat families.

Window: 2026-05-03 → 2026-05-15  (no future dates)
Routed tier: front_lines
Targets (chosen from prior unfiltered FL sweep):
    earned_runs       UNDER  bands: [-149,-110], [-109,+100], [+101,+200]
    hits              OVER   bands: [-299,-200], [-199,-150], [-149,-110]
    batter_strikeouts OVER   bands: [-299,-200], [-199,-150], [-149,-110]
    total_bases       OVER   bands: [-299,-200], [-199,-150], [-149,-110]

For each (family/side/band) test these 6 configurations:
    1) EDG≥5  ∧ CV≤0.70                  (loose)
    2) EDG≥5  ∧ CV≤0.50                  (medium)
    3) EDG≥10 ∧ CV≤0.70                  (edge-strict / loose CV)
    4) EDG≥10 ∧ CV≤0.50                  (edge-strict / medium CV)
    5) HR20≥65 ∧ EDG≥5  ∧ CV≤0.70        (HR-filtered loose)
    6) HR20≥75 ∧ EDG≥10 ∧ CV≤0.50        (production-style strict)

Folds: A 05-03..06   B 05-07..10   C 05-11..15

Output per (family/side/band/cfg/fold): n, gr, W, L, HR, ROI, P&L, consist,
max-win-day, max-loss-day. CSV + console.
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


# ── Bands ─────────────────────────────────────────────────────────
def _band(o):
    if o is None: return None
    o = int(o)
    if -299 <= o <= -200: return "[-299,-200]"
    if -199 <= o <= -150: return "[-199,-150]"
    if -149 <= o <= -110: return "[-149,-110]"
    if -109 <= o <=  100: return "[-109,+100]"
    if  101 <= o <=  200: return "[+101,+200]"
    return None


# ── Family targets ────────────────────────────────────────────────
TARGETS = [
    ("earned_runs",       "UNDER", ["[-149,-110]", "[-109,+100]", "[+101,+200]"]),
    ("hits",              "OVER",  ["[-299,-200]", "[-199,-150]", "[-149,-110]"]),
    ("batter_strikeouts", "OVER",  ["[-299,-200]", "[-199,-150]", "[-149,-110]"]),
    ("total_bases",       "OVER",  ["[-299,-200]", "[-199,-150]", "[-149,-110]"]),
]


# ── Filter configurations ─────────────────────────────────────────
CONFIGS = [
    {"label": "loose       EDG≥5  CV≤0.70",        "hr": None, "edg": 5.0,  "cv": 0.70},
    {"label": "medium      EDG≥5  CV≤0.50",        "hr": None, "edg": 5.0,  "cv": 0.50},
    {"label": "edge-loose  EDG≥10 CV≤0.70",        "hr": None, "edg": 10.0, "cv": 0.70},
    {"label": "edge-med    EDG≥10 CV≤0.50",        "hr": None, "edg": 10.0, "cv": 0.50},
    {"label": "HR65 loose  HR≥65 EDG≥5  CV≤0.70",  "hr": 65.0, "edg": 5.0,  "cv": 0.70},
    {"label": "PROD-strict HR≥75 EDG≥10 CV≤0.50",  "hr": 75.0, "edg": 10.0, "cv": 0.50},
]


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
    grade: str
    pnl: float
    stake: float
    game_date: str
    band: Optional[str]


def _fold(d: str) -> str:
    if d <= "2026-05-06": return "A: 05-03..06"
    if d <= "2026-05-10": return "B: 05-07..10"
    return "C: 05-11..15"


def _agg(rows: List[Row]) -> Dict[str, Any]:
    g = [r for r in rows if r.grade in ("win", "loss", "push")]
    w = sum(1 for r in g if r.grade == "win")
    l = sum(1 for r in g if r.grade == "loss")
    pnl = sum(r.pnl for r in g); stk = sum(r.stake for r in g)
    hr = (100 * w / (w + l)) if (w + l) else None
    roi = (100 * pnl / stk) if stk else None
    rets = [r.pnl / r.stake for r in g if r.stake]
    roi_lo = roi_hi = None
    if rets:
        n = len(rets); m = sum(rets) / n
        v = sum((x - m) ** 2 for x in rets) / max(n - 1, 1)
        se = math.sqrt(v / n)
        roi_lo = round(100 * (m - 1.96 * se), 2)
        roi_hi = round(100 * (m + 1.96 * se), 2)
    by_d: Dict[str, float] = defaultdict(float)
    by_d_grd: Dict[str, int] = defaultdict(int)
    for r in g:
        by_d[r.game_date] += r.pnl
        by_d_grd[r.game_date] += 1
    if by_d:
        max_d = max(by_d.items(), key=lambda kv: kv[1])
        min_d = min(by_d.items(), key=lambda kv: kv[1])
        mwd = f"{max_d[0]}/{by_d_grd[max_d[0]]}p/{max_d[1]:+.2f}u"
        mld = f"{min_d[0]}/{by_d_grd[min_d[0]]}p/{min_d[1]:+.2f}u"
    else:
        mwd = mld = "-"
    # consistency = positive-ROI graded days / graded days
    by_d_rows: Dict[str, List[Row]] = defaultdict(list)
    for r in g: by_d_rows[r.game_date].append(r)
    pos = grd_d = 0
    for rs in by_d_rows.values():
        gr2 = [r for r in rs if r.grade in ("win", "loss", "push")]
        if not gr2: continue
        stk2 = sum(r.stake for r in gr2); pnl2 = sum(r.pnl for r in gr2)
        roi2 = (100 * pnl2 / stk2) if stk2 else None
        grd_d += 1
        if (roi2 or 0) >= 0: pos += 1
    consist = round(pos / grd_d, 3) if grd_d else None
    return {"n": len(rows), "gr": len(g), "w": w, "l": l,
            "hr": round(hr, 2) if hr is not None else None,
            "roi": round(roi, 2) if roi is not None else None,
            "roi_lo": roi_lo, "roi_hi": roi_hi,
            "pnl": round(pnl, 3),
            "mwd": mwd, "mld": mld,
            "consist": consist, "grd_days": grd_d}


def _passes(r: Row, *, edg, cv, hr) -> bool:
    if edg is not None and (r.edge_pp is None or r.edge_pp < edg): return False
    if cv  is not None and (r.cv      is None or r.cv > cv):       return False
    if hr  is not None and (r.hr_l20  is None or r.hr_l20 < hr):   return False
    return True


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    serials = [f"GSS-MLB-2026{m:02d}{d:02d}-FRON-POOL"
               for m in [5] for d in range(3, 16)]
    print("Loading FL pool rows from all-gates-disabled sweep...")

    by_fb: Dict[Tuple[str, str], List[Row]] = defaultdict(list)
    families_of_interest = {fam for fam, _, _ in TARGETS}
    async for d in db.mlb_test_outputs.find(
        {"replay_serial": {"$in": serials},
         "stat_family": {"$in": list(families_of_interest)},
         "routed_tier": "front_lines"},
        projection={"_id": 0}):
        odds = d.get("odds")
        if odds is None: continue
        side = d.get("side") or "OVER"
        fam = d.get("stat_family")
        b = _band(odds)
        if b is None: continue
        r = Row(
            odds=odds, cv=d.get("cv"),
            edge_pp=_edge_pp(d.get("edge")),
            hr_l20=d.get("hit_rate_l20"),
            grade=d.get("grade_status") or "not_qualified",
            pnl=d.get("profit_units") or 0.0,
            stake=d.get("stake_units") or 0.0,
            game_date=d.get("game_date") or "",
            band=b,
        )
        by_fb[(fam, side)].append(r)
    c.close()

    # Overview
    print("\n── POOL OVERVIEW ─────────────────────────────────")
    for fam, side, bands in TARGETS:
        all_rows = by_fb.get((fam, side), [])
        in_bands = [r for r in all_rows if r.band in bands]
        gr = sum(1 for r in in_bands if r.grade in ("win", "loss", "push"))
        print(f"  {fam:<22s} {side:<6s}  rows_in_bands={len(in_bands):>4d}  graded={gr:>4d}")
        for b in bands:
            sub = [r for r in all_rows if r.band == b]
            sg = sum(1 for r in sub if r.grade in ("win", "loss", "push"))
            print(f"     {b:<14s}: n={len(sub):>4d} gr={sg:>4d}")

    # CSV accumulator
    csv_rows: List[List[Any]] = []
    csv_rows.append(["family", "side", "band", "fold", "cfg",
                     "n_total", "n_graded", "wins", "losses",
                     "HR_pct", "ROI_pct", "ROI_lo", "ROI_hi",
                     "P_and_L", "consist", "grd_days",
                     "max_win_day", "max_loss_day"])

    FOLDS = ["A: 05-03..06", "B: 05-07..10", "C: 05-11..15"]

    # ── Per family/side/band/cfg matrix ─────────────────────────────
    for fam, side, bands in TARGETS:
        pool = by_fb.get((fam, side), [])
        print(f"\n\n{'═'*120}")
        print(f"  {fam.upper()} · side={side}")
        print(f"{'═'*120}")
        for band in bands:
            sub_band = [r for r in pool if r.band == band]
            grd_band = sum(1 for r in sub_band if r.grade in ("win", "loss", "push"))
            print(f"\n┌── band {band}  (n={len(sub_band)} / graded={grd_band}) ──┐")
            print(f"  {'fold':<14s} {'cfg':<38s} {'n':>4s} {'gr':>4s} {'W':>3s} {'L':>3s} "
                  f"{'HR%':>6s} {'ROI%':>7s} {'CI':>16s} {'P&L':>7s} {'cons':>5s} "
                  f"{'maxWinDay':<22s} {'maxLossDay':<22s}")
            for fold in ["OVERALL"] + FOLDS:
                fold_rows = (sub_band if fold == "OVERALL"
                              else [r for r in sub_band if _fold(r.game_date) == fold])
                for cfg in CONFIGS:
                    matches = [r for r in fold_rows
                                if _passes(r, edg=cfg["edg"], cv=cfg["cv"], hr=cfg["hr"])]
                    a = _agg(matches)
                    if a["gr"] == 0:
                        continue
                    ci = (f"[{a['roi_lo']},{a['roi_hi']}]"
                          if a["roi_lo"] is not None else "-")
                    hr_pct = str(a["hr"]) if a["hr"] is not None else "-"
                    roi_pct = str(a["roi"]) if a["roi"] is not None else "-"
                    print(f"  {fold[:14]:<14s} {cfg['label'][:38]:<38s} "
                          f"{a['n']:>4d} {a['gr']:>4d} {a['w']:>3d} {a['l']:>3d} "
                          f"{hr_pct:>6s} {roi_pct:>7s} {ci[:16]:>16s} "
                          f"{a['pnl']:>7.2f} {str(a['consist']):>5s} "
                          f"{a['mwd'][:22]:<22s} {a['mld'][:22]:<22s}")
                    csv_rows.append([fam, side, band, fold, cfg["label"],
                                     a["n"], a["gr"], a["w"], a["l"],
                                     a["hr"], a["roi"], a["roi_lo"], a["roi_hi"],
                                     a["pnl"], a["consist"], a["grd_days"],
                                     a["mwd"], a["mld"]])

    # ── Robustness scorecard: 3-fold positive count + overall ROI ──
    print(f"\n\n{'═'*120}")
    print("  ROBUSTNESS SCORECARD  (per family/side/band/cfg)")
    print(f"{'═'*120}")
    print(f"  {'family':<22s} {'side':<6s} {'band':<14s} {'cfg':<38s} "
          f"{'OVER':>6s} {'A':>6s} {'B':>6s} {'C':>6s} "
          f"{'pos folds':>9s}  {'verdict':<18s}")
    for fam, side, bands in TARGETS:
        pool = by_fb.get((fam, side), [])
        for band in bands:
            sub_band = [r for r in pool if r.band == band]
            for cfg in CONFIGS:
                # overall
                m = [r for r in sub_band
                     if _passes(r, edg=cfg["edg"], cv=cfg["cv"], hr=cfg["hr"])]
                a = _agg(m)
                if a["gr"] < 12: continue  # skip too-tiny
                roi_overall = a["roi"]
                # per-fold ROI
                fold_rois = []
                for fold in FOLDS:
                    fr = [r for r in sub_band if _fold(r.game_date) == fold]
                    mf = [r for r in fr
                          if _passes(r, edg=cfg["edg"], cv=cfg["cv"], hr=cfg["hr"])]
                    af = _agg(mf)
                    fold_rois.append(af["roi"] if af["gr"] >= 3 else None)
                pos = sum(1 for r in fold_rois if (r or 0) > 0)
                valid_folds = sum(1 for r in fold_rois if r is not None)
                # Verdict
                if roi_overall is None or roi_overall < 0:
                    verdict = "🔴 negative"
                elif pos == valid_folds and valid_folds >= 2:
                    verdict = "✅ robust"
                elif pos >= 2 and valid_folds == 3:
                    verdict = "🟢 strong"
                elif pos == 1 and valid_folds == 3:
                    verdict = "🟡 fold-fragile"
                else:
                    verdict = "🟡 undersized"
                def _fmt(x): return f"{x:>6}" if x is not None else "   - "
                print(f"  {fam:<22s} {side:<6s} {band:<14s} {cfg['label'][:38]:<38s} "
                      f"{_fmt(roi_overall)} "
                      f"{_fmt(fold_rois[0])} {_fmt(fold_rois[1])} {_fmt(fold_rois[2])} "
                      f"{pos}/{valid_folds:>7d}  {verdict:<18s}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out = f"/app/backend/audits/robustness_other_families_{stamp}.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f); w.writerows(csv_rows)
    print(f"\nSaved CSV → {out}")

asyncio.run(main())
