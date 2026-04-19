"""
Photo Service
=============
Service for managing player photo URLs and caching.
Extracted from picks_getter_service.py for modularity.
"""

import logging
from typing import Dict, List, Optional, Any
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)


class PhotoService:
    """
    Manages player photo URL resolution and caching.
    
    Uses player_photos collection as primary source,
    falls back to ESPN CDN with player ID lookup.
    """
    
    ESPN_CDN_BASE = "https://a.espncdn.com/combiner/i?img=/i/headshots/nba/players/full"
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.photos_collection = db.player_photos
        self.master_hub = db[COLL("master_hub", "nba")]
        self._photo_cache: Dict[str, str] = {}
        self._cache_loaded = False
    
    async def load_cache(self) -> None:
        """Load all photos into memory for fast lookup."""
        if self._cache_loaded:
            return
        
        try:
            cursor = self.photos_collection.find({}, {"_id": 0, "player_name": 1, "photo_url": 1})
            async for doc in cursor:
                name = doc.get("player_name", "").lower()
                url = doc.get("photo_url")
                if name and url:
                    self._photo_cache[name] = url
            
            self._cache_loaded = True
            logger.info(f"[PHOTO] Loaded {len(self._photo_cache)} photos into cache")
        except Exception as e:
            logger.error(f"[PHOTO] Failed to load cache: {e}")
    
    async def get_photo_url(self, player_name: str) -> Optional[str]:
        """
        Get photo URL for a player.
        
        Args:
            player_name: Player's name
            
        Returns:
            Photo URL or None
        """
        if not player_name:
            return None
        
        # Ensure cache is loaded
        if not self._cache_loaded:
            await self.load_cache()
        
        # Check memory cache first
        name_lower = player_name.lower()
        if name_lower in self._photo_cache:
            return self._photo_cache[name_lower]
        
        # Try database lookup
        photo_doc = await self.photos_collection.find_one(
            {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0, "photo_url": 1}
        )
        
        if photo_doc and photo_doc.get("photo_url"):
            url = photo_doc["photo_url"]
            self._photo_cache[name_lower] = url
            return url
        
        # Try master hub for ESPN ID
        master_doc = await self.master_hub.find_one(
            {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0, "espn_id": 1, "photo_url": 1, "headshot_url": 1}
        )
        
        if master_doc:
            # Check if master hub has photo URL
            url = master_doc.get("photo_url") or master_doc.get("headshot_url")
            if url:
                self._photo_cache[name_lower] = url
                return url
            
            # Build ESPN CDN URL from ID
            espn_id = master_doc.get("espn_id")
            if espn_id:
                url = f"{self.ESPN_CDN_BASE}/{espn_id}.png&w=350&h=254"
                self._photo_cache[name_lower] = url
                return url
        
        return None
    
    async def enrich_with_photos(self, items: List[Dict], name_key: str = "player_name") -> List[Dict]:
        """
        Add photo URLs to a list of items.
        
        Args:
            items: List of dictionaries to enrich
            name_key: Key containing player name
            
        Returns:
            Same list with photo_url added to each item
        """
        if not items:
            return items
        
        # Ensure cache is loaded
        if not self._cache_loaded:
            await self.load_cache()
        
        for item in items:
            name = item.get(name_key)
            if name and not item.get("photo_url"):
                photo_url = await self.get_photo_url(name)
                if photo_url:
                    item["photo_url"] = photo_url
        
        return items
    
    async def save_photo(self, player_name: str, photo_url: str) -> bool:
        """
        Save a photo URL to the database.
        
        Args:
            player_name: Player's name
            photo_url: URL of the photo
            
        Returns:
            True if saved successfully
        """
        try:
            await self.photos_collection.update_one(
                {"player_name": player_name},
                {"$set": {"player_name": player_name, "photo_url": photo_url}},
                upsert=True
            )
            self._photo_cache[player_name.lower()] = photo_url
            return True
        except Exception as e:
            logger.error(f"[PHOTO] Failed to save photo for {player_name}: {e}")
            return False
    
    def clear_cache(self) -> None:
        """Clear the in-memory photo cache."""
        self._photo_cache = {}
        self._cache_loaded = False
        logger.info("[PHOTO] Cache cleared")
    
    async def get_cache_stats(self) -> Dict[str, Any]:
        """Get statistics about the photo cache."""
        if not self._cache_loaded:
            await self.load_cache()
        
        db_count = await self.photos_collection.count_documents({})
        
        return {
            "cache_size": len(self._photo_cache),
            "db_count": db_count,
            "cache_loaded": self._cache_loaded
        }
