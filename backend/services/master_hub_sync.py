"""
NBA Master Hub Sync Service
============================
Daily sync job to populate nba_master_hub_2026 with:
- player_id, player_name, team_abbreviation
- photo_url (direct asset link)
- baseline_stats: L5_avg, L10_avg, season_avg for all prop categories

Data Source: BallDontLie API (Primary and ONLY source)
Scheduled to run daily at 0300 EST via CRON.

NOTE: BDL is the only stats source. All stats come from BDL.
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
    Uses BallDontLie API exclusively for all stats data.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.master_hub = db.nba_master_hub_2026
    
    async def run_full_sync(self) -> Dict[str, Any]:
        """
        Run full master hub sync using BDL API.
        
        This is the primary sync method called by the CRON job.
        """
        from services.bdl_comprehensive_sync import BDLComprehensiveSyncService
        
        logger.info("[MASTER_HUB] Starting full sync using BDL API...")
        start_time = datetime.now(timezone.utc)
        
        try:
            # Use BDL comprehensive sync
            bdl_service = BDLComprehensiveSyncService(self.db)
            result = await bdl_service.run_full_sync()
            
            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
            logger.info(f"[MASTER_HUB] Sync completed in {elapsed:.1f}s")
            
            return {
                "success": True,
                "source": "bdl",
                "elapsed_seconds": elapsed,
                "details": result,
                "synced_at": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"[MASTER_HUB] Sync failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "synced_at": datetime.now(timezone.utc).isoformat()
            }
    
    async def sync_single_player(self, player_name: str) -> Dict[str, Any]:
        """
        Sync a single player's stats from BDL.
        """
        from services.bdl_comprehensive_sync import BDLComprehensiveSyncService
        
        logger.info(f"[MASTER_HUB] Syncing single player: {player_name}")
        
        try:
            bdl_service = BDLComprehensiveSyncService(self.db)
            result = await bdl_service.sync_single_player(player_name)
            
            return {
                "success": True,
                "player": player_name,
                "details": result
            }
        except Exception as e:
            logger.error(f"[MASTER_HUB] Single player sync failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }


# Singleton instance
_service_instance: Optional[MasterHubSyncService] = None


def get_master_hub_sync_service(db: AsyncIOMotorDatabase) -> MasterHubSyncService:
    """Get or create the singleton service instance."""
    global _service_instance
    if _service_instance is None:
        _service_instance = MasterHubSyncService(db)
    return _service_instance
