"""Deep-drill: odds-band, side split, daily breakdown, and DELTA vs production
on the top 4 micro-grid winners for FL pitcher_strikeouts."""
import asyncio, os, sys, math
from collections import defaultdict
from dataclasses import dataclass
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

_BANDS = ["[-500, -300]","[-299, -200]","[-199, -150]","[-149, -110]",
          "[-109, +100]","[+101, +200]","[+201, +∞]","unknown"]


@dataclass
class Row:
    side: str
    odds: Optional[float]
    cv: Optional[float]
    edge_pp: Optional[float]
    hr_l20: Optional[float]
    tp: Optional[float]
    grade: str
    pnl: float
    stake: float
    game_date: str
    line: Optional[float]
    mu: Optional[float]
    p_model: Optional[float]


def _agg(rows):
    g = [r for r in rows if r.grade in ("win","loss","push")]
    w = sum(1 for r in g if r.grade=="win")
    l = sum(1 for r in g if r.grade=="loss")
    pnl = sum(r.pnl for r in g)
    stk = sum(r.stake for r in g)
    hr = (100*w/(w+l)) if (w+l) else None
    roi = (100*pnl/stk) if stk else None
    return {"n":len(rows),"gr":len(g),"w":w,"l":l,
            "hr": round(hr,2) if hr is not None else None,
            "roi": round(roi,2) if roi is not None else None,
            "pnl": round(pnl,3)}


def _filter(rows, *, hr_min=None, edg_min=None, tp_min=None,
            cv_max=None, side="ANY"):
    out = []
    for r in rows:
        if hr_min is not None and (r.hr_l20 is None or r.hr_l20 < hr_min): continue
        if edg_min is not None and (r.edge_pp is None or r.edge_pp < edg_min): continue
        if tp_min is not None and (r.tp is None or r.tp < tp_min): continue
        if cv_max is not None and (r.cv is None or r.cv > cv_max): continue
        if side != "ANY" and r.side != side: continue
        out.append(r)
    return out


def _edge_pp(v):
    if v is None: return None
    try: x = float(v)
    except: return None
    return x*100.0 if abs(x) < 1.5 else x


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    serials = [f"GSS-MLB-2026{m:02d}{d:02d}-FRON-POOL"
               for m in [5] for d in range(3,16)]
    rows: List[Row] = []
    async for d in db.mlb_test_outputs.find(
        {"replay_serial":{"$in":serials},
         "stat_family":"pitcher_strikeouts",
         "routed_tier":"front_lines"},
        projection={"_id":0}):
        rows.append(Row(
            side=d.get("side") or "OVER",
            odds=d.get("odds"),
            cv=d.get("cv"),
            edge_pp=_edge_pp(d.get("edge")),
            hr_l20=d.get("hit_rate_l20"),
            tp=d.get("tp"),
            grade=d.get("grade_status") or "not_qualified",
            pnl=d.get("profit_units") or 0.0,
            stake=d.get("stake_units") or 0.0,
            game_date=d.get("game_date") or "",
            line=d.get("line"),
            mu=d.get("projection_mu"),
            p_model=d.get("model_probability"),
        ))
    c.close()
    print(f"Loaded {len(rows)} FL ps rows  graded={sum(1 for r in rows if r.grade in ('win','loss','push'))}")

    # Top configs to drill into
    CONFIGS = [
        ("PROD (live gates)", dict(hr_min=70.0, edg_min=4.0, tp_min=50.0, cv_max=0.50, side="ANY")),
        ("MAX-P&L: EDG≥-5 ∧ CV≤0.50 ∧ OVER", dict(edg_min=-5.0, cv_max=0.50, side="OVER")),
        ("VF +15%: HR≥50 ∧ EDG≥-5 ∧ CV≤0.45 ∧ ANY", dict(hr_min=50.0, edg_min=-5.0, cv_max=0.45, side="ANY")),
        ("BALANCED: HR≥50 ∧ EDG≥-5 ∧ CV≤0.50 ∧ OVER", dict(hr_min=50.0, edg_min=-5.0, cv_max=0.50, side="OVER")),
        ("WIDE: EDG≥-5 ∧ CV≤0.70 ∧ OVER (highest P&L)", dict(edg_min=-5.0, cv_max=0.70, side="OVER")),
        ("TIGHT-HR: HR≥65 ∧ EDG≥0 ∧ CV≤0.50 ∧ OVER", dict(hr_min=65.0, edg_min=0.0, cv_max=0.50, side="OVER")),
    ]

    for name, cfg in CONFIGS:
        sub = _filter(rows, **cfg)
        a = _agg(sub)
        print(f"\n══════ {name} ══════")
        print(f"  overall:  n={a['n']}/{a['gr']}  HR={a['hr']}%  ROI={a['roi']}%  P&L={a['pnl']}")
        # side split (if combo wasn't already side-fixed)
        if cfg.get("side", "ANY") == "ANY":
            for sd in ("OVER","UNDER"):
                s = _filter(sub, side=sd)
                aa = _agg(s)
                if (aa["gr"] or 0) > 0:
                    print(f"    {sd:<6s}: n={aa['n']}/{aa['gr']}  HR={aa['hr']}%  ROI={aa['roi']}%  P&L={aa['pnl']}")
        # odds bands
        by_b = defaultdict(list)
        for r in sub:
            by_b[_odds_band(r.odds)].append(r)
        for b in _BANDS:
            if b not in by_b: continue
            ab = _agg(by_b[b])
            if (ab["gr"] or 0)==0: continue
            print(f"    {b:<18s} n={ab['n']:>3d}/{ab['gr']:>3d}  HR={ab['hr']}%  ROI={ab['roi']}%  P&L={ab['pnl']}")
        # daily
        by_d = defaultdict(list)
        for r in sub: by_d[r.game_date].append(r)
        print(f"    by-day (only graded days):")
        for d in sorted(by_d.keys()):
            ad = _agg(by_d[d])
            if (ad["gr"] or 0)==0: continue
            print(f"      {d}  n={ad['n']:>3d}/{ad['gr']:>3d}  HR={str(ad['hr'])+'%':>7s} ROI={str(ad['roi'])+'%':>8s}  P&L={ad['pnl']:>6.2f}")

asyncio.run(main())
