"""
PropVision Oracle Service
==========================
Adversarial AI-powered analysis using Bull vs Bear agents.

Data Synthesis:
- VK Projection (Vegas Killer ML model)
- Pinnacle De-Vigged Probability
- DK Alt Line Ladder

Adversarial Logic (Gemini 3.1 Pro):
- Agent Bull: Argues why 'More' will hit based on 5-year historical trends
- Agent Bear: Argues why it's a 'Trap' based on cold streaks or line manipulation

Oracle Confidence Score:
- Scale: 1-10
- Props with score < 7 are automatically demoted from 'Safe Haven'
"""

import os
import logging
import json
import math
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.db_config import get_collection_name

logger = logging.getLogger(__name__)

# Gemini API configuration
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

# Oracle thresholds
SAFE_HAVEN_MIN_SCORE = 7  # Props below this get demoted
WAR_ZONE_MAX_SCORE = 4    # High volatility plays


class PropVisionOracleService:
    """
    PropVision Oracle Service - Adversarial Bull vs Bear Analysis.
    
    Uses Gemini 3.1 Pro to run adversarial analysis on top props,
    synthesizing VK projections, sharp probabilities, and DK lines.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self._genai = None
        self._model = None
        self.sport = "mlb"  # Default to MLB
    
    def _initialize_genai(self) -> bool:
        """Initialize Google Generative AI client."""
        if self._genai is not None:
            return True
        
        if not GOOGLE_API_KEY:
            logger.error("[ORACLE] GOOGLE_API_KEY not configured")
            return False
        
        try:
            import google.generativeai as genai
            genai.configure(api_key=GOOGLE_API_KEY)
            self._genai = genai
            # Use gemini-2.5-flash for Bull vs Bear analysis
            self._model = genai.GenerativeModel("gemini-2.5-flash")
            logger.info("[ORACLE] Gemini API initialized with gemini-2.5-flash")
            return True
        except Exception as e:
            logger.error(f"[ORACLE] Failed to initialize Gemini: {e}")
            return False
    
    def _get_collection(self, name: str):
        """Get sport-specific collection."""
        collection_name = get_collection_name(name, self.sport)
        return self.db[collection_name]
    
    def _get_raw_collection(self, name: str):
        """Get raw collection by name (not sport-prefixed)."""
        return self.db[name]
    
    # =========================================================================
    # DATA SYNTHESIS
    # =========================================================================
    
    async def _get_vk_projection(self, player_name: str, stat_type: str) -> Optional[Dict]:
        """
        Get VK (Vegas Killer) projection for a player/stat combo.
        
        Returns:
            Dict with: projected_value, r_squared, std_error, edge_pct
        """
        # VK projections are stored in sport-specific collection
        vk_collection = self._get_collection("vk_projections")
        
        projection = await vk_collection.find_one(
            {
                "player_name": player_name,
                "stat_type": stat_type
            },
            {"_id": 0}
        )
        
        if projection:
            return {
                "projected_value": projection.get("projected_value"),
                "r_squared": projection.get("r_squared"),
                "std_error": projection.get("std_error"),
                "edge_pct": projection.get("edge_pct"),
                "sample_size": projection.get("sample_size", 0),
                "confidence": projection.get("confidence", "medium")
            }
        return None
    
    def _calculate_pinnacle_devig(self, sharp_odds: Optional[int]) -> Optional[float]:
        """
        Calculate de-vigged probability from Pinnacle odds.
        
        De-vig removes the bookmaker's margin to get true probability.
        
        Args:
            sharp_odds: American odds from Pinnacle
            
        Returns:
            Implied probability (0-1) after removing vig
        """
        if sharp_odds is None:
            return None
        
        # Convert American odds to implied probability
        if sharp_odds > 0:
            implied_prob = 100 / (sharp_odds + 100)
        else:
            implied_prob = abs(sharp_odds) / (abs(sharp_odds) + 100)
        
        # Apply standard de-vig (assume ~2% total vig on Pinnacle)
        # For simplicity, we adjust by 1% towards 50%
        devigged = implied_prob * 0.98 + 0.01
        
        return round(devigged, 4)
    
    def _build_dk_line_ladder(self, prop: Dict) -> Dict:
        """
        Build DraftKings alternate line ladder.
        
        Returns:
            Dict with available DK lines and their implied probabilities
        """
        dk_line = prop.get("dk_line")
        dk_odds = prop.get("dk_odds")
        all_lines = prop.get("all_lines", {})
        all_odds = prop.get("all_odds", {})
        
        ladder = {
            "primary_line": dk_line,
            "primary_odds": dk_odds,
            "primary_implied_prob": None,
            "lines_available": []
        }
        
        if dk_odds:
            # Calculate implied probability
            if dk_odds > 0:
                ladder["primary_implied_prob"] = round(100 / (dk_odds + 100), 4)
            else:
                ladder["primary_implied_prob"] = round(abs(dk_odds) / (abs(dk_odds) + 100), 4)
        
        # Add any alternate lines from DK
        if "draftkings" in all_lines:
            ladder["lines_available"].append({
                "line": all_lines["draftkings"],
                "odds": all_odds.get("draftkings"),
                "book": "draftkings"
            })
        
        return ladder
    
    async def oracle_data_synthesis(self, prop: Dict) -> Dict:
        """
        Synthesize all data sources for Oracle analysis.
        
        Args:
            prop: Prop dictionary from cached board
            
        Returns:
            Dict with VK projection, Pinnacle de-vig, DK ladder
        """
        player_name = prop.get("player_name")
        stat_type = prop.get("stat_type")
        
        # 1. VK Projection
        vk_projection = await self._get_vk_projection(player_name, stat_type)
        
        # 2. Pinnacle De-Vigged Probability
        sharp_odds = prop.get("sharp_odds")
        pinnacle_devig = self._calculate_pinnacle_devig(sharp_odds)
        
        # 3. DK Alt Line Ladder
        dk_ladder = self._build_dk_line_ladder(prop)
        
        # 4. Historical data summary
        historical = {
            "l5_avg": prop.get("l5_avg"),
            "l10_avg": prop.get("l10_avg"),
            "hit_rate_l5": prop.get("hit_rate_l5"),
            "hit_rate_l10": prop.get("hit_rate_l10"),
            "season_avg": prop.get("season_avg"),
        }
        
        return {
            "player_name": player_name,
            "stat_type": stat_type,
            "line": prop.get("line"),
            "pp_line": prop.get("pp_line"),
            "pp_odds": prop.get("pp_odds"),
            "recommendation": prop.get("recommendation"),
            "vk_projection": vk_projection,
            "pinnacle_devig_prob": pinnacle_devig,
            "sharp_line": prop.get("sharp_line"),
            "sharp_odds": sharp_odds,
            "dk_ladder": dk_ladder,
            "historical": historical,
            "is_goblin": prop.get("is_goblin"),
            "is_demon": prop.get("is_demon"),
            "team": prop.get("team"),
            "opponent": prop.get("away_team") if prop.get("team") == prop.get("home_team") else prop.get("home_team")
        }
    
    # =========================================================================
    # ADVERSARIAL BULL VS BEAR ANALYSIS
    # =========================================================================
    
    def _build_bull_bear_prompt(self, synthesized_props: List[Dict]) -> str:
        """
        Build the Bull vs Bear adversarial prompt for Gemini.
        
        Args:
            synthesized_props: List of props with synthesized data
            
        Returns:
            Formatted prompt string
        """
        prompt = """You are the PropVision Oracle, running an adversarial Bull vs Bear analysis on sports props.

