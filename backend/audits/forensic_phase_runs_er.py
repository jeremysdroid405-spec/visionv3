"""
Phase: FL forensic validation — runs UNDER goldmine + earned_runs UNDER audit.

Window: 2026-05-03 → 2026-05-15
Routed tier: front_lines

TARGET 1 — runs UNDER  [-149,-110]  TP>=50   3-fold robustness
TARGET 2 — earned_runs UNDER  [+101,+200]    raw-row forensic audit

NO production changes. Validation only.
"""
import asyncio, math, os, sys, json
from collections import defaultdict, Counter
from dataclasses import dataclass
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
    # day-level
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


async def fetch_rows(db, *, family, side, band_test, extra_filter=None):
    """Pull all FL rows matching family/side; filter by band in Python."""
    serials = [f"GSS-MLB-2026{m:02d}{d:02d}-FRON-POOL"
               for m in [5] for d in range(3,16)]
    cur = db.mlb_test_outputs.find(
        {"replay_serial":{"$in":serials},
         "stat_family":family, "routed_tier":"front_lines", "side":side},
        projection={"_id":0})
    rows = []
    async for d in cur:
        odds = d.get("odds")
        if odds is None: continue
        b = _band(odds)
        if b != band_test: continue
        line = d.get("line"); mu = d.get("projection_mu")
        mu_gap = (mu - line) if (mu is not None and line is not None) else None
        rec = {
            "odds": odds, "cv": d.get("cv"),
            "edge_pp": _edge_pp(d.get("edge")),
            "hr_l20": d.get("hit_rate_l20"),
            "hr_l5": d.get("hit_rate_l5"),
            "tp": d.get("tp"),
            "line": line, "mu": mu, "mu_gap": mu_gap,
            "grade": d.get("grade_status") or "not_qualified",
            "pnl": d.get("profit_units") or 0.0,
            "stake": d.get("stake_units") or 0.0,
            "game_date": d.get("game_date") or "",
            "player_name": d.get("player_name"),
            "player_norm": d.get("player_name_normalized"),
            "event_id": d.get("event_id"),
            "home_team": d.get("home_team"),
            "away_team": d.get("away_team"),
            "commence_time": d.get("commence_time"),
            "snapshot_iso": d.get("snapshot_iso"),
            "actual_value": d.get("actual_value"),
            "stat_family": d.get("stat_family"),
            "side": d.get("side"),
            "book": d.get("book"),
            "tier_ref_book": d.get("tier_reference_book"),
            "tier_ref_odds": d.get("tier_reference_odds"),
            "fair_p": d.get("fair_probability"),
            "p_model": d.get("model_probability"),
            "books_used": d.get("books_used"),
            "canon_market_key": d.get("canonical_market_key"),
            "is_alternate": d.get("is_alternate"),
            "books_over": d.get("over_books"),
            "books_under": d.get("under_books"),
        }
        if extra_filter and not extra_filter(rec):
            continue
        rows.append(rec)
    return rows


