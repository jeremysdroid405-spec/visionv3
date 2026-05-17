"""Phase 4b — Clean baseline 6-day × 3-tier sweep with universal odds routing.

Reuses the Phase 4 sweep + Phase 4a regrade + Phase 4a void→push paths,
but now `gate_path="universal"` invokes the universal odds-bucket
router as the FIRST step per row — so props are rejected with
`tier_odds_bucket_fail` when their `tier_reference_odds` doesn't match
the tier being evaluated. NBA path untouched.

Output: comprehensive consolidated report + JSON artifact.
"""
from __future__ import annotations
import os, sys
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import asyncio, json, gc
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple, Optional, Set
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne

from services.replay.production_replay_runner import run_production_replay
from services.replay.providers.mlb_adapter import (
    _resolve_mlb_family, _MLB_STAT_FIELD_MAP,
)
from services.replay.mlb_feature_cache import normalize_player_name
from services.scoring.odds_bucket_router import TIER_ODDS_BUCKET_FAIL


DATES = ("2026-05-01", "2026-05-02", "2026-05-03",
         "2026-05-04", "2026-05-05", "2026-05-06")
TIERS = ("safe_haven", "front_lines", "war_zone")
SPORT = "mlb"
MAX_DT_HOURS = 18.0


# Prior cohort serials (pre-routing) — used only for "removed/moved" diff
PRIOR_SERIALS = {
    ("2026-05-01","safe_haven"): "MLB-PRODREPLAY-20260501-SH-1100UTC-00018",
    ("2026-05-02","safe_haven"): "MLB-PRODREPLAY-20260502-SH-1100UTC-00021",
    ("2026-05-03","safe_haven"): "MLB-PRODREPLAY-20260503-SH-1100UTC-00024",
    ("2026-05-04","safe_haven"): "MLB-PRODREPLAY-20260504-SH-1100UTC-00027",
    ("2026-05-05","safe_haven"): "MLB-PRODREPLAY-20260505-SH-1100UTC-00030",
    ("2026-05-06","safe_haven"): "MLB-PRODREPLAY-20260506-SH-1100UTC-00033",
    ("2026-05-01","front_lines"): "MLB-PRODREPLAY-20260501-FL-1100UTC-00019",
    ("2026-05-02","front_lines"): "MLB-PRODREPLAY-20260502-FL-1100UTC-00022",
    ("2026-05-03","front_lines"): "MLB-PRODREPLAY-20260503-FL-1100UTC-00025",
    ("2026-05-04","front_lines"): "MLB-PRODREPLAY-20260504-FL-1100UTC-00028",
    ("2026-05-05","front_lines"): "MLB-PRODREPLAY-20260505-FL-1100UTC-00031",
    ("2026-05-06","front_lines"): "MLB-PRODREPLAY-20260506-FL-1100UTC-00034",
    ("2026-05-01","war_zone"): "MLB-PRODREPLAY-20260501-WZ-1100UTC-00020",
    ("2026-05-02","war_zone"): "MLB-PRODREPLAY-20260502-WZ-1100UTC-00023",
    ("2026-05-03","war_zone"): "MLB-PRODREPLAY-20260503-WZ-1100UTC-00026",
    ("2026-05-04","war_zone"): "MLB-PRODREPLAY-20260504-WZ-1100UTC-00029",
    ("2026-05-05","war_zone"): "MLB-PRODREPLAY-20260505-WZ-1100UTC-00032",
    ("2026-05-06","war_zone"): "MLB-PRODREPLAY-20260506-WZ-1100UTC-00035",
}


def _bkt_odds(o):
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


def _parse(s):
    if not s: return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _date_prefix(s):
    return s[:10] if s and len(s) >= 10 else None


def _composite(stats, keys):
    vals = []
    for k in keys:
        v = stats.get(k)
        if v is None: return None
        try: vals.append(float(v))
        except Exception: return None
    return sum(vals)


def _resolve_actual(stats, fam):
    if fam == "hits_runs_rbis":
        return _composite(stats, ("hits", "runs", "rbis"))
    field = _MLB_STAT_FIELD_MAP.get(fam)
    if field is None: return None
    v = stats.get(field)
    if v is None:
        if field == "pitcher_outs":
            ip = stats.get("innings_pitched")
            return float(ip)*3.0 if ip is not None else None
        return None
    try: return float(v)
    except (TypeError, ValueError): return None


