"""
Demon Tracker v2 - Three-Way Data Sync Engine
Orchestrates: The Odds API + BallDontLie + Tank01

March 2026 Season Implementation
"""

import httpx
import logging
import os
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any, Tuple
from thefuzz import fuzz
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# API Configuration
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "e1ae76ab21c34ee88ed552cffb4449fd")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

BDL_API_KEY = os.environ.get("BDL_API_KEY", "ad5544be-9969-434b-9389-2b7cf658c8e0")
BDL_BASE_URL = "https://api.balldontlie.io/v1"

TANK01_API_KEY = os.environ.get("TANK01_API_KEY", "402edbcac6mshd04997e7ca01d17p1879eajsn65ab176cdb1e")
TANK01_BASE = "https://tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com"

# Current NBA Season (2025-26)
CURRENT_SEASON = "2025"

# Supported markets for player props
PLAYER_MARKETS = [
    "player_points", "player_rebounds", "player_assists", "player_threes",
    "player_double_double", "player_blocks", "player_steals", "player_turnovers",
    "player_points_q1", "player_points_h1"
]

# All markets including game-level
ALL_MARKETS = ",".join([
    "h2h", "spreads", "totals",
    "player_points", "player_rebounds", "player_assists", "player_threes"
])

# Target bookmakers
TARGET_BOOKMAKERS = ["draftkings", "fanduel"]


