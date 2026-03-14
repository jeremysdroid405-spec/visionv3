"""
Intel Briefing Engine v2.0 - Targeted Strategic Vision
========================================================

Generates bet-specific 2-sentence Strategic Theses for player props.

Conditional Trigger: Only executes for players with:
- is_demon = True
- is_goblin = True  
- in_parlay_maker = True

Data Anchored: Each prompt includes specific prop_type and target_line.

Output Format (2 Sentences):
1. The Matchup Exploit - Why the opponent's defense allows this specific stat
2. The Math Leverage - Why the line is soft

Example Output (Luka Doncic Over 24.5 Pts):
"Cleveland is currently missing Jarrett Allen in the paint, leaving their interior 
defense vulnerable to Luka's elite driving gravity. With Kyrie Irving sidelined 
tonight, Luka's projected usage rate jumps to a season-high 38%, making this 24.5 
point line an easy exploitation of a depleted Cavaliers frontcourt."

Model: gemini-2.5-flash
"""

import os
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# Google Gemini API Key from environment
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")


class IntelBriefingEngine:
    """
    Targeted Strategic Vision Engine v2.0
    
    Generates bet-specific 2-sentence Strategic Theses:
    - Sentence 1: The Matchup Exploit (opponent defensive weakness)
    - Sentence 2: The Math Leverage (why the line is mispriced)
    
    Only triggers for is_demon, is_goblin, or in_parlay_maker players.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.cached_board = db.dg_cached_board
        self.intel_cache = db.dg_intel_briefings
        self.radar_picks = db.dg_radar_picks
        self.goblin_vault = db.dg_goblin_vault
        self._api_available = bool(GOOGLE_API_KEY)
        
        if not self._api_available:
            logger.warning("[VISION] No GOOGLE_API_KEY - Strategic Vision disabled")
        else:
            logger.info("[VISION] Targeted Strategic Vision Engine v2.0 initialized")
    
    async def generate_strategic_thesis(
        self,
        player_name: str,
        prop_type: str,
        target_line: float,
        direction: str,
        team: str,
        opponent: str,
        l10_stats: Dict[str, Any],
        l5_stats: Dict[str, Any] = None,
        position: str = "",
        injury_context: str = "",
        game_id: str = "",
        is_demon: bool = False,
        is_goblin: bool = False
    ) -> Optional[str]:
        """
        Generate a targeted 2-sentence Strategic Thesis for a specific bet.
        
        Args:
            player_name: Player's full name
            prop_type: Specific prop type (points, rebounds, assists, etc.)
            target_line: The exact betting line (e.g., 24.5)
            direction: Over or Under
            team: Player's team
            opponent: Opposing team
            l10_stats: Last 10 games statistics for this prop
            l5_stats: Last 5 games statistics (optional)
            position: Player position
            injury_context: Relevant injury news
            game_id: Game identifier for caching
            is_demon: Whether this is a Demon pick
            is_goblin: Whether this is a Goblin pick
        
        Returns:
            2-sentence Strategic Thesis or None
        """
        if not self._api_available:
            return None
        
        # Create unique cache key including prop type and line
        cache_key = f"{player_name}_{game_id}_{prop_type}_{target_line}"
        
        # Check cache
        existing = await self.intel_cache.find_one(
            {"cache_key": cache_key},
            {"_id": 0, "intel_briefing": 1}
        )
        
        if existing and existing.get("intel_briefing"):
            logger.debug(f"[VISION] Cache HIT: {player_name} {prop_type} {target_line}")
            return existing["intel_briefing"]
        
        pick_type = "DEMON" if is_demon else "GOBLIN" if is_goblin else "TARGET"
        logger.info(f"[VISION] Generating {pick_type} thesis: {player_name} {direction} {target_line} {prop_type} vs {opponent}")
        
        # Build the targeted prompt
        prompt = self._build_targeted_prompt(
            player_name=player_name,
            prop_type=prop_type,
            target_line=target_line,
            direction=direction,
            team=team,
            opponent=opponent,
            l10_stats=l10_stats,
            l5_stats=l5_stats or {},
            position=position,
            injury_context=injury_context,
            is_demon=is_demon,
            is_goblin=is_goblin
        )
        
        try:
            thesis = await self._call_gemini(prompt)
            
            if thesis:
                # Cache the result
                await self.intel_cache.update_one(
                    {"cache_key": cache_key},
                    {
                        "$set": {
                            "cache_key": cache_key,
                            "player_name": player_name,
                            "game_id": game_id,
                            "prop_type": prop_type,
                            "target_line": target_line,
                            "direction": direction,
                            "opponent": opponent,
                            "intel_briefing": thesis,
                            "is_demon": is_demon,
                            "is_goblin": is_goblin,
                            "generated_at": datetime.now(timezone.utc).isoformat()
                        }
                    },
                    upsert=True
                )
                logger.info(f"[VISION] Generated thesis for {player_name}")
                return thesis
                
        except Exception as e:
            logger.error(f"[VISION] Generation failed: {player_name} - {e}")
        
        return None
    
    def _build_targeted_prompt(
        self,
        player_name: str,
        prop_type: str,
        target_line: float,
        direction: str,
        team: str,
        opponent: str,
        l10_stats: Dict[str, Any],
        l5_stats: Dict[str, Any],
        position: str,
        injury_context: str,
        is_demon: bool,
        is_goblin: bool
    ) -> str:
        """Build the targeted 2-sentence Strategic Thesis prompt."""
        
        # Format prop type for display
        prop_display = prop_type.replace("_", " ").replace("player ", "").replace("alternate ", "").strip().title()
        if not prop_display:
            prop_display = "Points"
        
        # Extract stats
        l10_avg = l10_stats.get("avg", 0)
        l10_hit_rate = l10_stats.get("hit_rate_pct", 0)
        l10_over = l10_stats.get("games_over", 0)
        l10_games = l10_stats.get("total_games", 10)
        l5_avg = l5_stats.get("avg", 0)
        l5_over = l5_stats.get("games_over", 0)
        l5_games = l5_stats.get("total_games", 5)
        
        # Determine stat-specific defensive weakness context
        stat_lower = prop_display.lower()
        if "point" in stat_lower:
            defensive_weakness = "perimeter defense, rim protection, or isolation containment"
            stat_context = "scoring load and shot volume"
            math_angle = "usage rate and shot attempts"
        elif "rebound" in stat_lower:
            defensive_weakness = "rebounding rotations, box-out discipline, or frontcourt depth"
            stat_context = "glass positioning and crash timing"
            math_angle = "minutes played and rebound opportunities"
        elif "assist" in stat_lower:
            defensive_weakness = "help defense rotations, passing lane coverage, or pick-and-roll containment"
            stat_context = "playmaking opportunities and kick-out frequency"
            math_angle = "teammate shooting efficiency and offensive possessions"
        elif "3" in stat_lower or "three" in stat_lower:
            defensive_weakness = "perimeter closeouts, three-point defense, or transition coverage"
            stat_context = "three-point attempts and catch-and-shoot opportunities"
            math_angle = "shot volume and defensive attention on teammates"
        elif "steal" in stat_lower:
            defensive_weakness = "ball security, turnover tendencies, or careless passing"
            stat_context = "passing lane disruption and on-ball pressure"
            math_angle = "opponent turnover rate and pace of play"
        elif "block" in stat_lower:
            defensive_weakness = "interior finishing, reliance on rim attacks, or weak floater games"
            stat_context = "rim protection opportunities and help defense positioning"
            math_angle = "opponent shot selection and paint touches"
        else:
            defensive_weakness = "specific matchup vulnerabilities"
            stat_context = "overall production"
            math_angle = "opportunity and volume metrics"
        
        # Calculate the edge
        edge_vs_line = ((l10_avg - target_line) / target_line * 100) if target_line > 0 else 0
        
        # Build the prompt
        prompt = f"""You are an elite NBA betting analyst. Write a 2-sentence Strategic Thesis for this SPECIFIC bet.

