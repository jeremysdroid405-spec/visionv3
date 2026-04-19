"""
HEADSHOT SERVICE - Local Player Headshot Storage
=================================================
Downloads and stores player headshots locally for reliable, fast loading.

Architecture:
- Downloads headshots once from NBA CDN
- Stores locally at /app/backend/static/player-headshots/{nba_id}.png
- Serves via /static/player-headshots/{nba_id}.png
- Falls back to placeholder only if download fails

Author: PropVision v3.2
"""

import os
import asyncio
import aiohttp
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================

# Local storage path
HEADSHOT_DIR = Path("/app/backend/static/player-headshots")

# NBA CDN source URL pattern
NBA_CDN_URL = "https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_id}.png"

# Alternative sources (fallbacks)
ESPN_CDN_URL = "https://a.espncdn.com/combiner/i?img=/i/headshots/nba/players/full/{nba_id}.png&w=350&h=254"

# Local serving URL pattern (what gets stored in photo_url)
LOCAL_URL_PATTERN = "/static/player-headshots/{nba_id}.png"

# Placeholder for players without headshots
PLACEHOLDER_URL = "/static/player-headshots/placeholder.png"


class HeadshotService:
    """
    Service for downloading and managing local player headshots.
    
    Usage:
        service = HeadshotService(db)
        
        # Download single headshot
        url = await service.download_headshot(203932)
        
        # Bulk download for all players
        stats = await service.bulk_download_headshots()
        
        # Get local URL for a player
        url = service.get_local_url(203932)
    """
    
    def __init__(self, db=None):
        self.db = db
        self.headshot_dir = HEADSHOT_DIR
        
        # Ensure directory exists
        self.headshot_dir.mkdir(parents=True, exist_ok=True)
        
        # Create placeholder if it doesn't exist
        self._ensure_placeholder()
    
    def _ensure_placeholder(self):
        """Create a simple placeholder image if it doesn't exist."""
        placeholder_path = self.headshot_dir / "placeholder.png"
        if not placeholder_path.exists():
            # Create a minimal 1x1 transparent PNG as placeholder
            # This is a valid PNG header for a 1x1 transparent image
            png_data = bytes([
                0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,  # PNG signature
                0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,  # IHDR chunk
                0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,  # 1x1
                0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4,  # 8-bit RGBA
                0x89, 0x00, 0x00, 0x00, 0x0A, 0x49, 0x44, 0x41,  # IDAT chunk
                0x54, 0x78, 0x9C, 0x63, 0x00, 0x01, 0x00, 0x00,
                0x05, 0x00, 0x01, 0x0D, 0x0A, 0x2D, 0xB4, 0x00,
                0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE,  # IEND chunk
                0x42, 0x60, 0x82
            ])
            placeholder_path.write_bytes(png_data)
            logger.info("[HEADSHOT] Created placeholder image")
    
    def get_local_path(self, nba_id: int) -> Path:
        """Get the local file path for a headshot."""
        return self.headshot_dir / f"{nba_id}.png"
    
    def get_local_url(self, nba_id: int) -> str:
        """Get the local serving URL for a headshot."""
        return LOCAL_URL_PATTERN.format(nba_id=nba_id)
    
    def headshot_exists(self, nba_id: int) -> bool:
        """Check if a headshot file exists locally."""
        return self.get_local_path(nba_id).exists()
    
    async def download_headshot(
        self, 
        nba_id: int, 
        force: bool = False
    ) -> Dict[str, Any]:
        """
        Download a single player headshot from NBA CDN.
        
        Args:
            nba_id: NBA.com player ID
            force: If True, re-download even if file exists
            
        Returns:
            Dict with success status and local URL
        """
        result = {
            "nba_id": nba_id,
            "success": False,
            "local_url": None,
            "local_path": None,
            "source": None,
            "error": None,
            "skipped": False
        }
        
        local_path = self.get_local_path(nba_id)
        
        # Skip if already exists (unless forced)
        if local_path.exists() and not force:
            result["success"] = True
            result["local_url"] = self.get_local_url(nba_id)
            result["local_path"] = str(local_path)
            result["skipped"] = True
            result["source"] = "cached"
            return result
        
        # Try downloading from NBA CDN
        sources = [
            ("nba_cdn", NBA_CDN_URL.format(nba_id=nba_id)),
        ]
        
        async with aiohttp.ClientSession() as session:
            for source_name, url in sources:
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                        if response.status == 200:
                            content_type = response.headers.get("Content-Type", "")
                            
                            # Verify it's an image
                            if "image" in content_type:
                                image_data = await response.read()
                                
                                # Verify minimum size (avoid empty/error images)
                                if len(image_data) > 1000:
                                    # Save locally
                                    local_path.write_bytes(image_data)
                                    
                                    result["success"] = True
                                    result["local_url"] = self.get_local_url(nba_id)
                                    result["local_path"] = str(local_path)
                                    result["source"] = source_name
                                    result["size_bytes"] = len(image_data)
                                    
                                    logger.debug(f"[HEADSHOT] Downloaded {nba_id} from {source_name}")
                                    return result
                                else:
                                    logger.warning(f"[HEADSHOT] Image too small for {nba_id}: {len(image_data)} bytes")
                        else:
                            logger.debug(f"[HEADSHOT] {source_name} returned {response.status} for {nba_id}")
                            
                except asyncio.TimeoutError:
                    logger.warning(f"[HEADSHOT] Timeout downloading {nba_id} from {source_name}")
                except Exception as e:
                    logger.warning(f"[HEADSHOT] Error downloading {nba_id} from {source_name}: {e}")
        
        result["error"] = "Failed to download from all sources"
        return result
    
    async def bulk_download_headshots(
        self,
        nba_ids: List[int] = None,
        force: bool = False,
        concurrency: int = 5,
        progress_callback=None
    ) -> Dict[str, Any]:
        """
        Bulk download headshots for multiple players.
        
        Args:
            nba_ids: List of NBA IDs to download. If None, fetches from master hub.
            force: If True, re-download all even if files exist
            concurrency: Number of concurrent downloads
            progress_callback: Optional callback(current, total) for progress
            
        Returns:
            Dict with download statistics
        """
        stats = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "total": 0,
            "downloaded": 0,
            "skipped": 0,
            "failed": 0,
            "errors": []
        }
        
        # Get NBA IDs from master hub if not provided
        if nba_ids is None and self.db is not None:
            cursor = self.db[COLL("master_hub", "nba")].find(
                {"nba_id": {"$exists": True, "$ne": None}},
                {"nba_id": 1, "display_name": 1}
            )
            players = await cursor.to_list(length=1000)
            nba_ids = [p["nba_id"] for p in players if p.get("nba_id")]
        
        if not nba_ids:
            stats["error"] = "No NBA IDs provided or found"
            return stats
        
        stats["total"] = len(nba_ids)
        logger.info(f"[HEADSHOT] Starting bulk download for {len(nba_ids)} players")
        
        # Use semaphore for concurrency control
        semaphore = asyncio.Semaphore(concurrency)
        
        async def download_with_semaphore(nba_id):
            async with semaphore:
                return await self.download_headshot(nba_id, force=force)
        
        # Download all
        tasks = [download_with_semaphore(nba_id) for nba_id in nba_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                stats["failed"] += 1
                stats["errors"].append(str(result))
            elif result.get("success"):
                if result.get("skipped"):
                    stats["skipped"] += 1
                else:
                    stats["downloaded"] += 1
            else:
                stats["failed"] += 1
                if result.get("error"):
                    stats["errors"].append(f"{result['nba_id']}: {result['error']}")
            
            # Progress callback
            if progress_callback:
                progress_callback(i + 1, len(nba_ids))
        
        stats["completed_at"] = datetime.now(timezone.utc).isoformat()
        stats["duration_seconds"] = (
            datetime.fromisoformat(stats["completed_at"].replace("+00:00", "")) -
            datetime.fromisoformat(stats["started_at"].replace("+00:00", ""))
        ).total_seconds()
        
        logger.info(
            f"[HEADSHOT] Bulk download complete: "
            f"{stats['downloaded']} downloaded, {stats['skipped']} skipped, {stats['failed']} failed"
        )
        
        return stats
    
    async def update_player_photo_urls(self) -> Dict[str, Any]:
        """
        Update photo_url fields in player collections to use local URLs.
        
        Updates:
        - nba_master_hub_2026.photo_url
        - Triggers mapper reload which will pick up new URLs
        """
        if self.db is None:
            return {"error": "Database not initialized"}
        
        stats = {
            "updated": 0,
            "skipped": 0,
            "errors": []
        }
        
        try:
            # Get all players with nba_id
            cursor = self.db[COLL("master_hub", "nba")].find(
                {"nba_id": {"$exists": True, "$ne": None}},
                {"_id": 1, "nba_id": 1, "display_name": 1, "photo_url": 1}
            )
            players = await cursor.to_list(length=1000)
            
            for player in players:
                nba_id = player.get("nba_id")
                if not nba_id:
                    continue
                
                local_url = self.get_local_url(nba_id)
                current_url = player.get("photo_url")
                
                # Only update if different and local file exists
                if current_url != local_url and self.headshot_exists(nba_id):
                    await self.db[COLL("master_hub", "nba")].update_one(
                        {"_id": player["_id"]},
                        {"$set": {
                            "photo_url": local_url,
                            "headshot_url": local_url
                        }}
                    )
                    stats["updated"] += 1
                else:
                    stats["skipped"] += 1
            
            logger.info(f"[HEADSHOT] Updated {stats['updated']} player photo URLs")
            
        except Exception as e:
            stats["errors"].append(str(e))
            logger.error(f"[HEADSHOT] Error updating photo URLs: {e}")
        
        return stats
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about local headshot storage."""
        files = list(self.headshot_dir.glob("*.png"))
        
        total_size = sum(f.stat().st_size for f in files)
        
        return {
            "directory": str(self.headshot_dir),
            "total_files": len(files),
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "url_pattern": LOCAL_URL_PATTERN
        }


# ==================== SINGLETON ====================

_headshot_service: Optional[HeadshotService] = None


def get_headshot_service(db=None) -> HeadshotService:
    """Get or create HeadshotService singleton."""
    global _headshot_service
    if _headshot_service is None:
        _headshot_service = HeadshotService(db)
    return _headshot_service
