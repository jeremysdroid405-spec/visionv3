"""
Intel Briefing Engine v1.0
===========================

Static Vision Insight Generation using Gemini 3 Flash

Purpose:
- Generate one-time AI Mission Intel Briefings for each PlayerID + GameID combination
- Uses L10 stats and current betting line to write tactical reports
- Military Scout tone with [Sector Trend] and [Engagement Context] structure

Model: gemini-3-flash-preview with thinking_level=low for speed/cost efficiency
"""

import os
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# Google Gemini API Key from environment
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")


class IntelBriefingEngine:
    """
    Static Intel Briefing Generator using Gemini 3 Flash
    
    Generates tactical reports for player props with:
    - [Sector Trend]: Analysis based on L10 stats
    - [Engagement Context]: Current betting line context
    
    Only generates once per PlayerID + GameID combination.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.cached_board = db.dg_cached_board
        self.intel_cache = db.dg_intel_briefings  # Cache for generated briefings
        self._api_available = bool(GOOGLE_API_KEY)
        
        if not self._api_available:
            logger.warning("[INTEL BRIEFING] No GOOGLE_API_KEY found - Intel Briefing Engine disabled")
        else:
            logger.info("[INTEL BRIEFING] Engine initialized with Gemini 3 Flash")
    
    async def generate_intel_briefing(
        self,
        player_name: str,
        game_id: str,
        prop_type: str,
        line: float,
        l10_stats: Dict[str, Any],
        team: str = "",
        opponent: str = "",
        l5_stats: Dict[str, Any] = None,
        position: str = "",
        injury_context: str = ""
    ) -> Optional[str]:
        """
        Generate a Mission Intel Briefing for a specific player prop.
        
        Args:
            player_name: Player's full name
            game_id: The Odds API game ID
            prop_type: Type of prop (points, rebounds, assists, etc.)
            line: Current betting line
            l10_stats: Last 10 games statistics
            team: Player's team abbreviation
            opponent: Opponent team abbreviation
            l5_stats: Last 5 games statistics (optional)
            position: Player's position (PG, SG, SF, PF, C)
            injury_context: Any relevant injury news affecting usage
        
        Returns:
            Generated intel briefing text or None if generation fails
        """
        if not self._api_available:
            return None
        
        # Create unique cache key
        cache_key = f"{player_name}_{game_id}_{prop_type}"
        
        # Check if intel already exists for this combination
        existing = await self.intel_cache.find_one(
            {"cache_key": cache_key},
            {"_id": 0, "intel_briefing": 1}
        )
        
        if existing and existing.get("intel_briefing"):
            logger.debug(f"[INTEL BRIEFING] Cache HIT for {cache_key}")
            return existing["intel_briefing"]
        
        logger.info(f"[INTEL BRIEFING] Generating for {player_name} ({prop_type} @ {line}) vs {opponent}")
        
        # Extract L10 data for the prompt
        l10_avg = l10_stats.get("avg", 0)
        l10_hit_rate = l10_stats.get("hit_rate_pct", 0)
        l10_over = l10_stats.get("games_over", 0)
        l10_games = l10_stats.get("total_games", 10)
        l10_high = l10_stats.get("high", 0)
        l10_low = l10_stats.get("low", 0)
        
        # Extract L5 data if available
        l5_stats = l5_stats or {}
        l5_over = l5_stats.get("games_over", 0)
        l5_games = l5_stats.get("total_games", 5)
        
        # Build the prompt
        prompt = self._build_prompt(
            player_name=player_name,
            prop_type=prop_type,
            line=line,
            l10_avg=l10_avg,
            l10_hit_rate=l10_hit_rate,
            l10_over=l10_over,
            l10_games=l10_games,
            l10_high=l10_high,
            l10_low=l10_low,
            team=team,
            opponent=opponent,
            l5_over=l5_over,
            l5_games=l5_games,
            position=position,
            injury_context=injury_context
        )
        
        try:
            # Call Gemini API
            intel_briefing = await self._call_gemini(prompt)
            
            if intel_briefing:
                # Cache the result
                await self.intel_cache.update_one(
                    {"cache_key": cache_key},
                    {
                        "$set": {
                            "cache_key": cache_key,
                            "player_name": player_name,
                            "game_id": game_id,
                            "prop_type": prop_type,
                            "line": line,
                            "intel_briefing": intel_briefing,
                            "generated_at": datetime.now(timezone.utc).isoformat()
                        }
                    },
                    upsert=True
                )
                logger.info(f"[INTEL BRIEFING] Generated and cached for {player_name}")
                return intel_briefing
            
        except Exception as e:
            logger.error(f"[INTEL BRIEFING] Generation failed for {player_name}: {e}")
        
        return None
    
    def _build_prompt(
        self,
        player_name: str,
        prop_type: str,
        line: float,
        l10_avg: float,
        l10_hit_rate: float,
        l10_over: int,
        l10_games: int,
        l10_high: float,
        l10_low: float,
        team: str,
        opponent: str,
        l5_over: int = 0,
        l5_games: int = 5,
        position: str = "",
        injury_context: str = ""
    ) -> str:
        """Build the enhanced prompt for Gemini with tactical scout instructions."""
        
        # Format prop type for readability
        prop_display = prop_type.replace("_", " ").replace("player ", "").replace("alternate", "").strip().title()
        if not prop_display:
            prop_display = "Points"
        
        # Calculate L5 hit rate
        l5_hit_rate = (l5_over / l5_games * 100) if l5_games > 0 else 0
        
        # Format stat type for defensive context
        stat_category = prop_display.lower()
        if "point" in stat_category:
            defensive_stat = "points allowed"
            position_context = "scorers"
        elif "rebound" in stat_category:
            defensive_stat = "rebounds allowed"
            position_context = "rebounders"
        elif "assist" in stat_category:
            defensive_stat = "assists allowed"
            position_context = "playmakers"
        elif "3" in stat_category or "three" in stat_category:
            defensive_stat = "three-pointers allowed"
            position_context = "perimeter shooters"
        elif "steal" in stat_category:
            defensive_stat = "steals allowed"
            position_context = "ball handlers"
        elif "block" in stat_category:
            defensive_stat = "blocks allowed"
            position_context = "rim protectors"
        else:
            defensive_stat = f"{stat_category} allowed"
            position_context = "players at this position"
        
        # Build system instructions prompt
        prompt = f"""SYSTEM INSTRUCTIONS:
