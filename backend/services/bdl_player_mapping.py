"""
BDL Player ID Mapping Service
==============================
Maintains a complete mapping of player names to BDL IDs.
Syncs all active players from BDL and creates aliases for name variations.

This eliminates the need for name-based searches during sync operations.
"""

import logging
import asyncio
import re
from typing import Dict, Optional, List, Any
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorDatabase
import httpx
import os

logger = logging.getLogger(__name__)

BDL_API_KEY = os.environ.get("BDL_API_KEY")
BDL_BASE_URL = "https://api.balldontlie.io/v1"

# Known name variations that BDL uses differently than PrizePicks/other sources
NAME_ALIASES = {
    # "BDL Name": ["PrizePicks variations", "Other variations"]
    "Nah'Shon Hyland": ["Bones Hyland", "Nahshon Hyland"],
    "Gregory Jackson II": ["G.G. Jackson", "GG Jackson", "Gregory Jackson"],
    "Herbert Jones": ["Herb Jones"],
    "Cameron Johnson": ["Cam Johnson"],
    "Jalen Johnson": ["J. Johnson"],
    "Patrick Williams": ["Pat Williams"],
    "Jabari Smith Jr.": ["Jabari Smith Jr", "Jabari Smith"],
    "Michael Porter Jr.": ["Michael Porter Jr", "MPJ"],
    "Jaren Jackson Jr.": ["Jaren Jackson Jr", "JJJ"],
    "Gary Trent Jr.": ["Gary Trent Jr"],
    "Kelly Oubre Jr.": ["Kelly Oubre Jr"],
    "Tim Hardaway Jr.": ["Tim Hardaway Jr"],
    "Wendell Carter Jr.": ["Wendell Carter Jr"],
    "Larry Nance Jr.": ["Larry Nance Jr"],
    "Marcus Morris Sr.": ["Marcus Morris Sr", "Marcus Morris"],
    "Robert Williams III": ["Robert Williams III", "Time Lord", "Robert Williams"],
    "Lonnie Walker IV": ["Lonnie Walker IV", "Lonnie Walker"],
    "Kenyon Martin Jr.": ["Kenyon Martin Jr", "KJ Martin"],
    "Otto Porter Jr.": ["Otto Porter Jr", "Otto Porter"],
    "Dennis Smith Jr.": ["Dennis Smith Jr"],
    "Marvin Bagley III": ["Marvin Bagley III", "Marvin Bagley"],
    "Kevin Knox II": ["Kevin Knox II", "Kevin Knox"],
    "Tre Jones": ["Tre' Jones"],
    "Cody Williams": ["C. Williams"],
}


def _normalize_name(name: str) -> str:
    """Normalize player name for comparison."""
    if not name:
        return ""
    # Lowercase, remove periods, extra spaces, and suffixes for comparison
    normalized = name.lower().strip()
    normalized = re.sub(r'\s+', ' ', normalized)
    normalized = normalized.replace('.', '')
    return normalized


