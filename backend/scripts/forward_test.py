"""
Live Prop Capture & Forward Testing
=====================================
Since we can't backtest (environment has simulated BDL data, real Odds API data),
this captures live props and tracks predictions vs outcomes for forward testing.

Run daily before games to capture lines, then validate after games complete.
"""

import os
import sys
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import requests
from pymongo import MongoClient

sys.path.insert(0, '/app/backend')

from dotenv import load_dotenv
load_dotenv('/app/backend/.env')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME", "pick_vision")
BDL_API_KEY = os.environ.get("BDL_API_KEY")

client = MongoClient(MONGO_URL)
db = client[DB_NAME]

# Collection for forward test tracking
forward_tests = db['forward_test_picks']


def capture_daily_props():
    """
    Capture today's player props from BDL live odds.
    Store with our model predictions for later validation.
    """
    logger.info("Capturing today's player props...")
    
    # Get today's games from BDL
    today = datetime.now().strftime('%Y-%m-%d')
    
    games_url = "https://api.balldontlie.io/nba/v2/games"
    headers = {"Authorization": BDL_API_KEY}
    
    response = requests.get(games_url, headers=headers, params={"dates[]": today})
    if response.status_code != 200:
        logger.error(f"Failed to get games: {response.status_code}")
        return
    
    games = response.json().get('data', [])
    logger.info(f"Found {len(games)} games today")
    
    # Get live props for each game
    props_url = "https://api.balldontlie.io/nba/v2/odds/player_props"
    
    total_captured = 0
    
    for game in games:
        game_id = game.get('id')
        home_team = game.get('home_team', {}).get('full_name')
        away_team = game.get('visitor_team', {}).get('full_name')
        
        logger.info(f"Fetching props for {away_team} @ {home_team}")
        
        response = requests.get(props_url, headers=headers, params={"game_id": game_id})
        if response.status_code != 200:
            logger.warning(f"No props for game {game_id}")
            continue
        
        props = response.json().get('data', [])
        
        for prop in props:
            # Only capture over_under markets
            market = prop.get('market', {})
            if market.get('type') != 'over_under':
                continue
            
            prop_type = prop.get('prop_type')
            
            # Map to our stat types
            stat_map = {
                'points': 'PTS',
                'rebounds': 'REB',
                'assists': 'AST',
                'threes': '3PM',
                'points_rebounds_assists': 'PRA',
            }
            
            stat_type = stat_map.get(prop_type)
            if not stat_type:
                continue
            
            player_id = prop.get('player_id')
            line = float(prop.get('line_value', 0))
            
            # Get player name from hub
            player = db['nba_master_hub_2026'].find_one({'bdl_id': player_id})
            player_name = player.get('display_name') if player else f"Player_{player_id}"
            
            # Get model prediction
            from services.vegas_killer_model import VegasKillerModel
            model = VegasKillerModel(db)
            model.load_models()
            
            prediction = model.predict(
                player_name=player_name,
                stat_type=stat_type,
                line=line,
            )
            
            pred_data = prediction.get('prediction', {})
            
            # Store for forward test
            doc = {
                "game_id": game_id,
                "game_date": today,
                "player_id": player_id,
                "player_name": player_name,
                "home_team": home_team,
                "away_team": away_team,
                "stat_type": stat_type,
                "line": line,
                "over_odds": market.get('over_odds'),
                "under_odds": market.get('under_odds'),
                "predicted": pred_data.get('predicted'),
                "prob_over": pred_data.get('prob_over'),
                "prob_under": pred_data.get('prob_under'),
                "recommendation": pred_data.get('recommendation'),
                "captured_at": datetime.utcnow(),
                "actual_result": None,  # Filled in after game
                "validated": False,
            }
            
            forward_tests.update_one(
                {
                    "game_id": game_id,
                    "player_id": player_id,
                    "stat_type": stat_type,
                },
                {"$set": doc},
                upsert=True
            )
            total_captured += 1
    
    logger.info(f"Captured {total_captured} props for forward testing")


