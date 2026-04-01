"""
STATELESS TIER SERVICE - Open Door Policy Implementation
=========================================================
Fetches live data from NBA API/Odds Provider on-demand.
NO database caching, NO sync jobs, NO stale data.

Architecture:
- Request hits endpoint
- Service fetches LIVE from NBA API + Odds API
- Processes in-memory using GOD-TIER 4-Pillar Formula
- Returns unified props format directly to UI

This replaces the legacy sync-dependent tier_builder_service.py
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
import httpx
import os

logger = logging.getLogger(__name__)

# Minimum hit rate for Safe Haven (80%)
SAFE_HAVEN_MIN_HIT_RATE = 0.80

# Odds API configuration
ODDS_API_KEY = os.environ.get("THE_ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"


class StatelessTierService:
    """
    Stateless tier service that fetches live data on-demand.
    No database dependencies for core data - just live API calls.
    """
    
    def __init__(self, db=None):
        """
        Initialize service.
        DB is optional - only used for DvP rankings cache (not core data).
        """
        self.db = db
        self._dvp_cache = {}
    
    async def get_goblin_vault_live(self, limit: int = 10) -> Dict[str, Any]:
        """
        STATELESS Goblin Vault - fetches live data on every request.
        
        Flow:
        1. Fetch today's NBA player props from Odds API
        2. Fetch player game logs from NBA API
        3. Calculate hit rates in-memory
        4. Apply GOD-TIER 4-Pillar Formula
        5. Return top picks with unified props format
        """
        request_time = datetime.now(timezone.utc)
        
        try:
            # STEP 1: Fetch live odds/props from The Odds API
            live_props = await self._fetch_live_props()
            
            if not live_props:
                return {
                    "success": True,
                    "mode": "STATELESS",
                    "picks": [],
                    "picks_count": 0,
                    "message": "No live props available - markets may be closed",
                    "fetched_at": request_time.isoformat()
                }
            
            # STEP 2: For each player, fetch their game logs and calculate hit rates
            all_candidates = []
            
            for player_name, player_props in live_props.items():
                # Fetch player's recent game logs from NBA API
                game_logs = await self._fetch_player_game_logs(player_name)
                
                if not game_logs or len(game_logs) < 5:
                    continue
                
                # Calculate hit rates for each prop line
                for prop in player_props:
                    candidate = self._score_goblin_candidate(
                        player_name=player_name,
                        prop=prop,
                        game_logs=game_logs,
                        request_time=request_time
                    )
                    if candidate:
                        all_candidates.append(candidate)
            
            # STEP 3: Sort by vault score and deduplicate
            all_candidates.sort(key=lambda x: x["vault_score"], reverse=True)
            
            # Deduplicate - one pick per player
            seen_players = set()
            unique_picks = []
            for pick in all_candidates:
                if pick["player_name"] not in seen_players:
                    seen_players.add(pick["player_name"])
                    unique_picks.append(pick)
            
            top_picks = unique_picks[:limit]
            
            # STEP 4: Enrich with props array for unified format
            for pick in top_picks:
                pick["props"] = self._build_props_array(
                    pick["player_name"], 
                    live_props.get(pick["player_name"], []),
                    pick.get("game_logs_stats", {})
                )
            
            return {
                "success": True,
                "mode": "STATELESS",
                "picks_count": len(top_picks),
                "total_candidates": len(all_candidates),
                "picks": top_picks,
                "fetched_at": request_time.isoformat(),
                "algorithm": {
                    "name": "GOD-TIER 4-Pillar Formula (LIVE)",
                    "description": "Stateless live calculation with 80% hit rate bouncer",
                    "formula": "vault_score = (consistency * 0.50) + (vegas * 0.20) + (dvp * 0.15) + (context * 0.15)",
                    "data_source": "LIVE from NBA API + Odds API"
                }
            }
            
        except Exception as e:
            logger.error(f"[STATELESS] Goblin Vault error: {e}")
            return {
                "success": False,
                "mode": "STATELESS",
                "error": str(e),
                "picks": [],
                "fetched_at": request_time.isoformat()
            }
    
    async def _fetch_live_props(self) -> Dict[str, List[Dict]]:
        """
        Fetch live player props from The Odds API.
        Returns dict of {player_name: [props]}
        """
        if not ODDS_API_KEY:
            logger.warning("[STATELESS] No Odds API key configured")
            return {}
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                # Fetch NBA player props
                response = await client.get(
                    f"{ODDS_API_BASE}/sports/basketball_nba/events",
                    params={
                        "apiKey": ODDS_API_KEY,
                        "regions": "us",
                        "markets": "player_points,player_rebounds,player_assists,player_threes",
                        "oddsFormat": "american",
                        "includeMultipliers": "true"
                    }
                )
                
                if response.status_code != 200:
                    logger.error(f"[STATELESS] Odds API error: {response.status_code}")
                    return {}
                
                events = response.json()
                
                # Parse into player-centric format
                player_props = {}
                
                for event in events:
                    bookmakers = event.get("bookmakers", [])
                    home_team = event.get("home_team", "")
                    away_team = event.get("away_team", "")
                    
                    for bookmaker in bookmakers[:1]:  # Use first bookmaker
                        markets = bookmaker.get("markets", [])
                        
                        for market in markets:
                            market_key = market.get("key", "")
                            outcomes = market.get("outcomes", [])
                            
                            for outcome in outcomes:
                                player_name = outcome.get("description", "")
                                if not player_name:
                                    continue
                                
                                if player_name not in player_props:
                                    player_props[player_name] = []
                                
                                player_props[player_name].append({
                                    "market": market_key,
                                    "stat_type": self._market_to_stat(market_key),
                                    "line": outcome.get("point", 0),
                                    "direction": outcome.get("name", "Over").lower(),
                                    "price": outcome.get("price", -110),
                                    "home_team": home_team,
                                    "away_team": away_team
                                })
                
                return player_props
                
        except Exception as e:
            logger.error(f"[STATELESS] Fetch props error: {e}")
            return {}
    
    async def _fetch_player_game_logs(self, player_name: str) -> List[Dict]:
        """
        Fetch player's recent game logs from NBA API.
        Returns last 10 games with pts, reb, ast, etc.
        """
        try:
            from nba_api.stats.static import players as nba_players
            from nba_api.stats.endpoints import playergamelog
            import time
            
            # Find player ID
            all_players = nba_players.get_players()
            player_match = None
            
            name_lower = player_name.lower()
            for p in all_players:
                full_name = p.get("full_name", "").lower()
                if name_lower == full_name or name_lower in full_name:
                    player_match = p
                    break
            
            if not player_match:
                return []
            
            player_id = player_match.get("id")
            
            # Rate limit
            time.sleep(0.5)
            
            # Fetch game log
            log = playergamelog.PlayerGameLog(
                player_id=player_id,
                season="2025-26"
            )
            
            df = log.get_data_frames()[0]
            
            if df.empty:
                return []
            
            # Convert to list of dicts
            games = []
            for _, row in df.head(10).iterrows():
                games.append({
                    "game_id": row.get("Game_ID"),
                    "date": row.get("GAME_DATE"),
                    "pts": int(row.get("PTS", 0)),
                    "reb": int(row.get("REB", 0)),
                    "ast": int(row.get("AST", 0)),
                    "fg3m": int(row.get("FG3M", 0)),
                    "stl": int(row.get("STL", 0)),
                    "blk": int(row.get("BLK", 0)),
                    "min": int(row.get("MIN", 0))
                })
            
            return games
            
        except Exception as e:
            logger.debug(f"[STATELESS] Game logs error for {player_name}: {e}")
            return []
    
    def _score_goblin_candidate(
        self,
        player_name: str,
        prop: Dict,
        game_logs: List[Dict],
        request_time: datetime
    ) -> Optional[Dict]:
        """
        Score a single prop as a Goblin Vault candidate.
        Uses GOD-TIER 4-Pillar Formula calculated in-memory.
        """
        stat_type = prop.get("stat_type", "")
        line = prop.get("line", 0)
        direction = prop.get("direction", "over")
        price = prop.get("price", -110)
        
        if not stat_type or line <= 0 or direction != "over":
            return None
        
        # Calculate hit rates from game logs
        stat_key = stat_type.lower()
        stat_map = {
            "PTS": "pts", "REB": "reb", "AST": "ast",
            "3PM": "fg3m", "STL": "stl", "BLK": "blk"
        }
        log_key = stat_map.get(stat_type, stat_type.lower())
        
        l10_games = game_logs[:10]
        l5_games = game_logs[:5]
        
        # Count hits (games where player exceeded line)
        l10_over = sum(1 for g in l10_games if g.get(log_key, 0) > line)
        l5_over = sum(1 for g in l5_games if g.get(log_key, 0) > line)
        
        l10_count = len(l10_games)
        l5_count = len(l5_games)
        
        if l10_count == 0:
            return None
        
        h10_rate = l10_over / l10_count
        h5_rate = l5_over / l5_count if l5_count > 0 else 0
        
        # Hard filter: must have 80%+ L10 hit rate
        if h10_rate < SAFE_HAVEN_MIN_HIT_RATE:
            return None
        
        # Calculate season average
        season_total = sum(g.get(log_key, 0) for g in l10_games)
        season_avg = season_total / l10_count if l10_count > 0 else 0
        
        # GOD-TIER 4-Pillar Formula
        pillar_1_consistency = (h10_rate * 0.6) + (h5_rate * 0.4)
        pillar_2_vegas = self._odds_to_implied_prob(price)
        pillar_3_dvp = 0.5  # Neutral placeholder
        pillar_4_context = 0.5  # Neutral placeholder
        
        vault_score = (
            (pillar_1_consistency * 0.50) +
            (pillar_2_vegas * 0.20) +
            (pillar_3_dvp * 0.15) +
            (pillar_4_context * 0.15)
        )
        
        # Gap calculation
        gap_pct = (season_avg - line) / season_avg if season_avg > 0 else 0
        final_ev_score = vault_score * (1 + max(0, gap_pct))
        
        return {
            "player_name": player_name,
            "team": prop.get("home_team") or prop.get("away_team", ""),
            "stat_type": stat_type,
            "direction": direction,
            "goblin_line": line,
            "line": line,
            "price": price,
            "h10_rate": round(h10_rate * 100, 1),
            "h5_rate": round(h5_rate * 100, 1),
            "h10_over": l10_over,
            "h10_games": l10_count,
            "h5_over": l5_over,
            "h5_games": l5_count,
            "season_avg": round(season_avg, 1),
            "l5_avg": round(sum(g.get(log_key, 0) for g in l5_games) / l5_count if l5_count > 0 else 0, 1),
            "l10_avg": round(season_avg, 1),
            "gap_pct": round(gap_pct * 100, 1),
            "vault_score": round(vault_score, 4),
            "final_ev_score": round(final_ev_score, 4),
            "is_goblin": True,
            "is_vault_pick": True,
            "data_source": "LIVE",
            "calculated_at": request_time.isoformat(),
            "game_logs_stats": {
                "pts": [g.get("pts", 0) for g in l10_games],
                "reb": [g.get("reb", 0) for g in l10_games],
                "ast": [g.get("ast", 0) for g in l10_games],
                "fg3m": [g.get("fg3m", 0) for g in l10_games]
            }
        }
    
    def _build_props_array(
        self,
        player_name: str,
        all_props: List[Dict],
        game_logs_stats: Dict
    ) -> List[Dict]:
        """
        Build unified props array for the player.
        This is what TacticalPlayerCard expects.
        """
        props = []
        
        for prop in all_props:
            stat_type = prop.get("stat_type", "")
            line = prop.get("line", 0)
            direction = prop.get("direction", "over")
            
            # Calculate hit rates from game logs
            log_key = {"PTS": "pts", "REB": "reb", "AST": "ast", "3PM": "fg3m"}.get(stat_type, "")
            values = game_logs_stats.get(log_key, [])
            
            if values:
                l10_over = sum(1 for v in values[:10] if v > line)
                l5_over = sum(1 for v in values[:5] if v > line)
                h10_rate = (l10_over / len(values[:10])) * 100 if values[:10] else 0
                h5_rate = (l5_over / len(values[:5])) * 100 if values[:5] else 0
                season_avg = sum(values) / len(values) if values else 0
            else:
                h10_rate = h5_rate = season_avg = 0
            
            props.append({
                "market": f"player_{stat_type.lower()}",
                "stat_type": stat_type,
                "stat_type_extracted": stat_type,
                "line": line,
                "direction": direction,
                "price": prop.get("price", -110),
                "h10_rate": round(h10_rate, 1),
                "h5_rate": round(h5_rate, 1),
                "season_avg": round(season_avg, 1),
                "l5_avg": round(sum(values[:5]) / 5 if len(values) >= 5 else 0, 1),
                "l10_avg": round(sum(values[:10]) / 10 if len(values) >= 10 else 0, 1),
                "is_goblin": h10_rate >= 80,
                "is_demon": False
            })
        
        return props
    
    def _market_to_stat(self, market: str) -> str:
        """Convert Odds API market key to stat type."""
        mapping = {
            "player_points": "PTS",
            "player_rebounds": "REB",
            "player_assists": "AST",
            "player_threes": "3PM"
        }
        return mapping.get(market, market.upper())
    
    def _odds_to_implied_prob(self, american_odds: int) -> float:
        """Convert American odds to implied probability (0-1)."""
        if american_odds >= 100:
            return 100 / (american_odds + 100)
        else:
            return abs(american_odds) / (abs(american_odds) + 100)


# Singleton instance
_stateless_service: Optional[StatelessTierService] = None


def get_stateless_tier_service(db=None) -> StatelessTierService:
    """Get or create stateless tier service."""
    global _stateless_service
    if _stateless_service is None:
        _stateless_service = StatelessTierService(db)
    return _stateless_service
