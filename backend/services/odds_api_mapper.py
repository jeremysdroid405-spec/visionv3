"""
ODDS API MAPPING MASTER
========================

Permanent mapping between nba_master_hub_2026 and The Odds API V4 player name strings.

This module ensures zero "Player Not Found" errors by providing:
1. A static mapping collection (odds_api_mapping_master)
2. Fast O(1) lookups from Odds API player names to player_ids
3. Reverse lookups from player_ids to Odds API names

CONSTRAINT: All Odds API player matching MUST go through this mapper.
"""

import asyncio
import logging
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================

COLLECTION_NAME = "odds_api_mapping_master"
MASTER_HUB_COLLECTION = "nba_master_hub_2026"

# Local headshot storage
HEADSHOT_DIR = Path("/app/backend/static/player-headshots")
LOCAL_URL_PATTERN = "/static/player-headshots/{nba_id}.png"

# Patterns that indicate a team logo (NOT a player headshot)
TEAM_LOGO_PATTERNS = [
    "cdn.nba.com/logos/nba/",
    "/logos/nba/",
    "/global/L/logo",
    "/global/D/logo",
    "team-logos",
]


def is_team_logo_url(url: str) -> bool:
    """Check if a URL is a team logo instead of a player headshot."""
    if not url:
        return False
    url_lower = url.lower()
    return any(pattern.lower() in url_lower for pattern in TEAM_LOGO_PATTERNS)


def get_canonical_photo_url(nba_id: int) -> str:
    """
    Get the canonical photo URL for a player.
    
    Priority:
    1. Local file if it exists
    2. Proxy URL as fallback
    """
    if nba_id:
        local_path = HEADSHOT_DIR / f"{nba_id}.png"
        if local_path.exists():
            return LOCAL_URL_PATTERN.format(nba_id=nba_id)
        # Fallback to proxy
        return f"/api/proxy/nba-headshot/{nba_id}"
    return None


def sanitize_photo_url(photo_url: str, nba_id: int = None) -> str:
    """
    Sanitize photo URL - use local file or fallback to proxy.
    """
    # If we have nba_id, always use canonical URL (local or proxy)
    if nba_id:
        return get_canonical_photo_url(nba_id)
    
    # Team logos should never be used
    if is_team_logo_url(photo_url):
        return None
    
    return photo_url


