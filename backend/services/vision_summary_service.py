"""
Vision AI Summary Service
=========================
Uses Google Gemini to generate short, insightful summaries explaining why a pick was made.
Only for "Vision" picks that have context badges and matchup data.
"""
import os
import asyncio
import logging
from typing import Optional, Dict, Any
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class VisionSummaryService:
    def __init__(self):
        self.api_key = os.environ.get("GOOGLE_API_KEY")
        if not self.api_key:
            logger.warning("[VISION] No GOOGLE_API_KEY found - summaries will be disabled")
    
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
        is_goblin: bool = False,
        dvp_rank: int = None,
        dvp_friction: str = None
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
            dvp_rank: Opponent's defensive rank for this stat (1=best defense, 30=worst)
            dvp_friction: Friction level (Low/Medium/High/Elite)
            
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
            
            # Build DvP context for the AI
            if dvp_rank and dvp_friction:
                if dvp_rank <= 5:
                    dvp_context = f"ELITE DEFENSE - {opponent} ranks #{dvp_rank} vs {stat_type} (Top 5 - very tough matchup)"
                elif dvp_rank <= 10:
                    dvp_context = f"STRONG DEFENSE - {opponent} ranks #{dvp_rank} vs {stat_type} (Top 10 - tough matchup)"
                elif dvp_rank <= 20:
                    dvp_context = f"AVERAGE DEFENSE - {opponent} ranks #{dvp_rank} vs {stat_type} (neutral matchup)"
                elif dvp_rank <= 25:
                    dvp_context = f"WEAK DEFENSE - {opponent} ranks #{dvp_rank} vs {stat_type} (favorable matchup)"
                else:
                    dvp_context = f"POOR DEFENSE - {opponent} ranks #{dvp_rank} vs {stat_type} (Bottom 5 - very favorable matchup)"
            else:
                dvp_context = f"Matchup data unavailable for {opponent}"
            
            # Build prompt
            prompt = f"""You are an elite sports betting analyst. Write a 3-sentence pick breakdown.

INPUT:

PLAYER: {last_name}
PICK: {direction} {line} {stat_type}
SEASON AVG: {season_avg} {stat_type}
L10 HIT RATE: {h10_rate}%
OPPONENT: {opponent or 'TBD'}
DEFENSIVE MATCHUP: {dvp_context}
PICK TYPE: {pick_reasoning}

CONTEXT BADGES:
{badge_context}

TASK:

Sentence 1 - THE EDGE: State the pick clearly with the primary statistical edge
→ "{last_name} {direction} {line} {stat_type}" + why the numbers favor this

Sentence 2 - MATCHUP CONTEXT: Analyze the DEFENSIVE MATCHUP data provided
→ The DVP rank tells you if the opponent is good/bad at defending this stat
→ Rank 1-10 = tough matchup (defense is strong), Rank 21-30 = favorable (defense is weak)
→ Use the actual ranking provided - do NOT guess or assume

Sentence 3 - POTENTIAL CONFLICTS: Flag any risks or factors working against this pick
→ If facing elite defense (rank 1-10), that IS a conflict - acknowledge it
→ Recent cold streak, tough travel, injury concern, etc.
→ If no major conflicts exist, note why this is a clean spot

RULES:
- Use ONLY the player's last name
- Be specific with numbers (averages, rates, rankings)
- Be ACCURATE about the defensive matchup - use the DVP data provided
- Do NOT say a matchup is "favorable" if the defense ranks in the Top 10
- No filler words, no hedging language
- DO NOT say "This pick" or "I like"

OUTPUT: 3 tight sentences covering EDGE → MATCHUP → CONFLICTS"""

            # Initialize Google Gemini directly
            from google import genai
            
            client = genai.Client(api_key=self.api_key)
            
            system_msg = "You are an elite sports betting analyst specializing in player props. Deliver sharp, data-driven insights that cover the statistical edge, matchup context, and potential conflicts. Use only player last names. Be honest about both upside and risk. Do NOT use any markdown formatting like asterisks, bold, or italic."
            
            full_prompt = f"{system_msg}\n\n{prompt}"
            
            # Call Gemini API
            response = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-3.1-flash-lite-preview",
                contents=full_prompt
            )
            
            if response and response.text:
                # Clean up response - remove markdown formatting
                summary = response.text.strip()
                # Remove asterisks (bold/italic markdown)
                summary = summary.replace("**", "").replace("*", "")
                # Remove other common markdown
                summary = summary.replace("__", "").replace("_", " ")
                # Clean up any double spaces
                while "  " in summary:
                    summary = summary.replace("  ", " ")
                return summary.strip()
            
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
