"""
Cached Board Builder Service
============================
Extracted from services.engines.demon_goblin_engine.py for modularity.

Handles building the centralized cached board from props data.
All tier sections (War Zone, Goblin Vault, Front Lines) read from here.

ANCHOR-BASED CLASSIFICATION:
Uses PrizePicks "Standard Line" as the anchor.
- Alternate ABOVE standard = DEMON (Red)
- Alternate BELOW standard = GOBLIN (Green)
- Equal to standard = STANDARD (Gray)
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
import logging

from motor.motor_asyncio import AsyncIOMotorDatabase
from services.utils_service import sanitize_player_name
from services.anchor_classification_service import classify_props_by_anchor, _normalize_name as anchor_normalize_name

logger = logging.getLogger(__name__)


class CachedBoardBuilderService:
    """
    Service for building the centralized cached board.
    
    Architecture v4.0: Odds API Mapper Integration + Shadow Table Strategy
    - Props come from Odds API with player names in 'description' field
    - Uses Odds API Mapper to get player_id directly
    - Mapper returns full player data from nba_master_hub_2026
    - ZERO-DOWNTIME: Writes to dg_cached_board_temp, then atomic rename
    """
    
    # Global flag to skip legacy tier builder (set when using Elite Top 10)
    SKIP_LEGACY_TIER_BUILDER = False
    
    def __init__(self, db: AsyncIOMotorDatabase, tier_builder_service, parlay_builder_service):
        self.db = db
        self.tier_builder_service = tier_builder_service
        self.parlay_builder_service = parlay_builder_service
        
        # Collection references
        self.cached_board = db.dg_cached_board
        self.cached_board_temp = db.dg_cached_board_temp  # Shadow table
        self.sync_log = db.dg_sync_log
        self.master_roster = db.dg_master_roster
        self.flagged_players = db.dg_flagged_players
        
        # Mapper reference - must be set externally
        self._odds_mapper = None
    
    def set_odds_mapper(self, mapper):
        """Set the Odds API Mapper instance"""
        self._odds_mapper = mapper
    
    async def build_cached_board(
        self,
        props: List[Dict],
        sync_time: datetime,
        ensure_mapper_loaded_callback=None
    ) -> Dict[str, Any]:
        """
        Build the centralized cached board from props.
        
        This is THE ONLY place where player data enrichment happens.
        All sections (War Zone, Goblin Recon, Gauntlet, Safe Haven) read from here.
        
        Data Flow (v4.0 - Mapper-based):
        1. Props come from Odds API with player names in 'description' field
        2. For each prop, use Odds API Mapper to get player_id directly
        3. Mapper returns full player data from nba_master_hub_2026
        4. Store everything in dg_cached_board
        5. No more fuzzy name matching - exact lookup via permanent mapping
        """
        if not props:
            return {"success": True, "message": "No props to build", "players_count": 0}
        
        logger.info(f"[CACHED_BOARD] Building centralized board from {len(props)} props...")
        
        # STEP 0: LOAD STATS FROM MASTER HUB (needed for L5 fallback classification)
        stats_map = await self._load_stats_map()
        
        # Build player_stats dict for anchor classification (L5 fallback)
        # Format: "player_name|STAT" -> {"l5_avg": X, "season_avg": Y}
        # CRITICAL: Calculate L5 from bdl_game_logs, NOT from stale baseline_stats
        player_stats_for_anchor = {}
        
        # Stat field mapping for game logs
        stat_field_map = {
            "PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl", "BLK": "blk",
            "3PM": "fg3m", "THREES": "fg3m", "TO": "turnover"
        }
        
        for player_name, player_stats in stats_map.items():
            game_logs = player_stats.get("bdl_game_logs", [])
            baseline = player_stats.get("baseline_stats", {})
            
            # Filter DNP games
            def did_play(game):
                mins = game.get("min", "0") or "0"
                if isinstance(mins, str):
                    mins = mins.split(":")[0] if ":" in mins else mins
                    try:
                        return int(mins) > 0
                    except ValueError:
                        return False
                return float(mins) > 0 if mins else False
            
            played_games = [g for g in game_logs if did_play(g)] if game_logs else []
            l5_games = played_games[:5]
            l10_games = played_games[:10]
            
            # Calculate fresh L5 and L10 averages for each stat type
            for stat_key in ["PTS", "REB", "AST", "STL", "BLK", "3PM", "TO"]:
                key = f"{anchor_normalize_name(player_name)}|{stat_key}"
                log_field = stat_field_map.get(stat_key, stat_key.lower())
                
                # Calculate L5 from game logs (FRESH data)
                l5_avg = None
                if l5_games:
                    values = [g.get(log_field, 0) or 0 for g in l5_games]
                    if values:
                        l5_avg = round(sum(values) / len(values), 1)
                
                # Calculate L10 from game logs (FRESH data) - PREFERRED for anchor
                l10_avg = None
                if l10_games:
                    values = [g.get(log_field, 0) or 0 for g in l10_games]
                    if values:
                        l10_avg = round(sum(values) / len(values), 1)
                
                # Fallback to baseline only if no game logs
                if l5_avg is None:
                    stat_data = baseline.get(stat_key, {})
                    if isinstance(stat_data, dict):
                        l5_avg = stat_data.get("l5_avg")
                
                if l10_avg is None:
                    stat_data = baseline.get(stat_key, {})
                    if isinstance(stat_data, dict):
                        l10_avg = stat_data.get("l10_avg")
                
                # Get season avg from baseline (this is OK since it's not recency-sensitive)
                season_avg = None
                stat_data = baseline.get(stat_key, {})
                if isinstance(stat_data, dict):
                    season_avg = stat_data.get("season_avg")
                
                if l5_avg or l10_avg or season_avg:
                    player_stats_for_anchor[key] = {
                        "l5_avg": l5_avg,
                        "l10_avg": l10_avg,
                        "season_avg": season_avg
                    }
            
            # Also handle combo stats (PRA, PR, PA, RA)
            if l10_games:
                pts_vals_l10 = [g.get("pts", 0) or 0 for g in l10_games]
                reb_vals_l10 = [g.get("reb", 0) or 0 for g in l10_games]
                ast_vals_l10 = [g.get("ast", 0) or 0 for g in l10_games]
                
                pts_l10 = sum(pts_vals_l10) / len(pts_vals_l10) if pts_vals_l10 else 0
                reb_l10 = sum(reb_vals_l10) / len(reb_vals_l10) if reb_vals_l10 else 0
                ast_l10 = sum(ast_vals_l10) / len(ast_vals_l10) if ast_vals_l10 else 0
                
                pts_vals_l5 = [g.get("pts", 0) or 0 for g in l5_games] if l5_games else []
                reb_vals_l5 = [g.get("reb", 0) or 0 for g in l5_games] if l5_games else []
                ast_vals_l5 = [g.get("ast", 0) or 0 for g in l5_games] if l5_games else []
                
                pts_l5 = sum(pts_vals_l5) / len(pts_vals_l5) if pts_vals_l5 else 0
                reb_l5 = sum(reb_vals_l5) / len(reb_vals_l5) if reb_vals_l5 else 0
                ast_l5 = sum(ast_vals_l5) / len(ast_vals_l5) if ast_vals_l5 else 0
                
                # PRA
                player_stats_for_anchor[f"{anchor_normalize_name(player_name)}|PRA"] = {
                    "l5_avg": round(pts_l5 + reb_l5 + ast_l5, 1),
                    "l10_avg": round(pts_l10 + reb_l10 + ast_l10, 1),
                    "season_avg": baseline.get("PRA", {}).get("season_avg") if isinstance(baseline.get("PRA"), dict) else None
                }
                # PR
                player_stats_for_anchor[f"{anchor_normalize_name(player_name)}|PR"] = {
                    "l5_avg": round(pts_l5 + reb_l5, 1),
                    "l10_avg": round(pts_l10 + reb_l10, 1),
                    "season_avg": baseline.get("PR", {}).get("season_avg") if isinstance(baseline.get("PR"), dict) else None
                }
                # PA
                player_stats_for_anchor[f"{anchor_normalize_name(player_name)}|PA"] = {
                    "l5_avg": round(pts_l5 + ast_l5, 1),
                    "l10_avg": round(pts_l10 + ast_l10, 1),
                    "season_avg": baseline.get("PA", {}).get("season_avg") if isinstance(baseline.get("PA"), dict) else None
                }
                # RA
                player_stats_for_anchor[f"{anchor_normalize_name(player_name)}|RA"] = {
                    "l5_avg": round(reb_l5 + ast_l5, 1),
                    "l10_avg": round(reb_l10 + ast_l10, 1),
                    "season_avg": baseline.get("RA", {}).get("season_avg") if isinstance(baseline.get("RA"), dict) else None
                }
        
        logger.info(f"[CACHED_BOARD] Loaded {len(player_stats_for_anchor)} player/stat combos for L5 fallback")
        
        # =================================================================
        # STEP 0.5: MERGE ORACLE APEX ANALYSIS DATA
        # =================================================================
        # The Oracle Apex scan ran BEFORE this, storing analyzed props in
        # oracle_apex_analyzed collection. Merge that VK data into our props.
        # =================================================================
        oracle_apex_map = {}
        try:
            oracle_analyzed = self.db.oracle_apex_analyzed
            apex_count = await oracle_analyzed.count_documents({})
            if apex_count > 0:
                async for apex_prop in oracle_analyzed.find({}, {"_id": 0}):
                    # Key: player_name|stat_type|line
                    key = f"{apex_prop.get('player_name')}|{apex_prop.get('stat_type')}|{apex_prop.get('line')}"
                    oracle_apex_map[key] = apex_prop
                logger.info(f"[CACHED_BOARD] Loaded {len(oracle_apex_map)} Oracle Apex analyzed props")
        except Exception as apex_err:
            logger.warning(f"[CACHED_BOARD] Could not load Oracle Apex data: {apex_err}")
        
        # Merge Oracle Apex data into props
        if oracle_apex_map:
            for prop in props:
                player_name = prop.get("player_name", "")
                stat_type = prop.get("stat_type_extracted") or prop.get("market", "").replace("player_", "").upper()
                line = prop.get("line", 0)
                key = f"{player_name}|{stat_type}|{line}"
                
                apex_data = oracle_apex_map.get(key)
                if apex_data:
                    # Merge VK predictions and Oracle Apex qualification
                    prop["oracle_apex_qualified"] = apex_data.get("oracle_apex_qualified", False)
                    prop["apex_reason"] = apex_data.get("apex_reason")
                    prop["vk_predicted"] = apex_data.get("vk_predicted")
                    prop["vk_edge"] = apex_data.get("vk_edge")
                    prop["vk_prob_over"] = apex_data.get("vk_prob_over")
                    prop["vk_prob_under"] = apex_data.get("vk_prob_under")
                    prop["vk_recommendation"] = apex_data.get("vk_recommendation")
                    prop["cv"] = apex_data.get("cv")
                    prop["h5_rate"] = apex_data.get("h5_rate")
                    prop["h10_rate"] = apex_data.get("h10_rate")
                    prop["h20_rate"] = apex_data.get("h20_rate")
                    prop["l5_hits"] = apex_data.get("l5_hits")
                    prop["l10_hits"] = apex_data.get("l10_hits")
                    prop["l20_hits"] = apex_data.get("l20_hits")
                    prop["l5_avg"] = apex_data.get("l5_avg")
                    prop["l10_avg"] = apex_data.get("l10_avg")
                    prop["l20_avg"] = apex_data.get("l20_avg")
            
            merged_count = sum(1 for p in props if p.get("vk_predicted") is not None)
            apex_qualified_count = sum(1 for p in props if p.get("oracle_apex_qualified"))
            logger.info(f"[CACHED_BOARD] Merged VK data into {merged_count} props, {apex_qualified_count} Oracle Apex qualified")
        
        # STEP 1: APPLY ANCHOR-BASED CLASSIFICATION (with L5 fallback)
        # This overrides Odds API is_demon/is_goblin flags with our own logic:
        # - Alternate ABOVE standard line = DEMON
        # - Alternate BELOW standard line = GOBLIN
        # - If no main line, use L5 avg as anchor
        props = classify_props_by_anchor(props, player_stats_for_anchor)
        logger.info("[CACHED_BOARD] Applied anchor-based tier classification")
        
        # Check if mapper is available
        if ensure_mapper_loaded_callback:
            mapper_ready = await ensure_mapper_loaded_callback()
            if not mapper_ready or self._odds_mapper is None:
                logger.error("[CACHED_BOARD] Odds API Mapper not available - falling back to legacy lookup")
                return await self.build_cached_board_legacy(props, sync_time)
        elif self._odds_mapper is None:
            logger.error("[CACHED_BOARD] Odds API Mapper not set - falling back to legacy lookup")
            return await self.build_cached_board_legacy(props, sync_time)
        
        logger.info("[CACHED_BOARD] Odds API Mapper loaded and ready")
        
        # STEP 2: BATCH LOOKUP ALL PLAYER NAMES
        unique_player_names = set(prop.get("player_name", "Unknown") for prop in props)
        logger.info(f"[CACHED_BOARD] Looking up {len(unique_player_names)} unique players via Mapper")
        
        # Batch lookup via mapper
        player_data_map = await self._odds_mapper.lookupBatch(list(unique_player_names))
        
        # Count matches
        matched_count = sum(1 for v in player_data_map.values() if v is not None)
        unmatched_count = len(unique_player_names) - matched_count
        logger.info(f"[CACHED_BOARD] Mapper results: {matched_count} matched, {unmatched_count} unmatched")
        
        # STEP 3: LOAD REMAINING SUPPLEMENTARY DATA (stats already loaded in Step 0)
        signals_map = await self._load_signals_map()
        ripple_map = await self._load_usage_ripple_map()  # NEW: Load Usage Ripple data
        
        # STEP 4: BUILD PLAYER DICT
        players_dict, unmatched_players = await self._build_players_dict(
            props, player_data_map, stats_map, signals_map, sync_time, ripple_map
        )
        
        # Flag unmatched players
        if unmatched_players:
            logger.warning(f"[CACHED_BOARD] {len(unmatched_players)} players not found in Odds API Mapper: {unmatched_players[:5]}...")
            await self._flag_unmatched_players(unmatched_players, sync_time)
        
        # STEP 5: UPDATE-IN-PLACE (No Blackout Pattern)
        # Instead of delete_many + insert_many, we use bulk upserts
        # This keeps the old data visible while new data is hydrated
        
        # CIRCUIT BREAKER: Don't wipe the board if we have very few players
        # This prevents empty DB scenarios from bad API responses
        if len(players_dict) < 20:
            existing_count = await self.cached_board.count_documents({})
            if existing_count > len(players_dict) * 2:
                logger.warning(f"[CIRCUIT BREAKER] Only {len(players_dict)} players from sync, existing has {existing_count}. Preserving existing data!")
                return {
                    "success": False,
                    "circuit_breaker": True,
                    "reason": f"Sync returned only {len(players_dict)} players, existing has {existing_count}",
                    "players_preserved": existing_count
                }
        
        sorted_players = sorted(
            players_dict.values(),
            key=lambda x: len(x["props"]),
            reverse=True
        )
        
        for idx, player in enumerate(sorted_players):
            player["rank"] = idx + 1
        
        # SHADOW TABLE STRATEGY: Write to temp collection, then merge with vision intel
        # This ensures zero-downtime during sync AND preserves enrichment data
        try:
            # Step 1: Clear and populate temp collection
            await self.cached_board_temp.delete_many({})
            
            if sorted_players:
                # CRITICAL: Preserve vision intel from existing cached_board
                # Fetch all enriched props BEFORE doing anything destructive
                enriched_data = {}
                async for existing in self.cached_board.find({"props.is_vision_enriched": True}, {"_id": 0}):
                    pname = existing.get("player_name", "")
                    enriched_data[pname] = {}
                    for prop in existing.get("props", []):
                        if prop.get("is_vision_enriched"):
                            stat = prop.get("stat_type_extracted") or prop.get("stat_type", "")
                            line = prop.get("line", 0)
                            key = f"{stat}|{line}"
                            enriched_data[pname][key] = {
                                "vision_summary": prop.get("vision_summary"),
                                "is_vision_enriched": prop.get("is_vision_enriched"),
                                "vision_enriched_at": prop.get("vision_enriched_at"),
                                "intel_suite": prop.get("intel_suite"),
                                "vision_score": prop.get("vision_score"),
                                "board": prop.get("board"),
                                "active_badges": prop.get("active_badges"),
                            }
                
                if enriched_data:
                    logger.info(f"[CACHED_BOARD] Preserved vision intel for {len(enriched_data)} players")
                
                # Step 2: Merge preserved vision intel into new player data
                for player in sorted_players:
                    pname = player.get("player_name", "")
                    player_enriched = enriched_data.get(pname, {})
                    if player_enriched:
                        for prop in player.get("props", []):
                            stat = prop.get("stat_type_extracted") or prop.get("stat_type", "")
                            line = prop.get("line", 0)
                            key = f"{stat}|{line}"
                            if key in player_enriched:
                                # Preserve vision intel fields
                                prop.update(player_enriched[key])
                
                # Step 3: Insert all players to temp collection in batches
                batch_size = 100
                for i in range(0, len(sorted_players), batch_size):
                    batch = sorted_players[i:i+batch_size]
                    await self.cached_board_temp.insert_many(batch)
                
                logger.info(f"[CACHED_BOARD] Wrote {len(sorted_players)} players to temp collection")
                
                # Step 4: Atomic swap using rename (if MongoDB supports it)
                # Otherwise, use bulk upsert to live collection
                try:
                    # Try atomic rename (requires admin privileges)
                    # First drop old collection, then rename temp to live
                    await self.cached_board.drop()
                    await self.db.command({
                        "renameCollection": f"{self.db.name}.dg_cached_board_temp",
                        "to": f"{self.db.name}.dg_cached_board"
                    })
                    logger.info(f"[CACHED_BOARD] Atomic swap completed - vision intel preserved")
                except Exception as rename_error:
                    # Fallback: bulk upsert to live collection
                    logger.warning(f"[CACHED_BOARD] Atomic rename failed, using bulk upsert: {rename_error}")
                    
                    from pymongo import UpdateOne
                    
                    # For each player, fetch existing vision enrichment data to preserve
                    for player in sorted_players:
                        existing = await self.cached_board.find_one(
                            {"player_name": player["player_name"]},
                            {"_id": 0, "props": 1}
                        )
                        if existing and existing.get("props"):
                            # Create a lookup of existing enriched props by stat+line
                            enriched_props = {}
                            for prop in existing.get("props", []):
                                if prop.get("is_vision_enriched"):
                                    key = f"{prop.get('stat_type_extracted')}|{prop.get('line')}"
                                    enriched_props[key] = {
                                        "vision_summary": prop.get("vision_summary"),
                                        "is_vision_enriched": prop.get("is_vision_enriched"),
                                        "vision_enriched_at": prop.get("vision_enriched_at"),
                                        "intel_suite": prop.get("intel_suite")
                                    }
                            
                            # Transfer enriched data to new props
                            if enriched_props:
                                for new_prop in player.get("props", []):
                                    key = f"{new_prop.get('stat_type_extracted')}|{new_prop.get('line')}"
                                    if key in enriched_props:
                                        new_prop.update(enriched_props[key])
                    
                    bulk_ops = [
                        UpdateOne(
                            {"player_name": player["player_name"]},
                            {"$set": player},
                            upsert=True
                        )
                        for player in sorted_players
                    ]
                    
                    # Execute in batches
                    for i in range(0, len(bulk_ops), batch_size):
                        batch = bulk_ops[i:i+batch_size]
                        result = await self.cached_board.bulk_write(batch)
                        logger.info(f"[CACHED_BOARD] Batch {i//batch_size + 1}: matched={result.matched_count}, upserted={result.upserted_count}")
                    
                    # Verify write
                    count_after = await self.cached_board.count_documents({})
                    logger.info(f"[CACHED_BOARD] Post-write count: {count_after}")
                    
                    # Remove stale players
                    current_player_names = {p["player_name"] for p in sorted_players}
                    delete_result = await self.cached_board.delete_many({
                        "player_name": {"$nin": list(current_player_names)},
                        "synced_at": {"$lt": sync_time.isoformat()}
                    })
                    logger.info(f"[CACHED_BOARD] Deleted {delete_result.deleted_count} stale players")
                    
                    count_final = await self.cached_board.count_documents({})
                    logger.info(f"[CACHED_BOARD] Final count after cleanup: {count_final}")
                    
                    logger.info(f"[CACHED_BOARD] Bulk upsert completed - {len(sorted_players)} players")
                    
        except Exception as e:
            logger.error(f"[CACHED_BOARD] Shadow table sync failed: {e}")
            raise
        
        # Store sync metadata
        verified_count = sum(1 for p in sorted_players if p.get("is_verified"))
        mapper_matched = sum(1 for p in sorted_players if p.get("is_mapper_matched"))
        
        await self.sync_log.update_one(
            {"type": "cached_board"},
            {"$set": {
                "type": "cached_board",
                "synced_at": sync_time.isoformat(),
                "players_count": len(sorted_players),
                "verified_count": verified_count,
                "mapper_matched_count": mapper_matched,
                "unverified_count": len(sorted_players) - verified_count,
                "total_props": sum(len(p["props"]) for p in sorted_players),
                "lookup_method": "odds_api_mapper_v4"
            }},
            upsert=True
        )
        
        logger.info(f"[CACHED_BOARD] Built board: {len(sorted_players)} players ({mapper_matched} mapper-matched, {verified_count} verified)")
        
        # Build derived collections
        await self._build_derived_collections(players_dict, sync_time)
        
        # TRACK LINE MOVEMENTS for Top Picks
        try:
            from services.line_movement_tracker import get_line_tracker
            line_tracker = get_line_tracker(self.db)
            
            # First detect movements (compare to previous lines)
            movements = await line_tracker.detect_line_movements(sorted_players)
            logger.info(f"[CACHED_BOARD] Detected {len(movements)} line movements")
            
            # Then record current lines for next sync comparison
            lines_recorded = await line_tracker.record_current_lines(sorted_players)
            logger.info(f"[CACHED_BOARD] Recorded {lines_recorded} lines for tracking")
            
        except Exception as e:
            logger.warning(f"[CACHED_BOARD] Line tracking failed (non-fatal): {e}")
        
        return {
            "success": True,
            "players_count": len(sorted_players),
            "verified_count": verified_count,
            "mapper_matched_count": mapper_matched
        }
    
    async def build_cached_board_legacy(
        self,
        props: List[Dict],
        sync_time: datetime
    ) -> Dict[str, Any]:
        """
        LEGACY FALLBACK: Name-based lookup method.
        Used only if Odds API Mapper fails to initialize.
        """
        logger.warning("[CACHED_BOARD_LEGACY] Using legacy name-based lookup (Mapper unavailable)")
        
        # Load player stats FIRST (needed for L5 fallback classification)
        stats_map = await self._load_stats_map()
        
        # Build player_stats dict for anchor classification (L5 fallback)
        # CRITICAL: Calculate L5 from bdl_game_logs, NOT from stale baseline_stats
        player_stats_for_anchor = {}
        
        stat_field_map = {
            "PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl", "BLK": "blk",
            "3PM": "fg3m", "THREES": "fg3m", "TO": "turnover"
        }
        
        for player_name, player_stats in stats_map.items():
            game_logs = player_stats.get("bdl_game_logs", [])
            baseline = player_stats.get("baseline_stats", {})
            
            def did_play(game):
                mins = game.get("min", "0") or "0"
                if isinstance(mins, str):
                    mins = mins.split(":")[0] if ":" in mins else mins
                    try:
                        return int(mins) > 0
                    except ValueError:
                        return False
                return float(mins) > 0 if mins else False
            
            played_games = [g for g in game_logs if did_play(g)] if game_logs else []
            l5_games = played_games[:5]
            l10_games = played_games[:10]
            
            for stat_key in ["PTS", "REB", "AST", "STL", "BLK", "3PM", "TO"]:
                key = f"{anchor_normalize_name(player_name)}|{stat_key}"
                log_field = stat_field_map.get(stat_key, stat_key.lower())
                
                l5_avg = None
                l10_avg = None
                if l5_games:
                    values = [g.get(log_field, 0) or 0 for g in l5_games]
                    if values:
                        l5_avg = round(sum(values) / len(values), 1)
                
                if l10_games:
                    values = [g.get(log_field, 0) or 0 for g in l10_games]
                    if values:
                        l10_avg = round(sum(values) / len(values), 1)
                
                if l5_avg is None:
                    stat_data = baseline.get(stat_key, {})
                    if isinstance(stat_data, dict):
                        l5_avg = stat_data.get("l5_avg")
                
                if l10_avg is None:
                    stat_data = baseline.get(stat_key, {})
                    if isinstance(stat_data, dict):
                        l10_avg = stat_data.get("l10_avg")
                
                season_avg = None
                stat_data = baseline.get(stat_key, {})
                if isinstance(stat_data, dict):
                    season_avg = stat_data.get("season_avg")
                
                if l5_avg or l10_avg or season_avg:
                    player_stats_for_anchor[key] = {
                        "l5_avg": l5_avg,
                        "l10_avg": l10_avg,
                        "season_avg": season_avg
                    }
            
            if l10_games:
                pts_vals_l10 = [g.get("pts", 0) or 0 for g in l10_games]
                reb_vals_l10 = [g.get("reb", 0) or 0 for g in l10_games]
                ast_vals_l10 = [g.get("ast", 0) or 0 for g in l10_games]
                
                pts_l10 = sum(pts_vals_l10) / len(pts_vals_l10) if pts_vals_l10 else 0
                reb_l10 = sum(reb_vals_l10) / len(reb_vals_l10) if reb_vals_l10 else 0
                ast_l10 = sum(ast_vals_l10) / len(ast_vals_l10) if ast_vals_l10 else 0
                
                pts_vals_l5 = [g.get("pts", 0) or 0 for g in l5_games] if l5_games else []
                reb_vals_l5 = [g.get("reb", 0) or 0 for g in l5_games] if l5_games else []
                ast_vals_l5 = [g.get("ast", 0) or 0 for g in l5_games] if l5_games else []
                
                pts_l5 = sum(pts_vals_l5) / len(pts_vals_l5) if pts_vals_l5 else 0
                reb_l5 = sum(reb_vals_l5) / len(reb_vals_l5) if reb_vals_l5 else 0
                ast_l5 = sum(ast_vals_l5) / len(ast_vals_l5) if ast_vals_l5 else 0
                
                player_stats_for_anchor[f"{anchor_normalize_name(player_name)}|PRA"] = {
                    "l5_avg": round(pts_l5 + reb_l5 + ast_l5, 1),
                    "l10_avg": round(pts_l10 + reb_l10 + ast_l10, 1),
                    "season_avg": baseline.get("PRA", {}).get("season_avg") if isinstance(baseline.get("PRA"), dict) else None
                }
                player_stats_for_anchor[f"{anchor_normalize_name(player_name)}|PR"] = {
                    "l5_avg": round(pts_l5 + reb_l5, 1),
                    "l10_avg": round(pts_l10 + reb_l10, 1),
                    "season_avg": baseline.get("PR", {}).get("season_avg") if isinstance(baseline.get("PR"), dict) else None
                }
                player_stats_for_anchor[f"{anchor_normalize_name(player_name)}|PA"] = {
                    "l5_avg": round(pts_l5 + ast_l5, 1),
                    "l10_avg": round(pts_l10 + ast_l10, 1),
                    "season_avg": baseline.get("PA", {}).get("season_avg") if isinstance(baseline.get("PA"), dict) else None
                }
                player_stats_for_anchor[f"{anchor_normalize_name(player_name)}|RA"] = {
                    "l5_avg": round(reb_l5 + ast_l5, 1),
                    "l10_avg": round(reb_l10 + ast_l10, 1),
                    "season_avg": baseline.get("RA", {}).get("season_avg") if isinstance(baseline.get("RA"), dict) else None
                }
        
        # STEP 1: APPLY ANCHOR-BASED CLASSIFICATION (with L5 fallback)
        props = classify_props_by_anchor(props, player_stats_for_anchor)
        logger.info("[CACHED_BOARD_LEGACY] Applied anchor-based tier classification")
        
        # Load master roster into memory
        master_roster_map = {}
        roster_cursor = self.master_roster.find({}, {"_id": 0})
        async for player in roster_cursor:
            normalized = player.get("normalized_name", "").lower()
            if normalized:
                master_roster_map[normalized] = player
        
        logger.info(f"[CACHED_BOARD_LEGACY] Loaded {len(master_roster_map)} players from master roster")
        
        # Load remaining supplementary data
        signals_map = await self._load_signals_map()
        
        # Group props by player and enrich
        players_dict = {}
        unmatched_players = []
        
        for prop in props:
            player_name = prop.get("player_name", "Unknown")
            normalized_name = sanitize_player_name(player_name).lower()
            
            if player_name not in players_dict:
                roster_player = master_roster_map.get(normalized_name)
                
                # Try suffix variations
                if not roster_player:
                    for suffix in [" jr", " iii", " ii", " iv", " sr"]:
                        clean_name = normalized_name.replace(suffix, "").strip()
                        roster_player = master_roster_map.get(clean_name)
                        if roster_player:
                            break
                
                if not roster_player:
                    unmatched_players.append(player_name)
                    players_dict[player_name] = self._create_unmatched_player(
                        player_name, prop, sync_time
                    )
                else:
                    players_dict[player_name] = self._create_matched_player_legacy(
                        player_name, roster_player, stats_map, signals_map, normalized_name, sync_time
                    )
            
            # Add prop to player
            self._add_prop_to_player(players_dict[player_name], prop)
        
        if unmatched_players:
            logger.warning(f"[CACHED_BOARD_LEGACY] {len(unmatched_players)} players not in master roster: {unmatched_players[:5]}...")
        
        # UPDATE-IN-PLACE (No Blackout Pattern)
        sorted_players = sorted(
            players_dict.values(),
            key=lambda x: len(x["props"]),
            reverse=True
        )
        
        for idx, player in enumerate(sorted_players):
            player["rank"] = idx + 1
        
        # BULK UPSERT - Update existing, insert new, keep old visible
        if sorted_players:
            bulk_ops = []
            for player in sorted_players:
                bulk_ops.append({
                    "filter": {"player_name": player["player_name"]},
                    "update": {"$set": player},
                    "upsert": True
                })
            
            batch_size = 100
            for i in range(0, len(bulk_ops), batch_size):
                batch = bulk_ops[i:i+batch_size]
                from pymongo import UpdateOne
                await self.cached_board.bulk_write([
                    UpdateOne(op["filter"], op["update"], upsert=op["upsert"]) 
                    for op in batch
                ])
            
            # Remove stale players
            current_player_names = {p["player_name"] for p in sorted_players}
            await self.cached_board.delete_many({
                "player_name": {"$nin": list(current_player_names)},
                "synced_at": {"$lt": sync_time.isoformat()}
            })
            
            logger.info(f"[CACHED_BOARD_LEGACY] Updated {len(sorted_players)} players in-place (no blackout)")
        
        verified_count = sum(1 for p in sorted_players if p.get("is_verified"))
        await self.sync_log.update_one(
            {"type": "cached_board"},
            {"$set": {
                "type": "cached_board",
                "synced_at": sync_time.isoformat(),
                "players_count": len(sorted_players),
                "verified_count": verified_count,
                "unverified_count": len(sorted_players) - verified_count,
                "total_props": sum(len(p["props"]) for p in sorted_players),
                "lookup_method": "legacy_name_based"
            }},
            upsert=True
        )
        
        logger.info(f"[CACHED_BOARD_LEGACY] Built board: {len(sorted_players)} players ({verified_count} verified)")
        
        # Build derived collections
        await self._build_derived_collections(players_dict, sync_time)
        
        return {
            "success": True,
            "players_count": len(sorted_players),
            "verified_count": verified_count,
            "lookup_method": "legacy"
        }
    
    # ==================== PRIVATE HELPER METHODS ====================
    
    async def _load_stats_map(self) -> Dict[str, Any]:
        """
        Load player stats from MASTER HUB indexed by normalized name.
        
        This includes baseline_stats with L5/L10/season averages needed for:
        - L5 fallback anchor classification
        - Stats display on player cards
        
        Also includes bdl_game_logs for accurate per-line hit rate calculations.
        """
        stats_map = {}
        
        # Load from nba_master_hub_2026 (SSOT for player stats)
        # All records have bdl_id - this is the primary key
        # CRITICAL: Include bdl_game_logs for per-line hit rate calculation
        hub_cursor = self.db.nba_master_hub_2026.find(
            {"bdl_id": {"$exists": True}},
            {"_id": 0, "bdl_id": 1, "display_name": 1, "baseline_stats": 1, "team": 1, "bdl_game_logs": 1}
        )
        
        async for player in hub_cursor:
            player_name = player.get("display_name", "")
            normalized = sanitize_player_name(player_name).lower()
            if normalized and player.get("baseline_stats"):
                stats_map[normalized] = {
                    "bdl_id": player.get("bdl_id"),
                    "player_name": player_name,
                    "baseline_stats": player.get("baseline_stats", {}),
                    "team": player.get("team"),
                    "bdl_game_logs": player.get("bdl_game_logs", [])
                }
        
        logger.info(f"[CACHED_BOARD] Loaded {len(stats_map)} player stats from master hub")
        return stats_map
    
    async def _load_signals_map(self) -> Dict[str, Any]:
        """Load social signals indexed by player name"""
        signals_map = {}
        try:
            signals_cursor = self.db.dg_social_signals.find({}, {"_id": 0})
            async for signal in signals_cursor:
                player_name = signal.get("player_name", "")
                if player_name:
                    signals_map[player_name.lower()] = signal
        except Exception as e:
            logger.warning(f"[CACHED_BOARD] Could not load social signals: {e}")
        return signals_map
    
    async def _load_usage_ripple_map(self) -> Dict[str, Any]:
        """Load usage ripple data from daily_insights indexed by player name"""
        ripple_map = {}
        try:
            # Only get players with usage_bump data
            ripple_cursor = self.db.dg_daily_insights.find(
                {"usage_bump_percent": {"$gt": 0}},
                {"_id": 0, "player_name": 1, "usage_bump_percent": 1, 
                 "usage_bump_reason": 1, "injured_teammates": 1, "ripple_detected": 1}
            )
            async for insight in ripple_cursor:
                player_name = insight.get("player_name", "")
                if player_name:
                    ripple_map[player_name.lower()] = insight
            if ripple_map:
                logger.info(f"[CACHED_BOARD] Loaded {len(ripple_map)} players with Usage Ripple boosts")
        except Exception as e:
            logger.warning(f"[CACHED_BOARD] Could not load usage ripple data: {e}")
        return ripple_map
    
    async def _build_players_dict(
        self,
        props: List[Dict],
        player_data_map: Dict[str, Any],
        stats_map: Dict[str, Any],
        signals_map: Dict[str, Any],
        sync_time: datetime,
        ripple_map: Dict[str, Any] = None
    ) -> tuple:
        """Build players dictionary from props with mapper data"""
        players_dict = {}
        unmatched_players = []
        
        for prop in props:
            player_name = prop.get("player_name", "Unknown")
            
            if player_name not in players_dict:
                hub_player = player_data_map.get(player_name)
                
                if not hub_player:
                    unmatched_players.append(player_name)
                    players_dict[player_name] = self._create_unmatched_player(
                        player_name, prop, sync_time
                    )
                else:
                    players_dict[player_name] = self._create_matched_player(
                        player_name, hub_player, stats_map, signals_map, sync_time, ripple_map
                    )
            
            # Add prop to player
            self._add_prop_to_player(players_dict[player_name], prop)
        
        return players_dict, unmatched_players
    
    def _create_unmatched_player(
        self,
        player_name: str,
        prop: Dict,
        sync_time: datetime
    ) -> Dict[str, Any]:
        """Create player dict for unmatched player"""
        return {
            "player_name": player_name,
            "player_id": None,
            "bdl_id": None,  # Primary join key (missing for unmatched)
            "bdl_player_id": None,  # Legacy field name
            "team": prop.get("home_team") or prop.get("away_team") or "UNK",
            "photo_url": None,
            "headshot_url": None,
            "nba_com_id": None,
            "espn_id": None,
            "position": None,
            # SSOT: Stats REMOVED - use Master Hub (PIPE 1) for stats
            "is_verified": False,
            "is_mapper_matched": False,
            "props": [],
            "demons": [],
            "goblins": [],
            "synced_at": sync_time.isoformat()
        }
    
    def _create_matched_player(
        self,
        player_name: str,
        hub_player: Dict,
        stats_map: Dict[str, Any],
        signals_map: Dict[str, Any],
        sync_time: datetime,
        ripple_map: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Create player dict from mapper/hub data.
        
        CRITICAL (LOCAL-FIRST DATA MODEL):
        - nba_id MUST come from nba_master_hub_2026 (SSOT)
        - photo_url MUST be /static/player-headshots/{nba_id}.png
        - NO /api/proxy/ URLs - they are BANNED
        """
        player_id = hub_player.get("player_id")
        normalized_name = sanitize_player_name(player_name).lower()
        
        player_stats = stats_map.get(normalized_name, {})
        social = signals_map.get(player_name.lower(), {})
        ripple = (ripple_map or {}).get(player_name.lower(), {})
        
        hub_stats = hub_player.get("stats", {})
        season_avg = hub_stats.get("season_avg", {})
        baseline_stats = player_stats.get("baseline_stats", {})
        bdl_game_logs = player_stats.get("bdl_game_logs", [])
        
        # =====================================================================
        # LOCAL-FIRST: Photo URL from static files ONLY
        # =====================================================================
        # nba_id is the ONLY valid source for player photos.
        # Photo URL is ALWAYS /static/player-headshots/{nba_id}.png
        # NO PROXY URLS - NO EXTERNAL FALLBACKS
        nba_id = hub_player.get("nba_id")
        
        # Hardcode local static path - NO PROXIES
        if nba_id:
            photo_url = f"/static/player-headshots/{nba_id}.png"
        else:
            photo_url = None  # No nba_id = no photo (don't use external fallbacks)
        
        # Get the bdl_id - this is the primary join key
        bdl_id = hub_player.get("bdl_id")
        
        return {
            # Primary identifiers - CRITICAL: bdl_id is the primary join key
            "player_name": player_name,
            "player_id": player_id,
            "bdl_id": bdl_id,  # Primary join key for master hub lookups
            "bdl_player_id": bdl_id,  # Legacy field name (same value)
            "nba_com_id": nba_id,  # HYDRATED: Explicitly set from Master Hub nba_id
            "nba_id": nba_id,  # Also store as nba_id for direct access
            "espn_id": hub_player.get("espn_id"),
            
            # Team info
            "team": hub_player.get("team"),
            "team_name": hub_player.get("team_name"),
            "team_logo_url": None,
            
            # Photo - LOCAL-FIRST: /static/player-headshots/{nba_id}.png ONLY
            # NO PROXY URLS - NO EXTERNAL FALLBACKS
            "photo_url": photo_url,
            "headshot_url": photo_url,  # Keep in sync for legacy compatibility
            "photo_source": "local_static" if nba_id else None,
            
            # Player info
            "position": hub_player.get("position"),
            "jersey_number": hub_player.get("jersey"),
            
            # Stats for hit_rates calculation
            "baseline_stats": baseline_stats,
            "bdl_game_logs": bdl_game_logs,  # CRITICAL: For per-line hit rate calculation
            "season_avg": season_avg,
            "games_played": season_avg.get("gp", player_stats.get("games_played", 0)),
            
            # Social signals
            "volatility_flag": social.get("volatility_flag", False),
            "revenge_game": social.get("revenge_game", False),
            "injury_status": hub_player.get("injury", {}).get("status") or social.get("injury_status"),
            
            # Usage Ripple data (from daily_insights)
            "usage_bump_percent": ripple.get("usage_bump_percent", 0),
            "usage_bump_reason": ripple.get("usage_bump_reason"),
            "injured_teammates": ripple.get("injured_teammates", []),
            "ripple_detected": ripple.get("ripple_detected", False),
            
            # Verification
            "is_verified": True,
            "is_mapper_matched": True,
            
            # Props containers
            "props": [],
            "demons": [],
            "goblins": [],
            "synced_at": sync_time.isoformat()
        }
    
    def _create_matched_player_legacy(
        self,
        player_name: str,
        roster_player: Dict,
        stats_map: Dict[str, Any],
        signals_map: Dict[str, Any],
        normalized_name: str,
        sync_time: datetime
    ) -> Dict[str, Any]:
        """Create player dict from legacy roster data"""
        player_stats = stats_map.get(normalized_name, {})
        social = signals_map.get(player_name.lower(), {})
        baseline_stats = player_stats.get("baseline_stats", {})
        
        # Get bdl_id - primary join key
        bdl_id = roster_player.get("bdl_player_id") or player_stats.get("bdl_id")
        
        return {
            "player_name": player_name,
            "bdl_id": bdl_id,  # Primary join key
            "bdl_player_id": bdl_id,  # Legacy field name (same value)
            "nba_com_id": roster_player.get("nba_com_id"),
            "espn_id": roster_player.get("espn_id"),
            "team": roster_player.get("team_abbreviation") or player_stats.get("team"),
            "team_name": roster_player.get("team_name"),
            "team_logo_url": roster_player.get("team_logo_url"),
            "photo_url": roster_player.get("photo_url"),
            "photo_source": roster_player.get("photo_source"),
            "position": roster_player.get("position"),
            "jersey_number": roster_player.get("jersey_number"),
            "games_played": baseline_stats.get("games_played") or player_stats.get("games_played", 0),
            # CRITICAL: Include baseline_stats for hit rate calculation
            "baseline_stats": baseline_stats,
            "volatility_flag": social.get("volatility_flag", False),
            "revenge_game": social.get("revenge_game", False),
            "injury_status": social.get("injury_status"),
            "is_verified": True,
            "props": [],
            "demons": [],
            "goblins": [],
            "synced_at": sync_time.isoformat()
        }
    
    def _add_prop_to_player(self, player: Dict, prop: Dict) -> None:
        """Add prop to player with hit_rates calculated from bdl_game_logs"""
        
        # Get stat type - use extracted short form if available, otherwise derive from market
        stat_type = prop.get("stat_type_extracted")
        if not stat_type:
            # Fallback: derive from market
            market = prop.get("market", "")
            stat_type = market.replace("player_", "").replace("_alternate", "").upper()
            # Map common stat names to short form
            stat_map = {
                "POINTS": "PTS",
                "REBOUNDS": "REB",
                "ASSISTS": "AST",
                "STEALS": "STL",
                "BLOCKS": "BLK",
                "THREES": "3PM",
                "TURNOVERS": "TO",
            }
            stat_type = stat_map.get(stat_type, stat_type)
        
        line = prop.get("line", 0)
        
        # Get baseline stats for averages display
        baseline = player.get("baseline_stats", {})
        stat_baseline = baseline.get(stat_type, {})
        
        # Get bdl_game_logs for per-line hit rate calculation
        # These are sorted most-recent-first by the sync service
        bdl_logs = player.get("bdl_game_logs", [])
        
        # Map stat types to bdl_game_logs field names
        # bdl_game_logs uses: pts, reb, ast, fg3m (not tptfgm), turnover (not tov), stl, blk
        stat_field_map = {
            "PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl", "BLK": "blk",
            "3PM": "fg3m", "THREES": "fg3m", "TO": "turnover"
        }
        log_field = stat_field_map.get(stat_type.upper(), stat_type.lower())
        
        # Extract values from game logs for this stat type
        def get_stat_value(game: Dict) -> float:
            """Get the stat value from a game log, handling combined stats"""
            if stat_type.upper() in ["PRA"]:
                return (game.get("pts", 0) or 0) + (game.get("reb", 0) or 0) + (game.get("ast", 0) or 0)
            elif stat_type.upper() in ["P+R", "PR"]:
                return (game.get("pts", 0) or 0) + (game.get("reb", 0) or 0)
            elif stat_type.upper() in ["P+A", "PA"]:
                return (game.get("pts", 0) or 0) + (game.get("ast", 0) or 0)
            elif stat_type.upper() in ["R+A", "RA"]:
                return (game.get("reb", 0) or 0) + (game.get("ast", 0) or 0)
            else:
                return game.get(log_field, 0) or 0
        
        # Filter out DNP games (min = 0 or "0" or empty)
        def did_play(game: Dict) -> bool:
            mins = game.get("min", "0") or "0"
            if isinstance(mins, str):
                mins = mins.split(":")[0] if ":" in mins else mins
                try:
                    return int(mins) > 0
                except ValueError:
                    return False
            return float(mins) > 0 if mins else False
        
        played_games = [g for g in bdl_logs if did_play(g)]
        l10_values = [get_stat_value(g) for g in played_games[:10]]
        l5_values = l10_values[:5] if len(l10_values) >= 5 else l10_values
        
        # =====================================================================
        # CRITICAL: SINGLE SOURCE OF TRUTH FOR L5/L10 AVERAGES
        # =====================================================================
        # We calculate l5_avg and l10_avg from bdl_game_logs, NOT baseline_stats.
        #
        # WHY: baseline_stats can be STALE (updated only during scheduled syncs)
        #      but bdl_game_logs are the RAW game values that are always current.
        #
        # NEVER use: stat_baseline.get("l5_avg") or stat_baseline.get("l10_avg")
        # ALWAYS calculate from the l5_values/l10_values arrays extracted above.
        #
        # This was a CRITICAL BUG that caused incorrect averages to appear in
        # War Zone, Safe Haven, and all other pick displays (March 2026).
        # =====================================================================
        
        hit_rates = {
            "l10_rate": None,
            "l5_rate": None,
            "l10_hit_count": 0,
            "l5_hit_count": 0,
            "l5_avg": None,
            "season_avg": stat_baseline.get("season_avg"),  # season_avg is OK from baseline
            "l10_avg": None
        }
        
        # Calculate averages from ACTUAL game log values (not stale baseline_stats)
        if l10_values:
            hit_rates["l10_avg"] = round(sum(l10_values) / len(l10_values), 1)
        else:
            # Fallback to baseline ONLY if no game logs exist at all
            hit_rates["l10_avg"] = stat_baseline.get("l10_avg")
        
        if l5_values:
            hit_rates["l5_avg"] = round(sum(l5_values) / len(l5_values), 1)
        else:
            # Fallback to baseline ONLY if no game logs exist at all
            hit_rates["l5_avg"] = stat_baseline.get("l5_avg")
        
        # Calculate hit rates using > (strictly over) for betting props
        if l10_values and line:
            over_hits = sum(1 for v in l10_values if v > line)
            hit_rates["l10_rate"] = round((over_hits / len(l10_values)) * 100)
            hit_rates["l10_hit_count"] = over_hits
        
        if l5_values and line:
            over_hits = sum(1 for v in l5_values if v > line)
            hit_rates["l5_rate"] = round((over_hits / len(l5_values)) * 100) if l5_values else None
            hit_rates["l5_hit_count"] = over_hits
        
        # Add hit_rates to prop (nested object)
        prop["hit_rates"] = hit_rates
        prop["stat_type"] = stat_type
        
        # ALSO flatten to prop level for functions that expect h5_rate/h10_hit_rate
        prop["h5_rate"] = hit_rates["l5_rate"]
        prop["h10_rate"] = hit_rates["l10_rate"]
        prop["h10_hit_rate"] = hit_rates["l10_rate"]  # Legacy field name
        prop["h5_hit_rate"] = hit_rates["l5_rate"]    # Legacy field name
        prop["l5_avg"] = hit_rates["l5_avg"]
        prop["l10_avg"] = hit_rates["l10_avg"]
        prop["season_avg"] = hit_rates["season_avg"]
        prop["l10_hit_count"] = hit_rates["l10_hit_count"]
        prop["l5_hit_count"] = hit_rates["l5_hit_count"]
        
        # =====================================================================
        # INTEL SUITE: Build Vision Intelligence data for frontend
        # =====================================================================
        # This provides the complete data needed for the Vision Intel Suite UI
        opponent = prop.get("away_team") if prop.get("away_team") != player.get("team") else prop.get("home_team")
        
        # Calculate stability from hit rates
        l5_rate = hit_rates["l5_rate"] or 0
        l10_rate = hit_rates["l10_rate"] or 0
        stability_score = int((l5_rate + l10_rate) / 2) if (l5_rate or l10_rate) else 50
        
        # Get blowout risk from prop if available
        blowout_level = prop.get("blowout_risk", "UNKNOWN")
        
        # Get REAL DvP analysis from the dvp_service
        from services.dvp_service import get_full_dvp_analysis, get_dvp_rank, get_dvp_rank_color
        
        dvp_rank = get_dvp_rank(opponent, stat_type) if opponent else 15
        dvp_color = get_dvp_rank_color(dvp_rank)
        
        # Determine friction level based on rank
        if dvp_rank <= 9:
            friction_level = "High"  # Best defense = hard matchup
            friction_label = f"Rank #{dvp_rank} (Tough)"
        elif dvp_rank >= 25:
            friction_level = "Low"  # Worst defense = easy matchup
            friction_label = f"Rank #{dvp_rank} (Soft)"
        else:
            friction_level = "Medium"
            friction_label = f"Rank #{dvp_rank}"
        
        prop["intel_suite"] = {
            # Blowout Risk Analysis
            "blowout_risk": {
                "risk_level": blowout_level,
                "player_team_record": player.get("team_record", ""),
                "opponent_team_record": prop.get("opponent_record", ""),
                "warning": f"Blowout risk: {blowout_level}" if blowout_level in ["HIGH", "MEDIUM"] else None
            },
            # Matchup DvP Analysis - NOW WITH REAL DATA
            "matchup_dvp": {
                "display": f"vs {opponent}" if opponent else "TBD",
                "opponent": opponent,
                "opponent_abbr": opponent,
                "friction_level": friction_level,
                "friction_label": friction_label,
                "color": dvp_color,
                "dvp_rank": dvp_rank,
                "stat_type": stat_type
            },
            # Pace Delta
            "pace_delta": {
                "display": "0.0",
                "possessions": 0,
                "tempo_label": "Neutral Pace",
                "expected_game_pace": "98.0"
            },
            # Stability Index from hit rates
            "stability_index": {
                "display": f"{stability_score}%",
                "score": stability_score,
                "consistency": "Consistent" if stability_score >= 70 else "Variable" if stability_score >= 50 else "Volatile"
            },
            # Usage Ripple (from vacuum data if available)
            "usage_ripple": {
                "display": "Standard Volume",
                "reasoning": "Based on team role and recent minutes",
                "bump_percent": 0,
                "shift_label": "Normal",
                "injuries_affecting": []
            },
            # Context Badges
            "context_badges": [],
            # Vision Insight (placeholder for AI)
            "vision_insight": {
                "primary": f"Analyzing {player.get('player_name', 'player')} {stat_type} @ {line}",
                "reasons": [],
                "confidence": "STANDARD"
            },
            # Preserve existing enrichment data
            "momentum_data": prop.get("momentum_data"),
            "vacuum_data": prop.get("vacuum_data"),
            "whistle_data": prop.get("whistle_data")
        }
        
        # Add context badges based on prop flags
        badges = []
        if prop.get("trap_risk"):
            badges.append("trap_risk")
        if prop.get("sharp_movement"):
            badges.append("sharp_movement")
        if stability_score >= 80:
            badges.append("consistent")
        if blowout_level == "HIGH":
            badges.append("blowout_risk")
        prop["intel_suite"]["context_badges"] = badges
        
        # Mark as vision-enriched so frontend knows to display the intel_suite
        prop["is_vision_enriched"] = True
        
        # =====================================================================
        # ANOMALY DETECTION: Flag oddsmaker errors
        # =====================================================================
        # STEP 1: Season average >= line (oddsmaker set it wrong historically)
        # STEP 2: L10 hit rate >= 50% (confirm they're still hitting it)
        #
        # Demon anomaly: Season avg >= demon line AND L10 HR >= 50%
        # Goblin anomaly: Hit rate >= 90% (near-guaranteed hit)
        
        season_avg = hit_rates["season_avg"] or 0
        l10_avg = hit_rates["l10_avg"] or 0
        h10_rate = hit_rates["l10_rate"] or 0
        is_demon = prop.get("is_demon", False)
        is_goblin = prop.get("is_goblin", False)
        
        # Demon anomaly: Season avg beats the demon line + confirmed by L10 hit rate
        is_demon_anomaly = is_demon and season_avg and line and season_avg >= line and h10_rate >= 50
        is_goblin_anomaly = is_goblin and h10_rate >= 90
        is_anomaly = is_demon_anomaly or is_goblin_anomaly
        
        prop["is_anomaly"] = is_anomaly
        prop["is_demon_anomaly"] = is_demon_anomaly
        prop["is_goblin_anomaly"] = is_goblin_anomaly
        
        # Calculate margins
        if season_avg and line:
            prop["season_margin"] = round(season_avg - line, 1)
        else:
            prop["season_margin"] = 0
            
        if l10_avg and line:
            prop["margin"] = round(l10_avg - line, 1)
        else:
            prop["margin"] = 0
        
        player["props"].append(prop)
        
        if prop.get("is_demon"):
            player["demons"].append(prop)
        elif prop.get("is_goblin"):
            player["goblins"].append(prop)
        
        # Track standard props
        if not prop.get("is_demon") and not prop.get("is_goblin"):
            if "standard" not in player:
                player["standard"] = []
            player["standard"].append(prop)
        
        # Calculate opponent (do once per player)
        if not player.get("opponent"):
            home_team = prop.get("home_team")
            away_team = prop.get("away_team")
            player_team = player.get("team")
            
            if player_team and home_team and away_team:
                if player_team == home_team:
                    player["opponent"] = away_team
                    player["opponent_abbr"] = away_team
                elif player_team == away_team:
                    player["opponent"] = home_team
                    player["opponent_abbr"] = home_team
                else:
                    player["opponent"] = away_team if home_team else None
                    player["opponent_abbr"] = away_team if home_team else None
    
    async def _flag_unmatched_players(
        self,
        unmatched_players: List[str],
        sync_time: datetime
    ) -> None:
        """Flag unmatched players for manual review"""
        for player_name in unmatched_players[:20]:
            await self.flagged_players.update_one(
                {"player_name": player_name},
                {"$set": {
                    "player_name": player_name,
                    "flagged_at": sync_time.isoformat(),
                    "reason": "Not found in odds_api_mapping_master",
                    "source": "odds_api_v4"
                }},
                upsert=True
            )
    
    async def _build_derived_collections(
        self,
        players_dict: Dict[str, Dict],
        sync_time: datetime,
        skip_legacy_tiers: bool = False
    ) -> None:
        """Build derived collections (War Zone, Goblin Vault, etc.) and trigger Vision Intel
        
        Args:
            players_dict: Dictionary of player data
            sync_time: Current sync timestamp
            skip_legacy_tiers: If True, skip legacy tier builder calls (use when Elite Top 10 will run after)
        """
        # Check both the parameter AND the global class flag
        should_skip = skip_legacy_tiers or CachedBoardBuilderService.SKIP_LEGACY_TIER_BUILDER
        
        if not should_skip:
            await self.tier_builder_service.build_war_zone(players_dict, sync_time)
            await self.tier_builder_service.build_goblin_vault(players_dict, sync_time)
            await self.tier_builder_service.build_front_lines(players_dict, sync_time)
        else:
            logger.info("[CACHED_BOARD] SKIPPING legacy tier builder - Elite Top 10 mode active")
        
        await self.parlay_builder_service.build_parlay_builder(players_dict, sync_time)
        await self.parlay_builder_service.build_goblin_recon(players_dict, sync_time)
        
        # NOTE: Vision Intel enrichment is now triggered AFTER the entire sync completes
        # in adaptive_sync_engine.py to ensure data isn't wiped by subsequent sync operations
