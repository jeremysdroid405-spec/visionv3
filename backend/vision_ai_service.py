"""
Vision AI Service - Generates "badass" AI insights for NBA player props
Uses Claude Sonnet 4.5 via Emergent LLM integration

This module provides:
1. AI insight generation for Demons, Goblins, and High Volatility players
2. Integration with daily_insights MongoDB collection
3. Cost-efficient batch processing
"""

import os
import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

from emergentintegrations.llm.chat import LlmChat, UserMessage

logger = logging.getLogger(__name__)

# Configuration
EMERGENT_LLM_KEY = os.environ.get('EMERGENT_LLM_KEY')
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_SERVICE_ROLE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')


class VisionAIService:
    """
    The AI "Oracle" - Generates sharp, aggressive sports betting insights
    using Claude Sonnet 4.5 via Emergent integration.
    """
    
    SYSTEM_PROMPT = """You are a sharp, aggressive sports betting analyst for an elite 2026 app called "Demon & Goblin."
Analyze player prop data and provide a 1-sentence "badass" insight.
Do not use filler words. Focus on why the "future" favors this bet.
Use a punchy, high-tech tone. Be prophetic and confident.
Never mention uncertainty. Speak as if you've seen the future.
Maximum 25 words per insight."""

    def __init__(self, db):
        """Initialize with MongoDB database connection."""
        self.db = db
        self.daily_insights = db.dg_daily_insights
        self.cached_board = db.dg_cached_board
        
        if not EMERGENT_LLM_KEY:
            logger.warning("[VISION] EMERGENT_LLM_KEY not found - AI insights will be disabled")
        
    async def generate_single_insight(
        self,
        player_name: str,
        stat_type: str,
        current_line: float,
        l10_rate: float,
        pace_factor: float = 1.0,
        fatigue: str = "Normal",
        usage_bump: float = 0,
        volatility: str = "Med",
        is_demon: bool = False,
        is_goblin: bool = False,
        projected_score: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Generate a single AI insight for a player prop.
        
        Args:
            player_name: Player's full name
            stat_type: Prop type (points, rebounds, assists, etc.)
            current_line: The betting line
            l10_rate: Last 10 games hit rate (0-100)
            pace_factor: Pace adjustment multiplier
            fatigue: "Fresh", "Normal", or "Fatigued"
            usage_bump: Usage increase percentage due to teammate injuries
            volatility: "Low", "Med", or "High"
            is_demon: Whether this is a Demon pick
            is_goblin: Whether this is a Goblin pick
            projected_score: AI's projected score (for discrepancy detection)
            
        Returns:
            Dict with success status and generated insight
        """
        if not EMERGENT_LLM_KEY:
            return {
                "success": False,
                "error": "EMERGENT_LLM_KEY not configured",
                "insight": None
            }
        
        try:
            # Determine player classification
            player_type = "DEMON (High Payout)" if is_demon else "GOBLIN (High Safety)" if is_goblin else "Standard"
            
            # Check for discrepancy edge (>15% difference)
            discrepancy_note = ""
            if projected_score and current_line > 0:
                discrepancy_pct = abs((projected_score - current_line) / current_line) * 100
                if discrepancy_pct > 15:
                    direction = "OVER" if projected_score > current_line else "UNDER"
                    discrepancy_note = f"\nCRITICAL EDGE: Model projects {projected_score:.1f} vs line {current_line}. {discrepancy_pct:.0f}% discrepancy favors {direction}. Mention this edge."
            
            # Build the prompt
            user_prompt = f"""
PLAYER: {player_name}
PROP TYPE: {stat_type}
CLASSIFICATION: {player_type}
LINE: {current_line}
L10 HIT RATE: {l10_rate}%
ADVANCED ANALYTICS:
- Pace Adjustment: {'+' if pace_factor > 1 else ''}{((pace_factor - 1) * 100):.0f}%
- Fatigue Status: {fatigue}
- Usage Bump: {'+' if usage_bump > 0 else ''}{usage_bump:.0f}%
- Volatility Risk: {volatility}
{discrepancy_note}

Generate a 1-sentence badass insight for a pro bettor. Why does the data favor this outcome?"""

            # Initialize Claude Sonnet 4.5 via Emergent
            chat = LlmChat(
                api_key=EMERGENT_LLM_KEY,
                session_id=f"vision-{player_name.replace(' ', '-')}-{datetime.now().timestamp()}",
                system_message=self.SYSTEM_PROMPT
            ).with_model("anthropic", "claude-sonnet-4-5-20250929")
            
            # Send message and get response
            message = UserMessage(text=user_prompt)
            insight = await chat.send_message(message)
            
            # Clean up the insight
            insight = insight.strip().strip('"').strip("'")
            
            logger.info(f"[VISION] Generated insight for {player_name}: {insight[:50]}...")
            
            return {
                "success": True,
                "player": player_name,
                "insight": insight,
                "classification": player_type,
                "generated_at": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"[VISION] Error generating insight for {player_name}: {e}")
            return {
                "success": False,
                "player": player_name,
                "error": str(e),
                "insight": None
            }
    
    async def generate_batch_insights(
        self,
        players: List[Dict[str, Any]],
        max_concurrent: int = 3,
        delay_between: float = 0.5
    ) -> Dict[str, Any]:
        """
        Generate AI insights for a batch of players.
        Only processes Demons, Goblins, and High Volatility players.
        
        Args:
            players: List of player data dicts
            max_concurrent: Max concurrent API calls
            delay_between: Delay between batches (rate limiting)
            
        Returns:
            Summary of batch processing results
        """
        if not EMERGENT_LLM_KEY:
            return {
                "success": False,
                "error": "EMERGENT_LLM_KEY not configured",
                "insights_generated": 0
            }
        
        # Filter for eligible players (Demons, Goblins, High Volatility)
        eligible_players = [
            p for p in players 
            if p.get('is_demon') or p.get('is_goblin') or p.get('volatility_score') == 'High'
        ]
        
        logger.info(f"[VISION] Processing {len(eligible_players)} eligible players out of {len(players)} total")
        
        results = []
        errors = []
        
        # Process in batches to respect rate limits
        for i in range(0, len(eligible_players), max_concurrent):
            batch = eligible_players[i:i + max_concurrent]
            
            tasks = []
            for player in batch:
                task = self.generate_single_insight(
                    player_name=player.get('player_name', 'Unknown'),
                    stat_type=player.get('stat_type', 'points'),
                    current_line=player.get('line', 0),
                    l10_rate=player.get('l10_hit_rate', 50),
                    pace_factor=player.get('pace_adjustment_factor', 1.0),
                    fatigue="Fatigued" if player.get('is_back_to_back') else "Normal",
                    usage_bump=player.get('usage_bump_percent', 0),
                    volatility=player.get('volatility_score', 'Med'),
                    is_demon=player.get('is_demon', False),
                    is_goblin=player.get('is_goblin', False)
                )
                tasks.append(task)
            
            batch_results = await asyncio.gather(*tasks)
            
            for result in batch_results:
                if result.get('success'):
                    results.append(result)
                    # Update MongoDB
                    await self._save_insight_to_db(result)
                else:
                    errors.append(result)
            
            # Rate limiting delay
            if i + max_concurrent < len(eligible_players):
                await asyncio.sleep(delay_between)
        
        return {
            "success": True,
            "insights_generated": len(results),
            "errors_count": len(errors),
            "eligible_players": len(eligible_players),
            "total_players": len(players),
            "results": results[:5],  # Return first 5 as sample
            "errors": errors[:3] if errors else []
        }
    
    async def _save_insight_to_db(self, result: Dict[str, Any]) -> None:
        """Save generated insight to MongoDB daily_insights collection."""
        if not result.get('success') or not result.get('insight'):
            return
        
        try:
            await self.daily_insights.update_one(
                {"player_name": result['player']},
                {
                    "$set": {
                        "insight_summary": result['insight'],
                        "ai_generated_at": result['generated_at'],
                        "ai_model": "claude-sonnet-4.5",
                        "classification": result.get('classification', 'Standard')
                    }
                },
                upsert=False  # Only update existing records
            )
        except Exception as e:
            logger.error(f"[VISION] Failed to save insight to DB: {e}")
    
    async def trigger_insights_for_sync(self) -> Dict[str, Any]:
        """
        Trigger AI insight generation for all eligible players in the current sync.
        Called after daily data sync completes.
        """
        logger.info("[VISION] Starting AI insight generation for daily sync...")
        
        # Get all players with insights data
        cursor = self.daily_insights.find({}, {"_id": 0})
        players = await cursor.to_list(length=500)
        
        if not players:
            return {
                "success": False,
                "message": "No players found in daily_insights collection"
            }
        
        # Enrich with demon/goblin status from cached_board
        enriched_players = []
        for player in players:
            player_name = player.get('player_name')
            
            # Get demon/goblin status from cached board
            cached = await self.cached_board.find_one(
                {"player_name": player_name},
                {"_id": 0, "props": 1}
            )
            
            if cached and cached.get('props'):
                # Check if player has any demon or goblin props
                has_demon = any(
                    p.get('value_score', 0) > 1.3 
                    for p in cached['props'] if isinstance(p, dict)
                )
                has_goblin = any(
                    p.get('hit_rate_l10', 0) >= 70 
                    for p in cached['props'] if isinstance(p, dict)
                )
                
                player['is_demon'] = has_demon
                player['is_goblin'] = has_goblin
            
            enriched_players.append(player)
        
        # Generate insights
        result = await self.generate_batch_insights(enriched_players)
        
        logger.info(f"[VISION] Completed: {result.get('insights_generated', 0)} insights generated")
        
        return result


# Singleton instance holder
_vision_service: Optional[VisionAIService] = None


def get_vision_service(db) -> VisionAIService:
    """Get or create the Vision AI service singleton."""
    global _vision_service
    if _vision_service is None:
        _vision_service = VisionAIService(db)
    return _vision_service
