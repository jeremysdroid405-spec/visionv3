"""
Headshot Scraper Service
========================
Finds and validates player headshots from multiple sources.

Sources (in priority order):
1. NBA.com CDN (official) - https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_id}.png
2. ESPN CDN - https://a.espncdn.com/i/headshots/nba/players/full/{espn_id}.png  
3. NBA.com alternate - https://cdn.nba.com/headshots/nba/latest/260x190/{nba_id}.png
4. Sports Reference - https://www.basketball-reference.com/req/202106291/images/headshots/{bbref_id}.jpg

This service validates URLs and updates master hub with working headshots.
"""
import asyncio
import logging
import httpx
from typing import Dict, List, Optional, Tuple
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# Headshot URL templates
HEADSHOT_SOURCES = {
    "nba_cdn_large": "https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_id}.png",
    "nba_cdn_small": "https://cdn.nba.com/headshots/nba/latest/260x190/{nba_id}.png",
    "espn": "https://a.espncdn.com/i/headshots/nba/players/full/{espn_id}.png",
    "espn_combiner": "https://a.espncdn.com/combiner/i?img=/i/headshots/nba/players/full/{espn_id}.png",
}


class HeadshotScraperService:
    """Service to find and validate player headshots from multiple sources."""
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.hub = db.nba_master_hub_2026
        self.http_client = None
    
    async def _get_http_client(self) -> httpx.AsyncClient:
        if self.http_client is None:
            self.http_client = httpx.AsyncClient(
                timeout=10.0,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
        return self.http_client
    
    async def _validate_url(self, url: str) -> bool:
        """Check if a headshot URL returns a valid image."""
        try:
            client = await self._get_http_client()
            response = await client.head(url)
            
            if response.status_code == 200:
                content_type = response.headers.get('content-type', '')
                if 'image' in content_type:
                    return True
            return False
        except Exception as e:
            logger.debug(f"URL validation failed for {url}: {e}")
            return False
    
    async def _find_espn_id(self, player_name: str) -> Optional[str]:
        """
        Try to find ESPN player ID by searching.
        ESPN IDs can be found via their search API or by scraping.
        """
        try:
            client = await self._get_http_client()
            
            # ESPN search endpoint
            search_url = f"https://site.api.espn.com/apis/common/v3/search?query={player_name}&limit=5&type=player"
            response = await client.get(search_url)
            
            if response.status_code == 200:
                data = response.json()
                items = data.get('items', [])
                
                for item in items:
                    # Check if it's an NBA player
                    if item.get('type') == 'player':
                        link = item.get('link', '')
                        # Extract ID from link like /nba/player/_/id/12345/name
                        if '/nba/player/' in link and '/id/' in link:
                            parts = link.split('/id/')
                            if len(parts) > 1:
                                espn_id = parts[1].split('/')[0]
                                return espn_id
            return None
        except Exception as e:
            logger.debug(f"ESPN ID search failed for {player_name}: {e}")
            return None
    
    async def find_headshot_for_player(self, player: Dict) -> Optional[str]:
        """
        Find a working headshot URL for a player from multiple sources.
        
        Returns the first valid URL found, or None.
        """
        nba_id = player.get('nba_player_id')
        espn_id = player.get('espn_id')
        player_name = player.get('display_name', '')
        
        urls_to_try = []
        
        # Try NBA CDN first (most reliable for active players)
        if nba_id:
            urls_to_try.append(("nba_cdn_large", HEADSHOT_SOURCES["nba_cdn_large"].format(nba_id=nba_id)))
            urls_to_try.append(("nba_cdn_small", HEADSHOT_SOURCES["nba_cdn_small"].format(nba_id=nba_id)))
        
        # Try ESPN if we have an ESPN ID
        if espn_id:
            urls_to_try.append(("espn", HEADSHOT_SOURCES["espn"].format(espn_id=espn_id)))
        
        # Validate each URL
        for source, url in urls_to_try:
            if await self._validate_url(url):
                logger.debug(f"Found valid headshot for {player_name} from {source}")
                return url
        
        # If no ESPN ID, try to find one
        if not espn_id and player_name:
            found_espn_id = await self._find_espn_id(player_name)
            if found_espn_id:
                espn_url = HEADSHOT_SOURCES["espn"].format(espn_id=found_espn_id)
                if await self._validate_url(espn_url):
                    # Save ESPN ID for future use
                    await self.hub.update_one(
                        {'_id': player['_id']},
                        {'$set': {'espn_id': found_espn_id}}
                    )
                    logger.debug(f"Found ESPN headshot for {player_name} (espn_id: {found_espn_id})")
                    return espn_url
        
        return None
    
    async def validate_and_fix_all_headshots(self) -> Dict:
        """
        Validate all player headshots and fix broken ones.
        
        Returns summary of results.
        """
        logger.info("[HEADSHOT_SCRAPER] Starting headshot validation and repair...")
        
        results = {
            "total_checked": 0,
            "already_valid": 0,
            "fixed": 0,
            "still_missing": 0,
            "fixed_players": [],
            "missing_players": []
        }
        
        # Get all synced players
        cursor = self.hub.find(
            {'game_logs_source': 'nba_official'},
            {'display_name': 1, 'nba_player_id': 1, 'espn_id': 1, 'headshot_url': 1, 'player_id': 1}
        )
        players = await cursor.to_list(600)
        
        results["total_checked"] = len(players)
        logger.info(f"[HEADSHOT_SCRAPER] Checking {len(players)} players...")
        
        for i, player in enumerate(players):
            player_name = player.get('display_name', 'Unknown')
            current_url = player.get('headshot_url')
            
            # Check if current URL is valid
            if current_url and await self._validate_url(current_url):
                results["already_valid"] += 1
                continue
            
            # Try to find a working headshot
            new_url = await self.find_headshot_for_player(player)
            
            if new_url:
                # Update master hub
                await self.hub.update_one(
                    {'_id': player['_id']},
                    {'$set': {
                        'headshot_url': new_url,
                        'photo_url': new_url,
                        'headshot_validated': True
                    }}
                )
                results["fixed"] += 1
                results["fixed_players"].append(player_name)
                logger.info(f"[HEADSHOT_SCRAPER] Fixed: {player_name}")
            else:
                results["still_missing"] += 1
                results["missing_players"].append({
                    "name": player_name,
                    "nba_id": player.get('nba_player_id'),
                    "player_id": player.get('player_id')
                })
            
            # Progress logging
            if (i + 1) % 50 == 0:
                logger.info(f"[HEADSHOT_SCRAPER] Progress: {i + 1}/{len(players)}")
            
            # Small delay to avoid rate limiting
            await asyncio.sleep(0.1)
        
        # Close HTTP client
        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None
        
        logger.info(
            f"[HEADSHOT_SCRAPER] Complete: "
            f"{results['already_valid']} valid, "
            f"{results['fixed']} fixed, "
            f"{results['still_missing']} still missing"
        )
        
        return results
    
    async def fix_single_player(self, player_name: str) -> Dict:
        """Fix headshot for a single player."""
        player = await self.hub.find_one({
            'display_name': {'$regex': player_name, '$options': 'i'}
        })
        
        if not player:
            return {"success": False, "error": f"Player not found: {player_name}"}
        
        new_url = await self.find_headshot_for_player(player)
        
        if new_url:
            await self.hub.update_one(
                {'_id': player['_id']},
                {'$set': {
                    'headshot_url': new_url,
                    'photo_url': new_url,
                    'headshot_validated': True
                }}
            )
            
            # Close HTTP client
            if self.http_client:
                await self.http_client.aclose()
                self.http_client = None
            
            return {
                "success": True,
                "player_name": player.get('display_name'),
                "headshot_url": new_url
            }
        
        return {
            "success": False,
            "player_name": player.get('display_name'),
            "error": "No valid headshot found from any source"
        }


def get_headshot_scraper(db: AsyncIOMotorDatabase) -> HeadshotScraperService:
    """Get headshot scraper service instance."""
    return HeadshotScraperService(db)
