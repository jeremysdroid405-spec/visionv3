"""
vision_intel_service.py
=======================
PropVision Intelligence Engine - Unified Gemini 3.1 Pro Integration

This is the SINGLE source for ALL Gemini AI calls in the application.
It runs ONCE during Step 6 of the pipeline (after 3-Gate filter, before Top 10 selection).

Generates ALL AI outputs in one batch call:
1. Vision Intel Summary (ai_summary) - Human-like analysis
2. Intelligence Score (intel_score) - 1-10 confidence rating
3. Verdict (intel_verdict) - CHALK | TRAP | VALUE
4. Risk Factor (intel_risk) - Low | Medium | High
5. Composite Score - VK Prob * 0.7 + Intel Score * 0.3
6. Badge Analysis - Key situational factors
7. Matchup Insight - Defense/offense context

NO OTHER SERVICE SHOULD CALL GEMINI. This is the single point of AI intelligence.
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import asyncio

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

# Google Gemini Integration
try:
    from google import genai
    GEMINI_AVAILABLE = True
except ImportError:
    logger.warning("google-genai not available - Vision Intel will use fallback")
    GEMINI_AVAILABLE = False


# System prompt for batch analysis
VISION_INTEL_BATCH_PROMPT = """## Role
You are the **Lead NBA Scout** for PropVision. Your job is to write a gritty, 2-to-3 sentence scouting report explaining to a DFS bettor why we are locking in this specific PrizePicks prop.

**Tone:** Speak like a human sharp. Use basketball betting slang (e.g., 'smash spot', 'usage bump', 'blowout risk', 'green light', 'riding the hot hand', 'lock-down matchup'). DO NOT sound like a robot reading a spreadsheet. Never just list the raw percentages.

## Input Context
You will receive a data package containing:
1. **Model Stats:** VK Predicted Value, VK Edge, and VK Probability.
2. **Technical Gates:** Results of the 3-Gate qualification (Hit Rate, CV, Edge).
3. **Situational Intel:** Defense vs Position (DvP) matchup ranking, blowout risk, badges.
4. **Market Context:** Current DraftKings odds and prop classification (Goblin/Demon).

## CRITICAL: DvP Matchup Interpretation
The "defense" field shows the OPPONENT's defensive ranking vs that stat type:
- Rank #1-5 = OPPONENT is ELITE defender → BAD for player (flag as concern)
- Rank #6-15 = OPPONENT is solid → Challenging matchup
- Rank #16-25 = OPPONENT is weak → Favorable for player
- Rank #26-30 = OPPONENT is terrible → SMASH spot (boost confidence)

## Objective
1. **Validate the Math:** Compare the VK Model's output against the situational intel. Flag if matchup/context undermines the model.
2. **Assign Confidence:** Provide an "Intelligence Score" (1-10) that factors in what the model CAN'T see.
3. **Generate Intel:** Write a 1-2 sentence "vision_intel_summary" that explains the play's logic.
4. **Final Verdict:** CHALK (lock it), VALUE (good edge), or TRAP (context says no).

## Output Format (Strict JSON Array)
Return a JSON array with one object per prop:
[
  {
    "prop_id": "PlayerName_STAT_Line",
    "intel_score": 7,
    "verdict": "CHALK",
    "vision_intel_summary": "Maxey cooking at home with 90% L10. Houston's perimeter D (#28) is a sieve. Lock the over.",
    "risk_factor": "Low",
    "adjusted_confidence": 0.82
  }
]

## Scoring Guidelines
- **intel_score 8-10**: Elite spot. Matchup + numbers + situation all align. CHALK.
- **intel_score 6-7**: Solid edge with minor concerns. VALUE.
- **intel_score 4-5**: Mixed signals. Lean VALUE but watch it.
- **intel_score 1-3**: Red flags override the math. TRAP.