You are a professional NBA Tactical Scout. You are analyzing a specific player prop.

DATA CONTEXT:
- Asset: {player_name}
- Team: {team}
- Position: {position if position else "Guard/Forward"}
- Stat Type: {prop_display}
- Line: Over/Under {line}
- Opponent: {opponent}

RECENT DEPLOYMENT DATA:
- L5 Missions: Cleared {line} in {l5_over}/{l5_games} deployments ({l5_hit_rate:.0f}% success)
- L10 Missions: Cleared {line} in {l10_over}/{l10_games} deployments ({l10_hit_rate:.0f}% success)
- L10 Average Output: {l10_avg:.1f}
- Output Range: {l10_low:.0f} - {l10_high:.0f}

{f"INJURY INTEL: {injury_context}" if injury_context else ""}

THE TWO-SENTENCE MANDATE:

Sentence 1 [Sector Trend]: Analyze the asset's recent form (L5/L10) specifically against this {line} line. Do NOT just list the average. Explain consistency (e.g., "Cleared in {l10_over} of last {l10_games} missions" or "Showing volatility with outputs ranging from {l10_low:.0f} to {l10_high:.0f}").

Sentence 2 [Engagement Context]: Analyze the matchup against {opponent}. Reference defensive vulnerabilities or strengths. If teammate injuries create a usage bump opportunity, mention it.

TACTICAL RULES:
- No fluff. No "I think." No hedging language.
- Use military terminology: Sector, Tactical Edge, Deployment, High-Value Target, Asset, Mission, Cleared.
- Be specific about the numbers.
- Maximum 2 sentences total.

EXAMPLE OUTPUT FORMAT:
[Sector Trend] Duncan Robinson has cleared 4+ RA in 70% of his last 10 deployments, showing elite consistency as a secondary facilitator. [Engagement Context] Detroit's defensive perimeter is currently compromised, ranking 28th in {defensive_stat} to {position_context}, creating a high-probability tactical edge.

