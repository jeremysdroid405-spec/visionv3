"""
Vision AI Summary Service
=========================
Uses Gemini to generate short, insightful summaries explaining why a pick was made.
Only for "Vision" picks that have context badges and matchup data.
"""
import os
import logging
from typing import Optional, Dict, Any
from emergentintegrations.llm.chat import LlmChat, UserMessage
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class VisionSummaryService:
    def __init__(self):
        self.api_key = os.environ.get("EMERGENT_LLM_KEY")
        if not self.api_key:
            logger.warning("[VISION] No EMERGENT_LLM_KEY found - summaries will be disabled")
    
    async def generate_pick_summary(
        self,
        player_name: str,
        stat_type: str,
        line: float,
        season_avg: float,
        h10_rate: float,
        badges: list,
        opponent: str = None,
        is_demon: bool = False,
        is_goblin: bool = False
    ) -> Optional[str]:
        """
        Generate a 2-3 sentence summary explaining why Vision picked this bet.
        
        Args:
            player_name: Player's name
            stat_type: PTS, REB, AST, PRA, etc.
            line: The betting line
            season_avg: Player's season average for this stat
            h10_rate: Last 10 games hit rate percentage
            badges: List of context badges (pay_day, revenge, jet_lag, etc.)
            opponent: Opponent team abbreviation
            is_demon: True if this is a demon (over) pick
            is_goblin: True if this is a goblin (under/safe) pick
            
        Returns:
            A short summary string or None if generation fails
        """
        if not self.api_key:
            return None
        
        if not badges:
            return None
        
        try:
            # Build badge context
            badge_descriptions = []
            for badge in badges[:4]:  # Limit to 4 most relevant badges
                badge_key = badge.get("badge_key", "")
                headline = badge.get("headline", "")
                if badge_key and headline:
                    badge_descriptions.append(f"- {badge_key.upper()}: {headline}")
            
            badge_context = "\n".join(badge_descriptions) if badge_descriptions else "No special context"
            
            # Determine pick type
            pick_type = "DEMON (aggressive over)" if is_demon else "GOBLIN (safe floor)" if is_goblin else "balanced"
            direction = "OVER" if line < season_avg else "UNDER" if line > season_avg else "AT"
            
            # Build prompt
            prompt = f"""You are a sports betting analyst explaining a pick in 2-3 short sentences. Be confident and insightful.

PICK: {player_name} {direction} {line} {stat_type}
TYPE: {pick_type}
SEASON AVG: {season_avg}
L10 HIT RATE: {h10_rate}%
OPPONENT: {opponent or 'Unknown'}

CONTEXT BADGES:
{badge_context}

Write a brief, punchy explanation of why this is a smart pick. Focus on the most compelling badge/context. Use the player's first name. Don't start with "This pick" or "I recommend"."""

            # Initialize Gemini chat
            chat = LlmChat(
                api_key=self.api_key,
                session_id=f"vision_{player_name}_{stat_type}",
                system_message="You are a sharp sports betting analyst. Give brief, confident insights."
            ).with_model("gemini", "gemini-3-flash-preview")
            
            # Send message and get response
            response = await chat.send_message(UserMessage(text=prompt))
            
            if response:
                # Clean up response
                summary = response.strip()
                # Limit to ~200 chars if too long
                if len(summary) > 250:
                    summary = summary[:247] + "..."
                return summary
            
            return None
            
        except Exception as e:
            logger.error(f"[VISION] Error generating summary for {player_name}: {e}")
            return None
    
    async def enrich_picks_with_vision_summary(self, picks: list) -> list:
        """
        Enrich a list of picks with Vision AI summaries.
        Only adds summaries for picks that have badges.
        
        Args:
            picks: List of pick dictionaries
            
        Returns:
            Same list with 'vision_summary' field added where applicable
        """
        if not self.api_key:
            return picks
        
        for pick in picks:
            # Only generate for picks with badges
            badges = pick.get("badges") or pick.get("context_badges") or []
            if not badges:
                continue
            
            try:
                summary = await self.generate_pick_summary(
                    player_name=pick.get("player_name", ""),
                    stat_type=pick.get("stat_type", ""),
                    line=pick.get("line", 0),
                    season_avg=pick.get("season_avg", 0),
                    h10_rate=pick.get("h10_rate") or pick.get("l10_hit_rate", 0),
                    badges=badges,
                    opponent=pick.get("opponent"),
                    is_demon=pick.get("is_demon", False),
                    is_goblin=pick.get("is_goblin", False)
                )
                
                if summary:
                    pick["vision_summary"] = summary
                    
            except Exception as e:
                logger.error(f"[VISION] Error enriching pick {pick.get('player_name')}: {e}")
                continue
        
        return picks