## Automatic TRAP Triggers
- Elite DvP matchup (#1-5) against the stat type
- Blowout risk HIGH for volume stats (PTS, PRA)
- Line set at/above season average with negative cushion
- CV > 0.40 for non-combo stats indicates boom/bust volatility

## CRITICAL INSTRUCTION
Do NOT mention or reference L3 (last 3 games) hit rates or data. This data is NOT provided. Only reference data fields that exist in the PROPS DATA: h20_rate (L20), h10_rate (L10), l5_avg, season_avg, vk_proj, vk_prob, vk_edge, cushion, cv, defense, dk_odds, blowout_risk, badges.

IMPORTANT: Return ONLY the JSON array. No markdown, no code blocks, no extra text."""


class VisionIntelService:
    """
    Unified Gemini intelligence layer for PropVision.
    ALL AI analysis happens here - no other service should call Gemini.
    """
    
    def __init__(self):
        self.api_key = os.environ.get('GOOGLE_API_KEY')
        self.enabled = GEMINI_AVAILABLE and self.api_key is not None
        self.client = None
        self.model_name = 'gemini-3.1-flash-lite-preview'
        
        if self.enabled:
            try:
                self.client = genai.Client(api_key=self.api_key)
                logger.info(f"Vision Intel Service initialized with {self.model_name}")
            except Exception as e:
                logger.error(f"Failed to initialize Gemini: {e}")
                self.enabled = False
        else:
            logger.warning("Vision Intel Service disabled - missing API key or library")
    
    def _build_batch_prompt(self, props: List[Dict[str, Any]], tier_name: str) -> str:
        """Build a batch prompt for analyzing multiple props at once."""
        
        props_data = []
        for prop in props:
            player_name = prop.get('player_name', 'Unknown')
            stat_type = prop.get('stat_type', 'PTS')
            line = prop.get('line', 0)
            
            # Core stats
            vk_predicted = prop.get('vk_predicted', 0)
            vk_prob = prop.get('vk_prob_over', 50)
            vk_edge = prop.get('vk_edge', 0)
            h20_rate = prop.get('h20_rate', 0)
            h10_rate = prop.get('h10_rate', 0)
            cv = prop.get('cv', 0)
            
            # Averages
            l5_avg = prop.get('l5_avg', 0)
            l10_avg = prop.get('l10_avg', 0)
            l20_avg = prop.get('l20_avg', 0)
            season_avg = prop.get('season_avg', l20_avg)
            
            # Market data
            dk_odds = prop.get('dk_odds', 'N/A')
            
            # Classification
            is_demon = prop.get('is_demon', False)
            is_goblin = prop.get('is_goblin', False)
            pick_type = "Demon (Over ceiling)" if is_demon else "Goblin (Safe floor)" if is_goblin else "Standard"
            
            # Matchup - OPPONENT's defense against this stat type
            opponent = prop.get('opponent', 'TBD')
            dvp_rank = prop.get('dvp_rank')  # Defense vs Position - how opponent defends this stat
            
            # Build OPPONENT defensive context (DvP = how well opponent defends this stat)
            # Rank 1-5 = opponent is ELITE at stopping this stat (bad for player)
            # Rank 26-30 = opponent is TERRIBLE at stopping this stat (good for player)
            if dvp_rank:
                if dvp_rank <= 5:
                    dvp_text = f"{opponent} ELITE D vs {stat_type} (#{dvp_rank} - TOUGH matchup)"
                elif dvp_rank <= 10:
                    dvp_text = f"{opponent} Strong D vs {stat_type} (#{dvp_rank} - difficult)"
                elif dvp_rank <= 20:
                    dvp_text = f"{opponent} Average D vs {stat_type} (#{dvp_rank} - neutral)"
                elif dvp_rank <= 25:
                    dvp_text = f"{opponent} Weak D vs {stat_type} (#{dvp_rank} - favorable)"
                else:
                    dvp_text = f"{opponent} POOR D vs {stat_type} (#{dvp_rank} - SMASH spot)"
            else:
                dvp_text = f"vs {opponent} (no DvP data)"
            
            # Badges/situational factors
            badges = prop.get('active_badges', [])
            badge_text = ", ".join([b.get('badge_key', b) if isinstance(b, dict) else str(b) for b in badges[:3]]) if badges else "None"
            
            # Blowout risk info
            blowout_risk = prop.get('intel_suite', {}).get('blowout_risk', {})
            blowout_level = blowout_risk.get('risk_level', 'UNKNOWN')
            
            # Calculate cushion (how far above line is average)
            cushion = round(l5_avg - line, 1) if l5_avg and line else 0
            
            prop_data = {
                "prop_id": f"{player_name}_{stat_type}_{line}",
                "player": player_name,
                "stat": stat_type,
                "line": line,
                "type": pick_type,
                "vk_proj": round(vk_predicted, 1) if vk_predicted else 0,
                "vk_prob": round(vk_prob, 0) if vk_prob else 50,
                "vk_edge": round(vk_edge, 1) if vk_edge else 0,
                "cushion": cushion,  # How far L5 avg is above/below line
                "h20_rate": round(h20_rate, 0) if h20_rate else 0,
                "h10_rate": round(h10_rate, 0) if h10_rate else 0,
                "l5_avg": round(l5_avg, 1) if l5_avg else 0,
                "season_avg": round(season_avg, 1) if season_avg else 0,
                "cv": round(cv, 2) if cv else 0,
                "opponent": opponent,
                "defense": dvp_text,
                "dk_odds": dk_odds,
                "blowout_risk": blowout_level,
                "badges": badge_text
            }
            props_data.append(prop_data)
        
        prompt = f"""## {tier_name.upper()} TIER - {len(props)} Props to Analyze

These props have passed the mathematical 3-Gate system. Validate each against context.

PROPS DATA:
{json.dumps(props_data, indent=2)}

Return your analysis as a JSON array. One object per prop with all required fields."""
        
        return prompt
    
    async def analyze_tier_batch(
        self, 
        props: List[Dict[str, Any]], 
        tier_name: str
    ) -> List[Dict[str, Any]]:
        """
        Analyze ALL props for a tier in ONE Gemini API call.
        
        This is the main entry point - processes entire tier at once.
        Returns props enriched with all Vision Intel fields.
        """
        if not props:
            return []
        
        logger.info(f"[VISION INTEL] Batch analyzing {len(props)} props for {tier_name}")
        
        if not self.enabled or not self.client:
            logger.warning(f"[VISION INTEL] Service disabled - using fallback for {tier_name}")
            return [self._enrich_with_fallback(prop) for prop in props]
        
        try:
            # Build the batch prompt
            prompt = self._build_batch_prompt(props, tier_name)
            full_prompt = f"{VISION_INTEL_BATCH_PROMPT}\n\n{prompt}"
            
            # Debug: Log first part of prompt to verify L3 is not included
            logger.info(f"[VISION INTEL] Sending {len(props)} props to Gemini for {tier_name}")
            logger.debug(f"[VISION INTEL] Sample prop data keys: {list(props[0].keys()) if props else 'N/A'}")
            
            # Make ONE API call for the entire tier
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.client.models.generate_content(
                    model=self.model_name,
                    contents=full_prompt
                )
            )
            
            # Debug: Log raw response to see if Gemini is hallucinating
            logger.info(f"[VISION INTEL] Gemini response length: {len(response.text)} chars")
            
            # Parse the batch response
            intel_map = self._parse_batch_response(response.text, props)
            
            # Enrich each prop with its intel
            enriched_props = []
            for prop in props:
                prop_id = f"{prop.get('player_name')}_{prop.get('stat_type')}_{prop.get('line')}"
                intel = intel_map.get(prop_id, {})
                enriched_props.append(self._merge_intel_to_prop(prop, intel))
            
            # Sort by composite score
            enriched_props.sort(key=lambda x: x.get('composite_score', 0), reverse=True)
            
            logger.info(f"[VISION INTEL] Batch complete for {tier_name}: {len(enriched_props)} props enriched")
            return enriched_props
            
        except Exception as e:
            logger.error(f"[VISION INTEL] Batch analysis failed for {tier_name}: {e}")
            return [self._enrich_with_fallback(prop) for prop in props]
    
    def _parse_batch_response(self, response: str, props: List[Dict]) -> Dict[str, Dict]:
        """Parse the batch JSON response from Gemini."""
        intel_map = {}
        
        try:
            # Clean response
            cleaned = response.strip()
            
            # Remove markdown code blocks if present
            if cleaned.startswith('```'):
                lines = cleaned.split('\n')
                lines = [l for l in lines if not l.startswith('```')]
                cleaned = '\n'.join(lines)
            
            # Find JSON array
            if '[' in cleaned:
                start = cleaned.index('[')
                end = cleaned.rindex(']') + 1
                cleaned = cleaned[start:end]
            
            results = json.loads(cleaned)
            
            if not isinstance(results, list):
                results = [results]
            
            for item in results:
                prop_id = item.get('prop_id', '')
                if prop_id:
                    intel_map[prop_id] = {
                        'intel_score': max(1, min(10, int(item.get('intel_score', 5)))),
                        'intel_verdict': item.get('verdict', 'VALUE'),
                        'vision_intel': item.get('vision_intel_summary', ''),
                        'intel_risk': item.get('risk_factor', 'Medium'),
                        'adjusted_confidence': float(item.get('adjusted_confidence', 0.5))
                    }
            
            logger.info(f"[VISION INTEL] Parsed {len(intel_map)} intel responses")
            
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"[VISION INTEL] Failed to parse batch response: {e}")
            # Return empty map, will use fallbacks
        
        return intel_map
    
    def _merge_intel_to_prop(self, prop: Dict, intel: Dict) -> Dict:
        """Merge Vision Intel data into a prop."""
        enriched = {**prop}
        
        if intel:
            enriched['vision_intel'] = intel.get('vision_intel', '')
            enriched['intel_score'] = intel.get('intel_score', 5)
            enriched['intel_verdict'] = intel.get('intel_verdict', 'VALUE')
            enriched['intel_risk'] = intel.get('intel_risk', 'Medium')
            enriched['adjusted_confidence'] = intel.get('adjusted_confidence', 0.5)
            
            # Also set vision_summary for backward compatibility
            enriched['vision_summary'] = intel.get('vision_intel', '')
        else:
            # Use fallback
            fallback = self._generate_fallback_intel(prop)
            enriched['vision_intel'] = fallback['vision_intel']
            enriched['intel_score'] = fallback['intel_score']
            enriched['intel_verdict'] = fallback['intel_verdict']
            enriched['intel_risk'] = fallback['intel_risk']
            enriched['adjusted_confidence'] = fallback['adjusted_confidence']
            enriched['vision_summary'] = fallback['vision_intel']
        
        # =================================================================
        # INTELLIGENCE GATING: Gemini verdicts affect prop qualification
        # =================================================================
        intel_score = enriched.get('intel_score', 5)
        intel_verdict = enriched.get('intel_verdict', 'VALUE')
        adjusted_confidence = enriched.get('adjusted_confidence', 0.5)
        
        # TRAP verdict = KILL the prop (mark for removal)
        if intel_verdict == 'TRAP':
            enriched['gemini_killed'] = True
            enriched['gemini_kill_reason'] = f"TRAP verdict (intel_score: {intel_score}/10, confidence: {adjusted_confidence:.0%})"
            enriched['composite_score'] = 0  # Zero out to sort to bottom
            logger.info(f"[VISION INTEL] KILLED: {prop.get('player_name')} {prop.get('stat_type')} - TRAP verdict")
        
        # Low intel score = KILL (Gemini sees red flags)
        elif intel_score <= 3:
            enriched['gemini_killed'] = True
            enriched['gemini_kill_reason'] = f"Low intel score ({intel_score}/10) - context undermines math"
            enriched['composite_score'] = 0
            logger.info(f"[VISION INTEL] KILLED: {prop.get('player_name')} {prop.get('stat_type')} - intel score {intel_score}/10")
        
        # Low adjusted confidence = KILL
        elif adjusted_confidence < 0.45:
            enriched['gemini_killed'] = True
            enriched['gemini_kill_reason'] = f"Low adjusted confidence ({adjusted_confidence:.0%})"
            enriched['composite_score'] = 0
            logger.info(f"[VISION INTEL] KILLED: {prop.get('player_name')} {prop.get('stat_type')} - confidence {adjusted_confidence:.0%}")
        
        else:
            # PASSED Gemini gate - use adjusted_confidence as composite score
            enriched['gemini_killed'] = False
            
            # Composite Score = Gemini's adjusted_confidence (already factors in VK + context)
            # Scale to 0-100 and apply CHALK boost
            verdict_multiplier = 1.05 if intel_verdict == 'CHALK' else 1.0
            enriched['composite_score'] = round(adjusted_confidence * 100 * verdict_multiplier, 1)
            
            # Cap at 99
            if enriched['composite_score'] > 99:
                enriched['composite_score'] = 99.0
        
        return enriched
    
    def _enrich_with_fallback(self, prop: Dict) -> Dict:
        """Enrich a prop with fallback intel when Gemini is unavailable."""
        fallback = self._generate_fallback_intel(prop)
        return self._merge_intel_to_prop(prop, fallback)
    
    def _generate_fallback_intel(self, prop: Dict) -> Dict:
        """Generate fallback intel without Gemini."""
        vk_prob = prop.get('vk_prob_over', 50)
        vk_edge = prop.get('vk_edge', 0)
        h20_rate = prop.get('h20_rate', 50)
        h10_rate = prop.get('h10_rate', 50)
        
        player = prop.get('player_name', 'Player')
        stat = prop.get('stat_type', 'stat')
        line = prop.get('line', 0)
        
        # Calculate intel score based on available data
        score = 5
        if vk_prob >= 70: score += 2
        if vk_edge >= 5: score += 1
        if h20_rate >= 80: score += 1
        if h10_rate >= 90: score += 1
        if prop.get('cv', 1) <= 0.25: score += 1
        score = max(1, min(10, score))
        
        # Calculate adjusted confidence (0-1)
        adjusted_confidence = (vk_prob / 100 * 0.6) + (score / 10 * 0.4)
        
        # Determine verdict
        if vk_prob >= 75 and h20_rate >= 80:
            verdict = "CHALK"
            risk = "Low"
            summary = f"{player} hitting {h20_rate:.0f}% L20 on {stat}. Line at {line} is well within range. Lock it."
        elif vk_edge < 0 or h20_rate < 60:
            verdict = "TRAP"
            risk = "High"
            summary = f"Caution on {player} {stat} @ {line}. Numbers look marginal - consider passing."
            adjusted_confidence = 0.35  # Force low confidence for traps
        else:
            verdict = "VALUE"
            risk = "Medium"
            summary = f"{player} {stat} @ {line} shows value. {vk_prob:.0f}% model probability with solid recent form."
        
        return {
            'vision_intel': summary,
            'intel_score': score,
            'intel_verdict': verdict,
            'intel_risk': risk,
            'adjusted_confidence': round(adjusted_confidence, 2)
        }
    
    # Legacy method for backward compatibility
    async def analyze_tier_props(
        self, 
        props: List[Dict[str, Any]], 
        tier_name: str,
        max_concurrent: int = 3  # Ignored - we do batch
    ) -> List[Dict[str, Any]]:
        """
        Legacy method - redirects to batch analysis.
        """
        return await self.analyze_tier_batch(props, tier_name)
    
    async def analyze_prop(self, prop: Dict[str, Any], situational_intel: str = "") -> Dict[str, Any]:
        """
        Analyze a single prop (for testing or one-off analysis).
        For production, use analyze_tier_batch instead.
        """
        results = await self.analyze_tier_batch([prop], "single")
        return results[0] if results else self._enrich_with_fallback(prop)


# Singleton instance
_vision_intel_service: Optional[VisionIntelService] = None

def get_vision_intel_service() -> VisionIntelService:
    """Get or create the Vision Intel service singleton."""
    global _vision_intel_service
    if _vision_intel_service is None:
        _vision_intel_service = VisionIntelService()
    return _vision_intel_service