def validate_completed_games():
    """
    Check for unvalidated picks and fill in actual results.
    """
    logger.info("Validating completed games...")
    
    # Find unvalidated picks
    unvalidated = list(forward_tests.find({"validated": False}))
    logger.info(f"Found {len(unvalidated)} unvalidated picks")
    
    for pick in unvalidated:
        player_id = pick.get('player_id')
        game_id = pick.get('game_id')
        stat_type = pick.get('stat_type')
        
        # Get actual stats
        stats_url = f"https://api.balldontlie.io/nba/v2/stats"
        headers = {"Authorization": BDL_API_KEY}
        
        response = requests.get(stats_url, headers=headers, params={
            "game_ids[]": game_id,
            "player_ids[]": player_id,
        })
        
        if response.status_code != 200:
            continue
        
        stats = response.json().get('data', [])
        if not stats:
            continue
        
        stat = stats[0]
        
        # Get actual value
        actual = None
        if stat_type == 'PTS':
            actual = stat.get('pts')
        elif stat_type == 'REB':
            actual = stat.get('reb')
        elif stat_type == 'AST':
            actual = stat.get('ast')
        elif stat_type == '3PM':
            actual = stat.get('fg3m')
        elif stat_type == 'PRA':
            actual = (stat.get('pts', 0) or 0) + (stat.get('reb', 0) or 0) + (stat.get('ast', 0) or 0)
        
        if actual is not None:
            line = pick.get('line')
            predicted = pick.get('predicted')
            
            # Determine results
            actual_direction = 'OVER' if actual > line else 'UNDER'
            pred_direction = 'OVER' if predicted and predicted > line else 'UNDER'
            
            # Check if we would have bet
            prob_over = pick.get('prob_over', 50)
            prob_under = pick.get('prob_under', 50)
            bet_direction = None
            if prob_over >= 55:
                bet_direction = 'OVER'
            elif prob_under >= 55:
                bet_direction = 'UNDER'
            
            win = bet_direction == actual_direction if bet_direction else None
            
            forward_tests.update_one(
                {"_id": pick["_id"]},
                {"$set": {
                    "actual_result": actual,
                    "actual_direction": actual_direction,
                    "pred_direction": pred_direction,
                    "bet_direction": bet_direction,
                    "win": win,
                    "validated": True,
                    "validated_at": datetime.utcnow(),
                }}
            )
            logger.info(f"{pick['player_name']} {stat_type}: Line {line}, Predicted {predicted}, Actual {actual} - {'WIN' if win else 'LOSS' if win is False else 'NO BET'}")


def get_forward_test_summary():
    """Get summary of forward test results."""
    total = forward_tests.count_documents({})
    validated = forward_tests.count_documents({"validated": True})
    
    bets = list(forward_tests.find({"bet_direction": {"$ne": None}, "validated": True}))
    wins = sum(1 for b in bets if b.get('win'))
    losses = len(bets) - wins
    
    win_rate = wins / len(bets) * 100 if bets else 0
    roi = (wins * 0.91 - losses) / len(bets) * 100 if bets else 0
    
    return {
        "total_captured": total,
        "validated": validated,
        "total_bets": len(bets),
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 2),
        "roi": round(roi, 2),
        "profitable": win_rate > 52.4,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--capture', action='store_true', help='Capture today props')
    parser.add_argument('--validate', action='store_true', help='Validate completed games')
    parser.add_argument('--summary', action='store_true', help='Show summary')
    args = parser.parse_args()
    
    if args.capture:
        capture_daily_props()
    elif args.validate:
        validate_completed_games()
    elif args.summary:
        summary = get_forward_test_summary()
        print("\nFORWARD TEST SUMMARY")
        print("=" * 40)
        for k, v in summary.items():
            print(f"  {k}: {v}")
    else:
        parser.print_help()