def _best_log_for(logs, *, game_date, commence_time):
    if not logs: return None
    try: d0 = datetime.strptime(game_date, "%Y-%m-%d").date()
    except Exception: return None
    window = {(d0-timedelta(days=1)).isoformat(),
               d0.isoformat(),
               (d0+timedelta(days=1)).isoformat()}
    ct = _parse(commence_time)
    cands = []
    for g in logs:
        dp = _date_prefix(g.get("date") or g.get("game_date"))
        if dp is None or dp not in window: continue
        if ct is None:
            if dp == d0.isoformat(): cands.append((0.0, g))
            continue
        lt = _parse(g.get("date") or g.get("game_date"))
        if lt is None: continue
        dt = abs((lt-ct).total_seconds())/3600.0
        if dt > MAX_DT_HOURS: continue
        cands.append((dt, g))
    if not cands: return None
    cands.sort(key=lambda kv: kv[0])
    return cands[0][1]


def _grade(actual, *, line, side, odds):
    if actual is None:
        return {"status":"ungraded","profit_units":0.0,
                 "stake_units":0.0,"actual":None}
    payout = (odds/100.0) if odds > 0 else (100.0/-odds)
    if side == "OVER":
        if actual > line: return {"status":"win","profit_units":payout,
                                    "stake_units":1.0,"actual":actual}
        if actual < line: return {"status":"loss","profit_units":-1.0,
                                    "stake_units":1.0,"actual":actual}
        return {"status":"push","profit_units":0.0,"stake_units":1.0,"actual":actual}
    if actual < line: return {"status":"win","profit_units":payout,
                                "stake_units":1.0,"actual":actual}
    if actual > line: return {"status":"loss","profit_units":-1.0,
                                "stake_units":1.0,"actual":actual}
    return {"status":"push","profit_units":0.0,"stake_units":1.0,"actual":actual}


