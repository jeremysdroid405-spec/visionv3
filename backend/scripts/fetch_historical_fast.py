"""
Fast Historical Game Logs Fetch
================================
Uses the /stats endpoint with season filtering for bulk fetch.
Much faster than per-player queries.
"""
import asyncio
import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient, UpdateOne
import httpx

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BDL_BASE_URL = "https://api.balldontlie.io/v1"
BDL_API_KEY = os.environ.get("BDL_API_KEY", "")
SEASONS = [2020, 2021, 2022, 2023, 2024]  # Skip 2025 (current season in hub)


async def fetch_season_stats(client: httpx.AsyncClient, season: int) -> list:
    """Fetch ALL stats for a season in one paginated stream."""
    all_stats = []
    cursor = None
    
    logger.info(f"Fetching season {season}...")
    
    while True:
        params = {"seasons[]": season, "per_page": 100}
        if cursor:
            params["cursor"] = cursor
        
        try:
            response = await client.get(f"{BDL_BASE_URL}/stats", params=params)
            response.raise_for_status()
            data = response.json()
            
            stats = data.get("data", [])
            all_stats.extend(stats)
            
            cursor = data.get("meta", {}).get("next_cursor")
            if not cursor:
                break
            
            if len(all_stats) % 5000 == 0:
                logger.info(f"  Season {season}: {len(all_stats):,} stats...")
            
            await asyncio.sleep(0.3)  # Rate limit
            
        except Exception as e:
            logger.error(f"Error at cursor {cursor}: {e}")
            await asyncio.sleep(2)
            continue
    
    return all_stats


def process_stat(stat: dict, season: int) -> dict:
    """Convert BDL stat to game log format."""
    game = stat.get("game", {})
    player = stat.get("player", {})
    
    return {
        "game_id": game.get("id"),
        "date": game.get("date"),
        "season": season,
        "player_id": player.get("id"),
        "player_name": f"{player.get('first_name', '')} {player.get('last_name', '')}".strip(),
        "team_id": stat.get("team", {}).get("id"),
        "min": stat.get("min"),
        "pts": stat.get("pts"),
        "reb": stat.get("reb"),
        "ast": stat.get("ast"),
        "stl": stat.get("stl"),
        "blk": stat.get("blk"),
        "turnover": stat.get("turnover"),
        "pf": stat.get("pf"),
        "fgm": stat.get("fgm"),
        "fga": stat.get("fga"),
        "fg_pct": stat.get("fg_pct"),
        "fg3m": stat.get("fg3m"),
        "fg3a": stat.get("fg3a"),
        "fg3_pct": stat.get("fg3_pct"),
        "ftm": stat.get("ftm"),
        "fta": stat.get("fta"),
        "ft_pct": stat.get("ft_pct"),
        "oreb": stat.get("oreb"),
        "dreb": stat.get("dreb"),
    }


async def main():
    logger.info("=" * 60)
    logger.info("FAST HISTORICAL GAME LOGS FETCH (2020-2024)")
    logger.info("=" * 60)
    
    if not BDL_API_KEY:
        logger.error("BDL_API_KEY not set!")
        return
    
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    
    client = MongoClient(mongo_url)
    db = client[db_name]
    collection = db["bdl_historical_game_logs"]
    
    # Create indexes
    collection.create_index([("player_id", 1), ("season", 1)])
    collection.create_index([("game_id", 1), ("player_id", 1)], unique=True)
    collection.create_index([("date", -1)])
    
    total_fetched = 0
    
    async with httpx.AsyncClient(
        timeout=60.0,
        headers={"Authorization": BDL_API_KEY}
    ) as http_client:
        
        for season in SEASONS:
            # Check existing count
            existing = collection.count_documents({"season": season})
            logger.info(f"\nSeason {season}: {existing:,} existing records")
            
            if existing > 20000:
                logger.info(f"  Skipping - already have enough data")
                continue
            
            stats = await fetch_season_stats(http_client, season)
            logger.info(f"  Fetched {len(stats):,} raw stats")
            
            if stats:
                game_logs = [process_stat(s, season) for s in stats if s.get("player")]
                
                # Bulk upsert
                if game_logs:
                    ops = [
                        UpdateOne(
                            {"game_id": gl["game_id"], "player_id": gl["player_id"]},
                            {"$set": gl},
                            upsert=True
                        )
                        for gl in game_logs
                    ]
                    
                    # Process in batches of 5000
                    for i in range(0, len(ops), 5000):
                        batch = ops[i:i+5000]
                        result = collection.bulk_write(batch, ordered=False)
                        logger.info(f"  Stored batch {i//5000 + 1}: {result.upserted_count + result.modified_count} records")
                    
                    total_fetched += len(game_logs)
            
            logger.info(f"Season {season} complete: {collection.count_documents({'season': season}):,} total records")
    
    # Final summary
    logger.info("\n" + "=" * 60)
    logger.info("FETCH COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Total records fetched this run: {total_fetched:,}")
    logger.info(f"Total in collection: {collection.count_documents({}):,}")
    
    for season in SEASONS:
        count = collection.count_documents({"season": season})
        logger.info(f"  Season {season}: {count:,}")


if __name__ == "__main__":
    asyncio.run(main())