Generate the intel briefing for {player_name} {prop_display} Over {line}:"""

        return prompt
    
    async def _call_gemini(self, prompt: str) -> Optional[str]:
        """Call Gemini Flash API for text generation using google.genai."""
        
        try:
            from google import genai
            
            # Create client with API key
            client = genai.Client(api_key=GOOGLE_API_KEY)
            
            # Generate content synchronously (Gemini SDK handles this well)
            def generate():
                return client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt
                )
            
            # Run in thread to not block event loop
            response = await asyncio.get_event_loop().run_in_executor(None, generate)
            
            if response and response.text:
                return response.text.strip()
            
        except ImportError:
            logger.error("[INTEL BRIEFING] google-genai package not installed")
        except Exception as e:
            logger.error(f"[INTEL BRIEFING] Gemini API error: {e}")
        
        return None
    
    async def check_and_generate_for_board(self) -> Dict[str, Any]:
        """
        Scan cached_board for entries missing intel_briefing and generate them.
        
        This should be called after a sync to populate intel for new entries.
        
        Returns:
            Dict with generation stats
        """
        if not self._api_available:
            return {"success": False, "error": "GOOGLE_API_KEY not configured"}
        
        logger.info("[INTEL BRIEFING] Scanning board for missing intel...")
        
        # Find entries without intel_briefing
        cursor = self.cached_board.find(
            {
                "$or": [
                    {"intel_briefing": {"$exists": False}},
                    {"intel_briefing": None},
                    {"intel_briefing": ""}
                ]
            },
            {"_id": 0}
        )
        
        entries = await cursor.to_list(length=50)  # Limit batch size
        
        if not entries:
            logger.info("[INTEL BRIEFING] No entries missing intel")
            return {"success": True, "generated": 0, "message": "All entries have intel"}
        
        logger.info(f"[INTEL BRIEFING] Found {len(entries)} entries needing intel")
        
        generated = 0
        errors = 0
        
        for entry in entries:
            player_name = entry.get("player_name", "")
            
            # Get the first prop to generate intel for
            props = entry.get("props", [])
            if not props:
                continue
            
            # Use the first demon or goblin prop, or first prop
            target_prop = None
            for p in props:
                if p.get("is_demon") or p.get("is_goblin"):
                    target_prop = p
                    break
            if not target_prop:
                target_prop = props[0]
            
            # Get game_id from entry or from prop's event_id
            game_id = entry.get("game_id") or entry.get("event_id") or target_prop.get("event_id", "")
            
            if not player_name or not game_id:
                continue
            
            prop_type = target_prop.get("market", "") or target_prop.get("prop_type", "points")
            line = target_prop.get("line", 0)
            
            # Extract L10 and L5 stats from hit_rates structure
            hit_rates = target_prop.get("hit_rates", {})
            l10_data = hit_rates.get("l10", {})
            l5_data = hit_rates.get("l5", {})
            
            # Also get opponent from prop if not on entry
            opponent = entry.get("opponent", "") or target_prop.get("away_team", "") or target_prop.get("home_team", "")
            team = entry.get("team", "") or ""
            position = entry.get("position", "") or ""
            
            # Check for injury context (usage bump opportunities)
            injury_context = ""
            if entry.get("has_usage_bump"):
                injury_context = f"Teammate injuries may create usage bump opportunity."
            
            # Generate intel with enhanced parameters
            intel = await self.generate_intel_briefing(
                player_name=player_name,
                game_id=game_id,
                prop_type=prop_type,
                line=line,
                l10_stats=l10_data,
                team=team,
                opponent=opponent,
                l5_stats=l5_data,
                position=position,
                injury_context=injury_context
            )
            
            if intel:
                # Update cached_board with the intel (use player_name as primary key)
                await self.cached_board.update_one(
                    {"player_name": player_name},
                    {"$set": {"intel_briefing": intel}}
                )
                generated += 1
            else:
                errors += 1
            
            # Rate limit to avoid API throttling
            await asyncio.sleep(0.5)
        
        logger.info(f"[INTEL BRIEFING] Generated {generated} intel briefings ({errors} errors)")
        
        return {
            "success": True,
            "scanned": len(entries),
            "generated": generated,
            "errors": errors
        }
    
    async def get_intel_for_player(self, player_name: str, game_id: str = None) -> Optional[str]:
        """
        Get cached intel briefing for a player.
        
        Args:
            player_name: Player name
            game_id: Optional game ID for specific game
        
        Returns:
            Intel briefing text or None
        """
        query = {"player_name": player_name}
        if game_id:
            query["game_id"] = game_id
        
        doc = await self.intel_cache.find_one(
            query,
            {"_id": 0, "intel_briefing": 1},
            sort=[("generated_at", -1)]  # Get most recent
        )
        
        return doc.get("intel_briefing") if doc else None


# Singleton instance
_intel_engine: Optional[IntelBriefingEngine] = None


def get_intel_briefing_engine(db: AsyncIOMotorDatabase = None) -> Optional[IntelBriefingEngine]:
    """Get or create the Intel Briefing Engine singleton."""
    global _intel_engine
    
    if _intel_engine is None and db is not None:
        _intel_engine = IntelBriefingEngine(db)
    
    return _intel_engine


def init_intel_briefing_engine(db: AsyncIOMotorDatabase) -> IntelBriefingEngine:
    """Initialize the Intel Briefing Engine."""
    global _intel_engine
    _intel_engine = IntelBriefingEngine(db)
    return _intel_engine
