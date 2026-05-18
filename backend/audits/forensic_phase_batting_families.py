"""
Phase: FL forensic validation — batting-family OVER goldmines.

Window: 2026-05-03 → 2026-05-15  (no future dates)
Routed tier: front_lines

TARGETS:
  T3) hits              OVER  [-199,-150]  config: HR20≥60 ∧ EDG≥5
  T4) total_bases       OVER  [-149,-110]  config: HR20≥45 ∧ μ-line≥0.5
  T5) batter_strikeouts OVER  [-199,-150]  config: HR20≥65 ∧ μ-line≥0.5

Validation only. NO production gate changes.
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
    if d <= "2026-05-06": return "A: 05-03..06"
    if d <= "2026-05-10": return "B: 05-07..10"
    return "C: 05-11..15"


def _agg(rows):
    g = [r for r in rows if r["grade"] in ("win","loss","push")]
    w = sum(1 for r in g if r["grade"]=="win")
    l = sum(1 for r in g if r["grade"]=="loss")
    p = sum(1 for r in g if r["grade"]=="push")
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
        rs = by_d[d]
        dp = sum(r["pnl"] for r in rs)
        ds = sum(r["stake"] for r in rs)
        droi = (100*dp/ds) if ds else None
        daily_pnl.append((d, len(rs), dp, droi))
    pos_days = sum(1 for d,n,pn,r in daily_pnl if (r or 0) >= 0)
    cons = round(pos_days/len(daily_pnl),3) if daily_pnl else None
    mwd = max(daily_pnl, key=lambda t: t[2]) if daily_pnl else None
    mld = min(daily_pnl, key=lambda t: t[2]) if daily_pnl else None
    return {"n":len(rows),"gr":len(g),"w":w,"l":l,"p":p,
            "hr":round(hr,2) if hr is not None else None,
            "roi":round(roi,2) if roi is not None else None,
            "roi_lo":roi_lo,"roi_hi":roi_hi,
            "pnl":round(pnl,3),"consist":cons,"grd_days":len(daily_pnl),
            "max_win_day": (f"{mwd[0]}/{mwd[1]}p/+{mwd[2]:.2f}u" if mwd else "-"),
            "max_loss_day": (f"{mld[0]}/{mld[1]}p/{mld[2]:+.2f}u" if mld else "-"),
            "daily_pnl": daily_pnl}


async def fetch_rows(db, *, family, side, band_test, filt=None):
    serials = [f"GSS-MLB-2026{m:02d}{d:02d}-FRON-POOL"
               for m in [5] for d in range(3,16)]
    rows = []
    async for d in db.mlb_test_outputs.find(
        {"replay_serial":{"$in":serials},
         "stat_family":family, "routed_tier":"front_lines", "side":side},
        projection={"_id":0}):
        odds = d.get("odds")
        if odds is None: continue
        b = _band(odds)
        if b != band_test: continue
        line = d.get("line"); mu = d.get("projection_mu")
        mu_gap = (mu - line) if (mu is not None and line is not None) else None
        rec = {
            "odds":odds,"cv":d.get("cv"),
            "edge_pp":_edge_pp(d.get("edge")),
            "hr_l20":d.get("hit_rate_l20"),
            "tp":d.get("tp"),
            "line":line,"mu":mu,"mu_gap":mu_gap,
            "grade":d.get("grade_status") or "not_qualified",
            "pnl":d.get("profit_units") or 0.0,
            "stake":d.get("stake_units") or 0.0,
            "game_date":d.get("game_date") or "",
            "player_name":d.get("player_name"),
            "player_norm":d.get("player_name_normalized"),
            "event_id":d.get("event_id"),
            "home_team":d.get("home_team"),
            "away_team":d.get("away_team"),
            "commence_time":d.get("commence_time"),
            "snapshot_iso":d.get("snapshot_iso"),
            "actual_value":d.get("actual_value"),
            "side":d.get("side"),
            "book":d.get("book"),
            "fair_p":d.get("fair_probability"),
            "p_model":d.get("model_probability"),
        }
        if filt and not filt(rec):
            continue
        rows.append(rec)
    return rows


async def audit_target(db, *, name, family, side, band_test, filter_fn,
                        filter_label, opposite_side="UNDER"):
    print("\n\n" + "═"*120)
    print(f"  TARGET — {name}")
    print(f"  family={family}  side={side}  band={band_test}  filter={filter_label}")
    print("═"*120)
    raw_rows = await fetch_rows(db, family=family, side=side,
                                   band_test=band_test)
    rows = [r for r in raw_rows if filter_fn(r)]
    graded = [r for r in rows if r["grade"] in ("win","loss","push")]
    print(f"  Band pool: n={len(raw_rows)}  graded={sum(1 for r in raw_rows if r['grade'] in ('win','loss','push'))}")
    print(f"  After filter: n={len(rows)}  graded={len(graded)}")

    # ── 1) Side-grading verification ───────────────────────────
    print("\n  ▶ 1) SIDE-GRADING VERIFICATION")
    wrong_grade = []
    for r in graded:
        av = r["actual_value"]; line = r["line"]
        if av is None or line is None: continue
        if side == "OVER":
            expected = ("push" if av == line
                         else ("win" if av > line else "loss"))
        else:
            expected = ("push" if av == line
                         else ("win" if av < line else "loss"))
        if r["grade"] != expected:
            wrong_grade.append({"player":r["player_name"],"date":r["game_date"],
                                "line":line,"actual":av,"graded":r["grade"],
                                "expected":expected,"odds":r["odds"],"pnl":r["pnl"]})
    print(f"    actual_value populated: {sum(1 for r in graded if r['actual_value'] is not None)} / {len(graded)}")
    print(f"    Mis-graded rows: {len(wrong_grade)}")
    for ex in wrong_grade[:5]:
        print(f"      {ex}")
    pnl_wrong = []
    for r in graded:
        if r["stake"]==0: continue
        if r["grade"]=="push": exp = 0.0
        elif r["grade"]=="win":
            exp = r["stake"]*(r["odds"]/100.0 if r["odds"]>0 else 100.0/abs(r["odds"]))
        elif r["grade"]=="loss": exp = -r["stake"]
        else: continue
        if abs(r["pnl"]-exp) > 0.001:
            pnl_wrong.append({"player":r["player_name"],"date":r["game_date"],
                              "odds":r["odds"],"stake":r["stake"],"grade":r["grade"],
                              "pnl":r["pnl"],"expected_pnl":round(exp,3)})
    print(f"    P&L formula mismatches: {len(pnl_wrong)}")
    for ex in pnl_wrong[:5]:
        print(f"      {ex}")

    # ── 2) Odds integrity ──────────────────────────────────────
    print("\n  ▶ 2) ODDS INTEGRITY")
    odds_dist = Counter(int(r["odds"]) for r in rows)
    print(f"    Distinct odds: {len(odds_dist)}")
    print(f"    Top-10 odds: {dict(odds_dist.most_common(10))}")
    bad_snap = [r for r in rows if r["snapshot_iso"] and r["commence_time"]
                  and r["snapshot_iso"] >= r["commence_time"]]
    print(f"    Lookahead snapshots (snap >= game_start): {len(bad_snap)}")
    book_dist = Counter(r["book"] for r in rows)
    print(f"    Top books: {dict(book_dist.most_common(10))}")

    # ── 3) Leakage ─────────────────────────────────────────────
    print("\n  ▶ 3) LEAKAGE CHECKS")
    leak_mu = [r for r in rows if r["actual_value"] is not None
               and r["mu"] is not None and abs(r["actual_value"]-r["mu"]) < 1e-9]
    print(f"    Rows where mu == actual_value exactly: {len(leak_mu)}")
    fp = [r["fair_p"] for r in rows if r["fair_p"] is not None]
    if fp:
        print(f"    Mean fair_p: {sum(fp)/len(fp):.3f}   "
              f"rows with fair_p > 0.95: {sum(1 for p in fp if p > 0.95)}")

    # ── 4) Duplicates ──────────────────────────────────────────
    print("\n  ▶ 4) DUPLICATE CHECKS")
    keyf = lambda r: (r["event_id"], r["player_norm"], r["side"], r["line"])
    dup_c = Counter(keyf(r) for r in rows)
    dups = [k for k,v in dup_c.items() if v>1]
    print(f"    Distinct (event,player,side,line) keys: {len(dup_c)}")
    print(f"    Duplicate keys: {len(dups)}")
    for d in dups[:5]:
        cnt = dup_c[d]
        sample = [r for r in rows if keyf(r)==d][:3]
        print(f"      ×{cnt} {d} odds={[r['odds'] for r in sample]} "
              f"books={[r['book'] for r in sample]}")
    ev = Counter(r["event_id"] for r in rows)
    pp = Counter(r["player_norm"] for r in rows)
    print(f"    Distinct events: {len(ev)}   events with >6 picks: "
          f"{sum(1 for v in ev.values() if v>6)}")
    print(f"    Distinct players: {len(pp)}  top: {dict(pp.most_common(5))}")

    # ── 5) Distribution ────────────────────────────────────────
    print("\n  ▶ 5) DISTRIBUTION CHECKS")
    by_d = defaultdict(lambda: {"w":0,"l":0,"p":0,"pnl":0.0})
    for r in graded:
        k = r["grade"][0]
        by_d[r["game_date"]][k] = by_d[r["game_date"]].get(k,0)+1
        by_d[r["game_date"]]["pnl"] += r["pnl"]
    print("    Daily W/L/P&L:")
    for d in sorted(by_d.keys()):
        x = by_d[d]
        print(f"      {d}  W={x.get('w',0):>3d}  L={x.get('l',0):>3d}  "
              f"P={x.get('p',0):>2d}  P&L={x['pnl']:+.2f}u")
    win_p = Counter(r["player_norm"] for r in graded if r["grade"]=="win")
    print("\n    Top winners (player W-L P&L):")
    for p, cnt in win_p.most_common(8):
        loss = sum(1 for r in graded if r["player_norm"]==p and r["grade"]=="loss")
        pn = sum(r["pnl"] for r in graded if r["player_norm"]==p)
        print(f"      {p:<25s}  W={cnt}  L={loss}  P&L={pn:+.2f}u")
    win_h = Counter(r["home_team"] for r in graded if r["grade"]=="win")
    print("\n    Top HOME-team wins:")
    for t, cnt in win_h.most_common(5):
        print(f"      {t:<25s}  W={cnt}")
    # Odds sub-buckets within band
    bm = {"[-199,-150]":[(-199,-180),(-179,-170),(-169,-160),(-159,-150)],
          "[-149,-110]":[(-149,-140),(-139,-130),(-129,-120),(-119,-110)],
          "[-299,-200]":[(-299,-260),(-259,-240),(-239,-220),(-219,-200)],
          "[+101,+200]":[(101,120),(121,140),(141,170),(171,200)],
          "[-109,+100]":[(-109,-100),(-99,0),(1,50),(51,100)]}
    print("\n    Odds sub-band breakdown:")
    for lo,hi in bm.get(band_test, []):
        s = [r for r in graded if lo <= int(r["odds"]) <= hi]
        if not s:
            print(f"      [{lo:>+5d},{hi:>+5d}]  (no picks)")
            continue
        w = sum(1 for r in s if r["grade"]=="win")
        l = sum(1 for r in s if r["grade"]=="loss")
        pnl = sum(r["pnl"] for r in s); stk = sum(r["stake"] for r in s)
        roi = (100*pnl/stk) if stk else None
        print(f"      [{lo:>+5d},{hi:>+5d}]  n={len(s):>3d}  W={w:>3d}  L={l:>3d}  "
              f"HR={(100*w/(w+l)) if (w+l) else 0:>5.2f}%  "
              f"ROI={(roi if roi is not None else 0):>+6.2f}%  "
              f"P&L={pnl:>+6.2f}u")

    # ── 6) Day-level concentration ─────────────────────────────
    print("\n  ▶ 6) DAY-LEVEL P&L CONCENTRATION")
    daily = [(d, by_d[d]["pnl"]) for d in sorted(by_d.keys())]
    tot = sum(p for _,p in daily)
    sd = sorted(daily, key=lambda t: t[1], reverse=True)
    print(f"    Total P&L = {tot:+.2f}u  over {len(daily)} graded days")
    if sd and tot:
        t1 = sd[0]; t3 = sum(p for _,p in sd[:3]); w1 = sd[-1]
        print(f"    Top-1 day: {t1[0]} {t1[1]:+.2f}u ({100*t1[1]/tot:.1f}%)")
        print(f"    Top-3 days: {t3:+.2f}u ({100*t3/tot:.1f}% of P&L)")
        print(f"    Worst day: {w1[0]} {w1[1]:+.2f}u")

    # ── 7) Side asymmetry ──────────────────────────────────────
    print(f"\n  ▶ 7) SIDE ASYMMETRY  ({family} {side} vs {opposite_side}, "
          f"{band_test}, SAME filter)")
    opp_raw = await fetch_rows(db, family=family, side=opposite_side,
                                  band_test=band_test)
    opp_filt = [r for r in opp_raw if filter_fn(r)]
    ao = _agg(opp_filt); au = _agg(rows)
    print(f"    {side:<6s}: n={au['n']}/{au['gr']}  HR={au['hr']}%  "
          f"ROI={au['roi']}%  P&L={au['pnl']}u  cons={au['consist']}")
    print(f"    {opposite_side:<6s}: n={ao['n']}/{ao['gr']}  HR={ao['hr']}%  "
          f"ROI={ao['roi']}%  P&L={ao['pnl']}u  cons={ao['consist']}")
    lo = Counter(r["line"] for r in opp_filt)
    lu = Counter(r["line"] for r in rows)
    print(f"    {side} line distribution: {dict(sorted(lu.items())[:10])}")
    print(f"    {opposite_side} line distribution: {dict(sorted(lo.items())[:10])}")

    # ── 8) Audit summary ───────────────────────────────────────
    issues = []
    if wrong_grade: issues.append(f"{len(wrong_grade)} mis-graded rows")
    if pnl_wrong: issues.append(f"{len(pnl_wrong)} P&L formula mismatches")
    if bad_snap: issues.append(f"{len(bad_snap)} lookahead snapshots")
    if dups: issues.append(f"{len(dups)} duplicate keys")
    print("\n  ▶ AUDIT SUMMARY")
    if not issues:
        print("    ✅ CLEAN — no integrity issues detected.")
    else:
        print("    ⚠️ ISSUES DETECTED:")
        for i in issues: print(f"      - {i}")

    # ── 9) 3-fold robustness ───────────────────────────────────
    print(f"\n  ▶ 9) 3-FOLD ROBUSTNESS  (same filter applied)")
    a = _agg(rows)
    print(f"    OVERALL: n={a['n']} gr={a['gr']} W={a['w']} L={a['l']} P={a['p']}  "
          f"HR={a['hr']}% ROI={a['roi']}% P&L={a['pnl']}u  "
          f"CI=[{a['roi_lo']},{a['roi_hi']}]  cons={a['consist']} ({a['grd_days']}d)")
    print(f"      maxWin={a['max_win_day']}  maxLoss={a['max_loss_day']}")
    for fold in ("A: 05-03..06","B: 05-07..10","C: 05-11..15"):
        sub = [r for r in rows if _fold(r["game_date"]) == fold]
        af = _agg(sub)
        print(f"    {fold:<16s}  n={af['n']:>3d} gr={af['gr']:>3d} "
              f"W={af['w']:>3d} L={af['l']:>3d}  HR={str(af['hr']):>5s}%  "
              f"ROI={str(af['roi']):>6s}%  CI=[{af['roi_lo']},{af['roi_hi']}]  "
              f"P&L={af['pnl']:>+6.2f}u  cons={af['consist']}  "
              f"maxWin={af['max_win_day']}  maxLoss={af['max_loss_day']}")


async def main():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]

    # T3 — hits OVER [-199,-150] HR20≥60 ∧ EDG≥5
    await audit_target(db,
        name="hits OVER [-199,-150] HR20≥60 ∧ EDG≥5",
        family="hits", side="OVER", band_test="[-199,-150]",
        filter_fn=lambda r: (r["hr_l20"] is not None and r["hr_l20"]>=60.0
                              and r["edge_pp"] is not None and r["edge_pp"]>=5.0),
        filter_label="HR20≥60 ∧ EDG≥5")

    # T4 — total_bases OVER [-149,-110] HR20≥45 ∧ μ≥0.5
    await audit_target(db,
        name="total_bases OVER [-149,-110] HR20≥45 ∧ μ≥0.5",
        family="total_bases", side="OVER", band_test="[-149,-110]",
        filter_fn=lambda r: (r["hr_l20"] is not None and r["hr_l20"]>=45.0
                              and r["mu_gap"] is not None and r["mu_gap"]>=0.5),
        filter_label="HR20≥45 ∧ μ-line≥0.5")

    # T5 — batter_strikeouts OVER [-199,-150] HR20≥65 ∧ μ≥0.5
    await audit_target(db,
        name="batter_strikeouts OVER [-199,-150] HR20≥65 ∧ μ≥0.5",
        family="batter_strikeouts", side="OVER", band_test="[-199,-150]",
        filter_fn=lambda r: (r["hr_l20"] is not None and r["hr_l20"]>=65.0
                              and r["mu_gap"] is not None and r["mu_gap"]>=0.5),
        filter_label="HR20≥65 ∧ μ-line≥0.5")

    c.close()


asyncio.run(main())
