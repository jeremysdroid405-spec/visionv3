"""
Photo Storage Service
=====================
Downloads NBA player headshots and stores them as base64 in MongoDB.
This eliminates all external dependencies for player photos.
"""
import base64
import httpx
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import os

logger = logging.getLogger(__name__)

class PhotoStorageService:
    def __init__(self, db):
        self.db = db
        self.master_hub = db.nba_master_hub_2026
        self.photo_cache = db.player_photos  # New collection for photos
        
    async def download_and_store_photo(self, bdl_id: int, player_name: str) -> Optional[str]:
        """
        Download a player photo from NBA CDN and store as base64 in MongoDB.
        
        Returns the base64 data URI if successful, None otherwise.
        """
        if not bdl_id:
            return None
            
        # Check if already cached
        cached = self.photo_cache.find_one({"bdl_id": bdl_id})
        if cached and cached.get("photo_base64"):
            return cached["photo_base64"]
        
        # Download from NBA CDN
        url = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{bdl_id}.png"
        
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=15, follow_redirects=True)
                
                if resp.status_code != 200:
                    logger.warning(f"[PHOTO] Failed to download for {player_name} (ID: {bdl_id}): HTTP {resp.status_code}")
                    return None
                
                # Convert to base64 data URI
                img_data = resp.content
                content_type = resp.headers.get("content-type", "image/png")
                base64_data = base64.b64encode(img_data).decode('utf-8')
                data_uri = f"data:{content_type};base64,{base64_data}"
                
                # Store in MongoDB
                self.photo_cache.update_one(
                    {"bdl_id": bdl_id},
                    {
                        "$set": {
                            "bdl_id": bdl_id,
                            "player_name": player_name,
                            "photo_base64": data_uri,
                            "content_type": content_type,
                            "size_bytes": len(img_data),
                            "downloaded_at": datetime.now(timezone.utc)
                        }
                    },
                    upsert=True
                )
                
                logger.info(f"[PHOTO] Stored photo for {player_name} (ID: {bdl_id}, {len(img_data)} bytes)")
                return data_uri
                
        except httpx.TimeoutException:
            logger.warning(f"[PHOTO] Timeout downloading photo for {player_name} (ID: {bdl_id})")
            return None
        except Exception as e:
            logger.error(f"[PHOTO] Error downloading photo for {player_name}: {e}")
            return None
    
    async def sync_all_active_player_photos(self) -> Dict[str, Any]:
        """
        Download and store photos for all active players in the master hub.
        """
        # Get all active players with BDL IDs
        players = list(self.master_hub.find(
            {"bdl_id": {"$exists": True, "$ne": None}},
            {"bdl_id": 1, "player_name": 1, "name": 1}
        ))
        
        logger.info(f"[PHOTO_SYNC] Starting photo sync for {len(players)} players")
        
        stats = {
            "total_players": len(players),
            "downloaded": 0,
            "already_cached": 0,
            "failed": 0,
            "started_at": datetime.now(timezone.utc).isoformat()
        }
        
        for i, player in enumerate(players):
            bdl_id = player.get("bdl_id")
            player_name = player.get("player_name") or player.get("name")
            
            if not bdl_id:
                continue
            
            # Check if already cached
            cached = self.photo_cache.find_one({"bdl_id": bdl_id})
            if cached and cached.get("photo_base64"):
                stats["already_cached"] += 1
                continue
            
            # Download and store
            result = await self.download_and_store_photo(bdl_id, player_name)
            
            if result:
                stats["downloaded"] += 1
            else:
                stats["failed"] += 1
            
            # Rate limit - 100ms between requests
            await asyncio.sleep(0.1)
            
            # Progress log every 50 players
            if (i + 1) % 50 == 0:
                logger.info(f"[PHOTO_SYNC] Progress: {i + 1}/{len(players)} ({stats['downloaded']} downloaded, {stats['failed']} failed)")
        
        stats["completed_at"] = datetime.now(timezone.utc).isoformat()
        logger.info(f"[PHOTO_SYNC] Complete: {stats}")
        
        return stats
    
    def get_photo(self, bdl_id: int) -> Optional[str]:
        """
        Get a player's photo as base64 data URI from the cache.
        """
        if not bdl_id:
            return None
            
        cached = self.photo_cache.find_one({"bdl_id": bdl_id})
        if cached:
            return cached.get("photo_base64")
        return None
    
    def get_photo_by_name(self, player_name: str) -> Optional[str]:
        """
        Get a player's photo by name (slower, uses master hub lookup).
        """
        player = self.master_hub.find_one(
            {"$or": [
                {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
                {"name": {"$regex": f"^{player_name}$", "$options": "i"}}
            ]},
            {"bdl_id": 1}
        )
        
        if player and player.get("bdl_id"):
            return self.get_photo(player["bdl_id"])
        return None
    
    def enrich_with_photos(self, picks: list) -> list:
        """
        Enrich a list of picks with base64 photos from the cache.
        This replaces the proxy URL approach.
        """
        # Build a map of bdl_id -> base64 photo for efficiency
        bdl_ids = []
        for pick in picks:
            bdl_id = pick.get("bdl_id")
            if not bdl_id:
                # Try to get from master hub by name
                player_name = pick.get("player_name")
                if player_name:
                    player = self.master_hub.find_one(
                        {"$or": [
                            {"player_name": player_name},
                            {"name": player_name}
                        ]},
                        {"bdl_id": 1}
                    )
                    if player:
                        bdl_id = player.get("bdl_id")
                        pick["bdl_id"] = bdl_id
            
            if bdl_id:
                bdl_ids.append(bdl_id)
        
        # Batch fetch all photos
        if bdl_ids:
            photos = {
                doc["bdl_id"]: doc.get("photo_base64")
                for doc in self.photo_cache.find({"bdl_id": {"$in": bdl_ids}})
            }
            
            for pick in picks:
                bdl_id = pick.get("bdl_id")
                if bdl_id and bdl_id in photos:
                    pick["photo_url"] = photos[bdl_id]
        
        return picks
    
    def get_sync_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the photo cache.
        """
        total = self.photo_cache.count_documents({})
        with_photos = self.photo_cache.count_documents({"photo_base64": {"$exists": True, "$ne": None}})
        
        # Get total size
        pipeline = [
            {"$match": {"size_bytes": {"$exists": True}}},
            {"$group": {"_id": None, "total_size": {"$sum": "$size_bytes"}}}
        ]
        size_result = list(self.photo_cache.aggregate(pipeline))
        total_size = size_result[0]["total_size"] if size_result else 0
        
        return {
            "total_entries": total,
            "with_photos": with_photos,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "avg_size_kb": round(total_size / with_photos / 1024, 2) if with_photos else 0
        }
