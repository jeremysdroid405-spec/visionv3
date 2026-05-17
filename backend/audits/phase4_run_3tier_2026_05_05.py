"""Phase 4 — 3-tier replay for 2026-05-05 via the production gate path.

Routes EVERY gate decision through `evaluate_tier_with_overrides`
(same code path the live serving uses). No duplicated thresholds.

Produces one run per tier (SH/FL/WZ), persists per-tier
`mlb_production_replay_runs` + `mlb_production_replay_outputs` +
`mlb_production_replay_cards` documents, then prints a consolidated
report:
  • Per-tier qualified-pool HR / ROI / profit
  • Per-tier displayed-card HR / ROI / profit
  • Per-tier × per-odds-bucket breakdown
  • Per-tier × per-stat-family breakdown
  • Biggest winning + losing stat family per tier
"""
from __future__ import annotations
import os, sys
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import asyncio, json
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

from services.replay.production_replay_runner import run_production_replay


GAME_DATE = "2026-05-05"
SNAPSHOT = "2026-05-05T11:00:00Z"
SPORT = "mlb"
TIERS = ("safe_haven", "front_lines", "war_zone")


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


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    print(f"\n=== Phase 4 — 3-tier production-gate replay ({GAME_DATE}) ===\n")

    summaries = {}
    for tier in TIERS:
        print(f"\n──── TIER: {tier} ───────────────────────────────────")
        # Clear prior phase4 outputs for the date+tier so the rerun
        # is clean. (Other tiers' outputs are isolated by their own
        # serials.)
        s = await run_production_replay(
            db, sport=SPORT, game_date=GAME_DATE, snapshot_iso=SNAPSHOT,
            tier=tier, gate_path="universal", dry_run=False,
            force_layer3=False,
            notes=f"phase4_3tier_{GAME_DATE}",
        )
        summaries[tier] = s
        print(f"   serial          : {s['serial']}")
        print(f"   rows_scanned    : {s['rows_scanned']}")
        print(f"   rows_qualified  : {s['rows_qualified']}")
        print(f"   cards_displayed : {s['cards_displayed']}")
        print(f"   W/L/P/U         : {s['wins']}/{s['losses']}/{s['pushes']}/{s['ungraded']}")
        print(f"   stake / profit  : {s['stake_units']:.2f} / {s['profit_units']:+.4f}")
        print(f"   HR / ROI        : {s['hit_rate_pct']:.2f}% / {s['roi_pct']:+.2f}%")

    # ── Consolidated report ─────────────────────────────────────────
    print("\n\n" + "=" * 100)
    print(f"  CONSOLIDATED 3-TIER PHASE 4 REPLAY — {GAME_DATE}")
    print("=" * 100)

    # Per-tier qualified-pool aggregates
    print("\n──── QUALIFIED-POOL aggregates (full gate-passed pool, $1/row)")
    print(f"  {'tier':<13}{'serial':<48}{'n':>6}{'W':>5}{'L':>5}{'U':>5}"
          f"{'HR%':>8}{'ROI%':>8}{'P&L':>10}")
    for tier in TIERS:
        s = summaries[tier]
        print(f"  {tier:<13}{s['serial']:<48}{s['rows_qualified']:>6}"
              f"{s['wins']:>5}{s['losses']:>5}{s['ungraded']:>5}"
              f"{s['hit_rate_pct']:>7.2f}%{s['roi_pct']:>+7.2f}%"
              f"{s['profit_units']:>+10.4f}")

    # Per-tier displayed-card aggregates
    print("\n──── DISPLAYED-CARD aggregates (top-20 per tier, $1/card flat)")
    print(f"  {'tier':<13}{'cards':>6}{'W':>5}{'L':>5}{'P':>5}{'U':>5}"
          f"{'HR%':>8}{'ROI%':>8}{'P&L':>10}")
    card_agg = {}
    for tier in TIERS:
        s = summaries[tier]
        cards = await db.mlb_production_replay_cards.find(
            {"replay_serial": s["serial"]}, projection={"_id": 0}
        ).to_list(length=None)
        w = sum(1 for c in cards if c.get("grade_status") == "win")
        l = sum(1 for c in cards if c.get("grade_status") == "loss")
        p = sum(1 for c in cards if c.get("grade_status") == "push")
        u = sum(1 for c in cards if c.get("grade_status")
                not in ("win", "loss", "push"))
        stake = sum(float(c.get("stake_units") or 0) for c in cards)
        profit = sum(float(c.get("profit_units") or 0) for c in cards)
        dec = w + l
        hr = (100*w/dec) if dec else 0.0
        roi = (100*profit/stake) if stake else 0.0
        card_agg[tier] = {"cards": cards, "w": w, "l": l, "p": p, "u": u,
                          "stake": stake, "profit": profit, "hr": hr, "roi": roi}
        print(f"  {tier:<13}{len(cards):>6}{w:>5}{l:>5}{p:>5}{u:>5}"
              f"{hr:>7.2f}%{roi:>+7.2f}%{profit:>+10.4f}")

    # Per-tier × per-odds-bucket (qualified pool, NOT displayed cards)
    print("\n──── QUALIFIED-POOL × ODDS BUCKET (HR / ROI / P&L)")
    print(f"  {'tier':<13}{'bucket':>14}{'n':>6}{'W':>5}{'L':>5}"
          f"{'HR%':>8}{'ROI%':>8}{'P&L':>10}")
    for tier in TIERS:
        rows = await db.mlb_production_replay_outputs.find(
            {"replay_serial": summaries[tier]["serial"], "gate_pass": True},
            projection={"_id": 0, "odds": 1, "grade_status": 1,
                        "stake_units": 1, "profit_units": 1},
        ).to_list(length=None)
        agg = {}
        for r in rows:
            b = _odds_bucket(r.get("odds"))
            e = agg.setdefault(b, {"n":0,"w":0,"l":0,"stake":0.0,"profit":0.0})
            e["n"] += 1
            st = r.get("grade_status")
            if st == "win": e["w"] += 1
            elif st == "loss": e["l"] += 1
            e["stake"] += float(r.get("stake_units") or 0)
            e["profit"] += float(r.get("profit_units") or 0)
        for b in ("plus_high","plus_med","plus_low","even","minus_low",
                   "minus_med","minus_heavy","minus_xx","_unknown"):
            if b not in agg: continue
            e = agg[b]
            dec = e["w"] + e["l"]
            hr = (100*e["w"]/dec) if dec else 0.0
            roi = (100*e["profit"]/e["stake"]) if e["stake"] else 0.0
            print(f"  {tier:<13}{b:>14}{e['n']:>6}{e['w']:>5}{e['l']:>5}"
                  f"{hr:>7.2f}%{roi:>+7.2f}%{e['profit']:>+10.4f}")

    # Per-tier × per-stat-family (qualified pool)
    print("\n──── QUALIFIED-POOL × STAT FAMILY (HR / ROI / P&L)")
    print(f"  {'tier':<13}{'family':>20}{'n':>6}{'W':>5}{'L':>5}"
          f"{'HR%':>8}{'ROI%':>8}{'P&L':>10}")
    family_summary = {}
    for tier in TIERS:
        rows = await db.mlb_production_replay_outputs.find(
            {"replay_serial": summaries[tier]["serial"], "gate_pass": True},
            projection={"_id": 0, "stat_family": 1, "grade_status": 1,
                        "stake_units": 1, "profit_units": 1},
        ).to_list(length=None)
        agg = {}
        for r in rows:
            f = (r.get("stat_family") or "_unknown")
            e = agg.setdefault(f, {"n":0,"w":0,"l":0,"stake":0.0,"profit":0.0})
            e["n"] += 1
            st = r.get("grade_status")
            if st == "win": e["w"] += 1
            elif st == "loss": e["l"] += 1
            e["stake"] += float(r.get("stake_units") or 0)
            e["profit"] += float(r.get("profit_units") or 0)
        family_summary[tier] = agg
        for f, e in sorted(agg.items(), key=lambda kv: -kv[1]["n"]):
            dec = e["w"] + e["l"]
            hr = (100*e["w"]/dec) if dec else 0.0
            roi = (100*e["profit"]/e["stake"]) if e["stake"] else 0.0
            print(f"  {tier:<13}{f:>20}{e['n']:>6}{e['w']:>5}{e['l']:>5}"
                  f"{hr:>7.2f}%{roi:>+7.2f}%{e['profit']:>+10.4f}")

    # Biggest winning / losing family per tier
    print("\n──── BIGGEST WINNING & LOSING STAT FAMILY (by P&L, qualified pool)")
    for tier in TIERS:
        agg = family_summary[tier]
        if not agg:
            print(f"  {tier:<13} : (no qualified rows)")
            continue
        sorted_pl = sorted(agg.items(), key=lambda kv: kv[1]["profit"])
        worst = sorted_pl[0]
        best = sorted_pl[-1]
        print(f"  {tier:<13}  best={best[0]:<20} P&L={best[1]['profit']:+.4f} "
              f"(n={best[1]['n']}, W/L={best[1]['w']}/{best[1]['l']})  |  "
              f"worst={worst[0]:<20} P&L={worst[1]['profit']:+.4f} "
              f"(n={worst[1]['n']}, W/L={worst[1]['w']}/{worst[1]['l']})")

    # Persist
    art = f"/app/backend/audits/phase4_3tier_{GAME_DATE}.json"
    light = {
        tier: {
            "summary": {k: v for k, v in summaries[tier].items()
                        if k != "layer3_summary"},
            "card_agg": {
                "n": len(card_agg[tier]["cards"]),
                "w": card_agg[tier]["w"], "l": card_agg[tier]["l"],
                "p": card_agg[tier]["p"], "u": card_agg[tier]["u"],
                "stake": card_agg[tier]["stake"],
                "profit": card_agg[tier]["profit"],
                "hr_pct": round(card_agg[tier]["hr"], 4),
                "roi_pct": round(card_agg[tier]["roi"], 4),
            },
            "family_summary": family_summary[tier],
        }
        for tier in TIERS
    }
    with open(art, "w") as fh:
        json.dump({"game_date": GAME_DATE, "snapshot_iso": SNAPSHOT,
                   "sport": SPORT, "tiers": light}, fh, indent=2, default=str)
    print(f"\n[json] wrote {art}")

    cli.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
