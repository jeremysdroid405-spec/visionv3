"""
Demon Tracker v2.1 - Three-Pillar Data Engine
============================================

Pillar 1: Line Ingestion (The Odds API)
- Pull ALL available betting lines for daily games
- Include exotic/alternate props from DraftKings and FanDuel

Pillar 2: Statistical Verification (BallDontLie API)
- Cross-reference every line with player stats
- Calculate Hit Rate for 2025-26 season (L5, L10, Season)

Pillar 3: Contextual Research (Tank01 API)
- Search for Injury Reports, Player News
- Flag players with injury/load management risk

Autonomous: Sync on app startup and populate Demon Cards
- Green: High hit rate (>= 50%)
- Yellow: Injury/news warning
- Red: Low hit rate (< 30%)
"""

import httpx
import logging
import os
import asyncio
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any, Tuple
from thefuzz import fuzz
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# ==================== API CONFIGURATION ====================

# Pillar 1: The Odds API
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "e1ae76ab21c34ee88ed552cffb4449fd")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Pillar 2: BallDontLie API
BDL_API_KEY = os.environ.get("BDL_API_KEY", "ad5544be-9969-434b-9389-2b7cf658c8e0")
BDL_BASE_URL = "https://api.balldontlie.io/v1"

# Pillar 3: Tank01 API (via RapidAPI)
TANK01_API_KEY = os.environ.get("TANK01_API_KEY", "402edbcac6mshd04997e7ca01d17p1879eajsn65ab176cdb1e")
TANK01_BASE = "https://tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com"

# Current NBA Season (2025-26)
CURRENT_SEASON = "2025"

# ALL available markets from The Odds API
ALL_MARKETS = ",".join([
    # Game markets
    "h2h", "spreads", "totals",
    # Player prop markets
    "player_points", "player_rebounds", "player_assists", "player_threes",
    "player_blocks", "player_steals", "player_turnovers",
    "player_points_rebounds", "player_points_assists", "player_rebounds_assists",
    "player_points_rebounds_assists",
    # Alternate/exotic markets
    "alternate_spreads", "alternate_totals"
])

# Target bookmakers
TARGET_BOOKMAKERS = ["draftkings", "fanduel"]

# Hit rate thresholds for card colors
HIT_RATE_HIGH = 0.50   # Green
HIT_RATE_LOW = 0.30    # Red
# Between LOW and HIGH = Standard

# Injury/load management keywords
INJURY_KEYWORDS = [
    "injury", "injured", "out", "questionable", "doubtful", "probable",
    "day-to-day", "GTD", "load management", "rest", "ankle", "knee",
    "hamstring", "back", "shoulder", "concussion", "illness", "personal"
]