async def regrade_and_void_push(db, serials):
    """Apply regrade-by-best-log + void-push to every card. In-place."""
    cards = await db.mlb_production_replay_cards.find(
        {"replay_serial": {"$in": serials}}, projection={"_id":0}
    ).to_list(length=None)
    out_rows = await db.mlb_production_replay_outputs.find(
        {"replay_serial": {"$in": serials}, "gate_pass": True},
        projection={"_id":0,"replay_serial":1,"player_name_normalized":1,
                     "market":1,"line":1,"side":1,"book":1,
                     "event_id":1,"commence_time":1,"game_date":1,
                     "stat_family":1,"home_team":1,"away_team":1,
                     "tier_reference_odds":1,"tier_reference_book":1,
                     "routed_tier":1},
    ).to_list(length=None)
    by_key = {(o["replay_serial"], o["player_name_normalized"],
               o["market"], o["line"], o["side"], o["book"]): o
              for o in out_rows}
    players = {c["player_name_normalized"] for c in cards}
    players.discard(None)
    bdl = {}
    cursor = db.mlb_master_hub_2026.find(
        {"bdl_game_logs.0":{"$exists":True}},
        projection={"_id":0,"player_name":1,"display_name":1,
                     "mlb_full_name":1,"bdl_game_logs":1})
    async for hub in cursor:
        canon = hub.get("display_name") or hub.get("player_name") or hub.get("mlb_full_name") or ""
        nk = normalize_player_name(canon)
        if nk and nk in players:
            logs = hub.get("bdl_game_logs") or []
            prior = bdl.get(nk)
            if prior is None or len(logs) > len(prior):
                bdl[nk] = logs

    bulk = []
    for c in cards:
        k = (c["replay_serial"], c["player_name_normalized"],
             c["market"], c["line"], c["side"], c["book"])
        out = by_key.get(k)
        if out is None: continue
        market = (c.get("market") or "").lower()
        fam = _resolve_mlb_family(market, out.get("stat_family"))
        logs = bdl.get(c["player_name_normalized"] or "", [])
        log = _best_log_for(logs, game_date=out.get("game_date") or "",
                             commence_time=out.get("commence_time"))
        actual = _resolve_actual(log, fam) if log is not None else None
        new = _grade(actual, line=float(c["line"]),
                      side=(c["side"] or "OVER").upper(),
                      odds=int(c["odds"]))
        new_status = new["status"]
        # If still ungraded after regrade → industry void→push
        if new_status not in ("win","loss","push"):
            bulk.append(UpdateOne(
                {"replay_serial":c["replay_serial"],"rank":c.get("rank")},
                {"$set":{"grade_status":"push","actual_value":None,
                          "profit_units":0.0,"stake_units":1.0,
                          "regrade_method":"phase4b_void_push_2026_05_17",
                          "regrade_reason":"void_dnp_or_postponed"}}))
        else:
            cur_status = c.get("grade_status")
            cur_profit = float(c.get("profit_units") or 0)
            if new_status != cur_status or cur_profit != float(new["profit_units"]):
                bulk.append(UpdateOne(
                    {"replay_serial":c["replay_serial"],"rank":c.get("rank")},
                    {"$set":{"grade_status":new_status,
                              "actual_value":new["actual"],
                              "profit_units":float(new["profit_units"]),
                              "stake_units":float(new["stake_units"]),
                              "regrade_method":"phase4b_regrade_2026_05_17"}}))
    if bulk:
        res = await db.mlb_production_replay_cards.bulk_write(bulk, ordered=False)
        print(f"   regrade+void: matched={res.matched_count} modified={res.modified_count}")
    return cards


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    print(f"\n=== Phase 4b — 6-day × 3-tier CLEAN sweep (universal odds routing ON) ===\n")

    results = {d:{} for d in DATES}
    rejects_by_tier_date = {}
    for d in DATES:
        snap = f"{d}T11:00:00Z"
        for tier in TIERS:
            print(f"→ {d} {tier}")
            s = await run_production_replay(
                db, sport=SPORT, game_date=d, snapshot_iso=snap,
                tier=tier, gate_path="universal", dry_run=False,
                force_layer3=False, notes="phase4b_6day_2026_05_17",
            )
            # Count routing rejects for this run
            n_router_rejects = await db.mlb_production_replay_outputs.count_documents(
                {"replay_serial": s["serial"],
                 "failed_gates": TIER_ODDS_BUCKET_FAIL})
            results[d][tier] = {"summary": s, "n_router_rejects": n_router_rejects}
            print(f"   {s['serial']}  qualified={s['rows_qualified']}  "
                  f"cards={s['cards_displayed']}  "
                  f"router_rejects={n_router_rejects}")
            gc.collect()

    # Regrade + void→push pass over the new cards (in-place)
    new_serials = [results[d][t]["summary"]["serial"] for d in DATES for t in TIERS]
    print(f"\n→ regrade + void→push over {len(new_serials)} new serials")
    await regrade_and_void_push(db, new_serials)

    # ── REPORTS ──────────────────────────────────────────────────────
    # Pull final cards
    final_cards = {}
    for d in DATES:
        for tier in TIERS:
            sr = results[d][tier]["summary"]["serial"]
            cs = await db.mlb_production_replay_cards.find(
                {"replay_serial": sr}, projection={"_id":0}
            ).sort("rank", 1).to_list(length=None)
            final_cards[(d,tier)] = cs

    def _agg(cards):
        w=l=p=u=0; stake=profit=0.0
        for c in cards:
            st = c.get("grade_status")
            if st == "win": w+=1
            elif st == "loss": l+=1
            elif st == "push": p+=1
            else: u+=1
            stake += float(c.get("stake_units") or 0)
            profit += float(c.get("profit_units") or 0)
        dec = w+l
        return {"n":len(cards),"w":w,"l":l,"p":p,"u":u,"stake":stake,
                "profit":profit,
                "hr_pct":round(100*w/dec,4) if dec else 0.0,
                "roi_pct":round(100*profit/stake,4) if stake else 0.0}

    print("\n"+"="*128)
    print("  PHASE 4b — 6-day × 3-tier CLEAN BASELINE")
    print("="*128)

    print("\n(1)+(2)+(3)+(4)+(5)+(6)+(7) PER-DATE × PER-TIER")
    print(f"  {'date':>11} {'tier':<13} {'serial':<48} "
          f"{'qual':>5} {'cards':>5} {'W':>3} {'L':>3} {'P':>3} {'U':>3} "
          f"{'HR%':>8} {'ROI%':>8} {'P&L':>9} {'routerX':>8}")
    grand = {"n":0,"w":0,"l":0,"p":0,"u":0,"stake":0.0,"profit":0.0}
    tier_totals = {t:{"n":0,"w":0,"l":0,"p":0,"u":0,"stake":0.0,"profit":0.0,
                       "qualified":0,"router_rejects":0} for t in TIERS}
    daily_totals = {}
    for d in DATES:
        dt = {"n":0,"w":0,"l":0,"p":0,"u":0,"stake":0.0,"profit":0.0}
        for tier in TIERS:
            a = _agg(final_cards[(d,tier)])
            s = results[d][tier]["summary"]
            rx = results[d][tier]["n_router_rejects"]
            print(f"  {d:>11} {tier:<13} {s['serial']:<48} "
                  f"{s['rows_qualified']:>5} {a['n']:>5} {a['w']:>3} {a['l']:>3} "
                  f"{a['p']:>3} {a['u']:>3} {a['hr_pct']:>7.2f}% "
                  f"{a['roi_pct']:>+7.2f}% {a['profit']:>+9.4f} {rx:>8}")
            for k in ("n","w","l","p","u","stake","profit"):
                grand[k] += a[k]; tier_totals[tier][k] += a[k]; dt[k] += a[k]
            tier_totals[tier]["qualified"] += s["rows_qualified"]
            tier_totals[tier]["router_rejects"] += rx
        daily_totals[d] = dt

    gdec = grand["w"]+grand["l"]
    print("\n──── (5)+(6)+(7) AGGREGATE 6-DAY")
    print(f"  cards   : {grand['n']}")
    print(f"  W/L/P/U : {grand['w']}/{grand['l']}/{grand['p']}/{grand['u']}")
    print(f"  stake   : ${grand['stake']:.2f}")
    print(f"  profit  : ${grand['profit']:+.4f}")
    print(f"  HR      : {100*grand['w']/gdec:.4f}%" if gdec else "  HR      : —")
    print(f"  ROI     : {100*grand['profit']/grand['stake']:+.4f}%" if grand['stake'] else "  ROI     : —")
    print(f"  ungraded%: {100*grand['u']/grand['n']:.4f}%" if grand['n'] else "  ungraded%: —")

    print("\n──── BY-TIER aggregate (6-day, displayed cards)")
    for tier in TIERS:
        e = tier_totals[tier]
        dec = e["w"]+e["l"]
        hr = (100*e["w"]/dec) if dec else 0.0
        roi = (100*e["profit"]/e["stake"]) if e["stake"] else 0.0
        print(f"  {tier:<13} cards={e['n']:>3} W={e['w']:>3} L={e['l']:>3} P={e['p']:>3} "
              f"U={e['u']:>3} qual={e['qualified']:>4} rtrX={e['router_rejects']:>6}  "
              f"HR={hr:>6.2f}%  ROI={roi:>+6.2f}%  P&L=${e['profit']:+.4f}")

    print("\n──── DAILY blended (HR/ROI/P&L)")
    for d in DATES:
        e = daily_totals[d]
        dec = e["w"]+e["l"]
        hr = (100*e["w"]/dec) if dec else 0.0
        roi = (100*e["profit"]/e["stake"]) if e["stake"] else 0.0
        print(f"  {d}: cards={e['n']:>3} W/L/P/U={e['w']}/{e['l']}/{e['p']}/{e['u']}  "
              f"HR={hr:>6.2f}%  ROI={roi:>+6.2f}%  P&L=${e['profit']:+.4f}")

    # (8) BY STAT FAMILY (blended displayed cards)
    print("\n(8) BY STAT FAMILY (blended displayed cards, 6-day)")
    fam_per_tier = {t:{} for t in TIERS}
    fam_blended = {}
    for d in DATES:
        for tier in TIERS:
            for c in final_cards[(d,tier)]:
                fam = c.get("stat_family") or "_unknown"
                for bucket in (fam_per_tier[tier], fam_blended):
                    e = bucket.setdefault(fam,
                        {"n":0,"w":0,"l":0,"p":0,"u":0,"stake":0.0,"profit":0.0})
                    e["n"] += 1
                    st = c.get("grade_status")
                    if st == "win": e["w"]+=1
                    elif st == "loss": e["l"]+=1
                    elif st == "push": e["p"]+=1
                    else: e["u"]+=1
                    e["stake"] += float(c.get("stake_units") or 0)
                    e["profit"] += float(c.get("profit_units") or 0)
    print(f"\n  Blended:")
    print(f"  {'family':>22}{'n':>5}{'W':>4}{'L':>4}{'P':>3}{'U':>3}"
          f"{'HR%':>9}{'ROI%':>9}{'P&L':>10}")
    for fam,e in sorted(fam_blended.items(), key=lambda kv:-kv[1]["n"]):
        dec = e["w"]+e["l"]
        hr = (100*e["w"]/dec) if dec else 0.0
        roi = (100*e["profit"]/e["stake"]) if e["stake"] else 0.0
        print(f"  {fam:>22}{e['n']:>5}{e['w']:>4}{e['l']:>4}{e['p']:>3}{e['u']:>3}"
              f"{hr:>8.2f}%{roi:>+8.2f}%{e['profit']:>+10.4f}")
    print(f"\n  Per-tier:")
    print(f"  {'tier':<13}{'family':>22}{'n':>5}{'W':>4}{'L':>4}{'HR%':>9}{'ROI%':>9}{'P&L':>10}")
    for tier in TIERS:
        for fam,e in sorted(fam_per_tier[tier].items(), key=lambda kv:-kv[1]["n"]):
            dec = e["w"]+e["l"]
            hr = (100*e["w"]/dec) if dec else 0.0
            roi = (100*e["profit"]/e["stake"]) if e["stake"] else 0.0
            print(f"  {tier:<13}{fam:>22}{e['n']:>5}{e['w']:>4}{e['l']:>4}"
                  f"{hr:>8.2f}%{roi:>+8.2f}%{e['profit']:>+10.4f}")

    # (9) BY ODDS BUCKET (blended displayed cards, on the DISPLAYED odds — for parity with prior reports)
    print("\n(9) BY ODDS BUCKET (blended displayed cards, 6-day)")
    bkt = {}
    for d in DATES:
        for tier in TIERS:
            for c in final_cards[(d,tier)]:
                b = _bkt_odds(c.get("odds"))
                e = bkt.setdefault(b, {"n":0,"w":0,"l":0,"p":0,"u":0,"stake":0.0,"profit":0.0})
                e["n"] += 1
                st = c.get("grade_status")
                if st == "win": e["w"]+=1
                elif st == "loss": e["l"]+=1
                elif st == "push": e["p"]+=1
                else: e["u"]+=1
                e["stake"] += float(c.get("stake_units") or 0)
                e["profit"] += float(c.get("profit_units") or 0)
    print(f"  {'bucket':>14}{'n':>5}{'W':>4}{'L':>4}{'P':>3}{'HR%':>9}{'ROI%':>9}{'P&L':>10}")
    for b in ("plus_high","plus_med","plus_low","even","minus_low",
               "minus_med","minus_heavy","minus_xx","_unknown"):
        if b not in bkt: continue
        e = bkt[b]
        dec = e["w"]+e["l"]
        hr = (100*e["w"]/dec) if dec else 0.0
        roi = (100*e["profit"]/e["stake"]) if e["stake"] else 0.0
        print(f"  {b:>14}{e['n']:>5}{e['w']:>4}{e['l']:>4}{e['p']:>3}"
              f"{hr:>8.2f}%{roi:>+8.2f}%{e['profit']:>+10.4f}")

    # (10) Routing reject counts
    print("\n(10) ROUTING REJECT COUNTS (universal `tier_odds_bucket_fail`)")
    print(f"  {'date':>11} {'tier':<13} {'router_rejects':>15} {'rows_scanned':>13}")
    total_rx = 0
    total_scanned = 0
    for d in DATES:
        for tier in TIERS:
            rx = results[d][tier]["n_router_rejects"]
            rs = results[d][tier]["summary"]["rows_scanned"]
            total_rx += rx; total_scanned += rs
            print(f"  {d:>11} {tier:<13} {rx:>15} {rs:>13}")
    print(f"\n  TOTAL: {total_rx:,} router rejects across "
          f"{total_scanned:,} row evaluations ({100*total_rx/total_scanned:.2f}%)")

    # (11) Tier overlap (displayed cards)
    print("\n(11) TIER OVERLAP (displayed cards, per date)")
    print(f"  {'date':>11}{'SH∩FL':>8}{'SH∩WZ':>8}{'FL∩WZ':>8}{'all_3':>8}")
    def ck(c): return (str(c.get("player_name_normalized")),
                       str(c.get("stat_family")),
                       float(c.get("line")) if c.get("line") is not None else None,
                       str(c.get("side")))
    overlap = {}
    for d in DATES:
        ks = {t: set(ck(c) for c in final_cards[(d,t)]) for t in TIERS}
        sf = len(ks["safe_haven"] & ks["front_lines"])
        sw = len(ks["safe_haven"] & ks["war_zone"])
        fw = len(ks["front_lines"] & ks["war_zone"])
        a3 = len(ks["safe_haven"] & ks["front_lines"] & ks["war_zone"])
        overlap[d] = {"sh_fl":sf,"sh_wz":sw,"fl_wz":fw,"all3":a3}
        print(f"  {d:>11}{sf:>8}{sw:>8}{fw:>8}{a3:>8}")

    # (12) Removed/moved examples — old SH cards that are GONE from new SH
    print("\n(12) REMOVED/MOVED EXAMPLES — old SH cards no longer in new SH")
    moved_examples = []
    for d in DATES:
        prior_sh_sr = PRIOR_SERIALS[(d, "safe_haven")]
        prior_sh_cards = await db.mlb_production_replay_cards.find(
            {"replay_serial": prior_sh_sr}, projection={"_id":0}
        ).to_list(length=None)
        prior_keys = {ck(c): c for c in prior_sh_cards}
        new_keys = {ck(c) for c in final_cards[(d, "safe_haven")]}
        gone = set(prior_keys.keys()) - new_keys
        for k in sorted(gone):
            c = prior_keys[k]
            # Find the prop's ref_odds + routed_tier from the NEW (Phase 4b)
            # SH output rows (rejects table). Look up by key in any of the
            # 3 new tier outputs (router rejects appear identically).
            new_out = await db.mlb_production_replay_outputs.find_one(
                {"replay_serial": results[d]["safe_haven"]["summary"]["serial"],
                 "player_name_normalized": c["player_name_normalized"],
                 "market": c["market"], "line": c["line"],
                 "side": c["side"], "book": c["book"]},
                projection={"_id":0,"tier_reference_odds":1,
                             "tier_reference_book":1,"routed_tier":1,
                             "failed_gates":1})
            ref = (new_out or {}).get("tier_reference_odds")
            ref_book = (new_out or {}).get("tier_reference_book")
            routed = (new_out or {}).get("routed_tier")
            moved_examples.append({
                "date":d, "player":c.get("player_name"),
                "stat":c.get("stat_family"), "line":c.get("line"),
                "side":c.get("side"), "row_odds":c.get("odds"),
                "book":c.get("book"),
                "prior_grade":c.get("grade_status"),
                "prior_profit":c.get("profit_units"),
                "tier_reference_odds":ref, "tier_reference_book":ref_book,
                "routed_tier":routed,
            })
    print(f"  total removed from new-SH cohort: {len(moved_examples)}")
    print(f"  (these props were in OLD SH but are now routed to FL/WZ/unqualified)")
    print(f"  ── distribution by new routed_tier:")
    rdist = {}
    for x in moved_examples:
        t = x["routed_tier"] or "_no_ref"
        rdist[t] = rdist.get(t,0)+1
    print(f"     {rdist}")
    print(f"  ── sample (first 30):")
    for x in moved_examples[:30]:
        print(f"     {x['date']} {(x['player'] or '')[:22]:<22} "
              f"{(x['stat'] or '')[:18]:<18} {x['line']!s:>4}/{x['side']:<5} "
              f"row={x['row_odds']:>5} ref={x['tier_reference_odds']!s:<5} "
              f"({x['tier_reference_book']!s:<10}) → routed={x['routed_tier']}  "
              f"(was {x['prior_grade']} ${x['prior_profit']})")

    # ── Persist JSON artifact ───────────────────────────────────────
    art = "/app/backend/audits/phase4b_6day_clean_baseline_2026_05_17.json"
    out = {
        "regrade_method": "phase4b_universal_router_2026_05_17",
        "dates": list(DATES), "tiers": list(TIERS),
        "gate_path": "universal", "odds_routing": "universal_router_on",
        "per_run": {
            f"{d}|{t}": {
                "serial": results[d][t]["summary"]["serial"],
                "summary": {k:v for k,v in results[d][t]["summary"].items()
                             if k != "layer3_summary"},
                "router_rejects": results[d][t]["n_router_rejects"],
                "card_agg": _agg(final_cards[(d,t)]),
            }
            for d in DATES for t in TIERS
        },
        "tier_totals": tier_totals,
        "daily_totals": daily_totals,
        "grand": {
            **grand,
            "hr_pct": round(100*grand["w"]/gdec,4) if gdec else 0.0,
            "roi_pct": round(100*grand["profit"]/grand["stake"],4) if grand["stake"] else 0.0,
            "ungraded_pct": round(100*grand["u"]/grand["n"],4) if grand["n"] else 0.0,
        },
        "fam_blended": fam_blended,
        "fam_per_tier": fam_per_tier,
        "odds_bucket_blended": bkt,
        "overlap": overlap,
        "moved_from_old_sh": moved_examples,
        "moved_from_old_sh_dist": rdist,
    }
    with open(art, "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"\n[json] wrote {art}")

    cli.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
