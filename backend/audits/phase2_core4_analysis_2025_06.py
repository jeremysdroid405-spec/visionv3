"""FL Core-4 Portfolio Analysis — Phase 2 (June 2025 durability test)
Window: 2025-06-01 → 2025-06-28
LOCKED configs (zero modifications from Phase 1):
  1) pitcher_strikeouts  OVER [+101,+200]  EDG>=5 AND CV<=0.70
  2) earned_runs         UNDER [+101,+200] baseline
  3) runs                UNDER [-149,-110] TP in [50,55)   ← post-Phase-1 refinement
  4) batter_strikeouts   OVER [-199,-150]  HR20>=65 AND mu-line>=0.5
"""
import asyncio, math, os, sys
from collections import defaultdict
from typing import Any, Dict, List, Optional
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

def _band(o):
    if o is None: return None
    o = int(o)
    if o <= -300: return "[-500,-300]"
    if o <= -200: return "[-299,-200]"
    if o <= -150: return "[-199,-150]"
    if o <= -110: return "[-149,-110]"
    if o <=  100: return "[-109,+100]"
    if o <=  200: return "[+101,+200]"
    return "[+201,+inf]"

def _edge_pp(v):
    if v is None: return None
    try: x = float(v)
    except: return None
    return x*100.0 if abs(x) < 1.5 else x


CORE4 = [
    {"name":"PS_OVER_dog", "family":"pitcher_strikeouts", "side":"OVER",
     "band":"[+101,+200]",
     "filter": lambda r: (r["edge_pp"] is not None and r["edge_pp"]>=5.0
                          and r["cv"] is not None and r["cv"]<=0.70)},
    {"name":"ER_UNDER_dog", "family":"earned_runs", "side":"UNDER",
     "band":"[+101,+200]",
     "filter": lambda r: True},
    {"name":"R_UNDER_mid",  "family":"runs", "side":"UNDER",
     "band":"[-149,-110]",
     # Phase-1 refinement: TP in [50,55) only
     "filter": lambda r: (r["tp"] is not None and 50.0 <= r["tp"] < 55.0)},
    {"name":"R_UNDER_mid_orig",  "family":"runs", "side":"UNDER",
     "band":"[-149,-110]",
     # Also test the original TP>=50 (matches Phase-1 snapshot)
     "filter": lambda r: (r["tp"] is not None and r["tp"] >= 50.0)},
    {"name":"BS_OVER_mid",  "family":"batter_strikeouts", "side":"OVER",
     "band":"[-199,-150]",
     "filter": lambda r: (r["hr_l20"] is not None and r["hr_l20"]>=65.0
                          and r["mu_gap"] is not None and r["mu_gap"]>=0.5)},
]


def _fold(d):
    # June split into ~3 folds of ~10 days each
    if d <= "2025-06-10": return "A: 06-01..10"
    if d <= "2025-06-20": return "B: 06-11..20"
    return "C: 06-21..28"


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
    daily_pnl = []
    for d in sorted(by_d.keys()):
        dp = sum(r["pnl"] for r in by_d[d])
        ds = sum(r["stake"] for r in by_d[d])
        daily_pnl.append((d, len(by_d[d]), dp,
                          (100*dp/ds) if ds else None))
    pos_days = sum(1 for _,_,_,r in daily_pnl if (r or 0) >= 0)
    cons = round(pos_days/len(daily_pnl),3) if daily_pnl else None
    mwd = max(daily_pnl, key=lambda t: t[2]) if daily_pnl else None
    mld = min(daily_pnl, key=lambda t: t[2]) if daily_pnl else None
    return {"n":len(rows),"gr":len(g),"w":w,"l":l,
            "hr":round(hr,2) if hr is not None else None,
            "roi":round(roi,2) if roi is not None else None,
            "roi_lo":roi_lo,"roi_hi":roi_hi,
            "pnl":round(pnl,3),"stake":round(stk,3),
            "consist":cons,"grd_days":len(daily_pnl),
            "max_win_day": (f"{mwd[0]}/{mwd[1]}p/+{mwd[2]:.2f}u"
                             if mwd else "-"),
            "max_loss_day": (f"{mld[0]}/{mld[1]}p/{mld[2]:+.2f}u"
                              if mld else "-"),
            "daily_pnl": daily_pnl}


