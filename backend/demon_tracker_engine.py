"""
Demon Tracker v3 - Two-Step Deep Ingestion Engine
=================================================

Step 1: Event Discovery - Get ALL event IDs for today
Step 2: Deep Prop Pull - For EACH event, pull ALL markets
Step 3: BallDontLie Integration - Map ALL players, calculate hit rates
Step 4: Tank01 Safety Check - Query injuries and news for warnings

Final Output: Scrollable list of ALL players with lines and verified stats
"""

import httpx
import logging
import os
import asyncio
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any, Tuple, Set
from thefuzz import fuzz
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# ==================== API CONFIGURATION ====================

# Pillar 1: The Odds API
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "e1ae76ab21c34ee88ed552cffb4449fd")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Pillar 2: BallDontLie API (Primary stats source)
BDL_API_KEY = os.environ.get("BDL_API_KEY", "ad5544be-9969-434b-9389-2b7cf658c8e0")
BDL_BASE_URL = "https://api.balldontlie.io/v1"

# NOTE: Tank01 has been REMOVED from this application. BDL is the only stats source.

# Current NBA Season (2025-26)
CURRENT_SEASON = "2025"

# ALL available player prop markets - COMPREHENSIVE LIST
ALL_PLAYER_MARKETS = ",".join([
    # Basic stats
    "player_points",
    "player_rebounds", 
    "player_assists",
    "player_threes",
    "player_blocks",
    "player_steals",
    "player_turnovers",
    # Combo stats
    "player_points_rebounds",
    "player_points_assists",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
    # Special markets
    "player_double_double",
    "player_triple_double",
    "player_first_basket",
    # Alternate lines (exotic)
    "alternate_player_points",
    "alternate_player_rebounds",
    "alternate_player_assists",
    "alternate_player_threes",
])

# Target bookmakers
TARGET_BOOKMAKERS = ["draftkings", "fanduel"]

# Hit rate thresholds
HIT_RATE_HIGH = 0.50   # Green
HIT_RATE_LOW = 0.30    # Red

# Injury keywords for news parsing
INJURY_KEYWORDS = [
    "injury", "injured", "out", "questionable", "doubtful", "probable",
    "day-to-day", "GTD", "game time decision", "load management", "rest",
    "ankle", "knee", "hamstring", "back", "shoulder", "concussion", 
    "illness", "personal", "sore", "sprain", "strain"
]


