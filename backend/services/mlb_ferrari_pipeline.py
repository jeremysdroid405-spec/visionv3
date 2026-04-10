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
        # PHASE 2: Vision Intel Scout Badges
        # =====================================================================
        logger.info("\n[PHASE 2] Evaluating Vision Intel Scout Badges...")
        
        async def add_badges(props: List[Dict]) -> List[Dict]:
            """Add scout badges to props."""
            for prop in props:
                player_name = prop.get("player_name", "")
                stat_type = prop.get("stat_type", "").lower()
                is_pitcher = "pitcher" in stat_type or "earned" in stat_type or "out" in stat_type
                
                badges = await self.vision_scout.evaluate_all_badges(
                    player_name=player_name,
                    prop=prop,
                    is_pitcher=is_pitcher
                )
                
                prop["scout_badges"] = badges
            
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
            
            # Clean props for MongoDB (remove any ObjectId issues)
            def clean_prop(prop: Dict) -> Dict:
                """Remove _id and clean for JSON serialization."""
                clean = {k: v for k, v in prop.items() if k != "_id"}
                clean["pipeline_timestamp"] = datetime.now(timezone.utc).isoformat()
                return clean
            
            # Save Safe Haven
            if safe_haven:
                safe_haven_coll = self.db["mlb_ferrari_safe_haven"]
                await safe_haven_coll.delete_many({})
                clean_safe_haven = [clean_prop(p) for p in safe_haven]
                await safe_haven_coll.insert_many(clean_safe_haven)
                logger.info(f"  ✓ Saved {len(safe_haven)} Safe Haven props")
            
            # Save Front Lines
            if front_lines:
                front_lines_coll = self.db["mlb_ferrari_front_lines"]
                await front_lines_coll.delete_many({})
                clean_front_lines = [clean_prop(p) for p in front_lines]
                await front_lines_coll.insert_many(clean_front_lines)
                logger.info(f"  ✓ Saved {len(front_lines)} Front Lines props")
            
            # Save War Zone
            if war_zone:
                war_zone_coll = self.db["mlb_ferrari_war_zone"]
                await war_zone_coll.delete_many({})
                clean_war_zone = [clean_prop(p) for p in war_zone]
                await war_zone_coll.insert_many(clean_war_zone)
                logger.info(f"  ✓ Saved {len(war_zone)} War Zone props")
        
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
