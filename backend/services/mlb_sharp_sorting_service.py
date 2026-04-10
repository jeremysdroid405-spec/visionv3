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
        self._player_logs_cache = {}  # Cache for player historical logs
    
    # =========================================================================
    # MLB HIT RATE CALCULATION FROM GAME LOGS
    # =========================================================================
    
    async def _load_player_logs_cache(self):
        """Load all player game logs from mlb_master_hub_2026 into cache for fast lookup.
        
        SSOT: Uses mlb_master_hub_2026.bdl_game_logs as the single source of truth.
        This ensures consistency between pick cards, player detail views, and hit rate calculations.
        """
        if self._player_logs_cache:
            return  # Already loaded
        
        try:
            # SSOT: mlb_master_hub_2026.bdl_game_logs
            master_hub = self.db["mlb_master_hub_2026"]
            all_players = await master_hub.find(
                {"bdl_game_logs": {"$exists": True, "$ne": []}},
                {"_id": 0, "display_name": 1, "bdl_game_logs": 1}
            ).to_list(length=None)
            
            for player_doc in all_players:
                player_name = player_doc.get("display_name", "").lower().strip()
                if player_name:
                    self._player_logs_cache[player_name] = player_doc.get("bdl_game_logs", [])
            logger.info(f"[SHARP_SORT] Loaded game logs from mlb_master_hub_2026 for {len(self._player_logs_cache)} MLB players")
        except Exception as e:
            logger.warning(f"[SHARP_SORT] Failed to load player logs cache: {e}")
    
    def calculate_mlb_hit_rates(self, player_name: str, stat_type: str, line: float) -> Dict[str, Any]:
        """
        Calculate L5/L10 hit rates from MLB historical game logs.
        
        Args:
            player_name: Player name to look up
            stat_type: MLB stat type (e.g., "Hits", "Total Bases", "RBIs", etc.)
            line: The prop line to compare against
            
        Returns:
            Dict with h5_rate, h10_rate, l5_avg, l10_avg, season_avg
        """
        default_result = {
            "h5_rate": None,
            "h10_rate": None,
            "l5_avg": None,
            "l10_avg": None,
            "season_avg": None
        }
        
        if not player_name or not line:
            return default_result
        
        # Look up player logs
        player_key = player_name.lower().strip()
        game_logs = self._player_logs_cache.get(player_key, [])
        
        if not game_logs:
            return default_result
        
        # Map stat type to game log field
        stat_map = {
            "hits": "hits",
            "total bases": "total_bases",
            "rbis": "rbis",
            "runs": "runs",
            "home runs": "home_runs",
            "stolen bases": "stolen_bases",
            "walks": "walks",
            "strikeouts": "strikeouts",
            "hits+runs+rbis": ["hits", "runs", "rbis"],  # Combo stat
            "pitcher strikeouts": "pitcher_strikeouts",
            "pitching outs": "innings_pitched",  # IP * 3
            "earned runs": "earned_runs",
            "hits allowed": "hits_allowed",
            "walks allowed": "pitcher_walks",
        }
        
        stat_key = stat_type.lower().strip()
        log_field = stat_map.get(stat_key, stat_key.replace(" ", "_"))
        
        # Sort logs by date descending
        try:
            sorted_logs = sorted(
                game_logs, 
                key=lambda x: x.get("date", "") or "", 
                reverse=True
            )
        except Exception:
            sorted_logs = game_logs
        
        def get_stat_value(game, field):
            """Extract stat value from game log, handling combo stats.
            
            Returns None if value is missing (to be skipped in calculation).
            """
            if isinstance(field, list):
                # Combo stat - all components must exist
                combo_val = 0
                for f in field:
                    v = game.get(f)
                    if v is None:
                        return None  # Skip games with missing combo components
                    combo_val += (v or 0)
                return combo_val
            else:
                val = game.get(field)
                if val is None:
                    return None  # Skip games with missing data
                # Special handling for pitching outs (IP * 3)
                if field == "innings_pitched" and val:
                    return round(val * 3)
                return val or 0
        
        def calc_stats(game_list):
            """Calculate avg and hit rate for a set of games.
            
            SSOT: Skips games with None/missing values (consistent with player detail endpoint).
            """
            if not game_list:
                return 0, 0
            
            values = []
            hits = 0
            for g in game_list:
                val = get_stat_value(g, log_field)
                if val is None:
                    continue  # Skip games with missing data
                values.append(val)
                if line and val >= line:  # >= for "over" comparison
                    hits += 1
            
            if not values:
                return 0, 0
            
            avg = sum(values) / len(values)
            hit_rate = (hits / len(values) * 100)
            return avg, hit_rate
        
        # Calculate L5, L10, and season stats
        l5_avg, h5_rate = calc_stats(sorted_logs[:5])
        l10_avg, h10_rate = calc_stats(sorted_logs[:10])
        season_avg, _ = calc_stats(sorted_logs)
        
        return {
            "h5_rate": round(h5_rate) if h5_rate else None,
            "h10_rate": round(h10_rate) if h10_rate else None,
            "l5_avg": round(l5_avg, 1) if l5_avg else None,
            "l10_avg": round(l10_avg, 1) if l10_avg else None,
            "season_avg": round(season_avg, 1) if season_avg else None
        }
    
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
        # Note: edge_pct and hit_rate available in vk_projection but not used in current classification logic
        
        # =================================================================
        # GOBLIN CHECK: PP odds-based (negative odds = favorable)
        # Since Pinnacle doesn't offer MLB props, use PP classification
        # =================================================================
        # Check the is_goblin flag (set during sync based on PP odds < 0)
        if prop.get("is_goblin"):
            return "GOBLIN"
        
        # Alternative: Check PP odds directly
        pp_odds = prop.get("pp_odds")
        if pp_odds is not None and pp_odds < 0:
            # Favorable PP odds = Goblin
            # Also require some VK confirmation if available
            vk_confirms = True
            if projected_value and line:
                if direction == "OVER" and projected_value <= line:
                    vk_confirms = False
                elif direction == "UNDER" and projected_value >= line:
                    vk_confirms = False
            
            if vk_confirms:
                return "GOBLIN"
        
        # Sharp odds classification (if Pinnacle data available - usually not for MLB)
        if sharp_odds is not None and sharp_odds <= -150:
            vk_confirms = False
            if direction == "OVER" and projected_value and projected_value > line:
                vk_confirms = True
            elif direction == "UNDER" and projected_value and projected_value < line:
                vk_confirms = True
            
            if vk_confirms:
                return "GOBLIN"
        
        # =================================================================
        # DEMON CHECK: PP odds >= +100 or significant line discrepancy
        # =================================================================
        # Check the is_demon flag (set during sync based on PP odds >= +100)
        if prop.get("is_demon"):
            return "DEMON"
        
        # Alternative: Check PP odds directly
        if pp_odds is not None and pp_odds >= 100:
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
            
            # Load player historical logs cache for hit rate calculation
            await self._load_player_logs_cache()
            
            # Process each prop
            fair_values = []
            hit_rates_calculated = 0
            
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
                
                # Calculate hit rates from historical game logs
                hit_rates = self.calculate_mlb_hit_rates(
                    prop.get("player_name"),
                    prop.get("stat_type"),
                    prop.get("line")
                )
                
                # Apply calculated hit rates (prioritize fresh calculation over VK data)
                if hit_rates.get("h5_rate") is not None:
                    prop["h5_rate"] = hit_rates["h5_rate"]
                    hit_rates_calculated += 1
                if hit_rates.get("h10_rate") is not None:
                    prop["h10_rate"] = hit_rates["h10_rate"]
                    prop["hit_rate_l10"] = hit_rates["h10_rate"] / 100  # Also keep decimal version
                else:
                    # Fallback to VK data if no logs
                    prop["hit_rate_l10"] = vk_projection.get("hit_rate_l10")
                    # Convert to percentage for h10_rate
                    if prop["hit_rate_l10"] is not None:
                        prop["h10_rate"] = round(prop["hit_rate_l10"] * 100) if prop["hit_rate_l10"] <= 1 else prop["hit_rate_l10"]
                
                # Apply averages from game logs
                if hit_rates.get("l10_avg") is not None:
                    prop["l10_avg"] = hit_rates["l10_avg"]
                else:
                    prop["l10_avg"] = vk_projection.get("l10_avg")
                
                if hit_rates.get("season_avg") is not None:
                    prop["season_avg"] = hit_rates["season_avg"]
                elif prop.get("l10_avg"):
                    prop["season_avg"] = prop["l10_avg"]  # Use L10 as fallback
                
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
            logger.info(f"  • Hit Rates Calculated: {hit_rates_calculated}")
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
