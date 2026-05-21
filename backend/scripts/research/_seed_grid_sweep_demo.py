"""Seed a small synthetic outcomes+enriched dataset on the LOCAL preview pod
so the grid_sweep pipeline can be exercised end-to-end without prod data.

Inserts ~600 outcome+enriched pairs spanning 5 stat_families, 2 sides,
30 days, with a deliberately tuned edge-vs-outcome relationship so the
sweep produces meaningful 'recommended' cells.

Safe to re-run: deletes prior `_seed=grid_sweep_demo` rows first.
"""
from __future__ import annotations
import asyncio
import os
import random
from datetime import date, timedelta

from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


SEED_TAG = "grid_sweep_demo"
LEAGUE = "MLB"
START  = date(2025, 6, 1)
DAYS   = 30
FAMILIES = ["hits", "total_bases", "batter_strikeouts", "pitcher_strikeouts",
            "hits_runs_rbis", "fantasy_score"]
SIDES = ["OVER", "UNDER"]


async def main():
    rng = random.Random(42)
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    await db.sgo_pp_research_outcomes.delete_many({"_seed": SEED_TAG})
    await db.sgo_pp_research_core_enriched.delete_many({"_seed": SEED_TAG})

    out_rows = []
    enr_rows = []
    n = 0
    for d in range(DAYS):
        gd = (START + timedelta(days=d)).isoformat()
        # ~20 bets a day
        for k in range(20):
            n += 1
            fam = rng.choices(FAMILIES,
                               weights=[3, 3, 2, 2, 2, 1])[0]
            side = rng.choice(SIDES)
            event_id = f"E{d}-{k // 5}"
            player_id = f"P{n % 130}"
            stat_id   = f"S-{fam}"
            line = round(rng.uniform(0.5, 5.5), 1)
            period = "fullgame"

            # Engineer the relationship: higher edge + ≥2 sharp books +
            # tight market_width → real positive expectancy.
            edge = round(rng.gauss(0.04, 0.06), 4)   # centered just over 0
            devig = rng.choice([1, 1, 2, 2, 3, 3, 5, 7])
            sharp = rng.choice([0, 0, 1, 1, 2, 2, 3])
            mw    = round(abs(rng.gauss(0.07, 0.05)), 3)
            cd    = round(abs(rng.gauss(0.06, 0.04)), 3)
            pp_imp = round(rng.uniform(0.42, 0.62), 4)

            # win prob = pp_imp + edge boost only when sharp≥2 AND mw<0.10
            # Calibrate so qualified cells push hit-rate well above pp_imp.
            win_prob = pp_imp + (0.18 if (sharp >= 2 and mw <= 0.10 and edge >= 0.05) else 0.0)
            win_prob = max(0.05, min(0.95, win_prob))
            won = 1 if rng.random() < win_prob else 0

            key = dict(event_id=event_id, player_id=player_id, stat_id=stat_id,
                         side=side, line=line, period_id=period,
                         game_date=gd, league_id=LEAGUE, stat_family=fam)

            out_rows.append({**key, "_seed": SEED_TAG,
                              "outcome_resolved": True,
                              "outcome_numeric": won,
                              "outcome": "WIN" if won else "LOSS",
                              "hit": bool(won),
                              "actual": line + (0.5 if (side == "OVER" and won) else -0.5)})
            enr_rows.append({**key, "_seed": SEED_TAG,
                              "best_book_edge": edge,
                              "edge_vs_consensus": edge * 0.8,
                              "best_book_probability": pp_imp + max(0.0, edge),
                              "pp_implied_probability": pp_imp,
                              "devig_book_count": devig,
                              "sharp_book_count": sharp,
                              "market_width": mw,
                              "consensus_disagreement": cd})

    if out_rows:
        await db.sgo_pp_research_outcomes.insert_many(out_rows, ordered=False)
        await db.sgo_pp_research_core_enriched.insert_many(enr_rows, ordered=False)
    print(f"seeded {len(out_rows)} outcome rows + matched enriched rows")


if __name__ == "__main__":
    asyncio.run(main())
