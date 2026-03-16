"""
NBA Master Hub Sync Service
============================
Daily sync job to populate nba_master_hub_2026 with:
- player_id, player_name, team_abbreviation
- photo_url (direct asset link)
- baseline_stats: L5_avg, L10_avg, season_avg for all prop categories

Data Source: Tank01 Fantasy Stats API (RapidAPI)
Scheduled to run daily at 0300 EST via CRON.

NOTE: BallDontLie API integration has been DEPRECATED and removed.
All stats now come from Tank01's reliable game log data.
"""

import os
import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

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
]


class MasterHubSyncService:
    """
    Service to sync NBA player data to the master hub collection.
    Uses Tank01 Fantasy Stats API for all stats data.
    
    DEPRECATED: BallDontLie integration has been removed.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.master_hub = db.nba_master_hub_2026
    
    async def run_full_sync(self) -> Dict[str, Any]:
        """
        Run full master hub sync using Tank01 API.
        
        This is the primary sync method called by the CRON job.
        """
        from services.tank01_stats_service import run_tank01_sync
        
        logger.info("[MASTER_HUB] Starting full sync via Tank01...")
        
        start_time = datetime.now(timezone.utc)
        result = await run_tank01_sync(self.db)
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        
        logger.info(f"[MASTER_HUB] Sync complete: {result.get('updated', 0)} players updated in {duration:.1f}s")
        
        return {
            "success": True,
            "source": "tank01",
            "players_updated": result.get("updated", 0),
            "duration_seconds": round(duration, 1),
            "completed_at": datetime.now(timezone.utc).isoformat()
        }
    
    async def sync_single_player(self, player_name: str) -> Optional[Dict[str, Any]]:
        """
        Sync stats for a single player using Tank01 API.
        """
        from services.tank01_stats_service import get_tank01_service
        
        service = get_tank01_service(self.db)
        return await service.sync_single_player(player_name)


async def run_master_hub_sync(db: AsyncIOMotorDatabase) -> Dict[str, Any]:
    """
    Entry point for manual/CRON sync.
    Uses Tank01 API exclusively.
    """
    service = MasterHubSyncService(db)
    return await service.run_full_sync()


# ======================================
# DEPRECATED - BallDontLie integration removed
# ======================================
# The following functions have been removed:
# - fetch_player_game_logs() - Used BallDontLie
# - _fetch_season_averages() - Used BallDontLie
# - _sync_player_stats() - Used BallDontLie
# 
# All stats now come from Tank01 Fantasy Stats API
# via services/tank01_stats_service.py
# ======================================


if __name__ == "__main__":
    # Test sync
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient
    
    async def test():
        client = AsyncIOMotorClient(os.environ.get("MONGO_URL"))
        db = client[os.environ.get("DB_NAME", "test_database")]
        
        service = MasterHubSyncService(db)
        result = await service.run_full_sync()
        print(f"Result: {result}")
    
    asyncio.run(test())
