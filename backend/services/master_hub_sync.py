"""
NBA Master Hub Sync Service
============================
Daily sync job to populate nba_master_hub_2026 with:
- player_id, player_name, team_abbreviation
- photo_url (direct asset link)
- baseline_stats: L5_avg, L10_avg, season_avg for all prop categories

This ensures the frontend NEVER communicates with third-party APIs directly.
The database is always ready to serve the client instantly.

Scheduled to run daily at 0300 EST via CRON.
"""

import os
import logging
import asyncio
import httpx
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# External API configuration
BDL_API_KEY = os.environ.get("BALLDONTLIE_API_KEY", "")
BDL_BASE = "https://api.balldontlie.io/nba/v1"

# Prop categories to track
PROP_CATEGORIES = [
    "PTS",      # Points
    "REB",      # Rebounds  
    "AST",      # Assists
    "STL",      # Steals
    "BLK",      # Blocks
    "3PM",      # 3-Pointers Made
    "TO",       # Turnovers
    "PRA",      # Points + Rebounds + Assists
    "PR",       # Points + Rebounds
    "PA",       # Points + Assists
    "RA",       # Rebounds + Assists
    "BLST",     # Blocks + Steals
    "FGM",      # Field Goals Made
    "FTM",      # Free Throws Made
    "MIN",      # Minutes
]


