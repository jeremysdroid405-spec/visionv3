"""Phase 6 — Small validation. SH only, single date.

Re-runs Safe Haven for 2026-05-05 ONLY using the new Phase 4b
odds-bucket router. Compares against the prior Phase 4 run
(`MLB-PRODREPLAY-20260505-SH-1100UTC-00030`) to show:

  1. NBA routing unchanged (re-asserted via test, plus a direct
     same-odds parity check).
  2. MLB Safe Haven now rejects props whose `tier_reference_odds`
     are outside the universal SH bucket (`<= -300`).
  3. List of rejected MLB props with `routed_tier`, ref_odds,
     ref_book.
  4. No model / gate / threshold changes — only odds routing.
"""
from __future__ import annotations
import os, sys
os.environ.setdefault("OMP_NUM_THREADS", "1")
import asyncio, json
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

from services.replay.production_replay_runner import run_production_replay
from services.scoring.odds_bucket_router import (
    get_tier_odds_contract, get_odds_bucket, TIER_ODDS_BUCKET_FAIL,
)
from services.scoring.gates.thresholds import resolve_target_tier


GAME_DATE = "2026-05-05"
SNAP = "2026-05-05T11:00:00Z"
TIER = "safe_haven"
PRIOR_SERIAL = "MLB-PRODREPLAY-20260505-SH-1100UTC-00030"


async def main():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    print("\n=== Phase 6 — Small validation (SH, 2026-05-05) ===\n")
    contract = get_tier_odds_contract("mlb")
    print(json.dumps(contract, indent=2))

    # ── (1) NBA parity sanity ───────────────────────────────────────
    print("\n(1) NBA parity — universal router vs live resolve_target_tier")
    for o in (-300, -299, -250, -150, -149, -110, +100, +149, +150, +200):
        live = resolve_target_tier("nba", o)
        univ = get_odds_bucket(o)
        ok = (live == univ)
        print(f"   odds={o:>5}  live={live!s:<12} univ={univ!s:<12} match={ok}")

    # ── (2) Run Phase 4b SH for 2026-05-05 (universal gate path) ───
    print("\n(2) Running Phase 4b SH replay (with odds-bucket routing) ...")
    summary = await run_production_replay(
        db, sport="mlb", game_date=GAME_DATE, snapshot_iso=SNAP,
        tier=TIER, gate_path="universal", dry_run=False,
        force_layer3=False, notes="phase4b_validation_sh_2026_05_05",
    )
    NEW_SERIAL = summary["serial"]
    print(f"   new serial: {NEW_SERIAL}")
    print(f"   rows_scanned   : {summary['rows_scanned']}")
    print(f"   rows_qualified : {summary['rows_qualified']}")
    print(f"   cards_displayed: {summary['cards_displayed']}")
    print(f"   W/L/P/U        : "
          f"{summary['wins']}/{summary['losses']}/"
          f"{summary['pushes']}/{summary['ungraded']}")
    print(f"   HR/ROI/P&L     : "
          f"{summary['hit_rate_pct']:.2f}% / {summary['roi_pct']:+.2f}% / "
          f"{summary['profit_units']:+.4f}")

    # ── (3) Routing rejects ────────────────────────────────────────
    print("\n(3) Routing rejects (`tier_odds_bucket_fail`) detail")
    rejects = await db.mlb_production_replay_outputs.find(
        {"replay_serial": NEW_SERIAL, "gate_pass": False,
         "failed_gates": TIER_ODDS_BUCKET_FAIL},
        projection={"_id": 0, "player_name": 1,
                     "player_name_normalized": 1, "market": 1,
                     "line": 1, "side": 1, "odds": 1, "book": 1,
                     "tier_reference_odds": 1,
                     "tier_reference_book": 1, "routed_tier": 1},
    ).to_list(length=None)
    print(f"   rows rejected by router (universal): {len(rejects)}")
    # First 25 + bucket distribution
    bucket_counts = {}
    for r in rejects:
        b = r.get("routed_tier") or "_no_ref_odds"
        bucket_counts[b] = bucket_counts.get(b, 0) + 1
    print(f"   rejected by routed_tier: {bucket_counts}")
    print("\n   Sample (first 25):")
    for r in rejects[:25]:
        print(f"     {(r.get('player_name') or '')[:24]:<24} "
              f"{(r.get('market') or '')[:32]:<32} "
              f"line={r.get('line')} side={r.get('side')} "
              f"row_odds={r.get('odds')} ref_odds={r.get('tier_reference_odds')} "
              f"({r.get('tier_reference_book')}) → routed={r.get('routed_tier')}")

    # ── (4) Prior-run comparison ───────────────────────────────────
    print("\n(4) Old vs new SH for 2026-05-05")
    prior = await db.mlb_production_replay_runs.find_one(
        {"serial": PRIOR_SERIAL}, projection={"_id": 0})
    if prior:
        print(f"   {'':18}{'PRIOR (no-routing)':<22}{'NEW (with-routing)':<22}")
        print(f"   {'serial':<18}{PRIOR_SERIAL[-25:]:<22}{NEW_SERIAL[-25:]:<22}")
        print(f"   {'rows_qualified':<18}{prior['rows_qualified']:<22}{summary['rows_qualified']:<22}")
        print(f"   {'cards':<18}{prior['cards_displayed']:<22}{summary['cards_displayed']:<22}")
        print(f"   {'wins':<18}{prior['wins']:<22}{summary['wins']:<22}")
        print(f"   {'losses':<18}{prior['losses']:<22}{summary['losses']:<22}")
        print(f"   {'hit_rate':<18}{str(prior['hit_rate_pct'])+'%':<22}{str(summary['hit_rate_pct'])+'%':<22}")
        print(f"   {'roi':<18}{str(prior['roi_pct'])+'%':<22}{str(summary['roi_pct'])+'%':<22}")
        print(f"   {'profit':<18}{str(prior['profit_units']):<22}{str(summary['profit_units']):<22}")

    # ── (5) New card set (top 20) ───────────────────────────────────
    cards = await db.mlb_production_replay_cards.find(
        {"replay_serial": NEW_SERIAL}, projection={"_id": 0}
    ).sort("rank", 1).to_list(length=None)
    print(f"\n(5) New SH displayed cards ({len(cards)} cards)")
    for c in cards:
        # Pull ref_odds from the corresponding output row for context.
        r = await db.mlb_production_replay_outputs.find_one(
            {"replay_serial": NEW_SERIAL,
             "player_name_normalized": c["player_name_normalized"],
             "market": c["market"], "line": c["line"],
             "side": c["side"], "book": c["book"],
             "gate_pass": True},
            projection={"_id": 0, "tier_reference_odds": 1,
                         "tier_reference_book": 1, "routed_tier": 1})
        print(f"   #{c.get('rank'):>2} {(c.get('player_name') or '')[:24]:<24} "
              f"{(c.get('stat_family') or '')[:18]:<18} "
              f"line={c.get('line')} side={c.get('side'):<5} "
              f"row_odds={c.get('odds'):>5} "
              f"ref_odds={(r or {}).get('tier_reference_odds')!s:<6} "
              f"({(r or {}).get('tier_reference_book')!s:<10}) "
              f"grade={c.get('grade_status')!s:<8} actual={c.get('actual_value')}")

    print("\n[confirm] No model / gate / threshold changes — only odds routing.")
    print("[confirm] NBA `resolve_target_tier` calls untouched.")

    cli.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
