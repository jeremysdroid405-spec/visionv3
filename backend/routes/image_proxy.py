"""
Image Proxy Route
=================
Proxies NBA CDN images to avoid CORS issues in the browser.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
import httpx
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/proxy", tags=["proxy"])

# Cache for images (in-memory, simple TTL)
_image_cache = {}

@router.get("/nba-headshot/{player_id}")
async def proxy_nba_headshot(player_id: int):
    """
    Proxy NBA CDN headshot images to avoid CORS issues.
    
    Args:
        player_id: The BDL/NBA player ID
        
    Returns:
        The image with proper headers
    """
    # Check cache
    cache_key = f"headshot_{player_id}"
    if cache_key in _image_cache:
        img_data, content_type = _image_cache[cache_key]
        return Response(content=img_data, media_type=content_type)
    
    # Fetch from NBA CDN
    url = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{player_id}.png"
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10, follow_redirects=True)
            
            if resp.status_code != 200:
                raise HTTPException(status_code=404, detail="Image not found")
            
            img_data = resp.content
            content_type = resp.headers.get("content-type", "image/png")
            
            # Cache it (limit cache size)
            if len(_image_cache) < 1000:
                _image_cache[cache_key] = (img_data, content_type)
            
            return Response(
                content=img_data,
                media_type=content_type,
                headers={
                    "Cache-Control": "public, max-age=86400",  # 24 hour cache
                    "Access-Control-Allow-Origin": "*"
                }
            )
    
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Image fetch timeout")
    except Exception as e:
        logger.error(f"Error proxying image {player_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
