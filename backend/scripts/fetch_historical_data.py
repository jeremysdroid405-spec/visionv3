"""
Fetch Historical BDL Data (2020-2025)
=====================================
Pulls game logs and advanced stats from BDL for seasons 2020-2025
to expand the training dataset for Vegas Killer model.

This is a one-time historical data pull.
"""
import asyncio
import os
import sys
import logging
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient
import httpx

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BDL_BASE_URL = "https://api.balldontlie.io/v1"
BDL_API_KEY = os.environ.get("BDL_API_KEY", "")
SEASONS = [2020, 2021, 2022, 2023, 2024, 2025]

# Rate limiting
RATE_LIMIT_DELAY = 0.5  # 500ms between requests
BATCH_SIZE = 25  # Players per batch


async def fetch_all_players(client: httpx.AsyncClient) -> list:
    """Fetch all NBA players from BDL."""
    all_players = []
    cursor = None
    
    logger.info("Fetching all players from BDL...")
    
    while True:
        params = {"per_page": 100}
        if cursor:
            params["cursor"] = cursor
            
        response = await client.get(f"{BDL_BASE_URL}/players", params=params)
        response.raise_for_status()
        data = response.json()
        
        players = data.get("data", [])
        all_players.extend(players)
        
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break
            
        await asyncio.sleep(RATE_LIMIT_DELAY)
    
    logger.info(f"Found {len(all_players)} total players")
    return all_players


