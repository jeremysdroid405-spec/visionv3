"""
HEADSHOT ROUTES - Player Headshot Management API
=================================================
Endpoints for downloading, serving, and managing local player headshots.

Routes:
- GET /api/headshots/{nba_id} - Serve a headshot image
- POST /api/headshots/download/{nba_id} - Download single headshot
- POST /api/headshots/bulk-download - Bulk download headshots
- POST /api/headshots/update-urls - Update player photo_url fields
- GET /api/headshots/stats - Get storage statistics
"""

import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from typing import Optional, List

from services.headshot_service import get_headshot_service, HEADSHOT_DIR

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/headshots", tags=["Headshots"])

# Database reference
_db = None


def set_headshot_db(db):
    """Set database reference for headshot routes."""
    global _db
    _db = db


def get_db():
    """Get database instance."""
    return _db


# ==================== STATUS ENDPOINTS (defined first to avoid path conflicts) ====================

@router.get("/stats")
async def get_headshot_stats():
    """Get statistics about local headshot storage."""
    service = get_headshot_service(get_db())
    return service.get_stats()


@router.get("/check/{nba_id}")
async def check_headshot_exists(nba_id: int):
    """Check if a headshot exists locally."""
    service = get_headshot_service(get_db())
    exists = service.headshot_exists(nba_id)
    
    return {
        "nba_id": nba_id,
        "exists": exists,
        "local_url": service.get_local_url(nba_id) if exists else None,
        "local_path": str(service.get_local_path(nba_id))
    }


# ==================== SERVE HEADSHOTS ====================

@router.get("/image/{nba_id}")
async def serve_headshot(nba_id: int):
    """
    Serve a player headshot image.
    
    Returns the local image file if it exists, or a placeholder.
    """
    service = get_headshot_service(get_db())
    local_path = service.get_local_path(nba_id)
    
    if local_path.exists():
        return FileResponse(
            local_path,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=86400",  # Cache for 24 hours
                "X-Headshot-Source": "local"
            }
        )
    
    # Return placeholder
    placeholder = HEADSHOT_DIR / "placeholder.png"
    if placeholder.exists():
        return FileResponse(
            placeholder,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=3600",  # Cache placeholder for 1 hour
                "X-Headshot-Source": "placeholder"
            }
        )
    
    raise HTTPException(status_code=404, detail="Headshot not found")


# ==================== DOWNLOAD ENDPOINTS ====================

@router.post("/download/{nba_id}")
async def download_single_headshot(
    nba_id: int,
    force: bool = Query(False, description="Force re-download even if file exists")
):
    """
    Download a single player headshot.
    
    Args:
        nba_id: NBA.com player ID
        force: If true, re-download even if file exists
    """
    service = get_headshot_service(get_db())
    result = await service.download_headshot(nba_id, force=force)
    
    return {
        "success": result.get("success", False),
        "nba_id": nba_id,
        "local_url": result.get("local_url"),
        "source": result.get("source"),
        "skipped": result.get("skipped", False),
        "error": result.get("error")
    }


@router.post("/bulk-download")
async def bulk_download_headshots(
    background_tasks: BackgroundTasks,
    force: bool = Query(False, description="Force re-download all"),
    nba_ids: Optional[List[int]] = Query(None, description="Specific NBA IDs to download"),
    sync_mode: bool = Query(False, description="Run synchronously (blocks until complete)")
):
    """
    Bulk download headshots for all players with nba_id.
    
    By default runs in background. Set sync_mode=true to wait for completion.
    """
    service = get_headshot_service(get_db())
    
    if sync_mode:
        # Run synchronously
        stats = await service.bulk_download_headshots(nba_ids=nba_ids, force=force)
        return stats
    else:
        # Run in background
        background_tasks.add_task(
            service.bulk_download_headshots,
            nba_ids=nba_ids,
            force=force
        )
        return {
            "status": "started",
            "message": "Bulk download started in background",
            "check_stats_at": "/api/headshots/stats"
        }


@router.post("/update-urls")
async def update_player_photo_urls():
    """
    Update photo_url fields in player collections to use local URLs.
    
    This updates nba_master_hub_2026 to point to local headshot files.
    Run this after bulk-download to switch to local images.
    """
    service = get_headshot_service(get_db())
    stats = await service.update_player_photo_urls()
    return stats
