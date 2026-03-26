"""
Image Proxy Route
=================
Proxies NBA CDN images to avoid CORS issues in the browser.
Uses smaller image size (260x190) for faster loading.
Aggressive in-memory caching to prevent repeated fetches.
"""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
import httpx
import logging
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/proxy", tags=["proxy"])

# In-memory cache for images - stores (image_bytes, content_type)
# Using dict with LRU-like behavior by limiting size
_image_cache: Dict[str, Tuple[bytes, str]] = {}
MAX_CACHE_SIZE = 5000  # Increased from 2000

# NBA CDN image sizes: 1040x760, 260x190
IMAGE_SIZES = {
    "small": "260x190",   # ~15KB - fast loading for lists
    "large": "1040x760"   # ~200KB - high quality for detail views
}

# Placeholder image for missing headshots (1x1 transparent PNG)
PLACEHOLDER_IMAGE = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'

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
        The image with aggressive caching headers (30 days)
    """
    # Validate size
    img_size = IMAGE_SIZES.get(size, IMAGE_SIZES["small"])
    
    # Check cache first (instant response)
    cache_key = f"headshot_{player_id}_{img_size}"
    if cache_key in _image_cache:
        img_data, content_type = _image_cache[cache_key]
        return Response(
            content=img_data, 
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=2592000, immutable",  # 30 days
                "Access-Control-Allow-Origin": "*",
                "X-Cache": "HIT"
            }
        )
    
    # Fetch from NBA CDN
    url = f"https://cdn.nba.com/headshots/nba/latest/{img_size}/{player_id}.png"
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=5, follow_redirects=True)
            
            if resp.status_code == 200:
                img_data = resp.content
                content_type = resp.headers.get("content-type", "image/png")
            else:
                # Return placeholder for missing images (don't throw 404)
                img_data = PLACEHOLDER_IMAGE
                content_type = "image/png"
            
            # Cache it (with size limit)
            if len(_image_cache) >= MAX_CACHE_SIZE:
                # Remove oldest entries (first 500)
                keys_to_remove = list(_image_cache.keys())[:500]
                for k in keys_to_remove:
                    del _image_cache[k]
            
            _image_cache[cache_key] = (img_data, content_type)
            
            return Response(
                content=img_data,
                media_type=content_type,
                headers={
                    "Cache-Control": "public, max-age=2592000, immutable",  # 30 days
                    "Access-Control-Allow-Origin": "*",
                    "X-Cache": "MISS"
                }
            )
    
    except httpx.TimeoutException:
        # Return placeholder on timeout instead of error
        return Response(
            content=PLACEHOLDER_IMAGE,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=3600",  # 1 hour for errors
                "Access-Control-Allow-Origin": "*"
            }
        )
    except Exception as e:
        logger.error(f"Error proxying image {player_id}: {e}")
        return Response(
            content=PLACEHOLDER_IMAGE,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=3600",
                "Access-Control-Allow-Origin": "*"
            }
        )
