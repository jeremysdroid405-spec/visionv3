"""
Fetch Historical Game Stats for Backtest
==========================================
Fetches BDL game stats for dates matching our historical odds data.
"""

import os
import sys
import requests
import logging
from datetime import datetime
from pymongo import MongoClient

sys.path.insert(0, '/app/backend')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# Load env manually
from dotenv import load_dotenv
load_dotenv('/app/backend/.env')

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME", "pick_vision")
BDL_API_KEY = os.environ.get("BDL_API_KEY")

client = MongoClient(MONGO_URL)
db = client[DB_NAME]

# Collection for historical backtest data
backtest_games = db['backtest_game_logs']


def fetch_stats_for_date(date_str: str) -> int:
    """Fetch all player stats for a specific date."""
    base_url = "https://api.balldontlie.io/nba/v1/stats"  # Use v1 for historical
    headers = {"Authorization": BDL_API_KEY}
    
    params = {
        "dates[]": date_str,
        "per_page": 100,
    }
    
    total_stored = 0
    cursor = None
    
    while True:
        if cursor:
            params["cursor"] = cursor
        
        response = requests.get(base_url, headers=headers, params=params, timeout=30)
        
        if response.status_code != 200:
            logger.error(f"Error fetching {date_str}: {response.status_code}")
            break
        
        data = response.json()
        stats = data.get('data', [])
        
        for stat in stats:
            player = stat.get('player', {})
            player_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
            
            doc = {
                "player_id": player.get('id'),
                "player_name": player_name,
                "game_date": date_str,
                "game_id": stat.get('game', {}).get('id'),
                "team_id": stat.get('team', {}).get('id'),
                "home_team": stat.get('game', {}).get('home_team_id'),
                "away_team": stat.get('game', {}).get('visitor_team_id'),
                "pts": stat.get('pts'),
                "reb": stat.get('reb'),
                "ast": stat.get('ast'),
                "fg3m": stat.get('fg3m'),
                "min": stat.get('min'),
                "fga": stat.get('fga'),
                "fgm": stat.get('fgm'),
            }
            
            backtest_games.update_one(
                {"player_id": doc["player_id"], "game_date": doc["game_date"]},
                {"$set": doc},
                upsert=True
            )
            total_stored += 1
        
        # Check for more pages
        meta = data.get('meta', {})
        cursor = meta.get('next_cursor')
        if not cursor:
            break
    
    return total_stored


def main():
    logger.info("=" * 60)
    logger.info("FETCHING BDL GAME STATS FOR BACKTEST")
    logger.info("=" * 60)
    
    # Get unique dates from our odds data
    dates_with_odds = list(db['historical_odds'].distinct('game_date'))
    logger.info(f"Have odds for {len(dates_with_odds)} unique game times")
    
    # Get unique dates
    unique_dates = set()
    for dt in dates_with_odds:
        unique_dates.add(dt.strftime('%Y-%m-%d'))
    
    unique_dates = sorted(unique_dates)
    logger.info(f"Unique dates to fetch: {len(unique_dates)}")
    
    total = 0
    for date_str in unique_dates:
        logger.info(f"Fetching {date_str}...")
        count = fetch_stats_for_date(date_str)
        total += count
        logger.info(f"  Stored {count} player stats")
    
    logger.info(f"\nTotal backtest game stats: {backtest_games.count_documents({})}")


if __name__ == "__main__":
    main()
