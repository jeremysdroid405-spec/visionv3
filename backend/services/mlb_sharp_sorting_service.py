"""
MLB Sharp Sorting & Tier Distribution Service
==============================================
Advanced prop sorting using sharp book analysis.

Layers:
1. Pinnacle De-Vig: Calculate fair value probability from sharp odds
2. DraftKings Market Depth: Compare DK alt-lines to PrizePicks
3. Ferrari Final Sort: Classify into Goblins, Demons, Standard

Collections:
- mlb_goblins: Sharp odds ≤ -240 AND VK Projection > Line
- mlb_demons: VK Slope massive over + DK alt-line mispricing
- mlb_standard: Sharp and public agree (-110 to -130)
"""

import os
import logging
import math
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.db_config import get_collection_name

logger = logging.getLogger(__name__)


class MLBSharpSortingService:
    """
    MLB Sharp Sorting & Tier Distribution.
    
    Uses Pinnacle (sharp) odds to identify value and classify props.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
    
    # =========================================================================
    # PINNACLE DE-VIG CALCULATIONS
    # =========================================================================
    
    def american_to_decimal(self, american_odds: int) -> float:
        """Convert American odds to decimal odds."""
        if american_odds is None:
            return 2.0  # Default -110 equivalent
        
        if american_odds > 0:
            return (american_odds / 100) + 1
        else:
            return (100 / abs(american_odds)) + 1
    
    def decimal_to_implied_prob(self, decimal_odds: float) -> float:
        """Convert decimal odds to implied probability."""
        if decimal_odds <= 0:
            return 0.5
        return 1 / decimal_odds
    
    def remove_vig(self, over_odds: int, under_odds: int) -> Tuple[float, float]:
        """
        Remove the vig from a two-way market to get fair probabilities.
        
        Uses the additive method: Fair Prob = Implied Prob / Sum of Implied Probs
        
        Args:
            over_odds: American odds for OVER
            under_odds: American odds for UNDER
            
        Returns:
            Tuple of (over_fair_prob, under_fair_prob)
        """
        over_decimal = self.american_to_decimal(over_odds)
        under_decimal = self.american_to_decimal(under_odds)
        
        over_implied = self.decimal_to_implied_prob(over_decimal)
        under_implied = self.decimal_to_implied_prob(under_decimal)
        
        total_implied = over_implied + under_implied
        
        if total_implied == 0:
            return 0.5, 0.5
        
        over_fair = over_implied / total_implied
        under_fair = under_implied / total_implied
        
        return round(over_fair, 4), round(under_fair, 4)
    
    def calculate_fair_value(self, odds: int) -> float:
        """
        Calculate fair value probability from single-side odds.
        
        Assumes standard -110/-110 vig (~4.5% total).
        Removes estimated vig to get fair probability.
        """
        decimal_odds = self.american_to_decimal(odds)
        implied_prob = self.decimal_to_implied_prob(decimal_odds)
        
        # Estimate vig removal (assuming ~4.5% total vig on two-way)
        # Fair prob ≈ implied_prob / 1.045
        fair_prob = implied_prob / 1.045
        
        return round(min(fair_prob, 1.0), 4)
    
    def is_sharp_goblin(self, sharp_odds: int, direction: str) -> bool:
        """
        Check if prop qualifies as Sharp Goblin.
        
        Criteria: Sharp odds ≤ -180 (implies >64% fair probability after de-vig)
        
        Note: -240 is too strict for typical Pinnacle data.
        Using -180 which implies ~62% fair value after vig removal.
        """
        if sharp_odds is None:
            return False
        
        # -180 American = ~64.3% implied
        # After de-vig, this is ~61.5% fair
        return sharp_odds <= -180
    
    # =========================================================================
    # DRAFTKINGS MARKET DEPTH ANALYSIS
    # =========================================================================
    
    def analyze_dk_vs_pp(
        self,
        dk_line: float,
        dk_odds: int,
        pp_line: float,
        pp_odds: int = -110
    ) -> Dict[str, Any]:
        """
        Compare DraftKings line to PrizePicks.
        
        Identifies mispricing where DK alt-line suggests PP is mispriced.
        
        Args:
            dk_line: DraftKings line
            dk_odds: DraftKings American odds
            pp_line: PrizePicks line
            pp_odds: PrizePicks implied odds (usually -110 equivalent)
            
        Returns:
            Analysis dict with mispricing detection
        """
        if dk_line is None or pp_line is None:
            return {"is_demon": False, "mispricing": None}
        
        line_diff = dk_line - pp_line
        
        # Convert to implied probabilities
        dk_implied = self.decimal_to_implied_prob(self.american_to_decimal(dk_odds or -110))
        pp_implied = self.decimal_to_implied_prob(self.american_to_decimal(pp_odds))
        
        # Calculate mispricing
        # If DK has +180 (35.7% implied) but PP is -110 (47.6% implied)
        # That's a 12% edge on PP
        mispricing = pp_implied - dk_implied
        
        # Demon criteria: PP is significantly overvalued compared to DK
        # DK at +180 (~36%) vs PP equivalent at +400 (~20%)
        # This means PP thinks it's MORE likely than DK
        is_demon = mispricing > 0.10 and dk_odds >= 150  # DK is plus money but PP is favored
        
        return {
            "is_demon": is_demon,
            "mispricing": round(mispricing * 100, 2),  # Percentage
            "dk_implied": round(dk_implied * 100, 2),
            "pp_implied": round(pp_implied * 100, 2),
            "line_diff": line_diff
        }
    
    # =========================================================================
    # TIER CLASSIFICATION
    # =========================================================================
    
    def classify_prop(
        self,
        prop: Dict,
        vk_projection: Dict = None
    ) -> str:
        """
        Classify a prop into Goblin, Demon, or Standard tier.
        
        UPDATED CRITERIA (based on real Pinnacle data ranges):
        - GOBLIN: Sharp odds ≤ -150 AND VK Projection aligns with direction
                  OR Sharp Fair Value > 58% AND VK confirms
        - DEMON: DK/PP line discrepancy > 0.5 AND high edge
        - STANDARD: Sharp and public agree in -130 to +110 range
        
        Args:
            prop: Prop data with all_odds, sharp_line, etc.
            vk_projection: VK regression projection data
            
        Returns:
            Tier name: "GOBLIN", "DEMON", or "STANDARD"
        """
        sharp_odds = None
        all_odds = prop.get("all_odds", {})
        
        # Get Pinnacle (sharp) odds
        if "pinnacle" in all_odds:
            sharp_odds = all_odds.get("pinnacle")
        
        direction = prop.get("recommendation", "OVER")
        line = prop.get("line", 0)
        projected_value = vk_projection.get("projected_value") if vk_projection else prop.get("projected_value")
        edge_pct = vk_projection.get("edge_pct") if vk_projection else prop.get("edge_pct")
        hit_rate = vk_projection.get("hit_rate_l10") if vk_projection else prop.get("hit_rate_l10")
        
        # =================================================================
        # GOBLIN CHECK: Sharp money + VK alignment
        # =================================================================
        # Realistic threshold: -150 or better (covers -214, -189, -166, etc.)
        if sharp_odds is not None and sharp_odds <= -150:
            vk_confirms = False
            if direction == "OVER" and projected_value and projected_value > line:
                vk_confirms = True
            elif direction == "UNDER" and projected_value and projected_value < line:
                vk_confirms = True
            
            if vk_confirms:
                return "GOBLIN"
        
        # Alternative Goblin: Sharp fair value > 58% with strong edge
        if sharp_odds is not None:
            fair_value = self.calculate_fair_value(sharp_odds)
            if fair_value > 0.58 and edge_pct and abs(edge_pct) > 15:
                return "GOBLIN"
        
        # =================================================================
        # DEMON CHECK: DK/PP line discrepancy with edge support
        # =================================================================
        dk_line = prop.get("dk_line")
        pp_line = line
        
        if dk_line is not None and pp_line is not None:
            line_diff = abs(dk_line - pp_line)
            # Significant line discrepancy (0.5+ is meaningful in baseball)
            if line_diff >= 0.5:
                # Check if we have edge support
                if edge_pct and abs(edge_pct) > 20:
                    return "DEMON"
                # Check if hit rate is high despite variance
                if hit_rate and hit_rate > 0.6:
                    return "DEMON"
        
        # =================================================================
        # STANDARD CHECK: Books agree on the line
        # =================================================================
        if sharp_odds is not None:
            # Standard: Sharp odds in the -130 to +110 range (neutral pricing)
            if -130 <= sharp_odds <= 110:
                return "STANDARD"
        
        # Fallback: Check DK odds for standard classification
        dk_odds = all_odds.get("draftkings")
        if dk_odds is not None:
            if -130 <= dk_odds <= 110:
                return "STANDARD"
        
        return "UNCLASSIFIED"
    
    # =========================================================================
    # MAIN SORTING PROCESS
    # =========================================================================
    
    async def run_sharp_sorting(
        self,
        stat_types: List[str] = None,
        save_to_db: bool = True
    ) -> Dict[str, Any]:
        """
        Run Sharp Sorting on all MLB props.
        
        Process:
        1. Fetch all props from mlb_live_props
        2. For each prop, calculate Pinnacle fair value
        3. Analyze DK vs PP market depth
        4. Classify into Goblins, Demons, Standard
        5. Save to respective collections
        
        Args:
            stat_types: Filter to specific stat types (e.g., ["Hits+Runs+RBIs", "Total Bases"])
            save_to_db: Whether to save results to collections
            
        Returns:
            Sorting results summary
        """
        logger.info("=" * 70)
        logger.info("[SHARP_SORT] Starting MLB Sharp Sorting & Tier Distribution")
        logger.info("=" * 70)
        
        start_time = datetime.now(timezone.utc)
        
        results = {
            "success": True,
            "started_at": start_time.isoformat(),
            "props_processed": 0,
            "goblins": [],
            "demons": [],
            "standard": [],
            "unclassified": 0,
            "stats": {
                "total_with_sharp_odds": 0,
                "total_with_dk_line": 0,
                "sharp_fair_value_avg": 0,
            },
            "errors": []
        }
        
        try:
            # Fetch props
            live_props = self.db[get_collection_name("live_props", "mlb")]
            
            query = {}
            if stat_types:
                query["stat_type"] = {"$in": stat_types}
            
            props = await live_props.find(query, {"_id": 0}).to_list(length=None)
            results["props_processed"] = len(props)
            
            logger.info(f"[SHARP_SORT] Processing {len(props)} props")
            
            if not props:
                logger.warning("[SHARP_SORT] No props found")
                return results
            
            # Get VK projections from war_zone (where most props end up)
            war_zone = self.db[get_collection_name("war_zone", "mlb")]
            vk_props = await war_zone.find({}, {"_id": 0}).to_list(length=None)
            
            # Build lookup by player/stat/line - use direction (VK) or recommendation (live_props)
            vk_lookup = {}
            for vk in vk_props:
                # VK picks use 'direction' field, normalize to support both
                dir_field = vk.get('direction') or vk.get('recommendation', 'OVER')
                # Create multiple lookup keys for flexible matching
                key1 = f"{vk.get('player_name')}|{vk.get('stat_type')}|{vk.get('line')}|{dir_field}"
                key2 = f"{vk.get('player_name')}|{vk.get('stat_type')}|{vk.get('line')}"  # Without direction
                vk_lookup[key1] = vk
                vk_lookup[key2] = vk
            
            # Also check safe haven and front lines
            for tier_name in ["safe_haven", "front_lines"]:
                tier_coll = self.db[get_collection_name(tier_name, "mlb")]
                tier_props = await tier_coll.find({}, {"_id": 0}).to_list(length=None)
                for vk in tier_props:
                    dir_field = vk.get('direction') or vk.get('recommendation', 'OVER')
                    key1 = f"{vk.get('player_name')}|{vk.get('stat_type')}|{vk.get('line')}|{dir_field}"
                    key2 = f"{vk.get('player_name')}|{vk.get('stat_type')}|{vk.get('line')}"
                    vk_lookup[key1] = vk
                    vk_lookup[key2] = vk
            
            logger.info(f"[SHARP_SORT] Loaded {len(vk_lookup)} VK projections for matching")
            
            # Process each prop
            fair_values = []
            
            for prop in props:
                all_odds = prop.get("all_odds", {})
                sharp_odds = all_odds.get("pinnacle")
                dk_odds = all_odds.get("draftkings")
                
                # Track stats
                if sharp_odds is not None:
                    results["stats"]["total_with_sharp_odds"] += 1
                    fair_value = self.calculate_fair_value(sharp_odds)
                    fair_values.append(fair_value)
                    prop["sharp_fair_value"] = fair_value
                
                if prop.get("dk_line") is not None:
                    results["stats"]["total_with_dk_line"] += 1
                
                # Get VK projection - try multiple key formats
                prop_dir = prop.get('recommendation') or prop.get('direction', 'OVER')
                key1 = f"{prop.get('player_name')}|{prop.get('stat_type')}|{prop.get('line')}|{prop_dir}"
                key2 = f"{prop.get('player_name')}|{prop.get('stat_type')}|{prop.get('line')}"
                vk_projection = vk_lookup.get(key1) or vk_lookup.get(key2, {})
                
                # Merge VK data into prop
                prop["projected_value"] = vk_projection.get("projected_value")
                prop["r_squared"] = vk_projection.get("r_squared")
                prop["slope"] = vk_projection.get("slope")
                prop["edge_pct"] = vk_projection.get("edge_pct")
                prop["hit_rate_l10"] = vk_projection.get("hit_rate_l10")
                prop["l10_avg"] = vk_projection.get("l10_avg")
                
                # Analyze DK vs PP
                dk_analysis = self.analyze_dk_vs_pp(
                    prop.get("dk_line"),
                    dk_odds,
                    prop.get("line"),
                    -110
                )
                prop["dk_analysis"] = dk_analysis
                
                # Classify
                tier = self.classify_prop(prop, vk_projection)
                prop["sharp_tier"] = tier
                prop["classified_at"] = datetime.now(timezone.utc).isoformat()
                
                # Add boolean flags for frontend
                prop["is_goblin"] = (tier == "GOBLIN")
                prop["is_demon"] = (tier == "DEMON")
                prop["tier_label"] = tier
                
                # Add to appropriate list
                if tier == "GOBLIN":
                    results["goblins"].append(prop)
                elif tier == "DEMON":
                    results["demons"].append(prop)
                elif tier == "STANDARD":
                    results["standard"].append(prop)
                else:
                    results["unclassified"] += 1
            
            # Calculate average fair value
            if fair_values:
                results["stats"]["sharp_fair_value_avg"] = round(sum(fair_values) / len(fair_values), 4)
            
            # Sort by edge/value
            results["goblins"].sort(key=lambda x: abs(x.get("edge_pct") or 0), reverse=True)
            results["demons"].sort(key=lambda x: abs(x.get("edge_pct") or 0), reverse=True)
            results["standard"].sort(key=lambda x: abs(x.get("edge_pct") or 0), reverse=True)
            
            # Save to collections
            if save_to_db:
                await self._save_to_collections(results)
            
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            results["duration_seconds"] = round(duration, 2)
            results["completed_at"] = datetime.now(timezone.utc).isoformat()
            
            logger.info("[SHARP_SORT] Sharp Sorting Complete:")
            logger.info(f"  • Props Processed: {results['props_processed']}")
            logger.info(f"  • Sharp Goblins: {len(results['goblins'])}")
            logger.info(f"  • Demons: {len(results['demons'])}")
            logger.info(f"  • Standard: {len(results['standard'])}")
            logger.info(f"  • Unclassified: {results['unclassified']}")
            
        except Exception as e:
            logger.error(f"[SHARP_SORT] Error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results["success"] = False
            results["errors"].append(str(e))
        
        return results
    
    async def _save_to_collections(self, results: Dict) -> None:
        """Save classified props to their respective collections."""
        
        # Goblins
        if results["goblins"]:
            goblins_coll = self.db["mlb_goblins"]
            await goblins_coll.delete_many({})
            # Remove _id if present
            clean_goblins = [{k: v for k, v in p.items() if k != "_id"} for p in results["goblins"]]
            await goblins_coll.insert_many(clean_goblins)
            logger.info(f"[SHARP_SORT] Saved {len(clean_goblins)} Goblins")
        
        # Demons
        if results["demons"]:
            demons_coll = self.db["mlb_demons"]
            await demons_coll.delete_many({})
            clean_demons = [{k: v for k, v in p.items() if k != "_id"} for p in results["demons"]]
            await demons_coll.insert_many(clean_demons)
            logger.info(f"[SHARP_SORT] Saved {len(clean_demons)} Demons")
        
        # Standard
        if results["standard"]:
            standard_coll = self.db["mlb_standard"]
            await standard_coll.delete_many({})
            clean_standard = [{k: v for k, v in p.items() if k != "_id"} for p in results["standard"]]
            await standard_coll.insert_many(clean_standard)
            logger.info(f"[SHARP_SORT] Saved {len(clean_standard)} Standard")


# Singleton
_sharp_sorting: Optional[MLBSharpSortingService] = None


def get_sharp_sorting_service(db: AsyncIOMotorDatabase) -> MLBSharpSortingService:
    """Get or create Sharp Sorting service."""
    global _sharp_sorting
    if _sharp_sorting is None:
        _sharp_sorting = MLBSharpSortingService(db)
    return _sharp_sorting


async def run_mlb_sharp_sorting(
    db: AsyncIOMotorDatabase,
    stat_types: List[str] = None,
    save_to_db: bool = True
) -> Dict[str, Any]:
    """
    Run MLB Sharp Sorting & Tier Distribution.
    
    Classifies props into:
    - Goblins: Sharp favored (odds ≤ -240) + VK confirms
    - Demons: DK mispricing + VK slope trend
    - Standard: Books agree (-110 to -130)
    """
    service = get_sharp_sorting_service(db)
    return await service.run_sharp_sorting(stat_types, save_to_db)
