"""
Demon & Goblin Analytics Engine v3.0
=====================================

PrizePicks-Style Mimic System for NBA Player Props

Demon Icon (Red): Alternate lines with Odds >= +200 (Harder, High-Payout)
Goblin Icon (Green): Alternate lines with Odds <= -300 (Easier, High-Probability)

Triple-Pillar Integration:
1. The Odds API - All betting lines from DraftKings & FanDuel
2. BallDontLie API - Player stats for hit rate calculation
3. Tank01 API - Injury reports and player news
"""

import httpx
import logging
import os
import asyncio
from datetime import datetime, timezone, timedelta, time
from typing import Optional, Dict, List, Any, Set
from thefuzz import fuzz
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# ==================== API CONFIGURATION ====================

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "e1ae76ab21c34ee88ed552cffb4449fd")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

BDL_API_KEY = os.environ.get("BDL_API_KEY", "ad5544be-9969-434b-9389-2b7cf658c8e0")
BDL_BASE_URL = "https://api.balldontlie.io/v1"

TANK01_API_KEY = os.environ.get("TANK01_API_KEY", "402edbcac6mshd04997e7ca01d17p1879eajsn65ab176cdb1e")
TANK01_BASE = "https://tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com"

CURRENT_SEASON = "2025"  # 2025-26 NBA Season

# Demon & Goblin Thresholds (Based on American Odds)
DEMON_ODDS_THRESHOLD = 200    # +200 or higher = Demon (harder)
GOBLIN_ODDS_THRESHOLD = -300  # -300 or lower = Goblin (easier)

# Hit rate threshold for Goblin warning
GOBLIN_HIT_RATE_WARNING = 0.90  # 90% hit rate

# Comprehensive player prop markets
ALL_PLAYER_MARKETS = ",".join([
    "player_points", "player_rebounds", "player_assists", "player_threes",
    "player_blocks", "player_steals", "player_turnovers",
    "player_points_rebounds", "player_points_assists", "player_rebounds_assists",
    "player_points_rebounds_assists", "player_double_double", "player_triple_double",
    "player_first_basket",
    "alternate_player_points", "alternate_player_rebounds", 
    "alternate_player_assists", "alternate_player_threes",
])

TARGET_BOOKMAKERS = ["draftkings", "fanduel"]

INJURY_KEYWORDS = [
    "injury", "injured", "out", "questionable", "doubtful", "probable",
    "day-to-day", "GTD", "game time decision", "load management", "rest",
    "ankle", "knee", "hamstring", "back", "shoulder", "concussion", 
    "illness", "personal", "sore", "sprain", "strain"
]