## YOUR TASK
For each prop below, you must provide:
1. **Agent Bull's Argument** (2-3 sentences): Why the "OVER/MORE" will hit based on historical trends, favorable matchups, and statistical edges.
2. **Agent Bear's Argument** (2-3 sentences): Why this is a "TRAP" based on cold streaks, line manipulation, unfavorable conditions, or regression concerns.
3. **Oracle Confidence Score** (1-10): Your final verdict weighing both arguments.
   - 9-10: STRONG conviction, extremely high confidence
   - 7-8: SOLID play, good risk/reward
   - 5-6: NEUTRAL, coin flip
   - 3-4: LEAN AVOID, risk outweighs reward
   - 1-2: TRAP, strong avoid

## SCORING CRITERIA
Consider these factors when scoring:
- VK Projection edge vs line (higher edge = bullish)
- R-squared confidence (>0.70 = reliable model)
- Pinnacle de-vigged probability (>55% = sharp money agrees)
- L5/L10 hit rates (>70% = consistent performer)
- Is it marked as Goblin (favorable) or Demon (risky)?
- DK line vs PP line discrepancy (line shopping edge)

## PROPS TO ANALYZE
"""
        
        for i, prop in enumerate(synthesized_props, 1):
            vk = prop.get("vk_projection") or {}
            hist = prop.get("historical") or {}
            dk = prop.get("dk_ladder") or {}
            
            prompt += f"""