class DemonTrackerEngine:
    """
    Three-way data sync engine for NBA prop betting analysis.
    
    Flow:
    1. Fetch today's events from The Odds API
    2. Pull all player prop lines from DraftKings & FanDuel
    3. Map player names to BallDontLie player IDs
    4. Calculate Triple-View hit rates (L5, L10, Season)
    5. Verify with Tank01 and get matchup strength
    6. Flag discrepancies > 1%
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.events_cache = db.events_cache
        self.odds_cache = db.odds_cache
        self.player_props = db.player_props
        self.stats_cache = db.stats_cache
        self.league_roster = db.league_roster
        self.matchup_data = db.matchup_data
        self.discrepancies = db.stat_discrepancies
        
        # In-memory caches for fast lookups
        self._player_name_map = {}  # odds_name -> bdl_player_id
        self._tank01_cache = {}
        self._last_sync = None
    
    # ==================== THE ODDS API ====================
    
    async def fetch_todays_events(self) -> List[Dict[str, Any]]:
        """
        Step 1: Get all NBA events for today from The Odds API
        """
        try:
            url = f"{ODDS_API_BASE}/sports/basketball_nba/events"
            params = {
                "apiKey": ODDS_API_KEY,
                "dateFormat": "iso"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=15.0)
                
                if response.status_code == 200:
                    events = response.json()
                    
                    # Store in cache
                    await self.events_cache.delete_many({})
                    for event in events:
                        event["fetched_at"] = datetime.now(timezone.utc).isoformat()
                        await self.events_cache.insert_one(event)
                    
                    logger.info(f"✓ Fetched {len(events)} NBA events from Odds API")
                    return events
                else:
                    logger.error(f"Odds API events error: {response.status_code}")
                    return []
                    
        except Exception as e:
            logger.error(f"Error fetching events: {e}")
            return []
    
    async def fetch_event_odds(self, event_id: str) -> Dict[str, Any]:
        """
        Step 2: Fetch ALL markets for a specific event
        Includes player props from DraftKings and FanDuel
        """
        try:
            url = f"{ODDS_API_BASE}/sports/basketball_nba/events/{event_id}/odds"
            params = {
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": ALL_MARKETS,
                "bookmakers": ",".join(TARGET_BOOKMAKERS),
                "oddsFormat": "american"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=20.0)
                
                if response.status_code == 200:
                    odds_data = response.json()
                    
                    # Store in cache
                    odds_data["event_id"] = event_id
                    odds_data["fetched_at"] = datetime.now(timezone.utc).isoformat()
                    
                    await self.odds_cache.update_one(
                        {"event_id": event_id},
                        {"$set": odds_data},
                        upsert=True
                    )
                    
                    logger.info(f"✓ Fetched odds for event {event_id}: {odds_data.get('home_team')} vs {odds_data.get('away_team')}")
                    return odds_data
                else:
                    logger.error(f"Odds API error for event {event_id}: {response.status_code}")
                    return {}
                    
        except Exception as e:
            logger.error(f"Error fetching odds for {event_id}: {e}")
            return {}
    
    async def fetch_all_todays_odds(self) -> List[Dict[str, Any]]:
        """
        Fetch odds for ALL today's events
        """
        events = await self.fetch_todays_events()
        all_odds = []
        
        for event in events:
            event_id = event.get("id")
            if event_id:
                odds = await self.fetch_event_odds(event_id)
                if odds:
                    all_odds.append(odds)
                # Small delay to respect rate limits
                await asyncio.sleep(0.3)
        
        logger.info(f"✓ Fetched odds for {len(all_odds)} events")
        return all_odds
    
    def extract_player_props_from_odds(self, odds_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract all player props from odds data
        Returns list of prop objects with player name, market, line, odds
        """
        props = []
        event_id = odds_data.get("id") or odds_data.get("event_id")
        home_team = odds_data.get("home_team", "")
        away_team = odds_data.get("away_team", "")
        commence_time = odds_data.get("commence_time", "")
        
        for bookmaker in odds_data.get("bookmakers", []):
            book_key = bookmaker.get("key")
            book_title = bookmaker.get("title")
            
            for market in bookmaker.get("markets", []):
                market_key = market.get("key", "")
                
                # Only process player prop markets
                if not market_key.startswith("player_"):
                    continue
                
                for outcome in market.get("outcomes", []):
                    player_name = outcome.get("description", "")
                    direction = outcome.get("name", "")  # Over/Under
                    line = outcome.get("point")
                    price = outcome.get("price")
                    
                    if player_name and line is not None:
                        prop = {
                            "event_id": event_id,
                            "home_team": home_team,
                            "away_team": away_team,
                            "commence_time": commence_time,
                            "player_name": player_name,
                            "market": market_key,
                            "direction": direction,
                            "line": float(line),
                            "price": price,
                            "bookmaker": book_key,
                            "bookmaker_title": book_title,
                            "fetched_at": datetime.now(timezone.utc).isoformat()
                        }
                        props.append(prop)
        
        return props
    
    # ==================== BALLDONTLIE API ====================
    
    async def search_bdl_player(self, player_name: str) -> Optional[Dict[str, Any]]:
        """
        Search for player in BallDontLie API
        Returns player dict with id, first_name, last_name, team info
        """
        # Check in-memory cache first
        if player_name in self._player_name_map:
            return self._player_name_map[player_name]
        
        try:
            # Split name for search (BDL works best with single names)
            name_parts = player_name.strip().split()
            search_terms = []
            
            if len(name_parts) >= 2:
                search_terms.append(name_parts[-1])  # Last name
                search_terms.append(name_parts[0])   # First name
            else:
                search_terms.append(player_name)
            
            url = f"{BDL_BASE_URL}/players"
            headers = {"Authorization": BDL_API_KEY}
            
            for search_term in search_terms:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        url, 
                        params={"search": search_term},
                        headers=headers,
                        timeout=10.0
                    )
                    
                    if response.status_code == 200:
                        players = response.json().get("data", [])
                        
                        if not players:
                            continue
                        
                        # Find best match
                        best_match = None
                        best_score = 0
                        
                        for player in players:
                            full_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
                            score = max(
                                fuzz.ratio(player_name.lower(), full_name.lower()),
                                fuzz.partial_ratio(player_name.lower(), full_name.lower())
                            )
                            
                            if score > best_score and score >= 60:
                                best_score = score
                                best_match = player
                        
                        if best_match:
                            self._player_name_map[player_name] = best_match
                            logger.debug(f"✓ Mapped {player_name} -> {best_match.get('first_name')} {best_match.get('last_name')} (ID: {best_match.get('id')})")
                            return best_match
            
            logger.warning(f"Could not find BDL player for: {player_name}")
            return None
            
        except Exception as e:
            logger.error(f"BDL player search error: {e}")
            return None
    
    async def fetch_bdl_player_stats(self, player_id: int) -> List[Dict[str, Any]]:
        """
        Fetch player game stats from BallDontLie for current season
        """
        try:
            # Check cache first
            cached = await self.stats_cache.find_one({"player_id": str(player_id)})
            if cached:
                cached_time = datetime.fromisoformat(cached["cached_at"])
                if datetime.now(timezone.utc) - cached_time < timedelta(hours=6):
                    return cached.get("games", [])
            
            url = f"{BDL_BASE_URL}/stats"
            params = {
                "player_ids[]": player_id,
                "seasons[]": CURRENT_SEASON,
                "per_page": 100
            }
            headers = {"Authorization": BDL_API_KEY}
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, headers=headers, timeout=15.0)
                
                if response.status_code == 200:
                    games = response.json().get("data", [])
                    
                    # Sort by date (most recent first)
                    games_sorted = sorted(
                        games,
                        key=lambda x: x.get("game", {}).get("date", ""),
                        reverse=True
                    )
                    
                    # Filter out DNP games (0 minutes)
                    def player_played(game):
                        minutes = game.get("min")
                        if minutes:
                            min_str = str(minutes).replace(":", "").strip()
                            if min_str and min_str != "0" and min_str != "00" and min_str != "000":
                                return True
                        pts = game.get("pts", 0) or 0
                        reb = game.get("reb", 0) or 0
                        ast = game.get("ast", 0) or 0
                        return (pts + reb + ast) > 0
                    
                    played_games = [g for g in games_sorted if player_played(g)]
                    
                    # Cache the results
                    await self.stats_cache.update_one(
                        {"player_id": str(player_id)},
                        {"$set": {
                            "player_id": str(player_id),
                            "games": played_games,
                            "cached_at": datetime.now(timezone.utc).isoformat(),
                            "season": CURRENT_SEASON
                        }},
                        upsert=True
                    )
                    
                    logger.debug(f"✓ Fetched {len(played_games)} games for player {player_id}")
                    return played_games
                    
        except Exception as e:
            logger.error(f"BDL stats fetch error: {e}")
        
        return []
    
    def calculate_triple_view_hit_rate(
        self,
        games: List[Dict[str, Any]],
        market: str,
        line: float
    ) -> Dict[str, Any]:
        """
        Calculate Triple-View hit rate: L5, L10, Season
        """
        # Map market to stat key
        market_to_stat = {
            "player_points": "pts",
            "player_points_q1": "pts",  # Approximate with full game
            "player_points_h1": "pts",
            "player_rebounds": "reb",
            "player_assists": "ast",
            "player_threes": "fg3m",
            "player_blocks": "blk",
            "player_steals": "stl",
            "player_turnovers": "turnover"
        }
        
        stat_key = market_to_stat.get(market, "pts")
        
        def calc_window(game_list, line_val):
            if not game_list:
                return {"games_over": 0, "total_games": 0, "hit_rate": 0, "avg": 0}
            
            games_over = sum(1 for g in game_list if (g.get(stat_key, 0) or 0) > line_val)
            total = len(game_list)
            hit_rate = games_over / total if total > 0 else 0
            avg = sum((g.get(stat_key, 0) or 0) for g in game_list) / total if total > 0 else 0
            
            return {
                "games_over": games_over,
                "total_games": total,
                "hit_rate": round(hit_rate, 3),
                "avg": round(avg, 1)
            }
        
        l5 = calc_window(games[:5], line)
        l10 = calc_window(games[:10], line)
        season = calc_window(games, line)
        
        # Trend detection
        trends = []
        if l5["avg"] > season["avg"] * 1.20:
            trends.append("HOT")
        elif l5["avg"] < season["avg"] * 0.80:
            trends.append("COLD")
        
        # Demon qualification (L10 hit rate >= 40%)
        is_demon = l10["hit_rate"] >= 0.40
        
        return {
            "l5": l5,
            "l10": l10,
            "season": season,
            "trends": trends,
            "is_demon": is_demon,
            "stat_key": stat_key
        }
    
    # ==================== TANK01 API ====================
    
    async def fetch_tank01_teams(self, include_rosters: bool = True) -> Dict[str, Any]:
        """
        Fetch team data from Tank01 including rosters with player stats
        """
        try:
            url = f"{TANK01_BASE}/getNBATeams"
            params = {}
            if include_rosters:
                params["rosters"] = "true"
                params["teamStats"] = "true"
            
            headers = {
                "X-RapidAPI-Key": TANK01_API_KEY,
                "X-RapidAPI-Host": "tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, headers=headers, timeout=30.0)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if isinstance(data, dict) and "body" in data:
                        teams = data.get("body", [])
                        logger.info(f"✓ Fetched {len(teams)} teams from Tank01")
                        return {"teams": teams, "success": True}
                    elif isinstance(data, list):
                        logger.info(f"✓ Fetched {len(data)} teams from Tank01")
                        return {"teams": data, "success": True}
                    else:
                        logger.warning(f"Tank01 unexpected response: {data}")
                        return {"teams": [], "success": False, "error": str(data)}
                else:
                    logger.error(f"Tank01 API error: {response.status_code}")
                    return {"teams": [], "success": False}
                    
        except Exception as e:
            logger.error(f"Tank01 fetch error: {e}")
            return {"teams": [], "success": False, "error": str(e)}
    
    async def get_tank01_player_stats(self, player_name: str) -> Optional[Dict[str, Any]]:
        """
        Get player stats from Tank01 for verification
        """
        # Check cache
        if player_name in self._tank01_cache:
            return self._tank01_cache[player_name]
        
        try:
            # Fetch teams with rosters
            teams_data = await self.fetch_tank01_teams(include_rosters=True)
            
            if not teams_data.get("success"):
                return None
            
            # Search through all team rosters
            for team in teams_data.get("teams", []):
                roster = team.get("Roster", {})
                if isinstance(roster, dict):
                    for player_id, player_data in roster.items():
                        tank_name = player_data.get("longName", "")
                        if fuzz.ratio(player_name.lower(), tank_name.lower()) >= 80:
                            self._tank01_cache[player_name] = player_data
                            return player_data
            
            return None
            
        except Exception as e:
            logger.error(f"Tank01 player search error: {e}")
            return None
    
    async def get_matchup_strength(
        self,
        player_team: str,
        opponent_team: str,
        position: str
    ) -> Dict[str, Any]:
        """
        Get matchup strength data from Tank01
        Returns defensive ranking vs position
        """
        try:
            teams_data = await self.fetch_tank01_teams(include_rosters=False)
            
            if not teams_data.get("success"):
                return {"def_rank": None, "matchup_grade": "N/A"}
            
            # Find opponent team stats
            for team in teams_data.get("teams", []):
                team_abv = team.get("teamAbv", "")
                team_name = team.get("teamName", "")
                
                if team_abv == opponent_team or opponent_team in team_name:
                    # Get defensive stats
                    oppg = float(team.get("oppg", 0) or 0)  # Opponent points per game
                    
                    # Calculate defensive rank (lower oppg = better defense)
                    # This is simplified - real implementation would track all teams
                    if oppg < 105:
                        def_rank = "Elite"
                        matchup_grade = "D"  # Hard matchup
                    elif oppg < 110:
                        def_rank = "Good"
                        matchup_grade = "C"
                    elif oppg < 115:
                        def_rank = "Average"
                        matchup_grade = "B"
                    else:
                        def_rank = "Poor"
                        matchup_grade = "A"  # Easy matchup
                    
                    return {
                        "opponent": team_name,
                        "oppg": oppg,
                        "def_rank": def_rank,
                        "matchup_grade": matchup_grade
                    }
            
            return {"def_rank": None, "matchup_grade": "N/A"}
            
        except Exception as e:
            logger.error(f"Matchup strength error: {e}")
            return {"def_rank": None, "matchup_grade": "N/A"}
    
    def verify_stats_discrepancy(
        self,
        bdl_avg: float,
        tank01_avg: float,
        stat_name: str
    ) -> Dict[str, Any]:
        """
        Compare BDL stats vs Tank01 stats
        Flag if discrepancy > 1%
        """
        if bdl_avg == 0 and tank01_avg == 0:
            return {"discrepancy": False, "diff_pct": 0}
        
        if bdl_avg == 0:
            diff_pct = 100
        else:
            diff_pct = abs(bdl_avg - tank01_avg) / bdl_avg * 100
        
        return {
            "discrepancy": diff_pct > 1,
            "diff_pct": round(diff_pct, 2),
            "bdl_value": bdl_avg,
            "tank01_value": tank01_avg,
            "stat": stat_name,
            "flagged": diff_pct > 1
        }
    
    # ==================== MAIN ORCHESTRATION ====================
    
    async def process_single_prop(
        self,
        prop: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Process a single player prop through the full pipeline:
        1. Map to BDL player
        2. Calculate hit rates
        3. Verify with Tank01
        4. Get matchup strength
        """
        player_name = prop.get("player_name", "")
        market = prop.get("market", "")
        line = prop.get("line", 0)
        
        result = {
            **prop,
            "bdl_player_id": None,
            "hit_rates": None,
            "tank01_verified": False,
            "matchup": None,
            "discrepancy": None,
            "processed_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Step 1: Map to BDL player
        bdl_player = await self.search_bdl_player(player_name)
        if not bdl_player:
            result["error"] = "Player not found in BDL"
            return result
        
        result["bdl_player_id"] = bdl_player.get("id")
        result["bdl_team"] = bdl_player.get("team", {}).get("abbreviation", "")
        
        # Step 2: Fetch BDL stats and calculate hit rates
        games = await self.fetch_bdl_player_stats(bdl_player.get("id"))
        if games:
            hit_rates = self.calculate_triple_view_hit_rate(games, market, line)
            result["hit_rates"] = hit_rates
        
        # Step 3: Verify with Tank01 (optional - can be slow)
        # tank01_player = await self.get_tank01_player_stats(player_name)
        # if tank01_player and hit_rates:
        #     # Compare season averages
        #     tank01_ppg = float(tank01_player.get("pts", 0) or 0)
        #     bdl_ppg = hit_rates["season"]["avg"]
        #     result["discrepancy"] = self.verify_stats_discrepancy(bdl_ppg, tank01_ppg, "points")
        #     result["tank01_verified"] = True
        
        # Step 4: Get matchup strength
        opponent = prop.get("away_team") if prop.get("home_team") and result["bdl_team"] in prop.get("home_team", "") else prop.get("home_team")
        if opponent:
            matchup = await self.get_matchup_strength(
                result["bdl_team"],
                opponent,
                bdl_player.get("position", "")
            )
            result["matchup"] = matchup
        
        return result
    
    async def run_full_sync(self) -> Dict[str, Any]:
        """
        Run the complete three-way data sync:
        1. Fetch today's events
        2. Get all odds/lines
        3. Process each player prop
        4. Store results
        """
        sync_start = datetime.now(timezone.utc)
        logger.info("🚀 DEMON TRACKER V2 - Starting full sync")
        
        results = {
            "success": True,
            "events_count": 0,
            "props_count": 0,
            "processed_count": 0,
            "demon_count": 0,
            "errors": [],
            "sync_duration": 0
        }
        
        try:
            # Step 1: Fetch all today's odds
            all_odds = await self.fetch_all_todays_odds()
            results["events_count"] = len(all_odds)
            
            if not all_odds:
                results["success"] = False
                results["errors"].append("No events found")
                return results
            
            # Step 2: Extract all player props
            all_props = []
            for odds_data in all_odds:
                props = self.extract_player_props_from_odds(odds_data)
                all_props.extend(props)
            
            results["props_count"] = len(all_props)
            logger.info(f"📊 Found {len(all_props)} player props across {len(all_odds)} events")
            
            # Step 3: Process unique player/market/line combinations
            # Group by player + market + line to avoid duplicates
            unique_props = {}
            for prop in all_props:
                key = f"{prop['player_name']}|{prop['market']}|{prop['line']}"
                if key not in unique_props:
                    unique_props[key] = prop
                else:
                    # Merge bookmaker data
                    existing = unique_props[key]
                    if prop['bookmaker'] not in str(existing.get('bookmakers', '')):
                        if 'bookmakers' not in existing:
                            existing['bookmakers'] = {existing['bookmaker']: existing['price']}
                        existing['bookmakers'][prop['bookmaker']] = prop['price']
            
            logger.info(f"📋 Processing {len(unique_props)} unique props")
            
            # Step 4: Process each prop (limit to avoid rate limits)
            processed_props = []
            demons = []
            
            for i, (key, prop) in enumerate(list(unique_props.items())[:100]):  # Limit to 100 for now
                try:
                    processed = await self.process_single_prop(prop)
                    processed_props.append(processed)
                    
                    if processed.get("hit_rates", {}).get("is_demon"):
                        demons.append(processed)
                    
                    # Rate limiting
                    if i % 10 == 0:
                        logger.info(f"  Processed {i+1}/{min(100, len(unique_props))} props")
                    await asyncio.sleep(0.2)
                    
                except Exception as e:
                    logger.error(f"Error processing prop {key}: {e}")
                    results["errors"].append(str(e))
            
            results["processed_count"] = len(processed_props)
            results["demon_count"] = len(demons)
            
            # Step 5: Store in database
            if processed_props:
                await self.player_props.delete_many({})
                await self.player_props.insert_many(processed_props)
            
            self._last_sync = datetime.now(timezone.utc)
            
        except Exception as e:
            logger.error(f"Sync error: {e}")
            results["success"] = False
            results["errors"].append(str(e))
        
        results["sync_duration"] = (datetime.now(timezone.utc) - sync_start).total_seconds()
        logger.info(f"✅ SYNC COMPLETE: {results['processed_count']} props, {results['demon_count']} demons in {results['sync_duration']:.1f}s")
        
        return results
    
    async def get_processed_props(
        self,
        event_id: Optional[str] = None,
        bookmaker: Optional[str] = None,
        market: Optional[str] = None,
        demons_only: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Retrieve processed props with optional filters
        """
        query = {}
        
        if event_id:
            query["event_id"] = event_id
        if bookmaker:
            query["bookmaker"] = bookmaker
        if market:
            query["market"] = market
        if demons_only:
            query["hit_rates.is_demon"] = True
        
        cursor = self.player_props.find(query, {"_id": 0})
        return await cursor.to_list(1000)
    
    async def get_sync_status(self) -> Dict[str, Any]:
        """
        Get current sync status
        """
        events_count = await self.events_cache.count_documents({})
        props_count = await self.player_props.count_documents({})
        demons_count = await self.player_props.count_documents({"hit_rates.is_demon": True})
        
        return {
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "events_cached": events_count,
            "props_cached": props_count,
            "demons_found": demons_count,
            "data_sources": {
                "odds_api": "active",
                "balldontlie": "active",
                "tank01": "limited"  # Rate limited
            }
        }
