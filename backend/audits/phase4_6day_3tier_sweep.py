"""Phase 4 — 6-day, 3-tier sweep.

Dates: 2026-05-01 → 2026-05-06.
Tiers: safe_haven, front_lines, war_zone.
Gate path: universal only (Phase 4 production gate engine).
Wager mode: displayed-card (top-20 per tier per date), $1 flat.
Snapshot: 11:00:00Z each date.

NO gate or threshold or model changes. Read-only on every collection
except the standard replay output schemas.
"""
from __future__ import annotations
import os, sys
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import asyncio, gc, json
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

from services.replay.production_replay_runner import run_production_replay


DATES = ("2026-05-01", "2026-05-02", "2026-05-03",
         "2026-05-04", "2026-05-05", "2026-05-06")
TIERS = ("safe_haven", "front_lines", "war_zone")
SPORT = "mlb"


def _odds_bucket(o):
    if o is None: return "_unknown"
    o = int(o)
    if o >= 200: return "plus_high"
    if 100 <= o < 200: return "plus_med"
    if 0 < o < 100: return "plus_low"
    if o == 100: return "even"
    if -110 < o < 0: return "minus_low"
    if -150 < o <= -110: return "minus_med"
    if -250 < o <= -150: return "minus_heavy"
    return "minus_xx"


async def _cards_for(db, serial):
    return await db.mlb_production_replay_cards.find(
        {"replay_serial": serial}, projection={"_id": 0}
    ).sort("rank", 1).to_list(length=None)


def _grade_agg(cards):
    w = sum(1 for c in cards if c.get("grade_status") == "win")
    l = sum(1 for c in cards if c.get("grade_status") == "loss")
    p = sum(1 for c in cards if c.get("grade_status") == "push")
    u = sum(1 for c in cards if c.get("grade_status")
            not in ("win", "loss", "push"))
    stake = sum(float(c.get("stake_units") or 0) for c in cards)
    profit = sum(float(c.get("profit_units") or 0) for c in cards)
    dec = w + l
    return {
        "n": len(cards), "w": w, "l": l, "p": p, "u": u,
        "stake": stake, "profit": profit,
        "hr_pct": round(100*w/dec, 4) if dec else 0.0,
        "roi_pct": round(100*profit/stake, 4) if stake else 0.0,
    }


