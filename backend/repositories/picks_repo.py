"""
Picks Repository - War Zone, Goblin Vault, Front Lines
=======================================================
Handles all pick-related database operations.
"""
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from .base import BaseRepository
import logging

logger = logging.getLogger(__name__)


class PicksRepository:
    """Repository for managing pick collections"""
    
    def __init__(self, db):
        self.db = db
        self.radar_picks = BaseRepository(db.dg_radar_picks)
        self.goblin_vault = BaseRepository(db.dg_goblin_vault)
        self.front_lines = BaseRepository(db.dg_front_lines)
    
    # ==================== WAR ZONE (DEMONS) ====================
    
    async def get_war_zone_picks(self, limit: int = 10) -> List[Dict]:
        """Get top War Zone picks sorted by EV"""
        return await self.radar_picks.find_many(
            query={},
            sort=[("ev", -1)],
            limit=limit
        )
    
    async def save_war_zone_picks(self, picks: List[Dict]) -> int:
        """Replace all War Zone picks"""
        await self.radar_picks.delete_many()
        if picks:
            return await self.radar_picks.insert_many(picks)
        return 0
    
    async def get_war_zone_count(self) -> int:
        """Count War Zone picks"""
        return await self.radar_picks.count()
    
    # ==================== GOBLIN VAULT (SAFE PICKS) ====================
    
    async def get_goblin_vault_picks(self, limit: int = 10) -> List[Dict]:
        """Get top Goblin Vault picks sorted by EV"""
        return await self.goblin_vault.find_many(
            query={},
            sort=[("ev", -1)],
            limit=limit
        )
    
    async def save_goblin_vault_picks(self, picks: List[Dict]) -> int:
        """Replace all Goblin Vault picks"""
        await self.goblin_vault.delete_many()
        if picks:
            return await self.goblin_vault.insert_many(picks)
        return 0
    
    async def get_goblin_vault_count(self) -> int:
        """Count Goblin Vault picks"""
        return await self.goblin_vault.count()
    
    # ==================== FRONT LINES (MIXED) ====================
    
    async def get_front_lines_picks(self, limit: int = 10) -> List[Dict]:
        """Get Front Lines picks sorted by EV"""
        return await self.front_lines.find_many(
            query={},
            sort=[("ev", -1)],
            limit=limit
        )
    
    async def save_front_lines_picks(self, picks: List[Dict]) -> int:
        """Replace all Front Lines picks"""
        await self.front_lines.delete_many()
        if picks:
            return await self.front_lines.insert_many(picks)
        return 0
    
    async def get_front_lines_count(self) -> int:
        """Count Front Lines picks"""
        return await self.front_lines.count()
    
    # ==================== COMBINED OPERATIONS ====================
    
    async def get_all_picks(self) -> Dict[str, List[Dict]]:
        """Get all picks from all tiers"""
        return {
            "war_zone": await self.get_war_zone_picks(),
            "goblin_vault": await self.get_goblin_vault_picks(),
            "front_lines": await self.get_front_lines_picks()
        }
    
    async def get_pick_counts(self) -> Dict[str, int]:
        """Get counts for all pick tiers"""
        return {
            "war_zone": await self.get_war_zone_count(),
            "goblin_vault": await self.get_goblin_vault_count(),
            "front_lines": await self.get_front_lines_count()
        }
    
    async def clear_all_picks(self) -> Dict[str, int]:
        """Clear all pick collections"""
        return {
            "war_zone": await self.radar_picks.delete_many(),
            "goblin_vault": await self.goblin_vault.delete_many(),
            "front_lines": await self.front_lines.delete_many()
        }
