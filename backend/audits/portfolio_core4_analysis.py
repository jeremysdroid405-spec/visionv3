"""
FL Core 4 Portfolio Analysis
Window: 2026-05-03 → 2026-05-15
Mode: validation only — NO production gate changes.

CORE 4:
  1) pitcher_strikeouts  OVER [+101,+200]  EDG≥5 ∧ CV≤0.70
  2) earned_runs         UNDER [+101,+200] baseline
  3) runs                UNDER [-149,-110] TP≥50
  4) batter_strikeouts   OVER [-199,-150]  HR20≥65 ∧ μ-line≥0.5

Analysis:
  1. Combined portfolio metrics
  2. Day-by-day P&L curve + bankroll
  3. Drawdown analysis (max DD, rolling 3/5-day)
  4. Overlap / correlation analysis
  5. Family contribution breakdown
  6. Production safety verdict
"""
import asyncio, math, os, sys
from collections import defaultdict, Counter
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


def _fold(d):
    if d <= "2026-05-06": return "A"
    if d <= "2026-05-10": return "B"
    return "C"


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
     "filter": lambda r: (r["tp"] is not None and r["tp"]>=50.0)},
    {"name":"BS_OVER_mid",  "family":"batter_strikeouts", "side":"OVER",
     "band":"[-199,-150]",
     "filter": lambda r: (r["hr_l20"] is not None and r["hr_l20"]>=65.0
                          and r["mu_gap"] is not None and r["mu_gap"]>=0.5)},
]


async def fetch_all(db):
    serials = [f"GSS-MLB-2026{m:02d}{d:02d}-FRON-POOL"
               for m in [5] for d in range(3,16)]
    fams = {c["family"] for c in CORE4}
    all_rows = []
    async for d in db.mlb_test_outputs.find(
        {"replay_serial":{"$in":serials},
         "stat_family":{"$in":list(fams)}, "routed_tier":"front_lines"},
        projection={"_id":0}):
        odds = d.get("odds")
        if odds is None: continue
        line = d.get("line"); mu = d.get("projection_mu")
        mu_gap = (mu - line) if (mu is not None and line is not None) else None
        rec = {
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
            "home_team":d.get("home_team"), "away_team":d.get("away_team"),
            "book":d.get("book"),
            "fair_p":d.get("fair_probability"),
        }
        all_rows.append(rec)
    return all_rows


def _agg(rows):
    g = [r for r in rows if r["grade"] in ("win","loss","push")]
    w = sum(1 for r in g if r["grade"]=="win")
    l = sum(1 for r in g if r["grade"]=="loss")
    pnl = sum(r["pnl"] for r in g); stk = sum(r["stake"] for r in g)
    hr = (100*w/(w+l)) if (w+l) else None
    roi = (100*pnl/stk) if stk else None
    return {"n":len(rows),"gr":len(g),"w":w,"l":l,
            "hr":round(hr,2) if hr is not None else None,
            "roi":round(roi,2) if roi is not None else None,
            "pnl":round(pnl,3),"stake":round(stk,3)}


