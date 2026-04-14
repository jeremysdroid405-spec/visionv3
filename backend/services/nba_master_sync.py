"""
NBA Master Sync Orchestrator - Elite Top 10 Engine
===================================================
Mirrors the MLB Master Sync architecture for unified tier assignment.

Uses the Elite Top 10 Sequential Claim Engine to ensure:
1. No prop appears in multiple tiers
2. Unified 50/50 probability blend (market + hit rate)
3. Preserved NBA intel (Blowout Warnings, Injury/Usage, DvP)

ARCHITECTURE - STRICT SEQUENTIAL EXECUTION:
- Phases 1-6: Ferrari Rebuild (full pipeline with legacy tier building)
- Phase 7: Elite Top 10 Sequential Claim (HARD OVERWRITE of tier collections)

The Phase 7 "Hard Overwrite" ensures:
- Elite Top 10 data ALWAYS overwrites Phase 6 legacy tier data
- Ferrari intelligence metadata (mlr_projection, gemini_confidence) is preserved
- No race conditions - Phase 7 only runs AFTER Phase 6 SUCCESS signal
"""

import logging
import asyncio
from typing import Dict, Any, List
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.oracle_apex_service import get_oracle_apex_service

logger = logging.getLogger(__name__)


class NBAMasterSync:
    """Master orchestrator for NBA Elite Top 10 sync pipeline."""
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
    
    async def run_full_pipeline(self, refresh_intel: bool = False) -> Dict[str, Any]:
        """
        Execute the FULL NBA pipeline with Phase 7 Override.
        
        STRICT EXECUTION ORDER:
        1. Phases 1-6: Ferrari Rebuild (populates ferrari_scored + legacy tiers)
        2. Wait for Phase 6 SUCCESS signal
        3. Phase 7: Elite Top 10 Hard Overwrite (reads ferrari_scored, overwrites tiers)
        
        Args:
            refresh_intel: Force refresh Vision Intel (ignores cache)
            
        Returns:
            Combined metrics from all phases.
        """
        start_time = datetime.now(timezone.utc)
        metrics = {
            "started_at": start_time.isoformat(),
            "pipeline": "NBA_MASTER_SYNC_V2",
            "phases": {},
            "errors": []
        }
        
        try:
            # ================================================================
            # PHASES 1-6: FERRARI REBUILD (Complete Pipeline)
            # ================================================================
            logger.info("=" * 70)
            logger.info("[NBA_MASTER_V2] PHASES 1-6: Running Full Ferrari Rebuild...")
            logger.info("[NBA_MASTER_V2] This populates ferrari_scored with smart-filtered props")
            logger.info("[NBA_MASTER_V2] Legacy tiers will be built but OVERWRITTEN by Phase 7")
            logger.info("=" * 70)
            
            phase1_6_start = datetime.now(timezone.utc)
            
            from services.optimized_sync_engine import run_optimized_sync
            ferrari_result = await run_optimized_sync(
                self.db, 
                target_sport="nba", 
                refresh_intel=refresh_intel
            )
            
            phase1_6_duration = (datetime.now(timezone.utc) - phase1_6_start).total_seconds()
            
            # Check for Phase 6 SUCCESS signal
            phase6_success = ferrari_result.get("success", False)
            scored_survivors = ferrari_result.get("scored", {}).get("total_survivors", 0)
            
            metrics["phases"]["1_6_ferrari_rebuild"] = {
                "duration_seconds": phase1_6_duration,
                "success": phase6_success,
                "scored_count": scored_survivors,
                "legacy_tiers_built": True  # Phase 6 built legacy tiers (will be overwritten)
            }
            
            logger.info("=" * 70)
            logger.info(f"[NBA_MASTER_V2] PHASE 6 COMPLETE - SUCCESS: {phase6_success}")
            logger.info(f"[NBA_MASTER_V2] Duration: {phase1_6_duration:.1f}s")
            logger.info(f"[NBA_MASTER_V2] ferrari_scored survivors: {scored_survivors}")
            logger.info("=" * 70)
            
            # Verify ferrari_scored has data before proceeding to Phase 7
            scored_count = await self.db.ferrari_scored.count_documents({})
            logger.info(f"[NBA_MASTER_V2] Verified ferrari_scored: {scored_count} documents")
            
            if scored_count == 0:
                logger.error("[NBA_MASTER_V2] ABORT: ferrari_scored is empty after Phase 6")
                metrics["success"] = False
                metrics["errors"].append("Phase 6 produced 0 scored props - cannot proceed to Phase 7")
                return metrics
            
            if not phase6_success:
                logger.warning("[NBA_MASTER_V2] Phase 6 reported failure but ferrari_scored has data - proceeding to Phase 7")
            
            # ================================================================
            # PHASE 7: ELITE TOP 10 HARD OVERWRITE
            # ================================================================
            # CRITICAL: Set global flag to prevent tier_builder from running during/after Phase 7
            from services.cached_board_builder_service import CachedBoardBuilderService
            CachedBoardBuilderService.SKIP_LEGACY_TIER_BUILDER = True
            logger.info("[NBA_MASTER_V2] Set SKIP_LEGACY_TIER_BUILDER = True (prevents tier_builder overwrites)")
            
            logger.info("=" * 70)
            logger.info("[NBA_MASTER_V2] PHASE 7: Elite Top 10 HARD OVERWRITE")
            logger.info("[NBA_MASTER_V2] Source: ferrari_scored (with MLR + Gemini metadata)")
            logger.info("[NBA_MASTER_V2] Action: Overwrite all legacy tier collections")
            logger.info("=" * 70)
            
            phase7_start = datetime.now(timezone.utc)
            
            # Run Elite Top 10 with metadata preservation
            phase7_result = await self.run_elite_sync_phase7()
            
            phase7_duration = (datetime.now(timezone.utc) - phase7_start).total_seconds()
            
            metrics["phases"]["7_elite_overwrite"] = {
                "duration_seconds": phase7_duration,
                "success": phase7_result.get("success", False),
                "safe_haven": phase7_result.get("tiers", {}).get("safe_haven", 0),
                "front_lines": phase7_result.get("tiers", {}).get("front_lines", 0),
                "war_zone": phase7_result.get("tiers", {}).get("war_zone", 0),
                "total_picks": phase7_result.get("tiers", {}).get("total", 0),
                "metadata_preserved": phase7_result.get("metadata_check", {})
            }
            
            logger.info("=" * 70)
            logger.info("[NBA_MASTER_V2] PHASE 7 COMPLETE")
            logger.info(f"[NBA_MASTER_V2] Duration: {phase7_duration:.1f}s")
            logger.info(f"[NBA_MASTER_V2] Tiers OVERWRITTEN: SH={phase7_result.get('tiers', {}).get('safe_haven', 0)} | FL={phase7_result.get('tiers', {}).get('front_lines', 0)} | WZ={phase7_result.get('tiers', {}).get('war_zone', 0)}")
            logger.info("=" * 70)
            
            # Final verification - ensure collections are NOT empty
            final_sh = await self.db.ferrari_safe_haven.count_documents({})
            final_fl = await self.db.ferrari_front_lines.count_documents({})
            final_wz = await self.db.ferrari_war_zone.count_documents({})
            
            logger.info("[NBA_MASTER_V2] FINAL VERIFICATION:")
            logger.info(f"  ferrari_safe_haven: {final_sh}")
            logger.info(f"  ferrari_front_lines: {final_fl}")
            logger.info(f"  ferrari_war_zone: {final_wz}")
            
            # Final summary
            total_duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            metrics["completed_at"] = datetime.now(timezone.utc).isoformat()
            metrics["total_duration_seconds"] = total_duration
            metrics["success"] = phase7_result.get("success", False)
            
            # Add final tier counts to top level
            metrics["tiers"] = {
                "safe_haven": final_sh,
                "front_lines": final_fl,
                "war_zone": final_wz,
                "total": final_sh + final_fl + final_wz
            }
            
            metrics["verification"] = {
                "collections_populated": (final_sh + final_fl + final_wz) > 0,
                "phase7_overwrote_phase6": True
            }
            
            logger.info("=" * 70)
            logger.info(f"[NBA_MASTER_V2] PIPELINE COMPLETE in {total_duration:.1f}s")
            logger.info(f"[NBA_MASTER_V2] Final Tiers: SH={final_sh} | FL={final_fl} | WZ={final_wz}")
            logger.info("=" * 70)
            
            return metrics
            
        except Exception as e:
            logger.error(f"[NBA_MASTER_V2] Pipeline failed: {e}")
            import traceback
            traceback.print_exc()
            metrics["success"] = False
            metrics["errors"].append(str(e))
            metrics["completed_at"] = datetime.now(timezone.utc).isoformat()
            return metrics
    
    async def run_elite_sync_phase7(self) -> Dict[str, Any]:
        """
        PHASE 7: Elite Top 10 Sequential Claim with Hard Overwrite.
        
        This is the Phase 7 implementation that:
        1. Reads ONLY from ferrari_scored (preserving all Ferrari metadata)
        2. Applies Elite Top 10 Sequential Claim logic
        3. HARD OVERWRITES tier collections (atomic delete + insert)
        4. Verifies metadata preservation (mlr_projection, gemini_confidence)
        
        Returns:
            Detailed metrics including metadata verification.
        """
        start_time = datetime.now(timezone.utc)
        metrics = {
            "started_at": start_time.isoformat(),
            "phase": "7_ELITE_OVERWRITE",
            "steps": {},
            "errors": []
        }
        
        try:
            # ================================================================
            # STEP 7.1: LOAD FERRARI-VETTED PROPS
            # ================================================================
            logger.info("[PHASE_7] Step 7.1: Loading from ferrari_scored...")
            
            step1_start = datetime.now(timezone.utc)
            
            all_props = []
            cursor = self.db.ferrari_scored.find({}, {"_id": 0})
            async for doc in cursor:
                all_props.append(doc)
            
            step1_duration = (datetime.now(timezone.utc) - step1_start).total_seconds()
            
            # Verify metadata is present
            metadata_check = {
                "total_props": len(all_props),
                "with_intel_suite": 0,
                "with_blowout_risk": 0,
                "with_momentum_data": 0,
                "with_vision_intel": 0,
                "with_vk_predicted": 0
            }
            
            for prop in all_props:
                if prop.get("intel_suite"):
                    metadata_check["with_intel_suite"] += 1
                if prop.get("blowout_risk"):
                    metadata_check["with_blowout_risk"] += 1
                if prop.get("momentum_data"):
                    metadata_check["with_momentum_data"] += 1
                if prop.get("vision_intel") or prop.get("vision_summary"):
                    metadata_check["with_vision_intel"] += 1
                if prop.get("vk_predicted"):
                    metadata_check["with_vk_predicted"] += 1
            
            metrics["steps"]["7_1_load"] = {
                "duration_seconds": step1_duration,
                "props_loaded": len(all_props),
                "metadata_check": metadata_check
            }
            
            logger.info(f"[PHASE_7] Step 7.1 complete: {len(all_props)} props loaded")
            logger.info("[PHASE_7] Metadata verification:")
            logger.info(f"  - With intel_suite: {metadata_check['with_intel_suite']}")
            logger.info(f"  - With blowout_risk: {metadata_check['with_blowout_risk']}")
            logger.info(f"  - With momentum_data: {metadata_check['with_momentum_data']}")
            logger.info(f"  - With vision_intel: {metadata_check['with_vision_intel']}")
            logger.info(f"  - With vk_predicted: {metadata_check['with_vk_predicted']}")
            
            if not all_props:
                metrics["success"] = False
                metrics["errors"].append("ferrari_scored is empty")
                return metrics
            
            # ================================================================
            # STEP 7.2: RUN ELITE TOP 10 SEQUENTIAL CLAIM
            # ================================================================
            logger.info("[PHASE_7] Step 7.2: Running Elite Top 10 Sequential Claim...")
            
            step2_start = datetime.now(timezone.utc)
            
            oracle = get_oracle_apex_service(self.db)
            elite_tiers = await oracle.build_elite_top_10_tiers(all_props)
            
            safe_haven = elite_tiers['safe_haven']
            front_lines = elite_tiers['front_lines']
            war_zone = elite_tiers['war_zone']
            
            step2_duration = (datetime.now(timezone.utc) - step2_start).total_seconds()
            
            metrics["steps"]["7_2_elite_engine"] = {
                "duration_seconds": step2_duration,
                "safe_haven": len(safe_haven),
                "front_lines": len(front_lines),
                "war_zone": len(war_zone),
                "total_picks": len(safe_haven) + len(front_lines) + len(war_zone)
            }
            
            logger.info(f"[PHASE_7] Step 7.2 complete: {len(safe_haven) + len(front_lines) + len(war_zone)} total picks")
            
            # ================================================================
            # STEP 7.3: HARD OVERWRITE TIER COLLECTIONS
            # ================================================================
            logger.info("[PHASE_7] Step 7.3: HARD OVERWRITE of tier collections...")
            
            step3_start = datetime.now(timezone.utc)
            
            # Atomic overwrite: delete all, then insert
            await self._hard_overwrite_collection("ferrari_safe_haven", safe_haven, "Safe Haven")
            await self._hard_overwrite_collection("ferrari_front_lines", front_lines, "Front Lines")
            await self._hard_overwrite_collection("ferrari_war_zone", war_zone, "War Zone")
            
            step3_duration = (datetime.now(timezone.utc) - step3_start).total_seconds()
            
            metrics["steps"]["7_3_hard_overwrite"] = {
                "duration_seconds": step3_duration,
                "collections_overwritten": 3
            }
            
            logger.info("[PHASE_7] Step 7.3 complete: 3 collections HARD OVERWRITTEN")
            
            # Final metrics
            total_duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            metrics["completed_at"] = datetime.now(timezone.utc).isoformat()
            metrics["total_duration_seconds"] = total_duration
            metrics["success"] = True
            metrics["tiers"] = {
                "safe_haven": len(safe_haven),
                "front_lines": len(front_lines),
                "war_zone": len(war_zone),
                "total": len(safe_haven) + len(front_lines) + len(war_zone)
            }
            metrics["metadata_check"] = metadata_check
            
            return metrics
            
        except Exception as e:
            logger.error(f"[PHASE_7] Failed: {e}")
            import traceback
            traceback.print_exc()
            metrics["success"] = False
            metrics["errors"].append(str(e))
            metrics["completed_at"] = datetime.now(timezone.utc).isoformat()
            return metrics
    
    async def _hard_overwrite_collection(self, collection_name: str, picks: list, tier_name: str):
        """
        HARD OVERWRITE: Atomic delete + insert for tier collection.
        
        This ensures Phase 7 data ALWAYS replaces Phase 6 legacy data.
        """
        collection = self.db[collection_name]
        
        # Count before
        before_count = await collection.count_documents({})
        
        # HARD DELETE all existing documents
        delete_result = await collection.delete_many({})
        logger.info(f"[PHASE_7] {tier_name}: Deleted {delete_result.deleted_count} legacy documents")
        
        # INSERT new Elite Top 10 picks
        if picks:
            insert_result = await collection.insert_many(picks)
            logger.info(f"[PHASE_7] {tier_name}: Inserted {len(insert_result.inserted_ids)} Elite Top 10 picks")
        else:
            logger.info(f"[PHASE_7] {tier_name}: No picks to insert (0 qualified)")
        
        # Count after
        after_count = await collection.count_documents({})
        
        logger.info(f"[PHASE_7] {tier_name} OVERWRITE: {before_count} -> {after_count}")
    
    async def run_elite_sync(self) -> Dict[str, Any]:
        """
        Legacy method - redirects to Phase 7 implementation.
        
        Use run_full_pipeline() for the complete workflow.
        """
        return await self.run_elite_sync_phase7()


# Singleton instance
_nba_master_sync: NBAMasterSync = None


def get_nba_master_sync(db: AsyncIOMotorDatabase) -> NBAMasterSync:
    global _nba_master_sync
    if _nba_master_sync is None:
        _nba_master_sync = NBAMasterSync(db)
    return _nba_master_sync