class DemonGoblinEngine:
    """
    The Demon & Goblin Analytics Engine
    
    Classifies betting lines into:
    - Demons: High-payout lines (+200 or higher odds)
    - Goblins: High-probability lines (-300 or lower odds)
    
    Data organized hierarchically by player for easy navigation.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.events_cache = db.dg_events_cache
        self.odds_cache = db.dg_odds_cache
        self.player_data = db.dg_player_data
        self.stats_cache = db.dg_stats_cache
        self.sync_log = db.dg_sync_log
        
        # In-memory caches
        self._player_name_map: Dict[str, Any] = {}
        self._injury_data: Dict[str, Any] = {}
        self._news_data: List[Dict] = []
        self._last_sync: Optional[datetime] = None
        self._current_date: Optional[str] = None
    
    def get_current_date(self) -> str:
        """Auto-derive today's date from system clock"""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    # ==================== PILLAR 1: THE ODDS API ====================
    
    async def fetch_todays_events(self) -> List[Dict[str, Any]]:
        """Fetch all NBA events for today"""
        try:
            url = f"{ODDS_API_BASE}/sports/basketball_nba/events"
            params = {"apiKey": ODDS_API_KEY, "dateFormat": "iso"}
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=15.0)
                
                if response.status_code == 200:
                    events = response.json()
                    
                    # Filter to today's events only
                    today = self.get_current_date()
                    todays_events = [
                        e for e in events 
                        if e.get("commence_time", "").startswith(today)
                    ]
                    
                    # Store in cache
                    await self.events_cache.delete_many({})
                    for event in todays_events:
                        event["fetched_at"] = datetime.now(timezone.utc).isoformat()
                        await self.events_cache.insert_one(event)
                    
                    logger.info(f"[PILLAR 1] Found {len(todays_events)} events for {today}")
                    return todays_events
                    
        except Exception as e:
            logger.error(f"[PILLAR 1] Event fetch error: {e}")
        
        return []
    
    async def fetch_event_odds(self, event_id: str, event_info: Dict) -> Dict[str, Any]:
        """Fetch ALL odds for a specific event including alternates"""
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
                    odds_data["event_id"] = event_id
                    odds_data["fetched_at"] = datetime.now(timezone.utc).isoformat()
                    
                    # Count outcomes
                    total_outcomes = 0
                    for bm in odds_data.get("bookmakers", []):
                        for market in bm.get("markets", []):
                            total_outcomes += len(market.get("outcomes", []))
                    
                    logger.info(f"  [ODDS] {event_info.get('away_team')} @ {event_info.get('home_team')}: {total_outcomes} outcomes")
                    
                    # Store in cache
                    await self.odds_cache.update_one(
                        {"event_id": event_id},
                        {"$set": odds_data},
                        upsert=True
                    )
                    
                    return odds_data
                    
                elif response.status_code == 422:
                    # Fallback to basic markets
                    params["markets"] = "player_points,player_rebounds,player_assists,player_threes"
                    response = await client.get(url, params=params, timeout=30.0)
                    if response.status_code == 200:
                        return response.json()
                        
        except Exception as e:
            logger.error(f"[PILLAR 1] Odds fetch error for {event_id}: {e}")
        
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
            book_title = bookmaker.get("title")
            
            for market in bookmaker.get("markets", []):
                market_key = market.get("key", "")
                
                if not (market_key.startswith("player_") or market_key.startswith("alternate_")):
                    continue
                
                for outcome in market.get("outcomes", []):
                    player_name = outcome.get("description", "")
                    direction = outcome.get("name", "")  # Over/Under
                    line = outcome.get("point")
                    price = outcome.get("price")  # American odds
                    
                    if player_name and line is not None and price is not None:
                        # Classify as Demon or Goblin based on odds
                        is_demon = price >= DEMON_ODDS_THRESHOLD
                        is_goblin = price <= GOBLIN_ODDS_THRESHOLD
                        
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
                            "is_demon": is_demon,
                            "is_goblin": is_goblin,
                            "fetched_at": datetime.now(timezone.utc).isoformat()
                        })
        
        return props
    
    # ==================== PILLAR 2: BALLDONTLIE API ====================
    
    async def search_bdl_player(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Map player name to BallDontLie player data"""
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
            
        except Exception as e:
            logger.error(f"[PILLAR 2] BDL search error for {player_name}: {e}")
        
        return None
    
    async def fetch_player_season_stats(self, player_id: int) -> List[Dict[str, Any]]:
        """Fetch season stats for hit rate calculation"""
        try:
            # Check cache first
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
            logger.error(f"[PILLAR 2] Stats fetch error for player {player_id}: {e}")
        
        return []
    
    def calculate_hit_rates(self, games: List[Dict], market: str, line: float) -> Dict[str, Any]:
        """Calculate L5, L10, and Season hit rates"""
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
        
        return {
            "l5": l5,
            "l10": l10,
            "season": season,
            "trends": trends
        }
    
    # ==================== PILLAR 3: TANK01 API ====================
    
    async def fetch_injuries(self) -> Dict[str, Any]:
        """Fetch injury data from Tank01"""
        try:
            url = f"{TANK01_BASE}/getNBATeams"
            params = {"rosters": "true", "schedules": "false"}
            headers = {
                "X-RapidAPI-Key": TANK01_API_KEY,
                "X-RapidAPI-Host": "tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, headers=headers, timeout=30.0)
                
                if response.status_code == 200:
                    data = response.json()
                    teams = data.get("body", []) if isinstance(data, dict) else data
                    
                    injuries = {}
                    if isinstance(teams, list):
                        for team in teams:
                            roster = team.get("Roster", {})
                            if isinstance(roster, dict):
                                for player_id, player_data in roster.items():
                                    injury_info = player_data.get("injury", {})
                                    if injury_info and isinstance(injury_info, dict):
                                        status = injury_info.get("designation", "")
                                        if status:
                                            player_name = player_data.get("longName", "")
                                            injuries[player_name.lower()] = {
                                                "status": status,
                                                "description": injury_info.get("description", ""),
                                                "return_date": injury_info.get("injReturnDate", ""),
                                                "team": team.get("teamAbv", "")
                                            }
                    
                    self._injury_data = injuries
                    logger.info(f"[PILLAR 3] Found {len(injuries)} injured players")
                    return injuries
                    
                elif response.status_code == 429:
                    logger.warning("[PILLAR 3] Tank01 rate limited")
                    
        except Exception as e:
            logger.error(f"[PILLAR 3] Injury fetch error: {e}")
        
        return {}
    
    async def fetch_news(self) -> List[Dict[str, Any]]:
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
                        self._news_data = news_items[:100]
                        logger.info(f"[PILLAR 3] Fetched {len(news_items)} news items")
                        return news_items
                        
        except Exception as e:
            logger.error(f"[PILLAR 3] News fetch error: {e}")
        
        return []
    
    def get_player_injury_status(self, player_name: str) -> Dict[str, Any]:
        """Get injury status for a player"""
        player_lower = player_name.lower()
        result = {
            "has_injury": False,
            "injury_status": None,
            "injury_description": None,
            "has_news": False,
            "news_items": [],
            "warning_level": "none"  # none, questionable, out
        }
        
        # Check injury data
        if player_lower in self._injury_data:
            injury = self._injury_data[player_lower]
            status = injury.get("status", "").lower()
            result["has_injury"] = True
            result["injury_status"] = injury.get("status", "").upper()
            result["injury_description"] = injury.get("description", "")
            
            if status in ["out"]:
                result["warning_level"] = "out"
            elif status in ["questionable", "doubtful", "day-to-day", "gtd"]:
                result["warning_level"] = "questionable"
        
        # Check news for injury mentions
        name_parts = player_lower.split()
        for news in self._news_data[:50]:
            title = (news.get("title", "") or "").lower()
            mentioned = any(part in title for part in name_parts if len(part) > 2)
            
            if mentioned:
                has_injury_keyword = any(kw in title for kw in INJURY_KEYWORDS)
                if has_injury_keyword:
                    result["has_news"] = True
                    result["news_items"].append({
                        "title": news.get("title", ""),
                        "link": news.get("link", "")
                    })
                    if result["warning_level"] == "none":
                        result["warning_level"] = "questionable"
        
        return result
    
    # ==================== MAIN ORCHESTRATION ====================
    
    async def process_player_prop(self, prop: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single prop through all three pillars"""
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
            "has_goblin_warning": False,  # High hit rate + Questionable
            "processed_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Pillar 2: BallDontLie stats
        bdl_player = await self.search_bdl_player(player_name)
        if bdl_player:
            result["bdl_player_id"] = bdl_player.get("id")
            result["bdl_team"] = bdl_player.get("team", {}).get("abbreviation", "")
            result["position"] = bdl_player.get("position", "")
            
            games = await self.fetch_player_season_stats(bdl_player.get("id"))
            if games:
                hit_rates = self.calculate_hit_rates(games, market, line)
                result["hit_rates"] = hit_rates
        
        # Pillar 3: Injury check
        injury_info = self.get_player_injury_status(player_name)
        result["injury_info"] = injury_info
        
        # Special warning: Goblin with high hit rate but Questionable
        if prop.get("is_goblin") and result.get("hit_rates"):
            l10_hit_rate = result["hit_rates"].get("l10", {}).get("hit_rate", 0)
            if l10_hit_rate >= GOBLIN_HIT_RATE_WARNING and injury_info["warning_level"] == "questionable":
                result["has_goblin_warning"] = True
        
        return result
    
    async def run_full_sync(self) -> Dict[str, Any]:
        """Execute the full three-pillar sync"""
        sync_start = datetime.now(timezone.utc)
        self._current_date = self.get_current_date()
        
        logger.info("=" * 70)
        logger.info(f"DEMON & GOBLIN ENGINE v3.0 - SYNC STARTED")
        logger.info(f"Date: {self._current_date}")
        logger.info("=" * 70)
        
        results = {
            "success": True,
            "sync_date": self._current_date,
            "sync_time": sync_start.isoformat(),
            "events_count": 0,
            "total_props": 0,
            "unique_players": 0,
            "demons_count": 0,
            "goblins_count": 0,
            "stats_fetched": 0,
            "injuries_found": 0,
            "goblin_warnings": 0,
            "errors": [],
            "duration": 0
        }
        
        try:
            # ===== PILLAR 1: FETCH EVENTS AND ODDS =====
            logger.info("\n[PILLAR 1] Fetching events and odds from The Odds API...")
            
            events = await self.fetch_todays_events()
            results["events_count"] = len(events)
            
            if not events:
                # If no events today, fetch all upcoming events
                logger.warning("No events for today, fetching all upcoming events...")
                url = f"{ODDS_API_BASE}/sports/basketball_nba/events"
                params = {"apiKey": ODDS_API_KEY, "dateFormat": "iso"}
                
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, params=params, timeout=15.0)
                    if response.status_code == 200:
                        events = response.json()[:10]  # Take first 10 upcoming
                        results["events_count"] = len(events)
            
            all_props = []
            all_players: Set[str] = set()
            
            for event in events:
                event_id = event.get("id")
                if event_id:
                    odds_data = await self.fetch_event_odds(event_id, event)
                    if odds_data:
                        props = self.extract_player_props(odds_data)
                        all_props.extend(props)
                        for p in props:
                            all_players.add(p.get("player_name", ""))
                    
                    await asyncio.sleep(0.3)  # Rate limiting
            
            results["total_props"] = len(all_props)
            results["unique_players"] = len(all_players)
            results["demons_count"] = sum(1 for p in all_props if p.get("is_demon"))
            results["goblins_count"] = sum(1 for p in all_props if p.get("is_goblin"))
            
            logger.info(f"[PILLAR 1] Complete: {len(all_props)} props, {len(all_players)} players")
            logger.info(f"           Demons: {results['demons_count']}, Goblins: {results['goblins_count']}")
            
            # ===== PILLAR 3: FETCH INJURIES FIRST =====
            logger.info("\n[PILLAR 3] Fetching injury data from Tank01...")
            
            injuries = await self.fetch_injuries()
            await self.fetch_news()
            results["injuries_found"] = len(injuries)
            
            # ===== PILLAR 2: PROCESS STATS =====
            logger.info("\n[PILLAR 2] Processing stats from BallDontLie...")
            
            # Deduplicate by player+market+line+direction
            unique_props = {}
            for prop in all_props:
                key = f"{prop['player_name']}|{prop['market']}|{prop['line']}|{prop['direction']}"
                if key not in unique_props:
                    unique_props[key] = prop
                else:
                    # Merge bookmaker data
                    existing = unique_props[key]
                    if "all_prices" not in existing:
                        existing["all_prices"] = {existing["bookmaker"]: existing["price"]}
                    existing["all_prices"][prop["bookmaker"]] = prop["price"]
            
            # Process all unique props
            processed_props = []
            prop_list = list(unique_props.values())
            batch_size = 50
            
            for i in range(0, len(prop_list), batch_size):
                batch = prop_list[i:i+batch_size]
                
                for prop in batch:
                    try:
                        processed = await self.process_player_prop(prop)
                        processed_props.append(processed)
                        
                        if processed.get("bdl_player_id"):
                            results["stats_fetched"] += 1
                        
                        if processed.get("has_goblin_warning"):
                            results["goblin_warnings"] += 1
                        
                        await asyncio.sleep(0.1)
                        
                    except Exception as e:
                        results["errors"].append(f"Prop error: {str(e)[:50]}")
                
                logger.info(f"  Processed {min(i+batch_size, len(prop_list))}/{len(prop_list)} props")
            
            # ===== STORE RESULTS GROUPED BY PLAYER =====
            logger.info("\n[STORAGE] Organizing data by player...")
            
            # Group props by player
            player_data = {}
            for prop in processed_props:
                player_name = prop.get("player_name", "Unknown")
                
                if player_name not in player_data:
                    player_data[player_name] = {
                        "player_name": player_name,
                        "team": prop.get("bdl_team", ""),
                        "position": prop.get("position", ""),
                        "injury_info": prop.get("injury_info", {}),
                        "props": [],
                        "demons": [],
                        "goblins": [],
                        "has_goblin_warning": False
                    }
                
                player_data[player_name]["props"].append(prop)
                
                if prop.get("is_demon"):
                    player_data[player_name]["demons"].append(prop)
                
                if prop.get("is_goblin"):
                    player_data[player_name]["goblins"].append(prop)
                
                if prop.get("has_goblin_warning"):
                    player_data[player_name]["has_goblin_warning"] = True
            
            # Store in MongoDB
            await self.player_data.delete_many({})
            if player_data:
                await self.player_data.insert_many(list(player_data.values()))
            
            # Log sync result
            await self.sync_log.insert_one({
                "sync_date": self._current_date,
                "sync_time": sync_start.isoformat(),
                "results": results,
                "completed_at": datetime.now(timezone.utc).isoformat()
            })
            
            self._last_sync = datetime.now(timezone.utc)
            
        except Exception as e:
            logger.error(f"Sync error: {e}")
            results["success"] = False
            results["errors"].append(str(e))
        
        results["duration"] = (datetime.now(timezone.utc) - sync_start).total_seconds()
        
        logger.info("\n" + "=" * 70)
        logger.info(f"""
DEMON & GOBLIN SYNC COMPLETE
============================
Duration: {results['duration']:.1f}s
Date: {results['sync_date']}

PILLAR 1 - THE ODDS API:
  Events: {results['events_count']}
  Total Props: {results['total_props']}
  Unique Players: {results['unique_players']}
  
CLASSIFICATION:
  Demons (+200 or higher): {results['demons_count']}
  Goblins (-300 or lower): {results['goblins_count']}
  
PILLAR 2 - BALLDONTLIE:
  Stats Fetched: {results['stats_fetched']}
  
PILLAR 3 - TANK01:
  Injuries Found: {results['injuries_found']}
  Goblin Warnings: {results['goblin_warnings']}
""")
        logger.info("=" * 70)
        
        return results
    
    # ==================== DATA ACCESS ====================
    
    async def get_all_players(self) -> List[Dict[str, Any]]:
        """Get all players with their props (collapsed view data)"""
        cursor = self.player_data.find({}, {"_id": 0})
        players = await cursor.to_list(1000)
        
        # Sort: Players with Demons/Goblins first
        def sort_key(p):
            has_special = len(p.get("demons", [])) + len(p.get("goblins", []))
            return (-has_special, p.get("player_name", ""))
        
        players.sort(key=sort_key)
        return players
    
    async def get_player_detail(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Get full detail for a specific player (expanded view)"""
        player = await self.player_data.find_one(
            {"player_name": {"$regex": player_name, "$options": "i"}},
            {"_id": 0}
        )
        
        if player:
            # Sort props: Demons first, then Goblins, then rest by hit rate
            props = player.get("props", [])
            
            def prop_sort_key(p):
                priority = 2  # Standard
                if p.get("is_demon"):
                    priority = 0
                elif p.get("is_goblin"):
                    priority = 1
                
                hit_rate = 0
                if p.get("hit_rates") and p.get("hit_rates", {}).get("l10"):
                    hit_rate = p.get("hit_rates", {}).get("l10", {}).get("hit_rate", 0) or 0
                
                return (priority, -hit_rate)
            
            props.sort(key=prop_sort_key)
            player["props"] = props
        
        return player
    
    async def get_all_demons(self) -> List[Dict[str, Any]]:
        """Get all Demon lines across all players"""
        players = await self.player_data.find({}, {"_id": 0}).to_list(1000)
        
        demons = []
        for player in players:
            for demon in player.get("demons", []):
                demon["player_team"] = player.get("team", "")
                demon["player_injury"] = player.get("injury_info", {})
                demons.append(demon)
        
        # Sort by price (highest odds first)
        demons.sort(key=lambda x: x.get("price", 0), reverse=True)
        return demons
    
    async def get_all_goblins(self) -> List[Dict[str, Any]]:
        """Get all Goblin lines across all players"""
        players = await self.player_data.find({}, {"_id": 0}).to_list(1000)
        
        goblins = []
        for player in players:
            for goblin in player.get("goblins", []):
                goblin["player_team"] = player.get("team", "")
                goblin["player_injury"] = player.get("injury_info", {})
                goblin["has_warning"] = player.get("has_goblin_warning", False)
                goblins.append(goblin)
        
        # Sort by hit rate (highest first)
        def sort_key(g):
            hit_rate = 0
            if g.get("hit_rates") and g.get("hit_rates", {}).get("l10"):
                hit_rate = g.get("hit_rates", {}).get("l10", {}).get("hit_rate", 0) or 0
            return -hit_rate
        
        goblins.sort(key=sort_key)
        return goblins
    
    async def get_sync_status(self) -> Dict[str, Any]:
        """Get current sync status"""
        players_count = await self.player_data.count_documents({})
        
        # Count demons and goblins
        pipeline = [
            {"$project": {
                "demons_count": {"$size": {"$ifNull": ["$demons", []]}},
                "goblins_count": {"$size": {"$ifNull": ["$goblins", []]}},
                "props_count": {"$size": {"$ifNull": ["$props", []]}}
            }},
            {"$group": {
                "_id": None,
                "total_demons": {"$sum": "$demons_count"},
                "total_goblins": {"$sum": "$goblins_count"},
                "total_props": {"$sum": "$props_count"}
            }}
        ]
        
        agg_result = await self.player_data.aggregate(pipeline).to_list(1)
        counts = agg_result[0] if agg_result else {"total_demons": 0, "total_goblins": 0, "total_props": 0}
        
        # Get last sync log
        last_sync = await self.sync_log.find_one({}, sort=[("sync_time", -1)])
        
        return {
            "last_sync": self._last_sync.isoformat() if self._last_sync else (last_sync.get("sync_time") if last_sync else None),
            "sync_date": self._current_date or self.get_current_date(),
            "unique_players": players_count,
            "total_props": counts.get("total_props", 0),
            "demons_count": counts.get("total_demons", 0),
            "goblins_count": counts.get("total_goblins", 0),
            "season": CURRENT_SEASON
        }
    
    async def search_players(self, query: str) -> List[Dict[str, Any]]:
        """Search for players by name"""
        cursor = self.player_data.find(
            {"player_name": {"$regex": query, "$options": "i"}},
            {"_id": 0}
        )
        return await cursor.to_list(50)
