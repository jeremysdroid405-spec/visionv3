"""
Vision AI Summary Service
=========================
Uses Google Gemini to generate short, insightful summaries explaining why a pick was made.
Only for "Vision" picks that have context badges and matchup data.
Includes aggressive caching to avoid repeated API calls.
"""
import os
import asyncio
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class VisionSummaryService:
    # Class-level cache for AI summaries
    # Key: "player_name|stat_type|line" -> summary string
    _summary_cache: Dict[str, str] = {}
    _cache_timestamps: Dict[str, datetime] = {}
    _CACHE_TTL_SECONDS = 3600  # Cache summaries for 1 hour
    
    # Circuit breaker: If API fails, skip subsequent calls for a period
    _circuit_breaker_open = False
    _circuit_breaker_until: Optional[datetime] = None
    _CIRCUIT_BREAKER_DURATION = 30  # Skip API calls for 30 seconds after failure (reduced since fast model)
    
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
        dvp_friction: str = None,
        player_team: str = None
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
            player_team: Player's team abbreviation (for blowout risk calculation)
            
        Returns:
            A short summary string or None if generation fails
        """
        if not self.api_key:
            return None
        
        now = datetime.now(timezone.utc)
        
        # Circuit breaker: Skip API calls if recently failed
        if VisionSummaryService._circuit_breaker_open:
            if VisionSummaryService._circuit_breaker_until and now < VisionSummaryService._circuit_breaker_until:
                logger.debug(f"[VISION] Circuit breaker OPEN - skipping API call")
                return None
            else:
                # Reset circuit breaker
                VisionSummaryService._circuit_breaker_open = False
                VisionSummaryService._circuit_breaker_until = None
        
        # Check cache first - use player|stat|line as key
        cache_key = f"{player_name}|{stat_type}|{line}"
        
        if cache_key in VisionSummaryService._summary_cache:
            cached_time = VisionSummaryService._cache_timestamps.get(cache_key)
            if cached_time and (now - cached_time).total_seconds() < VisionSummaryService._CACHE_TTL_SECONDS:
                logger.debug(f"[VISION] Cache HIT for {cache_key}")
                return VisionSummaryService._summary_cache[cache_key]
        
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
            if dvp_rank is not None:
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
                dvp_context = f"Matchup data unavailable for {opponent}" if opponent else "No opponent data available"
            
            # Calculate blowout risk if we have team data
            blowout_context = "Game competitiveness data unavailable"
            blowout_warning = None
            if player_team and opponent:
                try:
                    from services.standings_service import StandingsService
                    blowout_data = await StandingsService.calculate_blowout_risk(player_team, opponent)
                    blowout_context = StandingsService.format_blowout_context(blowout_data)
                    blowout_warning = blowout_data.get("warning")
                except Exception as e:
                    logger.warning(f"[VISION] Blowout risk calculation failed: {e}")
            
            # Build prompt
            prompt = f"""You're a sharp sports bettor sharing a quick take with a friend. Keep it real and conversational.

PICK INFO:
- Player: {last_name}
- Bet: {direction} {line} {stat_type}
- Season Average: {season_avg}
- L10 Hit Rate: {h10_rate}%
- Opponent: {opponent or 'TBD'}
- Defense: {dvp_context}
- Game Context: {blowout_context}
- Why it's on the board: {pick_reasoning}

Active situational factors:
{badge_context}

Give me 3 quick sentences like you're texting a buddy:

1. THE PLAY - What's the bet and why does it hit? Lead with the numbers.

2. THE MATCHUP - How does the opponent factor in? Use the defensive ranking I gave you (1-10 = tough, 21-30 = soft).

3. THE CATCH - Any red flags? {"IMPORTANT: " + blowout_warning + " Factor this into your analysis." if blowout_warning else "Bad recent form, brutal schedule, injury worry? If it's a clean spot, say so."}

Keep it tight. No fluff. Talk like a real person, not a robot. Use {last_name}'s name naturally. Skip any "I think" or "This pick" openers."""

            # Initialize Google Gemini directly
            from google import genai
            
            client = genai.Client(api_key=self.api_key)
            
            system_msg = "You're a seasoned sports bettor who knows their stuff. You talk like a real person - confident but not cocky, honest about the risks. Use the player's last name naturally. No markdown formatting (no asterisks, bold, or italics). Just clean, straight talk."
            
            full_prompt = f"{system_msg}\n\n{prompt}"
            
            # Call Gemini API with retry logic
            max_retries = 3
            base_timeout = 8.0  # Increased from 3s to 8s
            
            for attempt in range(max_retries):
                try:
                    # Exponential backoff: 8s, 12s, 16s
                    timeout = base_timeout + (attempt * 4)
                    
                    response = await asyncio.wait_for(
                        asyncio.to_thread(
                            client.models.generate_content,
                            model="gemini-3.1-flash-lite-preview",  # Gemini 3.1 Flash Lite
                            contents=full_prompt
                        ),
                        timeout=timeout
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
                        summary = summary.strip()
                        
                        # Cache the result
                        VisionSummaryService._summary_cache[cache_key] = summary
                        VisionSummaryService._cache_timestamps[cache_key] = now
                        logger.info(f"[VISION] Cached summary for {cache_key}")
                        
                        return summary
                    
                except asyncio.TimeoutError:
                    if attempt < max_retries - 1:
                        logger.warning(f"[VISION] Timeout for {player_name} (attempt {attempt + 1}/{max_retries}) - retrying...")
                        await asyncio.sleep(1.0 * (attempt + 1))  # Brief delay before retry
                        continue
                    else:
                        logger.warning(f"[VISION] Final timeout for {player_name} after {max_retries} attempts")
                        return None
                        
                except Exception as e:
                    logger.warning(f"[VISION] Error for {player_name}: {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1.0 * (attempt + 1))
                        continue
                    return None
            
            return None
            
        except Exception as e:
            # Open circuit breaker on failure
            VisionSummaryService._circuit_breaker_open = True
            VisionSummaryService._circuit_breaker_until = datetime.now(timezone.utc) + \
                __import__('datetime').timedelta(seconds=VisionSummaryService._CIRCUIT_BREAKER_DURATION)
            logger.warning(f"[VISION] Error for {player_name}, circuit breaker OPEN for {VisionSummaryService._CIRCUIT_BREAKER_DURATION}s: {e}")
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
