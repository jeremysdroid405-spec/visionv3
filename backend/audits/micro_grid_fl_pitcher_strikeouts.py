"""
Forensic micro-grid on FL pitcher_strikeouts.

Reads the all-gates-disabled FL pool (replay_serial = GSS-MLB-2026MMDD-FRON-POOL)
from `mlb_test_outputs`, restricts to stat_family='pitcher_strikeouts' AND
routed_tier='front_lines', then runs a high-resolution micro-grid sweep across:

    HR20 floor  ∈ {None, 50, 55, 60, 65, 70, 75, 80}
    EDG  floor  ∈ {None, -5, -2.5, 0, 2.5, 5, 7.5, 10}
    TP   floor  ∈ {None, 40, 50, 55, 60, 65, 70}
    CV   ceil   ∈ {None, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70}
    side        ∈ {ANY, OVER, UNDER}

Also benchmarks the LIVE production gate config
(HR20≥70 ∧ EDG≥4 ∧ TP≥50 ∧ CV≤0.50) against the micro-grid winners.

For each top combo: HR%, ROI%, P&L, daily consistency, odds-band breakdown,
side split, and "what's the cost/benefit vs production".
"""
from __future__ import annotations
import asyncio, csv, json, math, os, sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


# ── Odds-band partition (mirrors gss tool) ─────────────────────────
def _odds_band(odds: Optional[float]) -> str:
    if odds is None:
        return "unknown"
    o = int(odds)
    if o <= -300:           return "[-500, -300]"
    if o <= -200:           return "[-299, -200]"
    if o <= -150:           return "[-199, -150]"
    if o <= -110:           return "[-149, -110]"
    if o <=  100:           return "[-109, +100]"
    if o <=  200:           return "[+101, +200]"
    return "[+201, +∞]"

_ODDS_BAND_ORDER = [
    "[-500, -300]", "[-299, -200]", "[-199, -150]",
    "[-149, -110]", "[-109, +100]", "[+101, +200]",
    "[+201, +∞]", "unknown",
]


# ── Candidate row (in-memory) ─────────────────────────────────────
@dataclass
class Row:
    side: str
    odds: Optional[float]
    cv: Optional[float]
    edge_pct: Optional[float]   # already in percentage points (NOT raw 0-1)
    hr_l20: Optional[float]
    hr_l5: Optional[float]
    tp: Optional[float]
    grade_status: str
    profit_units: float
    stake_units: float
    game_date: str
    band: str


def _edge_pp(raw: Any) -> Optional[float]:
    """Accept either fraction (0.10) or already-percent (10.0)."""
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    # If |v| < 1.5 we treat as fraction; else assume already pp
    return v * 100.0 if abs(v) < 1.5 else v


def _wilson(wins: int, n: int) -> Tuple[Optional[float], Optional[float]]:
    if n == 0:
        return None, None
    p = wins / n
    z = 1.96
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / denom
    return round((centre - half) * 100, 2), round((centre + half) * 100, 2)


def _aggregate(rows: List[Row]) -> Dict[str, Any]:
    graded = [r for r in rows if r.grade_status in ("win", "loss", "push")]
    wins = sum(1 for r in graded if r.grade_status == "win")
    losses = sum(1 for r in graded if r.grade_status == "loss")
    pushes = sum(1 for r in graded if r.grade_status == "push")
    decided = wins + losses
    hr = (100.0 * wins / decided) if decided else None
    pnl = sum(r.profit_units for r in graded)
    stake = sum(r.stake_units for r in graded)
    roi = (100.0 * pnl / stake) if stake else None
    hr_lo, hr_hi = _wilson(wins, decided) if decided else (None, None)
    # ROI 95% CI via bootstrap (lightweight: use SE on per-pick returns)
    rets = [r.profit_units / r.stake_units for r in graded if r.stake_units]
    roi_lo = roi_hi = None
    if rets:
        n = len(rets)
        mean = sum(rets) / n
        var = sum((x - mean) ** 2 for x in rets) / max(n - 1, 1)
        se = math.sqrt(var / n)
        roi_lo = round(100.0 * (mean - 1.96 * se), 2)
        roi_hi = round(100.0 * (mean + 1.96 * se), 2)
    return {
        "n_total": len(rows), "n_graded": len(graded),
        "wins": wins, "losses": losses, "pushes": pushes,
        "hr": round(hr, 2) if hr is not None else None,
        "hr_lo": hr_lo, "hr_hi": hr_hi,
        "roi": round(roi, 2) if roi is not None else None,
        "roi_lo": roi_lo, "roi_hi": roi_hi,
        "pnl": round(pnl, 3), "stake": round(stake, 3),
    }