async def fetch_stats_for_season(
    client: httpx.AsyncClient,
    player_ids: list,
    season: int
) -> list:
    """Fetch all stats for given players in a season."""
    all_stats = []
    
    # Process in batches
    for i in range(0, len(player_ids), BATCH_SIZE):
        batch = player_ids[i:i+BATCH_SIZE]
        cursor = None
        batch_stats = []
        
        while True:
            params = {
                "seasons[]": season,
                "per_page": 100
            }
            for pid in batch:
                params[f"player_ids[]"] = pid
            if cursor:
                params["cursor"] = cursor
            
            try:
                response = await client.get(f"{BDL_BASE_URL}/stats", params=params)
                response.raise_for_status()
                data = response.json()
                
                stats = data.get("data", [])
                batch_stats.extend(stats)
                
                cursor = data.get("meta", {}).get("next_cursor")
                if not cursor:
                    break
                    
                await asyncio.sleep(RATE_LIMIT_DELAY)
            except Exception as e:
                logger.warning(f"Error fetching batch for season {season}: {e}")
                break
        
        all_stats.extend(batch_stats)
        
        if (i // BATCH_SIZE) % 10 == 0:
            logger.info(f"  Season {season}: Processed {i + len(batch)}/{len(player_ids)} players, {len(all_stats)} stats so far")
        
        await asyncio.sleep(RATE_LIMIT_DELAY)
    
    return all_stats


async def fetch_advanced_stats_for_season(
    client: httpx.AsyncClient,
    season: int
) -> list:
    """Fetch advanced stats for a season."""
    all_stats = []
    cursor = None
    
    while True:
        params = {
            "seasons[]": season,
            "per_page": 100
        }
        if cursor:
            params["cursor"] = cursor
        
        try:
            response = await client.get(f"{BDL_BASE_URL}/stats/advanced", params=params)
            response.raise_for_status()
            data = response.json()
            
            stats = data.get("data", [])
            all_stats.extend(stats)
            
            cursor = data.get("meta", {}).get("next_cursor")
            if not cursor:
                break
                
            await asyncio.sleep(RATE_LIMIT_DELAY)
            
            if len(all_stats) % 1000 == 0:
                logger.info(f"  Season {season} advanced: {len(all_stats)} stats fetched...")
                
        except Exception as e:
            logger.warning(f"Error fetching advanced stats for season {season}: {e}")
            break
    
    return all_stats


def process_game_log(stat: dict, season: int) -> dict:
    """Convert BDL stat to game log format."""
    game = stat.get("game", {})
    player = stat.get("player", {})
    
    return {
        "game_id": game.get("id"),
        "date": game.get("date"),
        "season": season,
        "home_team_id": game.get("home_team_id"),
        "visitor_team_id": game.get("visitor_team_id"),
        "home_team_score": game.get("home_team_score"),
        "visitor_team_score": game.get("visitor_team_score"),
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


def process_advanced_stat(stat: dict, season: int) -> dict:
    """Convert BDL advanced stat to storage format."""
    game = stat.get("game", {})
    player = stat.get("player", {})
    
    return {
        "game_id": game.get("id"),
        "game_date": game.get("date"),
        "season": season,
        "player_id": player.get("id"),
        "player_name": f"{player.get('first_name', '')} {player.get('last_name', '')}".strip(),
        "team_id": stat.get("team", {}).get("id"),
        # Advanced stats
        "pie": stat.get("pie"),
        "pace": stat.get("pace"),
        "assist_percentage": stat.get("assist_percentage"),
        "assist_ratio": stat.get("assist_ratio"),
        "assist_to_turnover": stat.get("assist_to_turnover"),
        "defensive_rating": stat.get("defensive_rating"),
        "defensive_rebound_percentage": stat.get("defensive_rebound_percentage"),
        "effective_field_goal_percentage": stat.get("effective_field_goal_percentage"),
        "net_rating": stat.get("net_rating"),
        "offensive_rating": stat.get("offensive_rating"),
        "offensive_rebound_percentage": stat.get("offensive_rebound_percentage"),
        "rebound_percentage": stat.get("rebound_percentage"),
        "true_shooting_percentage": stat.get("true_shooting_percentage"),
        "turnover_ratio": stat.get("turnover_ratio"),
        "usage_percentage": stat.get("usage_percentage"),
    }


async def main():
    """Main function to fetch historical data."""
    logger.info("=" * 70)
    logger.info("FETCHING HISTORICAL BDL DATA (2020-2025)")
    logger.info("=" * 70)
    
    if not BDL_API_KEY:
        logger.error("BDL_API_KEY not set!")
        return
    
    # Connect to MongoDB
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "pick_vision")
    
    client = MongoClient(mongo_url)
    db = client[db_name]
    
    # Collections
    game_logs_collection = db["bdl_historical_game_logs"]
    advanced_stats_collection = db["bdl_advanced_stats"]
    
    # Create indexes
    game_logs_collection.create_index([("player_id", 1), ("season", 1)])
    game_logs_collection.create_index([("game_id", 1), ("player_id", 1)], unique=True)
    game_logs_collection.create_index([("date", -1)])
    
    advanced_stats_collection.create_index([("player_id", 1), ("season", 1)])
    advanced_stats_collection.create_index([("game_id", 1), ("player_id", 1)], unique=True)
    
    async with httpx.AsyncClient(
        timeout=60.0,
        headers={"Authorization": BDL_API_KEY}
    ) as http_client:
        
        # Get all players
        players = await fetch_all_players(http_client)
        player_ids = [p["id"] for p in players if p.get("id")]
        
        logger.info(f"\nWill fetch data for {len(player_ids)} players across {len(SEASONS)} seasons")
        
        total_game_logs = 0
        total_advanced = 0
        
        for season in SEASONS:
            logger.info(f"\n{'='*50}")
            logger.info(f"SEASON {season}-{season+1}")
            logger.info(f"{'='*50}")
            
            # Fetch game logs
            logger.info(f"Fetching game logs for season {season}...")
            stats = await fetch_stats_for_season(http_client, player_ids, season)
            
            if stats:
                # Process and store
                game_logs = [process_game_log(s, season) for s in stats]
                
                # Bulk upsert
                from pymongo import UpdateOne
                if game_logs:
                    ops = [
                        UpdateOne(
                            {"game_id": gl["game_id"], "player_id": gl["player_id"]},
                            {"$set": gl},
                            upsert=True
                        )
                        for gl in game_logs
                    ]
                    result = game_logs_collection.bulk_write(ops, ordered=False)
                    logger.info(f"  Stored {result.upserted_count + result.modified_count} game logs for season {season}")
                    total_game_logs += len(game_logs)
            
            # Fetch advanced stats
            logger.info(f"Fetching advanced stats for season {season}...")
            advanced = await fetch_advanced_stats_for_season(http_client, season)
            
            if advanced:
                # Process and store
                advanced_stats = [process_advanced_stat(s, season) for s in advanced]
                
                if advanced_stats:
                    ops = [
                        UpdateOne(
                            {"game_id": a["game_id"], "player_id": a["player_id"]},
                            {"$set": a},
                            upsert=True
                        )
                        for a in advanced_stats
                    ]
                    result = advanced_stats_collection.bulk_write(ops, ordered=False)
                    logger.info(f"  Stored {result.upserted_count + result.modified_count} advanced stats for season {season}")
                    total_advanced += len(advanced_stats)
            
            logger.info(f"Season {season} complete!")
    
    # Summary
    logger.info("\n" + "=" * 70)
    logger.info("HISTORICAL DATA FETCH COMPLETE")
    logger.info("=" * 70)
    logger.info(f"Total game logs: {total_game_logs:,}")
    logger.info(f"Total advanced stats: {total_advanced:,}")
    
    # Check collection counts
    gl_count = game_logs_collection.count_documents({})
    adv_count = advanced_stats_collection.count_documents({})
    logger.info(f"\nCollection counts:")
    logger.info(f"  bdl_historical_game_logs: {gl_count:,}")
    logger.info(f"  bdl_advanced_stats: {adv_count:,}")


if __name__ == "__main__":
    asyncio.run(main())
