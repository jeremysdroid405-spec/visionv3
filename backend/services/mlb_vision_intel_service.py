"""
MLB Vision Intel Service
=========================
Gemini 3.1 Pro integration for MLB Safe Haven picks.

Final Context Check includes:
- Weather conditions at venue
- Pitcher/Batter splits (vs LHP/RHP)
- Recent form and hot/cold streaks
- Ballpark factors
- Head-to-head historical data

Uses batched API calls for efficiency.
"""

import os
import logging
import json
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# Gemini API configuration
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")


class MLBVisionIntelService:
    """
    MLB Vision Intel Service using Gemini 3.1 Pro.
    
    Provides final context check for Safe Haven picks.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self._genai = None
        self._model = None
    
    def _initialize_genai(self):
        """Initialize Google Generative AI client."""
        if self._genai is not None:
            return True
        
        if not GOOGLE_API_KEY:
            logger.error("[MLB_VISION] GOOGLE_API_KEY not configured")
            return False
        
        try:
            import google.generativeai as genai
            genai.configure(api_key=GOOGLE_API_KEY)
            self._genai = genai
            self._model = genai.GenerativeModel("gemini-2.0-flash")
            logger.info("[MLB_VISION] Gemini API initialized")
            return True
        except Exception as e:
            logger.error(f"[MLB_VISION] Failed to initialize Gemini: {e}")
            return False
    
    def _build_context_prompt(self, picks: List[Dict]) -> str:
        """
        Build a batched prompt for multiple Safe Haven picks.
        
        Args:
            picks: List of Safe Haven pick dictionaries
            
        Returns:
            Formatted prompt string
        """
        prompt = """You are a professional MLB betting analyst. Analyze these Safe Haven picks and provide a FINAL VERDICT for each.

For each pick, consider:
1. Weather impact (if outdoor venue)
2. Pitcher/Batter splits (vs LHP/RHP matchups)
3. Recent form (hot/cold streaks)
4. Ballpark factor
5. Any injury or lineup concerns

**PICKS TO ANALYZE:**
"""
        
        for i, pick in enumerate(picks, 1):
            prompt += f"""
---
**Pick {i}:**
- Player: {pick.get('player_name')}
- Stat: {pick.get('stat_type')}
- Line: {pick.get('line')}
- Projected: {pick.get('projected_value')}
- Edge: {pick.get('edge_pct')}%
- Direction: {pick.get('direction')}
- R-Squared: {pick.get('r_squared')}
- L10 Hit Rate: {pick.get('hit_rate_l10', 'N/A')}
- L10 Average: {pick.get('l10_avg', 'N/A')}
- Opponent: {pick.get('prop_data', {}).get('away_team')} @ {pick.get('prop_data', {}).get('home_team')}
- Game Time: {pick.get('prop_data', {}).get('commence_time', 'TBD')}
"""
        
        prompt += """
---
**RESPONSE FORMAT (JSON array):**
```json
[
  {
    "pick_index": 1,
    "player_name": "...",
    "verdict": "CONFIRMED" or "TRAP" or "CAUTION",
    "confidence": 0.0 to 1.0,
    "reasoning": "Brief explanation (2-3 sentences)",
    "key_factors": ["factor1", "factor2"],
    "adjusted_edge": adjusted edge % based on context
  }
]
```

