"""
Vision Intel Enrichment Service v2.0
=====================================
Board-Driven Pre-Caching for Vision Intel Suite & AI Summaries

This service generates Intel ONLY for players currently on the 3 Tiered Boards:
- War Zone (dg_radar_picks) - Top demons
- Safe Haven (dg_goblin_vault) - Top goblins  
- Front Lines (dg_front_lines) - Mixed tier

Logic Flow:
1. BDL/Odds Sync completes
2. Tiered Board Build identifies ~30 unique players
3. Vision Intel Sync runs (capped at 60 props)
4. Results cached in dg_cached_board
5. Frontend serves from cache (no JIT calls)

Key Features:
- Board-Driven Scope: Only enriches players on current boards
- 60-Count Cap: Maximum 60 props per sync cycle
- Auto-Cleanup: Marks stale intel when players rotate off boards
- Semaphore(5): Rate-limits Gemini API calls
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Set
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# Configuration
GEMINI_SEMAPHORE_LIMIT = 5  # Max concurrent Gemini API calls
MAX_PROPS_PER_CYCLE = 60    # Hard cap on props per sync cycle
STALE_THRESHOLD_HOURS = 2   # Mark intel as stale after 2 hours


class VisionIntelEnrichmentService:
    """
    Board-Driven Vision Intel Enrichment.
    
    Only generates Intel for players currently on the 3 Tiered Boards.
    Caps at 60 props per cycle. Marks stale intel for rotated players.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.cached_board = db.dg_cached_board
        
        # Tier board collections
        self.war_zone = db.dg_radar_picks      # War Zone demons
        self.safe_haven = db.dg_goblin_vault   # Safe Haven goblins
        self.front_lines = db.dg_front_lines   # Mixed tier
        
        # Semaphore for rate-limiting Gemini calls
        self._gemini_semaphore = asyncio.Semaphore(GEMINI_SEMAPHORE_LIMIT)
        
        # Track last run stats
        self._last_run_stats: Dict[str, Any] = {}
    
    async def run_board_driven_enrichment(self) -> Dict[str, Any]:
        """
        Main entry point - Board-Driven Vision Intel Enrichment.
        
        1. Fetches unique players from all 3 tier boards
        2. Enriches their props (capped at 60)
        3. Marks stale intel for players no longer on boards
        
        Called immediately after Board Build completes.
        """
        start_time = datetime.now(timezone.utc)
        logger.info("=" * 70)
        logger.info("[VISION_INTEL v2.0] BOARD-DRIVEN ENRICHMENT STARTING")
        logger.info("=" * 70)
        
        stats = {
            "started_at": start_time.isoformat(),
            "board_players_found": 0,
            "unique_players": 0,
            "props_to_enrich": 0,
            "props_enriched": 0,
            "ai_summaries_generated": 0,
            "stale_marked": 0,
            "errors": [],
            "skipped": 0
        }
        
        try:
            # Step 1: Get unique players from all tier boards
            board_players = await self._get_board_players()
            stats["board_players_found"] = len(board_players)
            stats["unique_players"] = len(set(p["player_name"] for p in board_players))
            
            logger.info(f"[VISION_INTEL] Found {stats['unique_players']} unique players across boards")
            
            if not board_players:
                logger.info("[VISION_INTEL] No players on boards - skipping enrichment")
                return stats
            
            # Step 2: Get props to enrich (capped at 60)
            props_to_enrich = await self._get_props_for_board_players(board_players)
            stats["props_to_enrich"] = len(props_to_enrich)
            
            logger.info(f"[VISION_INTEL] {len(props_to_enrich)} props to enrich (cap: {MAX_PROPS_PER_CYCLE})")
            
            # Step 3: Enrich props with AI summaries (rate-limited)
            enrichment_tasks = []
            for prop_info in props_to_enrich[:MAX_PROPS_PER_CYCLE]:
                task = self._enrich_single_prop(prop_info)
                enrichment_tasks.append(task)
            
            results = await asyncio.gather(*enrichment_tasks, return_exceptions=True)
            
            # Process results
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    stats["errors"].append(str(result)[:100])
                elif result.get("success"):
                    stats["props_enriched"] += 1
                    if result.get("ai_summary_generated"):
                        stats["ai_summaries_generated"] += 1
                else:
                    stats["skipped"] += 1
            
            # Step 4: Mark stale intel for players no longer on boards
            stale_count = await self._mark_stale_intel(board_players)
            stats["stale_marked"] = stale_count
            
            end_time = datetime.now(timezone.utc)
            duration = (end_time - start_time).total_seconds()
            stats["duration_seconds"] = round(duration, 2)
            stats["completed_at"] = end_time.isoformat()
            
            self._last_run_stats = stats
            
            logger.info("=" * 70)
            logger.info("[VISION_INTEL v2.0] ENRICHMENT COMPLETE")
            logger.info(f"[VISION_INTEL] Players: {stats['unique_players']} | Props: {stats['props_enriched']}/{stats['props_to_enrich']}")
            logger.info(f"[VISION_INTEL] AI Summaries: {stats['ai_summaries_generated']} | Stale: {stats['stale_marked']}")
            logger.info(f"[VISION_INTEL] Duration: {duration:.1f}s")
            logger.info("=" * 70)
            
            return stats
            
        except Exception as e:
            logger.error(f"[VISION_INTEL] Enrichment failed: {e}")
            stats["error"] = str(e)
            return stats
    
    async def _get_board_players(self) -> List[Dict[str, Any]]:
        """
        Fetch players currently on tier boards by querying dg_cached_board.
        
        Board eligibility is determined by:
        - War Zone: props with is_demon=True (sorted by 4-pillar score)
        - Safe Haven: props with is_goblin=True (sorted by 4-pillar score)
        - Front Lines: top mixed picks
        
        Returns list of player info dicts with player_name, stat_type, line, tier_source.
        """
        board_players = []
        seen_keys = set()  # Dedupe by player+stat+line
        
        try:
            # Query dg_cached_board for players with demon/goblin props
            cursor = self.cached_board.find(
                {},
                {"_id": 0, "player_name": 1, "team": 1, "props": 1}
            )
            
            demons = []
            goblins = []
            
            async for player_doc in cursor:
                player_name = player_doc.get("player_name", "")
                team = player_doc.get("team", "")
                
                for prop in player_doc.get("props", []):
                    stat_type = prop.get("stat_type_extracted") or prop.get("stat_type", "")
                    line = prop.get("line", 0)
                    is_demon = prop.get("is_demon", False)
                    is_goblin = prop.get("is_goblin", False)
                    
                    if not (is_demon or is_goblin):
                        continue
                    
                    # Get score for sorting
                    h10_rate = prop.get("h10_rate") or prop.get("h10_hit_rate", 0) or 0
                    
                    pick_info = {
                        "player_name": player_name,
                        "team": team,
                        "stat_type": stat_type,
                        "line": line,
                        "is_demon": is_demon,
                        "is_goblin": is_goblin,
                        "h10_rate": h10_rate,
                        "l5_avg": prop.get("l5_avg"),
                        "season_avg": prop.get("season_avg"),
                        "opponent": prop.get("opponent"),
                        "dvp_rank": prop.get("dvp_rank"),
                        "tier_source": "war_zone" if is_demon else "safe_haven"
                    }
                    
                    if is_demon:
                        demons.append(pick_info)
                    if is_goblin:
                        goblins.append(pick_info)
            
            # Sort by h10_rate (hit rate) descending and take top 20 from each
            demons.sort(key=lambda x: x.get("h10_rate", 0) or 0, reverse=True)
            goblins.sort(key=lambda x: x.get("h10_rate", 0) or 0, reverse=True)
            
            # Add top 20 demons (War Zone)
            for pick in demons[:20]:
                key = f"{pick['player_name']}|{pick['stat_type']}|{pick['line']}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    board_players.append(pick)
            
            # Add top 20 goblins (Safe Haven)
            for pick in goblins[:20]:
                key = f"{pick['player_name']}|{pick['stat_type']}|{pick['line']}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    board_players.append(pick)
            
            # Add top 20 mixed for Front Lines (highest h10 regardless of demon/goblin)
            all_picks = demons + goblins
            all_picks.sort(key=lambda x: x.get("h10_rate", 0) or 0, reverse=True)
            for pick in all_picks[:20]:
                key = f"{pick['player_name']}|{pick['stat_type']}|{pick['line']}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    pick["tier_source"] = "front_lines"
                    board_players.append(pick)
            
            logger.info(f"[VISION_INTEL] Found {len(board_players)} board-eligible picks ({len(demons)} demons, {len(goblins)} goblins)")
            return board_players
            
        except Exception as e:
            logger.error(f"[VISION_INTEL] Error getting board players: {e}")
            return []
    
    async def _get_props_for_board_players(self, board_players: List[Dict]) -> List[Dict[str, Any]]:
        """
        Get props from cached_board for the board players.
        
        Returns list of prop info ready for enrichment.
        """
        props_to_enrich = []
        player_names = list(set(p["player_name"] for p in board_players))
        
        # Create lookup of board picks by player+stat+line
        board_lookup = {}
        for bp in board_players:
            key = f"{bp['player_name']}|{bp['stat_type']}|{bp['line']}"
            board_lookup[key] = bp
        
        # Query cached_board for these players
        cursor = self.cached_board.find(
            {"player_name": {"$in": player_names}},
            {"_id": 0}
        )
        
        async for player_doc in cursor:
            player_name = player_doc.get("player_name", "")
            team = player_doc.get("team", "")
            
            for prop_index, prop in enumerate(player_doc.get("props", [])):
                stat_type = prop.get("stat_type_extracted") or prop.get("stat_type", "")
                line = prop.get("line", 0)
                
                # Check if this prop is on a board
                key = f"{player_name}|{stat_type}|{line}"
                board_info = board_lookup.get(key)
                
                if not board_info:
                    # This prop isn't on any board - skip
                    continue
                
                # Check if already enriched recently
                if prop.get("is_vision_enriched"):
                    enriched_at = prop.get("vision_enriched_at")
                    if enriched_at:
                        try:
                            enriched_time = datetime.fromisoformat(enriched_at.replace('Z', '+00:00'))
                            age_hours = (datetime.now(timezone.utc) - enriched_time).total_seconds() / 3600
                            if age_hours < 1.0:
                                # Already enriched within the hour - skip
                                continue
                        except (ValueError, TypeError):
                            pass
                
                props_to_enrich.append({
                    "player_name": player_name,
                    "team": team,
                    "stat_type": stat_type,
                    "line": line,
                    "prop_index": prop_index,
                    "tier_source": board_info.get("tier_source"),
                    "is_demon": board_info.get("is_demon", False),
                    "is_goblin": board_info.get("is_goblin", False),
                    "h10_rate": prop.get("h10_rate") or board_info.get("h10_rate", 0),
                    "l5_avg": prop.get("l5_avg") or board_info.get("l5_avg"),
                    "season_avg": prop.get("season_avg") or board_info.get("season_avg"),
                    "opponent": board_info.get("opponent"),
                    "dvp_rank": board_info.get("dvp_rank"),
                })
        
        return props_to_enrich
    
    async def _enrich_single_prop(self, prop_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enrich a single prop with Vision Intel (AI summary + intel_suite).
        
        Uses semaphore to rate-limit Gemini API calls.
        """
        player_name = prop_info.get("player_name", "Unknown")
        stat_type = prop_info.get("stat_type", "PTS")
        line = prop_info.get("line", 0)
        prop_index = prop_info.get("prop_index", -1)
        
        result = {
            "success": False,
            "player_name": player_name,
            "stat_type": stat_type,
            "line": line,
            "ai_summary_generated": False
        }
        
        if prop_index < 0:
            return result
        
        try:
            # Rate-limited Gemini call
            async with self._gemini_semaphore:
                ai_summary = await self._generate_ai_summary(prop_info)
            
            if ai_summary:
                result["ai_summary_generated"] = True
            
            # Build intel_suite
            intel_suite = self._build_intel_suite(prop_info, ai_summary)
            
            # Update MongoDB
            now = datetime.now(timezone.utc)
            update_data = {
                f"props.{prop_index}.vision_summary": ai_summary,
                f"props.{prop_index}.vision_enriched_at": now.isoformat(),
                f"props.{prop_index}.intel_suite": intel_suite,
                f"props.{prop_index}.is_vision_enriched": True,
                f"props.{prop_index}.is_stale": False,
                f"props.{prop_index}.tier_source": prop_info.get("tier_source"),
            }
            
            await self.cached_board.update_one(
                {"player_name": player_name},
                {"$set": update_data}
            )
            
            result["success"] = True
            logger.debug(f"[VISION_INTEL] Enriched: {player_name} {stat_type}@{line}")
            
            return result
            
        except Exception as e:
            logger.error(f"[VISION_INTEL] Error enriching {player_name} {stat_type}@{line}: {e}")
            result["error"] = str(e)
            return result
    
    async def _generate_ai_summary(self, prop_info: Dict[str, Any]) -> Optional[str]:
        """
        Generate AI Vision Summary using Gemini.
        """
        try:
            from services.vision_summary_service import VisionSummaryService
            
            vision_service = VisionSummaryService()
            
            h10_rate = prop_info.get("h10_rate", 0) or 0
            season_avg = prop_info.get("season_avg") or prop_info.get("l5_avg") or 0
            
            summary = await vision_service.generate_pick_summary(
                player_name=prop_info.get("player_name", ""),
                stat_type=prop_info.get("stat_type", "PTS"),
                line=prop_info.get("line", 0),
                season_avg=season_avg,
                h10_rate=h10_rate,
                badges=[],
                opponent=prop_info.get("opponent", ""),
                is_demon=prop_info.get("is_demon", False),
                is_goblin=prop_info.get("is_goblin", False),
                dvp_rank=prop_info.get("dvp_rank"),
                dvp_friction=None,
                player_team=prop_info.get("team", "")
            )
            
            return summary
            
        except Exception as e:
            logger.warning(f"[VISION_INTEL] Gemini error: {e}")
            return None
    
    def _build_intel_suite(self, prop_info: Dict[str, Any], ai_summary: Optional[str]) -> Dict[str, Any]:
        """
        Build pre-computed intel_suite object.
        """
        stat_type = prop_info.get("stat_type", "PTS")
        line = prop_info.get("line", 0) or 0
        h10_rate = prop_info.get("h10_rate", 0) or 0
        l5_avg = prop_info.get("l5_avg", 0) or 0
        season_avg = prop_info.get("season_avg", 0) or 0
        dvp_rank = prop_info.get("dvp_rank") or 15
        opponent = prop_info.get("opponent", "")
        is_demon = prop_info.get("is_demon", False)
        
        # Ensure numeric types
        try:
            dvp_rank = int(dvp_rank)
        except (ValueError, TypeError):
            dvp_rank = 15
        
        try:
            h10_rate = float(h10_rate)
        except (ValueError, TypeError):
            h10_rate = 0
        
        # Friction level
        if dvp_rank >= 25:
            friction_level = "Low"
            friction_label = "Soft Defense"
        elif dvp_rank >= 15:
            friction_level = "Medium"
            friction_label = "Average Defense"
        elif dvp_rank >= 6:
            friction_level = "High"
            friction_label = "Above Average"
        else:
            friction_level = "Elite"
            friction_label = "Elite Defense"
        
        # Stability
        try:
            stability_score = int(h10_rate) if h10_rate else 50
        except (ValueError, TypeError):
            stability_score = 50
        
        if stability_score >= 70:
            consistency = "HIGHLY CONSISTENT"
        elif stability_score >= 50:
            consistency = "MODERATE VARIANCE"
        else:
            consistency = "HIGH VARIANCE"
        
        # Reasons
        reasons = []
        if l5_avg and line and l5_avg >= line:
            reasons.append(f"L5 avg ({l5_avg}) exceeds line ({line})")
        if h10_rate and h10_rate >= 60:
            hits = int(h10_rate / 10)
            reasons.append(f"Hit in {hits}/10 recent games")
        if season_avg and line and line < season_avg:
            reasons.append(f"Line below season avg ({season_avg})")
        if dvp_rank >= 25:
            reasons.append(f"Favorable matchup: #{dvp_rank} vs {stat_type}")
        
        primary_insight = reasons[0] if reasons else f"Analyzing {stat_type} @ {line}"
        
        return {
            "context_badges": [],
            "usage_ripple": {
                "display": "Elevated Usage" if is_demon else "Standard Volume",
                "bump_percent": 3 if is_demon else 1,
            },
            "matchup_dvp": {
                "display": f"vs {opponent}" if opponent else "Matchup TBD",
                "opponent": opponent,
                "friction_level": friction_level,
                "friction_label": friction_label,
                "dvp_rank": dvp_rank,
                "stat_type": stat_type
            },
            "pace_delta": {
                "display": "+0 POSS",
                "possessions": 0,
                "tempo_label": "Standard tempo"
            },
            "stability_index": {
                "display": f"{stability_score}%",
                "score": stability_score,
                "consistency": consistency
            },
            "vision_insight": {
                "primary": primary_insight,
                "reasons": reasons if reasons else [primary_insight],
                "confidence": "HIGH" if len(reasons) >= 3 else "MEDIUM" if len(reasons) >= 2 else "STANDARD",
                "ai_summary": ai_summary
            },
            "pre_cached": True,
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "tier_source": prop_info.get("tier_source")
        }
    
    async def _mark_stale_intel(self, current_board_players: List[Dict]) -> int:
        """
        Mark intel as stale for players no longer on boards.
        
        Returns count of stale-marked props.
        """
        # Get current board player+stat+line keys
        current_keys = set()
        for bp in current_board_players:
            key = f"{bp['player_name']}|{bp['stat_type']}|{bp['line']}"
            current_keys.add(key)
        
        stale_count = 0
        
        # Find enriched props not on current boards
        cursor = self.cached_board.find(
            {"props.is_vision_enriched": True},
            {"_id": 0, "player_name": 1, "props": 1}
        )
        
        async for player_doc in cursor:
            player_name = player_doc.get("player_name", "")
            
            for i, prop in enumerate(player_doc.get("props", [])):
                if not prop.get("is_vision_enriched"):
                    continue
                
                stat_type = prop.get("stat_type_extracted") or prop.get("stat_type", "")
                line = prop.get("line", 0)
                key = f"{player_name}|{stat_type}|{line}"
                
                # If this enriched prop is no longer on boards, mark stale
                if key not in current_keys and not prop.get("is_stale"):
                    await self.cached_board.update_one(
                        {"player_name": player_name},
                        {"$set": {f"props.{i}.is_stale": True}}
                    )
                    stale_count += 1
        
        if stale_count > 0:
            logger.info(f"[VISION_INTEL] Marked {stale_count} props as stale (rotated off boards)")
        
        return stale_count
    
    def get_last_run_stats(self) -> Dict[str, Any]:
        """Get stats from the last enrichment run."""
        return self._last_run_stats


# Singleton instance
_vision_intel_service: Optional[VisionIntelEnrichmentService] = None


def get_vision_intel_enrichment_service(db: AsyncIOMotorDatabase) -> VisionIntelEnrichmentService:
    """Get or create the Vision Intel Enrichment Service singleton."""
    global _vision_intel_service
    if _vision_intel_service is None:
        _vision_intel_service = VisionIntelEnrichmentService(db)
    return _vision_intel_service


async def run_vision_intel_enrichment(db: AsyncIOMotorDatabase) -> Dict[str, Any]:
    """
    Convenience function to run Board-Driven Vision Intel enrichment.
    
    This should be called immediately after Board Build completes.
    """
    service = VisionIntelEnrichmentService(db)
    return await service.run_board_driven_enrichment()
