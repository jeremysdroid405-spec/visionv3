"""
Vision AI Summary Service
=========================
Uses Google Gemini to generate short, insightful summaries explaining why a pick was made.
Only for "Vision" picks that have context badges and matchup data.

OPTIMIZATIONS (v7.1):
1. Aggressive caching with content-based hash (not just key)
2. 6-hour TTL for summaries (data doesn't change that often)
3. Background generation support for async returns
"""
import os
import asyncio
import logging
import hashlib
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def _generate_pick_hash(player_name: str, stat_type: str, line: float, 
                        h10_rate: float, badges: List[str], opponent: str = None) -> str:
    """Generate a content-based hash for cache invalidation."""
    content = f"{player_name}|{stat_type}|{line}|{h10_rate}|{','.join(sorted(badges or []))}|{opponent or ''}"
    return hashlib.md5(content.encode()).hexdigest()[:12]


class VisionSummaryService:
    # Class-level cache for AI summaries
    # Key: content_hash -> summary string
    _summary_cache: Dict[str, str] = {}
    _cache_timestamps: Dict[str, datetime] = {}
    _cache_keys: Dict[str, str] = {}  # Maps "player|stat|line" to content_hash for lookup
    _CACHE_TTL_SECONDS = 21600  # Cache summaries for 6 hours (data rarely changes)
    
    # Circuit breaker: If API fails, skip subsequent calls for a period
    _circuit_breaker_open = False
    _circuit_breaker_until: Optional[datetime] = None
    _CIRCUIT_BREAKER_DURATION = 30  # Skip API calls for 30 seconds after failure
    
    def __init__(self):
        self.api_key = os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            logger.warning("[VISION] No GEMINI_API_KEY found - summaries will be disabled")
    
    def _get_badge_headline(self, badge_key: str) -> str:
        """Get a descriptive headline for a badge key."""
        badge_headlines = {
            # Performance badges
            "pay_day": "Performing well recently - on a hot streak",
            "consistent": "Very consistent numbers - reliable performer",
            "streak_hot": "Red hot streak - crushing the over",
            "streak_cold": "Cold streak - struggling recently",
            "bounce_back": "Bounce back candidate after poor game",
            "regression": "Due for regression to the mean",
            
            # Context badges
            "deep_water": "Key teammate out - elevated role expected",
            "vacuum": "Star player injured - usage spike likely",
            "revenge": "Revenge game against former team",
            "division_rival": "Division rivalry - extra motivation",
            "national_tv": "National TV game - spotlight performance",
            "rest_advantage": "Well-rested compared to opponent",
            "b2b": "Playing on back-to-back - fatigue factor",
            "jet_lag": "Recent travel - time zone adjustment",
            "home_cooking": "Strong home performer",
            "road_warrior": "Plays well on the road",
            
            # Warning badges
            "trap_risk": "Suspicious line movement - possible trap",
            "hook_risk": "Line sitting at key number - beware the hook",
            "blowout_risk": "Blowout potential - minutes concern",
            "pace_down": "Pace down matchup - slower game expected",
            "elite_defense": "Facing elite defense at this stat",
            
            # Matchup badges
            "soft_matchup": "Favorable defensive matchup",
            "neutral_matchup": "Neutral matchup - no edge",
            "tough_matchup": "Difficult defensive matchup"
        }
        return badge_headlines.get(badge_key, f"Active factor: {badge_key.replace('_', ' ')}")
    
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
                logger.debug("[VISION] Circuit breaker OPEN - skipping API call")
                return None
            else:
                # Reset circuit breaker
                VisionSummaryService._circuit_breaker_open = False
                VisionSummaryService._circuit_breaker_until = None
        
        # Generate content-based hash for intelligent cache invalidation
        badge_keys = []
        if badges:
            for b in badges:
                if isinstance(b, dict):
                    badge_keys.append(b.get("badge_key") or b.get("key", ""))
                else:
                    badge_keys.append(str(b))
        
        content_hash = _generate_pick_hash(
            player_name, stat_type, line, h10_rate or 0, badge_keys, opponent
        )
        
        # Check cache using content hash (survives even if stats change)
        simple_key = f"{player_name}|{stat_type}|{line}"
        
        # If we have a cached summary for this exact content, return it
        if content_hash in VisionSummaryService._summary_cache:
            cached_time = VisionSummaryService._cache_timestamps.get(content_hash)
            if cached_time and (now - cached_time).total_seconds() < VisionSummaryService._CACHE_TTL_SECONDS:
                logger.debug(f"[VISION] Cache HIT (hash match) for {simple_key}")
                return VisionSummaryService._summary_cache[content_hash]
        
        # Also check if we have old key but same hash (data hasn't changed)
        old_hash = VisionSummaryService._cache_keys.get(simple_key)
        if old_hash and old_hash == content_hash and old_hash in VisionSummaryService._summary_cache:
            cached_time = VisionSummaryService._cache_timestamps.get(old_hash)
            if cached_time and (now - cached_time).total_seconds() < VisionSummaryService._CACHE_TTL_SECONDS:
                logger.debug(f"[VISION] Cache HIT (key->hash) for {simple_key}")
                return VisionSummaryService._summary_cache[old_hash]
        
        try:
            # Extract last name (like on jersey)
            name_parts = player_name.split()
            last_name = name_parts[-1] if name_parts else player_name
            
            # Build badge context (may be empty)
            badge_descriptions = []
            if badges:
                for badge in badges[:4]:  # Limit to 4 most relevant badges
                    # Handle both dict and string badge formats
                    if isinstance(badge, dict):
                        badge_key = badge.get("badge_key") or badge.get("key", "")
                        headline = badge.get("headline", "")
                    else:
                        # Badge is just a string key
                        badge_key = str(badge)
                        headline = self._get_badge_headline(badge_key)
                    
                    if badge_key:
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
                        
                        # Cache the result using content hash
                        VisionSummaryService._summary_cache[content_hash] = summary
                        VisionSummaryService._cache_timestamps[content_hash] = now
                        VisionSummaryService._cache_keys[simple_key] = content_hash
                        logger.info(f"[VISION] Cached summary for {simple_key} (hash: {content_hash})")
                        
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
    
    async def batch_generate_summaries(
        self,
        picks: list,
        max_concurrent: int = 5
    ) -> list:
        """
        Generate AI summaries for multiple picks concurrently with rate limiting.
        
        Uses a semaphore to limit concurrent API calls and avoid rate limits.
        """
        if not self.api_key:
            logger.warning("[VISION] No API key - skipping batch generation")
            return picks
        
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def generate_one(pick: dict) -> None:
            # Skip if already has summary
            if pick.get("vision_summary"):
                return
            
            # Skip if no badges
            badges = pick.get("badges") or pick.get("context_badges") or pick.get("active_badges") or []
            if not badges:
                return
            
            async with semaphore:
                try:
                    summary = await self.generate_pick_summary(
                        player_name=pick.get("player_name", ""),
                        stat_type=pick.get("stat_type", ""),
                        line=pick.get("line", 0),
                        season_avg=pick.get("season_avg", 0),
                        h10_rate=pick.get("h10_rate") or pick.get("l10_hit_rate", 0),
                        badges=[b.get("key") if isinstance(b, dict) else b for b in badges],
                        opponent=pick.get("opponent"),
                        is_demon=pick.get("is_demon", False),
                        is_goblin=pick.get("is_goblin", False)
                    )
                    
                    if summary:
                        pick["vision_summary"] = summary
                        
                except Exception as e:
                    logger.warning(f"[VISION] Batch gen failed for {pick.get('player_name')}: {e}")
        
        # Run all generations concurrently (limited by semaphore)
        tasks = [generate_one(pick) for pick in picks]
        await asyncio.gather(*tasks, return_exceptions=True)
        
        return picks