class ThreePillarEngine:
    """
    Three-Pillar Data Engine for NBA Prop Betting Analysis
    
    Autonomous Flow:
    1. Derive current date
    2. Fetch ALL lines from Odds API (Pillar 1)
    3. Calculate hit rates from BallDontLie (Pillar 2)
    4. Check injury/news from Tank01 (Pillar 3)
    5. Generate color-coded Demon Cards
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.events_cache = db.events_cache
        self.odds_cache = db.odds_cache
        self.player_props = db.player_props
        self.stats_cache = db.stats_cache
        self.injury_cache = db.injury_cache
        self.news_cache = db.news_cache
        self.demon_cards = db.demon_cards
        
        # In-memory caches
        self._player_name_map = {}
        self._injury_flags = {}
        self._last_sync = None
    
    # ==================== PILLAR 1: LINE INGESTION (ODDS API) ====================
    
    async def fetch_todays_events(self) -> List[Dict[str, Any]]:
        """
        Pillar 1: Get all NBA events for today from The Odds API
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
                    
                    logger.info(f"✓ PILLAR 1: Fetched {len(events)} NBA events")
                    return events
                else:
                    logger.error(f"Odds API events error: {response.status_code}")
                    return []
                    
        except Exception as e:
            logger.error(f"Pillar 1 error: {e}")
            return []
    
    async def fetch_all_event_odds(self, event_id: str) -> Dict[str, Any]:
        """
        Fetch ALL markets (including exotic props) for an event
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
                    odds_data["event_id"] = event_id
                    odds_data["fetched_at"] = datetime.now(timezone.utc).isoformat()
                    
                    await self.odds_cache.update_one(
                        {"event_id": event_id},
                        {"$set": odds_data},
                        upsert=True
                    )
                    
                    return odds_data
                elif response.status_code == 422:
                    # Some markets may not be available, try basic markets
                    params["markets"] = "player_points,player_rebounds,player_assists,player_threes"
                    response = await client.get(url, params=params, timeout=20.0)
                    if response.status_code == 200:
                        return response.json()
                    
                return {}
                    
        except Exception as e:
            logger.error(f"Odds fetch error for {event_id}: {e}")
            return {}
    
    def extract_player_props(self, odds_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract all player props from odds data"""
        props = []
        event_id = odds_data.get("id") or odds_data.get("event_id")
        home_team = odds_data.get("home_team", "")
        away_team = odds_data.get("away_team", "")
        commence_time = odds_data.get("commence_time", "")
        
        for bookmaker in odds_data.get("bookmakers", []):
            book_key = bookmaker.get("key")
            
            for market in bookmaker.get("markets", []):
                market_key = market.get("key", "")
                
                # Only player prop markets
                if not market_key.startswith("player_"):
                    continue
                
                for outcome in market.get("outcomes", []):
                    player_name = outcome.get("description", "")
                    direction = outcome.get("name", "")
                    line = outcome.get("point")
                    price = outcome.get("price")
                    
                    if player_name and line is not None:
                        props.append({
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
                            "fetched_at": datetime.now(timezone.utc).isoformat()
                        })
        
        return props
    
    # ==================== PILLAR 2: STATISTICAL VERIFICATION (BDL) ====================
    
    async def search_bdl_player(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Search for player in BallDontLie API"""
        if player_name in self._player_name_map:
            return self._player_name_map[player_name]
        
        try:
            name_parts = player_name.strip().split()
            search_terms = [name_parts[-1]] if len(name_parts) >= 2 else [player_name]
            if len(name_parts) >= 2:
                search_terms.append(name_parts[0])
            
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
                            return best_match
            
            return None
            
        except Exception as e:
            logger.error(f"BDL search error: {e}")
            return None
    
    async def fetch_player_stats(self, player_id: int) -> List[Dict[str, Any]]:
        """Fetch player game stats from BallDontLie for 2025-26 season"""
        try:
            # Check cache
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
                    
                    # Filter out DNP games
                    def player_played(game):
                        minutes = game.get("min")
                        if minutes:
                            min_str = str(minutes).replace(":", "").strip()
                            if min_str and min_str != "0" and min_str != "00":
                                return True
                        pts = game.get("pts", 0) or 0
                        reb = game.get("reb", 0) or 0
                        ast = game.get("ast", 0) or 0
                        return (pts + reb + ast) > 0
                    
                    played_games = [g for g in games_sorted if player_played(g)]
                    
                    # Cache results
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
                    
                    return played_games
                    
        except Exception as e:
            logger.error(f"BDL stats error: {e}")
        
        return []
    
    def calculate_triple_view_hit_rate(
        self,
        games: List[Dict[str, Any]],
        market: str,
        line: float
    ) -> Dict[str, Any]:
        """Calculate L5, L10, Season hit rates"""
        market_to_stat = {
            "player_points": "pts",
            "player_rebounds": "reb",
            "player_assists": "ast",
            "player_threes": "fg3m",
            "player_blocks": "blk",
            "player_steals": "stl",
            "player_turnovers": "turnover",
            "player_points_rebounds": ["pts", "reb"],
            "player_points_assists": ["pts", "ast"],
            "player_rebounds_assists": ["reb", "ast"],
            "player_points_rebounds_assists": ["pts", "reb", "ast"]
        }
        
        stat_keys = market_to_stat.get(market, "pts")
        if isinstance(stat_keys, str):
            stat_keys = [stat_keys]
        
        def get_stat_value(game):
            return sum((game.get(key, 0) or 0) for key in stat_keys)
        
        def calc_window(game_list, line_val):
            if not game_list:
                return {"games_over": 0, "total_games": 0, "hit_rate": 0, "avg": 0}
            
            games_over = sum(1 for g in game_list if get_stat_value(g) > line_val)
            total = len(game_list)
            hit_rate = games_over / total if total > 0 else 0
            avg = sum(get_stat_value(g) for g in game_list) / total if total > 0 else 0
            
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
        if l5["avg"] > season["avg"] * 1.15:
            trends.append("HOT")
        elif l5["avg"] < season["avg"] * 0.85:
            trends.append("COLD")
        
        # Determine card color based on L10 hit rate
        l10_rate = l10["hit_rate"]
        if l10_rate >= HIT_RATE_HIGH:
            card_color = "green"
        elif l10_rate < HIT_RATE_LOW:
            card_color = "red"
        else:
            card_color = "standard"
        
        return {
            "l5": l5,
            "l10": l10,
            "season": season,
            "trends": trends,
            "card_color": card_color,
            "is_demon": l10_rate >= 0.40
        }
    
    # ==================== PILLAR 3: CONTEXTUAL RESEARCH (TANK01) ====================
    
    async def fetch_tank01_news(self) -> List[Dict[str, Any]]:
        """Fetch latest NBA news from Tank01"""
        try:
            url = f"{TANK01_BASE}/getNBANews"
            headers = {
                "X-RapidAPI-Key": TANK01_API_KEY,
                "X-RapidAPI-Host": "tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, timeout=15.0)
                
                if response.status_code == 200:
                    data = response.json()
                    news_items = data.get("body", []) if isinstance(data, dict) else data
                    
                    if isinstance(news_items, list):
                        # Store in cache
                        await self.news_cache.delete_many({})
                        for item in news_items[:50]:  # Store latest 50
                            item["fetched_at"] = datetime.now(timezone.utc).isoformat()
                            await self.news_cache.insert_one(item)
                        
                        logger.info(f"✓ PILLAR 3: Fetched {len(news_items)} news items")
                        return news_items
                        
                elif response.status_code == 429:
                    logger.warning("Tank01 rate limited")
                    
        except Exception as e:
            logger.error(f"Tank01 news error: {e}")
        
        return []
    
    async def fetch_tank01_teams_with_injuries(self) -> Dict[str, List[str]]:
        """Fetch team rosters to identify injury status"""
        injuries = {}
        
        try:
            url = f"{TANK01_BASE}/getNBATeams"
            params = {"rosters": "true", "schedules": "false", "topPerformers": "false"}
            headers = {
                "X-RapidAPI-Key": TANK01_API_KEY,
                "X-RapidAPI-Host": "tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, headers=headers, timeout=30.0)
                
                if response.status_code == 200:
                    data = response.json()
                    teams = data.get("body", []) if isinstance(data, dict) else data
                    
                    if isinstance(teams, list):
                        for team in teams:
                            roster = team.get("Roster", {})
                            if isinstance(roster, dict):
                                for player_id, player_data in roster.items():
                                    injury_status = player_data.get("injury", {})
                                    if injury_status and isinstance(injury_status, dict):
                                        status = injury_status.get("designation", "")
                                        if status and status.lower() in ["out", "questionable", "doubtful", "day-to-day"]:
                                            player_name = player_data.get("longName", "")
                                            injuries[player_name.lower()] = {
                                                "status": status,
                                                "description": injury_status.get("description", ""),
                                                "team": team.get("teamAbv", "")
                                            }
                    
                    logger.info(f"✓ PILLAR 3: Found {len(injuries)} injured players")
                    
        except Exception as e:
            logger.error(f"Tank01 injuries error: {e}")
        
        return injuries
    
    def check_injury_from_news(self, player_name: str, news_items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Check if player has injury/news mention"""
        player_lower = player_name.lower()
        player_parts = player_lower.split()
        
        for news in news_items:
            title = (news.get("title", "") or "").lower()
            link = (news.get("link", "") or "").lower()
            
            # Check if player is mentioned
            player_mentioned = False
            for part in player_parts:
                if len(part) > 2 and part in title:
                    player_mentioned = True
                    break
            
            if player_mentioned:
                # Check for injury keywords
                for keyword in INJURY_KEYWORDS:
                    if keyword in title:
                        return {
                            "type": "injury_news",
                            "title": news.get("title", ""),
                            "link": news.get("link", ""),
                            "keyword": keyword
                        }
        
        return None
    
    def check_player_injury_status(
        self,
        player_name: str,
        injuries: Dict[str, Any],
        news_items: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Check player's injury/news status from all sources
        Returns warning level: none, yellow (caution), red (out)
        """
        player_lower = player_name.lower()
        
        # Check direct injury list
        if player_lower in injuries:
            injury_data = injuries[player_lower]
            status = injury_data.get("status", "").lower()
            
            if status in ["out"]:
                return {
                    "warning_level": "red",
                    "status": status.upper(),
                    "description": injury_data.get("description", ""),
                    "source": "injury_report"
                }
            elif status in ["questionable", "doubtful", "day-to-day"]:
                return {
                    "warning_level": "yellow",
                    "status": status.upper(),
                    "description": injury_data.get("description", ""),
                    "source": "injury_report"
                }
        
        # Check news for injury mentions
        news_injury = self.check_injury_from_news(player_name, news_items)
        if news_injury:
            return {
                "warning_level": "yellow",
                "status": "NEWS ALERT",
                "description": news_injury.get("title", ""),
                "source": "news",
                "keyword": news_injury.get("keyword", "")
            }
        
        return {"warning_level": "none"}
    
    # ==================== MAIN ORCHESTRATION ====================
    
    async def process_prop_with_full_analysis(
        self,
        prop: Dict[str, Any],
        injuries: Dict[str, Any],
        news_items: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Process a single prop through all three pillars"""
        player_name = prop.get("player_name", "")
        market = prop.get("market", "")
        line = prop.get("line", 0)
        
        result = {
            **prop,
            "bdl_player_id": None,
            "hit_rates": None,
            "injury_status": {"warning_level": "none"},
            "card_color": "standard",
            "processed_at": datetime.now(timezone.utc).isoformat()
        }
        
        # PILLAR 2: Get BDL stats
        bdl_player = await self.search_bdl_player(player_name)
        if bdl_player:
            result["bdl_player_id"] = bdl_player.get("id")
            result["bdl_team"] = bdl_player.get("team", {}).get("abbreviation", "")
            result["position"] = bdl_player.get("position", "")
            
            games = await self.fetch_player_stats(bdl_player.get("id"))
            if games:
                hit_rates = self.calculate_triple_view_hit_rate(games, market, line)
                result["hit_rates"] = hit_rates
                result["card_color"] = hit_rates.get("card_color", "standard")
        
        # PILLAR 3: Check injury status
        injury_status = self.check_player_injury_status(player_name, injuries, news_items)
        result["injury_status"] = injury_status
        
        # Override card color if injured
        if injury_status["warning_level"] == "red":
            result["card_color"] = "red"
        elif injury_status["warning_level"] == "yellow" and result["card_color"] != "red":
            result["card_color"] = "yellow"
        
        return result
    
    async def autonomous_three_pillar_sync(self) -> Dict[str, Any]:
        """
        Autonomous sync that runs on app startup
        Executes full three-pillar data sync and generates Demon Cards
        """
        sync_start = datetime.now(timezone.utc)
        current_date = sync_start.strftime("%Y-%m-%d")
        logger.info(f"🚀 THREE-PILLAR SYNC STARTED - {current_date}")
        
        results = {
            "success": True,
            "sync_date": current_date,
            "sync_time": sync_start.isoformat(),
            "pillar_1_events": 0,
            "pillar_1_props": 0,
            "pillar_2_stats_fetched": 0,
            "pillar_3_injuries_found": 0,
            "pillar_3_news_items": 0,
            "demon_cards": {
                "green": 0,
                "yellow": 0,
                "red": 0,
                "standard": 0,
                "total": 0
            },
            "errors": [],
            "duration": 0
        }
        
        try:
            # PILLAR 1: Fetch all events and odds
            logger.info("📊 PILLAR 1: Fetching lines from Odds API...")
            events = await self.fetch_todays_events()
            results["pillar_1_events"] = len(events)
            
            all_props = []
            for event in events:
                event_id = event.get("id")
                if event_id:
                    odds = await self.fetch_all_event_odds(event_id)
                    if odds:
                        props = self.extract_player_props(odds)
                        all_props.extend(props)
                    await asyncio.sleep(0.2)
            
            results["pillar_1_props"] = len(all_props)
            logger.info(f"✓ PILLAR 1 COMPLETE: {len(all_props)} props from {len(events)} events")
            
            # PILLAR 3: Fetch injury data and news (do before processing)
            logger.info("🏥 PILLAR 3: Fetching injury reports and news...")
            injuries = await self.fetch_tank01_teams_with_injuries()
            news_items = await self.fetch_tank01_news()
            results["pillar_3_injuries_found"] = len(injuries)
            results["pillar_3_news_items"] = len(news_items)
            logger.info(f"✓ PILLAR 3 DATA: {len(injuries)} injuries, {len(news_items)} news items")
            
            # Deduplicate props by player+market+line
            unique_props = {}
            for prop in all_props:
                key = f"{prop['player_name']}|{prop['market']}|{prop['line']}"
                if key not in unique_props:
                    unique_props[key] = prop
                else:
                    # Merge bookmaker data
                    existing = unique_props[key]
                    if "all_prices" not in existing:
                        existing["all_prices"] = {existing["bookmaker"]: existing["price"]}
                    existing["all_prices"][prop["bookmaker"]] = prop["price"]
            
            logger.info(f"📊 Processing {len(unique_props)} unique props...")
            
            # PILLAR 2 + 3: Process each prop
            processed_props = []
            for i, (key, prop) in enumerate(list(unique_props.items())[:150]):  # Limit to avoid timeouts
                try:
                    processed = await self.process_prop_with_full_analysis(prop, injuries, news_items)
                    processed_props.append(processed)
                    
                    # Count by card color
                    color = processed.get("card_color", "standard")
                    results["demon_cards"][color] = results["demon_cards"].get(color, 0) + 1
                    results["demon_cards"]["total"] += 1
                    
                    if processed.get("bdl_player_id"):
                        results["pillar_2_stats_fetched"] += 1
                    
                    if i % 20 == 0 and i > 0:
                        logger.info(f"  Processed {i}/{min(150, len(unique_props))} props")
                    
                    await asyncio.sleep(0.15)
                    
                except Exception as e:
                    results["errors"].append(f"Prop {key}: {str(e)}")
            
            # Store demon cards
            if processed_props:
                await self.demon_cards.delete_many({})
                await self.demon_cards.insert_many(processed_props)
            
            self._last_sync = datetime.now(timezone.utc)
            
        except Exception as e:
            logger.error(f"Sync error: {e}")
            results["success"] = False
            results["errors"].append(str(e))
        
        results["duration"] = (datetime.now(timezone.utc) - sync_start).total_seconds()
        
        logger.info(f"""
✅ THREE-PILLAR SYNC COMPLETE
   Duration: {results['duration']:.1f}s
   Events: {results['pillar_1_events']}
   Props: {results['pillar_1_props']}
   Demon Cards:
     🟢 Green (High): {results['demon_cards']['green']}
     🟡 Yellow (Warning): {results['demon_cards']['yellow']}
     🔴 Red (Low/Injured): {results['demon_cards']['red']}
     ⚪ Standard: {results['demon_cards']['standard']}
""")
        
        return results
    
    async def get_demon_cards(
        self,
        color: Optional[str] = None,
        market: Optional[str] = None,
        bookmaker: Optional[str] = None,
        demons_only: bool = False
    ) -> List[Dict[str, Any]]:
        """Get processed demon cards with optional filters"""
        query = {}
        
        if color:
            query["card_color"] = color
        if market:
            query["market"] = market
        if bookmaker:
            query["bookmaker"] = bookmaker
        if demons_only:
            query["hit_rates.is_demon"] = True
        
        cursor = self.demon_cards.find(query, {"_id": 0})
        cards = await cursor.to_list(1000)
        
        # Filter out None cards and cards without hit_rates
        cards = [c for c in cards if c is not None and c.get("hit_rates")]
        
        # Sort by card color priority (green first, then yellow, then standard, then red)
        color_priority = {"green": 0, "yellow": 1, "standard": 2, "red": 3}
        
        def sort_key(x):
            if not x:
                return (999, 0)
            card_color = x.get("card_color", "standard")
            hit_rate = 0
            if x.get("hit_rates") and x.get("hit_rates", {}).get("l10"):
                hit_rate = x.get("hit_rates", {}).get("l10", {}).get("hit_rate", 0) or 0
            return (color_priority.get(card_color, 2), -hit_rate)
        
        cards.sort(key=sort_key)
        
        return cards
    
    async def get_sync_status(self) -> Dict[str, Any]:
        """Get current sync status"""
        events_count = await self.events_cache.count_documents({})
        cards_count = await self.demon_cards.count_documents({})
        
        # Count by color
        green_count = await self.demon_cards.count_documents({"card_color": "green"})
        yellow_count = await self.demon_cards.count_documents({"card_color": "yellow"})
        red_count = await self.demon_cards.count_documents({"card_color": "red"})
        demons_count = await self.demon_cards.count_documents({"hit_rates.is_demon": True})
        
        return {
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "events_cached": events_count,
            "demon_cards_total": cards_count,
            "card_colors": {
                "green": green_count,
                "yellow": yellow_count,
                "red": red_count,
                "standard": cards_count - green_count - yellow_count - red_count
            },
            "demons_found": demons_count,
            "data_sources": {
                "odds_api": "active",
                "balldontlie": "active",
                "tank01": "active"
            },
            "season": CURRENT_SEASON
        }