async def fetch_all(db):
    serials = [f"GSS-MLB-202506{d:02d}-FRON-POOL" for d in range(1, 29)]
    fams = {c["family"] for c in CORE4}
    out = []
    async for d in db.mlb_test_outputs.find(
        {"replay_serial":{"$in":serials},
         "stat_family":{"$in":list(fams)},
         "routed_tier":"front_lines"},
        projection={"_id":0}):
        odds = d.get("odds")
        if odds is None: continue
        line = d.get("line"); mu = d.get("projection_mu")
        mu_gap = (mu - line) if (mu is not None and line is not None) else None
        out.append({
            "family":d.get("stat_family"), "side":d.get("side") or "OVER",
            "odds":odds, "band":_band(odds),
            "cv":d.get("cv"), "edge_pp":_edge_pp(d.get("edge")),
            "hr_l20":d.get("hit_rate_l20"), "tp":d.get("tp"),
            "line":line, "mu":mu, "mu_gap":mu_gap,
            "grade":d.get("grade_status") or "not_qualified",
            "pnl":d.get("profit_units") or 0.0,
            "stake":d.get("stake_units") or 0.0,
            "game_date":d.get("game_date") or "",
            "player_name":d.get("player_name"),
            "player_norm":d.get("player_name_normalized"),
            "event_id":d.get("event_id"),
        })
    return out


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    print("Loading FL pool 2025-06...")
    rows = await fetch_all(db)
    c.close()
    print(f"  Total rows: {len(rows)}  graded: {sum(1 for r in rows if r['grade'] in ('win','loss','push'))}")

    print("\n══════ LEG-LEVEL METRICS ══════")
    print(f"  {'leg':<22s} {'family':<22s} {'side':<6s} {'band':<14s} "
          f"{'n':>5s} {'gr':>5s} {'HR%':>6s} {'ROI%':>7s} {'CI':>17s} "
          f"{'P&L':>8s} {'cons':>5s}")
    leg_rows = {}
    for cfg in CORE4:
        matches = [r for r in rows
                   if r["family"]==cfg["family"]
                      and r["side"]==cfg["side"]
                      and r["band"]==cfg["band"]
                      and cfg["filter"](r)]
        leg_rows[cfg["name"]] = matches
        a = _agg(matches)
        ci = (f"[{a['roi_lo']},{a['roi_hi']}]"
              if a["roi_lo"] is not None else "-")
        print(f"  {cfg['name']:<22s} {cfg['family']:<22s} {cfg['side']:<6s} "
              f"{cfg['band']:<14s} {a['n']:>5d} {a['gr']:>5d} "
              f"{str(a['hr']):>6s} {str(a['roi']):>7s} {ci[:17]:>17s} "
              f"{a['pnl']:>+8.2f} {str(a['consist']):>5s}")

    print("\n══════ PORTFOLIO (using R_UNDER_mid with TP in [50,55)) ══════")
    portfolio = []
    for cfg in CORE4:
        if cfg["name"] == "R_UNDER_mid_orig": continue
        portfolio.extend(leg_rows[cfg["name"]])
    a = _agg(portfolio)
    n_days = len({r["game_date"] for r in portfolio
                    if r["grade"] in ("win","loss","push")})
    print(f"  Total picks: {a['n']}")
    print(f"  Graded:      {a['gr']}")
    print(f"  W/L:         {a['w']}/{a['l']}")
    print(f"  HR:          {a['hr']}%")
    print(f"  ROI:         {a['roi']}%  CI=[{a['roi_lo']},{a['roi_hi']}]")
    print(f"  P&L:         {a['pnl']:+.2f}u")
    print(f"  Stake:       {a['stake']}u")
    print(f"  Days graded: {n_days}")
    print(f"  Avg P&L/day: {a['pnl']/max(n_days,1):+.2f}u")
    print(f"  Consist:     {a['consist']} ({a['grd_days']} graded days)")
    print(f"  Max win day: {a['max_win_day']}")
    print(f"  Max loss:    {a['max_loss_day']}")

    print("\n══════ 3-FOLD ROBUSTNESS ══════")
    for fold in ("A: 06-01..10","B: 06-11..20","C: 06-21..28"):
        print(f"\n  Fold {fold}")
        for cfg in CORE4:
            if cfg["name"] == "R_UNDER_mid_orig": continue
            sub = [r for r in leg_rows[cfg["name"]]
                    if _fold(r["game_date"]) == fold]
            af = _agg(sub)
            if af["gr"] == 0:
                print(f"    {cfg['name']:<22s}: no graded")
                continue
            ci = (f"[{af['roi_lo']},{af['roi_hi']}]"
                  if af["roi_lo"] is not None else "-")
            print(f"    {cfg['name']:<22s}  n={af['n']:>4d} gr={af['gr']:>4d} "
                  f"W={af['w']:>3d} L={af['l']:>3d}  HR={str(af['hr']):>5s}%  "
                  f"ROI={str(af['roi']):>6s}%  CI={ci[:14]:<14s}  "
                  f"P&L={af['pnl']:>+7.2f}u  cons={af['consist']}")

    print("\n══════ DAY-BY-DAY PORTFOLIO P&L ══════")
    by_d = defaultdict(list)
    for r in portfolio: by_d[r["game_date"]].append(r)
    cum = 0.0
    daily = []
    print(f"  {'date':<12s} {'picks':>5s} {'gr':>4s} {'W':>3s} {'L':>3s} "
          f"{'HR%':>6s} {'ROI%':>7s} {'P&L':>7s} {'cum':>8s}")
    for d in sorted(by_d.keys()):
        ad = _agg(by_d[d])
        cum += ad["pnl"]
        daily.append({"date":d,"pnl":ad["pnl"],"roi":ad["roi"],
                      "hr":ad["hr"],"n":ad["n"],"gr":ad["gr"],
                      "w":ad["w"],"l":ad["l"],"cum":cum})
        print(f"  {d:<12s} {ad['n']:>5d} {ad['gr']:>4d} {ad['w']:>3d} "
              f"{ad['l']:>3d} {str(ad['hr']):>6s} {str(ad['roi']):>7s} "
              f"{ad['pnl']:>+7.2f} {cum:>+8.2f}")

    # Drawdown
    eq = [(d["date"], d["cum"]) for d in daily]
    peak = -1e18; max_dd = 0.0; ddp = ddt = None
    for d, ck in eq:
        if ck > peak: peak = ck; pkd = d
        if (peak - ck) > max_dd:
            max_dd = peak - ck; ddp = pkd; ddt = d
    print(f"\n  Max drawdown: -{max_dd:.2f}u  ({ddp} → {ddt})")
    # streaks
    cur_w=cur_l=max_w=max_l=0
    for d in daily:
        r = d["roi"] or 0
        if r > 0: cur_w += 1; cur_l = 0; max_w = max(max_w,cur_w)
        elif r < 0: cur_l += 1; cur_w = 0; max_l = max(max_l,cur_l)
        else: cur_w = cur_l = 0
    print(f"  Longest winning streak: {max_w} days")
    print(f"  Longest losing streak:  {max_l} days")
    pnls = [d["pnl"] for d in daily]
    m = sum(pnls)/len(pnls); v = sum((x-m)**2 for x in pnls)/max(len(pnls)-1,1)
    sd = math.sqrt(v); sharpe = m/sd if sd else 0
    print(f"  Daily mean: {m:+.2f}u  std: {sd:.2f}u  Sharpe: {sharpe:.2f}")

asyncio.run(main())