class DeepIngestionEngine:
    """
    Two-Step Deep Ingestion Engine for comprehensive prop coverage.
    
    Ensures NO markets are missed by:
    1. Fetching ALL event IDs first
    2. Making individual API calls for EACH event with ALL markets
    3. Processing EVERY player found (not just top stars)
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
        
        # In-memory caches for performance
        self._player_name_map = {}  # player_name -> bdl_player_data
        self._injury_data = {}      # player_name -> injury_status
        self._news_data = []        # list of news items
        self._last_sync = None
    
    # ==================== STEP 1: EVENT DISCOVERY ====================
    
    async def discover_all_events(self) -> List[Dict[str, Any]]:
        """
        Step 1: Get ALL event IDs for today from The Odds API
        This is the foundation - we need every single game
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
                    
                    # Clear and store fresh event data
                    await self.events_cache.delete_many({})
                    for event in events:
                        event["fetched_at"] = datetime.now(timezone.utc).isoformat()
                        await self.events_cache.insert_one(event)
                    
                    logger.info(f"✓ STEP 1 - EVENT DISCOVERY: Found {len(events)} NBA events")
                    for e in events:
                        logger.info(f"  • {e.get('id')}: {e.get('away_team')} @ {e.get('home_team')}")
                    
                    return events
                else:
                    logger.error(f"Event discovery failed: {response.status_code}")
                    return []
                    
        except Exception as e:
            logger.error(f"Event discovery error: {e}")
            return []
    
    # ==================== STEP 2: DEEP PROP PULL ====================
    
    async def deep_pull_event_odds(self, event_id: str, event_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Step 2: Deep pull ALL markets for a specific event
        Uses comprehensive market list to capture exotic lines
        """
        try:
            url = f"{ODDS_API_BASE}/sports/basketball_nba/events/{event_id}/odds"
            params = {
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": ALL_PLAYER_MARKETS,
                "bookmakers": ",".join(TARGET_BOOKMAKERS),
                "oddsFormat": "american"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=30.0)
                
                if response.status_code == 200:
                    odds_data = response.json()
                    
                    # Enrich with event info
                    odds_data["event_id"] = event_id
                    odds_data["fetched_at"] = datetime.now(timezone.utc).isoformat()
                    
                    # Count what we got
                    markets_found = set()
                    players_found = set()
                    total_outcomes = 0
                    
                    for bm in odds_data.get("bookmakers", []):
                        for market in bm.get("markets", []):
                            markets_found.add(market.get("key"))
                            for outcome in market.get("outcomes", []):
                                total_outcomes += 1
                                if outcome.get("description"):
                                    players_found.add(outcome.get("description"))
                    
                    logger.info(f"  ✓ {event_info.get('away_team')} @ {event_info.get('home_team')}: {len(markets_found)} markets, {len(players_found)} players, {total_outcomes} outcomes")
                    
                    # Store in cache
                    await self.odds_cache.update_one(
                        {"event_id": event_id},
                        {"$set": odds_data},
                        upsert=True
                    )
                    
                    return odds_data
                    
                elif response.status_code == 422:
                    # Some markets unavailable - retry with basic markets
                    logger.warning(f"  ⚠ Some markets unavailable for {event_id}, using basic set")
                    params["markets"] = "player_points,player_rebounds,player_assists,player_threes,player_blocks,player_steals"
                    response = await client.get(url, params=params, timeout=30.0)
                    if response.status_code == 200:
                        return response.json()
                        
                return {}
                    
        except Exception as e:
            logger.error(f"Deep pull error for {event_id}: {e}")
            return {}
    
    def extract_all_player_props(self, odds_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract EVERY player prop from odds data - no filtering
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
                
                # Process ALL player markets
                if not market_key.startswith("player_") and not market_key.startswith("alternate_player_"):
                    continue
                
                for outcome in market.get("outcomes", []):
                    player_name = outcome.get("description", "")
                    direction = outcome.get("name", "")  # Over/Under
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
                            "bookmaker_title": book_title,
                            "fetched_at": datetime.now(timezone.utc).isoformat()
                        })
        
        return props
    
    # ==================== STEP 3: BALLDONTLIE INTEGRATION ====================
    
    async def search_bdl_player(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Map player name to BallDontLie player data"""
        # Check cache first
        if player_name in self._player_name_map:
            return self._player_name_map[player_name]
        
        try:
            # Split name for better search
            name_parts = player_name.strip().split()
            search_terms = []
            
            if len(name_parts) >= 2:
                search_terms.append(name_parts[-1])  # Last name first
                search_terms.append(name_parts[0])   # Then first name
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
                        
                        # Find best match using fuzzy matching
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
            logger.error(f"BDL search error for {player_name}: {e}")
            return None
    
    async def fetch_player_season_stats(self, player_id: int) -> List[Dict[str, Any]]:
        """Fetch 2025-26 season stats for a player"""
        try:
            # Check cache
            cached = await self.stats_cache.find_one({"player_id": str(player_id)})
            if cached:
                cached_time = datetime.fromisoformat(cached["cached_at"])
                if datetime.now(timezone.utc) - cached_time < timedelta(hours=4):
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
                        return (game.get("pts", 0) or 0) + (game.get("reb", 0) or 0) + (game.get("ast", 0) or 0) > 0
                    
                    played_games = [g for g in games_sorted if player_played(g)]
                    
                    # Cache results
                    await self.stats_cache.update_one(
                        {"player_id": str(player_id)},
                        {"$set": {
                            "player_id": str(player_id),
                            "games": played_games,
                            "cached_at": datetime.now(timezone.utc).isoformat()
                        }},
                        upsert=True
                    )
                    
                    return played_games
                    
        except Exception as e:
            logger.error(f"Stats fetch error for player {player_id}: {e}")
        
        return []
    
    def calculate_hit_rates(self, games: List[Dict[str, Any]], market: str, line: float) -> Dict[str, Any]:
        """Calculate L5, L10, and Season hit rates"""
        # Map market to stat keys
        market_to_stat = {
            "player_points": ["pts"],
            "alternate_player_points": ["pts"],
            "player_rebounds": ["reb"],
            "alternate_player_rebounds": ["reb"],
            "player_assists": ["ast"],
            "alternate_player_assists": ["ast"],
            "player_threes": ["fg3m"],
            "alternate_player_threes": ["fg3m"],
            "player_blocks": ["blk"],
            "player_steals": ["stl"],
            "player_turnovers": ["turnover"],
            "player_points_rebounds": ["pts", "reb"],
            "player_points_assists": ["pts", "ast"],
            "player_rebounds_assists": ["reb", "ast"],
            "player_points_rebounds_assists": ["pts", "reb", "ast"],
        }
        
        stat_keys = market_to_stat.get(market, ["pts"])
        
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
        if l5["total_games"] >= 3 and season["total_games"] >= 10:
            if l5["avg"] > season["avg"] * 1.15:
                trends.append("HOT")
            elif l5["avg"] < season["avg"] * 0.85:
                trends.append("COLD")
        
        # Card color based on L10 hit rate
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
    
    # ==================== STEP 4: INJURY/NEWS CHECK (BDL + ESPN) ====================
    
    async def fetch_injuries(self) -> Dict[str, Any]:
        """Fetch injury data from BDL/ESPN (Tank01 REMOVED)"""
        # Injuries are synced via injury_service.py from ESPN + BDL
        # This method is kept for interface compatibility but returns cached data
        return self._injury_data or {}
    
    async def fetch_news(self) -> List[Dict[str, Any]]:
        """Fetch latest NBA news (Tank01 REMOVED)"""
        # News is now fetched via ESPN in injury_service.py
        return self._news_data or []
    
    def check_player_injury_and_news(self, player_name: str) -> Dict[str, Any]:
        """Check if player has injury or relevant news"""
        player_lower = player_name.lower()
        result = {
            "has_injury": False,
            "injury_status": None,
            "injury_description": None,
            "has_news": False,
            "news_items": [],
            "warning_level": "none"  # none, yellow (GTD/questionable), red (OUT)
        }
        
        # Check direct injury status
        if player_lower in self._injury_data:
            injury = self._injury_data[player_lower]
            status = injury.get("status", "").lower()
            result["has_injury"] = True
            result["injury_status"] = injury.get("status", "").upper()
            result["injury_description"] = injury.get("description", "")
            
            if status in ["out"]:
                result["warning_level"] = "red"
            elif status in ["questionable", "doubtful", "day-to-day", "gtd"]:
                result["warning_level"] = "yellow"
        
        # Check news for injury mentions
        name_parts = player_lower.split()
        for news in self._news_data[:50]:
            title = (news.get("title", "") or "").lower()
            
            # Check if player mentioned
            mentioned = any(part in title for part in name_parts if len(part) > 2)
            
            if mentioned:
                # Check for injury keywords
                has_injury_keyword = any(kw in title for kw in INJURY_KEYWORDS)
                if has_injury_keyword:
                    result["has_news"] = True
                    result["news_items"].append({
                        "title": news.get("title", ""),
                        "link": news.get("link", "")
                    })
                    if result["warning_level"] == "none":
                        result["warning_level"] = "yellow"
        
        return result
    
    # ==================== MAIN ORCHESTRATION ====================
    
    async def process_single_prop(self, prop: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single prop through full pipeline"""
        player_name = prop.get("player_name", "")
        market = prop.get("market", "")
        line = prop.get("line", 0)
        
        result = {
            **prop,
            "bdl_player_id": None,
            "bdl_team": None,
            "position": None,
            "hit_rates": None,
            "injury_info": {"warning_level": "none"},
            "card_color": "standard",
            "processed_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Step 3: BallDontLie integration
        bdl_player = await self.search_bdl_player(player_name)
        if bdl_player:
            result["bdl_player_id"] = bdl_player.get("id")
            result["bdl_team"] = bdl_player.get("team", {}).get("abbreviation", "")
            result["position"] = bdl_player.get("position", "")
            
            games = await self.fetch_player_season_stats(bdl_player.get("id"))
            if games:
                hit_rates = self.calculate_hit_rates(games, market, line)
                result["hit_rates"] = hit_rates
                result["card_color"] = hit_rates.get("card_color", "standard")
        
        # Step 4: Tank01 safety check
        injury_info = self.check_player_injury_and_news(player_name)
        result["injury_info"] = injury_info
        
        # Override card color for injuries
        if injury_info["warning_level"] == "red":
            result["card_color"] = "red"
        elif injury_info["warning_level"] == "yellow" and result["card_color"] != "red":
            result["card_color"] = "yellow"
        
        return result
    
    async def run_deep_ingestion(self) -> Dict[str, Any]:
        """
        Main entry point: Two-Step Deep Ingestion
        """
        sync_start = datetime.now(timezone.utc)
        current_date = sync_start.strftime("%Y-%m-%d")
        logger.info(f"🚀 DEEP INGESTION STARTED - {current_date}")
        
        results = {
            "success": True,
            "sync_date": current_date,
            "sync_time": sync_start.isoformat(),
            "step1_events": 0,
            "step2_total_props": 0,
            "step2_unique_players": 0,
            "step3_stats_fetched": 0,
            "step4_injuries_found": 0,
            "step4_news_items": 0,
            "card_counts": {"green": 0, "yellow": 0, "red": 0, "standard": 0},
            "demons_found": 0,
            "errors": [],
            "duration": 0
        }
        
        try:
            # ===== STEP 1: EVENT DISCOVERY =====
            logger.info("=" * 60)
            logger.info("STEP 1: EVENT DISCOVERY")
            logger.info("=" * 60)
            events = await self.discover_all_events()
            results["step1_events"] = len(events)
            
            if not events:
                results["success"] = False
                results["errors"].append("No events found")
                return results
            
            # ===== STEP 2: DEEP PROP PULL =====
            logger.info("=" * 60)
            logger.info("STEP 2: DEEP PROP PULL (All Markets)")
            logger.info("=" * 60)
            
            all_props = []
            all_players: Set[str] = set()
            
            for event in events:
                event_id = event.get("id")
                if event_id:
                    odds_data = await self.deep_pull_event_odds(event_id, event)
                    if odds_data:
                        props = self.extract_all_player_props(odds_data)
                        all_props.extend(props)
                        for p in props:
                            all_players.add(p.get("player_name", ""))
                    
                    await asyncio.sleep(0.3)  # Rate limiting
            
            results["step2_total_props"] = len(all_props)
            results["step2_unique_players"] = len(all_players)
            logger.info(f"✓ STEP 2 COMPLETE: {len(all_props)} props, {len(all_players)} unique players")
            
            # ===== STEP 4: TANK01 SAFETY CHECK (before processing) =====
            logger.info("=" * 60)
            logger.info("STEP 4: TANK01 SAFETY CHECK")
            logger.info("=" * 60)
            
            injuries = await self.fetch_tank01_injuries()
            news = await self.fetch_tank01_news()
            results["step4_injuries_found"] = len(injuries)
            results["step4_news_items"] = len(news)
            
            # ===== STEP 3: BALLDONTLIE INTEGRATION =====
            logger.info("=" * 60)
            logger.info("STEP 3: BALLDONTLIE INTEGRATION")
            logger.info("=" * 60)
            
            # Deduplicate by player+market+line
            unique_props = {}
            for prop in all_props:
                key = f"{prop['player_name']}|{prop['market']}|{prop['line']}"
                if key not in unique_props:
                    unique_props[key] = prop
                else:
                    # Merge bookmaker prices
                    existing = unique_props[key]
                    if "all_prices" not in existing:
                        existing["all_prices"] = {existing["bookmaker"]: existing["price"]}
                    existing["all_prices"][prop["bookmaker"]] = prop["price"]
            
            logger.info(f"Processing {len(unique_props)} unique props...")
            
            # Process ALL props (no limit)
            processed_props = []
            batch_size = 50
            prop_list = list(unique_props.values())
            
            for i in range(0, len(prop_list), batch_size):
                batch = prop_list[i:i+batch_size]
                
                for prop in batch:
                    try:
                        processed = await self.process_single_prop(prop)
                        processed_props.append(processed)
                        
                        # Count cards
                        color = processed.get("card_color", "standard")
                        results["card_counts"][color] = results["card_counts"].get(color, 0) + 1
                        
                        if processed.get("hit_rates", {}).get("is_demon"):
                            results["demons_found"] += 1
                        
                        if processed.get("bdl_player_id"):
                            results["step3_stats_fetched"] += 1
                        
                        await asyncio.sleep(0.1)
                        
                    except Exception as e:
                        results["errors"].append(f"Prop error: {str(e)[:50]}")
                
                logger.info(f"  Processed {min(i+batch_size, len(prop_list))}/{len(prop_list)} props")
            
            # Store all demon cards
            if processed_props:
                await self.demon_cards.delete_many({})
                await self.demon_cards.insert_many(processed_props)
            
            self._last_sync = datetime.now(timezone.utc)
            
        except Exception as e:
            logger.error(f"Deep ingestion error: {e}")
            results["success"] = False
            results["errors"].append(str(e))
        
        results["duration"] = (datetime.now(timezone.utc) - sync_start).total_seconds()
        
        logger.info("=" * 60)
        logger.info(f"""
✅ DEEP INGESTION COMPLETE
   Duration: {results['duration']:.1f}s
   Events: {results['step1_events']}
   Props: {results['step2_total_props']}
   Unique Players: {results['step2_unique_players']}
   Stats Fetched: {results['step3_stats_fetched']}
   
   Card Distribution:
     🟢 Green (High):    {results['card_counts']['green']}
     🟡 Yellow (Caution): {results['card_counts']['yellow']}
     🔴 Red (Low/Out):   {results['card_counts']['red']}
     ⚪ Standard:        {results['card_counts']['standard']}
   
   Demons Found: {results['demons_found']}
""")
        logger.info("=" * 60)
        
        return results
    
    # ==================== DATA ACCESS ====================
    
    async def get_demon_cards(
        self,
        color: Optional[str] = None,
        market: Optional[str] = None,
        bookmaker: Optional[str] = None,
        player_name: Optional[str] = None,
        demons_only: bool = False
    ) -> List[Dict[str, Any]]:
        """Get processed demon cards with filters"""
        query = {}
        
        if color:
            query["card_color"] = color
        if market:
            query["market"] = market
        if bookmaker:
            query["bookmaker"] = bookmaker
        if player_name:
            query["player_name"] = {"$regex": player_name, "$options": "i"}
        if demons_only:
            query["hit_rates.is_demon"] = True
        
        cursor = self.demon_cards.find(query, {"_id": 0})
        cards = await cursor.to_list(5000)  # Higher limit for all players
        
        # Filter out invalid cards
        cards = [c for c in cards if c and c.get("player_name")]
        
        # Sort by color priority then hit rate
        color_priority = {"green": 0, "yellow": 1, "standard": 2, "red": 3}
        
        def sort_key(x):
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
        
        green_count = await self.demon_cards.count_documents({"card_color": "green"})
        yellow_count = await self.demon_cards.count_documents({"card_color": "yellow"})
        red_count = await self.demon_cards.count_documents({"card_color": "red"})
        demons_count = await self.demon_cards.count_documents({"hit_rates.is_demon": True})
        
        # Get unique players
        pipeline = [{"$group": {"_id": "$player_name"}}, {"$count": "total"}]
        unique_players = await self.demon_cards.aggregate(pipeline).to_list(1)
        players_count = unique_players[0]["total"] if unique_players else 0
        
        return {
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "events_cached": events_count,
            "total_cards": cards_count,
            "unique_players": players_count,
            "card_colors": {
                "green": green_count,
                "yellow": yellow_count,
                "red": red_count,
                "standard": cards_count - green_count - yellow_count - red_count
            },
            "demons_found": demons_count,
            "season": CURRENT_SEASON
        }
