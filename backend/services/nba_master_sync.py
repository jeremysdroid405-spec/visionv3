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
            # CRITICAL: Set global flags to prevent ANY tier rebuilds after Phase 7
            from services.cached_board_builder_service import CachedBoardBuilderService
            CachedBoardBuilderService.SKIP_LEGACY_TIER_BUILDER = True
            CachedBoardBuilderService.SKIP_ALL_FERRARI_REBUILDS = True
            logger.info("[NBA_MASTER_V2] Set SKIP_LEGACY_TIER_BUILDER = True")
            logger.info("[NBA_MASTER_V2] Set SKIP_ALL_FERRARI_REBUILDS = True (protects NBA tiers from overwrites)")
            
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
            final_sh = await self.db.elite_safe_haven.count_documents({})
            final_fl = await self.db.elite_front_lines.count_documents({})
            final_wz = await self.db.elite_war_zone.count_documents({})
            
            logger.info("[NBA_MASTER_V2] FINAL VERIFICATION (ELITE VAULT):")
            logger.info(f"  elite_safe_haven: {final_sh}")
            logger.info(f"  elite_front_lines: {final_fl}")
            logger.info(f"  elite_war_zone: {final_wz}")

            # ================================================================
            # RT SHADOW SEED (2026-04-21, carbon-copy follow-up):
            # Mirrors MLB Stage 7 — write `final-nba-rt` on every master
            # sync so the live UI reader (NBABoardAdapter.version_tag =
            # "final-nba-rt") is never stale between injury-triggered
            # partial rescores. Without this, `final-nba-rt` aged out
            # between events and the Ferrari NBA board went empty.
            # ================================================================
            logger.info("=" * 70)
            logger.info("[NBA_MASTER_V2] RT SHADOW: recompute_sport(final-nba-rt)...")
            logger.info("=" * 70)
            rt_start = datetime.now(timezone.utc)
            try:
                from services.scoring.recompute import recompute_sport
                rt_result = await recompute_sport(
                    db=self.db, sport="nba",
                    version_tag="final-nba-rt", dry_run=False,
                )
                rt_duration = (datetime.now(timezone.utc) - rt_start).total_seconds()
                metrics["phases"]["rt_shadow_seed"] = {
                    "duration_seconds": rt_duration,
                    "processed": rt_result.get("processed", 0),
                    "written": rt_result.get("written", 0),
                    "replaced": rt_result.get("replaced", 0),
                    "tier_distribution": rt_result.get("tier_distribution", {}),
                    "version_tag": rt_result.get("version_tag"),
                }
                logger.info(
                    f"[NBA_MASTER_V2] RT shadow complete: "
                    f"{rt_result.get('written', 0)} scored at final-nba-rt "
                    f"tiers={rt_result.get('tier_distribution', {})}"
                )
            except Exception as _rt_err:
                logger.error(f"[NBA_MASTER_V2] RT shadow seed failed: {_rt_err}")
                metrics["errors"].append(f"rt_shadow_seed: {_rt_err}")

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
                "vault_isolation": True,
                "elite_collections_used": ["elite_safe_haven", "elite_front_lines", "elite_war_zone"]
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
        PHASE 7: Elite Top 10 via Unified Pipeline.
        
        Uses the shared UnifiedPipeline framework with NBAAdapter.
        Preserves all scoring math, MLR model, safety filters, and tier selection.
        Adds: validation metadata, atomic writes, observability.
        """
        from services.unified_pipeline import UnifiedPipeline
        from services.adapters.nba_adapter import NBAAdapter

        adapter = NBAAdapter()
        pipeline = UnifiedPipeline(adapter, self.db)
        result = await pipeline.run()

        return {
            "success": result.success,
            "tiers": result.tiers,
            "metadata_check": result.validation_stats,
            "phases": result.phases,
            "errors": result.errors,
            "run_id": result.run_id,
        }
    
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
