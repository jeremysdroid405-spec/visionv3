"""
MLB PropVision Ferrari Pipeline
=================================
Complete pipeline integrating:
- Phase 1: Quantitative Sorting Gates (mlb_tier_sorter.py)
- Phase 2: Vision Intel Scout Badges (mlb_vision_scout.py)
- Phase 3: Gemini Oracle Summarizer (mlb_oracle_summarizer.py)
- Phase 4: Final Output to Ferrari collections

Execute the full pipeline to sort MLB props into Ferrari tiers with
AI-powered analysis and summaries.
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorDatabase

from .mlb_tier_sorter import get_tier_sorter, MLBTierSorter
from .mlb_vision_scout import get_vision_scout, MLBVisionScout
from .mlb_oracle_summarizer import get_oracle_summarizer, MLBOracleSummarizer

logger = logging.getLogger(__name__)


class MLBFerrariPipeline:
    """
    MLB PropVision Ferrari Pipeline.
    
    Executes the full prop evaluation pipeline:
    1. Quantitative sorting into tiers
    2. Scout badge evaluation
    3. Gemini Oracle summaries
    4. Save to Ferrari collections
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.tier_sorter = get_tier_sorter(db)
        self.vision_scout = get_vision_scout(db)
        self.oracle = get_oracle_summarizer(db)
    
    async def execute(self, save_to_db: bool = True) -> Dict[str, Any]:
        """
        Execute the full MLB PropVision Ferrari Pipeline.
        
        Args:
            save_to_db: Whether to save results to collections
            
        Returns:
            Dict with all tier results and statistics
        """
        start_time = datetime.now(timezone.utc)
        
        logger.info("=" * 60)
        logger.info("[FERRARI PIPELINE] Starting MLB PropVision Ferrari Pipeline")
        logger.info("=" * 60)
        
        # =====================================================================
        # PHASE 1: Quantitative Sorting Gates
        # =====================================================================
        logger.info("\n[PHASE 1] Running Quantitative Sorting Gates...")
        
        tier_results = await self.tier_sorter.sort_props(save_to_db=False)
        
        safe_haven = tier_results.get("safe_haven", [])
        front_lines = tier_results.get("front_lines", [])
        war_zone = tier_results.get("war_zone", [])
        
        logger.info(f"  ✓ Safe Haven: {len(safe_haven)} props qualified")
        logger.info(f"  ✓ Front Lines: {len(front_lines)} props qualified")
        logger.info(f"  ✓ War Zone: {len(war_zone)} props qualified")
        
        # =====================================================================
        # PHASE 2: Vision Intel Scout Badges + Weather
        # =====================================================================
        logger.info("\n[PHASE 2] Evaluating Vision Intel Scout Badges...")
        
        # Fetch weather for all unique teams
        from services.mlb_weather_service import get_weather_service
        weather_service = get_weather_service()
        
        # Get unique teams from props
        all_props = safe_haven + front_lines + war_zone
        unique_teams = set()
        for prop in all_props:
            team = prop.get("team") or prop.get("opp_team") or ""
            if team:
                unique_teams.add(team.upper())
        
        # Fetch weather for all teams
        weather_map = {}
        if unique_teams:
            logger.info(f"  Fetching weather for {len(unique_teams)} stadiums...")
            weather_map = await weather_service.get_weather_for_games(list(unique_teams))
            wind_favorable = sum(1 for w in weather_map.values() if w and w.get("is_favorable"))
            logger.info(f"  ✓ Weather fetched: {wind_favorable} stadiums with favorable wind")
        
        async def add_badges(props: List[Dict]) -> List[Dict]:
            """Add scout badges to props including weather-based badges."""
            for prop in props:
                player_name = prop.get("player_name", "")
                stat_type = prop.get("stat_type", "").lower()
                is_pitcher = "pitcher" in stat_type or "earned" in stat_type or "out" in stat_type
                
                # Get weather for this prop's team
                team = prop.get("team") or prop.get("opp_team") or ""
                weather = weather_map.get(team.upper()) if team else None
                
                badges = await self.vision_scout.evaluate_all_badges(
                    player_name=player_name,
                    prop=prop,
                    is_pitcher=is_pitcher,
                    weather=weather
                )
                
                prop["scout_badges"] = badges
                
                # Add weather info to prop for Oracle context
                if weather:
                    prop["weather"] = {
                        "description": weather.get("description"),
                        "wind_speed": weather.get("wind_speed"),
                        "wind_effect": weather.get("wind_effect"),
                        "is_favorable": weather.get("is_favorable")
                    }
            
            return props
        
        safe_haven = await add_badges(safe_haven)
        front_lines = await add_badges(front_lines)
        war_zone = await add_badges(war_zone)
        
        # Count badges
        total_badges = sum(
            len(p.get("scout_badges", [])) 
            for p in safe_haven + front_lines + war_zone
        )
        logger.info(f"  ✓ {total_badges} scout badges assigned")
        
        # =====================================================================
        # PHASE 3: Gemini Oracle Summarizer
        # =====================================================================
        logger.info("\n[PHASE 3] Generating Gemini Oracle Summaries...")
        
        safe_haven = await self.oracle.generate_batch_summaries(safe_haven, "safe_haven")
        front_lines = await self.oracle.generate_batch_summaries(front_lines, "front_lines")
        war_zone = await self.oracle.generate_batch_summaries(war_zone, "war_zone")
        
        logger.info(f"  ✓ {len(safe_haven) + len(front_lines) + len(war_zone)} Oracle summaries generated")
        
        # =====================================================================
        # PHASE 4: Save to Ferrari Collections
        # =====================================================================
        if save_to_db:
            logger.info("\n[PHASE 4] Saving to Ferrari Collections...")
            
            # Clean props for MongoDB and normalize to match NBA field structure
            def clean_prop(prop: Dict, tier: str) -> Dict:
                """Remove _id, clean for JSON, and normalize to NBA field names."""
                clean = {k: v for k, v in prop.items() if k != "_id"}
                clean["pipeline_timestamp"] = datetime.now(timezone.utc).isoformat()
                
                # === NORMALIZE TO NBA FIELD STRUCTURE ===
                # Map oracle_summary -> vision_intel (NBA uses vision_intel)
                if clean.get("oracle_summary") and not clean.get("vision_intel"):
                    clean["vision_intel"] = clean["oracle_summary"]
                    clean["vision_summary"] = clean["oracle_summary"]
                
                # Add intel metadata like NBA
                clean["intel_score"] = 10 if tier == "safe_haven" else (7 if tier == "front_lines" else 5)
                clean["intel_verdict"] = "CHALK" if tier == "safe_haven" else ("LEAN" if tier == "front_lines" else "GAMBLE")
                clean["intel_risk"] = "Low" if tier == "safe_haven" else ("Medium" if tier == "front_lines" else "High")
                clean["adjusted_confidence"] = 0.95 if tier == "safe_haven" else (0.75 if tier == "front_lines" else 0.55)
                
                # Map tier info
                clean["tier"] = tier
                clean["dk_tier"] = tier
                clean["is_goblin"] = tier in ["safe_haven", "front_lines"]
                clean["is_demon"] = tier == "war_zone"
                
                # Ensure hit rates are present at top level
                if clean.get("hit_rate_l10") and not clean.get("h10_rate"):
                    clean["h10_rate"] = clean["hit_rate_l10"] * 100 if clean["hit_rate_l10"] <= 1 else clean["hit_rate_l10"]
                if clean.get("hit_rate_l5") and not clean.get("h5_rate"):
                    clean["h5_rate"] = clean["hit_rate_l5"] * 100 if clean["hit_rate_l5"] <= 1 else clean["hit_rate_l5"]
                
                # Map averages - use l20_avg as season_avg, derive l5/l10 from it
                if clean.get("l20_avg") and not clean.get("season_avg"):
                    clean["season_avg"] = clean["l20_avg"]
                if clean.get("l20_avg") and not clean.get("l10_avg"):
                    clean["l10_avg"] = clean["l20_avg"]
                if clean.get("l20_avg") and not clean.get("l5_avg"):
                    clean["l5_avg"] = clean["l20_avg"]
                
                # RECALCULATE EDGE: Edge = Hit Rate - True Probability (percentage points)
                # This fixes the incorrect edge values (e.g., 240 from DK odds)
                hit_rate = clean.get("h20_rate") or clean.get("h10_rate") or clean.get("hit_rate_l10")
                tp_odds = clean.get("tp_odds")
                if hit_rate is not None and tp_odds is not None:
                    correct_edge = round(hit_rate - tp_odds, 1)
                    clean["edge"] = correct_edge
                    clean["edge_pct"] = correct_edge
                
                return clean
            
            # ========================================================
            # ATOMIC UPSERT for MLB Tiers (prevents race conditions)
            # ========================================================
            async def atomic_upsert_mlb_tier(collection_name, picks, tier_name):
                """Atomic upsert: Replace all docs without emptying collection."""
                coll = self.db[collection_name]
                
                if not picks:
                    await coll.delete_many({})
                    return 0
                
                from pymongo import UpdateOne
                
                # Clean and build operations
                clean_picks = [clean_prop(p, tier_name.lower().replace(" ", "_")) for p in picks]
                current_keys = set()
                operations = []
                
                for pick in clean_picks:
                    key = f"{pick.get('player_name')}|{pick.get('stat_type')}|{pick.get('line')}"
                    current_keys.add(key)
                    
                    operations.append(UpdateOne(
                        {
                            "player_name": pick.get("player_name"),
                            "stat_type": pick.get("stat_type"),
                            "line": pick.get("line")
                        },
                        {"$set": pick},
                        upsert=True
                    ))
                
                # Upsert first (collection always has data)
                if operations:
                    await coll.bulk_write(operations, ordered=False)
                
                # Clean stale picks
                all_docs = await coll.find({}, {"player_name": 1, "stat_type": 1, "line": 1}).to_list(length=100)
                stale_ids = []
                for doc in all_docs:
                    key = f"{doc.get('player_name')}|{doc.get('stat_type')}|{doc.get('line')}"
                    if key not in current_keys:
                        stale_ids.append(doc["_id"])
                
                if stale_ids:
                    await coll.delete_many({"_id": {"$in": stale_ids}})
                    logger.info(f"  [{tier_name}] Cleaned {len(stale_ids)} stale props")
                
                logger.info(f"  ✓ Saved {len(picks)} {tier_name} props (atomic upsert)")
                return len(picks)
            
            # Save all tiers using atomic upsert
            await atomic_upsert_mlb_tier("mlb_ferrari_safe_haven", safe_haven, "Safe Haven")
            await atomic_upsert_mlb_tier("mlb_ferrari_front_lines", front_lines, "Front Lines")
            await atomic_upsert_mlb_tier("mlb_ferrari_war_zone", war_zone, "War Zone")
        
        # =====================================================================
        # PIPELINE COMPLETE
        # =====================================================================
        end_time = datetime.now(timezone.utc)
        duration = (end_time - start_time).total_seconds()
        
        logger.info("\n" + "=" * 60)
        logger.info("[FERRARI PIPELINE] Pipeline Complete!")
        logger.info(f"  Duration: {duration:.1f}s")
        logger.info("=" * 60)
        
        return {
            "success": True,
            "pipeline_timestamp": end_time.isoformat(),
            "duration_seconds": duration,
            "stats": {
                "total_processed": tier_results.get("stats", {}).get("total_processed", 0),
                "safe_haven_count": len(safe_haven),
                "front_lines_count": len(front_lines),
                "war_zone_count": len(war_zone),
                "total_badges": total_badges,
            },
            "safe_haven": safe_haven,
            "front_lines": front_lines,
            "war_zone": war_zone,
        }
    
    async def get_top_hrr_safe_haven(self, limit: int = 3) -> List[Dict]:
        """
        Get top Safe Haven HRR (Hits+Runs+RBIs) props.
        
        Args:
            limit: Number of props to return
            
        Returns:
            List of top HRR Safe Haven props
        """
        safe_haven_coll = self.db["mlb_ferrari_safe_haven"]
        
        # Find HRR props sorted by board score
        hrr_props = await safe_haven_coll.find(
            {
                "$or": [
                    {"stat_type": {"$regex": "hits.*runs.*rbis", "$options": "i"}},
                    {"stat_type": {"$regex": "HRR", "$options": "i"}},
                    {"stat_type": {"$regex": "rbis", "$options": "i"}},
                ]
            },
            {"_id": 0}
        ).sort("board_score", -1).limit(limit).to_list(length=limit)
        
        return hrr_props


# Singleton
_pipeline: Optional[MLBFerrariPipeline] = None


def get_ferrari_pipeline(db: AsyncIOMotorDatabase) -> MLBFerrariPipeline:
    """Get or create Ferrari Pipeline instance."""
    global _pipeline
    if _pipeline is None:
        _pipeline = MLBFerrariPipeline(db)
    return _pipeline


async def run_mlb_ferrari_pipeline(
    db: AsyncIOMotorDatabase,
    save_to_db: bool = True
) -> Dict[str, Any]:
    """
    Run the full MLB PropVision Ferrari Pipeline.
    
    Args:
        db: MongoDB database
        save_to_db: Whether to save results to collections
        
    Returns:
        Pipeline results with all tiers
    """
    pipeline = get_ferrari_pipeline(db)
    return await pipeline.execute(save_to_db)


async def get_top_safe_haven_hrr(
    db: AsyncIOMotorDatabase,
    limit: int = 3
) -> List[Dict]:
    """Get top Safe Haven HRR props."""
    pipeline = get_ferrari_pipeline(db)
    return await pipeline.get_top_hrr_safe_haven(limit)
