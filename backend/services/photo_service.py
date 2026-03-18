"""
Photo Service - Player Photo URL Management
============================================
Handles:
- ESPN headshot URL generation (using ESPN IDs from master hub)
- NBA.com CDN fallback
- Team logo fallbacks
- Batch photo sync operations

NOTE: Tank01 has been REMOVED. Photos now use:
1. ESPN IDs already in nba_master_hub_2026 (from BDL sync)
2. NBA CDN as fallback
"""
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import asyncio
import httpx
import logging

from config.settings import TEAM_LOGOS, NBA_PLAYER_IDS

logger = logging.getLogger(__name__)


class PhotoService:
    """Service for managing player photo URLs"""
    
    def __init__(self, db):
        self.db = db
        self.master_hub = db.nba_master_hub_2026
        self.cached_board = db.dg_cached_board
        self._espn_id_cache: Dict[str, Dict] = {}
    
    # ==================== PHOTO URL GENERATION ====================
    
    def get_espn_headshot_url(self, espn_id: str) -> str:
        """Generate ESPN CDN headshot URL from ESPN ID."""
        if not espn_id:
            return ""
        return f"https://a.espncdn.com/combiner/i?img=/i/headshots/nba/players/full/{espn_id}.png&w=350&h=254"
    
    def get_nba_headshot_url(self, nba_id: str) -> str:
        """Generate NBA.com CDN headshot URL from NBA ID."""
        if not nba_id:
            return ""
        return f"https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_id}.png"
    
    def get_team_logo_fallback(self, team_abbrev: str) -> str:
        """Get team logo as fallback when no player photo available."""
        return TEAM_LOGOS.get(team_abbrev, TEAM_LOGOS.get("NBA", ""))
    
    async def get_player_photo_url(self, player_name: str) -> Optional[str]:
        """
        Get best available photo URL for a player.
        
        Priority:
        1. Existing photo_url in master hub
        2. Generate from ESPN ID in master hub
        3. Generate from NBA ID in master hub
        4. Team logo fallback
        """
        # Check master hub for existing data
        player = await self.master_hub.find_one(
            {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0, "photo_url": 1, "espn_id": 1, "nba_player_id": 1, "team": 1}
        )
        
        if not player:
            logger.debug(f"[PHOTO] Player not found in master hub: {player_name}")
            return None
        
        # Return existing photo URL if available
        if player.get("photo_url"):
            return player["photo_url"]
        
        # Try ESPN headshot
        espn_id = player.get("espn_id")
        if espn_id:
            return self.get_espn_headshot_url(str(espn_id))
        
        # Try NBA headshot
        nba_id = player.get("nba_player_id")
        if nba_id:
            return self.get_nba_headshot_url(str(nba_id))
        
        # Fallback to team logo
        team = player.get("team", "")
        if team:
            return self.get_team_logo_fallback(team)
        
        return None
    
    # ==================== GLOBAL PHOTO SYNC ====================
    
    async def sync_all_photos(self) -> Dict[str, Any]:
        """
        Sync photos for all players in master hub.
        
        Uses ESPN IDs already in the database (populated by BDL sync).
        """
        logger.info("[PHOTO] Starting photo sync for all players...")
        start_time = datetime.now(timezone.utc)
        
        updated = 0
        skipped = 0
        failed = 0
        
        try:
            # Get all players from master hub
            cursor = self.master_hub.find(
                {},
                {"_id": 0, "display_name": 1, "photo_url": 1, "espn_id": 1, "nba_player_id": 1, "team": 1}
            )
            
            async for player in cursor:
                name = player.get("display_name")
                existing_url = player.get("photo_url")
                
                # Skip if already has a working photo URL
                if existing_url and "espncdn.com" in existing_url:
                    skipped += 1
                    continue
                
                # Generate new URL
                photo_url = None
                espn_id = player.get("espn_id")
                nba_id = player.get("nba_player_id")
                team = player.get("team", "")
                
                if espn_id:
                    photo_url = self.get_espn_headshot_url(str(espn_id))
                elif nba_id:
                    photo_url = self.get_nba_headshot_url(str(nba_id))
                elif team:
                    photo_url = self.get_team_logo_fallback(team)
                
                if photo_url:
                    await self.master_hub.update_one(
                        {"display_name": name},
                        {"$set": {"photo_url": photo_url}}
                    )
                    updated += 1
                else:
                    failed += 1
            
            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
            logger.info(f"[PHOTO] Sync complete: {updated} updated, {skipped} skipped, {failed} failed in {elapsed:.1f}s")
            
            return {
                "success": True,
                "updated": updated,
                "skipped": skipped,
                "failed": failed,
                "elapsed_seconds": elapsed
            }
            
        except Exception as e:
            logger.error(f"[PHOTO] Sync failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def sync_active_players_with_photos(self) -> Dict[str, Any]:
        """
        Sync photos only for players currently on the cached board.
        """
        logger.info("[PHOTO] Syncing photos for active players...")
        
        try:
            # Get unique player names from cached board
            pipeline = [
                {"$match": {"props": {"$exists": True, "$ne": []}}},
                {"$group": {"_id": "$player_name"}}
            ]
            cursor = self.cached_board.aggregate(pipeline)
            player_names = [doc["_id"] async for doc in cursor]
            
            updated = 0
            for name in player_names:
                photo_url = await self.get_player_photo_url(name)
                if photo_url:
                    # Update cached board entries
                    await self.cached_board.update_many(
                        {"player_name": name},
                        {"$set": {"photo_url": photo_url}}
                    )
                    updated += 1
            
            logger.info(f"[PHOTO] Updated photos for {updated}/{len(player_names)} active players")
            
            return {
                "success": True,
                "total_players": len(player_names),
                "updated": updated
            }
            
        except Exception as e:
            logger.error(f"[PHOTO] Active player sync failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }


# Singleton instance
_photo_service = None

def get_photo_service(db) -> PhotoService:
    """Get or create photo service instance."""
    global _photo_service
    if _photo_service is None:
        _photo_service = PhotoService(db)
    return _photo_service