THE BET:
{player_name} ({team}, {position}) {direction} {target_line} {prop_display} vs {opponent}

PLAYER STATS FOR THIS PROP:
- L10 Average: {l10_avg:.1f} {prop_display.lower()} (hit this line {l10_over}/{l10_games} times = {l10_hit_rate:.0f}%)
- L5 Average: {l5_avg:.1f} {prop_display.lower()} (hit {l5_over}/{l5_games} = {l5_over/l5_games*100:.0f}% recent form)
- Edge vs Line: {'+' if edge_vs_line > 0 else ''}{edge_vs_line:.1f}%
{f"- INJURY CONTEXT: {injury_context}" if injury_context else ""}

YOUR OUTPUT MUST BE EXACTLY 2 SENTENCES:

SENTENCE 1 (The Matchup Exploit): 
Explain specifically why {opponent}'s {defensive_weakness} allows {player_name} to exceed {target_line} {prop_display.lower()}. 
DO NOT mention other stats - focus ONLY on {prop_display.lower()}.

SENTENCE 2 (The Math Leverage):
Explain why this {target_line} line is mispriced using {math_angle}. Include a specific number or percentage.

EXAMPLE OUTPUT (for Luka Over 24.5 Pts):
"Cleveland is currently missing Jarrett Allen in the paint, leaving their interior defense vulnerable to Luka's elite driving gravity. With Kyrie Irving sidelined tonight, Luka's projected usage rate jumps to a season-high 38%, making this 24.5 point line an easy exploitation of a depleted Cavaliers frontcourt."

