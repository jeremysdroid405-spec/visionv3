"""
BDL Enhanced Data Service
=========================
Integrates high-value BDL endpoints:
1. /player_injuries - Injury reports for context badges
2. /lineups - Starting lineup data
3. /stats/advanced - PIE, ratings, advanced metrics
4. /v2/odds/player_props - Player prop odds from sportsbooks

These endpoints enhance the app's analytics capabilities.
"""

import logging
import asyncio
from typing import Dict, Optional, List, Any
from datetime import datetime, timezone, timedelta
from motor.motor_asyncio import AsyncIOMotorDatabase
import httpx
import os

logger = logging.getLogger(__name__)

BDL_API_KEY = os.environ.get("BDL_API_KEY")
BDL_BASE_URL = "https://api.balldontlie.io/v1"


class BDLEnhancedDataService:
    """
    Service for fetching enhanced data from BDL endpoints.
    
    Collections:
    - bdl_injuries: Current injury reports
    - bdl_lineups: Starting lineups by game
    - bdl_advanced_stats: Advanced player metrics
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self._last_injuries_sync: Optional[datetime] = None
        self._injuries_cache: List[Dict] = []
    
    async def _make_request(self, endpoint: str, params: Dict = None, version: str = "v1") -> Optional[Dict]:
        """Make authenticated request to BDL API."""
        base = f"https://api.balldontlie.io/{version}"
        url = f"{base}{endpoint}"
        headers = {"Authorization": BDL_API_KEY}
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(url, headers=headers, params=params)
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 429:
                    wait_time = int(response.headers.get("Retry-After", 5))
                    logger.warning(f"[BDL_ENHANCED] Rate limited, waiting {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    return await self._make_request(endpoint, params, version)
                else:
                    logger.error(f"[BDL_ENHANCED] {endpoint} returned {response.status_code}: {response.text[:200]}")
                    return None
            except Exception as e:
                logger.error(f"[BDL_ENHANCED] Request error: {e}")
                return None
    
    # ==================== PLAYER INJURIES ====================
    
    async def sync_injuries(self) -> Dict[str, Any]:
        """
        Fetch current injury reports from BDL.
        Updates bdl_injuries collection and context engine.
        
        Returns: {success: bool, injuries_count: int, players_updated: int}
        """
        logger.info("[BDL_ENHANCED] Syncing player injuries...")
        
        data = await self._make_request("/player_injuries")
        if not data:
            return {"success": False, "error": "Failed to fetch injuries"}
        
        injuries = data.get("data", [])
        logger.info(f"[BDL_ENHANCED] Fetched {len(injuries)} injury reports")
        
        # Clear old injuries
        await self.db.bdl_injuries.delete_many({})
        
        # Store new injuries
        injury_docs = []
        players_with_injuries = []
        
        for inj in injuries:
            player = inj.get("player", {})
            player_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
            bdl_id = player.get("id")
            
            # Determine severity based on status
            status = inj.get("status", "").lower()
            if "out for season" in status:
                severity = "season_ending"
            elif "out" in status:
                severity = "out"
            elif "doubtful" in status:
                severity = "doubtful"
            elif "questionable" in status:
                severity = "questionable"
            elif "probable" in status or "day-to-day" in status:
                severity = "probable"
            else:
                severity = "unknown"
            
            doc = {
                "player_name": player_name,
                "bdl_id": bdl_id,
                "team": player.get("team", {}).get("abbreviation"),
                "status": inj.get("status"),
                "severity": severity,
                "injury_type": inj.get("comment"),
                "return_date": inj.get("return_date"),
                "synced_at": datetime.now(timezone.utc)
            }
            injury_docs.append(doc)
            
            # Track for context engine update
            if severity in ["out", "doubtful", "season_ending"]:
                players_with_injuries.append({
                    "name": player_name,
                    "bdl_id": bdl_id,
                    "severity": severity,
                    "status": inj.get("status")
                })
        
        if injury_docs:
            await self.db.bdl_injuries.insert_many(injury_docs)
        
        # Update context engine with injury flags
        context_updates = 0
        for injured in players_with_injuries:
            # Set "deep_water" badge for injured players
            result = await self.db.nba_context_engine.update_one(
                {"player_name": injured["name"]},
                {
                    "$set": {
                        "deep_water": True,
                        "deep_water_reason": f"Injury: {injured['status']}",
                        "injury_status": injured["status"],
                        "injury_severity": injured["severity"],
                        "updated_at": datetime.now(timezone.utc)
                    }
                },
                upsert=True
            )
            if result.modified_count > 0 or result.upserted_id:
                context_updates += 1
        
        # Cache injuries
        self._injuries_cache = injury_docs
        self._last_injuries_sync = datetime.now(timezone.utc)
        
        logger.info(f"[BDL_ENHANCED] Injuries sync complete: {len(injuries)} injuries, {context_updates} context updates")
        
        return {
            "success": True,
            "injuries_count": len(injuries),
            "players_updated": context_updates,
            "synced_at": datetime.now(timezone.utc).isoformat()
        }
    
    async def get_player_injury(self, player_name: str) -> Optional[Dict]:
        """Get injury status for a specific player."""
        injury = await self.db.bdl_injuries.find_one(
            {"player_name": {"$regex": player_name, "$options": "i"}},
            {"_id": 0}
        )
        return injury
    
    async def get_all_injuries(self) -> List[Dict]:
        """Get all current injuries."""
        injuries = await self.db.bdl_injuries.find(
            {},
            {"_id": 0}
        ).to_list(100)
        return injuries
    
    # ==================== ADVANCED STATS ====================
    
    async def sync_advanced_stats(self, player_ids: List[int] = None) -> Dict[str, Any]:
        """
        Fetch advanced stats for players.
        
        Advanced stats include:
        - PIE (Player Impact Estimate)
        - Net Rating
        - Offensive/Defensive Ratings
        - Pace
        
        Args:
            player_ids: List of BDL player IDs. If None, syncs for all players in master hub.
        """
        logger.info("[BDL_ENHANCED] Syncing advanced stats...")
        
        # Get player IDs from master hub if not provided
        if not player_ids:
            players = await self.db.nba_master_hub_2026.find(
                {"bdl_id": {"$exists": True}},
                {"bdl_id": 1}
            ).to_list(500)
            player_ids = [p["bdl_id"] for p in players if p.get("bdl_id")]
        
        if not player_ids:
            return {"success": False, "error": "No player IDs to sync"}
        
        logger.info(f"[BDL_ENHANCED] Syncing advanced stats for {len(player_ids)} players...")
        
        synced = 0
        failed = 0
        
        # Process in batches (API allows multiple player_ids)
        batch_size = 25
        for i in range(0, len(player_ids), batch_size):
            batch = player_ids[i:i+batch_size]
            
            # Build params with array notation
            params = {"seasons[]": 2025, "per_page": 100}
            for pid in batch:
                params[f"player_ids[]"] = pid
            
            data = await self._make_request("/stats/advanced", params)
            
            if data:
                stats = data.get("data", [])
                
                # Group stats by player_id
                player_stats = {}
                for stat in stats:
                    pid = stat.get("player", {}).get("id")
                    if pid:
                        if pid not in player_stats:
                            player_stats[pid] = []
                        player_stats[pid].append(stat)
                
                # Calculate season averages and update master hub
                for pid, games in player_stats.items():
                    if not games:
                        continue
                    
                    # Average PIE and net rating across games
                    pie_values = [g.get("pie") for g in games if g.get("pie") is not None]
                    net_values = [g.get("net_rating") for g in games if g.get("net_rating") is not None]
                    
                    advanced = {
                        "pie": round(sum(pie_values) / len(pie_values), 3) if pie_values else None,
                        "net_rating": round(sum(net_values) / len(net_values), 1) if net_values else None,
                        "games_counted": len(games),
                        "synced_at": datetime.now(timezone.utc).isoformat()
                    }
                    
                    await self.db.nba_master_hub_2026.update_one(
                        {"bdl_id": pid},
                        {"$set": {"advanced_stats": advanced}}
                    )
                    synced += 1
            else:
                failed += len(batch)
            
            # Rate limit protection
            await asyncio.sleep(0.5)
        
        logger.info(f"[BDL_ENHANCED] Advanced stats sync: {synced} synced, {failed} failed")
        
        return {
            "success": True,
            "players_synced": synced,
            "players_failed": failed,
            "synced_at": datetime.now(timezone.utc).isoformat()
        }
    
    # ==================== LINEUPS ====================
    
    async def sync_lineups_for_games(self, game_ids: List[int]) -> Dict[str, Any]:
        """
        Fetch starting lineups for specific games.
        
        Note: Requires game_ids from BDL (not from The Odds API).
        """
        logger.info(f"[BDL_ENHANCED] Fetching lineups for {len(game_ids)} games...")
        
        lineups_fetched = 0
        
        for game_id in game_ids:
            params = {"game_ids[]": game_id}
            data = await self._make_request("/lineups", params)
            
            if data:
                lineups = data.get("data", [])
                for lineup in lineups:
                    # Store lineup
                    doc = {
                        "game_id": game_id,
                        "team_id": lineup.get("team", {}).get("id"),
                        "team_name": lineup.get("team", {}).get("full_name"),
                        "players": [
                            {
                                "bdl_id": p.get("id"),
                                "name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
                                "position": p.get("position")
                            }
                            for p in lineup.get("players", [])
                        ],
                        "synced_at": datetime.now(timezone.utc)
                    }
                    
                    await self.db.bdl_lineups.update_one(
                        {"game_id": game_id, "team_id": doc["team_id"]},
                        {"$set": doc},
                        upsert=True
                    )
                    lineups_fetched += 1
            
            await asyncio.sleep(0.3)
        
        return {
            "success": True,
            "lineups_fetched": lineups_fetched,
            "synced_at": datetime.now(timezone.utc).isoformat()
        }
    
    async def get_today_games(self) -> List[Dict]:
        """Get today's games from BDL to get game_ids."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        data = await self._make_request("/games", {"dates[]": today})
        if data:
            return data.get("data", [])
        return []
    
    async def sync_today_lineups(self) -> Dict[str, Any]:
        """Fetch lineups for today's games."""
        games = await self.get_today_games()
        
        if not games:
            return {"success": True, "message": "No games today", "lineups_fetched": 0}
        
        game_ids = [g.get("id") for g in games if g.get("id")]
        logger.info(f"[BDL_ENHANCED] Found {len(game_ids)} games today, fetching lineups...")
        
        return await self.sync_lineups_for_games(game_ids)
    
    # ==================== PLAYER PROPS ODDS ====================
    
    async def get_player_props_for_game(self, game_id: int) -> Optional[Dict]:
        """
        Fetch player prop odds from BDL for a specific game.
        
        This provides odds from DraftKings, FanDuel, and other sportsbooks.
        Could be used to compare with PrizePicks lines.
        """
        data = await self._make_request(f"/odds/player_props", {"game_id": game_id}, version="v2")
        return data
    
    async def sync_player_props(self) -> Dict[str, Any]:
        """
        Fetch player prop odds for today's games.
        Stores in bdl_player_props collection.
        """
        games = await self.get_today_games()
        
        if not games:
            return {"success": True, "message": "No games today", "props_fetched": 0}
        
        logger.info(f"[BDL_ENHANCED] Fetching player props for {len(games)} games...")
        
        total_props = 0
        
        for game in games:
            game_id = game.get("id")
            if not game_id:
                continue
            
            data = await self.get_player_props_for_game(game_id)
            
            if data and data.get("data"):
                props = data.get("data", [])
                
                for prop in props:
                    player = prop.get("player", {})
                    player_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
                    
                    doc = {
                        "game_id": game_id,
                        "player_name": player_name,
                        "bdl_id": player.get("id"),
                        "market": prop.get("market"),
                        "line": prop.get("line"),
                        "over_odds": prop.get("over_odds"),
                        "under_odds": prop.get("under_odds"),
                        "sportsbook": prop.get("sportsbook"),
                        "synced_at": datetime.now(timezone.utc)
                    }
                    
                    await self.db.bdl_player_props.update_one(
                        {
                            "game_id": game_id,
                            "bdl_id": player.get("id"),
                            "market": prop.get("market"),
                            "sportsbook": prop.get("sportsbook")
                        },
                        {"$set": doc},
                        upsert=True
                    )
                    total_props += 1
            
            await asyncio.sleep(0.5)
        
        logger.info(f"[BDL_ENHANCED] Player props sync: {total_props} props stored")
        
        return {
            "success": True,
            "props_fetched": total_props,
            "games_processed": len(games),
            "synced_at": datetime.now(timezone.utc).isoformat()
        }
    
    # ==================== GASSED BADGE LOGIC ====================
    
    async def check_gassed_status(self, player_name: str, bdl_id: int = None) -> Dict[str, Any]:
        """
        Check if player should have "gassed" badge.
        
        Gassed criteria:
        - Played 3+ games in last 5 days (back-to-back-to-back)
        - High minutes in recent games (36+ avg)
        - Recent injury concern
        """
        # Check recent game schedule from BDL stats
        if bdl_id:
            # Get recent games
            data = await self._make_request("/stats", {
                "player_ids[]": bdl_id,
                "seasons[]": 2025,
                "per_page": 10
            })
            
            if data:
                stats = data.get("data", [])
                
                # Check game dates
                recent_dates = []
                total_minutes = 0
                
                for stat in stats[:5]:
                    game = stat.get("game", {})
                    game_date = game.get("date")
                    minutes = stat.get("min")
                    
                    if game_date:
                        recent_dates.append(game_date)
                    
                    if minutes:
                        # Parse minutes (format: "MM:SS" or just minutes)
                        try:
                            if ":" in str(minutes):
                                mins = int(minutes.split(":")[0])
                            else:
                                mins = int(minutes)
                            total_minutes += mins
                        except:
                            pass
                
                # Check for gassed conditions
                avg_minutes = total_minutes / len(stats[:5]) if stats else 0
                
                # Count games in last 5 days
                today = datetime.now(timezone.utc).date()
                games_last_5_days = 0
                for date_str in recent_dates[:5]:
                    try:
                        game_date = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
                        if (today - game_date).days <= 5:
                            games_last_5_days += 1
                    except:
                        pass
                
                is_gassed = (games_last_5_days >= 3) or (avg_minutes >= 36)
                
                return {
                    "is_gassed": is_gassed,
                    "games_last_5_days": games_last_5_days,
                    "avg_minutes_l5": round(avg_minutes, 1),
                    "reason": f"{games_last_5_days} games in 5 days, {round(avg_minutes, 1)} min avg" if is_gassed else None
                }
        
        return {"is_gassed": False, "reason": None}


# Singleton instance
_enhanced_service: Optional[BDLEnhancedDataService] = None


def get_bdl_enhanced_service(db: AsyncIOMotorDatabase) -> BDLEnhancedDataService:
    """Get or create the BDL enhanced data service singleton."""
    global _enhanced_service
    if _enhanced_service is None:
        _enhanced_service = BDLEnhancedDataService(db)
    return _enhanced_service
