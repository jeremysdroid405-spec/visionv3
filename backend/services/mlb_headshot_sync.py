"""
MLB Headshot Sync Service
==========================
Multi-step process to sync official MLB headshots:

1. ID Discovery: Search MLB API for official_mlb_id
2. Headshot Fetch: Download from MLB CDN using official ID
3. Fallback: ESPN CDN for unmapped players
4. Local Storage: /app/frontend/public/images/mlb_headshots/

MLB Search API: https://statsapi.mlb.com/api/v1/people/search?names={name}
MLB CDN: https://img.mlbstatic.com/mlb-photos/image/upload/d_player_profile_default_image.png/w_426,q_auto:best/v1/people/{id}/headshot/67/current
ESPN Fallback: https://a.espncdn.com/combiner/i?img=/i/headshots/mlb/players/full/{id}.png
"""

import os
import asyncio
import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
from pathlib import Path
import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.db_config import get_collection_name

logger = logging.getLogger(__name__)

# API URLs
MLB_SEARCH_API = "https://statsapi.mlb.com/api/v1/people/search"
MLB_CDN_URL = "https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:67:current.png/w_426,q_auto:best/v1/people/{mlb_id}/headshot/67/current"
ESPN_FALLBACK_URL = "https://a.espncdn.com/combiner/i?img=/i/headshots/mlb/players/full/{mlb_id}.png&w=350&h=254"

# Local storage paths
HEADSHOT_DIR = Path("/app/frontend/public/images/mlb_headshots")
ERROR_LOG_PATH = Path("/app/backend/logs/mlb_mapping_errors.log")

# Rate limiting
SEARCH_DELAY = 0.3  # Seconds between MLB API calls
DOWNLOAD_DELAY = 0.1  # Seconds between headshot downloads
BATCH_SIZE = 50  # Players per batch


