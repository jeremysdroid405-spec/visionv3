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
        
        try:
            # Extract last name (like on jersey)
            name_parts = player_name.split()
            last_name = name_parts[-1] if name_parts else player_name
            
            # Build badge context (may be empty)
            badge_descriptions = []
            if badges:
                for badge in badges[:4]:  # Limit to 4 most relevant badges
                    badge_key = badge.get("badge_key", "")
                    headline = badge.get("headline", "")
                    if badge_key and headline:
                        badge_descriptions.append(f"- {badge_key.upper()}: {headline}")
            
            badge_context = "\n".join(badge_descriptions) if badge_descriptions else "No special situational factors"
            
            # Determine pick direction based on line vs average
            if is_goblin:
                # Goblin picks are safe floors - we're betting OVER a low line
                direction = "OVER"
                pick_reasoning = f"This is a SAFE FLOOR play. The line of {line} is well below his average of {season_avg}."
            elif is_demon:
                # Demon picks are aggressive - betting player exceeds expectations
                direction = "OVER"
                pick_reasoning = f"This is an AGGRESSIVE play betting he exceeds {line} {stat_type}."
            else:
                # Determine based on line vs average
                if season_avg and line < season_avg - 1:
                    direction = "OVER"
                    pick_reasoning = f"Line of {line} is below his {season_avg} average - taking the OVER."
                elif season_avg and line > season_avg + 1:
                    direction = "UNDER"
                    pick_reasoning = f"Line of {line} is above his {season_avg} average - taking the UNDER."
                else:
                    direction = "OVER" if h10_rate and h10_rate >= 60 else "UNDER"
                    pick_reasoning = f"Based on recent form, targeting {direction} {line} {stat_type}."
            
            # Build prompt
            prompt = f"""You are an elite sports betting analyst. Write a 3-sentence pick breakdown.

INPUT:

PLAYER: {last_name}
PICK: {direction} {line} {stat_type}
SEASON AVG: {season_avg} {stat_type}
L10 HIT RATE: {h10_rate}%
OPPONENT: {opponent or 'TBD'}
PICK TYPE: {pick_reasoning}

CONTEXT BADGES:
{badge_context}

TASK:

Sentence 1 - THE EDGE: State the pick clearly with the primary statistical edge
→ "{last_name} {direction} {line} {stat_type}" + why the numbers favor this

Sentence 2 - MATCHUP CONTEXT: Analyze opponent matchup
→ How does {opponent}'s defense/pace affect this stat?
→ Are they elite, average, or weak against this stat type?
→ Does this help or create friction for the pick?

Sentence 3 - POTENTIAL CONFLICTS: Flag any risks or factors working against this pick
→ Recent cold streak, tough travel, injury concern, elite defender matchup, etc.
→ If no major conflicts exist, note why this is a clean spot

RULES:
- Use ONLY the player's last name
- Be specific with numbers (averages, rates, rankings)
- Be honest about both edge AND risk
- No filler words, no hedging language
- DO NOT say "This pick" or "I like"

OUTPUT: 3 tight sentences covering EDGE → MATCHUP → CONFLICTS"""

            # Initialize Gemini chat
            chat = LlmChat(
                api_key=self.api_key,
                session_id=f"vision_{player_name}_{stat_type}",
                system_message="You are an elite sports betting analyst specializing in player props. Deliver sharp, data-driven insights that cover the statistical edge, matchup context, and potential conflicts. Use only player last names. Be honest about both upside and risk."
            ).with_model("gemini", "gemini-3-flash-preview")
            
            # Send message and get response
            response = await chat.send_message(UserMessage(text=prompt))
            
            if response:
                # Clean up response
                summary = response.strip()
                # Limit to ~500 chars for 3 sentences
                if len(summary) > 520:
                    summary = summary[:517] + "..."
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