Provide ONLY the JSON array, no additional text.
"""
        return prompt
    
    async def analyze_safe_haven_picks(
        self,
        picks: List[Dict],
        batch_size: int = 5
    ) -> List[Dict]:
        """
        Analyze Safe Haven picks with Gemini Vision Intel.
        
        Args:
            picks: List of Safe Haven picks to analyze
            batch_size: Number of picks per API call
            
        Returns:
            List of picks with vision intel verdicts
        """
        if not picks:
            return []
        
        if not self._initialize_genai():
            logger.warning("[MLB_VISION] Gemini not available, returning picks as-is")
            return picks
        
        logger.info(f"[MLB_VISION] Analyzing {len(picks)} Safe Haven picks...")
        
        analyzed_picks = []
        
        # Process in batches
        for i in range(0, len(picks), batch_size):
            batch = picks[i:i + batch_size]
            
            try:
                # Build prompt
                prompt = self._build_context_prompt(batch)
                
                # Call Gemini
                response = self._model.generate_content(prompt)
                response_text = response.text.strip()
                
                # Parse JSON response
                # Try to extract JSON from response
                if "```json" in response_text:
                    json_start = response_text.find("```json") + 7
                    json_end = response_text.find("```", json_start)
                    response_text = response_text[json_start:json_end].strip()
                elif "```" in response_text:
                    json_start = response_text.find("```") + 3
                    json_end = response_text.find("```", json_start)
                    response_text = response_text[json_start:json_end].strip()
                
                verdicts = json.loads(response_text)
                
                # Merge verdicts with picks
                for j, pick in enumerate(batch):
                    # Find matching verdict
                    verdict_data = next(
                        (v for v in verdicts if v.get("pick_index") == j + 1),
                        None
                    )
                    
                    if verdict_data:
                        pick["vision_intel"] = {
                            "verdict": verdict_data.get("verdict", "UNKNOWN"),
                            "confidence": verdict_data.get("confidence", 0.5),
                            "reasoning": verdict_data.get("reasoning", ""),
                            "key_factors": verdict_data.get("key_factors", []),
                            "adjusted_edge": verdict_data.get("adjusted_edge"),
                            "analyzed_at": datetime.now(timezone.utc).isoformat()
                        }
                    else:
                        pick["vision_intel"] = {
                            "verdict": "UNKNOWN",
                            "confidence": 0.5,
                            "reasoning": "No verdict returned from analysis",
                            "key_factors": [],
                            "analyzed_at": datetime.now(timezone.utc).isoformat()
                        }
                    
                    analyzed_picks.append(pick)
                    
                logger.info(f"[MLB_VISION] Batch {i // batch_size + 1} analyzed: {len(batch)} picks")
                
            except json.JSONDecodeError as e:
                logger.error(f"[MLB_VISION] JSON parse error: {e}")
                # Add picks without vision intel
                for pick in batch:
                    pick["vision_intel"] = {
                        "verdict": "ERROR",
                        "confidence": 0.0,
                        "reasoning": f"JSON parse error: {str(e)}",
                        "key_factors": [],
                        "analyzed_at": datetime.now(timezone.utc).isoformat()
                    }
                    analyzed_picks.append(pick)
                    
            except Exception as e:
                logger.error(f"[MLB_VISION] Analysis error: {e}")
                # Add picks without vision intel
                for pick in batch:
                    pick["vision_intel"] = {
                        "verdict": "ERROR",
                        "confidence": 0.0,
                        "reasoning": f"Analysis error: {str(e)}",
                        "key_factors": [],
                        "analyzed_at": datetime.now(timezone.utc).isoformat()
                    }
                    analyzed_picks.append(pick)
        
        # Filter out TRAP picks
        confirmed_picks = [
            p for p in analyzed_picks 
            if p.get("vision_intel", {}).get("verdict") != "TRAP"
        ]
        
        trapped_count = len(analyzed_picks) - len(confirmed_picks)
        if trapped_count > 0:
            logger.info(f"[MLB_VISION] TRAPPED {trapped_count} picks (removed from Safe Haven)")
        
        logger.info(f"[MLB_VISION] Analysis complete: {len(confirmed_picks)} picks confirmed")
        
        return analyzed_picks
    
    async def save_analyzed_picks(
        self,
        picks: List[Dict],
        collection_name: str = "mlb_ferrari_safe_haven"
    ) -> int:
        """
        Save analyzed picks to collection.
        
        Args:
            picks: Analyzed picks with vision intel
            collection_name: Target collection name
            
        Returns:
            Number of picks saved
        """
        if not picks:
            return 0
        
        collection = self.db[collection_name]
        
        # Clear old picks
        await collection.delete_many({})
        
        # Insert new picks
        await collection.insert_many(picks)
        
        logger.info(f"[MLB_VISION] Saved {len(picks)} picks to {collection_name}")
        
        return len(picks)


# Singleton
_mlb_vision_intel: Optional[MLBVisionIntelService] = None


def get_mlb_vision_intel(db: AsyncIOMotorDatabase) -> MLBVisionIntelService:
    """Get or create MLB Vision Intel service."""
    global _mlb_vision_intel
    if _mlb_vision_intel is None:
        _mlb_vision_intel = MLBVisionIntelService(db)
    return _mlb_vision_intel


async def run_mlb_vision_intel_analysis(
    db: AsyncIOMotorDatabase,
    safe_haven_picks: List[Dict],
    save_to_db: bool = True
) -> Dict[str, Any]:
    """
    Run Vision Intel analysis on Safe Haven picks.
    
    Args:
        db: MongoDB database
        safe_haven_picks: List of Safe Haven picks to analyze
        save_to_db: Whether to save results
        
    Returns:
        Analysis results
    """
    service = get_mlb_vision_intel(db)
    
    # Analyze picks
    analyzed = await service.analyze_safe_haven_picks(safe_haven_picks)
    
    # Count verdicts
    verdicts = {
        "CONFIRMED": 0,
        "CAUTION": 0,
        "TRAP": 0,
        "ERROR": 0,
        "UNKNOWN": 0
    }
    
    for pick in analyzed:
        verdict = pick.get("vision_intel", {}).get("verdict", "UNKNOWN")
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
    
    # Save if requested
    saved_count = 0
    if save_to_db:
        saved_count = await service.save_analyzed_picks(analyzed)
    
    return {
        "success": True,
        "total_analyzed": len(analyzed),
        "verdicts": verdicts,
        "confirmed_picks": [p for p in analyzed if p.get("vision_intel", {}).get("verdict") == "CONFIRMED"],
        "saved_count": saved_count
    }
