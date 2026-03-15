"""
Picks Service - Pick Building and Management
=============================================
High-level service for building and managing picks.
Uses repository layer for data access.
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
import logging

from repositories import RepositoryManager
from services.stats_service import (
    calculate_hit_rates, calculate_heat_level, 
    calculate_safety_level, calculate_bullet_level
)
from services.insights_service import (
    calculate_confidence_rating, generate_insight_summary,
    calculate_volatility
)
from services.dvp_service import calculate_dvp_modifier, get_dvp_label

logger = logging.getLogger(__name__)


class PicksService:
    """Service for building and managing picks across all tiers"""
    
    def __init__(self, repo: RepositoryManager):
        self.repo = repo
    
    # ==================== WAR ZONE ====================
    
    async def get_war_zone(self, limit: int = 10) -> Dict[str, Any]:
        """Get War Zone picks with enrichment"""
        picks = await self.repo.picks.get_war_zone_picks(limit)
        
        # Enrich picks with insights
        enriched = await self._enrich_picks(picks, "war_zone")
        
        sync_status = await self.repo.sync.get_sync_status("cached_board")
        
        return {
            "success": True,
            "synced_at": sync_status.get("synced_at") if sync_status else None,
            "picks_count": len(enriched),
            "picks": enriched,
            "tier": "war_zone",
            "description": "High-risk, high-reward demon plays"
        }
    
    async def save_war_zone(self, picks: List[Dict]) -> int:
        """Save War Zone picks"""
        return await self.repo.picks.save_war_zone_picks(picks)
    
    # ==================== GOBLIN VAULT ====================
    
    async def get_goblin_vault(self, limit: int = 10) -> Dict[str, Any]:
        """Get Goblin Vault picks with enrichment"""
        picks = await self.repo.picks.get_goblin_vault_picks(limit)
        
        # Enrich picks with insights
        enriched = await self._enrich_picks(picks, "goblin_vault")
        
        sync_status = await self.repo.sync.get_sync_status("cached_board")
        
        return {
            "success": True,
            "synced_at": sync_status.get("synced_at") if sync_status else None,
            "picks_count": len(enriched),
            "picks": enriched,
            "tier": "goblin_vault",
            "description": "High-probability safe plays"
        }
    
    async def save_goblin_vault(self, picks: List[Dict]) -> int:
        """Save Goblin Vault picks"""
        return await self.repo.picks.save_goblin_vault_picks(picks)
    
    # ==================== FRONT LINES ====================
    
    async def get_front_lines(self, limit: int = 10) -> Dict[str, Any]:
        """Get Front Lines picks with enrichment"""
        picks = await self.repo.picks.get_front_lines_picks(limit)
        
        # Enrich picks with insights
        enriched = await self._enrich_picks(picks, "front_lines")
        
        sync_status = await self.repo.sync.get_sync_status("cached_board")
        
        return {
            "success": True,
            "synced_at": sync_status.get("synced_at") if sync_status else None,
            "picks_count": len(enriched),
            "picks": enriched,
            "tier": "front_lines",
            "description": "Balanced demon/goblin mix"
        }
    
    async def save_front_lines(self, picks: List[Dict]) -> int:
        """Save Front Lines picks"""
        return await self.repo.picks.save_front_lines_picks(picks)
    
    # ==================== ENRICHMENT ====================
    
    async def _enrich_picks(self, picks: List[Dict], tier: str) -> List[Dict]:
        """Enrich picks with insights and additional data"""
        enriched = []
        
        for pick in picks:
            player_name = pick.get("player_name", "")
            
            # Get AI insights if available
            insight = await self.repo.players.get_player_insights(player_name)
            if insight:
                pick["insight_summary"] = insight.get("insight_summary", "")
                pick["ai_confidence_rating"] = insight.get("ai_confidence_rating", 50)
            else:
                # Calculate fallback confidence from pillar_4_context
                pillar_4 = pick.get("pillar_4_context", 0.5)
                pick["ai_confidence_rating"] = int(pillar_4 * 100)
            
            # Get intel_briefing from cached_board
            board_entry = await self.repo.board.get_player_from_board(player_name)
            if board_entry and board_entry.get("intel_briefing"):
                pick["intel_briefing"] = board_entry.get("intel_briefing")
            
            enriched.append(pick)
        
        return enriched
    
    # ==================== STATISTICS ====================
    
    async def get_pick_stats(self) -> Dict[str, Any]:
        """Get statistics for all pick tiers"""
        counts = await self.repo.picks.get_pick_counts()
        
        return {
            "total_picks": sum(counts.values()),
            "by_tier": counts,
            "last_updated": datetime.now(timezone.utc).isoformat()
        }
    
    async def clear_all_picks(self) -> Dict[str, int]:
        """Clear all pick collections"""
        return await self.repo.picks.clear_all_picks()