def _card_key(c):
    return (str(c.get("player_name_normalized")),
            str(c.get("stat_family")),
            float(c.get("line")) if c.get("line") is not None else None,
            str(c.get("side")))


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    print(f"\n=== Phase 4 — 6-day × 3-tier sweep ===")
    print(f"dates: {', '.join(DATES)}")
    print(f"tiers: {', '.join(TIERS)}\n")

    # results[date][tier] = {summary, cards, card_agg}
    results = {d: {} for d in DATES}

    for d in DATES:
        snap = f"{d}T11:00:00Z"
        for tier in TIERS:
            print(f"  → running {d} {tier} ...", flush=True)
            s = await run_production_replay(
                db, sport=SPORT, game_date=d, snapshot_iso=snap,
                tier=tier, gate_path="universal", dry_run=False,
                force_layer3=False, notes="phase4_6day_sweep_2026_05_17",
            )
            cards = await _cards_for(db, s["serial"])
            agg = _grade_agg(cards)
            results[d][tier] = {"summary": s, "cards": cards, "agg": agg}
            print(f"     {s['serial']}  rows_qual={s['rows_qualified']}  "
                  f"cards={agg['n']}  W/L/P/U={agg['w']}/{agg['l']}/{agg['p']}/{agg['u']}  "
                  f"HR={agg['hr_pct']:.2f}%  ROI={agg['roi_pct']:+.2f}%  "
                  f"P&L={agg['profit']:+.4f}")
            gc.collect()

    # ── Reporting ──────────────────────────────────────────────────
    print("\n\n" + "=" * 120)
    print(f"  PHASE 4 — 6-DAY × 3-TIER CONSOLIDATED REPORT")
    print("=" * 120)

    # 1+2. Per-date per-tier table
    print("\n──── (1) PER-DATE × PER-TIER displayed-card serials + W/L/U / HR / ROI / P&L")
    print(f"  {'date':>11} {'tier':<13} {'serial':<48} {'cards':>6} {'W':>4} {'L':>4} {'P':>3} {'U':>4}"
          f"{'HR%':>9}{'ROI%':>9}{'P&L':>10}")
    for d in DATES:
        for tier in TIERS:
            a = results[d][tier]["agg"]
            s = results[d][tier]["summary"]
            print(f"  {d:>11} {tier:<13} {s['serial']:<48} {a['n']:>6} {a['w']:>4} {a['l']:>4} "
                  f"{a['p']:>3} {a['u']:>4}{a['hr_pct']:>8.2f}%{a['roi_pct']:>+8.2f}%{a['profit']:>+10.4f}")

    # 2. Daily totals (all tiers blended)
    print("\n──── (2) DAILY TOTALS (all 3 tiers blended, $1/card flat)")
    print(f"  {'date':>11} {'cards':>6} {'W':>4} {'L':>4} {'P':>3} {'U':>4}"
          f"{'HR%':>9}{'ROI%':>9}{'P&L':>10}")
    daily_totals = {}
    for d in DATES:
        w = sum(results[d][t]["agg"]["w"] for t in TIERS)
        l = sum(results[d][t]["agg"]["l"] for t in TIERS)
        p = sum(results[d][t]["agg"]["p"] for t in TIERS)
        u = sum(results[d][t]["agg"]["u"] for t in TIERS)
        n = sum(results[d][t]["agg"]["n"] for t in TIERS)
        stake = sum(results[d][t]["agg"]["stake"] for t in TIERS)
        profit = sum(results[d][t]["agg"]["profit"] for t in TIERS)
        dec = w + l
        hr = (100*w/dec) if dec else 0.0
        roi = (100*profit/stake) if stake else 0.0
        daily_totals[d] = {"n":n, "w":w, "l":l, "p":p, "u":u,
                            "stake":stake, "profit":profit,
                            "hr_pct":round(hr,4), "roi_pct":round(roi,4)}
        print(f"  {d:>11} {n:>6} {w:>4} {l:>4} {p:>3} {u:>4}{hr:>8.2f}%{roi:>+8.2f}%{profit:>+10.4f}")

    # 3. Aggregate (6-day totals)
    print("\n──── (3) AGGREGATE 6-DAY TOTALS")
    grand = {"n":0,"w":0,"l":0,"p":0,"u":0,"stake":0.0,"profit":0.0}
    for d in DATES:
        for k in ("n","w","l","p","u","stake","profit"):
            grand[k] += daily_totals[d][k]
    gdec = grand["w"] + grand["l"]
    ghr = (100*grand["w"]/gdec) if gdec else 0.0
    groi = (100*grand["profit"]/grand["stake"]) if grand["stake"] else 0.0
    print(f"  cards displayed  : {grand['n']}")
    print(f"  W / L / P / U    : {grand['w']} / {grand['l']} / {grand['p']} / {grand['u']}")
    print(f"  stake            : ${grand['stake']:.2f}")
    print(f"  profit           : ${grand['profit']:+.4f}")
    print(f"  HIT RATE         : {ghr:.4f}%")
    print(f"  ROI              : {groi:+.4f}%")

    # 4. By-tier breakdown (6-day aggregate, displayed cards)
    print("\n──── (4) BY-TIER 6-DAY AGGREGATE (displayed cards)")
    print(f"  {'tier':<13}{'cards':>6}{'W':>5}{'L':>5}{'P':>5}{'U':>5}{'stake':>9}"
          f"{'profit':>10}{'HR%':>9}{'ROI%':>9}")
    tier_totals = {}
    for tier in TIERS:
        cards_all = []
        for d in DATES:
            cards_all.extend(results[d][tier]["cards"])
        a = _grade_agg(cards_all)
        tier_totals[tier] = a
        print(f"  {tier:<13}{a['n']:>6}{a['w']:>5}{a['l']:>5}{a['p']:>5}{a['u']:>5}"
              f"{a['stake']:>9.2f}{a['profit']:>+10.4f}{a['hr_pct']:>8.2f}%{a['roi_pct']:>+8.2f}%")

    # 5. By-stat-family (displayed cards, all tiers blended; AND per-tier)
    print("\n──── (5) BY STAT FAMILY (6-day aggregate, displayed cards)")
    print(f"  {'tier':<13}{'family':>22}{'n':>5}{'W':>4}{'L':>4}{'U':>4}"
          f"{'HR%':>9}{'ROI%':>9}{'P&L':>10}")
    fam_per_tier = {tier: {} for tier in TIERS}
    fam_blended = {}
    for tier in TIERS:
        for d in DATES:
            for c in results[d][tier]["cards"]:
                fam = c.get("stat_family") or "_unknown"
                for bucket in (fam_per_tier[tier], fam_blended):
                    e = bucket.setdefault(fam,
                        {"n":0,"w":0,"l":0,"u":0,"stake":0.0,"profit":0.0})
                    e["n"] += 1
                    st = c.get("grade_status")
                    if st == "win": e["w"] += 1
                    elif st == "loss": e["l"] += 1
                    elif st not in ("push",): e["u"] += 1
                    e["stake"] += float(c.get("stake_units") or 0)
                    e["profit"] += float(c.get("profit_units") or 0)
    for tier in TIERS:
        for fam, e in sorted(fam_per_tier[tier].items(),
                             key=lambda kv: -kv[1]["n"]):
            dec = e["w"] + e["l"]
            hr = (100*e["w"]/dec) if dec else 0.0
            roi = (100*e["profit"]/e["stake"]) if e["stake"] else 0.0
            print(f"  {tier:<13}{fam:>22}{e['n']:>5}{e['w']:>4}{e['l']:>4}{e['u']:>4}"
                  f"{hr:>8.2f}%{roi:>+8.2f}%{e['profit']:>+10.4f}")
    print(f"\n  ── BLENDED (all tiers)")
    print(f"  {'family':>22}{'n':>5}{'W':>4}{'L':>4}{'U':>4}{'HR%':>9}{'ROI%':>9}{'P&L':>10}")
    for fam, e in sorted(fam_blended.items(), key=lambda kv: -kv[1]["n"]):
        dec = e["w"] + e["l"]
        hr = (100*e["w"]/dec) if dec else 0.0
        roi = (100*e["profit"]/e["stake"]) if e["stake"] else 0.0
        print(f"  {fam:>22}{e['n']:>5}{e['w']:>4}{e['l']:>4}{e['u']:>4}"
              f"{hr:>8.2f}%{roi:>+8.2f}%{e['profit']:>+10.4f}")

    # 6. By-odds-bucket (displayed cards)
    print("\n──── (6) BY ODDS BUCKET (6-day aggregate, displayed cards)")
    print(f"  {'tier':<13}{'bucket':>14}{'n':>5}{'W':>4}{'L':>4}{'U':>4}"
          f"{'HR%':>9}{'ROI%':>9}{'P&L':>10}")
    bkt_per_tier = {tier: {} for tier in TIERS}
    bkt_blended = {}
    for tier in TIERS:
        for d in DATES:
            for c in results[d][tier]["cards"]:
                b = _odds_bucket(c.get("odds"))
                for bucket_dict in (bkt_per_tier[tier], bkt_blended):
                    e = bucket_dict.setdefault(b,
                        {"n":0,"w":0,"l":0,"u":0,"stake":0.0,"profit":0.0})
                    e["n"] += 1
                    st = c.get("grade_status")
                    if st == "win": e["w"] += 1
                    elif st == "loss": e["l"] += 1
                    elif st not in ("push",): e["u"] += 1
                    e["stake"] += float(c.get("stake_units") or 0)
                    e["profit"] += float(c.get("profit_units") or 0)
    for tier in TIERS:
        for b in ("plus_high","plus_med","plus_low","even","minus_low",
                   "minus_med","minus_heavy","minus_xx","_unknown"):
            if b not in bkt_per_tier[tier]: continue
            e = bkt_per_tier[tier][b]
            dec = e["w"] + e["l"]
            hr = (100*e["w"]/dec) if dec else 0.0
            roi = (100*e["profit"]/e["stake"]) if e["stake"] else 0.0
            print(f"  {tier:<13}{b:>14}{e['n']:>5}{e['w']:>4}{e['l']:>4}{e['u']:>4}"
                  f"{hr:>8.2f}%{roi:>+8.2f}%{e['profit']:>+10.4f}")
    print(f"\n  ── BLENDED (all tiers)")
    print(f"  {'bucket':>14}{'n':>5}{'W':>4}{'L':>4}{'U':>4}{'HR%':>9}{'ROI%':>9}{'P&L':>10}")
    for b in ("plus_high","plus_med","plus_low","even","minus_low",
               "minus_med","minus_heavy","minus_xx","_unknown"):
        if b not in bkt_blended: continue
        e = bkt_blended[b]
        dec = e["w"] + e["l"]
        hr = (100*e["w"]/dec) if dec else 0.0
        roi = (100*e["profit"]/e["stake"]) if e["stake"] else 0.0
        print(f"  {b:>14}{e['n']:>5}{e['w']:>4}{e['l']:>4}{e['u']:>4}"
              f"{hr:>8.2f}%{roi:>+8.2f}%{e['profit']:>+10.4f}")

    # 7. Max-drawdown day + 8. Best day (by P&L on blended daily totals)
    sorted_days = sorted(daily_totals.items(),
                          key=lambda kv: kv[1]["profit"])
    worst_d, worst_v = sorted_days[0]
    best_d, best_v = sorted_days[-1]
    print(f"\n──── (7+8) WORST DAY (max single-day drawdown by P&L)")
    print(f"  {worst_d}: cards={worst_v['n']}  W/L/P/U={worst_v['w']}/{worst_v['l']}/{worst_v['p']}/{worst_v['u']}  "
          f"HR={worst_v['hr_pct']:.2f}%  ROI={worst_v['roi_pct']:+.2f}%  P&L={worst_v['profit']:+.4f}")
    print(f"      BEST DAY")
    print(f"  {best_d}: cards={best_v['n']}  W/L/P/U={best_v['w']}/{best_v['l']}/{best_v['p']}/{best_v['u']}  "
          f"HR={best_v['hr_pct']:.2f}%  ROI={best_v['roi_pct']:+.2f}%  P&L={best_v['profit']:+.4f}")

    # Cumulative running P&L for context
    print(f"\n  ── cumulative running blended P&L (chronological)")
    run_pl = 0.0
    for d in DATES:
        run_pl += daily_totals[d]["profit"]
        print(f"     after {d}: cum P&L = {run_pl:+.4f}")

    # 9. Overlap between tiers (same card key in 2+ tier cards on same date)
    print("\n──── (9) OVERLAP BETWEEN TIERS (displayed cards)")
    print(f"  {'date':>11}  {'SH∩FL':>8}  {'SH∩WZ':>8}  {'FL∩WZ':>8}  {'all_3':>8}")
    overlap_summary = {}
    for d in DATES:
        ks = {tier: set(_card_key(c) for c in results[d][tier]["cards"])
              for tier in TIERS}
        sh_fl = ks["safe_haven"] & ks["front_lines"]
        sh_wz = ks["safe_haven"] & ks["war_zone"]
        fl_wz = ks["front_lines"] & ks["war_zone"]
        all3 = ks["safe_haven"] & ks["front_lines"] & ks["war_zone"]
        overlap_summary[d] = {"sh_fl": len(sh_fl), "sh_wz": len(sh_wz),
                               "fl_wz": len(fl_wz), "all3": len(all3)}
        print(f"  {d:>11}  {len(sh_fl):>8}  {len(sh_wz):>8}  {len(fl_wz):>8}  {len(all3):>8}")

    # 10. Total displayed cards per day (re-stated as separate section)
    print("\n──── (10) TOTAL DISPLAYED CARDS PER DAY (sum across 3 tiers)")
    for d in DATES:
        per = {t: results[d][t]["agg"]["n"] for t in TIERS}
        tot = sum(per.values())
        print(f"  {d}: total={tot}  (SH={per['safe_haven']}, FL={per['front_lines']}, WZ={per['war_zone']})")

    # 12. Failed / partial grading counts
    print("\n──── (12) UNGRADED-CARD COUNTS (per tier × per date)")
    print(f"  {'date':>11} {'SH_u':>6} {'FL_u':>6} {'WZ_u':>6} {'total_u':>9}  reason")
    total_ungraded = 0
    ungraded_by_family = {}
    for d in DATES:
        u_sh = results[d]["safe_haven"]["agg"]["u"]
        u_fl = results[d]["front_lines"]["agg"]["u"]
        u_wz = results[d]["war_zone"]["agg"]["u"]
        u_total = u_sh + u_fl + u_wz
        total_ungraded += u_total
        # Reason aggregation: stat_family of ungraded cards
        for t in TIERS:
            for c in results[d][t]["cards"]:
                if c.get("grade_status") not in ("win","loss","push"):
                    fam = c.get("stat_family") or "_unknown"
                    ungraded_by_family[fam] = ungraded_by_family.get(fam, 0) + 1
        print(f"  {d:>11} {u_sh:>6} {u_fl:>6} {u_wz:>6} {u_total:>9}")
    print(f"\n  ── ungraded by stat_family (across the 6 days, all tiers)")
    for fam, n in sorted(ungraded_by_family.items(), key=lambda kv: -kv[1]):
        print(f"     {fam:>20}: {n}")

    # 11. JSON artifact path
    art = "/app/backend/audits/phase4_3tier_2026-05-01_to_2026-05-06.json"
    out = {
        "game_dates": list(DATES),
        "tiers": list(TIERS),
        "sport": SPORT,
        "gate_path": "universal",
        "wager_mode": "displayed_card_flat_1",
        "per_date": {
            d: {
                t: {
                    "serial": results[d][t]["summary"]["serial"],
                    "summary": {k: v for k, v in results[d][t]["summary"].items()
                                if k != "layer3_summary"},
                    "card_agg": results[d][t]["agg"],
                    "cards_count": len(results[d][t]["cards"]),
                }
                for t in TIERS
            }
            for d in DATES
        },
        "daily_totals": daily_totals,
        "tier_totals_displayed": tier_totals,
        "fam_per_tier": fam_per_tier,
        "fam_blended": fam_blended,
        "bkt_per_tier": bkt_per_tier,
        "bkt_blended": bkt_blended,
        "overlap": overlap_summary,
        "grand": {
            "n": grand["n"], "w": grand["w"], "l": grand["l"],
            "p": grand["p"], "u": grand["u"],
            "stake": grand["stake"], "profit": grand["profit"],
            "hr_pct": round(ghr, 4), "roi_pct": round(groi, 4),
        },
        "best_day": {"date": best_d, **best_v},
        "worst_day": {"date": worst_d, **worst_v},
        "ungraded_by_family": ungraded_by_family,
    }
    with open(art, "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"\n──── (11) JSON ARTIFACT: {art}")

    cli.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