class MLBHeadshotSyncService:
    """
    Syncs official MLB headshots for all players in mlb_master_hub_2026.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self._client: Optional[httpx.AsyncClient] = None
        self._setup_directories()
    
    def _setup_directories(self):
        """Ensure directories exist."""
        HEADSHOT_DIR.mkdir(parents=True, exist_ok=True)
        ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                limits=httpx.Limits(max_connections=10)
            )
        return self._client
    
    async def close_client(self):
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    def _get_collection(self, base_name: str):
        """Get MLB-specific collection."""
        collection_name = get_collection_name(base_name, "mlb")
        return self.db[collection_name]
    
    # =========================================================================
    # PHASE 1: ID DISCOVERY
    # =========================================================================
    
    async def search_mlb_player(self, player_name: str) -> Optional[Dict]:
        """
        Search MLB API for a player by name.
        
        Args:
            player_name: Full player name (e.g., "Shohei Ohtani")
            
        Returns:
            Player data with official MLB ID, or None if not found
        """
        client = await self._get_client()
        
        try:
            url = MLB_SEARCH_API
            params = {"names": player_name}
            
            response = await client.get(url, params=params)
            
            if response.status_code == 200:
                data = response.json()
                people = data.get("people", [])
                
                if people:
                    # Return first match (most relevant)
                    player = people[0]
                    return {
                        "official_mlb_id": player.get("id"),
                        "full_name": player.get("fullName"),
                        "first_name": player.get("firstName"),
                        "last_name": player.get("lastName"),
                        "primary_position": player.get("primaryPosition", {}).get("abbreviation"),
                        "current_team": player.get("currentTeam", {}).get("name"),
                        "active": player.get("active", False),
                        "mlb_debut": player.get("mlbDebutDate")
                    }
                
                return None
            else:
                logger.warning(f"[MLB_SEARCH] API returned {response.status_code} for '{player_name}'")
                return None
                
        except Exception as e:
            logger.error(f"[MLB_SEARCH] Error searching for '{player_name}': {e}")
            return None
    
    async def discover_mlb_ids(self, limit: int = None) -> Dict[str, Any]:
        """
        Discover official MLB IDs for all players in master_hub.
        
        Args:
            limit: Optional limit on players to process
            
        Returns:
            Discovery results with counts
        """
        logger.info("=" * 70)
        logger.info("[MLB_HEADSHOTS] Phase 1: ID Discovery")
        logger.info("=" * 70)
        
        start_time = datetime.now(timezone.utc)
        
        results = {
            "phase": "id_discovery",
            "started_at": start_time.isoformat(),
            "players_processed": 0,
            "ids_found": 0,
            "ids_missing": 0,
            "already_mapped": 0,
            "errors": []
        }
        
        master_hub = self._get_collection("master_hub")
        
        # Find players without official_mlb_id
        query = {
            "$or": [
                {"official_mlb_id": {"$exists": False}},
                {"official_mlb_id": None}
            ]
        }
        
        cursor = master_hub.find(query, {"display_name": 1, "team_abbr": 1, "bdl_id": 1})
        if limit:
            cursor = cursor.limit(limit)
        
        players = await cursor.to_list(length=None)
        
        # Check how many already have IDs
        already_mapped = await master_hub.count_documents({"official_mlb_id": {"$exists": True, "$ne": None}})
        results["already_mapped"] = already_mapped
        
        logger.info(f"[MLB_HEADSHOTS] Found {len(players)} players needing ID lookup")
        logger.info(f"[MLB_HEADSHOTS] {already_mapped} players already have official_mlb_id")
        
        unmapped_players = []
        
        for i, player in enumerate(players):
            player_name = player.get("display_name", "")
            if not player_name:
                continue
            
            results["players_processed"] += 1
            
            # Search MLB API
            mlb_data = await self.search_mlb_player(player_name)
            
            if mlb_data and mlb_data.get("official_mlb_id"):
                # Update player with official MLB ID
                await master_hub.update_one(
                    {"_id": player["_id"]},
                    {
                        "$set": {
                            "official_mlb_id": mlb_data["official_mlb_id"],
                            "mlb_full_name": mlb_data.get("full_name"),
                            "mlb_position": mlb_data.get("primary_position"),
                            "mlb_active": mlb_data.get("active"),
                            "mlb_id_mapped_at": datetime.now(timezone.utc).isoformat()
                        }
                    }
                )
                results["ids_found"] += 1
                
                if (i + 1) % 25 == 0:
                    logger.info(f"[MLB_HEADSHOTS] Progress: {i + 1}/{len(players)} - Found {results['ids_found']} IDs")
            else:
                # Log unmapped player
                unmapped_players.append(player_name)
                results["ids_missing"] += 1
            
            # Rate limiting
            await asyncio.sleep(SEARCH_DELAY)
        
        # Write unmapped players to error log
        if unmapped_players:
            self._log_mapping_errors(unmapped_players)
        
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        results["duration_seconds"] = round(duration, 2)
        
        logger.info("[MLB_HEADSHOTS] Phase 1 Complete:")
        logger.info(f"  • Processed: {results['players_processed']}")
        logger.info(f"  • IDs Found: {results['ids_found']}")
        logger.info(f"  • IDs Missing: {results['ids_missing']}")
        logger.info(f"  • Duration: {duration:.1f}s")
        
        return results
    
    def _log_mapping_errors(self, player_names: List[str]):
        """Log unmapped players to error file."""
        timestamp = datetime.now(timezone.utc).isoformat()
        
        with open(ERROR_LOG_PATH, "a") as f:
            f.write(f"\n# Mapping Errors - {timestamp}\n")
            for name in player_names:
                f.write(f"{name}\n")
        
        logger.info(f"[MLB_HEADSHOTS] Logged {len(player_names)} unmapped players to {ERROR_LOG_PATH}")
    
    # =========================================================================
    # PHASE 2: HEADSHOT FETCH
    # =========================================================================
    
    async def download_headshot(
        self,
        mlb_id: int,
        player_name: str,
        use_espn_fallback: bool = False
    ) -> Tuple[bool, str]:
        """
        Download a player's headshot.
        
        Args:
            mlb_id: Official MLB ID
            player_name: Player name for logging
            use_espn_fallback: Use ESPN CDN instead of MLB CDN
            
        Returns:
            Tuple of (success, local_path or error message)
        """
        client = await self._get_client()
        local_path = HEADSHOT_DIR / f"{mlb_id}.png"
        
        # Skip if already downloaded
        if local_path.exists():
            return True, str(local_path)
        
        # Choose URL
        if use_espn_fallback:
            url = ESPN_FALLBACK_URL.format(mlb_id=mlb_id)
        else:
            url = MLB_CDN_URL.format(mlb_id=mlb_id)
        
        try:
            response = await client.get(url)
            
            if response.status_code == 200:
                content_type = response.headers.get("content-type", "")
                
                # Verify it's an image
                if "image" in content_type or len(response.content) > 1000:
                    with open(local_path, "wb") as f:
                        f.write(response.content)
                    return True, str(local_path)
                else:
                    return False, f"Invalid content type: {content_type}"
            else:
                # Try ESPN fallback if MLB CDN fails
                if not use_espn_fallback:
                    return await self.download_headshot(mlb_id, player_name, use_espn_fallback=True)
                return False, f"HTTP {response.status_code}"
                
        except Exception as e:
            # Try ESPN fallback on error
            if not use_espn_fallback:
                return await self.download_headshot(mlb_id, player_name, use_espn_fallback=True)
            return False, str(e)
    
    async def fetch_headshots(self, limit: int = None) -> Dict[str, Any]:
        """
        Fetch headshots for all players with official_mlb_id.
        
        Args:
            limit: Optional limit on players to process
            
        Returns:
            Fetch results with counts
        """
        logger.info("=" * 70)
        logger.info("[MLB_HEADSHOTS] Phase 2: Headshot Fetch")
        logger.info("=" * 70)
        
        start_time = datetime.now(timezone.utc)
        
        results = {
            "phase": "headshot_fetch",
            "started_at": start_time.isoformat(),
            "players_processed": 0,
            "downloaded": 0,
            "already_cached": 0,
            "failed": 0,
            "errors": []
        }
        
        master_hub = self._get_collection("master_hub")
        
        # Find players with official_mlb_id
        query = {"official_mlb_id": {"$exists": True, "$ne": None}}
        cursor = master_hub.find(query, {"display_name": 1, "official_mlb_id": 1})
        if limit:
            cursor = cursor.limit(limit)
        
        players = await cursor.to_list(length=None)
        
        logger.info(f"[MLB_HEADSHOTS] Found {len(players)} players with official_mlb_id")
        
        for i, player in enumerate(players):
            mlb_id = player.get("official_mlb_id")
            player_name = player.get("display_name", "Unknown")
            
            if not mlb_id:
                continue
            
            results["players_processed"] += 1
            
            # Check if already cached
            local_path = HEADSHOT_DIR / f"{mlb_id}.png"
            if local_path.exists():
                results["already_cached"] += 1
                # Update player with headshot path
                await master_hub.update_one(
                    {"_id": player["_id"]},
                    {"$set": {"headshot_local": f"/images/mlb_headshots/{mlb_id}.png"}}
                )
                continue
            
            # Download headshot
            success, result = await self.download_headshot(mlb_id, player_name)
            
            if success:
                results["downloaded"] += 1
                # Update player with headshot path
                await master_hub.update_one(
                    {"_id": player["_id"]},
                    {"$set": {"headshot_local": f"/images/mlb_headshots/{mlb_id}.png"}}
                )
            else:
                results["failed"] += 1
                results["errors"].append(f"{player_name}: {result}")
            
            # Progress logging
            if (i + 1) % 50 == 0:
                logger.info(
                    f"[MLB_HEADSHOTS] Progress: {i + 1}/{len(players)} - "
                    f"Downloaded: {results['downloaded']}, Cached: {results['already_cached']}"
                )
            
            # Rate limiting
            await asyncio.sleep(DOWNLOAD_DELAY)
        
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        results["duration_seconds"] = round(duration, 2)
        results["headshot_dir"] = str(HEADSHOT_DIR)
        
        logger.info("[MLB_HEADSHOTS] Phase 2 Complete:")
        logger.info(f"  • Processed: {results['players_processed']}")
        logger.info(f"  • Downloaded: {results['downloaded']}")
        logger.info(f"  • Already Cached: {results['already_cached']}")
        logger.info(f"  • Failed: {results['failed']}")
        logger.info(f"  • Duration: {duration:.1f}s")
        
        return results
    
    # =========================================================================
    # FULL SYNC
    # =========================================================================
    
    async def run_full_sync(self, limit: int = None) -> Dict[str, Any]:
        """
        Run the complete headshot sync process.
        
        1. Discover MLB IDs for unmapped players
        2. Download headshots for all mapped players
        
        Args:
            limit: Optional limit on players per phase
            
        Returns:
            Combined results from both phases
        """
        logger.info("=" * 70)
        logger.info("[MLB_HEADSHOTS] Starting Full Headshot Sync")
        logger.info("=" * 70)
        
        start_time = datetime.now(timezone.utc)
        
        results = {
            "started_at": start_time.isoformat(),
            "phase_1_id_discovery": None,
            "phase_2_headshot_fetch": None
        }
        
        try:
            # Phase 1: ID Discovery
            results["phase_1_id_discovery"] = await self.discover_mlb_ids(limit)
            
            # Phase 2: Headshot Fetch
            results["phase_2_headshot_fetch"] = await self.fetch_headshots(limit)
            
            results["success"] = True
            
        except Exception as e:
            logger.error(f"[MLB_HEADSHOTS] Sync error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results["success"] = False
            results["error"] = str(e)
        
        finally:
            await self.close_client()
        
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        results["total_duration_seconds"] = round(duration, 2)
        
        return results
    
    async def get_sync_status(self) -> Dict[str, Any]:
        """Get current sync status."""
        master_hub = self._get_collection("master_hub")
        
        total_players = await master_hub.count_documents({})
        with_mlb_id = await master_hub.count_documents({"official_mlb_id": {"$exists": True, "$ne": None}})
        with_headshot = await master_hub.count_documents({"headshot_local": {"$exists": True, "$ne": None}})
        
        # Count local files
        local_files = len(list(HEADSHOT_DIR.glob("*.png"))) if HEADSHOT_DIR.exists() else 0
        
        return {
            "total_players": total_players,
            "players_with_mlb_id": with_mlb_id,
            "players_with_headshot_path": with_headshot,
            "local_headshot_files": local_files,
            "headshot_dir": str(HEADSHOT_DIR),
            "coverage_percent": round((with_headshot / total_players) * 100, 1) if total_players > 0 else 0
        }


# Singleton instance
_mlb_headshot_service: Optional[MLBHeadshotSyncService] = None


def get_mlb_headshot_service(db: AsyncIOMotorDatabase) -> MLBHeadshotSyncService:
    """Get or create the MLB headshot sync service."""
    global _mlb_headshot_service
    if _mlb_headshot_service is None:
        _mlb_headshot_service = MLBHeadshotSyncService(db)
    return _mlb_headshot_service
