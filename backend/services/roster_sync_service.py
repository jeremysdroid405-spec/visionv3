"""
Roster Sync Service
===================
Updates player team/roster data from BallDontLie API to eliminate ghost data.

This service:
1. Updates team assignments for all players in master hub
2. Syncs season stats (BDL /season_averages endpoint)
3. Handles player name normalization (Jr., Sr., III, etc.)

Run this to fix trade-related data issues like Coby White (CHI -> CHA).
"""
import asyncio
import httpx
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

BDL_BASE_URL = "https://api.balldontlie.io/v1"


def _normalize_name(name: str) -> str:
    """Normalize player name for matching."""
    if not name:
        return ""
    normalized = name.lower().strip()
    normalized = normalized.replace(".", "").replace(",", "")
    for suffix in [" jr", " sr", " ii", " iii", " iv", " v"]:
        if normalized.endswith(suffix):
            normalized = normalized[:-len(suffix)].strip()
    return normalized


class RosterSyncService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.master_hub = db[COLL("master_hub", "nba")]
        self.api_key = os.environ.get("BDL_API_KEY", "")
        self.headers = {"Authorization": self.api_key}
    
    async def sync_player_from_bdl(self, player_name: str) -> Optional[Dict]:
        """
        Sync a single player's data from BDL API.
        
        Returns updated player data or None if not found.
        """
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                # Try searching with first name only (more reliable)
                name_parts = player_name.split()
                first_name = name_parts[0] if name_parts else player_name
                
                # Search for player
                search_resp = await client.get(
                    f"{BDL_BASE_URL}/players",
                    params={"search": first_name, "per_page": 50},
                    headers=self.headers
                )
                
                if search_resp.status_code != 200:
                    logger.warning(f"[ROSTER_SYNC] BDL search failed for {player_name}: {search_resp.status_code}")
                    return None
                
                data = search_resp.json()
                players = data.get("data", [])
                
                if not players:
                    logger.warning(f"[ROSTER_SYNC] Player not found in BDL: {player_name}")
                    return None
                
                # Find best match using normalized names
                normalized_search = _normalize_name(player_name)
                bdl_player = None
                
                for p in players:
                    bdl_name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
                    if _normalize_name(bdl_name) == normalized_search:
                        bdl_player = p
                        break
                
                if not bdl_player:
                    # Try partial match on last name
                    if len(name_parts) > 1:
                        last_name_lower = name_parts[-1].lower().replace(".", "")
                        for p in players:
                            if p.get('last_name', '').lower() == last_name_lower:
                                bdl_player = p
                                break
                
                if not bdl_player:
                    logger.warning(f"[ROSTER_SYNC] No match found for {player_name} in {len(players)} results")
                    return None
                
                bdl_id = bdl_player.get("id")
                team_info = bdl_player.get("team", {})
                
                # Get season averages
                season_resp = await client.get(
                    f"{BDL_BASE_URL}/season_averages",
                    params={"season": 2024, "player_id": bdl_id},
                    headers=self.headers
                )
                
                season_stats = {}
                if season_resp.status_code == 200:
                    season_data = season_resp.json().get("data", [])
                    if season_data:
                        season_stats = season_data[0]
                
                # Build update
                update_data = {
                    "bdl_id": bdl_id,
                    "team": team_info.get("abbreviation", ""),
                    "team_full_name": team_info.get("full_name", ""),
                    "position": bdl_player.get("position", ""),
                    "baseline_stats": {
                        "pts": season_stats.get("pts"),
                        "reb": season_stats.get("reb"),
                        "ast": season_stats.get("ast"),
                        "stl": season_stats.get("stl"),
                        "blk": season_stats.get("blk"),
                        "fg_pct": season_stats.get("fg_pct"),
                        "fg3_pct": season_stats.get("fg3_pct"),
                        "ft_pct": season_stats.get("ft_pct"),
                        "turnover": season_stats.get("turnover"),
                        "min": season_stats.get("min"),
                        "games_played": season_stats.get("games_played"),
                        "season": 2024
                    },
                    "last_roster_sync": datetime.now(timezone.utc).isoformat()
                }
                
                # Update master hub
                result = await self.master_hub.update_one(
                    {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
                    {"$set": update_data},
                    upsert=False
                )
                
                if result.modified_count > 0:
                    logger.info(f"[ROSTER_SYNC] Updated {player_name}: team={update_data['team']}, pts={season_stats.get('pts')}")
                    return update_data
                else:
                    logger.warning(f"[ROSTER_SYNC] No document modified for {player_name}")
                    return None
                    
        except Exception as e:
            logger.error(f"[ROSTER_SYNC] Error syncing {player_name}: {e}")
            return None
    
    async def sync_active_players(self, player_names: List[str]) -> Dict[str, Any]:
        """
        Sync roster data for a list of active players.
        
        Args:
            player_names: List of player names to sync
            
        Returns:
            Summary of sync operation
        """
        results = {
            "success": 0,
            "failed": 0,
            "total": len(player_names),
            "updated_players": [],
            "failed_players": []
        }
        
        for name in player_names:
            # Rate limit to avoid API throttling
            await asyncio.sleep(0.2)
            
            updated = await self.sync_player_from_bdl(name)
            if updated:
                results["success"] += 1
                results["updated_players"].append({
                    "name": name,
                    "team": updated.get("team"),
                    "pts": updated.get("baseline_stats", {}).get("pts")
                })
            else:
                results["failed"] += 1
                results["failed_players"].append(name)
        
        logger.info(f"[ROSTER_SYNC] Complete: {results['success']}/{results['total']} players updated")
        return results
    
    async def sync_all_from_cached_board(self) -> Dict[str, Any]:
        """
        Sync roster data for all unique players currently in the cached board.
        """
        # Get unique player names from cached board
        player_names = await self.db[COLL("board_cache", "nba")].distinct("player_name")
        
        logger.info(f"[ROSTER_SYNC] Found {len(player_names)} unique players in cached board")
        
        return await self.sync_active_players(player_names)


def get_roster_sync_service(db: AsyncIOMotorDatabase) -> RosterSyncService:
    """Factory function to get RosterSyncService instance."""
    return RosterSyncService(db)
