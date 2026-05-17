"""Full pick-by-pick audit for one date × all three tiers.

Prints every qualified bet: bet identity, stake, projection, outcome.
Stake is fixed at 1 unit (flat). Profit = American-odds payout on win,
−1u on loss, 0 on push, 0 on ungraded (no game-log entry).
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


def _american_payout(odds: int) -> float:
    return (odds / 100.0) if odds > 0 else (100.0 / -odds)


async def amain(args):
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    tier_versions = {
        "safe_haven":  "mlb_sh_v1_2026_05_16",
        "front_lines": "mlb_fl_v1_2026_05_16",
        "war_zone":    "mlb_war_zone_v1_2026_05_16",
    }

    for tier, version in tier_versions.items():
        cursor = db.mlb_replay_gate_results.find(
            {"game_date": args.date,
             "snapshot_iso": f"{args.date}T11:00:00Z",
             "gate_config_version": version,
             "gate_pass": True},
            {"_id": 0, "player_name": 1, "stat_family": 1, "production_family": 1,
             "market": 1, "line": 1, "side": 1, "book": 1, "odds": 1,
             "is_alternate": 1, "projection_mu": 1, "model_probability": 1,
             "edge": 1, "cv": 1,
             "hit_rate_l5": 1, "hit_rate_l10": 1, "hit_rate_l20": 1,
             "grade_status": 1, "actual": 1,
             "profit_units": 1, "stake_units": 1,
             "team": 1, "opponent": 1},
        ).sort([("hit_rate_l10", -1), ("hit_rate_l20", -1),
                ("hit_rate_l5", -1), ("edge", -1)])
        picks = await cursor.to_list(None)
        n = len(picks)
        if n == 0:
            print(f"\n━━━ TIER: {tier.upper()} ({version}) — NO QUALIFIED PICKS ━━━")
            continue
        wins = sum(1 for p in picks if p["grade_status"] == "win")
        losses = sum(1 for p in picks if p["grade_status"] == "loss")
        pushes = sum(1 for p in picks if p["grade_status"] == "push")
        ungraded = sum(1 for p in picks if p["grade_status"] == "ungraded")
        profit = sum(p.get("profit_units") or 0 for p in picks)
        stake  = sum(p.get("stake_units")  or 0 for p in picks)
        graded_dec = wins + losses
        hr = (wins / graded_dec * 100.0) if graded_dec else None
        roi = (profit / stake * 100.0) if stake else None
        print(f"\n━━━━━━━━━━━━━━━━━━━━ TIER: {tier.upper()} "
              f"({version}) ━━━━━━━━━━━━━━━━━━━━")
        print(f"  date={args.date}  snapshot={args.date}T11:00:00Z  "
              f"qualified={n}  W/L/P/U={wins}/{losses}/{pushes}/{ungraded}  "
              f"HR={hr:.1f}%  ROI={(roi or 0):+.2f}%  "
              f"profit={profit:+.3f}u  stake={stake:.2f}u")
        print(f"  Stake policy: 1.00 unit flat per qualified pick.")
        print(f"")
        print(f"  {'#':>4}  {'Player':<22}  {'Stat':<14}  {'Mkt':<10}  "
              f"{'Line':>5}  {'Side':<5}  {'Book':<14}  {'Odds':>6}  "
              f"{'Stake':>5}  {'μ':>6}  {'Actual':>7}  {'Edge':>7}  "
              f"{'CV':>6}  {'L5/L10/L20':>11}  {'TP%':>5}  "
              f"{'Outcome':<8}  {'+u':>7}")
        for i, p in enumerate(picks, 1):
            actual = p.get("actual")
            outcome = p["grade_status"]
            actual_str = f"{actual:g}" if actual is not None else "—"
            mkt = "alt" if p.get("is_alternate") else "std"
            hrs = (f"{int(p.get('hit_rate_l5') or 0):>2}/"
                   f"{int(p.get('hit_rate_l10') or 0):>2}/"
                   f"{int(p.get('hit_rate_l20') or 0):>2}")
            tp_pct = (p.get("model_probability") or 0) * 100
            stake = p.get("stake_units") or 0
            profit = p.get("profit_units") or 0
            print(f"  {i:>4}  "
                  f"{(p.get('player_name') or '?')[:22]:<22}  "
                  f"{(p.get('production_family') or p.get('stat_family') or '?')[:14]:<14}  "
                  f"{mkt:<10}  "
                  f"{p['line']:>5.1f}  "
                  f"{p['side']:<5}  "
                  f"{p['book'][:14]:<14}  "
                  f"{int(p['odds']):>+6d}  "
                  f"{stake:>5.2f}  "
                  f"{p.get('projection_mu') or 0:>6.2f}  "
                  f"{actual_str:>7}  "
                  f"{(p.get('edge') or 0)*100:>+6.2f}%  "
                  f"{p.get('cv') or 0:>6.3f}  "
                  f"{hrs:>11}  "
                  f"{tp_pct:>5.1f}  "
                  f"{outcome:<8}  "
                  f"{profit:>+7.3f}")
    cli.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", default="2026-05-01")
    asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    main()