class MasterHubSyncService:
    """
    Service to sync NBA player data to the master hub collection.
    Ensures all player data is pre-computed and ready for instant client access.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.master_hub = db.nba_master_hub_2026
        self.http_client = None
    
    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self.http_client is None:
            self.http_client = httpx.AsyncClient(timeout=30.0)
        return self.http_client
    
    async def _close_http_client(self):
        """Close HTTP client."""
        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None
    
    async def fetch_player_game_logs(self, player_id: int, num_games: int = 20) -> List[Dict]:
        """Fetch recent game logs for a player from BallDontLie API."""
        client = await self._get_http_client()
        
        try:
            # Get current season
            current_year = datetime.now().year
            season = current_year if datetime.now().month >= 10 else current_year - 1
            
            headers = {"Authorization": BDL_API_KEY} if BDL_API_KEY else {}
            response = await client.get(
                f"{BDL_BASE}/stats",
                params={
                    "player_ids[]": player_id,
                    "seasons[]": season,
                    "per_page": num_games
                },
                headers=headers
            )
            
            if response.status_code != 200:
                return []
            
            data = response.json()
            return data.get("data", [])
            
        except Exception as e:
            logger.error(f"[MASTER_HUB] Error fetching game logs for player {player_id}: {e}")
            return []
    
    def calculate_baseline_stats(self, game_logs: List[Dict]) -> Dict[str, Dict[str, float]]:
        """
        Calculate L5, L10, and season averages for all prop categories.
        
        Returns:
            {
                "PTS": {"l5_avg": 25.2, "l10_avg": 24.8, "season_avg": 23.5},
                "REB": {"l5_avg": 7.4, "l10_avg": 7.1, "season_avg": 6.9},
                ...
            }
        """
        if not game_logs:
            return {}
        
        baseline_stats = {}
        
        # Sort by date (most recent first)
        sorted_logs = sorted(
            game_logs, 
            key=lambda x: x.get("game", {}).get("date", ""),
            reverse=True
        )
        
        for category in PROP_CATEGORIES:
            stats = {"l5_avg": None, "l10_avg": None, "season_avg": None}
            
            # Extract values based on category
            values = []
            for log in sorted_logs:
                value = self._extract_stat_value(log, category)
                if value is not None:
                    values.append(value)
            
            if values:
                # L5 average (last 5 games)
                l5_values = values[:5]
                if l5_values:
                    stats["l5_avg"] = round(sum(l5_values) / len(l5_values), 1)
                
                # L10 average (last 10 games)
                l10_values = values[:10]
                if l10_values:
                    stats["l10_avg"] = round(sum(l10_values) / len(l10_values), 1)
                
                # Season average (all games)
                stats["season_avg"] = round(sum(values) / len(values), 1)
            
            baseline_stats[category] = stats
        
        return baseline_stats
    
    def _extract_stat_value(self, game_log: Dict, category: str) -> Optional[float]:
        """Extract stat value from game log based on category."""
        try:
            pts = game_log.get("pts", 0) or 0
            reb = game_log.get("reb", 0) or 0
            ast = game_log.get("ast", 0) or 0
            stl = game_log.get("stl", 0) or 0
            blk = game_log.get("blk", 0) or 0
            fg3m = game_log.get("fg3m", 0) or 0
            turnover = game_log.get("turnover", 0) or 0
            fgm = game_log.get("fgm", 0) or 0
            ftm = game_log.get("ftm", 0) or 0
            mins = game_log.get("min", "0")
            
            # Parse minutes (can be "32:45" format)
            if isinstance(mins, str) and ":" in mins:
                parts = mins.split(":")
                mins = int(parts[0]) + int(parts[1]) / 60
            else:
                mins = float(mins) if mins else 0
            
            category_map = {
                "PTS": pts,
                "REB": reb,
                "AST": ast,
                "STL": stl,
                "BLK": blk,
                "3PM": fg3m,
                "TO": turnover,
                "PRA": pts + reb + ast,
                "PR": pts + reb,
                "PA": pts + ast,
                "RA": reb + ast,
                "BLST": blk + stl,
                "FGM": fgm,
                "FTM": ftm,
                "MIN": mins,
            }
            
            return category_map.get(category)
            
        except Exception as e:
            logger.error(f"[MASTER_HUB] Error extracting stat {category}: {e}")
            return None
    
    async def run_full_sync(self, batch_size: int = 50) -> Dict[str, Any]:
        """
        Run a full sync of baseline stats for ACTIVE NBA ROSTER ONLY.
        
        Only syncs players already in nba_master_hub_2026 (~530 active players).
        This is designed to run daily at 0300 EST.
        """
        start_time = datetime.now(timezone.utc)
        logger.info(f"[MASTER_HUB] Starting baseline stats sync for active roster at {start_time.isoformat()}")
        
        results = {
            "started_at": start_time.isoformat(),
            "total_players": 0,
            "synced": 0,
            "failed": 0,
            "skipped": 0,
        }
        
        try:
            # Get all active players from master hub (NOT from external API)
            active_players = await self.master_hub.find(
                {"headshot_url": {"$ne": None, "$ne": ""}},  # Only players with photos
                {"_id": 1, "display_name": 1, "bdl_player_id": 1}
            ).to_list(600)
            
            results["total_players"] = len(active_players)
            logger.info(f"[MASTER_HUB] Found {len(active_players)} active players in roster")
            
            if not active_players:
                logger.warning("[MASTER_HUB] No active players found in roster")
                results["error"] = "No active players in roster"
                return results
            
            # Process in batches
            for i in range(0, len(active_players), batch_size):
                batch = active_players[i:i + batch_size]
                
                for player in batch:
                    success = await self._sync_player_stats(player)
                    if success:
                        results["synced"] += 1
                    else:
                        results["skipped"] += 1
                    
                    # Small delay to respect rate limits
                    await asyncio.sleep(0.05)
                
                logger.info(f"[MASTER_HUB] Processed {min(i + batch_size, len(active_players))}/{len(active_players)} players")
                
                # Small delay between batches
                await asyncio.sleep(0.5)
            
            # Update sync status
            await self.db.master_hub_sync_status.update_one(
                {"_id": "baseline_sync_status"},
                {
                    "$set": {
                        "last_sync": datetime.now(timezone.utc).isoformat(),
                        "players_synced": results["synced"],
                        "status": "success"
                    }
                },
                upsert=True
            )
            
        except Exception as e:
            logger.error(f"[MASTER_HUB] Full sync error: {e}")
            results["error"] = str(e)
            results["failed"] = results["total_players"] - results["synced"]
        
        finally:
            await self._close_http_client()
        
        end_time = datetime.now(timezone.utc)
        results["completed_at"] = end_time.isoformat()
        results["duration_seconds"] = (end_time - start_time).total_seconds()
        
        logger.info(f"[MASTER_HUB] Sync completed: {results}")
        return results
    
    async def _sync_player_stats(self, player_doc: Dict) -> bool:
        """
        Sync baseline stats for a single player from the active roster.
        
        Args:
            player_doc: Document from nba_master_hub_2026 with _id, display_name, bdl_player_id
        """
        try:
            player_name = player_doc.get("display_name", "")
            bdl_id = player_doc.get("bdl_player_id")
            
            if not player_name:
                return False
            
            # If we don't have BallDontLie ID, search for it
            if not bdl_id:
                bdl_id = await self._find_bdl_player_id(player_name)
                if not bdl_id:
                    return False
            
            # Fetch game logs
            game_logs = await self.fetch_player_game_logs(bdl_id, num_games=20)
            if not game_logs:
                return False
            
            # Calculate baseline stats
            baseline_stats = self.calculate_baseline_stats(game_logs)
            if not baseline_stats:
                return False
            
            # Update the player document
            result = await self.master_hub.update_one(
                {"_id": player_doc["_id"]},
                {"$set": {
                    "bdl_player_id": bdl_id,
                    "baseline_stats": baseline_stats,
                    "baseline_stats_updated_at": datetime.now(timezone.utc).isoformat(),
                }}
            )
            
            return result.modified_count > 0
            
        except Exception as e:
            logger.error(f"[MASTER_HUB] Error syncing stats for {player_doc.get('display_name')}: {e}")
            return False
    
    async def _find_bdl_player_id(self, player_name: str) -> Optional[int]:
        """Search BallDontLie API for player ID by name."""
        client = await self._get_http_client()
        
        try:
            # Extract last name for search
            name_parts = player_name.split()
            search_term = name_parts[-1] if name_parts else player_name
            
            headers = {"Authorization": BDL_API_KEY} if BDL_API_KEY else {}
            response = await client.get(
                f"{BDL_BASE}/players",
                params={"search": search_term, "per_page": 25},
                headers=headers
            )
            
            if response.status_code != 200:
                return None
            
            data = response.json()
            players = data.get("data", [])
            
            # Find exact match
            player_name_lower = player_name.lower()
            for p in players:
                full_name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip().lower()
                if full_name == player_name_lower:
                    return p.get("id")
                # Also try without Jr./Sr.
                if player_name_lower.replace(" jr.", "").replace(" sr.", "") == full_name:
                    return p.get("id")
                if full_name.replace(" jr.", "").replace(" sr.", "") == player_name_lower:
                    return p.get("id")
            
            return None
            
        except Exception as e:
            logger.error(f"[MASTER_HUB] Error finding BDL ID for {player_name}: {e}")
            return None


async def run_master_hub_sync():
    """Entry point for CRON job."""
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME", "test_database")
    
    if not mongo_url:
        logger.error("[MASTER_HUB] MONGO_URL not configured")
        return {"error": "Database not configured"}
    
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    
    service = MasterHubSyncService(db)
    return await service.run_full_sync()


# For direct execution (testing)
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(run_master_hub_sync())
    print(f"Sync result: {result}")