# ──────────────────────────────────────────────────────────────────
async def target1_runs_under():
    """3-fold robustness on runs UNDER [-149,-110] TP>=50."""
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    print("\n" + "═"*120)
    print("  TARGET 1 — runs UNDER  band [-149,-110]  TP>=50   3-FOLD ROBUSTNESS")
    print("═"*120)
    rows = await fetch_rows(db, family="runs", side="UNDER",
                              band_test="[-149,-110]")
    # Apply TP filter
    filt = [r for r in rows if r["tp"] is not None and r["tp"] >= 50.0]
    print(f"  Pool (any TP):       n={len(rows)}  graded={sum(1 for r in rows if r['grade'] in ('win','loss','push'))}")
    print(f"  After TP≥50 filter:  n={len(filt)}  graded={sum(1 for r in filt if r['grade'] in ('win','loss','push'))}")

    # OVERALL
    print("\n  OVERALL:")
    a = _agg(filt)
    print(f"    n={a['n']}  gr={a['gr']}  W={a['w']}  L={a['l']}  P={a['p']}  "
          f"HR={a['hr']}%  ROI={a['roi']}%  [CI {a['roi_lo']},{a['roi_hi']}]  "
          f"P&L={a['pnl']}u  cons={a['consist']} ({a['grd_days']}d)")
    print(f"    max-win-day:  {a['max_win_day']}")
    print(f"    max-loss-day: {a['max_loss_day']}")

    # 3-fold
    print("\n  3-FOLD ROBUSTNESS:")
    for fold in ("A: 05-03..06","B: 05-07..10","C: 05-11..15"):
        sub = [r for r in filt if _fold(r["game_date"]) == fold]
        a = _agg(sub)
        print(f"    {fold:<16s}  n={a['n']:>3d} gr={a['gr']:>3d} W={a['w']:>2d} "
              f"L={a['l']:>2d}  HR={str(a['hr']):>5s}%  ROI={str(a['roi']):>6s}%  "
              f"CI=[{a['roi_lo']},{a['roi_hi']}]  P&L={a['pnl']:>6.2f}u  "
              f"cons={a['consist']}  maxWin={a['max_win_day']}  "
              f"maxLoss={a['max_loss_day']}")

    # Sub-band  (within [-149,-110])
    print("\n  ROI BY ODDS SUB-BAND (inside [-149,-110]):")
    sub_buckets = [(-149,-140),(-139,-130),(-129,-120),(-119,-110)]
    for lo,hi in sub_buckets:
        s = [r for r in filt if lo <= int(r["odds"]) <= hi]
        a = _agg(s)
        if a["gr"] == 0:
            print(f"    [{lo:>4d},{hi:>4d}]   (no picks)")
            continue
        print(f"    [{lo:>4d},{hi:>4d}]   n={a['n']:>3d} gr={a['gr']:>3d} "
              f"W={a['w']:>3d} L={a['l']:>3d}  HR={str(a['hr']):>5s}%  "
              f"ROI={str(a['roi']):>6s}%  P&L={a['pnl']:>6.2f}u  "
              f"cons={a['consist']}")

    # TP buckets
    print("\n  ROI BY TP BUCKET (TP filter ≥50 already applied):")
    tp_b = [(50,55),(55,60),(60,65),(65,70),(70,75),(75,80),(80,100)]
    for lo,hi in tp_b:
        s = [r for r in filt if r["tp"] is not None and lo <= r["tp"] < hi]
        a = _agg(s)
        if a["gr"] == 0:
            print(f"    TP [{lo:>3d},{hi:>3d})   (no picks)")
            continue
        print(f"    TP [{lo:>3d},{hi:>3d})   n={a['n']:>3d} gr={a['gr']:>3d}  "
              f"HR={str(a['hr']):>5s}%  ROI={str(a['roi']):>6s}%  "
              f"P&L={a['pnl']:>6.2f}u  cons={a['consist']}")

    # μ-line buckets (signed)
    print("\n  ROI BY μ-LINE BUCKET (projection_mu - line):")
    mu_b = [(-99,-1.5),(-1.5,-1.0),(-1.0,-0.5),(-0.5,0.0),
            (0.0,0.5),(0.5,1.0),(1.0,1.5),(1.5,99)]
    for lo,hi in mu_b:
        s = [r for r in filt if r["mu_gap"] is not None
             and lo <= r["mu_gap"] < hi]
        a = _agg(s)
        if a["gr"] == 0:
            print(f"    μ [{lo:>+5.1f},{hi:>+5.1f})   (no picks)")
            continue
        print(f"    μ [{lo:>+5.1f},{hi:>+5.1f})   n={a['n']:>3d} gr={a['gr']:>3d}  "
              f"HR={str(a['hr']):>5s}%  ROI={str(a['roi']):>6s}%  "
              f"P&L={a['pnl']:>6.2f}u")

    # EDGE buckets
    print("\n  ROI BY EDGE BUCKET (percentage points):")
    e_b = [(-99,-10),(-10,-5),(-5,0),(0,5),(5,10),(10,20),(20,99)]
    for lo,hi in e_b:
        s = [r for r in filt if r["edge_pp"] is not None
             and lo <= r["edge_pp"] < hi]
        a = _agg(s)
        if a["gr"] == 0:
            print(f"    EDG [{lo:>+5.1f},{hi:>+5.1f})   (no picks)")
            continue
        print(f"    EDG [{lo:>+5.1f},{hi:>+5.1f})   n={a['n']:>3d} gr={a['gr']:>3d}  "
              f"HR={str(a['hr']):>5s}%  ROI={str(a['roi']):>6s}%  "
              f"P&L={a['pnl']:>6.2f}u")

    c.close()