class OddsApiMapper:
    """
    Permanent mapping between Odds API V4 player names and nba_master_hub_2026 player_ids.
    
    The mapping is stored in a static collection and loaded into memory for O(1) lookups.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.mapping_collection = db[COLLECTION_NAME]
        self.master_hub = db[MASTER_HUB_COLLECTION]
        
        # In-memory lookup tables (loaded on initialization)
        self._odds_name_to_player_id: Dict[str, str] = {}  # odds_api_name -> player_id
        self._player_id_to_odds_name: Dict[str, str] = {}  # player_id -> odds_api_name
        self._player_id_to_full_data: Dict[str, Dict] = {}  # player_id -> full player object
        self._is_loaded = False
        self._last_loaded: Optional[datetime] = None
    
    # ==================== CORE LOOKUP FUNCTIONS ====================
    
    def getPlayerIdFromOddsName(self, odds_api_name: str) -> Optional[str]:
        """
        THE PRIMARY LOOKUP - Get player_id from Odds API player name.
        
        Args:
            odds_api_name: The exact player name string from Odds API V4
            
        Returns:
            player_id if found, None otherwise
        """
        if not self._is_loaded:
            logger.warning("[ODDS_MAPPER] Mapping not loaded. Call loadMapping() first.")
            return None
        
        # Exact match first (case-insensitive via lowercase key)
        player_id = self._odds_name_to_player_id.get(odds_api_name.lower().strip())
        
        if not player_id:
            # Try without common suffixes
            clean_name = odds_api_name.strip()
            for suffix in [" Jr.", " III", " II", " IV", " Sr."]:
                if clean_name.endswith(suffix):
                    clean_name = clean_name[:-len(suffix)]
                    player_id = self._odds_name_to_player_id.get(clean_name.lower())
                    if player_id:
                        break
        
        return player_id
    
    def getFullPlayerData(self, odds_api_name: str) -> Optional[Dict[str, Any]]:
        """
        Get complete player data from Odds API name.
        Returns the full player object from nba_master_hub_2026.
        """
        player_id = self.getPlayerIdFromOddsName(odds_api_name)
        if player_id:
            # player_id can be int or str - always convert to str for lookup
            return self._player_id_to_full_data.get(str(player_id))
        return None
    
    def getOddsNameFromPlayerId(self, player_id: str) -> Optional[str]:
        """Reverse lookup - get Odds API name from player_id."""
        return self._player_id_to_odds_name.get(player_id)
    
    def getAllMappings(self) -> Dict[str, str]:
        """Get all mappings (odds_api_name -> player_id)."""
        return dict(self._odds_name_to_player_id)
    
    # ==================== MAPPING MANAGEMENT ====================
    
    async def loadMapping(self, force_reload: bool = False) -> Dict[str, Any]:
        """
        Load the mapping into memory from MongoDB.
        
        This should be called once at startup and the mapping stays in memory.
        Force reload can be used after a rebuild.
        
        IMPORTANT: Merges mapping docs with nba_master_hub_2026 data to ensure
        _player_id_to_full_data contains complete player info including photo_url.
        """
        if self._is_loaded and not force_reload:
            return {
                "status": "already_loaded",
                "count": len(self._odds_name_to_player_id),
                "last_loaded": self._last_loaded.isoformat() if self._last_loaded else None
            }
        
        logger.info("[ODDS_MAPPER] Loading mapping into memory...")
        
        # Clear existing
        self._odds_name_to_player_id.clear()
        self._player_id_to_odds_name.clear()
        self._player_id_to_full_data.clear()
        
        # STEP 1: Load master hub data for enrichment (photo_url, nba_id, etc.)
        hub_cursor = self.master_hub.find({}, {"_id": 0})
        hub_players = await hub_cursor.to_list(length=2000)
        
        # Build lookup by bdl_id (primary join key)
        hub_lookup = {
            str(p.get("bdl_id")): p
            for p in hub_players
            if p.get("bdl_id")
        }
        logger.info(f"[ODDS_MAPPER] Loaded {len(hub_lookup)} players from master hub for enrichment")
        
        # STEP 2: Load mapping documents
        cursor = self.mapping_collection.find({}, {"_id": 0})
        mappings = await cursor.to_list(length=2000)
        
        enriched_count = 0
        for m in mappings:
            odds_name = m.get("odds_api_name", "")
            # Use bdl_id as primary, fall back to player_id for legacy
            player_id = m.get("bdl_id") or m.get("player_id", "")
            
            if odds_name and player_id:
                self._odds_name_to_player_id[odds_name.lower()] = player_id
                self._player_id_to_odds_name[str(player_id)] = odds_name
                
                # STEP 3: Merge mapping doc with master hub data
                # Hub data takes precedence for photo_url, nba_id, etc.
                hub_record = hub_lookup.get(str(player_id), {})
                merged = {**m, **hub_record}
                
                # CRITICAL: Sanitize photo_url - replace team logos with headshot proxy
                nba_id = merged.get("nba_id")
                raw_photo = merged.get("photo_url") or merged.get("headshot_url")
                merged["photo_url"] = sanitize_photo_url(raw_photo, nba_id)
                merged["headshot_url"] = merged["photo_url"]  # Keep in sync
                
                self._player_id_to_full_data[str(player_id)] = merged
                
                if hub_record:
                    enriched_count += 1
        
        self._is_loaded = True
        self._last_loaded = datetime.now(timezone.utc)
        
        logger.info(f"[ODDS_MAPPER] Loaded {len(self._odds_name_to_player_id)} mappings ({enriched_count} enriched with hub data)")
        
        return {
            "status": "loaded",
            "count": len(self._odds_name_to_player_id),
            "enriched_count": enriched_count,
            "last_loaded": self._last_loaded.isoformat()
        }
    
    async def rebuildMapping(self) -> Dict[str, Any]:
        """
        REBUILD MAPPING - Generate odds_api_mapping_master from nba_master_hub_2026.
        
        This creates a permanent static mapping by:
        1. Reading all players from nba_master_hub_2026
        2. Using display_name as the odds_api_name (what Odds API returns)
        3. Storing in odds_api_mapping_master for fast lookups
        
        Should be run:
        - Once initially to populate the collection
        - After any mass update to nba_master_hub_2026
        """
        logger.info("=" * 60)
        logger.info("[ODDS_MAPPER] REBUILDING MAPPING FROM MASTER HUB")
        logger.info("=" * 60)
        
        rebuild_start = datetime.now(timezone.utc)
        results = {
            "started_at": rebuild_start.isoformat(),
            "players_processed": 0,
            "mappings_created": 0,
            "missing_odds_name": 0,
            "errors": []
        }
        
        try:
            # Step A: Iterate through nba_master_hub_2026 (all records have bdl_id)
            logger.info("[ODDS_MAPPER] Step A: Reading from nba_master_hub_2026...")
            
            cursor = self.master_hub.find({"bdl_id": {"$exists": True}}, {"_id": 0})
            all_players = await cursor.to_list(length=2000)
            
            results["players_processed"] = len(all_players)
            logger.info(f"[ODDS_MAPPER] Found {len(all_players)} players in master hub")
            
            # Step B: Generate mapping documents
            logger.info("[ODDS_MAPPER] Step B: Generating mapping documents...")
            
            mapping_docs = []
            for player in all_players:
                # Use bdl_id as primary identifier (strictly number-based)
                bdl_id = player.get("bdl_id")
                display_name = player.get("display_name")
                
                if not bdl_id:
                    continue
                
                # Use display_name as the odds_api_name (what the Odds API returns)
                effective_odds_name = display_name
                
                if not effective_odds_name:
                    results["missing_odds_name"] += 1
                    logger.warning(f"[ODDS_MAPPER] No display_name for bdl_id: {bdl_id}")
                    continue
                
                # Create mapping document with bdl_id as primary key
                mapping_doc = {
                    "bdl_id": bdl_id,
                    "player_id": str(bdl_id),  # Legacy compatibility
                    "odds_api_name": effective_odds_name,
                    "odds_api_name_lower": effective_odds_name.lower(),
                    "display_name": display_name,
                    "normalized_name": player.get("normalized_name"),
                    "team": player.get("team"),
                    "team_full_name": player.get("team_full_name"),
                    "position": player.get("profile", {}).get("position"),
                    "nba_id": player.get("nba_id"),  # For photo proxy URL generation
                    # Photo URL: prefer photo_url from master hub, fallback to headshot_url
                    "photo_url": player.get("photo_url") or player.get("headshot_url"),
                    "headshot_url": player.get("photo_url") or player.get("headshot_url"),  # Legacy compat
                    "baseline_stats": player.get("baseline_stats"),
                    "created_at": rebuild_start.isoformat(),
                    "source": "nba_master_hub_2026"
                }
                
                mapping_docs.append(mapping_doc)
            
            # Step C: Write to odds_api_mapping_master
            logger.info("[ODDS_MAPPER] Step C: Writing to odds_api_mapping_master...")
            
            # Clear existing mappings and drop old indexes
            await self.mapping_collection.drop()
            
            if mapping_docs:
                await self.mapping_collection.insert_many(mapping_docs)
                
                # Create indexes for fast lookups (use bdl_id as primary)
                await self.mapping_collection.create_index("bdl_id", unique=True)
                await self.mapping_collection.create_index("odds_api_name_lower")
                await self.mapping_collection.create_index("odds_api_name")
            
            results["mappings_created"] = len(mapping_docs)
            results["success"] = True
            
            # Reload into memory
            await self.loadMapping(force_reload=True)
            
        except Exception as e:
            logger.error(f"[ODDS_MAPPER] Rebuild error: {e}")
            results["success"] = False
            results["errors"].append(str(e))
        
        results["completed_at"] = datetime.now(timezone.utc).isoformat()
        results["duration_seconds"] = (datetime.now(timezone.utc) - rebuild_start).total_seconds()
        
        logger.info(f"[ODDS_MAPPER] Rebuild complete: {results['mappings_created']} mappings created")
        logger.info("=" * 60)
        
        return results
    
    async def getStats(self) -> Dict[str, Any]:
        """Get mapping statistics."""
        total_mappings = await self.mapping_collection.count_documents({})
        
        # Count by team
        pipeline = [
            {"$group": {"_id": "$team", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}}
        ]
        team_counts = await self.mapping_collection.aggregate(pipeline).to_list(length=50)
        
        return {
            "total_mappings": total_mappings,
            "in_memory_count": len(self._odds_name_to_player_id),
            "is_loaded": self._is_loaded,
            "last_loaded": self._last_loaded.isoformat() if self._last_loaded else None,
            "by_team": {t["_id"]: t["count"] for t in team_counts if t["_id"]}
        }
    
    async def lookupBatch(self, odds_api_names: List[str]) -> Dict[str, Optional[Dict]]:
        """
        Batch lookup - get player data for multiple Odds API names at once.
        Returns dict mapping odds_api_name -> player_data (or None if not found).
        """
        results = {}
        not_found = []
        
        for name in odds_api_names:
            player_data = self.getFullPlayerData(name)
            results[name] = player_data
            if not player_data:
                not_found.append(name)
        
        if not_found:
            logger.warning(f"[ODDS_MAPPER] {len(not_found)} players not found in mapping: {not_found[:5]}...")
        
        return results


# ==================== SINGLETON INSTANCE ====================

_odds_api_mapper: Optional[OddsApiMapper] = None


def get_odds_api_mapper(db: AsyncIOMotorDatabase = None) -> OddsApiMapper:
    """Get or create the OddsApiMapper singleton."""
    global _odds_api_mapper
    if _odds_api_mapper is None:
        if db is None:
            raise ValueError("Database required for first initialization")
        _odds_api_mapper = OddsApiMapper(db)
    return _odds_api_mapper


async def init_odds_api_mapper(db: AsyncIOMotorDatabase) -> OddsApiMapper:
    """Initialize the mapper and load mappings into memory."""
    global _odds_api_mapper
    _odds_api_mapper = OddsApiMapper(db)
    await _odds_api_mapper.loadMapping()
    return _odds_api_mapper


# ==================== PUBLIC API ====================

async def getPlayerIdFromOddsName(odds_api_name: str) -> Optional[str]:
    """Public API - Get player_id from Odds API name."""
    if _odds_api_mapper is None:
        logger.error("[ODDS_MAPPER] Mapper not initialized")
        return None
    return _odds_api_mapper.getPlayerIdFromOddsName(odds_api_name)


async def getFullPlayerData(odds_api_name: str) -> Optional[Dict[str, Any]]:
    """Public API - Get full player data from Odds API name."""
    if _odds_api_mapper is None:
        logger.error("[ODDS_MAPPER] Mapper not initialized")
        return None
    return _odds_api_mapper.getFullPlayerData(odds_api_name)


async def rebuildMapping() -> Dict[str, Any]:
    """Public API - Rebuild the mapping."""
    if _odds_api_mapper is None:
        logger.error("[ODDS_MAPPER] Mapper not initialized")
        return {"success": False, "error": "Mapper not initialized"}
    return await _odds_api_mapper.rebuildMapping()