NOW WRITE YOUR 2-SENTENCE THESIS FOR: {player_name} {direction} {target_line} {prop_display}"""

        return prompt
    
    async def _call_gemini(self, prompt: str) -> Optional[str]:
        """Call Gemini Flash API for text generation."""
        
        try:
            from google import genai
            
            client = genai.Client(api_key=GOOGLE_API_KEY)
            
            def generate():
                return client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt
                )
            
            response = await asyncio.get_event_loop().run_in_executor(None, generate)
            
            if response and response.text:
                # Clean up the response - remove any markdown or extra formatting
                text = response.text.strip()
                # Remove markdown quotes if present
                text = text.replace('```', '').strip()
                if text.startswith('"') and text.endswith('"'):
                    text = text[1:-1]
                return text
            
        except ImportError:
            logger.error("[VISION] google-genai package not installed")
        except Exception as e:
            logger.error(f"[VISION] Gemini API error: {e}")
        
        return None
    
    async def generate_for_targeted_picks(self) -> Dict[str, Any]:
        """
        Generate Strategic Theses ONLY for players tagged as:
        - is_demon (Radar picks)
        - is_goblin (Vault picks)
        - in_parlay_maker (Parlay selections)
        
        This is the main entry point - called after sync.
        """
        if not self._api_available:
            return {"success": False, "error": "GOOGLE_API_KEY not configured"}
        
        logger.info("[VISION] Generating targeted Strategic Theses...")
        
        generated = 0
        errors = 0
        processed_players = set()
        
        # 1. Process Demon Radar Picks
        radar_picks = await self.radar_picks.find({}, {"_id": 0}).to_list(length=20)
        logger.info(f"[VISION] Processing {len(radar_picks)} Demon Radar picks")
        
        for pick in radar_picks:
            player_name = pick.get("player_name")
            if not player_name or player_name in processed_players:
                continue
            
            # Get full player data from cached_board
            player_data = await self.cached_board.find_one(
                {"player_name": player_name},
                {"_id": 0}
            )
            
            if not player_data:
                continue
            
            # Find the demon prop
            props = player_data.get("props", [])
            demon_prop = None
            for p in props:
                if p.get("is_demon"):
                    demon_prop = p
                    break
            
            if not demon_prop:
                # Use the pick's own data
                demon_prop = {
                    "market": pick.get("stat_type", "points"),
                    "line": pick.get("demon_line", 0),
                    "direction": pick.get("direction", "Over"),
                    "event_id": pick.get("event_id", ""),
                    "hit_rates": {}
                }
            
            # Extract hit rates
            hit_rates = demon_prop.get("hit_rates", {})
            l10_stats = hit_rates.get("l10", {})
            l5_stats = hit_rates.get("l5", {})
            
            # If hit_rates not in prop, try to get from pick
            if not l10_stats:
                l10_stats = {
                    "avg": pick.get("season_avg", 0),
                    "hit_rate_pct": pick.get("h10_rate", 0) * 100,
                    "games_over": pick.get("h10_over", 0),
                    "total_games": pick.get("h10_games", 10)
                }
            if not l5_stats:
                l5_stats = {
                    "games_over": pick.get("h5_over", 0),
                    "total_games": pick.get("h5_games", 5)
                }
            
            # Generate thesis
            thesis = await self.generate_strategic_thesis(
                player_name=player_name,
                prop_type=demon_prop.get("market", pick.get("stat_type", "points")),
                target_line=demon_prop.get("line", pick.get("demon_line", 0)),
                direction=demon_prop.get("direction", "Over"),
                team=player_data.get("team", pick.get("team", "")),
                opponent=demon_prop.get("away_team", "") or demon_prop.get("home_team", ""),
                l10_stats=l10_stats,
                l5_stats=l5_stats,
                position=player_data.get("position", ""),
                injury_context=player_data.get("injury_context", ""),
                game_id=demon_prop.get("event_id", ""),
                is_demon=True,
                is_goblin=False
            )
            
            if thesis:
                # Update cached_board
                await self.cached_board.update_one(
                    {"player_name": player_name},
                    {"$set": {"intel_briefing": thesis, "has_vision": True}}
                )
                # Also update radar pick
                await self.radar_picks.update_one(
                    {"player_name": player_name},
                    {"$set": {"intel_briefing": thesis}}
                )
                generated += 1
                processed_players.add(player_name)
            else:
                errors += 1
            
            await asyncio.sleep(0.3)  # Rate limit
        
        # 2. Process Goblin Vault Picks
        vault_picks = await self.goblin_vault.find({}, {"_id": 0}).to_list(length=20)
        logger.info(f"[VISION] Processing {len(vault_picks)} Goblin Vault picks")
        
        for pick in vault_picks:
            player_name = pick.get("player_name")
            if not player_name or player_name in processed_players:
                continue
            
            player_data = await self.cached_board.find_one(
                {"player_name": player_name},
                {"_id": 0}
            )
            
            if not player_data:
                continue
            
            # Find the goblin prop
            props = player_data.get("props", [])
            goblin_prop = None
            for p in props:
                if p.get("is_goblin"):
                    goblin_prop = p
                    break
            
            if not goblin_prop:
                goblin_prop = {
                    "market": pick.get("stat_type", "points"),
                    "line": pick.get("goblin_line", 0),
                    "direction": "Over",
                    "event_id": pick.get("event_id", ""),
                    "hit_rates": {}
                }
            
            hit_rates = goblin_prop.get("hit_rates", {})
            l10_stats = hit_rates.get("l10", {})
            l5_stats = hit_rates.get("l5", {})
            
            if not l10_stats:
                l10_stats = {
                    "avg": pick.get("season_avg", 0),
                    "hit_rate_pct": pick.get("h10_hit_rate", 0),
                    "games_over": pick.get("h10_over", 0),
                    "total_games": pick.get("h10_games", 10)
                }
            
            thesis = await self.generate_strategic_thesis(
                player_name=player_name,
                prop_type=goblin_prop.get("market", pick.get("stat_type", "points")),
                target_line=goblin_prop.get("line", pick.get("goblin_line", 0)),
                direction="Over",
                team=player_data.get("team", pick.get("team", "")),
                opponent=goblin_prop.get("away_team", "") or goblin_prop.get("home_team", ""),
                l10_stats=l10_stats,
                l5_stats=l5_stats,
                position=player_data.get("position", ""),
                injury_context=player_data.get("injury_context", ""),
                game_id=goblin_prop.get("event_id", ""),
                is_demon=False,
                is_goblin=True
            )
            
            if thesis:
                await self.cached_board.update_one(
                    {"player_name": player_name},
                    {"$set": {"intel_briefing": thesis, "has_vision": True}}
                )
                await self.goblin_vault.update_one(
                    {"player_name": player_name},
                    {"$set": {"intel_briefing": thesis}}
                )
                generated += 1
                processed_players.add(player_name)
            else:
                errors += 1
            
            await asyncio.sleep(0.3)
        
        logger.info(f"[VISION] Generated {generated} Strategic Theses ({errors} errors)")
        
        return {
            "success": True,
            "generated": generated,
            "errors": errors,
            "processed_players": list(processed_players)
        }
    
    async def check_and_generate_for_board(self) -> Dict[str, Any]:
        """
        Legacy method - redirects to targeted generation.
        """
        return await self.generate_for_targeted_picks()
    
    async def get_intel_for_player(self, player_name: str, game_id: str = None) -> Optional[str]:
        """Get cached intel briefing for a player."""
        query = {"player_name": player_name}
        if game_id:
            query["game_id"] = game_id
        
        doc = await self.intel_cache.find_one(
            query,
            {"_id": 0, "intel_briefing": 1},
            sort=[("generated_at", -1)]
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
