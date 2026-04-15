"""
mlb_vision_intel.py
=======================
MLB PropVision Intelligence Engine - Gemini 3.1 Pro Integration (1:1 NBA Clone)

This is the SINGLE source for ALL Gemini AI calls for MLB.
Same architecture as NBA Vision Intel:
1. Vision Intel Summary (ai_summary)
2. Intelligence Score (intel_score) - 1-10
3. Verdict (intel_verdict) - CHALK | TRAP | VALUE
4. Risk Factor (intel_risk) - Low | Medium | High
5. Composite Score
6. Badge Analysis
7. Matchup Insight
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
    logger.warning("google-genai not available - MLB Vision Intel will use fallback")
    GEMINI_AVAILABLE = False


# MLB-SPECIFIC System prompt for batch analysis
MLB_VISION_INTEL_BATCH_PROMPT = """## Role
You are the Lead MLB Scout for PropVision. Your job is to write a gritty, 2-to-3 sentence scouting report explaining to a bettor why we are locking in this specific prop line.

## Tone
Speak like a human sharp. Use baseball betting slang (e.g., 'smash spot', 'fade', 'trap', 'riding the hot hand', 'terrible bullpen', 'gas can', 'meat on the mound', 'printing money', 'soft landing', 'volume play'). DO NOT sound like a robot reading a spreadsheet.

## The Data Translation Key (CRITICAL)
You will be provided with math variables. You must weave these into a narrative, not just list the numbers.

**matchup_multiplier** (DVP/pitcher matchup):
- High (>1.1): Talk about how the opposing pitcher or bullpen is a great matchup to exploit. Use "smash spot", "soft landing", "gas can on the mound".
- Low (<0.9): Mention tough pitching, ace on the mound, or "fade" territory.

**tempo_multiplier** (plate appearance volume):
- High (>1.05): Talk about 'volume'—meaning the batter will get plenty of plate appearances today. Mention lineup spot, team pace, or "ABs will pile up".
- Low (<0.95): Mention limited PAs, bad lineup spot, or "home team 9th inning risk".

**vk_edge** (projection vs line cushion):
- High (>0.5): Mention massive cushion over the book's line. Use "the line is disrespectful", "free money", "book is sleeping".
- Moderate (0.2-0.5): Mention "comfortable edge", "solid value", "math works".
- Low (<0.2): Be cautious, mention "thin edge", "need the situation to hit".

**h10** (hit rate last 10 games):
- High (>70%): Talk about "riding the hot hand", "locked in", "can't miss right now".
- Low (<50%): Mention struggles, cold streak, or "due for regression".

**is_goblin**: This is a safe play - heavily juiced favorite. Mention "chalky for a reason" or "safe haven".
**is_demon**: This is a ceiling play - high risk, high reward. Mention "boom or bust", "ceiling play", "when it hits, it pays".

## Output Format (Strict JSON Array)
Return a JSON array with one object per prop:
[
  {
    "prop_id": "PlayerName_STAT_Line",
    "intel_score": 8,
    "verdict": "CHALK",
    "vision_intel_summary": "Your 2-3 sentence gritty scouting report here.",
    "risk_factor": "Low",
    "adjusted_confidence": 0.85
  }
]

## Scoring Guidelines
- **intel_score 8-10**: Elite spot. All factors align. CHALK it and don't look back.
- **intel_score 6-7**: Solid play. Edge is real but not a layup. VALUE.
- **intel_score 4-5**: Marginal. Thin edge, need things to break right. Lean but watch it.
- **intel_score 1-3**: TRAP. Numbers might look good but something stinks. Fade it.

## Risk Assessment
- **Low**: Perfect storm - weak pitcher, volume, cushion. Lock it.
- **Medium**: Edge exists but one factor is sus.
- **High**: Red flags. The math says yes but your gut says no.

IMPORTANT: Return ONLY the JSON array. No markdown, no code blocks, no extra text."""


class MLBVisionIntel:
    """
    Unified Gemini intelligence layer for PropVision.
    ALL AI analysis happens here - no other service should call Gemini.
    """
    
    def __init__(self):
        self.api_key = os.environ.get('GEMINI_API_KEY')
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
            
            # L3 hit rate (most recent form)
            l3_rate = prop.get('h3_rate', prop.get('l3_rate', 0))
            
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
                "l3_rate": round(l3_rate, 0) if l3_rate else 0,  # Most recent form
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
            full_prompt = f"{MLB_VISION_INTEL_BATCH_PROMPT}\n\n{prompt}"
            
            # Make ONE API call for the entire tier
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.client.models.generate_content(
                    model=self.model_name,
                    contents=full_prompt
                )
            )
            
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
_mlb_vision_intel_instance: Optional[MLBVisionIntel] = None

def get_mlb_vision_intel() -> MLBVisionIntel:
    """Get or create the MLB Vision Intel service singleton."""
    global _mlb_vision_intel_instance
    if _mlb_vision_intel_instance is None:
        _mlb_vision_intel_instance = MLBVisionIntel()
    return _mlb_vision_intel_instance

