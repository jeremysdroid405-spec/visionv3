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
You are the **PropVision Intelligence Engine** - a sharp sports betting analyst. You evaluate pre-qualified NBA player props by combining statistical models with real-world context.

## Your Task
Analyze each prop in the batch and return insights. You'll receive props that have ALREADY passed mathematical qualification (3-Gate system). Your job is to:
1. Validate if the math makes sense given matchup/situational context
2. Identify any red flags or hidden value
3. Generate a sharp, concise analysis

## Output Format
Return a JSON array with one object per prop. Each object MUST have:
```json
{
  "prop_id": "PlayerName_STAT_Line",
  "intel_score": 1-10,
  "verdict": "CHALK" | "TRAP" | "VALUE",
  "vision_summary": "2-3 sentence analysis like texting a sharp friend",
  "risk_factor": "Low" | "Medium" | "High",
  "key_factor": "One phrase explaining the main edge or risk",
  "matchup_note": "Brief matchup context"
}
```

## Verdict Definitions
- **CHALK**: Lock it in. Strong numbers + favorable situation. High confidence.
- **VALUE**: Good edge but some variance. Worth the play at the right price.
- **TRAP**: Numbers look good but context suggests caution. Proceed carefully.

## Style Guide
- Be conversational, like a sharp bettor texting picks
- Lead with the numbers (hit rate, average vs line)
- Call out defensive matchups explicitly
- Flag any blowout risk or injury concerns
- Keep each summary to 2-3 punchy sentences

IMPORTANT: Return ONLY valid JSON array. No markdown, no code blocks, no explanations outside the JSON."""


class VisionIntelService:
    """
    Unified Gemini intelligence layer for PropVision.
    ALL AI analysis happens here - no other service should call Gemini.
    """
    
    def __init__(self):
        self.api_key = os.environ.get('GOOGLE_API_KEY')
        self.enabled = GEMINI_AVAILABLE and self.api_key is not None
        self.client = None
        self.model_name = 'gemini-3.1-pro-preview'
        
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
            
            # Matchup
            opponent = prop.get('opponent', 'TBD')
            dvp_rank = prop.get('dvp_rank')
            
            # Build matchup context
            if dvp_rank:
                if dvp_rank <= 5:
                    dvp_text = f"ELITE D (#{dvp_rank})"
                elif dvp_rank <= 10:
                    dvp_text = f"Strong D (#{dvp_rank})"
                elif dvp_rank <= 20:
                    dvp_text = f"Average D (#{dvp_rank})"
                else:
                    dvp_text = f"Weak D (#{dvp_rank})"
            else:
                dvp_text = "Unknown"
            
            # Badges/situational factors
            badges = prop.get('active_badges', [])
            badge_text = ", ".join([b.get('badge_key', b) if isinstance(b, dict) else str(b) for b in badges[:3]]) if badges else "None"
            
            prop_data = {
                "prop_id": f"{player_name}_{stat_type}_{line}",
                "player": player_name,
                "stat": stat_type,
                "line": line,
                "type": pick_type,
                "vk_proj": round(vk_predicted, 1) if vk_predicted else 0,
                "vk_prob": round(vk_prob, 0) if vk_prob else 50,
                "vk_edge": round(vk_edge, 1) if vk_edge else 0,
                "h20_rate": round(h20_rate, 0) if h20_rate else 0,
                "h10_rate": round(h10_rate, 0) if h10_rate else 0,
                "l5_avg": round(l5_avg, 1) if l5_avg else 0,
                "season_avg": round(season_avg, 1) if season_avg else 0,
                "cv": round(cv, 2) if cv else 0,
                "opponent": opponent,
                "defense": dvp_text,
                "dk_odds": dk_odds,
                "badges": badge_text
            }
            props_data.append(prop_data)
        
        prompt = f"""## {tier_name.upper()} TIER - {len(props)} Props to Analyze

Analyze these {tier_name} picks. All have passed the 3-Gate qualification system.

PROPS DATA:
{json.dumps(props_data, indent=2)}

Return your analysis as a JSON array with one object per prop. Include all required fields."""
        
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
                        'vision_intel': item.get('vision_summary', ''),
                        'intel_risk': item.get('risk_factor', 'Medium'),
                        'key_factor': item.get('key_factor', ''),
                        'matchup_note': item.get('matchup_note', '')
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
            enriched['key_factor'] = intel.get('key_factor', '')
            enriched['matchup_note'] = intel.get('matchup_note', '')
            
            # Also set vision_summary for backward compatibility
            enriched['vision_summary'] = intel.get('vision_intel', '')
        else:
            # Use fallback
            fallback = self._generate_fallback_intel(prop)
            enriched['vision_intel'] = fallback['vision_intel']
            enriched['intel_score'] = fallback['intel_score']
            enriched['intel_verdict'] = fallback['intel_verdict']
            enriched['intel_risk'] = fallback['intel_risk']
            enriched['vision_summary'] = fallback['vision_intel']
        
        # Calculate composite score
        vk_prob = prop.get('vk_prob_over', 50) / 100
        intel_score = enriched.get('intel_score', 5) / 10
        enriched['composite_score'] = round((vk_prob * 0.7 + intel_score * 0.3) * 100, 1)
        
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
        
        # Determine verdict
        if vk_prob >= 75 and h20_rate >= 80:
            verdict = "CHALK"
            risk = "Low"
            summary = f"{player} hitting {h20_rate:.0f}% L20 on {stat}. Line at {line} is well within range. Lock it."
        elif vk_edge < 0 or h20_rate < 60:
            verdict = "TRAP"
            risk = "High"
            summary = f"Caution on {player} {stat} @ {line}. Numbers look marginal - consider passing."
        else:
            verdict = "VALUE"
            risk = "Medium"
            summary = f"{player} {stat} @ {line} shows value. {vk_prob:.0f}% model probability with solid recent form."
        
        return {
            'vision_intel': summary,
            'intel_score': score,
            'intel_verdict': verdict,
            'intel_risk': risk,
            'key_factor': f"{h20_rate:.0f}% L20 hit rate",
            'matchup_note': prop.get('opponent', 'TBD')
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