class BDLPlayerMappingService:
    """
    Service to maintain BDL player ID mappings.
    
    Collections:
    - bdl_player_mapping: {name_key: str, bdl_id: int, display_name: str, aliases: [str]}
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.mapping_collection = db.bdl_player_mapping
        self._cache: Dict[str, int] = {}  # name_key -> bdl_id
        self._loaded = False
    
    async def _make_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Make authenticated request to BDL API."""
        url = f"{BDL_BASE_URL}{endpoint}"
        headers = {"Authorization": BDL_API_KEY}
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(url, headers=headers, params=params)
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 429:
                    wait_time = int(response.headers.get("Retry-After", 5))
                    logger.warning(f"[BDL_MAPPING] Rate limited, waiting {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    return await self._make_request(endpoint, params)
                else:
                    logger.error(f"[BDL_MAPPING] {endpoint} returned {response.status_code}")
                    return None
            except Exception as e:
                logger.error(f"[BDL_MAPPING] Request error: {e}")
                return None
    
    async def load_cache(self):
        """Load all mappings from MongoDB into memory cache."""
        if self._loaded:
            return
        
        mappings = await self.mapping_collection.find({}, {'_id': 0}).to_list(3000)
        for m in mappings:
            name_key = m.get('name_key')
            bdl_id = m.get('bdl_id')
            if name_key and bdl_id:
                self._cache[name_key] = bdl_id
                # Also cache aliases
                for alias in m.get('aliases', []):
                    alias_key = _normalize_name(alias)
                    self._cache[alias_key] = bdl_id
        
        logger.info(f"[BDL_MAPPING] Loaded {len(self._cache)} name-to-ID mappings into cache")
        self._loaded = True
    
    async def get_bdl_id(self, player_name: str) -> Optional[int]:
        """
        Get BDL player ID for a given name.
        
        First checks cache, then falls back to API search if not found.
        """
        await self.load_cache()
        
        name_key = _normalize_name(player_name)
        
        # Check cache first
        if name_key in self._cache:
            return self._cache[name_key]
        
        # Check aliases
        for bdl_name, aliases in NAME_ALIASES.items():
            for alias in aliases:
                if _normalize_name(alias) == name_key:
                    bdl_key = _normalize_name(bdl_name)
                    if bdl_key in self._cache:
                        return self._cache[bdl_key]
        
        # Not in cache - try API search
        logger.debug(f"[BDL_MAPPING] Cache miss for '{player_name}', searching API...")
        bdl_id = await self._search_and_store(player_name)
        return bdl_id
    
    async def _search_and_store(self, name: str) -> Optional[int]:
        """Search BDL API for player and store mapping if found."""
        # Try last name search
        name_parts = name.strip().split()
        search_term = name_parts[-1] if name_parts else name
        
        # Remove suffix for search
        search_term = re.sub(r'\s*(jr\.?|sr\.?|ii|iii|iv)$', '', search_term, flags=re.I).strip()
        
        params = {"search": search_term, "per_page": 50}
        data = await self._make_request("/players", params)
        
        if not data:
            return None
        
        players = data.get("data", [])
        normalized_search = _normalize_name(name)
        
        for player in players:
            full_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
            if _normalize_name(full_name) == normalized_search:
                bdl_id = player.get('id')
                await self._store_mapping(full_name, bdl_id, [name])
                return bdl_id
            
            # Try without suffix
            full_name_no_suffix = re.sub(r'\s*(jr\.?|sr\.?|ii|iii|iv)$', '', full_name, flags=re.I).strip()
            search_no_suffix = re.sub(r'\s*(jr\.?|sr\.?|ii|iii|iv)$', '', name, flags=re.I).strip()
            if _normalize_name(full_name_no_suffix) == _normalize_name(search_no_suffix):
                bdl_id = player.get('id')
                await self._store_mapping(full_name, bdl_id, [name])
                return bdl_id
        
        logger.warning(f"[BDL_MAPPING] Player not found: {name}")
        return None
    
    async def _store_mapping(self, bdl_name: str, bdl_id: int, aliases: List[str] = None):
        """Store a name-to-ID mapping in MongoDB and cache."""
        name_key = _normalize_name(bdl_name)
        all_aliases = list(set(aliases or []))  # Deduplicate
        
        doc = {
            "name_key": name_key,
            "bdl_id": bdl_id,
            "display_name": bdl_name,
            "aliases": all_aliases,
            "updated_at": datetime.now(timezone.utc)
        }
        
        # Use simple upsert without $addToSet to avoid conflict
        await self.mapping_collection.update_one(
            {"bdl_id": bdl_id},
            {"$set": doc},
            upsert=True
        )
        
        # Update cache
        self._cache[name_key] = bdl_id
        for alias in all_aliases:
            self._cache[_normalize_name(alias)] = bdl_id
    
    async def sync_all_active_players(self) -> Dict[str, Any]:
        """
        Sync ALL active players from BDL to build complete mapping.
        This should be run daily to keep mappings up to date.
        """
        logger.info("[BDL_MAPPING] Starting full active player sync...")
        
        all_players = []
        cursor = None
        
        while True:
            params = {"per_page": 100}
            if cursor:
                params["cursor"] = cursor
            
            data = await self._make_request("/players/active", params)
            if not data:
                break
            
            players = data.get("data", [])
            all_players.extend(players)
            
            # Check pagination
            meta = data.get("meta", {})
            next_cursor = meta.get("next_cursor")
            if not next_cursor:
                break
            cursor = next_cursor
        
        logger.info(f"[BDL_MAPPING] Fetched {len(all_players)} active players from BDL")
        
        # Store all mappings
        stored = 0
        for player in all_players:
            bdl_id = player.get('id')
            first = player.get('first_name', '')
            last = player.get('last_name', '')
            full_name = f"{first} {last}".strip()
            
            if bdl_id and full_name:
                # Get any known aliases
                aliases = NAME_ALIASES.get(full_name, [])
                await self._store_mapping(full_name, bdl_id, aliases)
                stored += 1
        
        self._loaded = False  # Force cache reload
        await self.load_cache()
        
        logger.info(f"[BDL_MAPPING] Stored {stored} player mappings")
        
        return {
            "success": True,
            "total_players": len(all_players),
            "mappings_stored": stored,
            "cache_size": len(self._cache)
        }
    
    async def get_all_bdl_ids(self, player_names: List[str]) -> Dict[str, Optional[int]]:
        """
        Get BDL IDs for multiple player names efficiently.
        Returns dict: {name: bdl_id or None}
        """
        await self.load_cache()
        
        results = {}
        for name in player_names:
            bdl_id = await self.get_bdl_id(name)
            results[name] = bdl_id
        
        return results


# Singleton instance
_mapping_service: Optional[BDLPlayerMappingService] = None


def get_bdl_mapping_service(db: AsyncIOMotorDatabase) -> BDLPlayerMappingService:
    """Get or create the BDL mapping service singleton."""
    global _mapping_service
    if _mapping_service is None:
        _mapping_service = BDLPlayerMappingService(db)
    return _mapping_service