---
### PROP {i}: {prop.get('player_name')} - {prop.get('stat_type')} {prop.get('recommendation')} {prop.get('line')}

**Book Lines:**
- PrizePicks: {prop.get('pp_line')} @ {prop.get('pp_odds')}
- DraftKings: {dk.get('primary_line')} @ {dk.get('primary_odds')} (implied: {dk.get('primary_implied_prob')})
- Pinnacle: {prop.get('sharp_line')} @ {prop.get('sharp_odds')} (de-vigged: {prop.get('pinnacle_devig_prob')})

**VK Model:**
- Projected: {vk.get('projected_value', 'N/A')}
- R-squared: {vk.get('r_squared', 'N/A')}
- Edge: {vk.get('edge_pct', 'N/A')}%

**Historical Performance:**
- L5 Avg: {hist.get('l5_avg', 'N/A')} | L10 Avg: {hist.get('l10_avg', 'N/A')}
- L5 Hit Rate: {hist.get('hit_rate_l5', 'N/A')}% | L10 Hit Rate: {hist.get('hit_rate_l10', 'N/A')}%

**Classification:** {'GOBLIN (Favorable)' if prop.get('is_goblin') else 'DEMON (High Risk)' if prop.get('is_demon') else 'Standard'}
**Matchup:** {prop.get('team')} vs {prop.get('opponent', 'TBD')}
"""
        
        prompt += """
---

## RESPONSE FORMAT (JSON)
Respond with a JSON array. Each object must have:
```json
[
  {
    "prop_index": 1,
    "player_name": "...",
    "stat_type": "...",
    "bull_argument": "...",
    "bear_argument": "...",
    "oracle_score": 8,
    "verdict": "PLAY" | "LEAN" | "AVOID" | "TRAP",
    "key_factor": "One sentence on the deciding factor"
  }
]
```