def _apply_core4(all_rows):
    """Assign each row to a Core-4 leg (if it matches) and return mapping."""
    leg_rows = {c["name"]: [] for c in CORE4}
    for r in all_rows:
        for c in CORE4:
            if (r["family"] == c["family"] and r["side"] == c["side"]
                  and r["band"] == c["band"] and c["filter"](r)):
                leg_rows[c["name"]].append(r)
                # also tag the row
                r["_leg"] = c["name"]
                break
    return leg_rows


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    print("Loading FL pool …")
    all_rows = await fetch_all(db)
    c.close()
    print(f"Loaded {len(all_rows)} raw FL rows across 4 families.")

    leg_rows = _apply_core4(all_rows)
    portfolio = [r for rs in leg_rows.values() for r in rs]
    print(f"\nPortfolio composition:")
    for cfg in CORE4:
        a = _agg(leg_rows[cfg["name"]])
        print(f"  {cfg['name']:<18s} ({cfg['family']:<22s} {cfg['side']:<6s} {cfg['band']:<14s}): "
              f"n={a['n']:>4d}  gr={a['gr']:>4d}")
    apt = _agg(portfolio)
    print(f"\n  PORTFOLIO TOTAL: n={apt['n']}  gr={apt['gr']}")

    # ── 1) Combined portfolio metrics ───────────────────────────────
    print("\n" + "═"*120)
    print("  1) COMBINED PORTFOLIO METRICS")
    print("═"*120)
    a = _agg(portfolio)
    dates = sorted({r["game_date"] for r in portfolio})
    graded_dates = sorted({r["game_date"] for r in portfolio
                            if r["grade"] in ("win","loss","push")})
    days_total = len(graded_dates) if graded_dates else 1
    print(f"  Total picks                : {a['n']}")
    print(f"  Graded picks               : {a['gr']}")
    print(f"  W / L                      : {a['w']} / {a['l']}")
    print(f"  Overall HR                 : {a['hr']}%")
    print(f"  Overall ROI                : {a['roi']}%")
    print(f"  Total stake                : {a['stake']}u")
    print(f"  Total P&L                  : {a['pnl']}u")
    print(f"  Distinct dates             : {len(dates)}")
    print(f"  Distinct graded dates      : {days_total}")
    print(f"  Avg picks/day  (total)     : {a['n']/len(dates):.2f}")
    print(f"  Avg picks/day  (graded)    : {a['gr']/days_total:.2f}")
    print(f"  Avg P&L/day                : {a['pnl']/days_total:.2f}u")

    # ── 2) Day-by-day P&L curve ────────────────────────────────────
    print("\n" + "═"*120)
    print("  2) DAY-BY-DAY P&L CURVE + BANKROLL")
    print("═"*120)
    by_d = defaultdict(list)
    for r in portfolio: by_d[r["game_date"]].append(r)
    daily = []
    cum = 0.0
    print(f"  {'date':<12s} {'picks':>5s} {'gr':>4s} {'W':>3s} {'L':>3s} "
          f"{'HR%':>6s} {'ROI%':>7s} {'P&L':>7s} {'cum':>8s}")
    for d in sorted(by_d.keys()):
        rs = by_d[d]
        ad = _agg(rs)
        cum += ad["pnl"]
        daily.append({"date":d, "picks":ad["n"], "gr":ad["gr"],
                       "w":ad["w"], "l":ad["l"],
                       "hr":ad["hr"], "roi":ad["roi"],
                       "pnl":ad["pnl"], "cum":cum})
        print(f"  {d:<12s} {ad['n']:>5d} {ad['gr']:>4d} {ad['w']:>3d} {ad['l']:>3d} "
              f"{str(ad['hr']):>6s} {str(ad['roi']):>7s} {ad['pnl']:>+7.2f} "
              f"{cum:>+8.2f}")

    # streaks
    best_day = max(daily, key=lambda d: d["pnl"])
    worst_day = min(daily, key=lambda d: d["pnl"])
    pos_days = [d for d in daily if (d["roi"] or 0) > 0]
    neg_days = [d for d in daily if (d["roi"] or 0) < 0]
    flat_days = [d for d in daily if (d["roi"] or 0) == 0]
    # longest win/loss streak by ROI sign
    cur_w = cur_l = max_w = max_l = 0
    for d in daily:
        r = d["roi"] or 0
        if r > 0: cur_w += 1; cur_l = 0; max_w = max(max_w, cur_w)
        elif r < 0: cur_l += 1; cur_w = 0; max_l = max(max_l, cur_l)
        else: cur_w = cur_l = 0
    print(f"\n  Best day            : {best_day['date']}  {best_day['picks']}p  "
          f"{best_day['pnl']:+.2f}u  HR={best_day['hr']}%  ROI={best_day['roi']}%")
    print(f"  Worst day           : {worst_day['date']}  {worst_day['picks']}p  "
          f"{worst_day['pnl']:+.2f}u  HR={worst_day['hr']}%  ROI={worst_day['roi']}%")
    print(f"  Positive days       : {len(pos_days)} / {len(daily)}")
    print(f"  Negative days       : {len(neg_days)} / {len(daily)}")
    print(f"  Flat days           : {len(flat_days)} / {len(daily)}")
    print(f"  Longest winning streak : {max_w} day(s)")
    print(f"  Longest losing streak  : {max_l} day(s)")

    # ── 3) Drawdown analysis ───────────────────────────────────────
    print("\n" + "═"*120)
    print("  3) DRAWDOWN ANALYSIS")
    print("═"*120)
    # Equity curve based on cumulative P&L
    eq = [(d["date"], d["cum"]) for d in daily]
    peak = -1e18; max_dd = 0.0; max_dd_peak = max_dd_trough = None
    for d, c in eq:
        if c > peak:
            peak = c; peak_date = d
        dd = peak - c
        if dd > max_dd:
            max_dd = dd; max_dd_peak = peak_date; max_dd_trough = d
    print(f"  Max drawdown (peak-to-trough): {max_dd:.2f}u   "
          f"({max_dd_peak} → {max_dd_trough})")
    # Largest k-day losing stretch
    pnl_arr = [d["pnl"] for d in daily]
    for k in (3,5):
        if len(pnl_arr) >= k:
            sums = [(daily[i]["date"], daily[i+k-1]["date"],
                     sum(pnl_arr[i:i+k]))
                    for i in range(len(pnl_arr)-k+1)]
            worst_k = min(sums, key=lambda t: t[2])
            best_k = max(sums, key=lambda t: t[2])
            print(f"  Largest {k}-day losing stretch  : "
                  f"{worst_k[0]} → {worst_k[1]}   {worst_k[2]:+.2f}u")
            print(f"  Largest {k}-day winning stretch : "
                  f"{best_k[0]} → {best_k[1]}   {best_k[2]:+.2f}u")
    # Daily volatility (std dev of P&L)
    n = len(pnl_arr); m = sum(pnl_arr)/n
    var = sum((x-m)**2 for x in pnl_arr)/max(n-1,1)
    sd_pnl = math.sqrt(var)
    sharpe = (m/sd_pnl) if sd_pnl else None
    print(f"  Daily P&L mean      : {m:+.2f}u")
    print(f"  Daily P&L std-dev   : {sd_pnl:.2f}u")
    print(f"  Daily Sharpe ratio  : {(sharpe or 0):.2f}")
    # Rolling 3 and 5-day ROI
    print(f"\n  Rolling 3-day ROI:")
    for i in range(len(daily)-2):
        sl = daily[i:i+3]
        pnl3 = sum(d["pnl"] for d in sl); st3 = sum(_agg([r for r in portfolio if r['game_date'] in [d['date'] for d in sl]])["stake"]
                                                       for _ in [0])
        # Simpler: compute stake by aggregating rows
        rs3 = [r for r in portfolio if r["game_date"] in [d["date"] for d in sl]
                and r["grade"] in ("win","loss","push")]
        stk3 = sum(r["stake"] for r in rs3); pnl3 = sum(r["pnl"] for r in rs3)
        roi3 = (100*pnl3/stk3) if stk3 else None
        print(f"    {sl[0]['date']} → {sl[-1]['date']}  "
              f"P&L={pnl3:>+6.2f}u  ROI={(roi3 or 0):>+6.2f}%")
    print(f"\n  Rolling 5-day ROI:")
    for i in range(len(daily)-4):
        sl = daily[i:i+5]
        rs5 = [r for r in portfolio if r["game_date"] in [d["date"] for d in sl]
                and r["grade"] in ("win","loss","push")]
        stk5 = sum(r["stake"] for r in rs5); pnl5 = sum(r["pnl"] for r in rs5)
        roi5 = (100*pnl5/stk5) if stk5 else None
        print(f"    {sl[0]['date']} → {sl[-1]['date']}  "
              f"P&L={pnl5:>+6.2f}u  ROI={(roi5 or 0):>+6.2f}%")

    # ── 4) Overlap analysis ────────────────────────────────────────
    print("\n" + "═"*120)
    print("  4) OVERLAP / CORRELATION ANALYSIS")
    print("═"*120)
    # Tag each row with leg already done
    by_day_player = defaultdict(set)   # (date,player_norm) → legs
    by_day_event  = defaultdict(set)   # (date,event_id)    → legs
    by_day_team   = defaultdict(set)   # (date,team)        → legs
    for r in portfolio:
        leg = r.get("_leg")
        by_day_player[(r["game_date"], r["player_norm"])].add(leg)
        by_day_event[(r["game_date"], r["event_id"])].add(leg)
        by_day_team[(r["game_date"], r["home_team"])].add(leg)
        by_day_team[(r["game_date"], r["away_team"])].add(leg)
    overlap_player = sum(1 for legs in by_day_player.values() if len(legs)>1)
    overlap_event  = sum(1 for legs in by_day_event.values()  if len(legs)>1)
    overlap_team   = sum(1 for legs in by_day_team.values()   if len(legs)>1)
    print(f"  Same player across legs same day : "
          f"{overlap_player} / {len(by_day_player)} unique (date,player) keys")
    print(f"  Same event across legs same day  : "
          f"{overlap_event} / {len(by_day_event)} unique (date,event) keys")
    print(f"  Same team across legs same day   : "
          f"{overlap_team} / {len(by_day_team)} unique (date,team) keys")
    # Pairwise leg overlap (event-level)
    leg_event_sets = {c["name"]: set() for c in CORE4}
    for r in portfolio:
        leg_event_sets[r["_leg"]].add((r["game_date"], r["event_id"]))
    legs = [c["name"] for c in CORE4]
    print(f"\n  Pairwise event-overlap matrix (date,event):")
    print(f"    {'':<18s} " + "  ".join(f"{n:<18s}" for n in legs))
    for li in legs:
        row = f"    {li:<18s} "
        for lj in legs:
            inter = len(leg_event_sets[li] & leg_event_sets[lj])
            row += f"{inter:>18d}  "
        print(row)
    # Same-pitcher across PS_OVER & ER_UNDER (theoretically can stack)
    ps_pitchers = {(r["game_date"], r["player_norm"]) for r in leg_rows["PS_OVER_dog"]}
    er_pitchers = {(r["game_date"], r["player_norm"]) for r in leg_rows["ER_UNDER_dog"]}
    ps_er_overlap = ps_pitchers & er_pitchers
    print(f"\n  PS_OVER ∩ ER_UNDER same-pitcher same-day: "
          f"{len(ps_er_overlap)} pitchers (of "
          f"{len(ps_pitchers)} PS / {len(er_pitchers)} ER)")
    if ps_er_overlap:
        for d, p in sorted(ps_er_overlap)[:10]:
            print(f"      {d} {p}")
    # Same-game stack: R_UNDER + BS_OVER (game-level correlation - lower runs ↔ more Ks)
    runs_events = {(r["game_date"], r["event_id"]) for r in leg_rows["R_UNDER_mid"]}
    bs_events   = {(r["game_date"], r["event_id"]) for r in leg_rows["BS_OVER_mid"]}
    r_bs = runs_events & bs_events
    print(f"\n  R_UNDER ∩ BS_OVER same-game same-day:    "
          f"{len(r_bs)} (of {len(runs_events)} R / {len(bs_events)} BS games)")
    # Quantify P&L contribution from overlapped rows
    overlapped_event_keys = set()
    for ev_set in [r_bs, ps_er_overlap]:
        overlapped_event_keys |= ev_set
    overlap_pnl = 0.0
    overlap_cnt = 0
    for r in portfolio:
        key1 = (r["game_date"], r["event_id"])
        key2 = (r["game_date"], r["player_norm"])
        if key1 in r_bs or key2 in ps_er_overlap:
            overlap_pnl += r["pnl"]; overlap_cnt += 1
    tot_pnl = sum(r["pnl"] for r in portfolio
                    if r["grade"] in ("win","loss","push"))
    print(f"\n  Picks involved in any overlap        : {overlap_cnt} "
          f"({100*overlap_cnt/max(1,a['gr']):.1f}% of portfolio)")
    print(f"  P&L from overlapped picks            : {overlap_pnl:+.2f}u "
          f"({100*overlap_pnl/max(0.01,tot_pnl):.1f}% of total P&L)")

    # ── 5) Family contribution ────────────────────────────────────
    print("\n" + "═"*120)
    print("  5) FAMILY CONTRIBUTION BREAKDOWN")
    print("═"*120)
    print(f"  {'leg':<18s} {'n':>4s} {'gr':>4s} {'HR%':>6s} {'ROI%':>7s} "
          f"{'P&L':>7s}  {'% of P&L':>9s}  {'P&L/day':>8s}")
    for cfg in CORE4:
        rs = leg_rows[cfg["name"]]
        a = _agg(rs)
        n_days = len({r["game_date"] for r in rs
                       if r["grade"] in ("win","loss","push")}) or 1
        contrib = (100*a["pnl"]/tot_pnl) if tot_pnl else 0
        print(f"  {cfg['name']:<18s} {a['n']:>4d} {a['gr']:>4d} "
              f"{str(a['hr']):>6s} {str(a['roi']):>7s} {a['pnl']:>+7.2f}  "
              f"{contrib:>+8.1f}%  {a['pnl']/n_days:>+7.2f}u")
    # By date fold
    print(f"\n  By fold (A: 05-03..06, B: 05-07..10, C: 05-11..15):")
    for fold in ("A","B","C"):
        sub = [r for r in portfolio if _fold(r["game_date"]) == fold]
        a = _agg(sub)
        days = len({r["game_date"] for r in sub})
        print(f"    Fold {fold}: n={a['n']:>3d} gr={a['gr']:>3d}  "
              f"HR={a['hr']}%  ROI={a['roi']}%  P&L={a['pnl']:>+7.2f}  "
              f"days={days}")
        for cfg in CORE4:
            rs = [r for r in leg_rows[cfg["name"]]
                  if _fold(r["game_date"]) == fold]
            a2 = _agg(rs)
            if a2["gr"] == 0:
                print(f"      {cfg['name']:<18s}: (no graded)")
                continue
            print(f"      {cfg['name']:<18s}: n={a2['n']:>3d} gr={a2['gr']:>3d}  "
                  f"HR={str(a2['hr']):>5s}%  ROI={str(a2['roi']):>6s}%  "
                  f"P&L={a2['pnl']:>+7.2f}")
    # By odds band
    print(f"\n  By odds band:")
    by_band = defaultdict(list)
    for r in portfolio: by_band[r["band"]].append(r)
    for b, rs in sorted(by_band.items()):
        a = _agg(rs)
        contrib = (100*a["pnl"]/tot_pnl) if tot_pnl else 0
        print(f"    {b:<14s}: n={a['n']:>3d} gr={a['gr']:>3d}  "
              f"HR={a['hr']}%  ROI={a['roi']}%  P&L={a['pnl']:>+7.2f}u  "
              f"({contrib:+.1f}% of P&L)")
    # By side
    print(f"\n  By side:")
    for s in ("OVER","UNDER"):
        rs = [r for r in portfolio if r["side"] == s]
        a = _agg(rs)
        contrib = (100*a["pnl"]/tot_pnl) if tot_pnl else 0
        print(f"    {s:<6s}: n={a['n']:>3d} gr={a['gr']:>3d}  "
              f"HR={a['hr']}%  ROI={a['roi']}%  P&L={a['pnl']:>+7.2f}u  "
              f"({contrib:+.1f}% of P&L)")

asyncio.run(main())
