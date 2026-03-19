"""
Image Proxy Route
=================
Proxies NBA CDN images to avoid CORS issues in the browser.
Uses smaller image size (260x190) for faster loading.
"""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
import httpx
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/proxy", tags=["proxy"])

# In-memory cache for images
_image_cache = {}

# NBA CDN image sizes: 1040x760, 260x190
IMAGE_SIZES = {
    "small": "260x190",   # ~15KB - fast loading for lists
    "large": "1040x760"   # ~200KB - high quality for detail views
}

@router.get("/nba-headshot/{player_id}")
async def proxy_nba_headshot(
    player_id: int,
    size: str = Query("small", description="Image size: 'small' (260x190) or 'large' (1040x760)")
):
    """
    Proxy NBA CDN headshot images to avoid CORS issues.
    
    Args:
        player_id: The NBA player ID
        size: Image size - 'small' for lists (fast), 'large' for detail views
        
    Returns:
        The image with aggressive caching headers
    """
    # Validate size
    img_size = IMAGE_SIZES.get(size, IMAGE_SIZES["small"])
    
    # Check cache
    cache_key = f"headshot_{player_id}_{img_size}"
    if cache_key in _image_cache:
        img_data, content_type = _image_cache[cache_key]
        return Response(
            content=img_data, 
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=604800, immutable",  # 7 days, immutable
                "Access-Control-Allow-Origin": "*"
            }
        )
    
    # Fetch from NBA CDN
    url = f"https://cdn.nba.com/headshots/nba/latest/{img_size}/{player_id}.png"
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10, follow_redirects=True)
            
            if resp.status_code != 200:
                raise HTTPException(status_code=404, detail="Image not found")
            
            img_data = resp.content
            content_type = resp.headers.get("content-type", "image/png")
            
            # Cache it (limit cache size to 2000 images)
            if len(_image_cache) < 2000:
                _image_cache[cache_key] = (img_data, content_type)
            
            return Response(
                content=img_data,
                media_type=content_type,
                headers={
                    "Cache-Control": "public, max-age=604800, immutable",  # 7 days
                    "Access-Control-Allow-Origin": "*"
                }
            )
    
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Image fetch timeout")
    except Exception as e:
        logger.error(f"Error proxying image {player_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