Only return valid JSON. No markdown code blocks.
"""
        return prompt
    
    async def run_bull_bear_analysis(
        self, 
        props: List[Dict],
        max_props: int = 10
    ) -> List[Dict]:
        """
        Run Bull vs Bear adversarial analysis on props using Gemini.
        
        Args:
            props: List of props from cached board
            max_props: Maximum props to analyze (default 10)
            
        Returns:
            List of analysis results with Oracle scores
        """
        if not self._initialize_genai():
            logger.error("[ORACLE] Cannot run analysis - Gemini not initialized")
            return []
        
        # Limit to top props
        props_to_analyze = props[:max_props]
        
        # Synthesize data for each prop
        synthesized = []
        for prop in props_to_analyze:
            synth = await self.oracle_data_synthesis(prop)
            synthesized.append(synth)
        
        # Build prompt
        prompt = self._build_bull_bear_prompt(synthesized)
        
        try:
            # Call Gemini
            logger.info(f"[ORACLE] Analyzing {len(synthesized)} props with Bull vs Bear...")
            response = self._model.generate_content(prompt)
            
            # Parse response
            response_text = response.text.strip()
            
            # Clean up response (remove markdown if present)
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
            
            results = json.loads(response_text)
            logger.info(f"[ORACLE] Analysis complete: {len(results)} verdicts returned")
            
            return results
            
        except json.JSONDecodeError as e:
            logger.error(f"[ORACLE] Failed to parse Gemini response: {e}")
            logger.error(f"[ORACLE] Raw response: {response.text[:500]}")
            return []
        except Exception as e:
            logger.error(f"[ORACLE] Gemini API error: {e}")
            return []
    
    # =========================================================================
    # ORACLE FINAL VERDICT
    # =========================================================================
    
    async def oracle_final_verdict(
        self,
        vk_projection: Optional[Dict] = None,
        pinnacle_devig_prob: Optional[float] = None,
        dk_ladder: Optional[Dict] = None,
        prop: Optional[Dict] = None
    ) -> Dict:
        """
        Generate final Oracle verdict for a single prop.
        
        This is the main entry point for individual prop analysis.
        
        Args:
            vk_projection: VK model output (projected_value, r_squared, edge_pct)
            pinnacle_devig_prob: De-vigged probability from Pinnacle
            dk_ladder: DK alternate line ladder
            prop: Full prop dictionary (optional, for additional context)
            
        Returns:
            Dict with oracle_score, verdict, reasoning
        """
        # Calculate base score from quantitative factors
        score = 5.0  # Start neutral
        reasons = []
        
        # 1. VK Projection Analysis (+/- 2 points)
        if vk_projection:
            edge = vk_projection.get("edge_pct", 0)
            r_sq = vk_projection.get("r_squared", 0)
            
            if edge and edge > 20 and r_sq > 0.70:
                score += 2.0
                reasons.append(f"Strong VK edge ({edge:.1f}%) with high confidence (R²={r_sq:.2f})")
            elif edge and edge > 15 and r_sq > 0.60:
                score += 1.0
                reasons.append(f"Solid VK edge ({edge:.1f}%)")
            elif edge and edge < 5:
                score -= 1.0
                reasons.append(f"Weak VK edge ({edge:.1f}%)")
        
        # 2. Pinnacle Sharp Money (+/- 1.5 points)
        if pinnacle_devig_prob:
            if pinnacle_devig_prob > 0.60:
                score += 1.5
                reasons.append(f"Sharp money strongly agrees ({pinnacle_devig_prob*100:.1f}%)")
            elif pinnacle_devig_prob > 0.55:
                score += 0.5
                reasons.append(f"Sharp money leans agree ({pinnacle_devig_prob*100:.1f}%)")
            elif pinnacle_devig_prob < 0.45:
                score -= 1.0
                reasons.append(f"Sharp money disagrees ({pinnacle_devig_prob*100:.1f}%)")
        
        # 3. DK Line Discrepancy (+/- 1 point)
        if dk_ladder and prop:
            pp_line = prop.get("pp_line")
            dk_line = dk_ladder.get("primary_line")
            
            if pp_line and dk_line:
                if dk_line > pp_line:  # DK has higher line = PP line is easier
                    score += 0.5
                    reasons.append(f"Line shopping edge: PP {pp_line} vs DK {dk_line}")
                elif dk_line < pp_line:  # DK has lower line = PP line is harder
                    score -= 0.5
                    reasons.append(f"Line disadvantage: PP {pp_line} vs DK {dk_line}")
        
        # 4. Historical Hit Rates (+/- 1.5 points)
        if prop:
            l10_hr = prop.get("hit_rate_l10")
            l5_hr = prop.get("hit_rate_l5")
            
            if l10_hr and l10_hr > 70:
                score += 1.0
                reasons.append(f"Excellent L10 hit rate ({l10_hr:.0f}%)")
            elif l10_hr and l10_hr < 40:
                score -= 1.0
                reasons.append(f"Poor L10 hit rate ({l10_hr:.0f}%)")
            
            # Recent form (L5 vs L10)
            if l5_hr and l10_hr:
                if l5_hr > l10_hr + 10:
                    score += 0.5
                    reasons.append("Hot streak (L5 > L10)")
                elif l5_hr < l10_hr - 15:
                    score -= 0.5
                    reasons.append("Cold streak (L5 < L10)")
        
        # 5. Goblin/Demon Classification (+/- 0.5 points)
        if prop:
            if prop.get("is_goblin"):
                score += 0.5
                reasons.append("Goblin classification (favorable odds)")
            elif prop.get("is_demon"):
                score -= 0.5
                reasons.append("Demon classification (high risk)")
        
        # Clamp score to 1-10 range
        final_score = max(1, min(10, round(score)))
        
        # Determine verdict
        if final_score >= 8:
            verdict = "STRONG_PLAY"
        elif final_score >= 7:
            verdict = "PLAY"
        elif final_score >= 5:
            verdict = "LEAN"
        elif final_score >= 3:
            verdict = "AVOID"
        else:
            verdict = "TRAP"
        
        return {
            "oracle_score": final_score,
            "verdict": verdict,
            "reasoning": reasons,
            "tier_eligible": final_score >= SAFE_HAVEN_MIN_SCORE,
            "is_war_zone": final_score <= WAR_ZONE_MAX_SCORE,
            "calculated_at": datetime.now(timezone.utc).isoformat()
        }
    
    # =========================================================================
    # BATCH ORACLE PROCESSING
    # =========================================================================
    
    async def process_safe_haven_props(
        self,
        use_gemini: bool = True,
        max_props: int = 10
    ) -> Dict[str, Any]:
        """
        Process Safe Haven props through Oracle analysis.
        
        1. Fetch current Safe Haven picks
        2. Run Bull vs Bear analysis (if use_gemini=True)
        3. Calculate Oracle scores
        4. Demote props with score < 7
        
        Args:
            use_gemini: Whether to use Gemini for Bull/Bear analysis
            max_props: Max props to send to Gemini
            
        Returns:
            Summary of processing results
        """
        results = {
            "processed": 0,
            "promoted": 0,
            "demoted": 0,
            "props": [],
            "errors": []
        }
        
        try:
            # Get Safe Haven props from cached board
            cached_board = self._get_collection("cached_board")
            
            # Aggregate props with high hit rates (current Safe Haven criteria)
            pipeline = [
                {"$unwind": "$props"},
                {"$match": {
                    "props.hit_rate_l10": {"$gte": 60},
                    "props.is_goblin": True
                }},
                {"$replaceRoot": {"newRoot": "$props"}},
                {"$limit": max_props * 2}  # Fetch extra for filtering
            ]
            
            safe_haven_props = await cached_board.aggregate(pipeline).to_list(length=None)
            
            if not safe_haven_props:
                logger.info("[ORACLE] No Safe Haven props found")
                return results
            
            logger.info(f"[ORACLE] Found {len(safe_haven_props)} Safe Haven candidates")
            
            # Run Bull vs Bear analysis if enabled
            gemini_verdicts = {}
            if use_gemini and len(safe_haven_props) > 0:
                verdicts = await self.run_bull_bear_analysis(
                    safe_haven_props[:max_props]
                )
                for v in verdicts:
                    key = f"{v.get('player_name')}_{v.get('stat_type')}"
                    gemini_verdicts[key] = v
            
            # Process each prop
            for prop in safe_haven_props:
                player = prop.get("player_name")
                stat = prop.get("stat_type")
                key = f"{player}_{stat}"
                
                # Synthesize data
                synth = await self.oracle_data_synthesis(prop)
                
                # Get Oracle verdict
                verdict = await self.oracle_final_verdict(
                    vk_projection=synth.get("vk_projection"),
                    pinnacle_devig_prob=synth.get("pinnacle_devig_prob"),
                    dk_ladder=synth.get("dk_ladder"),
                    prop=prop
                )
                
                # Merge Gemini verdict if available
                if key in gemini_verdicts:
                    gv = gemini_verdicts[key]
                    verdict["gemini_score"] = gv.get("oracle_score")
                    verdict["bull_argument"] = gv.get("bull_argument")
                    verdict["bear_argument"] = gv.get("bear_argument")
                    verdict["gemini_verdict"] = gv.get("verdict")
                    verdict["key_factor"] = gv.get("key_factor")
                    
                    # Average the scores if both available
                    if gv.get("oracle_score"):
                        avg_score = (verdict["oracle_score"] + gv["oracle_score"]) / 2
                        verdict["oracle_score"] = round(avg_score)
                        verdict["tier_eligible"] = verdict["oracle_score"] >= SAFE_HAVEN_MIN_SCORE
                
                # Track results
                results["processed"] += 1
                
                if verdict["tier_eligible"]:
                    results["promoted"] += 1
                else:
                    results["demoted"] += 1
                
                results["props"].append({
                    "player_name": player,
                    "stat_type": stat,
                    "line": prop.get("line"),
                    "oracle_score": verdict["oracle_score"],
                    "verdict": verdict["verdict"],
                    "tier_eligible": verdict["tier_eligible"],
                    "bull_argument": verdict.get("bull_argument"),
                    "bear_argument": verdict.get("bear_argument"),
                    "reasoning": verdict["reasoning"]
                })
            
            # Sort by Oracle score descending
            results["props"].sort(key=lambda x: x["oracle_score"], reverse=True)
            
            logger.info(f"[ORACLE] Processed {results['processed']} props: {results['promoted']} promoted, {results['demoted']} demoted")
            
        except Exception as e:
            logger.error(f"[ORACLE] Error processing Safe Haven: {e}")
            results["errors"].append(str(e))
        
        return results
    
    async def update_cached_board_with_oracle(self) -> Dict[str, Any]:
        """
        Update cached board with Oracle scores.
        
        Adds oracle_score, oracle_verdict, and tier_eligible fields to props.
        """
        results = {
            "updated": 0,
            "errors": []
        }
        
        try:
            cached_board = self._get_collection("cached_board")
            
            # Get all players
            players = await cached_board.find({}, {"_id": 0}).to_list(length=None)
            
            for player_doc in players:
                player_name = player_doc.get("player_name")
                updated_props = []
                
                for prop in player_doc.get("props", []):
                    # Synthesize and score
                    synth = await self.oracle_data_synthesis(prop)
                    verdict = await self.oracle_final_verdict(
                        vk_projection=synth.get("vk_projection"),
                        pinnacle_devig_prob=synth.get("pinnacle_devig_prob"),
                        dk_ladder=synth.get("dk_ladder"),
                        prop=prop
                    )
                    
                    # Add Oracle fields to prop
                    prop["oracle_score"] = verdict["oracle_score"]
                    prop["oracle_verdict"] = verdict["verdict"]
                    prop["oracle_tier_eligible"] = verdict["tier_eligible"]
                    prop["oracle_reasoning"] = verdict["reasoning"]
                    
                    updated_props.append(prop)
                
                # Update player document
                await cached_board.update_one(
                    {"player_name": player_name},
                    {"$set": {"props": updated_props, "oracle_processed_at": datetime.now(timezone.utc).isoformat()}}
                )
                results["updated"] += 1
            
            logger.info(f"[ORACLE] Updated {results['updated']} players with Oracle scores")
            
        except Exception as e:
            logger.error(f"[ORACLE] Error updating cached board: {e}")
            results["errors"].append(str(e))
        
        return results


# Singleton instance
_oracle_service: Optional[PropVisionOracleService] = None


def get_oracle_service(db: AsyncIOMotorDatabase) -> PropVisionOracleService:
    """Get or create Oracle service instance."""
    global _oracle_service
    if _oracle_service is None:
        _oracle_service = PropVisionOracleService(db)
    return _oracle_service
