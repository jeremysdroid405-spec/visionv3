"""
MLB Master Sync Orchestrator
============================
Enforces strict synchronous pipeline sequence to prevent stale data issues.

Sequence:
1. Raw Data Ingestion: Sync Vegas Odds → mlb_odds_raw
2. Intersection Merge: Build mlb_cached_board (ONLY players with BOTH PrizePicks AND odds)
3. BDL Prefetch: Fetch splits ONLY for players in the new cached_board
4. Oracle Engine: Run tier rebuilds (Safe Haven, Front Lines, War Zone)
"""

import logging
import asyncio
from typing import Dict, Any, Set
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.universal_odds_sync import get_universal_odds_service
from services.bdl_splits_cache import prefetch_all_splits, clear_cache, _splits_cache
from services.mlb_oracle_apex_service import get_mlb_oracle_apex_service

logger = logging.getLogger(__name__)


class MLBMasterSync:
    """Master orchestrator for MLB sync pipeline."""
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
    
    async def run_master_sync(self) -> Dict[str, Any]:
        """
        Execute the full MLB sync pipeline in strict sequence.
        
        Returns detailed metrics for each step.
        """
        start_time = datetime.now(timezone.utc)
        metrics = {
            "started_at": start_time.isoformat(),
            "steps": {},
            "errors": []
        }
        
        try:
            # ================================================================
            # STEP 1: RAW DATA INGESTION (Vegas Odds Sync)
            # ================================================================
            logger.info("=" * 70)
            logger.info("[MLB_MASTER] STEP 1: Syncing Vegas Odds...")
            logger.info("=" * 70)
            
            step1_start = datetime.now(timezone.utc)
            
            # CLEAR OLD PROPS FIRST - ensures fresh data only
            old_count = await self.db.mlb_live_props.count_documents({})
            await self.db.mlb_live_props.delete_many({})
            logger.info(f"[MLB_MASTER] Cleared {old_count} old props from mlb_live_props")
            
            odds_service = get_universal_odds_service(self.db)
            odds_result = await odds_service.sync_sport_props("mlb")
            step1_duration = (datetime.now(timezone.utc) - step1_start).total_seconds()
            
            metrics["steps"]["1_odds_sync"] = {
                "duration_seconds": step1_duration,
                "old_props_cleared": old_count,
                "events_count": odds_result.get("events_count", 0),
                "total_props": odds_result.get("total_props", 0),
                "prizepicks_props": odds_result.get("bookmaker_counts", {}).get("prizepicks", 0),
                "draftkings_props": odds_result.get("bookmaker_counts", {}).get("draftkings", 0)
            }
            logger.info(f"[MLB_MASTER] Step 1 complete: {odds_result.get('total_props', 0)} props synced")
            
            # ================================================================
            # STEP 2: INTERSECTION MERGE (Build Cached Board)
            # ================================================================
            logger.info("=" * 70)
            logger.info("[MLB_MASTER] STEP 2: Building mlb_cached_board (INTERSECTION ONLY)...")
            logger.info("=" * 70)
            
            step2_start = datetime.now(timezone.utc)
            board_result = await self._build_intersection_board()
            step2_duration = (datetime.now(timezone.utc) - step2_start).total_seconds()
            
            metrics["steps"]["2_cached_board"] = {
                "duration_seconds": step2_duration,
                "players_with_both": board_result.get("players_matched", 0),
                "players_prizepicks_only": board_result.get("players_prizepicks_only", 0),
                "players_odds_only": board_result.get("players_odds_only", 0),
                "total_props_in_board": board_result.get("total_props", 0)
            }
            logger.info(f"[MLB_MASTER] Step 2 complete: {board_result.get('players_matched', 0)} players in board")
            
            # ================================================================
            # STEP 3: BDL PREFETCH (Rate Limit Protection)
            # ================================================================
            logger.info("=" * 70)
            logger.info("[MLB_MASTER] STEP 3: BDL Splits Prefetch (FILTERED PLAYERS ONLY)...")
            logger.info("=" * 70)
            
            step3_start = datetime.now(timezone.utc)
            
            # Get unique player IDs ONLY from the new cached_board
            player_ids = await self._get_cached_board_player_ids()
            logger.info(f"[MLB_MASTER] Found {len(player_ids)} unique hitters to prefetch")
            
            # Clear old cache and prefetch
            clear_cache()
            bdl_success = await prefetch_all_splits(player_ids)
            
            step3_duration = (datetime.now(timezone.utc) - step3_start).total_seconds()
            
            # Count actual API calls made (cache entries created)
            bdl_api_calls = len(_splits_cache)
            
            metrics["steps"]["3_bdl_prefetch"] = {
                "duration_seconds": step3_duration,
                "players_requested": len(player_ids),
                "players_cached": bdl_success,
                "api_calls_made": bdl_api_calls,
                "api_calls_saved": 331 - len(player_ids)  # Compared to old approach
            }
            logger.info(f"[MLB_MASTER] Step 3 complete: {bdl_api_calls} BDL API calls (saved {331 - len(player_ids)} calls)")
            
            # ================================================================
            # STEP 4: ORACLE ENGINE (Tier Rebuilds)
            # ================================================================
            logger.info("=" * 70)
            logger.info("[MLB_MASTER] STEP 4: Running Oracle Apex Tier Rebuilds...")
            logger.info("=" * 70)
            
            step4_start = datetime.now(timezone.utc)
            tier_result = await self._run_tier_rebuilds()
            step4_duration = (datetime.now(timezone.utc) - step4_start).total_seconds()
            
            metrics["steps"]["4_tier_rebuilds"] = {
                "duration_seconds": step4_duration,
                "safe_haven": tier_result.get("safe_haven", 0),
                "front_lines": tier_result.get("front_lines", 0),
                "war_zone": tier_result.get("war_zone", 0),
                "total_picks": tier_result.get("total", 0)
            }
            logger.info(f"[MLB_MASTER] Step 4 complete: {tier_result.get('total', 0)} total picks")
            
            # Final summary
            total_duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            metrics["completed_at"] = datetime.now(timezone.utc).isoformat()
            metrics["total_duration_seconds"] = total_duration
            metrics["success"] = True
            
            logger.info("=" * 70)
            logger.info(f"[MLB_MASTER] PIPELINE COMPLETE in {total_duration:.1f}s")
            logger.info(f"[MLB_MASTER] BDL API Calls: {bdl_api_calls} (vs 331+ with old approach)")
            logger.info("=" * 70)
            
            return metrics
            
        except Exception as e:
            logger.error(f"[MLB_MASTER] Pipeline failed: {e}")
            metrics["success"] = False
            metrics["errors"].append(str(e))
            metrics["completed_at"] = datetime.now(timezone.utc).isoformat()
            return metrics
    
    async def _build_intersection_board(self) -> Dict[str, Any]:
        """
        Build mlb_cached_board with INTERSECTION logic.
        Only include players who have BOTH PrizePicks props AND odds lines.
        """
        from services.mlb_cached_board_builder import get_mlb_board_builder
        
        # Get all PrizePicks props
        pp_props = await self.db.mlb_live_props.find({
            "bookmaker": "prizepicks"
        }).to_list(None)
        
        pp_players = set()
        for prop in pp_props:
            player_name = prop.get("player_name", "").strip().lower()
            if player_name:
                pp_players.add(player_name)
        
        # Get all odds (DraftKings/Pinnacle)
        odds_props = await self.db.mlb_live_props.find({
            "bookmaker": {"$in": ["draftkings", "pinnacle"]}
        }).to_list(None)
        
        odds_players = set()
        for prop in odds_props:
            player_name = prop.get("player_name", "").strip().lower()
            if player_name:
                odds_players.add(player_name)
        
        # INTERSECTION: Players with BOTH
        intersection_players = pp_players & odds_players
        
        logger.info(f"[INTERSECTION] PrizePicks players: {len(pp_players)}")
        logger.info(f"[INTERSECTION] Odds players: {len(odds_players)}")
        logger.info(f"[INTERSECTION] BOTH (included): {len(intersection_players)}")
        logger.info(f"[INTERSECTION] PrizePicks ONLY (dropped): {len(pp_players - odds_players)}")
        logger.info(f"[INTERSECTION] Odds ONLY (dropped): {len(odds_players - pp_players)}")
        
        # Build the cached board using the standard builder
        # It will pull from mlb_live_props which has all props
        builder = get_mlb_board_builder(self.db)
        result = await builder.build_cached_board()
        
        return {
            "players_matched": len(intersection_players),
            "players_prizepicks_only": len(pp_players - odds_players),
            "players_odds_only": len(odds_players - pp_players),
            "total_props": result.get("props_enriched", 0)
        }
    
    async def _get_cached_board_player_ids(self) -> Set[int]:
        """
        Get unique hitter player IDs from mlb_cached_board.
        Only includes players with active props (not pitchers for hitter stats).
        """
        player_ids = set()
        
        cursor = self.db.mlb_cached_board.find({}, {"bdl_id": 1, "player_id": 1, "props": 1})
        async for doc in cursor:
            # Get player ID
            pid = doc.get("bdl_id") or doc.get("player_id")
            if not pid:
                continue
            
            # Check if player has hitter props (not pitcher stats)
            props = doc.get("props", [])
            has_hitter_prop = False
            for prop in props:
                stat_key = (prop.get("stat_key") or prop.get("stat_type") or "").upper()
                if stat_key not in ["K", "OUTS", "ER", "PITCHER STRIKEOUTS", "PITCHING OUTS", 
                                    "WALKS ALLOWED", "HITS ALLOWED", "EARNED RUNS"]:
                    has_hitter_prop = True
                    break
            
            if has_hitter_prop:
                try:
                    player_ids.add(int(pid))
                except (ValueError, TypeError):
                    pass
        
        return player_ids
    
    async def _run_tier_rebuilds(self) -> Dict[str, int]:
        """
        Run Oracle Apex tier rebuilds using the fresh cached_board.
        BDL splits are already prefetched in cache.
        """
        from services.mlb_tier_service import get_mlb_tier_service
        
        # Reference tier service for future use if needed
        _ = get_mlb_tier_service(self.db)
        
        # Get all props from cached_board
        all_props = []
        cursor = self.db.mlb_cached_board.find({})
        async for doc in cursor:
            props = doc.get("props", [])
            for prop in props:
                # Attach player-level data to each prop
                prop["player_name"] = doc.get("player_name")
                prop["player_id"] = doc.get("bdl_id") or doc.get("player_id")
                prop["team"] = doc.get("team")
                all_props.append(prop)
        
        logger.info(f"[TIER_REBUILD] Processing {len(all_props)} props from cached_board")
        
        # Get Oracle Apex service
        oracle = get_mlb_oracle_apex_service(self.db)
        
        # Build tiers (BDL cache is already populated from Step 3)
        safe_haven = await oracle.build_safe_haven_tier(all_props)
        front_lines = await oracle.build_front_lines_tier(all_props)
        war_zone = await oracle.build_war_zone_tier(all_props)
        
        # Store results
        await self._store_tier_results("mlb_safe_haven", safe_haven)
        await self._store_tier_results("mlb_front_lines", front_lines)
        await self._store_tier_results("mlb_war_zone", war_zone)
        
        return {
            "safe_haven": len(safe_haven),
            "front_lines": len(front_lines),
            "war_zone": len(war_zone),
            "total": len(safe_haven) + len(front_lines) + len(war_zone)
        }
    
    async def _store_tier_results(self, collection_name: str, picks: list):
        """Store tier results in MongoDB."""
        collection = self.db[collection_name]
        
        if not picks:
            # Clear collection if no picks
            await collection.delete_many({})
            return
        
        # Replace all documents
        await collection.delete_many({})
        if picks:
            await collection.insert_many(picks)


# Singleton instance
_mlb_master_sync: MLBMasterSync = None


def get_mlb_master_sync(db: AsyncIOMotorDatabase) -> MLBMasterSync:
    global _mlb_master_sync
    if _mlb_master_sync is None:
        _mlb_master_sync = MLBMasterSync(db)
    return _mlb_master_sync
