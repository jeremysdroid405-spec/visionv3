"""
Complete Bovada Line Analysis Script
=====================================
Shows ALL Bovada lines and how they compare to PrizePicks lines.

Run: python -m scripts.full_bovada_analysis
"""
import asyncio
import os
import sys
from collections import defaultdict

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient
from services.odds_api_service import OddsApiService

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "betterbets")


async def analyze():
    """Full analysis of Bovada lines."""
    
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    odds_service = OddsApiService(db)
    
    # Fetch events
    events = await odds_service.fetch_todays_events()
    print(f"Found {len(events)} NBA events\n")
    
    # Storage for all data
    all_pp_lines = []  # PrizePicks lines
    all_bovada_lines = []  # Bovada lines
    
    # Fetch data for ALL events
    for event in events:
        event_id = event.get("id")
        if not event_id:
            continue
        
        # Get PrizePicks
        pp_data = await odds_service.fetch_prizepicks_odds(event_id, event)
        props = odds_service.extract_prizepicks_props(pp_data)
        for prop in props:
            all_pp_lines.append({
                "player": prop.get("player_name"),
                "market": prop.get("market"),
                "line": prop.get("line"),
                "direction": prop.get("direction", "over").lower()
            })
        
        # Get Sharp books (includes Bovada)
        sharp_data = await odds_service.fetch_sharp_book_odds(event_id, event)
        for bm in sharp_data.get("bookmakers", []):
            bm_key = bm.get("key")
            if bm_key != "bovada":
                continue
            for market in bm.get("markets", []):
                market_key = market.get("key")
                for outcome in market.get("outcomes", []):
                    all_bovada_lines.append({
                        "player": outcome.get("description"),
                        "market": market_key,
                        "line": outcome.get("point"),
                        "direction": outcome.get("name", "over").lower(),
                        "price": outcome.get("price")
                    })
    
    print("=" * 80)
    print("TOTALS")
    print("=" * 80)
    print(f"PrizePicks Lines: {len(all_pp_lines)}")
    print(f"Bovada Lines: {len(all_bovada_lines)}")
    
    # Build lookup sets
    pp_set = set((p["player"], p["market"], p["line"], p["direction"]) for p in all_pp_lines)
    bovada_set = set((b["player"], b["market"], b["line"], b["direction"]) for b in all_bovada_lines)
    
    # Exact matches
    exact_matches = pp_set & bovada_set
    print(f"\nExact Matches: {len(exact_matches)} / {len(pp_set)} ({len(exact_matches)/len(pp_set)*100:.1f}%)")
    
    # Build Bovada lookup by player+market+direction (ignoring line)
    bovada_by_pmd = defaultdict(list)
    for b in all_bovada_lines:
        # Normalize market key to base (remove _alternate)
        market_base = b["market"].replace("_alternate", "")
        key = (b["player"], market_base, b["direction"])
        bovada_by_pmd[key].append(b["line"])
    
    # Analyze matchability
    can_exact = 0
    can_interpolate = 0
    can_extrapolate = 0
    no_bovada = 0
    
    interpolation_examples = []
    
    for pp in all_pp_lines:
        # Check exact match first
        if (pp["player"], pp["market"], pp["line"], pp["direction"]) in bovada_set:
            can_exact += 1
            continue
        
        # Check if Bovada has ANY line for this player/market/direction
        market_base = pp["market"].replace("_alternate", "")
        key = (pp["player"], market_base, pp["direction"])
        bovada_lines = bovada_by_pmd.get(key, [])
        
        if not bovada_lines:
            no_bovada += 1
            continue
        
        pp_line = pp["line"]
        lower = [l for l in bovada_lines if l < pp_line]
        higher = [l for l in bovada_lines if l > pp_line]
        
        if lower and higher:
            can_interpolate += 1
            if len(interpolation_examples) < 5:
                closest_lower = max(lower)
                closest_higher = min(higher)
                interpolation_examples.append({
                    "player": pp["player"],
                    "market": pp["market"],
                    "pp_line": pp_line,
                    "bovada_lower": closest_lower,
                    "bovada_higher": closest_higher,
                    "direction": pp["direction"]
                })
        else:
            can_extrapolate += 1
    
    print(f"\n--- MATCHABILITY ANALYSIS ---")
    print(f"Exact Match: {can_exact} ({can_exact/len(all_pp_lines)*100:.1f}%)")
    print(f"Can Interpolate: {can_interpolate} ({can_interpolate/len(all_pp_lines)*100:.1f}%)")
    print(f"Can Extrapolate (closest): {can_extrapolate} ({can_extrapolate/len(all_pp_lines)*100:.1f}%)")
    print(f"No Bovada Data: {no_bovada} ({no_bovada/len(all_pp_lines)*100:.1f}%)")
    print(f"\n>>> TOTAL ACHIEVABLE with interpolation: {can_exact + can_interpolate + can_extrapolate} ({(can_exact + can_interpolate + can_extrapolate)/len(all_pp_lines)*100:.1f}%)")
    
    # Show interpolation examples
    if interpolation_examples:
        print("\n--- INTERPOLATION EXAMPLES ---")
        for ex in interpolation_examples:
            print(f"  {ex['player']} {ex['market']} {ex['direction']}")
            print(f"    PrizePicks: {ex['pp_line']}")
            print(f"    Bovada: {ex['bovada_lower']} <-- [{ex['pp_line']}] --> {ex['bovada_higher']}")
    
    # Show sample of unmatched
    print("\n--- SAMPLE: PLAYERS WITH NO BOVADA DATA ---")
    unmatched_players = set()
    for pp in all_pp_lines:
        market_base = pp["market"].replace("_alternate", "")
        key = (pp["player"], market_base, pp["direction"])
        if key not in bovada_by_pmd:
            unmatched_players.add(pp["player"])
    
    print(f"Unique players with NO Bovada data: {len(unmatched_players)}")
    for player in list(unmatched_players)[:10]:
        print(f"  - {player}")
    
    # Close
    await odds_service.close_client()
    client.close()


if __name__ == "__main__":
    asyncio.run(analyze())