# Module-level convenience function
async def generate_vision_summary(pick: dict) -> Optional[str]:
    """
    Convenience function to generate a summary for a single pick.
    """
    service = VisionSummaryService()
    
    badges = pick.get("badges") or pick.get("context_badges") or pick.get("active_badges") or []
    if not badges:
        return None
    
    # Get DvP data from intel_suite if available
    intel = pick.get("intel_suite", {})
    dvp_data = intel.get("matchup_dvp", {})
    momentum_data = intel.get("defensive_momentum", {})
    
    return await service.generate_pick_summary(
        player_name=pick.get("player_name", ""),
        stat_type=pick.get("stat_type", ""),
        line=pick.get("line", 0),
        season_avg=pick.get("season_avg") or pick.get("l10_avg", 0),
        h10_rate=pick.get("h10_rate") or pick.get("l10_rate") or pick.get("l10_hit_rate", 0),
        badges=badges,  # Pass as-is, the service handles both formats
        opponent=pick.get("opponent") or pick.get("opponent_abbr"),
        is_demon=pick.get("is_demon", False),
        is_goblin=pick.get("is_goblin", False),
        dvp_rank=dvp_data.get("rank") or momentum_data.get("composite_rank"),
        dvp_friction=dvp_data.get("friction") or momentum_data.get("friction_label"),
        player_team=pick.get("team")
    )