# ──────────────────────────────────────────────────────────────────
async def target2_earned_runs_under():
    """Forensic raw-row audit + 3-fold robustness on earned_runs UNDER [+101,+200]."""
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    print("\n\n" + "═"*120)
    print("  TARGET 2 — earned_runs UNDER  band [+101,+200]   RAW-ROW FORENSIC AUDIT")
    print("═"*120)
    rows = await fetch_rows(db, family="earned_runs", side="UNDER",
                              band_test="[+101,+200]")
    graded = [r for r in rows if r["grade"] in ("win","loss","push")]
    print(f"  Pool: n={len(rows)}  graded={len(graded)}")

    # ── 1) Side grading verification ───────────────────────────
    print("\n  ▶ 1) SIDE-GRADING VERIFICATION")
    # For UNDER bet, win = actual < line; loss = actual > line; push = actual == line
    wrong_grade = []
    for r in graded:
        av = r["actual_value"]; line = r["line"]
        if av is None or line is None: continue
        expected = ("push" if av == line
                    else ("win" if av < line else "loss"))
        if r["grade"] != expected:
            wrong_grade.append({"player":r["player_name"], "date":r["game_date"],
                                 "line":line, "actual":av,
                                 "graded":r["grade"], "expected":expected,
                                 "odds":r["odds"], "pnl":r["pnl"]})
    print(f"    Rows with actual_value populated: "
          f"{sum(1 for r in graded if r['actual_value'] is not None)} / {len(graded)}")
    print(f"    Mis-graded rows (actual vs grade): {len(wrong_grade)}")
    if wrong_grade[:5]:
        print("    Example mis-grades:")
        for ex in wrong_grade[:5]:
            print(f"      {ex}")
    # P&L formula check
    pnl_wrong = []
    for r in graded:
        odds = r["odds"]; stk = r["stake"]; pnl = r["pnl"]
        if stk == 0: continue
        if r["grade"] == "push":
            expected_pnl = 0.0
        elif r["grade"] == "win":
            expected_pnl = stk * (odds/100.0 if odds > 0 else 100.0/abs(odds))
        elif r["grade"] == "loss":
            expected_pnl = -stk
        else:
            continue
        if abs(pnl - expected_pnl) > 0.001:
            pnl_wrong.append({"player":r["player_name"], "date":r["game_date"],
                              "odds":odds, "stake":stk, "grade":r["grade"],
                              "pnl":pnl, "expected_pnl":round(expected_pnl,3)})
    print(f"    Rows with P&L formula mismatch: {len(pnl_wrong)}")
    if pnl_wrong[:5]:
        for ex in pnl_wrong[:5]:
            print(f"      {ex}")

    # ── 2) Odds integrity ──────────────────────────────────────
    print("\n  ▶ 2) ODDS INTEGRITY")
    odds_dist = Counter(int(r["odds"]) for r in rows)
    print(f"    Distinct odds values used: {len(odds_dist)}")
    print(f"    Top-15 most-common odds:")
    for o, cnt in odds_dist.most_common(15):
        print(f"      {o:+5d}  ×{cnt}")
    # Snapshot vs commence_time
    bad_snapshot = []
    for r in rows:
        s = r["snapshot_iso"]; ct = r["commence_time"]
        if s and ct and s >= ct:
            bad_snapshot.append({"player":r["player_name"], "date":r["game_date"],
                                 "snap":s, "commence":ct})
    print(f"    Snapshot AFTER game-start (potential lookahead): {len(bad_snapshot)}")
    if bad_snapshot[:5]:
        for ex in bad_snapshot[:5]:
            print(f"      {ex}")
    # Book consistency
    book_dist = Counter(r["book"] for r in rows)
    print(f"    Book distribution: {dict(book_dist.most_common(10))}")

    # ── 3) Leakage checks ──────────────────────────────────────
    print("\n  ▶ 3) LEAKAGE CHECKS")
    # Detect actual_value populated BEFORE game start (impossible)
    # Already covered: snapshot_iso >= commence_time would be lookahead
    # Check: any row where projection_mu == actual_value EXACTLY (suspicious)
    suspicious_mu_av = [r for r in rows
                         if r["actual_value"] is not None
                         and r["mu"] is not None
                         and abs(r["actual_value"] - r["mu"]) < 1e-9]
    print(f"    Rows where mu EXACTLY equals actual_value: {len(suspicious_mu_av)}")
    # fair_probability sanity vs odds
    impl_p = [(r,
               (abs(r["odds"])/(abs(r["odds"])+100)) if r["odds"] < 0
               else (100/(r["odds"]+100)),
               r["fair_p"])
              for r in rows if r["odds"] is not None and r["fair_p"] is not None]
    # fair_p for UNDER side should equal P(actual<line) which can be very high
    # No specific leakage signal; print distribution
    if impl_p:
        avg_fair = sum(p for _,_,p in impl_p)/len(impl_p)
        print(f"    Average fair_probability (UNDER model): {avg_fair:.3f}")
        # If fair_p > 0.95 across the board → suggests model has strong information
        # but not leakage per se
        high_fair = sum(1 for _,_,p in impl_p if p > 0.95)
        print(f"    Rows with fair_p > 0.95: {high_fair}")

    # ── 4) Duplicate checks ────────────────────────────────────
    print("\n  ▶ 4) DUPLICATE CHECKS")
    key = lambda r: (r["event_id"], r["player_norm"], r["side"], r["line"])
    dup_counter = Counter(key(r) for r in rows)
    dups = [k for k, v in dup_counter.items() if v > 1]
    print(f"    Distinct (event,player,side,line) tuples: {len(dup_counter)}")
    print(f"    Duplicated tuples: {len(dups)}")
    if dups[:5]:
        for d in dups[:5]:
            cnt = dup_counter[d]
            sample = [r for r in rows if key(r) == d][:3]
            print(f"      ×{cnt}  {d}  sample odds: "
                  f"{[r['odds'] for r in sample]}  "
                  f"books: {[r['book'] for r in sample]}")
    # event-level
    ev_counter = Counter(r["event_id"] for r in rows)
    print(f"    Distinct events: {len(ev_counter)}")
    print(f"    Events with > 3 picks: "
          f"{sum(1 for v in ev_counter.values() if v > 3)}")
    # player-level (pitcher) frequency
    p_counter = Counter(r["player_norm"] for r in rows)
    print(f"    Distinct pitchers: {len(p_counter)}")
    print(f"    Top-10 pitchers by pick count:")
    for p, cnt in p_counter.most_common(10):
        print(f"      {p}  ×{cnt}")

    # ── 5) Distribution checks ─────────────────────────────────
    print("\n  ▶ 5) DISTRIBUTION CHECKS")
    # By date
    print("    Wins/Losses by date:")
    by_d = defaultdict(lambda: {"w":0,"l":0,"p":0,"pnl":0.0})
    for r in graded:
        by_d[r["game_date"]][r["grade"][0]] = by_d[r["game_date"]].get(r["grade"][0],0)+1
        by_d[r["game_date"]]["pnl"] += r["pnl"]
    for d in sorted(by_d.keys()):
        rec = by_d[d]
        print(f"      {d}  W={rec.get('w',0):>2d}  L={rec.get('l',0):>2d}  "
              f"P={rec.get('p',0):>2d}  P&L={rec['pnl']:+.2f}u")
    # By pitcher (winning pitchers list)
    print("\n    Wins by pitcher (top-10):")
    win_by_p = Counter(r["player_norm"] for r in graded if r["grade"]=="win")
    for p, cnt in win_by_p.most_common(10):
        loss = sum(1 for r in graded if r["player_norm"]==p and r["grade"]=="loss")
        pnl = sum(r["pnl"] for r in graded if r["player_norm"]==p)
        print(f"      {p:<25s}  W={cnt}  L={loss}  P&L={pnl:+.2f}u")
    # By team (home)
    print("\n    Wins by HOME team (top-10):")
    win_by_h = Counter(r["home_team"] for r in graded if r["grade"]=="win")
    for t, cnt in win_by_h.most_common(10):
        print(f"      {t:<25s}  W={cnt}")
    # By odds bucket
    print("\n    Wins by odds bucket:")
    sub_buckets = [(101,120),(121,140),(141,170),(171,200)]
    for lo,hi in sub_buckets:
        s = [r for r in graded if lo <= int(r["odds"]) <= hi]
        if not s:
            print(f"      [+{lo},+{hi}]   (no picks)")
            continue
        w = sum(1 for r in s if r["grade"]=="win")
        l = sum(1 for r in s if r["grade"]=="loss")
        pnl = sum(r["pnl"] for r in s)
        stk = sum(r["stake"] for r in s)
        roi = (100*pnl/stk) if stk else None
        print(f"      [+{lo:>3d},+{hi:>3d}]   n={len(s):>3d}  W={w:>3d}  L={l:>3d}  "
              f"HR={(100*w/(w+l)) if (w+l) else 0:>5.2f}%  "
              f"ROI={(roi if roi is not None else 0):>+6.2f}%  P&L={pnl:+.2f}u")

    # ── 6) Day-level dependency ────────────────────────────────
    print("\n  ▶ 6) DAY-LEVEL P&L CONCENTRATION")
    daily = [(d, by_d[d]["pnl"]) for d in sorted(by_d.keys())]
    total_pnl = sum(p for _, p in daily)
    sorted_d = sorted(daily, key=lambda t: t[1], reverse=True)
    print(f"    Total P&L: {total_pnl:+.2f}u   over {len(daily)} graded days")
    if sorted_d and total_pnl:
        top1 = sorted_d[0]
        top3 = sum(p for _,p in sorted_d[:3])
        print(f"    Top-1 day:  {top1[0]}  +{top1[1]:.2f}u "
              f"({100*top1[1]/total_pnl:.1f}% of net P&L)")
        print(f"    Top-3 days: +{top3:.2f}u "
              f"({100*top3/total_pnl:.1f}% of net P&L)")
        worst = sorted_d[-1]
        print(f"    Worst day:  {worst[0]}  {worst[1]:+.2f}u")

    # ── 7) Compare OVER vs UNDER for earned_runs [+101,+200] ──
    print("\n  ▶ 7) SIDE ASYMMETRY  (earned_runs OVER vs UNDER, [+101,+200])")
    over_rows = await fetch_rows(db, family="earned_runs", side="OVER",
                                   band_test="[+101,+200]")
    print(f"    OVER  pool: n={len(over_rows)}  graded={sum(1 for r in over_rows if r['grade'] in ('win','loss','push'))}")
    print(f"    UNDER pool: n={len(rows)}  graded={len(graded)}")
    ao = _agg(over_rows); au = _agg(rows)
    print(f"    OVER  baseline: HR={ao['hr']}%  ROI={ao['roi']}%  P&L={ao['pnl']}u  consist={ao['consist']}")
    print(f"    UNDER baseline: HR={au['hr']}%  ROI={au['roi']}%  P&L={au['pnl']}u  consist={au['consist']}")
    # Line distribution comparison
    over_lines = Counter(r["line"] for r in over_rows)
    under_lines = Counter(r["line"] for r in rows)
    print(f"    OVER  line distribution: {dict(sorted(over_lines.items())[:10])}")
    print(f"    UNDER line distribution: {dict(sorted(under_lines.items())[:10])}")
    # Avg odds
    if over_rows:
        avg_over = sum(r["odds"] for r in over_rows)/len(over_rows)
        print(f"    Avg odds OVER:  {avg_over:+.1f}")
    avg_under = sum(r["odds"] for r in rows)/len(rows)
    print(f"    Avg odds UNDER: {avg_under:+.1f}")

    c.close()

    # ── 8) Audit pass/fail summary ─────────────────────────────
    print("\n  ▶ AUDIT SUMMARY")
    audit_issues = []
    if wrong_grade: audit_issues.append(f"{len(wrong_grade)} mis-graded rows")
    if pnl_wrong: audit_issues.append(f"{len(pnl_wrong)} P&L formula mismatches")
    if bad_snapshot: audit_issues.append(f"{len(bad_snapshot)} lookahead snapshots")
    if dups: audit_issues.append(f"{len(dups)} duplicate (event,player,side,line) keys")
    if not audit_issues:
        print("    ✅ CLEAN — no integrity issues detected.")
    else:
        print("    ⚠️ ISSUES DETECTED:")
        for i in audit_issues:
            print(f"      - {i}")

    # ── 9) 3-fold robustness on UNDER (only if audit broadly clean) ─
    print("\n  ▶ 9) 3-FOLD ROBUSTNESS  (earned_runs UNDER [+101,+200] baseline)")
    a = _agg(rows)
    print(f"    OVERALL: n={a['n']} gr={a['gr']} W={a['w']} L={a['l']} "
          f"HR={a['hr']}% ROI={a['roi']}% P&L={a['pnl']}u  "
          f"CI=[{a['roi_lo']},{a['roi_hi']}]  cons={a['consist']}")
    print(f"      maxWin={a['max_win_day']}  maxLoss={a['max_loss_day']}")
    for fold in ("A: 05-03..06","B: 05-07..10","C: 05-11..15"):
        sub = [r for r in rows if _fold(r["game_date"]) == fold]
        af = _agg(sub)
        print(f"    {fold:<16s}  n={af['n']:>3d} gr={af['gr']:>3d} "
              f"W={af['w']:>2d} L={af['l']:>2d}  HR={str(af['hr']):>5s}%  "
              f"ROI={str(af['roi']):>6s}%  CI=[{af['roi_lo']},{af['roi_hi']}]  "
              f"P&L={af['pnl']:>+6.2f}u  cons={af['consist']}  "
              f"maxWin={af['max_win_day']}  maxLoss={af['max_loss_day']}")


async def main():
    await target1_runs_under()
    await target2_earned_runs_under()


asyncio.run(main())
