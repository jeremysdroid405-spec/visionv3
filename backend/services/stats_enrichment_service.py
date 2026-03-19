"""
Stats Enrichment Service
========================
Extracted from demon_goblin_engine.py for modularity.

Handles stats fetching and enrichment from multiple sources:
- BallDontLie API (primary)
- BDL API (secondary)
- NBA.com API (tertiary fallback)
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
import asyncio
import httpx
import os
import logging

from motor.motor_asyncio import AsyncIOMotorDatabase
from thefuzz import fuzz

logger = logging.getLogger(__name__)

# API Configuration - BDL ONLY (Tank01 REMOVED)
BDL_BASE_URL = "https://api.balldontlie.io/v1"
BDL_API_KEY = os.environ.get("BDL_API_KEY")

CURRENT_SEASON = os.environ.get("NBA_SEASON", "2025")

# Check if NBA API is available
try:
    from nba_api.stats.endpoints import playergamelog
    from nba_api.stats.static import players as nba_players
    NBA_API_AVAILABLE = True
except ImportError:
    NBA_API_AVAILABLE = False


def sanitize_player_name(name: str) -> str:
    """Sanitize player name for matching."""
    import re
    name = re.sub(r'[^\w\s-]', '', name)
    name = ' '.join(name.split())
    return name.strip()


class StatsEnrichmentService:
    """
    Service for fetching and enriching player stats from multiple sources.
    
    Data Flow:
    1. Check MongoDB cache first
    2. Try BallDontLie API (primary)
    3. Try BDL API (secondary)
    4. Try NBA.com API (tertiary)
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.player_stats = db.dg_player_stats
        
        # In-memory cache for player ID mapping
        self._player_name_map: Dict[str, Dict] = {}
    
    async def enrich_props_with_stats(
        self,
        props: List[Dict],
        player_names: List[str],
        extract_stat_type_func,
        calculate_hit_rates_func
    ) -> List[Dict]:
        """
        Enrich props with hit rates from CACHED player stats in MongoDB.
        
        Reads from dg_player_stats collection (populated by sync_player_stats).
        Falls back to live API calls only if cache is empty.
        """
        logger.info(f"[STATS ENRICHMENT] Starting enrichment for {len(player_names)} players from cache...")
        
        player_stats_cache = {}
        cache_hits = 0
        api_fallbacks = 0
        
        # Batch load from MongoDB
        normalized_names = [sanitize_player_name(name) for name in player_names]
        cached_docs = await self.player_stats.find(
            {"normalized_name": {"$in": normalized_names}},
            {"_id": 0}
        ).to_list(None)
        
        # Build cache from MongoDB results
        for doc in cached_docs:
            player_stats_cache[doc.get("player_name")] = doc
            cache_hits += 1
        
        logger.info(f"[STATS ENRICHMENT] Loaded {cache_hits} players from MongoDB cache")
        
        # For players not in cache, try live API (fallback)
        missing_players = [name for name in player_names if name not in player_stats_cache]
        if missing_players:
            logger.info(f"[STATS ENRICHMENT] {len(missing_players)} players not in cache, fetching from API...")
            
            for player_name in missing_players[:20]:  # Limit API calls
                try:
                    stats = await self.fetch_player_season_stats(player_name)
                    if stats and stats.get("games"):
                        player_stats_cache[player_name] = stats
                        api_fallbacks += 1
                        
                        # Also save to cache for next time
                        doc = {
                            "player_name": player_name,
                            "normalized_name": sanitize_player_name(player_name),
                            "games": stats.get("games", []),
                            "total_games": len(stats.get("games", [])),
                            "source": stats.get("source", "api_fallback"),
                            "synced_at": datetime.now(timezone.utc).isoformat()
                        }
                        await self.player_stats.update_one(
                            {"normalized_name": doc["normalized_name"]},
                            {"$set": doc},
                            upsert=True
                        )
                except Exception as e:
                    logger.debug(f"[STATS] Error fetching stats for {player_name}: {e}")
                
                await asyncio.sleep(0.1)
        
        enriched_count = len(player_stats_cache)
        logger.info(f"[STATS ENRICHMENT] Total stats: {enriched_count} (cache: {cache_hits}, API: {api_fallbacks})")
        
        # Enrich props with hit rates
        for prop in props:
            player_name = prop.get("player_name")
            player_stats = player_stats_cache.get(player_name, {})
            
            if not player_stats:
                continue
            
            stat_type = extract_stat_type_func(prop.get("market", ""))
            line_value = prop.get("line", 0)
            
            if not stat_type or line_value <= 0:
                continue
            
            hit_rates = calculate_hit_rates_func(player_stats, stat_type, line_value)
            
            if hit_rates:
                prop["hit_rates"] = hit_rates
        
        return props
    
    async def fetch_player_season_stats(self, player_name: str) -> Dict[str, Any]:
        """
        Fetch a player's season stats with multi-source fallback.
        
        Order:
        1. BallDontLie API (primary)
        2. BDL API (secondary)
        3. NBA.com API (tertiary)
        """
        # Try BallDontLie first
        try:
            player_id = await self._get_bdl_player_id(player_name)
            if player_id:
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
                        data = response.json()
                        games = data.get("data", [])
                        
                        if games:
                            return {
                                "player_name": player_name,
                                "player_id": player_id,
                                "games": games,
                                "total_games": len(games),
                                "source": "balldontlie"
                            }
        except Exception as e:
            logger.debug(f"[BDL] Error fetching stats for {player_name}: {e}")
        
        # Fallback 1: BDL API
        logger.debug(f"[STATS] BallDontLie has no data for {player_name}, trying BDL...")
        tank_stats = await self._fetch_bdl_player_stats(player_name)
        if tank_stats and tank_stats.get("games"):
            return tank_stats
        
        # Fallback 2: NBA.com API
        logger.debug(f"[STATS] BDL has no data for {player_name}, trying NBA.com API...")
        nba_stats = self._fetch_nba_api_stats(player_name)
        if nba_stats and nba_stats.get("games"):
            return nba_stats
        
        return {}
    
    async def _get_bdl_player_id(self, player_name: str) -> Optional[int]:
        """Get BallDontLie player ID from name (with caching)."""
        if player_name in self._player_name_map:
            return self._player_name_map[player_name].get("id")
        
        try:
            url = f"{BDL_BASE_URL}/players"
            headers = {"Authorization": BDL_API_KEY}
            
            normalized_name = player_name.replace(".", "").strip()
            name_parts = normalized_name.split()
            first_name = name_parts[0] if name_parts else ""
            last_name = name_parts[-1] if len(name_parts) > 1 else ""
            
            search_terms = [
                first_name if first_name else normalized_name,
                normalized_name,
                last_name if last_name else normalized_name,
            ]
            
            async with httpx.AsyncClient() as client:
                for search_term in search_terms:
                    if not search_term:
                        continue
                    
                    params = {"search": search_term, "per_page": 100}
                    response = await client.get(url, params=params, headers=headers, timeout=10.0)
                    
                    if response.status_code != 200:
                        continue
                    
                    data = response.json()
                    players = data.get("data", [])
                    
                    if not players:
                        continue
                    
                    best_match = None
                    best_score = 0
                    
                    for player in players:
                        full_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
                        normalized_full = full_name.replace(".", "").strip()
                        
                        player_first = player.get('first_name', '').replace(".", "").strip().lower()
                        player_last = player.get('last_name', '').replace(".", "").strip().lower()
                        
                        exact_first = player_first == first_name.lower() if first_name else False
                        exact_last = player_last == last_name.lower() if last_name else False
                        starts_with_first = player_first.startswith(first_name.lower()) if first_name and len(first_name) >= 3 else False
                        
                        if exact_first and exact_last:
                            self._player_name_map[player_name] = player
                            return player.get("id")
                        
                        if exact_last and starts_with_first:
                            self._player_name_map[player_name] = player
                            return player.get("id")
                        
                        ratio = fuzz.ratio(normalized_name.lower(), normalized_full.lower())
                        partial = fuzz.partial_ratio(normalized_name.lower(), normalized_full.lower())
                        token_sort = fuzz.token_sort_ratio(normalized_name.lower(), normalized_full.lower())
                        
                        score = max(ratio, partial, token_sort)
                        
                        if exact_first:
                            score += 10
                        
                        if score > best_score:
                            best_score = score
                            best_match = player
                    
                    if best_match and best_score >= 80:
                        self._player_name_map[player_name] = best_match
                        return best_match.get("id")
                        
        except Exception as e:
            logger.debug(f"[BDL] Error searching for {player_name}: {e}")
        
        return None
    
    async def _fetch_bdl_player_stats(self, player_name: str) -> Dict[str, Any]:
        """
        Fetch player stats from BDL API.
        NOTE: Tank01 has been REMOVED. This now uses BDL exclusively.
        """
        try:
            # Search for player in BDL
            headers = {"Authorization": BDL_API_KEY}
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Search player
                search_url = f"{BDL_BASE_URL}/players"
                response = await client.get(
                    search_url,
                    params={"search": player_name},
                    headers=headers
                )
                
                if response.status_code != 200:
                    return {}
                
                data = response.json()
                players = data.get("data", [])
                if not players:
                    return {}
                
                player = players[0]
                player_id = player.get("id")
                
                if not player_id:
                    return {}
                
                # Get recent stats
                stats_url = f"{BDL_BASE_URL}/stats"
                response = await client.get(
                    stats_url,
                    params={"seasons[]": "2024", "player_ids[]": player_id, "per_page": 15},
                    headers=headers
                )
                
                if response.status_code != 200:
                    return {}
                
                stats_data = response.json()
                games = stats_data.get("data", [])
                
                if not games:
                    return {}
                
                # Filter to games actually played
                played_games = [g for g in games if g.get("min") and g.get("min") != "00"]
                
                # Format game logs
                game_logs = []
                for g in played_games[:15]:
                    game_logs.append({
                        "pts": g.get("pts", 0),
                        "reb": g.get("reb", 0),
                        "ast": g.get("ast", 0),
                        "stl": g.get("stl", 0),
                        "blk": g.get("blk", 0),
                        "fg3m": g.get("fg3m", 0),
                        "tov": g.get("turnover", 0),
                        "min": g.get("min", "0"),
                        "game_date": g.get("game", {}).get("date", "")
                    })
                
                return {"games": game_logs, "source": "bdl"}
                
        except Exception as e:
            logger.debug(f"[BDL] Error fetching stats for {player_name}: {e}")
            return {}
    
    def _fetch_nba_api_stats(self, player_name: str) -> Dict[str, Any]:
        """Fetch player stats from NBA.com official API as tertiary fallback."""
        if not NBA_API_AVAILABLE:
            return {}
        
        try:
            all_players = nba_players.get_players()
            
            player_match = None
            normalized_search = player_name.lower().strip()
            
            for p in all_players:
                if p['full_name'].lower() == normalized_search:
                    player_match = p
                    break
            
            if not player_match:
                for p in all_players:
                    if normalized_search in p['full_name'].lower():
                        player_match = p
                        break
            
            if not player_match:
                return {}
            
            player_id = player_match['id']
            
            import time
            time.sleep(0.6)
            
            from datetime import datetime
            now = datetime.now()
            if now.month >= 10:
                season_year = now.year
            else:
                season_year = now.year - 1
            current_season = f"{season_year}-{str(season_year + 1)[-2:]}"
            
            gamelog = playergamelog.PlayerGameLog(
                player_id=player_id,
                season=current_season,
                season_type_all_star='Regular Season'
            )
            df = gamelog.get_data_frames()[0]
            
            if df.empty:
                return {}
            
            games = []
            for _, row in df.iterrows():
                games.append({
                    "pts": row.get('PTS', 0),
                    "reb": row.get('REB', 0),
                    "ast": row.get('AST', 0),
                    "fg3m": row.get('FG3M', 0),
                    "blk": row.get('BLK', 0),
                    "stl": row.get('STL', 0),
                    "turnover": row.get('TOV', 0),
                    "game": {
                        "date": row.get('GAME_DATE', ''),
                        "matchup": row.get('MATCHUP', '')
                    }
                })
            
            logger.info(f"[NBA_API] Fetched {len(games)} games for {player_name}")
            
            return {
                "games": games,
                "player_name": player_name,
                "source": "nba_api"
            }
            
        except Exception as e:
            logger.debug(f"[NBA_API] Error fetching stats for {player_name}: {e}")
            return {}
