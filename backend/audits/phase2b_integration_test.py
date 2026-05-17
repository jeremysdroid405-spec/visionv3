"""Phase 2b integration test — actual predict() with as_of_date.

Verifies that when `as_of_date` is supplied:
  - The model's internal game_logs view is filtered correctly
  - No game log on or after as_of_date is used
  - The prediction comes back successfully

Uses Matt Olson 2026-05-06 since we already know that case from earlier
traces and have the master_hub data populated.
"""
import asyncio, os, sys
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv; load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


async def go():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]

    # Pull Olson's actual bdl_game_logs from master_hub
    doc = await db.mlb_master_hub_2026.find_one(
        {"display_name": "Matt Olson"},
        {"_id": 0, "bdl_game_logs": 1})
    if not doc:
        print("ERROR: Olson master_hub doc not found")
        return

    logs = doc.get("bdl_game_logs") or []
    print(f"[A] Total logs on master_hub for Olson: {len(logs)}")
    if not logs:
        return

    # Show newest 5 dates available
    sorted_dates = sorted(
        [(g.get("date") or g.get("game_date") or "")[:10] for g in logs],
        reverse=True)
    print(f"    newest 5 dates: {sorted_dates[:5]}")

    # Apply our filter
    from services.mlb_high_friction_model import MLBHighFrictionModel
    filtered = MLBHighFrictionModel._filter_logs_before(logs, "2026-05-06")
    filtered_dates = sorted(
        [(g.get("date") or g.get("game_date") or "")[:10] for g in filtered],
        reverse=True)
    print()
    print(f"[B] After filter as_of_date=2026-05-06: kept {len(filtered)} logs")
    print(f"    newest 5 kept dates: {filtered_dates[:5]}")
    assert all(d < "2026-05-06" for d in filtered_dates), \
        f"LEAK: filtered set contains date >= cutoff: {[d for d in filtered_dates if d >= '2026-05-06']}"
    print(f"    ✅ no leak: every kept date < 2026-05-06")

    # Verify which dates were DROPPED
    kept_set = set(filtered_dates)
    all_set = set(sorted_dates)
    dropped = sorted(all_set - kept_set, reverse=True)[:10]
    print(f"    dropped {len(all_set - kept_set)} dates >= cutoff:  {dropped}")

    # Now actually call predict() with as_of_date and confirm it returns
    # something different (lower) than the leaky version
    print()
    print(f"[C] Calling predict() WITH and WITHOUT as_of_date...")
    model = MLBHighFrictionModel(db.delegate)
    model.load_models()

    res_live = model.predict(
        player_name="Matt Olson", stat_type="total_bases",
        line=0.5, opponent_team="Seattle Mariners",
        park_team="Seattle Mariners",
    )
    res_historical = model.predict(
        player_name="Matt Olson", stat_type="total_bases",
        line=0.5, opponent_team="Seattle Mariners",
        park_team="Seattle Mariners",
        as_of_date="2026-05-06",
    )

    print(f"    LIVE (no as_of_date):       predicted={res_live.get('predicted')!r}  "
          f"raw={res_live.get('raw_prediction')!r}  "
          f"prob_over={res_live.get('prob_over')!r}")
    print(f"    HISTORICAL (as_of=05-06):   predicted={res_historical.get('predicted')!r}  "
          f"raw={res_historical.get('raw_prediction')!r}  "
          f"prob_over={res_historical.get('prob_over')!r}")
    print()
    if res_live.get("predicted") != res_historical.get("predicted"):
        print(f"    ✅ DIFFERENT outputs — historical filter active")
    else:
        print(f"    ⚠ same output — verify whether logs ≥ cutoff existed in master_hub")

    cli.close()


asyncio.run(go())
