"""
Historical Odds Fetcher - The Odds API
=======================================
Fetches historical NBA player prop lines for backtesting.

Uses The Odds API historical endpoint to get actual Vegas lines
that were offered before games, not simulated/fake lines.
"""

import os
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import requests
from pymongo import MongoClient

logger = logging.getLogger(__name__)

# Market key mapping
MARKET_MAP = {
    'PTS': 'player_points',
    'REB': 'player_rebounds', 
    'AST': 'player_assists',
    '3PM': 'player_threes',
    'PRA': 'player_points_rebounds_assists',
}

STAT_MAP = {v: k for k, v in MARKET_MAP.items()}


class HistoricalOddsFetcher:
    """
    Fetches historical player prop odds from The Odds API.
    
    Historical data available from May 3, 2023 onwards.
    Snapshots at 5-minute intervals for player props.
    """
    
    BASE_URL = "https://api.the-odds-api.com/v4"
    SPORT = "basketball_nba"
    
    def __init__(self, db, api_key: str = None):
        self.db = db
        self.api_key = api_key or os.environ.get("ODDS_API_KEY")
        if not self.api_key:
            raise ValueError("ODDS_API_KEY not configured")
        
        self.historical_odds = db['historical_odds']
        self.session = requests.Session()
        
        # Create indexes
        self._ensure_indexes()
    
    def _ensure_indexes(self):
        """Create MongoDB indexes for efficient queries."""
        self.historical_odds.create_index([("event_id", 1), ("player_name", 1), ("market", 1)])
        self.historical_odds.create_index([("game_date", 1)])
        self.historical_odds.create_index([("player_name", 1)])
        self.historical_odds.create_index([("snapshot_time", 1)])
    
    def _make_request(self, endpoint: str, params: Dict) -> Optional[Dict]:
        """Make API request with rate limit handling."""
        params['apiKey'] = self.api_key
        
        url = f"{self.BASE_URL}{endpoint}"
        
        try:
            response = self.session.get(url, params=params, timeout=30)
            
            # Log remaining quota
            remaining = response.headers.get('x-requests-remaining', 'unknown')
            used = response.headers.get('x-requests-used', 'unknown')
            logger.info(f"Odds API quota: {remaining} remaining, {used} used")
            
            if response.status_code == 429:
                logger.warning("Rate limited - waiting 60s")
                time.sleep(60)
                return self._make_request(endpoint, params)
            
            if response.status_code == 401:
                logger.error("Invalid API key")
                return None
            
            if response.status_code == 422:
                logger.warning(f"No data available: {response.text}")
                return None
                
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            return None
    
    def get_historical_events(self, date: str) -> List[Dict]:
        """
        Get list of NBA events (games) for a specific date.
        
        Args:
            date: Date string YYYY-MM-DD
        """
        endpoint = f"/historical/sports/{self.SPORT}/events"
        params = {
            'date': f"{date}T12:00:00Z",  # Midday snapshot
        }
        
        result = self._make_request(endpoint, params)
        if result and 'data' in result:
            return result['data']
        return []
    
    def get_historical_player_props(
        self,
        event_id: str,
        snapshot_time: str,
        markets: List[str] = None
    ) -> Optional[Dict]:
        """
        Get historical player props for a specific game and time.
        
        Args:
            event_id: The Odds API event ID
            snapshot_time: ISO8601 timestamp for historical snapshot
            markets: List of markets (e.g., ['player_points', 'player_rebounds'])
        """
        if markets is None:
            markets = ['player_points', 'player_rebounds', 'player_assists', 'player_threes']
        
        endpoint = f"/historical/sports/{self.SPORT}/events/{event_id}/odds"
        params = {
            'regions': 'us',
            'markets': ','.join(markets),
            'oddsFormat': 'american',
            'date': snapshot_time,
        }
        
        return self._make_request(endpoint, params)
    
    def fetch_and_store_game_props(
        self,
        event_id: str,
        game_date: str,
        home_team: str,
        away_team: str,
        hours_before_game: int = 2
    ) -> Dict[str, Any]:
        """
        Fetch player props for a game and store in MongoDB.
        
        Fetches props from ~2 hours before game time (when lines are most stable).
        
        Returns:
            Summary of fetched data
        """
        # Parse game date and get snapshot time (2 hours before)
        game_dt = datetime.fromisoformat(game_date.replace('Z', '+00:00'))
        snapshot_dt = game_dt - timedelta(hours=hours_before_game)
        snapshot_time = snapshot_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        
        logger.info(f"Fetching props for {away_team} @ {home_team} at {snapshot_time}")
        
        result = self.get_historical_player_props(event_id, snapshot_time)
        
        if not result or 'data' not in result:
            return {"error": "No data returned", "event_id": event_id}
        
        data = result['data']
        bookmakers = data.get('bookmakers', [])
        
        if not bookmakers:
            return {"error": "No bookmakers", "event_id": event_id}
        
        stored_count = 0
        
        for bookmaker in bookmakers:
            book_key = bookmaker.get('key')
            
            for market in bookmaker.get('markets', []):
                market_key = market.get('key')
                stat_type = STAT_MAP.get(market_key)
                
                if not stat_type:
                    continue
                
                for outcome in market.get('outcomes', []):
                    player_name = outcome.get('description')
                    if not player_name:
                        continue
                    
                    line = outcome.get('point')
                    price = outcome.get('price')
                    direction = outcome.get('name')  # Over or Under
                    
                    if line is None:
                        continue
                    
                    # Store the prop line
                    doc = {
                        "event_id": event_id,
                        "game_date": game_dt,
                        "snapshot_time": snapshot_dt,
                        "home_team": home_team,
                        "away_team": away_team,
                        "bookmaker": book_key,
                        "market": market_key,
                        "stat_type": stat_type,
                        "player_name": player_name,
                        "line": float(line),
                        "direction": direction,
                        "odds_american": price,
                        "fetched_at": datetime.utcnow(),
                    }
                    
                    # Upsert to avoid duplicates
                    self.historical_odds.update_one(
                        {
                            "event_id": event_id,
                            "player_name": player_name,
                            "market": market_key,
                            "direction": direction,
                            "bookmaker": book_key,
                        },
                        {"$set": doc},
                        upsert=True
                    )
                    stored_count += 1
        
        return {
            "event_id": event_id,
            "game": f"{away_team} @ {home_team}",
            "snapshot_time": snapshot_time,
            "bookmakers": len(bookmakers),
            "props_stored": stored_count,
        }
    
    def fetch_date_range(
        self,
        start_date: str,
        end_date: str,
        delay_seconds: float = 1.0
    ) -> Dict[str, Any]:
        """
        Fetch historical props for all games in a date range.
        
        Args:
            start_date: Start date YYYY-MM-DD
            end_date: End date YYYY-MM-DD
            delay_seconds: Delay between API calls to avoid rate limits
        
        Returns:
            Summary of all fetched data
        """
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')
        
        results = {
            "total_games": 0,
            "total_props": 0,
            "games_processed": [],
            "errors": [],
        }
        
        current = start
        while current <= end:
            date_str = current.strftime('%Y-%m-%d')
            logger.info(f"\n{'='*50}\nProcessing {date_str}\n{'='*50}")
            
            # Get events for this date
            events = self.get_historical_events(date_str)
            
            if not events:
                logger.info(f"No events found for {date_str}")
                current += timedelta(days=1)
                continue
            
            for event in events:
                event_id = event.get('id')
                commence_time = event.get('commence_time')
                home_team = event.get('home_team')
                away_team = event.get('away_team')
                
                if not all([event_id, commence_time, home_team, away_team]):
                    continue
                
                try:
                    result = self.fetch_and_store_game_props(
                        event_id=event_id,
                        game_date=commence_time,
                        home_team=home_team,
                        away_team=away_team,
                    )
                    
                    if 'error' in result:
                        results["errors"].append(result)
                    else:
                        results["total_games"] += 1
                        results["total_props"] += result.get('props_stored', 0)
                        results["games_processed"].append(result)
                    
                    # Rate limit delay
                    time.sleep(delay_seconds)
                    
                except Exception as e:
                    logger.error(f"Error processing {event_id}: {e}")
                    results["errors"].append({"event_id": event_id, "error": str(e)})
            
            current += timedelta(days=1)
        
        return results
    
    def get_stats_summary(self) -> Dict:
        """Get summary of stored historical odds."""
        total = self.historical_odds.count_documents({})
        
        # By stat type
        by_stat = {}
        for stat in ['PTS', 'REB', 'AST', '3PM', 'PRA']:
            count = self.historical_odds.count_documents({"stat_type": stat})
            by_stat[stat] = count
        
        # Date range
        oldest = self.historical_odds.find_one(sort=[("game_date", 1)])
        newest = self.historical_odds.find_one(sort=[("game_date", -1)])
        
        return {
            "total_props": total,
            "by_stat_type": by_stat,
            "date_range": {
                "oldest": oldest.get('game_date').isoformat() if oldest else None,
                "newest": newest.get('game_date').isoformat() if newest else None,
            },
            "unique_players": len(self.historical_odds.distinct("player_name")),
            "unique_games": len(self.historical_odds.distinct("event_id")),
        }
    
    def get_player_line(
        self,
        player_name: str,
        stat_type: str,
        game_date: datetime,
        bookmaker: str = "draftkings"
    ) -> Optional[float]:
        """
        Get the historical line for a player prop.
        
        Returns the Over line value (e.g., 23.5 for points).
        """
        market = MARKET_MAP.get(stat_type)
        if not market:
            return None
        
        # Find the line within a day of the game
        start = game_date - timedelta(hours=12)
        end = game_date + timedelta(hours=12)
        
        doc = self.historical_odds.find_one({
            "player_name": {"$regex": player_name, "$options": "i"},
            "stat_type": stat_type,
            "direction": "Over",
            "game_date": {"$gte": start, "$lte": end},
            "bookmaker": bookmaker,
        })
        
        if doc:
            return doc.get('line')
        
        # Try any bookmaker
        doc = self.historical_odds.find_one({
            "player_name": {"$regex": player_name, "$options": "i"},
            "stat_type": stat_type,
            "direction": "Over",
            "game_date": {"$gte": start, "$lte": end},
        })
        
        return doc.get('line') if doc else None