def _daily_consistency(rows: List[Row]) -> Tuple[Optional[float], int]:
    by_d: Dict[str, List[Row]] = defaultdict(list)
    for r in rows:
        by_d[r.game_date].append(r)
    pos = 0; grd_days = 0
    for d, rs in by_d.items():
        a = _aggregate(rs)
        if (a["n_graded"] or 0) == 0:
            continue
        grd_days += 1
        if (a["roi"] or 0) >= 0:
            pos += 1
    return (round(pos / grd_days, 3) if grd_days else None, grd_days)


def _filter(rows: List[Row], *, hr_min, edg_min, tp_min, cv_max, side) -> List[Row]:
    out = []
    for r in rows:
        if hr_min is not None and (r.hr_l20 is None or r.hr_l20 < hr_min): continue
        if edg_min is not None and (r.edge_pct is None or r.edge_pct < edg_min): continue
        if tp_min is not None and (r.tp is None or r.tp < tp_min): continue
        if cv_max is not None and (r.cv is None or r.cv > cv_max): continue
        if side != "ANY" and r.side != side: continue
        out.append(r)
    return out


# ── Main ───────────────────────────────────────────────────────────
async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    serials = [f"GSS-MLB-2026{m:02d}{d:02d}-FRON-POOL"
               for m in [5] for d in range(3, 16)]
    print(f"Reading {len(serials)} pool serials…")
    rows: List[Row] = []
    async for d in db.mlb_test_outputs.find(
        {"replay_serial": {"$in": serials},
         "stat_family": "pitcher_strikeouts",
         "routed_tier": "front_lines"},
        projection={"_id": 0,
                    "side": 1, "odds": 1, "cv": 1, "edge": 1,
                    "hit_rate_l20": 1, "hit_rate_l5": 1, "tp": 1,
                    "grade_status": 1, "profit_units": 1,
                    "stake_units": 1, "game_date": 1},
    ):
        rows.append(Row(
            side=d.get("side") or "OVER",
            odds=d.get("odds"),
            cv=d.get("cv"),
            edge_pct=_edge_pp(d.get("edge")),
            hr_l20=d.get("hit_rate_l20"),
            hr_l5=d.get("hit_rate_l5"),
            tp=d.get("tp"),
            grade_status=d.get("grade_status") or "not_qualified",
            profit_units=d.get("profit_units") or 0.0,
            stake_units=d.get("stake_units") or 0.0,
            game_date=d.get("game_date") or "",
            band=_odds_band(d.get("odds")),
        ))
    client.close()
    print(f"Loaded {len(rows)} FL pitcher_strikeouts rows  "
          f"(graded={sum(1 for r in rows if r.grade_status in ('win','loss','push'))})")

    # ── A) RAW baseline (no filter) ─────────────────────────────────
    print("\n══════════ A) RAW BASELINE (no filter, all FL ps rows) ══════════")
    a = _aggregate(rows)
    print(f"  n_total={a['n_total']}  graded={a['n_graded']}  HR={a['hr']}%  "
          f"ROI={a['roi']}%  P&L={a['pnl']}")

    # ── B) PRODUCTION gate config ───────────────────────────────────
    # Per /app/backend/services/scoring/gates/thresholds.py:
    #   pitcher_strikeouts FL = {cv_max:0.50, hr_min:70.0, edge_min:4.0, tp_min:50.0}
    print("\n══════════ B) PRODUCTION GATE BENCHMARK ══════════")
    prod_rows = _filter(rows,
        hr_min=70.0, edg_min=4.0, tp_min=50.0, cv_max=0.50, side="ANY")
    a = _aggregate(prod_rows)
    c, gd = _daily_consistency(prod_rows)
    print(f"  HR20≥70 ∧ EDG≥4 ∧ TP≥50 ∧ CV≤0.50  →  "
          f"n={a['n_total']}/{a['n_graded']}  HR={a['hr']}%  ROI={a['roi']}%  "
          f"[CI {a['roi_lo']},{a['roi_hi']}]  P&L={a['pnl']}  consist={c}/{gd}d")
    # OVER-only
    prod_over = [r for r in prod_rows if r.side == "OVER"]
    ao = _aggregate(prod_over)
    print(f"    OVER-only        n={ao['n_total']}/{ao['n_graded']}  "
          f"HR={ao['hr']}%  ROI={ao['roi']}%  P&L={ao['pnl']}")
    prod_under = [r for r in prod_rows if r.side == "UNDER"]
    au = _aggregate(prod_under)
    print(f"    UNDER-only       n={au['n_total']}/{au['n_graded']}  "
          f"HR={au['hr']}%  ROI={au['roi']}%  P&L={au['pnl']}")
    # Odds-band on production combo
    print("    odds-bands on production filter:")
    by_b: Dict[str, List[Row]] = defaultdict(list)
    for r in prod_rows: by_b[r.band].append(r)
    for b in _ODDS_BAND_ORDER:
        if b not in by_b: continue
        ab = _aggregate(by_b[b])
        if (ab["n_graded"] or 0) == 0: continue
        print(f"      {b:<18s} n={ab['n_total']:>3d}/{ab['n_graded']:>3d}  "
              f"HR={ab['hr']}%  ROI={ab['roi']}%  P&L={ab['pnl']}")

    # ── C) MICRO-GRID sweep ────────────────────────────────────────
    print("\n══════════ C) MICRO-GRID SWEEP ══════════")
    grid_hr   = [None, 50.0, 55.0, 60.0, 65.0, 70.0, 75.0, 80.0]
    grid_edg  = [None, -5.0, -2.5, 0.0, 2.5, 5.0, 7.5, 10.0]
    grid_tp   = [None, 40.0, 50.0, 55.0, 60.0, 65.0, 70.0]
    grid_cv   = [None, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70]
    grid_side = ["ANY", "OVER", "UNDER"]

    results: List[Dict[str, Any]] = []
    for hr in grid_hr:
        for ed in grid_edg:
            for tp in grid_tp:
                for cv in grid_cv:
                    for sd in grid_side:
                        sub = _filter(rows,
                            hr_min=hr, edg_min=ed, tp_min=tp, cv_max=cv, side=sd)
                        a = _aggregate(sub)
                        if (a["n_graded"] or 0) < 50:
                            continue
                        c, gd = _daily_consistency(sub)
                        results.append({
                            "combo": (f"HR≥{hr if hr else '-'}/"
                                       f"EDG≥{ed if ed is not None else '-'}/"
                                       f"TP≥{tp if tp else '-'}/"
                                       f"CV≤{cv if cv else '-'}/{sd}"),
                            "hr_min": hr, "edg_min": ed, "tp_min": tp,
                            "cv_max": cv, "side": sd,
                            "n_total": a["n_total"], "n_graded": a["n_graded"],
                            "wins": a["wins"], "losses": a["losses"],
                            "HR_pct": a["hr"],
                            "HR_ci_lo": a["hr_lo"], "HR_ci_hi": a["hr_hi"],
                            "ROI_pct": a["roi"],
                            "ROI_ci_lo": a["roi_lo"], "ROI_ci_hi": a["roi_hi"],
                            "P_and_L": a["pnl"],
                            "consistency": c, "graded_days": gd,
                        })

    print(f"  Generated {len(results)} qualified configs (graded ≥ 50)")

    # ── D) Leaderboards ─────────────────────────────────────────────
    by_roi = sorted([r for r in results if r["ROI_pct"] is not None],
                     key=lambda r: r["ROI_pct"], reverse=True)
    by_pnl = sorted(results, key=lambda r: r["P_and_L"], reverse=True)

    print("\n══════════ D1) TOP-20 by ROI (graded ≥ 50) ══════════")
    print(f"  {'combo':<60s} {'n':>5s} {'gr':>4s} {'HR':>5s} {'ROI':>6s} {'P&L':>7s} {'cons':>5s}")
    for r in by_roi[:20]:
        print(f"  {r['combo']:<60s} {r['n_total']:>5d} {r['n_graded']:>4d} "
              f"{str(r['HR_pct']):>5s} {str(r['ROI_pct']):>6s} "
              f"{r['P_and_L']:>7.2f} {str(r['consistency']):>5s}")

    print("\n══════════ D2) TOP-20 by P&L (graded ≥ 50) ══════════")
    print(f"  {'combo':<60s} {'n':>5s} {'gr':>4s} {'HR':>5s} {'ROI':>6s} {'P&L':>7s} {'cons':>5s}")
    for r in by_pnl[:20]:
        print(f"  {r['combo']:<60s} {r['n_total']:>5d} {r['n_graded']:>4d} "
              f"{str(r['HR_pct']):>5s} {str(r['ROI_pct']):>6s} "
              f"{r['P_and_L']:>7.2f} {str(r['consistency']):>5s}")

    # ── D3) VOLUME-FIRST: highest n where ROI >= 10 % ──────────────
    print("\n══════════ D3) VOLUME-FIRST (ROI ≥ +10 %, ranked by n_graded) ══════════")
    vf = [r for r in results if (r["ROI_pct"] or 0) >= 10.0]
    vf.sort(key=lambda r: r["n_graded"], reverse=True)
    for r in vf[:25]:
        print(f"  {r['combo']:<60s} n={r['n_graded']:>3d}  HR={r['HR_pct']}%  "
              f"ROI={r['ROI_pct']}%  [CI {r['ROI_ci_lo']},{r['ROI_ci_hi']}]  "
              f"P&L={r['P_and_L']:.2f}  cons={r['consistency']}")

    # ── D4) VOLUME-FIRST: ROI >= 15 %  ──────────────────────────────
    print("\n══════════ D4) VOLUME-FIRST (ROI ≥ +15 %, ranked by n_graded) ══════════")
    vf2 = [r for r in results if (r["ROI_pct"] or 0) >= 15.0]
    vf2.sort(key=lambda r: r["n_graded"], reverse=True)
    for r in vf2[:25]:
        print(f"  {r['combo']:<60s} n={r['n_graded']:>3d}  HR={r['HR_pct']}%  "
              f"ROI={r['ROI_pct']}%  [CI {r['ROI_ci_lo']},{r['ROI_ci_hi']}]  "
              f"P&L={r['P_and_L']:.2f}  cons={r['consistency']}")

    # ── E) ABLATION: drop one gate at a time vs prod ────────────────
    print("\n══════════ E) ABLATION: drop one production gate at a time ══════════")
    base = {"hr_min": 70.0, "edg_min": 4.0, "tp_min": 50.0, "cv_max": 0.50}
    print(f"  {'config':<60s} {'n':>5s} {'gr':>4s} {'HR':>5s} {'ROI':>6s} {'P&L':>7s}")
    for drop in [None, "hr_min", "edg_min", "tp_min", "cv_max"]:
        cfg = dict(base)
        if drop:
            cfg[drop] = None
        for side in ("ANY", "OVER", "UNDER"):
            sub = _filter(rows, **cfg, side=side)
            a = _aggregate(sub)
            if (a["n_graded"] or 0) < 30:
                continue
            label = (f"drop={drop or '-'}/{side}  HR≥{cfg['hr_min']}/"
                     f"EDG≥{cfg['edg_min']}/TP≥{cfg['tp_min']}/"
                     f"CV≤{cfg['cv_max']}")
            print(f"  {label[:60]:<60s} {a['n_total']:>5d} {a['n_graded']:>4d} "
                  f"{str(a['hr']):>5s} {str(a['roi']):>6s} {a['pnl']:>7.2f}")

    # ── F) Loosen each gate by ONE step (find slack) ───────────────
    print("\n══════════ F) LOOSEN EACH GATE BY ONE STEP (vs production) ══════════")
    loosen_variants = [
        ("hr_min↓ 70→65", {"hr_min": 65.0}),
        ("hr_min↓ 70→60", {"hr_min": 60.0}),
        ("hr_min↓ 70→55", {"hr_min": 55.0}),
        ("hr_min↓ 70→50", {"hr_min": 50.0}),
        ("edg_min↓ 4→0",  {"edg_min": 0.0}),
        ("edg_min↓ 4→-2.5", {"edg_min": -2.5}),
        ("edg_min↓ 4→-5", {"edg_min": -5.0}),
        ("tp_min↓ 50→40", {"tp_min": 40.0}),
        ("tp_min↓ 50→None", {"tp_min": None}),
        ("cv_max↑ 0.50→0.55", {"cv_max": 0.55}),
        ("cv_max↑ 0.50→0.60", {"cv_max": 0.60}),
        ("cv_max↑ 0.50→0.70", {"cv_max": 0.70}),
        ("cv_max↑ 0.50→None", {"cv_max": None}),
    ]
    print(f"  {'variant':<25s} {'side':<6s} {'n':>5s} {'gr':>4s} {'HR':>5s} {'ROI':>6s} {'P&L':>7s}")
    for name, override in loosen_variants:
        cfg = {**base, **override}
        for side in ("ANY",):
            sub = _filter(rows, **cfg, side=side)
            a = _aggregate(sub)
            print(f"  {name:<25s} {side:<6s} {a['n_total']:>5d} {a['n_graded']:>4d} "
                  f"{str(a['hr']):>5s} {str(a['roi']):>6s} {a['pnl']:>7.2f}")

    # ── G) Persist CSV ─────────────────────────────────────────────
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_csv = Path(f"/app/backend/audits/microgrid_fl_pitcher_strikeouts_"
                    f"2026-05-03_2026-05-15_{stamp}.csv")
    with out_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["combo", "hr_min", "edg_min", "tp_min", "cv_max", "side",
                     "n_total", "n_graded", "wins", "losses",
                     "HR_pct", "HR_ci_lo", "HR_ci_hi",
                     "ROI_pct", "ROI_ci_lo", "ROI_ci_hi",
                     "P_and_L", "consistency", "graded_days"])
        for r in by_roi:
            w.writerow([r[k] for k in ["combo","hr_min","edg_min","tp_min","cv_max",
                                          "side","n_total","n_graded","wins","losses",
                                          "HR_pct","HR_ci_lo","HR_ci_hi",
                                          "ROI_pct","ROI_ci_lo","ROI_ci_hi",
                                          "P_and_L","consistency","graded_days"]])
    print(f"\n  Saved leaderboard CSV → {out_csv}")


if __name__ == "__main__":
    asyncio.run(main())
