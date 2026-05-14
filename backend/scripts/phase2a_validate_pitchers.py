"""Phase 2A validation report (2026-05-15).

Runs feature_hydration against the live `mlb_live_props` collection
for a handful of high-profile names from the audit and prints a
BEFORE / AFTER comparison of the four pitcher-context fields plus the
two matchup flags. Strictly observational — no model retraining, no
score writes.

Usage::

    cd /app/backend && python scripts/phase2a_validate_pitchers.py

Names checked (per handoff):
    • Andy Pages
    • Kyle Tucker
    • Freddie Freeman
    • Ozzie Albies
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from copy import deepcopy
from typing import Any, Dict, List

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from services.feature_hydration import hydrate_game_context_on_props  # noqa: E402
from services.scoring.adapters.mlb_scoring import _propagate_phase1_context  # noqa: E402


WATCH_NAMES = ("Andy Pages", "Kyle Tucker", "Freddie Freeman", "Ozzie Albies")
PITCHER_FIELDS = (
    "probable_pitcher", "opp_pitcher_id", "opp_pitcher_name",
    "opp_pitcher_throws", "opp_pitcher_era", "opp_pitcher_whip",
    "opp_pitcher_k9",
)


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Load one prop per name (any stat — we only need the contextual
    # join keys: home_team / away_team / commence_time / bdl_player_id).
    name_to_prop: Dict[str, Dict[str, Any]] = {}
    cursor = db.mlb_live_props.find(
        {"player_name": {"$in": list(WATCH_NAMES)}},
        {"_id": 0},
    ).limit(500)
    async for p in cursor:
        name = p.get("player_name")
        if name in WATCH_NAMES and name not in name_to_prop:
            name_to_prop[name] = p
        if len(name_to_prop) == len(WATCH_NAMES):
            break

    missing = [n for n in WATCH_NAMES if n not in name_to_prop]
    if missing:
        print(f"[WARN] No live_props rows found for: {missing}")
        print("        Validation will only cover the rows that exist.")

    if not name_to_prop:
        print("[FAIL] No matching rows found in mlb_live_props.")
        client.close()
        return

    target_props: List[Dict[str, Any]] = list(name_to_prop.values())
    # Capture BEFORE snapshot (deep-copy so hydration can't mutate it).
    before = {p["player_name"]: deepcopy(p) for p in target_props}

    # Re-run hydration in-place. Only on the watch-list rows — keeps
    # the script lightweight and avoids touching the full ingest.
    report = await hydrate_game_context_on_props(
        db, "mlb", target_props,
    )

    # Derive matchup flags (the MLB adapter normally does this during
    # `build_context`; we invoke it here only for batter_hand × pitcher
    # propagation — no scoring runs).
    for p in target_props:
        _propagate_phase1_context(
            p, master_hub=None, bdl_player_id=p.get("bdl_player_id"),
        )

    print("=" * 88)
    print(f"Phase 2A validation — props hydrated: {report['props']}")
    print(f"  probable_pitcher_filled: {report.get('probable_pitcher_filled', 0)}")
    print("=" * 88)
    for p in target_props:
        name = p.get("player_name")
        b = before[name]
        print(f"\n── {name}  (event_id={p.get('event_id')})")
        print(f"   home={p.get('home_team')}  away={p.get('away_team')}")
        print(f"   is_home_team: {b.get('is_home_team')!r:>6}  →  {p.get('is_home_team')!r}")
        print(f"   batter_hand:  {b.get('batter_hand')!r:>6}  →  {p.get('batter_hand')!r}")
        for f in PITCHER_FIELDS:
            before_v = b.get(f)
            after_v = p.get(f)
            mark = "✓" if (after_v not in (None, "") and before_v != after_v) else " "
            print(f"   [{mark}] {f:<22}  {before_v!r:>20}  →  {after_v!r}")
        print(f"       same_hand_matchup:     {p.get('same_hand_matchup')!r}")
        print(f"       opposite_hand_matchup: {p.get('opposite_hand_matchup')!r}")

    print("\n" + "=" * 88)
    print("Hydration report:")
    print(json.dumps({
        k: v for k, v in report.items() if k != "imputed_field_summary"
    }, indent=2, default=str))
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
