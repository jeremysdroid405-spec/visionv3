"""
Sync Service - Data Sync Coordination
======================================
Extracted from services.engines.demon_goblin_engine.py for modularity.

Handles:
- Full sync orchestration
- Tier building (War Zone, Goblin Vault, Front Lines)
- Parlay builder coordination
- Sync logging and status
"""
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import asyncio
import logging

from repositories import RepositoryManager
from services.props_service import PropsService
from services.parlay_service import build_parlay_tickets, interleave_pick_arrays

logger = logging.getLogger(__name__)


class SyncService:
    """Service for coordinating data sync operations"""
    
    def __init__(self, repo: RepositoryManager, db):
        self.repo = repo
        self.db = db
        self.props_service = PropsService(db)
        
        # Direct collection access (legacy)
        self.cached_board = db.dg_cached_board
        self.radar_picks = db.dg_radar_picks
        self.goblin_vault = db.dg_goblin_vault
        self.front_lines = db.dg_front_lines
        self.parlay_builder = db.dg_parlay_builder
        self.goblin_recon = db.dg_goblin_recon
        self.sync_log = db.dg_sync_log
        self.daily_insights = db.dg_daily_insights
    
    # ==================== WAR ZONE BUILDER ====================
    
    async def build_war_zone(
        self, 
        players_dict: Dict[str, Dict], 
        sync_time: datetime,
        limit: int = 10
    ) -> Dict[str, Any]:
        """
        Build War Zone - Top Demon picks sorted by EV.
        
        War Zone shows the highest Expected Value demon plays.
        """
        logger.info("[WAR ZONE] Building top demon picks...")
        
        all_demons = []
        
        for player_name, player_data in players_dict.items():
            if player_data is None or not isinstance(player_data, dict):
                continue
            
            demons = player_data.get("demons", [])
            
            for demon in demons:
                if demon is None:
                    continue
                
                # Score the demon pick
                pick = await self.props_service.build_scored_pick(
                    prop=demon,
                    player_data=player_data,
                    tier="war_zone",
                    sync_time=sync_time
                )
                
                # Skip picks without real data
                if not pick.get("has_real_data", False):
                    continue
                
                all_demons.append(pick)
        
        # Sort by EV score (highest first)
        all_demons.sort(key=lambda x: x.get("ev_score", 0), reverse=True)
        
        # Take top picks
        top_demons = all_demons[:limit]
        
        # Add rank
        for idx, pick in enumerate(top_demons):
            pick["rank"] = idx + 1
            pick["is_war_zone_pick"] = True
        
        # Save to database
        await self.radar_picks.delete_many({})
        if top_demons:
            await self.radar_picks.insert_many(top_demons)
        
        # Update sync log
        await self.sync_log.update_one(
            {"type": "war_zone"},
            {"$set": {
                "type": "war_zone",
                "synced_at": sync_time.isoformat(),
                "picks_count": len(top_demons),
                "total_analyzed": len(all_demons)
            }},
            upsert=True
        )
        
        logger.info(f"[WAR ZONE] Built {len(top_demons)} top demons from {len(all_demons)} total")
        
        return {
            "success": True,
            "picks_count": len(top_demons),
            "total_analyzed": len(all_demons),
            "top_pick": top_demons[0] if top_demons else None
        }
    
    # ==================== GOBLIN VAULT BUILDER ====================
    
    async def build_goblin_vault(
        self, 
        players_dict: Dict[str, Dict], 
        sync_time: datetime,
        limit: int = 10
    ) -> Dict[str, Any]:
        """
        Build Goblin Vault - Top safe picks sorted by EV.
        
        Goblin Vault shows the highest Expected Value safe plays.
        """
        logger.info("[GOBLIN VAULT] Building top safe picks...")
        
        all_goblins = []
        
        for player_name, player_data in players_dict.items():
            if player_data is None or not isinstance(player_data, dict):
                continue
            
            goblins = player_data.get("goblins", [])
            
            for goblin in goblins:
                if goblin is None:
                    continue
                
                # Score the goblin pick
                pick = await self.props_service.build_scored_pick(
                    prop=goblin,
                    player_data=player_data,
                    tier="goblin_vault",
                    sync_time=sync_time
                )
                
                if not pick.get("has_real_data", False):
                    continue
                
                all_goblins.append(pick)
        
        # Sort by EV score
        all_goblins.sort(key=lambda x: x.get("ev_score", 0), reverse=True)
        
        top_goblins = all_goblins[:limit]
        
        for idx, pick in enumerate(top_goblins):
            pick["rank"] = idx + 1
            pick["is_goblin_vault_pick"] = True
        
        await self.goblin_vault.delete_many({})
        if top_goblins:
            await self.goblin_vault.insert_many(top_goblins)
        
        await self.sync_log.update_one(
            {"type": "goblin_vault"},
            {"$set": {
                "type": "goblin_vault",
                "synced_at": sync_time.isoformat(),
                "picks_count": len(top_goblins),
                "total_analyzed": len(all_goblins)
            }},
            upsert=True
        )
        
        logger.info(f"[GOBLIN VAULT] Built {len(top_goblins)} top goblins from {len(all_goblins)} total")
        
        return {
            "success": True,
            "picks_count": len(top_goblins),
            "total_analyzed": len(all_goblins),
            "top_pick": top_goblins[0] if top_goblins else None
        }
    
    # ==================== FRONT LINES BUILDER ====================
    
    async def build_front_lines(
        self, 
        players_dict: Dict[str, Dict], 
        sync_time: datetime,
        limit: int = 10
    ) -> Dict[str, Any]:
        """
        Build Front Lines - Mixed tier with demons and goblins.
        
        Front Lines shows a balanced mix of mild demons and goblins.
        """
        logger.info("[FRONT LINES] Building mixed tier picks...")
        
        mild_demons = []
        quality_goblins = []
        
        for player_name, player_data in players_dict.items():
            if player_data is None or not isinstance(player_data, dict):
                continue
            
            # Process demons
            for demon in player_data.get("demons", []):
                if demon is None:
                    continue
                
                pick = await self.props_service.build_scored_pick(
                    prop=demon,
                    player_data=player_data,
                    tier="front_lines",
                    sync_time=sync_time
                )
                
                if not pick.get("has_real_data", False):
                    continue
                
                # "Mild" demons have 40-60% hit rate
                h10 = pick.get("h10_rate", 0)
                if 40 <= h10 <= 70:
                    mild_demons.append(pick)
            
            # Process goblins
            for goblin in player_data.get("goblins", []):
                if goblin is None:
                    continue
                
                pick = await self.props_service.build_scored_pick(
                    prop=goblin,
                    player_data=player_data,
                    tier="front_lines",
                    sync_time=sync_time
                )
                
                if not pick.get("has_real_data", False):
                    continue
                
                # Quality goblins have 60-85% hit rate
                h10 = pick.get("h10_rate", 0)
                if 60 <= h10 <= 85:
                    quality_goblins.append(pick)
        
        # Sort each by score
        mild_demons.sort(key=lambda x: x.get("final_ev_score", 0), reverse=True)
        quality_goblins.sort(key=lambda x: x.get("final_ev_score", 0), reverse=True)
        
        # Interleave for variety
        interleaved = interleave_pick_arrays(
            quality_goblins[:limit//2 + 1],
            mild_demons[:limit//2 + 1]
        )
        
        front_lines_picks = interleaved[:limit]
        
        for idx, pick in enumerate(front_lines_picks):
            pick["rank"] = idx + 1
            pick["is_front_lines_pick"] = True
        
        await self.front_lines.delete_many({})
        if front_lines_picks:
            await self.front_lines.insert_many(front_lines_picks)
        
        await self.sync_log.update_one(
            {"type": "front_lines"},
            {"$set": {
                "type": "front_lines",
                "synced_at": sync_time.isoformat(),
                "picks_count": len(front_lines_picks),
                "demons_count": sum(1 for p in front_lines_picks if p.get("is_demon")),
                "goblins_count": sum(1 for p in front_lines_picks if p.get("is_goblin"))
            }},
            upsert=True
        )
        
        logger.info(f"[FRONT LINES] Built {len(front_lines_picks)} mixed picks")
        
        return {
            "success": True,
            "picks_count": len(front_lines_picks),
            "demons_count": len(mild_demons),
            "goblins_count": len(quality_goblins)
        }
    
    # ==================== PARLAY BUILDER ====================
    
    async def build_parlays(
        self, 
        players_dict: Dict[str, Dict], 
        sync_time: datetime
    ) -> Dict[str, Any]:
        """
        Build parlay tickets for all tiers.
        """
        logger.info("[PARLAY BUILDER] Generating parlays...")
        
        # Collect all scoreable picks
        war_zone_picks = []
        goblin_picks = []
        
        for player_name, player_data in players_dict.items():
            if player_data is None or not isinstance(player_data, dict):
                continue
            
            team = player_data.get("team", "")
            
            for demon in player_data.get("demons", []):
                if demon is None:
                    continue
                
                pick = await self.props_service.build_scored_pick(
                    prop=demon,
                    player_data=player_data,
                    tier="war_zone",
                    sync_time=sync_time
                )
                
                if pick.get("has_real_data") and pick.get("h10_rate", 0) >= 50:
                    pick["team"] = team
                    war_zone_picks.append(pick)
            
            for goblin in player_data.get("goblins", []):
                if goblin is None:
                    continue
                
                pick = await self.props_service.build_scored_pick(
                    prop=goblin,
                    player_data=player_data,
                    tier="goblin_vault",
                    sync_time=sync_time
                )
                
                if pick.get("has_real_data") and pick.get("h10_rate", 0) >= 70:
                    pick["team"] = team
                    goblin_picks.append(pick)
        
        # Sort by EV
        war_zone_picks.sort(key=lambda x: x.get("ev_score", 0), reverse=True)
        goblin_picks.sort(key=lambda x: x.get("ev_score", 0), reverse=True)
        
        # Build parlay tickets for each tier
        war_zone_parlays = build_parlay_tickets(war_zone_picks, "war_zone")
        safe_haven_parlays = build_parlay_tickets(goblin_picks, "safe_haven")
        
        # Combined for front lines
        combined_picks = interleave_pick_arrays(goblin_picks[:15], war_zone_picks[:15])
        front_lines_parlays = build_parlay_tickets(combined_picks, "front_lines")
        
        # Save to database
        await self.parlay_builder.delete_many({})
        
        parlay_doc = {
            "war_zone": war_zone_parlays,
            "safe_haven": safe_haven_parlays,
            "front_lines": front_lines_parlays,
            "total_war_zone_picks": len(war_zone_picks),
            "total_goblin_picks": len(goblin_picks),
            "synced_at": sync_time.isoformat()
        }
        
        await self.parlay_builder.insert_one(parlay_doc)
        
        logger.info(f"[PARLAY BUILDER] Generated parlays for all tiers")
        
        return {
            "success": True,
            "war_zone_tickets": len(war_zone_parlays),
            "safe_haven_tickets": len(safe_haven_parlays),
            "front_lines_tickets": len(front_lines_parlays)
        }
    
    # ==================== GOBLIN RECON BUILDER ====================
    
    async def build_goblin_recon(
        self, 
        players_dict: Dict[str, Dict], 
        sync_time: datetime
    ) -> Dict[str, Any]:
        """
        Build Goblin Recon - High-consistency parlay tickets.
        """
        logger.info("[GOBLIN RECON] Building high-consistency parlays...")
        
        high_consistency_picks = []
        
        for player_name, player_data in players_dict.items():
            if player_data is None or not isinstance(player_data, dict):
                continue
            
            team = player_data.get("team", "")
            
            for goblin in player_data.get("goblins", []):
                if goblin is None:
                    continue
                
                pick = await self.props_service.build_scored_pick(
                    prop=goblin,
                    player_data=player_data,
                    tier="goblin_vault",
                    sync_time=sync_time
                )
                
                # High consistency = 80%+ hit rate
                if pick.get("has_real_data") and pick.get("h10_rate", 0) >= 80:
                    pick["team"] = team
                    high_consistency_picks.append(pick)
        
        high_consistency_picks.sort(key=lambda x: x.get("h10_rate", 0), reverse=True)
        
        recon_parlays = build_parlay_tickets(high_consistency_picks, "safe_haven")
        
        await self.goblin_recon.delete_many({})
        
        recon_doc = {
            "parlays": recon_parlays,
            "total_picks_analyzed": len(high_consistency_picks),
            "min_hit_rate": "80%",
            "synced_at": sync_time.isoformat()
        }
        
        await self.goblin_recon.insert_one(recon_doc)
        
        logger.info(f"[GOBLIN RECON] Built {len(recon_parlays)} high-consistency parlays")
        
        return {
            "success": True,
            "tickets_count": len(recon_parlays),
            "picks_analyzed": len(high_consistency_picks)
        }
    
    # ==================== SYNC STATUS ====================
    
    async def get_sync_status(self) -> Dict[str, Any]:
        """Get current sync status for all tiers"""
        statuses = await self.sync_log.find({}).to_list(None)
        
        status_map = {}
        for s in statuses:
            sync_type = s.get("type", "")
            status_map[sync_type] = {
                "synced_at": s.get("synced_at"),
                "picks_count": s.get("picks_count", 0),
                "status": "active"
            }
        
        return {
            "success": True,
            "tiers": status_map,
            "last_sync": max(
                [s.get("synced_at") for s in statuses if s.get("synced_at")],
                default=None
            )
        }
    
    async def update_sync_log(
        self, 
        sync_type: str, 
        data: Dict[str, Any]
    ):
        """Update sync log for a specific type"""
        await self.sync_log.update_one(
            {"type": sync_type},
            {"$set": {**data, "type": sync_type}},
            upsert=True
        )
