"""
Bovada/Sharp Lines Analysis Script
===================================
Analyzes all available lines from Bovada, DraftKings, and FanDuel
to help design interpolation/matching strategy.

Run: python -m scripts.analyze_bovada_lines
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


async def analyze_lines():
    """Fetch and analyze all available sharp lines."""
    
    # Connect to DB
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    odds_service = OddsApiService(db)
    
    print("=" * 80)
    print("SHARP LINES ANALYSIS")
    print("=" * 80)
    
    # Fetch events
    events = await odds_service.fetch_todays_events()
    print(f"\nFound {len(events)} NBA events")
    
    if not events:
        print("No events found. Exiting.")
        return
    
    # Data structures to collect all lines
    all_prizepicks_lines = []  # [(player, market, line, direction), ...]
    all_sharp_lines = defaultdict(list)  # {bookmaker: [(player, market, line, direction), ...]}
    
    # Fetch PrizePicks odds
    print("\nFetching PrizePicks lines...")
    for event in events[:5]:  # Limit to 5 events for analysis
        event_id = event.get("id")
        if not event_id:
            continue
        
        pp_data = await odds_service.fetch_prizepicks_odds(event_id, event)
        props = odds_service.extract_prizepicks_props(pp_data)
        
        for prop in props:
            player = prop.get("player_name", "")
            market = prop.get("market", "")
            line = prop.get("line", 0)
            direction = prop.get("direction", "over").lower()
            all_prizepicks_lines.append((player, market, line, direction))
    
    print(f"Total PrizePicks props: {len(all_prizepicks_lines)}")
    
    # Fetch Sharp Book odds
    print("\nFetching Sharp Book lines (Bovada, DraftKings, FanDuel)...")
    for event in events[:5]:
        event_id = event.get("id")
        if not event_id:
            continue
        
        sharp_data = await odds_service.fetch_sharp_book_odds(event_id, event)
        
        for bm in sharp_data.get("bookmakers", []):
            bm_key = bm.get("key", "")
            for market in bm.get("markets", []):
                market_key = market.get("key", "")
                for outcome in market.get("outcomes", []):
                    player = outcome.get("description", "")
                    line = outcome.get("point", 0)
                    direction = outcome.get("name", "over").lower()
                    price = outcome.get("price")
                    all_sharp_lines[bm_key].append({
                        "player": player,
                        "market": market_key,
                        "line": line,
                        "direction": direction,
                        "price": price
                    })
    
    # Print summary
    print("\n" + "=" * 80)
    print("SHARP BOOK COVERAGE")
    print("=" * 80)
    for bm, lines in all_sharp_lines.items():
        print(f"\n{bm.upper()}: {len(lines)} outcomes")
        
        # Count by market type
        by_market = defaultdict(int)
        for l in lines:
            by_market[l["market"]] += 1
        
        for market, count in sorted(by_market.items()):
            print(f"  - {market}: {count}")
    
    # Find unique players from PrizePicks
    pp_players = set(p[0] for p in all_prizepicks_lines)
    print(f"\n\nUnique PrizePicks players: {len(pp_players)}")
    
    # Check exact matches
    print("\n" + "=" * 80)
    print("EXACT LINE MATCH ANALYSIS")
    print("=" * 80)
    
    pp_keys = set(all_prizepicks_lines)
    
    for bm, lines in all_sharp_lines.items():
        sharp_keys = set((l["player"], l["market"], l["line"], l["direction"]) for l in lines)
        matches = pp_keys & sharp_keys
        
        match_pct = len(matches) / len(pp_keys) * 100 if pp_keys else 0
        print(f"\n{bm.upper()}:")
        print(f"  Exact matches: {len(matches)} / {len(pp_keys)} ({match_pct:.1f}%)")
    
    # Now do detailed player analysis for a sample
    print("\n" + "=" * 80)
    print("SAMPLE PLAYER LINE COMPARISON (First 5 unique players)")
    print("=" * 80)
    
    sample_players = list(pp_players)[:5]
    
    for player in sample_players:
        print(f"\n{'='*60}")
        print(f"PLAYER: {player}")
        print(f"{'='*60}")
        
        # Get PrizePicks lines for this player
        pp_player_lines = [(p[1], p[2], p[3]) for p in all_prizepicks_lines if p[0] == player]
        pp_player_lines = list(set(pp_player_lines))  # Dedupe
        
        print(f"\nPRIZEPICKS LINES:")
        for market, line, direction in sorted(pp_player_lines):
            stat_type = market.replace("player_", "").replace("_alternate", "").upper()
            print(f"  {stat_type}: {line} ({direction})")
        
        # Get sharp lines for this player from each book
        for bm, lines in all_sharp_lines.items():
            bm_player_lines = [l for l in lines if l["player"] == player]
            if bm_player_lines:
                print(f"\n{bm.upper()} LINES:")
                for l in sorted(bm_player_lines, key=lambda x: (x["market"], x["line"])):
                    stat_type = l["market"].replace("player_", "").replace("_alternate", "").upper()
                    print(f"  {stat_type}: {l['line']} ({l['direction']}) @ {l['price']}")
    
    # Find closest line analysis
    print("\n" + "=" * 80)
    print("CLOSEST LINE ANALYSIS (For Interpolation)")
    print("=" * 80)
    
    # Build lookup for sharp lines by (player, market_base, direction)
    bovada_lines_by_player = defaultdict(list)
    for l in all_sharp_lines.get("bovada", []):
        market_base = l["market"].replace("_alternate", "")
        key = (l["player"], market_base, l["direction"])
        bovada_lines_by_player[key].append((l["line"], l["price"]))
    
    # Check a few PrizePicks lines and find closest Bovada
    unmatched_count = 0
    close_match_count = 0
    interpolatable_count = 0
    
    for player, market, line, direction in list(pp_keys)[:100]:
        market_base = market.replace("_alternate", "")
        key = (player, market_base, direction)
        bovada_options = bovada_lines_by_player.get(key, [])
        
        if not bovada_options:
            unmatched_count += 1
            continue
        
        # Check for exact match
        exact = [b for b in bovada_options if b[0] == line]
        if exact:
            close_match_count += 1
            continue
        
        # Find closest lines
        lines_only = [b[0] for b in bovada_options]
        lower = [l for l in lines_only if l < line]
        higher = [l for l in lines_only if l > line]
        
        if lower and higher:
            # Can interpolate
            interpolatable_count += 1
        elif lower or higher:
            # Can extrapolate from closest
            close_match_count += 1
        else:
            unmatched_count += 1
    
    total_checked = min(100, len(pp_keys))
    print(f"\nSample of {total_checked} PrizePicks props:")
    print(f"  Exact Bovada match: {close_match_count} ({close_match_count/total_checked*100:.1f}%)")
    print(f"  Can interpolate: {interpolatable_count} ({interpolatable_count/total_checked*100:.1f}%)")
    print(f"  No Bovada data: {unmatched_count} ({unmatched_count/total_checked*100:.1f}%)")
    
    # Close connections
    await odds_service.close_client()
    client.close()
    
    print("\n" + "=" * 80)
    print("ANALYSIS COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(analyze_lines())
